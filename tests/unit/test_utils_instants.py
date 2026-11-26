"""stored_instant reads back the times Raticode saved, for every module that stores them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from gofer.utils.instants import stored_instant

NOON = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def test_z_suffix_is_utc() -> None:
    assert stored_instant("2026-11-24T12:00:00Z") == NOON
    assert stored_instant("2026-11-24T12:00:00.250Z") == NOON + timedelta(milliseconds=250)


def test_offset_is_kept() -> None:
    parsed = stored_instant("2026-11-24T07:00:00-05:00")
    assert parsed == NOON
    assert parsed is not None and parsed.utcoffset() == timedelta(hours=-5)


@pytest.mark.parametrize("naive_as_utc", [True, False])
def test_naive_time(naive_as_utc: bool) -> None:
    for value in ("2026-11-24T12:00:00", datetime(2026, 11, 24, 12, 0)):
        expected = NOON if naive_as_utc else None
        assert stored_instant(value, naive_as_utc=naive_as_utc) == expected


@pytest.mark.parametrize("value", [None, "", 0, False])
def test_empty_is_none(value: object) -> None:
    assert stored_instant(value) is None
    assert stored_instant(value, naive_as_utc=False) is None


def test_datetime_object() -> None:
    eastern = datetime(2026, 11, 24, 7, 0, tzinfo=ZoneInfo("America/New_York"))
    assert stored_instant(eastern) is eastern
    fixed = datetime(2026, 11, 24, 21, 0, tzinfo=timezone(timedelta(hours=9)))
    assert stored_instant(fixed, naive_as_utc=False) == NOON


@pytest.mark.parametrize("value", ["soon", "2026-13-01T00:00:00Z", "None", 12345, ["2026"]])
def test_unparseable_is_none(value: object) -> None:
    assert stored_instant(value) is None
