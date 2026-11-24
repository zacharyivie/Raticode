"""Workflow shortcuts: a system-wide key combination that runs one workflow.

The user records a key in a workflow's menu (**Add shortcut…**) and the desktop app
registers it while Raticode runs (``frontend/electron/workflow-shortcuts.cjs``).
Pressing it runs the workflow as a run the user started. A workflow with a plain text
input opens a small Run sheet filled from the clipboard instead; that input is the
shortcut's ``input``.

Shortcuts belong to this computer: they live in ``workflow-shortcuts.json`` in the data
folder, shared by the app and ``Raticode rattish shortcuts``. A binding uses the
Settings > Keybindings format (``Ctrl+Alt+KeyR``, with ``Mod`` meaning Command on a Mac
and Ctrl elsewhere). The desktop app also refuses a key the dictation shortcuts use,
since only it knows them.

A shortcut runs only the workflow version the user approved, as a trigger does
(``trigger_approval``). Adding one approves the workflow as it is then; changing its key
or input keeps the approval it had. A press compares the fingerprint of the workflow and
the files it runs as code with the approved one and refuses to start a changed version;
the user reviews the change and approves it (``approve_shortcut``). Saving the workflow
in Studio approves only the workflow file it wrote (``approve_after_studio_save``). The
approved snapshot, which holds the text Review changes compares against, is kept per
shortcut in ``workflow-shortcut-approvals/``.
"""

from __future__ import annotations

import json
import re
import secrets
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gofer.rattish.trigger_approval import (
    Snapshot,
    approved_after_studio_save,
    describe_changes,
    take_snapshot,
    unseen_message,
)

SHORTCUTS_FILENAME = "workflow-shortcuts.json"
APPROVALS_DIRNAME = "workflow-shortcut-approvals"
# A snapshot keeps at most a million characters of text, which JSON may escape.
MAX_APPROVAL_BYTES = 16 * 1024 * 1024
# Who approved a shortcut's workflow version, as Review changes says it.
APPROVED_BY = ("desktop", "studio", "cli")
USER_ONLY = (
    "Only you can approve the workflow version a shortcut runs. Approve it in Settings > "
    "Devices, or with Raticode rattish shortcuts approve from your own terminal."
)
MAX_SHORTCUTS = 50
MAX_FILE_BYTES = 256 * 1024
# The text a Run sheet sends. Clipboards can hold far more; the sheet keeps the start.
MAX_TEXT_CHARS = 100_000
_ID = re.compile(r"[a-f0-9]{12}")
_FINGERPRINT = re.compile(r"sha256:[a-f0-9]{64}")
_MODIFIER_ORDER = ("Mod", "Ctrl", "Alt", "Shift", "Meta")
_NAMED_KEYS = frozenset(
    {
        "Space",
        "Enter",
        "Tab",
        "Backspace",
        "Delete",
        "Insert",
        "Home",
        "End",
        "PageUp",
        "PageDown",
        "ArrowUp",
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "Backquote",
        "Minus",
        "Equal",
        "BracketLeft",
        "BracketRight",
        "Backslash",
        "Semicolon",
        "Quote",
        "Comma",
        "Period",
        "Slash",
    }
)
_KEY = re.compile(r"Key[A-Z]|Digit[0-9]|Numpad[0-9]|F(?:[1-9]|1[0-9]|2[0-4])")
_FUNCTION_KEY = re.compile(r"F(?:[1-9]|1[0-9]|2[0-4])")

_lock = threading.Lock()
# save_approval's "replace whatever is approved now".
_ANY: Any = object()


class ShortcutError(ValueError):
    """A request the store can't accept, with a message for the user."""


class ShortcutNotFound(ShortcutError):
    """No shortcut has this ID."""


class ShortcutConflict(ShortcutError):
    """Another workflow's shortcut already uses this key."""


class ShortcutChanged(ShortcutError):
    """The workflow differs from the version approved for its shortcut, so it didn't run."""

    code = "shortcut_changed"

    def __init__(self, shortcut_id: str, workflow_name: str, state: str) -> None:
        self.shortcut_id = shortcut_id
        self.workflow_name = workflow_name
        self.state = state
        super().__init__(changed_title(workflow_name, state))


