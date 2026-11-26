"""Saved triggers that start Rattish workflows without a person pressing Run.

Triggers belong to this computer, not to the workflow source: Rattish 1 has no
schedule syntax, a shared ``workflow.rattish`` must not start firing on whoever
opens it, and webhook tokens must never sit in a file that gets exported. They
live in a private SQLite database under the data directory, which the desktop
app and the CLI read and write through this module.

``gofer.rattish.trigger_service`` is the part that actually fires them.

A trigger runs only the workflow version the user approved (``trigger_approval``), and
an approval covers only what the user was shown. Adding a trigger approves the workflow
as it is then. Saving it in the Studio window approves the workflow file it wrote, and
nothing else that changed. Changing or turning on a trigger approves nothing: changes
waiting for review stay waiting, and the desktop, phone and CLI show them.
``approve_trigger`` approves a reviewed change, and asks again when part of it wasn't
shown in full. Each of these takes an ``actor``, and only ``"user"`` approves: Rem and
its workers can still change a trigger, but the version it runs stays the one the user
approved.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import sqlite3
import sys
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger

from gofer.rattish.artifacts import RattishArtifactError, compile_rattish_file
from gofer.rattish.diagnostics import RattishError
from gofer.rattish.runtime import InvalidRattishWorkflowInputError, _prepare_workflow_inputs
from gofer.rattish.trigger_approval import (
    Snapshot,
    approved_after_studio_save,
    describe_changes,
    shown_path,
    take_snapshot,
    unseen_message,
)
from gofer.rattish.trigger_defaults import DEFAULT_DEBOUNCE_SECONDS as DEFAULT_DEBOUNCE_SECONDS
from gofer.rattish.trigger_defaults import DEFAULT_MAX_RUN_SECONDS as DEFAULT_MAX_RUN_SECONDS
from gofer.rattish.trigger_defaults import DEFAULT_MISSED as DEFAULT_MISSED
from gofer.rattish.trigger_defaults import DEFAULT_OVERLAP as DEFAULT_OVERLAP
from gofer.rattish.trigger_defaults import MAX_RUN_SECONDS_CEILING, format_run_time
from gofer.rattish.workspaces import list_registered_workflows

TriggerKind = Literal["cron", "file", "webhook"]
TRIGGER_KINDS: tuple[str, ...] = ("cron", "file", "webhook")
# skip: drop the occurrence and say so. queue: run once after the current run,
# with later occurrences folded into that one. parallel: start another run.
OVERLAP_POLICIES: tuple[str, ...] = ("skip", "queue", "parallel")
# record: show the missed runs and move on. run_once: also run once to catch up.
MISSED_POLICIES: tuple[str, ...] = ("record", "run_once")
MIN_DEBOUNCE_SECONDS = 0.1
MAX_DEBOUNCE_SECONDS = 3600.0
DEFAULT_WEBHOOK_PORT = 8767
EVENTS_KEPT_PER_TRIGGER = 200
MAX_MISSED_COUNTED = 2000
# Finished pauses kept so cron times inside them count as skipped, not missed.
PAUSE_WINDOWS_KEPT = 50
PAUSED_SKIP_MESSAGE = "Skipped because all triggers were paused."
# A run waiting for its provider's usage limit to reset, as run lists and the phone show it.
USAGE_WAIT_LABEL = "Waiting for usage limit"
RUNS_ONLY_WHILE_RUNNING_NOTICE = (
    "Triggers run only while Raticode or its background scheduler is running."
)
# Daily limits on what triggered runs spend (see ``DailyAllowance``).
DAILY_LIMIT_SKIP_MESSAGE = "Daily limit reached"
MAX_DAILY_COST_USD = 1_000_000.0
MAX_DAILY_TOKENS = 10_000_000_000
# Spending older than this can't fall inside any time zone's "today".
SPEND_KEPT_DAYS = 3
COST_REPORTING_PROVIDERS = frozenset({"claude_code"})
_SCHEMA_VERSION = 8
# Who approved a trigger's workflow version, as shown in the Triggers section.
APPROVED_BY = ("desktop", "studio", "phone", "cli", "upgrade")
USER_ONLY = (
    "Only you can approve the workflow version a trigger runs. Approve it in the "
    "workflow's Triggers section, on your phone, or with Raticode rattish triggers approve "
    "from your own terminal."
)
LIMITS_USER_ONLY = (
    "Only you can raise or remove a trigger's daily limits or Time limit; an agent turn can "
    "only lower them. Change them in the workflow's Triggers section or with Raticode rattish "
    "triggers update from your own terminal."
)
_MAX_CRON_LENGTH = 200
_MAX_GLOB_LENGTH = 300
_MAX_LABEL_LENGTH = 120
_CRON_WEEKDAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
# APScheduler's own weekday numbers start at Monday = 0.
_APS_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class TriggerError(ValueError):
    """Raised with a sentence for the user when a trigger can't be saved."""


class UserOnlyError(TriggerError):
    """Raised when someone other than the user asks for a decision only the user may make."""


class TriggerNotFound(TriggerError):
    """Raised when a trigger ID doesn't exist."""


class ApprovalConflict(TriggerError):
    """Raised when the workflow changed again after the user opened its changes."""


class UnseenChanges(TriggerError):
    """Raised when part of the change wasn't shown and the user hasn't confirmed it.

    ``files`` names each such file and why (see ``trigger_approval.unseen_files``).
    """

    def __init__(self, files: list[dict[str, Any]]) -> None:
        self.files = files
        super().__init__(unseen_message(files))


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
    daily_max_cost_usd: float | None = None
    daily_max_tokens: int | None = None
    # Run the workflow even after it changed, without asking for approval again.
    run_unapproved: bool = False
    # The Time limit: the scheduler stops a triggered run after this many seconds.
    # None means no limit.
    max_run_seconds: int | None = DEFAULT_MAX_RUN_SECONDS
    # Wait for a usage limit to reset: a run whose agent step hit its provider's usage
    # limit resumes after the reset instead of failing (``trigger_service``).
    wait_usage_limit: bool = True

    @property
    def has_daily_limit(self) -> bool:
        return self.daily_max_cost_usd is not None or self.daily_max_tokens is not None

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
            "dailyMaxCostUsd": self.daily_max_cost_usd,
            "dailyMaxTokens": self.daily_max_tokens,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "runEditsWithoutApproval": self.run_unapproved,
            "maxRunSeconds": self.max_run_seconds,
            "waitForUsageLimit": self.wait_usage_limit,
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


@dataclass(frozen=True, slots=True)
class UsageWait:
    """A triggered run that hit its provider's usage limit, waiting to be resumed.

    ``run_id`` is the run that failed, which resumes as a new run at ``resume_at``.
    ``waits`` counts this wait too, up to ``usage_limits.MAX_WAITS``.
    ``record_sha256`` and ``values_sha256`` are the SHA-256 of the run's record and of
    its saved values file when the wait began, or "" for a file that wasn't there.
    The run resumes only while both files still match. None is a wait saved before
    Raticode kept these, which never resumes on its own.
    """

    event_id: int
    trigger_id: str
    source_path: str
    run_id: str
    record_path: str
    provider: str
    resume_at: str
    waits: int
    error: str
    record_sha256: str | None = None
    values_sha256: str | None = None


def _usage_wait(row: sqlite3.Row) -> UsageWait:
    return UsageWait(
        event_id=int(row["event_id"]),
        trigger_id=row["trigger_id"],
        source_path=row["source_path"],
        run_id=row["run_id"],
        record_path=row["record_path"],
        provider=row["provider"],
        resume_at=row["resume_at"],
        waits=int(row["waits"]),
        error=row["error"],
        record_sha256=row["record_sha256"],
        values_sha256=row["values_sha256"],
    )


def usage_wait_payload(wait: UsageWait) -> dict[str, Any]:
    """A waiting run as run lists send it: the label, when it resumes, and its count."""
    from gofer.core.usage_limits import MAX_WAITS

    return {
        "label": USAGE_WAIT_LABEL,
        "provider": wait.provider,
        "resumeAt": wait.resume_at,
        "waits": wait.waits,
        "maxWaits": MAX_WAITS,
        "triggerId": wait.trigger_id,
    }


