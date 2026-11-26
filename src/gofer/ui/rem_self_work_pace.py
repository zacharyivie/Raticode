"""Self-work's Pace to reset: how many new tasks may start so an allowance lasts to its reset.

The pace line runs straight from 0% at the start of an allowance window to the aim (97% by
default) a few hours before the provider's reset (3 hours by default), then stays flat. Each
check compares the account's percent used with the line:

- ``used`` is the highest reading of the window over the last 30 minutes, so a percent that
  flaps by one point (88, 89, 88) doesn't flip starts on and off.
- Each self-work task already running on the account keeps ``ROOM_PER_TASK`` points of room,
  for the cost it hasn't reported yet.
- ``starts = floor((line - used - room * running) / room)``, never below 0.

Only the provider's own percent and reset time are used. Nothing here estimates an allowance
from token counts: a provider's limit isn't a token count Raticode can know.

Edge cases:

- A reset time that moves (a paid reset, a provider change) starts a new line: readings
  with another reset time belong to the old window and are left out.
- A window that hasn't started (Codex at 0% with a reset that slides while nobody uses it,
  or no reset time at all) allows one start with nothing running, which starts the week.
- A reading older than 30 minutes, or none, starts nothing (fail safe, as the daily cap).
- A 5-hour window at 90% or more holds new starts when a reading of it exists.
- Several weekly windows (Claude's overall and per-model ones) pace against whichever is
  furthest ahead of its line. An account with no weekly window but a credits period (Grok's
  monthly credits) paces against that period.
- Each account has its own line from its own reset time; metered API keys have no allowance
  and pacing holds nothing back on them.
- Pacing only limits new starts: running work, paused work the user resumed and the user's
  own runs are never touched.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from gofer.utils.instants import stored_instant

MODE_SETTING = "allowance_mode"
OFF, DAILY, PACE = "off", "daily", "pace"
MODES = (OFF, DAILY, PACE)
AIM_SETTING = "pace_aim_percent"
LEAD_SETTING = "pace_lead_hours"
# Any share of the allowance can be the aim, such as 50% to leave half the week for the user.
AIM_DEFAULT, AIM_MIN, AIM_MAX = 97, 1, 100
LEAD_DEFAULT, LEAD_MAX = 3, 24
# The daily cap a client gets when it picks Daily cap without a percent: a seventh of a week.
DAILY_DEFAULT = 14
# Points of room each running self-work task keeps for the cost it hasn't reported yet.
ROOM_PER_TASK = 1.5
# The highest reading over this long counts, so a percent flapping by one doesn't flicker.
PEAK_OVER = timedelta(minutes=30)
# New work starts only with a reading this recent.
READING_FRESH = timedelta(minutes=30)
# A 5-hour window this full holds new starts.
FIVE_HOUR_HOLD = 90.0
WEEK = timedelta(days=7)
# Metered billing: nothing to pace against (provider_usage._UNAVAILABLE).
METERED = frozenset({"openai_api", "anthropic_api"})
WEEKLY, CREDITS, FIVE_HOUR = "weekly", "credits", "5-hour"
AFTER = " Running work finishes normally."

# Plan states.
ROOM = "room"  # behind the line: up to ``starts`` tasks may start
AHEAD = "ahead"  # at or ahead of the line: nothing new starts
UNKNOWN = "unknown"  # no reading from the last 30 minutes: nothing new starts
NOT_STARTED = "not_started"  # the window hasn't started: one start begins it
CEILING = "ceiling"  # the 5-hour window is nearly full
NO_ALLOWANCE = "no_allowance"  # metered billing: pacing doesn't apply


@dataclass(frozen=True)
class Window:
    """One allowance window's newest reading."""

    id: str
    label: str
    kind: str  # WEEKLY, CREDITS or FIVE_HOUR
    percent: float
    observed_at: datetime
    resets_at: datetime | None = None
    starts_at: datetime | None = None
    period: timedelta = WEEK


@dataclass(frozen=True)
class Plan:
    """What pacing allows now on one account."""

    state: str
    # How many new tasks may start; None when pacing holds nothing back.
    starts: int | None
    window: Window | None = None
    used: float | None = None
    line: float | None = None
    next_start_at: datetime | None = None
    # More than one weekly window was read, so sentences name the one that binds.
    several: bool = False

    @property
    def held(self) -> bool:
        return self.starts == 0


def _in_utc(value: Any) -> datetime | None:
    """A saved time in UTC, so the times this module hands on read the same everywhere."""
    stamp = stored_instant(value)
    return stamp.astimezone(UTC) if stamp else None


