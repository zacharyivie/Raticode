"""Studio API for Rattish workflow triggers.

Every request here comes from the user: agent tokens can't reach these routes
(``gofer.ui.headless.agent_request_refusal``). Adding a trigger approves the workflow as
it is; changing or turning one on approves nothing, and the Triggers section opens Review
changes when a change is waiting. Approve this version approves the change the user
reviewed, and needs ``acceptUnseen`` when part of it wasn't shown in full.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gofer.rattish.trigger_approval import Snapshot
from gofer.rattish.trigger_login import login_status, set_login_enabled
from gofer.rattish.trigger_service import scheduler_status
from gofer.rattish.triggers import (
    TriggerError,
    TriggerRecord,
    TriggerStore,
    approval_status,
    approve_source_triggers,
    approve_trigger,
    computer_limit_state,
    cost_report_warnings,
    cron_summary,
    describe_cron,
    describe_trigger,
    model_notices,
    next_run_at,
    preview_cron,
    system_timezone,
    trigger_changes,
    trigger_limit_state,
    unsandboxed_steps,
    webhook_url,
    widened_steps,
)
from gofer.rattish.workspaces import RattishWorkspaceError, find_registered_workflow
from gofer.utils.logging import get_logger

log = get_logger(__name__)

_UPDATABLE_FIELDS = ("config", "label", "overlap", "missed", "inputs", "enabled")
_DAILY_FIELDS = {"dailyMaxCostUsd": "daily_max_cost_usd", "dailyMaxTokens": "daily_max_tokens"}
# The request field for TriggerStore's run_unapproved.
_RUN_UNAPPROVED = "runEditsWithoutApproval"


def _entrypoint(workflow_id: Any, base: Path) -> Path:
    if not isinstance(workflow_id, str) or not workflow_id:
        raise TriggerError("Choose a workflow.")
    try:
        return find_registered_workflow(workflow_id, registry_dir=base).entrypoint
    except RattishWorkspaceError as exc:
        raise TriggerError(str(exc)) from None


def _trigger_payload(
    store: TriggerStore, record: TriggerRecord, snapshots: dict[str, Snapshot] | None = None
) -> dict[str, Any]:
    latest = store.latest_event(record.id)
    return {
        **record.to_payload(),
        "description": describe_trigger(record),
        "summary": cron_summary(record),
        "nextRunAt": next_run_at(record),
        "webhookUrl": webhook_url(record.id, store.webhook_port())
        if record.kind == "webhook"
        else None,
        "latestEvent": latest.to_payload() if latest else None,
        "dailyLimit": _state_payload(trigger_limit_state(store, record)),
        "approval": approval_status(store, record, snapshots=snapshots).to_payload(),
    }


def _state_payload(state: Any) -> dict[str, Any] | None:
    return state.to_payload() if state is not None else None


def daily_budget_payload(base: Path) -> dict[str, Any]:
    """The computer-wide daily limit for triggered runs and how much of today it has used."""
    store = TriggerStore(base)
    budget = store.daily_budget()
    return {
        "budget": budget.to_payload(),
        "today": _state_payload(computer_limit_state(store, budget=budget)),
        "timezone": system_timezone(),
    }


def set_daily_budget_payload(base: Path, body: dict[str, Any]) -> dict[str, Any]:
    """Set or clear the computer-wide daily limit. null or 0 removes a part of it."""
    store = TriggerStore(base)
    changes = {
        target: body[name]
        for name, target in (("maxCostUsd", "max_cost_usd"), ("maxTokens", "max_tokens"))
        if name in body
    }
    if not changes:
        raise TriggerError("Send maxCostUsd, maxTokens or both. Use null to remove a limit.")
    budget = store.set_daily_budget(**changes)
    warnings: list[str] = []
    if budget.max_cost_usd is not None:
        seen: set[str] = set()
        for record in store.list_triggers():
            if record.enabled and record.source_path not in seen:
                seen.add(record.source_path)
                warnings.extend(cost_report_warnings(record.source_path, base))
    return {**daily_budget_payload(base), "warnings": warnings}


def _daily_changes(body: dict[str, Any]) -> dict[str, Any]:
    return {target: body[name] for name, target in _DAILY_FIELDS.items() if name in body}


def _time_limit_change(body: dict[str, Any]) -> dict[str, Any]:
    """The Time limit in seconds; null or 0 is no limit, and leaving it out keeps it."""
    return {"max_run_seconds": body["maxRunSeconds"]} if "maxRunSeconds" in body else {}


def _usage_limit_change(body: dict[str, Any]) -> dict[str, Any]:
    """Wait for a usage limit to reset; leaving it out or null keeps it (on when new).

    An export made before the switch existed carries null for it.
    """
    if body.get("waitForUsageLimit") is None:
        return {}
    return {"wait_usage_limit": _flag(body["waitForUsageLimit"], "Wait for a usage limit to reset")}


def _save_warnings(base: Path, record: TriggerRecord) -> list[str]:
    """Warnings shown when a trigger with a daily cost limit is saved."""
    if record.daily_max_cost_usd is None:
        return []
    return cost_report_warnings(record.source_path, base)


def triggers_payload(base: Path, workflow_id: Any) -> dict[str, Any]:
    """Everything the Triggers section shows for one workflow."""
    store = TriggerStore(base)
    source = _entrypoint(workflow_id, base)
    snapshots: dict[str, Snapshot] = {}
    return {
        "triggers": [
            _trigger_payload(store, item, snapshots) for item in store.list_triggers(source)
        ],
        "events": [item.to_payload() for item in store.events(source_path=source, limit=30)],
        "scheduler": scheduler_status(store),
        "login": login_status(base),
        "defaultTimezone": system_timezone(),
        "dailyBudget": daily_budget_payload(base),
        # Agent steps a triggered run would start with no sandbox. Adding a
        # trigger asks you to confirm while this list isn't empty.
        # Steps whose own workflow settings let their shell reach beyond the project
        # (extra paths outside it, network: any). Adding a trigger asks about these too.
        "sandbox": {
            "unsandboxed": unsandboxed_steps(source, base),
            "widened": widened_steps(source, base),
        },
        # Agent steps whose model is deprecated (a warning with its retirement date) or
        # already retired (preflight stops those runs).
        "models": {"notices": model_notices(source, base)},
    }


def create_trigger_payload(base: Path, body: dict[str, Any]) -> dict[str, Any]:
    store = TriggerStore(base)
    record, token = store.create(
        kind=str(body.get("kind") or ""),
        source_path=_entrypoint(body.get("workflowId"), base),
        config=_config(body),
        label=body.get("label", ""),
        overlap=body.get("overlap"),
        missed=body.get("missed"),
        inputs=body.get("inputs"),
        enabled=body.get("enabled", True) is not False,
        **_daily_changes(body),
        **_time_limit_change(body),
        **_usage_limit_change(body),
        run_unapproved=_flag(body.get(_RUN_UNAPPROVED, False), "Run edits without approval"),
        actor="user",
        approved_by="desktop",
    )
    return {
        "trigger": _trigger_payload(store, record),
        "token": token,
        "warnings": _save_warnings(base, record),
        "unsandboxed": unsandboxed_steps(record.source_path, base),
        "widened": widened_steps(record.source_path, base),
    }


def update_trigger_payload(base: Path, trigger_id: str, body: dict[str, Any]) -> dict[str, Any]:
    store = TriggerStore(base)
    changes = {name: body[name] for name in _UPDATABLE_FIELDS if name in body}
    if "config" in changes:
        changes["config"] = _config(body)
    if "enabled" in changes and not isinstance(changes["enabled"], bool):
        raise TriggerError("Enabled must be true or false.")
    if _RUN_UNAPPROVED in body:
        changes["run_unapproved"] = _flag(body[_RUN_UNAPPROVED], "Run edits without approval")
    daily = _daily_changes(body)
    record = store.update(
        trigger_id,
        **changes,
        **daily,
        **_time_limit_change(body),
        **_usage_limit_change(body),
        actor="user",
    )
    return {
        "trigger": _trigger_payload(store, record),
        "warnings": _save_warnings(base, record) if daily else [],
    }


def trigger_changes_payload(base: Path, trigger_id: str) -> dict[str, Any]:
    """Review changes: each file that differs from the approved version, as a diff."""
    return {"changes": trigger_changes(TriggerStore(base), trigger_id)}


def approve_trigger_payload(base: Path, trigger_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Approve this version. ``fingerprint`` is the version the user reviewed.

    ``acceptUnseen`` is the user's second confirmation for files whose change Review
    changes couldn't show in full; without it those refuse with ``UnseenChanges``.
    """
    fingerprint = body.get("fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        raise TriggerError("Open the changes before approving them.")
    accept_unseen = _flag(body.get("acceptUnseen", False), "acceptUnseen")
    store = TriggerStore(base)
    record = approve_trigger(
        store,
        trigger_id,
        actor="user",
        by="desktop",
        expected_fingerprint=fingerprint,
        accept_unseen=accept_unseen,
    )
    return {"trigger": _trigger_payload(store, record)}


def approve_after_studio_save(base: Path, workflow_id: str) -> int:
    """Saving a workflow in the Studio window approves the file it wrote for its triggers.

    Only the workflow file's new text is approved; other files it runs keep the version
    approved before (``approve_source_triggers``).

    The save already happened, so a problem here is logged rather than failing it; the
    trigger then shows Review changes as it would for any other edit.
    """
    try:
        store = TriggerStore(base)
        if not store.path.exists():
            return 0
        return approve_source_triggers(store, _entrypoint(workflow_id, base), by="studio")
    except Exception:  # noqa: BLE001 - see above
        log.exception("Could not approve the saved workflow %s for its triggers", workflow_id)
        return 0


def delete_trigger_payload(base: Path, trigger_id: str) -> dict[str, Any]:
    TriggerStore(base).delete(trigger_id)
    return {"deleted": True, "id": trigger_id}


def rotate_trigger_token_payload(base: Path, trigger_id: str) -> dict[str, Any]:
    return {"token": TriggerStore(base).rotate_token(trigger_id)}


def preview_trigger_payload(body: dict[str, Any]) -> dict[str, Any]:
    """The next few times a cron expression runs, and the expression in words.

    ``summary`` is "" for an unusual expression, which screens show by itself.
    """
    timezone = str(body.get("timezone") or "") or system_timezone()
    expression = str(body.get("expression") or "")
    return {
        "nextRuns": preview_cron(expression, timezone),
        "summary": describe_cron(expression),
    }


def trigger_pause_payload(base: Path) -> dict[str, Any]:
    """Whether every trigger is paused, and how many triggers that covers."""
    store = TriggerStore(base)
    triggers = store.list_triggers()
    return {
        "pause": store.pause_state().to_payload(),
        "triggers": len(triggers),
        "enabled": sum(1 for item in triggers if item.enabled),
        "scheduler": scheduler_status(store),
        "dailyBudget": daily_budget_payload(base),
    }


def set_trigger_pause_payload(base: Path, body: dict[str, Any]) -> dict[str, Any]:
    """Pause or resume all triggers. Each trigger keeps its own on/off setting."""
    paused = body.get("paused")
    if not isinstance(paused, bool):
        raise TriggerError("Paused must be true or false.")
    TriggerStore(base).set_paused(paused)
    return trigger_pause_payload(base)


def set_start_at_login_payload(base: Path, body: dict[str, Any]) -> dict[str, Any]:
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise TriggerError("Enabled must be true or false.")
    return {"login": set_login_enabled(base, enabled)}


def _flag(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise TriggerError(f"{what} must be true or false.")
    return value


def _config(body: dict[str, Any]) -> dict[str, Any]:
    config = body.get("config", {})
    if not isinstance(config, dict):
        raise TriggerError("Trigger settings must be an object.")
    return config
