"""Defects found in the 6 November 2026 Organizations audit, each pinned by a test."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import organization_repository as repository
from gofer.ui.organizations import OrganizationManager
from tests.unit.test_organization_hours_heartbeats import (
    act,
    as_worker,
    company,
    read,
    settle,
    start,
    ticket,
)


def patch(manager: OrganizationManager, org: dict[str, Any], tid: str, **fields: Any) -> None:
    def change(state: dict[str, Any]) -> dict[str, Any]:
        for task in state["runtime"]["tasks"]:
            if task["id"] == tid:
                task.update(fields)
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", change)


def in_run(
    manager: OrganizationManager, org: dict[str, Any], run: str, action: str, **params: Any
) -> Any:
    """Call as Mira from inside the run that holds claim `run`."""
    state = manager.store.get("", org["id"])
    return manager.call(
        org["projectRoot"],
        "employee:mira",
        {"organizationId": org["id"], "action": action, "params": params},
        employee_id="mira",
        bound_org=org["id"],
        generation=state["runtime"]["generation"],
        run_id=run,
    )


def slow(seconds: float = 30) -> Any:
    async def run(**options: Any) -> Any:
        await asyncio.sleep(seconds)
        yield {"type": "final", "message": {"body": "Late."}}

    return run


def to_review(manager: OrganizationManager, org: dict[str, Any], tid: str) -> Any:
    patch(manager, org, tid, status="in_review", evidence="Did it.")
    return ticket(manager, org, tid)


# ------------------------------------------------------------------- who may do what


def test_raticodes_own_task_records_cant_be_set_by_workers_rem_or_routines(
    tmp_path: Path,
) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    act(manager, org, "control", state="running")
    forged = {"checkout": str(tmp_path), "root": "/", "repository": "r", "branch": "b"}

    # Before the fix a forged worktree became the run's writable folder, and cancelling the
    # ticket removed it.
    def as_mira(**params: Any) -> Any:
        return as_worker(manager, org, "task_create", **params)

    def as_rem(**params: Any) -> Any:
        return act(manager, org, "task_create", "Rem:thread", **params)

    for caller in (as_mira, as_rem):
        for field, value in (
            ("worktree", forged),
            ("sharedWorktree", forged),
            ("heartbeat", True),
            ("routineId", "r1"),
        ):
            with pytest.raises(ValueError, match="Raticode sets these task fields itself"):
                caller(title="x", assignee="mira", **{field: value})
    with pytest.raises(ValueError, match="Routine tasks can't set these fields: worktree"):
        act(
            manager,
            org,
            "routine_save",
            routine={
                "name": "Nightly",
                "intervalSeconds": 3600,
                "task": {"title": "Sweep", "worktree": forged},
            },
        )
    # A template saved before this check still can't pass Raticode's records on.
    saved = act(
        manager,
        org,
        "routine_save",
        routine={"name": "Nightly", "intervalSeconds": 3600, "task": {"title": "Sweep"}},
    )

    def legacy(state: dict[str, Any]) -> dict[str, Any]:
        state["runtime"]["routines"][saved["id"]]["task"]["worktree"] = forged
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", legacy)
    made = act(manager, org, "routine_trigger", routineId=saved["id"], idempotencyKey="one")
    assert made["routineId"] == saved["id"]
    assert ticket(manager, org, made["id"])["worktree"] is None
    manager.close()


def test_a_new_run_needs_a_new_review(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    made = act(
        manager,
        org,
        "task_create",
        title="Reviewed",
        assignee="mira",
        reviewRequired=True,
        reviewer="lead",
        status="backlog",
    )
    current = to_review(manager, org, made["id"])
    act(
        manager,
        org,
        "review",
        taskId=made["id"],
        expectedRevision=current["revision"],
        decision="accept",
        body="Good",
    )
    act(manager, org, "control", state="running")
    current = ticket(manager, org, made["id"])
    again = as_worker(
        manager,
        org,
        "task_update",
        taskId=made["id"],
        expectedRevision=current["revision"],
        changes={"status": "todo"},
    )
    assert again["acceptedRevision"] is None
    current = to_review(manager, org, made["id"])
    # Before the fix the first acceptance still counted, so the rerun could skip review.
    with pytest.raises(ValueError, match="independent reviewer must accept"):
        as_worker(
            manager,
            org,
            "task_update",
            taskId=made["id"],
            expectedRevision=current["revision"],
            changes={"status": "done"},
        )
    manager.close()


def test_workers_cant_rewrite_a_block_they_may_not_lift(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Held", assignee="mira")
    held = act(
        manager,
        org,
        "task_update",
        taskId=made["id"],
        expectedRevision=made["revision"],
        changes={"status": "blocked"},
    )
    act(manager, org, "control", state="running")
    with pytest.raises(ValueError, match="blocked this task for review"):
        as_worker(
            manager,
            org,
            "task_update",
            taskId=made["id"],
            expectedRevision=held["revision"],
            changes={"blockReason": "Blocked by Mira"},
        )
    # Their own blocks stay theirs to change.
    own = as_worker(manager, org, "task_create", title="Mine", assignee="mira")
    own = as_worker(
        manager,
        org,
        "task_update",
        taskId=own["id"],
        expectedRevision=own["revision"],
        changes={"status": "blocked", "blockReason": "Blocked by Mira Santos: waiting"},
    )
    assert as_worker(
        manager,
        org,
        "task_update",
        taskId=own["id"],
        expectedRevision=own["revision"],
        changes={"status": "todo"},
    )["status"] in {"todo", "in_progress"}
    manager.close()


def test_only_raticodes_own_check_ins_close_themselves(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    made = act(
        manager,
        org,
        "task_create",
        title="Real work",
        assignee="mira",
        reviewRequired=True,
        reviewer="lead",
    )
    act(manager, org, "control", state="running")
    as_worker(
        manager,
        org,
        "task_update",
        taskId=made["id"],
        expectedRevision=made["revision"],
        changes={"title": "Heartbeat: review assignments"},
    )
    manager.dispatch()
    settle(manager)
    # Before the fix the renamed ticket closed itself as Done, skipping its review.
    assert ticket(manager, org, made["id"])["status"] == "in_review"
    manager.close()


def test_rejected_work_stays_stopped_until_the_user_or_rem_reopens_it(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Draft", assignee="mira", status="backlog")
    current = to_review(manager, org, made["id"])
    rejected = act(
        manager,
        org,
        "review",
        taskId=made["id"],
        expectedRevision=current["revision"],
        decision="reject",
        body="Wrong approach",
    )
    assert rejected["blockReason"].startswith("Rejected in review by you")
    act(manager, org, "control", state="running")
    with pytest.raises(ValueError, match="Only the user or Rem can reopen"):
        as_worker(manager, org, "reopen", taskId=made["id"], expectedRevision=rejected["revision"])
    assert (
        act(manager, org, "reopen", taskId=made["id"], expectedRevision=rejected["revision"])[
            "status"
        ]
        == "todo"
    )
    manager.close()


def test_work_created_in_a_run_keeps_the_runs_rules_whatever_parent_it_names(
    tmp_path: Path,
) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    old = act(manager, org, "task_create", title="Old", assignee="mira", status="backlog")
    chain = [
        act(
            manager,
            org,
            "task_create",
            title="Level 1",
            assignee="mira",
            reviewRequired=True,
            reviewer="lead",
            status="backlog",
        )
    ]
    for level in (2, 3, 4):
        chain.append(
            act(
                manager,
                org,
                "task_create",
                title=f"Level {level}",
                assignee="mira",
                parentId=chain[-1]["id"],
                status="backlog",
            )
        )
    act(manager, org, "control", state="running")
    patch(manager, org, chain[0]["id"], claim="run-1", status="in_progress")
    made = in_run(
        manager, org, "run-1", "task_create", title="Child", assignee="mira", parentId=old["id"]
    )
    # Naming an old ticket used to shed the run's review requirement.
    assert made["reviewRequired"] is True and made["parentId"] == old["id"]
    patch(manager, org, chain[3]["id"], claim="run-4", status="in_progress")
    with pytest.raises(ValueError, match="nested too deeply"):
        in_run(
            manager,
            org,
            "run-4",
            "task_create",
            title="Deeper",
            assignee="mira",
            parentId=old["id"],
        )
    manager.close()


def test_a_run_cant_make_extra_tickets_through_expired_memories(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    (tmp_path / "notes.md").write_text("fact")
    act(manager, org, "control", state="running")
    with pytest.raises(ValueError, match="expiry in the future"):
        as_worker(
            manager,
            org,
            "memory_save",
            body="Old fact",
            path=str(tmp_path / "notes.md"),
            expiresAt=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
        )
    manager.close()


def test_rem_asks_before_changing_routines_managers_tools_or_starting_risky_setups(
    tmp_path: Path,
) -> None:
    manager = start(tmp_path)
    org = company(
        manager,
        tmp_path,
        blockGitPush=False,
        employees=[
            {"id": "lead", "name": "Avery Lane"},
            {"id": "sam", "name": "Sam Ortiz", "reportsTo": "lead"},
            {
                "id": "mira",
                "name": "Mira Santos",
                "reportsTo": "lead",
                "resources": {
                    "mcpServers": [{"name": "files", "type": "stdio", "command": "/bin/true"}]
                },
            },
        ],
    )
    routine = act(
        manager,
        org,
        "routine_save",
        routine={"name": "Nightly", "intervalSeconds": 3600, "task": {"title": "Sweep"}},
    )
    changed = act(
        manager,
        org,
        "routine_save",
        "Rem:thread",
        routine={**routine, "task": {"title": "Sweep", "assignee": "mira"}},
        expectedRevision=routine["revision"],
    )
    assert (
        changed["needsUserConsent"]
        and "Change the work the routine Nightly" in (changed["summary"])
    )
    state = read(manager, org)
    config = state["config"]

    def with_mira(**fields: Any) -> dict[str, Any]:
        return {
            **config,
            "employees": [{**e, **fields} if e["id"] == "mira" else e for e in config["employees"]],
        }

    moved = act(
        manager,
        org,
        "configure",
        "Rem:thread",
        config=with_mira(reportsTo="sam"),
        expectedRevision=state["revision"],
        reason="Reorg",
    )
    assert "change who Mira Santos reports to" in moved["summary"]
    server = {"name": "files", "type": "stdio", "command": "/usr/bin/other"}
    swapped = act(
        manager,
        org,
        "configure",
        "Rem:thread",
        config=with_mira(resources={**config["employees"][2]["resources"], "mcpServers": [server]}),
        expectedRevision=state["revision"],
        reason="New server",
    )
    # Before the fix only the server's name was compared, so this went through silently.
    assert "give Mira Santos more tools" in swapped["summary"]
    started = act(manager, org, "control", "Rem:thread", state="running")
    assert started["summary"].startswith("Start the organization (git push allowed)")
    manager.close()


# ------------------------------------------------------------------- runs and timing


def test_a_run_that_hits_its_time_limit_says_so(tmp_path: Path, monkeypatch: Any) -> None:
    real = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda _seconds: real(0.3))
    manager = start(tmp_path, slow())
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Slow", assignee="mira")
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)
    stopped = ticket(manager, org, made["id"])
    attempt = next(iter(read(manager, org)["runtime"]["attempts"].values()))
    # Before the fix the error was lost and the ticket said the run had no final response.
    assert stopped["blockReason"] == "The last run failed; see the conversation"
    assert "longer than its 1800-second limit" in attempt["error"]
    manager.close()


def test_a_stop_after_the_run_finished_doesnt_undo_it(tmp_path: Path) -> None:
    holder: dict[str, Any] = {}

    async def finish_then_close(**options: Any) -> Any:
        yield {"type": "final", "message": {"body": "All done."}}
        holder["manager"].cancel(holder["org"], "hours")

    manager = start(tmp_path, finish_then_close)
    org = company(manager, tmp_path)
    holder.update(manager=manager, org=org["id"])
    made = act(manager, org, "task_create", title="Quick", assignee="mira")
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)
    # Before the fix the finished run went back to To do and would have run again.
    assert ticket(manager, org, made["id"])["status"] == "in_review"
    manager.close()


def test_a_result_the_worker_already_set_survives_a_failing_run(tmp_path: Path) -> None:
    holder: dict[str, Any] = {}

    async def done_then_crash(**options: Any) -> Any:
        patch(holder["manager"], holder["org"], holder["task"], status="done", evidence="Shipped.")
        raise RuntimeError("provider went away")
        yield {}

    manager = start(tmp_path, done_then_crash)
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Ship", assignee="mira")
    holder.update(manager=manager, org=org, task=made["id"])
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)
    kept = ticket(manager, org, made["id"])
    assert kept["status"] == "done"
    assert kept["comments"][-1]["body"].startswith("The run stopped after the worker set")
    manager.close()


def test_routines_make_their_tickets_on_time_while_workers_are_busy(tmp_path: Path) -> None:
    manager = start(tmp_path, slow())
    org = company(manager, tmp_path)
    act(manager, org, "task_create", title="Busy", assignee="mira")
    saved = act(
        manager,
        org,
        "routine_save",
        routine={
            "name": "Hourly",
            "intervalSeconds": 3600,
            "missed": "skip",
            "task": {"title": "Sweep", "assignee": "lead"},
        },
    )
    act(manager, org, "control", state="running")
    manager.dispatch()
    assert manager._active

    def due(state: dict[str, Any]) -> dict[str, Any]:
        routine = state["runtime"]["routines"][saved["id"]]
        routine["nextAt"] = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", due)
    manager.dispatch()  # The only slot is busy.
    # Before the fix the occurrence waited for a free slot, was then late, and was skipped.
    assert any(t["routineId"] == saved["id"] for t in read(manager, org)["runtime"]["tasks"])
    act(manager, org, "control", state="paused")
    settle(manager)
    manager.close()


def test_the_monthly_run_limit_lets_running_work_finish_then_pauses(tmp_path: Path) -> None:
    gate: dict[str, bool] = {}

    async def held(**options: Any) -> Any:
        for _ in range(250):
            if gate.get("go"):
                break
            await asyncio.sleep(0.02)
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start(tmp_path, held)
    org = company(manager, tmp_path, monthlyTurnLimit=1, maxConcurrency=2)
    first = act(manager, org, "task_create", title="First", assignee="mira")
    act(manager, org, "task_create", title="Second", assignee="lead")
    act(manager, org, "control", state="running")
    manager.dispatch()
    manager.dispatch()
    # Before the fix the organization paused here, revoking the running work's tools.
    assert read(manager, org)["runtime"]["state"] == "running"
    assert len(manager._active) == 1
    gate["go"] = True
    settle(manager)
    assert ticket(manager, org, first["id"])["status"] == "in_review"
    manager.dispatch()
    state = read(manager, org)["runtime"]
    assert state["state"] == "paused" and state["pauseReason"].startswith("Monthly limit")
    manager.close()


def test_a_removed_workers_tickets_can_still_be_reassigned_or_cancelled(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    one = act(manager, org, "task_create", title="One", assignee="mira", status="backlog")
    two = act(manager, org, "task_create", title="Two", assignee="mira", status="backlog")
    state = read(manager, org)
    config = state["config"]
    config["employees"] = [e for e in config["employees"] if e["id"] != "mira"]
    act(manager, org, "configure", config=config, expectedRevision=state["revision"], reason="x")
    one = ticket(manager, org, one["id"])
    # Before the fix every change failed with "Unknown employee".
    cancelled = act(
        manager,
        org,
        "task_update",
        taskId=one["id"],
        expectedRevision=one["revision"],
        changes={"status": "cancelled"},
    )
    assert cancelled["status"] == "cancelled"
    two = ticket(manager, org, two["id"])
    moved = act(
        manager,
        org,
        "task_update",
        taskId=two["id"],
        expectedRevision=two["revision"],
        changes={"assignee": "lead"},
    )
    assert moved["assignee"] == "lead"
    with pytest.raises(ValueError, match="Unknown employee"):
        act(
            manager,
            org,
            "task_update",
            taskId=two["id"],
            expectedRevision=moved["revision"],
            changes={"assignee": "ghost"},
        )
    manager.close()


# ------------------------------------------------------------------- board actions


def test_board_moves_reopen_into_backlog_and_undo_a_pull(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Shipped", assignee="mira", status="backlog")
    current = to_review(manager, org, made["id"])
    done = act(
        manager,
        org,
        "review",
        taskId=made["id"],
        expectedRevision=current["revision"],
        decision="accept",
        body="Good",
    )
    parked = act(
        manager,
        org,
        "reopen",
        taskId=made["id"],
        expectedRevision=done["revision"],
        status="backlog",
    )
    # Before, reopening always went to To do, where a free worker could start it at once.
    assert parked["status"] == "backlog"
    pulled = act(
        manager,
        org,
        "task_update",
        taskId=made["id"],
        expectedRevision=parked["revision"],
        changes={"status": "in_progress"},
    )
    assert pulled["pulledAt"]
    undone = act(
        manager,
        org,
        "task_update",
        taskId=made["id"],
        expectedRevision=pulled["revision"],
        changes={"status": "todo"},
    )
    # Undo used to leave the ticket at the front of the queue.
    assert undone["pulledAt"] is None
    manager.close()


def test_linking_a_note_keeps_your_plan_approval(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    note = act(
        manager,
        org,
        "knowledge_save",
        entry={"kind": "note", "title": "Style", "summary": "Use short names.", "body": ""},
    )
    made = act(
        manager,
        org,
        "task_create",
        title="Plan",
        assignee="mira",
        approvalRequired=True,
        status="backlog",
    )
    approved = act(manager, org, "approve", taskId=made["id"], expectedRevision=made["revision"])
    linked = act(
        manager,
        org,
        "task_update",
        taskId=made["id"],
        expectedRevision=approved["revision"],
        changes={"knowledge": [note["id"]]},
    )
    assert linked["approvedRevision"] is not None
    manager.close()


# ------------------------------------------------------------------- shared repository


def test_saving_through_the_plan_tools_stops_work_the_change_revokes(tmp_path: Path) -> None:
    manager = start(tmp_path, slow())
    org = company(manager, tmp_path)
    act(manager, org, "task_create", title="Busy", assignee="mira", project="desktop")
    act(manager, org, "control", state="running")
    manager.dispatch()
    assert manager._active
    revision = read(manager, org)["revision"]
    act(
        manager,
        org,
        "plan_save",
        kind="project",
        item={"id": "desktop", "workspacePath": str(tmp_path)},
        expectedRevision=revision,
    )
    # Before the fix the run carried on in the old folder until its 30-minute limit.
    settle(manager, 5)
    manager.close()


def test_git_failures_become_repository_errors(monkeypatch: Any, tmp_path: Path) -> None:
    def hang(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="git", timeout=60)

    monkeypatch.setattr(repository.subprocess, "run", hang)
    with pytest.raises(repository.RepositoryError, match="took over 60 seconds"):
        repository.git(tmp_path, "status")

    def missing(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("git")

    monkeypatch.setattr(repository.subprocess, "run", missing)
    with pytest.raises(repository.RepositoryError, match="Couldn't run git"):
        repository.git(tmp_path, "status")


def test_shared_changes_are_never_merged_into_a_branch_someone_checked_out(
    tmp_path: Path,
) -> None:
    org = {"id": "0f0e0d0c-0000-4000-8000-000000000001", "config": {"name": "Studio"}}
    path = repository.ensure(tmp_path, org)
    task = {"id": "t1234567", "title": "Notes"}
    worktree = repository.plan_worktree(tmp_path, org, task)
    repository.ensure_worktree(worktree, org["id"])
    (Path(worktree["checkout"]) / "notes" / "a.md").write_text("A")
    repository.git(path, "checkout", "-q", "-b", "review")
    note = repository.finish(worktree, org["id"], "Notes", merge=True)
    assert "isn't on main" in note
    assert repository.git(path, "rev-list", "--count", "review..main").stdout.strip() == "0"
    assert (
        repository.git(
            path, "rev-parse", "--verify", "-q", f"refs/heads/{worktree['branch']}", check=False
        ).returncode
        == 0
    )


def test_start_request_names_checks_folders_and_mcp_servers():
    # B-O4 (12 Nov security review): the start card names what else the start authorizes.
    from gofer.ui.organizations import starting_details

    config = {
        "projectRoots": ["/work/desk"],
        "projects": [{"id": "p", "checks": [{"name": "tests", "command": "pytest -q"}]}],
        "employees": [
            {
                "id": "e",
                "resources": {
                    "mcpServers": [
                        {"name": "files", "command": "/usr/bin/files"},
                        {"name": "off", "command": "x", "enabled": False},
                    ]
                },
            }
        ],
    }
    details = starting_details(config)
    assert "tests: pytest -q" in details
    assert "/work/desk" in details
    assert "MCP servers: files" in details and "off" not in details
    assert len(starting_details(config, limit=40)) == 40
    assert starting_details({}) == ""
