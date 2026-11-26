"""Allowance readings over time, and the weekly burndown and pace on Settings > Usage cards.

Every allowance reading a provider gives Raticode (a polled refresh, or the windows a
turn reports) is saved in ``usage_allowance_history`` when it differs from the window's
last saved reading. The latest-reading tables (``usage_snapshots``, ``usage_rate_limits``)
stay as they were; this table only adds the history.

The pace comes only from those readings. Nothing here estimates an allowance from token
counts: a provider's limit isn't a token count Raticode can know.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gofer.core.usage_ledger import usage_db
from gofer.utils.instants import stored_instant

# Providers can report the same reset a few seconds apart; a shift this small is the
# same window, not a new reading.
RESET_TOLERANCE_SECONDS = 120
WEEK = timedelta(days=7)
# How many readings one window sends with an account row. The chart keeps the first and
# last and thins the rest evenly.
MAX_POINTS = 240

PACE_TEXT = {
    "not_enough": "Not enough readings yet",
    "on_pace": "On pace to last until reset",
    "used_up": "Used up until reset",
}


def _same_reset(left: Any, right: Any) -> bool:
    first, second = stored_instant(left), stored_instant(right)
    if first is None or second is None:
        return first is None and second is None
    return abs((first - second).total_seconds()) <= RESET_TOLERANCE_SECONDS


def _changed(last: Mapping[str, Any] | None, percent: float, resets_at: str) -> bool:
    """Whether a reading differs from the window's last saved one."""
    if last is None:
        return True
    return abs(float(last["used_percent"]) - percent) > 1e-6 or not _same_reset(
        last["resets_at"], resets_at
    )


def record_allowance_readings(
    db: sqlite3.Connection,
    provider: str,
    profile: str,
    windows: Iterable[Any],
    observed_at: str | None,
) -> int:
    """Save the windows whose reading changed since the last one; returns rows written.

    ``profile`` is ``""`` for the provider's own sign-in, else the profile's usage
    identity (``profile_usage_identity``), as in ``usage_rate_limits``. A window
    without an id or a percent, a reading older than the last saved one and a reading
    taken after its own reset time are skipped.
    """
    from gofer.core.usage_service import window_used_percent

    observed = stored_instant(observed_at)
    if observed is None:
        return 0
    written = 0
    seen: set[str] = set()
    for window in windows:
        if not isinstance(window, dict) or not isinstance(window.get("id"), str):
            continue
        window_id = window["id"][:160]
        if not window_id or window_id in seen:
            continue
        percent = window_used_percent(window)
        if percent is None:
            continue
        resets_at = window.get("resets_at") if isinstance(window.get("resets_at"), str) else ""
        reset = stored_instant(resets_at)
        if reset is not None and reset <= observed:
            continue
        seen.add(window_id)
        last = db.execute(
            """SELECT used_percent, resets_at, observed_at FROM usage_allowance_history
            WHERE provider=? AND profile=? AND window_id=?
            ORDER BY observed_at DESC LIMIT 1""",
            (provider, profile, window_id),
        ).fetchone()
        if last is not None:
            previous = stored_instant(last["observed_at"])
            if previous is not None and observed <= previous:
                continue
        if not _changed(last, percent, resets_at or ""):
            continue
        db.execute(
            "INSERT INTO usage_allowance_history VALUES (?, ?, ?, ?, ?, ?)",
            (
                provider,
                profile,
                window_id,
                float(percent),
                reset.astimezone(UTC).isoformat() if reset else "",
                observed.astimezone(UTC).isoformat(),
            ),
        )
        written += 1
    return written


