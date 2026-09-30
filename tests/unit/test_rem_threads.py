"""Project handoffs use a fresh provider invocation and bounded thread tools."""

from copy import deepcopy
from typing import Any

import pytest

from gofer.ui import rem_threads


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
            workflow={"remThreads": config(), "remResources": {"shell": True}},
            messages=[{"role": "user", "body": "Fix the mobile app"}],
        )
    ]
    assert [event["type"] for event in events] == ["project-scope", "final"]
    assert calls[0]["permission_mode"] == ("read-only" if provider == "codex" else mode)
    assert calls[0]["workflow"]["remResources"]["shell"] is False
    assert calls[1]["permission_mode"] == mode
    assert calls[1]["workflow"]["projectRoot"] == "/mobile"
    assert calls[1]["workflow"]["remResources"]["shell"] is True
    assert calls[1]["workflow"]["remThreads"]["global"] is False
    assert calls[1]["messages"] == calls[0]["messages"]
    assert events[-1]["message"]["body"] == "2"
    assert all(instance.closed for instance in instances)


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
