"""Rem's decisions: no bare questions, recorded answers reused, and only the user changes them."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from gofer.ui import rem_decisions as decisions
from gofer.ui.rem_decisions import DecisionRefused, DecisionRequest
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_models import ConversationDecision, TaskUpdate, WorkerReply
from gofer.ui.rem_self_work import Proposal, checkable_finish
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe, wait_run


class Store:
    """Rem's settings in memory, the two calls the decision book uses."""

    def __init__(self) -> None:
        self.settings: dict[str, Any] = {}

    def setting(self, db: Any, key: str) -> dict[str, Any]:
        copied: dict[str, Any] = json.loads(json.dumps(self.settings.get(key, {})))
        return copied

    def set_setting(self, db: Any, key: str, value: dict[str, Any]) -> None:
        self.settings[key] = json.loads(json.dumps(value))


@pytest.fixture
def book() -> tuple[sqlite3.Connection, Store]:
    return sqlite3.connect(":memory:"), Store()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    (root / "scripts").mkdir(parents=True)
    (root / "AGENTS.md").write_text("Before landing, run python3 scripts/check-dev.py\n")
    (root / "scripts" / "check-dev.py").write_text("print('ok')\n")
    return root


# Where a project scan's requests come from; only it may set checks.fast, branch.integration
# and decisions.folder provisionally.
SCAN = {"via": "scan"}


def request(topic: str = "checks.fast", answer: str = "python3 scripts/check-dev.py", **kw: Any):
    return DecisionRequest.model_validate(
        {
            "topic": topic,
            "question": "How should Rem check that work here is sound?",
            "recommendation": {
                "answer": answer,
                "why": "AGENTS.md names it",
                "evidence": [
                    {
                        "kind": "quote",
                        "ref": "AGENTS.md",
                        "excerpt": "run python3 scripts/check-dev.py",
                    }
                ],
            },
            "alternatives": [{"answer": "pytest -q", "why_not": "Skips lint and types"}],
            **kw,
        }
    )


# R1: no question without a recommendation ------------------------------------------------


def test_a_question_for_the_user_needs_a_recommendation():
    with pytest.raises(ValidationError, match="decision_request"):
        WorkerReply.model_validate({"needs_user": True, "user_question": "Which branch?"})
    with pytest.raises(ValidationError, match="recommended answer"):
        DecisionRequest.model_validate({"topic": "branch.integration", "question": "Which?"})
    with pytest.raises(ValidationError, match="decision_request"):
        TaskUpdate.model_validate(
            {"summary": "Done.", "needs_user": True, "user_question": "Which branch?"}
        )
    reply = WorkerReply.model_validate(
        {"needs_user": True, "decision_request": request().model_dump()}
    )
    assert reply.user_question == "How should Rem check that work here is sound?"
    # A verdict only proposes the question; Rem's answer to the worker enforces it.
    ConversationDecision.model_validate(
        {"criteria": [], "summary": "x", "needs_user": True, "user_question": "Which branch?"}
    )


def test_only_a_human_only_question_may_come_without_a_recommendation_and_must_say_what_was_tried():
    with pytest.raises(ValidationError, match="what Rem tried"):
        DecisionRequest.model_validate({"topic": "keys", "question": "Which API key should I use?"})
    asked = DecisionRequest.model_validate(
        {
            "topic": "keys",
            "question": "Which API key should I use?",
            "tried": "Looked in .env.example and the keychain; neither has one.",
        }
    )
    assert asked.recommendation is None


def test_evidence_is_checked_and_unusable_evidence_is_dropped(project: Path):
    mixed = request().model_copy(deep=True)
    assert mixed.recommendation is not None
    mixed.recommendation.evidence += [
        decisions.Evidence(kind="file", ref="missing.py"),
        decisions.Evidence(kind="quote", ref="AGENTS.md", excerpt="never written"),
        decisions.Evidence(kind="file", ref="../outside.txt"),
        decisions.Evidence(kind="command", ref="pytest -q"),
    ]
    (project.parent / "outside.txt").write_text("x")
    checked, dropped = decisions.check_evidence(mixed, str(project), traces=[])
    assert checked.recommendation is not None
    assert [e.kind for e in checked.recommendation.evidence] == ["quote"]
    assert len(dropped) == 4
    ran = [
        {
            "id": "1",
            "phase": "result",
            "status": "completed",
            "exitCode": 0,
            "output": "ok",
            "input": "pytest -q",
        }
    ]
    only_command = request().model_copy(deep=True)
    assert only_command.recommendation is not None
    only_command.recommendation.evidence = [decisions.Evidence(kind="command", ref="pytest -q")]
    assert decisions.check_evidence(only_command, str(project), ran)[1] == []
    nothing = request().model_copy(deep=True)
    assert nothing.recommendation is not None
    nothing.recommendation.evidence = [decisions.Evidence(kind="file", ref="nope.md")]
    with pytest.raises(ValueError, match="could be checked"):
        decisions.check_evidence(nothing, str(project))


# R2: stored by project and topic, asked once ----------------------------------------------


def test_a_confirmed_answer_is_reused_and_never_asked_again(book, project: Path):
    db, store = book
    outcome, item = decisions.file_request(
        db, store, request(), project=str(project), decides_alone=False, source={"run_id": "r1"}
    )
    assert outcome == "pending" and item["status"] == "pending"
    # A second worker asking the same thing joins the same card.
    outcome, same = decisions.file_request(
        db, store, request(), project=str(project), decides_alone=False, source={"run_id": "r2"}
    )
    assert outcome == "waiting" and same["id"] == item["id"]
    assert decisions.read(db, store)["items"][0]["asked_by"] == ["r1", "r2"]
    decisions.change(db, store, {"action": "use", "id": item["id"]}, actor="user")
    outcome, answered = decisions.file_request(
        db, store, request(answer="pytest"), project=str(project), decides_alone=True
    )
    assert outcome == "answered"
    assert answered["answer"] == "python3 scripts/check-dev.py"
    listed = decisions.for_prompt(decisions.read(db, store), [str(project)])
    assert listed == [
        {
            "id": item["id"],
            "topic": "checks.fast",
            "decision": "python3 scripts/check-dev.py",
            "question": "How should Rem check that work here is sound?",
        }
    ]
    # Other projects don't get it.
    assert decisions.for_prompt(decisions.read(db, store), [str(project.parent)]) == []


