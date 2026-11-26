"""`Raticode rattish triggers`: start workflows on a schedule, a file change or a webhook.

A trigger runs only the workflow version you approved, and only what you were shown.
Adding a trigger from your own terminal approves the workflow as it is. Changing or
turning one on approves nothing by itself: when a change is waiting, they print the diff
and ask, or approve with `--yes`. `approve` prints what changed and approves it. When a
diff was cut short, a file can't be shown or an MCP server's package has no exact
version, approving also needs `--unseen` or a second yes. Inside a Raticode agent turn
(Rem or a worker) these commands still work but approve nothing, so an agent can't
approve its own edit.
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any

import typer

from gofer.cli.client import ExitCode, in_agent_session
from gofer.cli.output import confirm_unseen
from gofer.rattish.trigger_defaults import (
    DEFAULT_DEBOUNCE_SECONDS,
    DEFAULT_MAX_RUN_SECONDS,
    DEFAULT_MISSED,
    DEFAULT_OVERLAP,
    format_run_time,
    parse_run_time,
)
from gofer.utils.paths import get_data_dir

if TYPE_CHECKING:
    from gofer.rattish.triggers import TriggerRecord, TriggerStore

app = typer.Typer(
    help="Start Rattish workflows on a schedule, a file change or a local webhook.",
    no_args_is_help=True,
)
add_app = typer.Typer(help="Add a trigger to a workflow.", no_args_is_help=True)
app.add_typer(add_app, name="add")

_DATA_DIR_OPTION = typer.Option(None, "--data-dir", help="Override the data folder.")
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
_RUN_UNAPPROVED_OPTION = typer.Option(
    False,
    "--run-edits-without-approval",
    help="Run the workflow even after it changes, without asking you to approve the edit.",
)
_MAX_RUN_TIME_HELP = (
    "Time limit: stop a triggered run after this long, such as 30m, 2h or 1h30m, or none "
    "for no limit. The workflow's own timeout still applies if it's shorter."
)
_ADD_MAX_RUN_TIME_OPTION = typer.Option(
    format_run_time(DEFAULT_MAX_RUN_SECONDS).replace(" ", ""),
    "--max-run-time",
    help=_MAX_RUN_TIME_HELP,
)
_USAGE_LIMIT_HELP = (
    "When an agent step hits its provider's usage limit: wait for the limit to reset and "
    "resume the run (wait), or end it failed (fail)."
)
_ADD_USAGE_LIMIT_OPTION = typer.Option("wait", "--usage-limit", help=_USAGE_LIMIT_HELP)
_USAGE_LIMIT_CHOICES = {"wait": True, "fail": False}
_APPROVAL_STATES = {
    "changed": "Workflow changed since you approved it.",
    "unapproved": "Waiting for you to approve this workflow version.",
}
_YES_OPTION = typer.Option(
    False, "--yes", "-y", help="Approve changes waiting for review without asking."
)
_UNSEEN_OPTION = typer.Option(
    False,
    "--unseen",
    help="Also approve files whose change can't be shown in full (a diff cut short, or a "
    "binary or very large file) and MCP servers whose package has no exact version. "
    "Without it you're asked again, or --yes alone refuses.",
)


def _actor() -> str:
    """Who is asking: approvals are the user's alone, never an agent's."""
    return "agent" if in_agent_session() else "user"


def _store(data_dir: Path | None) -> TriggerStore:
    from gofer.rattish.triggers import TriggerStore

    return TriggerStore(data_dir or get_data_dir())


def _require_format(output_format: str) -> None:
    if output_format not in {"text", "json"}:
        typer.echo("--format must be text or json.", err=True)
        raise typer.Exit(2)


def _fail(error: Exception, output_format: str) -> typer.Exit:
    from gofer.rattish.triggers import UserOnlyError

    # A decision only the user may make exits 5, like every other user-only refusal.
    user_only = isinstance(error, UserOnlyError)
    if output_format == "json":
        payload = {"ok": False, "error": str(error)}
        if user_only:
            payload["code"] = "user_only"
        typer.echo(json.dumps(payload, sort_keys=True))
    else:
        typer.echo(str(error), err=True)
    return typer.Exit(int(ExitCode.AUTH) if user_only else 1)


def _refuse_in_agent_turn(action: str, output_format: str) -> None:
    """Keep webhook tokens, the webhook port, spending limits and a global resume
    with the user: an agent turn may add and change triggers, but not these."""
    if not in_agent_session():
        return
    message = (
        f"Only you can {action}. This command is running inside a Raticode agent turn; "
        "run it from your own terminal or use the app."
    )
    if output_format == "json":
        typer.echo(json.dumps({"ok": False, "error": message, "code": "user_only"}, sort_keys=True))
    else:
        typer.echo(message, err=True)
    raise typer.Exit(int(ExitCode.AUTH))


def _parse_inputs(values: list[str] | None) -> dict[str, Any]:
    from gofer.rattish.triggers import TriggerError

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
    from gofer.rattish.triggers import (
        approval_status,
        describe_trigger,
        next_run_at,
        trigger_limit_state,
        webhook_url,
    )

    latest = store.latest_event(record.id)
    today = trigger_limit_state(store, record)
    return {
        **record.to_payload(),
        "description": describe_trigger(record),
        "nextRunAt": next_run_at(record),
        "webhookUrl": webhook_url(record.id, store.webhook_port())
        if record.kind == "webhook"
        else None,
        "latestEvent": latest.to_payload() if latest else None,
        "dailyLimit": today.to_payload() if today is not None else None,
        "approval": approval_status(store, record).to_payload(),
    }


def _max_run_seconds(value: str) -> int | None:
    """``--max-run-time`` in seconds; None is no limit."""
    from gofer.rattish.triggers import TriggerError

    try:
        return parse_run_time(value)
    except ValueError as exc:
        raise TriggerError(str(exc)) from None


def _wait_usage_limit(value: str) -> bool:
    """``--usage-limit`` as the trigger's Wait for a usage limit to reset switch."""
    from gofer.rattish.triggers import TriggerError

    choice = value.strip().lower()
    if choice not in _USAGE_LIMIT_CHOICES:
        raise TriggerError(f"Usage limit must be wait or fail, not {value!r}.")
    return _USAGE_LIMIT_CHOICES[choice]


