"""Saved triggers that start Rattish workflows without a person pressing Run.

Triggers belong to this computer, not to the workflow source: Rattish 1 has no
schedule syntax, a shared ``workflow.rattish`` must not start firing on whoever
opens it, and webhook tokens must never sit in a file that gets exported. They
live in a private SQLite database under the data directory, which the desktop
app and the CLI read and write through this module.

``gofer.rattish.trigger_service`` is the part that actually fires them.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import sys
import threading
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger

from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_file
from gofer.rattish.diagnostics import RattishError
from gofer.rattish.runtime import InvalidRattishWorkflowInputError, _prepare_workflow_inputs
from gofer.rattish.workspaces import list_registered_workflows

TriggerKind = Literal["cron", "file", "webhook"]
TRIGGER_KINDS: tuple[str, ...] = ("cron", "file", "webhook")
# skip: drop the occurrence and say so. queue: run once after the current run,
# with later occurrences folded into that one. parallel: start another run.
OVERLAP_POLICIES: tuple[str, ...] = ("skip", "queue", "parallel")
# record: show the missed runs and move on. run_once: also run once to catch up.
MISSED_POLICIES: tuple[str, ...] = ("record", "run_once")
DEFAULT_OVERLAP = "skip"
DEFAULT_MISSED = "record"
DEFAULT_DEBOUNCE_SECONDS = 2.0
MIN_DEBOUNCE_SECONDS = 0.1
MAX_DEBOUNCE_SECONDS = 3600.0
DEFAULT_WEBHOOK_PORT = 8767
EVENTS_KEPT_PER_TRIGGER = 200
MAX_MISSED_COUNTED = 2000
RUNS_ONLY_WHILE_RUNNING_NOTICE = (
    "Triggers run only while Raticode or its background scheduler is running."
)
_SCHEMA_VERSION = 1
_MAX_CRON_LENGTH = 200
_MAX_GLOB_LENGTH = 300
_MAX_LABEL_LENGTH = 120
_CRON_WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


class TriggerError(ValueError):
    """Raised with a sentence for the user when a trigger can't be saved."""


class TriggerNotFound(TriggerError):
    """Raised when a trigger ID doesn't exist."""


@dataclass(frozen=True, slots=True)
class TriggerRecord:
    id: str
    kind: TriggerKind
    source_path: str
    workflow_id: str
    label: str
    config: dict[str, Any]
    overlap: str
    missed: str
    inputs: dict[str, Any]
    enabled: bool
    token_hash: str | None
    created_at: str
    updated_at: str
    baseline_at: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "sourcePath": self.source_path,
            "workflowId": self.workflow_id,
            "label": self.label,
            "config": dict(self.config),
            "overlap": self.overlap,
            "missed": self.missed,
            "inputs": dict(self.inputs),
            "enabled": self.enabled,
            "hasToken": self.token_hash is not None,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class TriggerEvent:
    id: int
    trigger_id: str
    workflow_id: str
    kind: str
    status: str
    at: str
    scheduled_for: str | None
    run_id: str | None
    message: str
    count: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "triggerId": self.trigger_id,
            "workflowId": self.workflow_id,
            "kind": self.kind,
            "status": self.status,
            "at": self.at,
            "scheduledFor": self.scheduled_for,
            "runId": self.run_id,
            "message": self.message,
            "count": self.count,
        }


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def system_timezone() -> str:
    """This computer's IANA time zone name, or UTC when it can't be determined."""
    try:
        from tzlocal import get_localzone_name

        name = str(get_localzone_name())
        ZoneInfo(name)
    except Exception:  # noqa: BLE001 - any failure here just means the safe default
        return "UTC"
    return name


def validate_timezone(timezone: str) -> tzinfo:
    name = str(timezone or "").strip()
    if not name:
        raise TriggerError("Choose a time zone, such as America/New_York or UTC.")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise TriggerError(
            f"Unknown time zone {name!r}. Use an IANA name such as America/New_York or UTC."
        ) from None


def _cron_weekday_index(token: str) -> int:
    if token.isdigit():
        number = int(token)
        if 0 <= number <= 7:
            return number % 7
    elif token[:3] in _CRON_WEEKDAYS and len(token) in {3, *range(6, 10)}:
        return _CRON_WEEKDAYS.index(token[:3])
    raise TriggerError(f"Weekday {token!r} isn't valid. Use 0-7 or sun-sat.")


