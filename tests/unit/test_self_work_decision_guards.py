"""Self-work's decision guardrails: work that breaks a decision in force never goes ahead.

Four checks (rem_decision_guard): a proposal that contradicts a decision isn't saved, a
backlog ticket that does is blocked instead of pulled, a ticket that does is blocked when
it would start, and the check phase fails work for one. Self-work also can't make itself
an exception: only the user changes decisions, and a request that would weaken one waits.
Fake providers and isolated data, through the board's own fixtures.
"""

import json
from typing import Any

import pytest

from gofer.ui import rem_decision_guard as guard
from gofer.ui import rem_decisions as decision_book
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_kanban import KanbanRefused, get_ticket
from gofer.ui.rem_self_work import Proposal
from tests.unit.test_rem_kanban import (
    add,
    at,
    project_of,
    ranked,
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
from tests.unit.test_self_work_auto_approve import (
    RESEARCH,
    ideas,
    research_done,
    timeline,
    turn_on,
)

REVIEWER = "independent read-only reviewer"


def decide(home: RemHome, **fields: Any) -> dict[str, Any]:
    """The user records a decision record for the test project; returns it."""
    view = home.change_decisions(
        {
            "action": "add",
            "project": project_of(home),
            "remember_as": "record",
            "question": "How are workflows written?",
            "answer": "Rattish, never TOML",
            "signals": ["toml workflows"],
            **fields,
        }
    )
    return max(
        (i for i in view["items"] if i["status"] == "confirmed"),
        key=lambda i: int(i["id"].split("-")[1]),
    )


def worker_calls(fake: Any, title: str) -> list[dict[str, Any]]:
    return [
        c
        for c in fake.calls
        if REVIEWER not in c["agent_instructions"] and title in str(c.get("prompt") or c)
    ]


def stored(home: RemHome, title: str) -> dict[str, Any]:
    with home.store.read() as db:
        return get_ticket(db, ticket(home, title)["ticket_id"])


# The guard itself -------------------------------------------------------------------


DECIDED: list[dict[str, Any]] = [
    {
        "id": "D-4",
        "question": "How are workflows written?",
        "answer": "Rattish, never TOML",
        "status": "confirmed",
        "remember_as": "record",
        "record": {"signals": ["toml workflows"], "forbids": ["workflows/*.toml"]},
    },
    {"id": "D-5", "question": "Checks", "answer": "pytest", "status": "confirmed"},
]


def test_a_proposal_or_ticket_contradicts_by_name_or_signal_word():
    named = Proposal.model_validate({"title": "Faster checks", "conflicts_with": ["D-5", "D-9"]})
    assert [h["id"] for h in guard.proposal_hits(named, DECIDED)] == ["D-5"]
    signal = Proposal.model_validate(
        {"title": "Import", "description": "x", "done_when": ["Reads TOML workflows from disk"]}
    )
    hits = guard.proposal_hits(signal, DECIDED)
    assert [h["id"] for h in hits] == ["D-4"] and "“toml workflows”" in hits[0]["why"]
    assert guard.proposal_hits(Proposal.model_validate({"title": "Docs"}), DECIDED) == []
    board_ticket: dict[str, Any] = {
        "title": "Support TOML workflows",
        "description": "",
        "status": "backlog",
    }
    assert [h["id"] for h in guard.ticket_hits(board_ticket, DECIDED)] == ["D-4"]
    # A proposal the user approved by hand was their call; its named conflict no longer counts.
    approved: dict[str, Any] = {
        "title": "Faster checks",
        "description": "",
        "proposal": {"state": "approved", "by": "user", "conflicts_with": ["D-5"]},
    }
    assert guard.ticket_hits(approved, DECIDED) == []
    approved["proposal"]["by"] = "self_work"
    assert [h["id"] for h in guard.ticket_hits(approved, DECIDED)] == ["D-5"]
    reason = guard.reason(guard.ticket_hits(board_ticket, DECIDED))
    assert reason.startswith("Violates decisions: D-4 (How are workflows written?)")


def test_signal_words_match_whole_words_only():
    rust = [{**DECIDED[0], "record": {"signals": ["rust"]}}]
    trust: dict[str, Any] = {"title": "Build trust in the checks", "description": ""}
    assert guard.ticket_hits(trust, rust) == []
    use_rust: dict[str, Any] = {"title": "Speed up parsing", "description": "use Rust here"}
    hits = guard.ticket_hits(use_rust, rust)
    assert [h["id"] for h in hits] == ["D-4"] and "“rust”" in hits[0]["why"]
    assert guard.proposal_hits(Proposal.model_validate({"title": "Trusted builds"}), rust) == []


def test_a_mark_holds_until_the_ticket_or_a_decision_changes():
    item: dict[str, Any] = {
        "title": "Support TOML workflows",
        "description": "",
        "status": "backlog",
    }
    hits = guard.ticket_hits(item, DECIDED)
    item["violates_decisions"] = guard.marker(item, hits, DECIDED, "backlog", "now")
    assert guard.still_marked(item, DECIDED)
    assert not guard.still_marked({**item, "description": "edited"}, DECIDED)
    changed = [{**DECIDED[0], "answer": "Rattish; TOML on import"}, DECIDED[1]]
    assert not guard.still_marked(item, changed)
    assert not guard.still_marked(item, DECIDED[1:])


def request(**fields: Any) -> decision_book.DecisionRequest:
    body: dict[str, Any] = {
        "topic": "imports.format",
        "question": "Which format do imports read?",
        "recommendation": {
            "answer": "JSON",
            "evidence": [{"kind": "doc", "ref": "README.md"}],
        },
        **fields,
    }
    return decision_book.DecisionRequest.model_validate(body)


def test_a_request_that_would_weaken_a_decision_is_found():
    assert guard.weakens(request(), DECIDED) == ([], "")
    assert guard.weakens(request(), []) == ([], "")
    ids, why = guard.weakens(
        request(question="Can imports skip D-5 for now?"),
        DECIDED,
    )
    assert ids == ["D-5"] and "self-work can't make exceptions" in why.casefold()
    ids, _ = guard.weakens(request(question="Allow TOML workflows for imports?"), DECIDED)
    assert ids == ["D-4"]
    ids, why = guard.weakens(
        request(question="Make an exception for the importer so it can keep its format?"),
        DECIDED,
    )
    assert ids == [] and "asks for an exception" in why
    ids, _ = guard.weakens(
        request(remember_as="record", record={"applies_to": ["workflows/legacy.toml"]}),
        DECIDED,
    )
    assert ids == ["D-4"]


# Check 4b: self-work can't make itself an exception ----------------------------------


def test_self_work_files_an_exception_as_a_conflict_for_the_user_even_in_auto_mode(board):
    home, _, _ = board
    record = decide(home)
    project = project_of(home)
    with home.store.connect() as db:
        outcome, item = decision_book.file_request(
            db,
            home.store,
            request(
                topic="importer.toml",
                question="Allow TOML workflows in the importer as an exception?",
            ),
            project=project,
            decides_alone=True,
            source={"run_id": "run-1"},
        )
    # Auto mode would have applied a plain request; this one waits as a conflict.
    assert outcome == "pending" and item["status"] == "pending"
    assert item["weakens"] == [record["id"]]
    assert record["id"] in item["conflict"]
    assert decision_book.card(item)["weakens"] == [record["id"]]
    with home.store.connect() as db:
        plain, other = decision_book.file_request(
            db,
            home.store,
            request(),
            project=project,
            decides_alone=True,
            source={"run_id": "run-1"},
        )
    assert plain == "provisional" and "weakens" not in other
    # The decision it would weaken is unchanged and still in force.
    kept = next(i for i in home.decisions()["items"] if i["id"] == record["id"])
    assert kept["status"] == "confirmed" and kept["answer"] == "Rattish, never TOML"


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "edit", "answer": "TOML is fine"},
        {"action": "replace", "answer": "TOML is fine"},
        {"action": "reject"},
        {"action": "reopen"},
        {"action": "forget"},
        {"action": "use"},
        {"action": "confirm_edit"},
    ],
)
def test_rem_can_never_edit_replace_waive_or_remove_a_decision(board, payload):
    home, _, _ = board
    record = decide(home)
    with home.store.connect() as db:
        with pytest.raises(decision_book.DecisionRefused) as refused:
            decision_book.change(db, home.store, {**payload, "id": record["id"]}, actor="rem")
    assert refused.value.code == "unauthorized"
    kept = next(i for i in home.decisions()["items"] if i["id"] == record["id"])
    assert kept["status"] == "confirmed" and kept["answer"] == "Rattish, never TOML"


