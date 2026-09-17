"""Private per-session MCP metadata, without relocating provider authentication.

Antigravity reuses the resource validation and MCP definitions. Grok public
source 37949780 accepts session _meta.pluginDirs with the native JSON MCP schema.
Configuration alone does not prove tools are ready or permissions are enforced.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gofer.core.prompt_envelope import AgentResources
from gofer.subscriptions.acp_transport import RequestHandler


@dataclass(frozen=True)
class AcpSessionConfig:
    params: dict[str, Any]
    grants: dict[str, set[str]]
    http_servers: dict[str, tuple[str, set[str]]]


@contextmanager
def acp_session_config(
    provider: str,
    cwd: Path,
    resources: AgentResources,
    *,
    trusted_swarm_url: str | None = None,
    second_brain_cli_path: Path | None = None,
) -> Iterator[AcpSessionConfig]:
    """Caller must close/drain the ACP process before leaving this context."""
    if provider not in {"antigravity", "grok"}:
        raise ValueError("Unsupported ACP provider")
    servers: list[dict[str, Any]] = []
    grants: dict[str, set[str]] = {}
    http_servers: dict[str, tuple[str, set[str]]] = {}
    prefix = "raticode-" + uuid.uuid4().hex
    for server in resources.mcpServers:
        if not server.enabled:
            continue
        # Grok uses double underscores to separate server and tool identities.
        # Keep aliases independent of names that might contain that separator.
        alias = f"{prefix}-{len(servers)}"
        tools = {"*"}
        if server.name == "swarm":
            if server.type != "http" or not trusted_swarm_url or server.url != trusted_swarm_url:
                raise ValueError("Swarm MCP endpoint does not match the trusted running turn")
            tools = {"swarm_action"}
        elif server.name == "second_brain":
            if (
                not second_brain_cli_path
                or server.type != "stdio"
                or server.command != str(second_brain_cli_path)
                or server.args[:2] != ["ui", "second-brain"]
            ):
                raise ValueError("Second Brain MCP executable does not match trusted installation")
            tools = {"rules", "search", "read_note", "save_note"}
        entry: dict[str, Any] = {"name": alias}
        if server.type == "http":
            entry.update({"type": "http", "url": server.url, "headers": []})
            http_servers[alias] = (server.url, tools - {"*"})
        else:
            entry.update({"command": server.command, "args": server.args, "env": []})
        servers.append(entry)
        grants[alias] = tools
    params: dict[str, Any] = {"cwd": str(cwd.resolve()), "mcpServers": servers}
    with tempfile.TemporaryDirectory(prefix="raticode-acp-") as temporary:
        if provider == "grok":
            mcp: dict[str, Any] = {}
            for entry in servers:
                name = entry["name"]
                if entry.get("type") == "http":
                    mcp[name] = {"type": "http", "url": entry["url"], "headers": {}}
                else:
                    mcp[name] = {"command": entry["command"], "args": entry["args"]}
            (Path(temporary) / ".mcp.json").write_text(
                json.dumps({"mcpServers": mcp}), encoding="utf-8"
            )
            params["mcpServers"] = []
            params["_meta"] = {"pluginDirs": [temporary]}
        yield AcpSessionConfig(params, grants, http_servers)


async def deny_acp_permission(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Ambiguous ACP tool descriptions cannot authorize execution."""
    if method != "session/request_permission":
        raise ValueError("Unsupported ACP client method")
    return {"outcome": {"outcome": "cancelled"}}


def grok_mcp_permission_handler(session_id: str, grants: dict[str, set[str]]) -> RequestHandler:
    """Authorize offered one-time MCP requests by Grok's canonical v1 stamp.

    This callback cannot enforce native restrictions on operations which bypass
    callbacks. Adapters must separately prove their native permission policy.
    """
    owned_grants = {server: frozenset(tools) for server, tools in grants.items()}

    async def handle(method: str, params: dict[str, Any]) -> dict[str, Any]:
        denied = await deny_acp_permission(method, params)
        if params.get("sessionId") != session_id:
            return denied
        call = params.get("toolCall")
        meta = call.get("_meta") if isinstance(call, dict) else None
        identity = meta.get("x.ai/tool") if isinstance(meta, dict) else None
        if (
            not isinstance(identity, dict)
            or type(identity.get("version")) is not int
            or identity["version"] != 1
            or identity.get("namespace") != "mcp"
            or not isinstance(identity.get("name"), str)
        ):
            return denied
        server, separator, tool = identity["name"].partition("__")
        allowed = owned_grants.get(server, frozenset())
        if not separator or not tool or (tool not in allowed and "*" not in allowed):
            return denied
        options = params.get("options")
        if not isinstance(options, list):
            return denied
        choices = [
            option["optionId"]
            for option in options
            if isinstance(option, dict)
            and option.get("kind") == "allow_once"
            and isinstance(option.get("optionId"), str)
            and option["optionId"]
        ]
        if len(choices) != 1:
            return denied
        if (
            sum(
                isinstance(option, dict) and option.get("optionId") == choices[0]
                for option in options
            )
            != 1
        ):
            return denied
        return {"outcome": {"outcome": "selected", "optionId": choices[0]}}

    return handle
