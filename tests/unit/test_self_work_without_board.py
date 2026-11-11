"""Self-work doesn't need the global Kanban board.

With the board turned off on the desktop, self-work leaves tickets alone and fills its threads
with research tasks whose ideas go to the running report. Turning the board on or off while
self-work is running neither repeats work nor leaves a ticket claimed by nothing.
"""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.rem_home import RemHome
from gofer.ui.rem_kanban import read_tickets
from gofer.ui.rem_self_work import IDEAS_AGAIN_AFTER
from tests.unit.test_rem_kanban import (
    PROPOSAL,
    add,
    at,
    set_options,
    settle,
    tick,
    ticket,
    wait_for,
)
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_self_work_fill import activity, quiet, worker_titles

RESEARCH = "Research new features for project"
RELIABILITY = "Research reliability problems in project"
SPEED = "Research speed and upkeep in project"


def use_board(home: RemHome, project: Path, on: bool | None) -> None:
    """What the desktop window sends when the Kanban plugin is shown or hidden.

    ``None`` is an older window that doesn't say, which counts as the board being on.
    """
    config: dict[str, Any] = {
        "provider": "codex",
        "model": "cli-default",
        "command_slots": {"maxWorkers": 8},
        "projects": [{"root": str(project), "name": "project"}],
    }
    if on is not None:
        config["kanban_board"] = on
    home.configure(config)


def all_tickets(home: RemHome) -> list[dict[str, Any]]:
    with home.store.read() as db:
        return read_tickets(db)


def report(home: RemHome) -> dict[str, Any]:
    result: dict[str, Any] = home.self_work.view()["report"]
    return result


def research_prompts(home: RemHome) -> list[str]:
    with home.store.read() as db:
        runs = [json.loads(row[0]) for row in db.execute("SELECT body FROM runs")]
    return [
        r["recipe"]["prompt"] for r in runs if (r.get("self_work") or {}).get("kind") == "research"
    ]


def test_the_window_says_whether_the_board_is_on(board):
    home, _, project = board
    use_board(home, project, None)
    assert home.self_work.view()["state"]["board_enabled"] is True
    use_board(home, project, False)
    assert home.self_work.view()["state"]["board_enabled"] is False
    use_board(home, project, True)
    assert home.self_work.view()["state"]["board_enabled"] is True
    with pytest.raises(ValueError, match="Kanban board"):
        home.configure({"provider": "codex", "kanban_board": "off"})


def test_saved_self_work_settings_are_kept_when_the_board_is_turned_off(board):
    """Regression: the settings move out of the board without being reset."""
    home, _, project = board
    set_options(home, enabled=False, max_threads=5, auto_pull_backlog=True)
    before = home.self_work.view()["settings"]
    use_board(home, project, False)
    after = home.self_work.view()["settings"]
    assert after == before
    assert (after["enabled"], after["max_threads"], after["auto_pull_backlog"]) == (False, 5, True)
    assert home.self_work.view()["state"]["mode"] == "off"


def test_with_the_board_off_self_work_runs_research_and_makes_no_tickets(board):
    home, fake, project = board
    add(home, "Waiting todo")
    add(home, "Backlog item", status="backlog")
    add(home, "Backlog item again", status="backlog")
    set_options(home, max_threads=3, auto_pull_backlog=True)
    use_board(home, project, False)
    fake.answers[RESEARCH] = "Found some.\n" + PROPOSAL
    before = {t["id"]: (t["status"], t["revision"]) for t in all_tickets(home)}
    fake.hold.clear()
    at(home, 15)
    tick(home)
    state = home.self_work.view()["state"]
    # Every thread is used, all for research: no to-do, backlog pull, ranking or merge.
    assert state["mode"] == "working" and state["active_threads"] == 3
    assert sorted(activity(home)) == sorted([RESEARCH, RELIABILITY, SPEED])
    assert state["todo_remaining"] == 0 and state["proposals_waiting"] == 0
    fake.hold.set()
    wait_for(lambda: quiet(home), 8)
    # The research saw no board and was asked for ideas, not tickets.
    prompts = research_prompts(home)
    assert len(prompts) == 3
    assert all("Board:" not in p and "duplicates" not in p for p in prompts)
    assert all("doesn't use a ticket board" in p for p in prompts)
    # Nothing on the board was created or touched.
    assert {t["id"]: (t["status"], t["revision"]) for t in all_tickets(home)} == before
    assert ticket(home, "Waiting todo")["status"] == "todo"
    seen = report(home)
    assert not seen["tickets_added"] and not seen["duplicates_merged"]
    ideas = [w for w in seen["work_done"] if w["title"] == RESEARCH]
    assert ideas and ideas[0]["state"] == "researched"
    assert ideas[0]["summary"] == "Found 1 idea: Voice shortcuts."
    assert any("with the board off" in note["text"] for note in seen["notes"])
    assert home.self_work.view()["state"]["detail"].startswith("Out of work")


