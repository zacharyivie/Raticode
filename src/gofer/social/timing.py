"""Best-time hints and spacing between posts.

The windows are general guidance from published studies of when people engage on each
network (Sprout Social's 2025 analysis: midweek mid-mornings to early afternoon, LinkedIn
around midday, YouTube near 1 p.m., TikTok noon to early evening). They are not the user's
own audience data, so every hint says "usually" and nothing here moves a post on its own.
Suggestions only fill the time field of a draft; the user still approves the time.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gofer.utils.instants import stored_instant

WEEKDAYS = (0, 1, 2, 3, 4)
MIDWEEK = (1, 2, 3)
# Two posts to one account closer than this compete with each other.
SPACING = timedelta(hours=3)
# Local hours when few people read: a post set for then gets a hint.
QUIET_HOURS = range(0, 6)
SEARCH_DAYS = 14


@dataclass(frozen=True)
class Window:
    days: tuple[int, ...]  # 0 is Monday
    start: int  # local hour, inclusive
    end: int  # local hour, exclusive
    reason: str


WINDOWS: dict[str, tuple[Window, ...]] = {
    "linkedin": (Window(MIDWEEK, 10, 13, "LinkedIn is usually busiest around midday, Tue-Thu."),),
    "x": (Window(MIDWEEK, 9, 12, "X usually does best mid-morning, Tue-Thu."),),
    "facebook": (Window(WEEKDAYS, 9, 12, "Facebook usually does best on weekday mornings."),),
    "youtube": (Window(WEEKDAYS, 12, 15, "YouTube viewing usually peaks in the early afternoon."),),
    "tiktok": (Window(WEEKDAYS, 12, 18, "TikTok is usually busiest from noon to early evening."),),
    "discord": (
        Window(WEEKDAYS, 10, 14, "Most communities are around from late morning on weekdays."),
    ),
    "patreon": (Window(WEEKDAYS, 10, 14, "Late morning on a weekday is a safe default."),),
    "buymeacoffee": (Window(WEEKDAYS, 10, 14, "Late morning on a weekday is a safe default."),),
}
SOURCE_NOTE = (
    "General guidance from published engagement studies, not your own audience. "
    "Try a few times and keep what works."
)


def zone(name: Any) -> tzinfo:
    """The named time zone, or UTC when the name is missing or unknown."""
    if isinstance(name, str) and name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ZoneInfo("UTC")


def _fits(platform: str, local: datetime) -> Window | None:
    for window in WINDOWS.get(platform, ()):
        if local.weekday() in window.days and window.start <= local.hour < window.end:
            return window
    return None


def suggest_times(
    platforms: Iterable[str],
    timezone: Any,
    now: datetime,
    taken: Iterable[datetime] = (),
    *,
    count: int = 3,
) -> list[dict[str, Any]]:
    """The next good times for a post to these platforms, at most one per day.

    A time that suits every platform beats one that suits only some. Times within
    ``SPACING`` of another post already planned for the same accounts are skipped.
    """
    wanted = [item for item in dict.fromkeys(platforms) if item in WINDOWS]
    if not wanted:
        wanted = ["discord"]
    tz = zone(timezone)
    busy = list(taken)
    local = now.astimezone(tz).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    # The best hour of each day: the most platforms suited, then the earliest.
    by_day: dict[str, tuple[int, datetime, list[Window], list[str]]] = {}
    for step in range(SEARCH_DAYS * 24):
        # Walk local wall-clock hours, so the hours stay right across a clock change.
        candidate = (local + timedelta(hours=step)).replace(tzinfo=None).replace(tzinfo=tz)
        fits = [(platform, _fits(platform, candidate)) for platform in wanted]
        matched = [(platform, window) for platform, window in fits if window is not None]
        if not matched:
            continue
        if any(abs(candidate - other) < SPACING for other in busy):
            continue
        day = candidate.date().isoformat()
        if day in by_day and by_day[day][0] >= len(matched):
            continue
        by_day[day] = (
            len(matched),
            candidate,
            [window for _, window in matched],
            [platform for platform, _ in matched],
        )
    best = sorted(by_day.values(), key=lambda item: item[1])
    full = [item for item in best if item[0] == len(wanted)]
    chosen = (full or sorted(best, key=lambda item: (-item[0], item[1])))[:count]
    chosen.sort(key=lambda item: item[1])
    return [
        {
            "at": candidate.isoformat(timespec="minutes"),
            "fits": names,
            "reason": " ".join(dict.fromkeys(window.reason for window in windows)),
        }
        for _, candidate, windows, names in chosen
    ]


def windows_view() -> dict[str, list[dict[str, Any]]]:
    return {
        platform: [
            {"days": list(item.days), "start": item.start, "end": item.end, "reason": item.reason}
            for item in windows
        ]
        for platform, windows in WINDOWS.items()
    }


def timing_hints(
    post: Mapping[str, Any], others: Iterable[Mapping[str, Any]]
) -> list[dict[str, str]]:
    """Plain notes about a post's time: posts close together on one account, and quiet hours."""
    when = stored_instant(post.get("scheduled_at"), naive_as_utc=False)
    if when is None:
        return []
    hints: list[dict[str, str]] = []
    tz = zone(post.get("timezone"))
    local = when.astimezone(tz)
    if local.hour in QUIET_HOURS:
        hints.append(
            {
                "kind": "quiet_hours",
                "message": f"Goes out at {local.strftime('%H:%M')} {post.get('timezone') or 'UTC'}"
                ", when few people are reading.",
            }
        )
    accounts = {
        str(variant.get("account_id") or variant.get("platform")): variant.get("account_name")
        or variant.get("platform")
        for variant in post.get("variants") or []
    }
    for other in others:
        if other.get("id") == post.get("id"):
            continue
        other_when = stored_instant(other.get("scheduled_at"), naive_as_utc=False)
        if other_when is None or abs(other_when - when) >= SPACING:
            continue
        shared = sorted(
            {
                str(accounts[key])
                for key in (
                    str(v.get("account_id") or v.get("platform"))
                    for v in other.get("variants") or []
                )
                if key in accounts
            }
        )
        if not shared:
            continue
        minutes = int(abs(other_when - when).total_seconds() // 60)
        gap = f"{minutes} minutes" if minutes < 120 else f"{minutes // 60} hours"
        hints.append(
            {
                "kind": "spacing",
                "message": f"{', '.join(shared)}: “{other.get('title') or 'another post'}” is "
                f"{gap} {'before' if other_when <= when else 'after'} this one.",
            }
        )
    return hints[:5]
