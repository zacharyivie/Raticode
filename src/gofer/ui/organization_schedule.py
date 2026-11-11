"""Organization work schedule: named focus blocks that tell workers what to work on when.

Working hours (``operatingHours``) still decide *whether* workers run. The work schedule
(``workSchedule``) decides *what kind of work* they favor while they do: feature work in the
day, hardening in the evening, a release at midnight, gardening overnight, a weekly release,
a monthly dependency audit or a quarterly security review. Both use the organization's one
time zone, ``operatingHours.timezone``.

Each block repeats daily, weekly (on chosen weekdays), monthly or quarterly (on a day of the
month or the nth weekday), from a start time to an end time. An end at or before the start
runs past midnight into the next day; the same start and end means a whole day.

When blocks overlap, one wins, by this rule:

1. The longer cadence wins: quarterly beats monthly, monthly beats weekly, weekly beats daily.
2. With the same cadence, the shorter block wins, because it is the more specific one.
3. Two overlapping blocks with the same cadence and the same length can't be told apart, so
   saving them is refused.

Times are evaluated at an instant in the time zone, so daylight saving changes need no
stored state. A time a spring-forward change skips moves an hour later, as the clocks do
(02:30 becomes 03:30); a time that happens twice in a fall-back change uses its first
occurrence. Blocks keep their clock times, so on those days a block runs an hour shorter
or longer. Day 29, 30 or 31 of
a month falls on the month's last day in shorter months, so "day 31" means the last day.
Quarters are the calendar quarters starting January, April, July and October.

Block changes never stop a run. Work that started under one block finishes under it; the
next ticket a worker picks, and the next check-in, follow the new block.
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Cadence = Literal["daily", "weekly", "monthly", "quarterly"]
CADENCE_RANK = {"daily": 0, "weekly": 1, "monthly": 2, "quarterly": 3}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_PLURALS = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"]
LONG_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ORDINALS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", -1: "last"}
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_BLOCKS = 40
# A block runs at most a day, so the active block at an instant began today or yesterday.
DAY_MINUTES = 24 * 60
# How far ahead to look for the next block when nothing changes within a week.
LONG_HORIZON_DAYS = 400
HANDOFF = (
    "Block changes never stop a run: work that started under one block finishes under it. "
    "When the block ends, the next ticket you pick and the next check-in follow the new block. "
    "If your block ends soon, finish the step you're on and leave a handoff comment rather "
    "than starting something large."
)


class ScheduleBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")
    label: str = Field(min_length=1, max_length=80)
    focus: str = Field(default="", max_length=600)
    cadence: Cadence = "daily"
    start: str
    end: str
    # Weekly: the weekdays it runs on, Monday 0 to Sunday 6.
    days: list[int] = Field(default_factory=list, max_length=7)
    # Monthly and quarterly: a day of the month (29-31 fall on the last day of shorter
    # months), or the nth weekday (nthWeek 1-4, or -1 for the last one).
    monthDay: int | None = Field(default=None, ge=1, le=31)
    nthWeek: Literal[1, 2, 3, 4, -1] | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    # Quarterly: which month of each quarter, 1 to 3.
    quarterMonth: int = Field(default=1, ge=1, le=3)
    # Words that mark the tickets this block favors, matched in titles and descriptions.
    keywords: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("label", "focus")
    @classmethod
    def trimmed(cls, value: str) -> str:
        return value.strip()

    @field_validator("days")
    @classmethod
    def valid_days(cls, days: list[int]) -> list[int]:
        if any(type(day) is not int or not 0 <= day <= 6 for day in days):
            raise ValueError("Days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(days))

    @field_validator("keywords")
    @classmethod
    def valid_keywords(cls, words: list[str]) -> list[str]:
        cleaned: list[str] = []
        for word in words:
            word = str(word).strip()
            if not 1 <= len(word) <= 40:
                raise ValueError("Keep each keyword between 1 and 40 characters")
            if word.casefold() not in {w.casefold() for w in cleaned}:
                cleaned.append(word)
        return cleaned

    @model_validator(mode="after")
    def valid_rule(self) -> ScheduleBlock:
        if not self.label:
            raise ValueError("Give each block a name")
        if not _CLOCK.match(self.start) or not _CLOCK.match(self.end):
            raise ValueError(f"{self.label}: use 24-hour times such as 08:00 and 17:30")
        by_day = self.monthDay is not None
        by_weekday = self.nthWeek is not None or self.weekday is not None
        if self.cadence in {"daily", "weekly"}:
            if by_day or by_weekday:
                raise ValueError(f"{self.label}: only monthly and quarterly blocks pick a day")
            if self.cadence == "daily" and self.days:
                raise ValueError(f"{self.label}: a daily block runs every day; use weekly")
            if self.cadence == "weekly" and not self.days:
                raise ValueError(f"{self.label}: choose the weekdays it runs on")
        else:
            if self.days:
                raise ValueError(f"{self.label}: choose a day of the month, not weekdays")
            if by_day == by_weekday:
                raise ValueError(
                    f"{self.label}: choose either a day of the month or a weekday such as "
                    "the first Monday"
                )
            if by_weekday and (self.nthWeek is None or self.weekday is None):
                raise ValueError(f"{self.label}: choose which weekday and which week")
        return self


class WorkSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocks: list[ScheduleBlock] = Field(default_factory=list, max_length=MAX_BLOCKS)

    @model_validator(mode="after")
    def valid_blocks(self) -> WorkSchedule:
        ids = [b.id for b in self.blocks]
        if len(set(ids)) != len(ids):
            raise ValueError("Each block needs its own ID")
        clash = same_rank_overlap([b.model_dump() for b in self.blocks])
        if clash:
            first, second = clash
            raise ValueError(
                f"{first} and {second} overlap and neither takes precedence: they repeat "
                "on the same cadence and last equally long. Change one's times, or its "
                "cadence."
            )
        return self


# ------------------------------------------------------------------ the default schedule

DEFAULT_TEMPLATE: list[dict[str, Any]] = [
    {
        "id": "features",
        "label": "New feature work",
        "focus": "Build planned features and the tickets that move milestones forward.",
        "cadence": "daily",
        "start": "08:00",
        "end": "17:00",
        "keywords": ["feature", "build", "implement", "add"],
    },
    {
        "id": "hardening",
        "label": "Application hardening",
        "focus": "Fix bugs, add tests, close security and reliability gaps, speed things up.",
        "cadence": "daily",
        "start": "17:00",
        "end": "00:00",
        "keywords": ["bug", "fix", "test", "security", "reliability", "performance", "hardening"],
    },
    {
        "id": "nightly-release",
        "label": "Nightly release",
        "focus": "Prepare the day's finished work for release: changelog, version, checks.",
        "cadence": "daily",
        "start": "00:00",
        "end": "01:00",
        "keywords": ["release", "changelog", "version"],
    },
    {
        "id": "gardening",
        "label": "Process improvement and gardening",
        "focus": "Refactor, tidy up, improve docs, tooling and the way the team works.",
        "cadence": "daily",
        "start": "00:00",
        "end": "08:00",
        "keywords": ["refactor", "cleanup", "docs", "process", "tooling", "dependency"],
    },
    {
        "id": "weekly-release",
        "label": "Weekly release",
        "focus": "Stabilize the week's work and get it ready to ship.",
        "cadence": "weekly",
        "days": [4],
        "start": "13:00",
        "end": "17:00",
        "keywords": ["release", "changelog", "regression"],
    },
    {
        "id": "dependency-audit",
        "label": "Dependency updates and audit",
        "focus": "Update dependencies, review advisories and remove what's unused.",
        "cadence": "monthly",
        "nthWeek": 1,
        "weekday": 0,
        "start": "09:00",
        "end": "12:00",
        "keywords": ["dependency", "upgrade", "audit", "advisory"],
    },
    {
        "id": "quarterly-review",
        "label": "Quarterly planning and security review",
        "focus": "Review security, revisit the roadmap and propose next quarter's work.",
        "cadence": "quarterly",
        "quarterMonth": 1,
        "nthWeek": 1,
        "weekday": 1,
        "start": "09:00",
        "end": "17:00",
        "keywords": ["security", "planning", "roadmap"],
    },
]


def template() -> dict[str, Any]:
    """The suggested schedule. Offered to the user; never applied without them."""
    return WorkSchedule.model_validate({"blocks": DEFAULT_TEMPLATE}).model_dump()


# ------------------------------------------------------------------------ recurrence


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def length(block: dict[str, Any]) -> int:
    """How many minutes the block lasts on the clock. The same start and end is a whole day."""
    span = (_minutes(block["end"]) - _minutes(block["start"])) % DAY_MINUTES
    return span or DAY_MINUTES


def precedence(block: dict[str, Any], index: int) -> tuple[int, int, int]:
    """Smaller wins: longer cadence first, then the shorter block, then list order."""
    return (-CADENCE_RANK[block.get("cadence", "daily")], length(block), index)


def _month_rule(block: dict[str, Any], day: date) -> bool:
    last = calendar.monthrange(day.year, day.month)[1]
    if block.get("monthDay") is not None:
        return day.day == min(int(block["monthDay"]), last)
    if day.weekday() != block.get("weekday"):
        return False
    nth = block.get("nthWeek")
    if nth == -1:
        return day.day + 7 > last
    return (day.day - 1) // 7 + 1 == nth


def occurs_on(block: dict[str, Any], day: date) -> bool:
    """Whether one of the block's occurrences starts on this local date."""
    cadence = block.get("cadence", "daily")
    if cadence == "daily":
        return True
    if cadence == "weekly":
        return day.weekday() in block.get("days", [])
    if cadence == "quarterly" and (day.month - 1) % 3 + 1 != block.get("quarterMonth", 1):
        return False
    return _month_rule(block, day)


