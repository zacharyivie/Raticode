"""Studio API for Rattish workflow triggers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gofer.rattish.trigger_login import login_status, set_login_enabled
from gofer.rattish.trigger_service import scheduler_status
from gofer.rattish.triggers import (
    TriggerError,
    TriggerRecord,
    TriggerStore,
    describe_trigger,
    next_run_at,
    preview_cron,
    system_timezone,
    webhook_url,
)
from gofer.rattish.workspaces import RattishWorkspaceError, find_registered_workflow

_UPDATABLE_FIELDS = ("config", "label", "overlap", "missed", "inputs", "enabled")


def _entrypoint(workflow_id: Any, base: Path) -> Path:
    if not isinstance(workflow_id, str) or not workflow_id:
        raise TriggerError("Choose a workflow.")
    try:
        return find_registered_workflow(workflow_id, registry_dir=base).entrypoint
    except RattishWorkspaceError as exc:
        raise TriggerError(str(exc)) from None


def _trigger_payload(store: TriggerStore, record: TriggerRecord) -> dict[str, Any]:
    latest = store.latest_event(record.id)
    return {
        **record.to_payload(),
        "description": describe_trigger(record),
        "nextRunAt": next_run_at(record),
        "webhookUrl": webhook_url(record.id, store.webhook_port())
        if record.kind == "webhook"
        else None,
        "latestEvent": latest.to_payload() if latest else None,
    }


def triggers_payload(base: Path, workflow_id: Any) -> dict[str, Any]:
    """Everything the Triggers section shows for one workflow."""
    store = TriggerStore(base)
    source = _entrypoint(workflow_id, base)
    return {
        "triggers": [_trigger_payload(store, item) for item in store.list_triggers(source)],
        "events": [item.to_payload() for item in store.events(source_path=source, limit=30)],
        "scheduler": scheduler_status(store),
        "login": login_status(base),
        "defaultTimezone": system_timezone(),
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
    )
    return {"trigger": _trigger_payload(store, record), "token": token}


def update_trigger_payload(base: Path, trigger_id: str, body: dict[str, Any]) -> dict[str, Any]:
    store = TriggerStore(base)
    changes = {name: body[name] for name in _UPDATABLE_FIELDS if name in body}
    if "config" in changes:
        changes["config"] = _config(body)
    if "enabled" in changes and not isinstance(changes["enabled"], bool):
        raise TriggerError("Enabled must be true or false.")
    return {"trigger": _trigger_payload(store, store.update(trigger_id, **changes))}


def delete_trigger_payload(base: Path, trigger_id: str) -> dict[str, Any]:
    TriggerStore(base).delete(trigger_id)
    return {"deleted": True, "id": trigger_id}


def rotate_trigger_token_payload(base: Path, trigger_id: str) -> dict[str, Any]:
    return {"token": TriggerStore(base).rotate_token(trigger_id)}


def preview_trigger_payload(body: dict[str, Any]) -> dict[str, Any]:
    timezone = str(body.get("timezone") or "") or system_timezone()
    return {"nextRuns": preview_cron(str(body.get("expression") or ""), timezone)}


def set_start_at_login_payload(base: Path, body: dict[str, Any]) -> dict[str, Any]:
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise TriggerError("Enabled must be true or false.")
    return {"login": set_login_enabled(base, enabled)}


def _config(body: dict[str, Any]) -> dict[str, Any]:
    config = body.get("config", {})
    if not isinstance(config, dict):
        raise TriggerError("Trigger settings must be an object.")
    return config