def _records(db: Any, store: Any, key: str, count: int, start: int = 0) -> None:
    for n in range(start, start + count):
        decisions.change(
            db,
            store,
            {
                "action": "add",
                "project": key,
                "remember_as": "record",
                "question": f"How does module {n} store its data?",
                "answer": f"Module {n} keeps its data in SQLite. "
                "It never writes JSON files beside it, because " + "x" * 300,
                "applies_to": [f"src/module{n}/**", f"tests/module{n}/**", "docs/**"],
                "forbids": [f"src/module{n}/**/*.json"],
            },
            actor="user",
        )


def _with_files(read: dict[str, Any]) -> dict[str, Any]:
    for item in read["items"]:
        if item["remember_as"] == "record":
            item["record_path"] = f".raticode/decisions/{item['id'].lower()}.md"
    return read


def test_the_worker_index_always_keeps_the_projects_facts(book, project: Path):
    db, store = book
    key = str(project)
    _records(db, store, key, 20)
    facts = {
        "branch.integration": "dev",
        "checks.fast": "python3 scripts/check-dev.py",
        "decisions.folder": ".raticode/decisions",
    }
    for topic, answer in facts.items():
        decisions.change(
            db,
            store,
            {"action": "add", "project": key, "topic": topic, "answer": answer},
            actor="user",
        )
    read = _with_files(decisions.read(db, store))
    listed = decisions.for_prompt(read, [key], "Fix an import in src/module7")
    assert sum(len(str(e)) for e in listed) <= decisions.PROMPT_CHARS
    # Every fact is listed, first, with the 20 records after them.
    assert {e["topic"]: e["decision"] for e in listed[:3]} == facts
    records = listed[3:]
    assert len(records) == 20 and all("left_out" not in e for e in records)
    # A record is its ID, topic, first sentence and file: no path lists. The one the
    # task names comes first.
    assert set(records[0]) == {"id", "topic", "decision", "file"}
    assert records[0]["decision"] == "Module 7 keeps its data in SQLite."
    assert records[0]["file"].startswith(".raticode/decisions/")

    # With more records than fit, the facts stay and the last entry says how many were
    # left out and where they are.
    _records(db, store, key, 60, start=20)
    read = _with_files(decisions.read(db, store))
    listed = decisions.for_prompt(read, [key], "Fix an import in src/module7")
    assert sum(len(str(e)) for e in listed) <= decisions.PROMPT_CHARS
    assert {e["topic"]: e["decision"] for e in listed[:3]} == facts
    last = listed[-1]
    kept = len(listed) - 4
    assert 20 <= kept < 80 and last["left_out"] == 80 - kept
    assert f"{80 - kept} more decision records" in last["note"]
    assert ".raticode/decisions" in last["note"]


def test_a_plans_answers_reach_only_the_work_that_cites_them(book, project: Path):
    db, store = book
    key = str(project)
    for topic, answer in {
        "checks.fast": "python3 scripts/check-dev.py",
        "branch.integration": "dev",
        "decisions.folder": ".raticode/decisions",
        "storage.engine": "SQLite",
    }.items():
        add = {"action": "add", "project": key, "topic": topic, "answer": answer}
        decisions.change(db, store, add, actor="user")
    for topic in ("plan:store", "plan:auth", "plan:sync"):
        _, item = decisions.file_request(
            db,
            store,
            request(topic=topic, answer=f"Answer for {topic}"),
            project=key,
            decides_alone=False,
            plan="P-1",
        )
        decisions.change(db, store, {"action": "use", "id": item["id"]}, actor="user")
    read = decisions.read(db, store)
    planned = [i["id"] for i in read["items"] if i.get("plan") == "P-1"]
    assert planned == ["D-5", "D-6", "D-7"]
    assert all(i["status"] == "confirmed" for i in read["items"])

    def ids(text: str = "", **kw: Any) -> list[str]:
        return [e["id"] for e in decisions.for_prompt(read, [key], text, **kw)]

    # A task that cites neither the plan nor its answers gets none of them, and still
    # gets the check command, the landing branch, the folder and untagged facts.
    assert ids("Fix the login form on P-10 and D-70") == ["D-2", "D-1", "D-3", "D-4"]
    # A slice ticket's "Rests on ... (plan P-1)" gets every answer of its plan.
    slice_ids = ids("Rests on D-7 (plan P-1).\n\nSave notes offline")
    assert sorted(slice_ids) == ["D-1", "D-2", "D-3", "D-4", "D-5", "D-6", "D-7"]
    # Citing one answer alone lists that one.
    assert "D-7" in ids("Rests on D-7.") and not {"D-5", "D-6"} & set(ids("Rests on D-7."))
    # Rem's chat lists them while the plan is asking or ready, not once it was handed back.
    for status, listed in (("asking", True), ("ready", True), ("handed_back", False)):
        read["plans"] = [{"id": "P-1", "status": status}]
        assert ("D-7" in ids(chat=True)) is listed, status
        assert {"D-1", "D-2", "D-3", "D-4"} <= set(ids(chat=True))


def test_a_chat_answer_needs_the_users_words_and_a_waiting_request(book, project: Path):
    db, store = book
    _, item = decisions.file_request(
        db, store, request(), project=str(project), decides_alone=False
    )
    answer = decisions.DecisionAnswer(id=item["id"], action="use", said="yes, use that")
    # Words the user didn't write (from an attachment or a lookup, say) record nothing.
    applied, said = decisions.answer_from_chat(db, store, answer, message="what's next?")
    assert applied is None and "doesn't say it" in said
    applied, _ = decisions.answer_from_chat(db, store, answer, message="Yes, use that.")
    assert applied is not None and applied["status"] == "confirmed"
    # A confirmed decision changes only through an edit card the user presses OK on.
    change = decisions.DecisionAnswer(id=item["id"], action="change", answer="true", said="no")
    applied, said = decisions.answer_from_chat(db, store, change, message="no")
    assert applied is None and "isn't waiting" in said
    stored = decisions.read(db, store)["items"][0]
    assert (stored["status"], stored["answer"]) == ("confirmed", "python3 scripts/check-dev.py")


