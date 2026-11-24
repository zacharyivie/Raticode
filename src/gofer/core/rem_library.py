"""Raticode-wide catalog of skills and MCP servers that threads can turn on.

Agents and users register entries here (through ``Raticode rem`` or the UI). Threads
store only their selection; library MCP definitions are resolved again at the
start of every turn so edits reach every provider without stale copies.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from gofer.core.prompt_envelope import McpReference

LIBRARY_FILE = "rem-resources.json"
SKILLS_FOLDER = "skills"
MAX_LIBRARY_BYTES = 2 * 1024 * 1024
MAX_SKILLS_PER_ROOT = 200
MAX_SKILL_HEADER_BYTES = 16 * 1024
MAX_DESCRIPTION_CHARS = 400
MAX_ENV_NAMES = 50
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
# Raticode supplies these connections itself; a library entry must not shadow them.
RESERVED_MCP_NAMES = frozenset(
    {
        "fleet",
        "organizations",
        "rem_artifacts",
        "rem_threads",
        "rem_wait",
        "reports",
        "second_brain",
        "social",
        "swarm",
    }
)

Source = Literal["app", "cli"]
_lock = threading.Lock()


class LibrarySkill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    addedBy: Source = "app"
    addedAt: str = ""


class LibraryMcpServer(McpReference):
    description: str = Field(default="", max_length=MAX_DESCRIPTION_CHARS)
    # Names of environment variables an mcp-tool step passes to this local server, on
    # top of a small base set. Only names are stored; values come from Raticode's
    # environment when the server starts. Rem threads don't use this list.
    env: list[str] = Field(default_factory=list)
    addedBy: Source = "app"
    addedAt: str = ""

    @field_validator("env")
    @classmethod
    def valid_env_names(cls, names: list[str]) -> list[str]:
        kept: list[str] = []
        for raw in names:
            name = str(raw).strip()
            if not name:
                continue
            if not _ENV_NAME.fullmatch(name):
                raise ValueError(
                    f"{name!r} isn't an environment variable name: use letters, digits and "
                    "underscores, not starting with a digit"
                )
            if name.upper().startswith("GOFER_"):
                raise ValueError(f"{name} is Raticode's own variable and is never passed on")
            if name not in kept:
                kept.append(name)
        if len(kept) > MAX_ENV_NAMES:
            raise ValueError(f"Name at most {MAX_ENV_NAMES} environment variables")
        return kept

    @model_validator(mode="after")
    def env_needs_a_local_program(self) -> LibraryMcpServer:
        if self.env and self.type != "stdio":
            raise ValueError("Only a local program server takes environment variables")
        return self


class RemLibrary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    skills: list[LibrarySkill] = Field(default_factory=list, max_length=500)
    mcpServers: list[LibraryMcpServer] = Field(default_factory=list, max_length=500)


def library_path(data_dir: Path) -> Path:
    return data_dir / LIBRARY_FILE


def skills_dir(data_dir: Path) -> Path:
    return data_dir / SKILLS_FOLDER


def skill_folder(path: str | Path) -> Path:
    """Normalize a skill reference to its folder, accepting a SKILL.md path."""
    folder = Path(path).expanduser()
    return folder.parent if folder.name == "SKILL.md" else folder


def load_library(data_dir: Path) -> RemLibrary:
    path = library_path(data_dir)
    try:
        if path.stat().st_size > MAX_LIBRARY_BYTES:
            raise ValueError("The Rem resource library exceeds the supported size")
        return RemLibrary.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return RemLibrary()
    except ValidationError as exc:
        raise ValueError(f"The Rem resource library at {path} is invalid: {exc}") from exc


def _save_library(data_dir: Path, library: RemLibrary) -> None:
    destination = library_path(data_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False
        ) as output:
            temporary = output.name
            data = library.model_dump(mode="json")
            # An empty env list stays out of the file, so a Raticode version without
            # the field can still read the library.
            for server in data["mcpServers"]:
                if not server.get("env"):
                    server.pop("env", None)
            output.write(json.dumps(data, indent=2))
            output.write("\n")
        os.replace(temporary, destination)
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def skill_header(folder: Path) -> dict[str, str]:
    """Read name and description from SKILL.md frontmatter without loading the body."""
    try:
        with (folder / "SKILL.md").open("rb") as handle:
            text = handle.read(MAX_SKILL_HEADER_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    key = ""
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if key and line[:1] in {" ", "\t"}:
            # Folded or indented continuation of the previous value.
            fields[key] = (fields[key] + " " + line.strip()).strip()
            continue
        name, separator, value = line.partition(":")
        if not separator or not name.strip().isidentifier():
            key = ""
            continue
        key = name.strip()
        value = value.strip()
        fields[key] = "" if value in {">", "|", ">-", "|-"} else value.strip("'\"")
    return {
        name: " ".join(fields[name].split())[:MAX_DESCRIPTION_CHARS]
        for name in ("name", "description")
        if fields.get(name)
    }


def discovery_roots(data_dir: Path, codex_home: Path | None = None) -> list[tuple[str, Path]]:
    """Folders whose immediate children are skill folders, in display order.

    Codex's skills come from ``codex_home``, else the folder of the Codex account
    Settings > Rem names (its own CODEX_HOME, codex_accounts.py), else Raticode's own
    CODEX_HOME.
    """
    home = Path.home()
    if codex_home is None:
        from gofer.core.codex_accounts import rem_codex_home

        codex_home = rem_codex_home(data_dir)
    if codex_home is None:
        codex_home = Path(os.environ.get("CODEX_HOME", str(home / ".codex")))
    return [
        ("raticode", skills_dir(data_dir)),
        ("agents", home / ".agents" / "skills"),
        ("claude", home / ".claude" / "skills"),
        ("codex", codex_home / "skills"),
    ]


def _skill_entry(folder: Path, origin: str, **extra: Any) -> dict[str, Any]:
    header = skill_header(folder)
    return {
        "path": str(folder),
        "name": header.get("name") or folder.name,
        "description": header.get("description", ""),
        "origin": origin,
        "available": (folder / "SKILL.md").is_file(),
        **extra,
    }


def discover_skills(data_dir: Path, codex_home: Path | None = None) -> list[dict[str, Any]]:
    """List registered skills plus skill folders found in well-known locations."""
    library = load_library(data_dir)
    entries: dict[str, dict[str, Any]] = {}
    for origin, root in discovery_roots(data_dir, codex_home):
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        found = 0
        for child in children:
            if found >= MAX_SKILLS_PER_ROOT:
                break
            if child.name.startswith(".") or not (child / "SKILL.md").is_file():
                continue
            found += 1
            key = str(child.resolve())
            entries.setdefault(key, _skill_entry(child, origin, registered=False))
    for skill in library.skills:
        folder = skill_folder(skill.path)
        key = str(folder.resolve()) if folder.exists() else str(folder)
        entry = entries.get(key) or _skill_entry(folder, "added")
        entry.update(registered=True, addedBy=skill.addedBy, addedAt=skill.addedAt)
        entries[key] = entry
    return list(entries.values())


def snapshot(data_dir: Path) -> dict[str, Any]:
    library = load_library(data_dir)
    return {
        "skillsDir": str(skills_dir(data_dir)),
        "skills": discover_skills(data_dir),
        "mcpServers": [server.model_dump() for server in library.mcpServers],
    }


def add_skill(data_dir: Path, path: str, *, added_by: Source = "app") -> dict[str, Any]:
    folder = skill_folder(path)
    if not folder.is_absolute():
        raise ValueError("Use an absolute skill folder path")
    if not (folder / "SKILL.md").is_file():
        raise ValueError(f"No SKILL.md found in {folder}")
    with _lock:
        library = load_library(data_dir)
        existing = {str(skill_folder(skill.path)) for skill in library.skills}
        if str(folder) not in existing:
            library.skills.append(LibrarySkill(path=str(folder), addedBy=added_by, addedAt=_now()))
            _save_library(data_dir, library)
    return _skill_entry(folder, "added", registered=True, addedBy=added_by)


def remove_skill(data_dir: Path, path: str) -> bool:
    folder = str(skill_folder(path))
    with _lock:
        library = load_library(data_dir)
        kept = [skill for skill in library.skills if str(skill_folder(skill.path)) != folder]
        if len(kept) == len(library.skills):
            return False
        library.skills = kept
        _save_library(data_dir, library)
    return True


def create_skill(
    data_dir: Path, name: str, description: str, body: str = "", *, added_by: Source = "app"
) -> dict[str, Any]:
    """Scaffold a skill in Raticode's own skills folder, where it is discovered."""
    if not name or len(name) > 64 or not all(c.isalnum() or c in "-_" for c in name):
        raise ValueError("Skill names use letters, digits, hyphens, or underscores")
    description = " ".join(description.split())
    if not description or len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError(f"Describe when to use the skill in 1-{MAX_DESCRIPTION_CHARS} characters")
    folder = skills_dir(data_dir) / name
    if folder.exists():
        raise ValueError(f"A skill named {name} already exists at {folder}")
    folder.mkdir(parents=True)
    content = body.strip() or f"# {name}\n\nDescribe the steps Rem should follow."
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{content}\n", encoding="utf-8"
    )
    return _skill_entry(folder, "raticode", registered=False, addedBy=added_by)


