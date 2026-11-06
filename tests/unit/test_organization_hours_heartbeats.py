"""Working hours, restarts, heartbeat check-ins and workers pulling tickets into In progress."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from gofer.ui.organization_hours import OperatingHours, describe, is_open, next_change, widens
from gofer.ui.organizations import OrganizationManager

NY = "America/New_York"


def hours(*windows: tuple[list[int], str, str], zone: str = "UTC", **extra: Any) -> dict:
    return {
        "mode": "scheduled",
        "timezone": zone,
        "windows": [{"days": d, "start": s, "end": e} for d, s, e in windows],
        **extra,
    }


def at(text: str, zone: str = "UTC") -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(zone))


# ------------------------------------------------------------------ the schedule itself


def test_weekday_hours_open_and_close_at_their_boundaries() -> None:
    week = hours(([0, 1, 2, 3, 4], "09:00", "17:00"), zone=NY)
    # 2026-11-09 is a Monday.
    assert not is_open(week, at("2026-11-09T08:59", NY))
    assert is_open(week, at("2026-11-09T09:00", NY))
    assert is_open(week, at("2026-11-09T16:59", NY))
    assert not is_open(week, at("2026-11-09T17:00", NY))
    assert not is_open(week, at("2026-11-14T12:00", NY))  # Saturday
    # Friday evening, the next opening is Monday morning in New York time.
    assert next_change(week, at("2026-11-13T18:00", NY)) == at("2026-11-16T09:00", NY)
    assert next_change(week, at("2026-11-13T10:00", NY)) == at("2026-11-13T17:00", NY)
    # The same instant seen from another zone gives the same answer.
    assert is_open(week, at("2026-11-09T14:30", "UTC"))  # 09:30 in New York
    assert describe(week) == "Weekdays 09:00–17:00 (America/New_York)"


def test_overnight_hours_run_past_midnight_into_the_next_day() -> None:
    nights = hours(([4], "22:00", "06:00"))  # Friday night shift
    assert is_open(nights, at("2026-11-13T23:30"))  # Friday
    assert is_open(nights, at("2026-11-14T05:59"))  # Saturday morning
    assert not is_open(nights, at("2026-11-14T06:00"))
    assert not is_open(nights, at("2026-11-14T23:00"))  # Saturday night isn't listed
    assert next_change(nights, at("2026-11-13T23:30")) == at("2026-11-14T06:00")
    # Sunday night into Monday wraps around the week.
    sunday = hours(([6], "20:00", "02:00"))
    assert is_open(sunday, at("2026-11-16T01:00"))  # Monday 01:00


def test_daylight_saving_changes_follow_the_chosen_zone() -> None:
    week = hours((list(range(7)), "09:00", "17:00"), zone=NY)
    # The day after clocks change, 09:00 New York is 14:00 UTC in winter, 13:00 in summer.
    assert is_open(week, at("2027-03-15T13:30", "UTC"))
    assert not is_open(week, at("2027-03-13T13:30", "UTC"))
    assert next_change(week, at("2027-03-14T05:00", "UTC")) == at("2027-03-14T13:00", "UTC")
    early = hours((list(range(7)), "02:30", "04:00"), zone=NY)
    # 02:30 doesn't exist on 2027-03-14; the window still opens and closes sensibly.
    change = next_change(early, at("2027-03-14T05:00", "UTC"))
    assert change is not None and change <= at("2027-03-14T08:00", "UTC")


def test_hours_are_validated_and_widening_is_detected() -> None:
    with pytest.raises(ValueError, match="Unknown time zone"):
        OperatingHours.model_validate(hours(([0], "09:00", "10:00"), zone="Mars/Olympus"))
    with pytest.raises(ValueError, match="at least one window"):
        OperatingHours.model_validate({"mode": "scheduled", "windows": []})
    with pytest.raises(ValueError, match="different start and end"):
        OperatingHours.model_validate(hours(([0], "09:00", "09:00")))
    with pytest.raises(ValueError, match="24-hour times"):
        OperatingHours.model_validate(hours(([0], "9am", "10:00")))
    office = hours(([0, 1, 2, 3, 4], "09:00", "17:00"))
    assert widens(office, {"mode": "always"})
    assert widens(office, hours(([0, 1, 2, 3, 4], "08:00", "17:00")))
    assert not widens(office, hours(([0, 1, 2, 3, 4], "10:00", "16:00")))
    assert not widens({"mode": "always"}, office)
    assert widens(office, {**office, "atClose": "finish"})


# ---------------------------------------------------------------- organizations at work


async def quick(**options: Any) -> Any:
    yield {"type": "final", "message": {"body": "Done."}}


def start(tmp_path: Path, stream: Any = quick) -> OrganizationManager:
    return OrganizationManager(tmp_path / "data", stream=stream, start_runtime=False)


def company(manager: OrganizationManager, root: Path, **config: Any) -> Any:
    employees = config.pop(
        "employees",
        [
            {"id": "lead", "name": "Avery Lane"},
            {"id": "mira", "name": "Mira Santos", "reportsTo": "lead"},
        ],
    )
    return manager.call(
        str(root),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Studio",
                    "slug": "studio",
                    "employees": employees,
                    "projects": [{"id": "desktop", "name": "Desktop"}],
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


def read(manager: OrganizationManager, org: dict[str, Any]) -> Any:
    return act(manager, org, "read")


def ticket(manager: OrganizationManager, org: dict[str, Any], tid: str) -> Any:
    return next(t for t in read(manager, org)["runtime"]["tasks"] if t["id"] == tid)


def settle(manager: OrganizationManager, seconds: float = 10) -> None:
    until = time.monotonic() + seconds
    while manager._active and time.monotonic() < until:
        time.sleep(0.02)
    assert not manager._active


def window_from_now(start_minutes: int, end_minutes: int) -> dict[str, Any]:
    now = datetime.now(UTC)
    start, end = now + timedelta(minutes=start_minutes), now + timedelta(minutes=end_minutes)
    return hours((list(range(7)), start.strftime("%H:%M"), end.strftime("%H:%M")))


def set_hours(manager: OrganizationManager, org: dict[str, Any], value: dict, actor="user") -> Any:
    revision = read(manager, org)["revision"]
    return act(
        manager,
        org,
        "plan_save",
        actor,
        kind="operations",
        item={"operatingHours": value},
        expectedRevision=revision,
    )


def test_nothing_starts_outside_working_hours(tmp_path: Path) -> None:
    seen: list[str] = []

    async def worker(**options: Any) -> Any:
        seen.append(options["workflow"]["organizationRun"]["taskId"])
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start(tmp_path, worker)
    org = company(
        manager,
        tmp_path,
        employees=[
            {"id": "lead", "name": "Avery Lane", "heartbeatSeconds": 900},
            {"id": "mira", "name": "Mira Santos", "reportsTo": "lead"},
        ],
    )
    set_hours(manager, org, window_from_now(120, 180))
    made = act(manager, org, "task_create", title="Ship", assignee="mira")
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)
    state = read(manager, org)
    assert seen == [] and state["diagnostics"]["hours"]["open"] is False
    assert state["diagnostics"]["taskReasons"][made["id"]].startswith("Off hours until ")
    # Heartbeats wait for working hours too.
    assert not any(t["heartbeat"] for t in state["runtime"]["tasks"])
    set_hours(manager, org, window_from_now(-60, 60))
    manager.dispatch()
    settle(manager)
    assert made["id"] in seen
    manager.close()


def test_closing_time_stops_running_work_and_hands_it_back(tmp_path: Path) -> None:
    async def slow(**options: Any) -> Any:
        await asyncio.sleep(30)
        yield {"type": "final", "message": {"body": "Too late."}}

    manager = start(tmp_path, slow)
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Long job", assignee="mira")
    act(manager, org, "control", state="running")
    manager.dispatch()
    assert manager._active
    set_hours(manager, org, window_from_now(120, 180))
    manager.dispatch()  # Raticode notices the hours ended.
    settle(manager)
    stopped = ticket(manager, org, made["id"])
    assert stopped["status"] == "todo" and stopped["turns"] == 0
    assert "Working hours ended" in stopped["comments"][-1]["body"]
    assert read(manager, org)["runtime"]["state"] == "running"  # Still on, just closed.
    manager.close()


def test_work_may_finish_after_closing_when_chosen(tmp_path: Path) -> None:
    holder: dict[str, Any] = {}

    async def worker(**options: Any) -> Any:
        for _ in range(100):
            if holder.get("go"):
                break
            await asyncio.sleep(0.02)
        yield {"type": "final", "message": {"body": "Finished."}}

    manager = start(tmp_path, worker)
    org = company(manager, tmp_path)
    made = act(manager, org, "task_create", title="Long job", assignee="mira")
    act(manager, org, "control", state="running")
    manager.dispatch()
    set_hours(manager, org, {**window_from_now(120, 180), "atClose": "finish"})
    manager.dispatch()
    holder["go"] = True
    settle(manager)
    assert ticket(manager, org, made["id"])["status"] == "in_review"
    manager.close()


def test_restarts_pause_unless_the_organization_should_keep_running(tmp_path: Path) -> None:
    for name in ("keep", "stop"):
        (tmp_path / name).mkdir()
    manager = start(tmp_path)
    keep = company(manager, tmp_path / "keep", resumeAfterRestart=True)
    stop = company(manager, tmp_path / "stop")
    for org in (keep, stop):
        act(manager, org, "control", state="running")
    interrupted = act(manager, keep, "task_create", title="Mid-run", assignee="mira")

    def claim(state: dict[str, Any]) -> dict[str, Any]:
        for task in state["runtime"]["tasks"]:
            task.update(claim="lost-run", status="in_progress")
        return {"probe": True}

    manager.store.mutate("", keep["id"], "test", "probe", claim)
    manager.close()
    restarted = start(tmp_path)
    kept = restarted.store.get("", keep["id"])["runtime"]
    assert kept["state"] == "running"
    lost = next(t for t in kept["tasks"] if t["id"] == interrupted["id"])
    assert lost["status"] == "blocked" and lost["blockReason"].startswith("Interrupted")
    assert restarted.store.get("", stop["id"])["runtime"]["state"] == "paused"
    restarted.close()


def test_rem_needs_consent_to_widen_hours_or_keep_running_after_restarts(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path, operatingHours=hours(([0, 1, 2, 3, 4], "09:00", "17:00")))
    wider = set_hours(manager, org, {"mode": "always"}, actor="Rem:thread")
    assert wider["needsUserConsent"] and "work at more hours" in wider["summary"]
    narrower = set_hours(
        manager, org, hours(([0, 1, 2, 3, 4], "10:00", "16:00")), actor="Rem:thread"
    )
    assert narrower["config"]["operatingHours"]["windows"][0]["start"] == "10:00"
    restart = act(
        manager,
        org,
        "plan_save",
        "Rem:thread",
        kind="operations",
        item={"resumeAfterRestart": True},
        expectedRevision=read(manager, org)["revision"],
    )
    assert "keep the organization running after Raticode restarts" in restart["summary"]
    with pytest.raises(ValueError, match="Unknown time zone"):
        set_hours(manager, org, hours(([0], "09:00", "10:00"), zone="Nowhere/Land"))
    manager.close()


# ------------------------------------------------------------------------- heartbeats


def due_now(manager: OrganizationManager, org: dict[str, Any], eid: str = "lead") -> None:
    def change(state: dict[str, Any]) -> dict[str, Any]:
        state["runtime"]["heartbeats"][eid] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", change)


def checkins(manager: OrganizationManager, org: dict[str, Any]) -> list[str]:
    return [t["status"] for t in read(manager, org)["runtime"]["tasks"] if t["heartbeat"]]


def test_heartbeat_check_ins_close_themselves_so_the_next_one_comes(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(
        manager, tmp_path, employees=[{"id": "lead", "name": "Avery Lane", "heartbeatSeconds": 900}]
    )
    act(manager, org, "control", state="running")
    manager.dispatch()  # creates the check-in
    manager.dispatch()  # runs it
    settle(manager)
    assert checkins(manager, org) == ["done"]
    done = next(t for t in read(manager, org)["runtime"]["tasks"] if t["heartbeat"])
    assert done["evidence"].startswith("Check-in: Done.")
    due_now(manager, org)
    manager.dispatch()
    manager.dispatch()
    settle(manager)
    # Before the fix the first check-in sat in Review and no second one ever came.
    assert checkins(manager, org) == ["done", "done"]
    manager.close()


def test_a_legacy_check_in_waiting_for_review_no_longer_blocks_heartbeats(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(
        manager, tmp_path, employees=[{"id": "lead", "name": "Avery Lane", "heartbeatSeconds": 900}]
    )
    act(
        manager,
        org,
        "task_create",
        title="Heartbeat: review assignments",
        assignee="lead",
        status="backlog",
    )

    def to_review(state: dict[str, Any]) -> dict[str, Any]:
        state["runtime"]["tasks"][0]["status"] = "in_review"
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", to_review)
    act(manager, org, "control", state="running")
    manager.dispatch()
    settle(manager)
    statuses = [t["status"] for t in read(manager, org)["runtime"]["tasks"]]
    # Before the fix the review-waiting check-in stopped every later heartbeat.
    assert statuses == ["in_review", "done"]
    manager.close()


def test_failed_check_ins_are_replaced_then_stop_and_say_so(tmp_path: Path) -> None:
    async def broken(**options: Any) -> Any:
        raise RuntimeError("provider unavailable")
        yield {}

    manager = start(tmp_path, broken)
    org = company(
        manager, tmp_path, employees=[{"id": "lead", "name": "Avery Lane", "heartbeatSeconds": 900}]
    )
    act(manager, org, "control", state="running")
    for _ in range(4):
        due_now(manager, org)
        manager.dispatch()
        manager.dispatch()
        settle(manager)
    state = read(manager, org)
    # Three failures were replaced; the fourth stays blocked and heartbeats stop, visibly.
    assert checkins(manager, org) == ["cancelled", "cancelled", "cancelled", "blocked"]
    due_now(manager, org)
    manager.dispatch()
    state = read(manager, org)
    stalled = state["diagnostics"]["heartbeats"]["lead"]["stalled"]
    assert stalled["reason"].startswith("The last run failed")
    assert checkins(manager, org) == ["cancelled", "cancelled", "cancelled", "blocked"]
    with pytest.raises(ValueError, match="operating policy"):
        manager.call(
            org["projectRoot"],
            "employee:lead",
            {
                "organizationId": org["id"],
                "action": "heartbeat_resume",
                "params": {"employeeId": "lead"},
            },
            employee_id="lead",
            bound_org=org["id"],
            generation=state["runtime"]["generation"],
        )
    act(manager, org, "heartbeat_resume", employeeId="lead")
    resumed = read(manager, org)
    assert resumed["diagnostics"]["heartbeats"]["lead"]["stalled"] is None
    assert checkins(manager, org) == ["cancelled"] * 4
    manager.close()


# ---------------------------------------------------------------- planning and pulling


def test_workers_plan_in_to_do_and_pull_work_into_in_progress(tmp_path: Path) -> None:
    seen: list[str] = []

    async def worker(**options: Any) -> Any:
        seen.append(options["workflow"]["organizationRun"]["taskId"])
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start(tmp_path, worker)
    org = company(manager, tmp_path)
    act(manager, org, "control", state="running")
    urgent = act(
        manager,
        org,
        "task_create",
        title="Urgent",
        assignee="mira",
        priority="critical",
        status="backlog",
    )
    later = as_worker(
        manager,
        org,
        "task_create",
        title="Later",
        assignee="mira",
        project="desktop",
        status="backlog",
    )
    planned = as_worker(
        manager,
        org,
        "task_create",
        title="Planned",
        assignee="mira",
        project="desktop",
        priority="low",
    )
    assert planned["status"] == "todo"
    pulled = as_worker(
        manager,
        org,
        "task_update",
        taskId=later["id"],
        expectedRevision=later["revision"],
        changes={"status": "in_progress"},
    )
    # Moving to In progress means "work on this next"; Raticode starts it.
    assert pulled["status"] == "todo" and pulled["pulledAt"]
    assert "moved this to In progress" in pulled["comments"][-1]["body"]
    act(
        manager,
        org,
        "task_update",
        taskId=urgent["id"],
        expectedRevision=urgent["revision"],
        changes={"status": "todo"},
    )
    manager.dispatch()
    settle(manager)
    assert seen == [later["id"]]
    assert ticket(manager, org, later["id"])["pulledAt"] is None
    manager.dispatch()
    settle(manager)
    assert seen == [later["id"], urgent["id"]]
    manager.close()


def test_a_pulled_ticket_shows_it_is_next_while_the_worker_is_busy(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    busy = act(manager, org, "task_create", title="Busy", assignee="mira")
    waiting = act(manager, org, "task_create", title="Waiting", assignee="mira", status="backlog")
    act(manager, org, "control", state="running")

    def claim(state: dict[str, Any]) -> dict[str, Any]:
        for task in state["runtime"]["tasks"]:
            if task["id"] == busy["id"]:
                task.update(claim="probe", status="in_progress")
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", claim)
    current = ticket(manager, org, waiting["id"])
    as_worker(
        manager,
        org,
        "task_update",
        taskId=waiting["id"],
        expectedRevision=current["revision"],
        changes={"status": "in_progress"},
    )
    reason = read(manager, org)["diagnostics"]["taskReasons"][waiting["id"]]
    assert reason == "Next up: Mira Santos is working on STU-1"
    manager.close()


def test_pulling_and_taking_work_keep_ticket_permissions(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(
        manager,
        tmp_path,
        employees=[
            {"id": "lead", "name": "Avery Lane"},
            {
                "id": "mira",
                "name": "Mira Santos",
                "reportsTo": "lead",
                "ticketAccess": {"desktop": ["create"], "*": ["edit", "move"]},
            },
        ],
    )
    act(manager, org, "control", state="running")
    mine = as_worker(
        manager,
        org,
        "task_create",
        title="Mine",
        assignee="mira",
        project="desktop",
        status="backlog",
    )
    with pytest.raises(ValueError, match="can't change the status of tickets in Desktop"):
        as_worker(
            manager,
            org,
            "task_update",
            taskId=mine["id"],
            expectedRevision=mine["revision"],
            changes={"status": "in_progress"},
        )
    # Anyone's unassigned ticket outside a project can be taken, but nothing else changed.
    loose = act(manager, org, "task_create", title="Loose", status="backlog")
    with pytest.raises(ValueError, match="Only the assignee"):
        as_worker(
            manager,
            org,
            "task_update",
            taskId=loose["id"],
            expectedRevision=loose["revision"],
            changes={"assignee": "mira", "title": "Renamed"},
        )
    taken = as_worker(
        manager,
        org,
        "task_update",
        taskId=loose["id"],
        expectedRevision=loose["revision"],
        changes={"assignee": "mira", "status": "in_progress"},
    )
    assert taken["assignee"] == "mira" and taken["status"] == "todo" and taken["pulledAt"]
    others = act(manager, org, "task_create", title="Avery's", assignee="lead", status="backlog")
    with pytest.raises(ValueError, match="Only the assignee"):
        as_worker(
            manager,
            org,
            "task_update",
            taskId=others["id"],
            expectedRevision=others["revision"],
            changes={"assignee": "mira", "status": "in_progress"},
        )
    manager.close()


def test_one_ticket_at_a_time_per_worker_and_concurrency_still_hold(tmp_path: Path) -> None:
    gate: dict[str, bool] = {}

    async def worker(**options: Any) -> Any:
        for _ in range(200):
            if gate.get("go"):
                break
            await asyncio.sleep(0.02)
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start(tmp_path, worker)
    org = company(manager, tmp_path)
    for title in ("One", "Two"):
        act(manager, org, "task_create", title=title, assignee="mira")
    act(manager, org, "task_create", title="Three", assignee="lead")
    act(manager, org, "control", state="running")
    for _ in range(3):
        manager.dispatch()
    # maxConcurrency defaults to 1: one run, even though two workers have ready work.
    assert len(manager._active) == 1
    gate["go"] = True
    settle(manager)
    manager.close()
