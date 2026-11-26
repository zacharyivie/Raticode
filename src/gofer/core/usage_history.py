"""Usage history periods and token totals per time bucket for Settings > Usage.

The ledger keeps every call (nothing deletes rows), so a period only limits what is read.
Buckets follow the viewer's local calendar: hours, days, Monday weeks, months or years,
chosen from the period's length so a chart shows roughly 10 to 60 bars.

The series carries two groupings of the same tokens: per provider (``providers``) and per
provider account, the (provider, profile) pairs the activity table lists (``accounts``).
Charts switch between them without asking again.
"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gofer.core.provider_colors import account_style, provider_style
from gofer.core.usage_ledger import (
    LOCAL_ROWS,
    TOKEN_FIELDS,
    read_ledger_mark,
    summarize_calls,
    usage_db,
)
from gofer.utils.instants import stored_instant
from gofer.utils.paths import get_data_dir

# Preset periods and their length in days; "all" reads from the first recorded call.
USAGE_RANGES: dict[str, int | None] = {
    "24h": 1,
    "7d": 7,
    "30d": 30,
    "3mo": 90,
    "6mo": 180,
    "1y": 365,
    "5y": 1826,
    "all": None,
}
RANGE_LABELS = {
    "24h": "Last 24 hours",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "3mo": "Last 3 months",
    "6mo": "Last 6 months",
    "1y": "Last year",
    "5y": "Last 5 years",
    "all": "All time",
    "custom": "Custom range",
}
# Older clients send ?days=N. 90 was "Last 90 days" and is now 3mo.
LEGACY_DAYS = {
    1: "24h",
    7: "7d",
    30: "30d",
    90: "3mo",
    180: "6mo",
    365: "1y",
    1825: "5y",
    1826: "5y",
}
RANGE_ALIASES = {"1d": "24h", "90d": "3mo", "180d": "6mo", "365d": "1y", "12mo": "1y"}
DEFAULT_RANGE = "30d"
EARLIEST_DATE = date(2000, 1, 1)


class UsageRangeError(ValueError):
    """A period the usage history can't show; the message is safe to show the user."""


@dataclass(frozen=True)
class UsageRange:
    key: str
    start: datetime | None  # UTC; None reads from the first recorded call
    end: datetime  # UTC, exclusive
    zone: tzinfo
    zone_name: str
    start_date: str = ""  # a custom range's own dates, as the user chose them
    end_date: str = ""

    @property
    def days(self) -> int | None:
        return USAGE_RANGES.get(self.key)

    def payload(self, first: datetime | None = None) -> dict[str, Any]:
        start = self.start or first
        return {
            "key": self.key,
            "label": RANGE_LABELS[self.key],
            "days": self.days,
            "start": start.isoformat() if start else None,
            "end": self.end.isoformat(),
            "start_date": self.start_date or None,
            "end_date": self.end_date or None,
            "timezone": self.zone_name,
        }


def _local_zone() -> tuple[tzinfo, str]:
    """This computer's time zone, by IANA name where it can be found."""
    name = os.environ.get("TZ", "").lstrip(":")
    if not name:
        try:
            target = os.path.realpath("/etc/localtime")
            if "zoneinfo/" in target:
                name = target.split("zoneinfo/", 1)[1]
        except OSError:
            name = ""
    if name:
        try:
            return ZoneInfo(name), name
        except (ZoneInfoNotFoundError, ValueError):
            pass
    zone = datetime.now().astimezone().tzinfo or UTC
    return zone, str(zone)


def usage_zone(name: str | None) -> tuple[tzinfo, str]:
    """The viewer's time zone by IANA name, else this computer's."""
    if name:
        try:
            return ZoneInfo(str(name)[:64]), str(name)[:64]
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return _local_zone()


def _parse_date(value: str, field: str) -> date:
    try:
        parsed = date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise UsageRangeError(f"Choose a valid {field} date (YYYY-MM-DD).") from None
    if parsed < EARLIEST_DATE:
        raise UsageRangeError(f"The {field} date must be in {EARLIEST_DATE.year} or later.")
    return parsed


