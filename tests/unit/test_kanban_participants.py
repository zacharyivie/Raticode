"""Participants on the global Kanban and the project boundaries of self-work."""

import shutil
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organization_store import Task
from gofer.ui.organizations import CHECKIN_OPENING, HEARTBEAT_TITLE, OrganizationManager
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_kanban import KanbanRefused
from tests.unit.test_device_application import application as application
from tests.unit.test_device_desktop_control import phone as phone
from tests.unit.test_organization_hours_heartbeats import (
    act,
    checkins,
    company,
    due_now,
    read,
    settle,
    start,
)
from tests.unit.test_rem_kanban import (
    add,
    at,
    ranked,
    set_options,
    start_new_session,
    tick,
    ticket,
    ticket_or_none,
    wait_for,
)
from tests.unit.test_rem_kanban import api_server as api_server
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_kanban import linked as linked
from tests.unit.test_rem_kanban import org as org
from tests.unit.test_rem_kanban import (
    settle as settle_tickets,
)


def participant(home: RemHome, identity: str = "rem") -> Any:
    return next(p for p in home.kanban.board()["participants"] if p["participant_id"] == identity)


def assign(home: RemHome, *roots: Any, identity: str = "rem") -> None:
    home.kanban.set_participant_projects(
        "user",
        {
            "participant_id": identity,
            "projects": [str(root) for root in roots],
            "expected_revision": participant(home, identity)["revision"],
        },
    )


def second_project(home: RemHome, project: Path, name: str = "other") -> Path:
    """Open another project that Rem is not assigned to."""
    folder = project.parent / name
    folder.mkdir()
    with home.store.read() as db:
        projects = list(home.store.setting(db, "config")["projects"])
    home.configure(
        {
            "provider": "codex",
            "model": "cli-default",
            "projects": [*projects, {"root": str(folder), "name": name}],
        }
    )
    return folder


def worker_titles(fake: Any) -> list[str]:
    return [
        c["workflow"]["chatThreadTitle"].removeprefix("Rem task: ")
        for c in fake.calls
        if "independent read-only reviewer" not in c["agent_instructions"]
    ]


# Who is on the board --------------------------------------------------------------


def test_the_board_lists_rem_and_every_organization_agent_as_participants(board, org):
    home, _, project = board
    manager, oid = org
    view = home.kanban.board()
    people = {p["participant_id"]: p for p in view["participants"]}
    assert set(people) == {"rem", f"agent:{oid}:lead"}
    assert people["rem"]["kind"] == "rem" and people["rem"]["projects"] == [str(project)]
    agent = people[f"agent:{oid}:lead"]
    assert agent["kind"] == "agent" and agent["title"] == "CTO"
    assert agent["organization_name"] == "Eng" and agent["projects"] == []
    assert agent["assignable"] == manager.store.get("", oid)["config"]["projectRoots"]
    catalog = {p["root"]: p for p in view["projects"]}
    assert catalog[str(project)]["available"] is True
    assert catalog[str(project)]["organization_name"] == "Eng"


def test_an_agent_can_only_get_its_own_organizations_projects(board, org, tmp_path):
    home, _, project = board
    _, oid = org
    identity = f"agent:{oid}:lead"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ValueError, match="this organization's projects"):
        assign(home, elsewhere, identity=identity)
    assert participant(home, identity)["projects"] == []
    assign(home, project, identity=identity)
    assert participant(home, identity)["projects"] == [str(project)]


def test_workspace_grants_limit_what_an_agent_can_get(tmp_path: Path):
    manager = start(tmp_path)
    grants = tmp_path / "granted"
    grants.mkdir()
    made = company(
        manager,
        tmp_path,
        employees=[{"id": "lead", "name": "Avery", "workspacePaths": [str(grants)]}],
    )
    with pytest.raises(ValueError, match="workspace access"):
        act(
            manager,
            made,
            "self_work_projects",
            employeeId="lead",
            projects=[made["projectRoot"]],
            expectedRevision=0,
        )
    manager.close()


def test_only_the_user_can_change_assignments(board, org):
    home, _, project = board
    manager, oid = org
    with pytest.raises(KanbanRefused) as refused:
        home.kanban.set_participant_projects(
            "rem", {"participant_id": "rem", "projects": [], "expected_revision": 1}
        )
    assert refused.value.code == "unauthorized"
    assert participant(home)["projects"] == [str(project)]
    with pytest.raises(ValueError, match="Only the user"):
        manager.call(
            "",
            "Rem:kanban",
            {
                "action": "self_work_projects",
                "organizationId": oid,
                "params": {"employeeId": "lead", "projects": [], "expectedRevision": 0},
            },
        )


def test_assignments_check_their_revision_and_only_accept_open_projects(board, tmp_path):
    home, _, project = board
    with pytest.raises(KanbanRefused, match="changed"):
        home.kanban.set_participant_projects(
            "user", {"participant_id": "rem", "projects": [], "expected_revision": 0}
        )
    closed = tmp_path / "closed"
    closed.mkdir()
    with pytest.raises(KanbanRefused, match="open projects"):
        assign(home, project, closed)
    with pytest.raises(KanbanRefused):
        assign(home, project, project)
    assert participant(home)["projects"] == [str(project)]


def test_assignments_survive_a_restart_and_clearing_history(board):
    home, _, project = board
    other = RemHome(
        home.data_dir,
        source=home.source,
        jobs=home.jobs,
        steering=home.steering,
        authorize=lambda c, r: None,
        generate=home.generate,
        start=False,
    )
    assert participant(other)["projects"] == [str(project)]
    conversation = home.store.conversation()["conversation"]
    home.clear_history(
        {"request_id": "reset", "conversation_id": conversation["id"], "confirm": True}
    )
    assert participant(home)["projects"] == [str(project)]


