"""Rem's self-work objectives: the goals that guide a whole self-work session.

Objectives are a short checklist the user and Rem share. They are high-order goals that
self-work works toward across many tasks and sessions, such as "Improve reliability of the
Second Brain tools" or "Raise UX quality of the workflow studio", never one task's worth of
work: that belongs on the board as a ticket.

- Stored as one Rem setting (``self_work_objectives``) with a revision, so two devices can't
  overwrite each other and self-work can read the list fresh before every task it starts.
- The user can add, edit, remove, check and uncheck anything.
- Rem changes them from chat when the user tells it what self-work should focus on
  (``source="chat"``), and on its own (``source="self_work"``) when the list is empty at the
  start of a session or self-work runs out of work, or ``source="own"`` in a chat turn the
  user didn't start. On its own it may only add objectives, check them off and give a finish
  line to one of its own that has none.
- Open-ended objectives (no ``done_when``) are the user's: an open-ended goal Rem picked
  itself got all of self-work's attention for as long as it ran. Every objective Rem writes on
  its own needs a concrete, checkable finish line and is refused without one. One that comes
  from the user's own words in chat may stay open-ended, and so may anything the user writes.
  An open-ended objective Rem wrote on its own before this rule is given a finish line by the
  next session's planner (``needs_finish_line``).
- Rem checks an objective off only when it has written doneness criteria (``done_when``) and
  Rem gives the evidence that they are fully met. Open-ended objectives have no criteria and
  stay open; they don't have to be finished before self-work ends.
- Self-work shares its threads among the open objectives (``balanced``): each start goes to
  the objective with the least work so far this session, so no single goal takes them all.
- Clients list them newest first with open ones above done ones (``ordered``), so there is no
  manual order. When a self-work session ends, the objectives done by then become stale
  (``mark_stale``): history that no longer counts toward the heading's done/total and sits in
  its own folded group. Open ones carry over to the next session and never go stale.
- Clients load the first ``PAGE`` objectives in that order and ask for more as the user
  scrolls (``view(limit=...)``); the list keeps up to ``MAX_STORED``, dropping the oldest
  stale ones first.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

SETTING = "self_work_objectives"
TEXT_MAX = 200
CRITERIA_MAX = 500
EVIDENCE_MAX = 700
# Open objectives the list holds, and the most a worker or Rem's model is shown.
MAX_ITEMS = 30
# Everything kept, stale history included; the oldest stale ones go first past it.
MAX_STORED = 500
# How many objectives clients load at first, and how many more each time the user scrolls.
PAGE = 30
# Open objectives Rem keeps of its own: a handful of goals, not a to-do list.
MAX_OPEN_BY_REM = 6
# Objectives one self-work planning answer may check off.
MAX_CHECKS = 10
# The fewest words a goal Rem writes can have; "Fix bug" is a task, not an objective.
MIN_WORDS = 3

Actor = Literal["user", "rem"]
# Where a change by Rem came from: the user's chat message, self-work deciding on its own, or
# a chat turn the user didn't start (a task finishing, a schedule), which is Rem on its own too.
Source = Literal["chat", "self_work", "own"]

# Rem's own objectives must read as goals for a session, not one ticket or one file.
TICKET_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
FILE_NAME = re.compile(
    r"(?:^|[\s(`'\"])[\w./-]*\w\.(?:py|jsx?|tsx?|cjs|mjs|kt|kts|java|rs|go|css|html|md|json|"
    r"toml|ya?ml|sh|rattish)\b",
    re.IGNORECASE,
)

# How Rem is told to write objectives, wherever it writes them.
HIGH_ORDER_RULE = (
    "An objective is a high-order goal that self-work works toward throughout the whole "
    "session and usually across sessions, such as 'Improve reliability of the Second Brain "
    "tools', 'Raise UX quality of the workflow studio' or 'Optimize performance of file "
    "handling'. It names an area and a direction, in a short sentence of at least three words. "
    "Never make an objective of something one task could finish (fix a bug, add a button, "
    "rename a setting, one ticket or one file): that is a ticket. Never name a ticket key or "
    "a file in one. Keep few: at most six open objectives of your own."
)
DONE_RULE = (
    "Check an objective off only when it has clear doneness criteria (done_when) and you can "
    "show they are fully met, with the evidence in one or two sentences. Open-ended objectives "
    "have no done_when and stay open; they don't need finishing before self-work ends."
)
# What Rem is told wherever it may write objectives on its own initiative.
FINISH_LINE_RULE = (
    "Open-ended objectives are reserved for the user. Every objective you write on your own "
    "initiative needs done_when: a concrete, checkable finish line that someone could verify "
    "from the board, the code or a test run, such as 'Every Second Brain tool error found in "
    "this session's research is fixed with a regression test' or 'The board's ten slowest "
    "screens each load in under a second'. Never write a vague one such as 'it is better' or "
    "'no more work is needed'. An objective without one is refused."
)


class ObjectiveRefused(ValueError):
    """A change that can't be made; ``code`` matches the board's error codes."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class NewObjective(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(min_length=1, max_length=TEXT_MAX)
    done_when: str = Field(default="", max_length=CRITERIA_MAX)