def save_mcp_server(
    data_dir: Path,
    server: dict[str, Any],
    *,
    added_by: Source = "app",
    replace: str | None = None,
) -> dict[str, Any]:
    """Add a server, or update the entry named ``replace`` in place."""
    entry = LibraryMcpServer.model_validate(
        {**server, "library": False, "enabled": True, "addedBy": added_by, "addedAt": _now()}
    )
    if entry.name in RESERVED_MCP_NAMES:
        raise ValueError(f"{entry.name} is reserved for a built-in Raticode connection")
    with _lock:
        library = load_library(data_dir)
        servers = library.mcpServers
        index = next((i for i, item in enumerate(servers) if item.name == replace), None)
        if replace is not None and index is None:
            raise ValueError(f"No MCP server named {replace}")
        if any(item.name == entry.name for i, item in enumerate(servers) if i != index):
            raise ValueError(f"An MCP server named {entry.name} already exists")
        previous = None
        if index is None:
            servers.append(entry)
        else:
            previous = servers[index]
            entry.addedBy = servers[index].addedBy
            entry.addedAt = servers[index].addedAt
            servers[index] = entry
        _save_library(data_dir, library)
    if previous is not None:
        _drop_changed_cache(data_dir, previous, entry)
    return entry.model_dump()


def remove_mcp_server(data_dir: Path, name: str) -> bool:
    with _lock:
        library = load_library(data_dir)
        kept = [server for server in library.mcpServers if server.name != name]
        if len(kept) == len(library.mcpServers):
            return False
        removed = [server for server in library.mcpServers if server.name == name]
        library.mcpServers = kept
        _save_library(data_dir, library)
    for server in removed:
        _drop_changed_cache(data_dir, server, None)
    return True


