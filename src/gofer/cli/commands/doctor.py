from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console

from gofer.cli.output import format_option, json_alias_option, wants_json

if TYPE_CHECKING:
    from gofer.core.health import HealthDiagnostic, HealthReport

console = Console()


def run_health_checks(*, data_dir: Path | None, workflow: str | None) -> HealthReport:
    """Load the health checks only when doctor runs; they pull in the workflow engine."""
    from gofer.core.health import run_health_checks as run_checks

    return run_checks(data_dir=data_dir, workflow=workflow)


def doctor(
    workflow: str | None = typer.Option(
        None,
        "--workflow",
        "-w",
        help="Workflow ID, file stem, or TOML path to include in diagnostics.",
    ),
    output_format: str = format_option(),
    json_output: bool = json_alias_option(),
    data_dir: Path | None = typer.Option(None, "--data-dir", hidden=True),
    secrets_dir: Path | None = typer.Option(
        None,
        "--secrets-dir",
        help=(
            "Check this headless secrets folder. Defaults to $CREDENTIALS_DIRECTORY, then "
            "the folder a running headless backend reads."
        ),
    ),
) -> None:
    """Inspect local Raticode readiness without making network calls."""
    json_output = wants_json(output_format, json_output)
    folder_note = _use_secrets_dir(secrets_dir, data_dir)
    report = run_health_checks(data_dir=data_dir, workflow=workflow)
    if folder_note is not None:
        report.diagnostics.insert(0, folder_note)
    if json_output:
        sys.stdout.write(json.dumps(report.to_dict(), indent=2) + "\n")
    else:
        _print_human_report(report.diagnostics)

    if not report.ok:
        raise typer.Exit(code=1)


def _use_secrets_dir(option: Path | None, data_dir: Path | None) -> HealthDiagnostic | None:
    """Point this doctor run at the secrets folder a headless backend would read.

    A folder named here or by $CREDENTIALS_DIRECTORY must pass the checks. The running
    backend's folder may be private to its service (systemd mounts one per unit), so a
    folder doctor can't check is a warning that points at the backend's own report.
    """
    from gofer.core.health import HealthDiagnostic
    from gofer.core.secrets_folder import (
        SecretsFolderError,
        configure_secrets_dir,
        resolve_secrets_dir,
    )

    folder = resolve_secrets_dir(option)
    from_backend = folder is None
    if folder is None:
        folder = _running_backend_secrets_dir(data_dir)
    if folder is None:
        return None
    try:
        configure_secrets_dir(folder)
    except SecretsFolderError as exc:
        if not from_backend:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc
        return HealthDiagnostic(
            id="secrets.folder",
            severity="warning",
            subject=str(folder),
            message=(
                f"The running backend reads the secrets folder {folder}, which doctor can't "
                f"check from here ({exc}). `Raticode api GET /doctor` shows the backend's view."
            ),
            detail={"path": str(folder)},
        )
    return None


def _running_backend_secrets_dir(data_dir: Path | None) -> Path | None:
    from gofer.ui.headless import read_endpoint
    from gofer.utils.paths import get_data_dir

    try:
        record = read_endpoint(data_dir or get_data_dir())
    except (OSError, ValueError):
        return None
    folder = record.get("secretsDir")
    return Path(folder) if isinstance(folder, str) and folder else None


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
