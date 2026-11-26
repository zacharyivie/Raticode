"""Run there: waiting work suggests a paired desktop with room, and only the user's pick sends it.

The suggestion comes from the placement snapshot (gofer.core.placement_suggestion). A Rem
task's pick pins its run there and makes it the user's own start; an organization
ticket's pick only sets the fleet execution target the owner already made there.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from gofer.core import placement_settings, repository_fingerprint
from gofer.core.placement_suggestion import suggest
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_models import RemConflict
from gofer.ui.rem_placement import pinned_computer, run_there_suggestion
from gofer.ui.rem_self_work import self_started
from tests.helpers.organizations import act
from tests.unit.test_organization_placement import (
    DESK,
    PROJECT_THERE,
    Gate,
    Registry,
    fleet_target,
    manager_for,
    runtime,
    wait_workers_stopped,
)
from tests.unit.test_organization_placement import Clock as OrgClock
from tests.unit.test_rem_home import force_run, recipe
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_placement import (
    RATICODE,
    START,
    Clock,
    FakeComputers,
    fingerprints,
    git_repository,
    ready,
    shared,
)

OFFER = {
    "computer_id": "desk-b",
    "computer_name": "Desktop B",
    "project": "Raticode",
    "text": "Desktop B has room: Run there",
    "pick": True,
    "grant": "",
}


def full_snapshot(tmp_path: Path, **catalog: Any) -> Any:
    """This computer full of organization workers; Desktop B online with room."""
    fake = FakeComputers(organizations=2, limit=2, catalog=shared(RATICODE, **catalog))
    snapshot = ready(tmp_path, fake, Clock()).snapshot(2)
    assert snapshot is not None
    snapshot.fingerprint = fingerprints
    return snapshot


def test_a_waiting_research_task_is_offered_the_desktop_with_room(tmp_path: Path) -> None:
    snapshot = full_snapshot(tmp_path)
    research = replace(START, read_only=True)
    # This computer is full, so the start waits; Desktop B could take it and has room.
    assert snapshot.place(start=research).wait
    found = suggest(snapshot, research)
    assert found is not None and found.pick
    assert found.public() == OFFER
    # A task that changes files can't be handed over, so nothing is offered.
    assert suggest(snapshot, replace(START, read_only=False)) is None
    # A desktop that fails a check (here the provider) offers nothing either.
    assert suggest(snapshot, replace(research, provider="claude_code")) is None


def test_the_run_keeps_the_offer_only_while_nobody_pinned_it(tmp_path: Path) -> None:
    snapshot = full_snapshot(tmp_path)
    run: dict[str, Any] = {
        "recipe": {
            "type": "research",
            "project_dir": "/home/me/code/Raticode",
            "provider": "codex",
        },
        "config": {"provider": "codex"},
    }
    assert run_there_suggestion(snapshot, run) == OFFER
    pinned = {**run, "recipe": {**run["recipe"], "computer": "x"}}
    assert run_there_suggestion(snapshot, pinned) is None
    assert run_there_suggestion(None, run) is None


def queued_research(home: RemHome, **patch: Any) -> dict[str, Any]:
    """A research task's queued run that self-work started, waiting with an offer."""
    home.create_task({"request_id": "create", **recipe(type="research")})
    run = home.store.tasks()[0]["last_run"]
    force_run(
        home,
        run["id"],
        state="queued",
        waiting_reason="Waiting for a free worker: Workstation is full (2 of 2 running)",
        suggestion=OFFER,
        self_work={"origin": "self_work", "ticket_id": "REM-1"},
        **patch,
    )
    with home.store.read() as db:
        return dict(home.store.get(db, "runs", run["id"]))


def test_run_there_pins_the_run_as_the_users_own_start(home: RemHome) -> None:
    run = queued_research(home)
    assert self_started(run) and not pinned_computer(run)
    picked = home.run_there(run["id"], {"request_id": "pick-1", "computer_id": "desk-b"})["run"]
    assert pinned_computer(picked) == "desk-b"
    assert picked["run_there"]["chosen_by"] == "user"
    assert picked["suggestion"] is None
    assert picked["waiting_reason"] == "Starting on Desktop B"
    # Your own start now: self-work's pace and daily cap don't count it.
    assert not self_started(picked)
    # The same request is answered again; a second pick has no offer left.
    again = home.run_there(run["id"], {"request_id": "pick-1", "computer_id": "desk-b"})
    assert pinned_computer(again["run"]) == "desk-b"
    with pytest.raises(RemConflict):
        home.run_there(run["id"], {"request_id": "pick-2"})