def usage_waits_by_run(data_dir: Path) -> dict[str, UsageWait]:
    """Each run waiting for a usage limit to reset, by run ID, for the run lists.

    Reads nothing and creates nothing when this computer has no triggers yet.
    """
    store = TriggerStore(data_dir)
    if not store.path.exists():
        return {}
    try:
        return {wait.run_id: wait for wait in store.usage_waits()}
    except sqlite3.Error:
        return {}


@dataclass(frozen=True, slots=True)
class TriggerPause:
    """Whether every trigger on this computer is paused at once.

    Pausing is one switch over all triggers. It never changes a trigger's own on/off
    setting, so resuming brings back exactly the triggers that were on. ``windows``
    are the latest finished pauses, so a scheduler that wasn't running during one can
    still tell a skipped cron time from a missed one.
    """

    paused: bool = False
    since: str | None = None
    windows: tuple[tuple[str, str], ...] = ()

    def covers(self, moment: datetime) -> bool:
        """Whether ``moment`` fell inside a pause, finished or still going."""
        if self.paused and self.since is not None and moment >= parse_iso(self.since):
            return True
        return any(parse_iso(start) <= moment < parse_iso(end) for start, end in self.windows)

    def to_payload(self) -> dict[str, Any]:
        return {"paused": self.paused, "pausedAt": self.since if self.paused else None}


@dataclass(frozen=True, slots=True)
class Approval:
    """The workflow version a trigger may run. ``snapshot`` is None until one is approved.

    ``notified`` is the changed version the phone was last told about, so each change
    sends one message however many starts it stops. ``changed_in_run`` is set when a
    triggered run found a covered file changed right before using it (``path``,
    ``role``, ``runId``, ``at``): the trigger counts as changed until the next approval,
    even if the file changes back.
    """

    snapshot: Snapshot | None
    approved_at: str | None
    approved_by: str | None
    notified: str | None = None
    changed_in_run: dict[str, Any] | None = None

    def notice_key(self, fingerprint: str) -> str:
        """What ``notified`` holds once the phone has heard about this change."""
        if self.changed_in_run:
            return f"run:{self.changed_in_run.get('runId') or ''}"
        return fingerprint


@dataclass(frozen=True, slots=True)
class ApprovalStatus:
    """Whether a trigger may run its workflow as it is now.

    ``state`` is ``approved``, ``changed`` (it differs from the approved version),
    ``unapproved`` (no version was ever approved, as when an agent added the trigger) or
    ``missing`` (the workflow file is gone, so the run fails and says so).
    """

    state: str
    current: Snapshot
    approval: Approval | None

    @property
    def runnable(self) -> bool:
        return self.state in {"approved", "missing"}

    def to_payload(self) -> dict[str, Any]:
        approved = self.approval.snapshot if self.approval else None
        changed = 0
        if self.state == "changed" and approved is not None:
            changed = len(describe_changes(approved, self.current)["files"])
        return {
            "state": self.state,
            "fingerprint": self.current.fingerprint,
            "approvedFingerprint": approved.fingerprint if approved else None,
            "approvedAt": self.approval.approved_at if self.approval else None,
            "approvedBy": self.approval.approved_by if self.approval else None,
            "changedFiles": changed,
            "changedDuringRun": self.approval.changed_in_run if self.approval else None,
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


def _cron_weekday_index(token: str, *, monday_zero: bool = False) -> int:
    """A weekday's number: crontab's (Sunday = 0, 7 too) or APScheduler's (Monday = 0)."""
    days, top = (_APS_WEEKDAYS, 6) if monday_zero else (_CRON_WEEKDAYS, 7)
    if token.isdigit():
        number = int(token)
        if 0 <= number <= top:
            return number % 7
    elif token[:3] in days and len(token) in {3, *range(6, 10)}:
        return days.index(token[:3])
    raise TriggerError(f"Weekday {token!r} isn't valid. Use 0-{top} or sun-sat.")


def _cron_weekday_set(field: str, *, monday_zero: bool = False) -> set[str] | None:
    """The days a cron weekday field picks, or None for every day.

    Crontab counts from Sunday = 0 (7 is Sunday too). ``monday_zero`` reads the field
    the way APScheduler does, from Monday = 0, which is how Rem tasks and organization
    routines ran their cron text before they stored day names.
    """
    field = field.strip().lower()
    if field in {"*", "?"}:
        return None
    days = _APS_WEEKDAYS if monday_zero else _CRON_WEEKDAYS
    selected: set[str] = set()
    for term in field.split(","):
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
            first = _cron_weekday_index(start, monday_zero=monday_zero)
            last = _cron_weekday_index(end, monday_zero=monday_zero)
            if not monday_zero and end.isdigit() and int(end) == 7:
                last = 7
            if first > last:
                raise TriggerError(f"Weekday range {body!r} runs backwards.")
        else:
            first = last = _cron_weekday_index(body, monday_zero=monday_zero)
        selected.update(days[day % 7] for day in range(first, last + 1, step))
    return selected


def _normalize_cron_weekdays(field: str) -> str:
    """Translate crontab weekdays (0 or 7 is Sunday) to day names.

    APScheduler numbers weekdays from Monday, so handing it "0" or "1-5" directly
    would quietly run on the wrong days. Names mean the same thing to both.
    """
    selected = _cron_weekday_set(field)
    if selected is None:
        return "*"
    return ",".join(day for day in _CRON_WEEKDAYS if day in selected)


def cron_with_day_names(expression: str, *, monday_zero: bool = False) -> str:
    """``expression`` with its weekday field written as day names, such as ``0 9 * * mon-fri``.

    Rem tasks and organization routines store their cron text this way, so it means the
    same days to crontab, to APScheduler and to anyone reading it. Numbers are read the
    crontab way (Sunday = 0); ``monday_zero`` reads them from Monday = 0 instead, for text
    saved before names were stored. Text that already uses names comes back the same.
    """
    fields = str(expression or "").split()
    if len(fields) != 5:
        return str(expression or "")
    selected = _cron_weekday_set(fields[4], monday_zero=monday_zero)
    if selected is None or len(selected) == 7:
        fields[4] = "*"
        return " ".join(fields)
    indexes = [i for i, day in enumerate(_CRON_WEEKDAYS) if day in selected]
    runs: list[list[int]] = []
    for index in indexes:
        if runs and runs[-1][-1] == index - 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    # Three or more days in a row read best as a range: mon-fri.
    fields[4] = ",".join(
        f"{_CRON_WEEKDAYS[run[0]]}-{_CRON_WEEKDAYS[run[-1]]}"
        if len(run) >= 3
        else ",".join(_CRON_WEEKDAYS[i] for i in run)
        for run in runs
    )
    return " ".join(fields)


def saved_cron_day_names(expression: str) -> str:
    """Cron text a Rem task or organization routine saved with numbered weekdays, in names.

    Both used to hand their cron text to APScheduler, which counts weekdays from
    Monday = 0 and puts Sunday last. Saves now always store names that read the crontab
    way, so a weekday field with numbers, or with names crontab can't read (a range
    ending on Sunday, such as sat-sun), was saved before that. It is read Monday-first,
    keeping the days the schedule ran on. Names that crontab reads mean the same days to
    APScheduler, so they stay. Running it again changes nothing. Text that was never
    valid comes back unchanged and fails the same way it did before.
    """
    fields = str(expression or "").split()
    if len(fields) != 5:
        return expression
    if not any(c.isdigit() for c in fields[4]):
        try:
            _cron_weekday_set(fields[4])
            return expression
        except TriggerError:
            pass
    try:
        return cron_with_day_names(expression, monday_zero=True)
    except TriggerError:
        return expression


def parse_cron(expression: str, timezone: str, *, saved: bool = False) -> CronTrigger:
    """Parse a standard five-field crontab expression in an IANA time zone.

    ``saved`` reads a Rem task's or organization routine's schedule as it was stored. It
    skips the checks new schedules get, so one saved before they existed (a day of the
    month together with a weekday, which APScheduler reads as both at once) keeps running.
    """
    text = str(expression or "").strip()
    if not text:
        raise TriggerError("Enter a cron expression, such as 0 9 * * 1-5.")
    if len(text) > _MAX_CRON_LENGTH:
        raise TriggerError("That cron expression is too long.")
    fields = text.split()
    if len(fields) != 5:
        raise TriggerError(
            "A cron expression needs five fields: minute, hour, day of month, month, weekday. "
            "For example, 0 9 * * 1-5 runs at 9:00 on weekdays and */15 * * * * every "
            "15 minutes."
        )
    zone = validate_timezone(timezone)
    minute, hour, day, month, weekday = fields
    if not saved and day not in {"*", "?"} and weekday not in {"*", "?"}:
        # Crontab means "either one" here and APScheduler means "both at once".
        raise TriggerError("Use a day of the month or a weekday, not both.")
    try:
        schedule = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=_normalize_cron_weekdays(weekday),
            timezone=zone,
        )
    except ValueError as exc:
        raise TriggerError(f"That cron expression isn't valid: {exc}") from None
    if not saved and next_occurrence(schedule, utc_now()) is None:
        # Such as 0 9 31 2 *: February never has a 31st, so this would never run.
        raise TriggerError(
            "That schedule never runs: no date matches its day of the month and month."
        )
    return schedule


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
    from gofer.rattish.file_inputs import file_input_specs

    files = file_input_specs(ir)
    given = sorted(name for name in supplied if name in files)
    if given:
        raise TriggerError(
            f"A trigger can't fill file input {given[0]!r}. Leave it out; a run you start "
            "yourself asks for the file."
        )
    required = [
        name
        for name in files
        if any(item["name"] == name and item["required"] for item in ir["workflow"]["inputs"])
    ]
    if required:
        raise TriggerError(
            f"This workflow needs a file for input {required[0]!r}, and a trigger has nobody "
            "to choose one. Make the input optional (required: false) to add a trigger."
        )
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