def test_with_the_board_off_each_project_is_researched_once_a_day(board):
    home, fake, project = board
    set_options(home, max_threads=3)
    use_board(home, project, False)
    at(home, 15)
    tick(home)
    wait_for(lambda: quiet(home), 8)
    assert len(worker_titles(fake)) == 3
    # The user comes back and leaves again: nothing new to research yet.
    come_back(home, 20)
    at(home, 40)
    tick(home)
    assert quiet(home) and len(worker_titles(fake)) == 3
    assert home.self_work.view()["state"]["detail"].startswith("Out of work")
    # A day after the research, it researches again.
    later = 15 + IDEAS_AGAIN_AFTER / timedelta(minutes=1)
    come_back(home, later)
    at(home, later + 15)
    tick(home)
    wait_for(lambda: quiet(home), 8)
    assert len(worker_titles(fake)) == 6


def come_back(home: RemHome, minutes: float) -> None:
    """The user prompts Rem, which ends the self-work session."""
    at(home, minutes)
    with home.store.connect() as db:
        home.self_work.note_prompt(db)
    tick(home)
    assert not home.self_work.view()["state"]["mode"] == "working" or activity(home)


def test_turning_the_board_off_lets_running_tickets_finish_and_starts_no_more(board):
    home, fake, project = board
    set_options(home, max_threads=2)
    add(home, "First", priority="high")
    add(home, "Second", priority="low")
    add(home, "Third", priority="low")
    fake.hold.clear()
    at(home, 15)
    tick(home)
    assert sorted(activity(home)) == ["First", "Second"]
    use_board(home, project, False)
    tick(home)
    # The running tickets still hold both threads, so nothing else starts.
    assert sorted(activity(home)) == ["First", "Second"]
    assert ticket(home, "Third")["status"] == "todo"
    fake.hold.set()
    settle(home, "First", "Second")
    # Their tickets are finished, not left claimed.
    assert ticket(home, "First")["status"] == "done" and not ticket(home, "First")["running"]
    assert ticket(home, "Second")["status"] == "done"
    wait_for(lambda: quiet(home), 8)
    # With the board off, the free threads went to research, never to the waiting to-do.
    assert ticket(home, "Third")["status"] == "todo"
    assert any(title.startswith("Research") for title in worker_titles(fake))


def test_research_that_finishes_after_the_board_is_turned_off_adds_no_tickets(board):
    home, fake, project = board
    set_options(home, max_threads=1)
    fake.answers[RESEARCH] = PROPOSAL
    fake.hold.clear()
    at(home, 15)
    tick(home)
    assert activity(home) == [RESEARCH]
    use_board(home, project, False)
    fake.hold.set()
    wait_for(
        lambda: not tick(home) and any(w["title"] == RESEARCH for w in report(home)["work_done"]), 8
    )
    assert not [t for t in all_tickets(home) if t["title"] == "Voice shortcuts"]
    done = next(w for w in report(home)["work_done"] if w["title"] == RESEARCH)
    assert done["summary"] == "Found 1 idea: Voice shortcuts."


def test_turning_the_board_back_on_picks_tickets_up_without_repeating_research(board):
    home, fake, project = board
    set_options(home, max_threads=1)
    use_board(home, project, False)
    add(home, "Todo for later")
    fake.answers[RESEARCH] = PROPOSAL
    fake.hold.clear()
    at(home, 15)
    tick(home)
    assert activity(home) == [RESEARCH]
    use_board(home, project, True)
    tick(home)
    # The research holds the only thread; the to-do waits for it instead of going over.
    assert activity(home) == [RESEARCH]
    fake.hold.set()
    settle(home, "Todo for later")
    assert ticket(home, "Todo for later")["status"] == "done"
    # Research started with the board off reports ideas and doesn't propose tickets.
    assert not [t for t in all_tickets(home) if t["title"] == "Voice shortcuts"]
    assert worker_titles(fake).count(RESEARCH) == 1