def test_run_there_needs_a_current_offer_for_that_computer(home: RemHome) -> None:
    run = queued_research(home)
    with pytest.raises(RemConflict, match="Desktop B is the one with room"):
        home.run_there(run["id"], {"request_id": "pick-1", "computer_id": "desk-c"})
    force_run(home, run["id"], suggestion=None)
    with pytest.raises(RemConflict, match="No paired desktop has room"):
        home.run_there(run["id"], {"request_id": "pick-2"})
    force_run(home, run["id"], suggestion={**OFFER, "pick": False})
    with pytest.raises(RemConflict):
        home.run_there(run["id"], {"request_id": "pick-3"})
    with home.store.read() as db:
        assert not pinned_computer(home.store.get(db, "runs", run["id"]))


def held_ticket(tmp_path: Path, targets: list[dict[str, Any]]) -> tuple[Any, Any, Gate, Any]:
    """An organization whose only ticket waits: this computer is full, Desktop B has room."""
    root = git_repository(tmp_path / "Raticode")
    here = repository_fingerprint.fingerprint(root)
    clock = OrgClock()
    gate = Gate()
    catalog = shared({"id": PROJECT_THERE, "name": "Raticode", "fingerprint": here})
    registry = Registry(limit=1, rem=1, desktop=DESK, catalog=catalog)
    manager = manager_for(tmp_path, gate, registry, clock)
    org = manager.call(
        str(root),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Studio",
                    "employees": [
                        {"id": "w0", "name": "Worker 0", "provider": "codex"},
                        {"id": "w1", "name": "Worker 1", "provider": "codex"},
                    ],
                    "projects": [{"id": "main", "name": "Main"}],
                    "executionTargets": targets,
                }
            },
        },
    )
    placement_settings.save(
        manager.data_dir,
        f"org:{org['id']}",
        "overflow",
        [],
        known_computers=registry.read()["computers"],
        organizations=[{"id": org["id"], "name": "Studio"}],
    )
    task = act(manager, org, "task_create", title="Ship", assignee="w0")
    act(manager, org, "control", state="running")
    for _ in range(3):
        manager.dispatch()
        clock.now += 1.5
    return manager, org, gate, task


def test_an_organization_pick_only_sets_the_owners_existing_fleet_target(tmp_path: Path) -> None:
    manager, org, gate, task = held_ticket(tmp_path, [fleet_target()])
    try:
        state = runtime(manager, org)
        assert manager.running_workers() == 0 and gate.started == []
        offer = state["placementSuggestions"][task["id"]]
        assert offer["pick"] is True and offer["target_id"] == "workstation"
        assert offer["text"] == "Desktop B has room: Run there"
        # Nothing went there unpicked: no execution, still waiting in To do.
        ticket = next(t for t in state["tasks"] if t["id"] == task["id"])
        assert ticket["status"] == "todo" and not ticket.get("execution")
        # Rem, and with it Auto mode, can't pick.
        with pytest.raises(ValueError, match="Only you can send a ticket"):
            manager.call(
                org["projectRoot"],
                "rem",
                {
                    "organizationId": org["id"],
                    "action": "run_there",
                    "params": {"taskId": task["id"], "computerId": DESK},
                },
            )
        targets_before = manager.store.get("", org["id"])["config"]["executionTargets"]
        act(manager, org, "run_there", taskId=task["id"], computerId=DESK)
        after = manager.store.get("", org["id"])
        ticket = next(t for t in after["runtime"]["tasks"] if t["id"] == task["id"])
        # The pick set only the target the owner made; nothing was granted.
        assert ticket["execution"] == {"targetId": "workstation"}
        assert after["config"]["executionTargets"] == targets_before
        assert task["id"] not in (after["runtime"].get("placementSuggestions") or {})
    finally:
        gate.release.set()
        wait_workers_stopped(manager)
        manager.close()


def test_without_an_owner_target_the_ticket_says_what_to_grant_and_offers_no_pick(
    tmp_path: Path,
) -> None:
    # A target for another worker isn't this worker's grant.
    manager, org, gate, task = held_ticket(tmp_path, [fleet_target(employees=["w1"])])
    try:
        offer = runtime(manager, org)["placementSuggestions"][task["id"]]
        grant = "To run it there, grant Worker 0 a fleet execution target on Desktop B for Raticode"
        assert offer["pick"] is False and "target_id" not in offer
        assert offer["grant"] == grant
        assert offer["text"] == f"Desktop B has room. {grant}"
        with pytest.raises(ValueError, match="grant Worker 0 a fleet execution target"):
            act(manager, org, "run_there", taskId=task["id"], computerId=DESK)
        ticket = next(t for t in runtime(manager, org)["tasks"] if t["id"] == task["id"])
        assert not ticket.get("execution") and ticket["status"] == "todo"
    finally:
        gate.release.set()
        wait_workers_stopped(manager)
        manager.close()
