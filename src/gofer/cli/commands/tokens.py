"""Limited API tokens for scripts and agents that talk to a running backend."""

from __future__ import annotations

from pathlib import Path

import typer

from gofer.cli.output import emit_json, format_option, wants_json
from gofer.ui.headless import AgentTokenStore, HeadlessError, create_private_file, in_agent_session
from gofer.utils.paths import get_data_dir

app = typer.Typer(
    help=(
        "Manage agent tokens: limited API tokens for scripts and agents. They can't approve "
        "consent requests or proposals, change Rem's settings, pair devices or read the "
        "owner token."
    ),
    no_args_is_help=True,
)
_DATA_DIR = typer.Option(None, "--data-dir", help="Raticode data directory")


def _store(data_dir: Path | None) -> AgentTokenStore:
    return AgentTokenStore(data_dir or get_data_dir())


def _refuse_agent_session() -> None:
    if in_agent_session():
        typer.echo(
            "Agent tokens can't be created or revoked from inside a Raticode agent session.",
            err=True,
        )
        raise typer.Exit(5)


@app.command("create")
def create(
    name: str = typer.Argument(..., help="Token name, such as ci or nightly-script"),
    output: Path = typer.Option(
        ...,
        "--output",
        "-o",
        help="New file that receives the token, readable only by you (mode 600).",
    ),
    expires_days: int | None = typer.Option(
        None, "--expires-days", min=1, help="Stop accepting the token after this many days."
    ),
    data_dir: Path | None = _DATA_DIR,
) -> None:
    """Create an agent token and write it to a new owner-only file. It is never printed."""
    _refuse_agent_session()
    if output.exists():
        typer.echo(f"Error: {output} already exists; choose a new file.", err=True)
        raise typer.Exit(2)
    store = _store(data_dir)
    try:
        token = store.create(name, expires_days=expires_days)
    except HeadlessError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from exc
    try:
        create_private_file(output, token + "\n")
    except (HeadlessError, OSError) as exc:
        store.revoke(name)
        typer.echo(f"Error: couldn't write {output}: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Created agent token {name!r} in {output}", err=True)


@app.command("list")
def list_tokens(
    output_format: str = format_option(),
    data_dir: Path | None = _DATA_DIR,
) -> None:
    """List agent tokens by name. Token values are never stored or shown."""
    as_json = wants_json(output_format)
    try:
        tokens = [token.to_payload() for token in _store(data_dir).list()]
    except (HeadlessError, ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if as_json:
        emit_json(tokens)
        return
    if not tokens:
        typer.echo("No agent tokens.")
        return
    for token in tokens:
        expiry = token["expiresAt"] or "never"
        state = "expired" if token["expired"] else "active"
        typer.echo(f"{token['name']}\t{state}\tcreated {token['createdAt']}\texpires {expiry}")


@app.command("revoke")
def revoke(
    name: str = typer.Argument(..., help="Token name"),
    data_dir: Path | None = _DATA_DIR,
) -> None:
    """Revoke an agent token. A running backend stops accepting it immediately."""
    _refuse_agent_session()
    try:
        removed = _store(data_dir).revoke(name)
    except (HeadlessError, ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    if not removed:
        typer.echo(f"Error: no agent token named {name!r}.", err=True)
        raise typer.Exit(3)
    typer.echo(f"Revoked agent token {name!r}", err=True)
