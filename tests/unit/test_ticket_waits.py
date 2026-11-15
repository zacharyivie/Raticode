"""Tickets that wait for other tickets: the link, loops, cancelled links and self-work."""

from __future__ import annotations

import json
from typing import Any

import pytest

from gofer.ui.rem_kanban import KanbanRefused
from gofer.ui.rem_self_work import read_research
from tests.unit.test_rem_kanban import (
    add,
    at,
    set_options,
    settle,
    tick,
    ticket,
    ticket_or_none,
    wait_for,
)
from tests.unit.test_rem_kanban import api_server as api_server
from tests.unit.test_rem_kanban import application as application  # noqa: F401
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_kanban import linked as linked
from tests.unit.test_rem_kanban import org as org
from tests.unit.test_rem_kanban import phone as phone


def edit_waits(home: Any, title: str, waits: list[str], actor: str = "user") -> None:
    current = ticket(home, title)
    home.kanban.edit(
        actor,
        {
            "ticket_id": current["ticket_id"],
            "expected_revision": current["revision"],
            "waits_for": waits,
        },
    )


def move(home: Any, title: str, column: str) -> None:
    current = ticket(home, title)
    home.kanban.move(
        "user",
        {
            "ticket_id": current["ticket_id"],
            "expected_revision": current["revision"],
            "column": column,
        },
    )


# The link ------------------------------------------------------------------------


def test_a_ticket_lists_what_it_waits_for_by_key_or_id(board):
    home, _, _ = board
    first = add(home, "Polling triggers")
    second = add(home, "Trigger editor")
    created = home.kanban.create(
        "user",
        {"title": "Starter workflows", "waits_for": ["REM-1", second["id"]]},
        "new-starters",
    )["ticket"]
    assert created["waits_for"] == [first["id"], second["id"]]
    shown = ticket(home, "Starter workflows")
    assert [(w["key"], w["title"], w["status"]) for w in shown["waits_for"]] == [
        ("REM-1", "Polling triggers", "todo"),
        ("REM-2", "Trigger editor", "todo"),
    ]
    assert shown["waiting"] is True and shown["wait_notice"] == ""
    # The timeline shows the same, and Rem's chat sees the keys.
    timeline = home.kanban.timeline({"ticket_id": created["id"], "scope": "rem"})
    assert [w["key"] for w in timeline["ticket"]["waits_for"]] == ["REM-1", "REM-2"]
    seen = next(t for t in home.kanban.for_prompt()["tickets"] if t["key"] == "REM-3")
    assert seen["waits_for"] == ["REM-1", "REM-2"]
    # Clearing the list stops the wait.
    edit_waits(home, "Starter workflows", [])
    assert ticket(home, "Starter workflows")["waits_for"] == []
    assert ticket(home, "Starter workflows")["waiting"] is False


def test_waiting_ends_when_every_listed_ticket_is_done(board):
    home, _, _ = board
    add(home, "One")
    add(home, "Two")
    add(home, "After", waits_for=["REM-1", "REM-2"])
    move(home, "One", "done")
    assert ticket(home, "After")["waiting"] is True
    move(home, "Two", "done")
    assert ticket(home, "After")["waiting"] is False


@pytest.mark.parametrize(
    ("waits", "code", "message"),
    [
        (["REM-1"], "invalid", "A ticket can't wait for itself."),
        (["REM-99"], "not_found", "REM-99 isn't one of Rem's tickets."),
        (["ENG-3"], "not_found", "ENG-3 isn't one of Rem's tickets."),
        ("REM-2", "invalid", "Choose the tickets this one waits for."),
        ([f"REM-{n}" for n in range(21)], "invalid", "at most 20 tickets"),
    ],
)
def test_bad_links_are_refused(board, waits: Any, code: str, message: str):
    home, _, _ = board
    add(home, "Self")
    add(home, "Other")
    with pytest.raises(KanbanRefused) as refused:
        edit_waits(home, "Self", waits)
    assert refused.value.code == code and message in str(refused.value)
    assert ticket(home, "Self")["waits_for"] == []


def test_loops_are_refused_and_named(board):
    home, _, _ = board
    add(home, "A")
    add(home, "B", waits_for=["REM-1"])
    add(home, "C", waits_for=["REM-2"])
    with pytest.raises(KanbanRefused) as refused:
        edit_waits(home, "A", ["REM-3"])
    assert refused.value.code == "invalid"
    assert str(refused.value) == (
        "Tickets can't wait for each other in a loop: REM-1 → REM-3 → REM-2 → REM-1."
    )
    assert ticket(home, "A")["waits_for"] == []
    # A ticket may still wait for several that share a dependency: that isn't a loop.
    edit_waits(home, "C", ["REM-1", "REM-2"])
    assert [w["key"] for w in ticket(home, "C")["waits_for"]] == ["REM-1", "REM-2"]


