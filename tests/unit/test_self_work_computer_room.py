"""Self-work starts only as many tickets as Rem's computers have room for.

Organization workers and phone turns share Rem's computers, so self-work reads the same
placement snapshot Rem's admission uses and moves no more tickets to In progress than
that room allows; with none it says which computers are full.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gofer.cli.commands.tickets import _status
from gofer.ui.rem_home import RemHome
from tests.unit.test_rem_kanban import add, at, set_options, tick, ticket
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_placement import Clock, FakeComputers

TITLES = ("First", "Second", "Third")


class BrokenComputers:
    def read(self) -> dict[str, Any]:
        raise OSError("registry unavailable")


def share(home: RemHome, computers: Any, clock: Clock, project: Path, workers: int = 3) -> None:
    """This computer's worker limit, shared through the registry the server would set."""
    home.configure(
        {
            "provider": "codex",
            "model": "cli-default",
            "command_slots": {"maxWorkers": workers},
            "projects": [{"root": str(project), "name": "project"}],
        }
    )
    home.placement.clock = clock
    home.placement.computers = computers
    if isinstance(computers, FakeComputers):
        # Its organization workers hold claims Rem's placement counts, as on the server.
        computers.attach(home.placement)


def in_progress(home: RemHome) -> list[str]:
    return [title for title in TITLES if ticket(home, title)["status"] == "in_progress"]


def computer_notes(home: RemHome) -> int:
    """Report notes about full computers, which the report shows as plain notes."""
    with home.store.read() as db:
        rows = db.execute("SELECT body FROM self_work_report").fetchall()
    return sum(1 for (body,) in rows if json.loads(body).get("limit") == "computers")


def passes(home: RemHome, clock: Clock, count: int = 3) -> None:
    for _ in range(count):
        tick(home)
        # A second apart, so each pass may read the registry again.
        clock.now += 1.1


def test_two_organization_workers_leave_room_for_one_self_work_ticket(board):
    home, fake, project = board
    fake.hold.clear()
    clock = Clock()
    share(home, FakeComputers(organizations=2, limit=3), clock, project)
    set_options(home, max_threads=3)
    for title in TITLES:
        add(home, title)
    at(home, 15)
    passes(home, clock)
    assert len(in_progress(home)) == 1
    state = home.self_work.view()["state"]
    assert state["active_threads"] == 1
    assert state["limit_reason"] == "Computers full: Workstation 3 of 3 running"
    assert state["mode"] == "working" and state["detail"].endswith(state["limit_reason"])


def test_a_full_computer_starts_nothing_and_says_which_one(board):
    home, fake, project = board
    fake.hold.clear()
    clock = Clock()
    computers = FakeComputers(organizations=3, limit=3)
    share(home, computers, clock, project)
    set_options(home, max_threads=3)
    for title in TITLES:
        add(home, title)
    at(home, 15)
    passes(home, clock)
    assert in_progress(home) == []
    view = home.self_work.view()
    state = view["state"]
    assert (state["mode"], state["active_threads"]) == ("waiting", 0)
    assert state["limit_reason"] == "Computers full: Workstation 3 of 3 running"
    assert state["detail"] == state["limit_reason"]
    # `Raticode selfwork status` prints the same reason.
    assert "Computers full: Workstation 3 of 3 running" in str(_status(view).text)
    # The report says it once for the session, however often the computer fills again.
    assert computer_notes(home) == 1
    # Once an organization worker finishes, self-work takes the freed room.
    computers.organizations = 2
    passes(home, clock)
    assert len(in_progress(home)) == 1
    assert home.self_work.view()["state"]["limit_reason"] == (
        "Computers full: Workstation 3 of 3 running"
    )


def test_without_a_reading_of_the_computers_nothing_starts(board):
    home, fake, project = board
    fake.hold.clear()
    clock = Clock()
    share(home, BrokenComputers(), clock, project)
    set_options(home, max_threads=3)
    for title in TITLES:
        add(home, title)
    at(home, 15)
    passes(home, clock)
    assert in_progress(home) == []
    state = home.self_work.view()["state"]
    assert state["limit_reason"] == "Waiting for the computers' reading: registry unavailable"
