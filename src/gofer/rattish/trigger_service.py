"""The scheduler that fires saved Rattish triggers.

One process at a time owns the triggers of a data directory, chosen by a file
lock: the desktop app's backend, or ``Raticode rattish triggers start``. Any other
process waits on standby and takes over if the owner exits, so a trigger can never
fire twice. Nothing fires while no owner is running, and the next owner says so:
scheduled times that passed are recorded as missed.

Every run goes through ``run_rattish_file``, the same path as ``Raticode rattish run``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hmac
import json
import os
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from apscheduler.triggers.cron import CronTrigger
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver, ObservedWatch

from gofer.core.resources import DEFAULT_RESOURCE_LIMITS
from gofer.rattish.artifacts import RattishArtifactError
from gofer.rattish.diagnostics import RattishError
from gofer.rattish.run_service import (
    RattishRunResult,
    has_live_run,
    new_run_id,
    run_rattish_file,
)
from gofer.rattish.triggers import (
    MAX_MISSED_COUNTED,
    RUNS_ONLY_WHILE_RUNNING_NOTICE,
    TriggerError,
    TriggerRecord,
    TriggerStore,
    hash_token,
    iso,
    occurrences_between,
    parse_cron,
    parse_iso,
    utc_now,
    webhook_path,
)
from gofer.utils.logging import get_logger

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

log = get_logger(__name__)

# How late a scheduled time may be noticed and still count as on time. Anything
# later means the computer slept or nothing was running.
LATE_GRACE_SECONDS = 60
STANDBY_RETRY_SECONDS = 5.0
HEARTBEAT_SECONDS = 15
HEARTBEAT_STALE_SECONDS = 45
RESYNC_SECONDS = 15
DOWNTIME_NOTICE_SECONDS = 120
MAX_ACTIVE_RUNS = 16
MAX_FILE_CHANGES_PER_RUN = min(256, DEFAULT_RESOURCE_LIMITS.max_fanout_items)
# A file trigger keeps waiting for quiet, but never longer than this, so a folder
# that changes constantly still gets a run.
MIN_DEBOUNCE_LIMIT_SECONDS = 60.0
DEBOUNCE_LIMIT_FACTOR = 10
# A workflow that writes inside its own watched folder would start itself forever.
SELF_TRIGGER_LIMIT = 10
WEBHOOK_MAX_BODY_BYTES = 1024 * 1024
_RUN_FILE_EVENTS = {"created", "modified", "deleted", "moved"}
_NEVER_WATCH_DIRECTORIES = {".git", ".raticode", ".venv", "__pycache__", "node_modules"}
_DUMMY_TOKEN_HASH = hash_token("no such trigger")


@dataclass(frozen=True, slots=True)
class TriggerRun:
    """Everything a runner needs to start one run."""

    source: Path
    workflow_id: str
    inputs: dict[str, Any]
    events: list[Mapping[str, Any]]
    data_dir: Path
    run_id: str
    cancel_event: threading.Event


Runner = Callable[[TriggerRun], RattishRunResult]


def run_in_process(run: TriggerRun) -> RattishRunResult:
    return asyncio.run(
        run_rattish_file(
            run.source,
            workflow_inputs=run.inputs,
            trigger_events=run.events or None,
            data_dir=run.data_dir,
            run_id=run.run_id,
            cancel_event=run.cancel_event,
        )
    )


@dataclass(slots=True)
class _Occurrence:
    trigger: TriggerRecord
    scheduled_for: datetime | None = None
    events: list[Mapping[str, Any]] = field(default_factory=list)
    catching_up: bool = False


@dataclass(slots=True)
class _Waiting:
    occurrence: _Occurrence
    event_id: int
    folded: int = 0


@dataclass(slots=True)
class _FileChanges:
    first_at: datetime
    last_at: datetime
    paths: dict[str, dict[str, Any]] = field(default_factory=dict)
    during_run: bool = False


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    status: str
    event_id: int
    run_id: str | None = None


class SchedulerLock:
    """Held for as long as this process owns a data directory's triggers."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._descriptor: int | None = None

    def acquire(self) -> bool:
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if sys.platform == "win32":
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            return False
        self._descriptor = descriptor
        return True

    def release(self) -> None:
        if self._descriptor is None:
            return
        try:
            if sys.platform == "win32":
                os.lseek(self._descriptor, 0, os.SEEK_SET)
                msvcrt.locking(self._descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(self._descriptor)
            self._descriptor = None


class _ChangeHandler(FileSystemEventHandler):
    def __init__(self, engine: TriggerEngine, trigger: TriggerRecord) -> None:
        self.engine = engine
        self.trigger_id = trigger.id
        self.root = Path(trigger.config["path"])
        self.glob = str(trigger.config["glob"])
        self.single_file = self.root.is_file()

    def _matches(self, path: Path) -> bool:
        if self.single_file:
            return path == self.root
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return False
        if _NEVER_WATCH_DIRECTORIES.intersection(relative.parts):
            return False
        return fnmatch.fnmatch(relative.as_posix(), self.glob) or fnmatch.fnmatch(
            relative.name, self.glob
        )

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or event.event_type not in _RUN_FILE_EVENTS:
            return
        moved_to = os.fsdecode(event.dest_path) if event.event_type == "moved" else ""
        path = Path(moved_to or os.fsdecode(event.src_path))
        if self._matches(path):
            self.engine.note_file_change(self.trigger_id, event.event_type, path)


class _WebhookServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], engine: TriggerEngine) -> None:
        super().__init__(address, _WebhookHandler)
        self.engine = engine


