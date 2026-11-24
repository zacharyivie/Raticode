"""Provider usage limits: recognise them and find when they reset.

A worker or workflow step that hits its provider's usage limit can't succeed until the
limit resets, and retrying a few minutes later (the temporary-failure retries) only fails
again. Rem parks a task's run until the reset the provider named, or checks again every
half hour when it named none, within a bounded number of waits; the trigger scheduler
(``rattish.trigger_service``) does the same for a triggered workflow run. Errors that
never reset on their own, such as an empty credit balance or an organization's spend
cap, aren't usage limits here, so the work fails as before.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# How many times one run waits for a limit to reset before it is blocked.
MAX_WAITS = 6
# The wait when the provider didn't say when the limit resets.
UNKNOWN_RESET_WAIT = timedelta(minutes=30)
# A named reset further away than this is waited for in steps of this length.
LONGEST_WAIT = timedelta(hours=6)
# Started a little after the reset, so the provider has counted it.
RESET_GRACE = timedelta(minutes=2)
# Only the start and end of a failed call's text are read: provider errors sit there.
SCAN_CHARS = 4000

USAGE_LIMIT = re.compile(
    r"usage limit|(?:hit|reached) (?:your |the )?(?:usage |weekly |daily |session |"
    r"5-hour |five-hour |monthly )?limit|(?:session|weekly|daily|5-hour|five-hour|monthly) "
    r"limit (?:reached|exceeded|hit)|quota (?:exceeded|exhausted)|"
    r"exceeded your (?:current )?quota|insufficient_quota",
    re.IGNORECASE,
)
# Credit and spending caps never reset on their own, whatever else the text says.
NEVER_RESETS = re.compile(
    r"credit balance|out of credits|insufficient (?:credits|balance|funds)|"
    r"enforced_spend_limit|spend(?:ing)? (?:limit|cap)",
    re.IGNORECASE,
)
_EPOCH = re.compile(r"limit reached\|(\d{10})\b", re.IGNORECASE)
_TRY_IN = re.compile(
    r"(?:try again|retry|resets?|available again) in\s+((?:\d+\s*(?:days?|d|hours?|hrs?|h|"
    r"minutes?|mins?|m|seconds?|secs?|s)\b[\s,and]*)+)",
    re.IGNORECASE,
)
_PART = re.compile(r"(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b", re.I)
_CLOCK = re.compile(
    r"(?:resets?|try again|available again)(?: at)?\s+(?:on\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s*\(([A-Za-z_]+/[A-Za-z_/+-]+|UTC)\))?",
    re.IGNORECASE,
)


def _scan(text: str) -> str:
    return text if len(text) <= 2 * SCAN_CHARS else text[:SCAN_CHARS] + "\n" + text[-SCAN_CHARS:]


def failure_text(exc: BaseException) -> str:
    """The failure's message and those of its causes, as the run's error shows them."""
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(str(current))
        current = current.__cause__ or current.__context__
    return "\n".join(parts)


def usage_limited(exc: BaseException) -> bool:
    """Whether a run failed because its provider's usage limit was reached."""
    if isinstance(exc, (TimeoutError, InterruptedError)):
        return False
    return usage_limited_text(failure_text(exc))


def usage_limited_text(text: str) -> bool:
    """Whether a failed provider call's text says a usage limit that will reset was reached."""
    sample = _scan(text)
    return USAGE_LIMIT.search(sample) is not None and NEVER_RESETS.search(sample) is None


def clock_text(moment: datetime, now: datetime) -> str:
    """A time on this computer's clock: ``05:00``, or ``05:00 on Tue`` on another day."""
    local = moment.astimezone()
    return local.strftime("%H:%M") + (
        "" if local.date() == now.astimezone().date() else local.strftime(" on %a")
    )


def reset_at(text: str, now: datetime, local: ZoneInfo | None = None) -> datetime | None:
    """When the limit named in ``text`` resets, if it says; None when it doesn't.

    Understands Claude Code's ``…limit reached|<epoch>``, "try again in 2 hours 13
    minutes", and a clock time such as "resets 3pm (America/Los_Angeles)" or "try again
    at 15:05", which means the next time that clock time comes round.
    """
    text = _scan(text)
    match = _EPOCH.search(text)
    if match:
        found = datetime.fromtimestamp(int(match[1]), UTC)
        return found if found > now else None
    match = _TRY_IN.search(text)
    if match:
        seconds = 0
        for amount, unit in _PART.findall(match[1]):
            unit = unit.lower()
            scale = (
                86400
                if unit.startswith("d")
                else 3600
                if unit.startswith("h")
                else 60
                if unit.startswith("m")
                else 1
            )
            seconds += int(amount) * scale
        return now + timedelta(seconds=seconds) if seconds else None
    match = _CLOCK.search(text)
    if match:
        hour, minute = int(match[1]), int(match[2] or 0)
        meridiem = (match[3] or "").lower()
        if meridiem:
            if not 1 <= hour <= 12:
                return None
            hour = hour % 12 + (12 if meridiem == "pm" else 0)
        elif not match[2] or hour > 23:
            # A bare number without am/pm or minutes is too ambiguous to be a time.
            return None
        zone: ZoneInfo | None = local
        if match[4]:
            try:
                zone = ZoneInfo("UTC" if match[4].upper() == "UTC" else match[4])
            except (ZoneInfoNotFoundError, ValueError):
                zone = local
        here = now.astimezone(zone) if zone else now.astimezone()
        candidate = here.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= here:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)
    return None


def next_check(text: str, now: datetime) -> tuple[datetime, bool]:
    """When to try the run again, and whether that is the reset the provider named."""
    found = reset_at(text, now)
    if found is None:
        return now + UNKNOWN_RESET_WAIT, False
    if found - now > LONGEST_WAIT:
        return now + LONGEST_WAIT, False
    return found + RESET_GRACE, True
