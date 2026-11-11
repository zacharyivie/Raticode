"""Self-work in Rem's chat, tickets on task cards, and board ticket keys shown as pills.

Regression: work Rem started on its own (to-dos, pulled backlog, ranking and research)
never showed in the chat, because every run marked ``self_work`` skipped the chat card
and the update. Fake providers and isolated data only.
"""

from typing import Any

import pytest

from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_store import duplicate_notices
from gofer.ui.rem_kanban import (
    get_ticket,
    merge_refs,
    message_ticket_keys,
    put_ticket,
    ticket_keys,
)
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
from tests.unit.test_rem_kanban import api_server as api_server
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_kanban import org as org
from tests.unit.test_self_work_fill import RANK, RESEARCH, add_backlog, quiet, ranking


def messages(home: RemHome) -> list[dict[str, Any]]:
    return list(home.store.conversation()["messages"])


def ticket_cards(home: RemHome) -> list[dict[str, Any]]:
    """Messages about ticket runs; research that fills spare threads is left out."""
    return [m for m in messages(home) if (m.get("work") or {}).get("self_work") == "ticket"]


def by_run(home: RemHome, run_id: str) -> list[dict[str, Any]]:
    return [m for m in messages(home) if (m.get("work") or {}).get("run_id") == run_id]


# Self-work tasks in chat -------------------------------------------------------------


def test_self_work_tasks_show_in_chat_with_one_batch_per_pass(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=3)
    for title in ("First", "Second", "Third"):
        add(home, title)
    at(home, 14)
    tick(home)
    # Nothing is said before the user has been quiet for 15 minutes.
    assert messages(home) == []
    at(home, 15)
    tick(home)
    started = messages(home)
    assert len(started) == 3
    assert {m["id"] for m in started} == {f"queued-{m['work']['run_id']}" for m in started}
    # One pass started them, so the chat shows them together under one line.
    assert len({m["self_work"]["batch"] for m in started}) == 1
    keys = {ticket(home, t)["key"] for t in ("First", "Second", "Third")}
    assert {m["work"]["tickets"][0]["key"] for m in started} == keys
    assert all(m["work"]["self_work"] == "ticket" for m in started)
    # Ticking again while they run adds no second card.
    tick(home)
    assert len(messages(home)) == 3
    fake.hold.set()
    settle(home, "First", "Second", "Third")
    wait_for(lambda: not tick(home) and len(ticket_cards(home)) == 6, 8)
    updates = [m for m in ticket_cards(home) if m.get("kind") == "task_update"]
    assert {u["work"]["run_id"] for u in updates} == {m["work"]["run_id"] for m in started}
    assert all(u["work"]["tickets"][0]["role"] == "worked" for u in updates)
    # The legacy duplicate-card filter leaves these alone: no reply repeats them.
    assert not duplicate_notices(messages(home))


def test_a_later_pass_is_its_own_batch(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=8)
    add(home, "Early")
    at(home, 15)
    tick(home)
    add(home, "Late")
    tick(home)
    batches = [
        m["self_work"]["batch"]
        for m in ticket_cards(home)
        if m["work"]["tickets"][0]["key"]
        in {ticket(home, "Early")["key"], ticket(home, "Late")["key"]}
    ]
    assert len(batches) == 2 and batches[0] != batches[1]
    fake.hold.set()
    settle(home, "Early", "Late")


def test_a_ticket_moved_to_in_progress_says_who_started_it(board):
    home, _, _ = board
    created = add(home, "Run me now")
    home.kanban.move(
        "user",
        {"ticket_id": created["id"], "expected_revision": created["revision"], "column": "running"},
    )
    first = messages(home)[0]
    assert first["body"] == f"Started REM-{created['number']} because you moved it to In progress."
    # Not part of a self-work pass, so it isn't grouped with one.
    assert "batch" not in first["self_work"]
    settle(home, "Run me now")


