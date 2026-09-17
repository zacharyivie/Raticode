from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gofer.core.prompt_envelope import AgentResources
from gofer.ui.chat import _build_chat_command, build_chat_prompt
from gofer.ui.rem_swarms import SWARM_HELP, SWARM_TOOL, RemSwarmAccess
from gofer.ui.swarms import SwarmManager


@pytest.fixture
def access(tmp_path: Path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    try:
        yield RemSwarmAccess(manager, str(tmp_path))
    finally:
        manager.close()


def invoke(access: RemSwarmAccess, operation: str, swarm_id: str = "", **params: Any) -> Any:
    return access.call("swarm_action", {"action": operation, "swarmId": swarm_id, "params": params})


def create(access: RemSwarmAccess) -> str:
    return str(
        invoke(
            access,
            "create",
            name="Release team",
            agents=[
                {"id": "lead", "name": "Lead", "role": "Coordinate", "isOrchestrator": True},
                {"id": "worker", "name": "Reviewer", "role": "Review"},
            ],
        )["id"]
    )


def test_rem_swarm_lifecycle_context_and_archives(access: RemSwarmAccess) -> None:
    sid = create(access)
    assert invoke(access, "list")["items"][0]["run"] is None
    invoke(access, "update", sid, charter="Ship a reviewed change")
    configuration = invoke(access, "read", sid, section="configuration")
    assert configuration["charter"] == "Ship a reviewed change"
    assert configuration["gitPermissions"] == {"local": True, "remote": False}
    run = invoke(
        access,
        "start",
        sid,
        task="Review",
        context={"files": ["src/app.py"], "constraint": "No release"},
    )["run"]
    board = invoke(access, "read", sid, section="board")["items"]
    assert '"constraint": "No release"' in board[0]["body"]
    assert board[0]["recipientIds"] == ["lead"]
    invoke(
        access,
        "message",
        sid,
        body="Findings",
        context="Read the parser",
        recipientId="worker",
        actionable=False,
        requestId="same",
    )
    invoke(
        access,
        "message",
        sid,
        body="Findings",
        context="Read the parser",
        recipientId="worker",
        actionable=False,
        requestId="same",
    )
    board = invoke(access, "read", sid, section="board", limit=1)
    assert board["total"] == 2 and board["nextOffset"] == 1
    last = invoke(access, "read", sid, section="board", offset=1)["items"][0]
    assert last["deliveries"][0]["state"] == "board_only"
    assert last["recipientIds"] == ["worker"]
    with pytest.raises(ValueError, match="Pause"):
        invoke(access, "update", sid, name="Renamed")
    invoke(access, "control", sid, action="pause")
    invoke(access, "update", sid, name="Renamed")
    invoke(access, "control", sid, action="resume")
    assert invoke(access, "read", sid, section="run_configuration")["name"] == "Release team"
    invoke(access, "control", sid, action="stop")
    invoke(access, "start", sid, task="Second run")
    history = invoke(access, "history", sid)["items"]
    assert history[0]["id"] == run["id"]
    archived = invoke(access, "read", sid, runId=run["id"], section="board")
    assert archived["total"] == 2
    assert invoke(access, "read", sid)["run"]["task"] == "Second run"
    assert invoke(access, "read", sid, section="agent", agentId="lead")["items"] == []
    assert invoke(access, "read", sid, section="events")["total"] >= 1


def test_rem_progress_revision_and_evidence(access: RemSwarmAccess) -> None:
    sid = create(access)
    invoke(access, "start", sid, task="Review")
    revision = invoke(access, "read", sid, section="progress")["revision"]
    objectives: list[dict[str, Any]] = [
        {
            "id": "o",
            "title": "Review",
            "milestones": [{"id": "m", "title": "Check", "ownerId": "worker"}],
        }
    ]
    invoke(access, "objectives", sid, revision=revision, objectives=objectives)
    with pytest.raises(ValueError, match="Progress changed"):
        invoke(access, "objectives", sid, revision=revision, objectives=[])
    with pytest.raises(ValueError, match="revision"):
        invoke(access, "objectives", sid, objectives=[])
    objectives[0]["milestones"][0]["status"] = "accepted"
    with pytest.raises(ValueError, match="evidence"):
        invoke(access, "objectives", sid, revision=revision + 1, objectives=objectives)


def test_rem_scope_and_read_only(access: RemSwarmAccess, tmp_path: Path) -> None:
    sid = create(access)
    other = tmp_path / "other"
    other.mkdir()
    foreign = RemSwarmAccess(access.manager, str(other))
    assert invoke(foreign, "list")["items"] == []
    for action in ("read", "update", "start", "control", "message", "history"):
        with pytest.raises(ValueError, match="not found"):
            invoke(foreign, action, sid, task="no", body="no", action="pause")
    with pytest.raises(ValueError, match="fixed"):
        invoke(access, "list", projectRoot=str(other))
    reader = RemSwarmAccess(access.manager, access.project, read_only=True)
    assert invoke(reader, "list")["total"] == 1
    for action in ("create", "update", "start", "control", "message", "objectives", "deliveries"):
        with pytest.raises(ValueError, match="read-only"):
            invoke(reader, action, sid)


def test_rem_rejects_oversize_handoff_before_start(access: RemSwarmAccess) -> None:
    sid = create(access)
    with pytest.raises(ValueError, match="32000"):
        invoke(access, "start", sid, task="Review", context="x" * 32000)
    assert invoke(access, "read", sid)["run"] is None
    with pytest.raises(ValueError, match="limit"):
        invoke(access, "list", limit=101)


@pytest.mark.parametrize("provider", ["codex", "claude_code"])
def test_rem_discovery_is_small_and_provider_uses_private_mcp(
    provider: str, tmp_path: Path
) -> None:
    resources = AgentResources.model_validate(
        {
            "shell": False,
            "mcpServers": [
                {"name": "swarm", "type": "http", "url": "http://127.0.0.1:1234/private"}
            ],
        }
    )
    prompt = build_chat_prompt(
        provider=provider,
        model="cli-default",
        messages=[{"role": "user", "body": "Hello"}],
        workflow={"remResources": resources.model_dump(by_alias=True)},
    )
    assert SWARM_HELP not in prompt
    assert "wakeIntervalSeconds" not in prompt
    assert "private" not in prompt
    assert len(json.dumps(SWARM_TOOL)) < 700
    command = _build_chat_command(
        provider=provider,
        model="cli-default",
        effort=None,
        prompt=prompt,
        working_dir=tmp_path,
        data_dir=tmp_path,
        resources=resources,
        trusted_swarm_url="http://127.0.0.1:1234/private",
    )
    assert "http://127.0.0.1:1234/private" in " ".join(command)
    if provider == "codex":
        assert any('tools.swarm_action.approval_mode="approve"' in arg for arg in command)
        untrusted = _build_chat_command(
            provider=provider,
            model="cli-default",
            effort=None,
            prompt=prompt,
            working_dir=tmp_path,
            data_dir=tmp_path,
            resources=resources,
        )
        assert not any('tools.swarm_action.approval_mode="approve"' in arg for arg in untrusted)
    else:
        assert "mcp__swarm__*" in " ".join(command)


def test_rem_long_context_is_retrievable_without_bloating_summaries(access: RemSwarmAccess) -> None:
    sid = create(access)
    context = ("Evidence from the conversation. " * 100).strip()
    result = invoke(access, "start", sid, task="Review", context=context)
    assert result["run"]["taskTruncated"] is True
    assert len(result["run"]["task"]) == 500
    assert context in invoke(access, "read", sid, section="task")["task"]
    assert context not in json.dumps(invoke(access, "list"))