class ApprovalConflict(ShortcutError):
    """The workflow changed again after the user opened its changes."""


class UnseenChanges(ShortcutError):
    """Part of the change wasn't shown in full and the user hasn't confirmed it.

    ``files`` names each such file and why (see ``trigger_approval.unseen_files``).
    """

    def __init__(self, files: list[dict[str, Any]]) -> None:
        self.files = files
        super().__init__(unseen_message(files))


def changed_title(workflow_name: str, state: str) -> str:
    """The desktop notice's words when a press finds the workflow changed."""
    name = workflow_name or "The workflow"
    if state == "unapproved":
        return f"{name} needs your approval before its shortcut runs it"
    return f"{name} changed since you added its shortcut"


@dataclass(frozen=True)
class WorkflowShortcut:
    id: str
    workflow_id: str
    binding: str
    input: str | None
    created_at: str
    # The approved version's fingerprint (``Snapshot.fingerprint``). None until one is
    # approved, as for a shortcut saved before shortcuts asked for approval.
    approved_fingerprint: str | None = None
    approved_at: str | None = None
    approved_by: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workflowId": self.workflow_id,
            "binding": self.binding,
            "input": self.input,
            "createdAt": self.created_at,
            "approvedFingerprint": self.approved_fingerprint,
            "approvedAt": self.approved_at,
            "approvedBy": self.approved_by,
        }


def clean_binding(value: Any) -> str:
    """The binding in its stored form, or ShortcutError saying why it can't be one.

    The rules match the desktop app's (``toAccelerator``): one combination, not a
    sequence, with Ctrl, Alt or the system key unless it is a function key.
    """
    text = str(value or "").strip()
    if not text:
        raise ShortcutError("Record a key combination for the shortcut.")
    if re.search(r"\s", text):
        raise ShortcutError(
            "A shortcut used in every app must be a single key combination, not a sequence."
        )
    parts = [part for part in text.split("+") if part]
    if not parts:
        raise ShortcutError("Record a key combination for the shortcut.")
    key = parts.pop()
    modifiers = set()
    for part in parts:
        if part not in _MODIFIER_ORDER:
            raise ShortcutError(f'Unknown modifier "{part[:20]}".')
        modifiers.add(part)
    if key not in _NAMED_KEYS and not _KEY.fullmatch(key):
        raise ShortcutError(f'The key "{key[:30]}" can\'t be used for a shortcut in every app.')
    if not modifiers - {"Shift"} and not _FUNCTION_KEY.fullmatch(key):
        raise ShortcutError(
            "Add Ctrl, Alt or another modifier, so the shortcut doesn't take a key you "
            "type with in other apps."
        )
    return "+".join([*(name for name in _MODIFIER_ORDER if name in modifiers), key])


def binding_identity(binding: str, platform: str | None = None) -> str:
    """The keys a binding presses on this computer, so two spellings compare equal."""
    mac = (platform or sys.platform) == "darwin"
    parts = binding.split("+")
    key = parts.pop()
    modifiers = sorted({("Meta" if mac else "Ctrl") if part == "Mod" else part for part in parts})
    return "+".join([*modifiers, key])


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _record(item: Any) -> WorkflowShortcut | None:
    if not isinstance(item, dict):
        return None
    shortcut_id = item.get("id")
    workflow_id = item.get("workflowId")
    if not isinstance(shortcut_id, str) or not _ID.fullmatch(shortcut_id):
        return None
    if not isinstance(workflow_id, str) or not workflow_id or len(workflow_id) > 200:
        return None
    try:
        binding = clean_binding(item.get("binding"))
    except ShortcutError:
        return None
    chosen = item.get("input")
    fingerprint = item.get("approvedFingerprint")
    if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint):
        fingerprint = None
    approved_by = item.get("approvedBy")
    approved_at = item.get("approvedAt")
    return WorkflowShortcut(
        id=shortcut_id,
        workflow_id=workflow_id,
        binding=binding,
        input=chosen if isinstance(chosen, str) and chosen and len(chosen) <= 200 else None,
        created_at=str(item.get("createdAt") or ""),
        approved_fingerprint=fingerprint,
        approved_at=str(approved_at)[:40] if fingerprint and approved_at else None,
        approved_by=approved_by if fingerprint and approved_by in APPROVED_BY else None,
    )