_UNCHANGED: Any = object()


def _number(value: Any, what: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise TriggerError(f"{what} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise TriggerError(f"{what} must be a number.") from None
    if not math.isfinite(number) or number < 0:
        raise TriggerError(f"{what} can't be negative.")
    return number


def clean_daily_cost(value: Any) -> float | None:
    """A daily cost limit in US dollars, or None for no limit. 0 also means none."""
    number = _number(value, "The daily cost limit")
    if not number:
        return None
    if number > MAX_DAILY_COST_USD:
        raise TriggerError(f"The daily cost limit can be at most ${MAX_DAILY_COST_USD:,.0f}.")
    return round(number, 6)


def clean_max_run_seconds(value: Any) -> int | None:
    """A Time limit in whole seconds, or None for no limit. 0 also means none."""
    number = _number(value, "The time limit")
    if not number:
        return None
    if number != int(number):
        raise TriggerError("The time limit must be a whole number of seconds.")
    if number > MAX_RUN_SECONDS_CEILING:
        raise TriggerError(
            f"The time limit can be at most {MAX_RUN_SECONDS_CEILING // 86400} days. "
            "Use none for no limit."
        )
    return int(number)


def _check_limits_not_loosened(
    before: TriggerRecord | None,
    after: TriggerRecord,
) -> None:
    """Refuse a limit an agent raised or removed. Equal or lower values pass.

    ``before`` is the trigger as saved, or None for a new trigger, whose daily limits
    start at none and whose Time limit starts at the default.
    """
    pairs: tuple[tuple[float | None, float | None], ...] = (
        (before.daily_max_cost_usd if before else None, after.daily_max_cost_usd),
        (before.daily_max_tokens if before else None, after.daily_max_tokens),
        (before.max_run_seconds if before else DEFAULT_MAX_RUN_SECONDS, after.max_run_seconds),
    )
    for old, new in pairs:
        if old is not None and (new is None or new > old):
            raise UserOnlyError(LIMITS_USER_ONLY)


def _clean_switch(value: Any, what: str) -> bool:
    """An on/off setting: only true or false, so a typo can't turn it on or off."""
    if not isinstance(value, bool):
        raise TriggerError(f"{what} must be true or false.")
    return value


def time_limit_message(seconds: int) -> str:
    """The trigger history line for a run the scheduler stopped at its Time limit."""
    return (
        f"Stopped after {format_run_time(seconds)}, this trigger's time limit. Raise the "
        "limit or set a timeout in the workflow."
    )


def clean_daily_tokens(value: Any) -> int | None:
    """A daily token limit, or None for no limit. 0 also means none."""
    number = _number(value, "The daily token limit")
    if not number:
        return None
    if number != int(number):
        raise TriggerError("The daily token limit must be a whole number.")
    if number > MAX_DAILY_TOKENS:
        raise TriggerError(f"The daily token limit can be at most {MAX_DAILY_TOKENS:,}.")
    return int(number)


@dataclass(frozen=True, slots=True)
class DailyBudget:
    """The computer-wide daily limit over every triggered run. None means no limit."""

    max_cost_usd: float | None = None
    max_tokens: int | None = None

    @property
    def active(self) -> bool:
        return self.max_cost_usd is not None or self.max_tokens is not None

    def to_payload(self) -> dict[str, Any]:
        return {"maxCostUsd": self.max_cost_usd, "maxTokens": self.max_tokens}


@dataclass(frozen=True, slots=True)
class DailySpend:
    """What triggered runs started since a moment have used so far."""

    cost_usd: float = 0.0
    tokens: int = 0
    runs: int = 0
    unknown_cost_runs: int = 0
    running: int = 0


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
                        """
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
                        CREATE TABLE IF NOT EXISTS spend (
                            run_id TEXT PRIMARY KEY,
                            trigger_id TEXT NOT NULL,
                            started_at TEXT NOT NULL,
                            cost_usd REAL,
                            tokens INTEGER,
                            finished INTEGER NOT NULL DEFAULT 0
                        );
                        CREATE INDEX IF NOT EXISTS spend_started ON spend(started_at);
                        CREATE TABLE IF NOT EXISTS approvals (
                            trigger_id TEXT PRIMARY KEY,
                            snapshot TEXT,
                            approved_at TEXT,
                            approved_by TEXT,
                            notified TEXT,
                            changed_in_run TEXT
                        );
                        CREATE TABLE IF NOT EXISTS usage_waits (
                            event_id INTEGER PRIMARY KEY,
                            trigger_id TEXT NOT NULL,
                            source_path TEXT NOT NULL,
                            run_id TEXT NOT NULL,
                            record_path TEXT NOT NULL,
                            provider TEXT NOT NULL,
                            resume_at TEXT NOT NULL,
                            waits INTEGER NOT NULL,
                            error TEXT NOT NULL,
                            record_sha256 TEXT,
                            values_sha256 TEXT
                        );
                        COMMIT;
                        """
                    )
                    columns = {row[1] for row in db.execute("PRAGMA table_info(triggers)")}
                    # Version 2 added daily spending limits. Version 3 added approvals:
                    # triggers saved before it have no approval row, and the scheduler
                    # approves each one as it is now, once. Version 6 added the Time
                    # limit, which triggers saved before it get at its default; 0 is none.
                    # Version 7 added waiting for a usage limit to reset, on for all, and
                    # the usage_waits table that keeps those waits across restarts.
                    for column, kind in (
                        ("daily_max_cost_usd", "REAL"),
                        ("daily_max_tokens", "INTEGER"),
                        ("run_unapproved", "INTEGER NOT NULL DEFAULT 0"),
                        (
                            "max_run_seconds",
                            f"INTEGER NOT NULL DEFAULT {DEFAULT_MAX_RUN_SECONDS}",
                        ),
                        ("wait_usage_limit", "INTEGER NOT NULL DEFAULT 1"),
                    ):
                        if column in columns:
                            continue
                        try:
                            db.execute(f"ALTER TABLE triggers ADD COLUMN {column} {kind}")
                        except sqlite3.OperationalError as exc:
                            # Another process added it first.
                            if "duplicate column" not in str(exc):
                                raise
                    # Version 4 marks a trigger whose files changed during a run.
                    approval_columns = {
                        row[1] for row in db.execute("PRAGMA table_info(approvals)")
                    }
                    if "changed_in_run" not in approval_columns:
                        try:
                            db.execute("ALTER TABLE approvals ADD COLUMN changed_in_run TEXT")
                        except sqlite3.OperationalError as exc:
                            if "duplicate column" not in str(exc):
                                raise
                    # Version 5 reserves a running run's cap, so parallel runs can't
                    # each start with the whole day's remainder.
                    spend_columns = {row[1] for row in db.execute("PRAGMA table_info(spend)")}
                    for column, kind in (
                        ("reserved_cost_usd", "REAL"),
                        ("reserved_tokens", "INTEGER"),
                    ):
                        if column in spend_columns:
                            continue
                        try:
                            db.execute(f"ALTER TABLE spend ADD COLUMN {column} {kind}")
                        except sqlite3.OperationalError as exc:
                            if "duplicate column" not in str(exc):
                                raise
                    # Version 8 keeps what a waiting run's record and saved values
                    # hashed to, so a resume refuses files changed while it waited.
                    # Waits saved before it have NULL there and don't resume.
                    wait_columns = {row[1] for row in db.execute("PRAGMA table_info(usage_waits)")}
                    for column in ("record_sha256", "values_sha256"):
                        if column in wait_columns:
                            continue
                        try:
                            db.execute(f"ALTER TABLE usage_waits ADD COLUMN {column} TEXT")
                        except sqlite3.OperationalError as exc:
                            if "duplicate column" not in str(exc):
                                raise
                    db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
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
            daily_max_cost_usd=row["daily_max_cost_usd"],
            daily_max_tokens=row["daily_max_tokens"],
            run_unapproved=bool(row["run_unapproved"]),
            max_run_seconds=row["max_run_seconds"] or None,
            wait_usage_limit=bool(row["wait_usage_limit"]),
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
        daily_max_cost_usd: Any = None,
        daily_max_tokens: Any = None,
        run_unapproved: bool = False,
        max_run_seconds: Any = DEFAULT_MAX_RUN_SECONDS,
        wait_usage_limit: bool = True,
        actor: str = "user",
        approved_by: str = "desktop",
        now: datetime | None = None,
    ) -> tuple[TriggerRecord, str | None]:
        """Validate and save a trigger. A webhook's token is returned only here.

        Saving it approves the workflow as it is now when ``actor`` is the user. Any other
        actor leaves it waiting for the user's approval, and can't turn on
        ``run_unapproved`` or turn off the default Time limit. ``max_run_seconds`` is the
        Time limit; None or 0 means none.
        ``wait_usage_limit`` is Wait for a usage limit to reset, on unless turned off.
        """
        if kind not in TRIGGER_KINDS:
            raise TriggerError(f"Trigger type must be one of: {', '.join(TRIGGER_KINDS)}.")
        source, workflow_id, project_root, ir = _compile_for_trigger(source_path, self.data_dir)
        if run_unapproved and actor != "user":
            raise UserOnlyError(USER_ONLY)
        moment = iso(now or utc_now())
        token = new_webhook_token() if kind == "webhook" else None
        snapshot = take_snapshot(source, self.data_dir) if actor == "user" else None
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
            daily_max_cost_usd=clean_daily_cost(daily_max_cost_usd),
            daily_max_tokens=clean_daily_tokens(daily_max_tokens),
            run_unapproved=bool(run_unapproved),
            max_run_seconds=clean_max_run_seconds(max_run_seconds),
            wait_usage_limit=_clean_switch(wait_usage_limit, "Wait for a usage limit to reset"),
        )
        if actor != "user":
            _check_limits_not_loosened(None, record)
        with self._transaction() as db:
            db.execute(
                """
                INSERT INTO triggers (
                    id, kind, source_path, workflow_id, label, config, overlap, missed,
                    inputs, enabled, token_hash, created_at, updated_at, baseline_at,
                    daily_max_cost_usd, daily_max_tokens, run_unapproved, max_run_seconds,
                    wait_usage_limit
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    record.daily_max_cost_usd,
                    record.daily_max_tokens,
                    int(record.run_unapproved),
                    record.max_run_seconds or 0,
                    int(record.wait_usage_limit),
                ),
            )
            self._write_approval(db, record.id, snapshot, moment if snapshot else None, approved_by)
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
        daily_max_cost_usd: Any = _UNCHANGED,
        daily_max_tokens: Any = _UNCHANGED,
        run_unapproved: bool | None = None,
        max_run_seconds: Any = _UNCHANGED,
        wait_usage_limit: bool | None = None,
        actor: str | None = None,
        now: datetime | None = None,
    ) -> TriggerRecord:
        """Change a trigger. Options left as None stay the same.

        For the daily limits and the Time limit (``max_run_seconds``), None or 0 removes
        the limit; leave them out to keep them.
        This never approves the workflow, even for the user: changes waiting for review
        stay waiting until ``approve_trigger``, so changing or turning on a trigger can't
        approve a change nobody looked at. Only the user may turn on ``run_unapproved``,
        and only the user may raise or remove a limit: any other actor can only lower one.
        """
        if run_unapproved and actor != "user":
            raise UserOnlyError(USER_ONLY)
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
        if daily_max_cost_usd is not _UNCHANGED:
            updated = replace(updated, daily_max_cost_usd=clean_daily_cost(daily_max_cost_usd))
        if daily_max_tokens is not _UNCHANGED:
            updated = replace(updated, daily_max_tokens=clean_daily_tokens(daily_max_tokens))
        if enabled is not None and bool(enabled) != current.enabled:
            # A trigger that was off didn't miss anything while it was off.
            updated = replace(updated, enabled=bool(enabled), baseline_at=moment)
        if run_unapproved is not None:
            updated = replace(updated, run_unapproved=bool(run_unapproved))
        if max_run_seconds is not _UNCHANGED:
            updated = replace(updated, max_run_seconds=clean_max_run_seconds(max_run_seconds))
        if wait_usage_limit is not None:
            updated = replace(
                updated,
                wait_usage_limit=_clean_switch(wait_usage_limit, "Wait for a usage limit to reset"),
            )
        if actor != "user":
            _check_limits_not_loosened(current, updated)
        updated = replace(updated, updated_at=moment)
        with self._transaction() as db:
            changed = db.execute(
                """
                UPDATE triggers SET label = ?, config = ?, overlap = ?, missed = ?, inputs = ?,
                    enabled = ?, updated_at = ?, baseline_at = ?, daily_max_cost_usd = ?,
                    daily_max_tokens = ?, run_unapproved = ?, max_run_seconds = ?,
                    wait_usage_limit = ?
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
                    updated.daily_max_cost_usd,
                    updated.daily_max_tokens,
                    int(updated.run_unapproved),
                    updated.max_run_seconds or 0,
                    int(updated.wait_usage_limit),
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
            db.execute("DELETE FROM approvals WHERE trigger_id = ?", (trigger_id,))
            db.execute("DELETE FROM usage_waits WHERE trigger_id = ?", (trigger_id,))
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
                db.execute("DELETE FROM approvals WHERE trigger_id = ?", (trigger_id,))
                db.execute("DELETE FROM usage_waits WHERE trigger_id = ?", (trigger_id,))
            if ids:
                self._bump(db)
        return len(ids)

    @staticmethod
    def _write_approval(
        db: sqlite3.Connection,
        trigger_id: str,
        snapshot: Snapshot | None,
        at: str | None,
        by: str,
        *,
        keep_run_mark: bool = False,
    ) -> None:
        """Save the approved version; None records a trigger still waiting for one.

        ``keep_run_mark`` keeps a run's changed-file mark, and the notice already sent for
        it, when this approval doesn't cover the marked file.
        """
        keep = 1 if keep_run_mark else 0
        db.execute(
            "INSERT INTO approvals(trigger_id, snapshot, approved_at, approved_by, notified, "
            "changed_in_run) VALUES(?, ?, ?, ?, NULL, NULL) ON CONFLICT(trigger_id) DO UPDATE "
            "SET snapshot = excluded.snapshot, approved_at = excluded.approved_at, "
            "approved_by = excluded.approved_by, "
            "notified = CASE WHEN ? AND changed_in_run IS NOT NULL THEN notified END, "
            "changed_in_run = CASE WHEN ? THEN changed_in_run END",
            (
                trigger_id,
                json.dumps(snapshot.to_json(), sort_keys=True) if snapshot else None,
                at,
                by if snapshot else None,
                keep,
                keep,
            ),
        )

    def approval(self, trigger_id: str) -> Approval | None:
        """The approved version, or None for a trigger saved before approvals existed."""
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM approvals WHERE trigger_id = ?", (trigger_id,)
            ).fetchone()
        if row is None:
            return None
        snapshot = None
        if row["snapshot"]:
            try:
                snapshot = Snapshot.from_json(json.loads(row["snapshot"]))
            except ValueError:
                snapshot = None
        changed_in_run = None
        if row["changed_in_run"]:
            try:
                found = json.loads(row["changed_in_run"])
            except ValueError:
                found = None
            changed_in_run = found if isinstance(found, dict) else {}
        return Approval(
            snapshot, row["approved_at"], row["approved_by"], row["notified"], changed_in_run
        )

    def save_approval(
        self,
        trigger_id: str,
        snapshot: Snapshot,
        *,
        actor: str,
        by: str,
        now: datetime | None = None,
        keep_run_mark: bool = False,
    ) -> None:
        """Approve ``snapshot`` as the version this trigger runs. Only the user may.

        Approving clears a run's changed-file mark (``mark_changed_in_run``) unless
        ``keep_run_mark``, for an approval that doesn't cover the marked file.
        """
        if actor != "user":
            raise UserOnlyError(USER_ONLY)
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM triggers WHERE id = ?", (trigger_id,)).fetchone() is None:
                raise TriggerNotFound(f"No trigger with ID {trigger_id!r}.")
            self._write_approval(
                db, trigger_id, snapshot, iso(now or utc_now()), by, keep_run_mark=keep_run_mark
            )
            self._bump(db)

    def mark_changed_in_run(
        self,
        trigger_id: str,
        *,
        approved_fingerprint: str,
        path: str,
        role: str,
        run_id: str,
        now: datetime | None = None,
    ) -> Approval | None:
        """Mark a trigger as changed: a run found ``path`` changed right before using it.

        Only while the approval is still the one the run checked against
        (``approved_fingerprint``), so a version approved during the run stays approved.
        The first mark of a run is kept. Returns the marked approval, or None.
        """
        with self._transaction() as db:
            row = db.execute(
                "SELECT snapshot, changed_in_run FROM approvals WHERE trigger_id = ?",
                (trigger_id,),
            ).fetchone()
            if row is None or not row["snapshot"]:
                return None
            try:
                current = Snapshot.from_json(json.loads(row["snapshot"]))
            except ValueError:
                current = None
            if current is None or current.fingerprint != approved_fingerprint:
                return None
            if not row["changed_in_run"]:
                mark = {"path": path, "role": role, "runId": run_id, "at": iso(now or utc_now())}
                db.execute(
                    "UPDATE approvals SET changed_in_run = ? WHERE trigger_id = ?",
                    (json.dumps(mark, sort_keys=True), trigger_id),
                )
                self._bump(db)
        return self.approval(trigger_id)

    def set_notified(self, trigger_id: str, fingerprint: str) -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE approvals SET notified = ? WHERE trigger_id = ?", (fingerprint, trigger_id)
            )

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
                DELETE FROM events WHERE trigger_id = ? AND status != 'waiting' AND id <= (
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
        at: datetime | None = None,
    ) -> None:
        assignments: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("at", iso(at) if at else None),
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
        """Mark runs left unfinished by a scheduler that is no longer running.

        A run waiting for a usage limit to reset stays waiting: its wait is saved, and
        the next scheduler resumes it. Only a waiting entry whose wait is gone closes.
        """
        with self._transaction() as db:
            return int(
                db.execute(
                    "UPDATE events SET status = 'interrupted', message = ? "
                    "WHERE status IN ('running', 'queued') OR (status = 'waiting' AND "
                    "id NOT IN (SELECT event_id FROM usage_waits))",
                    (message,),
                ).rowcount
            )

    def add_usage_wait(self, wait: UsageWait, *, message: str) -> None:
        """Save a run's wait for a usage limit to reset, and show it in the history."""
        with self._transaction() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO usage_waits (
                    event_id, trigger_id, source_path, run_id, record_path, provider,
                    resume_at, waits, error, record_sha256, values_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    wait.event_id,
                    wait.trigger_id,
                    wait.source_path,
                    wait.run_id,
                    wait.record_path,
                    wait.provider,
                    wait.resume_at,
                    wait.waits,
                    wait.error,
                    wait.record_sha256,
                    wait.values_sha256,
                ),
            )
            db.execute(
                "UPDATE events SET status = 'waiting', run_id = ?, message = ? WHERE id = ?",
                (wait.run_id, message, wait.event_id),
            )

    def usage_waits(self, *, source_path: str | Path | None = None) -> list[UsageWait]:
        """Runs waiting for a usage limit to reset, soonest first."""
        clause, values = "", []
        if source_path is not None:
            clause = "WHERE source_path = ?"
            values.append(str(Path(source_path).expanduser().resolve()))
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM usage_waits {clause} ORDER BY resume_at, event_id", values
            ).fetchall()
        return [_usage_wait(row) for row in rows]

    def take_usage_wait(self, event_id: int) -> UsageWait | None:
        """Remove a wait as its run resumes or ends; None when it was already gone."""
        with self._transaction() as db:
            row = db.execute("SELECT * FROM usage_waits WHERE event_id = ?", (event_id,)).fetchone()
            if row is None:
                return None
            db.execute("DELETE FROM usage_waits WHERE event_id = ?", (event_id,))
        return _usage_wait(row)

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

    def pause_state(self) -> TriggerPause:
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'pause'").fetchone()
        return _pause_from_json(row["value"]) if row else TriggerPause()

    def set_paused(self, paused: bool, *, now: datetime | None = None) -> TriggerPause:
        """Pause or resume every trigger. Each trigger's own on/off stays as it is.

        Pausing twice keeps the first pause's start, and resuming when nothing is
        paused changes nothing.
        """
        moment = iso(now or utc_now())
        with self._transaction() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'pause'").fetchone()
            current = _pause_from_json(row["value"]) if row else TriggerPause()
            if bool(paused) == current.paused:
                return current
            if paused:
                updated = TriggerPause(True, moment, current.windows)
            else:
                start = current.since or moment
                windows = (*current.windows, (start, moment))[-PAUSE_WINDOWS_KEPT:]
                updated = TriggerPause(False, None, windows)
            db.execute(
                "INSERT INTO settings(key, value) VALUES('pause', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (
                    json.dumps(
                        {
                            "paused": updated.paused,
                            "since": updated.since,
                            "windows": [list(item) for item in updated.windows],
                        },
                        sort_keys=True,
                    ),
                ),
            )
            self._bump(db)
        return updated

    def daily_budget(self) -> DailyBudget:
        with self._connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = 'daily_budget'").fetchone()
        if row is None:
            return DailyBudget()
        try:
            value = json.loads(row["value"])
            return DailyBudget(
                clean_daily_cost(value.get("maxCostUsd")),
                clean_daily_tokens(value.get("maxTokens")),
            )
        except (AttributeError, TypeError, ValueError):
            # A damaged record must not stop triggers. Show no limit rather than fail.
            return DailyBudget()

    def set_daily_budget(
        self, *, max_cost_usd: Any = _UNCHANGED, max_tokens: Any = _UNCHANGED
    ) -> DailyBudget:
        """Set the computer-wide daily limit. None or 0 removes that part of it."""
        current = self.daily_budget()
        updated = DailyBudget(
            current.max_cost_usd if max_cost_usd is _UNCHANGED else clean_daily_cost(max_cost_usd),
            current.max_tokens if max_tokens is _UNCHANGED else clean_daily_tokens(max_tokens),
        )
        with self._transaction() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES('daily_budget', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(updated.to_payload(), sort_keys=True),),
            )
            self._bump(db)
        return updated

    def start_spend(
        self,
        run_id: str,
        trigger_id: str,
        started_at: datetime,
        *,
        reserved_cost_usd: float | None = None,
        reserved_tokens: int | None = None,
    ) -> None:
        """Note a triggered run as started. Its usage counts toward the day it started.

        Until it finishes, the run counts as having used its cap (``reserved_*``, the
        day's remainder when it started), so a run started beside it sees that much less
        left instead of the same remainder again.
        """
        cutoff = iso(started_at - timedelta(days=SPEND_KEPT_DAYS))
        with self._transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO spend(run_id, trigger_id, started_at, cost_usd, tokens, "
                "finished, reserved_cost_usd, reserved_tokens) "
                "VALUES(?, ?, ?, NULL, NULL, 0, ?, ?)",
                (run_id, trigger_id, iso(started_at), reserved_cost_usd, reserved_tokens),
            )
            db.execute("DELETE FROM spend WHERE started_at < ?", (cutoff,))

    def finish_spend(self, run_id: str, *, cost_usd: float | None, tokens: int | None) -> None:
        """Record what a triggered run used. None means its provider didn't report it."""
        with self._transaction() as db:
            db.execute(
                "UPDATE spend SET cost_usd = ?, tokens = ?, finished = 1 WHERE run_id = ?",
                (cost_usd, tokens, run_id),
            )

    def close_open_spend(self) -> None:
        """Runs a stopped scheduler left unfinished count with what they last reported."""
        with self._transaction() as db:
            db.execute("UPDATE spend SET finished = 1 WHERE finished = 0")

    def spent_since(
        self, since: datetime, *, trigger_id: str | None = None, reserved: bool = False
    ) -> DailySpend:
        """Usage of triggered runs started at or after ``since``, of one trigger or all.

        With ``reserved``, a run still running counts as at least the cap it started
        with, for deciding whether another run may start; screens show what was used.
        """
        clause = "WHERE started_at >= ?"
        values: list[Any] = [iso(since)]
        if trigger_id is not None:
            clause += " AND trigger_id = ?"
            values.append(trigger_id)
        totals = (
            "COALESCE(SUM(CASE WHEN finished = 0 "
            "THEN MAX(COALESCE(cost_usd, 0), COALESCE(reserved_cost_usd, 0)) "
            "ELSE cost_usd END), 0) AS cost, "
            "COALESCE(SUM(CASE WHEN finished = 0 "
            "THEN MAX(COALESCE(tokens, 0), COALESCE(reserved_tokens, 0)) "
            "ELSE tokens END), 0) AS tokens, "
            if reserved
            else "COALESCE(SUM(cost_usd), 0) AS cost, COALESCE(SUM(tokens), 0) AS tokens, "
        )
        with self._connect() as db:
            row = db.execute(
                f"SELECT {totals}"
                "COUNT(*) AS runs, "
                "SUM(CASE WHEN finished = 1 AND cost_usd IS NULL THEN 1 ELSE 0 END) AS unknown, "
                f"SUM(CASE WHEN finished = 0 THEN 1 ELSE 0 END) AS running FROM spend {clause}",
                values,
            ).fetchone()
        return DailySpend(
            float(row["cost"] or 0.0),
            int(row["tokens"] or 0),
            int(row["runs"] or 0),
            int(row["unknown"] or 0),
            int(row["running"] or 0),
        )

    def claim_limit_notice(self, scope: str, day: str) -> bool:
        """True the first time ``scope`` reaches its limit on ``day``, so one message goes out."""
        with self._transaction() as db:
            row = db.execute(
                "SELECT value FROM settings WHERE key = 'daily_limit_notices'"
            ).fetchone()
            try:
                sent = dict(json.loads(row["value"])) if row else {}
            except (TypeError, ValueError):
                sent = {}
            if sent.get(scope) == day:
                return False
            sent[scope] = day
            # Keep the record small: only scopes noticed in the last few days matter.
            kept = dict(sorted(sent.items(), key=lambda item: str(item[1]))[-200:])
            db.execute(
                "INSERT INTO settings(key, value) VALUES('daily_limit_notices', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps(kept, sort_keys=True),),
            )
        return True

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


