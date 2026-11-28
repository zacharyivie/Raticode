"""A fixed-time cron schedule runs once on the day clocks go back, wherever it is saved.

Triggers, Rem's Custom cron tasks and organization routines read one shared table of
cases. A time that happens twice runs once, at the first, as cron(8) and Rem's Daily
tasks do. Schedules with ``*`` or ``*/N`` in the minute or hour keep running in both real
hours. The day clocks go forward stays as it was.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from gofer.rattish.triggers import occurrences_between, parse_cron, preview_cron
from gofer.ui.organization_operations import next_occurrence as routine_next
from gofer.ui.rem_home_models import Schedule
from gofer.ui.rem_task_workflow import trigger_schedule
from gofer.utils.cron_times import fixed_time, next_cron_time

NEW_YORK = "America/New_York"
LONDON = "Europe/London"

# (time zone, local date, expression, UTC times from local midnight to 04:00 that day)
CASES = [
    # New York falls back on 2026-11-01: 01:00-02:00 happens at 05:00 and 06:00 UTC.
    (NEW_YORK, "2026-11-01", "30 1 * * *", ["05:30"]),
    (NEW_YORK, "2026-11-01", "0,30 1 * * *", ["05:00", "05:30"]),
    (
        NEW_YORK,
        "2026-11-01",
        "*/30 * * * *",
        ["04:00", "04:30", "05:00", "05:30", "06:00", "06:30"]
        + ["07:00", "07:30", "08:00", "08:30", "09:00"],
    ),
    (NEW_YORK, "2026-11-01", "0 */2 * * *", ["04:00", "07:00", "09:00"]),
    # New York springs forward on 2026-03-08: 02:00-03:00 never happens.
    (NEW_YORK, "2026-03-08", "30 1 * * *", ["06:30"]),
    (NEW_YORK, "2026-03-08", "0,30 1 * * *", ["06:00", "06:30"]),
    (
        NEW_YORK,
        "2026-03-08",
        "*/30 * * * *",
        ["05:00", "05:30", "06:00", "06:30", "07:00", "07:30", "08:00"],
    ),
    (NEW_YORK, "2026-03-08", "0 */2 * * *", ["05:00", "07:00", "08:00"]),
    # London falls back on 2026-10-25: 01:00-02:00 happens at 00:00 and 01:00 UTC.
    (LONDON, "2026-10-25", "30 1 * * *", ["00:30"]),
    (LONDON, "2026-10-25", "0,30 1 * * *", ["00:00", "00:30"]),
    (
        LONDON,
        "2026-10-25",
        "*/30 * * * *",
        ["-23:00", "-23:30", "00:00", "00:30", "01:00", "01:30"]
        + ["02:00", "02:30", "03:00", "03:30", "04:00"],
    ),
    (LONDON, "2026-10-25", "0 */2 * * *", ["-23:00", "02:00", "04:00"]),
    # London springs forward on 2026-03-29: 01:00-02:00 never happens.
    (LONDON, "2026-03-29", "30 1 * * *", ["01:30"]),
    (LONDON, "2026-03-29", "0,30 1 * * *", ["01:00"]),
    (
        LONDON,
        "2026-03-29",
        "*/30 * * * *",
        ["00:00", "00:30", "01:00", "01:30", "02:00", "02:30", "03:00"],
    ),
    (LONDON, "2026-03-29", "0 */2 * * *", ["00:00", "01:00", "03:00"]),
]


def window(timezone: str, day: str) -> tuple[datetime, datetime]:
    """One second before local midnight to local 04:00 on ``day``, in UTC."""
    zone = ZoneInfo(timezone)
    start = datetime.fromisoformat(day).replace(tzinfo=zone)
    return (start - timedelta(seconds=1)).astimezone(UTC), start.replace(hour=4).astimezone(UTC)


def utc_times(day: str, clock_times: list[str]) -> list[datetime]:
    """``HH:MM`` on ``day`` in UTC; a leading ``-`` means the day before."""
    base = datetime.fromisoformat(day).replace(tzinfo=UTC)
    result = []
    for text in clock_times:
        date = base - timedelta(days=1) if text.startswith("-") else base
        hour, minute = (int(part) for part in text.lstrip("-").split(":"))
        result.append(date.replace(hour=hour, minute=minute))
    return result


def walk(next_after, start: datetime, until: datetime) -> list[datetime]:
    """Follow ``next_after`` (returning ISO text or a datetime) from ``start`` to ``until``."""
    found: list[datetime] = []
    cursor = start
    for _ in range(50):
        upcoming = next_after(cursor)
        if upcoming is None:
            break
        moment = datetime.fromisoformat(upcoming) if isinstance(upcoming, str) else upcoming
        if moment > until:
            break
        found.append(moment.astimezone(UTC))
        cursor = moment
    return found


@pytest.mark.parametrize(("timezone", "day", "expression", "expected"), CASES)
def test_every_cron_path_reads_clock_changes_the_same_way(
    timezone: str, day: str, expression: str, expected: list[str]
) -> None:
    start, until = window(timezone, day)
    want = utc_times(day, expected)

    trigger = parse_cron(expression, timezone)
    assert occurrences_between(trigger, start, until) == want
    preview = preview_cron(expression, timezone, count=len(want), now=start)
    assert [datetime.fromisoformat(text) for text in preview] == want

    task = Schedule(kind="cron", cron=expression, timezone=timezone)
    assert walk(task.next, start, until) == want

    routine = {"id": "nightly", "cron": expression, "timezone": timezone}
    assert walk(lambda at: routine_next(routine, at), start, until) == want


def test_a_repeated_time_runs_once_at_the_first() -> None:
    # 01:30 happens at 05:30 UTC (EDT) and again at 06:30 UTC (EST) on 2026-11-01.
    schedule = parse_cron("30 1 * * *", NEW_YORK)
    first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert next_cron_time(schedule, first - timedelta(minutes=1)) == first
    # Asked from between the two, the second is skipped for the next day's 01:30 EST.
    assert next_cron_time(schedule, first) == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)
    # Every 30 minutes still runs in both real hours.
    stepped = parse_cron("*/30 * * * *", NEW_YORK)
    assert next_cron_time(stepped, first) == datetime(2026, 11, 1, 6, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("expression", "fixed"),
    [
        ("30 1 * * *", True),
        ("0,30 1 * * *", True),
        # A range with a step is written out, not a wildcard, the way cron(8) reads it.
        ("0-59/30 1 * * *", True),
        ("*/30 * * * *", False),
        ("0 */2 * * *", False),
        ("30 * * * *", False),
        ("*/15 1 * * *", False),
    ],
)
def test_fixed_time_means_no_wildcard_in_minute_or_hour(expression: str, fixed: bool) -> None:
    assert fixed_time(parse_cron(expression, "UTC")) is fixed


def test_switching_a_daily_task_over_keeps_its_times() -> None:
    task = Schedule(kind="daily", local_time="01:30", timezone=NEW_YORK)
    expression, timezone = trigger_schedule(task.model_dump())
    assert (expression, timezone) == ("30 1 * * *", NEW_YORK)
    start = datetime(2026, 10, 31, tzinfo=ZoneInfo(NEW_YORK)).astimezone(UTC)
    until = datetime(2026, 11, 3, 23, 59, tzinfo=ZoneInfo(NEW_YORK)).astimezone(UTC)

    daily = walk(task.next, start, until)
    triggered = occurrences_between(parse_cron(expression, timezone), start, until)
    assert triggered == daily
    assert [moment.strftime("%m-%d %H:%M") for moment in daily] == [
        "10-31 05:30",
        "11-01 05:30",
        "11-02 06:30",
        "11-03 06:30",
    ]