def _local(day: date, clock: str, zone: ZoneInfo) -> datetime:
    hour, minute = (int(x) for x in clock.split(":"))
    # Round-trip through UTC: a time a spring-forward change skips moves an hour later, and
    # a time a fall-back change repeats uses its first occurrence.
    return datetime.combine(day, time(hour, minute), tzinfo=zone).astimezone(UTC)


def occurrence(block: dict[str, Any], day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = _local(day, block["start"], zone)
    end_day = day + timedelta(days=1) if _minutes(block["end"]) <= _minutes(block["start"]) else day
    return start, _local(end_day, block["end"], zone)


def _zone(config: dict[str, Any]) -> ZoneInfo:
    return ZoneInfo((config.get("operatingHours") or {}).get("timezone") or "UTC")


def _blocks(config: dict[str, Any]) -> list[dict[str, Any]]:
    return list((config.get("workSchedule") or {}).get("blocks") or [])


def active_at(
    blocks: list[dict[str, Any]], zone: ZoneInfo, at: datetime
) -> tuple[dict[str, Any], datetime, datetime] | None:
    """The winning block at an instant, with its occurrence's start and end."""
    today = at.astimezone(zone).date()
    found = []
    for index, block in enumerate(blocks):
        for day in (today - timedelta(days=1), today):
            if occurs_on(block, day):
                start, end = occurrence(block, day, zone)
                if start <= at < end:
                    found.append((precedence(block, index), block, start, end))
    if not found:
        return None
    _, block, start, end = min(found, key=lambda item: item[0])
    return block, start, end


def _boundaries(
    blocks: list[dict[str, Any]], zone: ZoneInfo, first: date, days: int
) -> list[datetime]:
    edges: set[datetime] = set()
    for offset in range(-1, days + 1):
        day = first + timedelta(days=offset)
        for block in blocks:
            if occurs_on(block, day):
                edges.update(occurrence(block, day, zone))
    return sorted(edges)


def next_change(
    blocks: list[dict[str, Any]], zone: ZoneInfo, at: datetime
) -> tuple[datetime, dict[str, Any] | None] | None:
    """When the winning block next changes, and what wins then (None: no block)."""
    if not blocks:
        return None
    current = active_at(blocks, zone, at)
    current_id = current[0]["id"] if current else None
    today = at.astimezone(zone).date()
    # Daily and weekly blocks repeat within a week, so a week settles almost every
    # schedule. Otherwise only a monthly or quarterly occurrence can change anything.
    for candidates, days in (
        (blocks, 9),
        (
            [b for b in blocks if b.get("cadence") in {"monthly", "quarterly"}],
            LONG_HORIZON_DAYS,
        ),
    ):
        for edge in _boundaries(candidates, zone, today, days):
            if edge <= at:
                continue
            winner = active_at(blocks, zone, edge)
            if (winner[0]["id"] if winner else None) != current_id:
                return edge, winner[0] if winner else None
    return None


# ------------------------------------------------------------------------ validation


def _naive_intervals(block: dict[str, Any], first: date, last: date) -> list[tuple[int, int]]:
    """Occurrences as minute offsets from ``first``, ignoring daylight saving."""
    found = []
    day = first
    while day <= last:
        if occurs_on(block, day):
            start = (day - first).days * DAY_MINUTES + _minutes(block["start"])
            found.append((start, start + length(block)))
        day += timedelta(days=1)
    return found


def _intersect(left: list[tuple[int, int]], right: list[tuple[int, int]]) -> bool:
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i][0] < right[j][1] and right[j][0] < left[i][1]:
            return True
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return False


