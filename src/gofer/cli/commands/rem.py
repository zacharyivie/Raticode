from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console
from rich.table import Table

from gofer.cli.output import wants_json
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
err_console = Console(stderr=True)

_DATA_DIR = typer.Option(None, "--data-dir", hidden=True)
_FORMAT = typer.Option("table", "--format", help="Output format: table (or text) or json")


def _fail(exc: Exception) -> NoReturn:
    from pydantic import ValidationError

    if isinstance(exc, ValidationError):
        errors = exc.errors()
        message = str(errors[0].get("msg", exc)) if errors else str(exc)
        err_console.print(f"[red]Error:[/red] {message.removeprefix('Value error, ')}")
    else:
        err_console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(1)


def _refuse_in_agent_turn(action: str) -> None:
    """Keep changes to things the user already trusts, and restores, with the user.

    An agent turn may add new skills and MCP servers, which stay off until the user
    turns them on in Thread tools, but not replace or remove existing ones.
    """
    from gofer.cli.client import ExitCode, in_agent_session

    if in_agent_session():
        err_console.print(
            f"[red]Error:[/red] Only you can {action}. This command is running inside a "
            "Raticode agent turn; run it from your own terminal or use the app."
        )
        raise typer.Exit(int(ExitCode.AUTH))


def _print_json(value: object) -> None:
    typer.echo(json.dumps(value, indent=2))


@app.command("resources")
def resources(data_dir: Path | None = _DATA_DIR, output: str = _FORMAT) -> None:
    """List every skill and MCP server available to Rem threads."""
    from gofer.core import rem_library

    try:
        snapshot = rem_library.snapshot(data_dir or get_data_dir())
    except ValueError as exc:
        _fail(exc)
    if wants_json(output, aliases=("table",)):
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
    from gofer.core import rem_library

    table = Table("Server", "Type", "Connection", "Description", title="MCP servers")
    for server in servers:
        names = server.get("env") or []
        connection = rem_library.describe_mcp(server)
        if isinstance(names, list) and names:
            connection += "\nenv: " + ", ".join(map(str, names))
        table.add_row(
            str(server["name"]),
            "HTTP" if server["type"] == "http" else "Local program",
            connection,
            str(server.get("description", "")),
        )
    console.print(table)


@skill_app.command("list")
def list_skills(data_dir: Path | None = _DATA_DIR, output: str = _FORMAT) -> None:
    """List registered and discovered skills."""
    from gofer.core import rem_library

    try:
        skills = rem_library.discover_skills(data_dir or get_data_dir())
    except ValueError as exc:
        _fail(exc)
    if wants_json(output, aliases=("table",)):
        _print_json(skills)
    else:
        _skills_table(skills)


@skill_app.command("path")
def skill_path(data_dir: Path | None = _DATA_DIR) -> None:
    """Print Raticode's skills folder. Each child folder with a SKILL.md is a skill."""
    from gofer.core import rem_library

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
    from gofer.core import rem_library

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
    from gofer.core import rem_library

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
    """Unregister a skill folder. Files are not deleted. Refused inside an agent turn."""
    from gofer.core import rem_library

    _refuse_in_agent_turn("remove a Rem skill")
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
    from gofer.core import rem_library

    try:
        servers = rem_library.snapshot(data_dir or get_data_dir())["mcpServers"]
    except ValueError as exc:
        _fail(exc)
    if wants_json(output, aliases=("table",)):
        _print_json(servers)
    else:
        _servers_table(servers)