def _pause_from_json(raw: str) -> TriggerPause:
    try:
        value = json.loads(raw)
        windows = tuple(
            (str(start), str(end))
            for start, end in value.get("windows") or []
            if parse_iso(str(start)) <= parse_iso(str(end))
        )
        since = value.get("since")
        paused = value.get("paused") is True
        if paused:
            since = str(since) if isinstance(since, str) else iso(utc_now())
            parse_iso(since)
        return TriggerPause(paused, since if paused else None, windows)
    except (AttributeError, TypeError, ValueError):
        # A damaged record must not stop triggers being listed or resumed.
        return TriggerPause()


def limit_timezone(record: TriggerRecord) -> str:
    """The time zone whose midnight starts a trigger's day: its schedule's, or this computer's."""
    if record.kind == "cron":
        name = str(record.config.get("timezone") or "")
        if name:
            return name
    return system_timezone()


def day_bounds(timezone: str, now: datetime) -> tuple[datetime, datetime, str]:
    """The start and end of ``now``'s day in ``timezone``, in UTC, and the date as text."""
    try:
        zone: tzinfo = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        zone = UTC
    local = now.astimezone(zone)
    start = datetime.combine(local.date(), datetime.min.time(), tzinfo=zone)
    end = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC), local.date().isoformat()