def test_auto_mode_applies_its_own_recommendation_provisionally(book, project: Path):
    db, store = book
    outcome, item = decisions.file_request(
        db, store, request(), project=str(project), decides_alone=True, source=SCAN
    )
    assert outcome == "provisional"
    assert item["status"] == "provisional" and item["decided_by"] == "rem"
    # In force for the next worker, flagged as provisional.
    assert decisions.for_prompt(decisions.read(db, store), [str(project)])[0]["provisional"]
    # Something that can't be undone still waits for the user.
    outcome, other = decisions.file_request(
        db,
        store,
        request(topic="release.publish", reversible=False),
        project=str(project),
        decides_alone=True,
        source=SCAN,
    )
    assert outcome == "pending" and other["status"] == "pending"


@pytest.mark.parametrize("topic", sorted(decisions.SCAN_ONLY_TOPICS))
@pytest.mark.parametrize(
    "source", [{"run_id": "run-1", "task_id": "task-1"}, {"via": "chat"}, {}], ids=str
)
def test_only_a_project_scan_sets_the_check_branch_and_folder_in_auto_mode(
    book, project: Path, topic: str, source: dict[str, Any]
):
    db, store = book
    # A worker's (or the chat's) request in auto mode, on a topic with no decision yet.
    outcome, item = decisions.file_request(
        db, store, request(topic=topic), project=str(project), decides_alone=True, source=source
    )
    assert outcome in {"pending", "queued"} and outcome != "provisional"
    assert item["status"] == "pending" and item["decided_by"] is None
    assert decisions.in_force(decisions.read(db, store), str(project), topic) is None
    # So Raticode never runs a command a worker chose, auto mode or not.
    assert not decisions.may_run(item, auto=True)
    assert not decisions.may_run(item, auto=False)


@pytest.mark.parametrize("topic", sorted(decisions.SCAN_ONLY_TOPICS))
def test_a_project_scan_still_files_the_check_branch_and_folder_provisionally(
    book, project: Path, topic: str
):
    db, store = book
    outcome, item = decisions.file_request(
        db, store, request(topic=topic), project=str(project), decides_alone=True, source=SCAN
    )
    assert outcome == "provisional" and item["status"] == "provisional"
    if topic == "checks.fast":
        assert decisions.may_run(item, auto=True) and not decisions.may_run(item, auto=False)
    # Other topics from a worker are still Rem's to decide in auto mode.
    outcome, other = decisions.file_request(
        db,
        store,
        request(topic="tests.layout", answer="tests/unit"),
        project=str(project),
        decides_alone=True,
        source={"run_id": "run-1"},
    )
    assert outcome == "provisional" and other["status"] == "provisional"


def test_rem_can_add_but_never_edit_replace_or_remove_a_decision(book, project: Path):
    db, store = book
    _, item = decisions.file_request(
        db, store, request(), project=str(project), decides_alone=True, source=SCAN
    )
    for action in ("use", "change", "edit", "reject", "reopen", "forget", "add"):
        with pytest.raises(DecisionRefused) as refused:
            decisions.change(
                db,
                store,
                {"action": action, "id": item["id"], "answer": "pytest", "topic": "checks.fast"},
                actor="rem",
            )
        assert refused.value.code == "unauthorized"
    # Rem asking again with a different answer, even in auto mode, can't replace it.
    outcome, same = decisions.file_request(
        db,
        store,
        request(answer="pytest -q"),
        project=str(project),
        decides_alone=True,
        source=SCAN,
    )
    assert outcome == "answered" and same["answer"] == "python3 scripts/check-dev.py"
    stored = decisions.read(db, store)["items"]
    assert [(i["id"], i["status"], i["answer"]) for i in stored] == [
        (item["id"], "provisional", "python3 scripts/check-dev.py")
    ]


def test_a_changed_evidence_file_reopens_the_topic_as_a_proposal_the_user_approves(
    book, project: Path
):
    db, store = book
    _, item = decisions.file_request(
        db, store, request(), project=str(project), decides_alone=True, source=SCAN
    )
    (project / "AGENTS.md").write_text("Before landing, run make check\n")
    assert "AGENTS.md changed" in decisions.stale_reason(item)
    outcome, proposal = decisions.file_request(
        db,
        store,
        request(answer="make check"),
        project=str(project),
        decides_alone=True,
        source=SCAN,
    )
    # Even in auto mode, replacing a decision waits for the user; the old one stays in force.
    assert outcome == "pending"
    assert proposal["supersedes"] == item["id"] and "AGENTS.md changed" in proposal["reopened"]
    current = decisions.in_force(decisions.read(db, store), str(project), "checks.fast")
    assert current is not None and current["id"] == item["id"]
    decisions.change(db, store, {"action": "use", "id": proposal["id"]}, actor="user")
    listed = {i["id"]: i for i in decisions.read(db, store)["items"]}
    assert listed[item["id"]]["status"] == "superseded"
    assert listed[item["id"]]["superseded_by"] == proposal["id"]
    assert listed[proposal["id"]]["status"] == "confirmed"


def test_a_command_that_fails_twice_in_a_row_opens_its_topic_again(book, project: Path):
    db, store = book
    _, item = decisions.file_request(
        db, store, request(), project=str(project), decides_alone=True, source=SCAN
    )
    decisions.note_check(db, store, item["id"], passed=False)
    decisions.note_check(db, store, item["id"], passed=True)
    decisions.note_check(db, store, item["id"], passed=False)
    outcome, _ = decisions.file_request(
        db, store, request(), project=str(project), decides_alone=True, source=SCAN
    )
    assert outcome == "answered"
    decisions.note_check(db, store, item["id"], passed=False)
    outcome, proposal = decisions.file_request(
        db, store, request(), project=str(project), decides_alone=True, source=SCAN
    )
    assert outcome == "pending" and "failed 2 times" in proposal["reopened"]