class ShortcutStore:
    """The computer's workflow shortcuts, in a private JSON file in the data folder."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.path = self.data_dir / SHORTCUTS_FILENAME

    def entries(self) -> list[WorkflowShortcut]:
        """Every saved shortcut, oldest first. Unreadable entries are left out."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise ShortcutError(f"Could not read the workflow shortcuts: {exc}") from exc
        if len(raw) > MAX_FILE_BYTES:
            raise ShortcutError("The workflow shortcuts file is too large to read.")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ShortcutError("The workflow shortcuts file isn't valid JSON.") from exc
        items = document.get("shortcuts") if isinstance(document, dict) else None
        records = [_record(item) for item in items or [] if item]
        return [record for record in records if record is not None][:MAX_SHORTCUTS]

    def get(self, shortcut_id: str) -> WorkflowShortcut:
        for record in self.entries():
            if record.id == shortcut_id:
                return record
        raise ShortcutNotFound(f"No workflow shortcut has the ID {shortcut_id[:40]!r}.")

    def for_workflow(self, workflow_id: str) -> WorkflowShortcut | None:
        wanted = workflow_id.lower()
        return next((item for item in self.entries() if item.workflow_id.lower() == wanted), None)

    @contextmanager
    def _editing(self) -> Iterator[list[WorkflowShortcut]]:
        with _lock:
            records = self.entries()
            yield records
            self._write(records)

    def _write(self, records: list[WorkflowShortcut]) -> None:
        from gofer.utils.atomic_output import atomic_binary_output

        document = {"version": 1, "shortcuts": [record.to_payload() for record in records]}
        content = json.dumps(document, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        try:
            with atomic_binary_output(self.path) as output:
                output.write(content)
        except OSError as exc:
            raise ShortcutError(f"Could not save the workflow shortcuts: {exc}") from exc

    def set(
        self,
        workflow_id: str,
        binding: Any,
        input_name: str | None,
        *,
        approval: Snapshot | None = None,
        by: str = "desktop",
    ) -> WorkflowShortcut:
        """Gives a workflow its shortcut, replacing the key it had. One per workflow.

        A new shortcut approves ``approval``, the workflow as the user saw it when adding
        the shortcut. Changing an existing shortcut's key or input keeps the approval it
        had, so a change waiting for review keeps waiting.
        """
        clean = clean_binding(binding)
        identity = binding_identity(clean)
        with self._editing() as records:
            existing = next(
                (item for item in records if item.workflow_id.lower() == workflow_id.lower()),
                None,
            )
            for item in records:
                if item is not existing and binding_identity(item.binding) == identity:
                    raise ShortcutConflict(
                        "Another workflow's shortcut already uses this key. Choose a "
                        "different one, or remove that shortcut in Settings > Devices."
                    )
            if existing is None and len(records) >= MAX_SHORTCUTS:
                raise ShortcutError(
                    f"Raticode keeps at most {MAX_SHORTCUTS} workflow shortcuts. Remove one "
                    "in Settings > Devices first."
                )
            if existing is not None:
                record = replace(existing, binding=clean, input=input_name)
                records[records.index(existing)] = record
            else:
                record = WorkflowShortcut(
                    id=secrets.token_hex(6),
                    workflow_id=workflow_id,
                    binding=clean,
                    input=input_name,
                    created_at=_now(),
                )
                if approval is not None:
                    self._write_snapshot(record.id, approval)
                    record = replace(
                        record,
                        approved_fingerprint=approval.fingerprint,
                        approved_at=record.created_at,
                        approved_by=_by(by),
                    )
                records.append(record)
        return record

    def remove(self, shortcut_id: str) -> WorkflowShortcut:
        with self._editing() as records:
            found = next((item for item in records if item.id == shortcut_id), None)
            if found is None:
                raise ShortcutNotFound(f"No workflow shortcut has the ID {shortcut_id[:40]!r}.")
            records.remove(found)
        self._snapshot_path(found.id).unlink(missing_ok=True)
        return found

    # --- The approved version ---------------------------------------------------------

    def _snapshot_path(self, shortcut_id: str) -> Path:
        if not _ID.fullmatch(shortcut_id):
            raise ShortcutNotFound(f"No workflow shortcut has the ID {shortcut_id[:40]!r}.")
        return self.data_dir / APPROVALS_DIRNAME / f"{shortcut_id}.json"

    def _write_snapshot(self, shortcut_id: str, snapshot: Snapshot) -> None:
        from gofer.utils.atomic_output import atomic_binary_output

        path = self._snapshot_path(shortcut_id)
        content = json.dumps(snapshot.to_json(), sort_keys=True).encode("utf-8")
        try:
            path.parent.mkdir(mode=0o700, exist_ok=True)
            with atomic_binary_output(path) as output:
                output.write(content)
        except OSError as exc:
            raise ShortcutError(f"Could not save the approved workflow version: {exc}") from exc

    def approved_snapshot(self, record: WorkflowShortcut) -> Snapshot | None:
        """The snapshot ``record`` approved, or None when it has none that matches.

        A snapshot whose fingerprint isn't the record's (an older file, or one edited by
        hand) is ignored: Review changes then shows every file as new.
        """
        if record.approved_fingerprint is None:
            return None
        try:
            raw = self._snapshot_path(record.id).read_bytes()
        except OSError:
            return None
        if len(raw) > MAX_APPROVAL_BYTES:
            return None
        try:
            snapshot = Snapshot.from_json(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if snapshot is None or snapshot.fingerprint != record.approved_fingerprint:
            return None
        return snapshot

    def save_approval(
        self,
        shortcut_id: str,
        snapshot: Snapshot,
        *,
        by: str,
        replacing: object = _ANY,
    ) -> WorkflowShortcut:
        """Approve ``snapshot`` as the version this shortcut runs.

        ``replacing`` is the approved fingerprint (or None) this approval replaces, so two
        approvals at once can't overwrite each other unseen. Who may approve is the
        caller's to check.
        """
        with self._editing() as records:
            found = next((item for item in records if item.id == shortcut_id), None)
            if found is None:
                raise ShortcutNotFound(f"No workflow shortcut has the ID {shortcut_id[:40]!r}.")
            if replacing is not _ANY and found.approved_fingerprint != replacing:
                raise ApprovalConflict(
                    "The shortcut's approval changed while you were approving. Review the "
                    "changes again."
                )
            self._write_snapshot(found.id, snapshot)
            record = replace(
                found,
                approved_fingerprint=snapshot.fingerprint,
                approved_at=_now(),
                approved_by=_by(by),
            )
            records[records.index(found)] = record
        return record


def _by(value: str) -> str:
    return value if value in APPROVED_BY else "desktop"


# --- What pressing a shortcut does ------------------------------------------------------


@dataclass(frozen=True)
class ShortcutTarget:
    """What a shortcut does now: ``run`` straight away, open the Run sheet (``text``),
    or nothing (``unavailable``, with ``problem`` saying why)."""

    mode: str
    workflow_name: str
    project_root: str
    source_path: str
    input: str | None = None
    input_label: str = ""
    text_inputs: tuple[dict[str, str], ...] = ()
    problem: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "workflowName": self.workflow_name,
            "projectRoot": self.project_root,
            "sourcePath": self.source_path,
            "input": self.input,
            "inputLabel": self.input_label,
            "textInputs": list(self.text_inputs),
            "problem": self.problem,
        }


NOT_OPEN = (
    "This workflow isn't in an open project any more. Open its project, or remove the shortcut."
)
HAS_ERRORS = "This workflow has errors. Open it in Raticode to fix them."


def plan_target(ir: dict[str, Any] | None, chosen: str | None) -> dict[str, Any]:
    """The mode for a compiled workflow and the input chosen for clipboard text.

    A workflow with a plain text input opens the Run sheet: ``chosen`` when it is still
    one, else the first required one, else the first. Any other input the run needs
    without a default can't be filled from a shortcut. Returns mode, input, inputLabel,
    textInputs and problem.
    """
    from gofer.devices.workflows import _input_fields

    if ir is None:
        return {"mode": "unavailable", "problem": HAS_ERRORS, "textInputs": []}
    fields = _input_fields(ir)
    text = [field for field in fields if field["kind"] == "text" and not field["secret"]]
    picked = next((field for field in text if field["name"] == chosen), None)
    if picked is None and text:
        picked = next((field for field in text if field["required"]), text[0])
    needed = [
        field["name"]
        for field in fields
        if field["required"]
        and not field["has_default"]
        and (picked is None or field["name"] != picked["name"])
    ]
    text_inputs = [{"name": field["name"], "label": field["label"]} for field in text]
    if needed:
        listed = ", ".join(needed[:5]) + (" and more" if len(needed) > 5 else "")
        return {
            "mode": "unavailable",
            "problem": (
                f"This workflow needs {listed}, which a shortcut can't fill. Run it from "
                "Raticode instead."
            ),
            "textInputs": text_inputs,
        }
    if picked is None:
        return {"mode": "run", "textInputs": text_inputs}
    return {
        "mode": "text",
        "input": picked["name"],
        "inputLabel": picked["label"],
        "textInputs": text_inputs,
    }


def resolve_target(data_dir: Path, workflow_id: str, chosen: str | None) -> ShortcutTarget:
    """Compiles the workflow as it is now and says what its shortcut does."""
    from gofer.devices.workflows import _compile
    from gofer.rattish.workspaces import RattishWorkspaceError, find_registered_workflow

    try:
        registered = find_registered_workflow(workflow_id, registry_dir=data_dir)
    except RattishWorkspaceError:
        return ShortcutTarget("unavailable", workflow_id, "", "", problem=NOT_OPEN)
    plan = plan_target(_compile(registered, data_dir), chosen)
    return ShortcutTarget(
        mode=plan["mode"],
        workflow_name=registered.name,
        project_root=str(registered.project_root),
        source_path=str(registered.entrypoint),
        input=plan.get("input"),
        input_label=plan.get("inputLabel", ""),
        text_inputs=tuple(plan.get("textInputs", ())),
        problem=plan.get("problem", ""),
    )


def describe(target: ShortcutTarget) -> str:
    """One line saying what pressing the shortcut does."""
    if target.mode == "run":
        return "Runs the workflow straight away."
    if target.mode == "text":
        return f"Opens the Run sheet with the clipboard in {target.input_label or target.input}."
    return target.problem


# --- Which version a shortcut may run ---------------------------------------------------


@dataclass(frozen=True)
class ShortcutApproval:
    """Whether a shortcut may run its workflow as it is now.

    ``state`` is ``approved``, ``changed`` (it differs from the approved version),
    ``unapproved`` (no version was approved, as for a shortcut added before shortcuts
    asked for approval) or ``missing`` (the workflow file is gone, which the run itself
    reports).
    """

    state: str
    record: WorkflowShortcut
    current: Snapshot
    approved: Snapshot | None
    project_root: Path | None
    workflow_name: str

    @property
    def runnable(self) -> bool:
        return self.state in {"approved", "missing"}

    def to_payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "approvedFingerprint": self.record.approved_fingerprint,
            "approvedAt": self.record.approved_at,
            "approvedBy": self.record.approved_by,
            "fingerprint": self.current.fingerprint,
        }