@dataclass(frozen=True, slots=True)
class DailyLimitState:
    """One daily limit, a trigger's or the computer's, and how much of it today has used."""

    scope: Literal["trigger", "computer"]
    timezone: str
    day: str
    resets_at: datetime
    max_cost_usd: float | None
    max_tokens: int | None
    spend: DailySpend

    @property
    def remaining_cost_usd(self) -> float | None:
        if self.max_cost_usd is None:
            return None
        return max(0.0, self.max_cost_usd - self.spend.cost_usd)

    @property
    def remaining_tokens(self) -> int | None:
        if self.max_tokens is None:
            return None
        return max(0, self.max_tokens - self.spend.tokens)

    @property
    def reached(self) -> bool:
        return (self.max_cost_usd is not None and self.spend.cost_usd >= self.max_cost_usd) or (
            self.max_tokens is not None and self.spend.tokens >= self.max_tokens
        )

    def used_text(self) -> str:
        """Such as ``$1.20 of $5.00 and 12,000 of 50,000 tokens used today``."""
        from gofer.rattish.run_usage import format_tokens, format_usd

        parts = []
        if self.max_cost_usd is not None:
            parts.append(f"{format_usd(self.spend.cost_usd)} of {format_usd(self.max_cost_usd)}")
        if self.max_tokens is not None:
            parts.append(
                f"{format_tokens(self.spend.tokens)} of {format_tokens(self.max_tokens)} tokens"
            )
        text = " and ".join(parts) + " used today"
        if self.spend.unknown_cost_runs and self.max_cost_usd is not None:
            text += " (some runs didn't report their cost)"
        return text

    def reached_message(self) -> str:
        who = (
            "This trigger's runs" if self.scope == "trigger" else "Triggered runs on this computer"
        )
        return (
            f"{DAILY_LIMIT_SKIP_MESSAGE}. {who} reached the daily limit: {self.used_text()}. "
            f"New triggered starts are skipped until midnight ({self.timezone})."
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "timezone": self.timezone,
            "day": self.day,
            "resetsAt": iso(self.resets_at),
            "maxCostUsd": self.max_cost_usd,
            "maxTokens": self.max_tokens,
            "spentCostUsd": round(self.spend.cost_usd, 6),
            "spentTokens": self.spend.tokens,
            "runs": self.spend.runs,
            "runningRuns": self.spend.running,
            "unknownCostRuns": self.spend.unknown_cost_runs,
            "remainingCostUsd": self.remaining_cost_usd,
            "remainingTokens": self.remaining_tokens,
            "reached": self.reached,
            "summary": self.used_text(),
        }