# Settings --------------------------------------------------------------------------------


def mode(settings: Mapping[str, Any]) -> str:
    """Off, Daily cap or Pace to reset. Settings saved before the choice existed are Daily
    cap when they have a daily percent, else Off."""
    chosen = settings.get(MODE_SETTING)
    if chosen in MODES:
        return str(chosen)
    return DAILY if int(settings.get("daily_allowance_percent") or 0) else OFF


def daily_cap(settings: Mapping[str, Any]) -> int:
    """The daily cap in force: its percent with Daily cap chosen, else 0."""
    return int(settings.get("daily_allowance_percent") or 0) if mode(settings) == DAILY else 0


def aim(settings: Mapping[str, Any]) -> int:
    value = settings.get(AIM_SETTING)
    return int(value) if type(value) is int and AIM_MIN <= value <= AIM_MAX else AIM_DEFAULT


def lead(settings: Mapping[str, Any]) -> int:
    value = settings.get(LEAD_SETTING)
    return int(value) if type(value) is int and 0 <= value <= LEAD_MAX else LEAD_DEFAULT


def resolve(stored: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """The allowance settings after a change, keeping Daily cap and Pace exclusive.

    Raises ValueError with a sentence for the user. A client that only knows the daily
    percent (an older phone) still works: a percent above 0 chooses Daily cap, 0 chooses Off.
    """
    current = mode(stored)
    changes: dict[str, Any] = {}
    for key, low, high, what in (
        (AIM_SETTING, AIM_MIN, AIM_MAX, "an aim from 1 to 100%"),
        (LEAD_SETTING, 0, LEAD_MAX, "a finish from 0 to 24 hours before the reset"),
    ):
        if key in payload:
            value = payload[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Choose {what}.")
            changes[key] = value
    has_percent = "daily_allowance_percent" in payload
    percent = payload.get("daily_allowance_percent")
    if has_percent and (type(percent) is not int or not 0 <= percent <= 100):
        raise ValueError("Choose a percent from 1 to 100, or 0 for no limit.")
    if MODE_SETTING in payload:
        chosen = payload[MODE_SETTING]
        if chosen not in MODES:
            raise ValueError("Choose Off, Daily cap or Pace to reset.")
        if chosen == DAILY:
            if has_percent and not percent:
                raise ValueError("Choose a daily cap from 1 to 100%.")
            kept = int(stored.get("daily_allowance_percent") or 0)
            percent = percent if has_percent else (kept or DAILY_DEFAULT)
        elif has_percent and percent:
            raise ValueError(
                "Daily cap and Pace to reset can't both be on. Choose one of them."
                if chosen == PACE
                else "Choose Daily cap to set a daily percent."
            )
        else:
            percent = 0
        changes.update({MODE_SETTING: chosen, "daily_allowance_percent": percent})
    elif has_percent:
        if percent and current == PACE:
            raise ValueError(
                "Daily cap and Pace to reset can't both be on. Choose Daily cap first."
            )
        chosen = DAILY if percent else (OFF if current == DAILY else current)
        changes.update({MODE_SETTING: chosen, "daily_allowance_percent": percent})
    return changes


# The planner -------------------------------------------------------------------------------


def window_start(window: Window) -> datetime | None:
    """When the window began: its reported start, else its reset minus its length."""
    if window.resets_at is None:
        return None
    if window.starts_at is not None and window.starts_at < window.resets_at:
        return window.starts_at
    return window.resets_at - window.period


def line_at(clock: datetime, start: datetime, finish: datetime, target: float) -> float:
    """The pace line at ``clock``: 0 at ``start``, ``target`` from ``finish`` on."""
    if finish <= start:
        return float(target)
    share = (clock - start) / (finish - start)
    return float(target) * min(1.0, max(0.0, share))


def same_reset(first: datetime | None, second: datetime | None) -> bool:
    from gofer.core.usage_allowance_history import RESET_TOLERANCE_SECONDS

    if first is None or second is None:
        return first is None and second is None
    return abs((first - second).total_seconds()) <= RESET_TOLERANCE_SECONDS


def peak(window: Window, history: Sequence[Mapping[str, Any]], clock: datetime) -> float:
    """The highest reading of this window over the last 30 minutes.

    ``history`` holds saved readings (``usage_allowance_history`` rows). Readings with another
    reset time belong to another window, such as last week's before a reset, and are left out.
    """
    highest = window.percent
    for row in history:
        seen = _in_utc(row.get("observed_at"))
        if seen is None or not clock - PEAK_OVER <= seen <= clock:
            continue
        if not same_reset(_in_utc(row.get("resets_at")), window.resets_at):
            continue
        highest = max(highest, float(row.get("used_percent") or 0.0))
    return highest


def fresh(window: Window, clock: datetime) -> bool:
    return clock - window.observed_at <= READING_FRESH


def plan_window(
    window: Window,
    history: Sequence[Mapping[str, Any]],
    running: int,
    clock: datetime,
    *,
    target: int = AIM_DEFAULT,
    lead_hours: int = LEAD_DEFAULT,
    room: float = ROOM_PER_TASK,
    floor: bool = False,
) -> Plan:
    """What pacing allows on one window, with ``running`` self-work tasks on its account.

    With ``floor`` an old reading still counts: use in a window only rises until its reset,
    so the last percent seen is the least it can be now.
    """
    if not floor and not fresh(window, clock):
        return Plan(UNKNOWN, 0, window)
    start = window_start(window)
    used = peak(window, history, clock)
    if start is None or window.resets_at is None:
        # No reset time: no line to draw. One task at a time starts the window, until
        # it is used up to the aim.
        if used >= target:
            return Plan(AHEAD, 0, window, used, float(target))
        return Plan(NOT_STARTED, 0 if running else 1, window, used)
    finish = max(start, window.resets_at - timedelta(hours=lead_hours))
    line = line_at(clock, start, finish, target)
    headroom = line - used - room * running
    starts = max(0, math.floor(headroom / room + 1e-9))
    if used >= 100:
        starts = 0
    if starts == 0 and used <= 0 and not running:
        # A window nobody has used yet (Codex's reset slides until the first request).
        return Plan(NOT_STARTED, 1, window, used, line)
    next_start = None
    if starts == 0:
        needed = used + room * (running + 1)
        if needed <= target and finish > start:
            next_start = max(clock, start + (finish - start) * (needed / target))
    return Plan(ROOM if starts else AHEAD, starts, window, used, line, next_start)


def plan(
    windows: Sequence[Window],
    history: Mapping[str, Sequence[Mapping[str, Any]]],
    running: int,
    clock: datetime,
    *,
    target: int = AIM_DEFAULT,
    lead_hours: int = LEAD_DEFAULT,
    provider: str = "",
    room: float = ROOM_PER_TASK,
) -> Plan:
    """What pacing allows on one account, from its windows' newest readings.

    ``history`` holds each window's saved readings by window id, ``running`` the self-work
    tasks running on the account now.
    """
    if provider in METERED:
        return Plan(NO_ALLOWANCE, None)
    current = [w for w in windows if w.resets_at is None or w.resets_at > clock]
    paced = [w for w in current if w.kind == WEEKLY] or [w for w in current if w.kind == CREDITS]
    usable = [w for w in paced if fresh(w, clock)]
    if not usable:
        newest = max(paced, key=lambda w: w.observed_at, default=None)
        return Plan(UNKNOWN, 0, newest)
    plans = [
        plan_window(
            w,
            history.get(w.id, ()),
            running,
            clock,
            target=target,
            lead_hours=lead_hours,
            room=room,
        )
        for w in usable
    ]
    # A window whose reading went stale while another stays fresh (Claude often reports
    # only one window per turn) still holds at its last percent: it can't have gone down.
    plans += [
        plan_window(
            w,
            history.get(w.id, ()),
            running,
            clock,
            target=target,
            lead_hours=lead_hours,
            room=room,
            floor=True,
        )
        for w in paced
        if w not in usable
    ]

    def ahead_by(found: Plan) -> float:
        return (found.used or 0.0) - (found.line if found.line is not None else 0.0)

    binding = min(
        plans, key=lambda p: (p.starts if p.starts is not None else 1 << 30, -ahead_by(p))
    )
    if len(usable) > 1:
        binding = replace(binding, several=True)
    five = [w for w in current if w.kind == FIVE_HOUR and fresh(w, clock)]
    full = max(five, key=lambda w: w.percent, default=None)
    if full is not None and full.percent >= FIVE_HOUR_HOLD and binding.starts:
        return Plan(CEILING, 0, full, full.percent, None, full.resets_at, binding.several)
    return binding


# Sentences ---------------------------------------------------------------------------------


def percent_text(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".") + "%"


def _what(found: Plan) -> str:
    window = found.window
    if window is None or window.kind == WEEKLY:
        named = (
            f" ({window.label})" if window is not None and found.several and window.label else ""
        )
        return "weekly allowance" + named
    if window.kind == FIVE_HOUR:
        return "5-hour limit"
    label = window.label.strip().lower()
    return label if label.endswith("credits") else "credits"


def allowance_name(found: Plan, who: str) -> str:
    """ "work's weekly allowance", "Codex's weekly allowance", "home's monthly credits"."""
    return f"{who}'s {_what(found)}"


def _clock_text(stamp: datetime, zone: tzinfo, clock: datetime) -> str:
    local = stamp.astimezone(zone)
    if local.date() == clock.astimezone(zone).date():
        return local.strftime("%H:%M")
    return local.strftime("%a %H:%M")


def limit(found: Plan, who: str, zone: tzinfo, clock: datetime) -> tuple[str, str] | None:
    """Why pacing starts nothing new now, as a key and a sentence; None while it allows one."""
    if not found.held:
        return None
    named = allowance_name(found, who)
    if found.state == UNKNOWN:
        seen = found.window.observed_at if found.window else None
        when = (
            f"the last one is from {_clock_text(seen, zone, clock)}"
            if seen
            else "there hasn't been one yet"
        )
        return "pace_unknown", (
            f"Starting nothing new: Pace to reset needs a reading of {named} from the last "
            f"30 minutes, and {when}. Check Settings > Usage; self-work goes on once a reading "
            f"comes in.{AFTER}"
        )
    if found.state == CEILING:
        back = ""
        if found.next_start_at:
            back = " It starts again after the 5-hour reset at "
            back += f"{_clock_text(found.next_start_at, zone, clock)}."
        return "pace_ceiling", (
            f"Holding new work: {who}'s 5-hour limit is {percent_text(found.used or 0)} used, "
            f"at or above {percent_text(FIVE_HOUR_HOLD)}.{back}{AFTER}"
        )
    if found.state == NOT_STARTED:
        return "pace_starting", (
            f"Holding new work: {named} hasn't started its window yet, so one task at a time "
            f"starts it.{AFTER}"
        )
    used = found.used or 0.0
    line = found.line or 0.0
    window = found.window
    if found.next_start_at is not None:
        then = f" Next start around {_clock_text(found.next_start_at, zone, clock)}."
    elif window is not None and window.resets_at is not None:
        then = f" It starts again after the reset at {_clock_text(window.resets_at, zone, clock)}."
    else:
        then = ""
    ahead = used - line
    where = (
        f"{percent_text(ahead)} ahead of its pace line ({percent_text(line)})"
        if ahead > 0.05
        else f"at its pace line ({percent_text(line)}) with room kept for running tasks"
    )
    return "pace", f"Holding new work: {named} is {percent_text(used)} used, {where}.{then}{AFTER}"


def status(found: Plan, who: str, zone: tzinfo, clock: datetime) -> str:
    """One line for Settings and the phone about what pacing allows now."""
    if found.state == NO_ALLOWANCE:
        return f"{who} is billed by use, with no allowance to pace against."
    if found.held:
        stopped = limit(found, who, zone, clock)
        return stopped[1].removesuffix(AFTER) if stopped else ""
    named = allowance_name(found, who)
    window = found.window
    resets = (
        f" Resets {_clock_text(window.resets_at, zone, clock)}."
        if window is not None and window.resets_at is not None
        else ""
    )
    starts = found.starts or 0
    can = f"Up to {starts} {'task' if starts == 1 else 'tasks'} can start."
    if found.state == NOT_STARTED:
        return f"Pacing {named}: its window hasn't started, so one task starts it.{resets}"
    line = f", line {percent_text(found.line)}" if found.line is not None else ""
    return f"Pacing {named}: {percent_text(found.used or 0)} used{line}. {can}{resets}"


def view(
    found: Plan | None,
    settings: Mapping[str, Any],
    who: str,
    zone: tzinfo,
    clock: datetime,
    *,
    account: str = "",
    provider: str = "",
) -> dict[str, Any]:
    """What Settings, the phone and the CLI show about pacing."""
    window = found.window if found else None
    chosen = mode(settings)
    return {
        "mode": chosen,
        "aim_percent": aim(settings),
        "lead_hours": lead(settings),
        "account": account,
        "provider": provider,
        "state": found.state if found and chosen == PACE else "",
        "starts": found.starts if found and chosen == PACE else None,
        "used_percent": round(found.used, 1) if found and found.used is not None else None,
        "line_percent": round(found.line, 1) if found and found.line is not None else None,
        "window": window.label if window else "",
        "resets_at": window.resets_at.isoformat() if window and window.resets_at else "",
        "reading_at": window.observed_at.isoformat() if window else "",
        "next_start_at": found.next_start_at.isoformat() if found and found.next_start_at else "",
        "message": status(found, who, zone, clock) if found and chosen == PACE else "",
    }


# Reading the store -----------------------------------------------------------------------


def _kind(window: Mapping[str, Any]) -> str | None:
    from gofer.core.usage_alerts import FIVE_HOUR as ALERT_FIVE_HOUR
    from gofer.core.usage_alerts import WEEKLY as ALERT_WEEKLY
    from gofer.core.usage_alerts import window_kind

    if str(window.get("id") or "") == CREDITS:
        return CREDITS
    found = window_kind(dict(window))
    if found == ALERT_WEEKLY:
        return WEEKLY
    if found == ALERT_FIVE_HOUR:
        return FIVE_HOUR
    return None


def read_windows(
    provider: str, data_dir: Path | None, clock: datetime, *, profile: str = ""
) -> list[Window]:
    """The newest stored reading of each allowance window of one account.

    ``profile`` is an account's usage identity (``profile_usage_identity``); "" reads the
    provider's default sign-in. Readings from after ``clock`` and windows whose reset has
    passed are left out.
    """
    from gofer.core.usage_ledger import usage_db
    from gofer.core.usage_service import window_used_percent
    from gofer.ui.rem_self_work_daily import period_days

    raw: list[tuple[str, Any]] = []
    with usage_db(data_dir) as db:
        row = db.execute(
            "SELECT payload FROM usage_snapshots WHERE id=?", (profile or provider,)
        ).fetchone()
        if row is not None:
            payload = json.loads(row["payload"])
            raw.extend(
                (str(payload.get("observed_at") or ""), w) for w in payload.get("windows") or []
            )
        for limit_row in db.execute(
            "SELECT observed_at, windows FROM usage_rate_limits WHERE provider=? AND profile=?",
            (provider, profile),
        ):
            raw.extend((str(limit_row["observed_at"]), w) for w in json.loads(limit_row["windows"]))
    newest: dict[str, Window] = {}
    for observed, window in raw:
        if not isinstance(window, dict) or not window.get("id"):
            continue
        kind = _kind(window)
        seen = _in_utc(observed)
        percent = window_used_percent(window)
        if kind is None or seen is None or percent is None or seen > clock + timedelta(minutes=1):
            continue
        resets = _in_utc(window.get("resets_at"))
        if resets is not None and resets <= clock:
            continue
        period = WEEK
        if kind == CREDITS:
            period = timedelta(days=period_days(window))
        elif kind == FIVE_HOUR:
            period = timedelta(hours=5)
        found = Window(
            id=str(window["id"]),
            label=str(window.get("label") or ""),
            kind=kind,
            percent=float(percent),
            observed_at=seen,
            resets_at=resets,
            starts_at=_in_utc(window.get("starts_at")),
            period=period,
        )
        kept = newest.get(found.id)
        if (
            kept is None
            or found.observed_at > kept.observed_at
            or (found.observed_at == kept.observed_at and found.percent > kept.percent)
        ):
            newest[found.id] = found
    return list(newest.values())


def read_history(
    provider: str, data_dir: Path | None, clock: datetime, *, profile: str = ""
) -> dict[str, list[dict[str, Any]]]:
    """Saved readings of the account's windows from the last 30 minutes, by window id."""
    from gofer.core.usage_allowance_history import allowance_history

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in allowance_history(
        data_dir, provider=provider, profile=profile, since=clock - PEAK_OVER
    ):
        grouped.setdefault(str(row["window_id"]), []).append(row)
    return grouped


def plan_account(
    settings: Mapping[str, Any],
    provider: str,
    data_dir: Path | None,
    running: int,
    clock: datetime,
    *,
    profile: str = "",
) -> Plan:
    """What pacing allows on one account now, read from Raticode's usage store."""
    if provider in METERED:
        return Plan(NO_ALLOWANCE, None)
    return plan(
        read_windows(provider, data_dir, clock, profile=profile),
        read_history(provider, data_dir, clock, profile=profile),
        running,
        clock,
        target=aim(settings),
        lead_hours=lead(settings),
        provider=provider,
    )
