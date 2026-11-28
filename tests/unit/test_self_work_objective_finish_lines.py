"""Open-ended objectives are the user's; every objective Rem writes on its own has a finish line.

An open-ended goal Rem picked for itself kept all of self-work's attention for as long as it
ran (2026-11-24). Now the planner and any turn the user didn't start need a concrete, checkable
``done_when``: an objective without one is refused, the planner is asked once to repair its
answer, and an open-ended one Rem wrote before this rule gets a finish line at the next session's
start. Objectives from the user's own words in chat, and the user's own, may stay open-ended.
Fake providers and isolated data only.
"""

from typing import Any

import pytest

from gofer.ui import rem_objectives as objectives
from gofer.ui.rem_home import RemHome
from tests.unit.test_rem_kanban import at, tick
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_self_work_check_answers import planner_answers
from tests.unit.test_self_work_objectives import (
    change,
    decide_objectives,
    listed,
    planned,
    planner,
    stored_list,
    texts,
)

HARDEN = "Harden existing logic in preparation for the release"
STUDIO = "Raise UX quality of the workflow studio"
STUDIO_DONE = "Every studio usability ticket in Backlog is done or cancelled"


def said(home: RemHome) -> list[str]:
    return [str(m["body"]) for m in home.store.conversation()["messages"]]


def test_a_user_requested_open_ended_objective_is_kept_but_rems_own_needs_a_finish_line(board):
    home, _, _ = board
    # On its own, whether in self-work or a turn the user didn't start, Rem can't write one
    # without a finish line, even to an empty list.
    for source in ("self_work", "own"):
        with pytest.raises(objectives.ObjectiveRefused, match="finish line"):
            change(home, {"action": "add", "text": STUDIO}, actor="rem", source=source)
        # A finish line too vague to check is refused as well.
        with pytest.raises(objectives.ObjectiveRefused, match="too vague"):
            change(
                home,
                {"action": "add", "text": STUDIO, "done_when": "When done"},
                actor="rem",
                source=source,
            )
    assert texts(home) == []
    # The user asks in chat for open-ended work: Rem writes it as asked, with no finish line.
    reply = decide_objectives(
        home,
        "Start self work and focus on hardening existing logic in preparation of the release",
        {"action": "set", "items": [{"text": HARDEN}]},
    )
    assert "Set the self-work objectives; 1 is open" in reply
    [item] = listed(home)
    assert (item["text"], item["done_when"], item["source"]) == (HARDEN, "", "chat")
    with home.store.read() as db:
        shown = objectives.for_prompt(objectives.read(db, home.store))
    assert shown[0].get("open_ended") is True and "needs_done_when" not in shown[0]
    # Asked for by the user, it stays open-ended: no planner gives it a finish line.
    assert not objectives.needs_finish_line(listed(home)[0])
    # And while it is open Rem adds nothing beside it on its own, finish line or not.
    for source in ("self_work", "own"):
        with pytest.raises(objectives.ObjectiveRefused, match="still open"):
            change(
                home,
                {"action": "add", "text": STUDIO, "done_when": STUDIO_DONE},
                actor="rem",
                source=source,
            )
    assert texts(home) == [HARDEN]
    # The user may still write open-ended objectives of their own.
    change(home, {"action": "add", "text": "Keep the docs honest"}, actor="user")
    assert "Keep the docs honest" in texts(home)


def test_a_chat_turn_the_user_didnt_start_is_rem_on_its_own(board):
    home, _, _ = board
    request = objectives.ObjectivesRequest(
        action="add", items=[objectives.NewObjective(text=STUDIO)]
    )
    with home.store.connect() as db:
        result, sentence = objectives.apply_request(db, home.store, request, from_user=False)
    assert result is None and "finish line" in sentence
    with home.store.connect() as db:
        result, _ = objectives.apply_request(db, home.store, request, from_user=True)
    assert result is not None and texts(home) == [STUDIO]


def test_a_turn_the_user_didnt_start_adds_nothing_while_an_objective_is_open(board):
    home, _, _ = board
    change(home, {"action": "add", "text": HARDEN}, actor="user")
    request = objectives.ObjectivesRequest(
        action="add", items=[objectives.NewObjective(text=STUDIO, done_when=STUDIO_DONE)]
    )
    # A task finishing, a schedule: Rem's own idea, with a finish line, is still refused.
    with home.store.connect() as db:
        result, sentence = objectives.apply_request(db, home.store, request, from_user=False)
    assert result is None and "still open" in sentence
    assert texts(home) == [HARDEN]
    # The user asking for it, here or on their phone, adds it.
    with home.store.connect() as db:
        result, _ = objectives.apply_request(db, home.store, request, from_user=True)
    assert result is not None and texts(home) == [STUDIO, HARDEN]


