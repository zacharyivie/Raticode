"""Studio API for workflow shortcuts (``gofer.rattish.workflow_shortcuts``).

``GET /api/workflow-shortcuts`` lists them cheaply for the desktop app, which registers
the keys; ``?details=1`` also compiles each workflow to say what its shortcut does, for
Settings > Devices. Adding, removing, running and approving are the user's own actions:
agent tokens can't make these writes (``gofer.ui.headless.agent_request_refusal``).

A press runs only the workflow version approved for the shortcut. A changed workflow
doesn't start: the run answers 409 with ``code: "shortcut_changed"`` and the desktop
app's notice opens Review changes (``GET .../ID/changes``, ``POST .../ID/approve``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from gofer.rattish import workflow_shortcuts as shortcuts
from gofer.rattish.workflow_shortcuts import (
    MAX_TEXT_CHARS,
    NOT_OPEN,
    ShortcutError,
    ShortcutStore,
    WorkflowShortcut,
    approval_status,
    approve_shortcut,
    current_snapshot,
    describe,
    require_approved,
    resolve_target,
    shortcut_changes,
)

MAX_BODY_BYTES = 4 * MAX_TEXT_CHARS + 4096
log = logging.getLogger(__name__)


def _names(base: Path) -> dict[str, Any]:
    from gofer.rattish.workspaces import list_registered_workflows

    try:
        workflows = list_registered_workflows(registry_dir=base)
    except Exception:  # noqa: BLE001 - a broken registry shows names as missing.
        return {}
    return {item.workflow_id.lower(): item for item in workflows}


def _entry(base: Path, record: WorkflowShortcut, registered: Any, details: bool) -> dict[str, Any]:
    payload = {
        **record.to_payload(),
        "workflowName": registered.name if registered else record.workflow_id,
        "projectRoot": str(registered.project_root) if registered else "",
        "sourcePath": str(registered.entrypoint) if registered else "",
        "available": registered is not None,
    }
    if details:
        target = resolve_target(base, record.workflow_id, record.input)
        payload["target"] = {**target.to_payload(), "summary": describe(target)}
        status = approval_status(base, record, registered=registered) if registered else None
        payload["approval"] = status.to_payload() if status else None
    return payload


def list_payload(
    base: Path, *, details: bool = False, workflow_id: str | None = None
) -> dict[str, Any]:
    names = _names(base)
    records = ShortcutStore(base).entries()
    if workflow_id:
        records = [item for item in records if item.workflow_id.lower() == workflow_id.lower()]
    return {
        "shortcuts": [
            _entry(base, record, names.get(record.workflow_id.lower()), details)
            for record in records
        ]
    }


def detail_payload(base: Path, shortcut_id: str) -> dict[str, Any]:
    record = ShortcutStore(base).get(shortcut_id)
    return {"shortcut": _entry(base, record, _names(base).get(record.workflow_id.lower()), True)}


def target_payload(base: Path, workflow_id: Any) -> dict[str, Any]:
    """What a new shortcut for this workflow would do, before it is saved."""
    if not isinstance(workflow_id, str) or not workflow_id:
        raise ShortcutError("Choose a workflow.")
    existing = ShortcutStore(base).for_workflow(workflow_id)
    target = resolve_target(base, workflow_id, existing.input if existing else None)
    return {
        "target": {**target.to_payload(), "summary": describe(target)},
        "shortcut": existing.to_payload() if existing else None,
    }


def set_payload(base: Path, body: dict[str, Any]) -> dict[str, Any]:
    """Adds a workflow's shortcut or changes its key and input."""
    workflow_id = body.get("workflowId")
    if not isinstance(workflow_id, str) or not workflow_id:
        raise ShortcutError("Choose a workflow.")
    chosen = body.get("input")
    if chosen is not None and not isinstance(chosen, str):
        raise ShortcutError("input must be the name of a text input.")
    target = resolve_target(base, workflow_id, chosen)
    if target.mode == "unavailable":
        raise ShortcutError(target.problem)
    if chosen and target.input != chosen:
        raise ShortcutError(f"The workflow has no plain text input named {chosen[:80]!r}.")
    store = ShortcutStore(base)
    # Adding a shortcut approves the workflow as it is now; changing one keeps its approval.
    approval = None
    if store.for_workflow(workflow_id) is None:
        found = current_snapshot(base, workflow_id)
        if found is None:
            raise ShortcutError(NOT_OPEN)
        approval = found[0]
    record = store.set(workflow_id, body.get("binding"), target.input, approval=approval)
    return detail_payload(base, record.id)