class FinishLine(BaseModel):
    """A finish line Rem gives one of its own open-ended objectives."""

    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=64)
    done_when: str = Field(min_length=1, max_length=CRITERIA_MAX)


class ObjectiveCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=64)
    evidence: str = Field(min_length=1, max_length=EVIDENCE_MAX)


class ObjectivesRequest(BaseModel):
    """A change to the objectives Rem returns in a chat decision, because the user asked.

    ``set`` replaces the open objectives with ``items`` (done ones stay as history);
    ``add`` appends ``items``; ``check`` marks ``checks`` done with evidence; ``edit``,
    ``remove`` and ``uncheck`` act on ``id``. ``move`` is still read, and refused: the list
    has a fixed order.
    """

    model_config = ConfigDict(extra="forbid")
    action: Literal["set", "add", "check", "edit", "remove", "uncheck", "move"]
    items: list[NewObjective] = Field(default_factory=list, max_length=MAX_OPEN_BY_REM)
    checks: list[ObjectiveCheck] = Field(default_factory=list, max_length=10)
    id: str = Field(default="", max_length=64)
    text: str = Field(default="", max_length=TEXT_MAX)
    done_when: str | None = Field(default=None, max_length=CRITERIA_MAX)
    index: int | None = Field(default=None, ge=0, le=MAX_ITEMS)


class PlannedObjectives(BaseModel):
    """What self-work's own objective planning returns."""

    model_config = ConfigDict(extra="ignore")
    objectives: list[NewObjective] = Field(default_factory=list, max_length=MAX_OPEN_BY_REM)
    done: list[ObjectiveCheck] = Field(default_factory=list, max_length=MAX_CHECKS)
    # Finish lines for Rem's own open-ended objectives (needs_finish_line).
    finish_lines: list[FinishLine] = Field(default_factory=list, max_length=MAX_OPEN_BY_REM)


def now() -> str:
    return datetime.now(UTC).isoformat()


def read(db: sqlite3.Connection, store: Any) -> dict[str, Any]:
    """The list as it is in this transaction: ``{"revision", "items", "updated_at"}``."""
    stored = store.setting(db, SETTING)
    items = [item for item in stored.get("items") or [] if isinstance(item, dict)]
    return {
        "revision": int(stored.get("revision") or 0),
        "items": items,
        "updated_at": stored.get("updated_at"),
    }


def open_items(objectives: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in objectives["items"] if not item.get("done")]


def on_its_own(actor: Actor, source: str) -> bool:
    """Whether a change is Rem acting on its own initiative, not on the user's words."""
    return actor == "rem" and source != "chat"


def needs_finish_line(item: dict[str, Any]) -> bool:
    """An open objective Rem wrote on its own with no finish line, which it may not keep.

    Objectives from the user's words in chat, and any the user made open-ended themselves,
    are the user's to keep open-ended.
    """
    return bool(
        not item.get("done")
        and item.get("created_by") == "rem"
        and item.get("source") != "chat"
        and not item.get("done_when")
        and item.get("open_ended_by") != "user"
    )


def is_stale(item: dict[str, Any]) -> bool:
    """Done in a self-work session that has ended: history, not this session's progress."""
    return bool(item.get("done") and item.get("stale"))


def current_items(objectives: dict[str, Any]) -> list[dict[str, Any]]:
    """Open objectives and those done since the last session ended."""
    return [item for item in objectives["items"] if not is_stale(item)]


