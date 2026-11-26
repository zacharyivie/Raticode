"""The user's own Rem runs go first when organization workers and self-work fill a computer.

While a Rem run the user started waits for a worker, the worker claims record it: self-work
pauses as many runs as it needs, counting the computer's real load, organization dispatch
claims no worker or ticket and says why, and the next free worker goes to the user's run.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from gofer.core.worker_claims import WAIT_SECONDS, WorkerClaims, waiting_reason
from gofer.ui.organizations import OrganizationManager
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_self_work import PRIORITY_REASON
from tests.helpers.organizations import act
from tests.unit.test_organization_placement import company, runtime
from tests.unit.test_rem_kanban import add, at, set_options, tick
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_pause_resume import run_of, runs, start_task
from tests.unit.test_rem_placement import Clock, FakeComputers
from tests.unit.test_self_work_computer_room import share


class Gate:
    """An organization worker stream that runs until released, and records each start."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started: list[str] = []

    async def __call__(self, **options: Any) -> Any:
        self.started.append(options["workflow"]["organizationRun"]["taskId"])
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        yield {"type": "final", "message": {"body": "Done."}}


def organization(tmp_path: Path, computers: FakeComputers, clock: Clock) -> OrganizationManager:
    """An organization manager sharing the computer and its worker claims with Rem."""
    manager = OrganizationManager(tmp_path / "orgs", stream=Gate(), start_runtime=False)
    manager.placement.clock = clock
    computers.attach(manager.placement)
    return manager


def passes(home: RemHome, clock: Clock, condition: Any, timeout: float = 10) -> None:
    """Tick Rem's loop, a second apart on the placement clock, until ``condition`` holds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        tick(home)
        clock.now += 1.1
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out")


def until(condition: Any, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out")


def dispatch(manager: OrganizationManager, clock: Clock) -> None:
    """Two dispatch passes: the first asks for the computers' reading, the second uses it."""
    for _ in range(2):
        manager.dispatch()
        clock.now += 1.1


def test_a_user_task_pauses_self_work_and_gets_its_worker_before_an_organization(board, tmp_path):
    home, fake, project = board
    fake.hold.clear()
    clock = Clock()
    # A worker limit of 3 on this computer, two organization workers running on it.
    computers = FakeComputers(organizations=2, limit=3)
    share(home, computers, clock, project)
    claims = computers.claims
    # Self-work's own limit has room for the user's task too: only the computer is full.
    set_options(home, max_threads=2)
    add(home, "Alpha")
    at(home, 15)
    passes(home, clock, lambda: fake.working() == 1)
    alpha = next(r for r in runs(home) if r["recipe"]["title"] == "Alpha")
    assert claims.running("pc-1") == {"rem": 1, "phone": 0, "organizations": 2, "swarms": 0}
    # An organization with a ticket ready and room under its maxConcurrency.
    manager = organization(tmp_path, computers, clock)
    org = company(manager, tmp_path / "studio", "Studio", workers=2, maxConcurrency=2)
    ticket = act(manager, org, "task_create", title="Ship", assignee="w0")
    act(manager, org, "control", state="running")
    try:
        mine = start_task(home, "Fix the login page")
        passes(home, clock, lambda: bool(run_of(home, alpha["id"]).get("pausing")))
        pausing = run_of(home, alpha["id"])["pausing"]
        assert (pausing["by"], pausing["reason"]) == ("priority", PRIORITY_REASON)
        # The next pass stops self-work's turn; the user's task still finds no room.
        tick(home)
        clock.now += 1.1
        # Self-work's worker is back; the computer has one free worker.
        until(lambda: claims.held("pc-1", "rem") == 0)
        assert run_of(home, mine["id"])["state"] == "queued"
        # The organization comes first to the free worker, but makes way for it.
        dispatch(manager, clock)
        state = runtime(manager, org)
        assert manager.running_workers() == 0
        assert next(t for t in state["tasks"] if t["id"] == ticket["id"])["status"] == "todo"
        assert state["placementWait"] == (
            "Waiting so your own Rem task can start first: “Fix the login page”"
        )
        # The user's task gets the freed worker on Rem's next pass.
        passes(home, clock, lambda: run_of(home, mine["id"])["state"] == "executing")
        assert claims.running("pc-1") == {"rem": 1, "phone": 0, "organizations": 2, "swarms": 0}
        # Settings › Computers names the user's task by its title.
        assert {"kind": "rem", "label": "Fix the login page"} in claims.running_work("pc-1")
        made_way = run_of(home, alpha["id"])
        assert made_way["state"] == "paused"
        assert (made_way["paused"]["by"], made_way["paused"]["reason"]) == (
            "priority",
            PRIORITY_REASON,
        )
        # Exactly one self-work run made way; ticking again pauses nothing more.
        for _ in range(3):
            tick(home)
            clock.now += 1.1
        assert [r["id"] for r in runs(home) if r["state"] == "paused"] == [alpha["id"]]
        assert claims.waiting() == []
    finally:
        fake.hold.set()
        manager.stream.release.set()  # type: ignore[attr-defined]
        manager.close()