class _WebhookHandler(BaseHTTPRequestHandler):
    server: _WebhookServer
    timeout = 10
    server_version = "Raticode"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        return

    def _reply(self, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _host_allowed(self) -> bool:
        host, _, _ = str(self.headers.get("Host") or "").partition(":")
        return host.lower() in {"127.0.0.1", "localhost"}

    def do_POST(self) -> None:
        # A page in a browser sends Origin. Only local programs may call this.
        if not self._host_allowed() or self.headers.get("Origin"):
            self._reply(403, {"error": "Only local programs can call this webhook."})
            return
        path = urlsplit(self.path).path
        prefix = webhook_path("")
        trigger_id = path[len(prefix) :] if path.startswith(prefix) else ""
        if not trigger_id or "/" in trigger_id:
            self._reply(404, {"error": "No such webhook."})
            return
        authorization = str(self.headers.get("Authorization") or "")
        scheme, _, token = authorization.partition(" ")
        trigger = self.server.engine.authenticate_webhook(
            trigger_id, token.strip() if scheme.lower() == "bearer" else ""
        )
        if trigger is None:
            self._reply(401, {"error": "Invalid webhook token."})
            return
        if not trigger.enabled:
            self._reply(409, {"error": "This trigger is turned off."})
            return
        if self.headers.get("Transfer-Encoding"):
            self._reply(411, {"error": "Send a Content-Length header."})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._reply(400, {"error": "Invalid Content-Length."})
            return
        if length < 0 or length > WEBHOOK_MAX_BODY_BYTES:
            self._reply(413, {"error": "The webhook body is too large."})
            return
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw) if raw.strip() else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._reply(400, {"error": "The webhook body must be JSON."})
            return
        outcome = self.server.engine.receive_webhook(trigger, payload)
        self._reply(
            202,
            {
                "triggerId": trigger.id,
                "status": outcome.status,
                "eventId": outcome.event_id,
                "runId": outcome.run_id,
            },
        )


def scheduler_status(store: TriggerStore, *, now: datetime | None = None) -> dict[str, Any]:
    """What the app and CLI show about whether triggers can fire right now."""
    record = store.scheduler_record() or {}
    moment = now or utc_now()
    running = bool(record.get("running"))
    heartbeat = record.get("heartbeat_at")
    if running and isinstance(heartbeat, str):
        age = (moment - parse_iso(heartbeat)).total_seconds()
        running = age <= HEARTBEAT_STALE_SECONDS
    else:
        running = False
    pid = record.get("pid")
    if running and isinstance(pid, int) and os.name != "nt":
        try:
            os.kill(pid, 0)
        except PermissionError:
            pass
        except OSError:
            running = False
    return {
        "running": running,
        "owner": record.get("owner") if running else None,
        "pid": pid if running else None,
        "startedAt": record.get("started_at") if running else None,
        "lastSeenAt": heartbeat,
        "webhookPort": store.webhook_port(),
        "webhookListening": bool(record.get("webhook_listening")) if running else False,
        "webhookError": record.get("webhook_error") if running else None,
        "watchErrors": dict(record.get("watch_errors") or {}) if running else {},
        "notice": RUNS_ONLY_WHILE_RUNNING_NOTICE,
    }


