"""Run limits, stall handling and access safeguards for swarms."""

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.core.prompt_envelope import AgentResources
from gofer.devices.remote_control import swarm_status
from gofer.ui import swarm_workspaces
from gofer.ui.chat import _build_chat_command
from gofer.ui.rem_swarms import RemSwarmAccess
from gofer.ui.swarms import RUN_LIMITS, SwarmError, SwarmManager


@pytest.fixture
def manager(tmp_path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    yield manager
    manager.close()


def agents(**lead: Any) -> list[dict[str, Any]]:
    return [
        {"id": "lead", "name": "Lead", "role": "Coordinate", "isOrchestrator": True, **lead},
        {"id": "one", "name": "One", "role": "Implement"},
    ]


def team(manager: SwarmManager, root: Path, **settings: Any) -> str:
    root.mkdir(exist_ok=True)
    swarm = manager.create(root, {"name": "Fixture", "agents": agents(), **settings})
    return str(swarm["id"])


async def idle(**kwargs: Any):
    await asyncio.sleep(0.01)
    yield {"type": "final", "message": {"body": "Nothing to do."}}


async def run_loop(manager: SwarmManager, seconds: float) -> None:
    loop = asyncio.create_task(manager._loop())
    try:
        await asyncio.sleep(seconds)
    finally:
        manager._closed.set()
        manager._wake.set()
        await loop


async def test_idle_orchestrator_is_prompted_once_then_the_run_pauses(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    manager.start(root, sid, "Do something")
    manager._stream = idle
    await run_loop(manager, 1.5)
    run = manager.get(root, sid)["run"]
    # The audit probe reached 102 turns in 8 seconds; now one recovery prompt is the limit.
    assert run["state"] == "paused" and run["replanRequired"] is True
    assert "orchestrator stalled" in run["pauseReason"]
    assert run["turnCount"] == 2
    assert sum(m["senderId"] == "system" for m in run["messages"]) == 1


def test_limits_default_on_and_legacy_zero_values_take_the_default(manager, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    swarm = manager.create(
        root, {"name": "Fixture", "agents": agents(), "maxTurns": 0, "maxRunSeconds": 0}
    )
    for key, (default, _, _) in RUN_LIMITS.items():
        assert swarm[key] == default
    assert swarm["maxTokens"] is None
    configured = manager.update(
        root, swarm["id"], {"maxTurns": 5, "maxRepairAttempts": 0, "stallTurnLimit": 2}
    )
    assert (configured["maxTurns"], configured["maxRepairAttempts"]) == (5, 0)
    for bad in ({"maxTurns": -1}, {"maxTurnSeconds": 1}, {"maxTokens": 5}, {"maxConcurrency": 99}):
        with pytest.raises(SwarmError):
            manager.update(root, swarm["id"], bad)


@pytest.mark.parametrize(
    ("settings", "change", "reason"),
    [
        ({"maxTurns": 3}, {"turnCount": 3}, "Turn limit"),
        ({"maxRunSeconds": 60}, {"runningSeconds": 61}, "Running-time limit"),
        (
            {"maxTokens": 1000},
            {"usage": {"reported_input_tokens": 900, "reported_output_tokens": 200}},
            "Token limit",
        ),
    ],
)
async def test_reaching_a_run_limit_pauses_and_blocks_resume(
    manager, tmp_path, settings, change, reason
):
    root = tmp_path / "project"
    sid = team(manager, root, **settings)
    manager.start(root, sid, "Build")
    with manager._lock:
        swarm = manager._get(root, sid)
        swarm["run"].update(change)
        manager._save(swarm)
    calls = []

    async def stream(**kwargs):
        calls.append(kwargs)
        yield {"type": "final", "message": {"body": "Working"}}

    manager._stream = stream
    await run_loop(manager, 0.5)
    run = manager.get(root, sid)["run"]
    assert run["state"] == "paused" and reason in run["pauseReason"]
    assert calls == []
    with pytest.raises(SwarmError, match="higher limits"):
        manager.control(root, sid, "resume")


def test_running_time_excludes_pauses(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    manager.start(root, sid, "Build")
    with manager._lock:
        swarm = manager._get(root, sid)
        swarm["run"]["runningSince"] = time.time() - 100
        manager._save(swarm)
    paused = manager.control(root, sid, "pause")["run"]
    assert 99 <= paused["runningSeconds"] <= 102
    with manager._lock:
        swarm = manager._get(root, sid)
        swarm["run"]["runningSeconds"] = 50
        manager._save(swarm)
    time.sleep(0.05)
    assert manager.get(root, sid)["run"]["elapsedSeconds"] == 50


async def test_a_hung_turn_is_stopped_at_its_deadline(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    manager.start(root, sid, "Build")
    with manager._lock:
        swarm = manager._get(root, sid)
        swarm["run"]["configuration"]["maxTurnSeconds"] = 0.2
        manager._save(swarm)

    async def hang(**kwargs):
        while not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "final", "message": {"body": "Cancelled"}}

    manager._stream = hang
    started = time.monotonic()
    await manager._turn(str(root), sid, "lead", threading.Event())
    assert time.monotonic() - started < 5
    run = manager.get(root, sid)["run"]
    assert "ran longer than" in run["agentStates"]["lead"]["error"]
    # Timed-out work may have side effects, so it waits for review instead of a retry.
    assert run["attempts"][-1]["state"] == "uncertain"
    assert run["agentStates"]["lead"]["state"] == "interrupted"


async def test_periodic_check_waits_for_board_activity(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    manager.start(root, sid, "Build")
    with manager._lock:
        swarm = manager._get(root, sid)
        run = swarm["run"]
        for message in run["messages"]:
            for delivery in message["deliveries"]:
                delivery["state"] = "completed"
        run["lastCheckedAt"] = "9999"
        run["nextCheckAt"] = 0
        run["idleRecovery"] = {"signature": "other", "at": "now"}
        manager._save(swarm)
    # A busy worker alone no longer wakes the orchestrator every interval.
    manager._active[f"{sid}:one"] = {"swarmId": sid, "agentId": "one", "cancel": threading.Event()}
    started = threading.Event()

    async def stream(**kwargs):
        started.set()
        yield {"type": "final", "message": {"body": "Checked"}}

    manager._stream = stream
    await run_loop(manager, 0.4)
    run = manager.get(root, sid)["run"]
    assert not started.is_set()
    assert not any(m["body"].startswith("Periodic check") for m in run["messages"])


async def test_worker_turn_does_not_add_a_second_prompt_after_reporting(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    run = manager.start(root, sid, "Build")["run"]
    manager.message(root, sid, {"body": "Report back", "recipientId": "one"})

    async def report(**kwargs):
        token = next(t for t, i in manager._tokens.items() if i[2] == "one")
        manager.tool(token, {"action": "message", "body": "Done, see notes", "actionable": True})
        yield {"type": "final", "message": {"body": "Reported"}}

    manager._stream = report
    await manager._turn(str(root), sid, "one", threading.Event())
    messages = manager.get(root, sid)["run"]["messages"]
    assert not any("turn finished" in m["body"] for m in messages)
    assert run["state"] == "running"


def test_messages_to_finished_runs_are_rejected(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    manager.start(root, sid, "Build")
    manager.control(root, sid, "stop")
    with pytest.raises(SwarmError, match="Start a new run"):
        manager.message(root, sid, {"body": "Anyone there?"})
    with pytest.raises(SwarmError, match="Cannot resume a stopped run"):
        manager.control(root, sid, "resume")


def test_full_access_agents_need_confirmation_to_start(manager, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    swarm = manager.create(
        root,
        {
            "name": "Fixture",
            "agents": agents(provider="codex", permissionMode="danger-full-access"),
        },
    )
    with pytest.raises(SwarmError, match="without a sandbox"):
        manager.start(root, swarm["id"], "Build")
    run = manager.start(root, swarm["id"], "Build", confirm_full_access=True)["run"]
    assert any(e["kind"] == "full_access_confirmed" for e in run["events"])


def test_full_access_started_by_rem_is_not_recorded_as_the_users_confirmation(manager, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    swarm = manager.create(
        root,
        {
            "name": "Fixture",
            "agents": agents(provider="codex", permissionMode="danger-full-access"),
        },
    )
    run = manager.start(root, swarm["id"], "Build", confirm_full_access=True, confirmed_by="rem")[
        "run"
    ]
    confirmed = [e for e in run["events"] if e["kind"] == "full_access_confirmed"]
    assert [e["actorId"] for e in confirmed] == ["rem"]


def test_start_rejects_a_configuration_changed_since_review(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    reviewed = manager.get(root, sid)["configurationHash"]
    manager.update(root, sid, {"maxTurns": 10})
    with pytest.raises(SwarmError, match="changed before launch"):
        manager.start(root, sid, "Build", expected_configuration_hash=reviewed)
    current = manager.get(root, sid)["configurationHash"]
    assert manager.start(root, sid, "Build", expected_configuration_hash=current)["run"]


def test_restart_leaves_interrupted_runs_paused(tmp_path):
    root = tmp_path / "project"
    data = tmp_path / "data"
    manager = SwarmManager(data, start_runtime=False)
    sid = team(manager, root)
    manager.start(root, sid, "Build")
    with manager._lock:
        swarm = manager._get(root, sid)
        swarm["run"]["attempts"].append(
            {"id": "a1", "ownerId": "one", "milestoneId": None, "state": "running"}
        )
        swarm["run"]["agentStates"]["one"]["state"] = "working"
        manager._save(swarm)
    manager.close()
    recovered = SwarmManager(data, start_runtime=False)
    try:
        run = recovered.get(root, sid)["run"]
        assert run["state"] == "paused" and run["pauseReason"]
        assert [i["kind"] for i in run["humanInbox"] if i["state"] == "pending"] == ["recovery"]
    finally:
        recovered.close()


def test_delete_removes_records_and_lists_retained_work(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    manager.start(root, sid, "Build")
    with pytest.raises(SwarmError, match="Stop the current run"):
        manager.delete(root, sid)
    manager.control(root, sid, "stop")
    assert manager.delete(root, sid)["deleted"] is True
    assert manager.list(root) == []
    with pytest.raises(SwarmError, match="not found"):
        manager.get(root, sid)


def test_input_bounds_and_worktree_workspaces(manager, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(SwarmError, match="charter"):
        manager.create(root, {"name": "Big", "agents": agents(), "charter": "x" * 9000})
    with pytest.raises(SwarmError, match="roles"):
        manager.create(root, {"name": "Big", "agents": agents(role="x" * 5000)})
    inside = manager.data_dir / "workspaces" / "other-run"
    inside.mkdir(parents=True)
    with pytest.raises(SwarmError, match="another swarm run"):
        manager.create(root, {"name": "Nested", "agents": agents(workspacePath=str(inside))})


def test_roster_names_repositories_instead_of_checkout_paths(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    run = manager.start(root, sid, "Build")["run"]
    agent = run["configuration"]["agents"][0]
    context = manager._context(run, agent, [], None)
    assert all("workspacePath" not in member for member in context["roster"])
    assert context["roster"][0]["repository"] == "project"


def test_rem_reads_the_limits_the_next_run_will_use(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    access = RemSwarmAccess(manager, str(root.resolve()))
    config = access.call("swarm_action", {"action": "read", "swarmId": sid, "params": {}})
    assert config  # overview works
    configuration = access.call(
        "swarm_action",
        {"action": "read", "swarmId": sid, "params": {"section": "configuration"}},
    )
    assert configuration["maxTurns"] == RUN_LIMITS["maxTurns"][0]
    assert configuration["maxRepairAttempts"] == RUN_LIMITS["maxRepairAttempts"][0]


def test_swarm_turns_do_not_get_the_data_folder(tmp_path):
    data = tmp_path / "data"
    work = tmp_path / "work"
    data.mkdir()
    work.mkdir()
    for provider, mode in (("codex", "workspace-write"), ("claude_code", None)):
        options: dict[str, Any] = {
            "provider": provider,
            "model": "cli-default",
            "prompt": "x",
            "data_dir": data,
            "working_dir": work,
            "extra_paths": [work],
            "permission_mode": mode,
            "resources": AgentResources.model_validate({}),
        }
        trusted = _build_chat_command(**options)
        isolated = _build_chat_command(**options, trust_data_dir=False)
        assert str(data.resolve()) in trusted
        assert str(data.resolve()) not in isolated and str(work.resolve()) in isolated


async def test_swarm_turns_ask_for_no_data_folder_access(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    manager.start(root, sid, "Build")
    seen: list[dict[str, Any]] = []

    async def stream(**kwargs):
        seen.append(kwargs)
        yield {"type": "final", "message": {"body": "Done"}}

    manager._stream = stream
    await manager._turn(str(root), sid, "lead", threading.Event())
    assert seen[0]["trust_data_dir"] is False
    # Shell pushes are blocked unless the team allows remote Git.
    assert seen[0]["workflow"]["remBlockGitPush"] is True


def test_start_snapshot_leaves_out_secret_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a")
    git("add", ".")
    git("commit", "-qm", "init")
    (repo / ".env").write_text("API_KEY=secret")
    (repo / "deploy.pem").write_text("key")
    (repo / "notes.md").write_text("new notes")
    workspace = swarm_workspaces.prepare_run(repo, tmp_path / "runs" / "r1", name="probe")
    files = git("ls-tree", "-r", "--name-only", workspace["baseRevision"]).split()
    assert "notes.md" in files and ".env" not in files and "deploy.pem" not in files
    assert workspace["excludedFiles"] == [".env", "deploy.pem"]


def test_phone_status_reports_finishing_and_stopping_runs_as_running():
    assert swarm_status({"run": {"state": "completing"}}) == "running"
    assert swarm_status({"run": {"state": "stopping"}}) == "running"
    assert swarm_status({"run": {"state": "stopped"}}) == "cancelled"