def trigger_limit_state(
    store: TriggerStore,
    record: TriggerRecord,
    now: datetime | None = None,
    *,
    reserved: bool = False,
) -> DailyLimitState | None:
    """Today's use of a trigger's own daily limit, or None when it has none."""
    if not record.has_daily_limit:
        return None
    timezone = limit_timezone(record)
    start, end, day = day_bounds(timezone, now or utc_now())
    return DailyLimitState(
        "trigger",
        timezone,
        day,
        end,
        record.daily_max_cost_usd,
        record.daily_max_tokens,
        store.spent_since(start, trigger_id=record.id, reserved=reserved),
    )


def computer_limit_state(
    store: TriggerStore,
    now: datetime | None = None,
    budget: DailyBudget | None = None,
    *,
    reserved: bool = False,
) -> DailyLimitState | None:
    """Today's use of the computer-wide daily limit, or None when there is none."""
    budget = budget or store.daily_budget()
    if not budget.active:
        return None
    timezone = system_timezone()
    start, end, day = day_bounds(timezone, now or utc_now())
    return DailyLimitState(
        "computer",
        timezone,
        day,
        end,
        budget.max_cost_usd,
        budget.max_tokens,
        store.spent_since(start, reserved=reserved),
    )


@dataclass(frozen=True, slots=True)
class DailyAllowance:
    """What a triggered run starting now may use: the least left of any daily limit."""

    states: tuple[DailyLimitState, ...] = ()

    @property
    def reached(self) -> DailyLimitState | None:
        return next((state for state in self.states if state.reached), None)

    @property
    def max_cost_usd(self) -> float | None:
        left = [s.remaining_cost_usd for s in self.states if s.remaining_cost_usd is not None]
        return min(left) if left else None

    @property
    def max_tokens(self) -> int | None:
        left = [s.remaining_tokens for s in self.states if s.remaining_tokens is not None]
        return min(left) if left else None


