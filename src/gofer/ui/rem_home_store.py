"""Transactional task, conversation, thread, and execution authority for Rem home."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from collections.abc import Set as AbstractSet
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from gofer.ui import rem_task_after as after_tasks
from gofer.ui.rem_home_models import (
    ACTIVE,
    NEEDS_ATTENTION,
    Recipe,
    RemConflict,
    Schedule,
    now,
    worker_config,
)
from gofer.ui.rem_ticket_activity import SCHEMA as TICKET_TIMELINE_SCHEMA


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def with_author(message: dict[str, Any]) -> dict[str, Any]:
    """Name who wrote a user-role thread message: ``sender`` is "rem" or "user".

    Rem's prompts to a worker have always been saved with ``sender: "rem"``.
    Every other user-role message in a work thread was typed by the human, but
    older ones were saved without a sender, which left readers to infer it.
    """
    if message.get("role") != "user" or message.get("sender") in {"rem", "user"}:
        return message
    return {**message, "sender": "user"}


# ``paused`` is neither running nor finished: the run keeps its thread, provider session and
# worktree and holds no worker until someone resumes it (see ``request_pause``).
PAUSED = "paused"
RUN_STATES = ACTIVE | {"succeeded", "blocked", "cancelled", "failed", "outcome_unknown", PAUSED}
# What a paused run's worker is told when it picks up again in the same thread.
RESUME_TEXT = (
    "This task was paused and is resuming now in this same thread. The pause stopped your "
    "last turn partway through, so first check what you already did (files, commits, commands "
    "and saved reports) and don't repeat finished actions. Then continue the original task."
)
# Newest run first. Saving a run replaces its row, which gives it a new rowid,
# so rowid order tracks the last update rather than when the run started.
NEWEST_RUN = "json_extract(body, '$.created_at') DESC, rowid DESC"
# Run fields only workers, reviewers and Rem's own turns read. Task lists that the
# desktop and phone poll leave them out: traces alone run to hundreds of kilobytes
# per run. SQLite drops them before Python has to parse them.
RUN_INTERNALS = (
    "worker_traces",
    "review_traces",
    "config",
    "recipe",
    "worker_result",
    "review_verdict",
    "previous_success",
)
SUMMARY_RUN = "json_remove(body, " + ", ".join(f"'$.{key}'" for key in RUN_INTERNALS) + ")"
# A finished one-time task leaves task lists, then is removed this long after its run finished.
RETENTION = timedelta(days=30)


def _instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def retention(task: dict[str, Any]) -> dict[str, Any]:
    """Whether a task view belongs in task lists, and when cleanup removes it.

    Only a one-time task whose newest run is over, and that needs nothing from
    the user, is finished. Recurring, scheduled and running tasks stay listed
    and are never removed by cleanup.
    """
    run = task.get("last_run") or {}
    finished_at = run.get("finished_at") or ""
    finished = _instant(finished_at)
    done = bool(
        (task.get("schedule") or {}).get("kind", "once") == "once"
        and not task.get("next_occurrence_at")
        and not task.get("active_run")
        and finished
        and (
            run.get("state") in {"succeeded", "cancelled"}
            or (run.get("state") in NEEDS_ATTENTION and run.get("acknowledged") is True)
        )
    )
    return {
        "listed": not done,
        "finished_at": finished_at,
        "cleanup_at": (finished + RETENTION).isoformat() if done and finished else "",
    }


def labelled_criteria(criteria: Any, *sources: Any) -> list[dict[str, Any]]:
    """A run's check results, each with the description of the check it answers.

    Runs store only criterion IDs with their results; the words live in the run's
    recipe, which task lists leave out. ``sources`` are criterion lists to look the
    IDs up in, first match first: the run's recipe, an edit waiting to apply, then
    the task's current checks. A check found in none of them keeps no description.
    """
    words: dict[str, str] = {}
    for source in sources:
        for item in source if isinstance(source, list) else []:
            if isinstance(item, dict) and isinstance(item.get("description"), str):
                words.setdefault(str(item.get("id")), item["description"])
    labelled = []
    for item in criteria if isinstance(criteria, list) else []:
        if not isinstance(item, dict):
            continue
        description = words.get(str(item.get("id")))
        labelled.append({**item, "description": description} if description else item)
    return labelled


def duplicate_notices(messages: list[dict[str, Any]]) -> set[str]:
    """Legacy "Started <task>." notices that repeat a reply written with them.

    Earlier builds announced a follow-up's new run and then wrote Rem's reply with
    the same task card in the same moment, so the task showed up twice. The stored
    messages stay; only the redundant notice is left out of the conversation.
    """
    replies: dict[str, list[datetime]] = {}
    for message in messages:
        work = message.get("work") or {}
        written = _instant(message.get("created_at"))
        if work.get("run_id") and written and message.get("kind") != "task_update":
            if message.get("id") != f"queued-{work['run_id']}":
                replies.setdefault(str(work["run_id"]), []).append(written)
    duplicates = set()
    for message in messages:
        work = message.get("work") or {}
        run = str(work.get("run_id") or "")
        written = _instant(message.get("created_at"))
        if (
            message.get("id") == f"queued-{run}"
            and message.get("body") == f"Started {work.get('title')}."
            and written
            and any(abs(reply - written) < timedelta(seconds=5) for reply in replies.get(run, []))
        ):
            duplicates.add(str(message["id"]))
    return duplicates


# A phone's page of earlier chat history: at most this many messages, and this many
# before a search result it opens at.
MAX_PAGE = 400
LEAD_IN = 10

PREVIEW_LENGTH = 280
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKUP = re.compile(r"(?m)^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)|[*_`~]{1,3}")


def reference_preview(message: dict[str, Any] | None, identity: str) -> dict[str, Any]:
    """What a reply shows of the message it refers to: who wrote it, when, and its opening words.

    A message that is no longer in the chat is marked ``unavailable``. Files, phone cards and
    details stay with the original message; only their count and the task title are shown.
    """
    if not message:
        return {"id": identity, "unavailable": True}
    body = _MARKUP.sub("", _LINK.sub(r"\1", str(message.get("body") or "")))
    body = " ".join(body.split())
    if len(body) > PREVIEW_LENGTH:
        body = body[: PREVIEW_LENGTH - 1].rstrip() + "…"
    preview: dict[str, Any] = {
        "id": identity,
        "role": message.get("role") or "rem",
        "body": body,
        "created_at": message.get("created_at") or "",
    }
    if message.get("kind") == "task_update":
        preview["kind"] = "task_update"
    title = (message.get("work") or {}).get("title")
    if isinstance(title, str) and title:
        preview["title"] = title[:160]
    files = len(message.get("attachments") or []) + len(message.get("files") or [])
    if files:
        preview["files"] = files
    return preview


def with_references(
    messages: list[dict[str, Any]], known: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Add each reply's ``referenced`` preview, looked up among ``known`` messages by ID."""
    return [
        {**m, "referenced": reference_preview(known.get(m["refers_to"]), m["refers_to"])}
        if isinstance(m.get("refers_to"), str)
        else m
        for m in messages
    ]


