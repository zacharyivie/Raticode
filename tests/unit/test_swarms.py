from __future__ import annotations

import asyncio
import copy
import json
import threading
import time
import urllib.request
from collections.abc import Callable
from typing import Any

import pytest

from gofer.ui.swarms import SwarmError, SwarmManager, weighted_progress


def config() -> dict[str, Any]:
    return {
        "name": "Build team",
        "agents": [
            {"id": "lead", "name": "Lead", "role": "Coordinate", "isOrchestrator": True},
            {"id": "worker", "name": "Worker", "role": "Implement"},
        ],
    }


@pytest.fixture
def manager(tmp_path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    yield manager
    manager.close()


def test_configuration_and_project_scope(manager, tmp_path):
    swarm = manager.create(tmp_path, config())
    assert not swarm["agents"][0]["allowSteering"]
    assert swarm["maxConcurrency"] == 3
    other = tmp_path / "other"
    other.mkdir()
    assert manager.list(other) == []
    with pytest.raises(SwarmError, match="not found"):
        manager.get(other, swarm["id"])
    invalid = config()
    invalid["agents"][1]["isOrchestrator"] = True
    with pytest.raises(SwarmError, match="exactly one"):
        manager.create(tmp_path, invalid)


def objectives() -> list[dict[str, Any]]:
    return [
        {
            "id": "objective",
            "title": "Deliver",
            "milestones": [
                {
                    "id": "a",
                    "title": "First",
                    "weight": 1,
                    "status": "accepted",
                    "evidence": "test",
                    "waiverReason": "Progress fixture, verified elsewhere by the user",
                },
                {
                    "id": "b",
                    "title": "Second",
                    "weight": 3,
                    "status": "accepted",
                    "evidence": "test",
                    "waiverReason": "Progress fixture, verified elsewhere by the user",
                },
                {"id": "c", "title": "Last", "weight": 1, "ownerId": "worker"},
            ],
        }
    ]


def test_weighted_progress_scope_revisions_and_worker_authority(manager, tmp_path):
    swarm = manager.create(tmp_path, config())
    sid = swarm["id"]
    manager.start(tmp_path, sid, "Build")
    state = manager.objectives(tmp_path, sid, {"objectives": objectives(), "revision": 0})
    assert state["run"]["progress"]["percent"] == 80
    assert state["run"]["progress"]["acceptedWeight"] == 4
    with pytest.raises(SwarmError, match="changed"):
        manager.objectives(tmp_path, sid, {"objectives": objectives(), "revision": 0})
    manager._tokens["worker-token"] = (
        str(tmp_path),
        sid,
        "worker",
        manager.get(tmp_path, sid)["run"]["id"],
    )
    with pytest.raises(SwarmError, match="Only the orchestrator"):
        manager.tool("worker-token", {"action": "objectives", "objectives": []})
    milestone = state["run"]["objectives"][0]["milestones"][2]
    with pytest.raises(SwarmError, match="stale"):
        manager.tool(
            "worker-token",
            {
                "action": "milestone",
                "milestoneId": "c",
                "attemptId": "old",
                "status": "in_review",
                "evidence": "tests",
            },
        )
    receipt = manager.tool(
        "worker-token",
        {
            "action": "milestone",
            "milestoneId": "c",
            "attemptId": milestone["attemptId"],
            "status": "in_review",
            "evidence": "tests",
        },
    )
    assert receipt["revision"] == 2
    assert "run" not in receipt
    state = manager.get(tmp_path, sid)
    assert state["run"]["progress"]["percent"] == 80
    assert state["run"]["revision"] == 2
    assert state["run"]["events"][-1]["kind"] == "milestone_updated"
    changed = copy.deepcopy(state["run"]["objectives"])
    changed[0]["milestones"][2]["weight"] = 6
    state = manager.objectives(tmp_path, sid, {"objectives": changed, "reason": "Expanded testing"})
    assert state["run"]["progress"]["percent"] == 40
    assert state["run"]["objectives"][0]["milestones"][2]["baselineWeight"] == 1


@pytest.mark.parametrize("weight", [0, -1, True, float("nan"), float("inf"), "3"])
def test_invalid_weights_rejected(manager, tmp_path, weight):
    swarm = manager.create(tmp_path, config())
    manager.start(tmp_path, swarm["id"], "Build")
    data = objectives()
    data[0]["milestones"][2]["weight"] = weight
    with pytest.raises(SwarmError, match="weights"):
        manager.objectives(tmp_path, swarm["id"], {"objectives": data})


def test_empty_cancelled_progress():
    assert weighted_progress([])["percent"] is None
    assert weighted_progress([{"weight": 5, "status": "cancelled"}])["percent"] is None


def test_message_deduplication_and_restart(tmp_path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    swarm = manager.create(tmp_path, config())
    sid = swarm["id"]
    manager.start(tmp_path, sid, "Build")
    payload = {"body": "Prioritize tests", "recipientId": "all", "requestId": "req"}
    manager.message(tmp_path, sid, payload)
    state = manager.message(tmp_path, sid, payload)
    assert len(state["run"]["messages"]) == 2
    assert len(state["run"]["messages"][-1]["deliveries"]) == 2
    manager.close()
    recovered = SwarmManager(tmp_path / "data", start_runtime=False)
    assert recovered.get(tmp_path, sid)["run"]["state"] == "paused"
    recovered.control(tmp_path, sid, "resume")
    recovered.control(tmp_path, sid, "stop")
    state = recovered.start(tmp_path, sid, "Next task")
    assert len(state["history"]) == 1
    assert state["run"]["task"] == "Next task"
    recovered.close()


def wait_for(predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out")


def test_runtime_uses_scoped_mcp_tools_and_bounds_turns(tmp_path):
    calls = []

    def call_tool(url, arguments):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "swarm_action", "arguments": arguments},
        }
        request = urllib.request.Request(
            url, json.dumps(payload).encode(), {"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)

    async def stream(**kwargs):
        calls.append(kwargs)
        url = kwargs["workflow"]["remResources"]["mcpServers"][-1]["url"]
        result = await asyncio.to_thread(
            call_tool,
            url,
            {
                "action": "message",
                "body": "Worker task",
                "recipientId": "worker",
                "actionable": True,
            },
        )
        assert "error" not in result
        yield {"type": "final", "message": {"body": "Assigned the work"}}

    manager = SwarmManager(tmp_path / "data", stream=stream)
    settings = config()
    settings["maxTurns"] = 1
    swarm = manager.create(tmp_path, settings)
    manager.start(tmp_path, swarm["id"], "Build")
    wait_for(lambda: manager.get(tmp_path, swarm["id"])["run"]["state"] == "paused")
    state = manager.get(tmp_path, swarm["id"])
    assert len(calls) == 1
    assert any(m["body"] == "Worker task" for m in state["run"]["messages"])
    assert not manager._tokens
    assert "swarm MCP tools" in calls[0]["agent_instructions"]
    manager.close()


def test_pause_keeps_active_turn_and_stop_cancels(tmp_path):
    started = threading.Event()
    finish = threading.Event()

    async def stream(**kwargs):
        started.set()
        while not finish.is_set() and not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "final", "message": {"body": "Done"}}

    manager = SwarmManager(tmp_path / "data", stream=stream)
    swarm = manager.create(tmp_path, config())
    sid = swarm["id"]
    manager.start(tmp_path, sid, "Build")
    assert started.wait(3)
    assert manager.control(tmp_path, sid, "pause")["run"]["state"] == "paused"
    assert manager.get(tmp_path, sid)["run"]["agentStates"]["lead"]["state"] == "working"
    assert manager.control(tmp_path, sid, "stop")["run"]["state"] == "stopping"
    wait_for(lambda: manager.get(tmp_path, sid)["run"]["state"] == "stopped")
    manager.close()


def test_parallel_workers_and_history_survive_turn_boundary(tmp_path):
    from gofer.ui.swarm_workspaces import git

    git(tmp_path, "init")
    git(tmp_path, "commit", "--allow-empty", "-m", "Initial")
    release = threading.Event()
    started = set()
    lock = threading.Lock()

    async def stream(**kwargs):
        name = kwargs["agent_instructions"].split(",", 1)[0]
        with lock:
            started.add(name)
        while not release.is_set() and not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "thought", "text": "Inspecting source", "trace": {"title": "Read"}}
        yield {"type": "final", "message": {"body": "Stored findings"}}

    manager = SwarmManager(tmp_path / "data", stream=stream)
    swarm = manager.create(tmp_path, config())
    sid = swarm["id"]
    manager.start(tmp_path, sid, "Build")
    manager.message(tmp_path, sid, {"body": "Implement", "recipientId": "worker"})
    wait_for(lambda: len(started) == 2)
    manager.control(tmp_path, sid, "pause")
    release.set()
    wait_for(lambda: not manager._active)
    state = manager.get(tmp_path, sid)
    for agent in ("lead", "worker"):
        assert state["run"]["agentStates"][agent]["messages"][-1]["body"] == "Stored findings"
        assert state["run"]["agentStates"][agent]["traces"][-1]["text"] == "Inspecting source"
    manager.close()


def test_old_turn_cannot_replace_current_run(manager, tmp_path):
    swarm = manager.create(tmp_path, config())
    sid = swarm["id"]
    manager.start(tmp_path, sid, "Build")
    manager._active[f"{sid}:worker"] = {
        "swarmId": sid,
        "agentId": "worker",
        "cancel": threading.Event(),
    }
    manager._tokens["lead-token"] = (
        str(tmp_path),
        sid,
        "lead",
        manager.get(tmp_path, sid)["run"]["id"],
    )
    manager.objectives(tmp_path, sid, {"objectives": objectives()})
    with pytest.raises(SwarmError, match="other agents"):
        manager.tool("lead-token", {"action": "complete"})
    manager.control(tmp_path, sid, "stop")
    with pytest.raises(SwarmError, match="active agents"):
        manager.start(tmp_path, sid, "Another task")
    with pytest.raises(SwarmError, match="no longer accepts"):
        manager.tool("lead-token", {"action": "message", "body": "Late result"})
    manager._active.clear()


def test_milestone_reopen_rotates_attempt_and_tools_require_revision(manager, tmp_path):
    swarm = manager.create(tmp_path, config())
    sid = swarm["id"]
    manager.start(tmp_path, sid, "Build")
    data = objectives()
    data[0]["milestones"][2].update(
        status="accepted", evidence="Tests pass", waiverReason="User reviewed tests"
    )
    state = manager.objectives(tmp_path, sid, {"objectives": data})
    old = state["run"]["objectives"][0]["milestones"][2]["attemptId"]
    data = state["run"]["objectives"]
    data[0]["milestones"][2]["status"] = "working"
    state = manager.objectives(tmp_path, sid, {"objectives": data})
    assert state["run"]["objectives"][0]["milestones"][2]["attemptId"] != old
    manager._tokens["lead-token"] = (
        str(tmp_path),
        sid,
        "lead",
        manager.get(tmp_path, sid)["run"]["id"],
    )
    with pytest.raises(SwarmError, match="revision"):
        manager.tool("lead-token", {"action": "objectives", "objectives": data})


class SteeringDouble:
    active = True

    def __init__(self, result: bool = True, uncertain: bool = False) -> None:
        self.result = result
        self.uncertain = uncertain
        self.calls: list[str] = []
        self.entered = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def steer(self, body: str) -> bool:
        self.calls.append(body)
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.uncertain:
            raise TimeoutError("Acknowledgement lost")
        return self.result


def prepare_steering(manager: SwarmManager, root: Any, control: SteeringDouble) -> str:
    swarm = manager.create(root, config())
    sid = swarm["id"]
    manager.start(root, sid, "Build")
    with manager._lock:
        state = manager._get(root, sid)
        state["run"]["messages"][0]["deliveries"][0]["state"] = "completed"
        manager._save(state)
    manager.message(root, sid, {"body": "New direction", "recipientId": "lead"})
    manager._active[f"{sid}:lead"] = {
        "swarmId": sid,
        "runId": state["run"]["id"],
        "agentId": "lead",
        "cancel": threading.Event(),
        "steering": control,
    }
    return str(sid)


@pytest.mark.parametrize(
    "accepted,uncertain,expected",
    [(True, False, "accepted"), (False, False, "queued"), (False, True, "uncertain")],
)
async def test_manager_steering_acknowledgement_and_no_blind_retry(
    manager, tmp_path, accepted, uncertain, expected
):
    control = SteeringDouble(accepted, uncertain)
    sid = prepare_steering(manager, tmp_path, control)
    await manager._steer_pending()
    await manager._steer_pending()
    state = manager.get(tmp_path, sid)
    assert state["run"]["messages"][-1]["deliveries"][0]["state"] == expected
    assert len(control.calls) == 1
    manager._active.clear()


async def test_rejected_steering_delivers_in_next_turn(manager, tmp_path):
    control = SteeringDouble(False)
    sid = prepare_steering(manager, tmp_path, control)
    await manager._steer_pending()
    received = []

    async def stream(**kwargs):
        received.append(kwargs["messages"][-1]["body"])
        yield {"type": "final", "message": {"body": "Handled queued input"}}

    manager._stream = stream
    await manager._turn(str(tmp_path), sid, "lead", threading.Event())
    state = manager.get(tmp_path, sid)
    assert "New direction" in received[0]
    message = next(m for m in state["run"]["messages"] if m["body"] == "New direction")
    assert message["deliveries"][0]["state"] == "completed"


@pytest.mark.parametrize(
    "error,cancelled,expected",
    [
        (None, False, "completed"),
        ("Provider failed", False, "uncertain"),
        (None, True, "uncertain"),
    ],
)
async def test_late_steering_acknowledgement_reconciles_turn_outcome(
    manager, tmp_path, error, cancelled, expected
):
    control = SteeringDouble()
    control.release = asyncio.Event()
    sid = prepare_steering(manager, tmp_path, control)
    task = asyncio.create_task(manager._steer_pending())
    await control.entered.wait()
    active = manager._active.pop(f"{sid}:lead")
    if cancelled:
        active["cancel"].set()
    with manager._lock:
        state = manager._get(tmp_path, sid)
        state["run"]["agentStates"]["lead"]["error"] = error
        manager._save(state)
    control.release.set()
    await task
    delivery = manager.get(tmp_path, sid)["run"]["messages"][-1]["deliveries"][0]
    assert delivery["state"] == expected
    if expected == "completed":
        assert delivery["reason"] == "Agent turn completed"


async def test_late_steering_acknowledgement_cannot_touch_replacement_run(manager, tmp_path):
    control = SteeringDouble()
    control.release = asyncio.Event()
    sid = prepare_steering(manager, tmp_path, control)
    task = asyncio.create_task(manager._steer_pending())
    await control.entered.wait()
    manager._active.clear()
    manager.control(tmp_path, sid, "stop")
    replacement = config()
    replacement["agents"][0]["id"] = "new-lead"
    manager.update(tmp_path, sid, replacement)
    new_run = manager.start(tmp_path, sid, "Fresh task")
    control.release.set()
    await task
    assert manager.get(tmp_path, sid)["run"] == new_run["run"]


async def test_pending_steering_does_not_block_other_agent_launches(manager, tmp_path):
    control = SteeringDouble()
    control.release = asyncio.Event()
    sid = prepare_steering(manager, tmp_path, control)
    worker_started = asyncio.Event()

    async def stream(**kwargs):
        worker_started.set()
        yield {"type": "final", "message": {"body": "Worker findings"}}

    manager._stream = stream
    manager.message(tmp_path, sid, {"body": "Work independently", "recipientId": "worker"})
    loop = asyncio.create_task(manager._loop())
    try:
        await asyncio.wait_for(control.entered.wait(), 2)
        await asyncio.wait_for(worker_started.wait(), 2)
        assert not control.release.is_set()
    finally:
        manager._closed.set()
        manager._wake.set()
        control.release.set()
        await loop
        manager._active.clear()


async def test_periodic_checks_skip_idle_board_and_coalesce_at_turn_limit(manager, tmp_path):
    settings = config()
    settings["maxTurns"] = 1
    swarm = manager.create(tmp_path, settings)
    sid = swarm["id"]
    manager.start(tmp_path, sid, "Build")
    with manager._lock:
        state = manager._get(tmp_path, sid)
        state["run"]["messages"][0]["deliveries"][0]["state"] = "completed"
        state["run"]["lastCheckedAt"] = state["run"]["messages"][0]["createdAt"]
        state["run"]["nextCheckAt"] = 0
        manager._save(state)
    calls = []
    started = asyncio.Event()

    async def stream(**kwargs):
        calls.append(kwargs)
        started.set()
        yield {"type": "final", "message": {"body": "Reviewed board"}}

    manager._stream = stream
    loop = asyncio.create_task(manager._loop())
    try:
        await asyncio.sleep(0.3)
        assert not calls
        with manager._lock:
            state = manager._get(tmp_path, sid)
            manager._post(state, "worker", {"body": "Found a blocker", "actionable": False})
            state["run"]["nextCheckAt"] = 0
            manager._save(state)
        await asyncio.wait_for(started.wait(), 2)
        await asyncio.sleep(0.3)
        assert len(calls) == 1
        assert manager.get(tmp_path, sid)["run"]["state"] == "paused"
        assert manager.get(tmp_path, sid)["run"]["pauseReason"] == "Turn limit reached"
    finally:
        manager._closed.set()
        manager._wake.set()
        await loop


def test_delivery_review_retries_only_selected_recipient_and_audits(manager, tmp_path):
    swarm = manager.create(tmp_path, config())
    sid = swarm["id"]
    manager.start(tmp_path, sid, "Build")
    manager.message(tmp_path, sid, {"body": "Broadcast", "recipientId": "all"})
    with manager._lock:
        state = manager._get(tmp_path, sid)
        message = state["run"]["messages"][-1]
        for delivery in message["deliveries"]:
            delivery.update(state="uncertain", steeringAttempted=True)
        manager._save(state)
    payload = {"messageId": message["id"], "agentId": "lead", "action": "retry"}
    state = manager.resolve_delivery(tmp_path, sid, payload)
    assert [d["state"] for d in state["run"]["messages"][-1]["deliveries"]] == [
        "queued",
        "uncertain",
    ]
    assert state["run"]["events"][-1]["kind"] == "delivery_resolved"
    assert state["run"]["events"][-1]["actorId"] == "user"
    with pytest.raises(SwarmError, match="uncertain"):
        manager.resolve_delivery(tmp_path, sid, payload)
    manager.control(tmp_path, sid, "stop")
    with pytest.raises(SwarmError, match="running or paused"):
        manager.resolve_delivery(tmp_path, sid, {**payload, "agentId": "worker"})
    state = manager.resolve_delivery(
        tmp_path, sid, {**payload, "agentId": "worker", "action": "dismiss"}
    )
    assert state["run"]["messages"][-1]["deliveries"][-1]["state"] == "dismissed"


def test_sidebar_summaries_omit_conversation_history(manager, tmp_path):
    swarm = manager.create(tmp_path, config())
    sid = swarm["id"]
    manager.start(tmp_path, sid, "Build")
    manager.objectives(tmp_path, sid, {"objectives": objectives()})
    with manager._lock:
        state = manager._get(tmp_path, sid)
        state["run"]["agentStates"]["lead"]["messages"] = [
            {"role": "assistant", "body": "Long report"}
        ]
        state["run"]["agentStates"]["lead"]["traces"] = [{"text": "Tool trace"}]
        manager._save(state)
    manager.control(tmp_path, sid, "pause")
    changed_config = config()
    changed_config["agents"][0]["name"] = "Next lead"
    manager.update(tmp_path, sid, changed_config)
    summary = manager.list(tmp_path)[0]
    assert "history" not in summary
    assert "messages" not in summary["run"]
    assert "events" not in summary["run"]
    assert "messages" not in summary["run"]["agentStates"]["lead"]
    assert summary["run"]["progress"]["percent"] == 80
    assert summary["run"]["configuration"]["agents"][0]["name"] == "Lead"
    assert summary["agents"][0]["name"] == "Next lead"
    assert manager.get(tmp_path, sid)["run"]["agentStates"]["lead"]["messages"]


def test_archives_do_not_participate_in_live_writes_or_list_reads(manager, tmp_path):
    swarm = manager.create(tmp_path, config())
    sid = swarm["id"]
    swarm["history"] = [{"id": "old", "task": "x" * 1_000_000}]
    manager._save(swarm)
    statements: list[str] = []
    manager._db.set_trace_callback(statements.append)
    manager.update(tmp_path, sid, {"name": "Changed"})
    summary = manager.list(tmp_path)
    manager._db.set_trace_callback(None)
    assert summary[0]["name"] == "Changed"
    assert "history" not in summary[0]
    assert not any("swarm_history" in sql for sql in statements)
    assert manager.get(tmp_path, sid)["history"][0]["task"] == "x" * 1_000_000
    current = manager.get(tmp_path, sid, include_history=False)
    assert manager.get(tmp_path, sid, since=current["updatedAt"]) == {}


def test_stream_collections_only_change_modified_rows(manager, tmp_path):
    swarm = manager.create(tmp_path, config())
    manager.start(tmp_path, swarm["id"], "Build")
    current = manager._get(tmp_path, swarm["id"])
    current["run"]["agentStates"]["lead"]["messages"] = [
        {"role": "assistant", "content": "previous reply"}
    ]
    manager._save(current)
    manager._db.execute("CREATE TEMP TABLE changes (body TEXT)")
    manager._db.execute(
        "CREATE TEMP TRIGGER track_items AFTER UPDATE ON swarm_items "
        "BEGIN INSERT INTO changes VALUES (new.body); END"
    )
    current = manager._get(tmp_path, swarm["id"])
    current["run"]["agentStates"]["lead"]["thought"] = "new thought"
    manager._save(current)
    assert manager._db.execute("SELECT count(*) FROM changes").fetchone()[0] == 0
    assert manager.get(tmp_path, swarm["id"])["run"]["agentStates"]["lead"]["messages"] == [
        {"role": "assistant", "content": "previous reply"}
    ]


def test_legacy_storage_migrates_and_preserves_archives(tmp_path):
    import sqlite3
    from contextlib import closing

    root = tmp_path / "data"
    original = SwarmManager(root, start_runtime=False)
    swarm = original.create(tmp_path, config())
    original.close()
    swarm["history"] = [{"id": "old", "task": "preserved"}]
    with closing(sqlite3.connect(root / "swarms.sqlite3")) as db, db:
        db.execute("UPDATE swarms SET body=?", (json.dumps(swarm),))
    restored = SwarmManager(root, start_runtime=False)
    try:
        assert restored.get(tmp_path, swarm["id"])["history"] == swarm["history"]
        assert "history" not in restored._get(tmp_path, swarm["id"])
    finally:
        restored.close()


def test_idle_runtime_does_not_query_archived_teams(manager, tmp_path):
    manager.create(tmp_path, config())
    statements: list[str] = []
    manager._db.set_trace_callback(statements.append)

    async def check() -> None:
        task = asyncio.create_task(manager._loop())
        await asyncio.sleep(0.3)
        manager._closed.set()
        manager._wake.set()
        await task

    asyncio.run(check())
    assert not any(sql.startswith("SELECT") for sql in statements)


def test_shared_concurrency_budget_admits_waiting_teams(tmp_path):
    entered: list[str] = []
    release = threading.Event()

    async def stream(**kwargs):
        entered.append(str(kwargs.get("prompt", "")))
        while not release.is_set():
            await asyncio.sleep(0.01)
        yield {"type": "result", "text": "done"}

    manager = SwarmManager(tmp_path / "data", stream=stream, start_runtime=False, max_concurrency=2)
    try:
        for i in range(3):
            project = tmp_path / f"project-{i}"
            project.mkdir()
            swarm = manager.create(project, {**config(), "name": f"Team {i}"})
            manager.start(project, swarm["id"], f"Task {i}")

        async def check() -> None:
            task = asyncio.create_task(manager._loop())
            for _ in range(100):
                if len(entered) == 2:
                    break
                await asyncio.sleep(0.01)
            assert len(entered) == 2
            assert len(manager._active) == 2
            await asyncio.sleep(0.25)
            assert len(entered) == 2
            release.set()
            for _ in range(100):
                if len(entered) == 3:
                    break
                await asyncio.sleep(0.01)
            manager._closed.set()
            manager._wake.set()
            await task
            assert len(entered) == 3
            assert not manager._active

        asyncio.run(check())
    finally:
        release.set()
        manager.close()


def test_collection_owner_names_cannot_overwrite_board_messages(manager, tmp_path):
    definition = config()
    definition["agents"][0]["id"] = "run"
    swarm = manager.create(tmp_path, definition)
    manager.start(tmp_path, swarm["id"], "Board task")
    current = manager._get(tmp_path, swarm["id"])
    current["run"]["agentStates"]["run"]["messages"] = [{"content": "Private transcript"}]
    manager._save(current)
    restored = manager.get(tmp_path, swarm["id"])["run"]
    assert restored["messages"][0]["body"] == "Board task"
    assert restored["agentStates"]["run"]["messages"] == [{"content": "Private transcript"}]


def test_failed_storage_transaction_keeps_the_previous_live_state(manager, tmp_path):
    import sqlite3

    swarm = manager.create(tmp_path, config())
    manager.start(tmp_path, swarm["id"], "Original task")
    original = manager.get(tmp_path, swarm["id"])
    manager._db.execute(
        "CREATE TEMP TRIGGER reject_summary BEFORE INSERT ON swarm_summaries "
        "BEGIN SELECT RAISE(ABORT, 'fixture failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="fixture failure"):
        manager.message(tmp_path, swarm["id"], {"body": "Must roll back"})
    assert manager.get(tmp_path, swarm["id"]) == original


def test_history_summaries_fetch_only_the_selected_archive(manager, tmp_path):
    swarm = manager.create(tmp_path, config())
    swarm["history"] = [
        {"id": str(i), "task": f"Task {i}", "state": "stopped", "messages": ["x" * 100_000]}
        for i in range(4)
    ]
    manager._save(swarm)
    summary = manager.get(tmp_path, swarm["id"], history_summaries=True)
    assert summary["history"] == [
        {"id": str(i), "task": f"Task {i}", "state": "stopped"} for i in range(4)
    ]
    assert manager.history_run(tmp_path, swarm["id"], "2") == swarm["history"][2]
    other = tmp_path / "other-project"
    other.mkdir()
    with pytest.raises(SwarmError, match="not found"):
        manager.history_run(other, swarm["id"], "2")
