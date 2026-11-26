"""`Raticode rattish secrets`: values ``secret["NAME"]`` reads, kept in the system keychain.

Values are read from a hidden prompt or standard input, never from a command-line argument
(other local accounts can read a process's arguments), and no command prints one back.
Saving and removing are the user's decisions, so both refuse inside an agent turn. Listing
shows names only, so agents can see what a workflow is missing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from gofer.cli.output import fail
from gofer.utils.paths import get_data_dir

app = typer.Typer(
    help=(
        'Save, list and remove the values Rattish secret["NAME"] references read. Values '
        "go to the system keychain and are never shown again. A reference checks the "
        "keychain first, then a headless backend's secrets folder (a read-only "
        "workflow-secret-NAME file), then the environment variable with the same name."
    ),
    no_args_is_help=True,
)

_DATA_DIR_OPTION = typer.Option(None, "--data-dir", help="Override the data folder.")
_FORMAT_OPTION = typer.Option("text", "--format", help="Output format: text or json.")


def _require_format(output_format: str) -> None:
    if output_format not in {"text", "json"}:
        typer.echo("--format must be text or json.", err=True)
        raise typer.Exit(2)


def _refuse_agent(action: str, output_format: str) -> None:
    from gofer.cli.commands._backend import agent_session

    if agent_session():
        raise fail(
            f"Only you can {action}. This command is running inside a Raticode agent turn; "
            "run it from your own terminal or use Workflow settings > Secrets.",
            output_format,
            code=5,
        )


def _read_value(name: str, from_stdin: bool) -> str:
    if from_stdin or not sys.stdin.isatty():
        value = sys.stdin.read()
        # One trailing newline is how echo and most files end; the rest is the value.
        if value.endswith("\r\n"):
            return value[:-2]
        return value.removesuffix("\n")
    return str(typer.prompt(f"Value for {name}", hide_input=True))


@app.command("set")
def set_secret(
    name: str = typer.Argument(..., help='The name workflows use in secret["NAME"].'),
    from_stdin: bool = typer.Option(
        False,
        "--stdin",
        help="Read the value from standard input (all of it, so it can span lines) "
        "instead of a hidden prompt.",
    ),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Save a workflow secret in the system keychain, replacing any saved value."""
    from gofer.rattish.workflow_secrets import (
        WorkflowSecretError,
        check_name,
        save_workflow_secret,
    )

    _require_format(output_format)
    # Refuse before prompting, so an agent turn never reads a value from its input.
    _refuse_agent("save a workflow secret", output_format)
    try:
        check_name(name)
    except WorkflowSecretError as exc:
        raise fail(str(exc), output_format, code=2) from None
    value = _read_value(name, from_stdin)
    try:
        entry = save_workflow_secret(name, value, data_dir or get_data_dir())
    except WorkflowSecretError as exc:
        raise fail(str(exc), output_format) from None
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "secret": entry}, sort_keys=True))
        return
    typer.echo(f'Saved {name} in the system keychain. Workflows read it as secret["{name}"].')
    if entry["inEnvironment"]:
        typer.echo(f"The {name} environment variable is also set; the saved value wins.")


@app.command("list")
def list_secrets(
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """List saved workflow secret names. Values are never shown."""
    from gofer.rattish.workflow_secrets import list_workflow_secrets

    _require_format(output_format)
    secrets = list_workflow_secrets(data_dir or get_data_dir())
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, "secrets": secrets}, sort_keys=True))
        return
    if not secrets:
        typer.echo("No workflow secrets saved. Add one: Raticode rattish secrets set NAME")
        return
    for item in secrets:
        line = item["name"]
        if item["updatedAt"]:
            line += f"  saved {item['updatedAt']}"
        if item["inEnvironment"]:
            line += "  (also in the environment; the saved value wins)"
        typer.echo(line)


@app.command("remove")
def remove_secret(
    name: str = typer.Argument(..., help="The saved secret's name."),
    output_format: str = _FORMAT_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
) -> None:
    """Delete a saved workflow secret from the system keychain."""
    from gofer.rattish.workflow_secrets import WorkflowSecretError, remove_workflow_secret

    _require_format(output_format)
    _refuse_agent("remove a workflow secret", output_format)
    try:
        result: dict[str, Any] = remove_workflow_secret(name, data_dir or get_data_dir())
    except WorkflowSecretError as exc:
        raise fail(str(exc), output_format) from None
    if output_format == "json":
        typer.echo(json.dumps({"ok": True, **result}, sort_keys=True))
        return
    typer.echo(f"Removed {name}.")
