"""Raticode's tool-server tokens stay off Codex's command line.

Any account on the computer can read a process's arguments. Codex gets each Raticode
tool server as a tokenless URL plus a Bearer token variable set only in its own
environment. Claude Code keeps the path-token URL, which goes in a private file.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gofer.core.prompt_envelope import (
    CODEX_MCP_TOKEN_ENV_PREFIX,
    AgentResources,
    McpReference,
    codex_mcp_server_names,
    codex_mcp_token_env,
    resource_cli_args,
)
from gofer.ui import chat
from gofer.ui.swarm_tools import SwarmToolServer

TRUSTED_NAMES = ("swarm", "organizations", "rem_threads", "rem_artifacts", "rem_wait")


@pytest.fixture
def servers() -> Iterator[tuple[SwarmToolServer, SwarmToolServer]]:
    # Swarms and Organizations each run their own server, on different ports.
    first, second = SwarmToolServer(), SwarmToolServer()
    yield first, second
    first.close()
    second.close()


def _turn_urls(servers: tuple[SwarmToolServer, SwarmToolServer]) -> dict[str, str]:
    urls = {}
    for index, name in enumerate(TRUSTED_NAMES):
        urls[name] = servers[index % 2].register(lambda _n, _a: "ok", [])
    return urls


def _resources(urls: dict[str, str]) -> AgentResources:
    return AgentResources(
        mcpServers=[
            *(McpReference(name=name, url=url) for name, url in urls.items()),
            McpReference(name="docs", url="https://example.com/mcp"),
        ]
    )


def _token(url: str) -> str:
    return url.rsplit("/", 1)[1]


def _server_entries(command: list[str]) -> dict[str, dict[str, Any]]:
    entries = {}
    for flag, value in zip(command, command[1:], strict=False):
        if flag == "-c" and value.startswith("mcp_servers.") and "={" in value:
            key, table = value.split("=", 1)
            name = key.removeprefix("mcp_servers.")
            if "." not in name:
                entries[name] = tomllib.loads(f"x={table}")["x"]
    return entries


def test_codex_resource_args_carry_no_tool_tokens(servers, tmp_path: Path) -> None:
    urls = _turn_urls(servers)
    resources = _resources(urls)

    command = resource_cli_args("codex", resources, tmp_path)
    env = codex_mcp_token_env(resources)

    joined = "\0".join(command)
    for url in urls.values():
        assert _token(url) not in joined
    entries = _server_entries(command)
    names = codex_mcp_server_names(resources, tmp_path)
    variables = set()
    for name, url in urls.items():
        entry = entries[names[name]]
        assert entry["url"] == url.rsplit("/", 1)[0] + "/"
        variable = entry["bearer_token_env_var"]
        assert variable.startswith(CODEX_MCP_TOKEN_ENV_PREFIX)
        assert env[variable] == _token(url)
        variables.add(variable)
    assert set(env) == variables
    assert len(variables) == len(urls)
    # A user's own server is left exactly as configured.
    assert entries[names["docs"]] == {"enabled": True, "url": "https://example.com/mcp"}


def test_disabled_and_closed_servers(servers, tmp_path: Path) -> None:
    live = servers[0].register(lambda _n, _a: "ok", [])
    closed = SwarmToolServer()
    stale = closed.register(lambda _n, _a: "ok", [])
    closed.close()
    resources = AgentResources(
        mcpServers=[
            McpReference(name="off", url=live, enabled=False),
            McpReference(name="stale", url=stale),
        ]
    )
    command = resource_cli_args("codex", resources, tmp_path)
    entries = _server_entries(command)
    names = codex_mcp_server_names(resources, tmp_path)
    # A disabled Raticode server gets no token, and its URL still loses it.
    assert entries[names["off"]] == {"enabled": False, "url": live.rsplit("/", 1)[0] + "/"}
    assert _token(live) not in "\0".join(command)
    assert codex_mcp_token_env(resources) == {}
    # Once a server closes, its host is no longer Raticode's, so a URL on that
    # port is treated like any other user server.
    assert entries[names["stale"]]["url"] == stale


def test_claude_code_keeps_path_token_urls(servers, tmp_path: Path) -> None:
    urls = _turn_urls(servers)
    command = resource_cli_args("claude_code", _resources(urls), tmp_path)
    config = json.loads(command[command.index("--mcp-config") + 1])
    for name, url in urls.items():
        assert config["mcpServers"][name] == {"type": "http", "url": url}


def test_chat_codex_command_carries_no_tool_tokens(servers, tmp_path: Path) -> None:
    urls = _turn_urls(servers)
    resources = _resources(urls)
    command = chat._build_chat_command(
        "codex",
        "cli-default",
        "prompt",
        data_dir=tmp_path,
        working_dir=tmp_path,
        resources=resources,
        trusted_swarm_url=urls["swarm"],
        trusted_organization_url=urls["organizations"],
        trusted_rem_threads_url=urls["rem_threads"],
        trusted_rem_artifacts_url=urls["rem_artifacts"],
        trusted_rem_wait_url=urls["rem_wait"],
    )
    joined = "\0".join(command)
    for url in urls.values():
        assert _token(url) not in joined
    names = codex_mcp_server_names(resources, tmp_path)
    # The trusted tools are still pre-approved by name.
    assert f'mcp_servers.{names["swarm"]}.tools.swarm_action.approval_mode="approve"' in command
    assert (
        f'mcp_servers.{names["organizations"]}.tools.organization_action.approval_mode="approve"'
        in command
    )
    assert f'mcp_servers.{names["rem_wait"]}.tools.sleep.approval_mode="approve"' in command


def _patch_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    async def validate(*_args: Any) -> None:
        return None

    monkeypatch.setattr(chat, "validate_provider_selection_async", validate)
    monkeypatch.setattr(chat, "provider_preference", lambda _: {})
    monkeypatch.setattr(chat, "resolve_provider_executable", lambda p: "codex")
    monkeypatch.setattr(chat, "ensure_local_gofer_cli", lambda _: None)
    monkeypatch.setattr(chat, "with_second_brain", lambda w, *a: w)
    monkeypatch.setattr(chat, "with_report_outputs", lambda w, *a: w)


CODEX_ANSWER = (
    json.dumps({"type": "thread.started", "thread_id": "codex-1"})
    + "\n"
    + json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Done"}})
    + "\n"
)


def _workflow(tmp_path: Path, url: str) -> dict[str, Any]:
    (tmp_path / "project").mkdir(exist_ok=True)
    return {
        "projectRoot": str(tmp_path / "project"),
        "remResources": {"mcpServers": [{"name": "swarm", "url": url}]},
    }


@pytest.mark.asyncio
async def test_streamed_codex_turn_gets_token_in_env_only(
    servers, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_chat(monkeypatch)
    calls: list[tuple[list[str], dict[str, Any]]] = []

    async def subprocess(command: list[str], **kwargs: Any):
        calls.append((command, kwargs))
        yield {"type": "chunk", "stream": "stdout", "text": CODEX_ANSWER}
        yield {"type": "exit", "returncode": 0}

    monkeypatch.setattr(chat, "stream_subprocess", subprocess)
    url = servers[0].register(lambda _n, _a: "ok", [])
    events = [
        event
        async for event in chat.stream_workflow_chat(
            "codex",
            "cli-default",
            [{"role": "user", "body": "hi"}],
            _workflow(tmp_path, url),
            data_dir=tmp_path / "data",
            trusted_swarm_url=url,
        )
    ]
    assert events[-1]["type"] == "final"
    [(command, kwargs)] = calls
    assert _token(url) not in "\0".join(command)
    assert _token(url) not in kwargs["stdin"].decode()
    assert kwargs["env"][f"{CODEX_MCP_TOKEN_ENV_PREFIX}1"] == _token(url)
