"""Terminal Git ownership and liveness, with real Git and provider doubles."""

from __future__ import annotations

import asyncio
import json
import tarfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from gofer.ui.swarm_workspaces import cleanup_run, git, prepare_attempt, prepare_run
from gofer.ui.swarms import SwarmError, SwarmManager


def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    (root / "base.txt").write_text("base")
    git(root, "add", ".")
    git(root, "commit", "-m", "Initial")
    return root


def team(manager: SwarmManager, root: Path) -> str:
    sid = manager.create(
        root,
        {
            "name": "Test",
            "agents": [
                {
                    "id": "lead",
                    "name": "Lead",
                    "role": "Coordinate",
                    "provider": "codex",
                    "isOrchestrator": True,
                },
                {"id": "worker", "name": "Worker", "role": "Implement", "provider": "codex"},
            ],
        },
    )["id"]
    run = manager.start(root, sid, "Implement")["run"]
    manager._tokens["lead"] = (str(root), sid, "lead", run["id"])
    manager._tokens["worker"] = (str(root), sid, "worker", run["id"])
    return str(sid)


def test_cleanup_keeps_parent_and_archives_unique_dirty_ignored_and_orphan_work(tmp_path):
    root = project(tmp_path)
    original = git(root, "rev-parse", "HEAD")
    workspace = prepare_run(root, tmp_path / "runs" / "run")
    checkout = prepare_attempt(workspace, "child")
    child = Path(checkout["path"])
    (child / "unique.txt").write_text("unique")
    git(child, "add", ".")
    git(child, "commit", "-m", "Unique work")
    unique = git(child, "rev-parse", "HEAD")
    (child / "base.txt").write_text("dirty")
    (child / ".gitignore").write_text("ignored.txt\n")
    (child / "ignored.txt").write_text("preserve me")
    (child / "new.txt").write_text("new")
    nested = child / "nested-repo"
    nested.mkdir()
    git(nested, "init")
    (nested / "unique.txt").write_text("nested work")
    orphan = prepare_attempt(workspace, "orphan")
    git(root, "worktree", "remove", orphan["path"])
    candidate = Path(workspace["directory"]) / "integration-deadbeef"
    git(root, "worktree", "add", "--detach", str(candidate), unique)
    result = cleanup_run(workspace, [{"workspace": checkout}, {"workspace": orphan}])
    assert result["passed"]
    assert git(root, "rev-parse", "HEAD") == original
    assert git(Path(workspace["path"]), "rev-parse", "HEAD") == original
    assert (
        git(root, "branch", "--format=%(refname:short)", "--list", "raticode/swarm/*").strip()
        == workspace["branch"]
    )
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 2
    archive = Path(workspace["directory"]) / "retained" / "child"
    assert json.loads((archive / "manifest.json").read_text())["revision"] == unique
    with tarfile.open(archive / "files.tar.gz") as saved:
        assert saved.extractfile("ignored.txt").read() == b"preserve me"
        assert saved.extractfile("base.txt").read() == b"dirty"
        assert saved.extractfile("new.txt").read() == b"new"
        assert saved.extractfile("nested-repo/unique.txt").read() == b"nested work"
        assert saved.getmember("nested-repo/.git/HEAD")
    restored = tmp_path / "restored"
    git(root, "clone", str(archive / "commits.bundle"), str(restored))
    assert (restored / "unique.txt").read_text() == "unique"
    assert cleanup_run(workspace, [])["passed"]  # Repeated cleanup is harmless.


def test_cleanup_archive_failure_preserves_child_and_ref(tmp_path):
    root = project(tmp_path)
    workspace = prepare_run(root, tmp_path / "runs" / "run")
    child = prepare_attempt(workspace, "child")
    with patch("gofer.ui.swarm_workspaces.tarfile.open", side_effect=OSError("Disk full")):
        with pytest.raises(OSError, match="Disk full"):
            cleanup_run(workspace, [{"workspace": child}])
    assert Path(child["path"]).exists()
    assert git(root, "rev-parse", child["branch"])