def test_ask_again_files_a_new_request_and_keeps_the_decision_in_force(book, project: Path):
    db, store = book
    _, item = decisions.file_request(
        db, store, request(), project=str(project), decides_alone=False
    )
    decisions.change(db, store, {"action": "use", "id": item["id"]}, actor="user")
    # Three cards already wait in the project.
    for n in range(3):
        outcome, _ = decisions.file_request(
            db, store, request(topic=f"t{n}"), project=str(project), decides_alone=False
        )
        assert outcome == "pending"
    decisions.change(db, store, {"action": "reopen", "id": item["id"]}, actor="user")
    book_now = decisions.read(db, store)
    # The decision stays in force: workers keep getting it, and Raticode keeps running it.
    current = decisions.in_force(book_now, str(project), "checks.fast")
    assert current is not None and current["id"] == item["id"]
    assert current["status"] == "confirmed" and current["decided_by"] == "user"
    assert decisions.may_run(current, auto=False)
    listed = decisions.for_prompt(book_now, [str(project)])
    assert {"id": item["id"], "topic": "checks.fast"}.items() <= next(
        e for e in listed if e.get("id") == item["id"]
    ).items()
    assert current["history"][-1]["action"] == "reopened"
    # The new request counts toward the three cards, so it is queued behind them.
    asked = next(
        i for i in book_now["items"] if i["topic"] == "checks.fast" and i["id"] != item["id"]
    )
    assert asked["status"] == "queued" and asked["supersedes"] == item["id"]
    assert asked["asked_again"] and asked["current_answer"] == "python3 scripts/check-dev.py"
    assert asked["reopened"] == decisions.ASKED_AGAIN
    # Without a fresh scan, the card makes no recommendation; it shows the current answer.
    assert asked["recommended"] == "" and "fresh" not in asked
    card = decisions.card(asked)
    assert card["asked_again"] is True and card["supersedes"] == item["id"]
    assert card["current_answer"] == "python3 scripts/check-dev.py"
    assert "Current answer: python3 scripts/check-dev.py" in decisions.waiting_text(card)
    # A worker asking about the topic meanwhile gets the decision, not the waiting card.
    outcome, answered = decisions.file_request(
        db, store, request(), project=str(project), decides_alone=False
    )
    assert outcome == "answered" and answered["id"] == item["id"]
    # Asking again a second time while it waits is refused, naming the card.
    with pytest.raises(DecisionRefused, match=asked["id"]):
        decisions.change(db, store, {"action": "reopen", "id": item["id"]}, actor="user")
    # Turning the card down keeps the current answer.
    decisions.change(db, store, {"action": "reject", "id": asked["id"]}, actor="user")
    still = decisions.in_force(decisions.read(db, store), str(project), "checks.fast")
    assert still is not None and still["id"] == item["id"]
    # Asked again and answered with a new command: that one replaces the old decision.
    decisions.change(db, store, {"action": "reopen", "id": item["id"]}, actor="user")
    again = next(
        i
        for i in decisions.read(db, store)["items"]
        if i["topic"] == "checks.fast" and i["status"] in {"pending", "queued"}
    )
    decisions.change(
        db, store, {"action": "change", "id": again["id"], "answer": "make check"}, actor="user"
    )
    listed_items = {i["id"]: i for i in decisions.read(db, store)["items"]}
    assert listed_items[item["id"]]["status"] == "superseded"
    assert listed_items[again["id"]]["status"] == "confirmed"
    assert listed_items[again["id"]]["answer"] == "make check"
    assert "asked_again" not in listed_items[again["id"]]
    assert "current_answer" not in listed_items[again["id"]]
    # Forgetting a decision while its Ask again card waits leaves a plain question.
    decisions.change(db, store, {"action": "reopen", "id": again["id"]}, actor="user")
    third = next(
        i
        for i in decisions.read(db, store)["items"]
        if i["topic"] == "checks.fast" and i["status"] in {"pending", "queued"}
    )
    decisions.change(db, store, {"action": "forget", "id": again["id"]}, actor="user")
    plain = next(i for i in decisions.read(db, store)["items"] if i["id"] == third["id"])
    assert not {"supersedes", "asked_again", "current_answer", "reopened"} & set(plain)


def test_ask_again_with_a_fresh_scan_recommends_what_the_scan_found(book, project: Path):
    db, store = book
    decisions.change(
        db,
        store,
        {"action": "add", "topic": "checks.fast", "answer": "pytest -q", "project": str(project)},
        actor="user",
    )
    item = decisions.in_force(decisions.read(db, store), str(project), "checks.fast")
    assert item is not None
    decisions.change(
        db, store, {"action": "reopen", "id": item["id"]}, actor="user", offer=request()
    )
    asked = next(i for i in decisions.read(db, store)["items"] if i["status"] == "pending")
    assert asked["fresh"] is True and asked["status"] == "pending"
    assert asked["recommended"] == "python3 scripts/check-dev.py"
    assert asked["evidence"][0]["ref"] == "AGENTS.md"
    assert asked["current_answer"] == "pytest -q"
    assert decisions.card(asked)["fresh"] is True
    # An offer about another topic is ignored.
    decisions.change(db, store, {"action": "reject", "id": asked["id"]}, actor="user")
    decisions.change(
        db,
        store,
        {"action": "reopen", "id": item["id"]},
        actor="user",
        offer=request(topic="branch.integration", answer="dev"),
    )
    other = next(i for i in decisions.read(db, store)["items"] if i["status"] == "pending")
    assert other["recommended"] == "" and "fresh" not in other


def test_a_new_check_command_starts_its_run_count_fresh(book, project: Path):
    db, store = book
    _, item = decisions.file_request(
        db,
        store,
        request(answer="make check"),
        project=str(project),
        decides_alone=True,
        source=SCAN,
    )
    old_run = {"command": "make check", "passed": False, "summary": "failed", "output": "x"}
    decisions.note_check(db, store, item["id"], passed=False, result=old_run)
    decisions.change(
        db, store, {"action": "edit", "id": item["id"], "answer": "make test"}, actor="user"
    )
    edited = decisions.read(db, store)["items"][0]
    assert edited["failures"] == 0 and "last_check" not in edited
    new_run = {"command": "make test", "passed": False, "summary": "failed again"}
    current = decisions.note_check(db, store, item["id"], passed=False, result=new_run)
    assert current is not None
    # One failure of the new command, not a second in a row: the decision still holds.
    assert decisions.stale_reason(current) == ""
    assert current["failures"] == 1 and current["last_check"] == new_run

    # Editing only the why keeps the count and the run; change resets both for a new answer.
    decisions.change(db, store, {"action": "edit", "id": item["id"], "why": "Faster"}, actor="user")
    kept = decisions.read(db, store)["items"][0]
    assert kept["failures"] == 1 and kept["last_check"] == new_run
    decisions.change(
        db, store, {"action": "change", "id": item["id"], "answer": "make lint"}, actor="user"
    )
    changed = decisions.read(db, store)["items"][0]
    assert changed["failures"] == 0 and "last_check" not in changed