def test_an_organization_claims_nothing_while_a_user_task_waits_for_a_worker(board, tmp_path):
    home, fake, project = board
    fake.hold.clear()
    clock = Clock()
    # A worker limit of 2, both taken by organization workers.
    computers = FakeComputers(organizations=2, limit=2)
    share(home, computers, clock, project, workers=2)
    manager = organization(tmp_path, computers, clock)
    org = company(manager, tmp_path / "studio", "Studio", workers=2, maxConcurrency=2)
    ticket = act(manager, org, "task_create", title="Ship", assignee="w0")
    act(manager, org, "control", state="running")
    gate: Gate = manager.stream  # type: ignore[assignment]
    try:
        mine = start_task(home, "Fix the login page")
        passes(home, clock, lambda: bool(computers.claims.waiting()))
        assert run_of(home, mine["id"])["waiting_reason"].startswith("Waiting for a free worker")
        assert computers.claims.waiting(["pc-1"]) == ["Fix the login page"]
        # One organization worker finishes: the organization, below its maxConcurrency,
        # claims neither the worker nor a ticket, and says which task goes first.
        computers.organizations = 1
        dispatch(manager, clock)
        state = runtime(manager, org)
        reason = "Waiting so your own Rem task can start first: “Fix the login page”"
        assert state["placementWait"] == reason
        assert next(t for t in state["tasks"] if t["id"] == ticket["id"])["status"] == "todo"
        assert gate.started == [] and manager.running_workers() == 0
        assert computers.claims.running("pc-1")["organizations"] == 1
        # Rem's next pass gives the freed worker to the user's task, and the wait ends.
        passes(home, clock, lambda: run_of(home, mine["id"])["state"] == "executing")
        assert computers.claims.waiting() == []
        assert computers.claims.running("pc-1") == {
            "rem": 1,
            "phone": 0,
            "organizations": 1,
            "swarms": 0,
        }
        # The running organization worker was never stopped; the computer is full again.
        dispatch(manager, clock)
        assert gate.started == []
        assert runtime(manager, org)["placementWait"].startswith("Waiting for a free worker")
    finally:
        fake.hold.set()
        gate.release.set()
        manager.close()


def test_a_waiting_user_start_lets_only_user_starts_take_a_worker():
    now = [0.0]
    claims = WorkerClaims(clock=lambda: now[0])
    claims.wait("run-1", ["pc-1"], "Fix the login page")
    # Organization workers and self-work make way on that computer, not on another.
    assert claims.claim("pc-1", "organizations", 4) is None
    assert claims.claim("pc-1", "rem", 4) is None
    other = claims.claim("pc-2", "organizations", 4)
    assert other is not None
    # The user's own start goes ahead, and phone turns never wait for it.
    first = claims.claim("pc-1", "rem", 4, first=True)
    phone = claims.claim("pc-1", "phone", 1, own_only=True)
    assert first is not None and phone is not None
    assert claims.waiting(["pc-1"]) == ["Fix the login page"]
    assert claims.waiting(["pc-2"]) == []
    claims.drop_wait("run-1")
    assert claims.claim("pc-1", "organizations", 4) is not None


def test_waits_name_the_oldest_first_and_lapse_when_nobody_renews_them():
    now = [0.0]
    claims = WorkerClaims(clock=lambda: now[0])
    claims.wait("a", ["pc-1"], "First")
    now[0] = 5
    claims.wait("b", ["pc-1"], "Second")
    # Renewing keeps a wait's place in line.
    claims.wait("a", ["pc-1"], "First")
    assert claims.waiting() == ["First", "Second"]
    assert claims.waiting_runs() == ["a", "b"]
    assert waiting_reason(claims.waiting()) == (
        "Waiting so your own Rem tasks can start first: “First”, “Second”"
    )
    assert waiting_reason(["A", "B", "C", "D", "E"]).endswith("“A”, “B”, “C” and 2 more")
    # Rem's pass keeps only the runs still waiting.
    claims.keep_waits({"b"})
    assert claims.waiting_runs() == ["b"]
    # A wait Rem's tick stopped renewing lapses, so it can't hold the computer forever.
    now[0] = 5 + WAIT_SECONDS + 1
    assert claims.waiting() == []
    assert claims.claim("pc-1", "organizations", 4) is not None
    # A run with no computer it could use waits for nothing here.
    claims.wait("c", [], "Nowhere")
    assert claims.waiting_runs() == []