def test_with_the_board_off_blocked_tickets_are_left_alone(board):
    """Picking blocked tickets back up is ticket work, so it waits for the board."""
    from tests.unit.test_ticket_timeline import timeline

    home, fake, project = board
    set_options(home, max_threads=1)
    use_board(home, project, False)
    fake.failing.add("Broken")
    # You can still start a ticket yourself, say from the phone's board.
    home.self_work.start_now(add(home, "Broken")["id"], "user")
    wait_for(lambda: not tick(home) and ticket(home, "Broken")["status"] == "blocked", 8)
    entries = len(timeline(home, "Broken"))
    at(home, 15)
    for _ in range(3):
        tick(home)
    wait_for(lambda: quiet(home), 8)
    # Still blocked, not tried again, nothing new on its timeline; the thread went to research.
    assert ticket(home, "Broken")["status"] == "blocked"
    assert worker_titles(fake).count("Broken") == 1
    assert len(timeline(home, "Broken")) == entries
    assert any(title.startswith("Research") for title in worker_titles(fake))
    # With the board back on, Rem picks it up again.
    fake.failing.discard("Broken")
    use_board(home, project, True)
    wait_for(lambda: not tick(home) and worker_titles(fake).count("Broken") > 1, 8)


def test_with_the_board_off_research_tasks_show_in_rem_chat_with_cards(board):
    """Regression: board-off self-work tasks appear in Rem's chat like any other task."""
    home, fake, project = board
    use_board(home, project, False)
    set_options(home, max_threads=2)
    fake.answers[RESEARCH] = PROPOSAL
    at(home, 15)
    tick(home)

    def chat() -> list[dict[str, Any]]:
        return list(home.store.conversation()["messages"])

    started = [m for m in chat() if (m.get("work") or {}).get("self_work") == "research"]
    # One card per task, both from the same pass so clients show them under one line.
    assert len(started) == 2
    assert len({m["self_work"]["batch"] for m in started}) == 1
    assert all("board is off" in m["body"] and "Backlog" not in m["body"] for m in started)
    first = next(m for m in started if m["work"]["title"] == RESEARCH)
    run_id = first["work"]["run_id"]
    wait_for(lambda: quiet(home), 8)
    updates = [
        m
        for m in chat()
        if (m.get("work") or {}).get("run_id") == run_id and m.get("kind") == "task_update"
    ]
    assert len(updates) == 1
    assert "Found 1 idea: Voice shortcuts." in updates[0]["body"]
    assert not updates[0]["work"].get("tickets")
    assert not [t for t in all_tickets(home) if t["title"] == "Voice shortcuts"]
    # Ticking again never posts a second update.
    for _ in range(3):
        tick(home)
    again = [
        m
        for m in chat()
        if (m.get("work") or {}).get("run_id") == run_id and m.get("kind") == "task_update"
    ]
    assert len(again) == 1


def test_a_ranking_that_finishes_after_the_board_is_turned_off_is_not_applied(board):
    from tests.unit.test_self_work_fill import RANK, ranking

    home, fake, project = board
    set_options(home, max_threads=1, auto_pull_backlog=True)
    item = add(home, "Backlog item", status="backlog")
    fake.answers[RANK] = ranking((item, 5, 5, False, "Worth it"))
    fake.hold.clear()
    at(home, 15)
    tick(home)
    assert activity(home) == [RANK]
    use_board(home, project, False)
    fake.hold.set()
    wait_for(lambda: quiet(home), 8)
    # The ticket keeps no ranking and stays where it was; the chat and report say why.
    assert ticket(home, "Backlog item")["status"] == "backlog"
    with home.store.read() as db:
        assert not read_tickets(db, "backlog")[0].get("triage")
    assert any("board is off now" in n["text"] for n in report(home)["notes"])
    updates = [
        m
        for m in home.store.conversation()["messages"]
        if m.get("kind") == "task_update" and "board is off now" in m["body"]
    ]
    assert len(updates) == 1