def allowance_history(
    data_dir: Path | None = None,
    *,
    provider: str | None = None,
    profile: str | None = None,
    window_id: str | None = None,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Saved readings, oldest first, optionally for one provider, account and window."""
    clauses, values = [], []
    for column, value in (("provider", provider), ("profile", profile), ("window_id", window_id)):
        if value is not None:
            clauses.append(f"{column}=?")
            values.append(value)
    if since is not None:
        clauses.append("observed_at>=?")
        values.append(since.astimezone(UTC).isoformat())
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with usage_db(data_dir) as db:
        rows = db.execute(
            "SELECT * FROM usage_allowance_history" + where + " ORDER BY observed_at, rowid",
            values,
        ).fetchall()
    return [dict(row) for row in rows]


def split_windows(readings: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """Readings, oldest first, cut into one list per window between resets.

    A new window starts when the reset time moves on, or, when a reset time is missing,
    when the percent used falls.
    """
    windows: list[list[Mapping[str, Any]]] = []
    for reading in readings:
        if windows:
            last = windows[-1][-1]
            if stored_instant(last.get("resets_at")) and stored_instant(reading.get("resets_at")):
                same = _same_reset(last.get("resets_at"), reading.get("resets_at"))
            else:
                same = float(reading["used_percent"]) >= float(last["used_percent"])
            if same:
                windows[-1].append(reading)
                continue
        windows.append([reading])
    return windows


def _local(stamp: datetime, zone: Any = None) -> str:
    local = stamp.astimezone(zone) if zone is not None else stamp.astimezone()
    return f"{local:%a %H:%M}"


def pace(
    readings: Sequence[Mapping[str, Any]],
    resets_at: str,
    *,
    checked_at: str | None = None,
    zone: Any = None,
) -> dict[str, Any]:
    """Whether one window lasts until its reset at the rate its own readings show.

    ``readings`` are the current window's saved readings, oldest first. ``checked_at``
    is when the provider last confirmed the newest one; readings are only saved when they
    change, so the rate runs from the first reading to that check. Fewer than two
    readings is ``not_enough``.
    """
    reset = stored_instant(resets_at)
    points = [
        (stamp, float(r["used_percent"]))
        for r in readings
        if (stamp := stored_instant(r.get("observed_at"))) is not None
    ]
    if len(points) < 2 or reset is None:
        return {"state": "not_enough", "runs_out_at": None, "text": PACE_TEXT["not_enough"]}
    (first_at, first), (last_at, last) = points[0], points[-1]
    if last >= 100:
        return {"state": "used_up", "runs_out_at": None, "text": PACE_TEXT["used_up"]}
    checked = stored_instant(checked_at)
    end_at = max(last_at, checked) if checked is not None and checked < reset else last_at
    seconds = (end_at - first_at).total_seconds()
    rate = (last - first) / seconds if seconds > 0 else 0.0
    if rate <= 0:
        return {"state": "on_pace", "runs_out_at": None, "text": PACE_TEXT["on_pace"]}
    runs_out = end_at + timedelta(seconds=(100 - last) / rate)
    if runs_out >= reset:
        return {"state": "on_pace", "runs_out_at": None, "text": PACE_TEXT["on_pace"]}
    return {
        "state": "runs_out",
        "runs_out_at": runs_out.astimezone(UTC).isoformat(),
        "text": f"At this pace, runs out around {_local(runs_out, zone)}",
    }


def _thin(points: list[dict[str, Any]], limit: int = MAX_POINTS) -> list[dict[str, Any]]:
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(index * step)] for index in range(limit)]


def weekly_window(account: Mapping[str, Any]) -> dict[str, Any] | None:
    """The account's main weekly window: Claude's ``seven_day``, else the first weekly one."""
    from gofer.core.usage_alerts import WEEKLY, window_kind

    windows = [w for w in account.get("windows") or [] if isinstance(w, dict) and w.get("id")]
    for window in windows:
        if window.get("id") == "seven_day":
            return window
    for window in windows:
        if window_kind(window) == WEEKLY:
            return window
    return None


def weekly_burndown(
    account: Mapping[str, Any],
    readings: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    zone: Any = None,
) -> dict[str, Any] | None:
    """The account card's weekly chart: this window's readings, its start, reset and pace.

    ``readings`` are the window's saved readings, oldest first, any number of windows
    back. None when the account has no weekly window with a reset time still ahead.
    """
    window = weekly_window(account)
    if window is None:
        return None
    reset = stored_instant(window.get("resets_at"))
    now = now or datetime.now(UTC)
    if reset is None or reset <= now:
        return None
    start = reset - WEEK
    current: list[Mapping[str, Any]] = []
    for part in split_windows(list(readings)):
        if _same_reset(part[-1].get("resets_at"), window.get("resets_at")):
            current = [r for r in part if (stored_instant(r.get("observed_at")) or reset) >= start]
    points = [
        {"observed_at": str(r["observed_at"]), "used_percent": float(r["used_percent"])}
        for r in current
    ]
    return {
        "window_id": str(window["id"]),
        "label": str(window.get("label") or window["id"]),
        "starts_at": start.isoformat(),
        "resets_at": reset.isoformat(),
        "readings": _thin(points),
        "reading_count": len(points),
        "pace": pace(current, reset.isoformat(), checked_at=account.get("observed_at"), zone=zone),
    }


def account_history_key(account: Mapping[str, Any]) -> tuple[str, str]:
    """(provider, profile) as the history table stores an overview account row."""
    provider = str(account.get("provider") or "")
    return provider, str(account.get("id") or "") if account.get("profile") else ""


def with_weekly_burndown(
    accounts: list[dict[str, Any]],
    data_dir: Path | None = None,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Account rows with ``weekly_burndown`` added where the account has a weekly window."""
    now = now or datetime.now(UTC)
    wanted = {
        (*account_history_key(account), str(window["id"]))
        for account in accounts
        if (window := weekly_window(account)) is not None
    }
    if not wanted:
        return accounts
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    # Two weeks back covers the current window and the one before its reset.
    for row in allowance_history(data_dir, since=now - 2 * WEEK):
        key = (row["provider"], row["profile"], row["window_id"])
        if key in wanted:
            grouped.setdefault(key, []).append(row)
    result = []
    for account in accounts:
        window = weekly_window(account)
        if window is None:
            result.append(account)
            continue
        key = (*account_history_key(account), str(window["id"]))
        chart = weekly_burndown(account, grouped.get(key, []), now=now)
        result.append({**account, "weekly_burndown": chart} if chart else account)
    return result