def test_a_running_ticket_can_change_what_it_waits_for_but_not_its_plan(board):
    home, fake, _ = board
    fake.hold.clear()
    add(home, "Before")
    add(home, "Busy")
    move(home, "Busy", "in_progress")
    assert ticket(home, "Busy")["running"]
    edit_waits(home, "Busy", ["REM-1"])
    assert [w["key"] for w in ticket(home, "Busy")["waits_for"]] == ["REM-1"]
    current = ticket(home, "Busy")
    with pytest.raises(KanbanRefused, match="Stop this ticket's run"):
        home.kanban.edit(
            "user",
            {
                "ticket_id": current["ticket_id"],
                "expected_revision": current["revision"],
                "title": "Renamed",
                "waits_for": [],
            },
        )
    fake.hold.set()
    settle(home, "Busy")


# A cancelled link --------------------------------------------------------------------


def test_a_cancelled_link_shows_a_notice_and_leaves_the_ticket_for_the_user(board):
    home, _, _ = board
    add(home, "Foundation")
    add(home, "Building", waits_for=["REM-1"])
    move(home, "Foundation", "cancelled")
    waiting = ticket(home, "Building")
    assert waiting["status"] == "todo" and waiting["waiting"] is True
    assert waiting["wait_notice"] == (
        "REM-1 “Foundation” was cancelled, so this ticket won't start on its own. Remove it "
        "from Waits for, or cancel or start this ticket yourself."
    )
    timeline = home.kanban.timeline({"ticket_id": waiting["ticket_id"], "scope": "rem"})
    assert any(
        c["title"] == "REM-1, which this waits for, was cancelled" for c in timeline["comments"]
    )
    # Self-work leaves it where it is.
    at(home, 15)
    tick(home)
    tick(home)
    assert ticket(home, "Building")["status"] == "todo"
    assert not ticket(home, "Building")["running"]
    # A new link to a cancelled ticket is refused, but the one already there can stay
    # while the user changes the rest of the list.
    add(home, "Another")
    with pytest.raises(KanbanRefused, match="REM-1 is cancelled"):
        edit_waits(home, "Another", ["REM-1"])
    edit_waits(home, "Building", ["REM-1", "REM-3"])
    assert [w["key"] for w in ticket(home, "Building")["waits_for"]] == ["REM-1", "REM-3"]
    # Restoring the cancelled ticket clears the notice; it waits as before.
    move(home, "Foundation", "backlog")
    assert ticket(home, "Building")["wait_notice"] == ""


def test_a_declined_proposal_counts_as_cancelled_for_its_waiters(board):
    from tests.unit.test_rem_kanban import add_proposal

    home, _, _ = board
    idea = add_proposal(home, "Idea")
    add(home, "Needs the idea", waits_for=[idea["id"]])
    home.kanban.decide(
        "user", {"ticket_id": idea["id"], "expected_revision": idea["revision"]}, "decline"
    )
    # The declined proposal leaves the board, but the waiting ticket still names it.
    assert ticket_or_none(home, "Idea") is None
    waiting = ticket(home, "Needs the idea")
    assert [w["title"] for w in waiting["waits_for"]] == ["Idea"]
    assert "was cancelled" in waiting["wait_notice"]


# Self-work -------------------------------------------------------------------------


def test_self_work_skips_a_waiting_ticket_until_what_it_waits_for_is_done(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=2)
    add(home, "First", priority="low")
    add(home, "Second", priority="critical", waits_for=["REM-1"])
    at(home, 15)
    tick(home)
    tick(home)
    # Second has the higher priority and a free thread, but it waits for First.
    assert ticket(home, "First")["running"]
    assert ticket(home, "Second")["status"] == "todo" and not ticket(home, "Second")["running"]
    state = home.self_work.view()["state"]
    assert state["waiting_on_tickets"] == 1 and state["todo_remaining"] == 1
    fake.hold.set()
    settle(home, "First")
    assert ticket(home, "First")["status"] == "done"
    settle(home, "Second")
    assert ticket(home, "Second")["status"] == "done"
    order = [
        c["workflow"]["chatThreadTitle"].removeprefix("Rem task: ")
        for c in fake.calls
        if "independent read-only reviewer" not in c["agent_instructions"]
    ]
    assert order.index("First") < order.index("Second")


