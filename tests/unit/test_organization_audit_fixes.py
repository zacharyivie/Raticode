"""Regressions for the 30 October Organizations audit: limits, consent, isolation, recovery."""

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organization_operations import Routine, schedule_routines
from gofer.ui.organization_store import Employee, OrganizationConflict
from gofer.ui.organizations import OrganizationManager
from gofer.utils.process import git_push_blocked_environment


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def repository(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("project\n")
    git(path, "add", "README.md")
    git(path, "commit", "-q", "-m", "initial")
    return path


async def final(**options: Any) -> Any:
    yield {"type": "final", "message": {"body": "Done."}}


def start_manager(tmp_path: Path, stream: Any = final) -> OrganizationManager:
    return OrganizationManager(tmp_path / "data", stream=stream, start_runtime=False)


def company(manager: OrganizationManager, root: Path, **config: Any) -> Any:
    employees = config.pop(
        "employees", [{"id": "lead", "name": "Avery"}, {"id": "builder", "name": "Mira"}]
    )
    return manager.call(
        str(root),
        "user",
        {
            "action": "create",
            "params": {"config": {"name": "Studio", "employees": employees, **config}},
        },
    )


def act(
    manager: OrganizationManager,
    org: dict[str, Any],
    action: str,
    actor: str = "user",
    **params: Any,
) -> Any:
    return manager.call(
        org["projectRoot"], actor, {"organizationId": org["id"], "action": action, "params": params}
    )


def settle(manager: OrganizationManager, timeout: float = 5) -> None:
    until = time.monotonic() + timeout
    while manager._active and time.monotonic() < until:
        time.sleep(0.02)
    assert not manager._active


def task(manager: OrganizationManager, org: dict[str, Any], tid: str) -> dict[str, Any]:
    current = manager.store.get("", org["id"])
    return next(t for t in current["runtime"]["tasks"] if t["id"] == tid)


class Gate:
    """A provider that waits until released or cancelled, recording what it was given."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **options: Any) -> Any:
        self.calls.append(options)
        while not self.release.is_set() and not options["cancel_event"].is_set():
            await asyncio.sleep(0.02)
        if not options["cancel_event"].is_set():
            yield {"type": "final", "message": {"body": "Done."}}


def test_pause_returns_running_work_to_todo_and_start_runs_it_again(tmp_path: Path) -> None:
    gate = Gate()
    manager = start_manager(tmp_path, gate)
    org = company(manager, tmp_path)
    created = act(manager, org, "task_create", title="Build", assignee="builder")
    act(manager, org, "control", state="running")
    manager.dispatch()
    until = time.monotonic() + 3
    while not gate.calls and time.monotonic() < until:
        time.sleep(0.02)
    act(manager, org, "control", state="paused")
    settle(manager)
    paused = task(manager, org, created["id"])
    # Blocked while the company is paused, saying why; Start puts it back in To do.
    assert paused["status"] == "blocked"
    assert paused["blockReason"] == "Waiting for the organization to start again"
    assert paused["turns"] == 0
    said = [c for c in paused["comments"] if c.get("kind") != "status"]
    assert "runs again when the organization starts" in said[-1]["body"]
    assert paused["comments"][-1]["title"] == "Blocked: waiting"
    current = manager.store.get("", org["id"])
    assert current["runtime"]["pauseReason"] == "Paused by you"
    assert [a["status"] for a in current["runtime"]["attempts"].values()] == ["paused"]
    gate.release.set()
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)
    assert task(manager, org, created["id"])["status"] == "in_review"
    manager.close()


def test_silent_provider_stops_promptly_when_paused(tmp_path: Path) -> None:
    started = threading.Event()

    async def silent(**options: Any) -> Any:
        started.set()
        await asyncio.sleep(3600)
        yield {"type": "final", "message": {"body": "never"}}

    manager = start_manager(tmp_path, silent)
    org = company(manager, tmp_path)
    act(manager, org, "task_create", title="Hang", assignee="builder")
    act(manager, org, "control", state="running")
    manager.dispatch()
    assert started.wait(3)
    begun = time.monotonic()
    act(manager, org, "control", state="paused")
    settle(manager, timeout=3)
    assert time.monotonic() - begun < 3
    manager.close()


def test_dispatch_blocks_keep_evidence_and_bump_revision(tmp_path: Path) -> None:
    manager = start_manager(tmp_path)
    org = company(manager, tmp_path, maxTaskTurns=1)
    limited = act(
        manager, org, "task_create", title="Limited", assignee="lead", evidence="User notes"
    )

    def spent(current: dict[str, Any]) -> dict[str, Any]:
        current["runtime"]["tasks"][0]["turns"] = 1
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", spent)
    act(manager, org, "control", state="running")
    before = task(manager, org, limited["id"])
    # No other task is ready; the scheduler still records why this one can't run.
    manager.dispatch()
    after = task(manager, org, limited["id"])
    assert after["status"] == "blocked"
    assert after["evidence"] == "User notes"
    assert "Reopen" in after["blockReason"]
    assert after["revision"] == before["revision"] + 1
    with pytest.raises(OrganizationConflict):
        act(
            manager,
            org,
            "task_update",
            taskId=limited["id"],
            expectedRevision=before["revision"],
            changes={"priority": "high"},
        )
    # The user's reopen grants a fresh run allowance.
    reopened = act(manager, org, "reopen", taskId=limited["id"], expectedRevision=after["revision"])
    assert reopened["status"] == "todo" and reopened["turns"] == 0
    manager.close()


def test_employee_blocks_get_a_reason(tmp_path: Path) -> None:
    manager = start_manager(tmp_path)
    org = company(manager, tmp_path)
    created = act(manager, org, "task_create", title="Plan", assignee="builder")
    act(
        manager,
        org,
        "task_update",
        taskId=created["id"],
        expectedRevision=created["revision"],
        changes={"status": "blocked"},
    )
    assert task(manager, org, created["id"])["blockReason"].startswith("Blocked by you")
    manager.close()


def test_concurrency_diagnostic_matches_what_can_run(tmp_path: Path) -> None:
    manager = start_manager(tmp_path)
    folder = tmp_path / "folder"
    folder.mkdir()
    shared = company(manager, folder, maxConcurrency=4)
    diagnostics = act(manager, shared, "diagnostics")
    assert diagnostics["effectiveConcurrency"] == 1
    assert "take turns" in diagnostics["concurrencyNote"]
    repo = repository(tmp_path / "repo")
    isolated = company(manager, repo, maxConcurrency=4)
    diagnostics = act(manager, isolated, "diagnostics")
    assert diagnostics["effectiveConcurrency"] == 2
    assert "own git worktree" in diagnostics["isolationNote"]
    manager.close()


def test_employees_work_in_their_own_worktree_and_branch(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")
    gate = Gate()
    manager = start_manager(tmp_path, gate)
    org = company(manager, repo, maxConcurrency=2)
    first = act(manager, org, "task_create", title="Write notes", assignee="lead")
    second = act(manager, org, "task_create", title="Write tests", assignee="builder")
    act(manager, org, "control", state="running")
    manager.dispatch()
    manager.dispatch()
    until = time.monotonic() + 5
    while len(gate.calls) < 2 and time.monotonic() < until:
        time.sleep(0.02)
    # Both employees run at once because neither touches the project folder.
    assert len(gate.calls) == 2
    roots = {Path(call["working_dir"]) for call in gate.calls}
    assert all(not root.is_relative_to(repo) for root in roots)
    for call in gate.calls:
        assert call["workflow"]["projectRoot"] == str(call["working_dir"])
        assert call["workflow"]["remBlockGitPush"] is True
        assert "git push is blocked" in call["agent_instructions"]
        (Path(call["working_dir"]) / "notes.txt").write_text("from an employee\n")
    gate.release.set()
    settle(manager)
    recorded = task(manager, org, first["id"])["worktree"]
    assert recorded["branch"].startswith("org/write-notes-")
    assert not (repo / "notes.txt").exists()
    assert git(repo, "status", "--porcelain") == ""
    current = act(
        manager,
        org,
        "task_update",
        taskId=first["id"],
        expectedRevision=task(manager, org, first["id"])["revision"],
        changes={"evidence": "Wrote notes.txt"},
    )
    act(
        manager,
        org,
        "review",
        taskId=first["id"],
        expectedRevision=current["revision"],
        decision="accept",
        body="Checked",
    )
    manager.release_worktrees(manager.store.get("", org["id"]))
    released = task(manager, org, first["id"])
    assert released["worktree"] is None
    assert not Path(recorded["checkout"]).exists()
    assert "notes.txt" in git(repo, "show", "--name-only", recorded["branch"])
    assert "branch" in released["comments"][-1]["body"]
    # Work still in review keeps its worktree for inspection.
    assert task(manager, org, second["id"])["worktree"]
    manager.close()


def test_checkout_mode_refuses_a_dirty_project_folder(tmp_path: Path) -> None:
    repo = repository(tmp_path / "repo")
    (repo / "README.md").write_text("user edit in progress\n")
    calls: list[Any] = []

    async def stream(**options: Any) -> Any:
        calls.append(options)
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start_manager(tmp_path, stream)
    org = company(manager, repo, workspaceIsolation="checkout")
    created = act(manager, org, "task_create", title="Edit", assignee="lead")
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)
    assert not calls
    blocked = task(manager, org, created["id"])
    assert blocked["status"] == "blocked"
    said = [c for c in blocked["comments"] if c.get("kind") != "status"]
    assert "uncommitted changes" in said[-1]["body"]
    manager.close()


def test_push_guard_blocks_push_but_not_commits(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    repo = repository(tmp_path / "repo")
    git(repo, "remote", "add", "origin", str(remote))
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), **git_push_blocked_environment({})}
    blocked = subprocess.run(
        ["git", "-C", str(repo), "push", "origin", "main"], env=env, capture_output=True, text=True
    )
    assert blocked.returncode != 0
    assert "raticode-push-blocked" in blocked.stderr
    assert subprocess.run(["git", "-C", str(repo), "fetch", "origin"], env=env).returncode == 0
    assert git_push_blocked_environment({"GIT_CONFIG_COUNT": "2"})["GIT_CONFIG_COUNT"] == "3"


def test_token_limits_pause_when_spent_and_name_the_scope(tmp_path: Path) -> None:
    manager = start_manager(tmp_path)
    org = company(manager, tmp_path, monthlyTokenLimit=1000)
    created = act(manager, org, "task_create", title="Work", assignee="lead")
    month = datetime.now(UTC).strftime("%Y-%m")

    def spend(current: dict[str, Any]) -> dict[str, Any]:
        current["runtime"].setdefault("usage", {})[month] = {"company": {"tokens": 1500}}
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", spend)
    act(manager, org, "control", state="running")
    reason = act(manager, org, "read")["diagnostics"]["taskReasons"][created["id"]]
    assert reason == "Monthly token limit: company"
    manager.dispatch()
    current = manager.store.get("", org["id"])
    assert current["runtime"]["state"] == "paused"
    assert "run limit" in current["runtime"]["pauseReason"]
    manager.close()


def test_new_organizations_get_bounded_defaults(tmp_path: Path) -> None:
    manager = start_manager(tmp_path)
    config = company(manager, tmp_path)["config"]
    assert config["monthlyTurnLimit"] == 200
    assert config["maxChildTasksPerTurn"] == 5
    assert config["maxDelegationDepth"] == 3
    assert config["workspaceIsolation"] == "worktree"
    assert config["blockGitPush"] is True
    assert (
        Employee.model_validate({"id": "c", "name": "C", "provider": "claude_code"}).permissionMode
        == "dontAsk"
    )
    manager.close()


def test_queued_routines_stop_stacking_up(tmp_path: Path) -> None:
    manager = start_manager(tmp_path)
    org = manager.store.get("", company(manager, tmp_path)["id"])
    routine = Routine(
        name="Poll", task={"title": "Poll", "assignee": "lead"}, intervalSeconds=60, overlap="queue"
    ).model_dump()
    begin = datetime.now(UTC)
    routine["nextAt"] = begin.isoformat()
    org["runtime"]["routines"] = {routine["id"]: routine}
    for minute in range(50):
        schedule_routines(org, begin + timedelta(seconds=61 * minute))
    assert sum(t["status"] not in {"done", "cancelled"} for t in org["runtime"]["tasks"]) == 3
    manager.close()


def test_idle_restarts_record_nothing(tmp_path: Path) -> None:
    manager = start_manager(tmp_path)
    org = company(manager, tmp_path)
    manager.close()
    for _ in range(3):
        start_manager(tmp_path).close()
    manager = start_manager(tmp_path)
    kinds = [event["kind"] for event in manager.store.events("", org["id"])]
    assert "runtime_recovered" not in kinds
    manager.close()


def test_employee_runs_have_a_delegation_budget(tmp_path: Path) -> None:
    manager = start_manager(tmp_path)
    org = company(manager, tmp_path, maxChildTasksPerTurn=2, maxDelegationDepth=1)
    parent = act(manager, org, "task_create", title="Parent", assignee="lead")
    act(manager, org, "control", state="running")

    def claim(current: dict[str, Any]) -> dict[str, Any]:
        current["runtime"]["tasks"][0].update(claim="run-1", status="in_progress")
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", claim)
    generation = manager.store.get("", org["id"])["runtime"]["generation"]

    def employee(**params: Any) -> Any:
        return manager.call(
            org["projectRoot"],
            "employee:lead",
            {"organizationId": org["id"], "action": "task_create", "params": params},
            employee_id="lead",
            bound_org=org["id"],
            generation=generation,
            run_id="run-1",
        )

    child = employee(title="Child", assignee="builder")
    assert child["parentId"] == parent["id"]
    with pytest.raises(ValueError, match="nested too deeply"):
        employee(title="Grandchild", assignee="builder", parentId=child["id"])
    employee(title="Second child", assignee="builder")
    with pytest.raises(ValueError, match="limit per run"):
        employee(title="Third child", assignee="builder")
    manager.close()


def test_rem_needs_the_users_consent_to_start_or_escalate(tmp_path: Path) -> None:
    manager = start_manager(tmp_path)
    org = company(manager, tmp_path)
    request = act(manager, org, "control", actor="Rem:thread", state="running")
    assert request["needsUserConsent"] is True
    assert manager.store.get("", org["id"])["runtime"]["state"] == "paused"
    with pytest.raises(ValueError, match="Only the user"):
        act(
            manager,
            org,
            "request_decide",
            actor="Rem:thread",
            requestId=request["requestId"],
            decision="approve",
        )
    started = act(
        manager, org, "request_decide", requestId=request["requestId"], decision="approve"
    )
    assert started["runtime"]["state"] == "running"
    assert started["runtime"]["consentRequests"][request["requestId"]]["status"] == "approved"
    # Pausing is always allowed.
    paused = act(manager, org, "control", actor="Rem:thread", state="paused")
    assert paused["runtime"]["pauseReason"] == "Paused by Rem"

    current = manager.store.get("", org["id"])
    dangerous = {
        **current["config"],
        "employees": [
            {**e, "permissionMode": "danger-full-access"} if e["id"] == "builder" else e
            for e in current["config"]["employees"]
        ],
    }
    request = act(
        manager,
        org,
        "configure",
        actor="Rem:thread",
        config=dangerous,
        expectedRevision=current["revision"],
        reason="Faster builds",
    )
    assert "danger-full-access" in request["summary"]
    assert manager.store.get("", org["id"])["revision"] == current["revision"]
    act(manager, org, "request_decide", requestId=request["requestId"], decision="decline")
    current = manager.store.get("", org["id"])
    assert current["config"]["employees"][1]["permissionMode"] == "workspace-write"
    assert current["runtime"]["consentRequests"][request["requestId"]]["status"] == "declined"
    # Ordinary edits still apply directly.
    renamed = act(
        manager,
        org,
        "configure",
        actor="Rem:thread",
        config={**current["config"], "name": "Renamed"},
        expectedRevision=current["revision"],
        reason="Rename",
    )
    assert renamed["config"]["name"] == "Renamed"
    with pytest.raises(ValueError, match="Organizations tab"):
        act(manager, org, "secret_store", actor="Rem:thread", id="token", value="secret-value")
    with pytest.raises(ValueError, match="stored for this organization"):
        act(manager, org, "secret_reference", actor="Rem:thread", id="token", account="other/thing")
    manager.close()


def test_archiving_releases_projects_and_keeps_history(tmp_path: Path) -> None:
    manager = start_manager(tmp_path)
    org = company(manager, tmp_path)
    act(manager, org, "control", state="running")
    with pytest.raises(ValueError, match="Pause"):
        act(manager, org, "archive", archived=True)
    act(manager, org, "control", state="paused")
    archived = act(manager, org, "archive", archived=True)
    assert archived["runtime"]["archived"] is True
    assert manager.store.summaries()[0]["archived"] is True
    with pytest.raises(ValueError, match="Restore"):
        act(manager, org, "control", state="running")
    other = company(manager, tmp_path)
    assert other["config"]["projectRoots"] == [str(tmp_path)]
    with pytest.raises(OrganizationConflict, match="already owned"):
        act(manager, org, "archive", archived=False)
    act(manager, other, "control", state="paused")
    act(manager, other, "archive", archived=True)
    restored = act(manager, org, "archive", archived=False)
    assert "archived" not in restored["runtime"]
    assert len(act(manager, org, "history")) == 1
    manager.close()


def test_run_transcripts_are_readable_and_bounded(tmp_path: Path, monkeypatch: Any) -> None:
    import gofer.ui.organizations as organizations

    monkeypatch.setattr(organizations, "MAX_TURN_EVENTS", 3)

    async def chatty(**options: Any) -> Any:
        for index in range(10):
            yield {"type": "thought", "text": f"step {index}"}
        yield {"type": "final", "message": {"body": "Finished."}}

    manager = start_manager(tmp_path, chatty)
    org = company(manager, tmp_path)
    created = act(manager, org, "task_create", title="Explain", assignee="lead")
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)
    attempt = next(iter(manager.store.get("", org["id"])["runtime"]["attempts"]))
    transcript = act(manager, org, "run_events", taskId=created["id"], turnId=attempt)
    assert [e.get("text") or e["type"] for e in transcript["events"]] == [
        "step 0",
        "step 1",
        "step 2",
        "final",
    ]
    with pytest.raises(ValueError, match="run of this task"):
        act(manager, org, "run_events", taskId="other", turnId=attempt)
    manager.close()


def test_phone_projection_names_people_and_reports_usage() -> None:
    from gofer.devices.desktop_control import _comment_author, _organization_usage

    employees = {"director": {"name": "Dana"}}
    assert _comment_author("employee:director", employees) == "Dana"
    assert _comment_author("user", employees) == "You"
    assert _comment_author("Rem:abc", employees) == "Rem"
    month = time.strftime("%Y-%m", time.gmtime())
    usage = {"usage": {month: {"company": {"turns": 2, "tokens": 50, "unknownCostTurns": 2}}}}
    assert _organization_usage(usage) == {
        "month": month,
        "runs": 2,
        "tokens": 50,
        "cost_usd": None,
    }