def _trigger_line(payload: dict[str, Any]) -> str:
    state = "on " if payload["enabled"] else "off"
    limit = payload.get("maxRunSeconds")
    pieces = [
        payload["id"],
        f"{payload['kind']:<7}",
        state,
        payload["description"],
        f"overlap={payload['overlap']}",
        f"time-limit={format_run_time(limit).replace(' ', '') if limit else 'none'}",
        f"usage-limit={'wait' if payload.get('waitForUsageLimit', True) else 'fail'}",
    ]
    if payload["kind"] == "cron":
        pieces.append(f"missed={payload['missed']}")
    if payload["nextRunAt"]:
        pieces.append(f"next={payload['nextRunAt']}")
    daily = payload.get("dailyLimit")
    if daily:
        pieces.append(f"daily: {daily['summary']}" + (" (reached)" if daily["reached"] else ""))
    latest = payload.get("latestEvent")
    if latest:
        pieces.append(f"last={latest['status']}")
    if payload.get("runEditsWithoutApproval"):
        pieces.append("runs edits without approval")
    else:
        approval_state = (payload.get("approval") or {}).get("state")
        if approval_state in _APPROVAL_STATES:
            pieces.append(f"approval={approval_state}")
    return "  ".join(pieces)


def _approval_notice(payload: dict[str, Any]) -> str | None:
    """What to do when a trigger won't run its workflow as it is now."""
    if payload.get("runEditsWithoutApproval"):
        return None
    state = (payload.get("approval") or {}).get("state")
    if state not in _APPROVAL_STATES:
        return None
    return (
        f"{_APPROVAL_STATES[state]} Review and approve it with: "
        f"Raticode rattish triggers approve {payload['id']}"
    )


def _status_sentence(status: dict[str, Any]) -> str:
    if not status["running"]:
        sentence = (
            "Scheduler: not running. Nothing will fire until Raticode or its scheduler starts.\n"
            "Open the Raticode app, run `Raticode rattish triggers start` in a terminal, "
            "or start it at every login with `Raticode rattish triggers login on`."
        )
    else:
        owner = "the Raticode app" if status["owner"] == "app" else "the background scheduler"
        sentence = f"Scheduler: running in {owner} (process {status['pid']})."
    if status.get("paused"):
        sentence += f"\n{_PAUSED_SENTENCE} (since {status.get('pausedAt')})"
    return sentence


