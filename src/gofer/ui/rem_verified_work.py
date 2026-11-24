"""Verified work: how often Rem's ticket work passes on its own, for one period.

Computed on request from records Raticode already keeps, so it reaches back over all stored
history and nothing new is written:

- each Rem ticket's timeline (``rem_ticket_activity``): the status entries written as a ticket
  followed its run (a review or Raticode's own project check sending it back to In progress,
  Done, Blocked and why), and the user's own moves and change requests;
- the runs still stored, for their ``repair_count``, ``project_check`` and
  ``project_check_flakes`` (check runs that failed, then passed on a rerun of the same commit;
  counted on their own, never as first-try check failures). Finished one-time runs are removed
  30 days after they end; the timeline stays;
- the usage ledger, for the tokens each ticket's runs used.

A ticket counts in a period when it reached Done (accepted) or Blocked there. It passed first
time when it was accepted with no repair and the user didn't step in. Organization tickets keep
their own records and aren't counted.

These numbers are for the user only: Settings › Usage, ``Raticode usage verified``, the phone's
Usage page and the self-work report card. They are never put into Rem's or a worker's prompt,
so Rem can't steer by them (tests/unit/test_rem_verified_work.py checks).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from gofer.core.usage_history import UsageRange, _floor, _next, bucket_for
from gofer.ui import turn_failures
from gofer.ui.rem_project_checks import FEEDBACK_START
from gofer.utils.paths import get_data_dir

log = logging.getLogger(__name__)

STORE_FILE = "rem-home.sqlite3"
# Why a ticket was blocked, as Usage › Verified work names it.
BLOCK_CAUSES = {
    "project_check": "Project check kept failing",
    "decisions": "Broke a project decision",
    "review": "Review checks failed",
    "question": "Waited for your answer",
    # A failed task, by what its message says (turn_failures.failure_cause).
    "no_activity": f"No activity for {turn_failures.TURN_IDLE_SECONDS // 60} minutes",
    "turn_limit": f"Reached the {turn_failures.TURN_LIMIT_SECONDS // 60}-minute turn limit",
    "usage_limit": "Usage limit waits used up",
    "provider_error": "Provider error",
    "not_started": "Couldn't start",
    "failed": "Other failure",
    "stopped": "Task stopped",
    "unknown": "Outcome unknown",
    "other": "Other",
}
# The status entry's title (rem_ticket_activity.transition_entry), lowercased.
_TITLE_CAUSES = {
    "blocked: review checks failed": "review",
    "blocked: waiting for your answer": "question",
    "blocked: task failed": "failed",
    "blocked: task stopped": "stopped",
    "blocked: outcome unknown": "unknown",
}
# Blocked only while it waits for an event or for whoever paused it: not stuck.
_NOT_STUCK = {
    "blocked: sleeping",
    "blocked: waiting",
    "blocked: paused",
    "blocked: paused for your work",
}
# What rem_home says when it stops a run after two repairs (rem_home.RemHome._execute).
_CHECK_GAVE_UP = "the project's check still fails"
_DECISIONS_GAVE_UP = "still breaks the project's decisions"
# The user's usual decisions, not stepping in: landing accepted work, declining a proposal.
_NOT_STEP_IN = {"Accepted and landed", "Cancelled: proposal declined"}
_REQUEST_CHANGES = "Requested changes"
_LANDED = "Accepted and landed"


@dataclass
class _Entry:
    ticket: str
    at: datetime
    kind: str
    author: str
    source: str
    target: str
    title: str
    run_id: str
    details: str
    body: str


@dataclass
class _Outcome:
    """A ticket reaching Done or Blocked, with what its stretch of work took until then."""

    ticket: str
    at: datetime
    accepted: bool
    repairs: int = 0
    stepped_in: bool = False
    by_hand: bool = False
    cause: str = ""
    checked: bool = False
    check_failed_first: bool = False
    # Check runs that failed, then passed on a rerun of the same commit: not first-try failures.
    flaky_checks: int = 0
    runs: frozenset[str] = frozenset()

    @property
    def first_pass(self) -> bool:
        return self.accepted and not self.repairs and not self.stepped_in and not self.by_hand


@dataclass
class _StepIn:
    ticket: str
    at: datetime
    kind: str  # request_changes or manual_move


@dataclass
class _Episode:
    """One stretch of work on a ticket: from its creation or last Done to the next Done."""

    since: datetime | None = None
    repairs: int = 0
    stepped_in: bool = False
    check_seen: bool = False
    first_repair_by_check: bool | None = None
    runs: set[str] = field(default_factory=set)


@dataclass
class _History:
    outcomes: list[_Outcome]
    step_ins: list[_StepIn]
    since: datetime | None


def _instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _cause(entry: _Entry) -> str:
    """Why a status entry blocked its ticket, or "" when it only waits."""
    title = entry.title.strip().casefold()
    if title in _NOT_STUCK:
        return ""
    body = entry.body.casefold()
    if _CHECK_GAVE_UP in body:
        return "project_check"
    if _DECISIONS_GAVE_UP in body:
        return "decisions"
    cause = _TITLE_CAUSES.get(title, "other")
    if cause == "failed":
        # The body is the run's own message, read up to 300 characters.
        return turn_failures.failure_cause(entry.body) or "failed"
    return cause


def _entries(db: sqlite3.Connection) -> list[_Entry]:
    """Status entries and the user's change requests, oldest first, without long text."""
    try:
        rows = db.execute(
            "SELECT ticket, json_extract(body,'$.at'), json_extract(body,'$.kind'), "
            "json_extract(body,'$.author'), json_extract(body,'$.from'), "
            "json_extract(body,'$.to'), json_extract(body,'$.title'), "
            "json_extract(body,'$.run_id'), substr(json_extract(body,'$.details'),1,80), "
            "substr(json_extract(body,'$.body'),1,300) FROM kanban_comments "
            "WHERE json_extract(body,'$.kind')='status' OR (json_extract(body,'$.author')='user' "
            "AND json_extract(body,'$.title')=?) ORDER BY seq",
            (_REQUEST_CHANGES,),
        ).fetchall()
    except sqlite3.OperationalError:  # A store from before ticket timelines.
        return []
    entries = []
    for ticket, at, kind, author, source, target, title, run_id, details, body in rows:
        moment = _instant(at)
        if moment is None:
            continue
        entries.append(
            _Entry(
                str(ticket),
                moment,
                str(kind or ""),
                str(author or ""),
                str(source or ""),
                str(target or ""),
                str(title or ""),
                str(run_id or ""),
                str(details or ""),
                str(body or ""),
            )
        )
    return entries