def _definition(server: LibraryMcpServer) -> dict[str, Any]:
    return server.model_dump(include={"name", "type", "url", "command", "args", "env"})


def _drop_changed_cache(
    data_dir: Path, old: LibraryMcpServer, new: LibraryMcpServer | None
) -> None:
    """Delete ``old``'s package cache unless ``new`` keeps the same definition."""
    from gofer.rattish import mcp_cache

    old_digest = mcp_cache.server_digest(_definition(old))
    new_digest = mcp_cache.server_digest(_definition(new)) if new is not None else None
    if old_digest and old_digest != new_digest:
        mcp_cache.remove(data_dir, old_digest)


def clear_mcp_cache(data_dir: Path, name: str | None = None) -> int:
    """Delete the package cache of server ``name``, or of every server; the bytes freed."""
    from gofer.rattish import mcp_cache

    if name is None:
        return mcp_cache.clear_all(data_dir)
    server = next((item for item in load_library(data_dir).mcpServers if item.name == name), None)
    if server is None:
        raise ValueError(f"No MCP server named {name}")
    if server.type != "stdio":
        raise ValueError(f"MCP server {name} is an HTTP server, which has no package cache")
    return mcp_cache.remove_for_server(data_dir, _definition(server))


def with_library_resources(
    workflow: dict[str, Any] | None, data_dir: Path
) -> dict[str, Any] | None:
    """Replace library-linked MCP selections with the library's current definitions."""
    resources = (workflow or {}).get("remResources")
    if not isinstance(resources, dict):
        return workflow
    servers = resources.get("mcpServers")
    if not isinstance(servers, list) or not any(
        isinstance(item, dict) and item.get("library") is True for item in servers
    ):
        return workflow
    definitions = {server.name: server for server in load_library(data_dir).mcpServers}
    resolved: list[Any] = []
    for item in servers:
        if not isinstance(item, dict) or item.get("library") is not True:
            resolved.append(item)
            continue
        definition = definitions.get(str(item.get("name", "")))
        if definition is None:
            # Removed from the library; never fall back to a stale copy.
            continue
        resolved.append(
            {
                **definition.model_dump(
                    include={"name", "type", "url", "command", "args"},
                ),
                "enabled": item.get("enabled", True) is not False,
                "library": True,
            }
        )
    return {**(workflow or {}), "remResources": {**resources, "mcpServers": resolved}}


def describe_mcp(server: dict[str, Any]) -> str:
    if server.get("type") == "stdio":
        return " ".join([str(server.get("command", "")), *map(str, server.get("args") or [])])
    return str(server.get("url", ""))
