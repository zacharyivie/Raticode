from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.table import Table

from gofer.cli.output import emit_json, format_option, wants_json
from gofer.utils.paths import get_data_dir

if TYPE_CHECKING:
    from gofer.core.scheduler import WorkflowScheduler
    from gofer.core.workflow import AgenticWorkflow

app = typer.Typer(
    help=(
        "[Legacy TOML] Manage workflow schedules. "
        "For Rattish, use Raticode rattish triggers add cron."
    ),
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


def _data_dir() -> Path:
    return get_data_dir()


def _default_db() -> Path:
    return _data_dir() / "schedules.db"


def _pid_file() -> Path:
    return _data_dir() / "scheduler.pid"


# A background scheduler that exits this soon failed to start.
_START_CHECK_SECONDS = 1.0


def _foreground_command(db_path: Path) -> list[str]:
    """The command that runs this scheduler in the foreground in a new process.

    A packaged CLI is a single executable that takes Raticode's own arguments, so it
    can't be started with ``-m gofer.cli.main`` like a Python interpreter.
    """
    suffix = ["schedule", "start", "--foreground", "--db", str(db_path)]
    if getattr(sys, "frozen", False):
        return [sys.executable, *suffix]
    return [sys.executable, "-m", "gofer.cli.main", *suffix]


def _read_pid(path: Path) -> int | None:
    """The process ID in the scheduler's PID file, or None when it isn't one."""
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def _is_scheduler(pid: int) -> bool:
    """Whether ``pid`` still looks like the background scheduler.

    A PID file outlives its process, and the ID can be reused by any program. Where
    the command line can be read (Linux), a process that isn't a scheduler doesn't count.
    """
    from gofer.utils.pids import pid_alive

    if not pid_alive(pid):
        return False
    try:
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True
    return b"schedule" in command and b"--foreground" in command


def _get_scheduler(db: Path) -> WorkflowScheduler:
    from gofer.core.scheduler import WorkflowScheduler

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
    err_console.print("[yellow]Agent filesystem access outside working_dir:[/yellow]")
    for warning in warnings:
        err_console.print(f"[yellow]- {warning}[/yellow]")


@app.command("add")
def add(
    workflow_file: Path = typer.Argument(..., help="Workflow TOML file"),
    db: Path | None = typer.Option(None, "--db"),
) -> None:
    """Add a workflow to the schedule."""
    from gofer.cli.commands._legacy import refuse_rattish
    from gofer.core.workflow import AgenticWorkflow

    refuse_rattish(
        str(workflow_file),
        None,
        "schedule add",
        "Raticode rattish triggers add cron {source} --help",
    )
    try:
        wf = AgenticWorkflow.from_file(workflow_file)
        wf.validate()
        _print_agent_access_summary(wf)
        scheduler = _get_scheduler(db or _default_db())
        scheduler.add_workflow(wf, workflow_file)
    except Exception as exc:
        err_console.print(f"[red]Schedule failed: {exc}[/red]")
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
def list_schedules(
    db: Path | None = typer.Option(None, "--db"),
    output_format: str = format_option(),
) -> None:
    """List all scheduled workflows."""
    as_json = wants_json(output_format)
    scheduler = _get_scheduler(db or _default_db())
    jobs = scheduler.list_workflows()
    if as_json:
        emit_json(jobs)
        return
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
    from gofer.utils.process import build_subprocess_env

    db_path = db or _default_db()

    if foreground:
        _run_foreground(db_path)
        return

    pid_path = _pid_file()
    if pid_path.exists():
        existing_pid = _read_pid(pid_path)
        try:
            if existing_pid is None or not _is_scheduler(existing_pid):
                raise ProcessLookupError
            err_console.print(f"[yellow]Scheduler already running (PID {existing_pid})[/yellow]")
            raise typer.Exit(1)
        except (ProcessLookupError, PermissionError):
            pid_path.unlink(missing_ok=True)

    proc = subprocess.Popen(
        _foreground_command(db_path),
        env=build_subprocess_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        code = proc.wait(timeout=_START_CHECK_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    else:
        err_console.print(
            f"[red]The scheduler stopped right after starting (exit {code}).[/red] "
            "Run Raticode schedule start --foreground to see why."
        )
        raise typer.Exit(1)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(proc.pid))
    console.print(f"[green]Scheduler started[/green] in background (PID {proc.pid})")
    console.print("Run [bold]Raticode schedule stop[/bold] to stop it.")


@app.command("stop")
def stop() -> None:
    """Stop the background scheduler."""
    pid_path = _pid_file()
    if not pid_path.exists():
        err_console.print("[yellow]No background scheduler found.[/yellow]")
        raise typer.Exit(1)

    pid = _read_pid(pid_path)
    if pid is None:
        pid_path.unlink(missing_ok=True)
        err_console.print("[yellow]The scheduler's PID file was unreadable; removed it.[/yellow]")
        raise typer.Exit(1)
    if not _is_scheduler(pid):
        pid_path.unlink(missing_ok=True)
        err_console.print(f"[yellow]Scheduler (PID {pid}) was not running.[/yellow]")
        return
    try:
        os.kill(pid, signal.SIGTERM)
        pid_path.unlink(missing_ok=True)
        console.print(f"[green]Scheduler stopped[/green] (PID {pid})")
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        err_console.print(f"[yellow]Scheduler (PID {pid}) was not running.[/yellow]")


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