def _runs(
    db: sqlite3.Connection,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[tuple[datetime, str]]]]:
    """Stored runs' repair counts and check results, and each ticket's runs by start time."""
    runs: dict[str, dict[str, Any]] = {}
    by_ticket: dict[str, list[tuple[datetime, str]]] = {}
    try:
        rows = db.execute(
            "SELECT id, json_extract(body,'$.repair_count'), json_type(body,'$.project_check'), "
            "json_extract(body,'$.project_check.skipped'), "
            "json_extract(body,'$.self_work.ticket_id'), json_extract(body,'$.created_at'), "
            "json_extract(body,'$.project_check_flakes') FROM runs"
        ).fetchall()
    except sqlite3.OperationalError:
        return runs, by_ticket
    for identity, repairs, check, skipped, ticket, created, flakes in rows:
        runs[identity] = {
            "repairs": int(repairs or 0),
            "checked": check == "object" and not skipped,
            "flaky": int(flakes) if isinstance(flakes, int) and flakes > 0 else 0,
        }
        moment = _instant(created)
        if ticket and moment is not None:
            by_ticket.setdefault(str(ticket), []).append((moment, identity))
    return runs, by_ticket


def read_history(db: sqlite3.Connection) -> _History:
    """Every ticket outcome and user step-in in Rem's store, oldest first."""
    entries = _entries(db)
    runs, ticket_runs = _runs(db)
    outcomes: list[_Outcome] = []
    step_ins: list[_StepIn] = []
    tickets: dict[str, list[_Entry]] = {}
    for entry in entries:
        tickets.setdefault(entry.ticket, []).append(entry)
    for ticket, timeline in tickets.items():
        episode = _Episode()
        for entry in timeline:
            if entry.run_id:
                episode.runs.add(entry.run_id)
            if entry.author == "user":
                if entry.kind != "status" and entry.title == _REQUEST_CHANGES:
                    step_ins.append(_StepIn(ticket, entry.at, "request_changes"))
                    episode.stepped_in = True
                elif entry.kind == "status" and entry.title not in _NOT_STEP_IN:
                    step_ins.append(_StepIn(ticket, entry.at, "manual_move"))
                    episode.stepped_in = True
            if entry.kind != "status":
                continue
            if (
                entry.author == "system"
                and entry.source == "in_review"
                and entry.target == "in_progress"
            ):
                # A review, or Raticode's own project check, sent the work back.
                by_check = entry.details.startswith(FEEDBACK_START)
                episode.repairs += 1
                episode.check_seen = episode.check_seen or by_check
                if episode.first_repair_by_check is None:
                    episode.first_repair_by_check = by_check
            if entry.target == "done" and entry.source != "done":
                outcomes.append(
                    _snapshot(entry, episode, runs, ticket_runs.get(ticket, []), accepted=True)
                )
                episode = _Episode(since=entry.at)
            elif entry.target == "blocked" and entry.author != "user":
                cause = _cause(entry)
                if cause:
                    outcome = _snapshot(
                        entry, episode, runs, ticket_runs.get(ticket, []), accepted=False
                    )
                    outcome.cause = cause
                    outcomes.append(outcome)
            elif entry.target == "cancelled":
                episode = _Episode(since=entry.at)
    outcomes.sort(key=lambda o: o.at)
    step_ins.sort(key=lambda s: s.at)
    return _History(outcomes, step_ins, entries[0].at if entries else None)


