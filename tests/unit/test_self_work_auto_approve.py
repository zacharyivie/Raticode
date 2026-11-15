"""Self-work approving its own backlog tickets: off by default, only the user turns it on.

Fake providers and isolated data, through the same fixtures as the board's own tests.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from gofer.ui.headless import agent_request_refusal
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_models import SelfWorkRequest
from gofer.ui.rem_kanban import KanbanRefused
from tests.unit.test_device_application import application as application
from tests.unit.test_device_desktop_control import phone as phone
from tests.unit.test_rem_kanban import (
    add,
    add_proposal,
    at,
    set_options,
    settle,
    tick,
    ticket,
    ticket_or_none,
    wait_for,
)
from tests.unit.test_rem_kanban import (
    board as board,
)
from tests.unit.test_rem_kanban import (
    linked as linked,
)
from tests.unit.test_rem_kanban import (
    org as org,
)

RESEARCH = "Research new features for project"


def ideas(*titles: str, duplicates: list[dict[str, str]] | None = None) -> str:
    body = {
        "proposals": [
            {"title": title, "description": f"Build {title}", "why": f"Users asked for {title}"}
            for title in titles
        ],
        "duplicates": duplicates or [],
    }
    return f"Findings.\n```json\n{json.dumps(body)}\n```"


def turn_on(home: RemHome) -> dict[str, Any]:
    revision = home.self_work.view()["settings"]["revision"]
    return home.self_work.update_settings(
        {
            "auto_approve_proposals": True,
            "confirm_auto_approve": True,
            "expected_revision": revision,
        }
    )


def research_done(home: RemHome, *titles: str) -> None:
    """Tick until research has turned these ideas into tickets."""
    wait_for(lambda: not tick(home) and all(ticket_or_none(home, t) for t in titles), 8)


class Rankings(dict[str, str]):
    """Answers a ranking run with the backlog as it is when the run finishes."""

    def __init__(self, home: RemHome, judge: Any) -> None:
        super().__init__()
        self.home = home
        self.judge = judge

    def get(self, key: Any, default: Any = None) -> Any:
        if str(key).startswith("Rank the backlog"):
            ranks = [
                {"ticket_id": t["ticket_id"], **self.judge(t)}
                for t in self.home.kanban.board()["tickets"]
                if t["status"] == "backlog"
            ]
            return f"```json\n{json.dumps({'tickets': ranks})}\n```"
        return super().get(key, default)


def timeline(home: RemHome, item: dict[str, Any]) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = home.kanban.timeline(
        {"ticket_id": item["ticket_id"], "scope": "rem"}
    )["comments"]
    return comments


# Off by default ----------------------------------------------------------------------


def test_off_by_default_ideas_still_wait_for_approval(board):
    home, fake, _ = board
    view = home.self_work.view()
    assert view["settings"]["auto_approve_proposals"] is False
    assert "auto_approve_session_max" not in view["limits"]
    assert view["state"]["auto_approved_session"] == 0
    fake.answers[RESEARCH] = ideas("Voice shortcuts")
    at(home, 15)
    tick(home)
    research_done(home, "Voice shortcuts")
    idea = ticket(home, "Voice shortcuts")
    assert idea["proposed"] is True and idea["auto_approved"] is False
    assert idea["status"] == "backlog"
    assert not [c for c in timeline(home, idea) if "Auto-approved" in c["title"]]
    added = home.self_work.view()["report"]["tickets_added"]
    assert [(a["proposed"], a["auto_approved"]) for a in added] == [(True, False)]
    assert "Proposed 1 for your approval" in home.self_work.view()["report"]["summary"]


# On ---------------------------------------------------------------------------------


def test_on_creates_approved_backlog_tickets_marked_as_self_works(board):
    home, fake, _ = board
    turn_on(home)
    fake.answers[RESEARCH] = ideas("Voice shortcuts")
    at(home, 15)
    tick(home)
    research_done(home, "Voice shortcuts")
    idea = ticket(home, "Voice shortcuts")
    # Approved, in Backlog, not waiting for anyone; backlog pulling is off, so it waits there.
    assert idea["proposed"] is False and idea["auto_approved"] is True
    assert idea["status"] == "backlog" and not idea["running"]
    assert idea["proposal_reason"] == "Users asked for Voice shortcuts"
    entries = timeline(home, idea)
    assert [e["title"] for e in entries] == ["Auto-approved by self-work"]
    assert "Settings > Rem" in entries[0]["body"] and entries[0]["author"] == "rem"
    assert entries[0]["thread_id"]
    view = home.self_work.view()
    assert view["state"]["auto_approved_session"] == 1
    assert view["state"]["proposals_waiting"] == 0
    added = view["report"]["tickets_added"]
    assert [(a["proposed"], a["auto_approved"]) for a in added] == [(False, True)]
    assert "Auto-approved 1 ticket" in view["report"]["summary"]
    # No "waiting for your approval" alert for an idea that isn't waiting.
    assert not [a for a in home.self_work.alerts_after(0)["alerts"] if a["reason"] == "proposals"]
    # The chat update says so and names the ticket.
    with home.store.read() as db:
        bodies = [json.loads(r[0])["body"] for r in db.execute("SELECT body FROM messages")]
    assert any("approved on my own because auto-approval is on" in b for b in bodies)
    # It stays the user's: editable, movable and cancellable like any ticket.
    home.kanban.edit(
        "user",
        {"ticket_id": idea["ticket_id"], "expected_revision": idea["revision"], "title": "Voice"},
    )
    edited = ticket(home, "Voice")
    home.kanban.move(
        "user",
        {
            "ticket_id": edited["ticket_id"],
            "expected_revision": edited["revision"],
            "column": "cancelled",
        },
    )
    assert ticket(home, "Voice")["status"] == "cancelled"


def test_rem_still_cannot_mark_an_auto_approved_ticket_done(board):
    home, fake, _ = board
    turn_on(home)
    fake.answers[RESEARCH] = ideas("Voice shortcuts")
    at(home, 15)
    tick(home)
    research_done(home, "Voice shortcuts")
    idea = ticket(home, "Voice shortcuts")
    with pytest.raises(KanbanRefused):
        home.kanban.move(
            "rem",
            {
                "ticket_id": idea["ticket_id"],
                "expected_revision": idea["revision"],
                "column": "done",
            },
        )
    # And Rem can't "approve" it again or decide anything on the user's behalf.
    with pytest.raises(KanbanRefused):
        home.kanban.decide(
            "rem",
            {"ticket_id": idea["ticket_id"], "expected_revision": idea["revision"]},
            "approve",
        )


def test_with_backlog_pulling_an_approved_idea_is_ranked_then_worked(board):
    home, fake, _ = board
    turn_on(home)
    set_options(home, auto_pull_backlog=True, max_threads=1)
    # Its ranking is what lets it be pulled, so an obsolete idea can still be left out.
    fake.answers = Rankings(home, lambda t: {"impact": 4, "relevance": 4})
    fake.answers[RESEARCH] = ideas("Voice shortcuts")
    at(home, 15)
    tick(home)
    research_done(home, "Voice shortcuts")
    settle(home, "Voice shortcuts", states=("done",))
    assert ticket(home, "Voice shortcuts")["status"] == "done"
    assert any(
        c.get("finished") and c["workflow"]["chatThreadTitle"].endswith("Voice shortcuts")
        for c in fake.calls
    )


def test_a_ranking_still_leaves_out_an_obsolete_approved_idea(board):
    home, fake, _ = board
    turn_on(home)
    set_options(home, auto_pull_backlog=True, max_threads=1)
    fake.answers = Rankings(
        home, lambda t: {"impact": 1, "relevance": 1, "skip": True, "reason": "Already shipped"}
    )
    fake.answers[RESEARCH] = ideas("Old plan")
    at(home, 15)
    tick(home)
    research_done(home, "Old plan")
    wait_for(lambda: not tick(home) and ticket(home, "Old plan")["skip_reason"], 8)
    for _ in range(3):
        tick(home)
    assert ticket(home, "Old plan")["status"] == "backlog"
    assert ticket(home, "Old plan")["skip_reason"] == "Already shipped"
    assert not any(c["workflow"]["chatThreadTitle"].endswith("Old plan") for c in fake.calls)


# Only the user turns it on -----------------------------------------------------------


def test_only_a_confirmed_user_change_turns_it_on(board):
    home, _, _ = board
    # Without the confirmation the user saw, the backend refuses and nothing changes.
    with pytest.raises(KanbanRefused, match="Confirm first"):
        home.self_work.update_settings({"auto_approve_proposals": True, "expected_revision": 0})
    with pytest.raises(KanbanRefused, match="on or off"):
        home.self_work.update_settings(
            {"auto_approve_proposals": "yes", "confirm_auto_approve": True, "expected_revision": 0}
        )
    assert home.self_work.view()["settings"]["auto_approve_proposals"] is False
    # Rem's chat changes only the switch and windows: the request can't even name it.
    with pytest.raises(ValidationError):
        SelfWorkRequest.model_validate({"action": "enable", "auto_approve_proposals": True})
    with home.store.connect() as db:
        home.self_work.apply_chat(db, "enable")
    assert home.self_work.view()["settings"]["auto_approve_proposals"] is False
    # Agent tokens can't reach the settings at all.
    assert agent_request_refusal("PUT", "/api/rem/kanban/settings") is not None
    # Turning it off needs no confirmation, and each change is noted in the report.
    turn_on(home)
    assert home.self_work.view()["settings"]["auto_approve_proposals"] is True
    set_options(home, auto_approve_proposals=False)
    notes = [n["text"] for n in home.self_work.view()["report"]["notes"]]
    assert any(n.startswith("You turned on auto-approval") for n in notes)
    assert any(n.startswith("You turned off auto-approval") for n in notes)
    # Rem knows the setting for status questions, read-only.
    assert home.self_work.for_prompt()["auto_approve_proposals"] is False


def test_the_phone_needs_the_confirmation_too(linked):
    _, _, _, send, home, _, _, _ = linked
    refused = send(
        "desktop.action",
        "selfwork.settings",
        {"auto_approve_proposals": True, "expected_revision": 0},
    )
    assert refused["code"] == "invalid" and "Confirm first" in refused["detail"]
    changed = send(
        "desktop.action",
        "selfwork.settings",
        {"auto_approve_proposals": True, "confirm_auto_approve": True, "expected_revision": 0},
    )
    assert changed["value"]["settings"]["auto_approve_proposals"] is True
    view = send("desktop.read", "selfwork.read")["value"]
    assert view["state"]["auto_approved_session"] == 0


# Caps, duplicates and project bounds -------------------------------------------------


def test_there_is_no_cap_it_keeps_approving_until_self_work_ends(board):
    """Regression: auto-approval used to stop at 10 tickets a session."""
    home, fake, _ = board
    turn_on(home)
    set_options(home, max_threads=1)
    fake.hold.clear()
    fake.answers[RESEARCH] = ideas("Alpha export", "Beta import", "Gamma sync")
    fake.answers["Research reliability problems in project"] = ideas("Delta retry", "Echo logs")
    at(home, 15)
    tick(home)
    wait_for(lambda: home.self_work.view()["state"]["active_threads"] > 0)
    with home.store.read() as db:
        session = home.store.setting(db, "self_work_state")["session"]["id"]
    # Twelve already approved this session, past the old cap of ten.
    for number in range(12):
        add_proposal(
            home,
            f"Earlier approved idea {number}",
            proposal={"state": "approved", "by": "self_work", "round": session},
        )
    assert home.self_work.view()["state"]["auto_approved_session"] == 12
    fake.hold.set()
    # Two research runs in the same session; every new idea is approved.
    research_done(home, "Alpha export", "Beta import", "Gamma sync", "Delta retry", "Echo logs")
    for title in ("Alpha export", "Beta import", "Gamma sync", "Delta retry", "Echo logs"):
        item = ticket(home, title)
        assert item["auto_approved"] is True and item["proposed"] is False, title
    view = home.self_work.view()
    assert view["state"]["auto_approved_session"] == 17
    assert view["state"]["proposals_waiting"] == 0
    assert not any("limit" in n["text"] for n in view["report"]["notes"])
    with home.store.read() as db:
        bodies = [json.loads(r[0])["body"] for r in db.execute("SELECT body FROM messages")]
    assert not any("until you approve" in b for b in bodies)
    assert not any("a session" in b for b in bodies)


def test_waiting_proposals_do_not_stop_research_while_auto_approval_is_on(board):
    home, fake, _ = board
    for number in range(12):
        add_proposal(home, f"Waiting idea {number}")
    turn_on(home)
    fake.answers[RESEARCH] = ideas("Fresh idea")
    at(home, 15)
    tick(home)
    research_done(home, "Fresh idea")
    assert ticket(home, "Fresh idea")["auto_approved"] is True
    # The ones that were already waiting stay the user's to decide.
    assert all(ticket(home, f"Waiting idea {n}")["proposed"] for n in range(12))


def test_duplicates_are_still_skipped_and_closed(board):
    home, fake, _ = board
    mine = add(home, "Dark mode", status="backlog")
    earlier = {"state": "approved", "by": "self_work", "round": "earlier"}
    first = add_proposal(home, "Voice control", proposal=earlier)
    copy = add_proposal(home, "Hands-free speaking", proposal=dict(earlier))
    assert ticket(home, "Hands-free speaking")["auto_approved"] is True
    turn_on(home)
    set_options(home, max_threads=1)
    # The idea repeating the user's ticket is skipped. The research run may close an idea
    # self-work approved itself as a duplicate, but never the user's own ticket.
    fake.answers[RESEARCH] = ideas(
        "Add dark mode",
        "Crash reports",
        duplicates=[
            {"ticket_id": copy["id"], "duplicate_of": first["id"], "reason": "Same"},
            {"ticket_id": mine["id"], "duplicate_of": first["id"], "reason": "Same"},
        ],
    )
    at(home, 15)
    tick(home)
    research_done(home, "Crash reports")
    assert sum(t["title"] == "Add dark mode" for t in home.kanban.board()["tickets"]) == 0
    closed = ticket(home, "Hands-free speaking")
    assert closed["status"] == "cancelled" and closed["duplicate_of"] == first["id"]
    assert ticket(home, "Dark mode")["status"] == "backlog"


def test_a_merge_keeps_the_users_ticket_over_an_auto_approved_one(board):
    home, _, _ = board
    add_proposal(home, "Dark mode", proposal={"state": "approved", "by": "self_work", "round": "r"})
    add(home, "Support dark mode", status="backlog")
    turn_on(home)
    at(home, 15)
    tick(home)
    assert ticket(home, "Dark mode")["status"] == "cancelled"
    assert ticket(home, "Support dark mode")["status"] == "backlog"


def test_ideas_from_a_project_rem_lost_are_discarded_not_approved(board):
    home, fake, project = board
    turn_on(home)
    fake.hold.clear()
    fake.answers[RESEARCH] = ideas("Voice shortcuts")
    at(home, 15)
    tick(home)
    wait_for(lambda: home.self_work.view()["state"]["active_threads"] > 0)
    home.kanban.set_participant_projects(
        "user", {"participant_id": "rem", "projects": [], "expected_revision": 1}
    )
    fake.hold.set()
    wait_for(lambda: not tick(home) and not home.self_work.view()["state"]["active_threads"], 8)
    for _ in range(3):
        tick(home)
    assert ticket_or_none(home, "Voice shortcuts") is None
    assert home.self_work.view()["state"]["auto_approved_session"] == 0
    assert project.is_dir()


# Toggling mid-session -----------------------------------------------------------------


def test_turning_it_on_mid_session_leaves_waiting_proposals_alone(board):
    home, fake, _ = board
    earlier = add_proposal(home, "Earlier idea")
    set_options(home, max_threads=1)
    fake.hold.clear()
    fake.answers[RESEARCH] = ideas("Voice shortcuts")
    at(home, 15)
    tick(home)
    wait_for(lambda: home.self_work.view()["state"]["active_threads"] > 0)
    # Turned on while research runs: what it finds next is approved, the old one isn't.
    turn_on(home)
    fake.hold.set()
    research_done(home, "Voice shortcuts")
    assert ticket(home, "Voice shortcuts")["auto_approved"] is True
    kept = ticket(home, "Earlier idea")
    assert kept["proposed"] is True and kept["ticket_id"] == earlier["id"]


def test_turning_it_off_mid_session_stops_approving_and_keeps_what_it_approved(board):
    home, fake, _ = board
    kept = add_proposal(
        home,
        "Voice control",
        proposal={"state": "approved", "by": "self_work", "round": "earlier"},
    )
    turn_on(home)
    set_options(home, max_threads=1)
    fake.answers[RESEARCH] = ideas("Crash reports")
    fake.hold.clear()
    at(home, 15)
    tick(home)
    wait_for(lambda: home.self_work.view()["state"]["active_threads"] > 0)
    # Started with auto-approval on, finished after the user turned it off.
    set_options(home, auto_approve_proposals=False)
    fake.hold.set()
    research_done(home, "Crash reports")
    late = ticket(home, "Crash reports")
    assert late["proposed"] is True and late["auto_approved"] is False
    # What it approved earlier stays approved, on the board, until the user decides.
    before = ticket(home, "Voice control")
    assert before["ticket_id"] == kept["id"]
    assert before["auto_approved"] is True and before["status"] == "backlog"