def _registered(data_dir: Path, workflow_id: str) -> Any | None:
    from gofer.rattish.workspaces import RattishWorkspaceError, find_registered_workflow

    try:
        return find_registered_workflow(workflow_id, registry_dir=data_dir)
    except RattishWorkspaceError:
        return None


def current_snapshot(data_dir: Path, workflow_id: str) -> tuple[Snapshot, Any] | None:
    """The workflow and the files it runs as code, read now, or None when it isn't open."""
    registered = _registered(data_dir, workflow_id)
    if registered is None:
        return None
    return take_snapshot(registered.entrypoint, data_dir), registered


def approval_status(
    data_dir: Path, record: WorkflowShortcut, *, registered: Any | None = None
) -> ShortcutApproval | None:
    """The shortcut's approval state, or None when its workflow isn't in an open project."""
    store = ShortcutStore(data_dir)
    if registered is None:
        registered = _registered(data_dir, record.workflow_id)
    if registered is None:
        return None
    approved = store.approved_snapshot(record)
    current = take_snapshot(
        registered.entrypoint, data_dir, known=approved.files if approved else ()
    )
    if current.entry_missing():
        state = "missing"
    elif record.approved_fingerprint is None:
        state = "unapproved"
    elif record.approved_fingerprint != current.fingerprint:
        state = "changed"
    else:
        state = "approved"
    return ShortcutApproval(
        state=state,
        record=record,
        current=current,
        approved=approved,
        project_root=Path(registered.project_root),
        workflow_name=current.workflow_name or registered.name,
    )


