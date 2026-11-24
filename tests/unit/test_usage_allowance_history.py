"""Allowance history: change-only rows, the weekly burndown and its pace."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.cli.commands.system import _usage_text
from gofer.core.usage_allowance_history import (
    allowance_history,
    pace,
    record_allowance_readings,
    split_windows,
    weekly_burndown,
    with_weekly_burndown,
)
from gofer.core.usage_ledger import record_invocation, usage_db
from gofer.core.usage_service import refresh_usage, usage_overview
from gofer.devices.desktop_control import _weekly_burndown

# Tuesday. The window resets on Saturday at noon.
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
RESET = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def _weekly(
    used: float, resets: datetime | None = RESET, window_id: str = "seven_day"
) -> dict[str, Any]:
    return {
        "id": window_id,
        "label": "Weekly allowance",
        "unit": "percent",
        "used": used,
        "limit": 100.0,
        "remaining_percent": 100.0 - used,
        "resets_at": resets.isoformat() if resets else None,
    }


def _record(tmp_path: Path, at: datetime, *windows: dict[str, Any], profile: str = "") -> int:
    with usage_db(tmp_path) as db:
        return record_allowance_readings(db, "claude_code", profile, windows, at.isoformat())


def _reading(at: datetime, used: float, resets: datetime | None = RESET) -> dict[str, Any]:
    return {
        "observed_at": at.isoformat(),
        "used_percent": used,
        "resets_at": resets.isoformat() if resets else "",
    }


# Writing only changed readings ------------------------------------------------------


def test_unchanged_readings_are_skipped(tmp_path: Path) -> None:
    assert _record(tmp_path, NOW, _weekly(40)) == 1
    # Same percent and reset, later: the history already has it.
    assert _record(tmp_path, NOW + timedelta(minutes=10), _weekly(40)) == 0
    # A reset time reported a few seconds apart is the same window.
    jitter = RESET + timedelta(seconds=7)
    assert _record(tmp_path, NOW + timedelta(minutes=20), _weekly(40, jitter)) == 0
    # A new percent is a new row.
    assert _record(tmp_path, NOW + timedelta(minutes=30), _weekly(41)) == 1
    # A reading older than the newest one never goes in, even when it differs.
    assert _record(tmp_path, NOW + timedelta(minutes=5), _weekly(39)) == 0
    rows = allowance_history(tmp_path)
    assert [(r["used_percent"], r["window_id"], r["profile"]) for r in rows] == [
        (40.0, "seven_day", ""),
        (41.0, "seven_day", ""),
    ]
    assert rows[0]["resets_at"] == RESET.isoformat()


def test_each_window_and_account_keeps_its_own_history(tmp_path: Path) -> None:
    five_hour = {**_weekly(10, NOW + timedelta(hours=3)), "id": "five_hour"}
    assert _record(tmp_path, NOW, _weekly(40), five_hour) == 2
    assert _record(tmp_path, NOW, _weekly(40), profile="profile:work:abc") == 1
    later = NOW + timedelta(minutes=5)
    # Only the 5-hour window moved.
    assert _record(tmp_path, later, _weekly(40), {**five_hour, "used": 12}) == 1
    assert len(allowance_history(tmp_path, profile="profile:work:abc")) == 1
    assert len(allowance_history(tmp_path, profile="", window_id="five_hour")) == 2


def test_readings_without_a_percent_or_past_their_reset_are_skipped(tmp_path: Path) -> None:
    credits = {"id": "codex:credits", "unit": "credits", "remaining": 30}
    no_id = {"label": "Weekly allowance", "unit": "percent", "used": 20}
    past = _weekly(50, NOW - timedelta(minutes=1))
    assert _record(tmp_path, NOW, credits, no_id, past) == 0
    with usage_db(tmp_path) as db:
        assert record_allowance_readings(db, "claude_code", "", [_weekly(5)], "soon") == 0
    assert allowance_history(tmp_path) == []


def test_turns_write_history_only_when_the_reading_changes(tmp_path: Path) -> None:
    def turn(invocation: str, used: float) -> None:
        record_invocation(
            invocation_id=invocation,
            provider="claude_code",
            model="claude-opus-5-5",
            data_dir=tmp_path,
            metadata={"quota_windows": [_weekly(used, datetime.now(UTC) + timedelta(days=3))]},
        )

    turn("a", 30)
    turn("b", 30)
    turn("c", 31)
    assert [r["used_percent"] for r in allowance_history(tmp_path)] == [30.0, 31.0]
    # The latest-reading table still holds one row per model, as before.
    with usage_db(tmp_path) as db:
        assert db.execute("SELECT COUNT(*) FROM usage_rate_limits").fetchone()[0] == 1


async def test_polled_readings_write_history_only_when_they_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.core import provider_usage

    resets = datetime.now(UTC) + timedelta(days=3)
    used = {"value": 20.0}

    async def adapter(provider: str, profile: str | None, **kwargs: Any) -> dict[str, Any]:
        if provider != "claude_code":
            return {"status": "unavailable", "windows": []}
        return {
            "provider": provider,
            "status": "available",
            "observed_at": datetime.now(UTC).isoformat(),
            "windows": [_weekly(used["value"], resets)],
        }

    monkeypatch.setattr(provider_usage, "refresh_provider_usage", adapter)
    for value in (20.0, 20.0, 23.0):
        used["value"] = value
        await refresh_usage(tmp_path, min_age=0)
    assert [r["used_percent"] for r in allowance_history(tmp_path)] == [20.0, 23.0]

    async def failing(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise TimeoutError

    # A failed refresh keeps the old reading as "stale"; that isn't a new reading.
    monkeypatch.setattr(provider_usage, "refresh_provider_usage", failing)
    await refresh_usage(tmp_path, min_age=0)
    assert len(allowance_history(tmp_path)) == 2


# Pace ------------------------------------------------------------------------------


def test_pace_needs_two_readings() -> None:
    assert pace([], RESET.isoformat())["text"] == "Not enough readings yet"
    one = pace([_reading(NOW, 40)], RESET.isoformat(), checked_at=NOW.isoformat())
    assert one == {"state": "not_enough", "runs_out_at": None, "text": "Not enough readings yet"}
    # Two readings without a known reset time can't be judged either.
    assert pace([_reading(NOW, 1), _reading(NOW, 2)], "")["state"] == "not_enough"


def test_pace_on_track_lasts_until_reset() -> None:
    readings = [_reading(NOW - timedelta(days=1), 30), _reading(NOW, 40)]
    # 10% a day leaves 60% for 4 days: it lasts.
    result = pace(readings, RESET.isoformat(), checked_at=NOW.isoformat(), zone=UTC)
    assert result == {
        "state": "on_pace",
        "runs_out_at": None,
        "text": "On pace to last until reset",
    }


def test_pace_names_the_day_and_hour_it_runs_out() -> None:
    readings = [_reading(NOW - timedelta(hours=12), 40), _reading(NOW, 60)]
    # 20% per 12 hours leaves 40% for 24 hours: Wednesday noon, before Saturday's reset.
    result = pace(readings, RESET.isoformat(), zone=UTC)
    assert result["state"] == "runs_out"
    assert result["runs_out_at"] == datetime(2026, 11, 18, 12, 0, tzinfo=UTC).isoformat()
    assert result["text"] == "At this pace, runs out around Wed 12:00"
    # 1% an hour leaves 50% for 50 hours.
    readings = [_reading(NOW - timedelta(hours=8), 40), _reading(NOW + timedelta(hours=2), 50)]
    later = pace(readings, RESET.isoformat(), zone=UTC)
    assert later["text"] == "At this pace, runs out around Thu 16:00"


def test_pace_counts_time_since_the_last_change_as_checked() -> None:
    readings = [_reading(NOW - timedelta(hours=12), 40), _reading(NOW, 60)]
    # The provider confirmed 60% for another 36 hours: 20% in 48 hours lasts to the reset.
    checked = (NOW + timedelta(hours=36)).isoformat()
    assert pace(readings, RESET.isoformat(), checked_at=checked)["state"] == "on_pace"


def test_pace_when_used_up() -> None:
    readings = [_reading(NOW - timedelta(hours=1), 90), _reading(NOW, 100)]
    assert pace(readings, RESET.isoformat())["text"] == "Used up until reset"


# Windows that reset in the middle of the history -----------------------------------


def test_split_windows_cuts_at_each_reset() -> None:
    previous = RESET - timedelta(days=7)
    readings = [
        _reading(previous - timedelta(days=2), 60, previous),
        _reading(previous - timedelta(days=1), 95, previous),
        _reading(previous + timedelta(hours=1), 2),
        _reading(previous + timedelta(hours=5), 9),
    ]
    assert [len(part) for part in split_windows(readings)] == [2, 2]
    # Without reset times, a fall in the percent used marks the reset.
    bare = [_reading(NOW, 70, None), _reading(NOW, 80, None), _reading(NOW, 3, None)]
    assert [len(part) for part in split_windows(bare)] == [2, 1]


def test_burndown_only_uses_the_current_window_after_a_reset() -> None:
    previous = RESET - timedelta(days=7)
    readings = [
        _reading(previous - timedelta(days=3), 50, previous),
        _reading(previous - timedelta(hours=1), 98, previous),
        _reading(NOW - timedelta(hours=20), 10),
    ]
    account = {"provider": "claude_code", "observed_at": NOW.isoformat(), "windows": [_weekly(10)]}
    chart = weekly_burndown(account, readings, now=NOW, zone=UTC)
    assert chart is not None
    # The 98% from last week doesn't make this week look like it's running out.
    assert chart["reading_count"] == 1
    assert chart["pace"]["text"] == "Not enough readings yet"
    assert chart["starts_at"] == previous.isoformat()
    assert chart["resets_at"] == RESET.isoformat()
    readings.append(_reading(NOW, 12))
    chart = weekly_burndown(account, readings, now=NOW, zone=UTC)
    assert chart is not None
    assert [p["used_percent"] for p in chart["readings"]] == [10.0, 12.0]
    assert chart["pace"]["state"] == "on_pace"


def test_burndown_needs_a_weekly_window_with_a_reset_ahead() -> None:
    five_hour = {**_weekly(10, NOW + timedelta(hours=2)), "id": "five_hour", "label": "Five-hour"}
    assert weekly_burndown({"windows": [five_hour]}, [], now=NOW) is None
    assert weekly_burndown({"windows": [_weekly(10, None)]}, [], now=NOW) is None
    assert (
        weekly_burndown({"windows": [_weekly(10, NOW - timedelta(minutes=1))]}, [], now=NOW) is None
    )
    # Codex names its weekly window by its length.
    codex = {**_weekly(10), "id": "codex:secondary", "label": "codex · secondary · 7-day"}
    chart = weekly_burndown({"provider": "codex", "windows": [codex]}, [], now=NOW)
    assert chart is not None and chart["window_id"] == "codex:secondary"


# The usage API, the phone and the CLI ----------------------------------------------


def test_overview_carries_each_accounts_weekly_burndown(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    resets = now + timedelta(days=2)

    def turn(invocation: str, used: float) -> None:
        record_invocation(
            invocation_id=invocation,
            provider="claude_code",
            model="claude-opus-5-5",
            data_dir=tmp_path,
            metadata={"quota_windows": [_weekly(used, resets)]},
        )

    turn("first", 20)
    with usage_db(tmp_path) as db:
        db.execute(
            "UPDATE usage_allowance_history SET observed_at=?",
            ((now - timedelta(hours=6)).isoformat(),),
        )
    turn("second", 26)
    row = next(r for r in usage_overview(tmp_path)["accounts"] if r["provider"] == "claude_code")
    chart = row["weekly_burndown"]
    assert [p["used_percent"] for p in chart["readings"]] == [20.0, 26.0]
    # 6% in 6 hours leaves 74% for 74 hours, past the reset in 48.
    assert chart["pace"]["text"] == "On pace to last until reset"
    others = [r for r in usage_overview(tmp_path)["accounts"] if r["provider"] != "claude_code"]
    assert all("weekly_burndown" not in r for r in others)

    phone = _weekly_burndown(chart)
    assert phone["reading_count"] == 2
    assert phone["pace"]["state"] == "on_pace"
    assert [p["used_percent"] for p in phone["readings"]] == [20.0, 26.0]

    text = _usage_text({"accounts": [row]})
    assert "Weekly allowance pace" in text
    assert "20% → 26%" in text
    assert "On pace to last until reset" in text


def test_with_weekly_burndown_reads_only_this_accounts_history(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    resets = now + timedelta(days=2)
    _record(tmp_path, now - timedelta(hours=2), _weekly(5, resets), profile="profile:work:1")
    _record(tmp_path, now - timedelta(hours=1), _weekly(50, resets))
    _record(tmp_path, now, _weekly(60, resets))
    default = {
        "id": "claude_code",
        "provider": "claude_code",
        "profile": None,
        "windows": [_weekly(60, resets)],
    }
    work = {
        "id": "profile:work:1",
        "provider": "claude_code",
        "profile": "work",
        "windows": [_weekly(5, resets)],
    }
    rows = with_weekly_burndown([default, work], tmp_path, now=now)
    assert rows[0]["weekly_burndown"]["reading_count"] == 2
    assert rows[1]["weekly_burndown"]["reading_count"] == 1


def test_phone_chart_is_bounded() -> None:
    readings = [
        {"observed_at": (NOW + timedelta(minutes=i)).isoformat(), "used_percent": i / 10}
        for i in range(500)
    ]
    phone = _weekly_burndown({"readings": readings, "reading_count": 500, "pace": {}})
    assert len(phone["readings"]) == 120
    assert phone["readings"][0] == readings[0] and phone["readings"][-1] == readings[-1]
    assert phone["pace"] == {"state": "", "runs_out_at": "", "text": ""}