def daily_allowance(
    store: TriggerStore, record: TriggerRecord, now: datetime | None = None
) -> DailyAllowance:
    moment = now or utc_now()
    # Runs still running count as their whole cap, so runs that overlap can't together
    # pass a limit.
    found = (
        trigger_limit_state(store, record, moment, reserved=True),
        computer_limit_state(store, moment, reserved=True),
    )
    states = [state for state in found if state is not None]
    return DailyAllowance(tuple(states))


_sandbox_lock = threading.Lock()
_sandbox_cache: dict[tuple[str, str], tuple[tuple[Any, ...], list[dict[str, str]]]] = {}


def _file_stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _workflow_stamp(source: Path, data_dir: Path) -> tuple[Any, ...] | None:
    """What a Triggers-section answer depends on: the source, the provider profiles and
    Rem's MCP server list. None when the source is gone."""
    source_stat = _file_stamp(source)
    if source_stat is None:
        return None
    return (
        source_stat,
        _file_stamp(data_dir / "provider-profiles.json"),
        _file_stamp(data_dir / "rem-resources.json"),
        sys.platform,
    )


def unsandboxed_steps(source_path: str | Path, data_dir: Path) -> list[dict[str, str]]:
    """The agent steps and MCP servers a triggered run of this workflow would run unsandboxed.

    Each item names the node, the provider (or MCP server) and why. The Triggers section
    polls this, so the answer is kept until the source, the provider profiles or Rem's
    MCP server list change.
    """
    from gofer.rattish.agent_sandbox import workflow_unsandboxed

    source = Path(source_path).expanduser().resolve()
    stamp = _workflow_stamp(source, data_dir)
    if stamp is None:
        return []
    key = (str(source), str(data_dir))
    with _sandbox_lock:
        cached = _sandbox_cache.get(key)
        if cached is not None and cached[0] == stamp:
            return [dict(item) for item in cached[1]]
    try:
        _, _, _, ir = _compile_for_trigger(source, data_dir)
    except TriggerError:
        return []
    found = workflow_unsandboxed(ir, data_dir)
    with _sandbox_lock:
        _sandbox_cache[key] = (stamp, found)
    return [dict(item) for item in found]


_widened_cache: dict[tuple[str, str], tuple[tuple[Any, ...], list[dict[str, str]]]] = {}


def widened_steps(source_path: str | Path, data_dir: Path) -> list[dict[str, str]]:
    """The agent steps whose own settings widen their sandbox, and how.

    Extra paths outside the project and ``network: any`` come from the workflow file
    itself, so a cloned or edited workflow could reach more than the user expects.
    Adding a trigger asks to confirm while this list isn't empty. Cached like
    ``unsandboxed_steps``.
    """
    from gofer.rattish.agent_sandbox import workflow_widened

    source = Path(source_path).expanduser().resolve()
    stamp = _workflow_stamp(source, data_dir)
    if stamp is None:
        return []
    key = (str(source), str(data_dir))
    with _sandbox_lock:
        cached = _widened_cache.get(key)
        if cached is not None and cached[0] == stamp:
            return [dict(item) for item in cached[1]]
    try:
        _, _, _, ir = _compile_for_trigger(source, data_dir)
    except TriggerError:
        return []
    found = workflow_widened(ir, data_dir)
    with _sandbox_lock:
        _widened_cache[key] = (stamp, found)
    return [dict(item) for item in found]


_model_lock = threading.Lock()
_model_cache: dict[tuple[str, str], tuple[tuple[Any, ...], list[dict[str, Any]]]] = {}


def model_notices(source_path: str | Path, data_dir: Path) -> list[dict[str, Any]]:
    """Agent steps whose model is deprecated or already retired.

    Each item names the node, the provider, the model, its retirement date and the
    replacement. It reads the pinned deprecation list only, never the network, because
    the Triggers section polls it; preflight also checks the provider's model list.
    """
    from gofer.core.model_lifecycle import pinned_problem, today_utc

    source = Path(source_path).expanduser().resolve()
    stamp = _workflow_stamp(source, data_dir)
    if stamp is None:
        return []
    stamp = (*stamp, today_utc().isoformat())
    key = (str(source), str(data_dir))
    with _model_lock:
        cached = _model_cache.get(key)
        if cached is not None and cached[0] == stamp:
            return [dict(item) for item in cached[1]]
    try:
        _, _, _, ir = _compile_for_trigger(source, data_dir)
    except TriggerError:
        return []
    from gofer.core.provider_profiles import resolve_provider_settings
    from gofer.rattish.provider_fallback import provider_choices
    from gofer.rattish.provider_runtime import runtime_subscription_id

    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for node in ir.get("nodes") or []:
        if "provider" not in (node.get("resolutions") or {}):
            continue
        for choice in provider_choices(node):
            try:
                settings = resolve_provider_settings(
                    agent_subscription=runtime_subscription_id(choice["provider_id"]),
                    profile_name=choice.get("profile"),
                    operation_model=choice.get("model"),
                    data_dir=data_dir,
                )
            except (KeyError, ValueError):
                continue
            problem = pinned_problem(settings)
            if problem is None:
                continue
            item_key = (str(node["id"]), str(choice["provider_id"]), problem.model)
            if item_key in seen:
                continue
            seen.add(item_key)
            found.append({"node": item_key[0], "provider": item_key[1], **problem.to_json()})
    with _model_lock:
        _model_cache[key] = (stamp, found)
    return [dict(item) for item in found]


