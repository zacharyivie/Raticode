"""Timed self-work windows and the report of each self-work period.

A window turns self-work on for a stretch of time: from a start (now, or a later instant)
until an end instant, or until the user's tasks running when it was set have finished. While
one runs, self-work counts as on whatever the Settings > Rem switch says, and it starts work
straight away unless the user asked it to wait for 15 quiet minutes as usual. When it ends,
self-work starts nothing new; work it already started finishes as it would after the switch
is turned off, and self-work that made way for the user's work stays paused until self-work
is on again. The switch then goes back to what it was (``after_end: restore``), or to on or
off when the user said so.

Everything is stored as UTC instants in one setting (``self_work_schedule``) and moved along
by self-work's tick under Rem's lease, so a restart, a daylight-saving change or a second
backend sees the same windows and ends each of them once. Times the user names are resolved
here, against their time zone, never by the model: a time that has passed, that a clock
change skips, or that happens twice is refused with the reason.

Windows never overlap. One that starts now replaces the end of the window running now
(extending or shortening it); a later one that overlaps the running window extends it; a
later one that overlaps other upcoming windows replaces them.

A *period* is the time self-work is on, from the moment it turns on (by the switch or a
window) to the moment it turns off. When a period ends, a report of what self-work did in it
is posted in Rem's chat once, built from the run and ticket records of that period. A period
in which self-work did nothing gets one line in the running report instead of a chat report.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gofer.ui.rem_home_models import ACTIVE, ClockTime, SelfWorkRequest

SCHEDULE_KEY = "self_work_schedule"
MAX_SPAN = timedelta(days=7)
MAX_AHEAD = timedelta(days=7)
MAX_UPCOMING = 5
HISTORY = 20
REPORT_ITEMS = 30
# Ended runs stay in the report this long after they started, for runs started before a period.
OUTCOMES = {
    "succeeded": "finished",
    "paused": "paused",
    "blocked": "blocked",
    "failed": "failed",
    "outcome_unknown": "blocked",
    "cancelled": "stopped",
}


class WindowRefused(ValueError):
    """A self-work window or time that can't be applied, with the reason for the user."""


def default_zone() -> str:
    """The user's time zone: this computer's, which the desktop and its phone share."""
    from gofer.rattish.triggers import system_timezone

    return system_timezone()


def zone(name: str | None) -> ZoneInfo:
    name = (name or "").strip() or default_zone()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise WindowRefused(
            f"“{name}” isn't a time zone I know. Use a name like Asia/Irkutsk or Europe/Berlin."
        ) from exc


