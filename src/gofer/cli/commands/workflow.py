from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import typer
from rich.console import Console
from rich.table import Table

from gofer.cli.client import CliError, ExitCode, in_agent_session
from gofer.cli.commands import rattish as rattish_commands
from gofer.cli.output import emit_json, format_option, json_alias_option, wants_json
from gofer.utils.paths import get_data_dir

if TYPE_CHECKING:
    from gofer.core.approvals import ApprovalRequest, ApprovalStore

app = typer.Typer(
    help=(
        "Run and inspect Rattish workflows: run, resume, plan, validate, list, logs, usage, "
        "approvals, export, import, rm, rename and duplicate. Author workflows with "
        "Raticode rattish."
    ),
    no_args_is_help=True,
)
logs_app = typer.Typer(help="Inspect workflow run logs", no_args_is_help=True)
app.add_typer(logs_app, name="logs")
console = Console()
err_console = Console(stderr=True)


def _resolve_rattish_source(name: str, data_dir: Path | None) -> Path | None:
    """Return the Rattish entrypoint for a .rattish path, workflow folder, or registered ID."""
    from gofer.rattish.workspaces import list_registered_workflows

    path = Path(name).expanduser()
    if path.suffix == ".rattish" and path.is_file():
        return path
    if path.is_dir() and (path / "workflow.rattish").is_file():
        return path / "workflow.rattish"
    if path.suffix in {".toml", ".rattish"}:
        return None
    try:
        registered = list_registered_workflows(registry_dir=data_dir)
    except (OSError, ValueError):
        return None
    return next(
        (item.entrypoint for item in registered if item.workflow_id == name),
        None,
    )


def _require_rattish_source(name: str, data_dir: Path | None) -> Path:
    """The Rattish entrypoint ``name`` names, or exit with what to do instead.

    A TOML workflow exits 2 with the command that converts it; Raticode no longer
    loads TOML workflows.
    """
    source = _resolve_rattish_source(name, data_dir)
    if source is not None:
        return source
    toml_path = Path(name).expanduser()
    if toml_path.suffix != ".toml":
        toml_path = (data_dir or get_data_dir()) / f"{name}.toml"
    if toml_path.suffix == ".toml" and toml_path.is_file():
        typer.echo(
            f"{toml_path} is a TOML workflow. Raticode runs only Rattish workflows now; "
            f"convert it with: Raticode rattish convert {shlex.quote(str(toml_path))} "
            "--project PATH",
            err=True,
        )
        raise typer.Exit(2)
    err_console.print(f"[red]Workflow '{name}' not found[/red]")
    raise typer.Exit(1)


def _rattish_workflow_id(name: str, data_dir: Path | None) -> str | None:
    """The registered ID of a Rattish workflow named by ID, folder or source path."""
    from gofer.rattish.workspaces import list_registered_workflows

    source = _resolve_rattish_source(name, data_dir)
    if source is None:
        return None
    try:
        registered = list_registered_workflows(registry_dir=data_dir)
    except (OSError, ValueError):
        registered = ()
    resolved = source.resolve()
    match = next(
        (item.workflow_id for item in registered if item.entrypoint.resolve() == resolved), None
    )
    if match is None:
        typer.echo(
            f"{source} isn't registered, so Raticode keeps no run history for it. "
            "Register it with Raticode rattish create, or run it with Raticode rattish run.",
            err=True,
        )
        raise typer.Exit(2)
    return match


_NO_BACKEND = object()


def _through_backend(
    data_dir: Path | None,
    method: str,
    path: str,
    body: Any = None,
    query: dict[str, str] | None = None,
) -> Any:
    """Send a workflow change to the running backend that published itself for this data
    folder, so the backend stays the only writer and re-syncs schedules and continuous
    runs straight away, as it does for the app.

    Returns ``_NO_BACKEND`` when none is published or it no longer answers; the caller
    then changes the files itself. The desktop window's backend publishes no address, so
    with only the app open the change is still made offline.
    """
    from gofer.cli.client import ApiClient, ExitCode, _pid_alive, discover_endpoint

    try:
        endpoint = discover_endpoint(data_dir=data_dir)
    except CliError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(int(exc.exit_code)) from exc
    if endpoint.source == "default":
        return _NO_BACKEND
    if endpoint.pid is not None and not _pid_alive(endpoint.pid):
        return _NO_BACKEND
    try:
        return ApiClient(endpoint).request(method, path, body=body, query=query).data
    except CliError as exc:
        if exc.exit_code == ExitCode.UNREACHABLE:
            typer.echo(
                f"The backend at {endpoint.url} didn't answer, so the files were changed directly.",
                err=True,
            )
            return _NO_BACKEND
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(int(exc.exit_code)) from exc