def ordered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open first, then done in this session, then stale; newest first within each group.

    Newest is the latest ``created_at``. Objectives added together share one time and keep
    the order they were given in, so a batch Rem writes reads as it wrote it.
    """

    def group(item: dict[str, Any]) -> int:
        return 2 if is_stale(item) else 1 if item.get("done") else 0

    # Both sorts are stable, so equal times keep the stored order.
    listed = sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return sorted(listed, key=group)


def view(objectives: dict[str, Any], limit: int = PAGE, offset: int = 0) -> dict[str, Any]:
    """What clients show: up to ``limit`` objectives in display order from ``offset``.

    ``open`` and ``done`` count the current session's objectives, which the heading shows as
    done/total; ``stale`` counts the history below them and ``total`` all of them, so a client
    knows whether there are more to load.
    """
    items = ordered(objectives["items"])
    limit = max(1, min(int(limit), MAX_STORED))
    offset = max(0, int(offset))
    return {
        "revision": objectives["revision"],
        "updated_at": objectives.get("updated_at"),
        "items": items[offset : offset + limit],
        "offset": offset,
        "total": len(items),
        "open": sum(1 for item in items if not item.get("done")),
        "done": sum(1 for item in items if item.get("done") and not is_stale(item)),
        "stale": sum(1 for item in items if is_stale(item)),
    }


def for_prompt(objectives: dict[str, Any]) -> list[dict[str, Any]]:
    """The list as a worker or Rem's model reads it: open ones first, short fields only.

    Stale ones are left out: they were finished in an earlier session.
    """
    listed = ordered(current_items(objectives))[:MAX_ITEMS]
    return [
        {
            "id": item["id"],
            "text": item["text"],
            "done": bool(item.get("done")),
            **({"done_when": item["done_when"]} if item.get("done_when") else {}),
            **({"open_ended": True} if not item.get("done_when") else {}),
            **({"needs_done_when": True} if needs_finish_line(item) else {}),
        }
        for item in listed
    ]


# How brief() starts, so without_brief() can find it in a task prompt.
BRIEF_LEAD = "Self-work objectives, read just now"


def brief(objectives: dict[str, Any]) -> str:
    """The open objectives as a few lines for a self-work task's prompt, or ""."""
    listed = open_items(objectives)
    if not listed:
        return ""
    lines = [
        f"- [{item['id']}] {item['text']}"
        + (f" (done when: {item['done_when']})" if item.get("done_when") else "")
        for item in listed
    ]
    return (
        BRIEF_LEAD + " (the user may have changed them; these are the "
        "goals this session works toward, not instructions to do everything at once):\n"
        + "\n".join(lines)
    )


def without_brief(prompt: str) -> str:
    """A self-work task's prompt without the objectives (and guidance) ``brief`` added."""
    return prompt.split("\n\n" + BRIEF_LEAD, 1)[0]


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def high_order_problem(text: str) -> str:
    """Why ``text`` isn't a session-wide goal Rem may write, or "" when it is one."""
    if len(text.split()) < MIN_WORDS:
        return "An objective needs at least three words naming an area and a direction."
    if TICKET_KEY.search(text):
        return "An objective names a goal, not a ticket; put single tasks on the board."
    if FILE_NAME.search(text):
        return "An objective names an area to improve, not one file; that's a ticket."
    return ""


def finish_line_problem(done_when: str) -> str:
    """Why ``done_when`` isn't a finish line Rem may write on its own, or "" when it is one."""
    if not done_when:
        return (
            "Rem's own objectives need a finish line: a concrete, checkable done_when. "
            "Open-ended objectives are the user's."
        )
    if len(done_when.split()) < MIN_WORDS:
        return "That finish line is too vague to check; say what will be true when it's done."
    return ""