def test_self_work_does_not_pull_a_waiting_backlog_ticket(board):
    from tests.unit.test_rem_kanban import ranked

    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=2, auto_pull_backlog=True)
    add(home, "Blocker", status="backlog")
    add(home, "Pulled later", status="backlog", priority="critical")
    edit_waits(home, "Pulled later", ["REM-1"])
    # Ranked after the edit: changing what it waits for leaves a ranking alone anyway.
    ranked(home, "Blocker")
    ranked(home, "Pulled later")
    at(home, 15)
    for _ in range(3):
        tick(home)
    # Two free threads: the blocker is pulled, the higher-ranked waiting ticket isn't.
    assert ticket(home, "Blocker")["running"]
    assert ticket(home, "Pulled later")["status"] == "backlog"
    fake.hold.set()
    settle(home, "Blocker")


def test_the_user_can_still_start_a_waiting_ticket(board):
    home, fake, _ = board
    add(home, "Not yet")
    add(home, "Start anyway", waits_for=["REM-1"])
    move(home, "Start anyway", "in_progress")
    assert ticket(home, "Start anyway")["status"] in {"in_progress", "in_review", "done"}
    settle(home, "Start anyway")
    assert ticket(home, "Not yet")["status"] == "todo"


def test_a_claim_rechecks_the_wait_so_a_race_cannot_start_it(board):
    home, _, _ = board
    add(home, "Gate")
    created = add(home, "Behind", waits_for=["REM-1"])
    with home.store.read() as db:
        config = home.store.setting(db, "config")
    assert home.self_work._begin_ticket(created["id"], config) is False
    assert ticket(home, "Behind")["status"] == "todo"


# Rem sets the link -------------------------------------------------------------------


def test_rem_sets_the_link_when_it_proposes_from_research(board):
    home, fake, _ = board
    add(home, "Polling triggers", status="backlog")
    assert read_research(
        '```json\n{"proposals":[{"title":"x","waits_for":["REM-1"]}]}\n```'
    ).proposals[0].waits_for == ["REM-1"]
    fake.answers["Research new features for project"] = (
        "Findings.\n```json\n"
        + json.dumps(
            {
                "proposals": [
                    {"title": "Webhook triggers", "description": "d", "why": "w"},
                    {
                        "title": "Starter workflows",
                        "description": "d",
                        "why": "w",
                        "waits_for": ["REM-1", "Webhook triggers", "REM-404", "Nothing like it"],
                    },
                    # A repeat of a board ticket isn't added, and stands for that ticket.
                    {"title": "Add polling triggers", "description": "d", "why": "w"},
                    {
                        "title": "Trigger gallery",
                        "description": "d",
                        "why": "w",
                        "waits_for": ["Add polling triggers"],
                    },
                ],
                "duplicates": [],
            }
        )
        + "\n```"
    )
    at(home, 15)
    tick(home)
    wait_for(lambda: not tick(home) and ticket_or_none(home, "Trigger gallery") is not None, 8)
    starters = ticket(home, "Starter workflows")
    # Unknown names are dropped; the proposal itself is kept.
    assert [w["title"] for w in starters["waits_for"]] == ["Polling triggers", "Webhook triggers"]
    assert starters["proposed"] is True
    assert [w["title"] for w in ticket(home, "Trigger gallery")["waits_for"]] == [
        "Polling triggers"
    ]
    assert ticket_or_none(home, "Add polling triggers") is None
    # The research run was shown the board's keys, so it can name them.
    with home.store.read() as db:
        prompts = [
            json.loads(r[0])["recipe"]["prompt"] for r in db.execute("SELECT body FROM runs")
        ]
    assert any('"key":"REM-1"' in p.replace(" ", "") and "waits_for" in p for p in prompts)


def test_rem_sets_the_link_from_chat(board):
    from gofer.ui.rem_home_models import TicketRequest

    home, _, _ = board
    add(home, "Base")
    text, _ = home.kanban.apply_changes(
        [TicketRequest(action="create", title="On top", waits_for=["REM-1"])], "chat-1"
    )
    assert "Added" in text
    assert [w["key"] for w in ticket(home, "On top")["waits_for"]] == ["REM-1"]
    loop = TicketRequest(
        action="edit",
        ticket_id=ticket(home, "Base")["ticket_id"],
        expected_revision=ticket(home, "Base")["revision"],
        waits_for=["REM-2"],
    )
    text, _ = home.kanban.apply_changes([loop], "chat-2")
    assert "loop" in text and ticket(home, "Base")["waits_for"] == []


