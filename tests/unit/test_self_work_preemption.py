"""Self-work makes room for the user's own tasks, one run per task that doesn't fit.

With the thread limit full of self-work, each task the user starts pauses exactly one self-work
run, the lowest priority first. With a thread free, nothing pauses. Paused runs keep their
thread and pick up as soon as a thread is free again. Fake providers and isolated data only.
"""

from typing import Any

from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_store import RESUME_TEXT
from gofer.ui.rem_self_work import _pause_order
from tests.unit.test_rem_kanban import add, at, set_options, tick, ticket
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_pause_resume import (
    git_project,
    reply_to,
    run_of,
    runs,
    start_task,
    until,
    worker_prompts,
)

RESEARCH = "Research new features for project"


def self_runs(home: RemHome) -> dict[str, dict[str, Any]]:
    """Self-work's current runs by title."""
    return {
        r["recipe"]["title"]: r
        for r in runs(home)
        if (r.get("self_work") or {}).get("origin", "self_work") == "self_work"
        and r.get("self_work")
    }


def paused_titles(home: RemHome) -> set[str]:
    return {
        title for title, r in self_runs(home).items() if r["state"] == "paused" or r.get("pausing")
    }


def running_titles(home: RemHome) -> set[str]:
    return {
        title
        for title, r in self_runs(home).items()
        if r["state"] in {"queued", "executing", "verifying", "repairing"}
    }


def fill(
    home: RemHome, fake: Any, project: Any, tickets: list[tuple[str, str]], limit: int
) -> None:
    """Self-work fills ``limit`` threads with these tickets (and research for the rest),
    then the user comes back, so nothing new starts on its own."""
    git_project(project)
    fake.hold.clear()
    for title, priority in tickets:
        add(home, title, priority=priority)
    set_options(home, max_threads=limit)
    at(home, 15)
    tick(home)
    until(home, lambda: len(running_titles(home)) == limit and fake.working() == limit)
    reply_to(home, "back", "I'm back")


def settle_ticks(home: RemHome, count: int = 15) -> None:
    for _ in range(count):
        tick(home)


def test_limit_four_with_four_running_pauses_exactly_the_lowest_priority_one(board):
    home, fake, project = board
    fill(
        home,
        fake,
        project,
        [("Crit", "critical"), ("High", "high"), ("Med", "medium"), ("Low", "low")],
        4,
    )
    mine = start_task(home, "Mine")
    until(home, lambda: run_of(home, mine["id"])["state"] == "executing")
    until(home, lambda: paused_titles(home) == {"Low"})
    settle_ticks(home)
    # One task of the user's, one pause; the others keep going.
    assert paused_titles(home) == {"Low"}
    assert running_titles(home) == {"Crit", "High", "Med"}
    fake.hold.set()


def test_limit_four_with_two_running_pauses_nothing(board):
    home, fake, project = board
    fill(home, fake, project, [("One", "low"), ("Two", "low")], 2)
    set_options(home, max_threads=4)
    mine = start_task(home, "Mine")
    until(home, lambda: run_of(home, mine["id"])["state"] == "executing")
    settle_ticks(home)
    # Three of four threads are busy: a thread is still free, so nothing makes way.
    assert paused_titles(home) == set()
    assert running_titles(home) == {"One", "Two"}
    assert home.self_work.view()["state"]["paused_for_you"] == 0
    fake.hold.set()


def test_two_tasks_back_to_back_pause_two_runs_lowest_first_and_never_more(board):
    home, fake, project = board
    # Three tickets and one research run, which ranks below every ticket.
    fill(home, fake, project, [("Crit", "critical"), ("High", "high"), ("Low", "low")], 4)
    assert RESEARCH in running_titles(home)
    first = start_task(home, "First")
    second = start_task(home, "Second")
    until(home, lambda: paused_titles(home) == {RESEARCH, "Low"})
    until(
        home,
        lambda: (
            run_of(home, first["id"])["state"] == "executing"
            and run_of(home, second["id"])["state"] == "executing"
        ),
    )
    settle_ticks(home)
    # Two tasks, two pauses, each of a different run; the ticket work that matters most runs.
    assert paused_titles(home) == {RESEARCH, "Low"}
    assert running_titles(home) == {"Crit", "High"}
    notes = [m for m in home.store.conversation()["messages"] if m["body"].startswith("Paused ")]
    assert sum(int(m["body"].split()[1]) for m in notes) == 2
    fake.hold.set()