def _normalize_cron_weekdays(field: str) -> str:
    """Translate crontab weekdays (0 or 7 is Sunday) to day names.

    APScheduler numbers weekdays from Monday, so handing it "0" or "1-5" directly
    would quietly run on the wrong days. Names mean the same thing to both.
    """
    if field in {"*", "?"}:
        return "*"
    selected: list[int] = []
    for term in field.lower().split(","):
        body, _, step_text = term.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) < 1:
                raise TriggerError(f"Weekday step {step_text!r} isn't valid.")
            step = int(step_text)
        if body in {"*", "?"}:
            first, last = 0, 6
        elif "-" in body:
            start, _, end = body.partition("-")
            first, last = _cron_weekday_index(start), _cron_weekday_index(end)
            if end.isdigit() and int(end) == 7:
                last = 7
            if first > last:
                raise TriggerError(f"Weekday range {body!r} runs backwards.")
        else:
            first = last = _cron_weekday_index(body)
        selected.extend(day % 7 for day in range(first, last + 1, step))
    return ",".join(_CRON_WEEKDAYS[day] for day in sorted(set(selected)))


def parse_cron(expression: str, timezone: str) -> CronTrigger:
    """Parse a standard five-field crontab expression in an IANA time zone."""
    text = str(expression or "").strip()
    if not text:
        raise TriggerError("Enter a cron expression, such as 0 9 * * 1-5.")
    if len(text) > _MAX_CRON_LENGTH:
        raise TriggerError("That cron expression is too long.")
    fields = text.split()
    if len(fields) != 5:
        raise TriggerError(
            "A cron expression needs five fields: minute, hour, day of month, month, weekday."
        )
    zone = validate_timezone(timezone)
    minute, hour, day, month, weekday = fields
    if day not in {"*", "?"} and weekday not in {"*", "?"}:
        # Crontab means "either one" here and APScheduler means "both at once".
        raise TriggerError("Use a day of the month or a weekday, not both.")
    try:
        return CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=_normalize_cron_weekdays(weekday),
            timezone=zone,
        )
    except ValueError as exc:
        raise TriggerError(f"That cron expression isn't valid: {exc}") from None


def occurrences_between(
    schedule: CronTrigger,
    after: datetime,
    until: datetime,
    *,
    limit: int = MAX_MISSED_COUNTED,
) -> list[datetime]:
    """Scheduled times strictly after ``after`` and no later than ``until``."""
    found: list[datetime] = []
    cursor = after
    while len(found) < limit:
        upcoming = schedule.get_next_fire_time(cursor, cursor)
        if upcoming is None or upcoming > until:
            break
        found.append(upcoming)
        cursor = upcoming
    return found


def next_occurrence(schedule: CronTrigger, after: datetime) -> datetime | None:
    upcoming: datetime | None = schedule.get_next_fire_time(after, after)
    return upcoming


def describe_trigger(record: TriggerRecord) -> str:
    if record.kind == "cron":
        return f"{record.config['expression']} ({record.config['timezone']})"
    if record.kind == "file":
        return f"{record.config['path']} ({record.config['glob']})"
    return f"POST /rattish-hooks/{record.id}"


def webhook_path(trigger_id: str) -> str:
    return f"/rattish-hooks/{trigger_id}"


def webhook_url(trigger_id: str, port: int) -> str:
    return f"http://127.0.0.1:{port}{webhook_path(trigger_id)}"


def hash_token(token: str) -> str:
    # Tokens are 256 random bits, so a plain digest can't be brute-forced.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_webhook_token() -> str:
    return f"rtw_{secrets.token_urlsafe(32)}"


def _registered_id(source: Path, data_dir: Path, compiled_id: str) -> str:
    for workflow in list_registered_workflows(registry_dir=data_dir):
        if workflow.entrypoint.resolve() == source:
            return workflow.workflow_id
    return compiled_id


def _compile_for_trigger(
    source_path: str | Path, data_dir: Path
) -> tuple[Path, str, Path, dict[str, Any]]:
    """Compile the workflow a trigger will run, and say why when it can't."""
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise TriggerError(f"Workflow file not found: {source}")
    try:
        compiled = compile_rattish_file(source, data_dir=data_dir)
    except RattishError as exc:
        detail = exc.diagnostics[0].message if exc.diagnostics else str(exc)
        raise TriggerError(f"Fix this workflow before adding a trigger: {detail}") from None
    except (RattishArtifactError, OSError) as exc:
        raise TriggerError(f"Couldn't read this workflow: {exc}") from None
    workflow_id = _registered_id(source, data_dir, str(compiled.ir["workflow"]["id"]))
    project_root = Path(compiled.ir["source"]["project_root"])
    return source, workflow_id, project_root, compiled.ir


