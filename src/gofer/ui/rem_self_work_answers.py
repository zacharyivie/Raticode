"""Reading the answers to self-work's own checks one entry at a time.

Self-work asks Rem's tool-free model three things on its own: which objectives to write or
check off (the planner), which tickets advance the open objectives (the objective-fit check)
and whether the user's stop condition is met (the stop check). A long answer used to be
dropped whole for one bad entry, so a 301-character reason cost 59 good judgments.

Each list is now read entry by entry, by the same rules as research and rankings
(rem_answer_entries): an entry whose required fields don't validate is dropped and another
bad field takes its default. Each drop and default is logged with its reason, so a partly
used answer leaves a trace. An over-long reason or evidence is cut after a sentence
(fit_lengths), and a list that runs long keeps its first usable entries. Only an answer that
can't be used at all raises UnusableAnswer, with validation details for the one repair
request the caller sends.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

from pydantic import BaseModel

from gofer.ui import rem_objective_fit as objective_fit
from gofer.ui import rem_objectives as objectives_store
from gofer.ui import rem_self_work_stop as stops
from gofer.ui.rem_answer_entries import read_entry
from gofer.ui.rem_home_models import fit_lengths

M = TypeVar("M", bound=BaseModel)

log = logging.getLogger(__name__)


class UnusableAnswer(ValueError):
    """An answer self-work can't use at all; ``errors`` says what was wrong, for a repair."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("; ".join(str(e.get("msg") or "") for e in errors))
        self.errors = errors


def _object(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise UnusableAnswer(
            [{"loc": [], "msg": "The answer must be one JSON object", "type": "dict_type"}]
        )
    return raw


def entries(
    model: type[M],
    answer: dict[str, Any],
    key: str,
    limit: int,
    *,
    required: tuple[str, ...],
    prose: tuple[str, ...] = (),
) -> list[M]:
    """The usable entries of ``answer[key]``, at most ``limit`` of them, in order.

    ``prose`` fields only explain an entry, so an over-long one is cut after a sentence
    (fit_lengths). Then each entry is read as research and rankings read theirs
    (rem_answer_entries.read_entry): one whose ``required`` fields don't validate is dropped,
    and any other field that doesn't validate takes its default. Both are logged with the
    reason. A missing or null list is empty; anything else that isn't a list makes the whole
    answer unusable.
    """
    value = answer.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise UnusableAnswer([{"loc": [key], "msg": "Input should be a list", "type": "list_type"}])
    kept: list[M] = []
    for number, item in enumerate(value):
        if len(kept) >= limit:
            break
        if isinstance(item, dict):
            item = fit_lengths(model, item, prose=prose)
        entry, reason, repairs = read_entry(item, model, required=required, noun=key)
        if entry is None:
            log.info("Rem self-work left out %s[%d] of its answer: %s", key, number, reason)
            continue
        if repairs:
            log.info("Rem self-work used %s[%d] in part: %s", key, number, "; ".join(repairs))
        kept.append(entry)
    return kept


def plan(raw: Any) -> objectives_store.PlannedObjectives:
    """The planner's answer: the objectives to add and the ones to check off."""
    answer = _object(raw)
    return objectives_store.PlannedObjectives(
        objectives=entries(
            objectives_store.NewObjective,
            answer,
            "objectives",
            objectives_store.MAX_OPEN_BY_REM,
            required=("text",),
        ),
        done=entries(
            objectives_store.ObjectiveCheck,
            answer,
            "done",
            objectives_store.MAX_CHECKS,
            required=("id", "evidence"),
            prose=("evidence",),
        ),
        finish_lines=entries(
            objectives_store.FinishLine,
            answer,
            "finish_lines",
            objectives_store.MAX_OPEN_BY_REM,
            required=("id", "done_when"),
        ),
    )


def plan_problems(plan: objectives_store.PlannedObjectives) -> list[dict[str, Any]]:
    """Objectives in a usable plan that lack a checkable finish line, for one repair request.

    The planner writes objectives on Rem's own initiative, so each needs done_when
    (FINISH_LINE_RULE). Those still without one after the repair are refused when saved.
    """
    problems: list[dict[str, Any]] = []
    for number, entry in enumerate(plan.objectives):
        problem = objectives_store.finish_line_problem(" ".join(entry.done_when.split()))
        if problem:
            problems.append(
                {
                    "loc": ["objectives", number, "done_when"],
                    "msg": f"“{entry.text.strip()[:80]}”: {problem}",
                    "type": "finish_line",
                }
            )
    return problems


def fit(raw: Any) -> objective_fit.FitResult:
    """The objective-fit check's answer: one judgment per ticket and each objective's scope."""
    answer = _object(raw)
    return objective_fit.FitResult(
        tickets=entries(
            objective_fit.TicketFit,
            answer,
            "tickets",
            objective_fit.CHECK_TICKETS,
            # A garbled objective would mark the ticket unrelated; dropped, it's asked again.
            required=("ticket_id", "objective"),
            prose=("reason",),
        ),
        scopes=entries(
            objective_fit.ObjectiveScope,
            answer,
            "scopes",
            objectives_store.MAX_ITEMS,
            # Projects that can't be read would mean none fits, so the scope is dropped.
            required=("objective", "projects"),
        ),
    )


def judgment(raw: Any) -> stops.Judgment:
    """The stop check's answer; unusable unless ``met`` is a boolean."""
    answer = _object(raw)
    met = answer.get("met")
    if not isinstance(met, bool):
        raise UnusableAnswer(
            [{"loc": ["met"], "msg": "Input should be true or false", "type": "bool_type"}]
        )
    evidence = answer.get("evidence")
    found = {"met": met, "evidence": evidence if isinstance(evidence, str) else ""}
    return stops.Judgment.model_validate(fit_lengths(stops.Judgment, found, prose=("evidence",)))
