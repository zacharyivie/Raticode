"""`Raticode rattish triggers`: start workflows on a schedule, a file change or a webhook."""

from __future__ import annotations

import json
import signal
import threading
import time
from pathlib import Path
from types import FrameType
from typing import Any

import typer

from gofer.rattish.trigger_login import LoginEntryError, login_status, set_login_enabled
from gofer.rattish.trigger_service import TriggerEngine, scheduler_status
from gofer.rattish.triggers import (
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_MISSED,
    DEFAULT_OVERLAP,
    RUNS_ONLY_WHILE_RUNNING_NOTICE,
    TriggerError,
    TriggerRecord,
    TriggerStore,
    describe_trigger,
    next_run_at,
    webhook_url,
)
from gofer.utils.paths import get_data_dir

app = typer.Typer(
    help="Start Rattish workflows on a schedule, a file change or a local webhook.",
    no_args_is_help=True,
)
add_app = typer.Typer(help="Add a trigger to a workflow.", no_args_is_help=True)
app.add_typer(add_app, name="add")

_DATA_DIR_OPTION = typer.Option(None, "--data-dir", help="Override the app data directory.")
_FORMAT_OPTION = typer.Option("text", "--format", help="Output format: text or json.")
_OVERLAP_OPTION = typer.Option(
    DEFAULT_OVERLAP,
    "--overlap",
    help="If the workflow is already running: skip, queue (one run after it), or parallel.",
)
_LABEL_OPTION = typer.Option("", "--label", help="A short name for this trigger.")
_INPUT_OPTION = typer.Option(
    None, "--input", help="Workflow input as NAME=JSON. Repeat for multiple inputs."
)
_DISABLED_OPTION = typer.Option(False, "--disabled", help="Save the trigger turned off.")


def _store(data_dir: Path | None) -> TriggerStore:
    return TriggerStore(data_dir or get_data_dir())


def _require_format(output_format: str) -> None:
    if output_format not in {"text", "json"}:
        typer.echo("--format must be text or json.", err=True)
        raise typer.Exit(2)