def test_research_lists_the_tickets_it_proposed_on_its_card(board):
    home, fake, _ = board
    fake.answers[RESEARCH] = PROPOSAL
    set_options(home, max_threads=1)
    at(home, 15)
    tick(home)
    start = messages(home)[0]
    assert start["work"]["self_work"] == "research"
    assert "while you were away" in start["body"]
    wait_for(lambda: not tick(home) and len(messages(home)) >= 2, 8)
    wait_for(lambda: quiet(home), 8)
    idea = ticket(home, "Voice shortcuts")
    update = [m for m in by_run(home, start["work"]["run_id"]) if m.get("kind") == "task_update"]
    assert len(update) == 1
    assert idea["key"] in update[0]["body"] and "until you approve" in update[0]["body"]
    assert [(t["key"], t["role"]) for t in update[0]["work"]["tickets"]] == [
        (idea["key"], "created")
    ]
    # The task's run keeps them too, for the task details and the live card.
    task = next(t for t in home.store.tasks(summary=True) if t["id"] == start["work"]["task_id"])
    assert task["last_run"]["tickets"][0]["key"] == idea["key"]
    # Reconciling again never posts the update twice.
    for _ in range(3):
        tick(home)
    assert len(by_run(home, start["work"]["run_id"])) == 2


def test_a_ranking_names_the_tickets_it_left_out(board):
    home, fake, _ = board
    set_options(home, max_threads=1, auto_pull_backlog=True)
    stale = add_backlog(home, "Port to the old API")
    good = add_backlog(home, "Useful work")
    fake.answers[RANK] = ranking(
        (stale, 1, 1, True, "The old API was removed"), (good, 4, 4, False, "Helps")
    )
    at(home, 15)
    tick(home)
    settle(home, "Useful work")
    wait_for(lambda: quiet(home), 8)
    rank = next(
        m for m in messages(home) if m.get("kind") == "task_update" and "Ranked" in m["body"]
    )
    assert f"Left out REM-{stale['number']}" in rank["body"]
    assert rank["work"]["self_work"] == "triage"
    assert [t["key"] for t in rank["work"]["tickets"]] == [
        f"REM-{stale['number']}",
        f"REM-{good['number']}",
    ]
    # The pulled ticket then shows as its own card, started in a later pass.
    pulled = next(m for m in messages(home) if m["body"].startswith("Pulled"))
    assert pulled["body"] == (
        f"Pulled REM-{good['number']} from the backlog and started it while you were away."
    )


def test_tickets_rem_adds_while_starting_a_task_are_on_that_tasks_card(board):
    home, _, _ = board
    home.generate = lambda *args: {
        "reply": "Starting it.",
        "tasks": [{"title": "Fix the login", "type": "develop", "prompt": "Fix the login"}],
        "memories": [],
        "tickets": [{"action": "create", "title": "Login bug", "status": "backlog"}],
    }
    home.submit_message({"request_id": "m1", "body": "Track and fix the login bug"})
    home.tick()
    wait_for(lambda: home.store.conversation()["conversation"].get("turn_state") == "idle")
    created = ticket(home, "Login bug")
    reply = next(m for m in messages(home) if m["role"] == "rem")
    assert f"Added {created['key']} to my Backlog." in reply["body"]
    task = next(t for t in home.store.tasks(summary=True) if t["title"] == "Fix the login")
    run = task["active_run"] or task["last_run"]
    assert [(t["key"], t["role"]) for t in run["tickets"]] == [(created["key"], "created")]


# Ticket keys --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "keys"),
    [
        ("See REM-5.", ["REM-5"]),
        ("(AGE-18), REM-5 and REM-5 again", ["AGE-18", "REM-5"]),
        ("**REM-12** is done", ["REM-12"]),
        ("xREM-5 REM-5a REM-5-2 a/REM-5 REM-5.2 rem-5 REM-0 R-5 REM-", []),
        ("UTF-8 and SHA-256", ["UTF-8", "SHA-256"]),
    ],
)
def test_ticket_keys_are_whole_words(text: str, keys: list[str]):
    # Keys that look like tickets but aren't on the board (UTF-8) are dropped when resolved.
    assert ticket_keys(text) == keys


