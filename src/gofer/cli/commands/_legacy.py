"""Shared refusal for legacy TOML commands that are given a Rattish workflow.

Every legacy command answers a Rattish workflow the same way: change nothing, print
the Rattish command to use instead on stderr, and exit 2.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import typer

from gofer.rattish.workspaces import list_registered_workflows


def resolve_rattish_source(name: str, data_dir: Path | None) -> Path | None:
    """Return the Rattish entrypoint for a .rattish path, workflow folder, or registered ID."""
    path = Path(name).expanduser()
    if path.suffix == ".rattish" and path.is_file():
        return path
    if path.is_dir() and (path / "workflow.rattish").is_file():
        return path / "workflow.rattish"
    if path.suffix in {".toml", ".rattish"}:
        return None
    try:
        registered = list_registered_workflows(registry_dir=data_dir)
    except (OSError, ValueError):
        return None
    return next(
        (item.entrypoint for item in registered if item.workflow_id == name),
        None,
    )


def refuse_rattish(name: str, data_dir: Path | None, command: str, hint: str) -> None:
    """Exit 2 with ``hint`` when ``name`` is a Rattish workflow; otherwise return.

    ``command`` is the legacy command below ``Raticode``, such as ``runner queue``.
    ``hint`` may use ``{source}`` (the quoted source path) and ``{name}``.
    """
    source = resolve_rattish_source(name, data_dir)
    if source is None:
        return
    instead = hint.format(source=shlex.quote(str(source)), name=shlex.quote(name))
    typer.echo(
        f"'{name}' is a Rattish workflow ({source}). Raticode {command} only "
        f"works with legacy TOML workflows.\nUse instead: {instead}",
        err=True,
    )
    raise typer.Exit(2)