def _fail(error: Exception, output_format: str) -> typer.Exit:
    if output_format == "json":
        typer.echo(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
    else:
        typer.echo(str(error), err=True)
    return typer.Exit(1)


def _parse_inputs(values: list[str] | None) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for item in values or []:
        name, separator, raw = item.partition("=")
        if not separator or not name:
            raise TriggerError(f"Input {item!r} must look like NAME=JSON.")
        try:
            inputs[name] = json.loads(raw)
        except json.JSONDecodeError:
            raise TriggerError(f"Input {name!r} must be valid JSON.") from None
    return inputs


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


def _trigger_line(payload: dict[str, Any]) -> str:
    state = "on " if payload["enabled"] else "off"
    pieces = [
        payload["id"],
        f"{payload['kind']:<7}",
        state,
        payload["description"],
        f"overlap={payload['overlap']}",
    ]
    if payload["kind"] == "cron":
        pieces.append(f"missed={payload['missed']}")
    if payload["nextRunAt"]:
        pieces.append(f"next={payload['nextRunAt']}")
    latest = payload.get("latestEvent")
    if latest:
        pieces.append(f"last={latest['status']}")
    return "  ".join(pieces)


def _status_sentence(status: dict[str, Any]) -> str:
    if not status["running"]:
        return "Scheduler: not running. Nothing will fire until Raticode or its scheduler starts."
    owner = "the Raticode app" if status["owner"] == "app" else "the background scheduler"
    return f"Scheduler: running in {owner} (process {status['pid']})."


def _add(
    *,
    kind: str,
    source: Path,
    config: dict[str, Any],
    label: str,
    overlap: str,
    missed: str,
    input_: list[str] | None,
    disabled: bool,
    output_format: str,
    data_dir: Path | None,
) -> None:
    _require_format(output_format)
    store = _store(data_dir)
    try:
        record, token = store.create(
            kind=kind,
            source_path=source,
            config=config,
            label=label,
            overlap=overlap,
            missed=missed,
            inputs=_parse_inputs(input_),
            enabled=not disabled,
        )
    except TriggerError as exc:
        raise _fail(exc, output_format) from None
    payload = _trigger_payload(store, record)
    status = scheduler_status(store)
    if output_format == "json":
        typer.echo(
            json.dumps(
                {"ok": True, "trigger": payload, "token": token, "scheduler": status},
                sort_keys=True,
            )
        )
        return
    typer.echo(_trigger_line(payload))
    if token:
        typer.echo(f"URL:   {payload['webhookUrl']}")
        typer.echo(f"Token: {token}")
        typer.echo("Save this token now. It can't be shown again.")
        typer.echo(
            f"curl -X POST -H 'Authorization: Bearer <token>' -d '{{}}' {payload['webhookUrl']}"
        )
    typer.echo(_status_sentence(status))
    typer.echo(RUNS_ONLY_WHILE_RUNNING_NOTICE)


@add_app.command("cron")
def add_cron(
    source: Path = typer.Argument(..., help="Path to a workflow.rattish file."),
    cron: str = typer.Option(..., "--cron", help="Five fields: minute hour day month weekday."),
    timezone: str = typer.Option(
        "", "--timezone", help="IANA time zone. Defaults to this computer's zone."
    ),
    overlap: str = _OVERLAP_OPTION,
    missed: str = typer.Option(
        DEFAULT_MISSED,
        "--missed",
        help="When a run is missed: record it, or run_once to also catch up one run.",
    ),
    label: str = _LABEL_OPTION,
    input_: list[str] | None = _INPUT_OPTION,
    disabled: bool = _DISABLED_OPTION,
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Run a workflow on a cron schedule."""
    _add(
        kind="cron",
        source=source,
        config={"expression": cron, "timezone": timezone},
        label=label,
        overlap=overlap,
        missed=missed,
        input_=input_,
        disabled=disabled,
        output_format=output_format,
        data_dir=data_dir,
    )


@add_app.command("file")
def add_file(
    source: Path = typer.Argument(..., help="Path to a workflow.rattish file."),
    path: str = typer.Option(
        ..., "--path", help="File or folder to watch. Relative paths start at the project."
    ),
    debounce: float = typer.Option(
        DEFAULT_DEBOUNCE_SECONDS,
        "--debounce",
        help="Seconds the files must stay quiet before the workflow starts.",
    ),
    glob: str = typer.Option("*", "--glob", help="Only react to files matching this pattern."),
    recursive: bool = typer.Option(
        True, "--recursive/--no-recursive", help="Include subfolders of a watched folder."
    ),
    overlap: str = _OVERLAP_OPTION,
    label: str = _LABEL_OPTION,
    input_: list[str] | None = _INPUT_OPTION,
    disabled: bool = _DISABLED_OPTION,
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Run a workflow when files change, after they go quiet."""
    _add(
        kind="file",
        source=source,
        config={"path": path, "debounceSeconds": debounce, "glob": glob, "recursive": recursive},
        label=label,
        overlap=overlap,
        missed=DEFAULT_MISSED,
        input_=input_,
        disabled=disabled,
        output_format=output_format,
        data_dir=data_dir,
    )


@add_app.command("webhook")
def add_webhook(
    source: Path = typer.Argument(..., help="Path to a workflow.rattish file."),
    overlap: str = _OVERLAP_OPTION,
    label: str = _LABEL_OPTION,
    input_: list[str] | None = _INPUT_OPTION,
    disabled: bool = _DISABLED_OPTION,
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Run a workflow from an authenticated webhook on this computer."""
    _add(
        kind="webhook",
        source=source,
        config={},
        label=label,
        overlap=overlap,
        missed=DEFAULT_MISSED,
        input_=input_,
        disabled=disabled,
        output_format=output_format,
        data_dir=data_dir,
    )


@app.command("list")
def list_triggers(
    source: Path | None = typer.Argument(None, help="Only this workflow.rattish file."),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """List triggers and whether anything is running them."""
    _require_format(output_format)
    store = _store(data_dir)
    triggers = [_trigger_payload(store, item) for item in store.list_triggers(source)]
    status = scheduler_status(store)
    if output_format == "json":
        typer.echo(json.dumps({"triggers": triggers, "scheduler": status}, sort_keys=True))
        return
    if not triggers:
        typer.echo("No triggers.")
    for payload in triggers:
        typer.echo(_trigger_line(payload))
        typer.echo(f"    {payload['sourcePath']}")
    typer.echo(_status_sentence(status))
    typer.echo(RUNS_ONLY_WHILE_RUNNING_NOTICE)


@app.command("update")
def update(
    trigger_id: str = typer.Argument(..., help="Trigger ID."),
    cron: str | None = typer.Option(None, "--cron", help="New cron expression."),
    timezone: str | None = typer.Option(None, "--timezone", help="New IANA time zone."),
    path: str | None = typer.Option(None, "--path", help="New file or folder to watch."),
    debounce: float | None = typer.Option(None, "--debounce", help="New quiet time in seconds."),
    glob: str | None = typer.Option(None, "--glob", help="New file pattern."),
    recursive: bool | None = typer.Option(
        None, "--recursive/--no-recursive", help="Include subfolders."
    ),
    overlap: str | None = typer.Option(None, "--overlap", help="skip, queue, or parallel."),
    missed: str | None = typer.Option(None, "--missed", help="record or run_once."),
    label: str | None = typer.Option(None, "--label", help="New name."),
    input_: list[str] | None = _INPUT_OPTION,
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Change a trigger. Options you leave out stay the same."""
    _require_format(output_format)
    changes: dict[str, Any] = {
        key: value
        for key, value in {
            "expression": cron,
            "timezone": timezone,
            "path": path,
            "debounceSeconds": debounce,
            "glob": glob,
            "recursive": recursive,
        }.items()
        if value is not None
    }
    store = _store(data_dir)
    try:
        record = store.update(
            trigger_id,
            config=changes or None,
            label=label,
            overlap=overlap,
            missed=missed,
            inputs=_parse_inputs(input_) if input_ else None,
        )
    except TriggerError as exc:
        raise _fail(exc, output_format) from None
    _emit_one(store, record, output_format)


def _emit_one(store: TriggerStore, record: TriggerRecord, output_format: str) -> None:
    payload = _trigger_payload(store, record)
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "trigger": payload}, sort_keys=True))
    else:
        typer.echo(_trigger_line(payload))