def _clean_label(label: Any) -> str:
    text = str(label or "").strip()
    if len(text) > _MAX_LABEL_LENGTH:
        raise TriggerError("That trigger name is too long.")
    return text


def _clean_policy(value: Any, allowed: tuple[str, ...], default: str, what: str) -> str:
    text = str(value or default).strip().lower().replace("-", "_")
    if text not in allowed:
        raise TriggerError(f"{what} must be one of: {', '.join(allowed)}.")
    return text


def _clean_inputs(inputs: Any, ir: Mapping[str, Any]) -> dict[str, Any]:
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, Mapping):
        raise TriggerError("Trigger inputs must be an object of input names to values.")
    supplied = dict(inputs)
    try:
        _prepare_workflow_inputs(ir, supplied)
    except InvalidRattishWorkflowInputError as exc:
        raise TriggerError(f"{exc} A trigger has nobody to ask, so set it here.") from None
    return supplied


def _clean_config(kind: str, config: Mapping[str, Any], project_root: Path) -> dict[str, Any]:
    if kind == "cron":
        expression = " ".join(str(config.get("expression") or "").split())
        timezone = str(config.get("timezone") or "").strip() or system_timezone()
        parse_cron(expression, timezone)
        return {"expression": expression, "timezone": timezone}
    if kind == "file":
        raw_path = str(config.get("path") or "").strip()
        if not raw_path:
            raise TriggerError("Choose a file or folder to watch.")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = project_root / path
        path = path.resolve()
        if not path.exists():
            raise TriggerError(f"Nothing to watch at {path}.")
        glob = str(config.get("glob") or "*").strip() or "*"
        if len(glob) > _MAX_GLOB_LENGTH:
            raise TriggerError("That file pattern is too long.")
        try:
            debounce = float(config.get("debounceSeconds", DEFAULT_DEBOUNCE_SECONDS))
        except (TypeError, ValueError):
            raise TriggerError("Debounce must be a number of seconds.") from None
        if not MIN_DEBOUNCE_SECONDS <= debounce <= MAX_DEBOUNCE_SECONDS:
            raise TriggerError(
                f"Debounce must be between {MIN_DEBOUNCE_SECONDS:g} and "
                f"{MAX_DEBOUNCE_SECONDS:g} seconds."
            )
        recursive = config.get("recursive", True)
        if not isinstance(recursive, bool):
            raise TriggerError("Recursive must be true or false.")
        return {
            "path": str(path),
            "glob": glob,
            "recursive": recursive,
            "debounceSeconds": debounce,
        }
    return {}


