from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

import typer

from gofer.cli.commands import (
    agent,
    doctor,
    provider,
    rattish,
    runner,
    schedule,
    schema,
    watch,
    workflow,
)

app = typer.Typer(
    name="gof",
    help="Raticode. For machine-readable authoring help, run: gof schema --format json",
    no_args_is_help=True,
)
app.add_typer(workflow.app, name="workflow")
app.add_typer(agent.app, name="agent")
app.add_typer(provider.app, name="provider")
app.add_typer(runner.app, name="runner")
app.add_typer(schedule.app, name="schedule")
app.add_typer(watch.app, name="watch")
app.add_typer(rattish.app, name="rattish")
app.command("doctor")(doctor.doctor)
app.command("schema")(schema.schema_command)

ui_app = typer.Typer(help="Run the workflow studio API", no_args_is_help=True)
app.add_typer(ui_app, name="ui")


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"gof {version('gofer-flow')}")
        raise typer.Exit()


@app.callback()
def main(
    show_version: bool = typer.Option(
        False,
        "--version",
        callback=_show_version,
        is_eager=True,
        help="Show the installed version and exit.",
    ),
) -> None:
    """Raticode command line tools."""


@ui_app.command("second-brain", hidden=True)
def second_brain(
    root: Path = typer.Option(..., "--root"),
    report_format: str = typer.Option("md", "--report-format"),
    report_theme: str = typer.Option("auto", "--report-theme"),
) -> None:
    """Serve the native Rem knowledge tools over MCP stdio."""
    from gofer.ui.second_brain import serve_second_brain

    serve_second_brain(root, report_format, report_theme)


@ui_app.command("serve")
def serve_ui(
    host: str = typer.Option("127.0.0.1", "--host", help="API bind host"),
    port: int = typer.Option(
        8765,
        "--port",
        help="API bind port. Use 0 to let the OS choose a free port.",
    ),
    data_dir: Path | None = typer.Option(
        None,
        "--data-dir",
        help=(
            "Raticode app data directory for global settings, schedules, registries, and app state."
        ),
    ),
) -> None:
    """Serve JSON endpoints used by the React workflow studio."""
    from gofer.ui.server import serve

    serve(host=host, port=port, data_dir=data_dir)


@app.command("licenses")
def export_licenses(
    output: Path = typer.Option(
        ..., "--output", help="New directory for licenses and source archives"
    ),
) -> None:
    """Export the third-party notices bundled with this build."""
    import shutil
    import sys

    frozen_root = getattr(sys, "_MEIPASS", None)
    root = Path(frozen_root) if frozen_root else Path(__file__).resolve().parents[3] / "dist"
    bundle = root / "third-party-licenses"
    if not (bundle / "inventory.json").is_file():
        typer.echo(
            "License bundle is missing. Build the backend before exporting notices.", err=True
        )
        raise typer.Exit(1)
    try:
        shutil.copytree(bundle, output)
    except OSError as exc:
        typer.echo(f"Could not export licenses: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Exported licenses and sources to {output.resolve()}")


if __name__ == "__main__":
    app()