_PAUSED_SENTENCE = "All triggers are paused. Nothing fires until you resume them."


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
    run_unapproved: bool,
    max_run_time: str,
    usage_limit: str,
    output_format: str,
    data_dir: Path | None,
) -> None:
    from gofer.rattish.trigger_service import scheduler_status
    from gofer.rattish.triggers import (
        RUNS_ONLY_WHILE_RUNNING_NOTICE,
        TriggerError,
        model_notices,
        unsandboxed_steps,
        widened_steps,
    )

    _require_format(output_format)
    store = _store(data_dir)
    try:
        record, token = store.create(
            max_run_seconds=_max_run_seconds(max_run_time),
            wait_usage_limit=_wait_usage_limit(usage_limit),
            kind=kind,
            source_path=source,
            config=config,
            label=label,
            overlap=overlap,
            missed=missed,
            inputs=_parse_inputs(input_),
            enabled=not disabled,
            run_unapproved=run_unapproved,
            actor=_actor(),
            approved_by="cli",
        )
    except TriggerError as exc:
        raise _fail(exc, output_format) from None
    payload = _trigger_payload(store, record)
    status = scheduler_status(store)
    unsandboxed = unsandboxed_steps(record.source_path, store.data_dir)
    widened = widened_steps(record.source_path, store.data_dir)
    models = model_notices(record.source_path, store.data_dir)
    if output_format == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "trigger": payload,
                    "token": token,
                    "scheduler": status,
                    "unsandboxed": unsandboxed,
                    "widened": widened,
                    "models": models,
                },
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
    notice = _approval_notice(payload)
    if notice:
        typer.echo(notice)
    typer.echo(_status_sentence(status))
    typer.echo(RUNS_ONLY_WHILE_RUNNING_NOTICE)
    for item in unsandboxed:
        typer.echo(
            f"Runs unsandboxed: step {item['node']!r} on {item['provider']} ({item['reason']})."
        )
    for item in widened:
        typer.echo(f"Widens its sandbox: step {item['node']!r} ({item['reason']}).")
    for item in models:
        typer.echo(f"Step {item['node']!r} on {item['provider']}: {item['message']}")


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
    run_unapproved: bool = _RUN_UNAPPROVED_OPTION,
    max_run_time: str = _ADD_MAX_RUN_TIME_OPTION,
    usage_limit: str = _ADD_USAGE_LIMIT_OPTION,
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
        run_unapproved=run_unapproved,
        max_run_time=max_run_time,
        usage_limit=usage_limit,
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
    run_unapproved: bool = _RUN_UNAPPROVED_OPTION,
    max_run_time: str = _ADD_MAX_RUN_TIME_OPTION,
    usage_limit: str = _ADD_USAGE_LIMIT_OPTION,
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
        run_unapproved=run_unapproved,
        max_run_time=max_run_time,
        usage_limit=usage_limit,
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
    run_unapproved: bool = _RUN_UNAPPROVED_OPTION,
    max_run_time: str = _ADD_MAX_RUN_TIME_OPTION,
    usage_limit: str = _ADD_USAGE_LIMIT_OPTION,
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
        run_unapproved=run_unapproved,
        max_run_time=max_run_time,
        usage_limit=usage_limit,
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
    from gofer.rattish.trigger_service import scheduler_status
    from gofer.rattish.triggers import RUNS_ONLY_WHILE_RUNNING_NOTICE

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
        notice = _approval_notice(payload)
        if notice:
            typer.echo(f"    {notice}")
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
    daily_max_cost_usd: float | None = typer.Option(
        None,
        "--daily-max-cost-usd",
        help="Most estimated cost in US dollars this trigger's runs may use each day. "
        "0 removes it.",
    ),
    daily_max_tokens: int | None = typer.Option(
        None,
        "--daily-max-tokens",
        help="Most tokens this trigger's runs may use each day. 0 removes it.",
    ),
    max_run_time: str | None = typer.Option(None, "--max-run-time", help=_MAX_RUN_TIME_HELP),
    usage_limit: str | None = typer.Option(None, "--usage-limit", help=_USAGE_LIMIT_HELP),
    run_unapproved: bool | None = typer.Option(
        None,
        "--run-edits-without-approval/--require-approval",
        help="Run the workflow after it changes without asking, or ask again (the default).",
    ),
    yes: bool = _YES_OPTION,
    unseen: bool = _UNSEEN_OPTION,
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Change a trigger. Options you leave out stay the same.

    This approves nothing by itself. When the workflow has changes waiting for review,
    it prints them and asks, or approves them with --yes.
    """
    from gofer.rattish.triggers import TriggerError, cost_report_warnings

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
    daily: dict[str, Any] = {}
    if daily_max_cost_usd is not None:
        daily["daily_max_cost_usd"] = daily_max_cost_usd
    if daily_max_tokens is not None:
        daily["daily_max_tokens"] = daily_max_tokens
    store = _store(data_dir)
    try:
        limit: dict[str, Any] = (
            {} if max_run_time is None else {"max_run_seconds": _max_run_seconds(max_run_time)}
        )
        record = store.update(
            trigger_id,
            config=changes or None,
            label=label,
            overlap=overlap,
            missed=missed,
            inputs=_parse_inputs(input_) if input_ else None,
            **daily,
            **limit,
            wait_usage_limit=None if usage_limit is None else _wait_usage_limit(usage_limit),
            run_unapproved=run_unapproved,
            actor=_actor(),
        )
    except TriggerError as exc:
        raise _fail(exc, output_format) from None
    warnings = (
        cost_report_warnings(record.source_path, store.data_dir)
        if daily and record.daily_max_cost_usd is not None
        else []
    )
    _emit_one(store, record, output_format, warnings=warnings, yes=yes, unseen=unseen, offer=True)


def _emit_one(
    store: TriggerStore,
    record: TriggerRecord,
    output_format: str,
    *,
    warnings: list[str] | None = None,
    yes: bool = False,
    unseen: bool = False,
    offer: bool = False,
) -> None:
    """Print a changed trigger, then offer to approve any change waiting for review.

    The edit is saved either way. From your own terminal, a waiting change is printed as a
    diff and approved with --yes or a yes at the prompt; otherwise it keeps waiting.
    """
    payload = _trigger_payload(store, record)
    waiting = offer and _approval_notice(payload) is not None and _actor() == "user"
    approval: dict[str, Any] | None = None
    if waiting and (yes or (output_format == "text" and _interactive())):
        if output_format == "text":
            typer.echo(_trigger_line(payload))
            for warning in warnings or []:
                typer.echo(f"Warning: {warning}", err=True)
        approval = _offer_approval(store, record.id, yes=yes, unseen=unseen, fmt=output_format)
        payload = _trigger_payload(store, store.get(record.id))
        if output_format == "text":
            if not approval["approved"]:
                typer.echo(_approval_notice(payload) or approval.get("error") or "")
            return
    if output_format == "json":
        extra: dict[str, Any] = {"warnings": warnings} if warnings else {}
        if approval is not None:
            extra["approved"] = approval["approved"]
            for key in ("error", "unseen", "conflict"):
                if key in approval:
                    extra[key] = approval[key]
        typer.echo(json.dumps({"ok": True, "trigger": payload, **extra}, sort_keys=True))
    else:
        typer.echo(_trigger_line(payload))
        for warning in warnings or []:
            typer.echo(f"Warning: {warning}", err=True)
        notice = _approval_notice(payload)
        if notice:
            typer.echo(notice)
            if waiting:
                typer.echo("Add --yes to approve it here, after reading the diff it prints.")


def _interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _offer_approval(
    store: TriggerStore, trigger_id: str, *, yes: bool, unseen: bool, fmt: str
) -> dict[str, Any]:
    """Print a trigger's waiting changes and approve them if you say yes.

    Returns ``approved`` and, when nothing was approved, why. Files whose change wasn't
    shown in full need ``unseen`` or a second yes at the prompt.
    """
    from gofer.rattish.triggers import (
        ApprovalConflict,
        TriggerError,
        UnseenChanges,
        approve_trigger,
        trigger_changes,
    )

    shown = trigger_changes(store, trigger_id)
    if fmt == "text":
        typer.echo(_changes_text(shown))
    if shown["state"] == "approved":
        return {"approved": True}
    if not yes and not typer.confirm("Approve this version for the trigger?", default=False):
        return {"approved": False, "error": "Not approved."}
    accept_unseen = confirm_unseen(shown.get("unseen") or [], yes=yes, unseen=unseen, fmt=fmt)
    try:
        approve_trigger(
            store,
            trigger_id,
            actor="user",
            by="cli",
            expected_fingerprint=shown["fingerprint"],
            accept_unseen=accept_unseen,
        )
    except ApprovalConflict as exc:
        if fmt == "text":
            typer.echo(str(exc), err=True)
        return {"approved": False, "error": str(exc), "conflict": True}
    except UnseenChanges as exc:
        if fmt == "text":
            typer.echo(_unseen_refusal(exc.files), err=True)
        return {"approved": False, "error": str(exc), "unseen": exc.files}
    except TriggerError as exc:
        if fmt == "text":
            typer.echo(str(exc), err=True)
        return {"approved": False, "error": str(exc)}
    if fmt == "text":
        typer.echo(f"Approved. {trigger_id} runs this version of the workflow from now on.")
    return {"approved": True}


def _unseen_refusal(unseen: list[dict[str, Any]]) -> str:
    from gofer.rattish.trigger_approval import unseen_refusal

    return unseen_refusal(unseen)


def _set_enabled(
    trigger_id: str,
    enabled: bool,
    output_format: str,
    data_dir: Path | None,
    *,
    yes: bool = False,
    unseen: bool = False,
) -> None:
    from gofer.rattish.triggers import TriggerError

    _require_format(output_format)
    store = _store(data_dir)
    try:
        record = store.update(trigger_id, enabled=enabled, actor=_actor())
    except TriggerError as exc:
        raise _fail(exc, output_format) from None
    _emit_one(store, record, output_format, yes=yes, unseen=unseen, offer=enabled)


@app.command("enable")
def enable(
    trigger_id: str = typer.Argument(..., help="Trigger ID."),
    yes: bool = _YES_OPTION,
    unseen: bool = _UNSEEN_OPTION,
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Turn a trigger on. This approves nothing by itself.

    When the workflow has changes waiting for review, it prints them and asks, or
    approves them with --yes.
    """
    _set_enabled(trigger_id, True, output_format, data_dir, yes=yes, unseen=unseen)