def _set_enabled(trigger_id: str, enabled: bool, output_format: str, data_dir: Path | None) -> None:
    _require_format(output_format)
    store = _store(data_dir)
    try:
        record = store.update(trigger_id, enabled=enabled)
    except TriggerError as exc:
        raise _fail(exc, output_format) from None
    _emit_one(store, record, output_format)


@app.command("enable")
def enable(
    trigger_id: str = typer.Argument(..., help="Trigger ID."),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Turn a trigger on."""
    _set_enabled(trigger_id, True, output_format, data_dir)


@app.command("disable")
def disable(
    trigger_id: str = typer.Argument(..., help="Trigger ID."),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Turn a trigger off without deleting it."""
    _set_enabled(trigger_id, False, output_format, data_dir)


@app.command("remove")
def remove(
    trigger_id: str = typer.Argument(..., help="Trigger ID."),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Delete a trigger and its history."""
    _require_format(output_format)
    try:
        _store(data_dir).delete(trigger_id)
    except TriggerError as exc:
        raise _fail(exc, output_format) from None
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "removed": trigger_id}, sort_keys=True))
    else:
        typer.echo(f"Removed {trigger_id}.")


@app.command("rotate-token")
def rotate_token(
    trigger_id: str = typer.Argument(..., help="Webhook trigger ID."),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Replace a webhook's token. The old token stops working at once."""
    _require_format(output_format)
    try:
        token = _store(data_dir).rotate_token(trigger_id)
    except TriggerError as exc:
        raise _fail(exc, output_format) from None
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "token": token}, sort_keys=True))
    else:
        typer.echo(f"Token: {token}")
        typer.echo("Save this token now. It can't be shown again.")


