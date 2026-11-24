"""Rem's self-work objectives: the goals that guide a whole self-work session.

Objectives are a short checklist the user and Rem share. They are high-order goals that
self-work works toward across many tasks and sessions, such as "Improve reliability of the
Second Brain tools" or "Raise UX quality of the workflow studio", never one task's worth of
work: that belongs on the board as a ticket.

- Stored as one Rem setting (``self_work_objectives``) with a revision, so two devices can't
  overwrite each other and self-work can read the list fresh before every task it starts.
- The user can add, edit, remove, reorder, check and uncheck anything.
- Rem changes them from chat when the user tells it what self-work should focus on
  (``source="chat"``), and on its own (``source="self_work"``) when the list is empty at the
  start of a session or self-work runs out of work. On its own it may only add objectives and
  check them off.
- Rem checks an objective off only when it has written doneness criteria (``done_when``) and
  Rem gives the evidence that they are fully met. Open-ended objectives have no criteria and
  stay open; they don't have to be finished before self-work ends.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

SETTING = "self_work_objectives"
TEXT_MAX = 200
CRITERIA_MAX = 500
EVIDENCE_MAX = 700
# Everything on the list, done ones included; the oldest done ones go first past it.
MAX_ITEMS = 30
# Open objectives Rem keeps of its own: a handful of goals, not a to-do list.
MAX_OPEN_BY_REM = 6
# Objectives one self-work planning answer may check off.
MAX_CHECKS = 10
# The fewest words a goal Rem writes can have; "Fix bug" is a task, not an objective.
MIN_WORDS = 3

Actor = Literal["user", "rem"]
# Where a change by Rem came from: the user's chat message, or self-work deciding on its own.
Source = Literal["chat", "self_work"]

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
    "have no done_when and stay open; they don't need finishing before self-work ends. Give "
    "done_when when you add an objective only if a clear, checkable finish line exists."
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


class ObjectiveCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=64)
    evidence: str = Field(min_length=1, max_length=EVIDENCE_MAX)


class ObjectivesRequest(BaseModel):
    """A change to the objectives Rem returns in a chat decision, because the user asked.

    ``set`` replaces the open objectives with ``items`` (done ones stay as history);
    ``add`` appends ``items``; ``check`` marks ``checks`` done with evidence; ``edit``,
    ``remove``, ``uncheck`` and ``move`` act on ``id``.
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


def view(objectives: dict[str, Any]) -> dict[str, Any]:
    """What clients show: the list in order, with how many are open and done."""
    items = objectives["items"]
    return {
        "revision": objectives["revision"],
        "updated_at": objectives.get("updated_at"),
        "items": items,
        "open": sum(1 for item in items if not item.get("done")),
        "done": sum(1 for item in items if item.get("done")),
    }


