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


def emit_json(payload: Any) -> None:
    """Write one JSON document to stdout without Rich markup or color codes."""
    sys.stdout.write(json.dumps(payload, default=str))
    sys.stdout.write("\n")
