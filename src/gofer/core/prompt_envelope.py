"""Compact, provider-neutral task and resource context."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SkillReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    enabled: bool = True


class McpReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    type: Literal["http", "stdio"] = "http"
    url: str = Field(default="", max_length=2048)
    command: str = Field(default="", max_length=4096)
    args: list[str] = Field(default_factory=list, max_length=100)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_connection(self) -> McpReference:
        if self.type == "stdio":
            if not self.command.strip():
                raise ValueError("Enter an executable for the stdio server")
        else:
            endpoint = urlsplit(self.url)
            if (
                endpoint.scheme not in {"http", "https"}
                or not endpoint.hostname
                or endpoint.username
                or endpoint.password
                or endpoint.fragment
            ):
                raise ValueError("Use an HTTP endpoint without credentials or fragments")
        return self


class AgentResources(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shell: bool = True
    web: bool = False
    skills: list[SkillReference] = Field(default_factory=list, max_length=100)
    mcpServers: list[McpReference] = Field(default_factory=list, max_length=100)


def resource_index(resources: AgentResources) -> str:
    """Index files, never inject their implementation or MCP connection details."""
    entries = []
    for skill in resources.skills:
        if skill.enabled:
            path = Path(skill.path).expanduser()
            if path.name != "SKILL.md":
                path = path / "SKILL.md"
            entries.append({"kind": "skill", "name": path.parent.name, "path": str(path)})
    for server in resources.mcpServers:
        if server.enabled:
            entries.append({"kind": "mcp", "name": server.name})
    return json.dumps(entries, ensure_ascii=False)


def prompt_envelope(*, instructions: str, context: str, request: str) -> str:
    # JSON quoting keeps embedded delimiters and role labels inside their data fields.
    return (
        instructions.strip()
        + "\n\n"
        + json.dumps(
            {"context": context, "request": request},
            ensure_ascii=False,
            indent=2,
        )
    )


def resource_cli_args(
    provider: str,
    resources: AgentResources,
    working_dir: Path | None = None,
) -> list[str]:
    if provider == "codex":
        args = [
            "-c",
            f"features.shell_tool={str(resources.shell).lower()}",
            "-c",
            f"web_search={json.dumps('live' if resources.web else 'disabled')}",
        ]
        if resources.skills:
            skills = ", ".join(
                "{path="
                + json.dumps(str(Path(item.path).expanduser()))
                + ",enabled="
                + str(item.enabled).lower()
                + "}"
                for item in resources.skills
            )
            args += ["-c", f"skills.config=[{skills}]"]
        for name in _codex_mcp_names(working_dir):
            # Codex splits CLI keys on dots without parsing TOML key quoting.
            # Quotes would create a different server with no command or URL.
            args += ["-c", f"mcp_servers.{name}.enabled=false"]
        server_names = codex_mcp_server_names(resources, working_dir)
        for server in resources.mcpServers:
            # A fresh name prevents Codex from merging inherited transport or credentials.
            fields = [f"enabled={str(server.enabled).lower()}"]
            if server.type == "stdio":
                fields += [
                    f"command={json.dumps(server.command)}",
                    f"args={json.dumps(server.args)}",
                ]
            else:
                fields += [f"url={json.dumps(server.url)}"]
            args += ["-c", f"mcp_servers.{server_names[server.name]}={{" + ",".join(fields) + "}"]
        return args
    if provider == "claude_code":
        tools = ["Read", "Edit", "Write", "Glob", "Grep"]
        if resources.shell:
            tools.append("Bash")
        if resources.web:
            tools += ["WebFetch", "WebSearch"]
        servers: dict[str, Any] = {
            item.name: (
                {"type": "stdio", "command": item.command, "args": item.args}
                if item.type == "stdio"
                else {"type": "http", "url": item.url}
            )
            for item in resources.mcpServers
            if item.enabled
        }
        # Explicit MCP configuration prevents ambient servers leaking between threads.
        return [
            "--tools",
            ",".join(tools),
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps({"mcpServers": servers}),
        ]
    raise ValueError(f"Resource configuration is unsupported by provider '{provider}'")


def codex_mcp_server_names(
    resources: AgentResources, working_dir: Path | None = None
) -> dict[str, str]:
    """Map selected servers to invocation names free of inherited configuration."""
    inherited = set(_codex_mcp_names(working_dir))
    occupied = inherited | {server.name for server in resources.mcpServers}
    names: dict[str, str] = {}
    for server in resources.mcpServers:
        if server.name in names:
            continue
        name = server.name
        if name in inherited:
            suffix = 1
            while True:
                name = f"raticode_{server.name[:40]}_{suffix}"
                if name not in occupied:
                    break
                suffix += 1
        occupied.add(name)
        names[server.name] = name
    return names


def _codex_mcp_names(working_dir: Path | None) -> list[str]:
    """Disable inherited MCP entries before applying the thread's explicit selection."""
    codex_dir = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    configs = [codex_dir / "config.toml", Path("/etc/codex/config.toml")]
    if working_dir:
        configs += [
            parent / ".codex" / "config.toml" for parent in [working_dir, *working_dir.parents]
        ]
    names: set[str] = set()
    for config in configs:
        try:
            if config.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Codex configuration exceeds the supported size")
            document = tomllib.loads(config.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        names.update(document.get("mcp_servers", {}).keys())
    return sorted(names)