# Rem's self-work --------------------------------------------------------------------


def test_with_no_project_assigned_rem_does_no_self_work(board):
    home, fake, project = board
    add(home, "Waiting")
    assign(home)
    at(home, 15)
    tick(home)
    tick(home)
    assert ticket(home, "Waiting")["status"] == "todo" and not fake.calls
    state = home.self_work.view()["state"]
    assert state["mode"] == "waiting" and "Settings > Rem" in state["detail"]
    assert state["assigned_projects"] == 0 and state["outside_projects"] == 1
    assert ticket(home, "Waiting")["self_work_scope"] == "unassigned"
    assign(home, project)
    at(home, 16)
    tick(home)
    assert ticket(home, "Waiting")["running"]
    settle_tickets(home, "Waiting")


def test_todos_outside_rems_projects_wait_and_hold_nothing_back(board):
    home, fake, project = board
    other = second_project(home, project)
    add(home, "Elsewhere", project_dir=str(other))
    add(home, "No folder", project_dir=None)
    add(home, "Inside")
    at(home, 15)
    tick(home)
    settle_tickets(home, "Inside")
    assert ticket(home, "Inside")["status"] == "done"
    for title, scope in (("Elsewhere", "unassigned"), ("No folder", "no_project")):
        current = ticket(home, title)
        assert current["status"] == "todo" and not current["running"]
        assert current["self_work_scope"] == scope
    # With the assigned project's to-dos done, research goes on there and only there.
    wait_for(lambda: not tick(home) and not home.self_work.view()["state"]["active_threads"], 8)
    research = [t for t in worker_titles(fake) if "Research" in t]
    assert len(research) == 3 and all(
        t.endswith(" in project") or " for project" in t for t in research
    )
    assert "Elsewhere" not in worker_titles(fake) and "No folder" not in worker_titles(fake)


def test_auto_pull_takes_backlog_only_from_assigned_projects(board):
    home, fake, project = board
    other = second_project(home, project)
    set_options(home, auto_pull_backlog=True)
    add(home, "Pull me", status="backlog")
    ranked(home, "Pull me")
    add(home, "Not mine", status="backlog", project_dir=str(other))
    at(home, 15)
    tick(home)
    settle_tickets(home, "Pull me")
    assert ticket(home, "Not mine")["status"] == "backlog"
    assert "Not mine" not in worker_titles(fake)


def test_research_and_its_board_stay_inside_the_assigned_project(board):
    home, fake, project = board
    other = second_project(home, project)
    add(home, "Secret plan", project_dir=str(other), status="backlog")
    add(home, "Visible plan", status="backlog")
    at(home, 15)
    tick(home)
    wait_for(lambda: not tick(home) and not home.self_work.view()["state"]["active_threads"], 8)
    research = [
        c
        for c in fake.calls
        if "Research" in c["workflow"]["chatThreadTitle"]
        and "independent read-only reviewer" not in c["agent_instructions"]
    ]
    assert research and all(c["workflow"]["projectName"] == "project" for c in research)
    for call in research:
        prompt = " ".join(str(m["body"]) for m in call["messages"])
        assert "Visible plan" in prompt and "Secret plan" not in prompt


def test_duplicates_are_only_merged_within_one_assigned_project(board):
    home, _, project = board
    other = second_project(home, project)
    add(home, "Dark mode", status="backlog")
    add(home, "Support dark mode", status="backlog", project_dir=str(other))
    add(home, "Dark theme toggle", status="backlog", project_dir=str(other))
    add(home, "Add dark theme toggle", status="backlog", project_dir=str(other))
    at(home, 15)
    tick(home)
    assert ticket(home, "Support dark mode")["status"] == "backlog"
    assert ticket(home, "Add dark theme toggle")["status"] == "backlog"
    assign(home, project, other)
    start_new_session(home, 20)
    # Now both are in scope: the same title in different projects is still separate work.
    assert ticket(home, "Support dark mode")["status"] == "backlog"
    assert ticket(home, "Add dark theme toggle")["status"] == "cancelled"


def test_an_assigned_project_that_is_gone_is_skipped_not_started(board):
    home, fake, project = board
    other = second_project(home, project)
    assign(home, project, other)
    add(home, "In gone", project_dir=str(other))
    shutil.rmtree(other)
    at(home, 15)
    tick(home)
    tick(home)
    assert ticket(home, "In gone")["status"] == "todo"
    assert ticket(home, "In gone")["self_work_scope"] == "unavailable"
    assert "In gone" not in worker_titles(fake)
    assert participant(home)["unavailable"] == [str(other)]


def test_a_project_closed_in_raticode_stops_counting(board):
    home, fake, project = board
    other = second_project(home, project)
    assign(home, project, other)
    add(home, "In closed", project_dir=str(other))
    home.configure(
        {
            "provider": "codex",
            "model": "cli-default",
            "projects": [{"root": str(project), "name": "project"}],
        }
    )
    at(home, 15)
    tick(home)
    tick(home)
    assert ticket(home, "In closed")["self_work_scope"] == "unavailable"
    assert "In closed" not in worker_titles(fake)