def test_the_planner_is_told_to_give_finish_lines_and_repairs_an_answer_without_them(board):
    home, _, _ = board
    open_ended = {"objectives": [{"text": STUDIO}]}
    repaired = {"objectives": [{"text": STUDIO, "done_when": STUDIO_DONE}]}
    prompts = planner_answers(home, open_ended, repaired)
    at(home, 15)
    tick(home)
    planned(home)
    assert "Open-ended objectives are reserved for the user" in prompts[0]
    assert "checkable done_when" in prompts[0]
    # One repair request names the objective that lacks a finish line.
    assert len(prompts) == 2
    assert f"“{STUDIO}”: Rem's own objectives need a finish line" in prompts[1]
    [item] = listed(home)
    assert (item["text"], item["done_when"], item["created_by"]) == (STUDIO, STUDIO_DONE, "rem")


def test_a_planner_objective_still_without_a_finish_line_is_left_out_and_said(board):
    home, _, _ = board
    answer = {
        "objectives": [
            {"text": STUDIO},
            {"text": "Optimize performance of file handling", "done_when": "x"},
        ]
    }
    prompts = planner_answers(home, answer, answer)
    at(home, 15)
    tick(home)
    planned(home)
    assert len(prompts) == 2
    assert texts(home) == []
    notes = " ".join(said(home))
    assert f"Left out “{STUDIO}” (Rem's own objectives need a finish line" in notes
    assert "too vague to check" in notes


def test_rems_own_open_ended_objectives_from_before_get_a_finish_line_at_session_start(board):
    home, _, _ = board
    stamp = "2026-11-20T10:00:00+00:00"

    def item(identity: str, text: str, **fields: Any) -> dict[str, Any]:
        return {
            "id": identity,
            "text": text,
            "done_when": "",
            "done": False,
            "created_at": stamp,
            "updated_at": stamp,
            **fields,
        }

    stored_list(
        home,
        [
            item(
                "mine",
                "Improve reliability of the Second Brain tools",
                created_by="rem",
                source="self_work",
            ),
            item("asked", HARDEN, created_by="rem", source="chat"),
            item("users", "Keep the docs honest", created_by="user", source="user"),
        ],
    )
    finish = "Every Second Brain tool error in this week's logs has a fix on dev"
    prompts = planner(
        home,
        {
            "objectives": [{"text": "Something new and unasked", "done_when": STUDIO_DONE}],
            "finish_lines": [
                {"id": "mine", "done_when": finish},
                # The user's own stay open-ended, whatever the planner says.
                {"id": "asked", "done_when": "All release hardening tickets are done"},
                {"id": "users", "done_when": "All docs pages were reviewed this week"},
            ],
        },
    )
    at(home, 15)
    tick(home)
    planned(home)
    assert len(prompts) == 1
    assert "have no finish line (needs_done_when" in prompts[0]
    assert prompts[0].count('"needs_done_when": true') == 1
    by_id = {i["id"]: i for i in listed(home)}
    assert by_id["mine"]["done_when"] == finish
    assert by_id["asked"]["done_when"] == "" and by_id["users"]["done_when"] == ""
    # This planning only gives finish lines; it adds nothing new.
    assert set(by_id) == {"mine", "asked", "users"}
    notes = " ".join(said(home))
    assert "Gave my own open-ended objectives a finish line" in notes


def test_the_user_making_rems_objective_open_ended_keeps_it_that_way(board):
    home, _, _ = board
    change(
        home,
        {"action": "add", "text": STUDIO, "done_when": STUDIO_DONE},
        actor="rem",
        source="self_work",
    )
    identity = listed(home)[0]["id"]
    change(home, {"action": "edit", "id": identity, "done_when": ""}, actor="user")
    assert not objectives.needs_finish_line(listed(home)[0])
    with pytest.raises(objectives.ObjectiveRefused) as refused:
        change(
            home,
            {"action": "edit", "id": identity, "done_when": STUDIO_DONE},
            actor="rem",
            source="self_work",
        )
    assert refused.value.code == "unauthorized"
