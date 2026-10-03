"""Project handoffs use a fresh provider invocation and bounded thread tools."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from gofer.core.prompt_envelope import AgentResources, resource_index
from gofer.ui import rem_threads
from gofer.ui.chat import _build_chat_command, build_chat_prompt


def config(global_scope: bool = True) -> dict[str, Any]:
    return {"global": global_scope, "projects": [{"root": "/mobile", "name": "Mobile"}]}


def test_thread_actions_validate_scope_authorization_and_deduplicate() -> None:
    actions = rem_threads.ThreadActions(
        config(False), [{"role": "user", "body": "Start a new thread"}]
    )
    with pytest.raises(ValueError, match="Only global"):
        actions.call("select_project", {"projectRoot": "/mobile"})
    with pytest.raises(ValueError, match="open projects"):
        actions.call("start_thread", {"projectRoot": "/unknown"})
    with pytest.raises(ValueError, match="Quote"):
        actions.call(
            "start_thread", {"projectRoot": "/mobile", "message": "Fix", "userRequest": "invented"}
        )
    args = {"projectRoot": "/mobile", "message": "Fix", "userRequest": "Start a new thread"}
    first = actions.call("start_thread", args)
    second = actions.call("start_thread", args)
    assert first["threadId"] == second["threadId"]
    assert len(actions.events) == 1


def test_select_project_is_callable_from_claude_code_plan_mode() -> None:
    # Global Claude Code turns run in plan mode, which only admits read-only MCP tools.
    tools = {tool["name"]: tool for tool in rem_threads.thread_tools(True, config()["projects"])}
    assert tools["select_project"]["annotations"] == {"readOnlyHint": True}
    assert "select_project" not in {
        tool["name"] for tool in rem_threads.thread_tools(False, config()["projects"])
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,mode", [("codex", "workspace-write"), ("claude_code", "dontAsk")]
)
async def test_global_handoff_restarts_in_selected_root_with_original_permissions(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    mode: str,
) -> None:
    instances = []

    class FakeTools:
        def __init__(self) -> None:
            self.callback: Any = None
            self.closed = False
            instances.append(self)

        def register(self, callback: Any, tools: Any, instructions: str) -> str:
            self.callback = callback
            return "http://127.0.0.1/tools"

        def revoke(self, url: str) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(rem_threads, "SwarmToolServer", FakeTools)
    calls = []

    async def source(**kwargs: Any) -> Any:
        calls.append(deepcopy(kwargs))
        if len(calls) == 1:
            instances[-1].callback("select_project", {"projectRoot": "/mobile"})
        yield {"type": "final", "message": {"body": str(len(calls))}}

    events = [
        event
        async for event in rem_threads.stream_with_thread_tools(
            source,
            provider=provider,
            permission_mode=mode,
            workflow={
                "remThreads": config(),
                "remResources": {
                    "shell": True,
                    "web": True,
                    "skills": [{"path": "/skills/research"}],
                    "mcpServers": [{"name": "docs", "url": "https://example.com/mcp"}],
                },
            },
            messages=[{"role": "user", "body": "Fix the mobile app"}],
        )
    ]
    assert [event["type"] for event in events] == ["project-scope", "final"]
    assert calls[0]["permission_mode"] == ("read-only" if provider == "codex" else mode)
    assert calls[0]["workflow"]["remResources"]["shell"] is (provider == "codex")
    for call in calls:
        selected = call["workflow"]["remResources"]
        assert selected["web"] is True
        assert selected["skills"] == [{"path": "/skills/research"}]
        assert [server["name"] for server in selected["mcpServers"]] == ["docs", "rem_threads"]
    assert calls[1]["permission_mode"] == mode
    assert calls[1]["workflow"]["projectRoot"] == "/mobile"
    assert calls[1]["workflow"]["remResources"]["shell"] is True
    assert calls[1]["workflow"]["remThreads"]["global"] is False
    assert calls[1]["messages"] == calls[0]["messages"]
    assert events[-1]["message"]["body"] == "2"
    assert all(instance.closed for instance in instances)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider", ["codex", "claude_code", "cursor", "copilot", "opencode", "grok", "antigravity"]
)
@pytest.mark.parametrize("web", [True, False])
@pytest.mark.parametrize("shell", [True, False])
async def test_global_research_retains_selected_resources_without_project_handoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: str, web: bool, shell: bool
) -> None:
    class FakeTools:
        def register(self, *args: Any) -> str:
            return "http://127.0.0.1/tools"

        def revoke(self, url: str) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(rem_threads, "SwarmToolServer", FakeTools)
    original = {
        "remThreads": config(),
        "remResources": {
            "shell": shell,
            "web": web,
            "skills": [
                {"path": str(tmp_path / "research")},
                {"path": "/disabled", "enabled": False},
            ],
            "mcpServers": [
                {"name": "docs", "url": "https://example.com/mcp"},
                {"name": "disabled", "type": "stdio", "command": "unused", "enabled": False},
            ],
        },
    }
    snapshot = deepcopy(original)
    calls = []

    async def source(**kwargs: Any) -> Any:
        calls.append(kwargs)
        workflow = kwargs["workflow"]
        resources = AgentResources.model_validate(workflow["remResources"])
        assert resources.shell is (shell and provider == "codex")
        assert resources.web is web
        assert resources.skills == AgentResources.model_validate(snapshot["remResources"]).skills
        assert [server.name for server in resources.mcpServers] == [
            "docs",
            "disabled",
            "rem_threads",
        ]
        index = resource_index(resources)
        assert str(tmp_path / "research" / "SKILL.md") in index
        assert "disabled" not in index
        prompt = build_chat_prompt(provider, "cli-default", kwargs["messages"], workflow)
        assert "Global scope retains selected web search, skills and MCP servers." in prompt
        if provider in {"codex", "claude_code"}:
            command = _build_chat_command(
                provider,
                "cli-default",
                prompt,
                working_dir=tmp_path,
                data_dir=tmp_path,
                resources=resources,
                permission_mode=kwargs["permission_mode"],
                global_scope=True,
            )
            if provider == "codex":
                assert command[command.index("--sandbox") + 1] == "read-only"
                assert f'web_search="{"live" if web else "disabled"}"' in command
                assert f"features.shell_tool={str(shell).lower()}" in command
                assert any(arg.startswith("mcp_servers.docs=") for arg in command)
                assert any(arg.startswith("skills.config=") for arg in command)
            else:
                native = command[command.index("--tools") + 1].split(",")
                assert "Read" in native
                assert ("WebSearch" in native) is web
                assert ("WebFetch" in native) is web
                assert not {"Write", "Edit", "Bash"}.intersection(native)
                allowed = command[command.index("--allowedTools") + 1 :]
                assert "mcp__docs__*" in allowed
                assert not any(tool.startswith("Bash") for tool in allowed)
        yield {"type": "final", "message": {"body": "Research complete"}}

    events = [
        event
        async for event in rem_threads.stream_with_thread_tools(
            source,
            provider=provider,
            permission_mode="danger-full-access" if provider == "codex" else "default",
            workflow=original,
            messages=[{"role": "user", "body": "Research usage dashboards"}],
        )
    ]
    assert len(calls) == 1
    assert events == [{"type": "final", "message": {"body": "Research complete"}}]
    assert original == snapshot


@pytest.mark.asyncio
async def test_failed_turn_does_not_apply_queued_action(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTools:
        callback: Any = None

        def register(self, callback: Any, *args: Any) -> str:
            FakeTools.callback = callback
            return "local"

        def revoke(self, url: str) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(rem_threads, "SwarmToolServer", FakeTools)

    async def source(**kwargs: Any) -> Any:
        FakeTools.callback("select_project", {"projectRoot": "/mobile"})
        yield {"type": "error", "error": "provider failed"}

    events = [
        event
        async for event in rem_threads.stream_with_thread_tools(
            source, workflow={"remThreads": config()}, messages=[]
        )
    ]
    assert [event["type"] for event in events] == ["error"]