# Check 1: authoring ------------------------------------------------------------------


@pytest.mark.parametrize("auto", [True, False])
def test_a_proposal_that_contradicts_a_decision_is_never_saved(board, auto):
    home, fake, _ = board
    if auto:
        turn_on(home)
    record = decide(home)
    fake.answers[RESEARCH] = ideas("Support TOML workflows again", "Voice shortcuts")
    at(home, 15)
    tick(home)
    research_done(home, "Voice shortcuts")
    assert ticket_or_none(home, "Support TOML workflows again") is None
    other = ticket(home, "Voice shortcuts")
    assert other["auto_approved"] is auto and other["proposed"] is not auto
    # Recorded with the decision it contradicts: on the research run and in the report.
    with home.store.read() as db:
        rows = [
            r[0]
            for r in db.execute(
                "SELECT body FROM self_work_report WHERE json_extract(body, '$.kind') = "
                "'proposal_rejected'"
            )
        ]
        runs = [
            r[0] for r in db.execute("SELECT body FROM runs WHERE body LIKE '%rejected_proposals%'")
        ]
    assert len(rows) == 1 and record["id"] in rows[0]
    assert "Support TOML workflows again" in rows[0]
    assert len(runs) == 1 and record["id"] in runs[0]
    notes = [n["text"] for n in home.self_work.view()["report"]["notes"]]
    assert any(
        "Didn't add “Support TOML workflows again”" in n and record["id"] in n for n in notes
    )