@app.command("webhook-port")
def webhook_port(
    port: int | None = typer.Argument(None, help="New port. Leave out to show the current one."),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Show or change the local port webhooks listen on."""
    _require_format(output_format)
    store = _store(data_dir)
    try:
        if port is not None:
            store.set_webhook_port(port)
    except TriggerError as exc:
        raise _fail(exc, output_format) from None
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "port": store.webhook_port()}, sort_keys=True))
    else:
        typer.echo(str(store.webhook_port()))


@app.command("history")
def history(
    trigger_id: str | None = typer.Argument(None, help="Only this trigger."),
    limit: int = typer.Option(30, "--limit", min=1, max=1000, help="How many entries to show."),
    failures: bool = typer.Option(
        False, "--problems", help="Only failed, missed, skipped or interrupted runs."
    ),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Show recent runs, including failed, missed and skipped ones."""
    _require_format(output_format)
    store = _store(data_dir)
    events = store.events(trigger_id=trigger_id, limit=limit)
    if failures:
        events = [
            item for item in events if item.status in {"failed", "missed", "skipped", "interrupted"}
        ]
    if output_format == "json":
        typer.echo(json.dumps({"events": [e.to_payload() for e in events]}, sort_keys=True))
        return
    if not events:
        typer.echo("No trigger history.")
    for item in events:
        run = f"  run={item.run_id}" if item.run_id else ""
        count = f"  x{item.count}" if item.count > 1 else ""
        message = f"  {item.message}" if item.message else ""
        typer.echo(f"{item.at}  {item.status:<11}  {item.trigger_id}{count}{run}{message}")


@app.command("status")
def status(
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Show whether anything is running triggers on this computer."""
    _require_format(output_format)
    base = data_dir or get_data_dir()
    store = TriggerStore(base)
    current = scheduler_status(store)
    login = login_status(base)
    if output_format == "json":
        typer.echo(json.dumps({"scheduler": current, "login": login}, sort_keys=True))
        return
    typer.echo(_status_sentence(current))
    typer.echo(f"Start at login: {'on' if login['enabled'] else 'off'}")
    typer.echo(RUNS_ONLY_WHILE_RUNNING_NOTICE)
    if current["webhookError"]:
        typer.echo(f"Webhooks: {current['webhookError']}")
    for trigger_id, message in current["watchErrors"].items():
        typer.echo(f"{trigger_id}: {message}")


@app.command("login")
def login(
    state: str = typer.Argument("status", help="on, off, or status."),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Start the scheduler at login, so triggers still fire after a reboot."""
    _require_format(output_format)
    if state not in {"on", "off", "status"}:
        typer.echo("Choose on, off, or status.", err=True)
        raise typer.Exit(2)
    base = data_dir or get_data_dir()
    try:
        result = login_status(base) if state == "status" else set_login_enabled(base, state == "on")
    except LoginEntryError as exc:
        raise _fail(exc, output_format) from None
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "login": result}, sort_keys=True))
        return
    typer.echo(f"Start at login: {'on' if result['enabled'] else 'off'}")
    if result["enabled"]:
        typer.echo(f"Login entry: {result['path']}")
        typer.echo("It starts at your next login. To start now: Raticode rattish triggers start")


@app.command("start")
def start(data_dir: Path | None = _DATA_DIR_OPTION) -> None:
    """Run triggers in this process until it's stopped."""
    base = data_dir or get_data_dir()
    engine = TriggerEngine(base, owner="background")
    stop = threading.Event()

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, request_stop)
    engine.start()
    deadline = time.monotonic() + 2
    while not engine.is_owner and time.monotonic() < deadline and not stop.is_set():
        time.sleep(0.05)
    if engine.is_owner:
        typer.echo(f"Running triggers from {engine.data_dir}. Press Ctrl+C to stop.")
    else:
        typer.echo(
            "Another Raticode process is already running these triggers. "
            "Waiting to take over if it stops."
        )
    stop.wait()
    engine.stop()
    typer.echo("Trigger scheduler stopped.")