@app.command("disable")
def disable(
    trigger_id: str = typer.Argument(..., help="Trigger ID."),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Turn a trigger off without deleting it."""
    _set_enabled(trigger_id, False, output_format, data_dir)


def _set_paused(paused: bool, every: bool, output_format: str, data_dir: Path | None) -> None:
    from gofer.rattish.trigger_service import scheduler_status

    _require_format(output_format)
    verb = "pause" if paused else "resume"
    if not every:
        typer.echo(
            f"Add --all to {verb} every trigger on this computer. To turn one trigger "
            f"{'off' if paused else 'on'}, use Raticode rattish triggers "
            f"{'disable' if paused else 'enable'} TRIGGER_ID.",
            err=True,
        )
        raise typer.Exit(2)
    store = _store(data_dir)
    before = store.pause_state()
    state = store.set_paused(paused)
    triggers = store.list_triggers()
    enabled = sum(1 for item in triggers if item.enabled)
    if output_format == "json":
        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "pause": state.to_payload(),
                    "changed": before.paused != state.paused,
                    "triggers": len(triggers),
                    "enabled": enabled,
                    "scheduler": scheduler_status(store),
                },
                sort_keys=True,
            )
        )
        return
    if paused:
        already = "already " if before.paused else ""
        typer.echo(
            f"All triggers are {already}paused. Cron times until you resume are recorded "
            "as skipped and won't be caught up."
        )
    else:
        already = "" if before.paused else "Triggers weren't paused. "
        typer.echo(
            f"{already}Triggers resumed: {enabled} of {len(triggers)} "
            f"trigger{'' if len(triggers) == 1 else 's'} on, as each was set."
        )


_ALL_OPTION = typer.Option(False, "--all", help="Every trigger on this computer.")


@app.command("pause")
def pause(
    every: bool = _ALL_OPTION,
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Pause every trigger at once. Each trigger keeps its own on/off setting."""
    _set_paused(True, every, output_format, data_dir)


@app.command("resume")
def resume(
    every: bool = _ALL_OPTION,
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Resume triggers after pause --all, exactly as each one was set.

    Refused inside a Raticode agent turn (exit 5).
    """
    _require_format(output_format)
    _refuse_in_agent_turn("resume every trigger", output_format)
    _set_paused(False, every, output_format, data_dir)


@app.command("budget")
def budget(
    max_cost_usd: float | None = typer.Option(
        None,
        "--max-cost-usd",
        help="Most estimated cost in US dollars all triggered runs may use each day. 0 removes it.",
    ),
    max_tokens: int | None = typer.Option(
        None,
        "--max-tokens",
        help="Most tokens all triggered runs may use each day. 0 removes it.",
    ),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Show or set the daily limit for every triggered run on this computer.

    The day ends at midnight in this computer's time zone. Runs started by hand, from
    the phone or by Rem don't count and are never blocked. Setting it is refused inside
    a Raticode agent turn (exit 5).
    """
    from gofer.rattish.triggers import TriggerError
    from gofer.ui.rattish_triggers import daily_budget_payload, set_daily_budget_payload

    _require_format(output_format)
    base = data_dir or get_data_dir()
    body: dict[str, Any] = {}
    if max_cost_usd is not None:
        body["maxCostUsd"] = max_cost_usd
    if max_tokens is not None:
        body["maxTokens"] = max_tokens
    if body:
        _refuse_in_agent_turn("change the daily limit for triggered runs", output_format)
    try:
        payload = set_daily_budget_payload(base, body) if body else daily_budget_payload(base)
    except TriggerError as exc:
        raise _fail(exc, output_format) from None
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, **payload}, sort_keys=True))
        return
    today = payload["today"]
    if today is None:
        typer.echo("No daily limit for triggered runs on this computer.")
    else:
        state = "Reached. Triggered starts are skipped" if today["reached"] else "Resets"
        typer.echo(f"Daily limit for all triggered runs: {today['summary']}.")
        typer.echo(f"{state} until midnight ({today['timezone']}).")
    for warning in payload.get("warnings") or []:
        typer.echo(f"Warning: {warning}", err=True)