def _parse_param_values(
    param_values: list[str] | None,
    params_json: str | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if params_json:
        try:
            parsed = json.loads(params_json)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"--params-json must be valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise typer.BadParameter("--params-json must decode to an object")
        params.update({str(key): value for key, value in parsed.items()})
    for item in param_values or []:
        if "=" not in item:
            raise typer.BadParameter("--param values must be KEY=VALUE")
        key, raw = item.split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter("--param keys cannot be empty")
        try:
            params[key] = json.loads(raw)
        except json.JSONDecodeError:
            params[key] = raw
    return params


def _workflow_log_context(workflow: str, data_dir: Path | None) -> tuple[str, Path]:
    # Rattish run history lives in the data folder under the registered workflow ID.
    _require_rattish_source(workflow, data_dir)
    rattish_id = _rattish_workflow_id(workflow, data_dir)
    assert rattish_id is not None
    return rattish_id, data_dir or get_data_dir()


@app.command("run")
def run(
    workflow: str = typer.Argument(..., help="Workflow ID, .rattish file or workflow folder"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Check the workflow with rattish preflight instead of running it"
    ),
    param: list[str] | None = typer.Option(
        None,
        "--param",
        help="Workflow input as KEY=VALUE, the same as --input. May be repeated.",
    ),
    params_json: str | None = typer.Option(
        None,
        "--params-json",
        help="Workflow inputs as a JSON object, the same as --inputs-json",
    ),
    input_: list[str] | None = typer.Option(
        None,
        "--input",
        help="Workflow input as KEY=VALUE. May be repeated.",
    ),
    inputs_json: str | None = typer.Option(
        None,
        "--inputs-json",
        help="Workflow inputs as a JSON object",
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Run a Rattish workflow by ID, file or folder."""
    rattish_source = _require_rattish_source(workflow, data_dir)
    workflow_inputs = {
        **_parse_param_values(param, params_json),
        **_parse_param_values(input_, inputs_json),
    }
    if dry_run:
        rattish_commands.preflight(rattish_source, output_format="text", data_dir=data_dir)
        return
    rattish_commands.execute_rattish_run(rattish_source, workflow_inputs, data_dir=data_dir)


@app.command("resume")
def resume(
    workflow: str = typer.Argument(..., help="Workflow ID, .rattish file or workflow folder"),
    run_id: str = typer.Option(..., "--run-id", help="The run to resume"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Resume a failed or stopped run where it stopped, as a new run."""
    rattish_source = _require_rattish_source(workflow, data_dir)
    rattish_commands.execute_rattish_resume(rattish_source, run_id, data_dir=data_dir)


def _print_usage_summary(summary: dict[str, object]) -> None:
    totals = summary.get("totals") if isinstance(summary, dict) else None
    if not isinstance(totals, dict) or not totals.get("agent_calls"):
        return
    console.print(
        "[bold]LLM usage:[/bold] "
        f"calls={totals.get('agent_calls')} "
        f"tokens={totals.get('total_tokens')} "
        f"cost~${float(totals.get('estimated_cost') or 0.0):.6f} "
        f"agent_time={float(totals.get('agent_time_seconds') or 0.0):.2f}s"
    )


@app.command("plan")
def plan(
    workflow: str = typer.Argument(..., help="Workflow ID, .rattish file or workflow folder"),
    output_format: str = format_option(),
    json_output: bool = json_alias_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Check a workflow compiles and this computer can run it (rattish preflight)."""
    json_output = wants_json(output_format, json_output)
    rattish_source = _require_rattish_source(workflow, data_dir)
    rattish_commands.preflight(
        rattish_source, output_format="json" if json_output else "text", data_dir=data_dir
    )


@app.command("usage")
def usage(
    workflow: str = typer.Argument(..., help="Workflow ID, .rattish file or workflow folder"),
    output_format: str = format_option(),
    json_output: bool = json_alias_option(),
    limit: int = typer.Option(10, "--limit", min=1, help="Number of recent runs to inspect"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Show LLM usage summaries for recent workflow runs."""
    from gofer.core.usage import summarize_node_outputs
    from gofer.ui.api import (
        WorkflowLogError,
        list_workflow_run_logs_payload,
        workflow_run_log_payload,
    )

    json_output = wants_json(output_format, json_output)
    try:
        workflow_id, base = _workflow_log_context(workflow, data_dir)
        runs_payload = list_workflow_run_logs_payload(workflow_id, base)
    except (KeyError, WorkflowLogError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    run_summaries = []
    for run_log in (runs_payload.get("runs") or [])[:limit]:
        run_id = str(run_log["id"])
        try:
            payload = workflow_run_log_payload(workflow_id, run_id, base)
        except WorkflowLogError:
            continue
        summary = payload.get("usageSummary")
        if not isinstance(summary, dict):
            summary = summarize_node_outputs(payload.get("nodeOutputs") or {})
        run_summaries.append(
            {
                "runId": run_id,
                "startedAt": payload.get("startedAt"),
                "status": payload.get("status"),
                "summary": summary,
            }
        )

    totals = {
        "agent_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost": 0.0,
        "agent_time_seconds": 0.0,
    }
    for run_summary in run_summaries:
        summary = run_summary["summary"]
        run_totals = summary.get("totals") if isinstance(summary, dict) else {}
        if not isinstance(run_totals, dict):
            continue
        for key in ("agent_calls", "input_tokens", "output_tokens", "total_tokens"):
            totals[key] += int(run_totals.get(key) or 0)
        totals["estimated_cost"] += float(run_totals.get("estimated_cost") or 0.0)
        totals["agent_time_seconds"] += float(run_totals.get("agent_time_seconds") or 0.0)

    payload = {
        "workflowId": workflow_id,
        "runs": run_summaries,
        "totals": totals,
    }
    if json_output:
        sys.stdout.write(json.dumps(payload, default=str))
        sys.stdout.write("\n")
        return

    if not run_summaries:
        console.print(f"No usage records found for [bold]{workflow_id}[/bold].")
        return
    _print_usage_summary({"totals": totals})
    table = Table(title=f"Recent LLM usage for {workflow_id}")
    table.add_column("Run")
    table.add_column("Status")
    table.add_column("Calls", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Agent time", justify="right")
    for run_summary in run_summaries:
        summary = run_summary["summary"]
        run_totals = summary.get("totals") if isinstance(summary, dict) else {}
        if not isinstance(run_totals, dict):
            run_totals = {}
        table.add_row(
            str(run_summary["runId"]),
            str(run_summary.get("status") or "unknown"),
            str(run_totals.get("agent_calls") or 0),
            str(run_totals.get("total_tokens") or 0),
            f"${float(run_totals.get('estimated_cost') or 0.0):.6f}",
            f"{float(run_totals.get('agent_time_seconds') or 0.0):.2f}s",
        )
    console.print(table)


def _find_approval_request(
    store: ApprovalStore,
    run_id: str,
    node_id: str,
    workflow_id: str | None,
) -> ApprovalRequest:
    """Find one approval. A closed one is returned so deciding it explains why it is closed."""
    if workflow_id:
        request = store.get(workflow_id, run_id, node_id)
        if request is None:
            raise ValueError(f"Approval {node_id!r} for run {run_id!r} was not found.")
        return request
    matches = [
        request
        for request in store.list_requests()
        if request.run_id == run_id and request.node_id == node_id
    ]
    candidates = [request for request in matches if request.status == "pending"] or matches
    if not candidates:
        raise ValueError(f"Approval {node_id!r} for run {run_id!r} was not found.")
    if len(candidates) > 1:
        raise ValueError("More than one workflow has this approval; pass --workflow.")
    return candidates[0]


def _approval_waiter_is_live(request: ApprovalRequest) -> bool:
    from gofer.utils.pids import pid_alive

    # Never os.kill(pid, 0): on Windows that ends the waiting run.
    return pid_alive(request.waiter_pid)


@app.command("approvals")
def approvals(
    workflow: str | None = typer.Option(
        None,
        "--workflow",
        "-w",
        help="Only show pending approvals for this workflow ID",
    ),
    all_requests: bool = typer.Option(
        False,
        "--all",
        help="Show pending and completed approval decisions",
    ),
    output_format: str = format_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """List approval gates."""
    from gofer.core.approvals import ApprovalStore

    as_json = wants_json(output_format)
    store = ApprovalStore(data_dir)
    all_loaded_requests = store.list_requests(workflow)
    requests = (
        all_loaded_requests
        if all_requests
        else [request for request in all_loaded_requests if request.status == "pending"]
    )
    if as_json:
        emit_json([request.to_dict() for request in requests])
        return
    if not requests:
        console.print("No approvals" if all_requests else "No pending approvals")
        return
    if all_requests:
        table = Table()
        table.add_column("Workflow")
        table.add_column("Run ID")
        table.add_column("Node")
        table.add_column("Status")
        table.add_column("Decision")
        table.add_column("Message")
    else:
        table = Table("Workflow", "Run ID", "Node", "Requested", "Message")
    for request in requests:
        decision = request.decision
        if all_requests:
            table.add_row(
                request.workflow_id,
                request.run_id,
                request.node_id,
                request.status,
                (
                    f"{decision.decision} by {decision.decided_by}"
                    if decision is not None
                    else request.cancelled_reason or ""
                ),
                request.message,
            )
        else:
            table.add_row(
                request.workflow_id,
                request.run_id,
                request.node_id,
                request.requested_at,
                request.message,
            )
    console.print(table)


def _decide_approval(
    decision: str,
    run_id: str,
    node_id: str,
    workflow: str | None,
    by: str | None,
    notes: str,
    data_dir: Path | None,
    *,
    choice: str | None = None,
    reply: str = "",
) -> None:
    from gofer.core.approvals import ApprovalStore

    verb = "approve" if decision == "approved" else "reject"
    if in_agent_session():
        # A workflow, provider or Rem worker must not decide its own gate. The same
        # refusal as `Raticode rattish approve` (exit 5, user_only).
        err_console.print(
            f"[red]Only you can {verb} a workflow approval. This command is running inside "
            "a Raticode agent turn; run it from your own terminal or use the app.[/red]"
        )
        raise typer.Exit(int(ExitCode.AUTH))
    store = ApprovalStore(data_dir)
    try:
        request = _find_approval_request(store, run_id, node_id, workflow)
        # --by picks which of the gate's named approvers you are deciding as. It is not
        # free text: a name the gate doesn't list (such as "user" or Raticode's own
        # "gofer") would misstate who decided.
        if by is not None and request.status == "pending" and by not in request.approvers:
            allowed = ", ".join(request.approvers)
            raise ValueError(
                f"Approver {by!r} is not allowed; --by must name one of this gate's "
                f"approvers: {allowed}."
                if allowed
                else "This gate names no approvers, so --by isn't used; leave it out."
            )
        if request.options and choice is None and decision != "approved":
            raise ValueError(
                "This approval has options instead of Reject; answer with approve --choice "
                "and one of: " + ", ".join(request.options) + "."
            )
        decided = store.decide(
            request.workflow_id,
            run_id,
            node_id,
            None if request.options else ("approved" if decision == "approved" else "rejected"),
            decided_by=by or "cli",
            notes=notes,
            choice=choice,
            reply=reply,
        )
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    picked = decided.decision.choice if decided.decision is not None else None
    label = (
        f"Answered {picked}:" if picked else ("Approved" if decision == "approved" else "Rejected")
    )
    console.print(f"[green]{label}[/green] {request.workflow_id} {run_id} {node_id}")
    # The run waiting at this gate reads the decision from the same store.
    if not _approval_waiter_is_live(decided):
        err_console.print(
            "No run is waiting at this gate any more. Resume it with "
            f"Raticode rattish resume {decided.workflow_id} {run_id}",
            soft_wrap=True,
        )


_APPROVER_HELP = (
    "Decide as this approver when the gate names approvers. Must be one of the names "
    "the gate lists; it records which approver role decided, not who you are."
)


@app.command("approve")
def approve(
    run_id: str = typer.Argument(..., help="Run ID from the pending approval"),
    node_id: str = typer.Argument(..., help="Approval node ID"),
    workflow: str | None = typer.Option(
        None,
        "--workflow",
        "-w",
        help="Workflow ID when run/node IDs are not unique",
    ),
    by: str | None = typer.Option(None, "--by", help=_APPROVER_HELP),
    choice: str | None = typer.Option(
        None,
        "--choice",
        help="The option to answer with, for a gate that lists options instead of Approve "
        "and Reject",
    ),
    reply: str = typer.Option("", "--reply", help="Typed reply, for a gate that asks for one"),
    notes: str = typer.Option("", "--notes", help="Decision notes"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Approve a pending approval gate. Refused inside Rem and worker turns (exit 5)."""
    _decide_approval(
        "approved", run_id, node_id, workflow, by, notes, data_dir, choice=choice, reply=reply
    )


@app.command("reject")
def reject(
    run_id: str = typer.Argument(..., help="Run ID from the pending approval"),
    node_id: str = typer.Argument(..., help="Approval node ID"),
    workflow: str | None = typer.Option(
        None,
        "--workflow",
        "-w",
        help="Workflow ID when run/node IDs are not unique",
    ),
    by: str | None = typer.Option(None, "--by", help=_APPROVER_HELP),
    reply: str = typer.Option("", "--reply", help="Typed reply, for a gate that asks for one"),
    notes: str = typer.Option("", "--notes", help="Decision notes"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Reject a pending approval gate. Refused inside Rem and worker turns (exit 5)."""
    _decide_approval("rejected", run_id, node_id, workflow, by, notes, data_dir, reply=reply)


@app.command("validate")
def validate(
    workflow: str = typer.Argument(..., help="Workflow ID, .rattish file or workflow folder"),
    output_format: str = format_option(),
    json_output: bool = json_alias_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Check a workflow (rattish check)."""
    json_output = wants_json(output_format, json_output)
    rattish_source = _require_rattish_source(workflow, data_dir)
    rattish_commands.check(
        rattish_source, output_format="json" if json_output else "text", data_dir=data_dir
    )


@app.command("import")
def import_workflow(
    source: Path = typer.Argument(..., help="A .raticode archive"),
    project: Path = typer.Option(
        ..., "--project", help="Project folder that receives the workflow"
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Install a Rattish .raticode archive into a project folder."""
    from gofer.ui.api import WorkflowBundleError, import_rattish_bundle_payload

    base = data_dir or get_data_dir()
    if not source.exists():
        err_console.print(f"[red]{source} not found[/red]")
        raise typer.Exit(1)
    if source.suffix.lower() != ".raticode":
        typer.echo(
            f"{source.name} isn't a .raticode archive. TOML workflows and .gof.zip bundles "
            "can't be imported any more; convert a TOML workflow with "
            "Raticode rattish convert FILE.toml --project PATH.",
            err=True,
        )
        raise typer.Exit(2)
    try:
        imported = import_rattish_bundle_payload(source, project, base)
    except WorkflowBundleError as exc:
        err_console.print(f"[red]Import failed: {exc}[/red]")
        raise typer.Exit(1)
    console.print(
        f"[green]Imported[/green] {imported['id']} → {imported.get('sourcePath') or project}"
    )


@app.command("export")
def export_workflow(
    workflow: str = typer.Argument(..., help="Workflow ID, .rattish file or workflow folder"),
    output: Path = typer.Option(..., "--output", "-o", help="The .raticode archive to create"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Export a registered workflow and its project files into a .raticode archive."""
    from gofer.ui.api import WorkflowBundleError, export_rattish_bundle_payload

    _require_rattish_source(workflow, data_dir)
    rattish_id = _rattish_workflow_id(workflow, data_dir)
    assert rattish_id is not None
    try:
        exported = export_rattish_bundle_payload(rattish_id, output, data_dir)
    except WorkflowBundleError as exc:
        err_console.print(f"[red]Export failed: {exc}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Exported[/green] {rattish_id} → {exported['bundlePath']}")


def _warn_skipped_runs(payload: Any) -> None:
    skipped = payload.get("skippedRuns") if isinstance(payload, dict) else None
    if isinstance(skipped, int) and skipped > 0:
        err_console.print(
            f"[yellow]Skipped {skipped} run record(s) that couldn't be read.[/yellow]"
        )


@logs_app.command("latest")
def logs_latest(
    workflow: str = typer.Argument(..., help="Workflow ID, .rattish file or workflow folder"),
    path_only: bool = typer.Option(False, "--path-only", help="Print only the log path"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Print the latest run log for a workflow."""
    from gofer.ui.api import WorkflowLogError, latest_workflow_log_payload

    try:
        workflow_id, base = _workflow_log_context(workflow, data_dir)
        payload = latest_workflow_log_payload(workflow_id, base)
    except (KeyError, WorkflowLogError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    _warn_skipped_runs(payload)
    log_path = payload.get("logPath")
    if not log_path:
        console.print(f"No run logs found for [bold]{workflow_id}[/bold].")
        return
    if path_only:
        console.print(str(log_path))
        return
    console.print(payload.get("logText") or "")


@logs_app.command("list")
def logs_list(
    workflow: str = typer.Argument(..., help="Workflow ID, .rattish file or workflow folder"),
    offset: int = typer.Option(0, "--offset", min=0, help="Number of runs to skip"),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum runs to list"),
    status: str | None = typer.Option(None, "--status", help="Filter by status"),
    trigger: str | None = typer.Option(None, "--trigger", help="Filter by trigger type"),
    search: str | None = typer.Option(None, "--search", help="Search run metadata and log text"),
    output_format: str = format_option(),
    json_output: bool = json_alias_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """List run logs for a workflow."""
    from gofer.ui.api import WorkflowLogError, list_workflow_run_logs_payload

    json_output = wants_json(output_format, json_output)
    try:
        workflow_id, base = _workflow_log_context(workflow, data_dir)
        payload = list_workflow_run_logs_payload(
            workflow_id,
            base,
            offset=offset,
            limit=limit,
            status=status,
            trigger_type=trigger,
            search=search,
        )
    except (KeyError, WorkflowLogError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    _warn_skipped_runs(payload)
    if json_output:
        sys.stdout.write(json.dumps(payload, default=str))
        sys.stdout.write("\n")
        return

    runs = payload.get("runs") or []
    if not runs:
        console.print(f"No run logs found for [bold]{workflow_id}[/bold].")
        pagination = payload.get("pagination") if isinstance(payload, dict) else {}
        if isinstance(pagination, dict) and pagination.get("searchTruncated"):
            err_console.print(
                "[yellow]Search stopped at the configured log scan byte limit; "
                "results may be incomplete.[/yellow]"
            )
        return

    for run_log in runs:
        console.print(
            "\t".join(
                (
                    str(run_log["id"]),
                    str(run_log.get("startedAt") or "unknown"),
                    str(run_log.get("status") or "unknown"),
                    str(run_log.get("triggerType") or "unknown"),
                    str(run_log.get("logSizeBytes") or 0),
                    str(run_log.get("logPath") or base / "logs" / workflow_id / str(run_log["id"])),
                )
            )
        )
    pagination = payload.get("pagination") if isinstance(payload, dict) else {}
    if isinstance(pagination, dict) and pagination.get("searchTruncated"):
        err_console.print(
            "[yellow]Search stopped at the configured log scan byte limit; "
            "results may be incomplete.[/yellow]"
        )


@logs_app.command("show")
def logs_show(
    workflow: str = typer.Argument(..., help="Workflow ID, .rattish file or workflow folder"),
    run_id: str = typer.Argument(..., help="Run log file name from workflow logs list"),
    offset: int | None = typer.Option(None, "--offset", min=0, help="Read from byte offset"),
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum bytes to read"),
    tail_bytes: int | None = typer.Option(None, "--tail-bytes", min=1, help="Read last N bytes"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Print a specific workflow run log."""
    from gofer.ui.api import WorkflowLogError, workflow_run_log_payload

    try:
        workflow_id, base = _workflow_log_context(workflow, data_dir)
        payload = workflow_run_log_payload(
            workflow_id,
            run_id,
            base,
            offset=offset,
            limit=limit,
            tail_bytes=tail_bytes,
            include_details=False,
        )
    except (KeyError, WorkflowLogError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(payload.get("logText") or "")


@logs_app.command("prune")
def logs_prune(
    workflow: str = typer.Argument(..., help="Workflow ID, .rattish file or workflow folder"),
    keep_last: int | None = typer.Option(
        None,
        "--keep-last",
        min=0,
        help="Always keep newest N runs",
    ),
    keep_days: int | None = typer.Option(
        None,
        "--keep-days",
        min=0,
        help="Keep runs newer than N days",
    ),
    keep_failed_days: int | None = typer.Option(
        None,
        "--keep-failed-days",
        min=0,
        help="Keep failed runs newer than N days",
    ),
    apply: bool = typer.Option(False, "--apply", help="Delete matching runs instead of previewing"),
    output_format: str = format_option(),
    json_output: bool = json_alias_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Preview or prune old workflow run logs and sidecar files."""
    from gofer.ui.api import WorkflowLogError, prune_workflow_run_logs_payload

    json_output = wants_json(output_format, json_output)
    try:
        workflow_id, base = _workflow_log_context(workflow, data_dir)
        payload = prune_workflow_run_logs_payload(
            workflow_id,
            base,
            keep_last=keep_last,
            keep_days=keep_days,
            keep_failed_days=keep_failed_days,
            dry_run=not apply,
        )
    except (KeyError, WorkflowLogError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    _warn_skipped_runs(payload)
    if json_output:
        sys.stdout.write(json.dumps(payload, default=str))
        sys.stdout.write("\n")
        return

    runs = payload.get("runs") or []
    action = "Would remove" if payload.get("dryRun") else "Removed"
    console.print(f"{action} {len(runs)} run(s) for [bold]{workflow_id}[/bold].")
    for run_log in runs:
        console.print(
            "\t".join(
                (
                    str(run_log["id"]),
                    str(run_log.get("startedAt") or "unknown"),
                    str(run_log.get("status") or "unknown"),
                    str(run_log.get("logSizeBytes") or 0),
                )
            )
        )


@app.command("list")
def list_workflows(
    output_format: str = format_option(),
    json_output: bool = json_alias_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """List all workflows in the data folder."""
    from gofer.ui.api import list_workflow_payloads

    json_output = wants_json(output_format, json_output)
    base = data_dir or get_data_dir()
    payload = list_workflow_payloads(base)
    workflows = payload.get("workflows") or []
    if json_output:
        sys.stdout.write(json.dumps(payload, default=str))
        sys.stdout.write("\n")
        return

    if not workflows:
        console.print(f"No workflows found in [bold]{base}[/bold].")
        return

    table = Table("ID", "Name", "Nodes", "Routes", "Status")
    for workflow in workflows:
        table.add_row(
            str(workflow.get("id") or ""),
            str(workflow.get("name") or ""),
            str(workflow.get("nodeCount") or 0),
            str(workflow.get("routeCount") or 0),
            str(workflow.get("status") or ""),
        )
    console.print(table)


def _run_worktrees_text(count: int) -> str:
    return f"{count} run worktree{'' if count == 1 else 's'}"


def _pending_run_worktrees(workflow_id: str, data_dir: Path | None) -> int:
    """How many of the workflow's run worktrees wait to be landed or discarded."""
    from gofer.rattish import run_worktrees

    found = _through_backend(
        data_dir, "GET", f"/workflows/{quote(workflow_id, safe='')}/run-worktrees"
    )
    if isinstance(found, dict):
        return int(found.get("pending") or 0)
    return len(run_worktrees.unlanded_run_ids(data_dir or get_data_dir(), workflow_id.lower()))


@app.command("rm")
def rm(
    workflow: str = typer.Argument(..., help="Workflow ID, .rattish file or workflow folder"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Delete a workflow: unregister it and remove its folder.

    When a headless backend is running for this data folder, the backend deletes it.
    """
    from gofer.ui.api import WorkflowUpdateError, delete_workflow_payload

    _require_rattish_source(workflow, data_dir)
    rattish_id = _rattish_workflow_id(workflow, data_dir)
    if rattish_id is not None:
        if not yes:
            pending = _pending_run_worktrees(rattish_id, data_dir)
            them = "it and its branch" if pending == 1 else "them and their branches"
            waiting = (
                f" It has {_run_worktrees_text(pending)} waiting to be landed or discarded;"
                f" deleting removes {them}, and their changes are lost."
                if pending
                else ""
            )
            typer.confirm(
                f"Delete Rattish workflow '{rattish_id}' and its folder?{waiting}", abort=True
            )
        sent = _through_backend(
            data_dir,
            "DELETE",
            f"/workflows/{quote(rattish_id, safe='')}",
        )
        if sent is _NO_BACKEND:
            try:
                sent = delete_workflow_payload(rattish_id, data_dir)
            except WorkflowUpdateError as exc:
                err_console.print(f"[red]{exc}[/red]")
                raise typer.Exit(1)
        result = sent if isinstance(sent, dict) else {}
        removed = int(result.get("runWorktreesRemoved") or 0)
        branches = "its branch" if removed == 1 else "their branches"
        detail = f" and {_run_worktrees_text(removed)} with {branches}" if removed else ""
        console.print(f"[green]Deleted[/green] {rattish_id}{detail}")
        for error in result.get("runWorktreeErrors") or []:
            err_console.print(f"[yellow]Couldn't remove run worktree {error}[/yellow]")


@app.command("rename")
def rename(
    workflow: str = typer.Argument(..., help="Workflow ID"),
    name: str = typer.Option(..., "--name", help="New workflow name"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Rename a workflow and update its ID to match the new name.

    When a headless backend is running for this data folder, the backend renames it.
    """
    from gofer.ui.api import (
        WorkflowAlreadyExistsError,
        WorkflowUpdateError,
        rename_workflow_payload,
    )

    sent = _through_backend(
        data_dir, "POST", f"/workflows/{quote(workflow, safe='')}/rename", {"name": name}
    )
    if sent is not _NO_BACKEND:
        saved = sent["workflow"]
        console.print(f"[green]Renamed[/green] workflow to {saved['name']} ({saved['id']})")
        return
    try:
        saved = rename_workflow_payload(workflow, name, data_dir)
    except (WorkflowAlreadyExistsError, WorkflowUpdateError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Renamed[/green] workflow to {saved['name']} ({saved['id']})")


@app.command("duplicate")
def duplicate(
    workflow: str = typer.Argument(..., help="Workflow ID"),
    name: str | None = typer.Option(None, "--name", help="Name for duplicate"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Duplicate a workflow with a new ID and name.

    When a headless backend is running for this data folder, the backend makes the copy.
    """
    from gofer.ui.api import (
        WorkflowAlreadyExistsError,
        WorkflowCreateError,
        WorkflowUpdateError,
        duplicate_workflow_payload,
    )

    sent = _through_backend(
        data_dir, "POST", f"/workflows/{quote(workflow, safe='')}/duplicate", {"name": name}
    )
    if sent is not _NO_BACKEND:
        saved = sent["workflow"]
        console.print(f"[green]Duplicated[/green] workflow to {saved['name']} ({saved['id']})")
        return
    try:
        saved = duplicate_workflow_payload(workflow, name, data_dir)
    except (WorkflowAlreadyExistsError, WorkflowCreateError, WorkflowUpdateError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Duplicated[/green] workflow to {saved['name']} ({saved['id']})")
