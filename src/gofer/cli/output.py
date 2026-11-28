"""Shared output options for CLI commands.

Every command that prints data accepts ``--format text|json``, the spelling the
``rattish`` group has always used. Commands that shipped with a ``--json`` flag keep
it as an alias, so existing scripts continue to work.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer

OUTPUT_FORMATS = ("text", "json")
FORMAT_HELP = "Output format: text or json."


def format_option(default: str = "text") -> Any:
    return typer.Option(default, "--format", help=FORMAT_HELP)


def json_alias_option() -> Any:
    return typer.Option(False, "--json", help="Same as --format json.")


def wants_json(
    output_format: str, json_flag: bool = False, *, aliases: tuple[str, ...] = ()
) -> bool:
    """Whether a command should print JSON; exits 2 for an unknown format."""
    value = output_format.strip().lower()
    if value not in (*OUTPUT_FORMATS, *aliases):
        typer.echo(
            f"Unsupported format {output_format!r}. Use --format text or --format json.",
            err=True,
        )
        raise typer.Exit(2)
    return json_flag or value == "json"


def require_format(output_format: str) -> None:
    """Exit 2 unless ``--format`` is exactly ``text`` or ``json``."""
    if output_format not in OUTPUT_FORMATS:
        typer.echo("Unsupported format. Use 'text' or 'json'.", err=True)
        raise typer.Exit(2)


def emit_json(payload: Any) -> None:
    """Write one JSON document to stdout without Rich markup or color codes."""
    sys.stdout.write(json.dumps(payload, default=str))
    sys.stdout.write("\n")


def fail(message: str, output_format: str, code: int = 1) -> typer.Exit:
    """Print an error (a JSON ``ok: false`` document, or text on stderr) for ``raise``."""
    if output_format == "json":
        typer.echo(json.dumps({"ok": False, "error": message}, sort_keys=True))
    else:
        typer.echo(message, err=True)
    return typer.Exit(code)


def confirm_unseen(files: list[dict[str, Any]], *, yes: bool, unseen: bool, fmt: str) -> bool:
    """Whether you approve files whose change wasn't shown: --unseen, or a second yes.

    With --yes or JSON output and no --unseen nothing asks, so the approval is refused
    and names them.
    """
    if not files or unseen:
        return unseen
    if yes or fmt != "text":
        return False
    from gofer.rattish.trigger_approval import unseen_heading, unseen_lines

    heading, advice = unseen_heading(files)
    typer.echo(heading)
    for line in unseen_lines(files):
        typer.echo(line)
    typer.echo(advice)
    return typer.confirm("Approve them without seeing all of their changes?", default=False)
