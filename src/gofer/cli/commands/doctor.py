from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console

from gofer.core.health import HealthDiagnostic, run_health_checks

console = Console()


def doctor(
    workflow: str | None = typer.Option(
        None,
        "--workflow",
        "-w",
        help="Workflow ID, file stem, or TOML path to include in diagnostics.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON diagnostics.",
    ),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
) -> None:
    """Inspect local Raticode readiness without making network calls."""
    report = run_health_checks(data_dir=data_dir, workflow=workflow)
    if json_output:
        sys.stdout.write(json.dumps(report.to_dict(), indent=2) + "\n")
    else:
        _print_human_report(report.diagnostics)

    if not report.ok:
        raise typer.Exit(code=1)


def _print_human_report(diagnostics: list[HealthDiagnostic]) -> None:
    errors = [item for item in diagnostics if item.severity == "error"]
    warnings = [item for item in diagnostics if item.severity == "warning"]
    ok_items = [item for item in diagnostics if item.severity == "ok"]

    console.print("[bold]Raticode doctor[/bold]")
    if errors:
        console.print("\n[red]Errors[/red]")
        for item in errors:
            console.print(f"- {item.message}")
    if warnings:
        console.print("\n[yellow]Warnings[/yellow]")
        for item in warnings:
            console.print(f"- {item.message}")
    if ok_items:
        console.print("\n[green]Ready checks[/green]")
        for item in ok_items:
            console.print(f"- {item.message}")
