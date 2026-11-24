"""Plan with me: Rem grills the user through decision requests, then files thin slices.

Only when the user asks in Rem's chat ("plan this with me", "grill me on ...") does Rem
return a plan entry (``PlanRequest``: title, goal, project_dir, questions). Raticode, not
the model, then does the rest:

- Each question goes through ``rem_decisions.file_request`` as an ordinary decision
  request, with its recommendation and checked evidence, tagged with the plan's ID
  (``P-n``). The usual limit of waiting cards per project shows one small batch at a time
  and queues the rest. Plan questions always wait for the user, even in auto mode.
- The desktop and phone Decisions lists group them under the plan's title with
  "N of M answered" (``rem_decisions.view`` sends ``plans``).
- When the last question is settled the plan becomes ``ready`` (``rem_decisions``) and
  Rem's home hands it back to Rem once, with every answer, as a queued chat message
  (``handback_entry``), the way an ask_rem reminder hands a queued request back.
- Rem answers that message with tickets: thin end-to-end slices, each with ``done_when``
  and ``waits_for``, citing the decision IDs it rests on (``check_slices``).

Rem never starts a plan on its own: only the user's own message, in the desktop chat or
from their phone, that asks for one (``asked_for_plan``) can file it.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gofer.ui import rem_decisions as decision_book
from gofer.ui.rem_decisions import DecisionRequest

# Questions one plan may ask in all; the per-project limit shows only a few at once.
MAX_QUESTIONS = 12
# The hand-back message's ID, so a restart or a second settle never sends it twice.
HANDBACK_PREFIX = "plan-"
# The chat message source of a plan's hand-back.
SOURCE = "plan"

# What the chat model is told on every turn. Short on purpose: it rides in the cached
# system prompt of every reply.
RULES = (
    "Plan with me: only when the user's own message asks you to plan something with them "
    "or to grill them ('plan this with me', 'grill me on ...'), return plan:{title,goal,"
    "project_dir,questions:[decision requests, the same shape as decision_requests, 3 to "
    f"{MAX_QUESTIONS}]}} instead of tasks. Work out every recommendation first from the "
    "project's files, with evidence. Each question is one decision the work rests on, "
    "ordered from the ones that shape the rest to the details. Raticode files them as "
    "decision requests the user answers a few at a time, and hands the plan back to you "
    "with every answer once the last is settled. Never return plan unless the user asked "
    "for it in this message."
)
# Added to the turn that hands a settled plan back to Rem.
HANDBACK_RULES = (
    "current_request.plan is a plan the user started with you whose questions are all "
    "settled. The user asked for this work when they started the plan, so file it now as "
    "tickets: thin end-to-end slices, each one a small change a user could see working "
    "on its own (not layers such as 'backend' then 'UI'), in the order they should be "
    "built. Every ticket needs project_dir (the plan's), done_when (how to tell it is "
    "finished) and waits_for: [] for the first slice, and for a later one the earlier "
    "slices it needs, written #1, #2 for the first and second ticket in this list, or "
    "the key of a ticket already on the board. Start each description with 'Rests on' "
    "and the decision IDs it depends on. Leave turned-down and withdrawn questions out. "
    "Don't start tasks; say in reply which tickets you filed."
)

# The user's own words asking Rem to plan with them or grill them.
_ASKED = re.compile(
    r"\bplan\b[^.?!\n]{0,120}\bwith me\b"
    r"|\bgrill\b[^.?!\n]{0,40}\bme\b"
    r"|\bgrill me\b"
    r"|\b(?:help me|let'?s|let us)\s+plan\b"
    r"|\binterview me\b",
    re.IGNORECASE,
)
_DECISION_ID = re.compile(r"\bD-\d+\b")
_SLICE_REF = re.compile(r"^#(\d{1,2})$")


class PlanRequest(BaseModel):
    """The plan Rem returns when the user asks it to plan something with them."""

    model_config = ConfigDict(extra="ignore")
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(default="", max_length=1000)
    project_dir: str | None = Field(default=None, max_length=4096)
    questions: list[DecisionRequest] = Field(min_length=1, max_length=MAX_QUESTIONS)


def asked_for_plan(message: str) -> bool:
    """Whether the user's message asks Rem to plan something with them or to grill them."""
    return bool(_ASKED.search(str(message or "")))


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def file_plan(
    db: sqlite3.Connection,
    store: Any,
    plan: PlanRequest,
    *,
    project: str | None,
    source: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """File the plan's questions as decision requests tagged with a new plan ID.

    Returns the stored plan and the sentence added to Rem's reply, or ``(None, why)``
    when none of its questions could be filed. A question whose evidence can't be checked
    is left out and named; one whose topic is already decided counts as answered.
    """
    book = decision_book.read(db, store)
    identity = f"P-{book['next_plan']}"
    filed: list[tuple[str, dict[str, Any]]] = []
    skipped: list[str] = []
    for question in plan.questions:
        try:
            checked, _ = decision_book.check_evidence(question, project)
        except ValueError as exc:
            skipped.append(f"{_clean(question.question, 120)} ({exc})")
            continue
        outcome, item = decision_book.file_request(
            db,
            store,
            checked,
            project=project,
            decides_alone=False,
            source={**(source or {}), "via": "plan"},
            plan=identity,
        )
        filed.append((outcome, item))
    left_out = (
        (" Left out, because Raticode couldn't check them: " + "; ".join(skipped))
        if (skipped)
        else ""
    )
    if not filed:
        return None, "I didn't start the plan: none of its questions could be filed." + left_out
    book = decision_book.read(db, store)
    stamp = decision_book.now()
    questions = list(dict.fromkeys(item["id"] for _, item in filed))
    entry: dict[str, Any] = {
        "id": identity,
        "title": _clean(plan.title, 200),
        "goal": _clean(plan.goal, 1000),
        "project": decision_book.project_key(project),
        "questions": questions,
        "status": "asking",
        "created_at": stamp,
        "updated_at": stamp,
        "source": {k: v for k, v in (source or {}).items() if v},
    }
    book["next_plan"] = int(book["next_plan"]) + 1
    book["plans"] = [*book["plans"], entry]
    book = decision_book._save(db, store, book)
    stored = next(p for p in book["plans"] if p["id"] == identity)
    progress = decision_book.plan_progress(book, stored)
    parts = [f"Plan {identity} “{stored['title']}”: {progress['total']} questions."]
    if progress["waiting"]:
        parts.append(f"{progress['waiting']} wait for you in Decisions now")
    if progress["queued"]:
        parts.append(f"{progress['queued']} more come up as you answer")
    answered = [item["id"] for outcome, item in filed if outcome == "answered"]
    if answered:
        parts.append("already decided: " + ", ".join(answered))
    sentence = parts[0] + (" " + "; ".join(parts[1:]) + "." if parts[1:] else "")
    if stored["status"] == "ready":
        sentence += " Every one is already decided, so I'll file the work now."
    return stored, sentence + left_out


def _answer_line(item: dict[str, Any] | None, identity: str) -> str:
    if item is None:
        return f"- {identity}: withdrawn (the user forgot this question). Leave it out."
    question = _clean(item.get("question"), 300)
    if item["status"] == "rejected":
        return f"- {identity}: {question} Turned down by the user. Leave it out."
    if item["status"] == "superseded":
        return f"- {identity}: {question} Replaced by {item.get('superseded_by') or 'a newer one'}."
    who = "the user" if item.get("decided_by") == "user" else "Rem"
    return f"- {identity}: {question} Answer ({who}): {_clean(item.get('answer'), 600)}"


def handback_body(book: dict[str, Any], plan: dict[str, Any]) -> str:
    """The message that hands a settled plan back to Rem, with every answer."""
    items = decision_book.plan_items(book, plan)
    lines = [
        f"Plan with me: every question in plan {plan['id']} “{plan.get('title') or ''}” "
        "is settled.",
    ]
    if plan.get("goal"):
        lines.append(f"Goal: {plan['goal']}")
    lines.append(f"Project: {plan.get('project') or 'none'}")
    lines.append("Answers:")
    lines += [
        _answer_line(item, str(identity))
        for item, identity in zip(items, plan.get("questions") or [], strict=False)
    ]
    lines.append(
        "File the work as tickets now: thin end-to-end slices, each with done_when and "
        "waits_for, citing the decision IDs it rests on."
    )
    return "\n".join(lines)


def handback_entry(book: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """The queued chat message for a ready plan; its ID makes sending it twice harmless."""
    return {
        "id": HANDBACK_PREFIX + plan["id"],
        "body": handback_body(book, plan),
        "created_at": decision_book.now(),
        "source": SOURCE,
        "plan": plan["id"],
    }


def ready(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Plans whose questions are all settled and that weren't handed back yet."""
    return [p for p in book.get("plans") or [] if p.get("status") == "ready"]


def mark_handed_back(db: sqlite3.Connection, store: Any, identity: str) -> None:
    """Record that the plan went back to Rem, so it never goes twice."""
    book = decision_book.read(db, store)
    plan = next((p for p in book["plans"] if p["id"] == identity), None)
    if plan is None or plan.get("status") != "ready":
        return
    plan["status"] = "handed_back"
    plan["handed_back_at"] = plan["updated_at"] = decision_book.now()
    decision_book._save(db, store, book)


def find(book: dict[str, Any], identity: Any) -> dict[str, Any] | None:
    return next((p for p in book.get("plans") or [] if p.get("id") == identity), None)


def for_prompt(book: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """The plan as Rem's hand-back turn sees it: its answers and the decision IDs to cite."""
    items = decision_book.plan_items(book, plan)
    return {
        "id": plan["id"],
        "title": plan.get("title") or "",
        "goal": plan.get("goal") or "",
        "project_dir": plan.get("project"),
        "answers": [
            {
                "id": str(identity),
                "question": _clean((item or {}).get("question"), 300),
                "status": (item or {}).get("status", "withdrawn"),
                **(
                    {"answer": _clean(item.get("answer"), 600)}
                    if item is not None and item["status"] in decision_book.IN_FORCE
                    else {}
                ),
            }
            for item, identity in zip(items, plan.get("questions") or [], strict=False)
        ],
    }


def decided_ids(book: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    """The plan's questions the user answered (in force now), which slices may rest on."""
    return [
        item["id"]
        for item in decision_book.plan_items(book, plan)
        if item is not None and item["status"] in decision_book.IN_FORCE
    ]


def check_slices(tickets: list[Any], plan: dict[str, Any], decided: list[str]) -> list[str]:
    """Hold the hand-back's new tickets to the plan's shape; returns why any were dropped.

    Each new ticket must say when it is done (``done_when``); one without is dropped and
    named, since a slice nobody can check isn't a slice. A ticket that waits for a dropped
    one (``#N`` in ``waits_for``), or for a ``#N`` the reply doesn't have, is dropped and
    named too, so no slice loses its order and starts before the work it needs. A kept new
    ticket goes in the plan's project unless it names another, waits for nothing when it
    says nothing (``waits_for`` []), and cites the plan's decisions: a description naming
    none of them gets a 'Rests on' line with all of them. ``tickets`` is changed in place;
    a ``#N`` in ``waits_for`` still names the same ticket afterwards.
    """
    notes: list[str] = []
    dropped: set[int] = set()
    for position, ticket in enumerate(tickets, start=1):
        if getattr(ticket, "action", None) == "create" and not (ticket.done_when or "").strip():
            dropped.add(position)
            notes.append(
                f"I didn't add “{ticket.label()}”: a slice of plan {plan['id']} needs "
                "done_when, so it can be checked."
            )
    # A wait on a dropped slice drops the waiting one too, however long the chain.
    changed = True
    while changed:
        changed = False
        for position, ticket in enumerate(tickets, start=1):
            if position in dropped:
                continue
            refs = [slice_ref(e) for e in getattr(ticket, "waits_for", None) or []]
            missing = [
                r for r in refs if r is not None and (r in dropped or not 0 < r <= len(tickets))
            ]
            if not missing:
                continue
            dropped.add(position)
            changed = True
            names = ", ".join(
                f"“{tickets[r - 1].label()}”" if 0 < r <= len(tickets) else f"#{r}" for r in missing
            )
            notes.append(
                f"I didn't add “{ticket.label()}”: it waits for {names}, which "
                f"{'is' if len(missing) == 1 else 'are'} not in plan {plan['id']}, so it "
                "would start out of order."
            )
    kept: list[Any] = []
    moved: dict[int, int] = {}
    for position, ticket in enumerate(tickets, start=1):
        if position in dropped:
            continue
        kept.append(ticket)
        moved[position] = len(kept)
        if getattr(ticket, "action", None) != "create":
            continue
        if ticket.project_dir is None and plan.get("project"):
            ticket.project_dir = plan["project"]
        if ticket.waits_for is None:
            ticket.waits_for = []
        text = f"{ticket.description or ''}\n{ticket.done_when or ''}"
        if decided and not set(_DECISION_ID.findall(text)) & set(decided):
            cited = f"Rests on {', '.join(decided)} (plan {plan['id']})."
            ticket.description = f"{cited}\n\n{ticket.description}" if ticket.description else cited
    for ticket in kept:
        waits = getattr(ticket, "waits_for", None)
        if not waits:
            continue
        renamed: list[str] = []
        for entry in waits:
            ref = slice_ref(entry)
            renamed.append(entry if ref is None else f"#{moved[ref]}")
        ticket.waits_for = renamed
    tickets[:] = kept
    return notes


def slice_ref(entry: str) -> int | None:
    """The 1-based position ``#N`` names among the tickets in one reply, or None."""
    match = _SLICE_REF.match(entry.strip())
    return int(match.group(1)) if match else None
