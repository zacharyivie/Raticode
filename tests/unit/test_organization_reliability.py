"""Regressions for the 2 November Organizations audit: consent, audit trail, provenance."""

from __future__ import annotations

import asyncio
import copy
import sqlite3
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organization_store import OrganizationConflict
from gofer.ui.organizations import OrganizationManager


async def final(**options: Any) -> Any:
    yield {"type": "final", "message": {"body": "Done."}}


def start(tmp_path: Path, stream: Any = final, name: str = "data") -> OrganizationManager:
    return OrganizationManager(tmp_path / name, stream=stream, start_runtime=False)


def company(manager: OrganizationManager, root: Path, **config: Any) -> Any:
    employees = config.pop(
        "employees",
        [{"id": "lead", "name": "Avery"}, {"id": "builder", "name": "Mira", "paused": True}],
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


def rem(manager: OrganizationManager, org: dict[str, Any], action: str, **params: Any) -> Any:
    return act(manager, org, action, "Rem:thread", **params)


def current(manager: OrganizationManager, org: dict[str, Any]) -> Any:
    return manager.store.get("", org["id"])


def settle(manager: OrganizationManager) -> None:
    until = time.monotonic() + 5
    while manager._active and time.monotonic() < until:
        time.sleep(0.02)
    assert not manager._active


def edited(
    manager: OrganizationManager, org: dict[str, Any], change: Any
) -> tuple[dict[str, Any], int]:
    state = current(manager, org)
    config = copy.deepcopy(state["config"])
    change(config)
    return config, state["revision"]


@pytest.mark.parametrize(
    ("change", "summary"),
    [
        (lambda c: c["employees"][1].update(paused=False), "resume Mira"),
        (lambda c: c.update(monthlyTurnLimit=0), "monthly limits"),
        (lambda c: c.update(maxChildTasksPerTurn=50), "operating limits"),
        (
            lambda c: c["employees"][0]["resources"]["mcpServers"].append(
                {"name": "net", "type": "http", "url": "https://example.com/mcp"}
            ),
            "more tools",
        ),
        (lambda c: c["employees"][0].update(permissionMode="danger-full-access"), "permissions"),
        (lambda c: c["employees"][0].update(heartbeatSeconds=900), "more often"),
        (lambda c: c["employees"].append({"id": "new", "name": "Noor"}), "active employee"),
        (lambda c: c["employees"][0].update(workspacePaths=[]), ""),
    ],
)
def test_rem_needs_consent_for_every_change_that_widens_access(
    tmp_path: Path, change: Any, summary: str
) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    config, revision = edited(manager, org, change)
    result = rem(manager, org, "configure", config=config, expectedRevision=revision, reason="Edit")
    if summary:
        assert result["needsUserConsent"] is True
        assert summary in result["summary"]
        assert current(manager, org)["revision"] == revision
    else:
        # Narrowing a grant applies directly.
        assert result["revision"] == revision + 1
    manager.close()


def test_rem_tightening_limits_or_renaming_applies_directly(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    config, revision = edited(
        manager, org, lambda c: c.update(name="Renamed", monthlyTurnLimit=50, maxConcurrency=1)
    )
    result = rem(
        manager, org, "configure", config=config, expectedRevision=revision, reason="Tighten"
    )
    assert result["config"]["monthlyTurnLimit"] == 50
    manager.close()


def test_rem_must_ask_to_approve_plans_and_schedule_work(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    task = rem(manager, org, "task_create", title="Gate", assignee="lead", approvalRequired=True)
    request = rem(manager, org, "approve", taskId=task["id"], expectedRevision=task["revision"])
    assert request["needsUserConsent"] and "Gate" in request["summary"]
    assert current(manager, org)["runtime"]["tasks"][0]["approvedRevision"] is None
    approved = act(manager, org, "approve", taskId=task["id"], expectedRevision=task["revision"])
    assert approved["approvedRevision"] == 1
    routine = {
        "name": "Poll",
        "task": {"title": "Poll", "assignee": "lead"},
        "intervalSeconds": 600,
    }
    assert rem(manager, org, "routine_save", routine=routine)["needsUserConsent"]
    # A disabled routine schedules nothing, so Rem may save it.
    saved = rem(manager, org, "routine_save", routine={**routine, "enabled": False})
    assert saved["enabled"] is False
    manager.close()


def test_consent_requests_dedupe_expire_and_apply_once(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    first = rem(manager, org, "control", state="running")
    again = rem(manager, org, "control", state="running")
    assert first["requestId"] == again["requestId"]
    act(manager, org, "request_decide", requestId=first["requestId"], decision="approve")
    with pytest.raises(ValueError, match="no longer waiting"):
        act(manager, org, "request_decide", requestId=first["requestId"], decision="approve")
    act(manager, org, "control", state="paused")

    stale = rem(manager, org, "control", state="running")

    def age(state: dict[str, Any]) -> dict[str, Any]:
        state["runtime"]["consentRequests"][stale["requestId"]]["at"] = (
            datetime.now(UTC) - timedelta(days=2)
        ).isoformat()
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", age)
    with pytest.raises(ValueError, match="more than a day old"):
        act(manager, org, "request_decide", requestId=stale["requestId"], decision="approve")
    state = current(manager, org)
    assert state["runtime"]["state"] == "paused"
    assert state["runtime"]["consentRequests"][stale["requestId"]]["status"] == "expired"
    manager.close()


def test_requests_that_would_fail_are_refused_before_waiting(tmp_path: Path) -> None:
    manager = start(tmp_path)
    owned = tmp_path / "owned"
    owned.mkdir()
    company(manager, owned)
    org = company(manager, tmp_path)
    config, revision = edited(manager, org, lambda c: c["projectRoots"].append(str(owned)))
    with manager.session(
        str(tmp_path), "Rem:thread", workspace_paths={str(tmp_path), str(owned)}
    ) as _:
        with pytest.raises(OrganizationConflict, match="already owned"):
            manager.call(
                str(tmp_path),
                "Rem:thread",
                {
                    "organizationId": org["id"],
                    "action": "configure",
                    "params": {"config": config, "expectedRevision": revision, "reason": "Add"},
                },
                workspace_paths={str(tmp_path), str(owned)},
            )
    assert not current(manager, org)["runtime"].get("consentRequests")
    manager.close()


def test_restart_closes_requests_that_were_being_applied(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    request = rem(manager, org, "resume_all", expectedRevision=org["revision"])

    def applying(state: dict[str, Any]) -> dict[str, Any]:
        state["runtime"]["consentRequests"][request["requestId"]]["status"] = "applying"
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", applying)
    manager.close()
    manager = start(tmp_path)
    item = current(manager, org)["runtime"]["consentRequests"][request["requestId"]]
    assert item["status"] == "failed" and "restarted" in item["error"]
    manager.close()


def test_every_state_change_is_recorded_with_its_task_changes(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path, maxTaskTurns=1)
    task = act(manager, org, "task_create", title="Limited", assignee="lead")

    def spent(state: dict[str, Any]) -> dict[str, Any]:
        state["runtime"]["tasks"][0]["turns"] = 1
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", spent)
    act(manager, org, "control", state="running")
    seen = len(manager.store.events("", org["id"]))
    manager.dispatch()
    events = manager.store.events("", org["id"])[seen:]
    assert [e["kind"] for e in events] == ["state_updated"]
    assert events[0]["payload"]["taskChanges"] == [
        {"taskId": task["id"], "title": "Limited", "from": "todo", "to": "blocked"}
    ]
    manager.close()


def test_activity_and_conversations_are_append_only_and_hash_linked(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    task = act(manager, org, "task_create", title="Ship", assignee="lead")
    act(manager, org, "comment", taskId=task["id"], body="Looks good")
    report = act(manager, org, "audit_verify")
    assert report["ok"] is True
    assert report["activity"]["linked"] == report["activity"]["events"] > 0
    with sqlite3.connect(manager.store.path) as db:
        for statement in (
            "UPDATE organization_events SET payload='{}'",
            "DELETE FROM organization_events",
            "UPDATE organization_comments SET body='{}'",
            "DELETE FROM organization_comments",
        ):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                db.execute(statement)
        # Someone who drops the guard can still be caught by the chain.
        db.execute("DROP TRIGGER events_no_update")
        db.execute("UPDATE organization_events SET payload='{}' WHERE kind='comment'")
    broken = act(manager, org, "audit_verify")
    assert broken["ok"] is False and broken["activity"]["brokenAt"]["reason"].startswith("Event")
    manager.close()


def test_events_written_before_linking_are_reported_not_rejected(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    with sqlite3.connect(manager.store.path) as db:
        first = db.execute("SELECT min(sequence) FROM organization_events").fetchone()[0]
        db.execute("DROP TRIGGER events_no_update")
        db.execute("UPDATE organization_events SET digest=NULL, previous_digest=NULL")
    manager.close()
    manager = start(tmp_path)
    act(manager, org, "task_create", title="After upgrade", assignee="lead")
    report = act(manager, org, "audit_verify")
    assert report["ok"] is True
    assert report["activity"]["beforeLinking"] >= 1 and report["activity"]["linked"] >= 1
    assert first is not None
    manager.close()


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_runs_record_what_ran_and_where_the_work_landed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(
        repo,
        "-c",
        "user.email=t@e.st",
        "-c",
        "user.name=T",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
    )

    async def writes(**options: Any) -> Any:
        (Path(options["working_dir"]) / "notes.txt").write_text("hello\n")
        await asyncio.sleep(0)
        yield {"type": "final", "message": {"body": "Wrote notes."}}

    manager = start(tmp_path, writes)
    org = company(manager, repo)
    act(manager, org, "task_create", title="Notes", assignee="lead")
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)
    attempt = next(iter(current(manager, org)["runtime"]["attempts"].values()))
    assert attempt["provider"] == "codex" and attempt["model"] == "cli-default"
    assert attempt["permissionMode"] == "workspace-write"
    assert attempt["isolation"] == "worktree" and attempt["blockGitPush"] is True
    assert len(attempt["inputSha256"]) == 64
    assert attempt["headCommit"] == git(repo, "rev-parse", "main")
    assert attempt["uncommittedChanges"] is True
    manager.close()


def test_worktree_is_recorded_before_it_is_created(tmp_path: Path, monkeypatch: Any) -> None:
    import gofer.ui.organizations as organizations

    created: list[Any] = []
    monkeypatch.setattr(organizations, "ensure_worktree", lambda worktree: created.append(worktree))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(
        repo,
        "-c",
        "user.email=t@e.st",
        "-c",
        "user.name=T",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
    )
    manager = start(tmp_path)
    org = company(manager, repo)
    task = act(manager, org, "task_create", title="Work", assignee="lead")
    with pytest.raises(OrganizationConflict):
        manager._task_worktree(org["projectRoot"], org["id"], task, str(repo), "revoked-run")
    assert not created
    assert current(manager, org)["runtime"]["tasks"][0]["worktree"] is None
    manager.close()


def test_expired_idempotency_records_leave_storage(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    act(manager, org, "task_create", title="Old", idempotencyKey="old-key")

    def age(state: dict[str, Any]) -> dict[str, Any]:
        for record in state["runtime"]["requests"].values():
            record["at"] = (datetime.now(UTC) - timedelta(days=45)).isoformat()
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", age)
    act(manager, org, "task_create", title="New", idempotencyKey="new-key")
    with sqlite3.connect(manager.store.path) as db:
        keys = [
            r[0] for r in db.execute("SELECT id FROM organization_records WHERE kind='requests'")
        ]
    assert keys == ["user:task_create:new-key"]
    manager.close()


def test_cancelled_runs_keep_their_worktree_until_they_stop(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@e.st")
    git(repo, "config", "user.name", "T")
    git(
        repo,
        "-c",
        "user.email=t@e.st",
        "-c",
        "user.name=T",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
    )

    begun: list[Path] = []

    async def slow(**options: Any) -> Any:
        begun.append(Path(options["working_dir"]))
        for _ in range(20):
            await asyncio.sleep(0.05)
            (Path(options["working_dir"]) / "still-writing.txt").write_text("x")
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start(tmp_path, slow)
    org = company(manager, repo)
    task = act(manager, org, "task_create", title="Work", assignee="lead")
    act(manager, org, "control", state="running")
    manager.dispatch()
    until = time.monotonic() + 3
    while not begun and time.monotonic() < until:
        time.sleep(0.02)
    running = current(manager, org)["runtime"]["tasks"][0]
    act(
        manager,
        org,
        "task_update",
        taskId=task["id"],
        expectedRevision=running["revision"],
        changes={"status": "cancelled"},
    )
    manager.release_worktrees(current(manager, org))
    assert Path(running["worktree"]["checkout"]).exists()
    settle(manager)
    manager.release_worktrees(current(manager, org))
    assert not Path(running["worktree"]["checkout"]).exists()

    manager.close()
