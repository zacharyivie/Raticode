"""Self-work's own checks keep the usable part of an answer and ask once to repair the rest.

The objective planner, the objective-fit check and the stop check read their answers one entry
at a time (rem_self_work_answers): a bad entry is dropped, an over-long reason or evidence is
cut after a sentence and a long list keeps its first entries. Only an answer with nothing
usable gets one repair request, and a repair that fails too is marked failed in the usage
ledger. Each check is recorded under its own call kind. Fake providers and isolated data only.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gofer.core import turn_context
from gofer.core.usage_ledger import record_rem_call
from gofer.ui import rem_objective_fit as objective_fit
from gofer.ui import rem_objectives as objectives
from gofer.ui import rem_self_work_answers as answers
from gofer.ui import rem_self_work_stop as stops
from gofer.ui.rem_self_work import PLANNER_RULES
from tests.unit.test_rem_kanban import add, at, tick
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_self_work_objectives import PLANNING, change, planned, step, stored, texts
from tests.unit.test_self_work_stop import JUDGE, judged, start_session, stop_view
from tests.unit.test_turn_context import _attempts, ok
from tests.unit.test_turn_context import backend as backend  # noqa: F401

GOAL = "Improve Raticode UI responsiveness"


def long_reason() -> str:
    """301 characters of whole sentences and the start of one more: one past the limit."""
    return ("This ticket makes the side pane respond faster. " * 10)[:301]


# Reading one entry at a time ------------------------------------------------------------


def test_bad_entries_are_dropped_long_prose_is_cut_and_long_lists_keep_their_first():
    reason = "It speeds up the board. " * 20
    result = answers.fit(
        {
            "tickets": [
                {"ticket_id": "a", "objective": "o1", "reason": reason},
                {"ticket_id": "", "objective": "o1"},  # required field fails: dropped
                "not an entry",
                {"ticket_id": "b", "reason": 7},  # a reason that isn't text is left out
                {"ticket_id": "c", "objective": "o" * 65},  # would read as unrelated: dropped
                *({"ticket_id": f"t{n}"} for n in range(70)),
            ],
            "scopes": [{"objective": "o1", "projects": ["/p", 3, "x" * 5000]}, {"projects": []}],
        }
    )
    assert len(result.tickets) == objective_fit.CHECK_TICKETS
    first, second = result.tickets[:2]
    assert first.ticket_id == "a" and len(first.reason) <= objective_fit.REASON_MAX
    assert first.reason.endswith("board.")
    assert (second.ticket_id, second.reason) == ("b", "")
    assert [(s.objective, s.projects) for s in result.scopes] == [("o1", ["/p"])]

    plan = answers.plan(
        {
            "objectives": [
                {"text": "Raise UX quality of the studio", "done_when": "x" * 501},
                {"text": "x" * 201},
            ]
            + [{"text": f"Improve area number {n}"} for n in range(10)],
            "done": [{"id": "o1", "evidence": "All tickets are done. " * 60}, {"id": "o2"}],
        }
    )
    assert len(plan.objectives) == objectives.MAX_OPEN_BY_REM
    # A finish line that can't be used leaves the goal open-ended rather than losing it.
    assert (plan.objectives[0].text, plan.objectives[0].done_when) == (
        "Raise UX quality of the studio",
        "",
    )
    assert all(len(o.text) <= objectives.TEXT_MAX for o in plan.objectives)
    [check] = plan.done
    assert check.id == "o1" and len(check.evidence) <= objectives.EVIDENCE_MAX

    judged_ = answers.judgment({"met": True, "evidence": "REM-1 is done. " * 60})
    assert judged_.met is True and len(judged_.evidence) <= stops.MAX_EVIDENCE
    assert judged_.evidence.endswith("done.")
    assert answers.judgment({"met": False, "evidence": ["x"]}).evidence == ""


def test_every_dropped_or_defaulted_entry_is_logged_with_its_reason(caplog):
    """A partly used answer leaves a trace (self-work report, 21 November afternoon)."""
    caplog.set_level("INFO", logger=answers.__name__)
    answers.fit(
        {
            "tickets": [
                {"ticket_id": "", "objective": "o1"},
                {"ticket_id": "b", "objective": "o1", "reason": 7},
                {"ticket_id": "c", "objective": "o1", "reason": "Fits."},
            ]
        }
    )
    lines = [r.getMessage() for r in caplog.records]
    assert lines == [
        "Rem self-work left out tickets[0] of its answer: "
        "ticket_id: String should have at least 1 character",
        "Rem self-work used tickets[1] in part: "
        "reason: Input should be a valid string; used the default",
    ]


@pytest.mark.parametrize(
    ("read", "raw"),
    [
        (answers.plan, ["not", "an object"]),
        (answers.plan, {"objectives": "Raise UX quality"}),
        (answers.fit, "tickets"),
        (answers.fit, {"tickets": {"ticket_id": "a"}}),
        (answers.judgment, {"met": "yes", "evidence": "REM-1 is done."}),
        (answers.judgment, {"evidence": "REM-1 is done."}),
    ],
)
def test_an_answer_with_nothing_usable_says_what_was_wrong(read: Any, raw: Any):
    with pytest.raises(answers.UnusableAnswer) as caught:
        read(raw)
    assert caught.value.errors and all("msg" in e for e in caught.value.errors)


def test_each_prompt_states_its_limits_and_sends_its_rules_as_system_text():
    assert "at most 300 characters" in objective_fit.FIT_RULES
    assert f"at most {objective_fit.CHECK_TICKETS}" in objective_fit.FIT_RULES
    assert "at most 400 characters" in stops.JUDGE_RULES
    for limit in ("at most 6 objectives", "at most 200 characters", "at most 700 characters"):
        assert limit in PLANNER_RULES
    # The records change from call to call, so they stay out of the fixed rules.
    assert objective_fit.prompt({"items": []}, [], []).startswith("Records:\n")


# The three checks in self-work ----------------------------------------------------------


def test_one_long_reason_keeps_all_sixty_judgments(board):
    home, _, _ = board
    change(home, {"action": "add", "text": GOAL}, actor="user")
    titles = [f"Speed up panel {n}" for n in range(objective_fit.CHECK_TICKETS)]
    for title in titles:
        add(home, title)
    long = long_reason()
    assert len(long) == 301
    shown: list[str] = []
    usual = home.generate

    def generate(prompt: str, config: Any, cancel: Any) -> Any:
        if not prompt.startswith(objective_fit.CHECK_PREFIX):
            return usual(prompt, config, cancel)
        records = json.loads(prompt.split("Records:\n", 1)[1])
        shown.append(prompt)
        goal = records["objectives"][0]["id"]
        return {
            "tickets": [
                {
                    "ticket_id": t["ticket_id"],
                    "objective": goal,
                    "reason": long if n == 0 else "Fits.",
                }
                for n, t in enumerate(records["tickets"])
            ]
        }

    home.generate = generate
    at(home, 15)
    step(home)
    assert len(shown) == 1
    fits = [stored(home, title).get(objective_fit.FIELD) or {} for title in titles]
    assert all(fit.get("objective") for fit in fits), "every judgment is saved"
    reasons = sorted(len(fit["reason"]) for fit in fits)
    assert reasons[-1] <= objective_fit.REASON_MAX and reasons[-1] > 200
    assert _attempts(home.data_dir, "self_work_fit") == [("first", 0)]


def planner_answers(home: Any, *replies: Any) -> list[str]:
    """Rem's model: answers objective planning with ``replies`` in turn, other calls as usual."""
    prompts: list[str] = []
    usual = home.generate

    def generate(prompt: str, config: Any, cancel: Any) -> Any:
        if prompt.startswith(PLANNING):
            prompts.append(prompt)
            return replies[min(len(prompts), len(replies)) - 1]
        return usual(prompt, config, cancel)

    home.generate = generate
    return prompts


