"""Plan with me: grilling through decision requests, then thin-slice tickets."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import rem_decisions as decisions
from gofer.ui import rem_plans as plans
from gofer.ui.rem_home import REPLY_RULES, RemHome, queue_user_message
from gofer.ui.rem_home_models import Decision, TicketRequest
from gofer.ui.rem_kanban import _batch_waits, get_ticket
from tests.unit.test_rem_decisions import Store
from tests.unit.test_rem_home import home as home

TOPICS = ["plan:store", "plan:auth", "plan:sync", "plan:ui", "plan:rollout"]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    (root / "AGENTS.md").write_text("Sessions live in SQLite. Every change lands on dev.\n")
    return root


def question(topic: str, answer: str = "SQLite") -> dict[str, Any]:
    return {
        "topic": topic,
        "question": f"What should {topic} use?",
        "recommendation": {
            "answer": f"{answer} for {topic}",
            "why": "AGENTS.md says so",
            "evidence": [
                {"kind": "quote", "ref": "AGENTS.md", "excerpt": "Sessions live in SQLite."}
            ],
        },
    }


def plan_reply(project: Path, topics: list[str] = TOPICS) -> dict[str, Any]:
    return {
        "reply": "Let's plan it. I recommend an answer for each question.",
        "tasks": [],
        "memories": [],
        "plan": {
            "title": "Offline sync",
            "goal": "Notes keep working without a network",
            "project_dir": str(project),
            "questions": [question(topic) for topic in topics],
        },
    }


def idle(home: RemHome, timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        home.tick()
        if home.store.conversation()["conversation"].get("turn_state") == "idle":
            return
        time.sleep(0.01)
    raise AssertionError(home.store.conversation()["conversation"])


def configure(home: RemHome, project: Path) -> None:
    home.configure(
        {
            "provider": "codex",
            "model": "cli-default",
            "command_slots": {"maxWorkers": 8},
            "projects": [{"root": str(project), "name": "app"}],
        }
    )


def plan_items(home: RemHome) -> list[dict[str, Any]]:
    return [i for i in home.decisions()["items"] if i.get("plan")]


# Filing a plan from Rem's chat ------------------------------------------------------------


def test_a_plan_of_five_questions_files_five_tagged_requests_three_waiting_two_queued(
    home: RemHome, project: Path
):
    configure(home, project)
    home.generate = lambda *args: plan_reply(project)
    home.submit_message({"request_id": "m1", "body": "Plan offline sync with me"})
    idle(home)
    listed = home.decisions()
    tagged = plan_items(home)
    assert len(tagged) == 5
    assert {i["plan"] for i in tagged} == {"P-1"}
    assert sorted(i["status"] for i in tagged) == ["pending"] * 3 + ["queued"] * 2
    # The oldest questions get the cards; the rest wait their turn as themselves.
    by_topic = {i["topic"]: i["status"] for i in tagged}
    assert [by_topic[t] for t in TOPICS] == ["pending"] * 3 + ["queued"] * 2
    # Each keeps its recommendation and the evidence Raticode checked.
    assert all(i["recommended"] and i["evidence"][0]["ref"] == "AGENTS.md" for i in tagged)
    (plan,) = listed["plans"]
    assert plan["title"] == "Offline sync" and plan["status"] == "asking"
    assert (plan["answered"], plan["total"], plan["waiting"], plan["queued"]) == (0, 5, 3, 2)
    reply = home.store.conversation()["messages"][-1]["body"]
    assert "Plan P-1 “Offline sync”: 5 questions." in reply
    assert "3 wait for you" in reply and "2 more come up as you answer" in reply


def test_rem_never_starts_a_plan_the_user_didnt_ask_for(home: RemHome, project: Path):
    configure(home, project)
    home.generate = lambda *args: plan_reply(project)
    home.submit_message({"request_id": "m1", "body": "What's left on offline sync?"})
    idle(home)
    assert plan_items(home) == [] and home.decisions()["plans"] == []
    # Nor from a workflow's prompt or queued work, whatever the text says.
    entry = {
        "id": "m2",
        "body": "Plan offline sync with me",
        "created_at": decisions.now(),
        "source": "workflow",
        "workflow": {"name": "nightly", "allow_actions": True},
    }
    with home.store.connect() as db:
        queue_user_message(home.store, db, entry, home.owner)
    idle(home)
    assert plan_items(home) == []


def test_asked_for_plan_reads_the_users_words():
    for said in [
        "plan this with me",
        "Can you plan the migration with me?",
        "grill me on the auth design",
        "Grill me about it",
        "let's plan the release",
        "help me plan offline sync",
    ]:
        assert plans.asked_for_plan(said), said
    for said in ["what's the plan?", "make a plan", "ship it", "the grill is hot"]:
        assert not plans.asked_for_plan(said), said


def test_the_chat_shape_and_rules_offer_plan():
    assert "plan:null}" in REPLY_RULES and plans.RULES in REPLY_RULES
    assert Decision.model_validate({"reply": "x", "plan": None}).plan is None


# Plan questions in the decision store -----------------------------------------------------


@pytest.fixture
def book() -> tuple[sqlite3.Connection, Store]:
    return sqlite3.connect(":memory:"), Store()


def test_plan_questions_wait_for_the_user_even_in_auto_mode(book, project: Path):
    db, store = book
    request = decisions.DecisionRequest.model_validate(question("plan:store"))
    outcome, item = decisions.file_request(
        db, store, request, project=str(project), decides_alone=True, plan="P-1"
    )
    assert outcome == "pending" and item["plan"] == "P-1"
    # The same question without a plan would be Rem's own answer in auto mode.
    other = decisions.DecisionRequest.model_validate(question("plan:other"))
    assert (
        decisions.file_request(db, store, other, project=str(project), decides_alone=True)[0]
        == "provisional"
    )


def test_settling_every_question_makes_the_plan_ready_once(book, project: Path):
    db, store = book
    filed, said = plans.file_plan(
        db,
        store,
        plans.PlanRequest.model_validate(plan_reply(project, TOPICS[:4])["plan"]),
        project=str(project),
    )
    assert filed is not None and filed["id"] == "P-1" and "4 questions" in said
    ids = filed["questions"]
    # Answered, turned down, forgotten while queued, changed: each one counts as settled.
    decisions.change(db, store, {"action": "use", "id": ids[0]}, actor="user")
    decisions.change(db, store, {"action": "reject", "id": ids[1]}, actor="user")
    decisions.change(db, store, {"action": "forget", "id": ids[3]}, actor="user")
    book_now = decisions.read(db, store)
    assert plans.ready(book_now) == []
    assert decisions.plan_progress(book_now, book_now["plans"][0])["answered"] == 3
    decisions.change(
        db, store, {"action": "change", "id": ids[2], "answer": "Postgres"}, actor="user"
    )
    book_now = decisions.read(db, store)
    (ready,) = plans.ready(book_now)
    body = plans.handback_body(book_now, ready)
    assert f"{ids[0]}: What should plan:store use? Answer (the user): SQLite for plan:store" in body
    assert f"{ids[1]}: What should plan:auth use? Turned down" in body
    assert f"{ids[2]}: What should plan:sync use? Answer (the user): Postgres" in body
    assert f"{ids[3]}: withdrawn" in body
    assert plans.decided_ids(book_now, ready) == [ids[0], ids[2]]
    plans.mark_handed_back(db, store, "P-1")
    plans.mark_handed_back(db, store, "P-1")
    assert plans.ready(decisions.read(db, store)) == []
    assert decisions.read(db, store)["plans"][0]["status"] == "handed_back"


def test_a_question_without_checkable_evidence_is_left_out_and_named(book, project: Path):
    db, store = book
    raw = plan_reply(project, TOPICS[:2])["plan"]
    raw["questions"][1]["recommendation"]["evidence"] = [{"kind": "file", "ref": "missing.py"}]
    filed, said = plans.file_plan(
        db, store, plans.PlanRequest.model_validate(raw), project=str(project)
    )
    assert filed is not None and len(filed["questions"]) == 1
    assert "Left out" in said and "plan:auth" in said


# The hand-back ----------------------------------------------------------------------------


def test_settling_the_last_question_hands_the_plan_back_once_and_files_slices(
    home: RemHome, project: Path
):
    configure(home, project)
    prompts: list[str] = []

    def generate(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        prompts.append(prompt)
        if len(prompts) == 1:
            return plan_reply(project, TOPICS[:4])
        ids = [a["id"] for a in plans.for_prompt(*_plan(home))["answers"]]
        return {
            "reply": "Filed the slices.",
            "tasks": [],
            "memories": [],
            "tickets": [
                {
                    "action": "create",
                    "title": "Notes save offline",
                    "description": f"Rests on {ids[0]}.",
                    "status": "todo",
                    "done_when": "A note typed offline is there after a restart",
                    "waits_for": [],
                },
                {
                    "action": "create",
                    "title": "Offline notes sync on reconnect",
                    "description": f"Rests on {ids[2]}.",
                    "status": "backlog",
                    "done_when": "A note typed offline shows on the desktop after reconnect",
                    "waits_for": ["#1"],
                },
                {
                    "action": "create",
                    "title": "No way to check",
                    "description": "Rests on nothing.",
                    "status": "backlog",
                },
            ],
        }

    home.generate = generate
    home.submit_message({"request_id": "m1", "body": "Plan offline sync with me"})
    idle(home)
    ids = _plan(home)[1]["questions"]
    for identity in ids[:3]:
        home.change_decisions({"action": "use", "id": identity})
        idle(home)
    assert len(prompts) == 1, "the plan goes back only when its last question is settled"
    home.change_decisions({"action": "use", "id": ids[3]})
    idle(home)
    for _ in range(3):
        home.next_plan_check = 0.0
        idle(home)
    assert len(prompts) == 2, "exactly one hand-back"
    handback = prompts[1]
    assert plans.HANDBACK_RULES in handback
    for identity, topic in zip(ids, TOPICS[:4], strict=True):
        assert f"{identity}: What should {topic} use? Answer (the user): SQLite for {topic}" in (
            handback
        )
    messages = home.store.conversation()["messages"]
    assert sum(m["id"] == "plan-P-1" for m in messages) == 1
    assert _plan(home)[1]["status"] == "handed_back"
    board = {t["title"]: t for t in home.kanban.board()["tickets"]}
    first, second = board["Notes save offline"], board["Offline notes sync on reconnect"]
    with home.store.read() as db:
        stored_first = get_ticket(db, first["ticket_id"])
        stored_second = get_ticket(db, second["ticket_id"])
    assert stored_first["done_when"] == "A note typed offline is there after a restart"
    assert stored_first["waits_for"] == []
    assert stored_second["done_when"].startswith("A note typed offline shows")
    # #1 became the first slice's key, so the second waits for it on the board.
    assert stored_second["waits_for"] == [first["ticket_id"]]
    assert [w["key"] for w in second["waits_for"]] == [first["key"]]
    assert first["project_dir"] == str(project.resolve())
    assert stored_first["description"].startswith(f"Rests on {ids[0]}")
    assert "No way to check" not in board
    reply = messages[-1]["body"]
    assert "I didn't add “No way to check”" in reply


def _plan(home: RemHome) -> tuple[dict[str, Any], dict[str, Any]]:
    with home.store.read() as db:
        book_now = decisions.read(db, home.store)
    return book_now, book_now["plans"][0]


def test_answering_the_last_question_in_chat_hands_the_plan_back(home: RemHome, project: Path):
    configure(home, project)
    turns: list[str] = []

    def generate(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        turns.append(prompt)
        if len(turns) == 1:
            return plan_reply(project, TOPICS[:1])
        if len(turns) == 2:
            identity = _plan(home)[1]["questions"][0]
            return {
                "reply": "Recorded.",
                "decision_answers": [{"id": identity, "action": "use", "said": "yes use that"}],
            }
        return {"reply": "Nothing to file.", "tickets": []}

    home.generate = generate
    home.submit_message({"request_id": "m1", "body": "Grill me on offline sync"})
    idle(home)
    home.submit_message({"request_id": "m2", "body": "Yes, use that"})
    idle(home)
    idle(home)
    assert len(turns) == 3 and plans.HANDBACK_RULES in turns[2]


def test_rems_chat_lists_a_plans_answers_only_until_the_plan_is_handed_back(
    home: RemHome, project: Path, monkeypatch: pytest.MonkeyPatch
):
    configure(home, project)
    listed: list[list[str]] = []
    real = decisions.for_prompt

    def spy(*args: Any, **kw: Any) -> list[dict[str, Any]]:
        found = real(*args, **kw)
        if kw.get("chat"):
            listed.append([e["id"] for e in found if "id" in e])
        return found

    monkeypatch.setattr(decisions, "for_prompt", spy)
    turns: list[str] = []

    def generate(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        turns.append(prompt)
        if len(turns) == 1:
            return plan_reply(project, TOPICS[:2])
        return {"reply": "Noted.", "tickets": []}

    home.generate = generate
    home.submit_message({"request_id": "m1", "body": "Plan offline sync with me"})
    idle(home)
    first, last = _plan(home)[1]["questions"]
    home.change_decisions({"action": "use", "id": first})
    idle(home)
    home.submit_message({"request_id": "m2", "body": "What is still open?"})
    idle(home)
    assert _plan(home)[1]["status"] == "asking"
    assert first in listed[-1]
    home.change_decisions({"action": "use", "id": last})
    idle(home)
    for _ in range(3):
        home.next_plan_check = 0.0
        idle(home)
    assert _plan(home)[1]["status"] == "handed_back"
    home.submit_message({"request_id": "m3", "body": "Thanks, what's next?"})
    idle(home)
    assert len(turns) == 4, "plan, question, hand-back, next message"
    assert not {first, last} & set(listed[-1])


# Slices -----------------------------------------------------------------------------------


def test_slices_need_done_when_cite_the_plan_and_keep_their_order():
    plan = {"id": "P-2", "project": "/work/app"}
    tickets = [
        TicketRequest(action="create", title="A", done_when="a works", waits_for=None),
        TicketRequest(action="create", title="Unclear"),
        TicketRequest(action="create", title="C", done_when="c works", waits_for=["#1", "REM-4"]),
    ]
    notes = plans.check_slices(tickets, plan, ["D-7", "D-9"])
    assert [t.title for t in tickets] == ["A", "C"]
    assert notes and "Unclear" in notes[0]
    a, c = tickets
    assert a.waits_for == [] and a.project_dir == "/work/app"
    assert a.description == "Rests on D-7, D-9 (plan P-2)."
    # #1 still names A after the ticket between them is dropped.
    assert c.waits_for == ["#1", "REM-4"]
    cited = [TicketRequest(action="create", title="B", description="Rests on D-9.", done_when="b")]
    plans.check_slices(cited, plan, ["D-7", "D-9"])
    assert cited[0].description == "Rests on D-9."


def test_a_slice_waiting_for_a_dropped_slice_is_dropped_instead_of_losing_its_wait():
    """Self-work report, 21 November afternoon: a later slice that waited for one dropped
    for having no done_when used to lose that wait and could start out of order."""
    plan = {"id": "P-3", "project": "/work/app"}
    tickets = [
        TicketRequest(action="create", title="Schema", done_when="schema test passes"),
        TicketRequest(action="create", title="No finish line"),
        TicketRequest(action="create", title="API", done_when="api passes", waits_for=["#2"]),
        TicketRequest(action="create", title="Screen", done_when="shows it", waits_for=["#3"]),
        TicketRequest(action="create", title="Docs", done_when="page builds", waits_for=["#1"]),
        TicketRequest(action="create", title="Stray", done_when="x passes", waits_for=["#9"]),
    ]
    notes = plans.check_slices(tickets, plan, [])
    assert [t.title for t in tickets] == ["Schema", "Docs"]
    assert tickets[1].waits_for == ["#1"]
    assert len(notes) == 4
    assert "“No finish line”" in notes[0] and "needs done_when" in notes[0]
    joined = "\n".join(notes[1:])
    assert "I didn't add “API”: it waits for “No finish line”" in joined
    assert "I didn't add “Screen”: it waits for “API”" in joined
    assert "I didn't add “Stray”: it waits for #9" in joined
    assert all("would start out of order" in n for n in notes[1:])


def test_a_ticket_can_wait_for_one_filed_earlier_in_the_same_list():
    assert _batch_waits(["#1", "REM-3"], {1: "REM-9"}, 2) == ["REM-9", "REM-3"]
    with pytest.raises(ValueError, match="comes after it"):
        _batch_waits(["#2"], {1: "REM-9"}, 2)
    with pytest.raises(ValueError, match="wasn't added"):
        _batch_waits(["#1"], {}, 3)
