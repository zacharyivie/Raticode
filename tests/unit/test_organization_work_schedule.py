"""Organization work schedules and workers that run like small Rems inside their projects."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from gofer.ui.organization_schedule import (
    WorkSchedule,
    active_at,
    describe,
    issues,
    next_change,
    schedule_state,
    template,
)
from gofer.ui.organization_store import Task
from gofer.ui.organizations import (
    EMPLOYEE_ACTIONS,
    HEARTBEAT_TITLE,
    OrganizationManager,
    checkin_seconds,
    session_tool,
)
from tests.unit.test_organization_hours_heartbeats import act, company, read, settle, start

NY = "America/New_York"


def at(text: str, zone: str = "UTC") -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(zone))


def block(
    block_id: str, start: str, end: str, cadence: str = "daily", **rule: Any
) -> dict[str, Any]:
    return {
        "id": block_id,
        "label": block_id.title(),
        "cadence": cadence,
        "start": start,
        "end": end,
        **rule,
    }


def schedule(*blocks: dict[str, Any]) -> list[dict[str, Any]]:
    saved: list[dict[str, Any]] = WorkSchedule.model_validate(
        {"blocks": list(blocks)}
    ).model_dump()["blocks"]
    return saved


def winner(blocks: list[dict[str, Any]], when: datetime, zone: str = "UTC") -> str | None:
    found = active_at(blocks, ZoneInfo(zone), when)
    return found[0]["id"] if found else None


# ------------------------------------------------------------------------ validation


def test_blocks_are_validated_per_cadence() -> None:
    with pytest.raises(ValueError, match="24-hour times"):
        schedule(block("a", "8am", "17:00"))
    with pytest.raises(ValueError, match="choose the weekdays"):
        schedule(block("a", "08:00", "17:00", "weekly"))
    with pytest.raises(ValueError, match="use weekly"):
        schedule(block("a", "08:00", "17:00", days=[1]))
    with pytest.raises(ValueError, match="either a day of the month or a weekday"):
        schedule(block("a", "08:00", "17:00", "monthly"))
    with pytest.raises(ValueError, match="either a day of the month or a weekday"):
        schedule(block("a", "08:00", "17:00", "monthly", monthDay=3, nthWeek=1, weekday=0))
    with pytest.raises(ValueError, match="which weekday and which week"):
        schedule(block("a", "08:00", "17:00", "quarterly", nthWeek=1))
    with pytest.raises(ValueError, match="only monthly and quarterly"):
        schedule(block("a", "08:00", "17:00", monthDay=1))
    with pytest.raises(ValueError, match="its own ID"):
        schedule(block("a", "08:00", "09:00"), block("a", "10:00", "11:00"))
    # Overnight blocks and whole days are fine.
    assert schedule(
        block("night", "22:00", "06:00"), block("day", "00:00", "00:00", "weekly", days=[6])
    )


def test_overlaps_the_precedence_rule_cannot_order_are_refused() -> None:
    # Same cadence, same length: neither wins.
    with pytest.raises(ValueError, match="A and B overlap and neither takes precedence"):
        schedule(block("a", "08:00", "12:00"), block("b", "10:00", "14:00"))
    # An overnight block wraps into the next morning and clashes there.
    with pytest.raises(ValueError, match="overlap"):
        schedule(block("a", "22:00", "02:00"), block("b", "01:00", "05:00"))
    # Weekly blocks clash only on shared days.
    schedule(
        block("a", "08:00", "12:00", "weekly", days=[0]),
        block("b", "10:00", "14:00", "weekly", days=[1]),
    )
    with pytest.raises(ValueError, match="overlap"):
        schedule(
            block("a", "08:00", "12:00", "weekly", days=[0, 4]),
            block("b", "10:00", "14:00", "weekly", days=[4]),
        )
    # Day 31 and "the last Friday" share a date in some months, years apart.
    with pytest.raises(ValueError, match="overlap"):
        schedule(
            block("a", "09:00", "10:00", "monthly", monthDay=31),
            block("b", "09:00", "10:00", "monthly", nthWeek=-1, weekday=4),
        )
    # A shorter block or a longer cadence takes precedence, so these are allowed.
    schedule(block("long", "00:00", "08:00"), block("short", "00:00", "01:00"))
    schedule(
        block("daily", "08:00", "17:00"), block("weekly", "08:00", "17:00", "weekly", days=[4])
    )


def test_the_suggested_schedule_is_valid_and_reads_well() -> None:
    blocks = template()["blocks"]
    assert [b["label"] for b in blocks][:4] == [
        "New feature work",
        "Application hardening",
        "Nightly release",
        "Process improvement and gardening",
    ]
    assert [describe(b) for b in blocks] == [
        "Every day 08:00–17:00",
        "Every day 17:00–00:00",
        "Every day 00:00–01:00",
        "Every day 00:00–08:00",
        "Fridays 13:00–17:00",
        "Monthly, 1st Monday, 09:00–12:00",
        "Quarterly, 1st Tuesday of the 1st month, 09:00–17:00",
    ]
    assert describe(block("x", "09:00", "10:00", "monthly", monthDay=31)).startswith(
        "Monthly, last day"
    )


# ----------------------------------------------------------------- the active block


def test_daily_blocks_wrap_around_midnight() -> None:
    blocks = template()["blocks"][:4]
    # 2026-11-10 is a Tuesday.
    assert winner(blocks, at("2026-11-10T07:59", NY), NY) == "gardening"
    assert winner(blocks, at("2026-11-10T08:00", NY), NY) == "features"
    assert winner(blocks, at("2026-11-10T17:00", NY), NY) == "hardening"
    assert winner(blocks, at("2026-11-10T23:59", NY), NY) == "hardening"
    # Midnight: the one-hour release beats the eight-hour gardening block.
    assert winner(blocks, at("2026-11-11T00:00", NY), NY) == "nightly-release"
    assert winner(blocks, at("2026-11-11T00:59", NY), NY) == "nightly-release"
    assert winner(blocks, at("2026-11-11T01:00", NY), NY) == "gardening"
    change = next_change(blocks, ZoneInfo(NY), at("2026-11-10T23:30", NY))
    assert change is not None and change[1] is not None
    assert change[0] == at("2026-11-11T00:00", NY) and change[1]["id"] == "nightly-release"


def test_longer_cadences_win_and_the_choice_is_stable() -> None:
    blocks = template()["blocks"]
    # Friday afternoon: the weekly release beats daily feature work.
    assert winner(blocks, at("2026-11-13T14:00", NY), NY) == "weekly-release"
    assert winner(blocks, at("2026-11-13T12:00", NY), NY) == "features"
    # The first Monday of December: the monthly audit beats daily feature work.
    assert winner(blocks, at("2026-12-07T10:00", NY), NY) == "dependency-audit"
    assert winner(blocks, at("2026-12-14T10:00", NY), NY) == "features"
    # The first Tuesday of the quarter, January 2027: the quarterly review wins all day.
    assert winner(blocks, at("2027-01-05T16:30", NY), NY) == "quarterly-review"
    assert winner(blocks, at("2027-02-02T16:30", NY), NY) == "features"
    # A quarterly block beats a monthly one on the same morning.
    both = schedule(
        block("m", "09:00", "12:00", "monthly", nthWeek=1, weekday=1),
        block("q", "09:00", "17:00", "quarterly", nthWeek=1, weekday=1),
    )
    assert winner(both, at("2027-04-06T10:00")) == "q"
    assert winner(both, at("2027-05-04T10:00")) == "m"


def test_the_same_instant_reads_in_the_organizations_time_zone() -> None:
    blocks = template()["blocks"][:4]
    instant = at("2026-11-10T14:00")  # 09:00 in New York, 23:00 in Tokyo
    assert winner(blocks, instant, NY) == "features"
    assert winner(blocks, instant, "Asia/Tokyo") == "hardening"
    assert winner(blocks, instant, "UTC") == "features"


def test_daylight_saving_days_keep_clock_times() -> None:
    blocks = template()["blocks"][:4]
    zone = ZoneInfo(NY)
    # 2027-03-14: clocks spring forward at 02:00, so gardening (01:00-08:00) is 6 hours.
    found = active_at(blocks, zone, at("2027-03-14T05:00"))  # 01:00 EDT? no: 00:00 EST
    assert found is not None and found[0]["id"] == "nightly-release"
    gardening = active_at(blocks, zone, at("2027-03-14T07:30"))  # 03:30 EDT
    assert gardening is not None and gardening[0]["id"] == "gardening"
    assert gardening[2] - gardening[1] == timedelta(hours=7)  # 00:00 EST to 08:00 EDT
    # 2026-11-01: clocks fall back at 02:00, so the overnight span is an hour longer.
    autumn = active_at(blocks, zone, at("2026-11-01T08:30"))  # 03:30 EST
    assert autumn is not None and autumn[0]["id"] == "gardening"
    assert autumn[2] - autumn[1] == timedelta(hours=9)
    # A start time the change skips moves an hour later, like the clocks.
    early = schedule(block("early", "02:30", "04:00"))
    skipped = next_change(early, zone, at("2027-03-14T06:00"))  # 01:00 EST
    assert skipped is not None and skipped[0] == at("2027-03-14T03:30", NY)


def test_month_lengths_leap_years_and_last_weekdays() -> None:
    last = schedule(block("end", "09:00", "10:00", "monthly", monthDay=31))
    for day in ("2027-01-31", "2027-02-28", "2028-02-29", "2027-04-30", "2027-12-31"):
        assert winner(last, at(day + "T09:30")) == "end", day
    assert winner(last, at("2028-02-28T09:30")) is None
    thirtieth = schedule(block("pay", "09:00", "10:00", "monthly", monthDay=30))
    assert winner(thirtieth, at("2027-02-28T09:30")) == "pay"
    assert winner(thirtieth, at("2027-03-30T09:30")) == "pay"
    assert winner(thirtieth, at("2027-03-31T09:30")) is None
    friday = schedule(block("retro", "15:00", "16:00", "monthly", nthWeek=-1, weekday=4))
    assert winner(friday, at("2026-11-27T15:30")) == "retro"
    assert winner(friday, at("2026-11-20T15:30")) is None
    assert winner(friday, at("2027-01-29T15:30")) == "retro"
    # An overnight block on the last day runs into the next month.
    night = schedule(block("close", "22:00", "02:00", "monthly", monthDay=31))
    assert winner(night, at("2027-03-01T01:00")) == "close"
    assert winner(night, at("2027-03-02T01:00")) is None


def test_quarter_boundaries_and_far_off_next_blocks() -> None:
    quarter_end = schedule(
        block("close", "09:00", "17:00", "quarterly", quarterMonth=3, monthDay=31)
    )
    for day in ("2027-03-31", "2027-06-30", "2027-09-30", "2027-12-31"):
        assert winner(quarter_end, at(day + "T12:00")) == "close", day
    for day in ("2027-01-31", "2027-04-30", "2027-05-31", "2027-04-01"):
        assert winner(quarter_end, at(day + "T12:00")) is None, day
    # With nothing but a quarterly block, the next one is months away.
    change = next_change(quarter_end, ZoneInfo("UTC"), at("2027-04-01T12:00"))
    assert change is not None and change[0] == at("2027-06-30T09:00")
    assert change[1] is not None and change[1]["id"] == "close"
    second_month = schedule(
        block("plan", "09:00", "12:00", "quarterly", quarterMonth=2, nthWeek=1, weekday=0)
    )
    assert winner(second_month, at("2027-02-01T10:00")) == "plan"
    assert winner(second_month, at("2027-01-04T10:00")) is None


def test_gaps_and_blocks_outside_working_hours_are_reported() -> None:
    day = schedule(block("day", "09:00", "17:00"))
    found = issues({"operatingHours": {"mode": "always"}, "workSchedule": {"blocks": day}})
    assert [i["text"] for i in found] == [
        "No block covers Every day 17:00–09:00. Workers use their usual priorities then."
    ]
    office = {
        "mode": "scheduled",
        "timezone": "UTC",
        "windows": [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}],
    }
    assert issues({"operatingHours": office, "workSchedule": {"blocks": day}}) == []
    night = schedule(block("night", "22:00", "06:00"), block("day", "09:00", "17:00"))
    found = issues({"operatingHours": office, "workSchedule": {"blocks": night}})
    assert [i["kind"] for i in found] == ["outside_hours"]
    assert found[0]["text"] == "Night is outside working hours, so nothing runs then."
    # The suggested schedule covers every hour.
    full = {"operatingHours": {"mode": "always"}, "workSchedule": template()}
    assert issues(full) == []


def test_state_names_the_current_and_next_block() -> None:
    config = {"operatingHours": {"mode": "always", "timezone": NY}, "workSchedule": template()}
    state = schedule_state(config, at("2026-11-10T16:30", NY))
    assert state["configured"] is True and state["timezone"] == NY
    assert state["current"]["label"] == "New feature work"
    assert state["current"]["when"] == "Every day 08:00–17:00"
    assert state["next"]["label"] == "Application hardening"
    assert state["changesAt"] == at("2026-11-10T17:00", NY).astimezone(UTC).isoformat()
    empty = schedule_state({"operatingHours": {"mode": "always"}})
    assert empty == {
        "configured": False,
        "timezone": "UTC",
        "current": None,
        "next": None,
        "changesAt": None,
        "issues": [],
    }


# -------------------------------------------------- saving, migrating and handing off


def stored_config(manager: OrganizationManager, oid: str) -> dict[str, Any]:
    with sqlite3.connect(manager.store.path) as db:
        config: dict[str, Any] = json.loads(
            db.execute("SELECT config FROM organizations WHERE id=?", (oid,)).fetchone()[0]
        )
    return config


def test_existing_hours_and_tickets_carry_over_untouched(tmp_path: Path) -> None:
    manager = start(tmp_path)
    office = {
        "mode": "scheduled",
        "timezone": NY,
        "windows": [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}],
        "atClose": "finish",
    }
    made = company(manager, tmp_path, operatingHours=office)
    ticket = act(manager, made, "task_create", title="Keep me", assignee="lead")
    # An organization saved before work schedules existed has no workSchedule at all.
    config = stored_config(manager, made["id"])
    config.pop("workSchedule")
    with sqlite3.connect(manager.store.path) as db:
        db.execute("UPDATE organizations SET config=? WHERE id=?", (json.dumps(config), made["id"]))
    loaded = read(manager, made)
    assert loaded["config"]["operatingHours"] == office
    assert loaded["diagnostics"]["schedule"]["configured"] is False
    assert loaded["runtime"]["state"] == "paused"
    # Nothing applies the suggested schedule by itself; the user saves it.
    saved = act(
        manager,
        made,
        "plan_save",
        kind="operations",
        item={"workSchedule": template()},
        expectedRevision=loaded["revision"],
    )
    assert saved["config"]["operatingHours"] == office
    assert len(saved["config"]["workSchedule"]["blocks"]) == 7
    assert saved["runtime"]["state"] == "paused"
    kept = next(t for t in saved["runtime"]["tasks"] if t["id"] == ticket["id"])
    assert kept["title"] == "Keep me" and kept["status"] == "todo"
    history = act(manager, made, "history", limit=1)
    assert history[0]["reason"] == "Changed the work schedule"
    manager.close()


def test_changing_the_schedule_never_stops_running_work(tmp_path: Path) -> None:
    manager = start(tmp_path)
    made = company(manager, tmp_path)

    def seed(current: dict[str, Any]) -> dict[str, Any]:
        current["runtime"]["state"] = "running"
        current["runtime"]["tasks"].append(
            Task(title="Busy", assignee="lead", status="in_progress", claim="run-9").model_dump()
        )
        return {"seeded": True}

    manager.store.mutate("", made["id"], "test", "seed", seed)
    before = manager.store.get("", made["id"])
    act(
        manager,
        made,
        "plan_save",
        kind="operations",
        item={"workSchedule": template()},
        expectedRevision=before["revision"],
    )
    after = manager.store.get("", made["id"])
    busy = next(t for t in after["runtime"]["tasks"] if t["title"] == "Busy")
    assert busy["claim"] == "run-9" and busy["status"] == "in_progress"
    assert after["runtime"]["generation"] == before["runtime"]["generation"]
    assert after["runtime"]["state"] == "running"
    manager.close()


# ------------------------------------------------------------------- priority shifts


def around_now(keywords: list[str]) -> dict[str, Any]:
    """A daily block that is on right now, whatever the time of day."""
    now = datetime.now(UTC)
    return {
        "blocks": [
            {
                "id": "focus",
                "label": "Hardening",
                "focus": "Tests and fixes",
                "start": (now - timedelta(hours=1)).strftime("%H:%M"),
                "end": (now + timedelta(hours=2)).strftime("%H:%M"),
                "keywords": keywords,
            }
        ]
    }


def recorder() -> tuple[list[dict[str, Any]], Any]:
    seen: list[dict[str, Any]] = []

    async def stream(**options: Any) -> Any:
        seen.append(options)
        yield {"type": "final", "message": {"body": "Done."}}

    return seen, stream


@pytest.mark.parametrize(
    "keywords,first", [(["test"], "Add tests for login"), (["design"], "New settings page")]
)
def test_the_current_block_decides_which_ticket_goes_first(
    tmp_path: Path, keywords: list[str], first: str
) -> None:
    seen, stream = recorder()
    manager = start(tmp_path, stream)
    made = company(manager, tmp_path, workSchedule=around_now(keywords))
    # Same priority; the older ticket would go first without the block.
    act(manager, made, "task_create", title="New settings page", assignee="lead")
    act(manager, made, "task_create", title="Add tests for login", assignee="lead")
    act(manager, made, "control", state="running")
    manager.dispatch()
    settle(manager)
    started = json.loads(seen[0]["messages"][0]["body"])
    assert started["task"]["title"] == first
    # The run knows its block, what comes next and how handoffs work.
    assert started["workSchedule"]["current"]["label"] == "Hardening"
    assert "Block changes never stop a run" in started["workSchedule"]["handoff"]
    assert "Work schedule (UTC):" in seen[0]["agent_instructions"]
    assert "- Now: Hardening until" in seen[0]["agent_instructions"]
    attempt = next(iter(read(manager, made)["runtime"]["attempts"].values()))
    assert attempt["scheduleBlock"] == {"id": "focus", "label": "Hardening"}
    manager.close()


def test_a_block_lifts_a_ticket_one_level_but_never_over_two(tmp_path: Path) -> None:
    seen, stream = recorder()
    manager = start(tmp_path, stream)
    made = company(manager, tmp_path, workSchedule=around_now(["test"]))
    act(manager, made, "task_create", title="Critical outage", assignee="lead", priority="critical")
    act(manager, made, "task_create", title="Add tests", assignee="lead", priority="medium")
    act(manager, made, "control", state="running")
    manager.dispatch()
    settle(manager)
    assert json.loads(seen[0]["messages"][0]["body"])["task"]["title"] == "Critical outage"
    manager.close()


# --------------------------------------------------- workers as small Rems: their tools


def test_workers_are_offered_only_ticket_and_knowledge_actions() -> None:
    offered = set(
        session_tool({"employee_id": "lead"})["inputSchema"]["properties"]["action"]["enum"]
    )
    assert offered <= EMPLOYEE_ACTIONS
    for owner_only in (
        "create",
        "configure",
        "control",
        "restore",
        "import",
        "archive",
        "routine_save",
        "plan_save",
        "plan_delete",
        "webhook_save",
        "resume_all",
        "secret_store",
        "secret_reference",
        "routing_experiment",
        "heartbeat_resume",
        "workflow_launch",
        "rehearse",
        "backup",
        "export",
        "history",
        "providers",
        "request_decide",
    ):
        assert owner_only not in offered, owner_only
    assert {"task_update", "comment", "knowledge_save", "claim_add"} <= offered
    rem = set(session_tool({})["inputSchema"]["properties"]["action"]["enum"])
    assert {"configure", "plan_save", "control"} <= rem


def worker(
    manager: OrganizationManager,
    made: dict[str, Any],
    eid: str,
    action: str,
    run: str | None = None,
    **params: Any,
) -> Any:
    state = manager.store.get("", made["id"])
    return manager.call(
        made["projectRoot"],
        f"employee:{eid}",
        {"organizationId": made["id"], "action": action, "params": params},
        employee_id=eid,
        bound_org=made["id"],
        generation=state["runtime"]["generation"],
        run_id=run,
    )


def running(
    manager: OrganizationManager, made: dict[str, Any], eid: str, root: str, claim: str = "run-1"
) -> str:
    """A running ticket of a worker, without starting a provider."""

    def seed(current: dict[str, Any]) -> dict[str, Any]:
        current["runtime"]["state"] = "running"
        current["runtime"]["tasks"].append(
            Task(
                title=f"{eid} at work",
                assignee=eid,
                status="in_progress",
                claim=claim,
                workspacePath=root,
            ).model_dump()
        )
        return {"seeded": True}

    manager.store.mutate("", made["id"], "test", "seed", seed)
    return claim


def two_projects(manager: OrganizationManager, tmp_path: Path) -> tuple[Any, str]:
    other = tmp_path / "other"
    other.mkdir()
    second = str(other.resolve())
    made = company(
        manager,
        tmp_path,
        projectRoots=[str(tmp_path), second],
        employees=[
            {"id": "lead", "name": "Avery"},
            {"id": "bea", "name": "Bea"},
            {"id": "cal", "name": "Cal"},
        ],
    )
    for eid, roots in (("lead", [made["projectRoot"]]), ("bea", [made["projectRoot"], second])):
        act(manager, made, "self_work_projects", employeeId=eid, projects=roots, expectedRevision=0)
    return made, second


def test_workers_cannot_change_the_organization_its_schedule_or_limits(tmp_path: Path) -> None:
    manager = start(tmp_path)
    made, _ = two_projects(manager, tmp_path)
    run = running(manager, made, "lead", made["projectRoot"])
    state = manager.store.get("", made["id"])
    for action, params in (
        (
            "plan_save",
            {
                "kind": "operations",
                "item": {"workSchedule": template()},
                "expectedRevision": state["revision"],
            },
        ),
        ("control", {"state": "paused"}),
        (
            "configure",
            {"config": state["config"], "expectedRevision": state["revision"], "reason": "x"},
        ),
        ("routine_save", {"routine": {}}),
        ("self_work_projects", {"employeeId": "lead", "projects": [], "expectedRevision": 1}),
        ("resume_all", {"expectedRevision": state["revision"]}),
        ("rehearse", {}),
    ):
        with pytest.raises(ValueError, match="Only the user or Rem|cannot configure"):
            worker(manager, made, "lead", action, run, **params)
    after = manager.store.get("", made["id"])
    assert after["revision"] == state["revision"]
    assert after["config"]["workSchedule"] == {"blocks": []}
    manager.close()


def test_a_bounded_worker_stays_inside_its_projects_in_every_run(tmp_path: Path) -> None:
    manager = start(tmp_path)
    made, second = two_projects(manager, tmp_path)
    run = running(manager, made, "lead", made["projectRoot"])
    with pytest.raises(ValueError, match="aren't assigned"):
        worker(
            manager,
            made,
            "lead",
            "task_create",
            run,
            title="Elsewhere",
            status="backlog",
            workspacePath=second,
        )
    inside = worker(manager, made, "lead", "task_create", run, title="Here", status="backlog")
    assert inside["status"] == "backlog"
    # Talking about someone else's ticket in a project it doesn't have is refused...
    theirs = act(
        manager,
        made,
        "task_create",
        title="Bea's",
        assignee="bea",
        workspacePath=second,
        status="backlog",
    )
    with pytest.raises(ValueError, match="aren't assigned"):
        worker(manager, made, "lead", "comment", run, taskId=theirs["id"], body="Hi")
    # ...but its own tickets are always its business.
    mine = act(
        manager,
        made,
        "task_create",
        title="Mine far away",
        assignee="lead",
        workspacePath=second,
        status="backlog",
    )
    worker(manager, made, "lead", "comment", run, taskId=mine["id"], body="Not my project")
    manager.close()


def test_a_worker_saved_before_assignments_keeps_its_old_reach(tmp_path: Path) -> None:
    manager = start(tmp_path)
    made, second = two_projects(manager, tmp_path)
    run = running(manager, made, "cal", made["projectRoot"])
    created = worker(
        manager,
        made,
        "cal",
        "task_create",
        run,
        title="Anywhere",
        status="backlog",
        workspacePath=second,
    )
    assert created["workspacePath"] == second
    # It runs requested work anywhere in the organization, but has no check-ins.
    act(manager, made, "task_create", title="Requested", assignee="cal", workspacePath=second)
    manager.close()


def test_reassigning_needs_a_reason_a_coworker_in_the_project_and_this_org(
    tmp_path: Path,
) -> None:
    manager = start(tmp_path)
    made, second = two_projects(manager, tmp_path)
    (tmp_path / "another").mkdir()
    other_org = company(
        manager,
        tmp_path / "another",
        name="Elsewhere",
        slug="elsewhere",
        employees=[{"id": "zed", "name": "Zed"}],
    )
    assert other_org["id"] != made["id"]
    run = running(manager, made, "lead", made["projectRoot"])
    ticket = act(
        manager, made, "task_create", title="Hand me on", assignee="lead", status="backlog"
    )
    revision = ticket["revision"]
    with pytest.raises(ValueError, match="Say why you're reassigning it"):
        worker(
            manager,
            made,
            "lead",
            "task_update",
            run,
            taskId=ticket["id"],
            expectedRevision=revision,
            changes={"assignee": "bea"},
        )
    with pytest.raises(ValueError, match="Unknown employee"):
        worker(
            manager,
            made,
            "lead",
            "task_update",
            run,
            taskId=ticket["id"],
            expectedRevision=revision,
            changes={"assignee": "zed"},
            comment="Zed in the other organization knows this area.",
        )
    far = act(
        manager,
        made,
        "task_create",
        title="Far work",
        assignee="bea",
        workspacePath=second,
        status="backlog",
    )
    bea_run = running(manager, made, "bea", second, claim="run-2")
    with pytest.raises(ValueError, match="Avery isn't assigned to other"):
        worker(
            manager,
            made,
            "bea",
            "task_update",
            bea_run,
            taskId=far["id"],
            expectedRevision=far["revision"],
            changes={"assignee": "lead"},
            comment="Avery is less busy this week than I am.",
        )
    moved = worker(
        manager,
        made,
        "lead",
        "task_update",
        run,
        taskId=ticket["id"],
        expectedRevision=revision,
        changes={"assignee": "bea"},
        comment="Bea already knows this code from last week.",
    )
    assert moved["assignee"] == "bea"
    note = [c for c in moved["comments"] if c.get("kind") == "reassigned"][-1]
    assert note["body"] == "Reassigned to Bea: Bea already knows this code from last week."
    # Once it's Bea's, Avery can't keep managing it.
    with pytest.raises(ValueError, match="Only the assignee or their manager"):
        worker(
            manager,
            made,
            "lead",
            "task_update",
            run,
            taskId=ticket["id"],
            expectedRevision=moved["revision"],
            changes={"priority": "high"},
        )
    manager.close()


# ------------------------------------------------- workers as small Rems: their loop


def checkin_tasks(manager: OrganizationManager, made: dict[str, Any]) -> list[dict[str, Any]]:
    return [t for t in read(manager, made)["runtime"]["tasks"] if t["heartbeat"]]


def test_assigned_workers_check_in_without_a_heartbeat_setting(tmp_path: Path) -> None:
    manager = start(tmp_path)
    made, _ = two_projects(manager, tmp_path)
    act(manager, made, "control", state="running")
    manager.dispatch()
    manager.dispatch()
    settle(manager)
    owners = sorted(t["assignee"] for t in checkin_tasks(manager, made))
    # Avery and Bea have projects; Cal, saved before assignments, doesn't self-work.
    assert owners == ["bea", "lead"]
    assert all(t["title"] == HEARTBEAT_TITLE for t in checkin_tasks(manager, made))
    manager.close()


def test_quiet_check_ins_space_out_and_real_work_resets_them() -> None:
    employee = {"id": "lead", "heartbeatSeconds": 0}
    assert checkin_seconds({}, employee) == 3600
    assert checkin_seconds({"quietCheckins": {"lead": 1}}, employee) == 7200
    assert checkin_seconds({"quietCheckins": {"lead": 9}}, employee) == 3600 * 8
    assert checkin_seconds({}, {"id": "lead", "heartbeatSeconds": 900}) == 900


def test_blocked_tickets_are_reviewed_before_to_do_once_per_blocked_spell(
    tmp_path: Path,
) -> None:
    seen, stream = recorder()
    manager = start(tmp_path, stream)
    made = company(manager, tmp_path, employees=[{"id": "lead", "name": "Avery"}])
    act(
        manager,
        made,
        "self_work_projects",
        employeeId="lead",
        projects=[made["projectRoot"]],
        expectedRevision=0,
    )
    stuck = act(manager, made, "task_create", title="Stuck", assignee="lead")

    def block_it(current: dict[str, Any]) -> dict[str, Any]:
        task = next(t for t in current["runtime"]["tasks"] if t["id"] == stuck["id"])
        task.update(status="blocked", blockReason="The last run failed; see the conversation")
        return {"blocked": True}

    manager.store.mutate("", made["id"], "test", "block", block_it)
    act(manager, made, "task_create", title="Ready work", assignee="lead")
    act(manager, made, "control", state="running")
    manager.dispatch()
    settle(manager)
    first = json.loads(seen[0]["messages"][0]["body"])["task"]
    assert first["heartbeat"] is True
    assert "Start with your blocked tickets: STU-1 (The last run failed" in first["description"]
    manager.dispatch()
    settle(manager)
    assert json.loads(seen[1]["messages"][0]["body"])["task"]["title"] == "Ready work"
    # The same blocked spell isn't looked at again.
    for _ in range(3):
        manager.dispatch()
        settle(manager)
    assert len(seen) == 2
    reviews = read(manager, made)["runtime"]["blockReviews"]
    assert list(reviews) == [stuck["id"]]
    manager.close()


def test_an_organization_saved_before_hours_and_schedules_still_runs(tmp_path: Path) -> None:
    """The Trading Lab's stored configuration has neither operatingHours nor workSchedule."""
    seen, stream = recorder()
    manager = start(tmp_path, stream)
    made = company(manager, tmp_path)
    act(manager, made, "task_create", title="Old work", assignee="lead")
    config = stored_config(manager, made["id"])
    config.pop("workSchedule")
    config.pop("operatingHours")
    with sqlite3.connect(manager.store.path) as db:
        db.execute(
            "UPDATE organizations SET config=? WHERE id=?", (json.dumps(config), made["id"])
        )
    assert read(manager, made)["diagnostics"]["schedule"]["configured"] is False
    act(manager, made, "control", state="running")
    manager.dispatch()
    settle(manager)
    started = json.loads(seen[0]["messages"][0]["body"])
    assert started["task"]["title"] == "Old work" and "workSchedule" not in started
    assert "Work schedule (" not in seen[0]["agent_instructions"]
    manager.close()
