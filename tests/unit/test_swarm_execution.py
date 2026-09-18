"""Swarm execution boundaries, exercised with local Git and fake providers."""

from __future__ import annotations

import asyncio
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from gofer.ui.swarm_workspaces import (
    cleanup_accepted_attempt,
    cleanup_completed_turn,
    git,
    prepare_attempt,
    prepare_run,
    run_checks,
)
from gofer.ui.swarms import SwarmError, SwarmManager


@pytest.fixture
def manager(tmp_path):
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    yield manager
    manager.close()


@pytest.mark.parametrize(
    "preserve", ["none", "dirty", "commit", "milestone", "uncertain", "ignored"]
)
def test_completed_turn_cleanup_preserves_work(tmp_path, preserve):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    (root / ".gitignore").write_text("ignored.txt\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Initial")
    workspace = prepare_run(root, tmp_path / "workspaces" / "run")
    checkout = prepare_attempt(workspace, "attempt")
    path = Path(checkout["path"])
    attempt = {"id": "attempt", "state": "succeeded", "workspace": checkout}
    if preserve in {"dirty", "commit"}:
        (path / "work.txt").write_text("preserve")
    if preserve == "commit":
        git(path, "add", ".")
        git(path, "commit", "-m", "Work")
    if preserve == "ignored":
        (path / "ignored.txt").write_text("preserve")
    if preserve == "milestone":
        attempt["milestoneId"] = "review-needed"
    if preserve == "uncertain":
        attempt["state"] = "uncertain"
    assert cleanup_completed_turn(workspace, attempt) is (preserve == "none")
    assert path.exists() is (preserve != "none")


@pytest.mark.parametrize(
    "preserve",
    [
        "none",
        "dirty",
        "untracked",
        "ignored",
        "commit",
        "unmerged",
        "active",
        "failed",
        "outside",
        "remove_error",
    ],
)
def test_accepted_attempt_cleanup_preserves_unintegrated_work(tmp_path, preserve):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    (root / ".gitignore").write_text("ignored.txt\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Initial")
    workspace = prepare_run(root, tmp_path / "workspaces" / "run")
    checkout = prepare_attempt(workspace, "attempt")
    path = Path(checkout["path"])
    (path / "work.txt").write_text("implemented")
    git(path, "add", ".")
    git(path, "commit", "-m", "Work")
    revision = git(path, "rev-parse", "HEAD")
    attempt: dict[str, Any] = {
        "id": "attempt",
        "state": "verified",
        "workspace": checkout,
        "result": {"passed": True, "revision": revision},
        "integration": {"passed": True, "submittedRevision": revision},
    }
    if preserve != "unmerged":
        git(Path(workspace["path"]), "merge", "--ff-only", revision)
    if preserve in {"dirty", "commit"}:
        (path / "work.txt").write_text("additional work")
    if preserve == "commit":
        git(path, "add", ".")
        git(path, "commit", "-m", "Unsubmitted work")
    if preserve in {"untracked", "ignored"}:
        (path / f"{preserve}.txt").write_text("preserve")
    if preserve == "active":
        attempt["state"] = "running"
    if preserve == "failed":
        attempt["integration"]["passed"] = False
    if preserve == "outside":
        workspace["directory"] = str(tmp_path / "different")
    if preserve == "remove_error":

        def fail_remove(root, *args, **kwargs):
            if args[:2] == ("worktree", "remove"):
                raise OSError("read-only metadata")
            return git(root, *args, **kwargs)

        with patch("gofer.ui.swarm_workspaces.git", side_effect=fail_remove):
            assert not cleanup_accepted_attempt(workspace, attempt)
    else:
        assert cleanup_accepted_attempt(workspace, attempt) is (preserve == "none")
    assert path.exists() is (preserve != "none")
    if preserve == "none":
        with pytest.raises(ValueError):
            git(root, "rev-parse", "--verify", checkout["branch"])
    else:
        assert git(root, "rev-parse", checkout["branch"]) == git(
            Path(workspace["path"]), "rev-parse", checkout["branch"]
        )


@pytest.mark.parametrize("original", ["", "/usr/local/lib"])
def test_checks_do_not_inherit_packaged_app_libraries(tmp_path, monkeypatch, original):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/_MEIexample")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", original)
    script = (
        "import os, ssl; "
        f"assert os.environ.get('LD_LIBRARY_PATH', '') == {original!r}; "
        "assert 'LD_LIBRARY_PATH_ORIG' not in os.environ"
    )
    results = run_checks(tmp_path, [[sys.executable, "-c", script]], tmp_path / "logs")
    assert results[0]["exitCode"] == 0


