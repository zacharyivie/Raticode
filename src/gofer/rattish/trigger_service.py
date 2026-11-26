"""The scheduler that fires saved Rattish triggers.

One process at a time owns the triggers of a data directory, chosen by a file
lock: the desktop app's backend, or ``Raticode rattish triggers start``. Any other
process waits on standby and takes over if the owner exits, so a trigger can never
fire twice. Nothing fires while no owner is running, and the next owner says so:
scheduled times that passed are recorded as missed.

Each trigger can have a daily limit on estimated cost, tokens or both, and so can the
whole computer (``TriggerStore.set_daily_budget``). A triggered run starts with its
run cap set to the smaller of the workflow's own limits and what is left of the day,
so the usage meter (``run_usage``) enforces it the same way as ``max-cost-usd``. Once
a limit is used up, further starts are skipped until midnight and the phone hears
about it once that day. Runs started by hand, from the phone or by Rem don't count.

Each trigger also has a Time limit (``max_run_seconds``, 6 hours unless changed, or
none). The scheduler stops a triggered run that reaches it through the run's cancel
event, the same as Stop, so the run ends as stopped and the trigger's history entry
becomes failed with "Stopped after 6 h, this trigger's time limit." A workflow's own
``timeout`` still applies, so whichever is smaller ends the run. Only triggered runs
have it.

When a start is skipped or queued because the workflow is busy, its history line names
the live run and how long it has run, so a hung run shows up in the history.

All triggers can be paused at once (``TriggerStore.set_paused``). While paused,
nothing starts: cron times are recorded as skipped and never caught up, file
changes aren't watched, and webhook calls are refused. Each trigger keeps its own
on/off setting, so resuming brings back exactly the ones that were on.

Before each start the scheduler checks that the workflow, and every file it runs as
code, is still the version the user approved (``trigger_approval``). If not, the start
is skipped with "Workflow changed since you approved it." in the trigger's history, and
the phone gets one Workflow message for that change. A trigger with Run edits without
approval on skips this check. Manual runs never go through here.

The run then checks each of those files again right before it uses one
(``approved_files``), so a step that rewrites a later step's script can't get it run.
A file that changed fails its step with "This file changed during the run, so it wasn't
run.", marks the trigger as changed until the user approves again, and sends the phone
one Workflow message through the same approval notice.

When a triggered run fails because its failing agent step hit the provider's usage
limit (recognised as Rem's tasks do, by ``core.usage_limits``; credit and spend caps never
count), its history entry becomes waiting instead of failed: "Waiting for Claude Code's
usage limit to reset at 05:00; resumes then." The wait is saved in the trigger store, so
it outlasts a restart. At the reset plus two minutes, or every half hour when the provider
named no time, the scheduler resumes that run through ``resume_rattish_run``, so steps
that passed aren't run again. The resume goes through the same approval, pause, daily
limit and worktree checks as any triggered start, and holds back while another run of the
workflow is going. After ``MAX_WAITS`` waits the entry fails with the last error. A
waiting run counts as live for the overlap policy. Each trigger can turn this off (Wait
for a usage limit to reset), and then such a run fails as before.

The resume reuses what the waiting run's record and saved values file hold, and on a
registered workflow those sit in the project, where anything working there could edit
them during the wait. So the wait keeps both files' SHA-256 (``HeldRunFiles``), and the
resume reads each file once, compares, and parses those same bytes. A changed, missing
or unreadable file, or a wait saved before the hashes were kept, fails the entry with
``RECORD_CHANGED_MESSAGE`` instead of starting. Resume from the Runs list is the user's
own start and doesn't check.

Every run goes through ``run_rattish_file``, the same path as ``Raticode rattish run``;
a resumed one through ``resume_rattish_run``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from apscheduler.triggers.cron import CronTrigger
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver, ObservedWatch

from gofer.core import usage_limits
from gofer.core.resources import DEFAULT_RESOURCE_LIMITS
from gofer.rattish.approved_files import ApprovedFiles
from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_file
from gofer.rattish.contracts import canonical_json_bytes
from gofer.rattish.diagnostics import RattishError
from gofer.rattish.run_service import (
    RattishResumeRefused,
    RattishRunResult,
    has_live_run,
    live_run,
    new_run_id,
    resume_rattish_run,
    resume_refusal,
    run_rattish_file,
)
from gofer.rattish.run_values import (
    SavedValuesUnavailable,
    parse_saved_values,
    saved_values_path,
)
from gofer.rattish.trigger_approval import (
    APPROVAL_SKIP_MESSAGE,
    UNAPPROVED_SKIP_MESSAGE,
    Snapshot,
    shown_path,
)
from gofer.rattish.trigger_defaults import format_run_time
from gofer.rattish.triggers import (
    MAX_MISSED_COUNTED,
    PAUSED_SKIP_MESSAGE,
    RUNS_ONLY_WHILE_RUNNING_NOTICE,
    Approval,
    DailyLimitState,
    TriggerError,
    TriggerNotFound,
    TriggerPause,
    TriggerRecord,
    TriggerStore,
    UsageWait,
    approval_status,
    daily_allowance,
    hash_token,
    iso,
    occurrences_between,
    parse_cron,
    parse_iso,
    time_limit_message,
    utc_now,
    webhook_path,
)
from gofer.utils.logging import get_logger

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

log = get_logger(__name__)

RECORD_CHANGED_MESSAGE = (
    "The run's saved record changed while it waited, so Raticode didn't resume it. Start it again."
)

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
class HeldRunFiles:
    """A waiting run's record and saved values file, each read once: bytes, or None if absent."""

    record: bytes | None
    values: bytes | None

    @classmethod
    def read(cls, record_path: Path) -> HeldRunFiles:
        """Both files as they are now. Raises OSError for one that is there but unreadable."""
        return cls(_read_if_there(record_path), _read_if_there(saved_values_path(record_path)))

    def hashes(self) -> tuple[str, str]:
        """Each file's SHA-256, or "" for a file that wasn't there."""
        return _sha256(self.record), _sha256(self.values)