@app.command("remove")
def remove(
    trigger_id: str = typer.Argument(..., help="Trigger ID."),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Delete a trigger and its history."""
    from gofer.rattish.triggers import TriggerError

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
    """Replace a webhook's token. The old token stops working at once.

    Refused inside a Raticode agent turn (exit 5).
    """
    from gofer.rattish.triggers import TriggerError

    _require_format(output_format)
    _refuse_in_agent_turn("replace a webhook's token", output_format)
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
    """Show or change the local port webhooks listen on.

    Changing it is refused inside a Raticode agent turn (exit 5).
    """
    from gofer.rattish.triggers import TriggerError

    _require_format(output_format)
    if port is not None:
        _refuse_in_agent_turn("change the webhook port", output_format)
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


def _changes_text(changes: dict[str, Any]) -> str:
    from gofer.rattish.trigger_approval import unified_diff_text

    state = changes["state"]
    if state == "approved":
        return "This trigger runs the workflow as it is now. Nothing to approve."
    if state == "missing":
        return "The workflow file is missing, so the trigger's runs fail until it's back."
    lines = [_APPROVAL_STATES.get(state, "")]
    if changes.get("approvedAt"):
        lines.append(f"Approved {changes['approvedAt']} from {changes.get('approvedBy')}.")
    marked = changes.get("changedDuringRun")
    if marked:
        lines.append(
            f"{marked.get('path')} changed during a triggered run, so the step that uses it "
            "wasn't run."
        )
    body = unified_diff_text(changes)
    if marked and not body:
        body = "No file differs from the approved version now."
    lines.append(body or "No file differs; the workflow uses a different set of files now.")
    if changes.get("unseen"):
        from gofer.rattish.trigger_approval import unseen_note

        lines.append(unseen_note(changes["unseen"]))
    return "\n".join(line for line in lines if line)


@app.command("changes")
def changes(
    trigger_id: str = typer.Argument(..., help="Trigger ID."),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Show what changed in the workflow since this trigger's version was approved."""
    from gofer.rattish.triggers import TriggerError, trigger_changes

    _require_format(output_format)
    try:
        result = trigger_changes(_store(data_dir), trigger_id)
    except TriggerError as exc:
        raise _fail(exc, output_format) from None
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "changes": result}, sort_keys=True))
    else:
        typer.echo(_changes_text(result))


