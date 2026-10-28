"""Transactional task, conversation, thread, and execution authority for Rem home."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from gofer.ui.rem_home_models import (
    ACTIVE,
    NEEDS_ATTENTION,
    Recipe,
    RemConflict,
    Schedule,
    now,
    worker_config,
)


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


RUN_STATES = ACTIVE | {"succeeded", "blocked", "cancelled", "failed", "outcome_unknown"}


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
        review_refresh_count=0,
        review_receipt_retry=False,
        transient_retries=0,
        not_before=None,
        waiting_reason=None,
    )
    run["criteria"] = [
        {"id": c["id"], "result": "unknown"} for c in run["recipe"]["acceptance_criteria"]
    ]
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
                PRAGMA user_version=1;
            """)
            if not self.setting(db, "conversation"):
                self.set_setting(db, "conversation", {"id": str(uuid4()), "turn_state": "idle"})
            # Apply the completion policy to threads created before this version, too.
            for row in db.execute("SELECT body FROM runs WHERE state='succeeded'").fetchall():
                self.archive_run_threads(db, json.loads(row[0]))

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
        task["next_occurrence_at"] = recipe.schedule.next(datetime.now(UTC), initial=True)
        self.put(db, "tasks", task)
        db.execute("INSERT INTO revisions VALUES(?,?,?)", (task["id"], 1, encode(task)))
        return task

    def add_run(
        self,
        db: sqlite3.Connection,
        task: dict[str, Any],
        occurrence: str,
        config: dict[str, Any],
        *,
        acknowledgement: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        active = db.execute(
            "SELECT body FROM runs WHERE task=? AND state IN ('queued','executing',"
            "'verifying','repairing','cancelling')",
            (task["id"],),
        ).fetchone()
        if active:
            return cast(dict[str, Any], json.loads(active[0]))
        uncertain = db.execute(
            "SELECT body FROM runs WHERE task=? AND state='outcome_unknown' "
            "ORDER BY rowid DESC LIMIT 1",
            (task["id"],),
        ).fetchone()
        if uncertain and not occurrence.startswith("manual:"):
            return cast(dict[str, Any], json.loads(uncertain[0]))
        known = db.execute("SELECT body FROM runs WHERE occurrence=?", (occurrence,)).fetchone()
        if known:
            return cast(dict[str, Any], json.loads(known[0]))
        if not thread_id:
            previous = db.execute(
                "SELECT body FROM runs WHERE task=? ORDER BY rowid DESC LIMIT 1", (task["id"],)
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
        }
        self.put(db, "runs", run)
        self.provision_thread(db, run, "execute")
        self.message(
            db,
            acknowledgement or f"Started {task['title']}.",
            identity=f"queued-{run['id']}",
            work=self.work(run),
        )
        self.event(db, "run.queued", {"run_id": run["id"]})
        return run

    @staticmethod
    def work(run: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": run["task_id"],
            "run_id": run["id"],
            "thread_id": run["thread_id"],
            "title": run["recipe"]["title"],
            "type": run["recipe"]["type"],
            "state": run["state"],
            "needs_user": run.get("needs_user", False),
        }

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

    def archive_run_threads(self, db: sqlite3.Connection, run: dict[str, Any]) -> None:
        """Archive every worker/reviewer for a completed run without removing history."""
        rows = db.execute(
            "SELECT body FROM threads WHERE json_extract(body, '$.managedRunId')=?",
            (run["id"],),
        ).fetchall()
        for row in rows:
            thread = json.loads(row[0])
            if thread.get("archived"):
                continue
            thread.update(
                archived=True, archivedAt=run.get("finished_at") or now(), updatedAt=now()
            )
            self.put(db, "threads", thread)
            self.event(db, "thread.archived", {"thread_id": thread["id"], "run_id": run["id"]})

    def task_view(self, db: sqlite3.Connection, task: dict[str, Any]) -> dict[str, Any]:
        runs = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT body FROM runs WHERE task=? ORDER BY rowid DESC LIMIT 20", (task["id"],)
            )
        ]
        return {
            **task,
            "active_run": next((r for r in runs if r["state"] in ACTIVE), None),
            "last_run": runs[0] if runs else None,
            "recent_runs": runs,
        }

    def conversation(self) -> dict[str, Any]:
        with self.read() as db:
            return {
                "conversation": self.setting(db, "conversation"),
                "messages": [
                    json.loads(r["body"])
                    for r in db.execute("SELECT body FROM messages ORDER BY seq")
                ],
            }

    def tasks(self) -> list[dict[str, Any]]:
        with self.read() as db:
            return [
                self.task_view(db, task)
                for row in db.execute("SELECT body FROM tasks")
                if not (task := json.loads(row[0])).get("deleted_at")
            ]

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
                    "SELECT body FROM runs WHERE task=? ORDER BY rowid DESC LIMIT 1", (identity,)
                ).fetchone()
                old_run = json.loads(previous[0]) if previous else {}
                run = (
                    self.queue_review(db, old_run, config)
                    if self.interrupted_review(old_run)
                    and old_run["recipe"].get("revision") == task["revision"]
                    else self.add_run(db, task, f"manual:{payload['request_id']}", config)
                )
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
                if "schedule" in payload or payload.get("enabled") is True:
                    task["schedule_revision"] += 1
                    task["next_occurrence_at"] = Schedule.model_validate(task["schedule"]).next(
                        datetime.now(UTC), initial=True
                    )
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
            "('queued','executing','verifying','repairing','cancelling','outcome_unknown')",
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
                if run["state"] in ACTIVE:
                    run.update(state="cancelling", epoch=run["epoch"] + 1)
            elif action == "acknowledge":
                if run["state"] not in NEEDS_ATTENTION:
                    raise RemConflict("Only an attention item can be dismissed")
                run["acknowledged"] = True
            else:
                raise ValueError("Unknown run action")
            self.put(db, "runs", run)
            return {"run": run}

        return self.command(f"run.{action}:{identity}", payload, operation, db=db)

    def due(self, clock: datetime) -> None:
        with self.connect() as db:
            config = self.setting(db, "config")
            for row in db.execute("SELECT body FROM tasks").fetchall():
                task = json.loads(row[0])
                at = task.get("next_occurrence_at")
                if not task["enabled"] or not at or datetime.fromisoformat(at) > clock:
                    continue
                occurrence = f"{task['id']}:{task['schedule_revision']}:{at}"
                self.add_run(db, task, occurrence, config)
                task["next_occurrence_at"] = Schedule.model_validate(task["schedule"]).next(clock)
                self.put(db, "tasks", task)

    def thread(self, identity: str) -> dict[str, Any]:
        with self.read() as db:
            thread = self.get(db, "threads", identity)
            messages = [
                json.loads(r[0])
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
        """Write a batch of transcript entries in one transaction."""
        if not messages:
            return
        with self.connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO thread_messages(thread,id,body) VALUES(?,?,?)",
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