def _read_if_there(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _sha256(data: bytes | None) -> str:
    return "" if data is None else hashlib.sha256(data).hexdigest()


def held_files_if_unchanged(wait: UsageWait) -> HeldRunFiles | None:
    """The waiting run's files when both still hash to what the wait saved, else None."""
    if wait.record_sha256 is None or wait.values_sha256 is None:
        return None  # Saved before Raticode kept the hashes.
    try:
        held = HeldRunFiles.read(Path(wait.record_path))
    except OSError:
        return None
    record_hash, values_hash = held.hashes()
    if not (
        hmac.compare_digest(record_hash, wait.record_sha256)
        and hmac.compare_digest(values_hash, wait.values_sha256)
    ):
        return None
    return held


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
    # What is left of the day's limits; the run's cap is the smaller of these and its own.
    daily_max_cost_usd: float | None = None
    daily_max_tokens: int | None = None
    # The version the user approved, checked file by file during the run. None when
    # the trigger has Run edits without approval on.
    approved: Snapshot | None = None
    # Called with (path, role) when a covered file changed right before its use.
    on_file_changed: Callable[[str, str], None] | None = None
    # The record of the run this one resumes after a usage limit reset, or None for
    # a new run. A resumed run uses that run's saved inputs and trigger events, from
    # ``resume_files``: the bytes the scheduler checked, never a second read.
    resume_record: str | None = None
    resume_files: HeldRunFiles | None = None

    def approved_files(self) -> ApprovedFiles | None:
        if self.approved is None:
            return None
        return ApprovedFiles.from_snapshot(
            self.approved,
            data_dir=self.data_dir,
            run_id=self.run_id,
            on_changed=self.on_file_changed,
        )


Runner = Callable[[TriggerRun], RattishRunResult]


def run_in_process(run: TriggerRun) -> RattishRunResult:
    return run_trigger(run)


def run_trigger(run: TriggerRun, **options: Any) -> RattishRunResult:
    """Start a triggered run, or resume the one it continues. ``options`` go to the run."""
    common: dict[str, Any] = {
        "data_dir": run.data_dir,
        "run_id": run.run_id,
        "cancel_event": run.cancel_event,
        "started_by": "trigger",
        "daily_max_cost_usd": run.daily_max_cost_usd,
        "daily_max_tokens": run.daily_max_tokens,
        "approved_files": run.approved_files(),
        **options,
    }
    if run.resume_record is not None:
        held = run.resume_files
        if held is None or held.record is None:
            raise RattishResumeRefused(
                "This run's saved record is missing, so it can't resume. Start it again."
            )
        document = json.loads(held.record.decode("utf-8"))
        try:
            values = parse_saved_values(document, held.values)
        except SavedValuesUnavailable as exc:
            raise RattishResumeRefused(str(exc)) from None
        # The password inputs the resumed run didn't save, given again from the trigger.
        withheld = {name: run.inputs[name] for name in values.withheld_inputs if name in run.inputs}
        return asyncio.run(
            resume_rattish_run(
                run.source,
                document,
                resume_inputs=withheld or None,
                resume_values=values,
                **common,
            )
        )
    return asyncio.run(
        run_rattish_file(
            run.source,
            workflow_inputs=run.inputs,
            trigger_events=run.events or None,
            **common,
        )
    )


@dataclass(frozen=True, slots=True)
class UsageLimitHit:
    """Why a run failed at a usage limit: the provider's name and its error text."""

    provider: str
    text: str


def usage_limit_failure(document: Mapping[str, Any]) -> UsageLimitHit | None:
    """The failing agent step's usage-limit error, when that is why the run failed.

    Only a provider call's error counts, never a run's own max-tokens or max-cost-usd
    limit, and never a credit or spending cap, which doesn't reset on its own.
    """
    if document.get("status") != "failed":
        return None
    candidates: list[tuple[Any, Any]] = [(document.get("error"), None)]
    for record in document.get("runs") or []:
        if isinstance(record, Mapping) and record.get("outcome") == "failure":
            candidates.append((record.get("error"), record.get("provider")))
    for error, provider in candidates:
        if not isinstance(error, Mapping) or error.get("kind") != "provider":
            continue
        text = str(error.get("message") or "")
        if not usage_limits.usage_limited_text(text):
            continue
        details = error.get("details")
        found = details.get("provider") if isinstance(details, Mapping) else None
        if not found and isinstance(provider, Mapping):
            found = provider.get("provider")
        return UsageLimitHit(provider_name(str(found or "")), text)
    return None


def provider_name(provider_id: str) -> str:
    """A provider as people know it, such as Claude Code for ``claude-code``."""
    from gofer.core.provider_usage import PROVIDER_LABELS

    key = provider_id.strip().lower().replace("-", "_")
    if not key:
        return "The provider"
    return PROVIDER_LABELS.get(key, provider_id)


def usage_wait_message(provider: str, until: datetime, named: bool, now: datetime) -> str:
    """The history line of a run waiting for ``provider``'s usage limit to reset."""
    if named:
        reset = usage_limits.clock_text(until - usage_limits.RESET_GRACE, now)
        return f"Waiting for {provider}'s usage limit to reset at {reset}; resumes then."
    return (
        f"{provider}'s usage limit was reached; trying again at "
        f"{usage_limits.clock_text(until, now)}."
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
        skipped = self.server.engine.refuse_while_paused(trigger)
        if skipped is not None:
            self._reply(
                409,
                {
                    "error": "All triggers are paused on this computer.",
                    "triggerId": trigger.id,
                    "status": "skipped",
                    "eventId": skipped,
                },
            )
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
            payload = parse_webhook_body(raw)
        except WebhookBodyError as exc:
            self._reply(400, {"error": str(exc)})
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


class WebhookBodyError(ValueError):
    """Raised with a sentence for the caller when a webhook body can't start a run."""


def _refuse_json_constant(name: str) -> Any:
    raise WebhookBodyError(f"{name} isn't a JSON number. Send it as a string or null.")


def parse_webhook_body(raw: bytes) -> Any:
    """The JSON value a webhook call sends, or None for an empty body.

    The value must also fit in a run record, which stores strict JSON, so it is
    refused here with a reason instead of failing the run later.
    """
    if not raw.strip():
        return None
    try:
        payload = json.loads(raw, parse_constant=_refuse_json_constant)
    except RecursionError:
        raise WebhookBodyError("The webhook body is nested too deeply.") from None
    except (UnicodeError, json.JSONDecodeError):
        raise WebhookBodyError("The webhook body must be JSON in UTF-8.") from None
    except WebhookBodyError:
        raise
    except ValueError:
        # Python refuses integers with more than 4,300 digits.
        raise WebhookBodyError("The webhook body has a number too large to store.") from None
    try:
        canonical_json_bytes(payload)
    except RecursionError:
        raise WebhookBodyError("The webhook body is nested too deeply.") from None
    except UnicodeError:
        raise WebhookBodyError(
            "The webhook body has text that isn't valid Unicode, such as a lone surrogate escape."
        ) from None
    except ValueError as exc:
        if "integers" in str(exc):
            raise WebhookBodyError(
                "A whole number in the webhook body is too large to keep exactly. "
                "Send numbers beyond 9007199254740991 as strings."
            ) from None
        raise WebhookBodyError(
            "A number in the webhook body is too large. Send it as a string."
        ) from None
    return payload


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
        **store.pause_state().to_payload(),
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
        enable_social: bool = True,
    ) -> None:
        self.store = TriggerStore(data_dir)
        self._enable_social = enable_social
        self._social_publisher: Any = None
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
        self._pause = TriggerPause()
        self._next_resync: datetime | None = None
        self._next_heartbeat: datetime | None = None
        self._active: dict[str, int] = {}
        self._cancels: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        # Each triggered run this scheduler started: its workflow and start time.
        self._started: dict[str, tuple[str, datetime]] = {}
        # The Time limit timer of each running triggered run, and the runs it stopped.
        self._time_limits: dict[str, threading.Timer] = {}
        self._timed_out: dict[str, int] = {}
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
        self._start_social_publisher()
        interrupted = self.store.close_open_events(
            "The scheduler stopped before this run finished."
        )
        if interrupted:
            log.warning("Marked %d unfinished trigger run(s) as interrupted", interrupted)
        self.store.close_open_spend()
        self._reload(now)
        self._record_downtime(previous, now)
        self._heartbeat(now)

    def _record_downtime(self, previous: Mapping[str, Any], now: datetime) -> None:
        """File changes and webhooks can't be replayed, so say a gap happened."""
        if self._pause.paused:
            return  # Nothing would have started anyway.
        last_seen = previous.get("heartbeat_at")
        for trigger in self._triggers.values():
            if not trigger.enabled or trigger.kind == "cron":
                continue
            since = parse_iso(trigger.baseline_at)
            if isinstance(last_seen, str):
                since = max(since, parse_iso(last_seen))
            for start, end in self._pause.windows:
                if parse_iso(start) <= since < parse_iso(end):
                    since = parse_iso(end)  # The gap began when the pause ended.
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
        self._resume_due(moment)
        self._drain_waiting(moment)
        if self._next_heartbeat is None or moment >= self._next_heartbeat:
            self._heartbeat(moment)

    def _reload(self, now: datetime, *, revision: int | None = None) -> None:
        self._revision = self.store.revision() if revision is None else revision
        self._next_resync = now + timedelta(seconds=RESYNC_SECONDS)
        with self._lock:
            self._triggers = {item.id: item for item in self.store.list_triggers()}
            self._pause = self.store.pause_state()
            if self._pause.paused:
                self._changes.clear()
                for waiting in self._waiting.values():
                    self.store.update_event(
                        waiting.event_id, status="skipped", message=PAUSED_SKIP_MESSAGE
                    )
                self._waiting.clear()
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
            # Read fresh: a pause or resume may have landed since this tick began.
            pause = self.store.pause_state()
            paused = [item for item in due if pause.covers(item)]
            if paused:
                # Times that fell in a pause are skipped on purpose: never caught up.
                self.store.add_event(
                    trigger,
                    status="skipped",
                    at=now,
                    scheduled_for=paused[0],
                    count=len(paused),
                    message=PAUSED_SKIP_MESSAGE
                    if len(paused) == 1
                    else f"{len(paused)} scheduled runs skipped because all triggers were paused.",
                )
                due = [item for item in due if item not in paused]
                if not due:
                    continue
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
            if trigger is None or not trigger.enabled or self._pause.paused:
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

    def refuse_while_paused(self, trigger: TriggerRecord) -> int | None:
        """Record a webhook call as skipped when all triggers are paused."""
        if not self.store.pause_state().paused:
            return None
        return self.store.add_event(
            trigger, status="skipped", at=self._clock(), message=PAUSED_SKIP_MESSAGE
        )

    def receive_webhook(self, trigger: TriggerRecord, payload: Any) -> DispatchOutcome:
        now = self._clock()
        event = {
            "type": "webhook",
            "trigger_id": trigger.id,
            "received_at": iso(now),
            "payload": payload,
        }
        return self.dispatch(_Occurrence(trigger, events=[event]), now=now)

    def _workflow_busy(self, source_path: str, *, count_waits: bool = True) -> bool:
        """Whether a run of this workflow is going, or waits for a usage limit to reset."""
        if self._active.get(source_path, 0) > 0:
            return True
        if has_live_run(Path(source_path), data_dir=self.data_dir):
            return True
        return count_waits and self._usage_wait(source_path) is not None

    def _usage_wait(self, source_path: str) -> UsageWait | None:
        """The oldest run of this workflow waiting for a usage limit to reset, if any."""
        try:
            waits = self.store.usage_waits(source_path=source_path)
        except sqlite3.Error:
            log.exception("Could not read the usage limit waits of %s", source_path)
            return None
        return min(waits, key=lambda wait: wait.event_id) if waits else None

    def _busy_run(self, source_path: str, now: datetime) -> tuple[str, str | None] | None:
        """The run that keeps this workflow busy, and how long it has run if known.

        The scheduler's own oldest run first, otherwise a run started elsewhere, read
        from its record. None when no run can be named.
        """
        mine = [
            (started, run_id)
            for run_id, (path, started) in self._started.items()
            if path == source_path
        ]
        if mine:
            started, run_id = min(mine)
        else:
            found = live_run(Path(source_path), data_dir=self.data_dir)
            if found is None:
                return None
            if found.started_at is None:
                return found.run_id, None
            run_id, started = found.run_id, found.started_at
        return run_id, format_run_time((now - started).total_seconds())

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
            if self._pause.paused:
                event_id = self.store.add_event(
                    trigger,
                    status="skipped",
                    at=moment,
                    scheduled_for=occurrence.scheduled_for,
                    message=PAUSED_SKIP_MESSAGE,
                )
                return DispatchOutcome("skipped", event_id)
            reached = daily_allowance(self.store, trigger, moment).reached
            if reached is not None:
                event_id = self.store.add_event(
                    trigger,
                    status="skipped",
                    at=moment,
                    scheduled_for=occurrence.scheduled_for,
                    message=reached.reached_message(),
                )
                self._notify_limit(trigger, reached)
                return DispatchOutcome("skipped", event_id)
            if self._workflow_busy(trigger.source_path):
                if trigger.overlap == "skip":
                    busy = self._busy_run(trigger.source_path, moment)
                    wait = None if busy is not None else self._usage_wait(trigger.source_path)
                    if wait is not None:
                        message = (
                            f"Skipped because run {wait.run_id} is waiting for "
                            f"{wait.provider}'s usage limit to reset."
                        )
                    elif busy is None:
                        message = "Skipped because this workflow was already running."
                    elif busy[1] is None:
                        message = f"Skipped because run {busy[0]} was already running."
                    else:
                        message = f"Skipped because run {busy[0]} has been running for {busy[1]}."
                    event_id = self.store.add_event(
                        trigger,
                        status="skipped",
                        at=moment,
                        scheduled_for=occurrence.scheduled_for,
                        message=message,
                    )
                    return DispatchOutcome("skipped", event_id)
                if trigger.overlap == "queue":
                    return self._queue(occurrence, moment)
            return self._start(occurrence, moment)

    def _queue(self, occurrence: _Occurrence, now: datetime) -> DispatchOutcome:
        trigger = occurrence.trigger
        busy = self._busy_run(trigger.source_path, now)
        wait = None if busy is not None else self._usage_wait(trigger.source_path)
        if wait is not None:
            waiting_for = (
                f"Waiting for run {wait.run_id}, which waits for {wait.provider}'s usage "
                "limit to reset."
            )
        elif busy is None:
            waiting_for = "Waiting for the current run to finish."
        elif busy[1] is None:
            waiting_for = f"Waiting for run {busy[0]} to finish."
        else:
            waiting_for = f"Waiting for run {busy[0]} to finish. It has run for {busy[1]}."
        waiting = self._waiting.get(trigger.id)
        if waiting is not None:
            waiting.folded += 1
            waiting.occurrence = _fold_occurrences(waiting.occurrence, occurrence)
            self.store.update_event(
                waiting.event_id,
                count=waiting.folded + 1,
                message=f"{waiting_for} {waiting.folded + 1} starts folded into this one.",
            )
            return DispatchOutcome("queued", waiting.event_id)
        event_id = self.store.add_event(
            trigger,
            status="queued",
            at=now,
            scheduled_for=occurrence.scheduled_for,
            message=waiting_for,
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

    def _resume_due(self, now: datetime) -> None:
        """Resume each run whose usage limit has had time to reset.

        A resume is a triggered start: it is skipped while all triggers are paused or
        its trigger is off, and ``_start`` applies the approval, daily limit and
        worktree checks. It holds back while another run of the workflow is going.
        """
        try:
            waits = self.store.usage_waits()
        except sqlite3.Error:
            log.exception("Could not read the runs waiting for a usage limit")
            return
        for wait in waits:
            if parse_iso(wait.resume_at) > now:
                continue
            with self._lock:
                if self._stopping:
                    return
                trigger = self._triggers.get(wait.trigger_id)
                if trigger is None or not trigger.enabled or self._pause.paused:
                    if trigger is None:
                        skipped = "The trigger was removed before this run could resume."
                    elif not trigger.enabled:
                        skipped = f"Run {wait.run_id} wasn't resumed because its trigger was off."
                    else:
                        skipped = PAUSED_SKIP_MESSAGE
                    if self.store.take_usage_wait(wait.event_id) is not None:
                        self.store.update_event(wait.event_id, status="skipped", message=skipped)
                    continue
                if trigger.overlap != "parallel" and self._workflow_busy(
                    trigger.source_path, count_waits=False
                ):
                    continue  # Resumes once the run in the way ends.
                if self.store.take_usage_wait(wait.event_id) is None:
                    continue
                held = held_files_if_unchanged(wait)
                if held is None:
                    self.store.update_event(
                        wait.event_id, status="failed", message=RECORD_CHANGED_MESSAGE
                    )
                    continue
                self._start(
                    _Occurrence(trigger), now, event_id=wait.event_id, resume=wait, held=held
                )

    def _start(
        self,
        occurrence: _Occurrence,
        now: datetime,
        *,
        event_id: int | None = None,
        resume: UsageWait | None = None,
        held: HeldRunFiles | None = None,
    ) -> DispatchOutcome:
        trigger = occurrence.trigger
        refused, approved = self._refuse_unapproved(occurrence, now, event_id)
        if refused is not None:
            return refused
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
        full = _worktree_limit(trigger, self.data_dir)
        if full is not None:
            message = f"Skipped. {full}"
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
        allowance = daily_allowance(self.store, trigger, now)
        reached = allowance.reached
        if reached is not None:
            # A queued start can find the day's limit used up by the run it waited for.
            message = reached.reached_message()
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
            self._notify_limit(trigger, reached)
            return DispatchOutcome("skipped", event_id)
        run_id = new_run_id()
        message = "Started to catch up on a missed run." if occurrence.catching_up else ""
        if resume is not None:
            message = f"Resumed run {resume.run_id} after {resume.provider}'s usage limit reset."
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
        self._started[run_id] = (trigger.source_path, now)
        limit = trigger.max_run_seconds
        if limit:
            timer = threading.Timer(limit, self._time_limit_reached, args=(run_id, limit))
            timer.daemon = True
            timer.name = f"rattish-trigger-limit-{run_id}"
            self._time_limits[run_id] = timer
        run = TriggerRun(
            source=Path(trigger.source_path),
            workflow_id=trigger.workflow_id,
            inputs=dict(trigger.inputs),
            events=list(occurrence.events),
            data_dir=self.data_dir,
            run_id=run_id,
            cancel_event=cancel_event,
            daily_max_cost_usd=allowance.max_cost_usd,
            daily_max_tokens=allowance.max_tokens,
            approved=approved,
            on_file_changed=(
                None
                if approved is None
                else self._file_changed_callback(trigger, run_id, approved.fingerprint)
            ),
            resume_record=resume.record_path if resume is not None else None,
            resume_files=held,
        )
        # Every triggered run counts, so a computer-wide limit set later today sees it.
        self.store.start_spend(
            run_id,
            trigger.id,
            now,
            reserved_cost_usd=allowance.max_cost_usd,
            reserved_tokens=allowance.max_tokens,
        )
        thread = threading.Thread(
            target=self._execute,
            args=(trigger, run, event_id, resume.waits if resume is not None else 0),
            name=f"rattish-trigger-{run_id}",
            daemon=True,
        )
        self._threads[run_id] = thread
        thread.start()
        limit_timer = self._time_limits.get(run_id)
        if limit_timer is not None:
            limit_timer.start()
        return DispatchOutcome("started", event_id, run_id)

    def _time_limit_reached(self, run_id: str, limit: int) -> None:
        """Stop a triggered run at its trigger's Time limit, the same way as Stop."""
        with self._lock:
            cancel = self._cancels.get(run_id)
            if cancel is None or cancel.is_set():
                return
            self._timed_out[run_id] = limit
        log.warning("Stopping triggered run %s at its %d s time limit", run_id, limit)
        cancel.set()

    def _refuse_unapproved(
        self, occurrence: _Occurrence, now: datetime, event_id: int | None
    ) -> tuple[DispatchOutcome | None, Snapshot | None]:
        """Skip a start when the workflow isn't the version the user approved.

        Otherwise returns the approved version for the run to check its files against,
        or None when the trigger has Run edits without approval on.
        """
        trigger = occurrence.trigger
        if trigger.run_unapproved:
            return None, None
        try:
            status = approval_status(self.store, trigger)
        except Exception:  # noqa: BLE001 - a failed check must not start unapproved code
            log.exception("Could not check trigger %s's approved version", trigger.id)
            status = None
        if status is not None and status.runnable:
            approved = status.approval.snapshot if status.approval else None
            # Only a missing workflow can get here with nothing approved. Its run fails
            # on the missing file, and checking against the current snapshot keeps the
            # run guarded all the same.
            return (None, approved) if approved is not None else (None, status.current)
        message = (
            UNAPPROVED_SKIP_MESSAGE
            if status is not None and status.state == "unapproved"
            else APPROVAL_SKIP_MESSAGE
        )
        latest = self.store.latest_event(trigger.id)
        if event_id is not None:
            self.store.update_event(event_id, status="skipped", message=message)
        elif latest is not None and latest.status == "skipped" and latest.message == message:
            # One history line per change, counting the starts it stopped.
            event_id = latest.id
            self.store.update_event(event_id, count=latest.count + 1, at=now)
        else:
            event_id = self.store.add_event(
                trigger,
                status="skipped",
                at=now,
                scheduled_for=occurrence.scheduled_for,
                message=message,
            )
        if status is not None:
            self._tell_phone(trigger, status)
        return DispatchOutcome("skipped", event_id), None

    def _file_changed_callback(
        self, trigger: TriggerRecord, run_id: str, approved_fingerprint: str
    ) -> Callable[[str, str], None]:
        def changed(path: str, role: str) -> None:
            self._file_changed(trigger, run_id, approved_fingerprint, path, role)

        return changed

    def _file_changed(
        self,
        trigger: TriggerRecord,
        run_id: str,
        approved_fingerprint: str,
        path: str,
        role: str,
    ) -> None:
        """A triggered run found ``path`` changed right before using it.

        The trigger counts as changed until the user approves it again, so later starts
        are skipped, and the phone hears about it once.
        """
        approval = self.store.mark_changed_in_run(
            trigger.id,
            approved_fingerprint=approved_fingerprint,
            path=path,
            role=role,
            run_id=run_id,
            now=self._clock(),
        )
        if approval is None or approval.changed_in_run is None:
            return
        marked = approval.changed_in_run
        self._send_approval_notice(
            trigger,
            approval,
            state="changed_in_run",
            workflow_name=approval.snapshot.workflow_name if approval.snapshot else "",
            key=approval.notice_key(""),
            changed_file=str(marked.get("path") or path),
        )

    def _tell_phone(self, trigger: TriggerRecord, status: Any) -> None:
        """One Workflow message per changed version, however many starts it stops."""
        approval = status.approval
        if approval is None:
            return
        self._send_approval_notice(
            trigger,
            approval,
            state=status.state,
            workflow_name=status.current.workflow_name,
            key=approval.notice_key(status.current.fingerprint),
        )

    def _send_approval_notice(
        self,
        trigger: TriggerRecord,
        approval: Approval,
        *,
        state: str,
        workflow_name: str,
        key: str,
        changed_file: str = "",
    ) -> None:
        """The approval notice: one Workflow message per ``key``."""
        if approval.notified == key:
            return
        from gofer.core.phone_alerts import PhoneAlertStore

        name = workflow_name or Path(trigger.source_path).parent.name
        what = trigger.label or {"cron": "Schedule", "file": "File change"}.get(
            trigger.kind, "Webhook"
        )
        if state == "changed_in_run":
            shown = shown_path(changed_file, Path(trigger.source_path).parent)
            title = "A file changed during a triggered run"
            body = (
                f"{what} on {name}: {shown} changed after you approved it, so the step "
                "that uses it wasn't run. The trigger won't start again until you review "
                "the changes and approve this version in the workflow's Triggers."
            )
        elif state == "unapproved":
            title = "A trigger is waiting for your approval"
            body = (
                f"{what} on {name} didn't start: nobody has approved this workflow version "
                "for it yet. Review it and approve it in the workflow's Triggers."
            )
        else:
            title = "Workflow changed since you approved it"
            body = (
                f"{what} on {name} didn't start because the workflow changed. Review the "
                "changes and approve this version in the workflow's Triggers."
            )
        try:
            PhoneAlertStore(self.data_dir).add(
                title=title,
                body=body,
                workflow_id=trigger.workflow_id,
                workflow_name=name,
                run_id="",
                source="trigger_approval",
            )
            self.store.set_notified(trigger.id, key)
        except Exception:  # noqa: BLE001 - the history line already says what happened
            log.exception("Could not tell the phone about trigger %s", trigger.id)

    def _execute(
        self, trigger: TriggerRecord, run: TriggerRun, event_id: int, waits: int = 0
    ) -> None:
        """Run it and record the result. ``waits`` counts its earlier usage limit waits."""
        status = "failed"
        message = ""
        usage: Mapping[str, Any] | None = None
        hit: UsageLimitHit | None = None
        record: Path | None = None
        try:
            result = self._runner(run)
            status, message = _summarize_result(result)
            found = result.document.get("usage")
            usage = found if isinstance(found, Mapping) else None
            hit = usage_limit_failure(result.document)
            if hit is not None and _resumable(result.document):
                record = getattr(result, "path", None)
        except RattishError as exc:
            message = exc.diagnostics[0].message if exc.diagnostics else str(exc)
        except (RattishArtifactError, RattishResumeRefused, OSError, ValueError) as exc:
            message = str(exc)
        except Exception as exc:  # noqa: BLE001 - a run failure must reach the history
            log.exception("Triggered run %s failed", run.run_id)
            message = str(exc) or type(exc).__name__
        finally:
            with self._lock:
                timer = self._time_limits.pop(run.run_id, None)
                limit = self._timed_out.pop(run.run_id, None)
            if timer is not None:
                timer.cancel()
            if limit is not None and status != "passed":
                # The scheduler stopped it: say so, so the history shows a failure to fix.
                status, message = "failed", time_limit_message(limit)
            elif hit is not None and status == "failed":
                status, message = self._after_usage_limit(
                    trigger, run, event_id, waits, hit, record, message
                )
            try:
                self.store.update_event(event_id, status=status, message=message or None)
            except Exception:  # noqa: BLE001
                log.exception("Could not record the result of triggered run %s", run.run_id)
            self._record_spend(trigger, run, usage)
            with self._lock:
                remaining = self._active.get(trigger.source_path, 0) - 1
                if remaining > 0:
                    self._active[trigger.source_path] = remaining
                else:
                    self._active.pop(trigger.source_path, None)
                self._cancels.pop(run.run_id, None)
                self._threads.pop(run.run_id, None)
                self._started.pop(run.run_id, None)

    def _after_usage_limit(
        self,
        trigger: TriggerRecord,
        run: TriggerRun,
        event_id: int,
        waits: int,
        hit: UsageLimitHit,
        record: Path | None,
        message: str,
    ) -> tuple[str, str]:
        """Save a wait for the limit to reset, or say why the run fails instead.

        Returns the history entry's status and line: waiting, or failed with the error.
        """
        try:
            current = self.store.get(trigger.id)
        except TriggerNotFound:
            return "failed", message
        if not current.wait_usage_limit or record is None:
            return "failed", message
        if waits >= usage_limits.MAX_WAITS:
            return "failed", (
                f"Still at {hit.provider}'s usage limit after {usage_limits.MAX_WAITS} waits "
                f"for it to reset. {message}"
            )
        now = self._clock()
        until, named = usage_limits.next_check(hit.text, now)
        line = usage_wait_message(hit.provider, until, named, now)
        if waits:
            line = line[:-1] + f" (wait {waits + 1} of {usage_limits.MAX_WAITS})."
        try:
            record_sha256, values_sha256 = HeldRunFiles.read(record).hashes()
        except OSError:
            log.exception("Could not read the record of run %s to wait", run.run_id)
            return "failed", message
        wait = UsageWait(
            event_id=event_id,
            trigger_id=trigger.id,
            source_path=trigger.source_path,
            run_id=run.run_id,
            record_path=str(record),
            provider=hit.provider,
            resume_at=iso(until),
            waits=waits + 1,
            error=message[:1000],
            record_sha256=record_sha256,
            values_sha256=values_sha256,
        )
        try:
            self.store.add_usage_wait(wait, message=line)
        except sqlite3.Error:
            log.exception("Could not save the usage limit wait of run %s", run.run_id)
            return "failed", message
        log.info("Triggered run %s waits for a usage limit until %s", run.run_id, until)
        return "waiting", line

    def _record_spend(
        self, trigger: TriggerRecord, run: TriggerRun, usage: Mapping[str, Any] | None
    ) -> None:
        """Count what a triggered run used toward today's limits, and say if it used them up.

        A run with no calls cost nothing. A cost no provider reported stays unknown and
        adds nothing; the run's cap already ended it at such a call.
        """
        try:
            found = usage or {}
            calls = int(found.get("calls") or 0)
            cost = found.get("cost_usd")
            tokens = found.get("total_tokens")
            nothing = 0 if not calls else None
            self.store.finish_spend(
                run.run_id,
                cost_usd=float(cost) if isinstance(cost, int | float) else nothing,
                tokens=int(tokens) if isinstance(tokens, int) else nothing,
            )
            # Read fresh: the limit may have changed while the run went.
            try:
                current = self.store.get(trigger.id)
            except TriggerNotFound:
                current = replace(trigger, daily_max_cost_usd=None, daily_max_tokens=None)
            reached = daily_allowance(self.store, current, self._clock()).reached
            if reached is not None:
                self._notify_limit(current, reached)
        except Exception:  # noqa: BLE001 - counting must never break the scheduler
            log.exception("Could not record the usage of triggered run %s", run.run_id)

    def _notify_limit(self, trigger: TriggerRecord, state: DailyLimitState) -> None:
        """Tell the phone once a day that a daily limit was reached, through Workflow messages."""
        scope = trigger.id if state.scope == "trigger" else "computer"
        try:
            if not self.store.claim_limit_notice(scope, state.day):
                return
            from gofer.core.phone_alerts import PhoneAlertStore

            workflow = _workflow_name(trigger, self.data_dir)
            name = trigger.label or workflow
            if state.scope == "trigger":
                title = f"Daily limit reached: {name}"
                body = (
                    f"A trigger of {workflow} reached its daily limit: "
                    f"{state.used_text()}. Its triggered starts are skipped until midnight "
                    f"({state.timezone})."
                )
            else:
                title = "Daily limit reached for all triggers"
                body = (
                    f"Triggered runs on this computer reached the daily limit: "
                    f"{state.used_text()}. Triggered starts are skipped until midnight "
                    f"({state.timezone}). Runs you start yourself aren't affected."
                )
            PhoneAlertStore(self.data_dir).add(
                title=title,
                body=body,
                workflow_id=trigger.workflow_id,
                run_id="",
                workflow_name=workflow,
                source="daily-limit",
            )
        except Exception:  # noqa: BLE001 - a notice must never stop the scheduler
            log.exception("Could not leave the daily limit notice for trigger %s", trigger.id)

    def _sync_watchers(self) -> None:
        wanted = {
            trigger.id: trigger
            for trigger in self._triggers.values()
            if trigger.kind == "file" and trigger.enabled and not self._pause.paused
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
            timers = list(self._time_limits.values())
        for timer in timers:
            timer.cancel()
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
        publisher, self._social_publisher = self._social_publisher, None
        if publisher is not None:
            publisher.stop(wait_seconds=wait_seconds)
        try:
            self.store.set_cursors({**self._cursors})
            self._publish(self._clock(), running=False)
        finally:
            self._is_owner = False
            self._file_lock.release()

    def _start_social_publisher(self) -> None:
        """Social posts go out from the scheduler's owner, so only one process sends them."""
        if not self._enable_social or self._social_publisher is not None:
            return
        try:
            from gofer.social.publisher import Publisher
            from gofer.social.service import SocialService

            self._social_publisher = Publisher(SocialService(self.data_dir))
            self._social_publisher.start()
        except Exception:  # noqa: BLE001 - posts must not stop triggers from running
            log.exception("Couldn't start the social posts publisher")


def _worktree_limit(trigger: TriggerRecord, data_dir: Path) -> str | None:
    """Why a run of a ``workspace: worktree`` workflow can't start: too many worktrees wait."""
    from gofer.rattish import run_worktrees

    try:
        compiled = compile_rattish_file(Path(trigger.source_path), data_dir=data_dir)
    except Exception:  # noqa: BLE001 - the run itself reports a workflow that won't compile
        return None
    if not run_worktrees.uses_worktree(compiled.ir):
        return None
    return run_worktrees.limit_message(data_dir, compiled.ir["workflow"]["id"])


def _workflow_name(trigger: TriggerRecord, data_dir: Path) -> str:
    from gofer.rattish.workspaces import list_registered_workflows

    try:
        for workflow in list_registered_workflows(registry_dir=data_dir):
            if workflow.workflow_id == trigger.workflow_id and workflow.name:
                return workflow.name
    except Exception:  # noqa: BLE001 - the ID still names it
        pass
    return trigger.workflow_id


def _fold_occurrences(waiting: _Occurrence, later: _Occurrence) -> _Occurrence:
    """One queued run standing for both starts.

    Changed files add up, so the run still sees a file that changed before the
    later batch; a file named in both keeps its latest change. Webhook calls and
    schedules carry no such set: the queued run takes the latest one.
    """
    if later.trigger.kind != "file":
        return later
    paths: dict[str, Mapping[str, Any]] = {}
    for event in [*waiting.events, *later.events]:
        path = str(event.get("path"))
        if path in paths or len(paths) < MAX_FILE_CHANGES_PER_RUN:
            paths[path] = event
    return _Occurrence(later.trigger, later.scheduled_for, list(paths.values()), later.catching_up)


def _watch_signature(trigger: TriggerRecord) -> str:
    return json.dumps(trigger.config, sort_keys=True)


def _resumable(document: Mapping[str, Any]) -> bool:
    """Whether a failed run could resume, checked against the workflow it ran.

    A run that can't resume, such as one whose agent remembers turns only while its
    run is going, fails at once instead of waiting hours to find that out. A workflow
    changed since is caught at the resume, by the approval check and resume itself.
    """
    snapshot = document.get("compiled_snapshot")
    if not isinstance(snapshot, Mapping):
        return True  # Nothing to check here; resume_rattish_run says why if it refuses.
    return resume_refusal(document, snapshot) is None


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