def _snapshot(
    entry: _Entry,
    episode: _Episode,
    runs: dict[str, dict[str, Any]],
    ticket_runs: list[tuple[datetime, str]],
    *,
    accepted: bool,
) -> _Outcome:
    run = runs.get(entry.run_id) or {}
    # A run's own count is exact while it is stored; the timeline misses a repair it never saw
    # (a check that failed between two polls), and the run's count restarts after a follow-up.
    repairs = max(episode.repairs, int(run.get("repairs") or 0))
    attempts = {
        identity
        for moment, identity in ticket_runs
        if moment <= entry.at and (episode.since is None or moment > episode.since)
    }
    spent = frozenset(episode.runs | attempts | ({entry.run_id} if entry.run_id else set()))
    return _Outcome(
        ticket=entry.ticket,
        at=entry.at,
        accepted=accepted,
        repairs=repairs,
        stepped_in=episode.stepped_in,
        by_hand=accepted and entry.author == "user" and entry.title != _LANDED,
        checked=episode.check_seen or bool(run.get("checked")),
        check_failed_first=bool(episode.first_repair_by_check),
        flaky_checks=sum(int((runs.get(r) or {}).get("flaky") or 0) for r in spent),
        runs=spent,
    )


def _percent(part: int, whole: int) -> int | None:
    return round(100 * part / whole) if whole else None


def first_pass_line(first_pass: int, accepted: int) -> str:
    """The one-line summary: "31 of 40 passed review first time"."""
    if not accepted:
        return "No tickets were accepted in this period."
    noun = "ticket" if accepted == 1 else "tickets"
    return f"{first_pass} of {accepted} {noun} passed review first time"


def _in(moment: datetime, start: datetime | None, end: datetime) -> bool:
    return (start is None or moment >= start) and moment < end