def team(manager: SwarmManager, root: Path, **settings: Any) -> str:
    root.mkdir(exist_ok=True)
    swarm = manager.create(
        root,
        {
            "name": "Fixture",
            "agents": [
                {"id": "lead", "name": "Lead", "role": "Coordinate", "isOrchestrator": True},
                {"id": "one", "name": "One", "role": "Implement"},
                {"id": "two", "name": "Two", "role": "Implement"},
            ],
            **settings,
        },
    )
    state = manager.start(root, swarm["id"], "Implement the requested work")
    for actor in ("lead", "one", "two"):
        manager._tokens[actor] = (str(root), swarm["id"], actor, state["run"]["id"])
    return str(swarm["id"])


def plan(
    manager: SwarmManager, root: Path, sid: str, milestones: list[dict[str, Any]]
) -> dict[str, Any]:
    return manager.objectives(
        root,
        sid,
        {
            "objectives": [
                {
                    "id": "objective",
                    "title": "Deliver",
                    "milestones": milestones,
                }
            ]
        },
    )


def milestone(mid: str = "a", owner: str = "one", **values: Any) -> dict[str, Any]:
    return {"id": mid, "title": mid, "ownerId": owner, **values}


async def finish(**kwargs):
    yield {"type": "final", "message": {"body": "Findings saved"}}


def assignment(
    manager: SwarmManager, root: Path, sid: str, mid: str = "a"
) -> tuple[dict[str, Any], dict[str, Any]]:
    run = manager.get(root, sid)["run"]
    item = next(m for o in run["objectives"] for m in o["milestones"] if m["id"] == mid)
    attempt = next(a for a in run["attempts"] if a["id"] == item["attemptId"])
    return item, attempt


@pytest.mark.parametrize("delivery_state", ["starting", "accepted", "steering"])
def test_paused_restart_recovers_once_and_requires_review(tmp_path, delivery_state):
    root = tmp_path / "project"
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    sid = team(manager, root)
    state = plan(manager, root, sid, [milestone()])
    run = state["run"]
    message = run["messages"][-1]
    message["deliveries"][0]["state"] = delivery_state
    run["attempts"][0]["state"] = "running"
    run["agentStates"]["one"]["state"] = "working"
    manager._save(state)
    manager.control(root, sid, "pause")
    manager.close()
    for iteration in range(2):
        manager = SwarmManager(tmp_path / "data", start_runtime=False)
        recovered = manager.get(root, sid)["run"]
        assert recovered["state"] == "paused"
        assert recovered["agentStates"]["one"]["state"] == "interrupted"
        assert recovered["attempts"][0]["state"] == "uncertain"
        assert recovered["messages"][-1]["deliveries"][0]["state"] == "uncertain"
        assert len([e for e in recovered["events"] if e["kind"] == "recovered"]) == 1
        if iteration == 0:
            manager.close()
    manager.resolve_delivery(
        root, sid, {"messageId": message["id"], "agentId": "one", "action": "retry"}
    )
    assert manager.get(root, sid)["run"]["attempts"][0]["state"] == "pending"
    manager.close()