def test_a_third_task_pauses_one_more_and_the_first_two_stay_paused(board):
    home, fake, project = board
    fill(home, fake, project, [("Crit", "critical"), ("High", "high"), ("Low", "low")], 4)
    start_task(home, "First")
    until(home, lambda: paused_titles(home) == {RESEARCH})
    start_task(home, "Second")
    until(home, lambda: paused_titles(home) == {RESEARCH, "Low"})
    start_task(home, "Third")
    until(home, lambda: paused_titles(home) == {RESEARCH, "Low", "High"})
    settle_ticks(home)
    assert running_titles(home) == {"Crit"}
    fake.hold.set()


def test_equal_priority_pauses_the_most_recently_started():
    older = {"state": "executing", "started_at": "2026-11-13T10:00:00+00:00", "self_work": {}}
    newer = {**older, "started_at": "2026-11-13T10:05:00+00:00"}
    waiting = {**older, "state": "queued", "started_at": None}
    research = {**older, "self_work": {"kind": "research"}}
    critical = {**older, "self_work": {"kind": "ticket", "ticket_id": "c"}}
    order = sorted(
        [older, critical, newer, waiting, research],
        key=lambda r: _pause_order(r, {"c": "critical"}),
    )
    # Research first, then medium tickets (not started, newest, oldest), critical last.
    assert order == [research, waiting, newer, older, critical]


def test_paused_runs_resume_as_threads_free_highest_priority_first(board):
    home, fake, project = board
    fill(home, fake, project, [("Crit", "critical"), ("High", "high"), ("Low", "low")], 4)
    first = start_task(home, "First")
    second = start_task(home, "Second")
    until(home, lambda: paused_titles(home) == {RESEARCH, "Low"})
    until(
        home,
        lambda: (
            {r["recipe"]["title"] for r in runs(home) if r["state"] == "paused"}
            == {RESEARCH, "Low"}
        ),
    )
    low = self_runs(home)["Low"]
    # One of the user's tasks finishes: one thread frees, and the paused ticket takes it
    # (it ranks above research), while the user is still here.
    fake.released.add("First")
    until(home, lambda: run_of(home, first["id"])["state"] == "succeeded")
    until(home, lambda: run_of(home, low["id"])["state"] == "executing")
    settle_ticks(home)
    assert paused_titles(home) == {RESEARCH}
    assert run_of(home, low["id"])["resumed"]["by"] == "rem"
    # Same run, same thread, told to check what it already did.
    until(home, lambda: len(worker_prompts(fake, low["thread_id"])) == 2)
    assert RESUME_TEXT in worker_prompts(fake, low["thread_id"])[-1]
    assert len([r for r in runs(home) if r["recipe"]["title"] == "Low"]) == 1
    # The second finishes: research picks up too.
    fake.released.add("Second")
    until(home, lambda: run_of(home, second["id"])["state"] == "succeeded")
    until(home, lambda: paused_titles(home) == set())
    assert running_titles(home) == {"Crit", "High", "Low", RESEARCH}
    fake.hold.set()


def test_a_ticket_the_user_moves_to_in_progress_counts_as_their_work(board):
    home, fake, project = board
    fill(home, fake, project, [("Crit", "critical"), ("Low", "low")], 2)
    mine = add(home, "Mine now", priority="low")
    home.kanban.move(
        "user", {"ticket_id": mine["id"], "expected_revision": 1, "column": "in_progress"}
    )
    until(home, lambda: paused_titles(home) == {"Low"})
    settle_ticks(home)
    # The user's ticket isn't self-work, so it is never the one paused.
    assert ticket(home, "Mine now")["running"]
    assert paused_titles(home) == {"Low"}
    fake.hold.set()


def test_paused_self_work_stays_paused_after_the_user_turns_self_work_off(board):
    home, fake, project = board
    fill(home, fake, project, [("Crit", "critical"), ("High", "high"), ("Low", "low")], 4)
    first = start_task(home, "First")
    start_task(home, "Second")
    until(home, lambda: paused_titles(home) == {RESEARCH, "Low"})
    paused = [self_runs(home)[RESEARCH], self_runs(home)["Low"]]
    # Self-work is off before a thread frees: nothing it paused picks up on its own.
    set_options(home, enabled=False)
    fake.released.add("First")
    until(home, lambda: run_of(home, first["id"])["state"] == "succeeded")
    settle_ticks(home)
    for run in paused:
        now = run_of(home, run["id"])
        assert now["state"] in {"paused", "cancelling", "cancelled"}, now["state"]
        assert not now.get("resumed")
    fake.hold.set()
