"""Self-work's daily cap: a share of the Claude Code weekly allowance it may use each day.

Claude reports one percent for the whole account's weekly allowance, not per caller, so
self-work's share can't be read directly. Instead Raticode watches that percent and counts
how much it rose while self-work had work running: between two readings, the rise counts
when self-work was running at either reading or at any check in between. Use made at the
same time by anything else on the account (Claude Code in a terminal, Rem's chat, tasks you
started) counts too, so the cap errs on the side of stopping early. Rises while self-work
runs nothing don't count.

The count is kept in Rem's store, so it carries across self-work sessions and restarts, and
starts again from zero at midnight in this computer's time zone.

Self-work on one of a provider's accounts (Settings > Rem > Accounts) is counted against that
account's own weekly allowance, one count per (provider, account usage identity) under
``accounts`` in the stored state. The top-level count stays the Claude Code default sign-in's,
as it was before accounts.

An account whose provider reports credits for a billing period instead of a weekly window
(Grok's monthly credits) is counted the same way against that credits window. Its cap is the
weekly cap's share of a week moved to the credits period: a 10% cap on a 30-day period stops
at about 2.3% of the month's credits a day.
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from gofer.utils.instants import stored_instant

PROVIDER = "claude_code"
WINDOW = "seven_day"
SETTING = "daily_allowance_percent"
STATE_KEY = "self_work_daily"
# New work starts only with a reading this recent, so the count is never far behind.
READING_FRESH = timedelta(minutes=30)
# Two readings further apart than this, with self-work stopped before the second, count only
# the part of the gap self-work was running (the app may have been closed in between).
LONG_GAP = timedelta(minutes=30)
# Two reset times this close name the same weekly window.
SAME_WINDOW = timedelta(hours=1)
# The window a provider reports its period's credits in (Grok's ``_x.ai/billing``).
CREDITS = "credits"
WEEK_DAYS = 7.0
# A credits period's length when neither its start nor its label says (Grok bills monthly).
DEFAULT_PERIOD_DAYS = 30.0
_PERIOD_WORDS = {"daily": 1.0, "weekly": WEEK_DAYS, "yearly": 365.0, "annual": 365.0}


def local_day(clock: datetime, zone: ZoneInfo) -> str:
    return clock.astimezone(zone).date().isoformat()


def next_midnight(clock: datetime, zone: ZoneInfo) -> datetime:
    local = clock.astimezone(zone)
    tomorrow = (local + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=zone)


def read_weekly(
    data_dir: Path | None, provider: str = PROVIDER, identity: str = ""
) -> dict[str, Any] | None:
    """The newest stored reading of the allowance the daily cap counts, or None.

    Without ``identity``, Claude Code's default sign-in's weekly allowance. With one (an
    account's ``profile_usage_identity``), that account's weekly window, found by its length
    so it works for providers whose window IDs don't say (Codex). An account with no weekly
    window but a credits window (Grok) gives that instead, marked with ``measure`` (such as
    "monthly credits") and ``scale``, the cap's factor for the credits period (credits_scale).
    """
    from gofer.core.usage_service import latest_window_reading

    if not identity:
        return latest_window_reading(PROVIDER, WINDOW, data_dir)
    found = latest_window_reading(provider, WINDOW, data_dir, profile=identity, kind="weekly")
    if found is None:
        found = latest_window_reading(provider, CREDITS, data_dir, profile=identity)
    if found is None or found.get("id") != CREDITS:
        return found
    return {**found, "measure": credits_measure(found), "scale": credits_scale(found)}


def credits_measure(reading: dict[str, Any]) -> str:
    """What a credits reading measures, from its label: "monthly credits", or "credits"."""
    label = str(reading.get("label") or "").strip().lower()
    return label if label.endswith("credits") else "credits"


def period_days(reading: dict[str, Any]) -> float:
    """How many days a credits period lasts.

    From its start and reset when the provider reported both; else from the label's period
    word (a month is the calendar month before the reset); else DEFAULT_PERIOD_DAYS.
    """
    start, end = (
        stored_instant(reading.get("starts_at"), naive_as_utc=False),
        stored_instant(reading.get("resets_at"), naive_as_utc=False),
    )
    if start and end and end > start:
        return max(1.0, (end - start) / timedelta(days=1))
    label = str(reading.get("label") or "").lower()
    for word, days in _PERIOD_WORDS.items():
        if word in label:
            return days
    if "monthly" in label and end:
        month = end.month - 1 or 12
        year = end.year if end.month > 1 else end.year - 1
        return float(calendar.monthrange(year, month)[1])
    return DEFAULT_PERIOD_DAYS


def credits_scale(reading: dict[str, Any]) -> float:
    """The cap's factor for a credits period: a week's share of the period's length."""
    return WEEK_DAYS / period_days(reading)


def measure(reading: dict[str, Any] | None) -> str:
    """What a reading measures: "weekly allowance", or the credits window's "monthly credits"."""
    return str((reading or {}).get("measure") or "weekly allowance")


def day_cap(cap: int, reading: dict[str, Any] | None) -> float:
    """The cap in the reading's own percent: the weekly cap, scaled for a credits period."""
    return cap * float((reading or {}).get("scale") or 1.0)


def scaled(reading: dict[str, Any] | None) -> bool:
    """Whether the cap is scaled for the reading: a credits period that isn't a week long."""
    return abs(float((reading or {}).get("scale") or 1.0) - 1.0) > 0.001


def cap_text(cap: int, reading: dict[str, Any] | None) -> str:
    """ "11%", or "2.3% (the 10% weekly cap over a 30-day period)" for a credits period."""
    if not scaled(reading):
        return f"{cap}%"
    days = round(WEEK_DAYS / float((reading or {})["scale"]))
    return f"{percent_text(day_cap(cap, reading))} (the {cap}% weekly cap over a {days}-day period)"


def account_key(provider: str, identity: str) -> str:
    """Where one account's count is kept under ``accounts`` in the stored state."""
    return f"{provider}|{identity}"


def account_state(state: dict[str, Any], key: str) -> dict[str, Any]:
    accounts = state.get("accounts")
    found = accounts.get(key) if isinstance(accounts, dict) else None
    return dict(found) if isinstance(found, dict) else {}


def whose(account: str) -> str:
    """Whose weekly allowance a sentence names: the default sign-in's or an account's."""
    return f"{account}'s" if account else "the Claude Code"


def allowance(account: str, reading: dict[str, Any] | None) -> str:
    """ "work's weekly allowance", "home's monthly credits", "the Claude Code weekly allowance"."""
    return f"{whose(account)} {measure(reading) if account else 'weekly allowance'}"


def _rise(last: dict[str, Any], reading: dict[str, Any]) -> float:
    """How much the weekly percent went up from ``last`` to ``reading``."""
    before = stored_instant(last.get("resets_at"), naive_as_utc=False)
    after = stored_instant(reading.get("resets_at"), naive_as_utc=False)
    percent = float(reading["percent"])
    if before and after and after - before > SAME_WINDOW:
        # The week reset in between: everything in the new week is new use.
        return max(0.0, percent)
    return max(0.0, percent - float(last.get("percent") or 0.0))


def advance(
    state: dict[str, Any],
    reading: dict[str, Any] | None,
    busy: bool,
    clock: datetime,
    zone: ZoneInfo,
) -> dict[str, Any]:
    """The daily count after one check; ``busy`` is whether self-work has work running now.

    Returns a new dict and never changes ``state``.
    """
    day = local_day(clock, zone)
    state = dict(state)
    if state.get("day") != day:
        state = {"day": day, "used": 0.0, "last": state.get("last")}
    last = dict(state["last"]) if isinstance(state.get("last"), dict) else None
    observed = stored_instant((reading or {}).get("observed_at"), naive_as_utc=False)
    previous = stored_instant((last or {}).get("observed_at"), naive_as_utc=False)
    if reading is not None and observed and (previous is None or observed > previous):
        if last is not None and previous is not None and (busy or last.get("busy")):
            rise = _rise(last, reading)
            gap = observed - previous
            if rise and not busy and gap > LONG_GAP:
                until = stored_instant(last.get("busy_until"), naive_as_utc=False) or previous
                share = max(0.0, min(1.0, (until - previous) / gap))
                rise *= share
            state["used"] = round(float(state.get("used") or 0.0) + rise, 3)
        last = {
            "percent": float(reading["percent"]),
            "resets_at": reading.get("resets_at") or "",
            "observed_at": observed.astimezone(UTC).isoformat(),
            "busy": busy,
            "busy_until": clock.isoformat() if busy else "",
        }
    elif last is not None and busy:
        last.update(busy=True, busy_until=clock.isoformat())
    state["last"] = last
    return state


def used_today(state: dict[str, Any], clock: datetime, zone: ZoneInfo) -> float:
    if state.get("day") != local_day(clock, zone):
        return 0.0
    return float(state.get("used") or 0.0)


def percent_text(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".") + "%"


def fresh(reading: dict[str, Any] | None, clock: datetime) -> bool:
    observed = stored_instant((reading or {}).get("observed_at"), naive_as_utc=False)
    return observed is not None and clock - observed <= READING_FRESH


def limit(
    cap: int,
    state: dict[str, Any],
    reading: dict[str, Any] | None,
    clock: datetime,
    zone: ZoneInfo,
    *,
    account: str = "",
) -> tuple[str, str] | None:
    """Why the daily cap stops new work now, as a key and a sentence; None while it doesn't.

    ``account`` names the account self-work would run on; "" is the default sign-in.
    """
    if not cap:
        return None
    after = " Running work finishes normally."
    if not fresh(reading, clock):
        seen = stored_instant((reading or {}).get("observed_at"), naive_as_utc=False)
        when = (
            f"the last one is from {seen.astimezone(zone).strftime('%H:%M')}"
            if seen
            else "there hasn't been one yet"
        )
        needs = (
            f"a reading of {allowance(account, reading)}"
            if account
            else "a Claude Code weekly allowance reading"
        )
        return "daily_allowance_unknown", (
            f"Starting nothing new: the daily cap needs {needs} "
            f"from the last 30 minutes, and {when}. Check Settings > Usage; self-work goes on "
            f"once a reading comes in.{after}"
        )
    used = used_today(state, clock, zone)
    if used >= day_cap(cap, reading):
        return "daily_allowance", (
            f"Stopped starting new work: self-work used about {percent_text(used)} of "
            f"{allowance(account, reading)} today, reaching its daily cap of "
            f"{cap_text(cap, reading)}. It starts again after midnight.{after}"
        )
    return None


def view(
    cap: int,
    state: dict[str, Any],
    reading: dict[str, Any] | None,
    clock: datetime,
    zone: ZoneInfo,
    *,
    account: str = "",
    provider: str = PROVIDER,
    others: list[tuple[str, float]] | None = None,
) -> dict[str, Any]:
    """What Settings and the phone show about today's count.

    ``account`` names the account self-work runs on next ("" is the default sign-in);
    ``others`` are other accounts self-work used today, as (name, percent) pairs.
    """
    used = used_today(state, clock, zone)
    available = fresh(reading, clock)
    named = allowance(account, reading)
    if not cap:
        message = f"Today self-work used about {percent_text(used)} of {named}." if used else ""
    elif not available:
        current = (
            f"No current reading of {named}, so self-work starts nothing new on it."
            if account
            else "No current Claude Code usage reading, so self-work starts nothing new."
        )
        message = f"{current} Used today: about {percent_text(used)} of {cap_text(cap, reading)}."
    elif scaled(reading):
        message = (
            f"Used today: about {percent_text(used)} of {named}, of a daily cap of "
            f"{cap_text(cap, reading)}. Resets at midnight."
        )
    else:
        message = (
            f"Used today: about {percent_text(used)} of {cap}% of {named}. Resets at midnight."
        )
    message = (message + others_text(others)).strip()
    return {
        "account": account,
        "provider": provider if account else PROVIDER,
        "cap_percent": cap,
        "used_percent": round(used, 1),
        "day": local_day(clock, zone),
        "timezone": zone.key,
        "resets_at": next_midnight(clock, zone).isoformat(),
        "reading_at": str((reading or {}).get("observed_at") or ""),
        "weekly_percent": round(float(reading["percent"]), 1) if reading else None,
        # What the reading measures, and the cap in its percent (scaled for a credits period).
        "measure": measure(reading) if account else "weekly allowance",
        "day_cap_percent": round(day_cap(cap, reading), 2),
        "available": available,
        "message": message,
    }


def others_text(others: list[tuple[str, float]] | None) -> str:
    """ " Also today: about 3% of personal's." for other accounts self-work used today."""
    used = [(name, value) for name, value in others or [] if value > 0]
    if not used:
        return ""
    parts = [f"about {percent_text(value)} of {whose(name)}" for name, value in used]
    return " Also today: " + ", ".join(parts) + "."
