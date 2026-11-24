"""Reading backlog rankings and guardrail retro answers one entry at a time.

A slightly-off block keeps what is usable: long text is cut to its limit, and only an entry
whose required fields can't be used is dropped, with its reason recorded on the run and in
the self-work report. An answer with no readable block goes back to its worker before
review, as research does. Fake providers and isolated data.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from gofer.ui import rem_guardrail_retro as retro
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_self_work import RANKING_FORMAT, TRIAGE_TICKETS, read_ranking
from tests.unit.test_rem_guardrail_retro import (
    answer,
    good,
    retro_calls,
    retro_state,
    seed_failures,
)
from tests.unit.test_rem_kanban import (
    BASE,
    at,
    set_options,
    settle,
    tick,
    ticket,
    ticket_or_none,
    wait_for,
)
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_self_work_fill import (
    RANK,
    add_backlog,
    quiet_with_no_ranking,
    ranker_prompts,
    ranking,
)


def ranks(*tickets: dict[str, Any]) -> str:
    return f"Done.\n```json\n{json.dumps({'tickets': list(tickets)})}\n```"


def rank(ticket_id: str, **fields: Any) -> dict[str, Any]:
    return {"ticket_id": ticket_id, "impact": 3, "relevance": 4, "reason": "Fits", **fields}


def runs_of(home: RemHome, kind: str) -> list[dict[str, Any]]:
    with home.store.read() as db:
        runs = [json.loads(r[0]) for r in db.execute("SELECT body FROM runs")]
    return [r for r in runs if (r.get("self_work") or {}).get("kind") == kind]


# Reading a ranking ------------------------------------------------------------------------


def test_a_ranking_with_one_over_long_reason_ranks_both_tickets():
    result = read_ranking(ranks(rank("a"), rank("b", reason="r" * 501)))
    assert result is not None
    assert [t.ticket_id for t in result.tickets] == ["a", "b"]
    assert result.tickets[1].reason == "r" * 500
    assert result.dropped == []


def test_a_ranking_with_impact_six_still_ranks_the_valid_ticket():
    result = read_ranking(ranks(rank("a"), rank("b", impact=6)))
    assert result is not None
    assert [t.ticket_id for t in result.tickets] == ["a"]
    assert len(result.dropped) == 1
    assert result.dropped[0]["ticket_id"] == "b"
    assert result.dropped[0]["reason"].startswith("impact:")
    assert "5" in result.dropped[0]["reason"]


def test_only_a_ticket_id_impact_or_relevance_that_cant_be_used_drops_a_ranking():
    result = read_ranking(
        ranks(
            {"impact": 3, "relevance": 3},
            rank("", reason="no id"),
            rank("c", relevance=0),
            rank("d", impact="high"),
            "text",
            rank("e", skip="maybe", objective=["x"] * 3, reason=["Two", "lines"]),
        )
    )
    assert result is not None
    assert [t.ticket_id for t in result.tickets] == ["e"]
    # A bad optional field falls back to its default instead of losing the ranking.
    assert result.tickets[0].skip is False and result.tickets[0].reason == "Two\nlines"
    assert [d["reason"].split(":")[0] for d in result.dropped] == [
        "ticket_id",
        "ticket_id",
        "relevance",
        "impact",
        "ranking",
    ]
    assert [d["ticket_id"] for d in result.dropped] == ["", "", "c", "d", ""]
    # ...and that is listed, so a skip the worker garbled never reads as a plain "no"
    # (self-work report, 21 November afternoon).
    assert result.repaired == [
        {
            "ticket_id": "e",
            "reason": "skip: Input should be a valid boolean, unable to interpret input; "
            "used the default",
        }
    ]


def test_a_ranking_reads_at_most_the_tickets_one_run_is_shown():
    result = read_ranking(ranks(*(rank(f"t{n}") for n in range(TRIAGE_TICKETS + 5))))
    assert result is not None and len(result.tickets) == TRIAGE_TICKETS
    assert read_ranking("No JSON here.") is None
    assert read_ranking('```json\n{"tickets":"none"}\n```') is None


def test_the_ranking_format_states_the_limits():
    text = " ".join(RANKING_FORMAT.split())
    assert f"only the first {TRIAGE_TICKETS} are read" in text
    assert "whole numbers from 1 to 5" in text
    assert "Keep reason to 500 characters; longer text is cut off there." in text


# Reading a retro ----------------------------------------------------------------------------


def test_a_retro_with_fix_docs_on_one_of_two_proposals_keeps_the_other():
    result = retro.read_result(answer(good("Decimal totals test"), good("Docs page", fix="docs")))
    assert result is not None
    assert [p.title for p in result.proposals] == ["Decimal totals test"]
    assert len(result.dropped) == 1
    assert result.dropped[0]["title"] == "Docs page"
    assert result.dropped[0]["reason"].startswith("fix:")
    assert "agents_pointer" in result.dropped[0]["reason"]


def test_retro_text_is_cut_to_its_limits_and_only_the_first_five_count():
    proposal, reason, repairs = retro.read_proposal(
        good(
            "t" * 200,
            description="d" * 5000,
            why="w" * 1200,
            cause="c" * 500,
            project="p" * 500,
            run_ids=["r" * 200] * 45,
            done_when=["x" * 400] * 5,
            fix=" Lint ",
        )
    )
    assert reason == "" and proposal is not None
    assert (len(proposal.title), len(proposal.description), len(proposal.why)) == (160, 4000, 1000)
    assert (len(proposal.cause), len(proposal.project)) == (400, 400)
    assert proposal.run_ids == ["r" * 127 + "…"] * 40
    assert proposal.done_when == ["x" * 299 + "…"] * 3
    assert repairs == [
        "run_ids: cut 45 items to 128 characters",
        "run_ids: kept the first 40 of 45 items",
        "done_when: cut 5 items to 300 characters",
        "done_when: kept the first 3 of 5 items",
    ]
    assert proposal.fix == "lint"
    # A missing fix keeps the default; a missing title loses the proposal.
    missing = {k: v for k, v in good("No fix named").items() if k != "fix"}
    assert retro.read_proposal(missing)[0] is not None
    assert retro.read_proposal({"fix": "test"})[1].startswith("title:")
    result = retro.read_result(answer(*(good(f"Fix {n}") for n in range(1, 8))))
    assert result is not None
    assert [p.title for p in result.proposals] == [f"Fix {n}" for n in range(1, 6)]
    assert retro.read_result("No JSON here.") is None


def test_the_retro_format_states_the_limits():
    text = " ".join(retro.FORMAT.split())
    assert "only the first 5 are read" in text
    assert "title to 160 characters, why to 1000, description to 4000" in text
    assert "fix must be test, lint, agents_pointer or raticode_bug, or the proposal is dropped" in (
        text
    )


# The repair gate ----------------------------------------------------------------------------


def test_a_ranking_or_retro_answer_with_no_readable_block_gets_a_repair_gate():
    for kind, readable, holds in (
        ("triage", ranks(rank("a")), "ticket rankings"),
        ("retro", answer(good("Decimal totals test")), "proposals"),
    ):
        run = {"self_work": {"kind": kind}}
        for unreadable, error in (
            ("Finished, no JSON.", "has no fenced ```json block"),
            ('```json\n{"tickets":[{"ticket_id":"a",}]}\n```', "isn't valid JSON"),
            ('```json\n{"other":1}\n```', "has no "),
        ):
            gate = RemHome._proposal_gate(run, unreadable)
            assert gate is not None, (kind, unreadable)
            assert gate.action == "repair"
            assert f"Self-work couldn't read your {holds}:" in gate.feedback
            assert error in gate.feedback and error in gate.reason
        assert RemHome._proposal_gate(run, readable) is None
        # An empty list is a readable answer, and restart recovery is left to review.
        assert RemHome._proposal_gate(run, '```json\n{"tickets":[],"proposals":[]}\n```') is None
        assert RemHome._proposal_gate({**run, "recovery_inspection": True}, "none") is None
    assert RemHome._proposal_gate({"self_work": {"kind": "ticket"}}, "none") is None


# Through self-work ----------------------------------------------------------------------------


def test_a_ranking_keeps_the_valid_ticket_and_records_the_dropped_one(board):
    home, fake, _ = board
    set_options(home, max_threads=1, auto_pull_backlog=True)
    useful = add_backlog(home, "Useful work")
    odd = add_backlog(home, "Oddly scored")
    fake.answers[RANK] = ranking((useful, 4, 4, False, "Fits"), (odd, 6, 3, False, "Huge"))
    at(home, 15)
    tick(home)
    settle(home, "Useful work")
    wait_for(lambda: quiet_with_no_ranking(home), 8)
    assert ticket(home, "Useful work")["status"] == "done"
    assert ticket(home, "Oddly scored")["status"] == "backlog"
    assert not ticket(home, "Oddly scored").get("triage")
    (run,) = runs_of(home, "triage")
    assert [d["ticket_id"] for d in run["dropped_rankings"]] == [odd["id"]]
    assert run["dropped_rankings"][0]["reason"].startswith("impact:")
    notes = [n["text"] for n in home.self_work.view()["report"]["notes"]]
    assert any(n.startswith("Didn't use the ranking of “Oddly scored”: impact:") for n in notes)
    work = home.self_work.view()["report"]["work_done"]
    assert any(
        w["title"] == RANK and "Dropped 1 ranking that couldn't be used." in w["summary"]
        for w in work
    )
    # It was sent once: a readable block needs no repair.
    assert len(ranker_prompts(fake)) == 1


def test_a_retro_keeps_the_usable_proposal_and_records_the_one_with_a_bad_fix(board):
    home, fake, _ = board
    seed_failures(home.store, BASE - timedelta(days=1))
    fake.answers[retro.TITLE] = answer(
        good("Decimal totals regression test"), good("Write a docs page", fix="docs")
    )
    at(home, 15)
    tick(home)
    wait_for(
        lambda: (
            not tick(home)
            and ticket_or_none(home, "Decimal totals regression test") is not None
            and not any(p.get("kind") == "retro" for p in retro_state(home).get("passes") or [])
        ),
        8,
    )
    assert ticket_or_none(home, "Write a docs page") is None
    (run,) = runs_of(home, "retro")
    dropped = [r for r in run.get("rejected_proposals") or [] if r["title"] == "Write a docs page"]
    assert len(dropped) == 1 and dropped[0]["reason"].startswith("fix:")
    notes = [n["text"] for n in home.self_work.view()["report"]["notes"]]
    assert any(n.startswith("Didn't add “Write a docs page”: fix:") for n in notes)
    assert len(retro_calls(fake)) == 1


def test_an_unreadable_retro_is_sent_back_before_review(board):
    home, fake, _ = board
    seed_failures(home.store, BASE - timedelta(days=1))
    answers = iter(["Saved the report, but no block.", answer(good("Decimal totals test"))])

    class Answers(dict[str, str]):
        def get(self, key: Any, default: Any = None) -> Any:
            return next(answers) if key == retro.TITLE else default

    fake.answers = Answers()
    at(home, 15)
    tick(home)
    wait_for(lambda: not tick(home) and ticket_or_none(home, "Decimal totals test") is not None, 8)
    (run,) = runs_of(home, "retro")
    assert run["repair_count"] == 1
    calls = retro_calls(fake)
    assert len(calls) == 2
    assert "has no fenced ```json block" in calls[1]["messages"][-1]["body"]