def progress_step(run: dict[str, Any]) -> str:
    """Which of Plan, Work, Check and Report a run is on, or stopped at.

    The state alone can't say: a run goes back to ``queued`` whenever its work is
    already done but has to wait, such as a review retried after a provider
    failure, Retry review, a restart's recovery check or waiting for a proof
    event. Those runs are at Check, not Plan; only a run that never started is.
    Both apps draw their progress steps from this, so they agree.
    """
    state = run.get("state")
    if state == "succeeded":
        return "report"
    if state == PAUSED:
        # Where it was when it paused; it picks up there.
        return str((run.get("paused") or {}).get("step") or "work")
    if state == "queued":
        if run.get("resume_review"):
            return "check"
        return "work" if run.get("started_at") else "plan"
    if state in {"verifying", "repairing"}:
        return "check"
    if state in {"executing", "cancelling"}:
        return "work"
    return "check" if run.get("verification_thread_id") else "work"


def current_cards(db: sqlite3.Connection, messages: list[dict[str, Any]]) -> None:
    """Give each message's task card its run's current state, in place.

    A card is written with the run as it was then, so a "Started" card says
    ``queued``. The apps look the run up in the task list for its live state,
    but a run can be missing from it (a deleted or cleaned-up task, an older run
    of a repeating task), and the card then fell back to its first snapshot and
    showed a finished run as waiting to start. A card whose run is gone keeps
    what it had.
    """
    wanted = {
        str(work["run_id"])
        for message in messages
        if isinstance(work := message.get("work"), dict) and work.get("run_id")
    }
    if not wanted:
        return
    marks = ",".join("?" * len(wanted))
    current = {
        row[0]: json.loads(row[1])
        for row in db.execute(
            "SELECT id, json_object('state', state, 'needs_user', "
            "json_extract(body, '$.needs_user'), 'resume_review', "
            "json_extract(body, '$.resume_review'), 'started_at', "
            "json_extract(body, '$.started_at'), 'verification_thread_id', "
            "json_extract(body, '$.verification_thread_id'), 'paused', "
            f"json_extract(body, '$.paused')) FROM runs WHERE id IN ({marks})",
            sorted(wanted),
        )
    }
    for message in messages:
        work = message.get("work")
        run = current.get(str((work or {}).get("run_id") or ""))
        if run is None or not isinstance(work, dict):
            continue
        if isinstance(run.get("paused"), str):
            run["paused"] = json.loads(run["paused"])
        message["work"] = {
            **work,
            "state": run["state"],
            "needs_user": bool(run.get("needs_user")),
            "step": progress_step(run),
        }
        message["work"].pop("paused_by", None)
        if run["state"] == PAUSED:
            message["work"]["paused_by"] = paused_by(run)


def paused_by(run: dict[str, Any]) -> str:
    """Who paused a run: ``user``, or ``priority`` when self-work made way for the user's work."""
    return "priority" if (run.get("paused") or {}).get("by") == "priority" else "user"


def ran_at(task: dict[str, Any]) -> str:
    """When a task last ran: its newest run's start or finish, or its creation time."""
    times = [
        moment
        for run in (task.get("active_run"), task.get("last_run"))
        for key in ("created_at", "finished_at")
        if run and (moment := _instant(run.get(key)))
    ]
    created = _instant(task.get("created_at"))
    if not times:
        return created.astimezone(UTC).isoformat() if created else ""
    return max(times).astimezone(UTC).isoformat()


def begin_cycle(
    run: dict[str, Any],
    kind: Literal["work", "review", "recover"],
    *,
    config: dict[str, Any] | None = None,
    bump_epoch: bool = True,
) -> dict[str, Any]:
    """Requeue a run with one consistent reset of every per-attempt field.

    ``work`` starts a fresh worker turn, ``review`` rechecks the saved result,
    and ``recover`` inspects saved progress after a restart. Every requeue path
    uses this helper so stale budgets, flags or verdicts cannot leak into the
    next attempt. Callers add kind-specific inputs such as a follow-up message.
    """
    if config is not None:
        for key in ("projects", "brain"):
            run["config"][key] = config.get(key, [] if key == "projects" else {})
    run.update(
        state="queued",
        epoch=run["epoch"] + int(bump_epoch),
        finished_at=None,
        summary_short="",
        acknowledged=False,
        needs_user=False,
        user_question="",
        stop_reason=None,
        error_detail=None,
        unresolved_fingerprint=None,
        last_save_error=None,
        review_refresh_count=0,
        review_receipt_retry=False,
        review_format_retry=False,
        transient_retries=0,
        not_before=None,
        waiting_reason=None,
        waiting_for=None,
        proof_waits=0,
        # A worker's sleep (rem_home_sleep) belongs to one stretch of work; a follow-up,
        # edit, retry or recovery starts with no sleep pending and a fresh wait budget.
        pending_sleep=None,
        sleep=None,
    )
    run["criteria"] = [
        {"id": c["id"], "result": "unknown"} for c in run["recipe"]["acceptance_criteria"]
    ]
    # Saved reports stay hash-bound evidence for the next attempt, so a
    # follow-up, retried review or recovery doesn't demand the same report
    # again. A missing or changed file still fails validation, and saves that
    # landed after their grant was revoked stay superseded.
    for artifact in run.get("artifacts", []):
        if not artifact.get("superseded"):
            artifact["epoch"] = run["epoch"]
    if kind == "work":
        run.update(
            repair_count=0,
            review_feedback="",
            resume_review=False,
            recovery_inspection=False,
            phase_label=None,
        )
        for key in ("conversation_round", "conversation_follow_up"):
            run.pop(key, None)
    elif kind == "review":
        run.update(repair_count=0, resume_review=True, phase_label="Checking the work")
    else:
        run.update(
            resume_review=True,
            review_recovered=True,
            reset_review_session=True,
            phase_label="Checking saved progress",
        )
    return run