def same_rank_overlap(blocks: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Two overlapping blocks the precedence rule can't order, by label."""
    for i, first in enumerate(blocks):
        for second in blocks[i + 1 :]:
            if first["cadence"] != second["cadence"] or length(first) != length(second):
                continue
            if first["cadence"] in {"daily", "weekly"}:
                # A week repeats; the extra days catch an overnight block wrapping around.
                begin, end = date(2027, 1, 4), date(2027, 1, 18)
            else:
                # Eight years cover every weekday a date can fall on, and two leap years.
                begin, end = date(2027, 1, 1), date(2034, 12, 31)
            if _intersect(
                _naive_intervals(first, begin, end), _naive_intervals(second, begin, end)
            ):
                return first["label"], second["label"]
    return None


def _open_minute(hours: dict[str, Any] | None, day: int, minute: int) -> bool:
    """Whether working hours are open at a minute of a week, Monday 0, ignoring time zones."""
    if not hours or hours.get("mode", "always") == "always":
        return True
    for window in hours.get("windows", []):
        start, end = _minutes(window["start"]), _minutes(window["end"])
        days = window["days"]
        if start < end:
            if day in days and start <= minute < end:
                return True
        elif (day in days and minute >= start) or ((day - 1) % 7 in days and minute < end):
            return True
    return False


def _clock(minute: int) -> str:
    return f"{minute // 60 % 24:02d}:{minute % 60:02d}"


def _day_list(days: list[int]) -> str:
    if days == list(range(7)):
        return "Every day"
    if days == [0, 1, 2, 3, 4]:
        return "Weekdays"
    if days == [5, 6]:
        return "Weekends"
    return ", ".join(DAY_NAMES[d] for d in days)


@lru_cache(maxsize=64)
def _issues(key: str) -> tuple[dict[str, Any], ...]:
    hours, blocks = json.loads(key)
    if not blocks:
        return ()
    week = 7 * DAY_MINUTES
    weekly = [b for b in blocks if b["cadence"] in {"daily", "weekly"}]
    covered = bytearray(week)
    issues: list[dict[str, Any]] = []
    for block in weekly:
        days = range(7) if block["cadence"] == "daily" else block["days"]
        open_minutes = 0
        for day in days:
            start = day * DAY_MINUTES + _minutes(block["start"])
            for minute in range(start, start + length(block)):
                covered[minute % week] = 1
                open_minutes += _open_minute(hours, minute // DAY_MINUTES % 7, minute % DAY_MINUTES)
        if not open_minutes:
            issues.append(
                {
                    "kind": "outside_hours",
                    "blockId": block["id"],
                    "text": f"{block['label']} is outside working hours, so nothing runs then.",
                }
            )
    if not weekly:
        return tuple(issues)
    # Gaps: open minutes no daily or weekly block covers, grouped by their times of day.
    # The scan starts at a minute that isn't a gap, so a gap over the end of Sunday stays
    # one gap.

    def gap(minute: int) -> bool:
        minute %= week
        return not covered[minute] and _open_minute(
            hours, minute // DAY_MINUTES, minute % DAY_MINUTES
        )

    begin = next((m for m in range(week) if not gap(m)), 0)
    gaps: dict[tuple[int, int], list[int]] = {}
    minute = begin
    while minute < begin + week:
        if not gap(minute):
            minute += 1
            continue
        start = minute
        while minute < begin + week and gap(minute):
            minute += 1
        day, offset = divmod(start % week, DAY_MINUTES)
        gaps.setdefault((offset, minute - start), []).append(day)
    for (offset, span), days in sorted(gaps.items(), key=lambda item: (item[1][0], item[0])):
        if span >= DAY_MINUTES:
            text = f"{DAY_NAMES[days[0]]} {_clock(offset)} for {span // 60} hours"
        else:
            text = f"{_day_list(sorted(days))} {_clock(offset)}–{_clock(offset + span)}"
        issues.append(
            {
                "kind": "gap",
                "days": sorted(days),
                "start": _clock(offset),
                "end": _clock(offset + span),
                "text": f"No block covers {text}. Workers use their usual priorities then.",
            }
        )
    return tuple(issues)


def issues(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Warnings worth showing: uncovered working time and blocks outside working hours."""
    key = json.dumps(
        [config.get("operatingHours") or {"mode": "always"}, _blocks(config)], sort_keys=True
    )
    return [dict(item) for item in _issues(key)]


# ------------------------------------------------------------------------- describing


def _time_range(block: dict[str, Any]) -> str:
    if block["start"] == block["end"]:
        return "all day" if block["start"] == "00:00" else f"{block['start']} for 24 hours"
    return f"{block['start']}–{block['end']}"


def describe(block: dict[str, Any]) -> str:
    """When a block repeats, in words: "Every day 08:00–17:00", "1st Monday monthly 09:00–12:00"."""
    cadence = block.get("cadence", "daily")
    times = _time_range(block)
    if cadence == "daily":
        return f"Every day {times}"
    if cadence == "weekly":
        days = block.get("days", [])
        named = (
            DAY_PLURALS[days[0]]
            if len(days) == 1
            else "Weekdays"
            if days == [0, 1, 2, 3, 4]
            else "Weekends"
            if days == [5, 6]
            else ", ".join(DAY_NAMES[d] for d in days)
        )
        return f"{named} {times}"
    if block.get("monthDay") is not None:
        day = int(block["monthDay"])
        which = "last day" if day == 31 else f"day {day}"
    else:
        which = f"{ORDINALS[block['nthWeek']]} {LONG_DAYS[block['weekday']]}"
    if cadence == "monthly":
        return f"Monthly, {which}, {times}"
    month = ORDINALS[int(block.get("quarterMonth", 1))]
    return f"Quarterly, {which} of the {month} month, {times}"


def _view(block: dict[str, Any]) -> dict[str, Any]:
    """The whole block, with when it repeats in words."""
    return {
        **{key: value for key, value in block.items() if value is not None},
        "focus": block.get("focus", ""),
        "cadence": block.get("cadence", "daily"),
        "keywords": list(block.get("keywords", [])),
        "when": describe(block),
    }


def checked(value: Any) -> dict[str, Any]:
    """A work schedule validated on its own, refused with the first reason in plain words."""
    from pydantic import ValidationError

    try:
        return WorkSchedule.model_validate(value).model_dump()
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ValueError(str(first.get("msg", "")).removeprefix("Value error, ")) from exc


def schedule_state(config: dict[str, Any], at: datetime | None = None) -> dict[str, Any]:
    """The block in effect now, the one after it and when it changes, for people and runs."""
    at = at or datetime.now(UTC)
    zone = _zone(config)
    blocks = _blocks(config)
    current = active_at(blocks, zone, at)
    change = next_change(blocks, zone, at)
    return {
        "configured": bool(blocks),
        "timezone": str(zone),
        "current": {
            **_view(current[0]),
            "startedAt": current[1].isoformat(),
            "endsAt": current[2].isoformat(),
        }
        if current
        else None,
        "next": _view(change[1]) if change and change[1] else None,
        "changesAt": change[0].isoformat() if change else None,
        "issues": issues(config),
    }


def matches(block: dict[str, Any] | None, text: str) -> bool:
    """Whether a ticket's text names one of the block's keywords, as a whole word."""
    if not block or not block.get("keywords"):
        return False
    folded = text.casefold()
    return any(
        re.search(r"(?<![a-z0-9])" + re.escape(word.casefold()), folded)
        for word in block["keywords"]
    )


def ticket_text(config: dict[str, Any], task: dict[str, Any]) -> str:
    epic: dict[str, Any] = next(
        (e for e in config.get("epics", []) if e.get("id") == task.get("epicId")), {}
    )
    initiative: dict[str, Any] = next(
        (p for p in config.get("projects", []) if p["id"] == task.get("project")), {}
    )
    return "\n".join(
        str(part or "")
        for part in (
            task.get("title"),
            str(task.get("description") or "")[:2000],
            task.get("doneWhen"),
            epic.get("title"),
            initiative.get("name"),
        )
    )


def run_brief(state: dict[str, Any]) -> str:
    """The lines a worker's instructions get about the current block."""
    current, following = state.get("current"), state.get("next")
    if not state.get("configured"):
        return ""
    lines = ["Work schedule (" + state["timezone"] + "):"]
    if current:
        lines.append(
            f"- Now: {current['label']} until {current['endsAt']}. Focus: "
            + (current["focus"] or current["label"])
        )
        if current["keywords"]:
            lines.append("  Favor tickets about: " + ", ".join(current["keywords"]) + ".")
    else:
        lines.append("- Now: no block, so use your usual priorities.")
    if following:
        lines.append(f"- Next: {following['label']} from {state['changesAt']}.")
    elif state.get("changesAt"):
        lines.append(f"- From {state['changesAt']}: no block.")
    lines.append(
        "Pick the assigned tickets that fit the current focus first, and propose work that "
        "fits it. " + HANDOFF
    )
    return "\n".join(lines) + "\n"