def require_approved(data_dir: Path, record: WorkflowShortcut, registered: Any) -> None:
    """Raises ShortcutChanged unless the workflow is the version approved for ``record``."""
    status = approval_status(data_dir, record, registered=registered)
    if status is not None and not status.runnable:
        raise ShortcutChanged(record.id, status.workflow_name, status.state)


def shortcut_changes(data_dir: Path, shortcut_id: str) -> dict[str, Any]:
    """What changed since the shortcut's workflow version was approved, file by file.

    ``unseen`` lists the files whose change this doesn't show in full: Approve this
    version asks about those a second time.
    """
    record = ShortcutStore(data_dir).get(shortcut_id)
    status = approval_status(data_dir, record)
    if status is None:
        raise ShortcutError(NOT_OPEN)
    if status.state == "approved":
        changes: dict[str, Any] = {
            "approvedFingerprint": record.approved_fingerprint,
            "fingerprint": status.current.fingerprint,
            "files": [],
            "truncated": False,
            "unseen": [],
        }
    else:
        changes = describe_changes(
            status.approved, status.current, project_root=status.project_root
        )
    return {
        "shortcutId": record.id,
        "workflowId": record.workflow_id,
        "workflowName": status.workflow_name,
        **status.to_payload(),
        **changes,
    }