def cost_report_warnings(source_path: str | Path, data_dir: Path) -> list[str]:
    """Why a daily cost limit may end this workflow's triggered runs early.

    A run with a cost limit ends at the first call that doesn't report its cost, the
    same rule as ``max-cost-usd``. Claude Code reports cost and a local model server
    counts as $0; other providers may not report it.
    """
    try:
        _, _, _, ir = _compile_for_trigger(source_path, data_dir)
    except TriggerError:
        return []
    try:
        from gofer.core.provider_profiles import load_provider_profiles

        profiles = load_provider_profiles(data_dir)
    except Exception:  # noqa: BLE001 - a warning must never stop a save
        profiles = {}
    unreported: dict[str, list[str]] = {}
    for node in ir.get("nodes") or []:
        node_id = str(node.get("id") or "")
        if node.get("type") in {"rem-prompt", "rem-task"}:
            unreported.setdefault("Rem", []).append(node_id)
            continue
        resolution = (node.get("resolutions") or {}).get("provider")
        if not isinstance(resolution, Mapping):
            continue
        for entry in [resolution, *(resolution.get("fallback") or [])]:
            if not isinstance(entry, Mapping):
                continue
            provider = str(entry.get("provider_id") or entry.get("provider") or "")
            profile = profiles.get(str(entry.get("profile") or ""))
            if provider in COST_REPORTING_PROVIDERS or (profile is not None and profile.local):
                continue
            nodes = unreported.setdefault(provider or "an unknown provider", [])
            if node_id not in nodes:
                nodes.append(node_id)
    warnings = []
    for provider, nodes in sorted(unreported.items()):
        names = ", ".join(repr(item) for item in nodes[:5])
        names += " and more" if len(nodes) > 5 else ""
        warnings.append(
            f"{'Node' if len(nodes) == 1 else 'Nodes'} {names} use {provider}, which may not "
            "report what a call costs. Under a daily cost limit a run ends at the first call "
            "with no cost, as with max-cost-usd. Use a daily token limit for it instead."
        )
    return warnings


def next_run_at(record: TriggerRecord, now: datetime | None = None) -> str | None:
    if record.kind != "cron" or not record.enabled:
        return None
    try:
        schedule = parse_cron(record.config["expression"], record.config["timezone"])
    except TriggerError:
        # Saved before a check existed, or its time zone left this computer's
        # database. The scheduler reports why; listing triggers must still work.
        return None
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


def approval_status(
    store: TriggerStore,
    record: TriggerRecord,
    *,
    snapshots: dict[str, Snapshot] | None = None,
) -> ApprovalStatus:
    """Whether ``record`` may run its workflow as it is now.

    A trigger saved before approvals existed approves the workflow as it is the first
    time this is asked, so upgrading doesn't stop triggers that were running.
    ``snapshots`` shares one snapshot per workflow among that workflow's triggers.
    """
    approval = store.approval(record.id)
    known = approval.snapshot.files if approval and approval.snapshot else ()
    current = (snapshots or {}).get(record.source_path)
    if current is None or (known and not current.complete):
        current = take_snapshot(record.source_path, store.data_dir, known=known)
        if snapshots is not None and current.complete:
            snapshots[record.source_path] = current
    if current.entry_missing():
        return ApprovalStatus("missing", current, approval)
    if approval is None:
        try:
            store.save_approval(record.id, current, actor="user", by="upgrade")
        except TriggerNotFound:
            pass
        return ApprovalStatus("approved", current, store.approval(record.id))
    if approval.snapshot is None:
        return ApprovalStatus("unapproved", current, approval)
    if approval.snapshot.fingerprint != current.fingerprint or approval.changed_in_run:
        return ApprovalStatus("changed", current, approval)
    return ApprovalStatus("approved", current, approval)


def _shown_root(record: TriggerRecord, data_dir: Path) -> Path:
    """The folder a trigger's changed files are shown relative to: its project."""
    try:
        compiled = compile_rattish_file(Path(record.source_path), data_dir=data_dir)
        return Path(str(compiled.ir["source"]["project_root"]))
    except (RattishError, RattishArtifactError, OSError, ValueError):
        return Path(record.source_path).parent


def trigger_changes(store: TriggerStore, trigger_id: str) -> dict[str, Any]:
    """What changed since a trigger's workflow version was approved, file by file.

    ``unseen`` lists the files whose change this doesn't show in full: Approve this
    version asks about those a second time.
    """
    record = store.get(trigger_id)
    status = approval_status(store, record)
    approved = status.approval.snapshot if status.approval else None
    project_root = _shown_root(record, store.data_dir)
    changes = (
        describe_changes(approved, status.current, project_root=project_root)
        if status.state != "approved"
        else {
            "approvedFingerprint": approved.fingerprint if approved else None,
            "fingerprint": status.current.fingerprint,
            "files": [],
            "truncated": False,
            "unseen": [],
        }
    )
    payload = {
        "triggerId": record.id,
        "workflowId": record.workflow_id,
        "workflowName": status.current.workflow_name,
        **status.to_payload(),
        **changes,
    }
    marked = payload.get("changedDuringRun")
    if marked:
        payload["changedDuringRun"] = {
            **marked,
            "path": shown_path(str(marked.get("path") or ""), project_root),
        }
    return payload


def _unseen_in(changes: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(changes.get("unseen") or [])


def approve_trigger(
    store: TriggerStore,
    trigger_id: str,
    *,
    actor: str,
    by: str,
    expected_fingerprint: str | None = None,
    accept_unseen: bool = False,
    unseen_in: Callable[[dict[str, Any]], list[dict[str, Any]]] = _unseen_in,
) -> TriggerRecord:
    """The user's Approve this version: the trigger runs the workflow as it is now.

    ``expected_fingerprint`` is the version the user reviewed. When the workflow changed
    again since, nothing is approved and ``ApprovalConflict`` asks them to look again.

    When a file's change wasn't shown in full (its diff was cut, or it is binary or too
    large), nothing is approved unless ``accept_unseen`` says the user confirmed that a
    second time; ``UnseenChanges`` names those files. ``unseen_in`` finds them in the
    changes as ``describe_changes`` words them; the phone, which shows less, passes its
    own.
    """
    if actor != "user":
        raise UserOnlyError(USER_ONLY)
    record = store.get(trigger_id)
    approval = store.approval(trigger_id)
    approved = approval.snapshot if approval else None
    known = approved.files if approved else ()
    current = take_snapshot(record.source_path, store.data_dir, known=known)
    if expected_fingerprint is not None and expected_fingerprint != current.fingerprint:
        raise ApprovalConflict(
            "The workflow changed again since you opened its changes. Review them again."
        )
    if not accept_unseen and (approved is None or approved.fingerprint != current.fingerprint):
        changes = describe_changes(
            approved, current, project_root=_shown_root(record, store.data_dir)
        )
        unseen = unseen_in(changes)
        if unseen:
            raise UnseenChanges(unseen)
    store.save_approval(trigger_id, current, actor=actor, by=by)
    return store.get(trigger_id)


def approve_source_triggers(store: TriggerStore, source_path: str | Path, *, by: str) -> int:
    """Approve the workflow file just saved in Studio for each trigger of that workflow.

    Only the saved file's new text is approved (``approved_after_studio_save``): a prompt,
    script or child workflow that changed since its last approval keeps waiting for
    review. A trigger still waiting for its first approval gets none here, and a trigger
    saved before approvals existed is left to ``approval_status``, which approves it once.
    """
    records = store.list_triggers(source_path)
    if not records:
        return 0
    entry = str(Path(source_path).expanduser().resolve())
    approved_count = 0
    for record in records:
        approval = store.approval(record.id)
        if approval is None or approval.snapshot is None:
            continue
        current = take_snapshot(source_path, store.data_dir, known=approval.snapshot.files)
        merged = approved_after_studio_save(approval.snapshot, current, source_path)
        if merged.fingerprint == approval.snapshot.fingerprint:
            continue
        # A run's mark on another file stays: saving approved only the workflow file.
        marked = (approval.changed_in_run or {}).get("path")
        keep = bool(approval.changed_in_run) and marked != entry
        try:
            store.save_approval(record.id, merged, actor="user", by=by, keep_run_mark=keep)
        except TriggerNotFound:
            continue
        approved_count += 1
    return approved_count
