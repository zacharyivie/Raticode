# ruff: noqa: E402
from __future__ import annotations

import sys

if sys.argv[1:2] == ["slot"]:
    # Command slot wrappers and the Claude Code hook run on every gated command
    # and shell call. Loading the full CLI first would add most of a second.
    from gofer.core.command_slots import main as _slot_main

    raise SystemExit(_slot_main(sys.argv[2:]))

from pathlib import Path

import typer

from gofer.cli.commands import (
    agent,
    api,
    brain,
    doctor,
    org,
    provider,
    rattish,
    rattish_triggers,
    rem,
    rem_api,
    runner,
    schedule,
    schema,
    swarm,
    system,
    tickets,
    tokens,
    watch,
    workflow,
    workflows,
)
from gofer.utils.version import installed_version

app = typer.Typer(
    name="Raticode",
    help=(
        "Raticode command line tools. Workflows are written in Rattish: Raticode rattish "
        "docs --format json lists the installed language docs, contracts and schemas. "
        "Commands that print records, such as lists and status, accept --format json. "
        "Commands that talk to a running backend print a JSON envelope by default; the "
        "others print bare JSON data with --format json. Errors go to stderr. Groups "
        "marked [Legacy TOML] work only with older TOML workflows."
    ),
    no_args_is_help=True,
)
app.add_typer(workflow.app, name="workflow")
app.add_typer(agent.app, name="agent")
app.add_typer(provider.app, name="provider")
app.add_typer(runner.app, name="runner")
app.add_typer(schedule.app, name="schedule")
app.add_typer(watch.app, name="watch")
app.add_typer(rattish.app, name="rattish")
app.add_typer(rem.app, name="rem")
rem_api.register(rem.app)
app.add_typer(tickets.app, name="tickets")
app.add_typer(tickets.selfwork_app, name="selfwork")
app.add_typer(tokens.app, name="tokens")
# Thin clients of a running backend (`Raticode serve --headless`); see the CLI headless guide.
workflows.register(rattish.app, rattish_triggers.app)
app.add_typer(org.app, name="org")
app.add_typer(swarm.app, name="swarm")
app.add_typer(brain.app, name="brain")
app.add_typer(system.usage_app, name="usage")
app.add_typer(system.projects_app, name="projects")
app.add_typer(system.devices_app, name="devices")
app.add_typer(system.threads_app, name="threads")
system.register_provider_commands(provider.app)
app.command("doctor")(doctor.doctor)
app.command("schema")(schema.schema_command)
app.command("api")(api.api_command)
app.command("wait")(api.wait_command)
app.command("completion")(api.completion_command)

ui_app = typer.Typer(help="Run the workflow studio API", no_args_is_help=True)
app.add_typer(ui_app, name="ui")


def _show_version(value: bool) -> None:
    if value:
        typer.echo(f"Raticode {installed_version() or '0.0.0+source'}")
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
    non_interactive: bool = typer.Option(
        False,
        "--non-interactive",
        help="Never prompt; commands that need confirmation fail unless given --yes. "
        "Same as RATICODE_NON_INTERACTIVE=1.",
    ),
) -> None:
    """Raticode command line tools."""
    if non_interactive:
        import os

        from gofer.cli.client import ENV_NON_INTERACTIVE

        os.environ[ENV_NON_INTERACTIVE] = "1"


@ui_app.command("second-brain", hidden=True)
def second_brain(
    root: Path = typer.Option(..., "--root"),
    report_format: str = typer.Option("md", "--report-format"),
    report_theme: str = typer.Option("auto", "--report-theme"),
    library_data_dir: Path | None = typer.Option(None, "--library-data-dir"),
    brain_id: list[str] | None = typer.Option(None, "--brain-id"),
    thread_id: str = typer.Option("", "--thread-id"),
    project_root: str = typer.Option("", "--project-root"),
    provider: str = typer.Option("", "--provider"),
    model: str = typer.Option("", "--model"),
) -> None:
    """Serve the native Rem knowledge tools over MCP stdio."""
    from gofer.ui.second_brain import serve_registered_brains, serve_second_brain

    if library_data_dir is not None and brain_id:
        serve_registered_brains(
            library_data_dir,
            brain_id,
            report_format,
            report_theme,
            {
                "threadId": thread_id,
                "projectRoot": project_root,
                "provider": provider,
                "model": model,
            },
        )
        return

    serve_second_brain(root, report_format, report_theme)


