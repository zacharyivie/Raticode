from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from gofer.core.watcher import WorkflowWatcher
from gofer.core.workflow import AgenticWorkflow
from gofer.utils.paths import get_data_dir

app = typer.Typer(help="Run workflow file/folder watchers", no_args_is_help=True)
console = Console()


def _workflow_files(data_dir: Path) -> list[Path]:
    return sorted(data_dir.glob("*.toml"))


def _sync_watchers(data_dir: Path, watcher: WorkflowWatcher) -> int:
    count = 0
    for path in _workflow_files(data_dir):
        try:
            workflow = AgenticWorkflow.from_file(path)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Skipping invalid workflow[/yellow] {path}: {exc}")
            continue
        if workflow.config.watch is None:
            continue
        try:
            workflow.validate()
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Skipping invalid workflow[/yellow] {path}: {exc}")
            continue
        _print_agent_access_summary(workflow)
        try:
            watcher.add_workflow(workflow, path)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Skipping invalid watcher[/yellow] {path}: {exc}")
            continue
        count += 1
    return count


def _print_agent_access_summary(wf: AgenticWorkflow) -> None:
    warnings = [
        warning
        for warning in wf.resource_warnings()
        if "grants provider filesystem access outside working_dir" in warning
    ]
    if not warnings:
        return
    console.print("[yellow]Agent filesystem access outside working_dir:[/yellow]")
    for warning in warnings:
        console.print(f"[yellow]- {warning}[/yellow]")


@app.command("list")
def list_watchers(
    data_dir: Path | None = typer.Option(None, "--data-dir", help="Raticode data directory"),
) -> None:
    """List workflows in the data directory that have file/folder watchers."""
    base = data_dir or get_data_dir()
    table = Table("ID", "Path", "Glob", "Mode", "Concurrency", "Recursive", "Debounce")
    found = False
    for path in _workflow_files(base):
        try:
            workflow = AgenticWorkflow.from_file(path)
        except Exception:
            continue
        watch = workflow.config.watch
        if watch is None:
            continue
        found = True
        table.add_row(
            workflow.config.id,
            str(watch.path),
            watch.glob,
            watch.mode,
            str(watch.max_concurrency),
            str(watch.recursive),
            f"{watch.debounce_seconds:g}s",
        )
    if not found:
        console.print("No watched workflows.")
        return
    console.print(table)


@app.command("start")
def start(
    data_dir: Path | None = typer.Option(None, "--data-dir", help="Raticode data directory"),
    poll_interval: float = typer.Option(
        1.0,
        "--poll-interval",
        min=0.1,
        help="Seconds between filesystem scans.",
    ),
) -> None:
    """Run file/folder watchers in the foreground."""
    base = data_dir or get_data_dir()
    watcher = WorkflowWatcher(poll_interval_seconds=poll_interval)
    count = _sync_watchers(base, watcher)
    if count == 0:
        console.print(f"No watched workflows found in [bold]{base}[/bold].")
        raise typer.Exit(1)

    watcher.start()
    console.print(f"[green]Watching[/green] {count} workflow(s) in [bold]{base}[/bold].")
    console.print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.shutdown()
        console.print("Watcher stopped.")