@app.command("approve")
def approve(
    trigger_id: str = typer.Argument(..., help="Trigger ID."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve without asking after the diff."),
    unseen: bool = _UNSEEN_OPTION,
    fingerprint: str | None = typer.Option(
        None,
        "--fingerprint",
        help="Approve only this version, as shown by `changes --format json`.",
    ),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Print what changed in the workflow, then approve this version for the trigger.

    When a diff was cut short or a file can't be shown, this names those files and asks
    again; with --yes it also needs --unseen. Only you can approve: inside a Raticode
    agent turn this is refused (exit 5).
    """
    from gofer.rattish.triggers import (
        USER_ONLY,
        ApprovalConflict,
        TriggerError,
        UnseenChanges,
        approve_trigger,
        trigger_changes,
    )

    _require_format(output_format)
    if in_agent_session():
        if output_format == "json":
            typer.echo(
                json.dumps({"ok": False, "error": USER_ONLY, "code": "user_only"}, sort_keys=True)
            )
        else:
            typer.echo(USER_ONLY, err=True)
        raise typer.Exit(int(ExitCode.AUTH))
    store = _store(data_dir)
    try:
        shown = trigger_changes(store, trigger_id)
    except TriggerError as exc:
        raise _fail(exc, output_format) from None
    if output_format == "text":
        typer.echo(_changes_text(shown))
    if shown["state"] == "approved" and fingerprint is None:
        if output_format == "json":
            typer.echo(
                json.dumps({"ok": True, "approved": False, "changes": shown}, sort_keys=True)
            )
        return
    expected = fingerprint or shown["fingerprint"]
    if not yes:
        if output_format == "json":
            typer.echo(
                json.dumps(
                    {
                        "ok": False,
                        "error": "Add --yes to approve without a prompt.",
                        "changes": shown,
                    },
                    sort_keys=True,
                )
            )
            raise typer.Exit(2)
        if not typer.confirm("Approve this version for the trigger?", default=False):
            typer.echo("Not approved. The trigger keeps skipping its starts until you approve.")
            raise typer.Exit(1)
    accept_unseen = confirm_unseen(
        shown.get("unseen") or [], yes=yes, unseen=unseen, fmt=output_format
    )
    try:
        record = approve_trigger(
            store,
            trigger_id,
            actor="user",
            by="cli",
            expected_fingerprint=expected,
            accept_unseen=accept_unseen,
        )
    except UnseenChanges as exc:
        if output_format == "json":
            typer.echo(
                json.dumps(
                    {"ok": False, "error": str(exc), "unseen": exc.files, "changes": shown},
                    sort_keys=True,
                )
            )
        else:
            typer.echo(_unseen_refusal(exc.files), err=True)
        raise typer.Exit(2) from None
    except ApprovalConflict as exc:
        if output_format == "json":
            typer.echo(json.dumps({"ok": False, "error": str(exc), "conflict": True}))
        else:
            typer.echo(str(exc), err=True)
        raise typer.Exit(int(ExitCode.CONFLICT)) from None
    except TriggerError as exc:
        raise _fail(exc, output_format) from None
    payload = _trigger_payload(store, record)
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "approved": True, "trigger": payload}, sort_keys=True))
    else:
        typer.echo(f"Approved. {payload['id']} runs this version of the workflow from now on.")


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
    from gofer.rattish.trigger_login import login_status
    from gofer.rattish.trigger_service import scheduler_status
    from gofer.rattish.triggers import RUNS_ONLY_WHILE_RUNNING_NOTICE, TriggerStore

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
    from gofer.rattish.trigger_login import LoginEntryError, login_status, set_login_enabled

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
    from gofer.rattish.trigger_service import TriggerEngine
    from gofer.ui.scratch_cleanup import start_scratch_cleanup

    base = data_dir or get_data_dir()
    engine = TriggerEngine(base, owner="background")
    # Scratch folders a stopped or crashed Rem turn or workflow run left behind.
    start_scratch_cleanup(base)
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