_HOST_OPTION = typer.Option(
    "127.0.0.1", "--host", help="API bind host. Headless mode accepts only loopback addresses."
)
_PORT_OPTION = typer.Option(
    8765, "--port", help="API bind port. Use 0 to let the OS choose a free port."
)
_DATA_DIR_OPTION = typer.Option(
    None,
    "--data-dir",
    help=(
        "Raticode app data directory for global settings, schedules, registries, and app "
        "state. Defaults to GOFER_DATA_DIR or the standard data folder."
    ),
)
_HEADLESS_OPTION = typer.Option(
    False,
    "--headless",
    help=(
        "Run with no desktop window: listen on loopback only, write the owner token and "
        "endpoint to owner-only files in <data>/headless/, and load Rem's settings from a file."
    ),
)
_TOKEN_FILE_OPTION = typer.Option(
    None,
    "--token-file",
    help="Headless: read the owner token from this owner-only file (mode 600) instead.",
)
_REM_SETTINGS_OPTION = typer.Option(
    None,
    "--rem-settings",
    help="Headless: Rem settings JSON. Defaults to <data>/headless/rem-settings.json.",
)


def _serve(
    host: str,
    port: int,
    data_dir: Path | None,
    headless: bool,
    token_file: Path | None,
    rem_settings: Path | None,
) -> None:
    from gofer.ui.headless import HeadlessError
    from gofer.ui.server import serve

    if not headless and (token_file is not None or rem_settings is not None):
        typer.echo("--token-file and --rem-settings need --headless.", err=True)
        raise typer.Exit(2)
    try:
        serve(
            host=host,
            port=port,
            data_dir=data_dir,
            headless=headless,
            token_file=token_file,
            rem_settings=rem_settings,
        )
    except HeadlessError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc


@app.command("serve")
def serve_command(
    host: str = _HOST_OPTION,
    port: int = _PORT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
    headless: bool = _HEADLESS_OPTION,
    token_file: Path | None = _TOKEN_FILE_OPTION,
    rem_settings: Path | None = _REM_SETTINGS_OPTION,
) -> None:
    """Run the Raticode backend API. Add --headless for servers and containers."""
    _serve(host, port, data_dir, headless, token_file, rem_settings)


@ui_app.command("serve")
def serve_ui(
    host: str = _HOST_OPTION,
    port: int = _PORT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
    headless: bool = _HEADLESS_OPTION,
    token_file: Path | None = _TOKEN_FILE_OPTION,
    rem_settings: Path | None = _REM_SETTINGS_OPTION,
) -> None:
    """Serve JSON endpoints used by the React workflow studio (same as Raticode serve)."""
    _serve(host, port, data_dir, headless, token_file, rem_settings)


@ui_app.command("browser", hidden=True)
def browser(
    capability_file: Path = typer.Option(..., "--capability-file"),
) -> None:
    """Serve Rem's tools for Raticode's browser tabs over MCP stdio."""
    from gofer.ui.browser_tools import serve_browser

    serve_browser(capability_file)


@ui_app.command("reports", hidden=True)
def reports(
    root: Path = typer.Option(..., "--root"),
    report_format: str = typer.Option("html", "--report-format"),
) -> None:
    """Serve native report output tools over MCP stdio."""
    from gofer.ui.report_outputs import serve_reports

    serve_reports(root, report_format)


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