# Check 2: pulling from Backlog ---------------------------------------------------------


def add_backlog(home: RemHome, title: str, **fields: Any) -> dict[str, Any]:
    created: dict[str, Any] = add(home, title, status="backlog", **fields)
    ranked(home, title)
    return created


def test_a_violating_backlog_ticket_is_blocked_marked_and_never_pulled(board):
    home, fake, _ = board
    record = decide(home)
    set_options(home, auto_pull_backlog=True)
    add_backlog(home, "Support TOML workflows")
    at(home, 15)
    for _ in range(3):
        tick(home)
    held = ticket(home, "Support TOML workflows")
    assert held["status"] == "blocked" and not held["running"]
    assert held["violates_decisions"] == [record["id"]]
    assert held["violation_reason"].startswith("Violates decisions: " + record["id"])
    assert held["block_reason"] == held["violation_reason"]
    assert not worker_calls(fake, "Support TOML workflows")
    assert [e["title"] for e in timeline(home, held)] == ["Blocked: violates decisions"]
    # Self-work's own unblocking never picks it up again while nothing changed.
    for _ in range(3):
        tick(home)
    assert ticket(home, "Support TOML workflows")["status"] == "blocked"
    assert not worker_calls(fake, "Support TOML workflows")


def test_editing_the_ticket_clears_the_mark_and_it_can_be_pulled(board):
    home, fake, _ = board
    decide(home)
    set_options(home, auto_pull_backlog=True)
    add_backlog(home, "Support TOML workflows")
    at(home, 15)
    tick(home)
    held = ticket(home, "Support TOML workflows")
    assert held["status"] == "blocked"
    home.kanban.edit(
        "user",
        {
            "ticket_id": held["ticket_id"],
            "expected_revision": held["revision"],
            "title": "Convert old workflow files to Rattish",
        },
    )
    tick(home)
    back = ticket(home, "Convert old workflow files to Rattish")
    assert back["violates_decisions"] == [] and back["status"] in {"backlog", "in_progress"}
    assert "No longer violates decisions" in [e["title"] for e in timeline(home, back)]
    ranked(home, "Convert old workflow files to Rattish")
    settle(home, "Convert old workflow files to Rattish")
    assert ticket(home, "Convert old workflow files to Rattish")["status"] == "done"