def approve_shortcut(
    data_dir: Path,
    shortcut_id: str,
    *,
    actor: str,
    by: str,
    expected_fingerprint: str | None = None,
    accept_unseen: bool = False,
) -> WorkflowShortcut:
    """The user's Approve this version: the shortcut runs the workflow as it is now.

    The same rules as ``triggers.approve_trigger``. ``expected_fingerprint`` is the
    version the user reviewed: when the workflow changed again since, nothing is approved
    and ``ApprovalConflict`` asks them to look again. When a file's change wasn't shown
    in full (its diff was cut, or it is binary or too large), nothing is approved unless
    ``accept_unseen`` says the user confirmed that a second time; ``UnseenChanges`` names
    those files. Only ``actor="user"`` approves.
    """
    if actor != "user":
        raise ShortcutError(USER_ONLY)
    store = ShortcutStore(data_dir)
    record = store.get(shortcut_id)
    status = approval_status(data_dir, record)
    if status is None:
        raise ShortcutError(NOT_OPEN)
    current = status.current
    if expected_fingerprint is not None and expected_fingerprint != current.fingerprint:
        raise ApprovalConflict(
            "The workflow changed again since you opened its changes. Review them again."
        )
    if status.state == "approved":
        return record
    if not accept_unseen:
        changes = describe_changes(status.approved, current, project_root=status.project_root)
        unseen = list(changes.get("unseen") or [])
        if unseen:
            raise UnseenChanges(unseen)
    return store.save_approval(record.id, current, by=by, replacing=record.approved_fingerprint)


def approve_after_studio_save(data_dir: Path, workflow_id: str) -> int:
    """Saving the workflow in Studio approves the file it wrote for its shortcut.

    Only the workflow file's new text is approved (``approved_after_studio_save``, the
    rule triggers use): a prompt, script or child workflow that changed since its last
    approval keeps waiting for review. A shortcut still waiting for its first approval
    gets none here. Returns how many shortcuts were approved.
    """
    store = ShortcutStore(data_dir)
    record = store.for_workflow(workflow_id)
    if record is None or record.approved_fingerprint is None:
        return 0
    approved = store.approved_snapshot(record)
    registered = _registered(data_dir, record.workflow_id)
    if approved is None or registered is None:
        return 0
    source = Path(registered.entrypoint)
    current = take_snapshot(source, data_dir, known=approved.files)
    merged = approved_after_studio_save(approved, current, source)
    if merged.fingerprint == approved.fingerprint:
        return 0
    try:
        store.save_approval(record.id, merged, by="studio", replacing=record.approved_fingerprint)
    except (ShortcutNotFound, ApprovalConflict):
        return 0
    return 1