def test_dependencies_are_validated_and_assignment_dispatch_is_atomic(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    with pytest.raises(SwarmError, match="cycle"):
        plan(
            manager, root, sid, [milestone(dependsOn=["b"]), milestone("b", "two", dependsOn=["a"])]
        )
    assert manager.get(root, sid)["run"]["objectives"] == []
    with pytest.raises(SwarmError, match="Unknown milestone dependency"):
        plan(manager, root, sid, [milestone(dependsOn=["missing"])])
    state = plan(manager, root, sid, [milestone(), milestone("b", "two", dependsOn=["a"])])
    run = state["run"]
    assert len(run["attempts"]) == 2
    blocked = run["messages"][-1]
    assert blocked["milestoneId"] == "b" and blocked["attemptId"]
    assert not manager._dispatchable(run, blocked, "two")
    same = manager.objectives(
        root, sid, {"objectives": run["objectives"], "revision": run["revision"]}
    )
    assert len(same["run"]["messages"]) == len(run["messages"])
    with pytest.raises(SwarmError, match="changed"):
        manager.objectives(
            root, sid, {"objectives": run["objectives"], "revision": run["revision"]}
        )
    with pytest.raises(SwarmError, match="dependencies"):
        manager.tool(
            "two",
            {
                "action": "milestone",
                "milestoneId": "b",
                "attemptId": blocked["attemptId"],
                "status": "working",
            },
        )
    data = same["run"]["objectives"]
    data[0]["milestones"][0].update(
        status="accepted", evidence="Reviewed", waiverReason="User inspected the output"
    )
    ready = manager.objectives(root, sid, {"objectives": data})
    assert manager._dispatchable(ready["run"], blocked, "two")


async def test_worker_failure_preserves_successful_sibling_and_blocks_replay(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    plan(manager, root, sid, [milestone(), milestone("b", "two")])
    manager._stream = finish
    await manager._turn(str(root), sid, "two", threading.Event())
    successful = assignment(manager, root, sid, "b")[1]

    async def failure(**kwargs):
        yield {"type": "error", "error": "Disconnected"}

    manager._stream = failure
    await manager._turn(str(root), sid, "one", threading.Event())
    state = manager.get(root, sid)
    assert state["run"]["state"] == "running"
    assert assignment(manager, root, sid, "b")[1] == successful
    failed = assignment(manager, root, sid)[1]
    assert failed["state"] == "pending"
    assert state["run"]["agentStates"]["one"]["state"] == "retry_wait"
    manager.message(root, sid, {"recipientId": "one", "body": "Try again"})
    run = manager.get(root, sid)["run"]
    assert not manager._dispatchable(run, run["messages"][-1], "one")
    assert failed["recovery"]["retryNumber"] == 1


async def test_artifact_verification_rejects_claims_failures_and_changed_files(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    plan(
        manager,
        root,
        sid,
        [milestone(checks=[[sys.executable, "-c", "assert open('result.txt').read() == 'pass'"]])],
    )
    manager._stream = finish
    await manager._turn(str(root), sid, "one", threading.Event())
    item, attempt = assignment(manager, root, sid)
    data = manager.get(root, sid)["run"]["objectives"]
    data[0]["milestones"][0].update(
        status="accepted", evidence="Tests passed", result={"passed": True}
    )
    with pytest.raises(SwarmError, match="application-recorded"):
        manager.objectives(root, sid, {"objectives": data})
    args = {
        "action": "verify",
        "milestoneId": "a",
        "attemptId": attempt["id"],
        "result": {"artifacts": ["result.txt"]},
    }
    (root / "result.txt").write_text("fail")
    result = manager.tool("one", args)["result"]
    assert not result["passed"] and result["checks"][0]["exitCode"] == 1
    assert "AssertionError" in Path(result["checks"][0]["logPath"]).read_text()
    (root / "result.txt").write_text("pass")
    assert manager.tool("one", args)["result"]["passed"]
    data = manager.get(root, sid)["run"]["objectives"]
    data[0]["milestones"][0].update(status="accepted", evidence="Runtime check passed")
    manager.objectives(root, sid, {"objectives": data})
    (root / "result.txt").write_text("changed")
    with pytest.raises(SwarmError, match="application-recorded"):
        manager.objectives(root, sid, {"objectives": data})
    with pytest.raises(SwarmError, match="Only the user"):
        data[0]["milestones"][0]["waiverReason"] = "Please trust me"
        manager.tool(
            "lead",
            {
                "action": "objectives",
                "revision": manager.get(root, sid)["run"]["revision"],
                "objectives": data,
            },
        )


async def test_worktrees_keep_both_conflicting_edits_and_dirty_user_index(manager, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    (root / "code.txt").write_text("original\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Initial")
    (root / "user.txt").write_text("staged\n")
    git(root, "add", "user.txt")
    (root / "user.txt").write_text("unstaged\n")
    (root / "untracked.txt").write_text("keep\n")
    before = (
        git(root, "status", "--porcelain"),
        git(root, "diff", "--cached"),
        git(root, "rev-parse", "HEAD"),
    )
    sid = team(manager, root)
    check = [sys.executable, "-c", "assert open('code.txt').read().strip() in ('one', 'two')"]
    plan(manager, root, sid, [milestone(checks=[check]), milestone("b", "two", checks=[check])])
    pending = manager.get(root, sid)["run"]["objectives"]
    pending[0]["milestones"][0].update(status="accepted", evidence="No work yet")
    with pytest.raises(SwarmError, match="current application-recorded"):
        manager.objectives(root, sid, {"objectives": pending})

    async def edit(**kwargs):
        cwd = kwargs["working_dir"]
        name = "one" if "You are One," in kwargs["agent_instructions"] else "two"
        assert cwd != root and (cwd / "user.txt").read_text() == "unstaged\n"
        (cwd / "code.txt").write_text(name + "\n")
        git(cwd, "add", "code.txt")
        git(cwd, "commit", "-m", name)
        yield {"type": "final", "message": {"body": "Committed"}}

    manager._stream = edit
    await asyncio.gather(
        manager._turn(str(root), sid, "one", threading.Event()),
        manager._turn(str(root), sid, "two", threading.Event()),
    )
    for mid, owner in (("a", "one"), ("b", "two")):
        item, attempt = assignment(manager, root, sid, mid)
        path = Path(attempt["workspace"]["path"])
        result = manager.tool(
            owner,
            {
                "action": "verify",
                "milestoneId": mid,
                "attemptId": attempt["id"],
                "result": {"revision": git(path, "rev-parse", "HEAD")},
            },
        )
        assert result["result"]["passed"]
        integrated = manager.tool(
            "lead", {"action": "integrate", "milestoneId": mid, "attemptId": attempt["id"]}
        )["result"]
        assert integrated["passed"] is (mid == "a")
        if mid == "a":
            assert integrated["cleanedUp"]
            assert not Path(integrated["path"]).exists()
            assert integrated["path"] not in git(root, "worktree", "list", "--porcelain")
        if mid == "b":
            assert integrated["conflicts"] == ["code.txt"]
            assert Path(integrated["path"]).is_dir()
        assert (path / "code.txt").read_text() == owner + "\n"
    assert before == (
        git(root, "status", "--porcelain"),
        git(root, "diff", "--cached"),
        git(root, "rev-parse", "HEAD"),
    )
    data = manager.get(root, sid)["run"]["objectives"]
    data[0]["milestones"][0].update(status="accepted", evidence="Combined checks passed")
    entered = threading.Event()
    release = threading.Event()
    from gofer.ui.swarm_workspaces import cleanup_accepted_attempt

    def blocked_cleanup(workspace, attempt):
        entered.set()
        assert release.wait(5)
        return cleanup_accepted_attempt(workspace, attempt)

    with (
        patch("gofer.ui.swarms.workspaces.cleanup_accepted_attempt", side_effect=blocked_cleanup),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        update = executor.submit(manager.objectives, root, sid, {"objectives": data})
        try:
            assert entered.wait(5)
            acquired = manager._lock.acquire(timeout=1)
            assert acquired, "Cleanup must leave the manager available for status and pause"
            try:
                current = manager.get(root, sid)
                assert current["run"]["objectives"][0]["milestones"][0]["status"] == "accepted"
                _, cleaning = assignment(manager, root, sid)
                with pytest.raises(SwarmError, match="cleanup is in progress"):
                    manager.tool(
                        "one", {"action": "verify", "milestoneId": "a", "attemptId": cleaning["id"]}
                    )
            finally:
                manager._lock.release()
        finally:
            release.set()
        update.result(timeout=5)
    assert not manager._cleaning_attempts
    # Removing an accepted checkout must not prevent unrelated tracker edits.
    accepted, accepted_attempt = assignment(manager, root, sid)
    assert not Path(accepted_attempt["workspace"]["path"]).exists()
    data[0]["milestones"][1]["evidence"] = "Conflict requires repair"
    manager.objectives(root, sid, {"objectives": data})
    run = manager.get(root, sid)["run"]
    manager._require_verified(run, accepted)
    run["objectives"][0]["milestones"][0]["status"] = "in_review"
    with pytest.raises(SwarmError, match="current application-recorded"):
        manager._require_verified(run, accepted)
    run["objectives"][0]["milestones"][0]["status"] = "accepted"
    # Historical acceptance cannot be reused after the verified scope changes.
    for field, value in (
        ("acceptanceCriteria", "Additional requirements"),
        ("checks", [[sys.executable, "-c", "assert False"]]),
        ("dependsOn", ["b"]),
    ):
        changed = manager.get(root, sid)["run"]["objectives"]
        changed[0]["milestones"][0][field] = value
        with pytest.raises(SwarmError, match="current application-recorded"):
            manager.objectives(root, sid, {"objectives": changed})
    recorded = next(a for a in run["attempts"] if a["id"] == accepted["attemptId"])
    recorded["integration"]["passed"] = False
    with pytest.raises(SwarmError, match="Integrate"):
        manager._require_verified(run, accepted)
    recorded["integration"]["passed"] = True
    git(Path(run["workspace"]["path"]), "reset", "--hard", recorded["workspace"]["baseRevision"])
    with pytest.raises(SwarmError, match="missing from the integration"):
        manager._require_verified(run, accepted)
    git(Path(run["workspace"]["path"]), "reset", "--hard", recorded["integration"]["revision"])
    data[0]["milestones"][1].update(status="accepted", evidence="Tests passed separately")
    with pytest.raises(SwarmError, match="Integrate"):
        manager.objectives(root, sid, {"objectives": data})
    with pytest.raises(SwarmError, match="All active milestones"):
        manager.tool("lead", {"action": "complete"})


async def test_repair_has_no_count_limit_and_old_results_are_stale(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root, maxRepairAttempts=1)
    plan(manager, root, sid, [milestone()])
    manager._stream = finish
    await manager._turn(str(root), sid, "one", threading.Event())
    _, old = assignment(manager, root, sid)
    manager.tool(
        "lead",
        {
            "action": "repair",
            "milestoneId": "a",
            "attemptId": old["id"],
            "reason": "Write the missing artifact",
        },
    )
    _, new = assignment(manager, root, sid)
    assert old["id"] != new["id"] and new["previousAttemptId"] == old["id"]
    with pytest.raises(SwarmError, match="stale"):
        manager.tool(
            "one",
            {
                "action": "milestone",
                "milestoneId": "a",
                "attemptId": old["id"],
                "status": "in_review",
                "evidence": "Late result",
            },
        )
    await manager._turn(str(root), sid, "one", threading.Event())
    manager.tool(
        "lead",
        {"action": "repair", "milestoneId": "a", "attemptId": new["id"], "reason": "Try again"},
    )
    assert assignment(manager, root, sid)[1]["repairCount"] == 2


async def test_context_cursors_usage_and_stall_replanning(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root, stallTurnLimit=2, contextCharLimit=8000)
    prompts = []

    async def stream(**kwargs):
        prompts.append(kwargs["messages"])
        manager.message(root, sid, {"body": "Inspect next result", "recipientId": "lead"})
        yield {
            "type": "final",
            "message": {"body": "No change"},
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }

    manager._stream = stream
    for i in range(3):
        manager.message(root, sid, {"body": f"Review {i}", "recipientId": "lead"})
        await manager._turn(str(root), sid, "lead", threading.Event())
    run = manager.get(root, sid)["run"]
    assert run["state"] == "running" and not run.get("replanRequired")
    assert run["usage"]["input_tokens"] == 30
    assert all(len(p) == 1 and len(p[0]["body"]) <= 8000 for p in prompts)
    assert "Review 0" not in prompts[1][0]["body"]
    manager.control(root, sid, "pause")
    manager.execution(
        root,
        sid,
        {"action": "replan", "reason": "Inspect one failing check before assigning more work"},
    )
    assert manager.control(root, sid, "resume")["run"]["state"] == "running"
    manager._stream = finish
    manager.message(root, sid, {"body": "Review"})
    await manager._turn(str(root), sid, "lead", threading.Event())
    usage = manager.get(root, sid)["run"]["usage"]
    assert usage["input_tokens"] is None and usage["reported_input_tokens"] == 30


def test_activity_updates_do_not_load_or_rewrite_history(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    state = manager.get(root, sid)
    for i in range(500):
        manager._post(state, "lead", {"body": f"Historical message {i}"})
    manager._save(state)
    statements: list[str] = []
    manager._db.set_trace_callback(statements.append)
    with patch.object(manager._store, "load", side_effect=AssertionError("Full history load")):
        for i in range(105):
            manager._record_activity(
                sid, "lead", {"type": "thought", "text": f"Step {i}"}, "secret"
            )
    manager._db.set_trace_callback(None)
    assert not any("Historical message" in sql for sql in statements)
    run = manager.get(root, sid)["run"]
    assert len(run["messages"]) == 501
    assert len(run["agentStates"]["lead"]["traces"]) == 100
    assert run["agentStates"]["lead"]["traces"][0]["text"] == "Step 5"
    assert manager.list(root)[0]["updatedAt"] == manager.get(root, sid)["updatedAt"]


async def test_completion_blocks_queued_requests(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    plan(
        manager,
        root,
        sid,
        [milestone(status="accepted", evidence="Reviewed", waiverReason="User reviewed artifact")],
    )
    with pytest.raises(SwarmError, match="outstanding actionable"):
        manager.tool("lead", {"action": "complete"})

    async def complete_in_turn(**kwargs):
        token = next(
            token
            for token, identity in manager._tokens.items()
            if token != "lead" and identity[2] == "lead"
        )
        manager._active[f"{sid}:lead"] = {"swarmId": sid, "agentId": "lead"}
        assert manager.tool(token, {"action": "complete"})["state"] == "completed"
        yield {"type": "final", "message": {"body": "Done"}}

    manager._stream = complete_in_turn
    await manager._turn(str(root), sid, "lead", threading.Event())
    assert manager.get(root, sid)["run"]["state"] == "completed"


async def test_non_git_writers_are_serial_without_legacy_elapsed_limit(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root, maxRunSeconds=1)
    manager.message(root, sid, {"body": "Write", "recipientId": "one"})
    entered = []

    async def stream(**kwargs):
        entered.append(kwargs["working_dir"])
        while not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "final", "message": {"body": "Cancelled"}}

    manager._stream = stream
    loop = asyncio.create_task(manager._loop())
    try:
        await asyncio.sleep(1.2)
        assert len(entered) == 1
        assert manager.get(root, sid)["run"]["state"] == "running"
        manager.control(root, sid, "stop")
    finally:
        manager._closed.set()
        manager._wake.set()
        await loop


async def test_new_commit_cannot_reuse_old_integration_or_criteria(manager, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    git(root, "commit", "--allow-empty", "-m", "Initial")
    sid = team(manager, root)
    plan(
        manager,
        root,
        sid,
        [milestone(checks=[[sys.executable, "-c", "assert open('result.txt').read()"]])],
    )
    manager._stream = finish
    await manager._turn(str(root), sid, "one", threading.Event())
    _, attempt = assignment(manager, root, sid)
    path = Path(attempt["workspace"]["path"])
    for text in ("first", "second"):
        (path / "result.txt").write_text(text)
        git(path, "add", ".")
        git(path, "commit", "-m", text)
        assert manager.tool(
            "one",
            {
                "action": "verify",
                "milestoneId": "a",
                "attemptId": attempt["id"],
                "result": {"revision": git(path, "rev-parse", "HEAD")},
            },
        )["result"]["passed"]
        if text == "first":
            assert manager.tool(
                "lead", {"action": "integrate", "milestoneId": "a", "attemptId": attempt["id"]}
            )["result"]["passed"]
    data = manager.get(root, sid)["run"]["objectives"]
    data[0]["milestones"][0].update(status="accepted", evidence="Claimed done")
    with pytest.raises(SwarmError, match="Integrate"):
        manager.objectives(root, sid, {"objectives": data})
    data[0]["acceptanceCriteria"] = "New requirements"
    with pytest.raises(SwarmError, match="application-recorded"):
        manager.objectives(root, sid, {"objectives": data})


async def test_stop_cancels_checks_and_preserves_uncertain_attempt(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    plan(
        manager,
        root,
        sid,
        [milestone(checks=[[sys.executable, "-c", "import time; time.sleep(30)"]])],
    )
    manager._stream = finish
    await manager._turn(str(root), sid, "one", threading.Event())
    (root / "result.txt").write_text("inspect me")
    _, attempt = assignment(manager, root, sid)
    task = asyncio.create_task(
        asyncio.to_thread(
            manager.tool,
            "one",
            {
                "action": "verify",
                "milestoneId": "a",
                "attemptId": attempt["id"],
                "result": {"artifacts": ["result.txt"]},
            },
        )
    )
    async with asyncio.timeout(3):
        while not manager._checks:
            await asyncio.sleep(0.01)
        assert manager.control(root, sid, "stop")["run"]["state"] == "stopping"
        with pytest.raises(SwarmError, match="active agents"):
            manager.start(root, sid, "Must not replace checking run")
        result = await task
    assert not result["result"]["passed"]
    assert assignment(manager, root, sid)[1]["state"] == "uncertain"
    assert manager.get(root, sid)["run"]["state"] == "stopped"


async def test_identical_failures_do_not_impose_a_check_limit(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root, maxRepairAttempts=1)
    plan(manager, root, sid, [milestone(checks=[[sys.executable, "-c", "raise SystemExit(1)"]])])
    manager._stream = finish
    await manager._turn(str(root), sid, "one", threading.Event())
    (root / "result.txt").write_text("unchanged")
    _, attempt = assignment(manager, root, sid)
    args = {
        "action": "verify",
        "milestoneId": "a",
        "attemptId": attempt["id"],
        "result": {"artifacts": ["result.txt"]},
    }
    for _ in range(2):
        assert not manager.tool("one", args)["result"]["passed"]
    assert not manager.tool("one", args)["result"]["passed"]
    assert assignment(manager, root, sid)[1]["repeatFailures"] == 3


def test_member_collection_reads_fetch_only_the_requested_page(manager, tmp_path):
    from gofer.ui.rem_swarms import RemSwarmAccess

    root = tmp_path / "project"
    sid = team(manager, root)
    state = manager.get(root, sid)
    for i in range(250):
        manager._post(state, "lead", {"body": f"Board update {i}"})
    manager._save(state)
    with patch.object(manager._store, "load", side_effect=AssertionError("Full history load")):
        page = manager.tool(
            "lead", {"action": "read", "section": "board", "offset": 200, "limit": 3}
        )
        assert len(page["items"]) == 3 and page["total"] == 251 and page["nextOffset"] == 203
        assert manager.tool("lead", {"action": "read"})["name"] == "Fixture"
        access = RemSwarmAccess(manager, str(root))
        assert (
            access.call(
                "swarm_action",
                {
                    "action": "read",
                    "swarmId": sid,
                    "params": {"section": "board", "offset": 200, "limit": 3},
                },
            )
            == page
        )


def test_final_verification_must_include_every_accepted_milestone_check(manager, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    git(root, "commit", "--allow-empty", "-m", "Initial")
    sid = team(manager, root)
    checks = [[sys.executable, "-c", "assert True"], [sys.executable, "-c", "assert 1 == 1"]]
    state = plan(
        manager,
        root,
        sid,
        [
            milestone(
                "a",
                checks=[checks[0]],
                status="accepted",
                evidence="Reviewed",
                waiverReason="User reviewed",
            ),
            milestone(
                "b",
                "two",
                checks=[checks[1]],
                status="accepted",
                evidence="Reviewed",
                waiverReason="User reviewed",
            ),
        ],
    )
    state["run"]["messages"][0]["deliveries"][0]["state"] = "completed"
    state["run"]["integration"] = {
        "passed": True,
        "revision": state["run"]["workspace"]["revision"],
        "checks": [{"command": checks[0], "exitCode": 0}],
    }
    manager._save(state)
    with pytest.raises(SwarmError, match="combined integration"):
        manager.tool("lead", {"action": "complete"})
    assert manager.tool("lead", {"action": "integrate"})["result"]["passed"]
    assert manager.tool("lead", {"action": "complete"})["state"] == "completing"
    asyncio.run(manager._finish_run(str(root), sid))
    assert manager.get(root, sid)["run"]["state"] == "completed"


def test_new_git_project_can_start_without_creating_a_user_commit(manager, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    (root / "draft.txt").write_text("New project draft")
    before = git(root, "status", "--porcelain")
    sid = team(manager, root)
    workspace = manager.get(root, sid)["run"]["workspace"]
    assert workspace["sourceRevision"] is None
    assert (Path(workspace["path"]) / "draft.txt").read_text() == "New project draft"
    assert git(root, "status", "--porcelain") == before
    with pytest.raises(ValueError):
        git(root, "rev-parse", "--verify", "HEAD")


async def test_repeating_successful_verification_does_not_reset_stall_counter(manager, tmp_path):
    root = tmp_path / "project"
    sid = team(manager, root)
    plan(manager, root, sid, [milestone()])
    manager._stream = finish
    await manager._turn(str(root), sid, "one", threading.Event())
    (root / "result.txt").write_text("Evidence")
    _, attempt = assignment(manager, root, sid)
    args = {
        "action": "verify",
        "milestoneId": "a",
        "attemptId": attempt["id"],
        "result": {"artifacts": ["result.txt"]},
    }
    assert manager.tool("one", args)["result"]["passed"]
    state = manager.get(root, sid)
    state["run"]["stalledTurns"] = 2
    manager._save(state)
    assert manager.tool("one", args)["result"]["passed"]
    assert manager.get(root, sid)["run"]["stalledTurns"] == 2
