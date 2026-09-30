from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from gofer.core.runner import (
    RunnerQueueStore,
    default_runner_capabilities,
    run_worker_once,
    workflow_required_capabilities,
)
from gofer.core.workflow import AgenticWorkflow, resolve_workflow_parameters
from gofer.utils.paths import get_data_dir

app = typer.Typer(help="Manage remote runners and queued workflow runs", no_args_is_help=True)
console = Console()


@app.command("register")
def register(
    name: str = typer.Option("local", "--name", help="Runner display name"),
    runner_id: str | None = typer.Option(None, "--id", help="Stable runner ID"),
    label: Annotated[list[str] | None, typer.Option("--label", help="Runner label")] = None,
    provider_cli: Annotated[
        list[str] | None,
        typer.Option("--provider-cli", help="Available provider CLI name"),
    ] = None,
    workspace_root: Annotated[
        list[Path] | None,
        typer.Option("--workspace-root", help="Workspace root available to the runner"),
    ] = None,
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Register or update a local runner record."""
    store = RunnerQueueStore(data_dir)
    capabilities = default_runner_capabilities(
        [str(path) for path in workspace_root] if workspace_root else None
    )
    if provider_cli is not None:
        capabilities["provider_clis"] = provider_cli
    record = store.register_runner(
        runner_id or uuid.uuid4().hex,
        name,
        label or [],
        capabilities,
    )
    console.print(f"[green]Registered[/green] {record.id} ({record.name})")


@app.command("list")
def list_runners(data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True)) -> None:
    """List known runners."""
    rows = RunnerQueueStore(data_dir).list_runners()
    if not rows:
        console.print("No runners registered.")
        return
    table = Table("ID", "Name", "Status", "Labels", "Provider CLIs", "Current Run")
    for runner in rows:
        table.add_row(
            runner.id,
            runner.name,
            runner.status,
            ", ".join(runner.labels),
            ", ".join(runner.capabilities.get("provider_clis") or []),
            runner.current_run_id or "",
        )
    console.print(table)


@app.command("queue")
def queue_run(
    workflow: str = typer.Argument(..., help="Workflow ID or TOML path"),
    priority: int = typer.Option(0, "--priority", help="Higher priority runs first"),
    trigger: str = typer.Option("manual", "--trigger", help="Requested trigger name"),
    label: Annotated[
        list[str] | None,
        typer.Option("--label", help="Target runner label"),
    ] = None,
    parameter: Annotated[
        list[str] | None,
        typer.Option("--param", help="Run parameter as KEY=JSON_VALUE or KEY=VALUE"),
    ] = None,
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Queue a workflow run for a separate runner process."""
    base = data_dir or get_data_dir()
    parameters = _parse_parameters(parameter or [])
    try:
        workflow_obj, workflow_path = _resolve_workflow(workflow, base)
        workflow_obj.validate(workflow_path, base)
        resolved_inputs = resolve_workflow_parameters(workflow_obj.config, parameters)
    except Exception as exc:
        console.print(f"[red]Queue failed: {exc}[/red]")
        raise typer.Exit(1)

    queued = RunnerQueueStore(base).enqueue(
        workflow_obj.config.id,
        workflow_path,
        priority=priority,
        trigger=trigger,
        parameters={"workflowInputs": resolved_inputs},
        target_labels=label or [],
        required_capabilities=workflow_required_capabilities(workflow_obj),
    )
    console.print(f"[green]Queued[/green] {queued.id} {queued.workflow_id}")


@app.command("status")
def queue_status(
    run_id: str | None = typer.Argument(None, help="Queued run ID"),
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum runs to list"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Inspect queued and running work."""
    store = RunnerQueueStore(data_dir)
    if run_id:
        run = store.get_run(run_id)
        if run is None:
            console.print(f"[red]Queued run '{run_id}' not found[/red]")
            raise typer.Exit(1)
        console.print(json.dumps(run.to_payload(), indent=2, sort_keys=True))
        return
    runs = store.list_runs(limit)
    if not runs:
        console.print("No queued runs.")
        return
    table = Table("ID", "Workflow", "Status", "Priority", "Runner", "Message")
    for run in runs:
        table.add_row(
            run.id,
            run.workflow_id,
            run.status,
            str(run.priority),
            run.runner_id or "",
            run.message or "",
        )
    console.print(table)


@app.command("cancel")
def cancel(
    run_id: str = typer.Argument(..., help="Queued run ID"),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Cancel queued or running work."""
    try:
        run = RunnerQueueStore(data_dir).cancel_run(run_id)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Cancel requested[/green] {run.id} ({run.status})")


@app.command("start")
def start(
    name: str = typer.Option("local", "--name", help="Runner display name"),
    runner_id: str | None = typer.Option(None, "--id", help="Stable runner ID"),
    label: Annotated[list[str] | None, typer.Option("--label", help="Runner label")] = None,
    provider_cli: Annotated[
        list[str] | None,
        typer.Option("--provider-cli", help="Available provider CLI name"),
    ] = None,
    once: bool = typer.Option(False, "--once", help="Process at most one queued run"),
    poll_seconds: float = typer.Option(2.0, "--poll-seconds", min=0.1),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Start a local runner process that executes queued workflows."""
    store = RunnerQueueStore(data_dir)
    capabilities = default_runner_capabilities()
    if provider_cli is not None:
        capabilities["provider_clis"] = provider_cli
    record = store.register_runner(
        runner_id or uuid.uuid4().hex,
        name,
        label or [],
        capabilities,
    )
    console.print(f"[green]Runner started[/green] {record.id} ({record.name})")
    while True:
        result = run_worker_once(store, record.id, data_dir=data_dir)
        if result is not None:
            console.print(f"{result.id}\t{result.workflow_id}\t{result.status}")
        if once:
            return
        time.sleep(poll_seconds)


def _resolve_workflow(workflow: str, data_dir: Path) -> tuple[AgenticWorkflow, Path]:
    path = Path(workflow).expanduser()
    if path.exists():
        return AgenticWorkflow.from_file(path), path
    workflow_path = data_dir / f"{workflow}.toml"
    if not workflow_path.exists():
        raise KeyError(f"Workflow '{workflow}' not found")
    return AgenticWorkflow.from_file(workflow_path), workflow_path


def _parse_parameters(values: list[str]) -> dict[str, object]:
    params: dict[str, object] = {}
    for value in values:
        if "=" not in value:
            raise typer.BadParameter("--param values must be KEY=VALUE")
        key, raw = value.split("=", 1)
        try:
            params[key] = json.loads(raw)
        except json.JSONDecodeError:
            params[key] = raw
    return params