def test_an_unusable_plan_is_repaired_once_then_marked_failed(board):
    home, _, _ = board
    prompts = planner_answers(home, ["Raise UX quality"], {"objectives": "Raise UX quality"})
    at(home, 15)
    tick(home)
    planned(home)
    assert len(prompts) == 2, "asked once more, never a third time"
    assert "couldn't be used" in prompts[1] and "Validation details" in prompts[1]
    assert "The answer must be one JSON object" in prompts[1]
    assert _attempts(home.data_dir, "self_work_plan") == [("first", 0), ("repair", 1)]
    assert texts(home) == []


def test_a_repaired_plan_is_used(board):
    home, _, _ = board
    goal = {"text": "Raise UX quality of the workflow studio"}
    prompts = planner_answers(home, "Here are my objectives", {"objectives": [goal]})
    at(home, 15)
    tick(home)
    planned(home)
    assert len(prompts) == 2
    assert texts(home) == [goal["text"]]
    assert _attempts(home.data_dir, "self_work_plan") == [("first", 0), ("repair", 0)]


def test_a_stop_check_whose_met_isnt_a_boolean_is_repaired(board):
    home, _, _ = board
    from tests.unit.test_rem_self_work_windows import Clock
    from tests.unit.test_self_work_stop import Model

    clock = Clock(home)
    model = Model(home)
    prompts: list[str] = []
    replies: list[dict[str, Any]] = [
        {"met": "no", "evidence": "REM-1 is still open."},
        {"met": False, "evidence": "REM-1 is still open."},
    ]
    chat = model.generate

    def generate(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        if prompt.startswith(JUDGE):
            prompts.append(prompt)
            return replies[len(prompts) - 1]
        return chat(prompt, config, cancel)

    home.generate = generate
    start_session(home, clock, model, "REM-1 is done")
    tick(home)
    judged(home)
    assert len(prompts) == 2 and "Input should be true or false" in prompts[1]
    shown = stop_view(home)
    assert shown["judgment"]["met"] is False and not shown["judge_failed"]
    assert _attempts(home.data_dir, "self_work_stop") == [("first", 0), ("repair", 0)]


# Recorded as three kinds ---------------------------------------------------------------


def test_the_three_checks_are_separate_kinds(backend, monkeypatch) -> None:  # noqa: F811
    assert "self_work" not in turn_context.REM_CALLS
    config = {"provider": "claude_code", "model": "sonnet"}
    for call in ("self_work_plan", "self_work_fit", "self_work_fit", "self_work_stop"):
        record_rem_call(call, "x" * 40, "rules", None, config=config, data_dir=backend.data)
    found = ok("rem", "context", "--format", "json")
    counts = {k["call"]: k["calls"] for k in found["by_kind"]}
    assert counts == {"self_work_plan": 1, "self_work_fit": 2, "self_work_stop": 1}
    labels = {k["call"]: k["label"] for k in found["by_kind"]}
    assert len(set(labels.values())) == 3
    only = ok("rem", "context", "--call", "self_work_stop", "--format", "json")
    assert [c["call"] for c in only["calls"]] == ["self_work_stop"]
    # The single kind they replaced isn't recorded any more.
    assert record_rem_call("self_work", "x", "", None, config=config, data_dir=backend.data) is None
    # Records kept from before the split can still be listed on their own (self-work report,
    # 21 November afternoon: --call self_work used to be refused).
    monkeypatch.setitem(turn_context.REM_CALLS, "self_work", "old")
    record_rem_call("self_work", "x" * 40, "rules", None, config=config, data_dir=backend.data)
    monkeypatch.delitem(turn_context.REM_CALLS, "self_work")
    old = ok("rem", "context", "--call", "self_work", "--format", "json")
    assert [c["call"] for c in old["calls"]] == ["self_work"]
    assert old["calls"][0]["call_label"] == "Self-work checks (before they were split)"