def remove_payload(base: Path, shortcut_id: str) -> dict[str, Any]:
    record = ShortcutStore(base).remove(shortcut_id)
    return {"removed": record.to_payload()}


async def run_payload(base: Path, shortcut_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Starts the shortcut's workflow as a run the user started.

    ``text`` fills the shortcut's text input; a workflow without one runs with no
    inputs. The usual run checks apply (inputs against their schema, run limits).
    """
    from gofer.ui.api import run_workflow_payload

    record = ShortcutStore(base).get(shortcut_id)
    registered = _names(base).get(record.workflow_id.lower())
    if registered is not None:
        # Only the approved version runs: a changed workflow waits for Review changes.
        require_approved(base, record, registered)
    target = resolve_target(base, record.workflow_id, record.input)
    if target.mode == "unavailable":
        raise ShortcutError(target.problem)
    inputs: dict[str, Any] = {}
    if target.mode == "text":
        text = body.get("text")
        if not isinstance(text, str):
            raise ShortcutError(f"Type or paste the text for {target.input_label}.")
        if len(text) > MAX_TEXT_CHARS:
            raise ShortcutError(
                f"The text is longer than {MAX_TEXT_CHARS:,} characters. Shorten it, or "
                "run the workflow from Raticode."
            )
        assert target.input is not None
        inputs[target.input] = text
    run = await run_workflow_payload(
        record.workflow_id,
        base,
        inputs=inputs,
        background=True,
        started_by="manual",
    )
    return {
        "run": {
            "workflowId": record.workflow_id,
            "workflowName": target.workflow_name,
            "projectRoot": target.project_root,
            "runId": run.get("runId") or run.get("id") or "",
            "status": run.get("status") or "running",
        }
    }


def changes_payload(base: Path, shortcut_id: str) -> dict[str, Any]:
    """Review changes: each file that differs from the approved version, as a diff."""
    return {"changes": shortcut_changes(base, shortcut_id)}


def approve_payload(base: Path, shortcut_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Approve this version. ``fingerprint`` is the version the user reviewed.

    ``acceptUnseen`` is the user's second confirmation for files whose change Review
    changes couldn't show in full; without it those refuse with ``UnseenChanges``.
    """
    fingerprint = body.get("fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ShortcutError("Open the changes before approving them.")
    accept_unseen = body.get("acceptUnseen", False)
    if not isinstance(accept_unseen, bool):
        raise ShortcutError("acceptUnseen must be true or false.")
    record = approve_shortcut(
        base,
        shortcut_id,
        actor="user",
        by="desktop",
        expected_fingerprint=fingerprint,
        accept_unseen=accept_unseen,
    )
    return detail_payload(base, record.id)


def approve_after_studio_save(base: Path, workflow_id: str) -> int:
    """Saving a workflow in the Studio window approves the file it wrote for its shortcut.

    The save already happened, so a problem here is logged rather than failing it; the
    shortcut then asks for review as it would after any other edit.
    """
    try:
        if not (base / shortcuts.SHORTCUTS_FILENAME).exists():
            return 0
        return shortcuts.approve_after_studio_save(base, workflow_id)
    except Exception:  # noqa: BLE001 - see above
        log.exception("Could not approve the saved workflow %s for its shortcut", workflow_id)
        return 0
