"""How workers get their next ticket: ordering, holds, explanations and recurring work."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organizations import OrganizationManager


def start(tmp_path: Path, stream: Any) -> OrganizationManager:
    return OrganizationManager(tmp_path / "data", stream=stream, start_runtime=False)


def company(manager: OrganizationManager, root: Path, **config: Any) -> Any:
    return manager.call(
        str(root),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Studio",
                    "slug": "studio",
                    "employees": [
                        {"id": "lead", "name": "Avery Lane"},
                        {"id": "mira", "name": "Mira Santos", "reportsTo": "lead"},
                    ],
                    "projects": [{"id": "desktop", "name": "Desktop"}],
                    "milestones": [{"id": "PM1", "title": "Release", "projectId": "desktop"}],
                    "epics": [{"id": "E1", "title": "Board", "milestoneId": "PM1"}],
                    **config,
                }
            },
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


def as_worker(manager: OrganizationManager, org: dict[str, Any], action: str, **params: Any) -> Any:
    state = manager.store.get("", org["id"])
    return manager.call(
        org["projectRoot"],
        "employee:mira",
        {"organizationId": org["id"], "action": action, "params": params},
        employee_id="mira",
        bound_org=org["id"],
        generation=state["runtime"]["generation"],
    )


def patch(manager: OrganizationManager, org: dict[str, Any], tid: str, **fields: Any) -> None:
    def change(state: dict[str, Any]) -> dict[str, Any]:
        for task in state["runtime"]["tasks"]:
            if task["id"] == tid:
                task.update(fields)
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", change)


def reasons(manager: OrganizationManager, org: dict[str, Any]) -> dict[str, str]:
    result = act(manager, org, "read")
    return {
        t["title"]: result["diagnostics"]["taskReasons"][t["id"]]
        for t in result["runtime"]["tasks"]
    }


def settle(manager: OrganizationManager) -> None:
    until = time.monotonic() + 10
    while manager._active and time.monotonic() < until:
        time.sleep(0.02)
    assert not manager._active


def recorder(seen: list[str]) -> Any:
    async def run(**options: Any) -> Any:
        seen.append(options["workflow"]["organizationRun"]["taskId"])
        yield {"type": "final", "message": {"body": "Done."}}

    return run


def run_once(manager: OrganizationManager) -> None:
    manager.dispatch()
    settle(manager)


def test_old_low_priority_work_never_jumps_critical_work(tmp_path: Path) -> None:
    seen: list[str] = []
    manager = start(tmp_path, recorder(seen))
    org = company(manager, tmp_path)
    old = act(manager, org, "task_create", title="Old chore", assignee="mira", priority="low")
    patch(manager, org, old["id"], createdAt=(datetime.now(UTC) - timedelta(days=9)).isoformat())
    urgent = act(manager, org, "task_create", title="Outage", assignee="mira", priority="critical")
    act(manager, org, "control", state="running")
    run_once(manager)
    # Before the fix a nine-day-old low ticket outranked a fresh critical one.
    assert seen == [urgent["id"]]
    manager.close()


def test_waiting_work_still_moves_up_one_priority_level(tmp_path: Path) -> None:
    seen: list[str] = []
    manager = start(tmp_path, recorder(seen))
    org = company(manager, tmp_path)
    fresh = act(manager, org, "task_create", title="Fresh", assignee="mira", priority="medium")
    waiting = act(manager, org, "task_create", title="Waiting", assignee="mira", priority="medium")
    patch(
        manager, org, waiting["id"], createdAt=(datetime.now(UTC) - timedelta(days=2)).isoformat()
    )
    act(manager, org, "control", state="running")
    run_once(manager)
    assert seen == [waiting["id"]] and fresh["id"] not in seen
    manager.close()


def test_a_cancelled_dependency_is_named_instead_of_waiting_silently(tmp_path: Path) -> None:
    seen: list[str] = []
    manager = start(tmp_path, recorder(seen))
    org = company(manager, tmp_path)
    first = act(manager, org, "task_create", title="Schema", assignee="lead", status="backlog")
    second = act(
        manager, org, "task_create", title="Migration", assignee="mira", dependsOn=[first["id"]]
    )
    assert reasons(manager, org)["Migration"] == "Waiting for STU-1"
    current = next(
        t for t in act(manager, org, "read")["runtime"]["tasks"] if t["id"] == first["id"]
    )
    act(
        manager,
        org,
        "task_update",
        taskId=first["id"],
        expectedRevision=current["revision"],
        changes={"status": "cancelled"},
    )
    assert reasons(manager, org)["Migration"] == (
        "Waiting for STU-1, which was cancelled. Remove the dependency or reopen it."
    )
    act(manager, org, "control", state="running")
    run_once(manager)
    assert second["id"] not in seen
    manager.close()


def test_tickets_in_a_cancelled_epic_or_milestone_wait(tmp_path: Path) -> None:
    seen: list[str] = []
    manager = start(tmp_path, recorder(seen))
    org = company(manager, tmp_path)
    ticket = act(manager, org, "task_create", title="Drag cards", assignee="mira", epicId="E1")

    def plan(kind: str, **item: Any) -> None:
        revision = act(manager, org, "read")["revision"]
        act(manager, org, "plan_save", kind=kind, item=item, expectedRevision=revision)

    plan("epic", id="E1", status="cancelled")
    act(manager, org, "control", state="running")
    assert reasons(manager, org)["Drag cards"].startswith("Its epic, Board, is cancelled")
    run_once(manager)
    assert seen == []
    plan("epic", id="E1", status="active")
    plan("milestone", id="PM1", status="cancelled")
    assert reasons(manager, org)["Drag cards"].startswith("Its milestone, Release, is cancelled")
    run_once(manager)
    assert seen == []
    plan("milestone", id="PM1", status="active")
    run_once(manager)
    assert seen == [ticket["id"]]
    manager.close()


def test_ready_work_says_when_it_is_queued_behind_the_workers_current_run(tmp_path: Path) -> None:
    manager = start(tmp_path, recorder([]))
    org = company(manager, tmp_path)
    busy = act(manager, org, "task_create", title="Busy", assignee="mira")
    act(manager, org, "task_create", title="Next", assignee="mira")
    act(manager, org, "task_create", title="Unassigned")
    act(manager, org, "control", state="running")
    patch(manager, org, busy["id"], claim="probe-run", status="in_progress")
    found = reasons(manager, org)
    assert found["Next"] == "Queued: Mira Santos is working on STU-1"
    assert found["Unassigned"] == "Assign an employee"
    manager.close()


def test_only_the_user_or_rem_schedule_recurring_work(tmp_path: Path) -> None:
    manager = start(tmp_path, recorder([]))
    org = company(manager, tmp_path)
    act(manager, org, "control", state="running")
    with pytest.raises(ValueError, match="Workers can't schedule recurring work"):
        as_worker(
            manager,
            org,
            "task_create",
            title="Poll",
            assignee="mira",
            recurring=True,
            intervalSeconds=3600,
        )
    mine = as_worker(manager, org, "task_create", title="Once", assignee="mira")
    with pytest.raises(ValueError, match="Workers can't schedule recurring work"):
        as_worker(
            manager,
            org,
            "task_update",
            taskId=mine["id"],
            expectedRevision=mine["revision"],
            changes={"recurring": True, "intervalSeconds": 3600},
        )
    with pytest.raises(ValueError, match="at least 15 minutes"):
        act(manager, org, "task_create", title="Spin", recurring=True, intervalSeconds=60)
    daily = act(
        manager,
        org,
        "task_create",
        title="Daily",
        assignee="mira",
        recurring=True,
        intervalSeconds=86400,
        status="backlog",
    )
    assert daily["recurring"] is True

    asked = act(
        manager,
        org,
        "task_create",
        "Rem:thread",
        title="Hourly",
        assignee="mira",
        recurring=True,
        intervalSeconds=3600,
    )
    assert asked["needsUserConsent"] is True and "Schedule recurring work" in asked["summary"]
    more = act(
        manager,
        org,
        "task_update",
        "Rem:thread",
        taskId=daily["id"],
        expectedRevision=daily["revision"],
        changes={"intervalSeconds": 3600},
    )
    assert more["needsUserConsent"] is True
    slower = act(
        manager,
        org,
        "task_update",
        "Rem:thread",
        taskId=daily["id"],
        expectedRevision=daily["revision"],
        changes={"intervalSeconds": 172800},
    )
    assert slower["intervalSeconds"] == 172800
    titles = [t["title"] for t in act(manager, org, "read")["runtime"]["tasks"]]
    assert "Hourly" not in titles and "Poll" not in titles
    manager.close()


def test_saved_fast_schedules_keep_working_until_changed(tmp_path: Path) -> None:
    manager = start(tmp_path, recorder([]))
    org = company(manager, tmp_path)
    legacy = act(manager, org, "task_create", title="Legacy", status="backlog")
    patch(manager, org, legacy["id"], recurring=True, intervalSeconds=120)
    current = next(
        t for t in act(manager, org, "read")["runtime"]["tasks"] if t["id"] == legacy["id"]
    )
    renamed = act(
        manager,
        org,
        "task_update",
        taskId=legacy["id"],
        expectedRevision=current["revision"],
        changes={"title": "Legacy poll"},
    )
    assert renamed["intervalSeconds"] == 120
    with pytest.raises(ValueError, match="at least 15 minutes"):
        act(
            manager,
            org,
            "task_update",
            taskId=legacy["id"],
            expectedRevision=renamed["revision"],
            changes={"intervalSeconds": 90},
        )
    manager.close()


def test_work_stopped_by_a_configuration_change_says_why(tmp_path: Path) -> None:
    manager = start(tmp_path, recorder([]))
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Busy", assignee="mira")
    act(manager, org, "control", state="running")
    patch(manager, org, made["id"], claim="probe-run", status="in_progress")
    state = act(manager, org, "read")
    config = state["config"]
    config["employees"] = [
        {**e, "permissionMode": "read-only"} if e["id"] == "mira" else e
        for e in config["employees"]
    ]
    act(
        manager,
        org,
        "configure",
        config=config,
        expectedRevision=state["revision"],
        reason="Read only",
    )
    stopped = next(
        t for t in act(manager, org, "read")["runtime"]["tasks"] if t["id"] == made["id"]
    )
    assert stopped["status"] == "blocked"
    assert stopped["blockReason"].startswith("Stopped because the configuration changed")
    manager.close()
