from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from gofer.core import rem_library
from gofer.utils.paths import get_data_dir

app = typer.Typer(
    help=(
        "Manage the skills and MCP servers Rem threads can use. Entries appear in "
        "Thread tools in Raticode, where users turn them on per thread."
    ),
    no_args_is_help=True,
)
skill_app = typer.Typer(help="Manage Rem skills", no_args_is_help=True)
mcp_app = typer.Typer(help="Manage Rem MCP servers", no_args_is_help=True)
app.add_typer(skill_app, name="skill")
app.add_typer(mcp_app, name="mcp")
console = Console()

_DATA_DIR = typer.Option(None, "--data-dir", hidden=True)
_FORMAT = typer.Option("table", "--format", help="Output format: table or json")


def _fail(exc: Exception) -> NoReturn:
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        message = str(errors[0].get("msg", exc)) if errors else str(exc)
        console.print(f"[red]Error:[/red] {message.removeprefix('Value error, ')}")
    else:
        console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(1)


def _print_json(value: object) -> None:
    typer.echo(json.dumps(value, indent=2))


@app.command("resources")
def resources(data_dir: Path | None = _DATA_DIR, output: str = _FORMAT) -> None:
    """List every skill and MCP server available to Rem threads."""
    try:
        snapshot = rem_library.snapshot(data_dir or get_data_dir())
    except ValueError as exc:
        _fail(exc)
    if output == "json":
        _print_json(snapshot)
        return
    _skills_table(snapshot["skills"])
    _servers_table(snapshot["mcpServers"])


def _skills_table(skills: list[dict[str, object]]) -> None:
    table = Table("Skill", "Source", "Path", "Description", title="Skills")
    for skill in skills:
        table.add_row(
            str(skill["name"]),
            str(skill["origin"]),
            str(skill["path"]),
            str(skill["description"]) if skill["available"] else "[red]SKILL.md missing[/red]",
        )
    console.print(table)


def _servers_table(servers: list[dict[str, object]]) -> None:
    table = Table("Server", "Type", "Connection", "Description", title="MCP servers")
    for server in servers:
        table.add_row(
            str(server["name"]),
            "HTTP" if server["type"] == "http" else "Local program",
            rem_library.describe_mcp(server),
            str(server.get("description", "")),
        )
    console.print(table)


@skill_app.command("list")
def list_skills(data_dir: Path | None = _DATA_DIR, output: str = _FORMAT) -> None:
    """List registered and discovered skills."""
    try:
        skills = rem_library.discover_skills(data_dir or get_data_dir())
    except ValueError as exc:
        _fail(exc)
    if output == "json":
        _print_json(skills)
    else:
        _skills_table(skills)


@skill_app.command("path")
def skill_path(data_dir: Path | None = _DATA_DIR) -> None:
    """Print Raticode's skills folder. Each child folder with a SKILL.md is a skill."""
    typer.echo(rem_library.skills_dir(data_dir or get_data_dir()))


@skill_app.command("new")
def new_skill(
    name: str = typer.Argument(..., help="Folder and skill name, such as release-notes"),
    description: str = typer.Option(
        ..., "--description", "-d", help="When Rem should use this skill"
    ),
    body_file: Path | None = typer.Option(
        None, "--body-file", help="Markdown file with the skill instructions"
    ),
    data_dir: Path | None = _DATA_DIR,
) -> None:
    """Create a skill in Raticode's skills folder."""
    try:
        body = body_file.read_text(encoding="utf-8") if body_file else ""
        skill = rem_library.create_skill(
            data_dir or get_data_dir(), name, description, body, added_by="cli"
        )
    except (OSError, ValueError) as exc:
        _fail(exc)
    console.print(f"Created skill [bold]{skill['name']}[/bold] at {skill['path']}/SKILL.md")
    console.print("Turn it on in Thread tools to use it in a Rem thread.")


@skill_app.command("add")
def add_skill(
    path: Path = typer.Argument(..., help="Folder containing SKILL.md"),
    data_dir: Path | None = _DATA_DIR,
) -> None:
    """Register an existing skill folder from anywhere on this computer."""
    try:
        skill = rem_library.add_skill(
            data_dir or get_data_dir(), str(path.expanduser().resolve()), added_by="cli"
        )
    except ValueError as exc:
        _fail(exc)
    console.print(f"Added skill [bold]{skill['name']}[/bold] from {skill['path']}")
    console.print("Turn it on in Thread tools to use it in a Rem thread.")


@skill_app.command("remove")
def remove_skill(
    path: Path = typer.Argument(..., help="Registered skill folder"),
    data_dir: Path | None = _DATA_DIR,
) -> None:
    """Unregister a skill folder. Files are not deleted."""
    try:
        removed = rem_library.remove_skill(
            data_dir or get_data_dir(), str(path.expanduser().resolve())
        )
    except ValueError as exc:
        _fail(exc)
    if not removed:
        _fail(ValueError(f"{path} is not a registered skill"))
    console.print(f"Removed {path} from the Rem library.")


@mcp_app.command("list")
def list_servers(data_dir: Path | None = _DATA_DIR, output: str = _FORMAT) -> None:
    """List MCP servers in the Rem library."""
    try:
        servers = rem_library.snapshot(data_dir or get_data_dir())["mcpServers"]
    except ValueError as exc:
        _fail(exc)
    if output == "json":
        _print_json(servers)
    else:
        _servers_table(servers)


@mcp_app.command(
    "add",
    epilog=(
        "Examples:\n\n"
        "  gof rem mcp add docs --url https://example.com/mcp\n\n"
        "  gof rem mcp add github -- npx -y @modelcontextprotocol/server-github"
    ),
)
def add_server(
    name: str = typer.Argument(..., help="Letters, digits, hyphens, or underscores"),
    command: list[str] | None = typer.Argument(None, help="Local program and arguments, after --"),
    url: str | None = typer.Option(None, "--url", help="HTTP or HTTPS MCP endpoint"),
    description: str = typer.Option("", "--description", "-d", help="What the server provides"),
    replace: bool = typer.Option(False, "--replace", help="Update an existing server"),
    data_dir: Path | None = _DATA_DIR,
) -> None:
    """Add an MCP server that any provider can use in Rem threads."""
    if bool(url) == bool(command):
        _fail(ValueError("Give either --url or a local program after --"))
    server: dict[str, object] = (
        {"name": name, "type": "http", "url": url, "description": description}
        if url
        else {
            "name": name,
            "type": "stdio",
            "command": (command or [""])[0],
            "args": (command or [])[1:],
            "description": description,
        }
    )
    try:
        rem_library.save_mcp_server(
            data_dir or get_data_dir(),
            server,
            added_by="cli",
            replace=name if replace else None,
        )
    except (ValidationError, ValueError) as exc:
        _fail(exc)
    console.print(f"{'Updated' if replace else 'Added'} MCP server [bold]{name}[/bold].")
    console.print("Turn it on in Thread tools to use it in a Rem thread.")


@mcp_app.command("remove")
def remove_server(
    name: str = typer.Argument(...),
    data_dir: Path | None = _DATA_DIR,
) -> None:
    """Remove an MCP server from the Rem library."""
    try:
        removed = rem_library.remove_mcp_server(data_dir or get_data_dir(), name)
    except ValueError as exc:
        _fail(exc)
    if not removed:
        _fail(ValueError(f"No MCP server named {name}"))
    console.print(f"Removed MCP server {name}.")