@pytest.mark.parametrize("busy", ["turn", "check", "retry", "queued"])
def test_waiting_work_is_not_an_idle_failure(tmp_path, busy):
    with_manager = SwarmManager(tmp_path / "data", start_runtime=False)
    try:
        sid = team(with_manager, tmp_path)
        state = with_manager._get(tmp_path, sid)
        if busy != "queued":
            state["run"]["messages"][0]["deliveries"][0]["state"] = "completed"
        if busy == "turn":
            with_manager._active["busy"] = {"swarmId": sid, "cancel": threading.Event()}
        elif busy == "check":
            with_manager._checks["busy"] = {"swarmId": sid, "cancel": threading.Event()}
        elif busy == "retry":
            state["run"]["agentStates"]["worker"]["state"] = "retry_wait"
        assert not with_manager._recover_idle(state)
        assert state["run"]["state"] == "running"
    finally:
        with_manager._active.clear()
        with_manager._checks.clear()
        with_manager.close()


def test_digest_requires_coordinator_and_exact_roster_and_persists(tmp_path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    try:
        sid = team(manager, tmp_path)
        entries = [
            {"agentId": "lead", "summary": "Reviewing the changes"},
            {"agentId": "worker", "summary": "Fixing the failing parser test"},
        ]
        with pytest.raises(SwarmError, match="Only the coordinator"):
            manager.tool("worker", {"action": "digest", "agents": entries})
        for invalid in [
            entries[:1],
            entries + entries[:1],
            [*entries[:1], {"agentId": "worker", "summary": "x" * 161}],
        ]:
            with pytest.raises(SwarmError, match="one summary"):
                manager.tool("lead", {"action": "digest", "agents": invalid})
        manager.tool("lead", {"action": "digest", "agents": entries})
        assert manager.get(tmp_path, sid)["run"]["digest"]["agents"] == entries
        assert manager.get(tmp_path, sid)["run"]["agentStates"]["lead"]["state"] == "queued"
    finally:
        manager.close()


async def test_stop_cleans_only_after_active_turn_drains(tmp_path):
    root = project(tmp_path)
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    try:
        sid = team(manager, root)
        entered, release = asyncio.Event(), asyncio.Event()

        async def stream(**kwargs):
            entered.set()
            await release.wait()
            yield {"type": "final", "message": {"body": "Stopped"}}

        manager._stream = stream
        loop = asyncio.create_task(manager._loop())
        await asyncio.wait_for(entered.wait(), 2)
        run = manager.control(root, sid, "stop")["run"]
        assert run["state"] == "stopping"
        path = Path(next(a for a in run["attempts"] if a.get("workspace"))["workspace"]["path"])
        assert path.exists()
        release.set()
        for _ in range(200):
            await asyncio.sleep(0.01)
            run = manager.get(root, sid)["run"]
            if run.get("cleanup", {}).get("passed"):
                break
        assert run["state"] == "stopped" and run["cleanup"]["passed"]
        assert not path.exists()
        assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 2
    finally:
        manager._closed.set()
        manager._wake.set()
        await loop
        manager.close()


@pytest.mark.parametrize("terminal", ["stopped", "completing"])
async def test_restart_finishes_pending_cleanup_without_starting_agents(tmp_path, terminal):
    root = project(tmp_path)
    data = tmp_path / "data"
    manager = SwarmManager(data, start_runtime=False)
    sid = team(manager, root)
    state = manager._get(root, sid)
    child = prepare_attempt(state["run"]["workspace"], "child")
    state["run"]["state"] = terminal
    manager._save(state)
    manager.close()
    recovered = SwarmManager(data, start_runtime=False)
    try:
        assert sid in recovered._terminal
        await recovered._finish_run(str(root), sid)
        run = recovered.get(root, sid)["run"]
        assert run["cleanup"]["passed"]
        assert run["state"] == ("completed" if terminal == "completing" else "stopped")
        assert not Path(child["path"]).exists()
        assert run["turnCount"] == 0
    finally:
        recovered.close()


async def test_periodic_digest_is_queued_even_when_all_turn_slots_are_busy(tmp_path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    sid = team(manager, tmp_path)
    state = manager._get(tmp_path, sid)
    state["run"]["messages"][0]["deliveries"][0]["state"] = "completed"
    state["run"]["configuration"]["maxConcurrency"] = 1
    state["run"]["nextCheckAt"] = 0
    manager._active[f"{sid}:worker"] = {
        "swarmId": sid,
        "agentId": "worker",
        "cancel": threading.Event(),
        "projectRoot": str(tmp_path),
    }
    manager._save(state)
    loop = asyncio.create_task(manager._loop())
    try:
        for _ in range(100):
            await asyncio.sleep(0.01)
            messages = manager.get(tmp_path, sid)["run"]["messages"]
            if len(messages) > 1:
                break
        assert len(messages) == 2
        assert "digest" in messages[-1]["body"]
        assert messages[-1]["deliveries"][0]["state"] == "queued"
        assert manager.get(tmp_path, sid)["run"]["turnCount"] == 0
    finally:
        manager._closed.set()
        manager._wake.set()
        await loop
        manager._active.clear()
        manager.close()


def test_idle_recovery_is_actionable_and_deduplicated(tmp_path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    try:
        sid = team(manager, tmp_path)
        swarm = manager._get(tmp_path, sid)
        run = swarm["run"]
        run["messages"][0]["deliveries"][0]["state"] = "completed"
        run.setdefault("attempts", []).append(
            {
                "id": "uncertain",
                "ownerId": "lead",
                "state": "uncertain",
            }
        )
        manager._recover_idle(swarm)
        message = run["messages"][-1]
        assert message["recovery"]
        assert manager._dispatchable(run, message, "lead")
        assert not manager._dispatchable(run, message, "worker") or not any(
            d["agentId"] == "worker" for d in message["deliveries"]
        )
        assert run["attempts"][-1]["state"] == "uncertain"
        count = len(run["messages"])
        manager._recover_idle(swarm)
        assert len(run["messages"]) == count
        assert run["state"] == "running"
        assert not run.get("cleanup")
    finally:
        manager.close()


async def test_recovery_timeout_retries_same_attempt_without_losing_workspace(tmp_path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    try:
        sid = team(manager, tmp_path)
        swarm = manager._get(tmp_path, sid)
        swarm["run"]["messages"][0]["deliveries"][0]["state"] = "completed"
        manager._recover_idle(swarm)

        async def timeout(**kwargs):
            raise TimeoutError("Provider timed out")
            yield  # pragma: no cover

        manager._stream = timeout
        await manager._turn(str(tmp_path), sid, "lead", threading.Event())
        swarm = manager._get(tmp_path, sid)
        run = swarm["run"]
        assert run["agentStates"]["lead"]["state"] == "retry_wait"
        first = dict(run["attempts"][0])
        run["agentStates"]["lead"]["retryAt"] = 0
        manager._save(swarm)

        async def finish(**kwargs):
            manager.control(tmp_path, sid, "pause")
            yield {"type": "final", "message": {"body": "Recovered"}}

        manager._stream = finish
        await manager._turn(str(tmp_path), sid, "lead", threading.Event())
        run = manager.get(tmp_path, sid)["run"]
        assert len(run["attempts"]) == 1
        assert run["attempts"][0]["id"] == first["id"]
        assert run["attempts"][0]["workspace"] == first["workspace"]
        assert run["attempts"][0]["state"] == "succeeded"
    finally:
        manager.close()


def test_coordinator_can_review_prior_uncertain_attempt_during_recovery(tmp_path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    try:
        sid = team(manager, tmp_path)
        swarm = manager._get(tmp_path, sid)
        swarm["run"].setdefault("attempts", []).append(
            {
                "id": "prior",
                "ownerId": "lead",
                "state": "uncertain",
            }
        )
        manager._save(swarm)
        manager._active[f"{sid}:lead"] = {
            "swarmId": sid,
            "agentId": "lead",
            "attemptId": "recovery",
        }
        payload = {
            "action": "resolve_attempt",
            "attemptId": "prior",
            "resolution": "review",
            "reason": "Prior process stopped; inspected its saved output",
        }
        with pytest.raises(SwarmError, match="Only the coordinator"):
            manager.tool("worker", payload)
        manager.tool("lead", payload)
        assert manager.get(tmp_path, sid)["run"]["attempts"][0]["state"] == "succeeded"
    finally:
        manager._active.clear()
        manager.close()


@pytest.mark.parametrize("decision", ["proceed", "redirect"])
def test_human_approval_waits_only_when_no_independent_work_remains(tmp_path, decision):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    try:
        sid = team(manager, tmp_path)
        request = {
            "action": "request_approval",
            "description": "Publish the change?",
            "recommendedAction": "Review the diff before publishing",
            "requestId": "publish",
        }
        manager.tool("worker", request)
        manager.tool("worker", request)
        state = manager._get(tmp_path, sid)
        assert len(state["run"]["humanInbox"]) == 1
        # The coordinator still has its initial task and may run independently.
        assert not manager._recover_idle(state)
        for message in state["run"]["messages"]:
            for delivery in message["deliveries"]:
                delivery["state"] = "completed"
        manager._save(state)
        assert manager._recover_idle(state)
        run = manager.get(tmp_path, sid)["run"]
        assert run["state"] == "paused"
        item = run["humanInbox"][0]
        payload = {
            "action": "human_response",
            "notificationId": item["id"],
            "decision": decision,
            "instruction": "Only inspect the diff. Do not publish."
            if decision == "redirect"
            else "",
        }
        manager.execution(tmp_path, sid, payload)
        manager.execution(tmp_path, sid, payload)
        run = manager.get(tmp_path, sid)["run"]
        responses = [m for m in run["messages"] if m.get("requestId") == f"approval:{item['id']}"]
        assert len(responses) == 1
        assert responses[0]["recipientIds"] == ["worker"]
        assert responses[0]["deliveries"][0]["state"] == "queued"
        expected = payload["instruction"] or request["recommendedAction"]
        assert run["humanInbox"][0]["nextSteps"] == expected
        assert run["humanInbox"][0]["state"] == "addressed"
        assert responses[0]["body"] == f"Human response: {expected}"
        manager.close()
        manager = SwarmManager(tmp_path / "data", start_runtime=False)
        assert manager.get(tmp_path, sid)["run"]["humanInbox"][0]["nextSteps"] == expected
        assert manager._dispatchable(run, responses[0], "worker")
    finally:
        manager.close()


@pytest.mark.parametrize("state", ["completed", "stopped", "failed"])
def test_startup_does_not_recover_terminal_runs(tmp_path, state):
    data = tmp_path / "data"
    manager = SwarmManager(data, start_runtime=False)
    sid = team(manager, tmp_path)
    swarm = manager._get(tmp_path, sid)
    swarm["run"]["state"] = state
    swarm["run"]["attempts"] = [{"id": "old", "ownerId": "worker", "state": "running"}]
    manager._save(swarm)
    before = manager.get(tmp_path, sid)["run"]
    manager.close()
    recovered = SwarmManager(data, start_runtime=False)
    try:
        assert recovered.get(tmp_path, sid)["run"] == before
    finally:
        recovered.close()


def test_legacy_recovery_keeps_independent_work_runnable_and_is_idempotent(tmp_path):
    data = tmp_path / "data"
    manager = SwarmManager(data, start_runtime=False)
    sid = team(manager, tmp_path)
    swarm = manager._get(tmp_path, sid)
    # Original JSON persistence, with no turn keys, inbox or recovery fields.
    swarm["run"]["attempts"] = [
        {
            "id": "legacy",
            "ownerId": "worker",
            "state": "dispatching",
            "workspace": {"path": str(tmp_path)},
            "milestoneId": None,
        }
    ]
    with manager._db:
        manager._db.execute("UPDATE swarms SET body=? WHERE id=?", (json.dumps(swarm), sid))
    manager.close()
    previous = None
    for _ in range(2):
        manager = SwarmManager(data, start_runtime=False)
        try:
            run = manager.get(tmp_path, sid)["run"]
            assert run["state"] == "running"  # Coordinator's initial task is independent.
            assert manager._dispatchable(run, run["messages"][0], "lead")
            assert run["attempts"][0]["state"] == "uncertain"
            assert len(run["humanInbox"]) == 1
            assert sum(e["kind"] == "recovered" for e in run["events"]) == 1
            if previous is not None:
                assert run == previous
            previous = run
        finally:
            manager.close()


def test_start_prepares_named_review_worktrees_once_per_project_and_retains_them(tmp_path):
    desktop = project(tmp_path)
    mobile_parent = tmp_path / "mobile"
    mobile_parent.mkdir()
    mobile = project(mobile_parent)
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    try:
        sid = manager.create(
            desktop,
            {
                "name": "Mobile + Desktop / Release",
                "agents": [
                    {
                        "id": "lead",
                        "name": "Lead",
                        "role": "Coordinate",
                        "provider": "codex",
                        "isOrchestrator": True,
                    },
                    {
                        "id": "mobile",
                        "name": "Mobile",
                        "role": "Implement",
                        "provider": "codex",
                        "workspacePath": str(mobile),
                    },
                    {
                        "id": "reviewer",
                        "name": "Reviewer",
                        "role": "Review",
                        "provider": "codex",
                        "workspacePath": str(mobile),
                    },
                ],
            },
        )["id"]
        run = manager.start(desktop, sid, "Prepare release")["run"]
        assert run["attempts"] == []
        assert list(run["projectWorkspaces"]) == [str(mobile)]
        destinations = [run["workspace"], run["projectWorkspaces"][str(mobile)]]
        assert destinations[0]["path"] != destinations[1]["path"]
        for root, workspace in zip((desktop, mobile), destinations, strict=True):
            path = Path(workspace["path"])
            assert path.name == "mobile-desktop-release-project-review"
            assert git(path, "branch", "--show-current") == workspace["branch"]
            assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 2
            child = prepare_attempt(workspace, "unfinished")
            (Path(child["path"]) / "draft.txt").write_text(str(root))
            outcome = cleanup_run(workspace, [{"id": "unfinished", "workspace": child}])
            assert outcome["passed"] and outcome["archives"]
            assert path.is_dir() and not Path(child["path"]).exists()
            assert not (path / "draft.txt").exists()
            assert cleanup_run(workspace, [])["passed"]
        # Reopening preserves destinations, including two repos with identical basenames.
        reopened = SwarmManager(tmp_path / "data", start_runtime=False)
        try:
            restored = reopened.get(desktop, sid)["run"]
            assert restored["workspace"] == run["workspace"]
            assert restored["projectWorkspaces"] == run["projectWorkspaces"]
        finally:
            reopened.close()
    finally:
        manager.close()


def test_start_preparation_failure_preserves_review_destination_without_dispatch(tmp_path):
    root = project(tmp_path)
    mobile = tmp_path / "mobile"
    mobile.mkdir()
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    try:
        sid = manager.create(
            root,
            {
                "name": "Release",
                "agents": [
                    {
                        "id": "lead",
                        "name": "Lead",
                        "role": "Coordinate",
                        "provider": "codex",
                        "isOrchestrator": True,
                        "workspacePath": str(mobile),
                    }
                ],
            },
        )["id"]
        original = prepare_run

        def prepare(path, directory, **kwargs):
            if path == mobile:
                raise OSError("Disk full")
            return original(path, directory, **kwargs)

        with patch("gofer.ui.swarms.workspaces.prepare_run", side_effect=prepare):
            with pytest.raises(SwarmError, match="Workspace preparation failed: Disk full"):
                manager.start(root, sid, "Prepare release")
        run = manager.get(root, sid)["run"]
        assert run["state"] == "failed"
        assert not run["attempts"] and not run["messages"]
        assert Path(run["workspace"]["path"]).is_dir()
    finally:
        manager.close()