def test_taking_a_project_away_stops_self_started_work_only(board):
    home, fake, project = board
    fake.hold.clear()
    other = second_project(home, project)
    add(home, "Asked for", project_dir=str(other))
    asked = ticket(home, "Asked for")
    home.kanban.move(
        "user",
        {
            "ticket_id": asked["ticket_id"],
            "expected_revision": asked["revision"],
            "column": "in_progress",
        },
    )
    add(home, "On its own")
    at(home, 15)
    tick(home)
    assert ticket(home, "On its own")["running"] and ticket(home, "Asked for")["running"]
    assign(home)
    fake.hold.set()
    settle_tickets(home, "On its own", "Asked for")
    stopped = ticket(home, "On its own")
    assert stopped["status"] == "blocked" and "no longer assigned" in stopped["block_reason"]
    assert ticket(home, "Asked for")["status"] == "done"


def test_a_project_taken_away_just_before_the_claim_is_refused(board):
    home, _, _ = board
    created = add(home, "Race")
    with home.store.read() as db:
        config = home.store.setting(db, "config")
    assign(home)
    assert home.self_work._begin_ticket(created["id"], config) is False
    assert ticket(home, "Race")["status"] == "todo"


def test_research_results_are_discarded_if_the_project_was_taken_away(board):
    from tests.unit.test_rem_kanban import PROPOSAL

    home, fake, project = board
    fake.hold.clear()
    set_options(home, max_threads=1)
    fake.answers["Research new features for project"] = PROPOSAL
    at(home, 15)
    tick(home)
    assert home.self_work.view()["state"]["active_threads"] == 1
    with home.store.read() as db:
        record = home.store.setting(db, "self_work_state")["passes"][0]
    # The run finishes before the boundary check can stop it.
    assert home._finish(record["run_id"], "succeeded", "Done")
    with home.store.connect() as db:
        run = home.store.get(db, "runs", record["run_id"])
        run["worker_result"] = PROPOSAL
        home.store.put(db, "runs", run)
    assign(home)
    home.self_work.reconcile()
    fake.hold.set()
    assert ticket_or_none(home, "Voice shortcuts") is None
    notes = [n["text"] for n in home.self_work.view()["report"]["notes"]]
    assert any("no longer assigned" in n for n in notes)


def test_a_research_worker_cannot_touch_tickets_in_another_project(board):
    home, fake, project = board
    other = second_project(home, project)
    home_ticket = add(home, "Mine", status="backlog")
    outside = add(home, "Theirs", status="backlog", project_dir=str(other))
    from gofer.ui.rem_kanban import put_ticket

    with home.store.connect() as db:
        put_ticket(
            db,
            {
                **outside,
                "created_by": "self_work",
                "proposal": {"state": "pending"},
                "revision": 1,
            },
        )
    fake.answers["Research new features for project"] = (
        '```json\n{"proposals":[],"duplicates":['
        f'{{"ticket_id":"{outside["id"]}","duplicate_of":"{home_ticket["id"]}","reason":"Same"}}'
        "]}\n```"
    )
    at(home, 15)
    tick(home)
    wait_for(lambda: not tick(home) and not home.self_work.view()["state"]["active_threads"], 8)
    assert ticket(home, "Theirs")["status"] == "backlog"


def test_explicit_requests_work_without_an_assignment(board):
    home, _, project = board
    assign(home)
    add(home, "Do this now", project_dir=str(project))
    created = ticket(home, "Do this now")
    home.kanban.move(
        "user",
        {
            "ticket_id": created["ticket_id"],
            "expected_revision": created["revision"],
            "column": "in_progress",
        },
    )
    assert ticket(home, "Do this now")["running"]
    settle_tickets(home, "Do this now")
    assert ticket(home, "Do this now")["status"] == "done"


# Organization agents ------------------------------------------------------------------


def lead_org(manager: OrganizationManager, tmp_path: Path) -> Any:
    return company(
        manager, tmp_path, employees=[{"id": "lead", "name": "Avery", "heartbeatSeconds": 900}]
    )


def give_lead(manager: OrganizationManager, made: dict[str, Any], revision: int = 0) -> None:
    act(
        manager,
        made,
        "self_work_projects",
        employeeId="lead",
        projects=[made["projectRoot"]],
        expectedRevision=revision,
    )


def test_an_unassigned_agent_gets_no_check_ins(tmp_path: Path):
    manager = start(tmp_path)
    made = lead_org(manager, tmp_path)
    act(manager, made, "control", state="running")
    manager.dispatch()
    manager.dispatch()
    settle(manager)
    assert checkins(manager, made) == []
    manager.close()


def test_an_assigned_agents_check_in_runs_in_its_project(tmp_path: Path):
    manager = start(tmp_path)
    made = lead_org(manager, tmp_path)
    give_lead(manager, made)
    act(manager, made, "control", state="running")
    manager.dispatch()
    manager.dispatch()
    settle(manager)
    state = read(manager, made)
    assert checkins(manager, made) == ["done"]
    checkin = next(t for t in state["runtime"]["tasks"] if t["heartbeat"])
    assert checkin["workspacePath"] == made["projectRoot"]
    assert "Work only in these assigned projects" in checkin["description"]
    manager.close()


def test_losing_the_last_project_closes_a_waiting_check_in(tmp_path: Path):
    manager = start(tmp_path)
    made = lead_org(manager, tmp_path)
    give_lead(manager, made)
    act(manager, made, "control", state="running")
    manager.dispatch()
    manager.dispatch()
    settle(manager)
    act(
        manager,
        made,
        "self_work_projects",
        employeeId="lead",
        projects=[],
        expectedRevision=1,
    )
    due_now(manager, made)
    manager.dispatch()
    settle(manager)
    assert checkins(manager, made) == ["done"]
    manager.close()


