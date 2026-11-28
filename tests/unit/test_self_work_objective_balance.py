"""Self-work shares its threads among all open objectives instead of serving one.

Before, To do and Backlog tickets started in priority and ranking order and research listed
every project of the oldest objective first, so whichever objective had the most or the
best-ranked tickets took about nine starts in ten (2026-11-24). Now each start goes to the
objective with the least work so far this session (running work plus starts), the user's
To do priorities still first. Fake providers and isolated data only.
"""

from typing import Any

from gofer.ui import rem_objectives as objectives
from tests.unit.test_rem_kanban import add, at, set_options, tick, wait_for
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_self_work_auto_approve import Rankings
from tests.unit.test_self_work_fill import worker_titles
from tests.unit.test_self_work_objectives import change, checker, listed, step

UI = "Improve Raticode UI responsiveness"
CLI = "Harden the command line against bad input"


def two_objectives(home: Any) -> tuple[str, str]:
    """UI first (the older), then CLI; returns their ids."""
    change(home, {"action": "add", "text": UI}, actor="user")
    change(home, {"action": "add", "text": CLI}, actor="user")
    by_text = {item["text"]: item["id"] for item in listed(home)}
    return by_text[UI], by_text[CLI]


def test_balanced_takes_turns_across_objectives_and_keeps_tiers_first():
    tickets = [{"n": n, "goal": "a"} for n in range(4)] + [{"n": 9, "goal": "b"}]
    goal = lambda t: t["goal"]  # noqa: E731
    order = objectives.balanced(tickets, goal, {"a": 0, "b": 0})
    assert [t["n"] for t in order] == [0, 9, 1, 2, 3]
    # An objective already busy waits its turn.
    order = objectives.balanced(tickets, goal, {"a": 2, "b": 0})
    assert [t["n"] for t in order][:2] == [9, 0]
    # A tier, such as the user's priority, comes before the balance.
    urgent = [{"n": 0, "goal": "a", "tier": 0}, {"n": 1, "goal": "a", "tier": 0}]
    order = objectives.balanced(
        [*urgent, {"n": 9, "goal": "b", "tier": 2}], goal, {}, tier=lambda t: t["tier"]
    )
    assert [t["n"] for t in order] == [0, 1, 9]


def test_to_do_tickets_take_turns_across_objectives(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=2)
    two_objectives(home)
    serves = {f"UI ticket {n}": UI for n in range(3)} | {"CLI ticket": CLI}
    checker(home, serves)
    for title in serves:
        add(home, title)
    at(home, 15)
    step(home)

    def started() -> list[str]:
        return sorted(
            t["title"] for t in home.kanban.board()["tickets"] if t["status"] == "in_progress"
        )

    wait_for(lambda: not tick(home) and len(started()) >= 2, 8)
    # Same priority: one of each objective, not the first two UI tickets by number.
    assert started() == ["CLI ticket", "UI ticket 0"]


def test_backlog_pulls_take_turns_even_when_one_objective_ranks_higher(board):
    home, fake, _ = board
    set_options(home, max_threads=1, auto_pull_backlog=True)
    two_objectives(home)
    serves = {f"UI ticket {n}": UI for n in range(3)} | {"CLI ticket": CLI}
    checker(home, serves)
    for title in serves:
        add(home, title, status="backlog")
    # Rem ranks every UI ticket above the CLI one.
    fake.answers = Rankings(
        home,
        lambda t: (
            {"impact": 5, "relevance": 5}
            if t["title"].startswith("UI")
            else {"impact": 2, "relevance": 2}
        ),
    )
    at(home, 15)
    step(home)

    def pulled() -> list[str]:
        return [t for t in worker_titles(fake) if t in serves]

    wait_for(lambda: not tick(home) and len(pulled()) >= 2, 10)
    # One pull for each objective, not the two best-ranked UI tickets. Which comes first
    # depends on timing: when the ranking's thread frees before its record clears, research
    # for the older UI objective takes it, and the CLI objective then has less work.
    assert {serves[title] for title in pulled()[:2]} == {UI, CLI}
    with home.store.read() as db:
        session = home.store.setting(db, "self_work_state")["session"]
    assert set(session["objective_starts"]) == {i["id"] for i in listed(home)}


def test_research_goes_to_the_objective_with_the_least_work(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=2)
    two_objectives(home)
    # UI, the older objective, already has a ticket running; research has one thread.
    checker(home, {"Render the side pane lazily": UI})
    add(home, "Render the side pane lazily")
    at(home, 15)
    step(home)

    def running() -> list[str]:
        return sorted(item["title"] for item in home.self_work.view()["state"]["activity"])

    wait_for(lambda: not tick(home) and len(running()) >= 2, 8)
    assert running() == [
        f"Find work for “{CLI}” in project",
        "Render the side pane lazily",
    ]
