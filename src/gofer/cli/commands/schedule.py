from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from gofer.core.scheduler import WorkflowScheduler
from gofer.core.workflow import AgenticWorkflow
from gofer.utils.paths import get_data_dir
from gofer.utils.process import build_subprocess_env

app = typer.Typer(help="Manage workflow schedules", no_args_is_help=True)
console = Console()


def _data_dir() -> Path:
    return get_data_dir()


def _default_db() -> Path:
    return _data_dir() / "schedules.db"


def _pid_file() -> Path:
    return _data_dir() / "scheduler.pid"


def _get_scheduler(db: Path) -> WorkflowScheduler:
    db.parent.mkdir(parents=True, exist_ok=True)
    return WorkflowScheduler(db_path=db)


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


@app.command("add")
def add(
    workflow_file: Path = typer.Argument(..., help="Workflow TOML file"),
    db: Path | None = typer.Option(None, "--db"),
) -> None:
    """Add a workflow to the schedule."""
    try:
        wf = AgenticWorkflow.from_file(workflow_file)
        wf.validate()
        _print_agent_access_summary(wf)
        scheduler = _get_scheduler(db or _default_db())
        scheduler.add_workflow(wf, workflow_file)
    except Exception as exc:
        console.print(f"[red]Schedule failed: {exc}[/red]")
        raise typer.Exit(1)
    else:
        console.print(f"[green]Scheduled[/green] '{wf.config.id}'")


@app.command("remove")
def remove(
    workflow_id: str = typer.Argument(...),
    db: Path | None = typer.Option(None, "--db"),
) -> None:
    """Remove a workflow from the schedule."""
    scheduler = _get_scheduler(db or _default_db())
    scheduler.remove_workflow(workflow_id)
    console.print(f"Removed '{workflow_id}'")


@app.command("list")
def list_schedules(db: Path | None = typer.Option(None, "--db")) -> None:
    """List all scheduled workflows."""
    scheduler = _get_scheduler(db or _default_db())
    jobs = scheduler.list_workflows()
    if not jobs:
        console.print("No scheduled workflows.")
        return
    table = Table("ID", "Name", "Next Run")
    for j in jobs:
        table.add_row(j["id"], j["name"], j["next_run"])
    console.print(table)


@app.command("start")
def start(
    foreground: bool = typer.Option(False, "--foreground", "-f", help="Run in the foreground"),
    db: Path | None = typer.Option(None, "--db"),
) -> None:
    """Start the scheduler (background by default)."""
    db_path = db or _default_db()

    if foreground:
        _run_foreground(db_path)
        return

    pid_path = _pid_file()
    if pid_path.exists():
        existing_pid = int(pid_path.read_text().strip())
        try:
            os.kill(existing_pid, 0)
            console.print(f"[yellow]Scheduler already running (PID {existing_pid})[/yellow]")
            raise typer.Exit(1)
        except (ProcessLookupError, PermissionError):
            pid_path.unlink(missing_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "gofer.cli.main",
        "schedule",
        "start",
        "--foreground",
        "--db",
        str(db_path),
    ]

    proc = subprocess.Popen(
        cmd,
        env=build_subprocess_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(proc.pid))
    console.print(f"[green]Scheduler started[/green] in background (PID {proc.pid})")
    console.print("Run [bold]gof schedule stop[/bold] to stop it.")


@app.command("stop")
def stop() -> None:
    """Stop the background scheduler."""
    pid_path = _pid_file()
    if not pid_path.exists():
        console.print("[yellow]No background scheduler found.[/yellow]")
        raise typer.Exit(1)

    pid = int(pid_path.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        pid_path.unlink(missing_ok=True)
        console.print(f"[green]Scheduler stopped[/green] (PID {pid})")
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        console.print(f"[yellow]Scheduler (PID {pid}) was not running.[/yellow]")


def _run_foreground(db_path: Path) -> None:
    scheduler = _get_scheduler(db_path)
    scheduler.start()
    console.print("[green]Scheduler running[/green]. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.shutdown()
        console.print("Scheduler stopped.")