def test_only_keys_of_tickets_on_the_board_resolve_with_their_current_title(board, org):
    home, _, _ = board
    manager, oid = org
    mine = add(home, "Old name")
    org_task = manager.call(
        "", "user", {"action": "task_create", "organizationId": oid, "params": {"title": "Theirs"}}
    )
    org_key = ticket(home, "Theirs")["key"]
    assert org_task
    found = home.kanban.resolve([f"REM-{mine['number']}", org_key, "REM-999", "UTF-8", "bad key"])
    assert set(found) == {f"REM-{mine['number']}", org_key}
    assert found[f"REM-{mine['number']}"]["title"] == "Old name"
    assert found[f"REM-{mine['number']}"]["status"] == "todo"
    assert found[org_key]["scope"] == "organization" and found[org_key]["organization_id"] == oid
    # A rename shows at once; a declined proposal no longer resolves.
    home.kanban.edit(
        "user", {"ticket_id": mine["id"], "expected_revision": mine["revision"], "title": "New"}
    )
    assert home.kanban.resolve([f"REM-{mine['number']}"])[f"REM-{mine['number']}"]["title"] == "New"
    with home.store.connect() as db:
        current = get_ticket(db, mine["id"])
        current["proposal"] = {"state": "declined"}
        put_ticket(db, current)
    assert home.kanban.resolve([f"REM-{mine['number']}"]) == {}


def test_chat_polls_carry_the_tickets_named_and_change_when_one_is_renamed(api_server):
    request, service = api_server
    created = service.kanban.create("user", {"title": "Fix sync"}, "r1")["ticket"]
    with service.store.connect() as db:
        service.store.message(db, "Working on REM-1, not `REM-2` or REM-77.")
    first = request("GET", "/api/rem/conversation").json()
    assert set(first["tickets"]) == {"REM-1"}
    assert first["tickets"]["REM-1"]["title"] == "Fix sync"
    known = {"X-Gofer-Known": first["version"]}
    assert request("GET", "/api/rem/conversation", headers=known).json().get("unchanged") is True
    service.kanban.edit(
        "user", {"ticket_id": created["id"], "expected_revision": 1, "title": "Fix cloud sync"}
    )
    renamed = request("GET", "/api/rem/conversation", headers=known).json()
    assert renamed["tickets"]["REM-1"]["title"] == "Fix cloud sync"
    assert "tickets" in request("GET", "/api/rem/tasks").json()


def test_card_tickets_are_indexed_and_merged_by_strongest_role():
    refs = merge_refs(
        [{"ticket_id": "a", "key": "REM-1", "title": "A", "role": "created"}],
        [
            {"ticket_id": "a", "key": "REM-1", "title": "A2", "role": "worked"},
            {"ticket_id": "b", "key": "REM-2", "title": "B", "role": "nonsense"},
            {"key": "REM-3"},
        ],
    )
    assert refs == [
        {"ticket_id": "a", "key": "REM-1", "title": "A2", "role": "created"},
        {"ticket_id": "b", "key": "REM-2", "title": "B", "role": "updated"},
    ]
    assert message_ticket_keys(
        [{"body": "REM-1"}, {"body": "", "work": {"tickets": refs}, "details": "AGE-4"}]
    ) == ["AGE-4", "REM-1", "REM-2"]


def test_a_tickets_update_is_told_its_ticket_so_it_can_name_it(board):
    home, _, _ = board
    prompts: list[str] = []

    def generate(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        prompts.append(prompt)
        return {"summary": "Fixed it.", "details": "", "suggestions": [], "needs_user": False}

    home.generate = generate
    created = add(home, "Fix the login")
    at(home, 15)
    tick(home)
    settle(home, "Fix the login")
    wait_for(
        lambda: not tick(home) and any(m.get("kind") == "task_update" for m in ticket_cards(home)),
        8,
    )
    update = next(p for p in prompts if "proactive task update" in p)
    assert f"REM-{created['number']}: Fix the login" in update
    assert "name them by key" in update


def test_a_blocked_ticket_rem_picks_back_up_shows_in_chat(board):
    home, fake, _ = board
    set_options(home, max_threads=1)
    fake.failing.add("Broken")
    add(home, "Broken")
    at(home, 15)
    tick(home)
    settle(home, "Broken")
    fake.failing.discard("Broken")
    blocked = ticket(home, "Broken")
    assert blocked["status"] == "blocked"
    wait_for(lambda: not tick(home) and ticket(home, "Broken")["status"] != "blocked", 8)
    resumed = [m for m in messages(home) if m["id"].startswith("resumed-")]
    assert len(resumed) == 1
    assert resumed[0]["body"] == (
        f"Picked {blocked['key']} back up while you were away, with a follow-up in its thread."
    )
    assert resumed[0]["work"]["self_work"] == "ticket"
    assert [t["key"] for t in resumed[0]["work"]["tickets"]] == [blocked["key"]]
    settle(home, "Broken")
