"""``Raticode rattish`` commands that go through a running Raticode backend.

The rest of ``Raticode rattish`` works on source files without a backend, and
``rattish run`` executes a workflow inside the CLI process. These commands ask the
backend instead (``rattish start``, ``stop``, ``resume``, ``runs``, ``logs``, approvals,
bundles), so runs started here show up in the app's Runs, the app's Stop button
reaches them, and they share the backend's run slots.
"""

from __future__ import annotations

import base64
import inspect
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import typer
from typer import _click
from typer.core import TyperGroup

from gofer.cli import client_options as opts
from gofer.cli.client import CliError, ExitCode, Output
from gofer.cli.commands import _resources as res
from gofer.cli.commands._backend import require_user
from gofer.cli.commands._resources import Backend, failure, finish, quote, session, table

# Collected here, then added to `Raticode rattish` and `Raticode rattish triggers` by
# register(). A separate plural `workflows` group was removed on purpose in April 2026.
app = typer.Typer()
triggers_app = typer.Typer()


class _RunsGroup(TyperGroup):
    """``rattish runs``: its own subcommands, else the run list (``rattish runs list``).

    ``rattish runs WORKFLOW_ID`` and ``rattish runs --all`` keep working; a workflow
    that is itself named ``list`` or ``rerun`` is listed with ``rattish runs list ID``.
    """

    def parse_args(self, ctx: _click.Context, args: list[str]) -> list[str]:
        first = args[0] if args else None
        if first is None or (first not in self.commands and first not in ctx.help_option_names):
            args = ["list", *args]
        return super().parse_args(ctx, args)


runs_app = typer.Typer(
    cls=_RunsGroup,
    help=(
        "(Backend) List workflow runs (`rattish runs WORKFLOW_ID`, `rattish runs --all`, the "
        "same as `rattish runs list`), or run one again with its inputs (`rattish runs rerun "
        "RUN_ID`)."
    ),
)


def register(rattish_app: typer.Typer, rattish_triggers_app: typer.Typer) -> None:
    """Add the backend commands next to the offline ones; no names overlap."""
    for source, target in (
        (app, rattish_app),
        (triggers_app, rattish_triggers_app),
        (runs_app, runs_app),
    ):
        taken = (
            {info.name for info in target.registered_commands} if source is not target else set()
        )
        for info in source.registered_commands:
            assert info.name not in taken, info.name
            # Help lists them beside the offline commands, so say which need a backend.
            info.help = f"(Backend) {inspect.getdoc(info.callback) or ''}".strip()
            if source is not target:
                target.registered_commands.append(info)
    assert "runs" not in {info.name for info in rattish_app.registered_commands}
    rattish_app.add_typer(runs_app, name="runs")


# Run states the app reports, and the exit code each ends a wait with.
_FINISHED = {
    "success": ExitCode.OK,
    "error": ExitCode.FAILED,
    "stopped": ExitCode.FAILED,
    "disconnected": ExitCode.UNKNOWN,
}


def _started_by() -> str:
    """``rem`` when Rem or one of its workers runs this command, else ``manual``."""
    from gofer.cli.client import in_agent_session

    return "rem" if in_agent_session() else "manual"


def _workflow(backend: Backend, workflow_id: str) -> dict[str, Any]:
    payload = backend.request("GET", "/workflows")
    for item in payload.get("workflows", []):
        if item.get("id") == workflow_id:
            return dict(item)
    raise failure(f"Workflow '{workflow_id}' is not registered", ExitCode.NOT_FOUND)


def _run_path(workflow_id: str, run_id: str) -> str:
    return f"/workflows/{quote(workflow_id)}/logs/{quote(run_id)}"


def _run_state(backend: Backend, workflow_id: str, run_id: str) -> dict[str, Any]:
    # The run list carries awaitingApproval, which the full log doesn't.
    page = backend.request(
        "GET", f"/workflows/{quote(workflow_id)}/logs", query={"q": run_id, "limit": 50}
    )
    for run in page.get("runs", []):
        if run.get("id") == run_id:
            return dict(run)
    raise failure(f"Run '{run_id}' was not found", ExitCode.NOT_FOUND)