def resolve_range(
    *,
    period: str | None = None,
    days: int | str | None = None,
    start: str | None = None,
    end: str | None = None,
    tz: str | None = None,
    now: datetime | None = None,
) -> UsageRange:
    """Turn a request's period options into UTC bounds. Raises UsageRangeError."""
    zone, zone_name = usage_zone(tz)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    key = str(period or "").strip().lower()
    key = RANGE_ALIASES.get(key, key)
    if not key and (start or end):
        key = "custom"
    if not key and days not in (None, ""):
        try:
            key = LEGACY_DAYS[int(str(days))]
        except (KeyError, ValueError):
            raise UsageRangeError(
                "Usage period must be 1, 7, 30, 90, 180, 365 or 1825 days."
            ) from None
    key = key or DEFAULT_RANGE
    if key == "custom":
        if not start or not end:
            raise UsageRangeError("Choose both a start and an end date.")
        first, last = _parse_date(start, "start"), _parse_date(end, "end")
        if first > last:
            raise UsageRangeError("The start date must be on or before the end date.")
        today = current.astimezone(zone).date()
        if first > today:
            raise UsageRangeError("The start date can't be in the future.")
        begin = datetime.combine(first, datetime.min.time(), zone).astimezone(UTC)
        # The end date is included: read up to the start of the next day, never past now.
        finish = datetime.combine(last + timedelta(days=1), datetime.min.time(), zone)
        return UsageRange(
            "custom",
            begin,
            min(finish.astimezone(UTC), current),
            zone,
            zone_name,
            first.isoformat(),
            last.isoformat(),
        )
    if key not in USAGE_RANGES:
        raise UsageRangeError(
            "Usage period must be one of 24h, 7d, 30d, 3mo, 6mo, 1y, 5y, all or custom."
        )
    length = USAGE_RANGES[key]
    return UsageRange(
        key,
        current - timedelta(days=length) if length is not None else None,
        current,
        zone,
        zone_name,
    )


def bucket_for(span: timedelta) -> str:
    """The bucket size that gives a readable number of bars for a period this long."""
    if span <= timedelta(days=2):
        return "hour"
    if span <= timedelta(days=35):
        return "day"
    if span <= timedelta(days=400):
        return "week"
    if span <= timedelta(days=6 * 366):
        return "month"
    return "year"


def _floor(moment: datetime, bucket: str) -> datetime:
    """The local wall-clock start of the bucket holding ``moment`` (already local)."""
    moment = moment.replace(minute=0, second=0, microsecond=0)
    if bucket == "hour":
        return moment
    moment = moment.replace(hour=0)
    if bucket == "week":
        return moment - timedelta(days=moment.weekday())
    if bucket == "month":
        return moment.replace(day=1)
    if bucket == "year":
        return moment.replace(month=1, day=1)
    return moment