def self_run(manager: OrganizationManager, made: dict[str, Any], **task: Any) -> str:
    """A running check-in for the lead, without starting a provider."""

    def seed(current: dict[str, Any]) -> dict[str, Any]:
        current["runtime"]["state"] = "running"
        current["runtime"]["tasks"].append(
            Task(
                title="Check-in",
                assignee="lead",
                heartbeat=True,
                status="in_progress",
                claim="run-1",
                workspacePath=made["projectRoot"],
                **task,
            ).model_dump()
        )
        return {"seeded": True}

    manager.store.mutate("", made["id"], "system", "test", seed)
    return "run-1"


def as_lead(
    manager: OrganizationManager, made: dict[str, Any], action: str, run: str, **params: Any
) -> Any:
    state = manager.store.get("", made["id"])
    return manager.call(
        made["projectRoot"],
        "employee:lead",
        {"organizationId": made["id"], "action": action, "params": params},
        employee_id="lead",
        bound_org=made["id"],
        generation=state["runtime"]["generation"],
        run_id=run,
    )


def test_a_check_in_can_only_propose_inside_assigned_projects(tmp_path: Path):
    manager = start(tmp_path)
    made = lead_org(manager, tmp_path)
    run = self_run(manager, made)
    with pytest.raises(ValueError, match="aren't assigned"):
        as_lead(manager, made, "task_create", run, title="Idea", status="backlog")
    give_lead(manager, made)
    created = as_lead(manager, made, "task_create", run, title="Idea", status="backlog")
    saved = next(t for t in read(manager, made)["runtime"]["tasks"] if t["id"] == created["id"])
    assert saved["selfWork"] is True and saved["workspacePath"] == made["projectRoot"]
    manager.close()


def test_a_check_in_cannot_pull_a_ticket_outside_its_projects(tmp_path: Path):
    manager = start(tmp_path)
    made = lead_org(manager, tmp_path)
    open_ticket = act(manager, made, "task_create", title="Unowned", status="backlog")
    run = self_run(manager, made)
    with pytest.raises(ValueError, match="aren't assigned"):
        as_lead(
            manager,
            made,
            "task_update",
            run,
            taskId=open_ticket["id"],
            expectedRevision=open_ticket["revision"],
            changes={"assignee": "lead", "status": "in_progress"},
        )
    manager.close()


def test_explicitly_assigned_work_is_not_bound_by_assignments(tmp_path: Path):
    manager = start(tmp_path)
    made = lead_org(manager, tmp_path)
    explicit = act(manager, made, "task_create", title="Requested", assignee="lead")
    act(manager, made, "control", state="running")
    manager.dispatch()
    settle(manager)
    state = next(t for t in read(manager, made)["runtime"]["tasks"] if t["id"] == explicit["id"])
    assert state["status"] != "todo" and state["status"] != "blocked"
    manager.close()


def test_a_worker_made_ticket_outside_the_assignees_projects_is_blocked(tmp_path: Path):
    manager = start(tmp_path)
    made = lead_org(manager, tmp_path)

    def seed(current: dict[str, Any]) -> dict[str, Any]:
        current["runtime"]["tasks"].append(
            Task(title="Planned on its own", assignee="lead", selfWork=True).model_dump()
        )
        return {"seeded": True}

    manager.store.mutate("", made["id"], "system", "test", seed)
    act(manager, made, "control", state="running")
    manager.dispatch()
    planned = next(
        t for t in read(manager, made)["runtime"]["tasks"] if t["title"] == "Planned on its own"
    )
    assert planned["status"] == "blocked" and "isn't assigned" in planned["blockReason"]
    manager.close()


def two_root_org(manager: OrganizationManager, tmp_path: Path) -> tuple[Any, str]:
    """An organization with two projects, its lead assigned only to the first."""
    outside = tmp_path / "unassigned"
    outside.mkdir()
    made = company(
        manager,
        tmp_path,
        employees=[{"id": "lead", "name": "Avery"}],
        projectRoots=[str(tmp_path), str(outside)],
        projects=[{"id": "far", "name": "Far", "workspacePath": str(outside)}],
    )
    give_lead(manager, made)
    return made, str(outside.resolve())


@pytest.mark.parametrize("repoint", ["workspace", "initiative"])
def test_a_check_in_cannot_repoint_a_ticket_outside_its_projects(tmp_path: Path, repoint: str):
    """Regression: only a ticket's old folder was checked, so a new one could be set and run."""
    manager = start(tmp_path)
    made, outside = two_root_org(manager, tmp_path)
    ticket_made = act(
        manager,
        made,
        "task_create",
        title="Requested",
        assignee="lead",
        status="backlog",
        workspacePath=made["projectRoot"],
    )
    run = self_run(manager, made)
    target: dict[str, Any] = (
        {"workspacePath": outside}
        if repoint == "workspace"
        else {"workspacePath": None, "project": "far"}
    )

    def saved() -> Any:
        return next(
            t for t in read(manager, made)["runtime"]["tasks"] if t["id"] == ticket_made["id"]
        )

    with pytest.raises(ValueError, match="aren't assigned"):
        as_lead(
            manager,
            made,
            "task_update",
            run,
            taskId=ticket_made["id"],
            expectedRevision=ticket_made["revision"],
            changes={**target, "status": "todo"},
        )
    assert saved()["status"] == "backlog" and saved()["workspacePath"] == made["projectRoot"]
    manager.dispatch()
    settle(manager)
    state = read(manager, made)["runtime"]
    assert saved()["turns"] == 0
    assert not any(a["workspacePath"] == outside for a in state.get("attempts", {}).values())
    manager.close()


