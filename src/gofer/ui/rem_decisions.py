"""Rem's decisions: questions with a recommendation, and the answers Rem reuses.

Every question Rem or a worker brings to the user is a typed request (``DecisionRequest``)
with one recommended answer and evidence Raticode checks. Once answered, the decision is
stored under its project and topic, and the next worker gets the answer instead of asking
again. This module is the one place decisions live; the desktop, phone and CLI read and
change them through it.

- Stored as one Rem setting (``rem_decisions``) with a revision, so two devices can't
  overwrite each other (a stale ``expected_revision`` is a ``conflict``).
- Statuses: ``pending`` (waits for the user), ``provisional`` (Rem applied its own
  recommendation because self-work's auto mode is on; in force until the user confirms or
  changes it), ``confirmed`` (the user's), ``superseded`` and ``rejected``. A project
  shows at most ``MAX_PENDING_PER_PROJECT`` cards at once; a request past that is kept as
  ``queued`` (no card yet) and becomes ``pending`` when one of the waiting ones is settled.
- Who may do what is enforced here, not in prompts. Rem (``actor="rem"``) may only file
  requests (``file_request``). A request never changes an existing decision: when a topic
  already has a decision in force, Rem's different answer becomes a ``pending`` proposal
  that names the decision it would replace, and the old one stays in force until the user
  approves. Only the user (``actor="user"``) confirms, changes, edits, rejects, reopens,
  retires or forgets decisions. A record whose file is in the repository is retired
  (closed, its file saying ``status: rejected``); forgetting one is refused.
- The user can ask Rem in chat to change a decision in force (``DecisionEdit``). Rem
  never applies it: ``propose_edit`` stores it as a waiting edit (``E-n``) with the before
  and after worked out here from the store, and the chat shows an OK/Cancel card. Only the
  user's ``confirm_edit``, through ``change``, applies it, and only while the decision is
  as it was when the card was made.
- A topic is asked once. A request for a topic with a decision in force is answered from
  the store. It opens again only when a staleness trigger fires: a file its evidence cited
  changed, or a confirmed command failed twice in a row (``note_check``). The new card says
  which trigger fired.
- The user's Ask again (``reopen``) works the same way: it files a new waiting request for
  the topic that names the decision it would replace (``supersedes``), and the decision
  stays in force until the user answers, so workers keep using it meanwhile. For a topic in
  ``SCAN_ONLY_TOPICS`` the caller may pass a fresh scan's request (``offer``) as the new
  card's recommendation.
- Plan with me (``rem_plans``): when the user asks Rem in chat to plan something with
  them, its questions are filed here as ordinary requests tagged with the plan's ID
  (``item["plan"]``, ``P-n``) and the plan itself is kept in ``book["plans"]``. Plan
  questions always wait for the user. When the last one is settled the plan becomes
  ``ready`` (``_settle_plans``), and Rem's home hands it back to Rem once.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from gofer.ui import rem_auto_mode as auto_mode
from gofer.ui import rem_decision_guard as decision_guard
from gofer.ui.rem_decision_records import RECORD_CHARS, RecordFields

SETTING = "rem_decisions"
# Edits the user asked for in chat: at most this many wait for OK at once, and this many
# are kept in all, the oldest closed ones going first.
EDITS_WAITING = 10
EDITS_KEPT = 50
# The words Rem quotes from the user's message must be at least this long once normalized,
# so a stray "yes" or "ok" can't stand for a request to change a decision.
SAID_MIN = 10
EDIT_STATUSES = ("waiting", "applied", "cancelled", "replaced")
# A change's parts, in the order a card shows them, with the label it shows.
EDIT_FIELDS = (
    ("question", "Question"),
    ("answer", "Decision"),
    ("why", "Why"),
    ("context", "Context"),
    ("options", "Options we didn't choose"),
    ("consequences", "Consequences"),
    ("check", "How we check it"),
    ("applies_to", "Applies to"),
    ("forbids", "Never"),
    ("signals", "Signals"),
)
# Everything kept, closed ones included; the oldest superseded or rejected go first past it.
MAX_ITEMS = 400
# Open requests one project may have waiting for the user at once. More are kept as
# "queued" and move up, oldest first, as the waiting ones are settled.
MAX_PENDING_PER_PROJECT = 3
# A confirmed command that fails this many times in a row opens its topic again.
FAILURES_TO_REOPEN = 2
# Characters of the decisions a worker or Rem's model gets in its prompt. Plain facts are
# always listed; records fill the rest, one short entry each.
PROMPT_CHARS = 4000
# A record's entry in that index carries the first sentence of its decision, cut to this.
RECORD_SENTENCE_CHARS = 240
FILE_BYTES = 2_000_000
# Plans kept in all; the oldest handed-back ones go first past it.
PLANS_KEPT = 50

Actor = Literal["user", "rem"]
Status = Literal["queued", "pending", "provisional", "confirmed", "superseded", "rejected"]
IN_FORCE = frozenset({"provisional", "confirmed"})
OPEN = frozenset({"pending", "provisional"})

# Topic keys are short and stable so two workers asking the same thing land on one entry:
# "checks.fast", "branch.integration", "decisions.folder", "adr:workflow-format".
TOPIC = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,79}$")
# The topics Raticode itself acts on: the command it runs before a review, the branch it
# checks work landed on, and the folder it writes records to. Only a project scan
# (``source["via"] == "scan"``) may set them provisionally in auto mode; a worker's or the
# chat's request for one always waits for the user.
SCAN_ONLY_TOPICS = frozenset({"checks.fast", "branch.integration", "decisions.folder"})
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
URL = re.compile(r"^https://\S+$")

# Shown when Raticode builds the card itself because Rem's request couldn't be used.
FALLBACK_TITLE = "Rem couldn't settle this. Here is what it found."
# Why the card a user's Ask again files is waiting; clients show "You asked again".
ASKED_AGAIN = "You asked again."

RULES = (
    "Never bring the user a bare question. When the user must decide something, set "
    "needs_user:true and fill decision_request: {topic:a short stable key such as "
    "checks.fast, branch.integration or adr:<slug>, question:one sentence, "
    "recommendation:{answer,why (under 300 characters),evidence:[{kind:file|quote|command|"
    "commit|doc,ref,excerpt}]}, alternatives:[{answer,why_not}] (0-3), "
    "remember_as:fact|record|memory, reversible:true|false, tried}. Work the answer out "
    "first from the project's files, history and the decisions already made, and recommend "
    "one. Evidence is checked: a file ref must exist in the project, a quote's excerpt must "
    "appear in its file ref, a command must have run in this task, a commit must exist. Only "
    "a question about credentials, an account, a payment or an action only the user can "
    "take may come without a recommendation, and then tried must say what was attempted. "
    "Never ask again about a topic listed under decisions: use the recorded answer; to "
    "propose changing one, set replaces to its ID (the user decides). "
    "A design decision workers must keep to is remember_as:record with record:{context,"
    "options,consequences,check,applies_to:[path globs it is about],forbids:[path globs a "
    "change must never add],signals:[words that suggest a contradiction]}. If two decisions "
    "conflict, don't try to satisfy both: return decision_conflicts:[{ids:[the conflicting "
    "IDs],why,fix:{question,answer,why}}] with one recommended clean replacement for them."
)


class ConflictFix(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: str = Field(default="", max_length=500)
    answer: str = Field(min_length=1, max_length=1000)
    why: str = Field(default="", max_length=300)


class DecisionConflict(BaseModel):
    """Decisions that can't all hold, and the one clean fix Rem recommends for them.

    Rem never satisfies both sides of a conflict. The fix is one new decision that replaces
    every listed one; it always waits for the user, auto mode or not.
    """

    model_config = ConfigDict(extra="ignore")
    ids: list[str] = Field(min_length=2, max_length=5)
    why: str = Field(min_length=1, max_length=600)
    fix: ConflictFix


class DecisionRefused(ValueError):
    """A change that can't be made; ``code`` maps to an HTTP status like objectives."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: Literal["file", "quote", "command", "commit", "doc"]
    ref: str = Field(min_length=1, max_length=500)
    excerpt: str = Field(default="", max_length=500)


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    answer: str = Field(min_length=1, max_length=1000)
    why: str = Field(default="", max_length=300)
    evidence: list[Evidence] = Field(min_length=1, max_length=5)


class Alternative(BaseModel):
    model_config = ConfigDict(extra="ignore")
    answer: str = Field(min_length=1, max_length=1000)
    why_not: str = Field(default="", max_length=300)