class RemHomeStore:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "rem-home.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions(task TEXT, revision INTEGER, body TEXT,
                    PRIMARY KEY(task, revision));
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, task TEXT NOT NULL,
                    occurrence TEXT UNIQUE NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS run_task ON runs(task);
                CREATE INDEX IF NOT EXISTS run_state ON runs(state);
                CREATE TABLE IF NOT EXISTS messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS thread_messages(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread TEXT, id TEXT UNIQUE, body TEXT);
                CREATE INDEX IF NOT EXISTS thread_message_thread ON thread_messages(thread, seq);
                CREATE INDEX IF NOT EXISTS message_work_run
                    ON messages(json_extract(body, '$.work.run_id'));
                CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY,
                    fingerprint TEXT, body TEXT);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT, body TEXT, at TEXT);
                CREATE TABLE IF NOT EXISTS lease(id INTEGER PRIMARY KEY CHECK(id=1),
                    owner TEXT, expires REAL);
                CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, run TEXT NOT NULL,
                    body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS kanban_tickets(id TEXT PRIMARY KEY,
                    status TEXT NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS kanban_status ON kanban_tickets(status);
                CREATE TABLE IF NOT EXISTS self_work_report(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL);
                PRAGMA user_version=1;
            """)
            # Each board ticket's timeline of status changes and comments.
            db.executescript(TICKET_TIMELINE_SCHEMA)
            if not self.setting(db, "conversation"):
                self.set_setting(db, "conversation", {"id": str(uuid4()), "turn_state": "idle"})
            self.restore_completed_threads(db)

    @contextmanager
    def connect(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            # Writers reserve the database up front so read-modify-write stays
            # atomic. Pure reads use a deferred snapshot and never block writers.
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with db:
                yield db
        finally:
            db.close()

    def read(self) -> AbstractContextManager[sqlite3.Connection]:
        """A read-only snapshot. Never write through it; use ``connect`` instead."""
        return self.connect(write=False)

    @staticmethod
    def setting(db: sqlite3.Connection, key: str) -> dict[str, Any]:
        row = db.execute("SELECT body FROM settings WHERE key=?", (key,)).fetchone()
        return cast(dict[str, Any], json.loads(row[0])) if row else {}

    @staticmethod
    def set_setting(db: sqlite3.Connection, key: str, value: dict[str, Any]) -> None:
        db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, encode(value)))

    @staticmethod
    def get(db: sqlite3.Connection, table: str, identity: str) -> dict[str, Any]:
        if table not in {"tasks", "runs", "threads"}:
            raise ValueError("Invalid table")
        row = db.execute(f"SELECT body FROM {table} WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        return cast(dict[str, Any], json.loads(row[0]))

    @staticmethod
    def put(db: sqlite3.Connection, table: str, item: dict[str, Any]) -> None:
        if table == "runs":
            if item["state"] not in RUN_STATES:
                raise ValueError(f"Unknown run state {item['state']!r}")
            db.execute(
                "INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?)",
                (item["id"], item["task_id"], item["occurrence"], item["state"], encode(item)),
            )
        elif table in {"tasks", "threads"}:
            db.execute(f"INSERT OR REPLACE INTO {table} VALUES(?,?)", (item["id"], encode(item)))
        else:
            raise ValueError("Invalid table")

    @staticmethod
    def event(db: sqlite3.Connection, kind: str, payload: dict[str, Any]) -> None:
        db.execute("INSERT INTO events(kind,body,at) VALUES(?,?,?)", (kind, encode(payload), now()))

    def command(
        self,
        key: str,
        payload: dict[str, Any],
        operation: Callable[[sqlite3.Connection], dict[str, Any]],
        *,
        db: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """Run an idempotent command, optionally inside a caller's transaction.

        Passing ``db`` lets several commands commit or roll back together.
        """
        identity = payload.get("request_id")
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 128:
            raise ValueError("Supply a request_id of at most 128 characters")
        fingerprint = hashlib.sha256(encode([key, payload]).encode()).hexdigest()

        def run(db: sqlite3.Connection) -> dict[str, Any]:
            if self.setting(db, "conversation").get("turn_state") == "resetting":
                raise RemConflict("Rem is clearing its history; try again shortly")
            receipt = db.execute("SELECT * FROM receipts WHERE id=?", (identity,)).fetchone()
            if receipt:
                if receipt["fingerprint"] != fingerprint:
                    raise RemConflict("This request_id was already used for another command")
                return cast(dict[str, Any], json.loads(receipt["body"]))
            result = operation(db)
            db.execute(
                "INSERT INTO receipts VALUES(?,?,?)", (identity, fingerprint, encode(result))
            )
            self.event(db, key, {"request_id": identity})
            return result

        if db is not None:
            return run(db)
        with self.connect() as connection:
            return run(connection)

    def message(
        self,
        db: sqlite3.Connection,
        body: str,
        *,
        identity: str | None = None,
        role: str = "rem",
        **extra: Any,
    ) -> dict[str, Any]:
        item = {
            "id": identity or str(uuid4()),
            "role": role,
            "body": body,
            "created_at": now(),
            **extra,
        }
        inserted = db.execute(
            "INSERT OR IGNORE INTO messages(id,body) VALUES(?,?)", (item["id"], encode(item))
        )
        if inserted.rowcount:
            self.event(db, "conversation.message", {"message_id": item["id"], "role": role})
        return item

    def add_task(
        self, db: sqlite3.Connection, recipe: Recipe, *, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        task = {
            **recipe.model_dump(),
            "id": str(uuid4()),
            "revision": 1,
            "state": "enabled",
            "enabled": True,
            "created_at": now(),
            "schedule_revision": 1,
        }
        if context:
            task["delegation_context"] = context
        clock = datetime.now(UTC)
        task["next_occurrence_at"] = recipe.schedule.next(clock, initial=True)
        if recipe.after:
            task["prerequisites"] = after_tasks.resolve(db, task["id"], recipe.after, clock)
        waits = after_tasks.conditions(db, task, clock)
        if waits:
            task["waits_for"] = waits
        if after_tasks.pending(task):
            # The gate in due() starts it; a requested time only delays that further.
            task["next_occurrence_at"] = None
        self.put(db, "tasks", task)
        db.execute("INSERT INTO revisions VALUES(?,?,?)", (task["id"], 1, encode(task)))
        return task

    @staticmethod
    def starts_now(recipe: Recipe) -> bool:
        """A one-time task for now with nothing to wait for starts as it is created."""
        return (
            recipe.schedule.kind == "once"
            and not recipe.schedule.at
            and not recipe.after
            and not recipe.after_running
            and not recipe.after_self_work
        )

    def add_run(
        self,
        db: sqlite3.Connection,
        task: dict[str, Any],
        occurrence: str,
        config: dict[str, Any],
        *,
        acknowledgement: str | None = None,
        thread_id: str | None = None,
        scheduled_for: str | None = None,
        announce: bool = True,
        message_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Queue a task's next run; ``announce=False`` when the caller writes its own card."""
        # A paused run is still this task's current run: nothing new starts beside it.
        active = db.execute(
            "SELECT body FROM runs WHERE task=? AND state IN ('queued','executing',"
            "'verifying','repairing','cancelling','paused')",
            (task["id"],),
        ).fetchone()
        if active:
            return cast(dict[str, Any], json.loads(active[0]))
        uncertain = db.execute(
            "SELECT body FROM runs WHERE task=? AND state='outcome_unknown' "
            "ORDER BY " + NEWEST_RUN + " LIMIT 1",
            (task["id"],),
        ).fetchone()
        if uncertain and not occurrence.startswith("manual:"):
            return cast(dict[str, Any], json.loads(uncertain[0]))
        known = db.execute("SELECT body FROM runs WHERE occurrence=?", (occurrence,)).fetchone()
        if known:
            return cast(dict[str, Any], json.loads(known[0]))
        if not thread_id:
            previous = db.execute(
                "SELECT body FROM runs WHERE task=? ORDER BY " + NEWEST_RUN + " LIMIT 1",
                (task["id"],),
            ).fetchone()
            if previous:
                saved = json.loads(previous[0])
                if saved["recipe"].get("project_dir") == task.get("project_dir"):
                    thread_id = saved["thread_id"]
        run = {
            "id": str(uuid4()),
            "task_id": task["id"],
            "occurrence": occurrence,
            "state": "queued",
            "epoch": 0,
            "recipe": task,
            "config": config,
            "created_at": now(),
            "thread_id": thread_id or str(uuid4()),
            "repair_count": 0,
            "criteria": [{"id": c["id"], "result": "unknown"} for c in task["acceptance_criteria"]],
            "scheduled_for": scheduled_for,
            "previous_success": self.previous_success(db, task["id"]),
        }
        self.put(db, "runs", run)
        self.provision_thread(db, run, "execute")
        if announce:
            self.message(
                db,
                acknowledgement or f"Started {task['title']}.",
                identity=f"queued-{run['id']}",
                work=self.work(run),
                **(message_extra or {}),
            )
        self.event(db, "run.queued", {"run_id": run["id"]})
        return run

    @staticmethod
    def previous_success(db: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
        """What the task's last accepted run covered, so the next one can pick up after it.

        Failed or stopped runs don't count: the next run's window starts at the
        last accepted occurrence, so nothing falls into a gap.
        """
        row = db.execute(
            "SELECT body FROM runs WHERE task=? AND state='succeeded' ORDER BY "
            + NEWEST_RUN
            + " LIMIT 1",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        run = json.loads(row[0])
        return {
            "run_id": run["id"],
            "for": run.get("scheduled_for") or run["created_at"],
            "finished_at": run.get("finished_at"),
            "summary": run.get("summary_short") or "",
            "reports": [
                a["path"]
                for a in run.get("artifacts", [])
                if a.get("epoch") == run["epoch"] and not a.get("superseded") and a.get("path")
            ],
        }

    @staticmethod
    def work(run: dict[str, Any]) -> dict[str, Any]:
        """A message's task card. It keeps the run's tickets so history outlives the task."""
        card = {
            "task_id": run["task_id"],
            "run_id": run["id"],
            "thread_id": run["thread_id"],
            "title": run["recipe"]["title"],
            "type": run["recipe"]["type"],
            "state": run["state"],
            "needs_user": run.get("needs_user", False),
            "step": progress_step(run),
        }
        if run.get("tickets"):
            card["tickets"] = run["tickets"]
        if run["state"] == PAUSED:
            card["paused_by"] = paused_by(run)
        # Work Rem started on its own (or for a ticket moved to In progress). Its phone
        # alerts come from the self-work report, so chat updates don't alert twice.
        if run.get("self_work"):
            card["self_work"] = str(run["self_work"].get("kind") or "ticket")
        return card

    def provision_thread(self, db: sqlite3.Connection, run: dict[str, Any], role: str) -> str:
        identity = run["thread_id"]
        recipe = run["recipe"]
        config = worker_config(run["config"], recipe)
        project: dict[str, Any] = next(
            (p for p in config.get("projects", []) if p["root"] == recipe.get("project_dir")), {}
        )
        existing = db.execute("SELECT body FROM threads WHERE id=?", (identity,)).fetchone()
        previous = json.loads(existing[0]) if existing else {}
        thread = {
            **previous,
            "id": identity,
            "title": recipe["title"],
            "projectRoot": recipe.get("project_dir") or "",
            "scopeMode": "project" if recipe.get("project_dir") else "global",
            "projectName": project.get("name")
            or (Path(recipe["project_dir"]).name if recipe.get("project_dir") else "Rem task"),
            "provider": config.get("provider", "codex"),
            "model": config.get("model", "cli-default"),
            "effort": config.get("effort", ""),
            "resources": config.get("resources", {}),
            "managedRunId": run["id"],
            "managedRole": "execute",
            "updatedAt": now(),
            "createdAt": previous.get("createdAt") or now(),
            "archived": False,
        }
        thread.pop("archivedAt", None)
        self.put(db, "threads", thread)
        return str(identity)

    def restore_completed_threads(self, db: sqlite3.Connection) -> None:
        """Undo the old policy that archived task threads when their run succeeded.

        Finished task threads now stay in the thread list as unread until the user
        opens them. Only that policy set ``archivedAt``, so threads the user archived
        themselves on the desktop are not affected; the desktop owns those.
        """
        rows = db.execute(
            "SELECT body FROM threads WHERE json_extract(body, '$.archivedAt') IS NOT NULL"
        ).fetchall()
        for row in rows:
            thread = json.loads(row[0])
            thread.pop("archivedAt", None)
            thread["archived"] = False
            self.put(db, "threads", thread)

    def task_view(
        self, db: sqlite3.Connection, task: dict[str, Any], *, summary: bool = False
    ) -> dict[str, Any]:
        runs = []
        for body, checked in db.execute(
            f"SELECT {SUMMARY_RUN if summary else 'body'}, "
            "json_extract(body, '$.recipe.acceptance_criteria') FROM runs WHERE task=? "
            "ORDER BY " + NEWEST_RUN + " LIMIT 20",
            (task["id"],),
        ):
            run = json.loads(body)
            if "criteria" in run:
                run["criteria"] = labelled_criteria(
                    run["criteria"],
                    json.loads(checked) if checked else None,
                    (run.get("pending_recipe") or {}).get("acceptance_criteria"),
                    task.get("acceptance_criteria"),
                )
            run["step"] = progress_step(run)
            runs.append(run)
        active = next((r for r in runs if r["state"] in ACTIVE), None)
        if runs and not active:
            # Retry review is offered only where retry_review would accept it.
            latest = runs[0]
            if summary and latest["state"] in {"blocked", "cancelled"}:
                # The check reads the worker result and recipe, which summaries leave out.
                latest = self.get(db, "runs", latest["id"])
            runs[0]["review_retryable"] = bool(
                self.interrupted_review(latest)
                and latest["recipe"].get("revision") == task.get("revision")
                and not task.get("deleted_at")
            )
        view = {
            **task,
            "active_run": active,
            "last_run": runs[0] if runs else None,
            "recent_runs": runs,
        }
        if after_tasks.pending(task) and not task.get("deleted_at"):
            # Waiting for other tasks: why, and where each of them stands.
            view["dependency"] = after_tasks.evaluate(db, task)
        return view

    def conversation(self) -> dict[str, Any]:
        with self.read() as db:
            messages = [
                json.loads(r["body"]) for r in db.execute("SELECT body FROM messages ORDER BY seq")
            ]
            for message in messages:
                # A failed reply's diagnostic (the decision that failed) stays in this
                # database for tracing; the apps and Rem's own prompts never need it.
                message.pop("diagnostic", None)
            hidden = duplicate_notices(messages)
            known = {m["id"]: m for m in messages}
            # After duplicate_notices: it matches cards by their stored text, not state.
            current_cards(db, messages)
            conversation = self.setting(db, "conversation")
            return {
                # Waiting messages are listed below, once.
                "conversation": {k: v for k, v in conversation.items() if k != "waiting"},
                "messages": with_references([m for m in messages if m["id"] not in hidden], known),
                # Messages sent while Rem was replying. They join messages when Rem
                # starts on them, so Rem's history never holds one it hasn't reached.
                "waiting": with_references(
                    [
                        {**item, "role": "user", "request_id": item["id"], "waiting": True}
                        for item in conversation.get("waiting") or []
                    ],
                    known,
                ),
            }

    def messages_after(self, after: int, limit: int = 300) -> dict[str, Any]:
        """Conversation messages stored after sequence ``after``, oldest first, for phone alerts.

        ``seq`` never changes once a message is stored, so it is the phone's cursor; ``last``
        is the last position read. Legacy duplicate notices are left out, as in
        ``conversation()``. Also returns the latest question asked at or before ``after`` and,
        for task updates, the message that started each task, so the caller can tell which
        requests were its own.
        """
        with self.read() as db:
            latest = int(db.execute("SELECT COALESCE(MAX(seq), 0) FROM messages").fetchone()[0])
            rows = db.execute(
                "SELECT seq, body FROM messages WHERE seq > ? ORDER BY seq LIMIT ?", (after, limit)
            ).fetchall()
            asked = db.execute(
                "SELECT id FROM messages WHERE seq <= ? AND json_extract(body, '$.role') = 'user' "
                "ORDER BY seq DESC LIMIT 1",
                (after,),
            ).fetchone()
            messages = [{**json.loads(body), "seq": int(seq)} for seq, body in rows]
            last = messages[-1]["seq"] if messages else after
            hidden = duplicate_notices(messages)
            messages = [m for m in messages if m["id"] not in hidden]
            sources: dict[str, str | None] = {}
            for message in messages:
                task = (message.get("work") or {}).get("task_id")
                if message.get("kind") != "task_update" or not task or task in sources:
                    continue
                row = db.execute("SELECT body FROM tasks WHERE id=?", (task,)).fetchone()
                context = (json.loads(row[0]).get("delegation_context") or {}) if row else {}
                sources[task] = context.get("source_message_id")
        return {
            "latest": latest,
            "last": last,
            "messages": messages,
            "asked": asked[0] if asked else None,
            "sources": sources,
        }

    def search(self, query: Any, *, before: int | None = None, limit: int = 50) -> dict[str, Any]:
        """Messages in the whole saved chat matching ``query``; see ``rem_chat_search``."""
        from gofer.ui.rem_chat_search import search_messages

        with self.read() as db:
            return search_messages(db, query, before=before, limit=limit)

    def page(self, before: str, *, through: str | None = None, limit: int = 40) -> dict[str, Any]:
        """Messages stored just before message ``before``, oldest first, for a phone's history.

        With ``through``, the page reaches back to that message and ``LEAD_IN`` more, so a
        search result opens with what led up to it, up to ``MAX_PAGE`` messages. ``earlier``
        counts the messages still before the page. Legacy duplicate notices are left out,
        as in ``conversation()``, and replies carry their ``referenced`` preview.
        Raises ``KeyError`` for a message that is no longer stored, such as after a clear.
        """
        with self.read() as db:
            start = db.execute("SELECT seq FROM messages WHERE id=?", (before,)).fetchone()
            if start is None:
                raise KeyError(before)
            count = max(1, min(limit, MAX_PAGE))
            if through:
                target = db.execute("SELECT seq FROM messages WHERE id=?", (through,)).fetchone()
                if target is None:
                    raise KeyError(through)
                between = db.execute(
                    "SELECT COUNT(*) FROM messages WHERE seq >= ? AND seq < ?",
                    (target[0], start[0]),
                ).fetchone()[0]
                count = max(count, min(int(between) + LEAD_IN, MAX_PAGE))
            rows = db.execute(
                "SELECT seq, body FROM messages WHERE seq < ? ORDER BY seq DESC LIMIT ?",
                (start[0], count),
            ).fetchall()
            messages = [json.loads(body) for _, body in reversed(rows)]
            oldest = rows[-1][0] if rows else start[0]
            earlier = int(
                db.execute("SELECT COUNT(*) FROM messages WHERE seq < ?", (oldest,)).fetchone()[0]
            )
            wanted = {
                m["refers_to"]
                for m in messages
                if isinstance(m.get("refers_to"), str)
                and m["refers_to"] not in {n.get("id") for n in messages}
            }
            known = {m["id"]: m for m in messages}
            for identity in wanted:
                row = db.execute("SELECT body FROM messages WHERE id=?", (identity,)).fetchone()
                if row:
                    known[identity] = json.loads(row[0])
            current_cards(db, messages)
        for message in messages:
            message.pop("diagnostic", None)
        hidden = duplicate_notices(messages)
        return {
            "messages": with_references([m for m in messages if m["id"] not in hidden], known),
            "earlier": earlier,
        }

    def transcript(self, identity: str, limit: int = 600) -> list[dict[str, Any]]:
        """A task thread's latest transcript entries, oldest first."""
        if not identity:
            return []
        with self.read() as db:
            rows = db.execute(
                "SELECT body FROM thread_messages WHERE thread=? ORDER BY seq DESC LIMIT ?",
                (identity, limit),
            ).fetchall()
        return [with_author(json.loads(row[0])) for row in reversed(rows)]

    def thread_ids(self) -> list[str]:
        with self.read() as db:
            return [row[0] for row in db.execute("SELECT id FROM threads")]

    def tasks(self, *, summary: bool = False) -> list[dict[str, Any]]:
        """Tasks with their recent runs; ``summary`` leaves out ``RUN_INTERNALS``."""
        with self.read() as db:
            return [
                self.task_view(db, task, summary=summary)
                for row in db.execute("SELECT body FROM tasks")
                if not (task := json.loads(row[0])).get("deleted_at")
            ]

    def cleanup(
        self,
        clock: datetime,
        *,
        busy_runs: AbstractSet[str] = frozenset(),
        busy_threads: AbstractSet[str] = frozenset(),
    ) -> list[str]:
        """Remove finished one-time tasks once their retention ends; return removed thread IDs.

        The task, its revisions, runs, work threads, transcripts, receipts and
        events go. Conversation messages keep their links to the task and thread.
        Runs or threads a worker still holds, and results not yet delivered, wait.
        """
        removed: list[str] = []
        with self.connect() as db:
            for row in db.execute("SELECT body FROM tasks").fetchall():
                task = json.loads(row[0])
                kept = retention(self.task_view(db, task))
                due = _instant(kept["cleanup_at"])
                if kept["listed"] or due is None or due > clock:
                    continue
                runs = [
                    json.loads(r[0])
                    for r in db.execute("SELECT body FROM runs WHERE task=?", (task["id"],))
                ]
                run_ids = [r["id"] for r in runs]
                marks = ",".join("?" * len(run_ids))
                threads = sorted(
                    {r["thread_id"] for r in runs}
                    | {
                        t[0]
                        for t in db.execute(
                            "SELECT id FROM threads WHERE json_extract(body, '$.managedRunId') "
                            f"IN ({marks})",
                            run_ids,
                        )
                    }
                )
                if (
                    any(r["state"] in ACTIVE for r in runs)
                    or not busy_runs.isdisjoint(run_ids)
                    or not busy_threads.isdisjoint(threads)
                    or db.execute(
                        f"SELECT 1 FROM outbox WHERE run IN ({marks})", run_ids
                    ).fetchone()
                ):
                    continue
                shared = ",".join("?" * len(threads))
                # Another task's run never reuses a thread, but never delete one that does.
                if db.execute(
                    f"SELECT 1 FROM runs WHERE task!=? AND json_extract(body, '$.thread_id') "
                    f"IN ({shared})",
                    (task["id"], *threads),
                ).fetchone():
                    continue
                db.execute(f"DELETE FROM thread_messages WHERE thread IN ({shared})", threads)
                db.execute(f"DELETE FROM threads WHERE id IN ({shared})", threads)
                # Chat cards read their run's state when served. The run is going, so
                # each card keeps its final state; otherwise it would fall back to the
                # "Started" snapshot and show finished work as waiting to start.
                for run in runs:
                    db.execute(
                        "UPDATE messages SET body=json_set(body, '$.work.state', ?, "
                        "'$.work.needs_user', json(?), '$.work.step', ?) "
                        "WHERE json_extract(body, '$.work.run_id')=?",
                        (
                            run["state"],
                            "true" if run.get("needs_user") else "false",
                            progress_step(run),
                            run["id"],
                        ),
                    )
                db.execute(f"DELETE FROM runs WHERE id IN ({marks})", run_ids)
                db.execute("DELETE FROM revisions WHERE task=?", (task["id"],))
                db.execute("DELETE FROM tasks WHERE id=?", (task["id"],))
                # Saved command results and activity entries copy the task's recipe and runs.
                for identity in [task["id"], *run_ids, *threads]:
                    db.execute("DELETE FROM receipts WHERE instr(body, ?) > 0", (identity,))
                    db.execute("DELETE FROM events WHERE instr(body, ?) > 0", (identity,))
                self.event(db, "task.cleaned", {"task_id": task["id"]})
                removed.extend(threads)
        return removed

    def mutate_task(
        self,
        identity: str,
        payload: dict[str, Any],
        action: str,
        *,
        context: dict[str, Any] | None = None,
        db: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        def operation(db: sqlite3.Connection) -> dict[str, Any]:
            task = self.get(db, "tasks", identity)
            if task.get("deleted_at"):
                raise KeyError(identity)
            config = self.setting(db, "config")
            if action == "run":
                previous = db.execute(
                    "SELECT body FROM runs WHERE task=? ORDER BY " + NEWEST_RUN + " LIMIT 1",
                    (identity,),
                ).fetchone()
                old_run = json.loads(previous[0]) if previous else {}
                run = (
                    # Run on a paused task picks the paused run back up in its thread.
                    self.resume_paused(db, old_run, config, by="user")
                    if old_run.get("state") == PAUSED
                    else self.queue_review(db, old_run, config)
                    if self.interrupted_review(old_run)
                    and old_run["recipe"].get("revision") == task["revision"]
                    else self.add_run(db, task, f"manual:{payload['request_id']}", config)
                )
                if after_tasks.pending(task):
                    # Starting it by hand settles the start condition for good.
                    after_tasks.release(task, "started_by_user", run["id"])
                    self.put(db, "tasks", task)
                    self.event(db, "task.after_released", {"task_id": identity})
                return {"task": self.task_view(db, task), "run": run}
            if action == "edit":
                if payload.get("expected_revision") != task["revision"]:
                    raise RemConflict("This task changed; reload before saving")
                allowed = set(Recipe.model_fields) | {
                    "request_id",
                    "expected_revision",
                    "apply_to",
                    "enabled",
                }
                if set(payload) - allowed:
                    raise ValueError("Unknown task fields")
                if payload.get("apply_to", "current_and_future") not in {
                    "current_and_future",
                    "future",
                }:
                    raise ValueError("Invalid apply_to")
                fields = {
                    k: payload.get(k, task.get(k))
                    for k in Recipe.model_fields
                    if k in payload or k in task
                }
                task.update(Recipe.model_validate(fields).model_dump())
                if "prompt" in payload:
                    # An edited goal replaces any follow-ups folded into the old one.
                    task.pop("base_prompt", None)
                    task.pop("follow_ups", None)
                if context:
                    task["delegation_context"] = context
                if "enabled" in payload:
                    if not isinstance(payload["enabled"], bool):
                        raise ValueError("enabled must be boolean")
                    task["enabled"] = payload["enabled"]
                    task["state"] = "enabled" if task["enabled"] else "paused"
                conditions = {"after", "after_running", "after_self_work"} & set(payload)
                if conditions:
                    if task.get("after_started"):
                        raise RemConflict(
                            "This task already started, so what it waits for is fixed"
                        )
                    if (
                        not after_tasks.pending(task)
                        and db.execute("SELECT 1 FROM runs WHERE task=?", (identity,)).fetchone()
                    ):
                        raise RemConflict("This task already ran, so it can't wait for another")
                    clock = datetime.now(UTC)
                    if "after" in payload:
                        task["prerequisites"] = after_tasks.resolve(
                            db, identity, task["after"], clock
                        )
                    if {"after_running", "after_self_work"} & conditions:
                        # Re-pinned now: "what's running" means what runs at this change.
                        task.pop("waits_for", None)
                        waits = after_tasks.conditions(db, task, clock)
                        if waits:
                            task["waits_for"] = waits
                if (
                    "schedule" in payload
                    or payload.get("enabled") is True
                    or (conditions and not after_tasks.pending(task))
                ):
                    task["schedule_revision"] += 1
                    task["next_occurrence_at"] = Schedule.model_validate(task["schedule"]).next(
                        datetime.now(UTC), initial=True
                    )
                if after_tasks.pending(task):
                    task["next_occurrence_at"] = None
            elif action == "pause":
                if not isinstance(payload.get("cancel_current", False), bool):
                    raise ValueError("cancel_current must be boolean")
                task.update(enabled=False, state="paused")
            elif action == "delete":
                task.update(enabled=False, state="deleted", deleted_at=now())
            else:
                raise ValueError("Unknown task action")
            task["revision"] += 1
            self.put(db, "tasks", task)
            db.execute(
                "INSERT INTO revisions VALUES(?,?,?)", (identity, task["revision"], encode(task))
            )
            for row in db.execute("SELECT body FROM runs WHERE task=?", (identity,)).fetchall():
                run = json.loads(row[0])
                if run["state"] not in ACTIVE:
                    continue
                stop = action == "delete" or (action == "pause" and payload.get("cancel_current"))
                update = (
                    action == "edit"
                    and payload.get("apply_to") != "future"
                    and any(
                        key in payload
                        for key in (
                            "prompt",
                            "type",
                            "project_dir",
                            "acceptance_criteria",
                            "execution_mode",
                            "conversation_turns",
                        )
                    )
                )
                if stop or update:
                    run["epoch"] += 1
                    run["state"] = "cancelling" if stop else run["state"]
                    if update:
                        run["pending_recipe"] = task
                        run["criteria"] = [
                            {"id": c["id"], "result": "unknown"}
                            for c in task["acceptance_criteria"]
                        ]
                    self.put(db, "runs", run)
            return {"task": self.task_view(db, task)}

        return self.command(f"task.{action}:{identity}", payload, operation, db=db)

    @staticmethod
    def interrupted_review(run: dict[str, Any]) -> bool:
        return bool(
            run.get("state") in {"blocked", "cancelled"}
            and run.get("worker_result")
            and run.get("verification_thread_id")
            and run.get("blocked_phase", "verifying") == "verifying"
        )

    def queue_review(
        self, db: sqlite3.Connection, run: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        busy = db.execute(
            "SELECT id FROM runs WHERE task=? AND id!=? AND state IN "
            "('queued','executing','verifying','repairing','cancelling','outcome_unknown',"
            "'paused')",
            (run["task_id"], run["id"]),
        ).fetchone()
        if busy:
            raise RemConflict("This task already has an active or uncertain run")
        begin_cycle(run, "review", config=config)
        self.put(db, "runs", run)
        self.event(db, "run.review_retried", {"run_id": run["id"]})
        return run

    def mutate_run(
        self,
        identity: str,
        payload: dict[str, Any],
        action: str,
        *,
        db: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        def operation(db: sqlite3.Connection) -> dict[str, Any]:
            run = self.get(db, "runs", identity)
            if action == "cancel":
                if run["state"] in ACTIVE or run["state"] == PAUSED:
                    # Stopping wins over a pause still settling, and ends a paused run.
                    run.pop("pausing", None)
                    run.update(state="cancelling", epoch=run["epoch"] + 1)
            elif action == "pause":
                self.request_pause(run, by="user")
            elif action == "acknowledge":
                if run["state"] not in NEEDS_ATTENTION:
                    raise RemConflict("Only an attention item can be dismissed")
                run["acknowledged"] = True
            else:
                raise ValueError("Unknown run action")
            self.put(db, "runs", run)
            return {"run": run}

        return self.command(f"run.{action}:{identity}", payload, operation, db=db)

    @staticmethod
    def request_pause(run: dict[str, Any], *, by: str, reason: str = "") -> bool:
        """Ask a running run to pause; returns whether this changed anything.

        The run stops like a Stop would: its turn ends and its worker lets go. Instead of
        finishing, it then settles as ``paused`` (``settle_pause``) with its thread, provider
        session and worktree kept. A run already paused, or pausing, stays as it is.
        """
        if run["state"] == PAUSED or run.get("pausing"):
            return False
        if run["state"] not in ACTIVE or run["state"] == "cancelling":
            raise RemConflict("Only a task that is running or waiting to start can be paused")
        run["pausing"] = {
            "by": by,
            "at": now(),
            "from_state": run["state"],
            "step": progress_step(run),
            "reason": reason[:500],
        }
        run.update(state="cancelling", epoch=run["epoch"] + 1)
        return True

    def settle_pause(self, db: sqlite3.Connection, run: dict[str, Any]) -> dict[str, Any]:
        """Turn a stopped run that was asked to pause into a ``paused`` one.

        ``resume`` records how it picks up again: ``queue`` puts back a run that hadn't
        started, ``review`` checks a finished worker result again, and ``continue`` sends a
        new turn into the same thread. Nothing here releases the run's worktree.
        """
        pausing = run.pop("pausing")
        started = pausing.get("from_state") != "queued"
        resume = (
            "queue"
            if not started
            else "review"
            if run.get("worker_result") and pausing.get("from_state") == "verifying"
            else "continue"
        )
        run.update(
            state=PAUSED,
            paused={**pausing, "resume": resume},
            active_turn_id=None,
            active_thread_id=None,
            waiting_reason=None,
            phase_label=None,
        )
        self.put(db, "runs", run)
        self.event(db, "run.paused", {"run_id": run["id"], "by": pausing.get("by")})
        return run

    def resume_paused(
        self, db: sqlite3.Connection, run: dict[str, Any], config: dict[str, Any], *, by: str
    ) -> dict[str, Any]:
        """Queue a paused run again: the same run, thread and worktree, never a new run."""
        if run["state"] != PAUSED:
            if run["state"] in ACTIVE:
                return run
            raise RemConflict("Only a paused task can be resumed")
        task = self.get(db, "tasks", run["task_id"])
        if task.get("deleted_at"):
            raise RemConflict("This task was deleted")
        paused = run.pop("paused", None) or {}
        how = paused.get("resume") or "continue"
        feedback = str(run.get("review_feedback") or "").strip()
        if how == "queue":
            run.update(state="queued", epoch=run["epoch"] + 1)
        elif how == "review":
            begin_cycle(run, "review", config=config)
            # The review it was in stopped partway; a fresh one starts from the saved result.
            run["reset_review_session"] = True
        else:
            begin_cycle(run, "work", config=config)
            run["follow_up_message"] = RESUME_TEXT + (
                "\nThe review had asked for these changes when it paused:\n" + feedback
                if feedback and paused.get("from_state") == "repairing"
                else ""
            )
        run["resumed"] = {"at": now(), "by": by, "from": how}
        self.put(db, "runs", run)
        self.event(db, "run.resumed", {"run_id": run["id"], "by": by})
        return run

    def due(self, clock: datetime) -> None:
        with self.connect() as db:
            config = self.setting(db, "config")
            for row in db.execute("SELECT body FROM tasks").fetchall():
                task = json.loads(row[0])
                if after_tasks.pending(task):
                    self.start_after(db, task, clock, config)
                    continue
                at = task.get("next_occurrence_at")
                if not task["enabled"] or not at or datetime.fromisoformat(at) > clock:
                    continue
                occurrence = f"{task['id']}:{task['schedule_revision']}:{at}"
                run = self.add_run(db, task, occurrence, config, scheduled_for=at)
                # A one-time request for a file at a later time still answers that
                # request; repeating schedules never send files on their own.
                source = (task.get("delegation_context") or {}).get("source_message_id")
                if (
                    task.get("send_files")
                    and task["schedule"].get("kind") == "once"
                    and source
                    and run["occurrence"] == occurrence
                ):
                    run["deliver_files_to"] = source
                    self.put(db, "runs", run)
                task["next_occurrence_at"] = Schedule.model_validate(task["schedule"]).next(clock)
                self.put(db, "tasks", task)

    def start_after(
        self, db: sqlite3.Connection, task: dict[str, Any], clock: datetime, config: dict[str, Any]
    ) -> None:
        """Start a waiting task once its prerequisites succeeded, or say once why it is held.

        Runs under Rem's lease inside ``due``'s transaction. The run's occurrence is
        fixed per task and the task is marked started in the same commit, so a restart
        or a second backend never starts it twice.
        """
        if not task.get("enabled", True) or task.get("deleted_at"):
            return
        status = after_tasks.evaluate(db, task, clock=clock)
        if status["state"] == "held":
            body, identity = after_tasks.hold_notice(task, status)
            if not db.execute("SELECT 1 FROM messages WHERE id=?", (identity,)).fetchone():
                # A task card with no run yet: "held" alerts like work that needs you.
                self.message(
                    db,
                    body,
                    identity=identity,
                    kind="task_update",
                    work={
                        "task_id": task["id"],
                        "run_id": "",
                        "thread_id": "",
                        "title": task["title"],
                        "type": task["type"],
                        "state": "held",
                        "needs_user": True,
                    },
                )
                self.event(db, "task.after_held", {"task_id": task["id"]})
            return
        if status["state"] != "met":
            return
        not_before = (task.get("schedule") or {}).get("at")
        if not_before and datetime.fromisoformat(not_before.replace("Z", "+00:00")) > clock:
            return
        occurrence = f"after:{task['id']}"
        run = self.add_run(
            db,
            task,
            occurrence,
            config,
            acknowledgement=after_tasks.started_text(task, status),
        )
        if run["occurrence"] == occurrence and "after_results" not in run:
            run["after_results"] = after_tasks.results(db, status)
            source = (task.get("delegation_context") or {}).get("source_message_id")
            if task.get("send_files") and source:
                run["deliver_files_to"] = source
            self.put(db, "runs", run)
        after_tasks.release(task, "prerequisites", run["id"])
        task["next_occurrence_at"] = None
        self.put(db, "tasks", task)
        self.event(db, "task.after_started", {"task_id": task["id"], "run_id": run["id"]})

    def thread(self, identity: str) -> dict[str, Any]:
        with self.read() as db:
            thread = self.get(db, "threads", identity)
            messages = [
                with_author(json.loads(r[0]))
                for r in db.execute(
                    "SELECT body FROM thread_messages WHERE thread=? ORDER BY seq", (identity,)
                )
            ]
            run = self.get(db, "runs", thread["managedRunId"])
            return {
                "metadata": thread,
                "messages": messages,
                "running": (
                    run["state"] in ACTIVE - {"queued"} and run.get("active_thread_id") == identity
                ),
                "activeTurn": (
                    run.get("active_turn_id") if run.get("active_thread_id") == identity else None
                ),
            }

    def append_thread(self, identity: str, message: dict[str, Any]) -> None:
        self.append_threads(identity, [message])

    def append_threads(self, identity: str, messages: list[dict[str, Any]]) -> None:
        """Write a batch of transcript entries in one transaction.

        Rewriting an entry keeps its place. REPLACE would delete and reinsert it
        with a new seq, moving it to the end of what the UI and models read.
        """
        if not messages:
            return
        with self.connect() as db:
            db.executemany(
                "INSERT INTO thread_messages(thread,id,body) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                [(identity, message["id"], encode(message)) for message in messages],
            )

    @staticmethod
    def enqueue_outcome(db: sqlite3.Connection, run: dict[str, Any], **outcome: Any) -> str:
        """Record a finished run's chat update for delivery after the state commits."""
        identity = f"finished-{run['id']}-{run['epoch']}"
        db.execute(
            "INSERT OR IGNORE INTO outbox VALUES(?,?,?)",
            (identity, run["id"], encode({"run": run, **outcome})),
        )
        return identity

    def outbox(self) -> list[dict[str, Any]]:
        with self.read() as db:
            return [
                {"id": r["id"], "run_id": r["run"], **json.loads(r["body"])}
                for r in db.execute("SELECT * FROM outbox ORDER BY rowid")
            ]

    def events(self, after: int) -> list[dict[str, Any]]:
        with self.read() as db:
            return [
                dict(r) | {"body": json.loads(r["body"])}
                for r in db.execute(
                    "SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT 500", (after,)
                )
            ]