def test_a_run_of_the_new_answer_stays_listed_when_the_answer_changes(book, project: Path):
    db, store = book
    _, item = decisions.file_request(
        db,
        store,
        request(answer="make check"),
        project=str(project),
        decides_alone=True,
        source=SCAN,
    )
    run = {"command": "make test", "passed": True}
    decisions.note_check(db, store, item["id"], passed=False, result=run)
    decisions.change(
        db, store, {"action": "edit", "id": item["id"], "answer": " make test "}, actor="user"
    )
    edited = decisions.read(db, store)["items"][0]
    assert edited["failures"] == 0 and edited["last_check"] == run


def test_the_user_changes_edits_rejects_and_forgets_decisions(book, project: Path):
    db, store = book
    _, item = decisions.file_request(
        db, store, request(), project=str(project), decides_alone=False
    )
    revision = decisions.read(db, store)["revision"]
    with pytest.raises(DecisionRefused) as stale:
        decisions.change(
            db,
            store,
            {"action": "use", "id": item["id"], "expected_revision": revision - 1},
            actor="user",
        )
    assert stale.value.code == "conflict"
    decisions.change(
        db, store, {"action": "change", "id": item["id"], "answer": "make test"}, actor="user"
    )
    decisions.change(db, store, {"action": "edit", "id": item["id"], "why": "Faster"}, actor="user")
    entry = decisions.read(db, store)["items"][0]
    assert (entry["status"], entry["answer"], entry["why"]) == ("confirmed", "make test", "Faster")
    assert [h["action"] for h in entry["history"]] == ["requested", "changed", "edited"]
    decisions.change(
        db,
        store,
        {"action": "add", "topic": "branch.integration", "answer": "main", "project": str(project)},
        actor="user",
    )
    assert decisions.in_force(decisions.read(db, store), str(project), "branch.integration")
    decisions.change(db, store, {"action": "forget", "id": item["id"]}, actor="user")
    assert [i["topic"] for i in decisions.read(db, store)["items"]] == ["branch.integration"]


def test_a_fourth_question_is_queued_as_itself_and_moves_up_when_one_is_answered(
    book, project: Path
):
    db, store = book
    shown = []
    for n in range(3):
        outcome, item = decisions.file_request(
            db,
            store,
            request(topic=f"t{n}"),
            project=str(project),
            decides_alone=False,
            source={"run_id": f"r{n}"},
        )
        assert outcome == "pending"
        shown.append(item)
    # The fourth is stored as itself, linked to the run that asked, with no card yet.
    outcome, fourth = decisions.file_request(
        db,
        store,
        request(topic="t3"),
        project=str(project),
        decides_alone=False,
        source={"run_id": "r3"},
    )
    assert outcome == "queued" and fourth["status"] == "queued"
    assert fourth["topic"] == "t3" and fourth["id"] not in {i["id"] for i in shown}
    assert fourth["asked_by"] == ["r3"]
    # The oldest waiting one is untouched: the run isn't parked on another question's card.
    assert decisions.read(db, store)["items"][0]["asked_by"] == ["r0"]
    card = decisions.card(fourth)
    assert card["status"] == "queued"
    assert decisions.waiting_text(card).startswith(decisions.QUEUED_TEXT)
    # A fifth and a second asker of the same queued question wait their turn too.
    outcome, fifth = decisions.file_request(
        db, store, request(topic="t4"), project=str(project), decides_alone=False
    )
    assert outcome == "queued"
    outcome, again = decisions.file_request(
        db,
        store,
        request(topic="t3"),
        project=str(project),
        decides_alone=False,
        source={"run_id": "r9"},
    )
    assert outcome == "queued" and again["id"] == fourth["id"]
    listed = decisions.view(decisions.read(db, store), str(project))
    assert (listed["waiting"], listed["queued"]) == (3, 2)
    assert [i["status"] for i in listed["items"]] == ["pending"] * 3 + ["queued"] * 2
    # Another project still gets its own cards.
    other = project.parent / "other"
    other.mkdir()
    outcome, _ = decisions.file_request(
        db, store, request(topic="t0", answer="make"), project=str(other), decides_alone=False
    )
    assert outcome == "pending"

    def status(item: dict[str, Any]) -> str:
        found = next(i for i in decisions.read(db, store)["items"] if i["id"] == item["id"])
        return str(found["status"])

    # Answering a waiting one turns the oldest queued one pending, keeping its askers.
    decisions.change(db, store, {"action": "use", "id": shown[1]["id"]}, actor="user")
    assert status(fourth) == "pending" and status(fifth) == "queued"
    moved = next(i for i in decisions.read(db, store)["items"] if i["id"] == fourth["id"])
    assert moved["asked_by"] == ["r3", "r9"]
    assert moved["history"][-1]["action"] == "moved up"
    # Turning one down or forgetting one makes room the same way.
    decisions.change(db, store, {"action": "reject", "id": shown[0]["id"]}, actor="user")
    assert status(fifth) == "pending"
    decisions.change(db, store, {"action": "forget", "id": shown[2]["id"]}, actor="user")
    listed = decisions.view(decisions.read(db, store), str(project))
    assert (listed["waiting"], listed["queued"]) == (2, 0)


def test_chat_says_a_question_is_queued_instead_of_naming_another(book, project: Path):
    db, store = book
    for n in range(3):
        decisions.file_request(
            db, store, request(topic=f"t{n}"), project=str(project), decides_alone=False
        )
    rem = type("Rem", (), {"store": store})()
    said = RemHome._file_chat_request(rem, db, request(topic="t3"), [{"root": str(project)}])  # type: ignore[arg-type]
    queued = decisions.read(db, store)["items"][-1]
    assert queued["status"] == "queued" and queued["topic"] == "t3"
    assert said.startswith(f"{decisions.QUEUED_TEXT} ({queued['id']})")
    first = decisions.read(db, store)["items"][0]
    assert first["id"] not in said
    # Rem's chat can still apply the user's answer to it by ID.
    answer = decisions.DecisionAnswer(id=queued["id"], action="use", said="use your pick for t3")
    applied, _ = decisions.answer_from_chat(db, store, answer, message="Use your pick for t3")
    assert applied is not None and applied["status"] == "confirmed"