def test_changing_the_decision_clears_the_mark(board):
    home, _, _ = board
    record = decide(home)
    set_options(home, auto_pull_backlog=False)
    add(home, "Support TOML workflows")
    at(home, 15)
    tick(home)
    assert ticket(home, "Support TOML workflows")["status"] == "blocked"
    home.change_decisions({"action": "forget", "id": record["id"]})
    tick(home)
    assert ticket(home, "Support TOML workflows")["violates_decisions"] == []
    # It was waiting in To do, so it goes back there and self-work starts it.
    settle(home, "Support TOML workflows")
    assert ticket(home, "Support TOML workflows")["status"] == "done"


def test_a_pending_proposal_is_left_for_the_user(board):
    home, _, _ = board
    decide(home)
    from tests.unit.test_rem_kanban import add_proposal

    add_proposal(home, "Support TOML workflows")
    at(home, 15)
    tick(home)
    waiting = ticket(home, "Support TOML workflows")
    assert waiting["proposed"] is True and waiting["status"] == "backlog"
    assert waiting["violates_decisions"] == []


# Check 3: starting an assigned ticket ------------------------------------------------


def test_a_violating_todo_is_blocked_at_start_and_no_worker_runs(board):
    home, fake, _ = board
    record = decide(home)
    created = add(home, "Support TOML workflows")
    # Started by the claim itself, as the loop's _fill would, without the board sweep.
    with home.store.read() as db:
        config = home.store.setting(db, "config")
    started = home.self_work._begin_ticket(created["id"], config)
    assert started is False
    held = ticket(home, "Support TOML workflows")
    assert held["status"] == "blocked" and held["violates_decisions"] == [record["id"]]
    assert not worker_calls(fake, "Support TOML workflows")
    assert stored(home, "Support TOML workflows")["violates_decisions"]["from"] == "todo"


def test_moving_a_violating_ticket_to_in_progress_blocks_it_instead(board):
    home, fake, _ = board
    record = decide(home)
    created = add(home, "Support TOML workflows")
    with pytest.raises(KanbanRefused) as refused:
        home.kanban.move(
            "user",
            {"ticket_id": created["id"], "expected_revision": 1, "column": "in_progress"},
        )
    assert record["id"] in str(refused.value)
    held = ticket(home, "Support TOML workflows")
    assert held["status"] == "blocked" and held["violates_decisions"] == [record["id"]]
    assert not held["running"] and not worker_calls(fake, "Support TOML workflows")


# Check 4: review ---------------------------------------------------------------------


def test_work_for_a_ticket_that_now_violates_a_decision_fails_its_check(board):
    home, fake, _ = board
    fake.hold.clear()
    add(home, "Support TOML workflows")
    at(home, 15)
    tick(home)
    wait_for(lambda: ticket(home, "Support TOML workflows")["running"])
    # The user decides against it while the worker is still on it.
    record = decide(home)
    fake.hold.set()
    settle(home, "Support TOML workflows")
    done = ticket(home, "Support TOML workflows")
    assert done["status"] == "blocked"
    assert done["violates_decisions"] == [record["id"]]
    assert "Violates decisions" in done["block_reason"]
    # No reviewer was asked to pass it, and no repair was sent. (Research self-work started
    # beside it has its own reviews.)
    with home.store.read() as db:
        run = home.store.get(db, "runs", done["run_id"])
    review = run.get("verification_thread_id") or "\0"
    assert not [
        c
        for c in fake.calls
        if REVIEWER in c["agent_instructions"] and review in c["conversation_id"]
    ]
    assert run["state"] == "blocked" and run["decision_check"]["passed"] is False
    assert run.get("repair_count", 0) == 0
    # Not picked up again on its own.
    for _ in range(3):
        tick(home)
    assert ticket(home, "Support TOML workflows")["status"] == "blocked"