class TriggerStore:
    """Private per-computer trigger storage shared by the app and the CLI."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.root = self.data_dir / "rattish-triggers"
        self.path = self.root / "triggers.sqlite3"
        self.lock_path = self.root / "scheduler.lock"
        self._init_lock = threading.Lock()
        self._initialized = False

    def _prepare(self) -> None:
        with self._init_lock:
            if self._initialized and self.path.exists():
                return
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if sys.platform != "win32":
                self.root.chmod(0o700)
                self.path.touch(mode=0o600, exist_ok=True)
                self.path.chmod(0o600)
            db = sqlite3.connect(self.path, timeout=10)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                if db.execute("PRAGMA user_version").fetchone()[0] < _SCHEMA_VERSION:
                    db.executescript(
                        f"""
                        BEGIN IMMEDIATE;
                        CREATE TABLE IF NOT EXISTS settings (
                            key TEXT PRIMARY KEY, value TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS triggers (
                            id TEXT PRIMARY KEY,
                            kind TEXT NOT NULL,
                            source_path TEXT NOT NULL,
                            workflow_id TEXT NOT NULL,
                            label TEXT NOT NULL,
                            config TEXT NOT NULL,
                            overlap TEXT NOT NULL,
                            missed TEXT NOT NULL,
                            inputs TEXT NOT NULL,
                            enabled INTEGER NOT NULL,
                            token_hash TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            baseline_at TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS triggers_source ON triggers(source_path);
                        CREATE TABLE IF NOT EXISTS cursors (
                            trigger_id TEXT PRIMARY KEY, cursor TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS events (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            trigger_id TEXT NOT NULL,
                            workflow_id TEXT NOT NULL,
                            source_path TEXT NOT NULL,
                            kind TEXT NOT NULL,
                            status TEXT NOT NULL,
                            at TEXT NOT NULL,
                            scheduled_for TEXT,
                            run_id TEXT,
                            message TEXT NOT NULL,
                            count INTEGER NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS events_trigger ON events(trigger_id, id);
                        CREATE INDEX IF NOT EXISTS events_source ON events(source_path, id);
                        PRAGMA user_version = {_SCHEMA_VERSION};
                        COMMIT;
                        """
                    )
            finally:
                db.close()
            self._initialized = True

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._prepare()
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=NORMAL")
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")

    @staticmethod
    def _bump(db: sqlite3.Connection) -> None:
        db.execute(
            "INSERT INTO settings(key, value) VALUES('revision', '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> TriggerRecord:
        return TriggerRecord(
            id=row["id"],
            kind=cast(TriggerKind, row["kind"]),
            source_path=row["source_path"],
            workflow_id=row["workflow_id"],
            label=row["label"],
            config=json.loads(row["config"]),
            overlap=row["overlap"],
            missed=row["missed"],
            inputs=json.loads(row["inputs"]),
            enabled=bool(row["enabled"]),
            token_hash=row["token_hash"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            baseline_at=row["baseline_at"],
        )

    def revision(self) -> int:
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'revision'").fetchone()
        return int(row["value"]) if row else 0

    def list_triggers(self, source_path: str | Path | None = None) -> list[TriggerRecord]:
        with self._connect() as db:
            if source_path is None:
                rows = db.execute("SELECT * FROM triggers ORDER BY created_at, id").fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM triggers WHERE source_path = ? ORDER BY created_at, id",
                    (str(Path(source_path).expanduser().resolve()),),
                ).fetchall()
        return [self._record(row) for row in rows]

    def get(self, trigger_id: str) -> TriggerRecord:
        with self._connect() as db:
            row = db.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
        if row is None:
            raise TriggerNotFound(f"No trigger with ID {trigger_id!r}.")
        return self._record(row)

    def create(
        self,
        *,
        kind: str,
        source_path: str | Path,
        config: Mapping[str, Any] | None = None,
        label: Any = "",
        overlap: Any = None,
        missed: Any = None,
        inputs: Any = None,
        enabled: bool = True,
        now: datetime | None = None,
    ) -> tuple[TriggerRecord, str | None]:
        """Validate and save a trigger. A webhook's token is returned only here."""
        if kind not in TRIGGER_KINDS:
            raise TriggerError(f"Trigger type must be one of: {', '.join(TRIGGER_KINDS)}.")
        source, workflow_id, project_root, ir = _compile_for_trigger(source_path, self.data_dir)
        moment = iso(now or utc_now())
        token = new_webhook_token() if kind == "webhook" else None
        record = TriggerRecord(
            id=f"trg_{uuid.uuid4().hex[:16]}",
            kind=cast(TriggerKind, kind),
            source_path=str(source),
            workflow_id=workflow_id,
            label=_clean_label(label),
            config=_clean_config(kind, config or {}, project_root),
            overlap=_clean_policy(overlap, OVERLAP_POLICIES, DEFAULT_OVERLAP, "Overlap"),
            missed=_clean_policy(missed, MISSED_POLICIES, DEFAULT_MISSED, "Missed runs"),
            inputs=_clean_inputs(inputs, ir),
            enabled=bool(enabled),
            token_hash=hash_token(token) if token else None,
            created_at=moment,
            updated_at=moment,
            baseline_at=moment,
        )
        with self._transaction() as db:
            db.execute(
                """
                INSERT INTO triggers (
                    id, kind, source_path, workflow_id, label, config, overlap, missed,
                    inputs, enabled, token_hash, created_at, updated_at, baseline_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.kind,
                    record.source_path,
                    record.workflow_id,
                    record.label,
                    json.dumps(record.config, sort_keys=True),
                    record.overlap,
                    record.missed,
                    json.dumps(record.inputs, sort_keys=True),
                    int(record.enabled),
                    record.token_hash,
                    record.created_at,
                    record.updated_at,
                    record.baseline_at,
                ),
            )
            self._bump(db)
        return record, token

    def update(
        self,
        trigger_id: str,
        *,
        config: Mapping[str, Any] | None = None,
        label: Any = None,
        overlap: Any = None,
        missed: Any = None,
        inputs: Any = None,
        enabled: bool | None = None,
        now: datetime | None = None,
    ) -> TriggerRecord:
        current = self.get(trigger_id)
        moment = iso(now or utc_now())
        updated = current
        if config is not None or inputs is not None:
            # Only edits that depend on the workflow need it to compile, so a trigger
            # on a temporarily broken workflow can still be switched off or renamed.
            _, _, project_root, ir = _compile_for_trigger(current.source_path, self.data_dir)
            if config is not None:
                merged = {**current.config, **config}
                updated = replace(
                    updated,
                    config=_clean_config(current.kind, merged, project_root),
                    baseline_at=moment,
                )
            if inputs is not None:
                updated = replace(updated, inputs=_clean_inputs(inputs, ir))
        if label is not None:
            updated = replace(updated, label=_clean_label(label))
        if overlap is not None:
            updated = replace(
                updated,
                overlap=_clean_policy(overlap, OVERLAP_POLICIES, DEFAULT_OVERLAP, "Overlap"),
            )
        if missed is not None:
            updated = replace(
                updated,
                missed=_clean_policy(missed, MISSED_POLICIES, DEFAULT_MISSED, "Missed runs"),
            )
        if enabled is not None and bool(enabled) != current.enabled:
            # A trigger that was off didn't miss anything while it was off.
            updated = replace(updated, enabled=bool(enabled), baseline_at=moment)
        updated = replace(updated, updated_at=moment)
        with self._transaction() as db:
            changed = db.execute(
                """
                UPDATE triggers SET label = ?, config = ?, overlap = ?, missed = ?, inputs = ?,
                    enabled = ?, updated_at = ?, baseline_at = ?
                WHERE id = ?
                """,
                (
                    updated.label,
                    json.dumps(updated.config, sort_keys=True),
                    updated.overlap,
                    updated.missed,
                    json.dumps(updated.inputs, sort_keys=True),
                    int(updated.enabled),
                    updated.updated_at,
                    updated.baseline_at,
                    trigger_id,
                ),
            ).rowcount
            if not changed:
                raise TriggerNotFound(f"No trigger with ID {trigger_id!r}.")
            if updated.baseline_at != current.baseline_at:
                db.execute("DELETE FROM cursors WHERE trigger_id = ?", (trigger_id,))
            self._bump(db)
        return updated

    def rotate_token(self, trigger_id: str) -> str:
        record = self.get(trigger_id)
        if record.kind != "webhook":
            raise TriggerError("Only webhook triggers have a token.")
        token = new_webhook_token()
        with self._transaction() as db:
            db.execute(
                "UPDATE triggers SET token_hash = ?, updated_at = ? WHERE id = ?",
                (hash_token(token), iso(utc_now()), trigger_id),
            )
            self._bump(db)
        return token

    def delete(self, trigger_id: str) -> None:
        with self._transaction() as db:
            removed = db.execute("DELETE FROM triggers WHERE id = ?", (trigger_id,)).rowcount
            if not removed:
                raise TriggerNotFound(f"No trigger with ID {trigger_id!r}.")
            db.execute("DELETE FROM cursors WHERE trigger_id = ?", (trigger_id,))
            db.execute("DELETE FROM events WHERE trigger_id = ?", (trigger_id,))
            self._bump(db)

    def delete_for_source(self, source_path: str | Path) -> int:
        """Remove every trigger of a workflow that no longer exists."""
        source = str(Path(source_path).expanduser().resolve())
        if not self.path.exists():
            return 0
        with self._transaction() as db:
            ids = [
                row["id"]
                for row in db.execute(
                    "SELECT id FROM triggers WHERE source_path = ?", (source,)
                ).fetchall()
            ]
            for trigger_id in ids:
                db.execute("DELETE FROM triggers WHERE id = ?", (trigger_id,))
                db.execute("DELETE FROM cursors WHERE trigger_id = ?", (trigger_id,))
                db.execute("DELETE FROM events WHERE trigger_id = ?", (trigger_id,))
            if ids:
                self._bump(db)
        return len(ids)

    def cursor(self, trigger_id: str) -> datetime | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT cursor FROM cursors WHERE trigger_id = ?", (trigger_id,)
            ).fetchone()
        return parse_iso(row["cursor"]) if row else None

    def set_cursors(self, cursors: Mapping[str, datetime]) -> None:
        if not cursors:
            return
        with self._transaction() as db:
            db.executemany(
                "INSERT INTO cursors(trigger_id, cursor) VALUES(?, ?) "
                "ON CONFLICT(trigger_id) DO UPDATE SET cursor = excluded.cursor",
                [(trigger_id, iso(value)) for trigger_id, value in cursors.items()],
            )

    def add_event(
        self,
        record: TriggerRecord,
        *,
        status: str,
        at: datetime,
        scheduled_for: datetime | None = None,
        run_id: str | None = None,
        message: str = "",
        count: int = 1,
    ) -> int:
        with self._transaction() as db:
            cursor = db.execute(
                """
                INSERT INTO events (
                    trigger_id, workflow_id, source_path, kind, status, at, scheduled_for,
                    run_id, message, count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.workflow_id,
                    record.source_path,
                    record.kind,
                    status,
                    iso(at),
                    iso(scheduled_for) if scheduled_for else None,
                    run_id,
                    message,
                    count,
                ),
            )
            db.execute(
                """
                DELETE FROM events WHERE trigger_id = ? AND id <= (
                    SELECT id FROM events WHERE trigger_id = ?
                    ORDER BY id DESC LIMIT 1 OFFSET ?
                )
                """,
                (record.id, record.id, EVENTS_KEPT_PER_TRIGGER),
            )
        return int(cursor.lastrowid or 0)

    def update_event(
        self,
        event_id: int,
        *,
        status: str | None = None,
        run_id: str | None = None,
        message: str | None = None,
        count: int | None = None,
    ) -> None:
        assignments: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("status", status),
            ("run_id", run_id),
            ("message", message),
            ("count", count),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value)
        if not assignments:
            return
        with self._transaction() as db:
            columns = ", ".join(assignments)
            db.execute(f"UPDATE events SET {columns} WHERE id = ?", [*values, event_id])

    def close_open_events(self, message: str) -> int:
        """Mark runs left unfinished by a scheduler that is no longer running."""
        with self._transaction() as db:
            return int(
                db.execute(
                    "UPDATE events SET status = 'interrupted', message = ? "
                    "WHERE status IN ('running', 'queued')",
                    (message,),
                ).rowcount
            )

    def events(
        self,
        *,
        trigger_id: str | None = None,
        source_path: str | Path | None = None,
        limit: int = 50,
    ) -> list[TriggerEvent]:
        clause = ""
        values: list[Any] = []
        if trigger_id is not None:
            clause = "WHERE trigger_id = ?"
            values.append(trigger_id)
        elif source_path is not None:
            clause = "WHERE source_path = ?"
            values.append(str(Path(source_path).expanduser().resolve()))
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM events {clause} ORDER BY id DESC LIMIT ?",
                [*values, max(1, min(limit, 1000))],
            ).fetchall()
        return [
            TriggerEvent(
                id=row["id"],
                trigger_id=row["trigger_id"],
                workflow_id=row["workflow_id"],
                kind=row["kind"],
                status=row["status"],
                at=row["at"],
                scheduled_for=row["scheduled_for"],
                run_id=row["run_id"],
                message=row["message"],
                count=row["count"],
            )
            for row in rows
        ]

    def latest_event(self, trigger_id: str) -> TriggerEvent | None:
        found = self.events(trigger_id=trigger_id, limit=1)
        return found[0] if found else None

    def webhook_port(self) -> int:
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'webhook_port'").fetchone()
        return int(row["value"]) if row else DEFAULT_WEBHOOK_PORT

    def set_webhook_port(self, port: int) -> None:
        if not 1024 <= int(port) <= 65535:
            raise TriggerError("The webhook port must be between 1024 and 65535.")
        with self._transaction() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES('webhook_port', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(int(port)),),
            )
            self._bump(db)

    def scheduler_record(self) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'scheduler'").fetchone()
        return cast(dict[str, Any], json.loads(row["value"])) if row else None

    def set_scheduler_record(self, record: Mapping[str, Any]) -> None:
        with self._transaction() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES('scheduler', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(record, sort_keys=True),),
            )


def next_run_at(record: TriggerRecord, now: datetime | None = None) -> str | None:
    if record.kind != "cron" or not record.enabled:
        return None
    schedule = parse_cron(record.config["expression"], record.config["timezone"])
    upcoming = next_occurrence(schedule, now or utc_now())
    return iso(upcoming) if upcoming else None


def preview_cron(
    expression: str, timezone: str, *, count: int = 3, now: datetime | None = None
) -> list[str]:
    """The next few times a schedule would fire, for showing before it's saved."""
    schedule = parse_cron(expression, timezone)
    moment = now or utc_now()
    result: list[str] = []
    for _ in range(count):
        upcoming = next_occurrence(schedule, moment)
        if upcoming is None:
            break
        result.append(iso(upcoming))
        moment = upcoming
    return result
