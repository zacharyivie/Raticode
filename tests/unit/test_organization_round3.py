"""Regressions for the third Organizations audit: remaining gates, anchors, isolation, inputs."""

from __future__ import annotations

import gzip
import json
import os
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organizations import OrganizationManager


async def final(**options: Any) -> Any:
    yield {"type": "final", "message": {"body": "Done."}}


def start(tmp_path: Path, stream: Any = final) -> OrganizationManager:
    return OrganizationManager(tmp_path / "data", stream=stream, start_runtime=False)


def company(manager: OrganizationManager, root: Path, **config: Any) -> Any:
    employees = config.pop(
        "employees",
        [{"id": "lead", "name": "Avery"}, {"id": "builder", "name": "Mira", "reportsTo": "lead"}],
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


def task_of(manager: OrganizationManager, org: dict[str, Any], tid: str) -> Any:
    return next(t for t in manager.store.get("", org["id"])["runtime"]["tasks"] if t["id"] == tid)


def patch_task(manager: OrganizationManager, org: dict[str, Any], tid: str, **fields: Any) -> Any:
    def change(state: dict[str, Any]) -> dict[str, Any]:
        for task in state["runtime"]["tasks"]:
            if task["id"] == tid:
                task.update(fields, revision=task["revision"] + 1)
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", change)
    return task_of(manager, org, tid)


def settle(manager: OrganizationManager) -> None:
    until = time.monotonic() + 5
    while manager._active and time.monotonic() < until:
        time.sleep(0.02)
    assert not manager._active


def test_rem_cannot_remove_human_gates_from_a_task(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    task = rem(
        manager, org, "task_create", title="Gated", assignee="builder", approvalRequired=True
    )
    request = rem(
        manager,
        org,
        "task_update",
        taskId=task["id"],
        expectedRevision=task["revision"],
        changes={"approvalRequired": False},
    )
    assert request["needsUserConsent"] and "approval step" in request["summary"]
    assert task_of(manager, org, task["id"])["approvalRequired"] is True
    # Ordinary edits still apply directly.
    edited = rem(
        manager,
        org,
        "task_update",
        taskId=task["id"],
        expectedRevision=task["revision"],
        changes={"priority": "high"},
    )
    assert edited["priority"] == "high"
    reviewed = rem(
        manager, org, "task_create", title="Checked", assignee="builder", reviewRequired=True
    )
    request = rem(
        manager,
        org,
        "task_update",
        taskId=reviewed["id"],
        expectedRevision=reviewed["revision"],
        changes={"reviewRequired": False},
    )
    assert "independent review" in request["summary"]
    manager.close()


def test_rem_must_ask_before_accepting_a_reviewed_result(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    task = act(manager, org, "task_create", title="Ship", assignee="builder", reviewRequired=True)
    task = patch_task(manager, org, task["id"], status="in_review", evidence="Built")
    request = rem(
        manager,
        org,
        "review",
        taskId=task["id"],
        expectedRevision=task["revision"],
        decision="accept",
        body="Looks right",
    )
    assert request["needsUserConsent"] and "Accept the result" in request["summary"]
    assert task_of(manager, org, task["id"])["status"] == "in_review"
    revised = rem(
        manager,
        org,
        "review",
        taskId=task["id"],
        expectedRevision=task["revision"],
        decision="revise",
        body="Add tests",
    )
    assert revised["status"] == "todo"
    manager.close()


def test_rem_cannot_run_an_enabled_routine_more_often(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    routine = {
        "name": "Poll",
        "task": {"title": "Poll", "assignee": "lead"},
        "intervalSeconds": 3600,
    }
    saved = act(manager, org, "routine_save", routine=routine)
    for change in (
        {"intervalSeconds": 60},
        {"intervalSeconds": 0, "cron": "* * * * *"},
        {"overlap": "queue"},
    ):
        request = rem(
            manager,
            org,
            "routine_save",
            routine={**saved, **change},
            expectedRevision=saved["revision"],
        )
        assert request["needsUserConsent"] and "more often" in request["summary"]
    renamed = rem(
        manager,
        org,
        "routine_save",
        routine={**saved, "name": "Hourly poll", "intervalSeconds": 7200},
        expectedRevision=saved["revision"],
    )
    assert renamed["name"] == "Hourly poll"
    manager.close()


def test_only_the_user_grants_a_fresh_run_allowance(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path, maxTaskTurns=1)
    task = act(manager, org, "task_create", title="Limited", assignee="builder")
    task = patch_task(manager, org, task["id"], turns=1, status="blocked", blockReason="Run limit")
    reopened = rem(manager, org, "reopen", taskId=task["id"], expectedRevision=task["revision"])
    assert reopened["status"] == "todo" and reopened["turns"] == 1
    task = patch_task(manager, org, task["id"], status="blocked", blockReason="Run limit")
    reopened = act(manager, org, "reopen", taskId=task["id"], expectedRevision=task["revision"])
    assert reopened["turns"] == 0
    manager.close()


def test_rem_cannot_reenable_a_revoked_credential(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    account = f"{org['id']}/api/1"
    act(manager, org, "secret_reference", id="api", account=account)
    revoked = rem(manager, org, "secret_reference", id="api", account=account, revoked=True)
    assert revoked["revoked"] is True
    request = rem(manager, org, "secret_reference", id="api", account=account, revoked=False)
    assert request["needsUserConsent"] and "credential api" in request["summary"]
    assert manager.store.get("", org["id"])["runtime"]["secrets"]["api"]["revoked"] is True
    manager.close()


@pytest.mark.parametrize(
    ("action", "params", "summary"),
    [
        ("workflow_launch", {"taskId": "t", "expectedRevision": 1, "irSha256": "x"}, "workflow"),
        (
            "webhook_save",
            {"routineId": "r", "expectedRevision": 1, "secretRef": "s", "enabled": True},
            "outside service",
        ),
        (
            "execution_resolve",
            {"attemptId": "a", "confirmStopped": True, "reason": "checked"},
            "stopped",
        ),
    ],
)
def test_rem_needs_consent_to_launch_trigger_or_settle_outside_work(
    tmp_path: Path, action: str, params: dict[str, Any], summary: str
) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    request = rem(manager, org, action, **params)
    assert request["needsUserConsent"] is True and summary in request["summary"]
    manager.close()


def employee(manager: OrganizationManager, org: dict[str, Any], action: str, **params: Any) -> Any:
    state = manager.store.get("", org["id"])
    return manager.call(
        org["projectRoot"],
        "employee:lead",
        {"organizationId": org["id"], "action": action, "params": params},
        employee_id="lead",
        bound_org=org["id"],
        generation=state["runtime"]["generation"],
        run_id="lead-run",
    )


def test_employees_cannot_lift_blocks_set_for_review(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    running = act(manager, org, "task_create", title="Manage", assignee="lead")
    patch_task(manager, org, running["id"], claim="lead-run", status="in_progress")
    held = act(manager, org, "task_create", title="Interrupted", assignee="builder")
    held = patch_task(
        manager,
        org,
        held["id"],
        status="blocked",
        blockReason="Interrupted when Raticode restarted",
    )
    act(manager, org, "control", state="running")
    with pytest.raises(ValueError, match="blocked this task for review"):
        employee(manager, org, "comment", taskId=held["id"], body="fine", unblock=True)
    with pytest.raises(ValueError, match="blocked this task for review"):
        employee(
            manager,
            org,
            "task_update",
            taskId=held["id"],
            expectedRevision=held["revision"],
            changes={"status": "todo"},
        )
    with pytest.raises(ValueError, match="blocked for review"):
        employee(manager, org, "reopen", taskId=held["id"], expectedRevision=held["revision"])
    cancelled = act(
        manager, org, "task_create", title="Dropped", assignee="builder", status="cancelled"
    )
    with pytest.raises(ValueError, match="cancelled"):
        employee(
            manager, org, "reopen", taskId=cancelled["id"], expectedRevision=cancelled["revision"]
        )
    # A block an employee set can still be lifted by its manager.
    own = act(manager, org, "task_create", title="Waiting on data", assignee="builder")
    patch_task(
        manager,
        org,
        own["id"],
        status="blocked",
        blockReason="Blocked by Mira; see the conversation",
    )
    employee(manager, org, "comment", taskId=own["id"], body="Data is in", unblock=True)
    assert task_of(manager, org, own["id"])["status"] == "todo"
    manager.close()


def test_heartbeats_under_fifteen_minutes_cannot_be_added(tmp_path: Path) -> None:
    manager = start(tmp_path)
    with pytest.raises(ValueError, match="900 seconds"):
        company(manager, tmp_path, employees=[{"id": "a", "name": "A", "heartbeatSeconds": 60}])
    org = company(manager, tmp_path, employees=[{"id": "a", "name": "A", "heartbeatSeconds": 900}])

    # A saved 60-second heartbeat from before the floor keeps working through other edits.
    with sqlite3.connect(manager.store.path) as db:
        row = db.execute("SELECT config FROM organizations").fetchone()
        config = json.loads(row[0])
        config["employees"][0]["heartbeatSeconds"] = 60
        db.execute("UPDATE organizations SET config=?", (json.dumps(config),))
    current = manager.store.get("", org["id"])
    renamed = act(
        manager,
        org,
        "configure",
        config={**current["config"], "name": "Renamed"},
        expectedRevision=current["revision"],
        reason="Rename",
    )
    assert renamed["config"]["employees"][0]["heartbeatSeconds"] == 60
    changed = json.loads(json.dumps(renamed["config"]))
    changed["employees"][0]["heartbeatSeconds"] = 300
    with pytest.raises(ValueError, match="900 seconds"):
        act(
            manager,
            org,
            "configure",
            config=changed,
            expectedRevision=renamed["revision"],
            reason="Faster",
        )
    manager.close()


def test_anchor_file_catches_removed_activity(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    task = act(manager, org, "task_create", title="Ship", assignee="lead")
    act(manager, org, "comment", taskId=task["id"], body="Started")
    assert act(manager, org, "audit_verify")["ok"] is True
    anchors = manager.store.anchor_path.read_text().splitlines()
    assert len(anchors) == 1 and stat.S_IMODE(os.stat(manager.store.anchor_path).st_mode) == 0o600
    assert manager.store.anchor(org["id"]) is None  # Unchanged head writes nothing.
    with sqlite3.connect(manager.store.path) as db:
        db.execute("DROP TRIGGER events_no_delete")
        newest = db.execute("SELECT max(sequence) FROM organization_events").fetchone()[0]
        db.execute("DELETE FROM organization_events WHERE sequence=?", (newest,))
    report = act(manager, org, "audit_verify")
    # The remaining chain is internally consistent; only the anchor shows the loss.
    assert report["activity"]["ok"] is True
    assert report["anchors"]["ok"] is False and report["ok"] is False
    manager.close()


def test_one_broken_organization_does_not_stall_the_others(
    tmp_path: Path, monkeypatch: Any
) -> None:
    manager = start(tmp_path)
    broken_root, healthy_root = tmp_path / "broken", tmp_path / "healthy"
    broken_root.mkdir()
    healthy_root.mkdir()
    broken = company(manager, broken_root)
    healthy = company(manager, healthy_root)
    for org in (broken, healthy):
        act(manager, org, "task_create", title="Work", assignee="lead")
        act(manager, org, "control", state="running")
    original = manager.store.get

    def failing(project: str, oid: str) -> Any:
        if oid == broken["id"]:
            raise RuntimeError("corrupt organization")
        return original(project, oid)

    monkeypatch.setattr(manager.store, "get", failing)
    manager._dispatch_cursor = 0
    manager.dispatch()
    settle(manager)
    monkeypatch.setattr(manager.store, "get", original)
    assert manager.store.get("", healthy["id"])["runtime"]["tasks"][0]["status"] == "in_review"
    manager.close()


def test_each_run_keeps_its_exact_input(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    task = act(manager, org, "task_create", title="Explain", description="Say why", assignee="lead")
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)
    attempt = next(iter(manager.store.get("", org["id"])["runtime"]["attempts"].values()))
    record = act(manager, org, "run_input", taskId=task["id"], turnId=attempt["id"])
    assert record["matchesRecord"] is True and record["sha256"] == attempt["inputSha256"]
    assert "Say why" in record["input"]["messages"][0]["body"]
    assert record["input"]["permissionMode"] == "workspace-write"
    assert not any(s["name"] == "organizations" for s in record["input"]["resources"]["mcpServers"])
    path = manager.run_input_path(org["id"], attempt["id"])
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    with gzip.open(path, "wb") as handle:
        handle.write(json.dumps({**record["input"], "model": "something-else"}).encode())
    assert (
        act(manager, org, "run_input", taskId=task["id"], turnId=attempt["id"])["matchesRecord"]
        is False
    )
    with pytest.raises(ValueError, match="run of this task"):
        act(manager, org, "run_input", taskId="other", turnId=attempt["id"])
    manager.close()
