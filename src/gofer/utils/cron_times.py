"""The next time a cron schedule runs, read the way cron(8) reads the day clocks go back.

Triggers, Rem's Custom cron tasks and organization routines all ask this one helper, so a
schedule means the same times wherever it is saved.

When clocks go back, an hour of local time happens twice. A fixed-time schedule, such as
``30 1 * * *``, runs once, at the first of the two, the same as Rem's Daily and Weekly
tasks. A schedule whose minute or hour is ``*`` or a stepped ``*/N`` runs in both real
hours, because "every 30 minutes" should keep its spacing. The day clocks go forward is
left to APScheduler, which runs a time that doesn't exist at the same distance past the
change, also matching Rem's Daily tasks.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.cron.expressions import AllExpression

# A local time repeats once at most on the day clocks go back, so one retry reaches a time
# that isn't the second of a pair. The bound only guards against an unusual time zone rule.
_MAX_REPEAT_SKIPS = 4


def fixed_time(schedule: CronTrigger) -> bool:
    """Whether neither the minute nor the hour field is a bare or stepped ``*``.

    RangeExpression (such as ``0-59/30`` or ``5``) subclasses AllExpression, so the type is
    compared exactly: only ``*`` and ``*/N`` are wildcards here.
    """
    return not any(
        type(expression) is AllExpression
        for field in schedule.fields
        if field.name in {"minute", "hour"}
        for expression in field.expressions
    )


def _repeated(moment: datetime, zone: tzinfo) -> bool:
    """Whether ``moment`` is the second of two instants with the same local wall time."""
    instant = moment.astimezone(UTC)
    local = instant.astimezone(zone)
    return local.replace(fold=0).astimezone(UTC) < instant


def next_cron_time(schedule: CronTrigger, after: datetime) -> datetime | None:
    """The first time ``schedule`` runs strictly after ``after``, in UTC, or None if never.

    UTC because two aware times in one zone compare by wall clock: 01:30 EDT equals
    01:30 EST to Python, and a time in a repeated hour never equals one in another zone.
    """
    fixed = fixed_time(schedule)
    cursor = after.astimezone(UTC)
    for _ in range(_MAX_REPEAT_SKIPS):
        upcoming: datetime | None = schedule.get_next_fire_time(
            None, cursor + timedelta(microseconds=1)
        )
        if upcoming is None:
            return None
        cursor = upcoming.astimezone(UTC)
        if not fixed or not _repeated(cursor, schedule.timezone):
            return cursor
        # The first of the pair already came: before ``after``, or as an earlier answer.
    # Only an unusual time zone rule gets here; running late beats never running.
    return cursor