def test_a_check_in_can_still_move_a_ticket_within_its_projects(tmp_path: Path):
    manager = start(tmp_path)
    made, _ = two_root_org(manager, tmp_path)
    ticket_made = act(
        manager,
        made,
        "task_create",
        title="Requested",
        assignee="lead",
        status="backlog",
        workspacePath=made["projectRoot"],
    )
    run = self_run(manager, made)
    as_lead(
        manager,
        made,
        "task_update",
        run,
        taskId=ticket_made["id"],
        expectedRevision=ticket_made["revision"],
        changes={"status": "todo"},
    )
    saved = next(t for t in read(manager, made)["runtime"]["tasks"] if t["id"] == ticket_made["id"])
    assert saved["status"] == "todo"
    manager.close()


def test_a_check_in_saved_before_the_flag_still_needs_a_project(tmp_path: Path):
    manager = start(tmp_path)
    made = lead_org(manager, tmp_path)

    def seed(current: dict[str, Any]) -> dict[str, Any]:
        current["runtime"]["tasks"].append(
            Task(
                title=HEARTBEAT_TITLE,
                description=f"{CHECKIN_OPENING} Plan follow-up work.",
                assignee="lead",
            ).model_dump()
        )
        return {"seeded": True}

    manager.store.mutate("", made["id"], "system", "test", seed)
    act(manager, made, "control", state="running")
    manager.dispatch()
    settle(manager)
    old = next(t for t in read(manager, made)["runtime"]["tasks"] if t["title"] == HEARTBEAT_TITLE)
    assert old["status"] == "blocked" and old["turns"] == 0
    manager.close()


def two_worker_org(manager: OrganizationManager, tmp_path: Path) -> Any:
    """Lead has the project; Bea reports to Lead and has no assignment."""
    made = company(
        manager,
        tmp_path,
        employees=[
            {"id": "lead", "name": "Avery"},
            {"id": "bea", "name": "Bea", "reportsTo": "lead"},
        ],
    )
    give_lead(manager, made)
    return made


def saved_ticket(manager: OrganizationManager, made: dict[str, Any], identity: str) -> Any:
    return next(t for t in read(manager, made)["runtime"]["tasks"] if t["id"] == identity)


def test_a_check_in_cannot_hand_requested_work_to_an_unassigned_worker(tmp_path: Path):
    """Regression: the ticket ran for the new assignee in a project it wasn't assigned to."""
    manager = start(tmp_path)
    made = two_worker_org(manager, tmp_path)
    requested = act(manager, made, "task_create", title="Req", assignee="lead", status="backlog")
    run = self_run(manager, made)
    with pytest.raises(ValueError, match="Bea isn't assigned"):
        as_lead(
            manager,
            made,
            "task_update",
            run,
            taskId=requested["id"],
            expectedRevision=requested["revision"],
            changes={"assignee": "bea", "status": "todo"},
        )
    manager.dispatch()
    settle(manager)
    current = saved_ticket(manager, made, requested["id"])
    assert current["assignee"] == "lead" and current["status"] == "backlog"
    assert current["turns"] == 0
    manager.close()


def test_a_check_in_cannot_hand_over_a_ticket_it_just_took(tmp_path: Path):
    manager = start(tmp_path)
    made = two_worker_org(manager, tmp_path)
    loose = act(manager, made, "task_create", title="Loose", status="backlog")
    run = self_run(manager, made)
    as_lead(
        manager,
        made,
        "task_update",
        run,
        taskId=loose["id"],
        expectedRevision=loose["revision"],
        changes={"assignee": "lead"},
    )
    taken = saved_ticket(manager, made, loose["id"])
    assert taken["selfWork"] is True
    with pytest.raises(ValueError, match="Bea isn't assigned"):
        as_lead(
            manager,
            made,
            "task_update",
            run,
            taskId=loose["id"],
            expectedRevision=taken["revision"],
            changes={"assignee": "bea", "status": "todo"},
        )
    manager.dispatch()
    settle(manager)
    current = saved_ticket(manager, made, loose["id"])
    assert current["assignee"] == "lead" and current["turns"] == 0
    manager.close()


def test_a_check_in_can_hand_work_to_an_assigned_worker(tmp_path: Path):
    manager = start(tmp_path)
    made = two_worker_org(manager, tmp_path)
    act(
        manager,
        made,
        "self_work_projects",
        employeeId="bea",
        projects=[made["projectRoot"]],
        expectedRevision=0,
    )
    requested = act(manager, made, "task_create", title="Req", assignee="lead", status="backlog")
    run = self_run(manager, made)
    as_lead(
        manager,
        made,
        "task_update",
        run,
        taskId=requested["id"],
        expectedRevision=requested["revision"],
        changes={"assignee": "bea", "status": "todo"},
        comment="Bea owns this part of the project and is free today.",
    )
    current = saved_ticket(manager, made, requested["id"])
    assert current["assignee"] == "bea" and current["selfWork"] is True
    # The reason is on the ticket, in the lead's name.
    note = next(c for c in current["comments"] if c.get("kind") == "reassigned")
    assert note["actor"] == "employee:lead" and note["to"] == "bea"
    assert note["body"] == "Reassigned to Bea: Bea owns this part of the project and is free today."
    manager.close()


