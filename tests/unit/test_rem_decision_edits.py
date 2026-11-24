"""The user tells Rem in chat to change a decision; it changes only after they press OK.

Fake models and isolated data only: the ``home`` fixture from test_rem_home and plain git
repositories in ``tmp_path``. Rem never applies a change to a decision in force: its chat
reply carries an OK/Cancel card with the before and after, and only the user's OK (the
desktop, phone or CLI's ``confirm_edit``) makes the change and rewrites the record file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from gofer.devices.desktop_control import _decision_edits
from gofer.ui import rem_decisions as decisions
from gofer.ui.rem_decisions import DecisionEdit, DecisionRefused
from gofer.ui.rem_home import RemHome, workflow_answer_only
from gofer.ui.rem_home_models import Decision, TaskUpdate, WorkerReply
from gofer.ui.rem_screen import acts
from tests.unit.test_rem_decision_records import decide, files_on, git, repo
from tests.unit.test_rem_decisions import Store
from tests.unit.test_rem_home import home  # noqa: F401
from tests.unit.test_rem_kanban import wait_for

ASKED = "Update D-1 so the API answers in JSON:API format"


def add_answer(db: sqlite3.Connection, store: Store, answer: str = "REST with plain JSON") -> str:
    book = decisions.change(
        db,
        store,
        {
            "action": "add",
            "project": "/work/app",
            "remember_as": "record",
            "question": "How does the API answer?",
            "answer": answer,
            "why": "Clients are simple",
            "applies_to": ["src/api/**"],
        },
        actor="user",
    )
    return str(book["items"][-1]["id"])


def edit(**kw: Any) -> DecisionEdit:
    return DecisionEdit.model_validate(
        {"id": "D-1", "answer": "REST answering in JSON:API format", "said": ASKED, **kw}
    )


@pytest.fixture
def book() -> tuple[sqlite3.Connection, Store]:
    return sqlite3.connect(":memory:"), Store()


# The store ----------------------------------------------------------------------------


def test_a_chat_edit_waits_for_ok_and_shows_before_and_after(book):
    db, store = book
    identity = add_answer(db, store)
    entry, said = decisions.propose_edit(db, store, edit(), message=ASKED)
    assert entry is not None and entry["id"] == "E-1" and entry["status"] == "waiting"
    assert "Nothing changes until you press OK" in said
    # Worked out from the store, not from Rem: only the part that changes is listed.
    assert entry["changes"] == [
        {
            "field": "answer",
            "label": "Decision",
            "before": "REST with plain JSON",
            "after": "REST answering in JSON:API format",
        }
    ]
    stored = decisions.read(db, store)
    assert stored["items"][0]["answer"] == "REST with plain JSON"
    view = decisions.view(stored)
    assert view["edits_waiting"] == 1 and view["edits"][0]["decision"] == identity
    # OK applies it as the user's edit, which is what lets the record file be rewritten.
    after = decisions.change(db, store, {"action": "confirm_edit", "id": "E-1"}, actor="user")
    item = after["items"][0]
    assert item["answer"] == "REST answering in JSON:API format"
    assert item["history"][-1]["actor"] == "user" and item["history"][-1]["action"] == "edited"
    assert ASKED in item["history"][-1]["note"]
    assert after["edits"][0]["status"] == "applied" and after["edits"][0]["result"] == "D-1"
    assert decisions.view(after)["edits"] == []
    with pytest.raises(DecisionRefused, match="already made"):
        decisions.change(db, store, {"action": "confirm_edit", "id": "E-1"}, actor="user")


def test_cancel_changes_nothing_and_rem_can_never_press_ok(book):
    db, store = book
    add_answer(db, store)
    decisions.propose_edit(db, store, edit(), message=ASKED)
    for action in ("confirm_edit", "cancel_edit"):
        with pytest.raises(DecisionRefused, match="Only the user") as refused:
            decisions.change(db, store, {"action": action, "id": "E-1"}, actor="rem")
        assert refused.value.code == "unauthorized"
    decisions.change(db, store, {"action": "cancel_edit", "id": "E-1"}, actor="user")
    stored = decisions.read(db, store)
    assert stored["items"][0]["answer"] == "REST with plain JSON"
    assert stored["edits"][0]["status"] == "cancelled"
    with pytest.raises(DecisionRefused, match="cancelled"):
        decisions.change(db, store, {"action": "confirm_edit", "id": "E-1"}, actor="user")


def test_only_words_from_the_users_message_can_make_a_card(book):
    db, store = book
    add_answer(db, store)
    # Rem's quote isn't in the message: say, it came from a task's result.
    entry, said = decisions.propose_edit(
        db, store, edit(said="switch the API to JSON:API"), message="How is the build going?"
    )
    assert entry is None and "in your own message" in said
    # A quote too short to mean anything doesn't count either.
    entry, _ = decisions.propose_edit(db, store, edit(said="yes"), message="yes")
    assert entry is None
    # Spacing, case and punctuation don't matter.
    entry, _ = decisions.propose_edit(
        db, store, edit(said="update d-1, so the API answers"), message=ASKED
    )
    assert entry is not None
    assert decisions.read(db, store)["items"][0]["answer"] == "REST with plain JSON"


def test_ok_is_refused_when_the_decision_changed_since_the_card(book):
    db, store = book
    add_answer(db, store)
    decisions.propose_edit(db, store, edit(), message=ASKED)
    decisions.change(db, store, {"action": "edit", "id": "D-1", "why": "Newer"}, actor="user")
    with pytest.raises(DecisionRefused, match="changed after Rem showed you this") as refused:
        decisions.change(db, store, {"action": "confirm_edit", "id": "E-1"}, actor="user")
    assert refused.value.code == "conflict"
    assert decisions.read(db, store)["items"][0]["answer"] == "REST with plain JSON"


def test_bookkeeping_on_the_decision_doesnt_refuse_ok(book):
    db, store = book
    add_answer(db, store)
    decisions.propose_edit(db, store, edit(), message=ASKED)
    # Raticode refreshing the evidence moves updated_at but not what the card showed.
    assert decisions.recheck(db, store, "D-1", []) is not None
    after = decisions.change(db, store, {"action": "confirm_edit", "id": "E-1"}, actor="user")
    assert after["items"][0]["answer"] == "REST answering in JSON:API format"


def test_a_newer_request_replaces_the_waiting_one_and_replace_makes_a_new_decision(book):
    db, store = book
    add_answer(db, store)
    decisions.propose_edit(db, store, edit(), message=ASKED)
    asked = "Replace D-1 with GraphQL for every client"
    entry, _ = decisions.propose_edit(
        db,
        store,
        edit(action="replace", answer="GraphQL", said=asked, record={"forbids": ["**/*.rest"]}),
        message=asked,
    )
    assert entry is not None and entry["id"] == "E-2"
    stored = decisions.read(db, store)
    assert [e["status"] for e in stored["edits"]] == ["replaced", "waiting"]
    assert stored["edits"][0]["replaced_by"] == "E-2"
    labels = {c["field"]: c for c in entry["changes"]}
    assert labels["answer"]["after"] == "GraphQL" and labels["forbids"]["after"] == "**/*.rest"
    after = decisions.change(db, store, {"action": "confirm_edit", "id": "E-2"}, actor="user")
    old, new = after["items"]
    assert old["status"] == "superseded" and old["superseded_by"] == new["id"]
    assert new["answer"] == "GraphQL" and new["decided_by"] == "user"
    assert new["record"]["applies_to"] == ["src/api/**"]  # kept: not part of the change
    assert after["edits"][1]["result"] == new["id"]


def test_only_decisions_in_force_and_real_changes_get_a_card(book):
    db, store = book
    add_answer(db, store)
    entry, said = decisions.propose_edit(
        db, store, edit(answer="REST with plain JSON"), message=ASKED
    )
    assert entry is None and "nothing to change" in said
    entry, said = decisions.propose_edit(db, store, edit(id="D-9"), message=ASKED)
    assert entry is None and "no decision D-9" in said
    decisions.change(db, store, {"action": "reject", "id": "D-1"}, actor="user")
    entry, said = decisions.propose_edit(db, store, edit(), message=ASKED)
    assert entry is None and "closed" in said


def test_workers_task_updates_screens_and_workflows_cannot_bring_a_card():
    payload = {"id": "D-1", "answer": "x", "said": ASKED}
    with pytest.raises(ValueError):
        WorkerReply.model_validate({"next_message": "Done.", "decision_edits": [payload]})
    with pytest.raises(ValueError):
        TaskUpdate.model_validate({"summary": "Done.", "decision_edits": [payload]})
    decision = Decision.model_validate({"reply": "Ready.", "decision_edits": [payload]})
    # Counts as acting, so a reply that saw screen text is decided again without it, and a
    # workflow prompt drops it.
    assert acts(decision)
    assert workflow_answer_only(decision).decision_edits == []


def test_the_phone_gets_bounded_cards_with_no_folder():
    cards = _decision_edits(
        [
            {
                "id": "E-3",
                "decision": "D-1",
                "action": "edit",
                "project": "/home/someone/app",
                "project_name": "app",
                "changes": [
                    {"field": "answer", "label": "Decision", "before": "a", "after": "b"},
                    {"field": "secret", "label": "Nope", "before": "", "after": ""},
                ],
                "status": "waiting",
            },
            {"id": "E-4", "decision": "D-2", "status": "bogus"},
        ]
    )
    assert len(cards) == 1 and cards[0]["project"] == "app"
    assert [c["field"] for c in cards[0]["changes"]] == ["answer"]


# Rem's chat ---------------------------------------------------------------------------


def chat(home: RemHome, request: str, body: str, *, source: str | None = None) -> dict[str, Any]:  # noqa: F811
    home.submit_message({"request_id": request, "body": body}, source=source)  # type: ignore[arg-type]
    home.tick()
    wait_for(
        lambda: any(m["id"] == f"reply-{request}" for m in home.store.conversation()["messages"])
    )
    return next(m for m in home.store.conversation()["messages"] if m["id"] == f"reply-{request}")


def record(home: RemHome, root: Path) -> dict[str, Any]:  # noqa: F811
    view = home.change_decisions(
        {
            "action": "add",
            "project": str(root),
            "remember_as": "record",
            "question": "Which database?",
            "answer": "SQLite",
            "applies_to": ["src/db/**"],
        }
    )
    return next(i for i in view["items"] if i["question"] == "Which database?")


def test_the_user_asks_in_chat_sees_ok_cancel_and_ok_rewrites_the_record_file(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    item = record(home, root)
    path = files_on(root)[0]
    asked = f"Please update {item['id']} to use Postgres, we outgrew SQLite"
    prompts: list[str] = []

    def generate(prompt: str, config: Any, cancel: Any) -> Any:
        prompts.append(prompt)
        return {
            "reply": "Here's the change for you to check.",
            "tasks": [],
            "memories": [],
            "decision_edits": [
                {
                    "id": item["id"],
                    "answer": "Postgres",
                    "record": {"consequences": "Run a database server."},
                    "said": f"update {item['id']} to use Postgres",
                }
            ],
        }

    home.generate = generate
    reply = chat(home, "update-db", asked)
    # Rem saw how to ask, and nothing changed yet: not the store, not the file.
    assert "decision_edits" in prompts[0]
    card = reply["decision_edits"][0]
    assert card["status"] == "waiting" and card["decision"] == item["id"]
    assert {c["field"]: (c["before"], c["after"]) for c in card["changes"]} == {
        "answer": ("SQLite", "Postgres"),
        "consequences": ("", "Run a database server."),
    }
    assert "Nothing changes until you press OK" in reply["body"]
    assert "SQLite" in git(root, "show", f"dev:{path}")
    assert next(i for i in home.decisions()["items"] if i["id"] == item["id"])["answer"] == "SQLite"
    # OK: the decision and its Markdown record change, and the card says it was applied.
    home.change_decisions({"action": "confirm_edit", "id": card["id"]})
    text = git(root, "show", f"dev:{path}")
    assert "Postgres" in text and "Run a database server." in text
    settled = next(m for m in home.store.conversation()["messages"] if m["id"] == "reply-update-db")
    assert settled["decision_edits"][0]["status"] == "applied"


def test_a_workflow_prompt_never_brings_a_card(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    from gofer.ui.rem_workflow import WorkflowOrigin, submit_prompt

    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    item = record(home, root)
    words = f"update {item['id']} to use Postgres right now"
    home.generate = lambda *args: {
        "reply": "Ready.",
        "tasks": [],
        "memories": [],
        "decision_edits": [{"id": item["id"], "answer": "Postgres", "said": words}],
    }
    identity = submit_prompt(
        home.store, WorkflowOrigin("wf", "run-1", "ask"), words, allow_actions=True
    )
    home.tick()
    wait_for(
        lambda: any(m["id"] == f"reply-{identity}" for m in home.store.conversation()["messages"])
    )
    reply = next(m for m in home.store.conversation()["messages"] if m["id"] == f"reply-{identity}")
    assert "decision_edits" not in reply
    assert home.decisions()["edits"] == []


def test_the_phone_presses_ok_on_a_card_from_its_own_message(
    home: RemHome,  # noqa: F811
    tmp_path: Path,
):
    root = repo(tmp_path)
    decide(home, root, "branch.integration", "dev")
    item = record(home, root)
    words = f"change {item['id']} to say Postgres"
    home.generate = lambda *args: {
        "reply": "Check it below.",
        "tasks": [],
        "memories": [],
        "decision_edits": [{"id": item["id"], "answer": "Postgres", "said": words}],
    }
    reply = chat(home, "from-phone", f"Hey, {words}", source="phone")
    card = reply["decision_edits"][0]
    home.change_decisions({"action": "cancel_edit", "id": card["id"]})
    assert next(i for i in home.decisions()["items"] if i["id"] == item["id"])["answer"] == "SQLite"
    settled = next(
        m for m in home.store.conversation()["messages"] if m["id"] == "reply-from-phone"
    )
    assert settled["decision_edits"][0]["status"] == "cancelled"