def summarize(
    history: _History,
    start: datetime | None,
    end: datetime,
    *,
    usage: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The period's numbers. ``usage`` is each run's ledger total; None leaves tokens out."""
    accepted: dict[str, _Outcome] = {}
    last: dict[str, _Outcome] = {}
    blocked: dict[str, set[str]] = {}
    for outcome in history.outcomes:
        if not _in(outcome.at, start, end):
            continue
        last[outcome.ticket] = outcome
        if outcome.accepted:
            accepted[outcome.ticket] = outcome
        else:
            blocked.setdefault(outcome.ticket, set()).add(outcome.cause)
    first_pass = sum(1 for o in accepted.values() if o.first_pass)
    repairs = sum(o.repairs for o in accepted.values())
    finished = len(last)
    causes: dict[str, int] = {}
    for found in blocked.values():
        for cause in found:
            causes[cause] = causes.get(cause, 0) + 1
    checked = [o for o in last.values() if o.checked]
    failed_first = sum(1 for o in checked if o.check_failed_first)
    steps = [s for s in history.step_ins if _in(s.at, start, end)]
    numbers: dict[str, Any] = {
        "accepted": len(accepted),
        "first_pass": first_pass,
        "first_pass_percent": _percent(first_pass, len(accepted)),
        "repairs": repairs,
        "repairs_per_ticket": round(repairs / len(accepted), 2) if accepted else None,
        "accepted_by_hand": sum(1 for o in accepted.values() if o.by_hand),
        "finished": finished,
        "blocked": len(blocked),
        "blocked_percent": _percent(len(blocked), finished),
        "blocked_causes": [
            {"cause": cause, "label": BLOCK_CAUSES[cause], "tickets": count}
            for cause, count in sorted(causes.items(), key=lambda item: (-item[1], item[0]))
        ],
        "project_check": {
            "checked": len(checked),
            "failed_first": failed_first,
            "failed_first_percent": _percent(failed_first, len(checked)),
            # Counted apart: a flaky run passed on its rerun, so it never sent work back.
            "flaky": sum(o.flaky_checks for o in last.values()),
        },
        "step_ins": {
            "total": len(steps),
            "request_changes": sum(1 for s in steps if s.kind == "request_changes"),
            "manual_moves": sum(1 for s in steps if s.kind == "manual_move"),
            "tickets": len({s.ticket for s in steps}),
        },
        "line": first_pass_line(first_pass, len(accepted)),
    }
    if usage is not None:
        numbers["tokens"] = _tokens(accepted.values(), usage)
    return numbers


def _tokens(accepted: Any, usage: dict[str, dict[str, Any]]) -> dict[str, Any]:
    total = 0
    counted = 0
    partial = False
    for outcome in accepted:
        used = [usage[r] for r in outcome.runs if r in usage]
        if not used:
            continue
        counted += 1
        for item in used:
            total += int(item.get("total_tokens") or 0)
            partial = partial or bool(item.get("unknown_calls") or item.get("partial_calls"))
    return {
        "total": total,
        "tickets": counted,
        "per_accepted": round(total / counted) if counted else None,
        "partial": partial,
    }


def _trend(
    history: _History, start: datetime | None, end: datetime, zone: tzinfo
) -> dict[str, Any]:
    """First-pass counts per day, week or month across the period, for the before-picture."""
    first = start or (history.outcomes[0].at if history.outcomes else None)
    if first is None or first >= end:
        return {"bucket": "day", "buckets": []}
    bucket = bucket_for(end - first)
    bucket = "day" if bucket == "hour" else bucket
    cursor = _floor(first.astimezone(zone), bucket)
    buckets: list[dict[str, Any]] = []
    while cursor.astimezone(UTC) < end and len(buckets) < 400:
        following = _next(cursor, bucket)
        lower = max(cursor.astimezone(UTC), start) if start else cursor.astimezone(UTC)
        upper = min(following.astimezone(UTC), end)
        part = summarize(history, lower, upper)
        buckets.append(
            {
                "start": cursor.isoformat(),
                "accepted": part["accepted"],
                "first_pass": part["first_pass"],
                "repairs": part["repairs"],
                "blocked": part["blocked"],
            }
        )
        cursor = following
    return {"bucket": bucket, "buckets": buckets}


def _open(data_dir: Path) -> sqlite3.Connection | None:
    path = data_dir / STORE_FILE
    if not path.is_file():
        return None
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=15)
    db.execute("BEGIN")
    return db


def verified_work(data_dir: Path | None, period: UsageRange) -> dict[str, Any]:
    """Settings › Usage › Verified work for one period, with tokens from the usage ledger."""
    from gofer.core.usage_ledger import run_usage

    root = data_dir or get_data_dir()
    history = _History([], [], None)
    try:
        db = _open(root)
        if db is not None:
            try:
                history = read_history(db)
            finally:
                db.close()
    except sqlite3.Error as exc:
        log.warning("Could not read Rem's ticket history (%s)", type(exc).__name__)
    usage = run_usage({r for o in history.outcomes if o.accepted for r in o.runs}, root)
    return {
        "scope": "rem_tickets",
        "range": period.payload(history.since),
        "history_since": history.since.isoformat() if history.since else None,
        **summarize(history, period.start, period.end, usage=usage),
        "trend": _trend(history, period.start, period.end, period.zone),
    }


def report_numbers(db: sqlite3.Connection, start: datetime, end: datetime) -> dict[str, Any]:
    """The self-work report card's line for its own period, read in its transaction.

    Only for the card the user sees; the report's chat text, which Rem reads back as its
    history, leaves it out.
    """
    numbers = summarize(read_history(db), start, end + timedelta(microseconds=1))
    return {
        key: numbers[key]
        for key in ("accepted", "first_pass", "repairs", "blocked", "finished", "line")
    }