@mcp_app.command(
    "add",
    epilog=(
        "Examples:\n\n"
        "  Raticode rem mcp add docs --url https://example.com/mcp\n\n"
        "  Raticode rem mcp add github --env GITHUB_PERSONAL_ACCESS_TOKEN -- "
        "github-mcp-server stdio\n\n"
        "A package runner (npx, uvx) should name an exact version, such as "
        "npx -y PACKAGE@1.2.3, or each run fetches the newest release."
    ),
)
def add_server(
    name: str = typer.Argument(..., help="Letters, digits, hyphens, or underscores"),
    command: list[str] | None = typer.Argument(None, help="Local program and arguments, after --"),
    url: str | None = typer.Option(None, "--url", help="HTTP or HTTPS MCP endpoint"),
    description: str = typer.Option("", "--description", "-d", help="What the server provides"),
    env: list[str] | None = typer.Option(
        None,
        "--env",
        help=(
            "Environment variable a local server needs in mcp-tool steps (repeat it). "
            "Only the name is stored"
        ),
    ),
    replace: bool = typer.Option(
        False, "--replace", help="Update an existing server. Refused inside an agent turn"
    ),
    data_dir: Path | None = _DATA_DIR,
) -> None:
    """Add an MCP server that any provider can use in Rem threads.

    An mcp-tool workflow step starts a local server with a small base environment
    (home folder, user, PATH, shell, terminal, locale and temporary-folder variables) and
    the variables named with --env, taken from Raticode's environment. Rem threads
    start servers through their provider and don't use --env.
    """
    from pydantic import ValidationError

    from gofer.core import rem_library

    if replace:
        _refuse_in_agent_turn("replace an MCP server that's already in Rem's library")
    if bool(url) == bool(command):
        _fail(ValueError("Give either --url or a local program after --"))
    if url and env:
        _fail(ValueError("--env is for a local program; an HTTP server isn't started here"))
    server: dict[str, object] = (
        {"name": name, "type": "http", "url": url, "description": description}
        if url
        else {
            "name": name,
            "type": "stdio",
            "command": (command or [""])[0],
            "args": (command or [])[1:],
            "description": description,
            "env": list(env or []),
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


@mcp_app.command(
    "tools",
    epilog=(
        "Examples:\n\n"
        "  Raticode rem mcp tools github --network api.github.com --network registry.npmjs.org\n\n"
        "  Raticode rem mcp tools docs --format json"
    ),
)
def server_tools(
    name: str = typer.Argument(..., help="Server name from Raticode rem mcp list"),
    network: list[str] | None = typer.Option(
        None,
        "--network",
        help="Host a local server may reach, as in an mcp-tool step (repeat it, or use any)",
    ),
    no_sandbox: bool = typer.Option(
        False, "--no-sandbox", help="Start a local server unsandboxed, like sandbox: off"
    ),
    timeout: float = typer.Option(30.0, "--timeout", min=1, help="Seconds to wait"),
    data_dir: Path | None = _DATA_DIR,
    output: str = _FORMAT,
) -> None:
    """Start an MCP server and list its tools with their arguments.

    A local server starts in the same sandbox as an mcp-tool workflow step, in the
    current folder. Use it to pick a tool and write its arguments.
    """
    from gofer.rattish import mcp_tool

    try:
        listing = mcp_tool.server_tools(
            data_dir or get_data_dir(),
            name,
            network=mcp_tool.parse_network(network),
            sandbox=not no_sandbox,
            working_dir=Path.cwd(),
            timeout_seconds=timeout,
        )
    except mcp_tool.McpToolError as exc:
        _fail(ValueError(exc.message))
    if wants_json(output, aliases=("table",)):
        _print_json(listing)
        return
    if not listing["tools"]:
        console.print(f"MCP server {name} has no tools.")
        return
    table = Table(title=f"Tools on {name}")
    table.add_column("Tool", style="bold")
    table.add_column("Arguments")
    table.add_column("Description")
    for tool in listing["tools"]:
        schema = tool.get("inputSchema") or {}
        required = set(schema.get("required") or [])
        names = [
            f"{key}{'' if key in required else '?'}: {(value or {}).get('type', 'any')}"
            for key, value in (schema.get("properties") or {}).items()
        ]
        table.add_row(tool["name"], "\n".join(names) or "none", tool.get("description", ""))
    console.print(table)


@mcp_app.command(
    "clear-cache",
    epilog=(
        "Examples:\n\n  Raticode rem mcp clear-cache github\n\n  Raticode rem mcp clear-cache --all"
    ),
)
def clear_server_cache(
    name: str | None = typer.Argument(None, help="Server name from Raticode rem mcp list"),
    all_servers: bool = typer.Option(False, "--all", help="Clear every server's package cache"),
    data_dir: Path | None = _DATA_DIR,
    output: str = _FORMAT,
) -> None:
    """Clear the package cache of a local MCP server that mcp-tool steps start.

    A server started with npx or uvx keeps its downloaded packages between starts. After
    this, its next start downloads them again.
    """
    from gofer.core import rem_library

    if bool(name) == all_servers:
        _fail(ValueError("Name one MCP server, or use --all"))
    try:
        freed = rem_library.clear_mcp_cache(data_dir or get_data_dir(), name)
    except ValueError as exc:
        _fail(exc)
    if wants_json(output, aliases=("table",)):
        _print_json({"server": name, "all": all_servers, "bytesFreed": freed})
        return
    target = "every MCP server's" if all_servers else f"MCP server {name}'s"
    console.print(f"Cleared {target} package cache ({_size_text(freed)} freed).")


def _size_text(size: int) -> str:
    if size < 1000:
        return f"{size} bytes"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1000
        if value < 1000:
            break
    return f"{value:.1f} {unit}"


@mcp_app.command("remove")
def remove_server(
    name: str = typer.Argument(...),
    data_dir: Path | None = _DATA_DIR,
) -> None:
    """Remove an MCP server from the Rem library. Refused inside an agent turn."""
    from gofer.core import rem_library

    _refuse_in_agent_turn("remove an MCP server from Rem's library")
    try:
        removed = rem_library.remove_mcp_server(data_dir or get_data_dir(), name)
    except ValueError as exc:
        _fail(exc)
    if not removed:
        _fail(ValueError(f"No MCP server named {name}"))
    console.print(f"Removed MCP server {name}.")


def _lease_held(store: Path) -> bool:
    """Whether a running Raticode backend holds Rem, as far as the file can tell."""
    import sqlite3
    import time
    from contextlib import closing

    if not store.is_file():
        return False
    try:
        with closing(sqlite3.connect(f"{store.resolve().as_uri()}?mode=ro", uri=True)) as db:
            row = db.execute("SELECT expires FROM lease WHERE id=1").fetchone()
    except sqlite3.Error:
        # Damaged or unreadable: a backend can't be using it either.
        return False
    return bool(row and row[0] and float(row[0]) > time.time())


def _local_time(value: object) -> str:
    from datetime import datetime

    if not isinstance(value, str) or not value:
        return "-"
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")


@app.command("restore-db")
def restore_db(
    snapshot: str | None = typer.Option(
        None,
        "--snapshot",
        help="Restore this copy, by its file name from --list, instead of the newest one",
    ),
    list_only: bool = typer.Option(False, "--list", help="List the verified copies and exit"),
    force: bool = typer.Option(
        False, "--force", help="Restore even though a running Raticode holds Rem"
    ),
    data_dir: Path | None = _DATA_DIR,
    output: str = _FORMAT,
) -> None:
    """Restore Rem's database from a verified copy. Quit Raticode first.

    Raticode keeps the last few checked copies of Rem's database, taken at startup and
    once a day. This moves the current database and its -wal/-shm files aside (they are
    kept, not deleted) and puts the newest copy that still passes its check in place.
    Rem posts what was restored, and from when, in its chat on the next start. Only
    --list works inside a Raticode agent turn.
    """
    from gofer.ui import rem_db_backups as backups

    root = data_dir or get_data_dir()
    copies = backups.snapshots(root)
    if list_only:
        found = [backups.describe(path) for path in copies]
        if wants_json(output, aliases=("table",)):
            _print_json({"folder": str(root / backups.FOLDER), "snapshots": found})
            return
        if not found:
            console.print("No verified copies of Rem's database yet.")
            return
        table = Table("Copy", "Saved", "Size")
        for item in found:
            table.add_row(item["name"], _local_time(item["taken_at"]), str(item["size"]))
        console.print(table)
        return
    _refuse_in_agent_turn("restore Rem's database")
    live = root / backups.STORE_FILE
    if not force and _lease_held(live):
        _fail(
            ValueError(
                "Raticode is running and holds Rem. Quit Raticode, then run this again "
                "(or pass --force)."
            )
        )
    if snapshot is not None:
        chosen = next((path for path in copies if path.name == snapshot), None)
        if chosen is None:
            _fail(ValueError(f"No verified copy named {snapshot}. See --list."))
    else:
        chosen = next((path for path in copies if backups.quick_check(path)), None)
        if chosen is None:
            _fail(ValueError("There is no verified copy of Rem's database to restore."))
    # Checked on a copy, so the files moved aside stay exactly as they were.
    try:
        damage = backups.damage_on_copy(root, full=True)
    except OSError as exc:
        _fail(OSError(f"Couldn't check the current database, so nothing was changed: {exc}"))
    try:
        notice = backups.restore(
            root,
            reason="damaged" if damage else "replaced",
            error=damage or "",
            choice=chosen,
        )
    except (OSError, ValueError) as exc:
        _fail(exc)
    if wants_json(output, aliases=("table",)):
        _print_json(notice)
        return
    console.print(
        f"Restored Rem's database from {notice['restored']} "
        f"(saved {_local_time(notice['restored_from'])})."
    )
    if notice["kept"]:
        console.print(f"The previous files are kept in {notice['kept']}")
    console.print("Rem posts this in its chat when Raticode next starts.")
