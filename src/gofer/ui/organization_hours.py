"""Organization operating hours: when workers may start and keep working.

Hours are weekly windows in one IANA time zone. A window whose end is at or before its
start runs overnight into the next day. Everything is evaluated at an instant, so daylight
saving changes and restarts need no stored state.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class HoursWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Monday is 0, as in Python's weekday().
    days: list[int] = Field(min_length=1, max_length=7)
    start: str
    end: str

    @field_validator("days")
    @classmethod
    def valid_days(cls, days: list[int]) -> list[int]:
        if any(type(day) is not int or not 0 <= day <= 6 for day in days):
            raise ValueError("Days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(days))

    @model_validator(mode="after")
    def valid_times(self) -> HoursWindow:
        if not _CLOCK.match(self.start) or not _CLOCK.match(self.end):
            raise ValueError("Use 24-hour times such as 09:00 and 17:30")
        if self.start == self.end:
            raise ValueError("A window needs different start and end times")
        return self


class OperatingHours(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["always", "scheduled"] = "always"
    timezone: str = "UTC"
    windows: list[HoursWindow] = Field(default_factory=list, max_length=28)
    # At closing time, stop running work (it resumes when hours open) or let it finish.
    atClose: Literal["stop", "finish"] = "stop"

    @model_validator(mode="after")
    def valid_schedule(self) -> OperatingHours:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown time zone: {self.timezone}") from exc
        if self.mode == "scheduled" and not self.windows:
            raise ValueError("Scheduled hours need at least one window")
        return self


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def is_open(hours: dict[str, Any] | None, at: datetime) -> bool:
    """Whether workers may work at this instant."""
    if not hours or hours.get("mode", "always") == "always":
        return True
    local = at.astimezone(ZoneInfo(hours["timezone"]))
    day, minute = local.weekday(), local.hour * 60 + local.minute
    for window in hours.get("windows", []):
        start, end = _minutes(window["start"]), _minutes(window["end"])
        days = window["days"]
        if start < end:
            if day in days and start <= minute < end:
                return True
        else:
            # Overnight: from start on a listed day until end the next morning.
            if (day in days and minute >= start) or ((day - 1) % 7 in days and minute < end):
                return True
    return False


def _candidates(hours: dict[str, Any], at: datetime) -> list[datetime]:
    zone = ZoneInfo(hours["timezone"])
    first = at.astimezone(zone).date()
    found = []
    for offset in range(0, 9):
        day: date = first + timedelta(days=offset)
        for window in hours.get("windows", []):
            for clock in (window["start"], window["end"]):
                hour, minute = (int(x) for x in clock.split(":"))
                local = datetime.combine(day, time(hour, minute), tzinfo=zone)
                # Round-trip through UTC so a time skipped by daylight saving lands after it.
                found.append(local.astimezone(UTC))
    return sorted(set(found))


def next_change(hours: dict[str, Any] | None, at: datetime) -> datetime | None:
    """The next instant the open/closed state flips, or None when it never does."""
    if not hours or hours.get("mode", "always") == "always":
        return None
    now_open = is_open(hours, at)
    for candidate in _candidates(hours, at):
        if candidate > at and is_open(hours, candidate) != now_open:
            return candidate
    return None


def hours_state(config: dict[str, Any], at: datetime | None = None) -> dict[str, Any]:
    at = at or datetime.now(UTC)
    hours = config.get("operatingHours") or {"mode": "always"}
    open_now = is_open(hours, at)
    change = next_change(hours, at)
    return {
        "mode": hours.get("mode", "always"),
        "timezone": hours.get("timezone", "UTC"),
        "open": open_now,
        "opensAt": None if open_now or change is None else change.isoformat(),
        "closesAt": change.isoformat() if open_now and change else None,
        "atClose": hours.get("atClose", "stop"),
    }


def widens(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
    """Whether new hours let workers work at any time the old hours didn't."""
    if not after or after.get("mode", "always") == "always":
        return before is not None and before.get("mode", "always") != "always"
    if not before or before.get("mode", "always") == "always":
        return False
    if after.get("atClose") == "finish" and before.get("atClose", "stop") == "stop":
        return True
    # Windows repeat weekly, so a week covers every case for one set of clock offsets. A
    # January and a July week cover both sides of daylight saving in either hemisphere,
    # which matters when the time zone changes.
    for monday in (datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 7, 6, tzinfo=UTC)):
        for step in range(-24 * 60, 8 * 24 * 60):
            moment = monday + timedelta(minutes=step)
            if is_open(after, moment) and not is_open(before, moment):
                return True
    return False


def describe(hours: dict[str, Any] | None) -> str:
    if not hours or hours.get("mode", "always") == "always":
        return "24/7"
    parts = []
    for window in hours.get("windows", []):
        days = window["days"]
        if days == [0, 1, 2, 3, 4]:
            label = "Weekdays"
        elif days == list(range(7)):
            label = "Every day"
        elif days == [5, 6]:
            label = "Weekends"
        else:
            label = ", ".join(DAY_NAMES[d] for d in days)
        parts.append(f"{label} {window['start']}–{window['end']}")
    return "; ".join(parts) + f" ({hours['timezone']})"