def _next(moment: datetime, bucket: str) -> datetime:
    if bucket == "hour":
        return moment + timedelta(hours=1)
    if bucket == "day":
        return moment + timedelta(days=1)
    if bucket == "week":
        return moment + timedelta(days=7)
    if bucket == "month":
        return moment.replace(year=moment.year + moment.month // 12, month=moment.month % 12 + 1)
    return moment.replace(year=moment.year + 1)


def _key(moment: datetime, bucket: str) -> str:
    """A bucket's identity: its UTC hour, or its first local day."""
    if moment.year < 1000:  # isoformat pads the year; strftime doesn't
        return moment.strftime("%Y-%m-%dT%H:00" if bucket == "hour" else "%Y-%m-%d")
    if bucket == "hour":
        return moment.isoformat()[:13] + ":00"
    return moment.date().isoformat()


def _stamp(local_naive: datetime, zone: tzinfo, bucket: str) -> str:
    if bucket == "hour":
        return local_naive.isoformat()
    return local_naive.replace(tzinfo=zone).isoformat()


# Calls are written with now_iso(): UTC ISO text with or without microseconds. Rows in
# any other form are read one by one, as before.
_UTC_TEXT = (
    "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00",
    "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]"
    ".[0-9][0-9][0-9][0-9][0-9][0-9]+00:00",
)
_KEEP_LIMIT = 32
# Long periods are read in up to this many slices at once, each of at least this many calls.
_MAX_SLICES = 4
_SLICE_ROWS = 25_000


@dataclass
class _Kept:
    """One period's history and what it was read from, so an unchanged ledger reuses it."""

    mark: tuple[int, int]
    start: datetime | None
    end: datetime
    first: datetime | None
    layout: tuple[str, ...]
    history: dict[str, Any]


_kept: dict[tuple[str, ...], _Kept] = {}
_kept_lock = threading.Lock()


def _range_filter(period: UsageRange) -> tuple[str, list[Any]]:
    # Only this computer's calls: a paired desktop's summary row isn't one of its accounts'.
    where = f" WHERE observed_at < ? AND {LOCAL_ROWS}"
    arguments: list[Any] = [period.end.isoformat()]
    if period.start is not None:
        where += " AND observed_at >= ?"
        arguments.append(period.start.isoformat())
    return where, arguments


def usage_history(data_dir: Path | None, period: UsageRange) -> dict[str, Any]:
    """Totals for the period, per provider account, plus reported tokens per bucket.

    The result is kept per period and reused while the ledger is unchanged, also when
    "now" moved on and the period's ends passed no call.
    """
    root = data_dir or get_data_dir()
    key = (str(root), period.key, period.zone_name, period.start_date, period.end_date)
    with usage_db(root) as db:
        started = db.execute("SELECT value FROM usage_meta WHERE key='started_at'").fetchone()[0]
        # Read before the calls, so one recorded meanwhile only makes the next read redo it.
        mark = read_ledger_mark(db)
        with _kept_lock:
            kept = _kept.get(key)
        if kept is not None and _still_valid(db, kept, mark, period):
            first, history = kept.first, kept.history
        else:
            first, history = _read_history(db, period)
            layout = _layout(period, period.start or first)
            with _kept_lock:
                _kept.pop(key, None)
                while len(_kept) >= _KEEP_LIMIT:
                    del _kept[next(iter(_kept))]
                _kept[key] = _Kept(mark, period.start, period.end, first, layout, history)
    result = {
        "tracking_started_at": started,
        "range": period.payload(first),
        **copy.deepcopy(history),
    }
    # Styles follow the configured profiles, which can change while the ledger doesn't.
    _style_accounts(result["series"], root)
    return result


def account_name(provider: str, profile: str | None) -> str:
    """An account's name as Settings > Usage shows it: its profile, or the default sign-in."""
    if profile:
        return profile
    return "Default sign-in" if provider == "claude_code" else "Default account"


def _account_key(provider: str, profile: str | None) -> str:
    return f"{provider}:{profile or ''}"


def _style_accounts(series: dict[str, Any], root: Path) -> None:
    """Give each account its shade and pattern, the same in every period.

    A provider's default sign-in is account 0. Its configured profiles follow by name,
    then profiles only the ledger still knows (since removed), by name.
    """
    accounts = series.get("accounts") or []
    if not accounts:
        return
    configured: dict[str, list[str]] = {}
    try:
        from gofer.core.provider_profiles import load_provider_profiles

        for name, profile in load_provider_profiles(root).items():
            configured.setdefault(str(profile.subscription), []).append(name)
    except Exception:  # A broken profiles file must not hide the chart.
        configured = {}
    places: dict[str, dict[str, int]] = {}
    for provider in {entry["provider"] for entry in accounts}:
        known = sorted(configured.get(provider, []), key=lambda name: (name.casefold(), name))
        former = sorted(
            {
                entry["profile"]
                for entry in accounts
                if entry["provider"] == provider
                and entry["profile"]
                and entry["profile"] not in known
            },
            key=lambda name: (name.casefold(), name),
        )
        places[provider] = {name: place for place, name in enumerate([*known, *former], 1)}
    for entry in accounts:
        place = places[entry["provider"]].get(entry["profile"], 0) if entry["profile"] else 0
        entry["style"] = account_style(entry["provider"], place)


def _still_valid(
    db: sqlite3.Connection, kept: _Kept, mark: tuple[int, int], period: UsageRange
) -> bool:
    """True when the kept history is exactly what reading ``period`` would give now."""
    if kept.mark != mark or period.end < kept.end:
        return False
    gaps = [(kept.end, period.end)]
    if (kept.start is None) != (period.start is None):
        return False
    if kept.start is not None and period.start is not None:
        if period.start < kept.start:
            return False
        gaps.append((kept.start, period.start))
    # A call inside a gap would have left or joined the period since it was read.
    for begin, finish in gaps:
        if begin >= finish:
            continue
        found = db.execute(
            "SELECT 1 FROM usage_invocations WHERE observed_at >= ? AND observed_at < ? LIMIT 1",
            (begin.isoformat(), finish.isoformat()),
        )
        if found.fetchone():
            return False
    return _layout(period, period.start or kept.first) == kept.layout


def _read_history(
    db: sqlite3.Connection, period: UsageRange
) -> tuple[datetime | None, dict[str, Any]]:
    """The period's first call and its activity and series, from grouped rows when exact."""
    where, arguments = _range_filter(period)
    oldest = db.execute(f"SELECT MIN(observed_at) FROM usage_invocations{where}", arguments)
    first = stored_instant(oldest.fetchone()[0])
    split = _split_hours(period, period.start or first)
    grouped = _grouped_history(db, period, split) if split is not None else None
    return grouped if grouped is not None else _per_row_history(db, period)


def _split_hours(period: UsageRange, start: datetime | None) -> frozenset[str] | None:
    """The UTC hours to count in quarter hours so every chart bucket's edges stay exact.

    Local midnights fall on whole UTC hours in most zones, so none is split. In zones a
    half or quarter hour off (India, Nepal, parts of Australia) the hour holding each
    midnight is split. None when an edge isn't on a quarter hour: read row by row.
    """
    skeleton = _skeleton(period, start)
    if skeleton is None or skeleton[0] == "hour":
        return frozenset()
    split = set()
    for entry in skeleton[1]:
        edge = datetime.fromisoformat(entry["start"]).astimezone(UTC)
        if edge.second or edge.microsecond or edge.minute % 15:
            return None
        if edge.minute:
            split.add(edge.strftime("%Y-%m-%dT%H"))
    return frozenset(split)


def _per_row_history(
    db: sqlite3.Connection, period: UsageRange
) -> tuple[datetime | None, dict[str, Any]]:
    """Every call in the period read one by one. Exact for any zone and any stored time."""
    where, arguments = _range_filter(period)
    rows = db.execute(
        "SELECT provider, profile, observed_at, tokens, estimated, partial FROM "
        f"usage_invocations{where} ORDER BY observed_at",
        arguments,
    ).fetchall()
    first = stored_instant(rows[0]["observed_at"]) if rows else None
    return first, {
        "activity": summarize_calls(rows),
        "series": _series(rows, period, period.start or first),
    }


def _grouped_history(
    db: sqlite3.Connection, period: UsageRange, split: frozenset[str]
) -> tuple[datetime | None, dict[str, Any]] | None:
    """Activity and series from calls counted and summed per UTC hour (or quarter hour).

    None when a group would still straddle two chart buckets; the caller then reads the
    period row by row.
    """
    rows = _grouped_rows(db, period, split)
    ties = _Ties(db)
    first_at = min((row[_FIRST_AT] for row in rows), default=None)
    first = stored_instant(first_at)
    series = _grouped_series(rows, period, period.start or first, split, ties)
    if series is None:
        return None
    return first, {"activity": _grouped_activity(rows, ties), "series": series}


def _grouped_query(
    period: UsageRange,
    split: frozenset[str],
    *,
    scan: bool = False,
    after: str | None = None,
    before: str | None = None,
) -> tuple[str, list[Any]]:
    """One query that counts the period's calls and sums their token fields per UTC hour
    (or quarter hour), provider, profile, estimated and partial.

    ``scan`` reads the table rather than the time index; ``after`` and ``before`` narrow
    the read to one slice of the period.
    """
    utc = " OR ".join(f"observed_at GLOB '{text}'" for text in _UTC_TEXT)
    # Split hours are this code's own "YYYY-MM-DDTHH" text, safe to write into the query.
    minute = (
        f"CASE WHEN hour IN ({', '.join(repr(hour) for hour in sorted(split))}) "
        "THEN CAST(substr(observed_at, 15, 2) AS INTEGER) / 15 * 15 ELSE 0 END"
        if split
        else "0"
    )
    # A call's total counts in the chart when it isn't estimated and is a whole number.
    # ledger_tokens stores only whole, non-negative counts, never true, false or null.
    known = "estimated = 0 AND typeof(total_tokens) = 'integer'"
    where, arguments = _range_filter(period)
    if after is not None:
        where += " AND observed_at >= ?"
        arguments.append(after)
    if before is not None:
        where += " AND observed_at < ?"
        arguments.append(before)
    # The inner read takes each call's token fields once; LIMIT -1 keeps SQLite from
    # inlining it, which would read the JSON again for every sum. A token field no call
    # reported sums to NULL, so it stays None rather than 0.
    inner = (
        f"SELECT CASE WHEN {utc} THEN substr(observed_at, 1, 13) END AS hour, observed_at, "
        "provider, profile, estimated, partial, tokens <> '{}' AS filled, "
        + ", ".join(f"json_extract(tokens, '$.{name}') AS {name}" for name in TOKEN_FIELDS)
        + f" FROM usage_invocations{' NOT INDEXED' if scan else ''}{where} LIMIT -1"
    )
    query = (
        f"SELECT hour, CASE WHEN hour IS NULL THEN 0 ELSE {minute} END AS minute, "
        "CASE WHEN hour IS NULL THEN observed_at END AS stamp, "
        "provider, profile, estimated, partial, COUNT(*) AS calls, "
        "SUM(input_tokens IS NOT NULL AND output_tokens IS NOT NULL) AS complete, "
        "SUM(filled) AS filled, "
        f"SUM(CASE WHEN {known} THEN 1 ELSE 0 END) AS known_calls, "
        f"SUM(CASE WHEN {known} THEN total_tokens END) AS known_total, "
        + ", ".join(f"SUM({name}) AS {name}" for name in TOKEN_FIELDS)
        + ", MIN(observed_at) AS first_at, "
        f"MIN(CASE WHEN {known} THEN observed_at END) AS known_at "
        f"FROM ({inner}) GROUP BY {', '.join(_GROUP_KEYS)}"
    )
    return query, arguments


def _grouped_rows(db: sqlite3.Connection, period: UsageRange, split: frozenset[str]) -> list[Any]:
    """The grouped query's rows, read in a few slices at once for long periods."""
    where, arguments = _range_filter(period)
    count = db.execute(f"SELECT COUNT(*) FROM usage_invocations{where}", arguments).fetchone()[0]
    newest = db.execute("SELECT MAX(rowid) FROM usage_invocations").fetchone()[0] or 0
    # Reading most of the ledger, a table scan beats the time index.
    scan = count * 2 >= newest
    slices = max(1, min(_MAX_SLICES, os.cpu_count() or 1, count // _SLICE_ROWS))
    if slices == 1:
        query, arguments = _grouped_query(period, split, scan=scan)
        cursor = db.cursor()
        cursor.row_factory = None
        return _merge_groups([cursor.execute(query, arguments).fetchall()])
    # Many calls: read the period in time slices that start on whole UTC hours, each on
    # its own connection and thread. SQLite runs a query without holding the GIL, and each
    # slice returns its groups as one JSON text, so the threads don't trade the GIL per row.
    cuts: list[str | None] = [None]
    for part in range(1, slices):
        cut = db.execute(
            f"SELECT observed_at FROM usage_invocations{where} "
            "ORDER BY observed_at LIMIT 1 OFFSET ?",
            [*arguments, count * part // slices],
        ).fetchone()[0][:13]
        if cut != cuts[-1]:
            cuts.append(cut)
    cuts.append(None)
    path = db.execute("PRAGMA database_list").fetchone()[2]
    columns = ", ".join(_GROUP_COLUMNS)

    def read(bounds: tuple[str | None, str | None]) -> list[list[Any]]:
        query, arguments = _grouped_query(
            period, split, scan=scan, after=bounds[0], before=bounds[1]
        )
        reader = sqlite3.connect(path, timeout=10)
        try:
            found = reader.execute(
                f"SELECT json_group_array(json_array({columns})) FROM ({query})", arguments
            ).fetchone()[0]
        finally:
            reader.close()
        groups: list[list[Any]] = json.loads(found)
        return groups

    with ThreadPoolExecutor(len(cuts) - 1) as pool:
        return _merge_groups(list(pool.map(read, zip(cuts, cuts[1:], strict=False))))


_GROUP_KEYS = ("hour", "minute", "stamp", "provider", "profile", "estimated", "partial")
_GROUP_COLUMNS = (
    *_GROUP_KEYS,
    "calls",
    "complete",
    "filled",
    "known_calls",
    "known_total",
    *TOKEN_FIELDS,
    "first_at",
    "known_at",
)
# Column positions in a grouped row, which stays a plain tuple or list for speed.
_COLUMN = {name: position for position, name in enumerate(_GROUP_COLUMNS)}
_HOUR, _MINUTE, _STAMP, _PROVIDER, _PROFILE, _ESTIMATED, _PARTIAL = range(len(_GROUP_KEYS))
_CALLS, _COMPLETE, _FILLED = _COLUMN["calls"], _COLUMN["complete"], _COLUMN["filled"]
_KNOWN_CALLS, _KNOWN_TOTAL = _COLUMN["known_calls"], _COLUMN["known_total"]
_FIRST_AT, _KNOWN_AT = _COLUMN["first_at"], _COLUMN["known_at"]
_FIELDS = tuple(_COLUMN[name] for name in TOKEN_FIELDS)


def _merge_groups(parts: list[list[Any]]) -> list[Any]:
    """Every slice's groups. No group spans two slices: slices start on a whole UTC hour,
    and a call stored in another form is grouped by its own exact text."""
    return [row for part in parts for row in part]


class _Ties:
    """Breaks ties between calls recorded at the same instant, in the per-row read's order."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self.found: dict[str, list[Any]] = {}

    def rows(self, stamp: str) -> list[Any]:
        if stamp not in self.found:
            self.found[stamp] = self.db.execute(
                "SELECT provider, profile, tokens, estimated FROM usage_invocations "
                "WHERE observed_at = ? ORDER BY rowid",
                (stamp,),
            ).fetchall()
        return self.found[stamp]

    def order(self, firsts: dict[Any, str], matches: Any) -> list[Any]:
        """``firsts``' keys by their first time, ties in the order their calls were read."""
        counts: dict[str, int] = {}
        for stamp in firsts.values():
            counts[stamp] = counts.get(stamp, 0) + 1

        def rank(item: Any) -> tuple[str, int]:
            stamp = firsts[item]
            if counts[stamp] == 1:
                return stamp, 0
            for position, row in enumerate(self.rows(stamp)):
                if matches(item, row):
                    return stamp, position
            return stamp, 0

        return sorted(firsts, key=rank)


def _grouped_activity(rows: list[Any], ties: _Ties) -> dict[str, Any]:
    """summarize_calls' result, from grouped rows."""
    # Calls per account, estimated and partial first: [calls, complete, filled, *fields].
    sums: dict[tuple[Any, ...], list[Any]] = {}
    firsts: dict[tuple[str, str | None], str] = {}
    for row in rows:
        account = (row[_PROVIDER], row[_PROFILE])
        kind = (*account, bool(row[_ESTIMATED]), bool(row[_PARTIAL]))
        found = sums.get(kind)
        if found is None:
            found = sums[kind] = [0, 0, 0, *(None for _ in _FIELDS)]
        found[0] += row[_CALLS]
        found[1] += row[_COMPLETE]
        found[2] += row[_FILLED]
        for slot, position in enumerate(_FIELDS, 3):
            value = row[position]
            if value is not None:
                found[slot] = value if found[slot] is None else found[slot] + value
        first = firsts.get(account)
        if first is None or row[_FIRST_AT] < first:
            firsts[account] = row[_FIRST_AT]

    def empty() -> dict[str, Any]:
        return {
            "calls": 0,
            "reported_calls": 0,
            "unknown_calls": 0,
            "partial_calls": 0,
            "estimated_calls": 0,
            **dict.fromkeys(TOKEN_FIELDS),
            "estimated_total_tokens": None,
        }

    total = empty()
    groups = {
        account: {"provider": account[0], "profile": account[1], **empty()} for account in firsts
    }
    reported_total = 3 + TOKEN_FIELDS.index("total_tokens")
    for (provider, profile, estimated, partial), found in sums.items():
        calls, complete, filled = found[0], found[1], found[2]
        for target in (groups[(provider, profile)], total):
            target["calls"] += calls
            if estimated:
                target["estimated_calls"] += calls
                if found[reported_total] is not None:
                    target["estimated_total_tokens"] = (
                        target["estimated_total_tokens"] or 0
                    ) + found[reported_total]
                continue
            reported = 0 if partial else complete
            target["reported_calls"] += reported
            target["partial_calls"] += filled - reported
            target["unknown_calls"] += calls - filled
            for slot, name in enumerate(TOKEN_FIELDS, 3):
                if found[slot] is not None:
                    target[name] = (target[name] or 0) + found[slot]
    order = ties.order(firsts, lambda key, call: (call["provider"], call["profile"]) == key)
    return {**total, "providers": [groups[key] for key in order]}


def _grouped_series(
    rows: list[Any],
    period: UsageRange,
    start: datetime | None,
    split: frozenset[str],
    ties: _Ties,
) -> dict[str, Any] | None:
    """_series' result from grouped rows, or None when a group spans two buckets."""
    skeleton = _skeleton(period, start)
    if skeleton is None:
        return _empty_series(period)
    bucket, buckets, index = skeleton
    zone = period.zone
    whole, quarter = (timedelta(minutes=size) - timedelta(microseconds=1) for size in (60, 15))
    keys: dict[tuple[str, int], str] = {}
    totals: dict[str, int] = {}
    seen: dict[str, int] = {}
    account_totals: dict[tuple[str, str], int] = {}
    account_seen: dict[tuple[str, str], int] = {}
    # Per bucket, each provider's reported tokens and the first time it reported any.
    stacks: dict[str, dict[str, list[Any]]] = {}
    for row in rows:
        hour = row[_HOUR]
        if hour is None:
            observed = stored_instant(row[_STAMP])
            if observed is None:
                continue
            key = _row_key(observed, bucket, zone)
        else:
            slot = (hour, row[_MINUTE])
            found_key = keys.get(slot)
            if found_key is None:
                try:
                    begin = datetime(
                        int(hour[:4]),
                        int(hour[5:7]),
                        int(hour[8:10]),
                        int(hour[11:13]),
                        row[_MINUTE],
                        tzinfo=UTC,
                    )
                except ValueError:
                    return None  # Not a real time: the per-row read decides what it means.
                found_key = _row_key(begin, bucket, zone)
                if bucket != "hour":
                    # Every call in the group must land in the same bucket.
                    last = begin + (quarter if hour in split else whole)
                    if not _one_bucket(begin, last, found_key, bucket, zone):
                        return None
                keys[slot] = found_key
            key = found_key
        entry = index.get(key)
        if entry is None:
            continue
        provider = str(row[_PROVIDER] or "unknown")
        account = (provider, str(row[_PROFILE] or ""))
        calls = row[_CALLS]
        seen[provider] = seen.get(provider, 0) + calls
        account_seen[account] = account_seen.get(account, 0) + calls
        entry["calls"] += calls
        known = 0 if row[_ESTIMATED] else row[_KNOWN_CALLS]
        entry["unknown_calls"] += calls - known
        if not known:
            continue
        reported = row[_KNOWN_TOTAL]
        entry["total_tokens"] += reported
        totals[provider] = totals.get(provider, 0) + reported
        account_totals[account] = account_totals.get(account, 0) + reported
        held = entry["accounts"]
        held[account] = held.get(account, 0) + reported
        stack = stacks.setdefault(key, {})
        part = stack.get(provider)
        if part is None:
            stack[provider] = [reported, row[_KNOWN_AT]]
        else:
            part[0] += reported
            part[1] = min(part[1], row[_KNOWN_AT])

    def reports(provider: str, call: Any) -> bool:
        if call["estimated"] or str(call["provider"] or "unknown") != provider:
            return False
        value = json.loads(call["tokens"]).get("total_tokens")
        return isinstance(value, int) and not isinstance(value, bool)

    for key, stack in stacks.items():
        order = ties.order({name: part[1] for name, part in stack.items()}, reports)
        index[key]["providers"] = {name: stack[name][0] for name in order}
    return _series_payload(period, bucket, buckets, seen, totals, account_seen, account_totals)


def _one_bucket(begin: datetime, last: datetime, key: str, bucket: str, zone: tzinfo) -> bool:
    """Whether every instant from ``begin`` to ``last`` falls in the bucket ``key``.

    The local calendar only moves forward between clock changes, so equal ends prove it.
    Across a clock change (a rare hour), every minute is checked as well.
    """
    if _row_key(last, bucket, zone) != key:
        return False
    if begin.astimezone(zone).utcoffset() == last.astimezone(zone).utcoffset():
        return True
    minute = begin
    while minute <= last:
        if _row_key(minute, bucket, zone) != key:
            return False
        minute += timedelta(minutes=1)
    return True


def _row_key(observed: datetime, bucket: str, zone: tzinfo) -> str:
    """The key of the bucket holding the instant ``observed``."""
    if bucket == "hour":
        return _key(observed.astimezone(UTC), bucket)
    return _key(_floor(observed.astimezone(zone).replace(tzinfo=None), bucket), bucket)


def _empty_series(period: UsageRange) -> dict[str, Any]:
    return {
        "bucket": "day",
        "timezone": period.zone_name,
        "providers": [],
        "accounts": [],
        "buckets": [],
    }


def _skeleton(
    period: UsageRange, start: datetime | None
) -> tuple[str, list[dict[str, Any]], dict[str, dict[str, Any]]] | None:
    """The period's empty buckets, or None when it has none."""
    zone = period.zone
    if start is None or start >= period.end:
        return None
    bucket = bucket_for(period.end - start)
    hourly = bucket == "hour"
    # Hours step in UTC so a daylight-saving change never repeats or drops one.
    if hourly:
        first = start.replace(minute=0, second=0, microsecond=0)
    else:
        first = _floor(start.astimezone(zone).replace(tzinfo=None), bucket)
    buckets: list[dict[str, Any]] = []
    index: dict[str, dict[str, Any]] = {}
    cursor = first
    while True:
        if hourly:
            begin_utc = cursor
            if begin_utc >= period.end:
                break
            follow = cursor + timedelta(hours=1)
            key = _key(cursor, bucket)
            begin_text, end_text = (
                cursor.astimezone(zone).isoformat(),
                follow.astimezone(zone).isoformat(),
            )
        else:
            begin_utc = cursor.replace(tzinfo=zone).astimezone(UTC)
            if begin_utc >= period.end:
                break
            follow = _next(cursor, bucket)
            key = _key(cursor, bucket)
            begin_text, end_text = _stamp(cursor, zone, bucket), _stamp(follow, zone, bucket)
        entry: dict[str, Any] = {
            "key": key,
            "start": begin_text,
            "end": end_text,
            "calls": 0,
            "unknown_calls": 0,
            "total_tokens": 0,
            "providers": {},
            "accounts": {},
        }
        buckets.append(entry)
        index[key] = entry
        cursor = follow
        if len(buckets) > 400:  # A safety stop; bucket_for keeps real periods far below.
            break
    return bucket, buckets, index


def _layout(period: UsageRange, start: datetime | None) -> tuple[str, ...]:
    """The bucket size and every bucket's key and bounds: what a kept series depends on."""
    skeleton = _skeleton(period, start)
    if skeleton is None:
        return ()
    bucket, buckets, _ = skeleton
    return (bucket, *(f"{b['key']} {b['start']} {b['end']}" for b in buckets))


def _series_payload(
    period: UsageRange,
    bucket: str,
    buckets: list[dict[str, Any]],
    seen: dict[str, int],
    totals: dict[str, int],
    account_seen: dict[tuple[str, str], int],
    account_totals: dict[tuple[str, str], int],
) -> dict[str, Any]:
    providers = sorted(seen, key=lambda name: (-totals.get(name, 0), name))
    rank = {name: place for place, name in enumerate(providers)}
    # A provider's accounts sit together, in the providers' order, largest first.
    accounts = sorted(
        account_seen,
        key=lambda account: (
            rank[account[0]],
            -account_totals.get(account, 0),
            bool(account[1]),
            account[1],
        ),
    )
    keys = {account: _account_key(*account) for account in accounts}
    for entry in buckets:
        held = entry["accounts"]
        entry["accounts"] = {
            keys[account]: held[account] for account in accounts if account in held
        }
    return {
        "bucket": bucket,
        "timezone": period.zone_name,
        "providers": [
            {
                "provider": name,
                "total_tokens": totals.get(name),
                "calls": seen[name],
                "style": provider_style(name),
            }
            for name in providers
        ],
        # Styles are added per request: see _style_accounts.
        "accounts": [
            {
                "key": keys[account],
                "provider": account[0],
                "profile": account[1] or None,
                "name": account_name(*account),
                "total_tokens": account_totals.get(account),
                "calls": account_seen[account],
            }
            for account in accounts
        ],
        "buckets": buckets,
    }


def _series(rows: list[Any], period: UsageRange, start: datetime | None) -> dict[str, Any]:
    skeleton = _skeleton(period, start)
    if skeleton is None:
        return _empty_series(period)
    bucket, buckets, index = skeleton
    totals: dict[str, int] = {}
    seen: dict[str, int] = {}
    account_totals: dict[tuple[str, str], int] = {}
    account_seen: dict[tuple[str, str], int] = {}
    for row in rows:
        observed = stored_instant(row["observed_at"])
        if observed is None:
            continue
        found = index.get(_row_key(observed, bucket, period.zone))
        if found is None:
            continue
        entry = found
        provider = str(row["provider"] or "unknown")
        account = (provider, str(row["profile"] or ""))
        seen[provider] = seen.get(provider, 0) + 1
        account_seen[account] = account_seen.get(account, 0) + 1
        entry["calls"] += 1
        tokens = {} if row["estimated"] else json.loads(row["tokens"])
        total = tokens.get("total_tokens")
        if not isinstance(total, int) or isinstance(total, bool):
            entry["unknown_calls"] += 1
            continue
        entry["total_tokens"] += total
        entry["providers"][provider] = entry["providers"].get(provider, 0) + total
        totals[provider] = totals.get(provider, 0) + total
        account_totals[account] = account_totals.get(account, 0) + total
        entry["accounts"][account] = entry["accounts"].get(account, 0) + total
    return _series_payload(period, bucket, buckets, seen, totals, account_seen, account_totals)