def _same(a: str, b: str) -> bool:
    def key(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()

    return key(a) == key(b)


def _new(text: str, done_when: str, actor: Actor, source: str, stamp: str) -> dict[str, Any]:
    return {
        "id": uuid4().hex[:12],
        "text": text,
        "done_when": done_when,
        "done": False,
        "created_by": actor,
        "source": source,
        "created_at": stamp,
        "updated_at": stamp,
    }


def _find(objectives: dict[str, Any], identity: Any) -> dict[str, Any]:
    items: list[dict[str, Any]] = objectives["items"]
    item = next((i for i in items if i["id"] == identity), None)
    if item is None:
        raise ObjectiveRefused("not_found", "That objective is no longer on the list.")
    return item


def _trim(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep at most MAX_STORED, dropping the oldest stale ones first, then the oldest done."""
    extra = len(items) - MAX_STORED
    if extra <= 0:
        return items
    done = sorted(
        (i for i in items if i.get("done")),
        key=lambda i: (not is_stale(i), str(i.get("done_at"))),
    )
    dropped = {i["id"] for i in done[:extra]}
    return [i for i in items if i["id"] not in dropped]


def _add(
    objectives: dict[str, Any],
    entries: list[NewObjective],
    actor: Actor,
    source: str,
    stamp: str = "",
) -> list[dict[str, Any]]:
    """Append new objectives; Rem's must be high-order and few. Returns those added.

    They all get the time ``stamp`` (now by default), so they list in the order given.
    """
    stamp = stamp or now()
    added: list[dict[str, Any]] = []
    for entry in entries:
        text = _clean(entry.text, TEXT_MAX)
        done_when = _clean(entry.done_when, CRITERIA_MAX)
        if not text:
            raise ObjectiveRefused("invalid", "Write the objective first.")
        if any(_same(text, i["text"]) for i in open_items(objectives)):
            continue
        if actor == "rem":
            if problem := high_order_problem(text):
                raise ObjectiveRefused("invalid", problem)
            if on_its_own(actor, source) and (problem := finish_line_problem(done_when)):
                raise ObjectiveRefused("invalid", problem)
            mine = [i for i in open_items(objectives) if i.get("created_by") == "rem"]
            if len(mine) >= MAX_OPEN_BY_REM:
                raise ObjectiveRefused(
                    "invalid",
                    f"Rem keeps at most {MAX_OPEN_BY_REM} open objectives of its own; "
                    "finish or drop one first.",
                )
        if len(open_items(objectives)) >= MAX_ITEMS:
            raise ObjectiveRefused(
                "invalid", f"The list holds at most {MAX_ITEMS} open objectives."
            )
        item = _new(text, done_when, actor, source, stamp)
        objectives["items"].append(item)
        objectives["items"] = _trim(objectives["items"])
        added.append(item)
    return added


def _check(
    objectives: dict[str, Any],
    identity: str,
    actor: Actor,
    source: str,
    evidence: str = "",
    done_when: str = "",
) -> dict[str, Any]:
    item = _find(objectives, identity)
    if item.get("done"):
        return item
    evidence = _clean(evidence, EVIDENCE_MAX)
    if actor == "rem":
        criteria = item.get("done_when") or ""
        # The user saying so in chat is itself the finish line, and Rem records it.
        if not criteria and source == "chat" and done_when:
            criteria = _clean(done_when, CRITERIA_MAX)
        if not criteria:
            raise ObjectiveRefused(
                "invalid",
                "This objective is open-ended: it has no doneness criteria, so Rem can't "
                "check it off.",
            )
        if not evidence:
            raise ObjectiveRefused(
                "invalid", "Say how the objective's doneness criteria were fully met."
            )
        item["done_when"] = criteria
    stamp = now()
    item.update(
        done=True,
        done_at=stamp,
        done_by=actor,
        done_evidence=evidence,
        updated_at=stamp,
    )
    return item


def change(
    db: sqlite3.Connection,
    store: Any,
    payload: dict[str, Any],
    *,
    actor: Actor,
    source: str = "",
    stamp: str = "",
) -> dict[str, Any]:
    """Apply one change in the caller's transaction and return the new list.

    ``stamp`` is the time new objectives get; a caller adding a batch one by one passes the
    same one, so they list in the order given.

    ``payload`` takes ``action`` (add, edit, remove, check, uncheck, set) and
    ``expected_revision`` when a client shows the list. The user may change anything; Rem's
    changes follow the rules in this module's docstring.
    """
    objectives = read(db, store)
    expected = payload.get("expected_revision")
    if expected is not None and expected != objectives["revision"]:
        raise ObjectiveRefused("conflict", "The objectives changed. Check them and try again.")
    action = payload.get("action")
    if on_its_own(actor, source) and action == "edit":
        _give_finish_line(objectives, payload)
        _save(db, store, objectives)
        return objectives
    if on_its_own(actor, source) and action not in {"add", "check"}:
        raise ObjectiveRefused(
            "unauthorized",
            "On its own, Rem can only add objectives, check them off and give its own a "
            "finish line.",
        )
    if action == "add":
        entries = payload.get("items") or [
            {"text": payload.get("text"), "done_when": payload.get("done_when") or ""}
        ]
        try:
            parsed = [NewObjective.model_validate(e) for e in entries]
        except ValueError as exc:
            raise ObjectiveRefused("invalid", "Write the objective in a short sentence.") from exc
        if not _add(objectives, parsed, actor, source or actor, stamp):
            raise ObjectiveRefused("conflict", "That objective is already on the list.")
    elif action == "set":
        if actor != "rem":
            raise ObjectiveRefused("invalid", "Choose add, edit, remove, check or uncheck.")
        try:
            parsed = [NewObjective.model_validate(e) for e in payload.get("items") or []]
        except ValueError as exc:
            raise ObjectiveRefused("invalid", "Write each objective in a short sentence.") from exc
        for entry in parsed:
            if problem := high_order_problem(_clean(entry.text, TEXT_MAX)):
                raise ObjectiveRefused("invalid", problem)
        kept = [i for i in objectives["items"] if i.get("done")]
        # An open objective named again keeps its identity, so task links survive.
        current = {i["text"].casefold(): i for i in open_items(objectives)}
        objectives["items"] = []
        stamp = stamp or now()
        for entry in parsed:
            text = _clean(entry.text, TEXT_MAX)
            same = next((i for k, i in current.items() if _same(k, text)), None)
            if same is not None and not any(i["id"] == same["id"] for i in objectives["items"]):
                same.update(text=text, updated_at=now())
                if entry.done_when:
                    same["done_when"] = _clean(entry.done_when, CRITERIA_MAX)
                objectives["items"].append(same)
            else:
                _add(objectives, [entry], actor, source or actor, stamp)
        # The finished ones stay as history; clients list the new goals above them.
        objectives["items"] = _trim([*objectives["items"], *kept])
    elif action == "edit":
        item = _find(objectives, payload.get("id"))
        if "text" in payload:
            text = _clean(payload.get("text"), TEXT_MAX)
            if not text:
                raise ObjectiveRefused("invalid", "Write the objective first, or remove it.")
            if actor == "rem" and (problem := high_order_problem(text)):
                raise ObjectiveRefused("invalid", problem)
            item["text"] = text
        if payload.get("done_when") is not None:
            if actor == "rem" and item.get("done"):
                raise ObjectiveRefused(
                    "invalid", "Uncheck the objective before changing its criteria."
                )
            item["done_when"] = _clean(payload.get("done_when"), CRITERIA_MAX)
            # The user's own words made it open-ended, so it stays that way for them.
            if item["done_when"]:
                item.pop("open_ended_by", None)
            else:
                item["open_ended_by"] = "user"
        item["updated_at"] = now()
    elif action == "remove":
        item = _find(objectives, payload.get("id"))
        objectives["items"] = [i for i in objectives["items"] if i["id"] != item["id"]]
    elif action == "move":
        # Older clients and chat answers may still ask; the order is fixed now.
        raise ObjectiveRefused(
            "invalid",
            "Objectives are listed newest first, with open ones above done ones, so they "
            "can't be moved.",
        )
    elif action == "check":
        checks = payload.get("checks") or [
            {"id": payload.get("id"), "evidence": payload.get("evidence") or ""}
        ]
        for check in checks:
            _check(
                objectives,
                str(check.get("id") or ""),
                actor,
                source,
                str(check.get("evidence") or ""),
                str(payload.get("done_when") or check.get("done_when") or ""),
            )
    elif action == "uncheck":
        item = _find(objectives, payload.get("id"))
        # Reopened, a stale one is current again and carries over like any open objective.
        for key in ("done_at", "done_by", "done_evidence", "stale", "stale_at"):
            item.pop(key, None)
        item.update(done=False, updated_at=now())
    else:
        raise ObjectiveRefused("invalid", "Choose add, edit, remove, check or uncheck.")
    _save(db, store, objectives)
    return objectives


def _give_finish_line(objectives: dict[str, Any], payload: dict[str, Any]) -> None:
    """Rem, on its own, gives a finish line to an open-ended objective it wrote itself."""
    item = _find(objectives, payload.get("id"))
    if not needs_finish_line(item) or "text" in payload:
        raise ObjectiveRefused(
            "unauthorized",
            "On its own, Rem can only give a finish line to an open-ended objective it wrote "
            "itself.",
        )
    done_when = _clean(payload.get("done_when"), CRITERIA_MAX)
    if problem := finish_line_problem(done_when):
        raise ObjectiveRefused("invalid", problem)
    item.update(done_when=done_when, updated_at=now())


def _save(db: sqlite3.Connection, store: Any, objectives: dict[str, Any]) -> None:
    objectives["revision"] += 1
    objectives["updated_at"] = now()
    store.set_setting(
        db,
        SETTING,
        {
            "revision": objectives["revision"],
            "items": objectives["items"],
            "updated_at": objectives["updated_at"],
        },
    )


def mark_stale(db: sqlite3.Connection, store: Any, clock: datetime) -> int:
    """A self-work session ended: every objective done by now goes stale.

    Open ones carry over to the next session untouched. Returns how many went stale; the
    list's revision moves only when one did.
    """
    objectives = read(db, store)
    fresh = [item for item in objectives["items"] if item.get("done") and not is_stale(item)]
    for item in fresh:
        item.update(stale=True, stale_at=clock.isoformat())
    if fresh:
        _save(db, store, objectives)
    return len(fresh)


def apply_request(
    db: sqlite3.Connection, store: Any, request: ObjectivesRequest, *, from_user: bool = True
) -> tuple[dict[str, Any] | None, str]:
    """Apply a chat decision's change; returns the new list (or None) and a sentence.

    ``from_user`` is whether the user's own message started the turn. A turn they didn't
    start (a task finishing, a schedule) is Rem on its own, under the same rules as self-work.
    """
    payload = request.model_dump(exclude_none=True)
    payload["items"] = [item.model_dump() for item in request.items]
    payload["checks"] = [check.model_dump() for check in request.checks]
    if request.action in {"edit"} and not request.text:
        payload.pop("text", None)
    try:
        objectives = change(db, store, payload, actor="rem", source="chat" if from_user else "own")
    except ObjectiveRefused as exc:
        return None, f"I didn't change the objectives: {exc}"
    open_count = len(open_items(objectives))
    said = {
        "set": "Set the self-work objectives",
        "add": "Added to the self-work objectives",
        "check": "Checked off on the objectives list",
        "edit": "Changed that objective",
        "remove": "Removed that objective",
        "uncheck": "Reopened that objective",
        "move": "Moved that objective",
    }[request.action]
    return objectives, (
        f"{said}; {open_count} {'is' if open_count == 1 else 'are'} open. "
        "They're in the Objectives list above Rem's tasks."
    )


def served(objectives: dict[str, Any], identity: Any) -> dict[str, Any] | None:
    """``{"id", "text"}`` of the open objective ``identity`` names, for a run or ticket."""
    if not identity:
        return None
    item = next((i for i in open_items(objectives) if i["id"] == str(identity).strip("[] ")), None)
    return {"id": item["id"], "text": item["text"]} if item else None


def balanced(
    candidates: list[dict[str, Any]],
    objective_of: Callable[[dict[str, Any]], str],
    load: dict[str, int],
    tier: Callable[[dict[str, Any]], int] | None = None,
) -> list[dict[str, Any]]:
    """``candidates`` in the order self-work should start them, taking turns across objectives.

    ``load`` is each objective's work so far (running and started this session). Each pick
    goes to the candidate whose objective has the least, and then counts toward it, so the
    next pick favors another objective. ``tier`` (lower first) comes before the load, for an
    order that must hold whatever the balance, such as the user's priorities on To do; equal
    candidates keep their given order. Without it, one objective whose tickets rank highest
    took every thread, the problem this fixes.
    """
    counts = dict(load)
    left = list(enumerate(candidates))
    picked: list[dict[str, Any]] = []
    while left:
        best = min(
            left,
            key=lambda pair: (
                tier(pair[1]) if tier else 0,
                counts.get(objective_of(pair[1]), 0),
                pair[0],
            ),
        )
        left.remove(best)
        picked.append(best[1])
        goal = objective_of(best[1])
        counts[goal] = counts.get(goal, 0) + 1
    return picked