def _wait_for_run(
    backend: Backend,
    out: Output | None,
    workflow_id: str,
    run_id: str,
    timeout: float,
    interval: float = 2.0,
) -> dict[str, Any]:
    """Wait for a run to finish or stop at an approval.

    With ``out``, each new run event is printed as it appears (NDJSON events, then the
    result line). Returns the final state, or raises with the exit code the run ended
    with: 1 failed or stopped, 7 waiting on an approval, 8 timed out.
    """
    seen = 0
    if out is not None:
        out.begin_stream()

    def check() -> tuple[bool, dict[str, Any]]:
        nonlocal seen
        if out is not None:
            events = backend.request("GET", _run_path(workflow_id, run_id) + "/events")
            run_events = (events.get("events") or {}).get("runEvents") or []
            for event in run_events[seen:]:
                out.event(event)
            seen = max(seen, len(run_events))
        state = _run_state(backend, workflow_id, run_id)
        finished = str(state.get("status") or "") in _FINISHED
        return finished or bool(state.get("awaitingApproval")), state

    state = dict(res.poll(check, timeout=timeout, interval=interval))
    status = str(state.get("status") or "")
    if state.get("awaitingApproval") and status == "running":
        raise failure(
            f"Run {run_id} is waiting on an approval. Decide it with `Raticode rattish "
            "approve` or in the app.",
            ExitCode.NEEDS_USER,
            data=state,
        )
    code = _FINISHED.get(status, ExitCode.OK)
    if code != ExitCode.OK:
        raise failure(f"Run {run_id} ended {status}.", code, code=f"run_{status}", data=state)
    return state