# Organization tickets ---------------------------------------------------------------


def test_organization_tickets_wait_through_their_own_dependencies(board, org):
    home, _, _ = board
    manager, oid = org
    created = [
        manager.call(
            "", "user", {"action": "task_create", "organizationId": oid, "params": {"title": t}}
        )
        for t in ("Schema", "Migration")
    ]
    tickets = {t["title"]: t for t in home.kanban.board()["tickets"] if t["scope"] != "rem"}
    first, second = tickets["Schema"], tickets["Migration"]
    home.kanban.edit(
        "user",
        {
            "ticket_id": second["ticket_id"],
            "scope": "organization",
            "organization_id": oid,
            "expected_revision": second["revision"],
            "waits_for": [first["key"]],
        },
    )
    task = next(
        t for t in manager.store.get("", oid)["runtime"]["tasks"] if t["title"] == "Migration"
    )
    assert task["dependsOn"] == [created[0]["id"]]
    shown = next(t for t in home.kanban.board()["tickets"] if t["title"] == "Migration")
    assert [w["key"] for w in shown["waits_for"]] == [first["key"]] and shown["waiting"]
    # Its organization refuses the same loops, and Rem's tickets aren't on its board.
    schema = next(t for t in home.kanban.board()["tickets"] if t["title"] == "Schema")
    payload = {
        "ticket_id": schema["ticket_id"],
        "scope": "organization",
        "organization_id": oid,
        "expected_revision": schema["revision"],
    }
    with pytest.raises(KanbanRefused, match="loop"):
        home.kanban.edit("user", {**payload, "waits_for": [shown["key"]]})
    add(home, "Rem's own")
    with pytest.raises(KanbanRefused, match="isn't one of Eng’s tickets"):
        home.kanban.edit("user", {**payload, "waits_for": ["REM-1"]})


# Phone and HTTP --------------------------------------------------------------------


def test_phone_sets_and_clears_what_a_ticket_waits_for(linked):
    _, _, _, send, home, _, _, _ = linked
    add(home, "Phone first")
    later = add(home, "Phone later")
    edited = send(
        "desktop.action",
        "kanban.ticket",
        {
            "action": "edit",
            "ticket_id": later["id"],
            "scope": "rem",
            "expected_revision": later["revision"],
            "waits_for": ["REM-1"],
        },
    )
    assert edited["ok"], edited
    shown = next(t for t in edited["value"]["tickets"] if t["title"] == "Phone later")
    assert [w["key"] for w in shown["waits_for"]] == ["REM-1"] and shown["waiting"] is True
    looped = send(
        "desktop.action",
        "kanban.ticket",
        {
            "action": "edit",
            "ticket_id": ticket(home, "Phone first")["ticket_id"],
            "scope": "rem",
            "expected_revision": ticket(home, "Phone first")["revision"],
            "waits_for": ["REM-2"],
        },
    )
    assert looped["ok"] is False and looped["code"] == "invalid"
    assert "loop" in looped["detail"]
    read = send("desktop.read", "kanban.board")["value"]
    assert next(t for t in read["tickets"] if t["title"] == "Phone later")["waits_for"]


def test_http_edit_carries_waits_for(api_server):
    request, _ = api_server
    for title in ("H1", "H2"):
        assert (
            request(
                "POST",
                "/api/rem/kanban/ticket",
                body={"action": "create", "title": title, "request_id": f"r-{title}"},
            ).status
            == 200
        )
    board = request("GET", "/api/rem/kanban").json()["board"]
    second = next(t for t in board["tickets"] if t["title"] == "H2")
    saved = request(
        "POST",
        "/api/rem/kanban/ticket",
        body={
            "action": "edit",
            "ticket_id": second["ticket_id"],
            "expected_revision": second["revision"],
            "waits_for": ["REM-1"],
        },
    )
    assert saved.status == 200, saved.json()
    after = next(t for t in saved.json()["board"]["tickets"] if t["title"] == "H2")
    assert [w["key"] for w in after["waits_for"]] == ["REM-1"]
    looped = request(
        "POST",
        "/api/rem/kanban/ticket",
        body={
            "action": "edit",
            "ticket_id": next(t for t in board["tickets"] if t["title"] == "H1")["ticket_id"],
            "expected_revision": 1,
            "waits_for": [second["key"]],
        },
    )
    assert looped.status == 400 and "loop" in looped.json()["error"]