@pytest.mark.parametrize("column", ["todo", "in_progress"])
def test_a_ticket_a_check_in_pulls_from_backlog_is_self_work(tmp_path: Path, column: str):
    manager = start(tmp_path)
    made = two_worker_org(manager, tmp_path)
    requested = act(manager, made, "task_create", title="Req", assignee="lead", status="backlog")
    run = self_run(manager, made)
    as_lead(
        manager,
        made,
        "task_update",
        run,
        taskId=requested["id"],
        expectedRevision=requested["revision"],
        changes={"status": column},
    )
    assert saved_ticket(manager, made, requested["id"])["selfWork"] is True
    manager.close()


def test_requested_work_a_check_in_only_updates_stays_requested(tmp_path: Path):
    manager = start(tmp_path)
    made = two_worker_org(manager, tmp_path)
    requested = act(manager, made, "task_create", title="Req", assignee="lead")
    run = self_run(manager, made)
    as_lead(
        manager,
        made,
        "task_update",
        run,
        taskId=requested["id"],
        expectedRevision=requested["revision"],
        changes={"priority": "high"},
    )
    current = saved_ticket(manager, made, requested["id"])
    assert current["priority"] == "high" and current["selfWork"] is False
    manager.close()


@pytest.mark.parametrize("how", ["reopen", "unblock"])
def test_a_check_in_restarting_a_blocked_ticket_stays_within_the_assignees_projects(
    tmp_path: Path, how: str
):
    manager = start(tmp_path)
    made = two_worker_org(manager, tmp_path)
    created = act(manager, made, "task_create", title="Stuck", assignee="bea")

    def block(current: dict[str, Any]) -> dict[str, Any]:
        task = next(t for t in current["runtime"]["tasks"] if t["id"] == created["id"])
        task.update(status="blocked", blockReason="dependencies")
        return {"seeded": True}

    manager.store.mutate("", made["id"], "system", "test", block)
    run = self_run(manager, made)
    if how == "reopen":
        as_lead(
            manager,
            made,
            "reopen",
            run,
            taskId=created["id"],
            expectedRevision=created["revision"],
        )
    else:
        as_lead(manager, made, "comment", run, taskId=created["id"], body="Go", unblock=True)
    assert saved_ticket(manager, made, created["id"])["selfWork"] is True
    manager.dispatch()
    settle(manager)
    current = saved_ticket(manager, made, created["id"])
    assert current["status"] == "blocked" and current["turns"] == 0
    assert "isn't assigned" in current["blockReason"]
    manager.close()


def set_ticket(
    manager: OrganizationManager, made: dict[str, Any], identity: str, **fields: Any
) -> Any:
    """Put a ticket in a state a real run would have left it in."""

    def change(current: dict[str, Any]) -> dict[str, Any]:
        task = next(t for t in current["runtime"]["tasks"] if t["id"] == identity)
        task.update(fields)
        return {"seeded": True}

    manager.store.mutate("", made["id"], "system", "test", change)
    return saved_ticket(manager, made, identity)


@pytest.mark.parametrize("status", ["done", "in_review"])
def test_a_check_in_restarting_finished_work_stays_within_the_assignees_projects(
    tmp_path: Path, status: str
):
    """Regression: a done or in-review ticket went back to To do and ran for its assignee."""
    manager = start(tmp_path)
    made = two_worker_org(manager, tmp_path)
    created = act(manager, made, "task_create", title="Finished", assignee="bea")
    finished = set_ticket(manager, made, created["id"], status=status, evidence="Did it")
    run = self_run(manager, made)
    as_lead(
        manager,
        made,
        "task_update",
        run,
        taskId=created["id"],
        expectedRevision=finished["revision"],
        changes={"status": "todo"},
    )
    assert saved_ticket(manager, made, created["id"])["selfWork"] is True
    manager.dispatch()
    settle(manager)
    current = saved_ticket(manager, made, created["id"])
    assert current["status"] == "blocked" and current["turns"] == 0
    assert "isn't assigned" in current["blockReason"]
    assert not any(
        a["taskId"] == created["id"] for a in read(manager, made)["runtime"]["attempts"].values()
    )
    manager.close()


def test_a_reviewing_check_in_cannot_send_work_back_outside_the_assignees_projects(
    tmp_path: Path,
):
    manager = start(tmp_path)
    made = two_worker_org(manager, tmp_path)
    created = act(manager, made, "task_create", title="Review me", assignee="bea", reviewer="lead")
    waiting = set_ticket(manager, made, created["id"], status="in_review")
    run = self_run(manager, made)
    as_lead(
        manager,
        made,
        "review",
        run,
        taskId=created["id"],
        expectedRevision=waiting["revision"],
        decision="revise",
        body="Try again",
    )
    assert saved_ticket(manager, made, created["id"])["selfWork"] is True
    manager.dispatch()
    settle(manager)
    current = saved_ticket(manager, made, created["id"])
    assert current["status"] == "blocked" and current["turns"] == 0
    manager.close()


def split_worker_org(manager: OrganizationManager, tmp_path: Path) -> tuple[Any, str]:
    """Lead has both projects; Bea, who reports to Lead, has only the first."""
    outside = tmp_path / "unassigned"
    outside.mkdir()
    second = str(outside.resolve())
    made = company(
        manager,
        tmp_path,
        employees=[
            {"id": "lead", "name": "Avery"},
            {"id": "bea", "name": "Bea", "reportsTo": "lead"},
        ],
        projectRoots=[str(tmp_path), second],
        projects=[{"id": "far", "name": "Far", "workspacePath": second}],
    )
    for worker, roots in (("lead", [made["projectRoot"], second]), ("bea", [made["projectRoot"]])):
        act(
            manager,
            made,
            "self_work_projects",
            employeeId=worker,
            projects=roots,
            expectedRevision=0,
        )
    return made, second