def instant(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def clock_label(moment: datetime) -> str:
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def when_label(moment: datetime, zone_name: str, clock: datetime) -> str:
    """``7:00 PM today (Asia/Irkutsk)``: a moment as the user reads it, in their zone."""
    place = zone(zone_name)
    local = moment.astimezone(place)
    today = clock.astimezone(place).date()
    day = (
        "today"
        if local.date() == today
        else "tomorrow"
        if local.date() == today + timedelta(days=1)
        else local.strftime("%a %-d %b")
    )
    return f"{clock_label(local)} {day} ({place.key})"


def _local(day: date, hour: int, minute: int, place: ZoneInfo) -> datetime:
    """A wall-clock time on a day, refused when a clock change skips or repeats it."""
    first = datetime(day.year, day.month, day.day, hour, minute, tzinfo=place, fold=0)
    second = first.replace(fold=1)
    shown = f"{clock_label(first)} on {day.strftime('%a %-d %b')} ({place.key})"
    back = first.astimezone(UTC).astimezone(place)
    if (back.hour, back.minute) != (hour, minute):
        raise WindowRefused(
            f"{shown} doesn't exist: the clocks skip it for daylight saving. Pick another time."
        )
    if first.utcoffset() != second.utcoffset():
        raise WindowRefused(
            f"{shown} happens twice because the clocks go back for daylight saving. "
            "Say which one, or pick another time."
        )
    return first.astimezone(UTC)


def resolve(
    when: ClockTime, *, zone_name: str | None, clock: datetime, after: datetime
) -> datetime:
    """The UTC instant a named time means, strictly after ``after``, or WindowRefused.

    Dates near year 1 or 9999 overflow when moved between zones; they are refused like any
    other time that can't be used, so the rest of the Rem turn still applies.
    """
    try:
        return _resolve(when, zone_name=zone_name, clock=clock, after=after)
    except OverflowError as exc:
        raise WindowRefused("That date is too far away. Pick a time within the next week.") from exc


def _resolve(
    when: ClockTime, *, zone_name: str | None, clock: datetime, after: datetime
) -> datetime:
    """The UTC instant a named time means, strictly after ``after``.

    A clock time with no day is the next time it comes round after ``after``; "today" and
    "tonight" are today in the zone (a small-hours time "tonight" is after midnight), and
    "tomorrow" is the day after. A time that has passed is refused rather than moved.
    """
    if when.at:
        try:
            moment = datetime.fromisoformat(when.at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise WindowRefused(f"“{when.at}” isn't a time I can read.") from exc
        if moment.tzinfo is None:
            raise WindowRefused("A time needs its time zone.")
        moment = moment.astimezone(UTC)
        if moment <= after:
            raise WindowRefused(
                f"{when_label(moment, zone_name or default_zone(), clock)} has already passed."
            )
        return moment
    place = zone(when.timezone or zone_name)
    assert when.time is not None
    hour, minute = map(int, when.time.split(":"))
    today = clock.astimezone(place).date()
    if when.date:
        try:
            day = date.fromisoformat(when.date)
        except ValueError as exc:
            raise WindowRefused(f"“{when.date}” isn't a date.") from exc
        days = [day]
    elif when.day in {"today", "tonight"}:
        small_hours = when.day == "tonight" and hour < 6
        days = [today + timedelta(days=1 if small_hours else 0)]
    elif when.day == "tomorrow":
        days = [today + timedelta(days=1)]
    else:
        start = after.astimezone(place).date()
        days = [start, start + timedelta(days=1), start + timedelta(days=2)]
    for day in days:
        moment = _local(day, hour, minute, place)
        if moment > after:
            return moment
    now_there = clock_label(clock.astimezone(place))
    raise WindowRefused(
        f"{when_label(moment, place.key, clock)} has already passed (it's {now_there} in "
        f"{place.key})."
    )


# Stored schedule -----------------------------------------------------------------------


def read(db: sqlite3.Connection) -> dict[str, Any]:
    row = db.execute("SELECT body FROM settings WHERE key=?", (SCHEDULE_KEY,)).fetchone()
    stored = json.loads(row[0]) if row else {}
    stored.setdefault("windows", [])
    stored.setdefault("history", [])
    return dict(stored)


def write(db: sqlite3.Connection, schedule: dict[str, Any]) -> None:
    schedule["history"] = schedule.get("history", [])[-HISTORY:]
    db.execute(
        "INSERT OR REPLACE INTO settings VALUES(?,?)",
        (SCHEDULE_KEY, json.dumps(schedule, sort_keys=True, separators=(",", ":"))),
    )


def _ends(window: dict[str, Any]) -> datetime | None:
    return instant(window.get("end_at"))


def active(
    schedule: dict[str, Any], clock: datetime, db: sqlite3.Connection | None = None
) -> dict[str, Any] | None:
    """The window running at ``clock``, or None.

    With ``db``, a window "until the running tasks finish" whose tasks have all ended is
    over, even before the tick takes it off the schedule: anything deciding whether to start
    work must pass the connection it read the runs with, so a task that ended a moment ago
    never lets self-work start more.
    """
    for window in schedule.get("windows") or []:
        start = instant(window["start_at"])
        end = _ends(window)
        if start and start <= clock and (end is None or clock < end):
            if db is not None and _condition_met(db, window):
                continue
            return dict(window)
    return None


def _condition_met(db: sqlite3.Connection, window: dict[str, Any]) -> bool:
    """Whether a window's end condition (the user's tasks finishing) has come true."""
    if window.get("until") != "running_done":
        return False
    return not _still_running(db, list(window.get("until_runs") or []))


def upcoming(schedule: dict[str, Any], clock: datetime) -> list[dict[str, Any]]:
    return sorted(
        (
            dict(w)
            for w in schedule.get("windows") or []
            if (instant(w["start_at"]) or clock) > clock
        ),
        key=lambda w: str(w["start_at"]),
    )


def effective(
    saved_enabled: bool,
    schedule: dict[str, Any],
    clock: datetime,
    db: sqlite3.Connection | None = None,
) -> bool:
    """Whether self-work is on: the switch, or a window running (see ``active``)."""
    return bool(saved_enabled or active(schedule, clock, db))


def running_runs(db: sqlite3.Connection, *, users_only: bool) -> list[dict[str, Any]]:
    """Rem's queued or running runs, oldest first; ``users_only`` leaves out self-work's."""
    states = sorted(ACTIVE)
    runs = [
        json.loads(row[0])
        for row in db.execute(
            f"SELECT body FROM runs WHERE state IN ({','.join('?' * len(states))}) "
            "ORDER BY json_extract(body,'$.created_at')",
            states,
        )
    ]
    if users_only:
        runs = [r for r in runs if not _self_started(r)]
    return runs


def _self_started(run: dict[str, Any]) -> bool:
    started = run.get("self_work") or {}
    return bool(started) and started.get("origin", "self_work") == "self_work"


def _still_running(db: sqlite3.Connection, run_ids: list[str]) -> list[dict[str, Any]]:
    found = []
    for identity in run_ids:
        row = db.execute("SELECT body FROM runs WHERE id=?", (identity,)).fetchone()
        if row:
            run = json.loads(row[0])
            if run["state"] in ACTIVE:
                found.append(run)
    return found


def describe(
    window: dict[str, Any], clock: datetime, db: sqlite3.Connection | None = None
) -> dict[str, Any]:
    """A window as every client shows it: the stored fields plus labels in its zone."""
    name = str(window.get("timezone") or "UTC")
    end = _ends(window)
    start = instant(window["start_at"]) or clock
    waiting = len(window.get("until_runs") or [])
    if db is not None and window.get("until") == "running_done":
        waiting = len(_still_running(db, list(window.get("until_runs") or [])))
    end_label = (
        when_label(end, name, clock)
        if end
        else f"when {_count(waiting, 'running task')} {'finishes' if waiting == 1 else 'finish'}"
    )
    after = {"restore": "then back to off", "on": "then on", "off": "then off"}[
        window.get("after_end", "restore")
    ]
    if window.get("after_end") == "restore" and window.get("saved_before"):
        after = "then back to on"
    label = (
        f"From {when_label(start, name, clock)} until {end_label}"
        if start > clock
        else f"Until {end_label}"
    )
    return {
        "id": window["id"],
        "start_at": window["start_at"],
        "end_at": window.get("end_at"),
        "until": window.get("until") or "",
        "timezone": name,
        "after_end": window.get("after_end", "restore"),
        "wait_for_idle": bool(window.get("wait_for_idle")),
        "label": label,
        "end_label": end_label,
        "after_label": after,
    }


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def view(db: sqlite3.Connection, clock: datetime) -> dict[str, Any]:
    schedule = read(db)
    running = active(schedule, clock, db)
    return {
        "timezone": default_zone(),
        "active": describe(running, clock, db) if running else None,
        "upcoming": [describe(w, clock, db) for w in upcoming(schedule, clock)],
        "last_period": schedule.get("last_period"),
    }


# Changing windows ----------------------------------------------------------------------


def _new_window(
    request: SelfWorkRequest, start: datetime, end: datetime | None, place: str, clock: datetime
) -> dict[str, Any]:
    return {
        "id": uuid4().hex,
        "start_at": start.isoformat(),
        "end_at": end.isoformat() if end else None,
        "until": request.until or "",
        "until_runs": [],
        "timezone": place,
        "after_end": request.after_end,
        "wait_for_idle": request.wait_for_idle,
        "created_at": clock.isoformat(),
        "started": False,
    }


def _overlaps(window: dict[str, Any], start: datetime, end: datetime | None) -> bool:
    other_start = instant(window["start_at"])
    other_end = _ends(window)
    if other_start is None:
        return False
    return (end is None or other_start < end) and (other_end is None or start < other_end)


def schedule_window(
    db: sqlite3.Connection, request: SelfWorkRequest, saved_enabled: bool, clock: datetime
) -> tuple[dict[str, Any], str]:
    """Record a window the user asked for; returns it and what changed, for the reply.

    Raises WindowRefused with the reason when the times can't be used.
    """
    schedule = read(db)
    place = zone((request.end or request.start or ClockTime(time="00:00")).timezone).key
    if request.start and request.start.timezone is None and request.end and request.end.timezone:
        place = zone(request.end.timezone).key
    start = (
        resolve(request.start, zone_name=place, clock=clock, after=clock)
        if request.start
        else clock
    )
    starts_now = start <= clock
    if request.until and not starts_now:
        raise WindowRefused(
            "Self-work until the running tasks finish can only start now. Name an end time "
            "for a later window."
        )
    end = resolve(request.end, zone_name=place, clock=clock, after=start) if request.end else None
    if end and end - start > MAX_SPAN:
        raise WindowRefused("A self-work window can last at most 7 days.")
    if start - clock > MAX_AHEAD:
        raise WindowRefused("A self-work window can start at most 7 days from now.")
    current = active(schedule, clock)
    windows = [dict(w) for w in schedule["windows"]]
    if request.until:
        runs = running_runs(db, users_only=True)
        if not runs:
            raise WindowRefused(
                "None of your tasks is running now, so self-work until they finish would end "
                "straight away."
            )
    after_end = request.after_end
    if starts_now and after_end == "restore" and saved_enabled and not current:
        # Self-work was already on, so "until 7 PM" can only mean it stops then.
        after_end = "off"
    if starts_now:
        if current:
            window = next(w for w in windows if w["id"] == current["id"])
            window.update(
                end_at=end.isoformat() if end else None,
                until=request.until or "",
                until_runs=[],
                timezone=place,
                after_end=after_end,
                wait_for_idle=request.wait_for_idle,
            )
            change = "changed"
        else:
            window = _new_window(request, start, end, place, clock)
            window.update(started=True, after_end=after_end, saved_before=saved_enabled)
            windows.append(window)
            change = "added"
        if request.until:
            window["until_runs"] = [r["id"] for r in running_runs(db, users_only=True)][:40]
        dropped = [w for w in windows if w["id"] != window["id"] and _overlaps(w, clock, end)]
    else:
        if current and (_ends(current) is None or start <= (_ends(current) or start)):
            window = next(w for w in windows if w["id"] == current["id"])
            current_end = _ends(window)
            if end and (current_end is None or end > current_end) and not window.get("until"):
                window["end_at"] = end.isoformat()
            window["after_end"] = after_end
            change = "extended"
            dropped = [
                w for w in windows if w["id"] != window["id"] and _overlaps(w, clock, _ends(window))
            ]
        else:
            window = _new_window(request, start, end, place, clock)
            window["saved_before"] = saved_enabled
            dropped = [w for w in windows if _overlaps(w, start, end)]
            windows.append(window)
            change = "added"
    for gone in dropped:
        windows.remove(gone)
        schedule["history"].append(
            {
                "id": gone["id"],
                "status": "replaced",
                "replaced_by": window["id"],
                "at": clock.isoformat(),
            }
        )
    if len(upcoming({"windows": windows}, clock)) > MAX_UPCOMING:
        raise WindowRefused(
            f"Self-work can have at most {MAX_UPCOMING} windows waiting. Cancel one first."
        )
    schedule["windows"] = windows
    write(db, schedule)
    return describe(window, clock, db), change


def stop_at(
    db: sqlite3.Connection, request: SelfWorkRequest, saved_enabled: bool, clock: datetime
) -> dict[str, Any] | None:
    """End the self-work running now at a time; None when there is nothing to stop."""
    schedule = read(db)
    current = active(schedule, clock)
    assert request.end is not None
    place = zone(request.end.timezone or (current or {}).get("timezone")).key
    end = resolve(request.end, zone_name=place, clock=clock, after=clock)
    if end - clock > MAX_SPAN:
        raise WindowRefused("I can stop self-work at most 7 days from now.")
    if current:
        window = next(w for w in schedule["windows"] if w["id"] == current["id"])
        window.update(end_at=end.isoformat(), until="", until_runs=[], after_end="off")
    elif saved_enabled:
        window = _new_window(
            SelfWorkRequest(action="schedule", end=request.end, after_end="off"),
            clock,
            end,
            place,
            clock,
        )
        window.update(started=True, saved_before=True)
        schedule["windows"].append(window)
    else:
        return None
    for gone in [
        w for w in schedule["windows"] if w["id"] != window["id"] and _overlaps(w, clock, end)
    ]:
        schedule["windows"].remove(gone)
        schedule["history"].append(
            {
                "id": gone["id"],
                "status": "replaced",
                "replaced_by": window["id"],
                "at": clock.isoformat(),
            }
        )
    write(db, schedule)
    return describe(window, clock, db)


def end_window(
    db: sqlite3.Connection, window_id: str, clock: datetime, *, status: str
) -> dict[str, Any] | None:
    """Take one window off the schedule now (ended or cancelled); returns it, or None."""
    schedule = read(db)
    window: dict[str, Any] | None = next(
        (w for w in schedule["windows"] if w["id"] == window_id), None
    )
    if window is None:
        return None
    schedule["windows"].remove(window)
    schedule["history"].append({"id": window_id, "status": status, "at": clock.isoformat()})
    write(db, schedule)
    return window


def settled(db: sqlite3.Connection, window_id: str) -> tuple[bool, dict[str, Any] | None]:
    """Whether a window someone waits for has ended, following replacements; else the window."""
    schedule = read(db)
    seen: set[str] = set()
    current = window_id
    while current and current not in seen:
        seen.add(current)
        window = next((w for w in schedule["windows"] if w["id"] == current), None)
        if window is not None:
            return False, window
        record = next((h for h in reversed(schedule["history"]) if h["id"] == current), None)
        current = (record or {}).get("replaced_by") or ""
    return True, None


def waitable(db: sqlite3.Connection, clock: datetime) -> dict[str, Any] | None:
    """The window a task "after self-work ends" waits for: the running one, else the next."""
    schedule = read(db)
    return active(schedule, clock) or next(iter(upcoming(schedule, clock)), None)


# Moving windows along ------------------------------------------------------------------


def advance(
    db: sqlite3.Connection, saved: dict[str, Any], clock: datetime
) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]:
    """Start and end windows that are due; returns the saved settings and what happened.

    Each event is ``(kind, window)`` with kind ``started``, ``ended`` or ``missed`` (a window
    that began and ended while Raticode wasn't running). Ending a window applies its
    ``after_end`` to the saved switch. Called inside one transaction, so each window starts
    and ends once.
    """
    schedule = read(db)
    events: list[tuple[str, dict[str, Any]]] = []
    changed = False
    for window in sorted(schedule["windows"], key=lambda w: str(w["start_at"])):
        start = instant(window["start_at"]) or clock
        end = _ends(window)
        done = bool(end and clock >= end)
        if start <= clock:
            done = done or _condition_met(db, window)
        if start > clock:
            continue
        if not window.get("started") and not done:
            window["started"] = True
            window.setdefault("saved_before", bool(saved.get("enabled")))
            changed = True
            events.append(("started", dict(window)))
            continue
        if not done:
            continue
        schedule["windows"].remove(window)
        missed = not window.get("started")
        schedule["history"].append(
            {"id": window["id"], "status": "missed" if missed else "ended", "at": clock.isoformat()}
        )
        how = window.get("after_end", "restore")
        if how in {"on", "off"} and saved.get("enabled") is not (how == "on"):
            saved = {**saved, "enabled": how == "on", "revision": int(saved["revision"]) + 1}
        changed = True
        events.append(("missed" if missed else "ended", dict(window)))
    if changed:
        write(db, schedule)
    return saved, events


# Periods and their reports --------------------------------------------------------------


def begin_period(db: sqlite3.Connection, clock: datetime) -> None:
    schedule = read(db)
    if schedule.get("period"):
        return
    seq = int(db.execute("SELECT COALESCE(MAX(seq),0) FROM self_work_report").fetchone()[0])
    schedule["period"] = {"id": uuid4().hex, "started_at": clock.isoformat(), "from_seq": seq}
    write(db, schedule)


def current_period(db: sqlite3.Connection) -> dict[str, Any] | None:
    period = read(db).get("period")
    return dict(period) if period else None


def build_report(
    db: sqlite3.Connection, period: dict[str, Any], ended_at: datetime, zone_name: str
) -> dict[str, Any]:
    """What self-work did from a period's start to ``ended_at``, from its records only.

    Runs are self-work's own (origin self_work) that started in the period, or started before
    it and changed state in it. Tickets and proposals come from the running report's rows
    written in the period.
    """
    started_at = instant(period["started_at"]) or ended_at
    items: list[dict[str, Any]] = []
    counts = dict.fromkeys(("started", "finished", "paused", "blocked", "running", "stopped"), 0)
    for (body,) in db.execute(
        "SELECT body FROM runs WHERE json_extract(body,'$.self_work') IS NOT NULL "
        "ORDER BY json_extract(body,'$.created_at')"
    ):
        run = json.loads(body)
        if not _self_started(run):
            continue
        created = instant(run.get("created_at")) or started_at
        finished = instant(run.get("finished_at"))
        paused_at = instant((run.get("paused") or {}).get("at"))
        new = started_at <= created <= ended_at
        moved = any(
            moment is not None and started_at <= moment <= ended_at
            for moment in (finished, paused_at)
        )
        if not (new or moved or (created < started_at and run["state"] in ACTIVE)):
            continue
        outcome = "running" if run["state"] in ACTIVE else OUTCOMES.get(run["state"], run["state"])
        if new:
            counts["started"] += 1
        if outcome == "finished":
            counts["finished"] += 1
        elif outcome == "paused":
            counts["paused"] += 1
        elif outcome in {"blocked", "failed"}:
            counts["blocked"] += 1
        elif outcome == "running":
            counts["running"] += 1
        elif outcome == "stopped":
            counts["stopped"] += 1
        items.append(
            {
                "task_id": run["task_id"],
                "run_id": run["id"],
                "thread_id": run.get("thread_id") or "",
                "title": str(run["recipe"].get("title") or "a task")[:160],
                "kind": str((run.get("self_work") or {}).get("kind") or "task")[:20],
                "outcome": outcome,
                "summary": str(run.get("summary_short") or "")[:300],
                "tickets": list(run.get("tickets") or [])[:10],
                "new": new,
            }
        )
    rows = [
        json.loads(r[0])
        for r in db.execute(
            "SELECT body FROM self_work_report WHERE seq>? ORDER BY seq",
            (int(period.get("from_seq") or 0),),
        )
    ]
    rows = [r for r in rows if (instant(r.get("at")) or ended_at) <= ended_at]
    numbers = {
        row[0]: row[1]
        for row in db.execute(
            "SELECT json_extract(body,'$.id'), json_extract(body,'$.number') FROM kanban_tickets"
        )
    }

    def key(ticket_id: Any) -> str:
        number = numbers.get(ticket_id)
        return f"REM-{number}" if number else ""

    added = [r for r in rows if r.get("kind") == "ticket_added"]
    proposals = [
        key(r.get("ticket_id")) or str(r.get("title") or "") for r in added if r.get("proposed")
    ]
    tickets_created = [
        key(r.get("ticket_id")) or str(r.get("title") or "") for r in added if not r.get("proposed")
    ]
    tickets_moved = sorted(
        {
            str(r.get("ticket_id"))
            for r in rows
            if r.get("kind") == "work_done" and r.get("ticket_id")
        }
        | {
            str(ref.get("ticket_id"))
            for item in items
            if item["new"]
            for ref in item["tickets"]
            if ref.get("ticket_id") and ref.get("role") == "worked"
        }
    )
    merged = [r for r in rows if r.get("kind") == "duplicate_merged"]
    counts.update(
        tickets_created=len(tickets_created),
        tickets_moved=len(tickets_moved),
        proposals=len(proposals),
        duplicates_merged=len(merged),
    )
    return {
        "period_id": period["id"],
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "timezone": zone_name,
        "counts": counts,
        "items": [{k: v for k, v in item.items() if k != "new"} for item in items][-REPORT_ITEMS:],
        "tickets_created": [c for c in tickets_created if c][:20],
        "proposals": [p for p in proposals if p][:20],
        "tickets_moved": [key(t) for t in tickets_moved if key(t)][:20],
    }


def empty(report: dict[str, Any]) -> bool:
    counts = report["counts"]
    return not report["items"] and not any(
        counts[k] for k in ("tickets_created", "tickets_moved", "proposals", "duplicates_merged")
    )


def summary(report: dict[str, Any]) -> str:
    counts = report["counts"]
    parts = []
    if counts["started"]:
        parts.append(f"started {_count(counts['started'], 'task')}")
    if counts["finished"]:
        parts.append(f"finished {counts['finished']}")
    if counts["running"]:
        parts.append(f"{counts['running']} still running")
    if counts["paused"]:
        parts.append(f"{counts['paused']} paused")
    if counts["blocked"]:
        parts.append(f"{counts['blocked']} blocked")
    if counts["stopped"]:
        parts.append(f"{counts['stopped']} stopped")
    if counts["tickets_created"]:
        parts.append(f"added {_count(counts['tickets_created'], 'ticket')}")
    if counts["proposals"]:
        parts.append(f"proposed {counts['proposals']} for your approval")
    if counts["tickets_moved"]:
        parts.append(f"moved {_count(counts['tickets_moved'], 'ticket')}")
    if counts["duplicates_merged"]:
        parts.append(f"merged {_count(counts['duplicates_merged'], 'duplicate')}")
    if not parts:
        return "Self-work didn't start or change anything."
    text = ", ".join(parts)
    return text[0].upper() + text[1:] + "."


ENDED_BY = {
    "window": "its window ended",
    "user": "you turned it off",
    "condition": "the tasks it waited for finished",
    "request": "you asked for this report; self-work is still on",
}


def report_text(report: dict[str, Any], clock: datetime, ended_by: str) -> str:
    """The chat message: a lead line, then one line per task, with ticket keys as pills."""
    name = report["timezone"]
    start = instant(report["started_at"]) or clock
    end = instant(report["ended_at"]) or clock
    lines = [
        f"**Self-work report**: {when_label(start, name, clock)} to "
        f"{when_label(end, name, clock)}, {ENDED_BY.get(ended_by, 'it stopped')}.",
        summary(report),
    ]
    words = {
        "finished": "Finished",
        "running": "Still running",
        "paused": "Paused",
        "blocked": "Blocked",
        "failed": "Didn't finish",
        "stopped": "Stopped",
    }
    for item in report["items"]:
        keys = " ".join(ref.get("key", "") for ref in item["tickets"] if ref.get("key"))
        detail = f": {item['summary']}" if item["summary"] and item["outcome"] != "running" else ""
        lines.append(
            f"- {words.get(item['outcome'], item['outcome'].title())}: “{item['title']}”"
            + (f" ({keys})" if keys else "")
            + detail
        )
    if report["tickets_created"]:
        lines.append("- Added: " + ", ".join(report["tickets_created"]))
    if report["proposals"]:
        lines.append("- Proposed for your approval: " + ", ".join(report["proposals"]))
    if report["counts"]["running"] and ended_by != "request":
        lines.append("Tasks still running finish in their threads; self-work starts nothing new.")
    return "\n".join(lines)[:6000]


def close_period(
    db: sqlite3.Connection, clock: datetime, ended_by: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """End the period running now; returns (report, message) to post, or None.

    The period is cleared in the same transaction, so it is reported once. An empty
    period returns its report with an empty message, so the caller can say so in a line.
    """
    schedule = read(db)
    period = schedule.pop("period", None)
    if not period:
        return None
    report = build_report(db, period, clock, default_zone())
    message: dict[str, Any] = {}
    if not empty(report):
        message = {
            "id": f"self-work-period-{period['id']}",
            "body": report_text(report, clock, ended_by),
            "kind": "self_work_report",
            "self_work_report": {**report, "ended_by": ended_by},
        }
    schedule["last_period"] = {
        "id": period["id"],
        "started_at": report["started_at"],
        "ended_at": report["ended_at"],
        "ended_by": ended_by,
        "summary": summary(report),
        "message_id": message.get("id", ""),
        "from_seq": period.get("from_seq", 0),
    }
    write(db, schedule)
    return report, message