# Decisions reach a worker once -------------------------------------------------------


def decide_facts(home: RemHome) -> list[str]:
    """Two plain answers and a record for the test project; returns the questions."""
    questions = [
        "Which branch does finished work land on?",
        "Which command shows the work is sound before it lands?",
    ]
    for question, topic, answer in zip(
        questions,
        ["branch.integration", "checks.fast"],
        ["dev", "python3 scripts/check-dev.py"],
        strict=True,
    ):
        home.change_decisions(
            {
                "action": "add",
                "project": project_of(home),
                "topic": topic,
                "question": question,
                "answer": answer,
            }
        )
    decide(home)
    return questions


def titled(fake: Any, title: str) -> list[dict[str, Any]]:
    """Worker turns of the task with this title (the research board lists other titles)."""
    return [
        c
        for c in fake.calls
        if REVIEWER not in c["agent_instructions"]
        and c["workflow"]["chatThreadTitle"] == f"Rem task: {title}"
    ]


def test_a_research_worker_gets_the_project_decisions_once(board):
    home, fake, _ = board
    questions = decide_facts(home)
    fake.answers[RESEARCH] = ideas("Voice shortcuts")
    at(home, 15)
    tick(home)
    research_done(home, "Voice shortcuts")
    research = titled(fake, RESEARCH)
    assert research
    for call in research:
        instructions = call["agent_instructions"]
        # The research prompt lists them; the background keeps only the rule.
        for question in questions:
            assert instructions.count(question) == 1, question
        assert instructions.count("Rattish, never TOML") == 1
        assert '"project_decisions":' not in instructions
        assert "project_decisions_rule" in instructions
        assert "Keep to project_decisions." in instructions


def test_research_lists_a_plans_answers_so_a_proposal_against_one_is_caught(board):
    home, fake, _ = board
    with home.store.connect() as db:
        _, item = decision_book.file_request(
            db,
            home.store,
            request(topic="plan:imports", question="Which format do plan imports read?"),
            project=project_of(home),
            decides_alone=False,
            plan="P-1",
        )
    answer = home.change_decisions({"action": "use", "id": item["id"]})
    assert {i["id"]: i["status"] for i in answer["items"]}[item["id"]] == "confirmed"
    body = json.dumps(
        {
            "proposals": [
                {"title": "YAML imports", "description": "Read YAML", "why": "Asked for"},
                {
                    "title": "TOML imports",
                    "description": "Read TOML instead of JSON",
                    "why": "Faster",
                    "conflicts_with": [item["id"]],
                },
            ],
            "duplicates": [],
        }
    )
    fake.answers[RESEARCH] = f"Findings.\n```json\n{body}\n```"
    at(home, 15)
    tick(home)
    research_done(home, "YAML imports")
    research = titled(fake, RESEARCH)
    assert research
    for call in research:
        # The research task cites neither the plan nor the answer, yet lists it once.
        assert call["agent_instructions"].count("Which format do plan imports read?") == 1
    # So research can name it, and the guard keeps the contradicting idea off the board.
    assert ticket_or_none(home, "TOML imports") is None
    notes = [n["text"] for n in home.self_work.view()["report"]["notes"]]
    assert any("Didn't add “TOML imports”" in n and item["id"] in n for n in notes)


def test_a_develop_worker_in_the_same_project_still_gets_project_decisions(board):
    home, fake, _ = board
    questions = decide_facts(home)
    add(home, "Faster usage page")
    at(home, 15)
    tick(home)
    settle(home, "Faster usage page")
    with home.store.read() as db:
        run = home.store.get(db, "runs", ticket(home, "Faster usage page")["run_id"])
    assert run["recipe"]["type"] == "develop"
    instructions = titled(fake, "Faster usage page")[0]["agent_instructions"]
    assert '"project_decisions":' in instructions
    assert "project_decisions_rule" in instructions
    for question in questions:
        assert instructions.count(question) == 1, question
    assert instructions.count("Rattish, never TOML") == 1