@pytest.mark.parametrize("repoint", ["workspace", "initiative"])
def test_a_check_in_cannot_move_a_subordinates_ticket_to_a_project_they_lack(
    tmp_path: Path, repoint: str
):
    """Regression: only a change of assignee was checked, so a change of folder slipped by."""
    manager = start(tmp_path)
    made, second = split_worker_org(manager, tmp_path)
    created = act(
        manager,
        made,
        "task_create",
        title="Bea's",
        assignee="bea",
        workspacePath=made["projectRoot"],
    )
    run = self_run(manager, made)
    with pytest.raises(ValueError, match="Bea isn't assigned"):
        as_lead(
            manager,
            made,
            "task_update",
            run,
            taskId=created["id"],
            expectedRevision=created["revision"],
            changes=(
                {"workspacePath": second}
                if repoint == "workspace"
                else {"workspacePath": None, "project": "far"}
            ),
        )
    assert saved_ticket(manager, made, created["id"])["workspacePath"] == made["projectRoot"]
    manager.dispatch()
    settle(manager)
    assert not any(
        a["workspacePath"] == second for a in read(manager, made)["runtime"]["attempts"].values()
    )
    manager.close()


def test_a_check_in_can_move_a_ticket_to_a_project_its_worker_has(tmp_path: Path):
    manager = start(tmp_path)
    made, second = split_worker_org(manager, tmp_path)
    act(
        manager,
        made,
        "self_work_projects",
        employeeId="bea",
        projects=[made["projectRoot"], second],
        expectedRevision=1,
    )
    created = act(manager, made, "task_create", title="Bea's", assignee="bea", status="backlog")
    run = self_run(manager, made)
    as_lead(
        manager,
        made,
        "task_update",
        run,
        taskId=created["id"],
        expectedRevision=created["revision"],
        changes={"workspacePath": second},
    )
    assert saved_ticket(manager, made, created["id"])["workspacePath"] == second
    manager.close()


def test_a_check_in_can_still_tidy_a_ticket_without_moving_it(tmp_path: Path):
    """An assignee with no projects keeps requested work; updating it changes nothing."""
    manager = start(tmp_path)
    made = two_worker_org(manager, tmp_path)
    requested = act(manager, made, "task_create", title="Req", assignee="bea")
    run = self_run(manager, made)
    as_lead(
        manager,
        made,
        "task_update",
        run,
        taskId=requested["id"],
        expectedRevision=requested["revision"],
        changes={"priority": "high"},
    )
    current = saved_ticket(manager, made, requested["id"])
    assert current["priority"] == "high" and current["selfWork"] is False
    manager.close()


@pytest.mark.parametrize("status", ["done", "in_review"])
def test_the_user_can_move_a_workers_ticket_but_it_runs_only_in_their_projects(
    tmp_path: Path, status: str
):
    """Workers are bounded to their assigned projects, even for work the user gives them."""
    manager = start(tmp_path)
    made, second = split_worker_org(manager, tmp_path)
    created = act(manager, made, "task_create", title="Bea's", assignee="bea", status="backlog")
    finished = set_ticket(manager, made, created["id"], status=status, evidence="Did it")
    act(
        manager,
        made,
        "task_update",
        taskId=created["id"],
        expectedRevision=finished["revision"],
        changes={"status": "todo", "workspacePath": second},
    )
    act(manager, made, "control", state="running")
    manager.dispatch()
    settle(manager)
    current = saved_ticket(manager, made, created["id"])
    assert current["selfWork"] is False and current["turns"] == 0
    assert current["workspacePath"] == second
    assert current["status"] == "blocked"
    assert current["blockReason"] == (
        "Bea isn't assigned to unassigned. Assign them to it, or give the ticket to someone who is."
    )
    manager.close()


def test_the_user_can_still_reassign_to_a_worker_with_no_projects(tmp_path: Path):
    manager = start(tmp_path)
    made = two_worker_org(manager, tmp_path)
    requested = act(manager, made, "task_create", title="Req", assignee="lead", status="backlog")
    act(
        manager,
        made,
        "task_update",
        taskId=requested["id"],
        expectedRevision=requested["revision"],
        changes={"assignee": "bea", "status": "todo"},
    )
    act(manager, made, "control", state="running")
    manager.dispatch()
    settle(manager)
    current = saved_ticket(manager, made, requested["id"])
    assert current["assignee"] == "bea" and current["turns"] == 1
    assert current["status"] != "blocked" and current["selfWork"] is False
    manager.close()


def test_a_user_can_take_over_a_worker_made_ticket(tmp_path: Path):
    manager = start(tmp_path)
    made = lead_org(manager, tmp_path)

    def seed(current: dict[str, Any]) -> dict[str, Any]:
        current["runtime"]["tasks"].append(
            Task(title="Planned on its own", assignee="lead", selfWork=True).model_dump()
        )
        return {"seeded": True}

    manager.store.mutate("", made["id"], "system", "test", seed)
    act(manager, made, "control", state="running")
    manager.dispatch()

    def planned() -> Any:
        return next(
            t for t in read(manager, made)["runtime"]["tasks"] if t["title"] == "Planned on its own"
        )

    assert planned()["status"] == "blocked"
    # Reopening it is the user asking for this work, so the boundary no longer applies.
    act(manager, made, "reopen", taskId=planned()["id"], expectedRevision=planned()["revision"])
    assert planned()["selfWork"] is False
    manager.dispatch()
    settle(manager)
    assert planned()["status"] not in {"todo", "blocked"}
    manager.close()