class DecisionRequest(BaseModel):
    """A question for the user with Rem's recommended answer and its evidence."""

    model_config = ConfigDict(extra="ignore")
    topic: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=500)
    recommendation: Recommendation | None = None
    alternatives: list[Alternative] = Field(default_factory=list, max_length=3)
    remember_as: Literal["fact", "record", "memory"] = "fact"
    reversible: bool = True
    tried: str = Field(default="", max_length=1000)
    # Only for a request in Rem's chat: the project root it is about, or null.
    project: str | None = Field(default=None, max_length=4096)
    # With remember_as "record": the decision record's other parts (rem_decision_records).
    record: RecordFields | None = None
    # The ID of a decision in force this request proposes to replace. It always waits for
    # the user: Rem never replaces a decision on its own.
    replaces: str | None = Field(default=None, max_length=64)

    @field_validator("topic", mode="before")
    @classmethod
    def clean_topic(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        cleaned = re.sub(r"[^a-z0-9._:/-]+", "-", value.strip().casefold()).strip("-")
        return cleaned[:80]

    @model_validator(mode="after")
    def recommended(self) -> DecisionRequest:
        if not TOPIC.match(self.topic):
            raise ValueError("Give the decision a short topic key such as checks.fast")
        if self.recommendation is None:
            if not auto_mode.human_only(self.question):
                raise ValueError(
                    "A question for the user needs one recommended answer with evidence"
                )
            if not self.tried.strip():
                raise ValueError("A question only the user can answer must say what Rem tried")
        return self


class DecisionAnswer(BaseModel):
    """The user's answer to a waiting request, given in Rem's chat (``decision_answers``).

    ``use`` takes the recommendation, ``change`` gives the user's own ``answer`` and
    ``reject`` turns the request down. Rem returns one only when the user's message, read
    with the conversation, plainly answers that request; ``said`` quotes the words.
    """

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    action: Literal["use", "change", "reject"]
    answer: str = Field(default="", max_length=1000)
    said: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def changed_answer(self) -> DecisionAnswer:
        if self.action == "change" and not self.answer.strip():
            raise ValueError("A changed decision needs the user's answer")
        return self


class RecordChange(BaseModel):
    """The parts of a decision record a chat edit changes; parts left out stay as they are."""

    model_config = ConfigDict(extra="forbid")
    context: str | None = Field(default=None, max_length=RECORD_CHARS)
    options: str | None = Field(default=None, max_length=RECORD_CHARS)
    consequences: str | None = Field(default=None, max_length=RECORD_CHARS)
    check: str | None = Field(default=None, max_length=600)
    applies_to: list[str] | None = Field(default=None, max_length=10)
    forbids: list[str] | None = Field(default=None, max_length=10)
    signals: list[str] | None = Field(default=None, max_length=10)


class DecisionEdit(BaseModel):
    """The user's request, in Rem's chat, to change a decision in force (``decision_edits``).

    ``edit`` rewrites the decision; ``replace`` makes a new one in its place. Rem never
    applies either: Raticode checks that ``said`` is in the user's own message, works out
    the before and after from the store and shows an OK/Cancel card (``propose_edit``).
    """

    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    action: Literal["edit", "replace"] = "edit"
    question: str | None = Field(default=None, max_length=500)
    answer: str | None = Field(default=None, max_length=1000)
    why: str | None = Field(default=None, max_length=300)
    record: RecordChange | None = None
    said: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def changes_something(self) -> DecisionEdit:
        if self.action == "replace" and not (self.answer or "").strip():
            raise ValueError("A replacement needs the new decision")
        parts = [self.question, self.answer, self.why]
        if self.record is not None:
            parts += [getattr(self.record, key) for key in RECORD_KEYS]
        if all(part is None for part in parts):
            raise ValueError("Say what changes")
        return self


def now() -> str:
    return datetime.now(UTC).isoformat()


def _clean(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def project_key(project: str | None) -> str | None:
    """Projects are stored by their resolved folder, so two spellings meet."""
    if not project:
        return None
    try:
        return str(Path(project).expanduser().resolve())
    except (OSError, RuntimeError):
        return str(project)


def read(db: sqlite3.Connection, store: Any) -> dict[str, Any]:
    stored = store.setting(db, SETTING)
    return {
        "revision": int(stored.get("revision") or 0),
        "next_number": int(stored.get("next_number") or 1),
        "items": [item for item in stored.get("items") or [] if isinstance(item, dict)],
        "updated_at": stored.get("updated_at"),
        # Changes the user asked for in chat, each waiting for their OK (propose_edit).
        "next_edit": int(stored.get("next_edit") or 1),
        "edits": [edit for edit in stored.get("edits") or [] if isinstance(edit, dict)],
        # Plan with me: the plans whose questions are filed here (rem_plans).
        "next_plan": int(stored.get("next_plan") or 1),
        "plans": [plan for plan in stored.get("plans") or [] if isinstance(plan, dict)],
    }


def _save(db: sqlite3.Connection, store: Any, book: dict[str, Any]) -> dict[str, Any]:
    book["revision"] = int(book["revision"]) + 1
    book["updated_at"] = now()
    book["items"] = _trim(book["items"])
    book["edits"] = _trim_edits(book.get("edits") or [])
    book["plans"] = _trim_plans(_settle_plans(book))
    store.set_setting(
        db,
        SETTING,
        {
            "revision": book["revision"],
            "next_number": book["next_number"],
            "items": book["items"],
            "updated_at": book["updated_at"],
            "next_edit": int(book.get("next_edit") or 1),
            "edits": book["edits"],
            "next_plan": int(book.get("next_plan") or 1),
            "plans": book["plans"],
        },
    )
    return book


def plan_items(book: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any] | None]:
    """Each of a plan's questions as stored now, None for one the user forgot."""
    by_id = {i["id"]: i for i in book["items"]}
    return [by_id.get(str(identity)) for identity in plan.get("questions") or []]


def plan_progress(book: dict[str, Any], plan: dict[str, Any]) -> dict[str, int]:
    """How many of a plan's questions are settled, still waiting, or queued without a card."""
    items = plan_items(book, plan)
    waiting = sum(1 for i in items if i is not None and i["status"] == "pending")
    queued = sum(1 for i in items if i is not None and i["status"] == "queued")
    return {
        "total": len(items),
        "answered": len(items) - waiting - queued,
        "waiting": waiting,
        "queued": queued,
    }


def _settle_plans(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Mark each plan whose last question was just settled ``ready`` for the hand-back.

    A question is settled once the user answered it, turned it down or forgot it. A plan
    is handed back to Rem once (rem_plans), so its status only moves forward:
    ``asking``, ``ready``, ``handed_back``.
    """
    plans = list(book.get("plans") or [])
    for plan in plans:
        if plan.get("status") != "asking":
            continue
        progress = plan_progress(book, plan)
        if progress["waiting"] or progress["queued"]:
            continue
        plan["status"] = "ready"
        plan["settled_at"] = now()
    return plans


def _trim_plans(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    extra = len(plans) - PLANS_KEPT
    if extra <= 0:
        return plans
    done = sorted(
        (p for p in plans if p.get("status") == "handed_back"),
        key=lambda p: str(p.get("handed_back_at")),
    )
    dropped = {p["id"] for p in done[:extra]}
    return [p for p in plans if p["id"] not in dropped]


def _trim_edits(edits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    extra = len(edits) - EDITS_KEPT
    if extra <= 0:
        return edits
    closed = sorted(
        (e for e in edits if e.get("status") != "waiting"),
        key=lambda e: str(e.get("created_at")),
    )
    dropped = {e["id"] for e in closed[:extra]}
    return [e for e in edits if e["id"] not in dropped]


def _trim(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    extra = len(items) - MAX_ITEMS
    if extra <= 0:
        return items
    # A record whose file is in the repository is never dropped: the next sync would read
    # the file back in as a new decision.
    closed = sorted(
        (
            i
            for i in items
            if i["status"] in {"superseded", "rejected"} and not i.get("record_path")
        ),
        key=lambda i: str(i.get("updated_at")),
    )
    dropped = {i["id"] for i in closed[:extra]}
    return [i for i in items if i["id"] not in dropped]


def _find(book: dict[str, Any], identity: Any) -> dict[str, Any]:
    item: dict[str, Any] | None = next((i for i in book["items"] if i["id"] == identity), None)
    if item is None:
        raise DecisionRefused("not_found", "That decision is no longer recorded.")
    return item


def in_force(book: dict[str, Any], project: str | None, topic: str) -> dict[str, Any] | None:
    """The decision that answers (project, topic) now, newest first."""
    project = project_key(project)
    found = [
        i
        for i in book["items"]
        if i["topic"] == topic and i.get("project") == project and i["status"] in IN_FORCE
    ]
    return max(found, key=lambda i: str(i.get("decided_at") or ""), default=None)


def _pending(book: dict[str, Any], project: str | None, topic: str) -> dict[str, Any] | None:
    """The request for (project, topic) still waiting for the user, shown or queued."""
    return next(
        (
            i
            for i in book["items"]
            if i["topic"] == topic
            and i.get("project") == project
            and i["status"] in {"pending", "queued"}
        ),
        None,
    )


def _promote(book: dict[str, Any], project: str | None) -> list[dict[str, Any]]:
    """Move a project's oldest queued requests up while it has room for more cards.

    Called whenever the user settles or forgets a request, so a queued question gets its
    card as soon as one of the waiting ones is answered, turned down or forgotten.
    """
    moved: list[dict[str, Any]] = []
    here = [i for i in book["items"] if i.get("project") == project]
    shown = sum(1 for i in here if i["status"] == "pending")
    # Oldest first; items are kept in the order they were filed, so ties keep that order.
    for item in sorted(
        (i for i in here if i["status"] == "queued"), key=lambda i: str(i.get("created_at"))
    ):
        if shown >= MAX_PENDING_PER_PROJECT:
            break
        item["status"] = "pending"
        item["updated_at"] = now()
        _history(item, "raticode", "moved up", "a question waiting before it was settled")
        moved.append(item)
        shown += 1
    return moved


def _history(item: dict[str, Any], actor: str, action: str, note: str = "") -> None:
    item.setdefault("history", []).append(
        {"at": now(), "actor": actor, "action": action, **({"note": note} if note else {})}
    )
    item["history"] = item["history"][-20:]


def _file_hash(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > FILE_BYTES:
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _inside(project: Path, ref: str) -> Path | None:
    """The file ``ref`` names inside ``project``, or None when it leaves the folder."""
    ref = ref.split("#", 1)[0]
    ref = re.sub(r":\d+(?:-\d+)?$", "", ref.strip())
    candidate = Path(ref).expanduser()
    if not candidate.is_absolute():
        candidate = project / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(project.resolve())
    except (OSError, ValueError, RuntimeError):
        return None
    return resolved


def _commit_exists(project: Path, ref: str) -> bool:
    from gofer.utils.process import NO_REPOSITORY_PROGRAMS, untrusted_repository_git_env

    try:
        env = untrusted_repository_git_env(project, {"PATH": "/usr/bin:/bin"})
        result = subprocess.run(
            [
                "git",
                *NO_REPOSITORY_PROGRAMS,
                "-C",
                str(project),
                "cat-file",
                "-e",
                ref + "^{commit}",
            ],
            capture_output=True,
            timeout=15,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _ran(command: str, traces: list[dict[str, Any]]) -> bool:
    from gofer.subscriptions.tool_trace import successful_receipt

    wanted = " ".join(command.split())
    starts = {
        t.get("id"): " ".join(str(t.get("input") or t.get("detail") or "").split())
        for t in traces
        if t.get("phase") == "start"
    }
    for trace in traces:
        if not successful_receipt(trace):
            continue
        text = " ".join(str(trace.get("input") or trace.get("detail") or "").split())
        if wanted and (wanted in text or wanted in starts.get(trace.get("id"), "")):
            return True
    return False


def check_evidence(
    request: DecisionRequest, project: str | None, traces: list[dict[str, Any]] | None = None
) -> tuple[DecisionRequest, list[str]]:
    """Keep only evidence Raticode could check; returns the request and what was dropped.

    A file must exist in the project, a quote's excerpt must appear in its file, a command
    must have a successful receipt from this run, a commit must exist in the project's git
    and a doc must be an https link or an existing file in the project. Raises ValueError
    when a recommendation is left with no evidence.
    """
    if request.recommendation is None:
        return request, []
    root = Path(project).expanduser() if project else None
    kept: list[Evidence] = []
    dropped: list[str] = []
    for item in request.recommendation.evidence:
        ok = False
        if item.kind in {"file", "quote", "doc"} and root is not None:
            path = _inside(root, item.ref)
            if path is not None and path.is_file():
                if item.kind == "quote":
                    try:
                        text = path.read_text(errors="replace")[:FILE_BYTES]
                    except OSError:
                        text = ""
                    excerpt = " ".join(item.excerpt.split())
                    ok = bool(excerpt) and excerpt in " ".join(text.split())
                else:
                    ok = True
        if item.kind == "doc" and URL.match(item.ref.strip()):
            ok = True
        if item.kind == "commit" and root is not None and COMMIT.match(item.ref.strip()):
            ok = _commit_exists(root, item.ref.strip())
        if item.kind == "command":
            ok = _ran(item.ref, traces or [])
        if ok:
            kept.append(item)
        else:
            dropped.append(f"{item.kind} {item.ref[:120]}")
    if not kept:
        raise ValueError(
            "None of the recommendation's evidence could be checked: " + "; ".join(dropped)
        )
    checked = request.model_copy(
        update={"recommendation": request.recommendation.model_copy(update={"evidence": kept})}
    )
    return checked, dropped


def _evidence_hashes(evidence: list[dict[str, Any]], project: str | None) -> dict[str, str]:
    if not project:
        return {}
    root = Path(project)
    hashes: dict[str, str] = {}
    for item in evidence:
        if item.get("kind") not in {"file", "quote", "doc"}:
            continue
        path = _inside(root, str(item.get("ref") or ""))
        digest = _file_hash(path) if path is not None else None
        if path is not None and digest:
            hashes[str(path.relative_to(root.resolve()))] = digest
    return hashes


def stale_reason(item: dict[str, Any]) -> str:
    """Why a decision in force should be asked again, or "" while it still holds."""
    if int(item.get("failures") or 0) >= FAILURES_TO_REOPEN:
        return f"Its command failed {item['failures']} times in a row."
    project = item.get("project")
    if not project:
        return ""
    root = Path(project)
    for rel, digest in (item.get("evidence_hashes") or {}).items():
        if _file_hash(root / rel) != digest:
            return f"{rel} changed since it was decided."
    return ""


def _new_item(
    book: dict[str, Any],
    request: DecisionRequest,
    project: str | None,
    status: Status,
    *,
    actor: Actor,
    source: dict[str, Any],
    plan: str | None = None,
) -> dict[str, Any]:
    stamp = now()
    evidence = (
        [e.model_dump() for e in request.recommendation.evidence] if request.recommendation else []
    )
    item: dict[str, Any] = {
        "id": f"D-{book['next_number']}",
        "project": project,
        "topic": request.topic,
        "question": _clean(request.question, 500),
        "answer": request.recommendation.answer.strip() if request.recommendation else "",
        "recommended": request.recommendation.answer.strip() if request.recommendation else "",
        "why": _clean(request.recommendation.why, 300) if request.recommendation else "",
        "evidence": evidence,
        "alternatives": [a.model_dump() for a in request.alternatives],
        "remember_as": "memory" if project is None else request.remember_as,
        **(
            {"record": request.record.model_dump() if request.record else {}}
            if project is not None and request.remember_as == "record"
            else {}
        ),
        "reversible": request.reversible,
        "tried": _clean(request.tried, 1000),
        "status": status,
        "decided_by": actor if status in IN_FORCE else None,
        "decided_at": stamp if status in IN_FORCE else None,
        "source": {k: v for k, v in source.items() if v},
        "asked_by": [source["run_id"]] if source.get("run_id") else [],
        "evidence_hashes": _evidence_hashes(evidence, project),
        "failures": 0,
        "created_at": stamp,
        "updated_at": stamp,
        **({"plan": plan} if plan else {}),
    }
    book["next_number"] += 1
    _history(item, actor, "requested" if status == "pending" else status)
    book["items"].append(item)
    return item


def file_request(
    db: sqlite3.Connection,
    store: Any,
    request: DecisionRequest,
    *,
    project: str | None,
    decides_alone: bool,
    source: dict[str, Any] | None = None,
    plan: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Record Rem's request and say what happens next. Rem's only way to write decisions.

    Returns ``(outcome, item)``:

    - ``answered``: the topic already has a decision in force that still holds; ``item`` is
      it, and nothing is stored.
    - ``waiting``: the same question is already waiting for the user; this run is added
      to it, so the user sees one card.
    - ``queued``: the user decides, but the project already has ``MAX_PENDING_PER_PROJECT``
      cards waiting. The request is stored as itself with no card yet, this run is linked
      to it, and it becomes ``pending`` when one of the waiting ones is settled. Asking the
      same queued question again adds the run to it and returns ``queued`` too.
    - ``provisional``: auto mode is on and the request is reversible with a recommendation,
      and the topic has no decision yet. Rem's answer is in force until the user changes it.
      For a topic in ``SCAN_ONLY_TOPICS`` only a project scan's request can be provisional.
    - ``pending``: the user decides. That includes every proposal to replace a decision in
      force (``item["supersedes"]``): Rem never edits or replaces one on its own. It also
      includes a request that would weaken a decision in force, such as an exception for
      the work at hand (``rem_decision_guard.weakens``); it waits as a conflict that names
      them (``item["weakens"]``), auto mode or not.

    ``plan`` tags the request with the plan it belongs to (rem_plans). A plan question
    always waits for the user, auto mode or not; one the same as a question already
    waiting is tagged too, unless another plan has it.
    """
    source = source or {}
    project = project_key(project)
    if plan:
        decides_alone = False
    book = read(db, store)
    current = in_force(book, project, request.topic)
    replaced = next(
        (
            i
            for i in book["items"]
            if request.replaces
            and i["id"] == request.replaces
            and i.get("project") == project
            and i["status"] in IN_FORCE
        ),
        None,
    )
    if replaced is not None:
        # An explicit proposal to replace a decision: a card for the user, never applied.
        current = replaced
        request = request.model_copy(update={"topic": replaced["topic"]})
    stale = stale_reason(current) if current else ""
    if current is not None and not stale and replaced is None:
        return "answered", current
    waiting = _pending(book, project, request.topic)
    if waiting is not None:
        run_id = source.get("run_id")
        changed = False
        if run_id and run_id not in waiting.setdefault("asked_by", []):
            waiting["asked_by"] = [*waiting["asked_by"], run_id][-20:]
            changed = True
        if plan and not waiting.get("plan"):
            waiting["plan"] = plan
            changed = True
        if changed:
            _save(db, store, book)
        return ("queued" if waiting["status"] == "queued" else "waiting"), waiting
    # Self-work never makes an exception to a decision for itself: one that would weaken a
    # decision in force is the user's to add, even with auto mode on.
    weakened, weakens_why = decision_guard.weakens(
        request, [i for i in decision_guard.in_force(book, project) if i is not current]
    )
    alone = (
        decides_alone
        and current is None
        and request.reversible
        and request.recommendation is not None
        and not weakens_why
        # A worker can't pick the command Raticode runs, the branch it lands on or the
        # folder Raticode writes to: only the scan, which reads the project's own files.
        and (request.topic not in SCAN_ONLY_TOPICS or source.get("via") == "scan")
    )
    status: Status = "provisional" if alone else "pending"
    if not alone:
        shown = sum(
            1 for i in book["items"] if i.get("project") == project and i["status"] == "pending"
        )
        if shown >= MAX_PENDING_PER_PROJECT:
            # Few cards at once: this one is kept as itself and waits its turn.
            status = "queued"
    item = _new_item(book, request, project, status, actor="rem", source=source, plan=plan)
    if current is not None:
        item["supersedes"] = current["id"]
        item["reopened"] = stale or "Rem proposes replacing it."
    if weakens_why:
        item["weakens"] = weakened
        item["conflict"] = weakens_why[:600]
        _history(item, "rem", "conflict found", ", ".join(weakened) or "asks for an exception")
    _save(db, store, book)
    return status, item


def file_conflict(
    db: sqlite3.Connection,
    store: Any,
    conflict: DecisionConflict,
    *,
    project: str | None,
    source: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Record Rem's one recommended fix for decisions that conflict; the user decides.

    The fix waits as a ``pending`` request that would replace every listed decision
    (``supersedes_all``), even with auto mode on: it changes decisions already made, which
    only the user may do. Returns ``("pending", item)``, ``("waiting", item)`` when the same
    conflict already waits, or ``("ignored", None)`` when fewer than two of the IDs are
    decisions in force for the project.
    """
    source = source or {}
    key = project_key(project)
    book = read(db, store)
    named = [
        i
        for i in book["items"]
        if i["id"] in set(conflict.ids) and i.get("project") == key and i["status"] in IN_FORCE
    ]
    if len(named) < 2:
        return "ignored", None
    ids = sorted(i["id"] for i in named)
    topic = "conflict:" + "-".join(x.lower() for x in ids)[:70]
    waiting = _pending(book, key, topic)
    if waiting is not None:
        return "waiting", waiting
    stamp = now()
    records = [i for i in named if i.get("remember_as") == "record"]
    item: dict[str, Any] = {
        "id": f"D-{book['next_number']}",
        "project": key,
        "topic": topic,
        "question": _clean(conflict.fix.question, 500)
        or "These decisions conflict: " + ", ".join(ids) + ". Replace them with one?",
        "answer": conflict.fix.answer.strip(),
        "recommended": conflict.fix.answer.strip(),
        "why": _clean(conflict.fix.why or conflict.why, 300),
        "evidence": [],
        "alternatives": [],
        "remember_as": "record" if records else named[0].get("remember_as", "fact"),
        **({"record": dict(records[0].get("record") or {})} if records else {}),
        "reversible": False,
        "tried": "",
        "conflict": _clean(conflict.why, 600),
        "supersedes_all": ids,
        "status": "pending",
        "decided_by": None,
        "decided_at": None,
        "source": {k: v for k, v in source.items() if v},
        "asked_by": [source["run_id"]] if source.get("run_id") else [],
        "evidence_hashes": {},
        "failures": 0,
        "created_at": stamp,
        "updated_at": stamp,
    }
    book["next_number"] += 1
    _history(item, "rem", "conflict found", ", ".join(ids))
    book["items"].append(item)
    _save(db, store, book)
    return "pending", item


def fallback_request(
    question: str, found: str, candidate: DecisionRequest | None
) -> dict[str, Any]:
    """The card Raticode builds itself when Rem's request failed twice.

    It holds no unchecked recommendation: the question, what the worker found and any
    candidate answer Rem had, marked as not checked.
    """
    return {
        "title": FALLBACK_TITLE,
        "question": _clean(question, 500) or "The task needs a decision from you.",
        "found": _clean(found, 1200),
        "candidate": (
            candidate.recommendation.answer.strip()
            if candidate is not None and candidate.recommendation is not None
            else ""
        ),
        "fallback": True,
    }


def card(item: dict[str, Any]) -> dict[str, Any]:
    """The fields a run, a ticket and a chat card show for a request."""
    return {
        "id": item["id"],
        "topic": item["topic"],
        "question": item["question"],
        "recommended": item.get("recommended") or item.get("answer") or "",
        "why": item.get("why") or "",
        "evidence": item.get("evidence") or [],
        "alternatives": item.get("alternatives") or [],
        "status": item["status"],
        "tried": item.get("tried") or "",
        **({"supersedes": item["supersedes"]} if item.get("supersedes") else {}),
        **({"supersedes_all": item["supersedes_all"]} if item.get("supersedes_all") else {}),
        **({"conflict": item["conflict"]} if item.get("conflict") else {}),
        **({"weakens": item["weakens"]} if item.get("weakens") else {}),
        **({"reopened": item["reopened"]} if item.get("reopened") else {}),
        # The user's Ask again: the answer in force meanwhile, and whether a fresh scan of
        # the project gave this card its recommendation.
        **(
            {"asked_again": True, "current_answer": item.get("current_answer") or ""}
            if item.get("asked_again")
            else {}
        ),
        **({"fresh": True} if item.get("fresh") else {}),
        # Plan with me: the plan this question belongs to (rem_plans).
        **({"plan": item["plan"]} if item.get("plan") else {}),
    }


QUEUED_TEXT = "Queued behind the questions already waiting for you"


def waiting_text(item: dict[str, Any]) -> str:
    """One line for a ticket or chat update: the question and the recommended answer."""
    if item.get("fallback"):
        candidate = (
            f" A possible answer, not checked: {item['candidate']}" if item.get("candidate") else ""
        )
        return f"{FALLBACK_TITLE} {item['question']}{candidate}"
    recommended = item.get("recommended") or item.get("answer") or ""
    if item.get("status") == "queued":
        line = f"{QUEUED_TEXT}: {item['question']}"
    else:
        line = f"Waiting on your choice: {item['question']}"
    if recommended:
        line += f" Rem recommends: {recommended}"
    elif item.get("tried"):
        line += f" Rem tried: {item['tried']}"
    if item.get("asked_again") and item.get("current_answer"):
        line += f" Current answer: {item['current_answer']}"
    return line


def _words(text: Any) -> str:
    """Text as plain lowercase words, so quotes, punctuation and spacing don't matter."""
    return " ".join(re.sub(r"[\W_]+", " ", str(text or "").casefold()).split())


def said_in(said: str, message: str) -> bool:
    """Whether the words Rem quotes are in the user's message, and long enough to mean it."""
    quoted = _words(said)
    return len(quoted) >= SAID_MIN and quoted in _words(message)


def _snapshot(item: dict[str, Any]) -> dict[str, Any]:
    """What a card compares: the decision's wording and, for a record, its parts."""
    shown = {key: item.get(key) or "" for key in ("question", "answer", "why")}
    if item.get("remember_as") == "record":
        record = item.get("record") or {}
        shown.update(
            {key: record.get(key) or ([] if key in RECORD_LISTS else "") for key in RECORD_KEYS}
        )
    return shown


def _fingerprint(item: dict[str, Any]) -> str:
    """What a card was made from: the decision's wording, record parts and status.

    OK applies a change only while this is the same, so an edit made elsewhere since is
    never overwritten, while bookkeeping such as refreshed evidence doesn't count.
    """
    shown = {**_snapshot(item), "status": item.get("status")}
    return hashlib.sha256(repr(sorted(shown.items())).encode()).hexdigest()


def _shown(value: Any) -> str:
    return ", ".join(value) if isinstance(value, list) else str(value or "")


def _edit_payload(edit: DecisionEdit, item: dict[str, Any]) -> dict[str, Any]:
    """The change ``change`` applies on OK: only the parts the user asked to change."""
    payload: dict[str, Any] = {"action": edit.action, "id": item["id"]}
    for key in ("question", "answer", "why"):
        value = getattr(edit, key)
        if value is not None:
            payload[key] = value.strip()
    if edit.record is not None and item.get("remember_as") == "record":
        for key in RECORD_KEYS:
            value = getattr(edit.record, key)
            if value is not None:
                payload[key] = value
    return payload


def _after(item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """The decision as it would read once ``payload`` is applied, by the rules of ``change``."""
    after = _snapshot(item)
    replace = payload["action"] == "replace"
    if replace:
        after["why"] = ""
    for key, limit in (("answer", 1000), ("question", 500), ("why", 300)):
        if key in payload:
            text = str(payload[key] or "").strip()[:limit]
            if key != "why" and not text:
                raise DecisionRefused("invalid", f"The {key} can't be empty.")
            after[key] = text
    if item.get("remember_as") == "record" and any(k in payload for k in RECORD_KEYS):
        after.update(_record_payload(payload, item.get("record") or {}))
    return after


def edit_card(edit: dict[str, Any]) -> dict[str, Any]:
    """What the chat's OK/Cancel card and the Decisions list show for a waiting edit."""
    return {
        "id": edit["id"],
        "decision": edit["decision"],
        "action": edit["action"],
        "topic": edit.get("topic") or "",
        "question": edit.get("question") or "",
        "project": edit.get("project"),
        "project_name": Path(edit["project"]).name if edit.get("project") else "",
        "record": edit.get("remember_as") == "record",
        "changes": edit.get("changes") or [],
        "said": edit.get("said") or "",
        "status": edit["status"],
        "created_at": edit.get("created_at"),
        **({"decided_at": edit["decided_at"]} if edit.get("decided_at") else {}),
        **({"result": edit["result"]} if edit.get("result") else {}),
        **({"replaced_by": edit["replaced_by"]} if edit.get("replaced_by") else {}),
    }


def propose_edit(
    db: sqlite3.Connection,
    store: Any,
    edit: DecisionEdit,
    *,
    message: str,
    source: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Store the user's chat request to change a decision as an edit waiting for their OK.

    Nothing about the decision changes here. The edit is kept only when ``edit.said`` is
    in ``message``, the user's own words in this chat message, and the decision is in
    force. The before and after come from the store, never from Rem. A newer request for
    the same decision replaces one still waiting. Returns the stored edit (or None) and a
    sentence for Rem's reply.
    """
    if not said_in(edit.said, message):
        return None, (
            f"I didn't prepare a change to {edit.id}: I can only change a decision you ask me "
            "to change in your own message."
        )
    book = read(db, store)
    item = next((i for i in book["items"] if i["id"] == edit.id), None)
    if item is None:
        return None, f"I didn't prepare a change: there's no decision {edit.id}."
    if item["status"] not in IN_FORCE:
        state = "still waits for your answer" if item["status"] == "pending" else "is closed"
        return None, f"I didn't prepare a change to {item['id']}: it {state}."
    payload = _edit_payload(edit, item)
    try:
        before, after = _snapshot(item), _after(item, payload)
    except DecisionRefused as exc:
        return None, f"I didn't prepare a change to {item['id']}: {exc}"
    changes = [
        {"field": key, "label": label, "before": _shown(before[key]), "after": _shown(after[key])}
        for key, label in EDIT_FIELDS
        if key in after and _shown(before[key]) != _shown(after[key])
    ]
    if not changes:
        return None, f"{item['id']} already says that, so there's nothing to change."
    waiting = [e for e in book["edits"] if e.get("status") == "waiting"]
    older = [e for e in waiting if e.get("decision") == item["id"]]
    if len(waiting) - len(older) >= EDITS_WAITING:
        return None, (
            f"I didn't prepare a change to {item['id']}: {EDITS_WAITING} changes already wait "
            "for your OK. Press OK or Cancel on those first."
        )
    stamp = now()
    entry: dict[str, Any] = {
        "id": f"E-{book['next_edit']}",
        "decision": item["id"],
        "action": edit.action,
        "topic": item["topic"],
        "question": item["question"],
        "project": item.get("project"),
        "remember_as": item.get("remember_as"),
        "payload": payload,
        "changes": changes,
        # The decision as the card showed it; OK applies only while it still reads this way.
        "base": _fingerprint(item),
        "said": _clean(edit.said, 300),
        "status": "waiting",
        "source": {k: v for k, v in (source or {}).items() if v},
        "created_at": stamp,
    }
    book["next_edit"] += 1
    for stale in older:
        stale.update(status="replaced", replaced_by=entry["id"], decided_at=stamp)
    book["edits"].append(entry)
    _save(db, store, book)
    verb = "replace" if edit.action == "replace" else "change"
    return entry, (f"Check the {verb} to {item['id']} below. Nothing changes until you press OK.")


EDIT_CLOSED = {
    "applied": "This change was already made.",
    "cancelled": "This change was cancelled. Ask Rem again to make it.",
    "replaced": "A newer change to this decision replaced this one.",
}


def _settle_edit(
    db: sqlite3.Connection, store: Any, payload: dict[str, Any], *, how: str
) -> dict[str, Any]:
    """The user's OK (``confirm_edit``) or Cancel (``cancel_edit``) on a waiting edit."""
    book = read(db, store)
    edit = next((e for e in book["edits"] if e.get("id") == payload.get("id")), None)
    if edit is None:
        raise DecisionRefused("not_found", "That change is no longer listed. Ask Rem again.")
    if edit.get("status") != "waiting":
        raise DecisionRefused(
            "invalid", EDIT_CLOSED.get(edit.get("status"), EDIT_CLOSED["applied"])
        )
    stamp = now()
    if payload.get("action") == "cancel_edit":
        edit.update(status="cancelled", decided_at=stamp)
        return _save(db, store, book)
    item = next((i for i in book["items"] if i["id"] == edit["decision"]), None)
    if item is None or item["status"] not in IN_FORCE or _fingerprint(item) != edit.get("base"):
        raise DecisionRefused(
            "conflict",
            f"{edit['decision']} changed after Rem showed you this. Cancel it and ask Rem again.",
        )
    note = _clean(f"confirmed in chat: {edit.get('said') or ''}", 300)
    book = change(db, store, {**edit["payload"], "note": note}, actor="user", source=how)
    edit = next(e for e in book["edits"] if e.get("id") == payload.get("id"))
    applied = next((i for i in book["items"] if i["id"] == edit["decision"]), None)
    result = (
        (applied or {}).get("superseded_by") if edit["action"] == "replace" else edit["decision"]
    )
    edit.update(status="applied", decided_at=stamp, **({"result": result} if result else {}))
    return _save(db, store, book)


def change(
    db: sqlite3.Connection,
    store: Any,
    payload: dict[str, Any],
    *,
    actor: Actor,
    source: str = "",
    offer: DecisionRequest | None = None,
) -> dict[str, Any]:
    """Apply the user's change in the caller's transaction and return the new book.

    Actions: ``use`` (take the recommendation), ``change`` (the user's own ``answer``),
    ``edit`` (``answer``, ``question`` or ``why`` of any decision), ``reject``, ``reopen``
    (Ask again: a new request for the topic waits for the user, queued past
    ``MAX_PENDING_PER_PROJECT``, while the decision stays in force until it is answered;
    ``offer``, a fresh scan's checked request for the topic, gives it a recommendation),
    ``forget`` (delete the entry) and
    ``retire`` (a record whose file is in the repository is turned down, not deleted, and
    its file is rewritten with ``status: rejected`` and committed on the landing branch;
    ``forget`` is refused for such a record, so only Retire makes that commit), all on
    ``id``; and ``add`` (a decision the
    user states: ``topic``, ``question``, ``answer``, optional ``project``).
    ``confirm_edit`` and ``cancel_edit`` press OK or Cancel on the waiting edit ``id``
    (``E-n``) that the user asked Rem for in chat; they don't use
    ``expected_revision``, since OK applies only while the decision is as the card showed
    it. Rem can't call any of them: its requests go through ``file_request``, which never
    changes an existing decision, and its chat edits only wait (``propose_edit``).
    """
    if actor != "user":
        raise DecisionRefused(
            "unauthorized",
            "Only the user can confirm, change, replace or remove a decision; Rem can only "
            "ask, with a recommendation.",
        )
    if payload.get("action") in {"confirm_edit", "cancel_edit"}:
        return _settle_edit(db, store, payload, how=source or "user")
    book = read(db, store)
    expected = payload.get("expected_revision")
    if expected is not None and expected != book["revision"]:
        raise DecisionRefused("conflict", "The decisions changed. Check them and try again.")
    action = payload.get("action")
    how = source or "user"
    if action == "add":
        topic = str(payload.get("topic") or "").strip()
        question = _clean(payload.get("question") or topic, 500)
        answer = str(payload.get("answer") or "").strip()[:1000]
        if not answer:
            raise DecisionRefused("invalid", "Write the decision first.")
        project = project_key(payload.get("project"))
        record = bool(project) and payload.get("remember_as") == "record"
        topic = _topic(
            topic or ("adr:" + _slug(question or answer) if record else question or answer)
        )
        current = in_force(book, project, topic)
        stamp = now()
        item: dict[str, Any] = {
            "id": f"D-{book['next_number']}",
            "project": project,
            "topic": topic,
            "question": question or answer[:500],
            "answer": answer,
            "recommended": "",
            "why": _clean(payload.get("why"), 300),
            "evidence": [],
            "alternatives": [],
            "remember_as": "record" if record else "fact" if project else "memory",
            **({"record": _record_payload(payload, {})} if record else {}),
            "reversible": True,
            "tried": "",
            "status": "confirmed",
            "decided_by": "user",
            "decided_at": stamp,
            "source": {"via": how},
            "asked_by": [],
            "evidence_hashes": {},
            "failures": 0,
            "created_at": stamp,
            "updated_at": stamp,
        }
        book["next_number"] += 1
        _history(item, "user", "added", how)
        if current is not None:
            _supersede(current, item)
        book["items"].append(item)
        return _save(db, store, book)
    item = _find(book, payload.get("id"))
    stamp = now()
    answer_before = str(item.get("answer") or "").strip()
    if action == "replace":
        # The user's new decision in place of one in force: a new entry, linked both ways.
        if item["status"] not in IN_FORCE:
            raise DecisionRefused("invalid", "Only a decision in force can be replaced.")
        answer = str(payload.get("answer") or "").strip()[:1000]
        if not answer:
            raise DecisionRefused("invalid", "Write the new decision first.")
        new: dict[str, Any] = {
            **{k: v for k, v in item.items() if k in {"project", "topic", "remember_as"}},
            "id": f"D-{book['next_number']}",
            "question": _clean(payload.get("question"), 500) or item["question"],
            "answer": answer,
            "recommended": "",
            "why": _clean(payload.get("why"), 300),
            "evidence": [],
            "alternatives": [],
            "reversible": True,
            "tried": "",
            "status": "confirmed",
            "decided_by": "user",
            "decided_at": stamp,
            "source": {"via": how},
            "asked_by": [],
            "evidence_hashes": {},
            "failures": 0,
            "created_at": stamp,
            "updated_at": stamp,
        }
        if item.get("remember_as") == "record":
            new["record"] = _record_payload(payload, item.get("record") or {})
        book["next_number"] += 1
        _history(new, "user", "added", _clean(payload.get("note"), 300) or how)
        _supersede(item, new)
        book["items"].append(new)
        return _save(db, store, book)
    if action in {"use", "change"}:
        if item["status"] in {"superseded", "rejected"}:
            raise DecisionRefused("invalid", "This decision was closed; add a new one instead.")
        if action == "change":
            answer = str(payload.get("answer") or "").strip()[:1000]
            if not answer:
                raise DecisionRefused("invalid", "Write your answer first.")
            item["answer"] = answer
        elif not item.get("answer"):
            raise DecisionRefused(
                "invalid", "This request has no recommended answer; give your own with Change."
            )
        current = in_force(book, item.get("project"), item["topic"])
        if current is not None and current["id"] != item["id"]:
            _supersede(current, item)
        for other_id in item.get("supersedes_all") or []:
            # A conflict's fix replaces every decision it names, once the user approves it.
            other = next((i for i in book["items"] if i["id"] == other_id), None)
            if other is not None and other["status"] in IN_FORCE:
                _supersede(other, item, single=False)
        item.update(status="confirmed", decided_by="user", decided_at=stamp, failures=0)
        item["evidence_hashes"] = _evidence_hashes(item.get("evidence") or [], item.get("project"))
        for key in ("reopened", "asked_again", "current_answer", "fresh"):
            item.pop(key, None)
        _history(item, "user", "confirmed" if action == "use" else "changed", how)
    elif action == "edit":
        for key, limit in (("answer", 1000), ("question", 500), ("why", 300)):
            if key in payload:
                text = str(payload.get(key) or "").strip()[:limit]
                if key != "why" and not text:
                    raise DecisionRefused("invalid", f"The {key} can't be empty.")
                item[key] = text
        if item.get("remember_as") == "record" and any(k in payload for k in RECORD_KEYS):
            item["record"] = _record_payload(payload, item.get("record") or {})
        item.pop("edited_outside", None)
        _history(item, "user", "edited", _clean(payload.get("note"), 300) or how)
    elif action == "reject":
        if item["status"] in {"superseded", "rejected"}:
            raise DecisionRefused("invalid", "This decision is already closed.")
        item["status"] = "rejected"
        _history(item, "user", "rejected", _clean(payload.get("reason"), 300) or how)
    elif action == "reopen":
        if item["status"] not in IN_FORCE:
            raise DecisionRefused("invalid", "Only a decision in force can be asked again.")
        waiting = _pending(book, item.get("project"), item["topic"])
        if waiting is not None:
            raise DecisionRefused(
                "invalid", f"This is already waiting for your answer as {waiting['id']}."
            )
        asked = _ask_again(book, item, offer=offer, how=how)
        _history(item, "user", "reopened", f"{asked['id']} waits for your answer")
    elif action == "forget" and written(item):
        # Retiring commits a change to the record's file on the landing branch, so it takes
        # its own action: a Forget never makes a commit the user didn't ask for.
        raise DecisionRefused(
            "invalid",
            f"{item['id']} has a record file in the repository "
            f"({item.get('record_path') or 'its file'}), so forgetting it wouldn't stick: the "
            "file would bring it back. Retire it instead: it stays turned down, and Raticode "
            "rewrites the file with status: rejected and commits that on the landing branch.",
        )
    elif action == "retire":
        # A record whose file is in the repository can't be deleted: the next sync would
        # read the file back in as a new decision. It stays, closed, and its file says so.
        if not written(item):
            raise DecisionRefused(
                "invalid",
                "Only a decision record whose file is in the repository is retired; "
                "forget this one instead.",
            )
        if item["status"] in {"superseded", "rejected"}:
            raise DecisionRefused(
                "invalid",
                "This record is already closed. Its file stays in the repository, and "
                "workers no longer get it.",
            )
        item.update(status="rejected", retired_at=stamp)
        _history(item, "user", "retired", _clean(payload.get("note"), 300) or how)
    elif action == "forget":
        book["items"] = [i for i in book["items"] if i["id"] != item["id"]]
        for other in book["items"]:
            if other.get("supersedes") == item["id"]:
                other.pop("supersedes", None)
                # An Ask again card no longer has a current answer to keep.
                for key in ("asked_again", "current_answer", "reopened"):
                    other.pop(key, None)
            if other.get("superseded_by") == item["id"]:
                other.pop("superseded_by", None)
        _promote(book, item.get("project"))
        return _save(db, store, book)
    else:
        raise DecisionRefused(
            "invalid",
            "Choose use, change, edit, replace, reject, reopen, forget, retire, add, "
            "confirm_edit or cancel_edit.",
        )
    if action in {"use", "change", "edit"}:
        _fresh_count_for_new_answer(item, answer_before)
    item["updated_at"] = stamp
    if action in {"use", "change", "reject", "retire", "forget"}:
        # A settled request makes room: the project's oldest queued one gets its card.
        _promote(book, item.get("project"))
    return _save(db, store, book)


def _fresh_count_for_new_answer(item: dict[str, Any], before: str) -> None:
    """A changed answer is a new command: its run count starts again.

    Failures of the old command say nothing about the new one, so they can't reopen it,
    and a last run of a different command leaves the Decisions list, which then shows only
    a run of the command as it reads now.
    """
    answer = str(item.get("answer") or "").strip()
    if answer == before:
        return
    item["failures"] = 0
    last = item.get("last_check")
    if isinstance(last, dict) and str(last.get("command") or "").strip() != answer:
        item.pop("last_check", None)


RECORD_KEYS = ("context", "options", "consequences", "check", "applies_to", "forbids", "signals")
RECORD_LISTS = ("applies_to", "forbids", "signals")


def written(item: dict[str, Any]) -> bool:
    """Whether a decision is a record whose file Raticode wrote into the repository.

    Such a decision is retired instead of forgotten: deleting it would let the next sync
    read its file back in as a new decision.
    """
    return (
        item.get("remember_as") == "record"
        and bool(item.get("project"))
        and bool(item.get("record_path"))
    )


def _record_payload(payload: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """A record's parts from a change: the given keys replace the current ones."""
    merged = {**current, **{k: payload[k] for k in RECORD_KEYS if k in payload}}
    try:
        return RecordFields.model_validate(merged).model_dump()
    except ValueError as exc:
        raise DecisionRefused("invalid", "A record part is too long.") from exc


def _supersede(old: dict[str, Any], new: dict[str, Any], *, single: bool = True) -> None:
    old["status"] = "superseded"
    old["superseded_by"] = new["id"]
    old["updated_at"] = now()
    _history(old, "user", "superseded", new["id"])
    if single:
        new["supersedes"] = old["id"]


def _ask_again(
    book: dict[str, Any],
    current: dict[str, Any],
    *,
    offer: DecisionRequest | None,
    how: str,
) -> dict[str, Any]:
    """File the user's Ask again as a new request that would replace ``current``.

    ``current`` stays in force, as with a staleness trigger in ``file_request``: workers
    keep getting it and Raticode keeps running its command until the user answers. The
    request counts toward the project's waiting cards and is ``queued`` when they're full.
    ``offer`` is a fresh scan's checked request for the same topic; without one the card
    has no recommendation and shows the answer in force as the current answer.
    """
    project = current.get("project")
    shown = sum(
        1 for i in book["items"] if i.get("project") == project and i["status"] == "pending"
    )
    status: Status = "queued" if shown >= MAX_PENDING_PER_PROJECT else "pending"
    stamp = now()
    item: dict[str, Any] = {
        "id": f"D-{book['next_number']}",
        "project": project,
        "topic": current["topic"],
        "question": current["question"],
        "answer": "",
        "recommended": "",
        "why": "",
        "evidence": [],
        "alternatives": [],
        "remember_as": current.get("remember_as", "fact"),
        **(
            {"record": dict(current.get("record") or {})}
            if current.get("remember_as") == "record"
            else {}
        ),
        "reversible": current.get("reversible", True),
        "tried": "",
        "status": status,
        "decided_by": None,
        "decided_at": None,
        "source": {"via": how},
        "asked_by": [],
        "evidence_hashes": {},
        "failures": 0,
        "created_at": stamp,
        "updated_at": stamp,
        "supersedes": current["id"],
        "reopened": ASKED_AGAIN,
        "asked_again": True,
        "current_answer": str(current.get("answer") or "")[:1000],
    }
    recommendation = offer.recommendation if offer is not None else None
    if offer is not None and recommendation is not None and offer.topic == current["topic"]:
        evidence = [e.model_dump() for e in recommendation.evidence]
        item.update(
            answer=recommendation.answer.strip(),
            recommended=recommendation.answer.strip(),
            why=_clean(recommendation.why, 300),
            evidence=evidence,
            alternatives=[a.model_dump() for a in offer.alternatives],
            evidence_hashes=_evidence_hashes(evidence, project),
            fresh=True,
        )
    book["next_number"] += 1
    _history(item, "user", "requested" if status == "pending" else "queued", ASKED_AGAIN)
    book["items"].append(item)
    return item


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:60] or "decision"


def _topic(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9._:/-]+", "-", text.strip().casefold()).strip("-")[:80]
    return cleaned if TOPIC.match(cleaned) else _slug(text)


def note_check(
    db: sqlite3.Connection,
    store: Any,
    identity: str,
    *,
    passed: bool,
    counts: bool = True,
    result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Count a run of a decided command; two failures in a row open its topic again.

    Raticode records this itself after running the command, so it isn't Rem changing a
    decision: only the failure count and the last result move, and the next request on
    the topic gets a card. ``counts`` is False for a failure that says nothing about the
    command itself, such as tests the work under check broke. ``result`` is kept as
    ``last_check`` for the Decisions list.
    """
    book = read(db, store)
    item: dict[str, Any] | None = next((i for i in book["items"] if i["id"] == identity), None)
    if item is None or item["status"] not in IN_FORCE:
        return None
    if passed:
        item["failures"] = 0
    elif counts:
        item["failures"] = int(item.get("failures") or 0) + 1
    if result is not None:
        item["last_check"] = {
            k: v for k, v in result.items() if k not in {"output", "first_output"}
        }
    _save(db, store, book)
    return item


def may_run(item: dict[str, Any] | None, *, auto: bool) -> bool:
    """Whether Raticode may run this decided command unattended.

    The user's own (confirmed) commands always; Rem's provisional ones only while
    self-work's auto mode is on, which is when the user lets Rem decide for them.
    """
    if item is None or not str(item.get("answer") or "").strip():
        return False
    if item["status"] == "confirmed":
        return True
    return item["status"] == "provisional" and auto


def recheck(
    db: sqlite3.Connection,
    store: Any,
    identity: str,
    evidence: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Raticode found the same answer again in changed files: keep it, with fresh evidence.

    Only the evidence and its file hashes move; the answer, status and who decided stay.
    Without this, every edit to a cited file (an AGENTS.md change, say) would ask the
    user to confirm an answer nobody changed.
    """
    book = read(db, store)
    item: dict[str, Any] | None = next((i for i in book["items"] if i["id"] == identity), None)
    if item is None or item["status"] not in IN_FORCE:
        return None
    if evidence:
        item["evidence"] = evidence[:5]
    item["evidence_hashes"] = _evidence_hashes(item["evidence"], item.get("project"))
    item["updated_at"] = now()
    _history(item, "raticode", "rechecked", "found the same answer in the project's files")
    _save(db, store, book)
    return item


def carry_over(
    db: sqlite3.Connection,
    store: Any,
    request: DecisionRequest,
    *,
    project: str,
    note: str,
) -> dict[str, Any] | None:
    """Record, as confirmed, what Raticode already did for a project before decisions existed.

    Used once per project and topic when a built-in behavior becomes a decision (landing
    on dev, for example), so nothing changes for the user. Does nothing when the topic
    already has any entry, so a decision the user forgot or turned down stays that way.
    """
    project_dir = project_key(project)
    book = read(db, store)
    if any(i["topic"] == request.topic and i.get("project") == project_dir for i in book["items"]):
        return None
    item = _new_item(
        book,
        request,
        project_dir,
        "confirmed",
        actor="user",
        source={"via": "carried over"},
    )
    item["history"] = [{"at": now(), "actor": "raticode", "action": "carried over", "note": note}]
    _save(db, store, book)
    return item


def topic_items(book: dict[str, Any], project: str | None, topic: str) -> list[dict[str, Any]]:
    """Every entry for (project, topic), whatever its status."""
    key = project_key(project)
    return [i for i in book["items"] if i["topic"] == topic and i.get("project") == key]


def answer_from_chat(
    db: sqlite3.Connection, store: Any, answer: DecisionAnswer, *, message: str
) -> tuple[dict[str, Any] | None, str]:
    """Apply the user's answer given in Rem's chat; returns the item and a sentence.

    Only a request still waiting for the user can be answered here, and only with words
    that are in ``message``, the user's own message. A decision already confirmed changes
    through an edit card the user presses OK on (``propose_edit``), never from a chat answer.
    """
    quoted = _words(answer.said)
    if not quoted or quoted not in _words(message):
        return None, "I didn't record that decision: your message doesn't say it."
    waiting = next((i for i in read(db, store)["items"] if i["id"] == answer.id), None)
    if waiting is not None and waiting["status"] not in OPEN | {"queued"}:
        return None, (
            f"I didn't record that decision: {waiting['id']} isn't waiting for an answer. "
            "Ask me to change it and press OK on the card."
        )
    payload = {
        "id": answer.id,
        "action": {"use": "use", "change": "change", "reject": "reject"}[answer.action],
        "answer": answer.answer,
        "reason": answer.said,
    }
    try:
        book = change(db, store, payload, actor="user", source=f"chat: {answer.said[:200]}")
    except DecisionRefused as exc:
        return None, f"I didn't record that decision: {exc}"
    item = next((i for i in book["items"] if i["id"] == answer.id), None)
    if item is None:
        return None, "I didn't record that decision: it's no longer listed."
    if answer.action == "reject":
        return item, f"Turned down {item['id']}: {item['question']}"
    return item, f"Recorded {item['id']}: {item['answer']}"


# Who asked a waiting question that no run asked: by ``source["via"]``.
ASKED_BY = {"chat": "Rem's chat", "plan": "Rem's chat", "scan": "Project scan"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _run_entry(run_id: str, run: dict[str, Any]) -> dict[str, Any]:
    """A run as the asked line names it: its ticket key, else its task's title."""
    tickets = run.get("tickets") or []
    first = tickets[0] if tickets and isinstance(tickets[0], dict) else {}
    ticket = str(first.get("key") or "")[:32]
    title = _clean((run.get("recipe") or {}).get("title"), 160)
    label = ticket or ("Self-work" if run.get("self_work") else "") or title or "A task"
    return {
        "run_id": run_id,
        "task_id": str(run.get("task_id") or ""),
        "ticket": ticket,
        "task_title": title,
        "label": label,
    }


def blocked_on(run: dict[str, Any], identity: str) -> bool:
    """Whether ``run`` is stopped now for the user's answer to decision ``identity``."""
    asked = run.get("decision_request") or {}
    return (
        run.get("state") == "blocked"
        and bool(run.get("needs_user"))
        and isinstance(asked, dict)
        and asked.get("id") == identity
    )


def asked(item: dict[str, Any], runs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Who asked a waiting question, when, and which of its runs wait for it now.

    ``runs`` holds the runs in ``item["asked_by"]`` by ID; one that isn't there (deleted)
    is left out. A run's question names its ticket key and task title (``source``'s
    ticket, else the run's first ticket); otherwise Rem's chat, a project scan, the user's
    own Ask again or Rem. ``waiting`` lists the runs blocked on this question now and
    ``went_on`` the ones that went on without it, such as an auto-mode run. ``opens`` is
    the task whose sheet the line opens: the asking task's, else a listed run's.
    """
    source = item.get("source") or {}
    listed = [
        _run_entry(run_id, runs[run_id])
        for run_id in item.get("asked_by") or []
        if isinstance(runs.get(run_id), dict)
    ]
    waiting = [e for e in listed if blocked_on(runs[e["run_id"]], item["id"])]
    went_on = [e for e in listed if e not in waiting]
    by, label, ticket, task_id, title = "rem", "Rem", "", "", ""
    via = source.get("via")
    if item.get("asked_again"):
        by, label = "you", "you"
    elif via in ASKED_BY:
        by, label = str(via), ASKED_BY[str(via)]
    elif source.get("run_id") or source.get("task_id"):
        mine = next((e for e in listed if e["run_id"] == source.get("run_id")), None)
        by = "run"
        ticket = str(source.get("ticket") or (mine or {}).get("ticket") or "")[:32]
        task_id = str(source.get("task_id") or (mine or {}).get("task_id") or "")
        title = (mine or {}).get("task_title") or ""
        label = ticket or (mine or {}).get("label") or "A task"
    opens = task_id or next((e["task_id"] for e in waiting + went_on if e["task_id"]), "")
    return {
        "by": by,
        "label": label,
        "ticket": ticket,
        "task_id": task_id,
        "task_title": title,
        "at": item.get("created_at") or "",
        "waiting": waiting,
        "went_on": went_on,
        "opens": opens,
    }


def short_day(stamp: Any) -> str:
    """``21 Nov`` for an ISO time, on this computer's calendar; "" when it can't be read."""
    try:
        when = datetime.fromisoformat(str(stamp))
    except ValueError:
        return ""
    if when.tzinfo is not None:
        when = when.astimezone()
    return f"{when.day} {MONTHS[when.month - 1]}"


def _names(entries: list[dict[str, Any]]) -> tuple[str, bool]:
    """The runs' labels as a sentence subject, and whether it is plural."""
    labels = list(dict.fromkeys(e["label"] for e in entries))
    if len(labels) == 1:
        return labels[0], False
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}", True
    return f"{labels[0]} and {len(labels) - 1} more", True


def asked_line(block: dict[str, Any] | None) -> str:
    """``Asked by REM-249 · 21 Nov · REM-249 waits for this``, as every client says it."""
    if not block:
        return ""
    parts = [f"Asked by {block.get('label') or 'Rem'}"]
    day = short_day(block.get("at"))
    if day:
        parts.append(day)
    if block.get("waiting"):
        names, plural = _names(block["waiting"])
        parts.append(f"{names} {'wait' if plural else 'waits'} for this")
    elif block.get("went_on"):
        names, _ = _names(block["went_on"])
        parts.append(f"{names} went on without it")
    return " · ".join(parts)


def view(
    book: dict[str, Any],
    project: str | None = None,
    runs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """What clients show: waiting first, then decisions in force, then closed ones.

    Each pending or queued item gets ``asked`` (``asked()``), read from ``runs``: the runs
    its ``asked_by`` names, by ID.
    """
    items = book["items"]
    if project:
        key = project_key(project)
        items = [i for i in items if i.get("project") == key]
    order = {
        "pending": 0,
        "queued": 1,
        "provisional": 2,
        "confirmed": 3,
        "superseded": 4,
        "rejected": 5,
    }
    listed = sorted(
        items,
        key=lambda i: (order.get(i["status"], 9), str(i.get("updated_at") or "")),
    )
    # Newest first inside each group.
    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in listed:
        grouped.setdefault(order.get(item["status"], 9), []).append(item)
    ordered = [
        {**i, "asked": asked(i, runs or {})} if i["status"] in {"pending", "queued"} else i
        for k in sorted(grouped)
        for i in reversed(grouped[k])
    ]
    key = project_key(project) if project else None
    edits = [
        edit_card(e)
        for e in book.get("edits") or []
        if e.get("status") == "waiting" and (not project or e.get("project") == key)
    ]
    plans = [
        {
            "id": plan["id"],
            "title": plan.get("title") or "",
            "goal": plan.get("goal") or "",
            "project": plan.get("project"),
            "status": plan.get("status") or "asking",
            "questions": list(plan.get("questions") or []),
            **plan_progress(book, plan),
        }
        for plan in book.get("plans") or []
        if not project or plan.get("project") == key
    ]
    return {
        "revision": book["revision"],
        "updated_at": book.get("updated_at"),
        "items": ordered,
        # Changes the user asked for in chat, waiting for their OK, oldest first.
        "edits": edits,
        "edits_waiting": len(edits),
        # Plan with me: each plan's title and how many of its questions are answered.
        "plans": plans,
        "waiting": sum(1 for i in items if i["status"] == "pending"),
        # Requests past the cap, without a card until a waiting one is settled.
        "queued": sum(1 for i in items if i["status"] == "queued"),
        "provisional": sum(1 for i in items if i["status"] == "provisional"),
        "confirmed": sum(1 for i in items if i["status"] == "confirmed"),
    }


def first_sentence(text: str, limit: int = RECORD_SENTENCE_CHARS) -> str:
    """The opening sentence of ``text``, cut to ``limit`` characters."""
    flat = " ".join(str(text or "").split())
    sentence = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9`\"“(])", flat, maxsplit=1)[0]
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


def _left_out(count: int, folders: list[str]) -> dict[str, Any]:
    where = folders[0] if len(folders) == 1 else "each project's decision folder"
    if len(folders) > 1:
        where += " (" + ", ".join(folders) + ")"
    return {
        "left_out": count,
        "note": f"{count} more decision record{'s' if count != 1 else ''} in force "
        f"didn't fit here. They are in {where}: read the ones about what you change "
        "before changing it.",
    }


def for_prompt(book: dict[str, Any], projects: list[str], text: str = "") -> list[dict[str, Any]]:
    """Decisions in force for these projects (and those about the user), within a budget.

    A worker or Rem's model reads this short index so it uses recorded answers instead of
    asking, and keeps to the project's decision records. Plain facts (``branch.integration``,
    ``checks.fast``, ``decisions.folder`` and every other answer that isn't a record) come
    first and are never left out. Each record is one short entry: its ID, topic, the first
    sentence of its decision and its file, where the paths and full text are one read away.
    With more records than fit, those whose words or paths appear in ``text`` (the task)
    come first, then the newest, and a last entry says how many were left out and where
    they are. Reviewers get the full text of the records a change touches separately
    (``rem_decision_records.full_text``).
    """
    from gofer.ui import rem_decision_records as decision_records

    keys = {project_key(p) for p in projects if p} | {None}
    found = [i for i in book["items"] if i["status"] in IN_FORCE and i.get("project") in keys]
    words = set(re.findall(r"[a-z0-9]{4,}", text.casefold()))

    def relevant(item: dict[str, Any]) -> int:
        record = item.get("record") or {}
        about = " ".join([item.get("question") or "", *(record.get("applies_to") or [])])
        return len(words & set(re.findall(r"[a-z0-9]{4,}", about.casefold())))

    def flags(item: dict[str, Any]) -> dict[str, Any]:
        return {"provisional": True} if item["status"] == "provisional" else {}

    facts = sorted(
        (i for i in found if i.get("remember_as") != "record"),
        key=lambda i: (i.get("project") is None, str(i["topic"])),
    )
    records = [i for i in found if i.get("remember_as") == "record"]
    records.sort(key=lambda i: str(i.get("decided_at") or ""), reverse=True)
    records.sort(key=relevant, reverse=True)
    listed: list[dict[str, Any]] = [
        {
            "id": item["id"],
            "topic": item["topic"],
            "decision": item["answer"][:300],
            "question": item["question"][:200],
            **flags(item),
        }
        for item in facts
    ]
    used = sum(len(str(e)) for e in listed)
    entries = [
        {
            "id": item["id"],
            "topic": item["topic"],
            "decision": first_sentence(item["answer"]),
            **flags(item),
            **({"file": item["record_path"]} if item.get("record_path") else {}),
        }
        for item in records
    ]
    folders = sorted(
        {
            os.path.join(str(item["project"]), decision_records.folder_of(book, item["project"]))
            if len(keys) > 2
            else decision_records.folder_of(book, item["project"])
            for item in records
            if item.get("project")
        }
    )
    budget = PROMPT_CHARS - used
    if sum(len(str(e)) for e in entries) > budget:
        # Room for the closing entry, counted at its longest.
        budget -= len(str(_left_out(len(entries), folders)))
    kept = 0
    for entry in entries:
        size = len(str(entry))
        if size > budget:
            continue
        listed.append(entry)
        budget -= size
        kept += 1
    if kept < len(entries):
        listed.append(_left_out(len(entries) - kept, folders))
    return listed


def waiting_for_prompt(book: dict[str, Any]) -> list[dict[str, Any]]:
    """Requests waiting for the user, so Rem's chat can apply an answer the user gives."""
    return [
        {
            "id": i["id"],
            "question": i["question"][:200],
            "recommended": (i.get("recommended") or "")[:200],
            "project": i.get("project"),
            **({"plan": i["plan"]} if i.get("plan") else {}),
        }
        for i in book["items"]
        if i["status"] in OPEN
    ][:10]
