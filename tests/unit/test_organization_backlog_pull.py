"""Organization workers pulling accepted Backlog tickets into To do when they need work."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organization_backlog import PULL_TITLE, plan_pulls
from gofer.ui.organizations import OrganizationManager


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


def as_worker(
    manager: OrganizationManager, org: dict[str, Any], action: str, eid: str = "mira", **params: Any
) -> Any:
    state = manager.store.get("", org["id"])
    return manager.call(
        org["projectRoot"],
        f"employee:{eid}",
        {"organizationId": org["id"], "action": action, "params": params},
        employee_id=eid,
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


def backlog(manager: OrganizationManager, org: dict[str, Any], title: str, **fields: Any) -> Any:
    fields.setdefault("assignee", "mira")
    return act(manager, org, "task_create", title=title, status="backlog", **fields)


def move(manager: OrganizationManager, org: dict[str, Any], tid: str, status: str) -> Any:
    current = ticket(manager, org, tid)
    return act(
        manager,
        org,
        "task_update",
        taskId=tid,
        expectedRevision=current["revision"],
        changes={"status": status},
    )


def pulls(task: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in task["comments"] if c.get("title") == PULL_TITLE]


def planned(manager: OrganizationManager, org: dict[str, Any], running: int = 0) -> list[str]:
    """The tickets the next pass would pull, by title, without moving anything."""
    state = manager.store.get("", org["id"])
    return [
        t["title"]
        for t, _ in plan_pulls(
            state,
            running=running,
            owns=lambda root: manager.store.owns_project(org["id"], root),
            instant=datetime.now(UTC),
        )
    ]


def operations(manager: OrganizationManager, org: dict[str, Any], actor: str = "user", **item: Any):
    return act(
        manager,
        org,
        "plan_save",
        actor,
        kind="operations",
        item=item,
        expectedRevision=read(manager, org)["revision"],
    )


# ------------------------------------------------------------------------ pulling


def test_a_free_worker_pulls_its_backlog_ticket_runs_it_and_says_so(tmp_path: Path) -> None:
    seen: list[str] = []

    async def worker(**options: Any) -> Any:
        seen.append(options["workflow"]["organizationRun"]["taskId"])
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start(tmp_path, worker)
    org = company(manager, tmp_path)
    staged = backlog(manager, org, "Staged work")
    # Pulling is on unless turned off, and the ticket says what will happen to it.
    assert read(manager, org)["config"]["pullBacklog"] is True
    act(manager, org, "control", state="running")
    reason = read(manager, org)["diagnostics"]["taskReasons"][staged["id"]]
    assert reason == "In Backlog until its worker is free to pull it"
    manager.dispatch()
    settle(manager)
    # Pulled into To do and run in the same pass, through the usual dispatcher.
    assert seen == [staged["id"]]
    after = ticket(manager, org, staged["id"])
    assert after["backlogPulledAt"] and after["keepInBacklog"] is False
    [entry] = pulls(after)
    assert entry["from"] == "backlog" and entry["to"] == "todo" and entry["actor"] == "system"
    assert entry["body"].startswith("Mira Santos had no other work it could take")
    assert "medium priority" in entry["body"]
    # The activity log has its own entry naming the worker and ticket.
    events = act(manager, org, "events")
    [note] = [e for e in events if e["kind"] == "backlog_pull"]
    assert note["actor"] == "system"
    assert note["payload"]["pulled"][0]["taskId"] == staged["id"]
    assert note["payload"]["pulled"][0]["employee"] == "Mira Santos"
    assert note["payload"]["pulled"][0]["key"] == "STU-1"
    assert {"taskId": staged["id"], "title": "Staged work", "from": "backlog", "to": "todo"} in (
        note["payload"]["taskChanges"]
    )
    manager.close()


def test_tickets_waiting_for_approval_stay_in_backlog(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    plan = backlog(manager, org, "Needs a plan approval", approvalRequired=True)
    act(manager, org, "control", state="running")
    # Workers act only while the organization runs.
    idea = as_worker(manager, org, "task_create", title="Mira's idea", status="backlog")
    assert idea["proposedBy"] == "mira"
    reasons = read(manager, org)["diagnostics"]["taskReasons"]
    assert reasons[plan["id"]] == "Waiting for plan approval"
    assert reasons[idea["id"]] == "Proposed by a worker. Move it to To do to accept it."
    assert planned(manager, org) == []
    for _ in range(3):
        manager.dispatch()
        settle(manager)
    assert ticket(manager, org, plan["id"])["status"] == "backlog"
    assert ticket(manager, org, idea["id"])["status"] == "backlog"
    # Approving the plan lets it be pulled; the worker's own idea still waits for the user.
    act(manager, org, "approve", taskId=plan["id"], expectedRevision=plan["revision"])
    assert planned(manager, org) == ["Needs a plan approval"]
    manager.close()


def test_a_ticket_with_a_request_waiting_for_the_user_stays(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    staged = backlog(manager, org, "Staged")
    act(manager, org, "control", state="running")

    def ask(state: dict[str, Any]) -> dict[str, Any]:
        state["runtime"].setdefault("consentRequests", {})["r1"] = {
            "id": "r1",
            "action": "task_update",
            "params": {"taskId": staged["id"]},
            "status": "pending",
            "at": datetime.now(UTC).isoformat(),
        }
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", ask)
    assert planned(manager, org) == []
    reason = read(manager, org)["diagnostics"]["taskReasons"][staged["id"]]
    assert reason == "Waiting for your decision on a request about it"
    manager.close()


def test_dependencies_must_be_done_before_a_pull(tmp_path: Path) -> None:
    manager = start(tmp_path, quick)
    org = company(manager, tmp_path)
    first = backlog(manager, org, "First", assignee="lead")
    second = backlog(manager, org, "Second", dependsOn=[first["id"]])
    act(manager, org, "control", state="running")
    assert planned(manager, org) == ["First"]
    manager.dispatch()
    settle(manager)
    assert ticket(manager, org, second["id"])["status"] == "backlog"
    # Finished work goes to review; once accepted, the next ticket can be pulled.
    done = ticket(manager, org, first["id"])
    assert done["status"] in {"in_review", "done"}
    if done["status"] == "in_review":
        act(
            manager,
            org,
            "review",
            taskId=first["id"],
            expectedRevision=done["revision"],
            decision="accept",
            body="Looks right.",
        )
    assert planned(manager, org) == ["Second"]
    manager.close()


def test_priority_then_the_focus_block_then_age_decide_what_comes_first(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path, employees=[{"id": "mira", "name": "Mira Santos"}])
    backlog(manager, org, "Old low", priority="low")
    backlog(manager, org, "Older medium")
    backlog(manager, org, "Newer medium about research")
    act(manager, org, "control", state="running")
    assert planned(manager, org) == ["Older medium"]
    # A block that is on now and names "research" lifts the matching ticket above its peer.
    now = datetime.now(UTC)
    operations(
        manager,
        org,
        workSchedule={
            "blocks": [
                {
                    "id": "research",
                    "label": "Research",
                    "cadence": "daily",
                    "start": (now - timedelta(minutes=30)).strftime("%H:%M"),
                    "end": (now + timedelta(minutes=30)).strftime("%H:%M"),
                    "keywords": ["research"],
                }
            ]
        },
    )
    assert planned(manager, org) == ["Newer medium about research"]
    urgent = backlog(manager, org, "Urgent", priority="critical")
    assert planned(manager, org) == ["Urgent"]
    manager.close()
    assert urgent


# ------------------------------------------------------------------ bounds and limits


def test_bounded_workers_take_unassigned_tickets_only_in_their_projects(tmp_path: Path) -> None:
    manager = start(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    org = company(
        manager,
        tmp_path,
        employees=[{"id": "mira", "name": "Mira Santos"}],
        projectRoots=[str(tmp_path), str(other)],
    )
    outside = backlog(manager, org, "Elsewhere", assignee=None, workspacePath=str(other))
    act(manager, org, "control", state="running")
    # A worker without assigned projects never takes unassigned work.
    assert planned(manager, org) == []
    act(
        manager,
        org,
        "self_work_projects",
        employeeId="mira",
        projects=[str(tmp_path)],
        expectedRevision=0,
    )
    assert planned(manager, org) == []
    inside = backlog(manager, org, "Here", assignee=None)
    assert planned(manager, org) == ["Here"]
    manager.dispatch()
    settle(manager)
    taken = ticket(manager, org, inside["id"])
    assert taken["assignee"] == "mira"
    assert "it had no assignee and is in their projects" in pulls(taken)[0]["body"]
    assert ticket(manager, org, outside["id"])["status"] == "backlog"
    # Its own ticket outside its projects isn't pulled either.
    backlog(manager, org, "Mine, elsewhere", workspacePath=str(other))
    assert planned(manager, org) == []
    manager.close()


def test_ticket_permissions_limit_taking_unassigned_work(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(
        manager,
        tmp_path,
        employees=[
            {"id": "mira", "name": "Mira Santos", "ticketAccess": {"desktop": ["create", "move"]}}
        ],
    )
    act(
        manager,
        org,
        "self_work_projects",
        employeeId="mira",
        projects=[str(tmp_path)],
        expectedRevision=0,
    )
    backlog(manager, org, "Unassigned desktop work", assignee=None, project="desktop")
    act(manager, org, "control", state="running")
    assert planned(manager, org) == []
    # Tickets the user already gave it are pulled whatever its ticket permissions say.
    backlog(manager, org, "Given to Mira", project="desktop")
    assert planned(manager, org) == ["Given to Mira"]
    manager.close()


def test_pulls_fill_free_run_slots_one_ticket_per_worker(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path, maxConcurrency=2)
    backlog(manager, org, "Mira one", priority="high")
    backlog(manager, org, "Mira two", priority="high")
    backlog(manager, org, "Lead one", assignee="lead")
    act(manager, org, "control", state="running")
    # Two free slots: one ticket each, never two for one worker.
    assert planned(manager, org) == ["Mira one", "Lead one"]
    # One slot already in use leaves room for one pull.
    assert planned(manager, org, running=1) == ["Mira one"]
    assert planned(manager, org, running=2) == []
    # A worker with To do work it can start doesn't pull, and its ticket takes a slot.
    act(manager, org, "task_create", title="Ready", assignee="mira")
    assert planned(manager, org) == ["Lead one"]
    assert planned(manager, org, running=1) == []
    manager.close()


def test_a_running_worker_does_not_pull(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path, maxConcurrency=4)
    busy = act(manager, org, "task_create", title="Busy", assignee="mira")
    backlog(manager, org, "Later")
    act(manager, org, "control", state="running")

    def claim(state: dict[str, Any]) -> dict[str, Any]:
        for task in state["runtime"]["tasks"]:
            if task["id"] == busy["id"]:
                task.update(claim="probe", status="in_progress")
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", claim)
    assert planned(manager, org, running=1) == []
    manager.close()


def test_limits_held_workers_and_run_limits_stop_pulls(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path, monthlyTurnLimit=1)
    backlog(manager, org, "Staged")
    act(manager, org, "control", state="running")
    assert planned(manager, org) == ["Staged"]

    def spend(state: dict[str, Any]) -> dict[str, Any]:
        month = datetime.now(UTC).strftime("%Y-%m")
        state["runtime"].setdefault("usage", {})[month] = {"company": {"turns": 1}}
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", spend)
    assert planned(manager, org) == []
    manager.close()
    # A held worker and a ticket at its run limit aren't pulled either.
    manager = start(tmp_path)
    (tmp_path / "second").mkdir()
    second = company(
        manager,
        tmp_path / "second",
        name="Second",
        slug="second",
        employees=[{"id": "mira", "name": "Mira Santos", "paused": True}],
    )
    backlog(manager, second, "Held")
    act(manager, second, "control", state="running")
    assert planned(manager, second) == []
    manager.close()


def test_a_ticket_at_its_run_limit_is_not_pulled(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path, maxTaskTurns=1)
    staged = backlog(manager, org, "Used up")
    act(manager, org, "control", state="running")

    def used(state: dict[str, Any]) -> dict[str, Any]:
        for task in state["runtime"]["tasks"]:
            if task["id"] == staged["id"]:
                task["turns"] = 1
        return {"probe": True}

    manager.store.mutate("", org["id"], "test", "probe", used)
    assert planned(manager, org) == []
    manager.close()


def test_nothing_is_pulled_while_paused_or_closed(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    staged = backlog(manager, org, "Staged")
    # Paused: the default for a new organization.
    manager.dispatch()
    assert ticket(manager, org, staged["id"])["status"] == "backlog"
    assert planned(manager, org) == []
    act(manager, org, "control", state="running")
    now = datetime.now(UTC)
    later = now + timedelta(hours=2)
    operations(
        manager,
        org,
        operatingHours={
            "mode": "scheduled",
            "timezone": "UTC",
            "windows": [
                {
                    "days": list(range(7)),
                    "start": later.strftime("%H:%M"),
                    "end": (later + timedelta(hours=1)).strftime("%H:%M"),
                }
            ],
        },
    )
    manager.dispatch()
    settle(manager)
    assert ticket(manager, org, staged["id"])["status"] == "backlog"
    assert planned(manager, org) == []
    events = act(manager, org, "events")
    assert not [e for e in events if e["kind"] == "backlog_pull"]
    manager.close()


def test_a_worker_looks_at_its_blocked_tickets_before_pulling(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path, employees=[{"id": "mira", "name": "Mira Santos"}])
    act(
        manager,
        org,
        "self_work_projects",
        employeeId="mira",
        projects=[str(tmp_path)],
        expectedRevision=0,
    )
    stuck = act(manager, org, "task_create", title="Stuck", assignee="mira")
    move(manager, org, stuck["id"], "blocked")
    backlog(manager, org, "Staged")
    act(manager, org, "control", state="running")
    manager.dispatch()  # The blocked-first check-in comes before any pull.
    settle(manager)
    state = read(manager, org)
    assert any(t["heartbeat"] for t in state["runtime"]["tasks"])
    staged = next(t for t in state["runtime"]["tasks"] if t["title"] == "Staged")
    assert not pulls(staged)
    manager.close()


# ------------------------------------------------------------- idempotence and holds


def test_pulls_happen_once_and_survive_restarts(tmp_path: Path) -> None:
    gate = asyncio.Event()

    async def slow(**options: Any) -> Any:
        try:
            await asyncio.wait_for(gate.wait(), 30)
        except TimeoutError:
            pass
        yield {"type": "final", "message": {"body": "Done."}}

    manager = start(tmp_path, slow)
    org = company(manager, tmp_path, maxConcurrency=1)
    first = backlog(manager, org, "First", priority="high")
    second = backlog(manager, org, "Second")
    act(manager, org, "control", state="running")
    for _ in range(5):
        manager.dispatch()
    # One run under way, one pull, and the other ticket waits for a free slot.
    assert len(pulls(ticket(manager, org, first["id"]))) == 1
    assert ticket(manager, org, second["id"])["status"] == "backlog"
    manager.close()
    # A restart pauses the organization and changes nothing in the Backlog.
    again = start(tmp_path)
    assert ticket(again, org, second["id"])["status"] == "backlog"
    assert len(pulls(ticket(again, org, first["id"]))) == 1
    events = act(again, org, "events")
    assert len([e for e in events if e["kind"] == "backlog_pull"]) == 1
    again.close()


def test_a_ticket_moved_back_to_backlog_is_never_pulled_again(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    staged = backlog(manager, org, "Staged")
    act(manager, org, "control", state="running")
    # Pulled, then the user moves it back before it runs.
    state = manager.store.get("", org["id"])
    manager._pull_backlog(org["projectRoot"], org["id"], state, datetime.now(UTC))
    assert ticket(manager, org, staged["id"])["status"] == "todo"
    move(manager, org, staged["id"], "backlog")
    held = ticket(manager, org, staged["id"])
    assert held["keepInBacklog"] is True
    reason = read(manager, org)["diagnostics"]["taskReasons"][staged["id"]]
    assert reason == "Kept in Backlog: it was moved back here, so workers leave it"
    for _ in range(3):
        manager.dispatch()
        settle(manager)
    assert ticket(manager, org, staged["id"])["status"] == "backlog"
    assert len(pulls(ticket(manager, org, staged["id"]))) == 1
    manager.close()
    # Still held after a restart.
    again = start(tmp_path)
    act(again, org, "control", state="running")
    again.dispatch()
    settle(again)
    assert ticket(again, org, staged["id"])["status"] == "backlog"
    # Moving it out by hand releases the hold.
    move(again, org, staged["id"], "todo")
    assert ticket(again, org, staged["id"])["keepInBacklog"] is False
    again.close()


def test_a_worker_moving_its_ticket_back_keeps_it_there_too(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    staged = backlog(manager, org, "Staged")
    act(manager, org, "control", state="running")
    state = manager.store.get("", org["id"])
    manager._pull_backlog(org["projectRoot"], org["id"], state, datetime.now(UTC))
    current = ticket(manager, org, staged["id"])
    as_worker(
        manager,
        org,
        "task_update",
        taskId=staged["id"],
        expectedRevision=current["revision"],
        changes={"status": "backlog"},
    )
    assert planned(manager, org) == []
    manager.close()


def test_a_ticket_created_in_backlog_is_not_held(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    staged = backlog(manager, org, "Staged")
    assert ticket(manager, org, staged["id"])["keepInBacklog"] is False
    # Rem's staged tickets are pulled too once accepted.
    rem = act(manager, org, "task_create", "Rem:thread", title="From Rem", status="backlog")
    assert rem.get("needsUserConsent") is not True
    act(manager, org, "control", state="running")
    assert set(planned(manager, org)) <= {"Staged", "From Rem"}
    manager.close()


# ---------------------------------------------------------------------- the setting


def test_turning_pulling_off_leaves_the_backlog_alone(tmp_path: Path) -> None:
    manager = start(tmp_path)
    org = company(manager, tmp_path)
    staged = backlog(manager, org, "Staged")
    saved = operations(manager, org, pullBacklog=False)
    assert saved
    history = act(manager, org, "history")
    assert "Workers leave Backlog tickets for you to move" in str(history)
    act(manager, org, "control", state="running")
    assert planned(manager, org) == []
    assert read(manager, org)["diagnostics"]["taskReasons"][staged["id"]] == "backlog"
    for _ in range(2):
        manager.dispatch()
        settle(manager)
    assert ticket(manager, org, staged["id"])["status"] == "backlog"
    with pytest.raises(ValueError, match="Say whether workers pull Backlog tickets"):
        operations(manager, org, pullBacklog="yes")
    # Turning it back on from Rem needs the user's consent; turning it off doesn't.
    asked = operations(manager, org, "Rem:thread", pullBacklog=True)
    assert asked["needsUserConsent"] is True
    assert "pull Backlog tickets" in asked["summary"]
    assert read(manager, org)["config"]["pullBacklog"] is False
    operations(manager, org, pullBacklog=True)
    off = operations(manager, org, "Rem:thread", pullBacklog=False)
    assert not (isinstance(off, dict) and off.get("needsUserConsent"))
    assert read(manager, org)["config"]["pullBacklog"] is False
    manager.close()