def test_a_full_set_of_outside_proposals_does_not_block_new_ones(board):
    from tests.unit.test_rem_kanban import PROPOSAL, add_proposal

    home, fake, project = board
    other = second_project(home, project)
    for number in range(12):
        add_proposal(home, f"Outside idea {number} unique{number}", project_dir=str(other))
    fake.answers["Research new features for project"] = PROPOSAL
    at(home, 15)
    tick(home)
    wait_for(lambda: not tick(home) and ticket_or_none(home, "Voice shortcuts") is not None, 8)
    assert ticket(home, "Voice shortcuts")["proposed"] is True


def test_taking_a_project_away_revokes_a_running_check_in(tmp_path: Path):
    manager = start(tmp_path)
    made = lead_org(manager, tmp_path)
    give_lead(manager, made)
    run = self_run(manager, made)
    stop = threading.Event()
    manager._active[run] = (made["id"], stop, threading.current_thread())
    try:
        act(
            manager,
            made,
            "self_work_projects",
            employeeId="lead",
            projects=[],
            expectedRevision=1,
        )
        assert stop.is_set()
    finally:
        manager._active.pop(run, None)
    manager.close()


def test_assigning_a_worker_is_recorded_and_keeps_the_organization_stopped(board, org):
    home, _, project = board
    manager, oid = org
    assign(home, project, identity=f"agent:{oid}:lead")
    stored = manager.store.get("", oid)
    assert stored["runtime"]["state"] == "paused"
    assert any(e["kind"] == "self_work_projects" for e in manager.store.events("", oid))


# The phone's interface and the desktop's ----------------------------------------------


def catalog_id(send: Any) -> str:
    return str(send("desktop.read", "overview")["value"]["projects"][0]["project_id"])


def link_project(home: RemHome, app: Any, peer: str, name: str = "") -> tuple[str, str]:
    """The phone's first catalog project, opened and assignable in Rem."""
    catalog = app.desktop.projects(peer)
    key, project = next(iter(catalog.items()))
    with home.store.read() as db:
        projects = list(home.store.setting(db, "config")["projects"])
    home.configure(
        {
            "provider": "codex",
            "model": "cli-default",
            "projects": [*projects, {"root": project["root"], "name": project["name"]}],
        }
    )
    return key, project["root"]


def test_phone_sees_participants_without_any_folder_paths(linked):
    app, peer, _, send, home, _, _, oid = linked
    assert send("desktop.read", "overview")["value"]["kanban_participants"] is True
    key, root = link_project(home, app, peer)
    value = send("desktop.read", "kanban.board")["value"]
    people = {p["participant_id"]: p for p in value["participants"]}
    assert key in people["rem"]["assignable"]
    # Rem's assigned test project isn't in this phone's catalog, so it's only counted.
    assert people["rem"]["projects"] == [] and people["rem"]["hidden_projects"] == 1
    assert people[f"agent:{oid}:lead"]["kind"] == "agent"
    assert all("root" not in p for p in value["projects"])
    assert any(p["project_id"] == key for p in value["projects"])
    assert home.data_dir.name not in str(value) and root not in str(value)


def test_phone_changes_assignments_and_keeps_projects_it_cannot_see(linked):
    app, peer, _, send, home, _, _, _ = linked
    key, root = link_project(home, app, peer)
    before = participant(home)["projects"]
    changed = send(
        "desktop.action",
        "kanban.participant",
        {"participant_id": "rem", "projects": [key], "expected_revision": 1},
    )
    assert changed["ok"], changed
    assert set(participant(home)["projects"]) == {root, *before}
    assert (
        send(
            "desktop.action",
            "kanban.participant",
            {"participant_id": "rem", "projects": [], "expected_revision": 1},
        )["code"]
        == "conflict"
    )
    assert (
        send(
            "desktop.action",
            "kanban.participant",
            {"participant_id": "rem", "projects": ["nope"], "expected_revision": 2},
        )["code"]
        == "invalid"
    )
    assert (
        send(
            "desktop.action",
            "kanban.participant",
            {"participant_id": "ghost", "projects": [], "expected_revision": 2},
        )["code"]
        == "not_found"
    )


def test_phone_ticket_reports_its_project_id(linked):
    app, peer, _, send, home, _, _, _ = linked
    key, root = link_project(home, app, peer)
    add(home, "Phone project", project_dir=root)
    value = send("desktop.read", "kanban.board")["value"]
    item = next(t for t in value["tickets"] if t["title"] == "Phone project")
    assert item["project_id"] == key and "project_dir" not in item


def test_phone_needs_its_device_permission_for_assignments(linked):
    app, peer, _, send, _, _, _, _ = linked
    app.desktop.enable(peer, False)
    assert (
        send("desktop.action", "kanban.participant", {"participant_id": "rem"})["code"]
        == "unauthorized"
    )


def test_desktop_route_requires_authentication_and_reports_conflicts(api_server):
    request, service = api_server
    body = {"participant_id": "rem", "projects": [], "expected_revision": 0}
    assert (
        request("POST", "/api/rem/kanban/participant", authenticated=False, body=body).status == 401
    )
    assert request("POST", "/api/rem/kanban/participant", body=body).status == 200
    conflict = request("POST", "/api/rem/kanban/participant", body=body)
    assert conflict.status == 409 and conflict.json()["code"] == "conflict"