@app.command("show")
def show_workflow(
    workflow_id: str = typer.Argument(..., help="Workflow ID from `Raticode rattish list`."),
    source: bool = typer.Option(False, "--source", help="Include the workflow.rattish source."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Show one workflow: name, status, inputs and where its source is."""
    current = session("rattish show", output, endpoint, token_file, data_dir)

    def call(backend: Backend, out: Output) -> dict[str, Any]:
        workflow = _workflow(backend, workflow_id)
        if source:
            document = backend.request("GET", f"/workflows/{quote(workflow_id)}/document")
            workflow["source"] = (document.get("document") or {}).get("source")
        return workflow

    def text(data: dict[str, Any]) -> str:
        lines = [
            f"{data.get('name')} ({data.get('id')})",
            f"Status:  {data.get('status')}",
            f"Source:  {data.get('sourcePath')}",
            f"Project: {data.get('projectRoot')}",
        ]
        if data.get("validationError"):
            lines.append(f"Error:   {data['validationError']}")
        for name, spec in (data.get("inputs") or {}).items():
            lines.append(f"Input:   {name} {json.dumps(spec)}")
        if data.get("source"):
            lines += ["", str(data["source"])]
        return "\n".join(lines)

    finish(current, call, text)


@app.command("start")
def run_workflow(
    workflow_id: str = typer.Argument(..., help="Workflow ID from `Raticode rattish list`."),
    inputs: list[str] | None = typer.Option(
        None,
        "--input",
        help="Workflow input as name=value. Values that parse as JSON keep their type.",
    ),
    inputs_file: Path | None = typer.Option(
        None, "--inputs-file", help="JSON object of inputs; - reads standard input."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the plan without running anything."
    ),
    wait: bool = typer.Option(False, "--wait", help="Wait for the run to finish."),
    follow: bool = typer.Option(
        False, "--follow", help="Wait, printing run events as NDJSON lines while it runs."
    ),
    timeout: opts.Timeout = None,
    expected_revision: str | None = typer.Option(
        None, "--expected-revision", help="Refuse to run if the source changed since this revision."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Start a run in the backend; `rattish run` runs one in this process instead.

    The run's nodes really run: providers, commands and services.

    Without --wait it returns as soon as the run starts. With --wait the exit code is
    the run's: 0 succeeded, 1 failed or stopped, 7 waiting on an approval, 8 timed out.
    """
    current = session("rattish start", output, endpoint, token_file, data_dir)
    limit = res.duration(current, timeout, 3600)
    values = res.parse_assignments(current, inputs)
    if inputs_file is not None:
        loaded = res.read_json_file(current, inputs_file)
        if not isinstance(loaded, dict):
            res.invalid(current, "The inputs file must hold a JSON object")
        values = {**loaded, **values}
    body: dict[str, Any] = {"inputs": values, "dryRun": dry_run, "startedBy": _started_by()}
    if not dry_run:
        body["background"] = True
    if expected_revision:
        body["expectedRevision"] = expected_revision

    def call(backend: Backend, out: Output) -> dict[str, Any]:
        result = backend.request("POST", f"/workflows/{quote(workflow_id)}/run", body)
        if dry_run:
            return {"plan": result.get("plan")}
        run = dict(result.get("run") or {})
        run_id = str(run.get("id") or run.get("runId") or "")
        if (wait or follow) and run_id:
            state = _wait_for_run(backend, out if follow else None, workflow_id, run_id, limit)
            run = {**run, **state}
        return run

    def text(data: dict[str, Any]) -> str:
        if "plan" in data:
            return json.dumps(data["plan"], indent=2)
        return f"Run {data.get('id') or data.get('runId')}: {data.get('status', 'started')}"

    finish(current, call, text)


@app.command("stop")
def stop_workflow(
    workflow_id: str = typer.Argument(...),
    run_id: str | None = typer.Option(None, "--run", help="Stop this run only."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Stop a workflow's active run. With no backend running, it still asks the run to stop.

    Reaches runs any process started, such as `rattish triggers start`. Without
    --run it stops every running run of the workflow.
    """
    current = session("rattish stop", output, endpoint, token_file, data_dir)
    path = (
        f"/workflows/{quote(workflow_id)}" + (f"/runs/{quote(run_id)}" if run_id else "") + "/stop"
    )

    def call(backend: Backend, _out: Output) -> dict[str, Any]:
        try:
            return dict(backend.request("POST", path))
        except CliError as exc:
            # A backend named with --endpoint may be another data folder's; only
            # the discovered local one falls back to this computer's run records.
            if exc.exit_code != ExitCode.UNREACHABLE or endpoint:
                raise
        return _stop_without_backend(workflow_id, run_id, data_dir)

    finish(
        current,
        call,
        lambda d: str(d.get("message") or ("Stopped" if d.get("stopped") else "Nothing to stop")),
    )


def _stop_without_backend(
    workflow_id: str, run_id: str | None, data_dir: Path | None
) -> dict[str, Any]:
    """Write stop markers the runs' own processes watch for."""
    from gofer.cli.client import resolve_data_dir
    from gofer.rattish.run_service import stop_rattish_runs

    try:
        asked = stop_rattish_runs(resolve_data_dir(data_dir), workflow_id, run_id)
    except ValueError as exc:
        raise failure(str(exc), ExitCode.USAGE) from None
    except OSError as exc:
        raise failure(f"Could not ask the run to stop: {exc}") from None
    result: dict[str, Any] = {"workflowId": workflow_id}
    if run_id:
        result["runId"] = run_id
    return {
        **result,
        "stopped": bool(asked),
        "runIds": asked,
        "backend": False,
        "message": "Stop requested" if asked else "No active run",
    }


@app.command("resume")
def resume_workflow(
    workflow_id: str = typer.Argument(...),
    run_id: str = typer.Argument(..., help="The run to resume."),
    from_node: str | None = typer.Option(None, "--from-node"),
    only_node: str | None = typer.Option(None, "--only-node"),
    force: bool = typer.Option(False, "--force", help="Resume even if the source changed."),
    inputs: list[str] | None = typer.Option(
        None,
        "--input",
        help="A password input the run didn't save, as name=value. Repeat for more.",
    ),
    wait: bool = typer.Option(False, "--wait", help="Wait for the resumed run to finish."),
    timeout: opts.Timeout = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Resume a stopped or failed run. It runs nodes again.

    A run started with inputs or by a trigger resumes with its saved inputs and events.
    Password inputs are never saved: give each one again with --input.
    """
    current = session("rattish resume", output, endpoint, token_file, data_dir)
    limit = res.duration(current, timeout, 3600)
    body: dict[str, Any] = {"background": True, "force": force, "startedBy": _started_by()}
    if inputs:
        body["inputs"] = res.parse_assignments(current, inputs)
    if from_node:
        body["fromNode"] = from_node
    if only_node:
        body["onlyNode"] = only_node

    def call(backend: Backend, out: Output) -> dict[str, Any]:
        path = f"/workflows/{quote(workflow_id)}/runs/{quote(run_id)}/resume"
        run = dict(backend.request("POST", path, body).get("run") or {})
        new_id = str(run.get("id") or run.get("runId") or "")
        if wait and new_id:
            run = {**run, **_wait_for_run(backend, None, workflow_id, new_id, limit)}
        return run

    finish(
        current,
        call,
        lambda d: f"Run {d.get('id') or d.get('runId')}: {d.get('status', 'started')}",
    )


@runs_app.command("list")
def list_runs(
    workflow_id: str | None = typer.Argument(
        None, help="The workflow whose runs to list. Leave it out with --all."
    ),
    all_workflows: bool = typer.Option(
        False, "--all", help="List runs from every registered workflow, newest first."
    ),
    status: str | None = typer.Option(
        None, "--status", help="success, error, running, stopped or disconnected."
    ),
    since: str | None = typer.Option(
        None,
        "--since",
        help="With --all: runs started since an ISO time, or a span back such as 30m, 24h, 7d.",
    ),
    workflows: list[str] | None = typer.Option(
        None, "--workflow", help="With --all: only this workflow. Repeat for more."
    ),
    limit: int = typer.Option(20, "--limit"),
    offset: int = typer.Option(0, "--offset"),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List a workflow's runs, or every workflow's runs with --all, newest first.

    `rattish runs WORKFLOW_ID` and `rattish runs --all` run this command.
    """
    current = session("rattish runs", output, endpoint, token_file, data_dir)
    if all_workflows == (workflow_id is not None):
        res.invalid(current, "Give a WORKFLOW_ID, or --all for every workflow's runs.")
    if not all_workflows and (since or workflows):
        res.invalid(current, "--since and --workflow work with --all.")
    if all_workflows:
        all_query = {
            "limit": limit,
            "offset": offset,
            "status": status,
            "since": since,
            "workflow": list(workflows or []) or None,
        }
        finish(
            current,
            lambda b, _o: b.request("GET", "/rattish/runs", query=all_query),
            _all_runs_text,
        )
        return
    assert workflow_id is not None
    query = {"limit": limit, "offset": offset, "status": status}

    def text(data: dict[str, Any]) -> str:
        rows = [
            [
                str(run.get("id")),
                str(run.get("status"))
                + (
                    " (stop requested)"
                    if run.get("stopRequested")
                    else " (needs approval)"
                    if run.get("awaitingApproval")
                    else ""
                ),
                str(run.get("startedAt") or ""),
                f"{float(run.get('durationSeconds') or 0):.1f}s",
                *_usage_cells(run.get("usage")),
            ]
            for run in data.get("runs", [])
        ]
        return (
            table(rows, ["RUN", "STATUS", "STARTED", "DURATION", "TOKENS", "EST. COST"])
            or "No runs."
        )

    finish(
        current,
        lambda b, _o: b.request("GET", f"/workflows/{quote(workflow_id)}/logs", query=query),
        text,
    )


_STARTED_BY = {"manual": "manual", "trigger": "trigger", "phone": "phone", "rem": "Rem"}


def _all_runs_text(data: dict[str, Any]) -> str:
    rows = [
        [
            str(run.get("startedAt") or "")[:19].replace("T", " "),
            str(run.get("workflowName") or run.get("workflowId")),
            _STARTED_BY.get(str(run.get("startedBy")), str(run.get("startedBy") or "")),
            str(run.get("status"))
            + (" (needs approval)" if run.get("awaitingApproval") else "")
            + (" (switched provider)" if run.get("fallbacks") else ""),
            f"{float(run.get('durationSeconds') or 0):.1f}s",
            *_usage_cells(run.get("usage")),
            str(run.get("id")),
        ]
        for run in data.get("runs", [])
    ]
    headers = ["STARTED", "WORKFLOW", "HOW", "STATUS", "DURATION", "TOKENS", "EST. COST", "RUN"]
    listed = table(rows, headers) or "No runs."
    totals = data.get("totals") or {}
    shown = len(rows)
    total = int((data.get("pagination") or {}).get("total") or 0)
    footer = str(totals.get("summary") or "")
    if total > shown:
        footer += f" (showing {shown}; use --limit and --offset for more)"
    switches = [
        f"{run.get('id')}  {item.get('line')}"
        for run in data.get("runs", [])
        for item in run.get("fallbacks") or []
        if item.get("line")
    ]
    if switches:
        listed += "\n\nProvider switches:\n" + "\n".join(switches)
    return f"{listed}\n\n{footer}" if footer and rows else listed


@runs_app.command("rerun")
def rerun_run(
    run_id: str = typer.Argument(..., help="The run to run again, from `rattish runs`."),
    inputs: list[str] | None = typer.Option(
        None,
        "--input",
        help="A password input the run didn't save, as name=value. Repeat for more.",
    ),
    workflow_id: str | None = typer.Option(
        None, "--workflow", help="The run's workflow. Found from the run ID when left out."
    ),
    wait: bool = typer.Option(False, "--wait", help="Wait for the new run to finish."),
    follow: bool = typer.Option(
        False, "--follow", help="Wait, printing run events as NDJSON lines while it runs."
    ),
    timeout: opts.Timeout = None,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Run a workflow again with the same inputs and trigger events as an earlier run.

    The new run uses the workflow as it is now. Password inputs are never saved, so
    give each one again with --input; the error names any that are missing. The run's
    nodes really run. With --wait the exit code is the new run's, as for `rattish start`.
    """
    current = session("rattish runs rerun", output, endpoint, token_file, data_dir)
    limit = res.duration(current, timeout, 3600)
    body: dict[str, Any] = {
        "background": True,
        "inputs": res.parse_assignments(current, inputs),
        "startedBy": _started_by(),
    }
    if workflow_id:
        body["workflowId"] = workflow_id

    def call(backend: Backend, out: Output) -> dict[str, Any]:
        run = dict(
            backend.request("POST", f"/rattish/runs/{quote(run_id)}/rerun", body).get("run") or {}
        )
        new_id = str(run.get("id") or run.get("runId") or "")
        owner = str(run.get("workflowId") or workflow_id or "")
        if (wait or follow) and new_id and owner:
            state = _wait_for_run(backend, out if follow else None, owner, new_id, limit)
            run = {**run, **state}
        return run

    finish(
        current,
        call,
        lambda d: f"Run {d.get('id') or d.get('runId')}: {d.get('status', 'started')}",
    )


@app.command("logs")
def run_logs(
    workflow_id: str = typer.Argument(...),
    run_id: str | None = typer.Argument(None, help="Run ID; defaults to the latest run."),
    tail_bytes: int | None = typer.Option(None, "--tail-bytes", help="Only the end of the log."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Print one run's log."""
    current = session("rattish logs", output, endpoint, token_file, data_dir)

    def call(backend: Backend, out: Output) -> dict[str, Any]:
        if run_id is None:
            path = f"/workflows/{quote(workflow_id)}/logs/latest"
            return dict(backend.request("GET", path).get("log") or {})
        query = {"tailBytes": tail_bytes}
        return dict(
            backend.request("GET", _run_path(workflow_id, run_id), query=query).get("log") or {}
        )

    def text(data: dict[str, Any]) -> str:
        body = data.get("text") or data.get("logText") or data.get("content")
        if not isinstance(body, str):
            body = json.dumps(data, indent=2)
        usage = _usage_text(data.get("usage"))
        return f"{usage}\n\n{body}" if usage else body

    finish(current, call, text)


def _usage_cells(usage: Any) -> list[str]:
    """Tokens and estimated cost cells; runs saved before usage tracking show a dash."""
    if not isinstance(usage, dict):
        return ["-", "-"]
    return [str(usage.get("tokensLabel") or "-"), str(usage.get("costLabel") or "-")]


def _usage_text(usage: Any) -> str:
    """A run's usage, then one line per node that called a model."""
    if not isinstance(usage, dict):
        return ""
    lines = [f"Usage: {usage.get('summary')}"]
    limits = usage.get("limits") or {}
    caps = [
        f"{name} {value}"
        for name, value in (
            ("max-tokens", limits.get("maxTokens")),
            ("max-cost-usd", limits.get("maxCostUsd")),
        )
        if value is not None
    ]
    if caps:
        lines.append("Limits: " + ", ".join(caps))
    for node, item in sorted((usage.get("nodes") or {}).items()):
        if isinstance(item, dict):
            lines.append(
                f"  {node}: tokens {item.get('tokensLabel')}, "
                f"estimated cost {item.get('costLabel')} ({item.get('calls')} calls)"
            )
    return "\n".join(lines)


@app.command("events")
def run_events(
    workflow_id: str = typer.Argument(...),
    run_id: str = typer.Argument(...),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Print a run's node events. Text output is one JSON event per line."""
    current = session("rattish events", output, endpoint, token_file, data_dir)
    finish(
        current,
        lambda b, _o: (
            b.request("GET", _run_path(workflow_id, run_id) + "/events").get("events") or {}
        ),
        lambda d: "\n".join(json.dumps(event) for event in d.get("runEvents", [])),
    )


@app.command("wait")
def wait_for_run(
    workflow_id: str = typer.Argument(...),
    run_id: str = typer.Argument(...),
    timeout: opts.Timeout = None,
    follow: bool = typer.Option(False, "--follow", help="Print run events as NDJSON lines."),
    interval: opts.Interval = 2.0,
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Wait for a run to finish or stop at an approval.

    Exit 0 succeeded, 1 failed or stopped, 7 waiting on an approval, 8 timed out.
    """
    current = session("rattish wait", output, endpoint, token_file, data_dir)
    limit = res.duration(current, timeout, 3600)
    finish(
        current,
        lambda b, out: _wait_for_run(
            b, out if follow else None, workflow_id, run_id, limit, interval
        ),
        lambda d: f"Run {run_id}: {d.get('status')}",
    )


@app.command("approvals")
def list_approvals(
    workflow_id: str = typer.Argument(...),
    pending: bool = typer.Option(False, "--pending", help="Only approvals still waiting."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """List a workflow's approval-gate requests."""
    current = session("rattish approvals", output, endpoint, token_file, data_dir)

    def text(data: dict[str, Any]) -> str:
        rows = [
            [
                str(item.get("runId")),
                str(item.get("nodeId")),
                str(item.get("status")),
                str(item.get("message") or item.get("title") or ""),
            ]
            for item in data.get("approvals", [])
        ]
        return table(rows, ["RUN", "NODE", "STATUS", "MESSAGE"]) or "No approvals."

    finish(
        current,
        lambda b, _o: b.request(
            "GET",
            f"/workflows/{quote(workflow_id)}/approvals",
            query={"all": "0" if pending else "1"},
        ),
        text,
    )


def _decide(decision: str) -> Any:
    def command(
        workflow_id: str = typer.Argument(...),
        run_id: str = typer.Argument(...),
        node_id: str = typer.Argument(..., help="The approval node's ID."),
        notes: str = typer.Option("", "--notes", help="Recorded with the decision."),
        output: opts.Output = opts.OutputFormat.json,
        endpoint: opts.EndpointUrl = None,
        token_file: opts.TokenFile = None,
        data_dir: opts.DataDir = None,
    ) -> None:
        current = session(f"rattish {decision}", output, endpoint, token_file, data_dir)
        path = (
            f"/workflows/{quote(workflow_id)}/approvals/{quote(run_id)}/{quote(node_id)}/{decision}"
        )

        def decide(backend: Backend, out: Output) -> Any:
            # A workflow run by Rem or a worker must not approve its own gate.
            require_user(f"{decision} a workflow approval")
            return backend.request("POST", path, {"by": "cli", "notes": notes})

        finish(current, decide, lambda d: f"{decision.capitalize()}d {node_id} in run {run_id}.")

    command.__doc__ = (
        "Approve an approval-gate node so the run continues."
        if decision == "approve"
        else "Reject an approval-gate node."
    )
    return command


app.command("approve")(_decide("approve"))
app.command("reject")(_decide("reject"))


@app.command("export")
def export_workflow(
    workflow_id: str = typer.Argument(...),
    file: Path = typer.Option(..., "--file", "-f", help="Where to write the .raticode bundle."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Export a workflow as a .raticode bundle file.

    The backend writes the bundle into its data folder, then this command moves it to
    --file. That needs the backend on this machine; otherwise the bundle stays where
    the result's bundlePath says.
    """
    current = session("rattish export", output, endpoint, token_file, data_dir)
    relative = f"cli-exports/{uuid.uuid4().hex}.raticode"

    def call(backend: Backend, out: Output) -> dict[str, Any]:
        result = dict(
            backend.request(
                "POST",
                f"/rattish/workflows/{quote(workflow_id)}/export",
                {"outputPath": relative},
            )
        )
        written = Path(str(result.get("bundlePath") or ""))
        if written.is_file():
            target = file.expanduser().resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(written), target)
            result["bundlePath"] = str(target)
        return result

    finish(current, call, lambda d: f"Exported to {d.get('bundlePath')}")


@app.command("import")
def import_workflow(
    bundle: Path = typer.Argument(..., help="A .raticode bundle file."),
    project: Path = typer.Option(..., "--project", help="Project folder for the workflow."),
    preview: bool = typer.Option(False, "--preview", help="Check the bundle without importing."),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Import (apply) a .raticode bundle into a project and register it.

    The backend only writes into folders it trusts: its data folder or a project the
    desktop approved. Anything else is refused (exit 5).
    """
    current = session("rattish import", output, endpoint, token_file, data_dir)
    try:
        content = base64.b64encode(bundle.read_bytes()).decode()
    except OSError as exc:
        res.invalid(current, f"Could not read {bundle}: {exc.strerror or exc}")

    def call(backend: Backend, out: Output) -> Any:
        if preview:
            return backend.request(
                "POST", "/rattish/workflows/import/preview", {"bundleContent": content}
            ).get("bundle")
        return backend.request(
            "POST",
            "/rattish/workflows/import",
            {"bundleContent": content, "projectRoot": str(project.expanduser().resolve())},
        ).get("workflow")

    finish(
        current,
        call,
        lambda d: (
            json.dumps(d, indent=2) if preview else f"Imported {d.get('name')} ({d.get('id')})"
        ),
    )


# Triggers --------------------------------------------------------------------------

# Fields a trigger file carries. IDs, tokens and history stay on this computer.
_PORTABLE_TRIGGER_FIELDS = ("kind", "label", "config", "overlap", "missed", "inputs", "enabled")


def _triggers(backend: Backend, workflow_id: str) -> dict[str, Any]:
    return dict(backend.request("GET", "/rattish/triggers", query={"workflowId": workflow_id}))


@triggers_app.command("export")
def export_triggers(
    workflow_id: str = typer.Argument(...),
    file: Path | None = typer.Option(
        None, "--file", "-f", help="File to write; default the envelope's data."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Write a workflow's triggers as a JSON file, without IDs or webhook tokens."""
    current = session("rattish triggers export", output, endpoint, token_file, data_dir)

    def call(backend: Backend, out: Output) -> dict[str, Any]:
        items = _triggers(backend, workflow_id).get("triggers", [])
        document = {
            "workflowId": workflow_id,
            "triggers": [
                {key: item.get(key) for key in _PORTABLE_TRIGGER_FIELDS} for item in items
            ],
        }
        if file is not None:
            res.write_json_file(file, document)
        return document

    finish(
        current,
        call,
        lambda d: (
            f"Wrote {len(d['triggers'])} triggers to {file}" if file else json.dumps(d, indent=2)
        ),
    )


def _trigger_key(item: dict[str, Any]) -> tuple[str, str]:
    return (str(item.get("kind") or ""), str(item.get("label") or ""))


@triggers_app.command("apply")
def apply_triggers(
    workflow_id: str = typer.Argument(...),
    file: Path = typer.Option(
        ..., "--file", "-f", help="Trigger file from `triggers export`; - for stdin."
    ),
    prune: bool = typer.Option(
        False, "--prune", help="Also remove triggers that aren't in the file."
    ),
    output: opts.Output = opts.OutputFormat.json,
    endpoint: opts.EndpointUrl = None,
    token_file: opts.TokenFile = None,
    data_dir: opts.DataDir = None,
) -> None:
    """Make a workflow's triggers match a file. Triggers match by kind and label.

    Applying the same file twice changes nothing. New webhook triggers get a fresh
    token, printed once in the result.
    """
    current = session("rattish triggers apply", output, endpoint, token_file, data_dir)
    document = res.read_json_file(current, file)
    wanted = document.get("triggers") if isinstance(document, dict) else None
    if not isinstance(wanted, list) or not all(isinstance(item, dict) for item in wanted):
        res.invalid(current, "The file needs a triggers list")
    keys = [_trigger_key(item) for item in wanted]
    if len(set(keys)) != len(keys):
        res.invalid(current, "Each trigger in the file needs a distinct kind and label")

    def call(backend: Backend, out: Output) -> dict[str, Any]:
        existing = {
            _trigger_key(item): item for item in _triggers(backend, workflow_id)["triggers"]
        }
        result: dict[str, list[Any]] = {
            "created": [],
            "updated": [],
            "unchanged": [],
            "removed": [],
        }
        for item in wanted:
            fields = {key: item[key] for key in _PORTABLE_TRIGGER_FIELDS if key in item}
            match = existing.pop(_trigger_key(item), None)
            if match is None:
                created = backend.request(
                    "POST", "/rattish/triggers", {**fields, "workflowId": workflow_id}
                )
                entry = {"id": created["trigger"]["id"]}
                if created.get("token"):
                    entry["token"] = created["token"]
                result["created"].append(entry)
                continue
            changes = {
                key: value
                for key, value in fields.items()
                if key != "kind" and match.get(key) != value
            }
            if not changes:
                result["unchanged"].append(match["id"])
                continue
            backend.request("POST", f"/rattish/triggers/{quote(match['id'])}", changes)
            result["updated"].append(match["id"])
        if prune:
            for leftover in existing.values():
                backend.request("DELETE", f"/rattish/triggers/{quote(leftover['id'])}")
                result["removed"].append(leftover["id"])
        return result

    def text(data: dict[str, Any]) -> str:
        lines = [f"{key}: {len(value)}" for key, value in data.items()]
        for item in data["created"]:
            if item.get("token"):
                lines.append(f"Webhook token for {item['id']} (shown once): {item['token']}")
        return "\n".join(lines)

    finish(current, call, text)