def for_prompt(objectives: dict[str, Any]) -> list[dict[str, Any]]:
    """The list as a worker or Rem's model reads it: open ones first, short fields only."""
    ordered = [*open_items(objectives), *(i for i in objectives["items"] if i.get("done"))]
    return [
        {
            "id": item["id"],
            "text": item["text"],
            "done": bool(item.get("done")),
            **({"done_when": item["done_when"]} if item.get("done_when") else {}),
            **({"open_ended": True} if not item.get("done_when") else {}),
        }
        for item in ordered
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


def _same(a: str, b: str) -> bool:
    def key(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()

    return key(a) == key(b)


def _new(text: str, done_when: str, actor: Actor, source: str) -> dict[str, Any]:
    stamp = now()
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
    """Keep at most MAX_ITEMS, dropping the oldest done ones first."""
    extra = len(items) - MAX_ITEMS
    if extra <= 0:
        return items
    done = sorted((i for i in items if i.get("done")), key=lambda i: str(i.get("done_at")))
    dropped = {i["id"] for i in done[:extra]}
    return [i for i in items if i["id"] not in dropped]


def _add(
    objectives: dict[str, Any],
    entries: list[NewObjective],
    actor: Actor,
    source: str,
) -> list[dict[str, Any]]:
    """Append new objectives; Rem's must be high-order and few. Returns those added."""
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
            mine = [i for i in open_items(objectives) if i.get("created_by") == "rem"]
            if len(mine) >= MAX_OPEN_BY_REM:
                raise ObjectiveRefused(
                    "invalid",
                    f"Rem keeps at most {MAX_OPEN_BY_REM} open objectives of its own; "
                    "finish or drop one first.",
                )
        if len(open_items(objectives)) >= MAX_ITEMS:
            raise ObjectiveRefused("invalid", f"The list holds at most {MAX_ITEMS} objectives.")
        item = _new(text, done_when, actor, source)
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
) -> dict[str, Any]:
    """Apply one change in the caller's transaction and return the new list.

    ``payload`` takes ``action`` (add, edit, remove, move, check, uncheck, set) and
    ``expected_revision`` when a client shows the list. The user may change anything; Rem's
    changes follow the rules in this module's docstring.
    """
    objectives = read(db, store)
    expected = payload.get("expected_revision")
    if expected is not None and expected != objectives["revision"]:
        raise ObjectiveRefused("conflict", "The objectives changed. Check them and try again.")
    action = payload.get("action")
    rem_alone = actor == "rem" and source == "self_work"
    if rem_alone and action not in {"add", "check"}:
        raise ObjectiveRefused(
            "unauthorized", "Self-work can only add objectives or check them off."
        )
    if action == "add":
        entries = payload.get("items") or [
            {"text": payload.get("text"), "done_when": payload.get("done_when") or ""}
        ]
        try:
            parsed = [NewObjective.model_validate(e) for e in entries]
        except ValueError as exc:
            raise ObjectiveRefused("invalid", "Write the objective in a short sentence.") from exc
        if not _add(objectives, parsed, actor, source or actor):
            raise ObjectiveRefused("conflict", "That objective is already on the list.")
    elif action == "set":
        if actor != "rem":
            raise ObjectiveRefused("invalid", "Choose add, edit, remove, move, check or uncheck.")
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
        for entry in parsed:
            text = _clean(entry.text, TEXT_MAX)
            same = next((i for k, i in current.items() if _same(k, text)), None)
            if same is not None and not any(i["id"] == same["id"] for i in objectives["items"]):
                same.update(text=text, updated_at=now())
                if entry.done_when:
                    same["done_when"] = _clean(entry.done_when, CRITERIA_MAX)
                objectives["items"].append(same)
            else:
                _add(objectives, [entry], actor, source or actor)
        # The new goals first, the finished ones below them as history.
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
        item["updated_at"] = now()
    elif action == "remove":
        item = _find(objectives, payload.get("id"))
        objectives["items"] = [i for i in objectives["items"] if i["id"] != item["id"]]
    elif action == "move":
        item = _find(objectives, payload.get("id"))
        index = payload.get("index")
        if type(index) is not int or index < 0:
            raise ObjectiveRefused("invalid", "Choose where to move the objective.")
        rest = [i for i in objectives["items"] if i["id"] != item["id"]]
        rest.insert(min(index, len(rest)), item)
        objectives["items"] = rest
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
        for key in ("done_at", "done_by", "done_evidence"):
            item.pop(key, None)
        item.update(done=False, updated_at=now())
    else:
        raise ObjectiveRefused("invalid", "Choose add, edit, remove, move, check or uncheck.")
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
    return objectives


def apply_request(
    db: sqlite3.Connection, store: Any, request: ObjectivesRequest
) -> tuple[dict[str, Any] | None, str]:
    """Apply a chat decision's change; returns the new list (or None) and a sentence."""
    payload = request.model_dump(exclude_none=True)
    payload["items"] = [item.model_dump() for item in request.items]
    payload["checks"] = [check.model_dump() for check in request.checks]
    if request.action in {"edit"} and not request.text:
        payload.pop("text", None)
    try:
        objectives = change(db, store, payload, actor="rem", source="chat")
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