# Rem's worker answers ---------------------------------------------------------------------


def _bare(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
    return {"needs_user": True, "user_question": "Should this land on main or dev?"}


def test_a_bare_question_is_retried_once_then_raticode_builds_the_card(
    home: RemHome, tmp_path: Path
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    prompts: list[str] = []

    def generate(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        prompts.append(prompt)
        return _bare(prompt, config, cancel)

    home.generate = generate
    reply = home._answer_worker(
        task["active_run"], "Main or dev?", "I found a dev branch.", threading.Event()
    )
    assert len(prompts) == 2
    assert "couldn't be used" in prompts[1] and "decision_request" in prompts[1]
    assert reply.needs_user and reply.decision_request is None
    card = reply._fallback
    assert card is not None and card["title"] == decisions.FALLBACK_TITLE
    assert card["found"] == "I found a dev branch."
    blocked = home._settle_question(task["active_run"], reply)
    assert blocked is not None
    text, shown = blocked
    assert text.startswith(decisions.FALLBACK_TITLE) and shown["fallback"] is True


def test_a_recommended_question_waits_for_the_user_and_their_answer_resumes_the_run(
    home: RemHome, tmp_path: Path, project: Path
):
    asked = {
        "needs_user": True,
        "decision_request": request().model_dump(),
    }
    original = home.source
    workers: list[dict[str, Any]] = []

    async def source(**kwargs: Any):
        is_review = "independent read-only reviewer" in kwargs["agent_instructions"]
        if not is_review:
            workers.append(kwargs)
        if is_review and len(workers) == 1:
            yield {
                "type": "final",
                "message": {
                    "body": json.dumps(
                        {
                            "criteria": [{"id": "check", "result": "unknown", "evidence": "?"}],
                            "feedback": "Answer the worker",
                            "question_to_rem": "Which check proves the work?",
                        }
                    )
                },
            }
            return
        async for event in original(**kwargs):
            yield event

    home.source = source
    home.generate = lambda p, c, e: asked
    task = home.create_task({"request_id": "work", **recipe(project)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "blocked" and run["needs_user"]
    card = run["decision_request"]
    assert card["recommended"] == "python3 scripts/check-dev.py"
    assert "Rem recommends: python3 scripts/check-dev.py" in run["user_question"]
    listed = home.decisions()
    assert listed["waiting"] == 1 and listed["items"][0]["asked_by"] == [run["id"]]
    home.generate = lambda p, c, e: {"summary": "Done.", "needs_user": False}
    home.change_decisions({"action": "use", "id": card["id"]})
    run = wait_run(home, task["id"], {"succeeded"})
    assert "The user decided" in workers[-1]["messages"][-1]["body"]
    # The next worker in this project gets the answer instead of asking again.
    background = home._worker_background({**run, "epoch": run["epoch"] + 1}, run["config"])
    assert background["project_decisions"][0]["decision"] == "python3 scripts/check-dev.py"


def test_forgetting_a_waiting_question_tells_the_run_that_asked_it(home: RemHome, project: Path):
    workers: list[dict[str, Any]] = []
    original = home.source

    async def source(**kwargs: Any):
        is_review = "independent read-only reviewer" in kwargs["agent_instructions"]
        if not is_review:
            workers.append(kwargs)
        if is_review and len(workers) == 1:
            yield {
                "type": "final",
                "message": {
                    "body": json.dumps(
                        {
                            "criteria": [{"id": "check", "result": "unknown", "evidence": "?"}],
                            "feedback": "Answer the worker",
                            "question_to_rem": "Which check proves the work?",
                        }
                    )
                },
            }
            return
        async for event in original(**kwargs):
            yield event

    home.source = source
    home.generate = lambda p, c, e: {
        "needs_user": True,
        "decision_request": request().model_dump(),
    }
    task = home.create_task({"request_id": "work", **recipe(project)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "blocked" and run["needs_user"]
    card = run["decision_request"]
    assert home.decisions()["items"][0]["asked_by"] == [run["id"]]
    asked = len(workers)

    home.generate = lambda p, c, e: {"summary": "Done without it.", "needs_user": False}
    listed = home.change_decisions({"action": "forget", "id": card["id"]})
    assert not [i for i in listed["items"] if i["id"] == card["id"]]
    run = wait_run(home, task["id"], {"succeeded"})
    assert run["state"] != "blocked"
    assert len(workers) > asked
    told = workers[asked]["messages"][-1]["body"]
    assert f"The user withdrew {card['id']}" in told
    assert "Finish everything that doesn't depend on it and say what is left." in told
    # The chat card for the question says it was withdrawn, not just gone.
    with home.store.read() as db:
        rows = db.execute(
            "SELECT body FROM messages WHERE json_extract(body, '$.work.decision.id') = ?",
            (card["id"],),
        ).fetchall()
    assert rows
    assert {json.loads(r[0])["work"]["decision"]["status"] for r in rows} == {"withdrawn"}


def test_forgetting_a_decided_question_sends_nothing(home: RemHome, project: Path):
    sent: list[dict[str, Any]] = []

    def answer(item: dict[str, Any]) -> list[str]:
        sent.append(item)
        return []

    home._answer_waiting_runs = answer  # type: ignore[method-assign]
    with home.store.connect() as db:
        decisions.change(
            db,
            home.store,
            {"action": "add", "topic": "checks.fast", "question": "Which check?", "answer": "x"},
            actor="user",
        )
    identity = home.decisions()["items"][0]["id"]
    home.change_decisions({"action": "forget", "id": identity})
    assert sent == []


def test_auto_mode_decides_reversible_questions_itself_and_keeps_working(
    home: RemHome, project: Path
):
    with home.store.connect() as db:
        home.store.set_setting(db, "self_work", {"enabled": True, "auto_approve_proposals": True})
    task = home.create_task({"request_id": "create", **recipe(project)})["task"]
    run = task["active_run"]
    reply = WorkerReply.model_validate(
        {"needs_user": True, "decision_request": request(topic="tests.runner").model_dump()}
    )
    assert home._settle_question(run, reply) is None
    assert not reply.needs_user and "provisional" in reply.next_message
    item = home.decisions()["items"][0]
    assert item["status"] == "provisional" and item["decided_by"] == "rem"
    # A second question on the topic is answered from the record.
    again = WorkerReply.model_validate(
        {
            "needs_user": True,
            "decision_request": request(topic="tests.runner", answer="pytest").model_dump(),
        }
    )
    assert home._settle_question(run, again) is None
    assert "already decided" in again.next_message and "check-dev.py" in again.next_message
    # The check command is the scan's or the user's to set: the worker's waits for the user,
    # and in a board ticket's run with auto mode on the worker carries on without it.
    check = WorkerReply.model_validate(
        {"needs_user": True, "decision_request": request().model_dump()}
    )
    assert home._settle_question({**run, "self_work": {"kind": "ticket"}}, check) is None
    assert not check.needs_user and "Only the project scan or the user" in check.next_message
    filed = next(i for i in home.decisions()["items"] if i["topic"] == "checks.fast")
    assert filed["status"] == "pending"
    # With auto mode off, the same kind of question waits for the user.
    with home.store.connect() as db:
        home.store.set_setting(db, "self_work", {"enabled": True, "auto_approve_proposals": False})
    other = WorkerReply.model_validate(
        {"needs_user": True, "decision_request": request(topic="branch.integration").model_dump()}
    )
    blocked = home._settle_question(run, other)
    assert blocked is not None and blocked[1]["status"] == "pending"


def _decision_notices(home: RemHome, cursor: str) -> dict[str, Any]:
    from gofer.ui.rem_notices import read_notices

    return read_notices(home, None, cursor, ["needs_you"])


def test_a_user_only_question_in_auto_mode_sends_one_needs_you_alert(home: RemHome, project: Path):
    with home.store.connect() as db:
        home.store.set_setting(db, "self_work", {"enabled": True, "auto_approve_proposals": True})
    start = _decision_notices(home, "")["cursor"]
    task = home.create_task({"request_id": "create", **recipe(project)})["task"]
    # A board ticket's run, which auto mode governs.
    run = {**task["active_run"], "self_work": {"kind": "ticket"}}
    asked = request(topic="release.channel", answer="Ship to beta first", reversible=False)
    reply = WorkerReply.model_validate({"needs_user": True, "decision_request": asked.model_dump()})
    # Auto mode: the user decides this one, but the worker goes on without it.
    assert home._settle_question(run, reply) is None and not reply.needs_user
    item = next(i for i in home.decisions()["items"] if i["topic"] == "release.channel")
    assert item["status"] == "pending"
    first = _decision_notices(home, start)
    assert [(n["kind"], n["reason"], n["decision"]["id"]) for n in first["notices"]] == [
        ("decision", "needs_you", item["id"])
    ]
    notice = first["notices"][0]
    assert notice["body"] == f"Rem has a question: {item['question']}"
    assert notice["decision"]["recommended"] == "Ship to beta first"
    assert notice["project_name"] == project.name
    # Asked again by the worker: still one card and one alert.
    again = WorkerReply.model_validate({"needs_user": True, "decision_request": asked.model_dump()})
    assert home._settle_question(run, again) is None
    assert _decision_notices(home, first["cursor"])["notices"] == []
    # The alert counts under Needs you only.
    from gofer.ui.rem_notices import read_notices

    assert read_notices(home, None, start, ["finished", "proposals"])["notices"] == []


def test_a_question_a_run_stops_for_or_rem_decides_sends_no_decision_alert(
    home: RemHome, project: Path
):
    start = _decision_notices(home, "")["cursor"]
    task = home.create_task({"request_id": "create", **recipe(project)})["task"]
    run = {**task["active_run"], "self_work": {"kind": "ticket"}}
    # Auto mode off: the run stops for the question, and its task update is the alert.
    asked = request(topic="release.channel", reversible=False)
    reply = WorkerReply.model_validate({"needs_user": True, "decision_request": asked.model_dump()})
    assert home._settle_question(run, reply) is not None
    # Auto mode on: a reversible question is Rem's provisional answer, nothing to tell.
    with home.store.connect() as db:
        home.store.set_setting(db, "self_work", {"enabled": True, "auto_approve_proposals": True})
    mine = WorkerReply.model_validate(
        {"needs_user": True, "decision_request": request(topic="tests.runner").model_dump()}
    )
    assert home._settle_question(run, mine) is None
    assert [n for n in _decision_notices(home, start)["notices"] if n["kind"] == "decision"] == []


def test_a_queued_question_alerts_once_it_moves_up(home: RemHome, project: Path):
    with home.store.connect() as db:
        home.store.set_setting(db, "self_work", {"enabled": True, "auto_approve_proposals": True})
    task = home.create_task({"request_id": "create", **recipe(project)})["task"]
    run = {**task["active_run"], "self_work": {"kind": "ticket"}}
    for number in range(decisions.MAX_PENDING_PER_PROJECT + 1):
        asked = request(topic=f"release.step{number}", reversible=False)
        reply = WorkerReply.model_validate(
            {"needs_user": True, "decision_request": asked.model_dump()}
        )
        assert home._settle_question(run, reply) is None
    items = home.decisions()["items"]
    queued = next(i for i in items if i["status"] == "queued")
    shown = _decision_notices(home, "")["cursor"]
    # Queued questions have no card yet, so no alert either; the three waiting ones had one.
    from gofer.core.phone_alerts import PhoneAlertStore

    alerts = PhoneAlertStore(Path(home.data_dir)).after(0)["alerts"]
    assert sorted(a["decision_id"] for a in alerts) == sorted(
        i["id"] for i in items if i["status"] == "pending"
    )
    first = next(i for i in items if i["status"] == "pending")
    home.change_decisions({"action": "use", "id": first["id"]})
    moved = _decision_notices(home, shown)
    assert [n["decision"]["id"] for n in moved["notices"] if n["kind"] == "decision"] == [
        queued["id"]
    ]


def test_the_user_only_routes_refuse_rem_and_agents(home: RemHome):
    # The service's change is always the user's; Rem's own path is file_request only.
    with home.store.connect() as db, pytest.raises(DecisionRefused):
        decisions.change(db, home.store, {"action": "add", "answer": "x"}, actor="rem")


# F2: finish lines on proposals ------------------------------------------------------------


def test_proposals_carry_a_checkable_finish_line():
    proposal = Proposal.model_validate(
        {
            "title": "Faster usage page",
            "done_when": "The usage page loads 90 days in under 300 ms\nIt works well\nok",
        }
    )
    assert len(proposal.done_when) == 3
    assert checkable_finish(proposal.done_when) == ["The usage page loads 90 days in under 300 ms"]
    assert checkable_finish(["The settings page is cleaner and easier to read"]) == []
    assert checkable_finish(["`pytest tests/unit/test_usage.py` passes"]) == [
        "`pytest tests/unit/test_usage.py` passes"
    ]


def test_each_finish_line_statement_becomes_its_own_review_check():
    from types import SimpleNamespace

    from gofer.ui.rem_self_work import SelfWork

    worker = SimpleNamespace(_auto_mode=lambda: False)
    made = SelfWork._recipe(
        worker,  # type: ignore[arg-type]
        {
            "title": "Faster usage page",
            "description": "Cache the grouped query.",
            "type": "develop",
            "project_dir": None,
            "created_by": "self_work",
            "done_when": "The usage page loads 90 days in under 300 ms\n"
            "`pytest tests/unit/test_usage.py` passes",
        },
    )
    assert [c.description for c in made.acceptance_criteria] == [
        "The usage page loads 90 days in under 300 ms",
        "`pytest tests/unit/test_usage.py` passes",
    ]
    assert {c.authored_by for c in made.acceptance_criteria} == {"rem_recommended"}


def test_the_cli_list_counts_queued_questions_and_names_their_status():
    from gofer.cli.commands import rem_api

    item = {"id": "D-7", "status": "queued", "question": "Which port?", "recommended": "8080"}
    shown = rem_api._decisions({"waiting": 3, "queued": 1, "confirmed": 2, "items": [item]})
    head, line = str(shown.text).splitlines()
    assert head == "3 waiting (1 more queued), 2 decided."
    assert line.startswith("D-7  queued behind the questions waiting for you: Which port?")


# Who asked a waiting question, when, and what waits on it ---------------------------------


def _ticket_run(run_id: str, state: str, **kw: Any) -> dict[str, Any]:
    return {
        "id": run_id,
        "task_id": "task-1",
        "state": state,
        "tickets": [{"key": "REM-1", "title": "Place work on computers"}],
        "recipe": {"title": "Place work on computers"},
        **kw,
    }


def test_a_waiting_question_says_which_ticket_asked_and_whether_its_run_waits(book, project: Path):
    db, store = book
    source = {"run_id": "run-1", "task_id": "task-1", "ticket": "REM-1"}
    outcome, item = decisions.file_request(
        db, store, request(), project=str(project), decides_alone=False, source=source
    )
    assert outcome == "pending"
    # The run stopped for this question: it is listed as waiting.
    blocked = _ticket_run(
        "run-1", "blocked", needs_user=True, decision_request=decisions.card(item)
    )
    listed = decisions.view(decisions.read(db, store), str(project), {"run-1": blocked})
    asked = listed["items"][0]["asked"]
    assert asked["by"] == "run" and asked["ticket"] == "REM-1"
    assert asked["task_title"] == "Place work on computers" and asked["opens"] == "task-1"
    assert asked["at"] == item["created_at"]
    assert [e["run_id"] for e in asked["waiting"]] == ["run-1"] and asked["went_on"] == []
    line = decisions.asked_line(asked)
    assert line.startswith("Asked by REM-1 · ") and line.endswith(" · REM-1 waits for this")
    # The same request from an auto-mode run that went on without it: nothing waits.
    db2, store2 = sqlite3.connect(":memory:"), Store()
    decisions.file_request(
        db2, store2, request(), project=str(project), decides_alone=True, source=source
    )
    went_on = _ticket_run("run-1", "running", needs_user=False, self_work={"kind": "ticket"})
    listed = decisions.view(decisions.read(db2, store2), str(project), {"run-1": went_on})
    asked = listed["items"][0]["asked"]
    assert asked["ticket"] == "REM-1" and asked["waiting"] == []
    assert [e["run_id"] for e in asked["went_on"]] == ["run-1"]
    assert decisions.asked_line(asked).endswith(" · REM-1 went on without it")
    # A question from Rem's chat or a project scan names that instead; a decided one has no line.
    for via, label in (({"via": "chat"}, "Rem's chat"), (SCAN, "Project scan")):
        db3, store3 = sqlite3.connect(":memory:"), Store()
        decisions.file_request(
            db3, store3, request(), project=str(project), decides_alone=False, source=via
        )
        shown = decisions.view(decisions.read(db3, store3), str(project))["items"][0]
        assert shown["asked"]["label"] == label and shown["asked"]["opens"] == ""
        assert decisions.asked_line(shown["asked"]).count(" · ") == 1
    decisions.change(db, store, {"action": "use", "id": item["id"]}, actor="user")
    assert "asked" not in decisions.view(decisions.read(db, store), str(project))["items"][0]


def test_the_asked_line_names_a_task_without_a_ticket_and_counts_several_runs():
    block = {
        "label": "Tidy the docs",
        "at": "2026-11-21T12:00:00+00:00",
        "waiting": [{"label": "REM-2"}, {"label": "REM-3"}, {"label": "REM-4"}],
        "went_on": [],
    }
    assert decisions.short_day("2026-11-21T12:00:00") == "21 Nov"
    assert decisions.asked_line(block) == (
        "Asked by Tidy the docs · 21 Nov · REM-2 and 2 more wait for this"
    )
    assert decisions._run_entry("r", {"self_work": {"kind": "objective"}})["label"] == "Self-work"


def test_the_cli_list_prints_who_asked_under_a_waiting_question():
    from gofer.cli.commands import rem_api

    item = {
        "id": "D-7",
        "status": "pending",
        "question": "Which port?",
        "recommended": "8080",
        "asked": {
            "label": "REM-1",
            "at": "2026-11-21T12:00:00",
            "waiting": [{"label": "REM-1"}],
            "went_on": [],
        },
    }
    shown = rem_api._decisions({"waiting": 1, "confirmed": 0, "items": [item]})
    assert str(shown.text).splitlines()[2] == "      Asked by REM-1 · 21 Nov · REM-1 waits for this"