class TriggerEngine:
    """Owns the triggers of one data directory while its thread runs."""

    def __init__(
        self,
        data_dir: Path,
        *,
        owner: str = "background",
        runner: Runner | None = None,
        clock: Callable[[], datetime] = utc_now,
        poll_seconds: float = 1.0,
        standby_seconds: float = STANDBY_RETRY_SECONDS,
        enable_webhooks: bool = True,
        enable_watchers: bool = True,
    ) -> None:
        self.store = TriggerStore(data_dir)
        self.data_dir = self.store.data_dir
        self.owner = owner
        self._runner = runner or run_in_process
        self._clock = clock
        self._poll_seconds = poll_seconds
        self._standby_seconds = standby_seconds
        self._enable_webhooks = enable_webhooks
        self._enable_watchers = enable_watchers
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._file_lock = SchedulerLock(self.store.lock_path)
        self._is_owner = False
        self._started_at: datetime | None = None
        self._triggers: dict[str, TriggerRecord] = {}
        self._schedules: dict[str, CronTrigger] = {}
        self._cursors: dict[str, datetime] = {}
        self._revision: int | None = None
        self._next_resync: datetime | None = None
        self._next_heartbeat: datetime | None = None
        self._active: dict[str, int] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._waiting: dict[str, _Waiting] = {}
        self._changes: dict[str, _FileChanges] = {}
        self._self_triggered: dict[str, int] = {}
        self._observer: BaseObserver | None = None
        self._watches: dict[str, tuple[str, ObservedWatch, _ChangeHandler]] = {}
        self._watch_errors: dict[str, str] = {}
        self._webhook_server: _WebhookServer | None = None
        self._webhook_thread: threading.Thread | None = None
        self._webhook_port: int | None = None
        self._webhook_error: str | None = None
        self._stopping = False

    @property
    def is_owner(self) -> bool:
        return self._is_owner

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="rattish-triggers", daemon=True)
        self._thread.start()

    def stop(self, *, wait_seconds: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=wait_seconds + 5)
        self._thread = None
        if self._is_owner:
            self._resign(wait_seconds=wait_seconds)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self._is_owner and not self._try_take_ownership():
                    self._stop.wait(self._standby_seconds)
                    continue
                self.tick()
            except Exception:  # noqa: BLE001 - one bad tick must not end the scheduler
                log.exception("Trigger scheduler tick failed")
            self._stop.wait(self._poll_seconds)

    def _try_take_ownership(self) -> bool:
        self.store._prepare()
        if not self._file_lock.acquire():
            return False
        self.become_owner()
        return True

    def become_owner(self) -> None:
        """Take over after the lock is held: settle what the last owner left."""
        now = self._clock()
        previous = self.store.scheduler_record() or {}
        self._is_owner = True
        self._started_at = now
        self._stopping = False
        interrupted = self.store.close_open_events(
            "The scheduler stopped before this run finished."
        )
        if interrupted:
            log.warning("Marked %d unfinished trigger run(s) as interrupted", interrupted)
        self._reload(now)
        self._record_downtime(previous, now)
        self._heartbeat(now)

    def _record_downtime(self, previous: Mapping[str, Any], now: datetime) -> None:
        """File changes and webhooks can't be replayed, so say a gap happened."""
        last_seen = previous.get("heartbeat_at")
        for trigger in self._triggers.values():
            if not trigger.enabled or trigger.kind == "cron":
                continue
            since = parse_iso(trigger.baseline_at)
            if isinstance(last_seen, str):
                since = max(since, parse_iso(last_seen))
            if (now - since).total_seconds() < DOWNTIME_NOTICE_SECONDS:
                continue
            what = "File changes" if trigger.kind == "file" else "Webhook calls"
            self.store.add_event(
                trigger,
                status="missed",
                at=now,
                scheduled_for=since,
                count=0,
                message=f"{what} since this time weren't seen because Raticode and its "
                "scheduler weren't running.",
            )

    def tick(self, now: datetime | None = None) -> None:
        moment = now or self._clock()
        revision = self.store.revision()
        if revision != self._revision or self._next_resync is None or moment >= self._next_resync:
            self._reload(moment, revision=revision)
        self._evaluate_cron(moment)
        self._flush_file_changes(moment)
        self._drain_waiting(moment)
        if self._next_heartbeat is None or moment >= self._next_heartbeat:
            self._heartbeat(moment)

    def _reload(self, now: datetime, *, revision: int | None = None) -> None:
        self._revision = self.store.revision() if revision is None else revision
        self._next_resync = now + timedelta(seconds=RESYNC_SECONDS)
        with self._lock:
            self._triggers = {item.id: item for item in self.store.list_triggers()}
        self._schedules = {}
        self._watch_errors = {}
        for trigger in self._triggers.values():
            if trigger.kind == "cron" and trigger.enabled:
                try:
                    self._schedules[trigger.id] = parse_cron(
                        trigger.config["expression"], trigger.config["timezone"]
                    )
                except TriggerError as exc:
                    self._watch_errors[trigger.id] = str(exc)
        for trigger_id in set(self._cursors) - set(self._triggers):
            del self._cursors[trigger_id]
        for trigger_id in set(self._waiting) - set(self._triggers):
            self.store.update_event(
                self._waiting.pop(trigger_id).event_id,
                status="skipped",
                message="The trigger was removed before this run could start.",
            )
        if self._enable_watchers:
            self._sync_watchers()
        if self._enable_webhooks:
            self._sync_webhook()
        if self._is_owner:
            self._publish(now, running=True)

    def _heartbeat(self, now: datetime) -> None:
        self._next_heartbeat = now + timedelta(seconds=HEARTBEAT_SECONDS)
        self.store.set_cursors({**self._cursors})
        self._publish(now, running=True)

    def _publish(self, now: datetime, *, running: bool) -> None:
        self.store.set_scheduler_record(
            {
                "running": running,
                "owner": self.owner,
                "pid": os.getpid(),
                "started_at": iso(self._started_at or now),
                "heartbeat_at": iso(now),
                "webhook_listening": self._webhook_server is not None,
                "webhook_error": self._webhook_error,
                "watch_errors": dict(self._watch_errors),
            }
        )

    def _cursor(self, trigger: TriggerRecord) -> datetime:
        known = self._cursors.get(trigger.id) or self.store.cursor(trigger.id)
        baseline = parse_iso(trigger.baseline_at)
        return max(known, baseline) if known else baseline

    def _evaluate_cron(self, now: datetime) -> None:
        for trigger_id, schedule in self._schedules.items():
            trigger = self._triggers[trigger_id]
            cursor = self._cursor(trigger)
            if cursor >= now:
                self._cursors[trigger_id] = cursor
                continue
            due = occurrences_between(schedule, cursor, now)
            self._cursors[trigger_id] = now
            if not due:
                continue
            self.store.set_cursors({trigger_id: now})
            late = [item for item in due if (now - item).total_seconds() > LATE_GRACE_SECONDS]
            on_time = [item for item in due if item not in late]
            if late:
                plural = "" if len(late) == 1 else "s"
                amount = f"At least {len(late)}" if len(due) >= MAX_MISSED_COUNTED else len(late)
                self.store.add_event(
                    trigger,
                    status="missed",
                    at=now,
                    scheduled_for=late[0],
                    count=len(late),
                    message=f"{amount} scheduled run{plural} did not happen because "
                    "Raticode and its scheduler weren't running.",
                )
            if on_time:
                self.dispatch(_Occurrence(trigger, on_time[-1]), now=now)
            elif trigger.missed == "run_once":
                self.dispatch(_Occurrence(trigger, late[-1], catching_up=True), now=now)

    def note_file_change(self, trigger_id: str, change: str, path: Path) -> None:
        now = self._clock()
        with self._lock:
            trigger = self._triggers.get(trigger_id)
            if trigger is None or not trigger.enabled:
                return
            pending = self._changes.get(trigger_id)
            if pending is None:
                pending = _FileChanges(first_at=now, last_at=now)
                self._changes[trigger_id] = pending
            pending.last_at = now
            pending.during_run = pending.during_run or self._active.get(trigger.source_path, 0) > 0
            if str(path) in pending.paths or len(pending.paths) < MAX_FILE_CHANGES_PER_RUN:
                pending.paths[str(path)] = {
                    "type": "file",
                    "kind": change,
                    "path": str(path),
                    "name": path.name,
                    "directory": str(path.parent),
                    "trigger_id": trigger_id,
                }

    def _flush_file_changes(self, now: datetime) -> None:
        with self._lock:
            ready: list[tuple[TriggerRecord, _FileChanges]] = []
            for trigger_id, pending in list(self._changes.items()):
                trigger = self._triggers.get(trigger_id)
                if trigger is None or not trigger.enabled:
                    self._changes.pop(trigger_id, None)
                    continue
                debounce = float(trigger.config["debounceSeconds"])
                limit = max(MIN_DEBOUNCE_LIMIT_SECONDS, debounce * DEBOUNCE_LIMIT_FACTOR)
                quiet = (now - pending.last_at).total_seconds() >= debounce
                overdue = (now - pending.first_at).total_seconds() >= limit
                if quiet or overdue:
                    self._changes.pop(trigger_id, None)
                    ready.append((trigger, pending))
        for trigger, pending in ready:
            if self._stops_self_triggering(trigger, pending, now):
                continue
            self.dispatch(_Occurrence(trigger, events=list(pending.paths.values())), now=now)

    def _stops_self_triggering(
        self, trigger: TriggerRecord, pending: _FileChanges, now: datetime
    ) -> bool:
        count = self._self_triggered.get(trigger.id, 0) + 1 if pending.during_run else 0
        self._self_triggered[trigger.id] = count
        if count < SELF_TRIGGER_LIMIT:
            return False
        self._self_triggered.pop(trigger.id, None)
        self.store.update(trigger.id, enabled=False, now=now)
        self.store.add_event(
            trigger,
            status="failed",
            at=now,
            message=f"Turned off after {SELF_TRIGGER_LIMIT} runs in a row started from changes "
            "made during a run. The workflow may be changing the files it watches.",
        )
        log.warning("Trigger %s turned off: it appears to be starting itself", trigger.id)
        return True

    def authenticate_webhook(self, trigger_id: str, token: str) -> TriggerRecord | None:
        try:
            trigger = self.store.get(trigger_id)
        except ValueError:
            trigger = None
        expected = trigger.token_hash if trigger is not None else None
        matches = hmac.compare_digest(hash_token(token), expected or _DUMMY_TOKEN_HASH)
        return trigger if matches and expected else None

    def receive_webhook(self, trigger: TriggerRecord, payload: Any) -> DispatchOutcome:
        now = self._clock()
        event = {
            "type": "webhook",
            "trigger_id": trigger.id,
            "received_at": iso(now),
            "payload": payload,
        }
        return self.dispatch(_Occurrence(trigger, events=[event]), now=now)

    def _workflow_busy(self, source_path: str) -> bool:
        if self._active.get(source_path, 0) > 0:
            return True
        return has_live_run(Path(source_path), data_dir=self.data_dir)

    def dispatch(self, occurrence: _Occurrence, *, now: datetime | None = None) -> DispatchOutcome:
        """Apply the overlap policy, then start the run or record why it didn't."""
        moment = now or self._clock()
        trigger = occurrence.trigger
        with self._lock:
            if self._stopping:
                event_id = self.store.add_event(
                    trigger,
                    status="skipped",
                    at=moment,
                    scheduled_for=occurrence.scheduled_for,
                    message="The scheduler was stopping.",
                )
                return DispatchOutcome("skipped", event_id)
            if self._workflow_busy(trigger.source_path):
                if trigger.overlap == "skip":
                    event_id = self.store.add_event(
                        trigger,
                        status="skipped",
                        at=moment,
                        scheduled_for=occurrence.scheduled_for,
                        message="Skipped because this workflow was already running.",
                    )
                    return DispatchOutcome("skipped", event_id)
                if trigger.overlap == "queue":
                    return self._queue(occurrence, moment)
            return self._start(occurrence, moment)

    def _queue(self, occurrence: _Occurrence, now: datetime) -> DispatchOutcome:
        trigger = occurrence.trigger
        waiting = self._waiting.get(trigger.id)
        if waiting is not None:
            waiting.folded += 1
            waiting.occurrence = occurrence
            self.store.update_event(
                waiting.event_id,
                count=waiting.folded + 1,
                message=f"Waiting for the current run. {waiting.folded + 1} starts folded "
                "into this one.",
            )
            return DispatchOutcome("queued", waiting.event_id)
        event_id = self.store.add_event(
            trigger,
            status="queued",
            at=now,
            scheduled_for=occurrence.scheduled_for,
            message="Waiting for the current run to finish.",
        )
        self._waiting[trigger.id] = _Waiting(occurrence, event_id)
        return DispatchOutcome("queued", event_id)

    def _drain_waiting(self, now: datetime) -> None:
        with self._lock:
            for trigger_id in list(self._waiting):
                waiting = self._waiting[trigger_id]
                if self._workflow_busy(waiting.occurrence.trigger.source_path):
                    continue
                del self._waiting[trigger_id]
                self._start(waiting.occurrence, now, event_id=waiting.event_id)

    def _start(
        self, occurrence: _Occurrence, now: datetime, *, event_id: int | None = None
    ) -> DispatchOutcome:
        trigger = occurrence.trigger
        if sum(self._active.values()) >= MAX_ACTIVE_RUNS:
            message = f"Skipped because {MAX_ACTIVE_RUNS} triggered runs were already going."
            if event_id is None:
                event_id = self.store.add_event(
                    trigger,
                    status="skipped",
                    at=now,
                    scheduled_for=occurrence.scheduled_for,
                    message=message,
                )
            else:
                self.store.update_event(event_id, status="skipped", message=message)
            return DispatchOutcome("skipped", event_id)
        run_id = new_run_id()
        message = "Started to catch up on a missed run." if occurrence.catching_up else ""
        if event_id is None:
            event_id = self.store.add_event(
                trigger,
                status="running",
                at=now,
                scheduled_for=occurrence.scheduled_for,
                run_id=run_id,
                message=message,
            )
        else:
            self.store.update_event(event_id, status="running", run_id=run_id, message=message)
        cancel_event = threading.Event()
        self._active[trigger.source_path] = self._active.get(trigger.source_path, 0) + 1
        self._cancels[run_id] = cancel_event
        run = TriggerRun(
            source=Path(trigger.source_path),
            workflow_id=trigger.workflow_id,
            inputs=dict(trigger.inputs),
            events=list(occurrence.events),
            data_dir=self.data_dir,
            run_id=run_id,
            cancel_event=cancel_event,
        )
        thread = threading.Thread(
            target=self._execute,
            args=(trigger, run, event_id),
            name=f"rattish-trigger-{run_id}",
            daemon=True,
        )
        self._threads[run_id] = thread
        thread.start()
        return DispatchOutcome("started", event_id, run_id)

    def _execute(self, trigger: TriggerRecord, run: TriggerRun, event_id: int) -> None:
        status = "failed"
        message = ""
        try:
            result = self._runner(run)
            status, message = _summarize_result(result)
        except RattishError as exc:
            message = exc.diagnostics[0].message if exc.diagnostics else str(exc)
        except (RattishArtifactError, OSError, ValueError) as exc:
            message = str(exc)
        except Exception as exc:  # noqa: BLE001 - a run failure must reach the history
            log.exception("Triggered run %s failed", run.run_id)
            message = str(exc) or type(exc).__name__
        finally:
            try:
                self.store.update_event(event_id, status=status, message=message or None)
            except Exception:  # noqa: BLE001
                log.exception("Could not record the result of triggered run %s", run.run_id)
            with self._lock:
                remaining = self._active.get(trigger.source_path, 0) - 1
                if remaining > 0:
                    self._active[trigger.source_path] = remaining
                else:
                    self._active.pop(trigger.source_path, None)
                self._cancels.pop(run.run_id, None)
                self._threads.pop(run.run_id, None)

    def _sync_watchers(self) -> None:
        wanted = {
            trigger.id: trigger
            for trigger in self._triggers.values()
            if trigger.kind == "file" and trigger.enabled
        }
        if self._observer is None and wanted:
            started = Observer()
            started.daemon = True
            started.start()
            self._observer = started
        observer = self._observer
        if observer is None:
            return
        for trigger_id, (signature, watch, handler) in list(self._watches.items()):
            trigger = wanted.get(trigger_id)
            if (
                trigger is None
                or _watch_signature(trigger) != signature
                or not Path(trigger.config["path"]).exists()
            ):
                # Two triggers on one folder share a watch, so only remove this handler.
                try:
                    observer.remove_handler_for_watch(handler, watch)
                except (KeyError, OSError, ValueError):
                    pass
                del self._watches[trigger_id]
        for trigger_id, trigger in wanted.items():
            if trigger_id in self._watches:
                continue
            root = Path(trigger.config["path"])
            if not root.exists():
                self._watch_errors[trigger_id] = f"Nothing to watch at {root}."
                continue
            try:
                directory = root if root.is_dir() else root.parent
                handler = _ChangeHandler(self, trigger)
                watch = observer.schedule(
                    handler,
                    str(directory),
                    recursive=bool(trigger.config["recursive"]) and root.is_dir(),
                )
            except OSError as exc:
                self._watch_errors[trigger_id] = f"Couldn't watch {root}: {exc}"
                continue
            self._watches[trigger_id] = (_watch_signature(trigger), watch, handler)

    def _sync_webhook(self) -> None:
        needed = any(
            trigger.kind == "webhook" and trigger.enabled for trigger in self._triggers.values()
        )
        port = self.store.webhook_port()
        if self._webhook_server is not None and (not needed or port != self._webhook_port):
            self._close_webhook()
        if not needed:
            self._webhook_error = None
            return
        if self._webhook_server is not None:
            return
        try:
            server = _WebhookServer(("127.0.0.1", port), self)
        except OSError as exc:
            self._webhook_error = (
                f"Couldn't listen on 127.0.0.1:{port}: {exc.strerror or exc}. "
                "Choose another port with Raticode rattish triggers webhook-port."
            )
            log.warning("%s", self._webhook_error)
            return
        self._webhook_error = None
        self._webhook_server = server
        self._webhook_port = port
        self._webhook_thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="rattish-trigger-webhooks",
            daemon=True,
        )
        self._webhook_thread.start()

    def _close_webhook(self) -> None:
        server = self._webhook_server
        if server is None:
            return
        self._webhook_server = None
        self._webhook_port = None
        server.shutdown()
        server.server_close()
        if self._webhook_thread is not None:
            self._webhook_thread.join(timeout=2)
            self._webhook_thread = None

    def _resign(self, *, wait_seconds: float) -> None:
        now = self._clock()
        with self._lock:
            self._stopping = True
            waiting = list(self._waiting.values())
            self._waiting.clear()
            self._changes.clear()
            cancels = list(self._cancels.values())
            threads = list(self._threads.values())
        for item in waiting:
            self.store.update_event(
                item.event_id,
                status="missed",
                message="The scheduler stopped before this run could start.",
            )
        for cancel in cancels:
            cancel.set()
        deadline = now.timestamp() + wait_seconds
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - self._clock().timestamp()))
        self._close_webhook()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
            self._watches.clear()
        try:
            self.store.set_cursors({**self._cursors})
            self._publish(self._clock(), running=False)
        finally:
            self._is_owner = False
            self._file_lock.release()


def _watch_signature(trigger: TriggerRecord) -> str:
    return json.dumps(trigger.config, sort_keys=True)


def _summarize_result(result: RattishRunResult) -> tuple[str, str]:
    """Reduce a finished run to the status and sentence shown in trigger history."""
    document = result.document
    status = str(document.get("status"))
    if status == "passed":
        return "passed", ""
    if status == "stopped":
        return "stopped", "The run was stopped."
    error = document.get("error") or {}
    message = str(error.get("message") or "") if isinstance(error, Mapping) else ""
    if not message:
        for diagnostic in document.get("diagnostics") or []:
            if isinstance(diagnostic, Mapping) and diagnostic.get("severity") == "error":
                message = str(diagnostic.get("message") or "")
                break
    labels = {
        "preflight_failed": "The workflow failed its preflight check.",
        "invalid_inputs": "The workflow's inputs were invalid.",
    }
    return "failed", message or labels.get(status, "The run failed.")
