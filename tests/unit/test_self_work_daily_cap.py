"""Self-work's daily cap: a share of the Claude Code weekly allowance it may use each day."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from gofer.core.usage_ledger import usage_db
from gofer.core.usage_service import latest_window_reading
from gofer.ui import rem_self_work_daily as daily
from gofer.ui import rem_self_work_windows as windows
from gofer.ui.rem_kanban import KanbanRefused
from gofer.ui.rem_self_work import SelfWork
from tests.unit.test_rem_kanban import BASE, add, at, set_options, settle, tick, ticket, wait_for
from tests.unit.test_ticket_usage_and_limits import metered as metered
from tests.unit.test_ticket_usage_and_limits import notes

NEW_YORK = ZoneInfo("America/New_York")
# The week's reset, in real time: readings whose reset has passed are ignored.
RESET = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0).isoformat()


def reading(percent: float, minutes: float, resets_at: str = RESET) -> dict[str, object]:
    return {
        "percent": percent,
        "resets_at": resets_at,
        "observed_at": (BASE + timedelta(minutes=minutes)).isoformat(),
    }


def clock(minutes: float) -> datetime:
    return BASE + timedelta(minutes=minutes)


# Counting -------------------------------------------------------------------------------


def test_only_rises_while_self_work_runs_count():
    state = daily.advance({}, reading(40, 0), False, clock(0), NEW_YORK)
    assert state["used"] == 0.0 and state["last"]["percent"] == 40
    # The user's own use while self-work runs nothing doesn't count.
    state = daily.advance(state, reading(45, 10), False, clock(10), NEW_YORK)
    assert state["used"] == 0.0
    # Self-work starts; the next reading's rise counts.
    state = daily.advance(state, reading(45, 10), True, clock(12), NEW_YORK)
    state = daily.advance(state, reading(48, 20), True, clock(20), NEW_YORK)
    assert state["used"] == 3.0
    # It finished before the next reading: that reading still counts the rise up to it.
    state = daily.advance(state, reading(48.5, 30), False, clock(30), NEW_YORK)
    assert state["used"] == 3.5
    # Then idle again: nothing more counts.
    state = daily.advance(state, reading(60, 40), False, clock(40), NEW_YORK)
    assert state["used"] == 3.5


def test_the_count_restarts_at_local_midnight_and_keeps_its_baseline():
    # 04:50 UTC is 23:50 the day before in New York; 05:10 UTC is 00:10.
    late = datetime(2026, 11, 10, 4, 50, tzinfo=UTC)
    state: dict[str, Any] = {
        "day": "2026-11-09",
        "used": 9.0,
        "last": {
            "percent": 50.0,
            "resets_at": RESET,
            "observed_at": late.isoformat(),
            "busy": True,
            "busy_until": late.isoformat(),
        },
    }
    assert daily.used_today(state, late, NEW_YORK) == 9.0
    after = late + timedelta(minutes=20)
    state = daily.advance(state, None, False, after, NEW_YORK)
    assert state["day"] == "2026-11-10" and state["used"] == 0.0
    assert state["last"]["percent"] == 50.0
    # In UTC both moments are the same day, so the count wouldn't have restarted.
    assert daily.local_day(late, ZoneInfo("UTC")) == daily.local_day(after, ZoneInfo("UTC"))
    assert daily.next_midnight(late, NEW_YORK).isoformat() == "2026-11-10T00:00:00-05:00"


def test_a_weekly_reset_counts_the_new_week_from_zero():
    later = (datetime.fromisoformat(RESET) + timedelta(days=7)).isoformat()
    state = daily.advance({}, reading(90, 0), True, clock(0), NEW_YORK)
    state = daily.advance(state, reading(2, 10, later), True, clock(10), NEW_YORK)
    assert state["used"] == 2.0


def test_a_long_gap_after_self_work_stopped_counts_only_its_share():
    # Self-work ran for 10 minutes, then the app was closed for hours while the user worked.
    state = daily.advance({}, reading(40, 0), True, clock(0), NEW_YORK)
    state = daily.advance(state, reading(40, 0), True, clock(10), NEW_YORK)
    state = daily.advance(state, reading(60, 100), False, clock(100), NEW_YORK)
    assert state["used"] == pytest.approx(2.0)


def test_the_cap_stops_new_work_and_fails_safe_without_a_reading():
    state = daily.advance({}, reading(40, 0), True, clock(0), NEW_YORK)
    state = daily.advance(state, reading(51, 10), True, clock(10), NEW_YORK)
    assert daily.limit(0, state, reading(51, 10), clock(11), NEW_YORK) is None
    assert daily.limit(12, state, reading(51, 10), clock(11), NEW_YORK) is None
    key, text = daily.limit(11, state, reading(51, 10), clock(11), NEW_YORK) or ("", "")
    assert key == "daily_allowance"
    assert "about 11% of the Claude Code weekly allowance today" in text
    assert "daily cap of 11%" in text and "Running work finishes normally." in text
    # No reading at all, or only an old one: start nothing new, and say why.
    key, text = daily.limit(11, {}, None, clock(11), NEW_YORK) or ("", "")
    assert key == "daily_allowance_unknown" and "there hasn't been one yet" in text
    key, text = daily.limit(11, state, reading(51, 10), clock(45), NEW_YORK) or ("", "")
    assert key == "daily_allowance_unknown" and "the last one is from 07:10" in text
    shown = daily.view(11, state, reading(51, 10), clock(45), NEW_YORK)
    assert shown["available"] is False and shown["used_percent"] == 11.0
    assert "No current Claude Code usage reading" in shown["message"]


def test_the_newest_stored_weekly_reading_wins(tmp_path: Path):
    old = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
    new = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    window = {"id": "seven_day", "unit": "percent", "used": 30.0, "resets_at": RESET}
    with usage_db(tmp_path) as db:
        db.execute(
            "INSERT INTO usage_snapshots VALUES (?, ?, ?)",
            ("claude_code", json.dumps({"observed_at": old, "windows": [window]}), old),
        )
        db.execute(
            "INSERT INTO usage_rate_limits VALUES (?, ?, ?, ?, ?)",
            ("claude_code", "", "opus", new, json.dumps([{**window, "used": 33.5}])),
        )
        # A reading of a window that has already reset says nothing about this week.
        db.execute(
            "INSERT INTO usage_rate_limits VALUES (?, ?, ?, ?, ?)",
            ("claude_code", "", "haiku", new, json.dumps([{**window, "resets_at": past}])),
        )
    found = latest_window_reading("claude_code", "seven_day", tmp_path)
    assert found is not None
    assert found == {
        "percent": 33.5,
        "resets_at": RESET,
        "observed_at": found["observed_at"],
        "id": "seven_day",
        "label": "",
        "starts_at": "",
    }
    assert datetime.fromisoformat(found["observed_at"]) == datetime.fromisoformat(new)
    assert latest_window_reading("claude_code", "five_hour", tmp_path) is None


def credits_reading(percent: float, minutes: float, **window: str) -> dict[str, object]:
    """A Grok credits reading as read_weekly gives it, for a 30-day period by default."""
    found = {
        **reading(percent, minutes),
        "id": "credits",
        "label": "Monthly credits",
        "starts_at": (datetime.fromisoformat(RESET) - timedelta(days=30)).isoformat(),
        **window,
    }
    return {**found, "measure": "monthly credits", "scale": daily.credits_scale(found)}


def test_a_credits_period_scales_the_weekly_cap_to_its_length():
    end = "2026-12-01T00:00:00+00:00"
    assert daily.period_days({"starts_at": "2026-11-01T00:00:00+00:00", "resets_at": end}) == 30
    # Without a start, the label's period word decides; a month is the one before the reset.
    assert daily.period_days({"label": "Monthly credits", "resets_at": end}) == 30
    march = "2026-03-01T00:00:00+00:00"
    assert daily.period_days({"label": "Monthly credits", "resets_at": march}) == 28
    assert daily.period_days({"label": "Weekly credits"}) == 7
    assert daily.period_days({"label": "Daily credits"}) == 1
    assert daily.period_days({"label": "Credits"}) == daily.DEFAULT_PERIOD_DAYS
    found = credits_reading(40, 0)
    assert found["scale"] == pytest.approx(7 / 30)
    assert daily.day_cap(10, found) == pytest.approx(70 / 30)
    assert daily.cap_text(10, found) == "2.3% (the 10% weekly cap over a 30-day period)"
    # A weekly credits period is the weekly cap as it is.
    assert daily.cap_text(10, credits_reading(40, 0, label="Weekly credits", starts_at="")) == "10%"
    assert daily.cap_text(10, reading(40, 0)) == "10%"


def test_the_cap_counts_a_credits_windows_rise_against_the_scaled_cap():
    state = daily.advance({}, credits_reading(40, 0), True, clock(0), NEW_YORK)
    state = daily.advance(state, credits_reading(42, 10), True, clock(10), NEW_YORK)
    assert state["used"] == 2.0
    # 2% of the month's credits is under 10% of a week (about 2.33% of a 30-day month).
    assert daily.limit(10, state, credits_reading(42, 10), clock(11), NEW_YORK, account="x") is None
    shown = daily.view(10, state, credits_reading(42, 10), clock(11), NEW_YORK, account="x")
    assert shown["message"] == (
        "Used today: about 2% of x's monthly credits, of a daily cap of 2.3% (the 10% weekly cap "
        "over a 30-day period). Resets at midnight."
    )
    assert shown["measure"] == "monthly credits" and shown["day_cap_percent"] == 2.33
    state = daily.advance(state, credits_reading(42.5, 20), True, clock(20), NEW_YORK)
    found = daily.limit(10, state, credits_reading(42.5, 20), clock(21), NEW_YORK, account="x")
    assert found is not None
    key, text = found
    assert key == "daily_allowance"
    assert "self-work used about 2.5% of x's monthly credits today" in text
    assert "daily cap of 2.3% (the 10% weekly cap over a 30-day period)" in text
    # An old credits reading names the credits, not a weekly allowance.
    found = daily.limit(10, state, credits_reading(42.5, 20), clock(60), NEW_YORK, account="x")
    assert found is not None
    key, text = found
    assert key == "daily_allowance_unknown" and "a reading of x's monthly credits" in text
    assert "weekly" not in text.split("Check Settings")[0]


def test_an_account_without_a_weekly_window_reads_its_credits(tmp_path: Path):
    from gofer.core.usage_ledger import profile_usage_identity

    who = profile_usage_identity("grok-home", tmp_path)
    observed = (BASE + timedelta(minutes=5)).isoformat()
    window = {
        "id": "credits",
        "label": "Monthly credits",
        "unit": "percent",
        "used": 40.0,
        "resets_at": RESET,
        "starts_at": (datetime.fromisoformat(RESET) - timedelta(days=30)).isoformat(),
    }
    with usage_db(tmp_path) as db:
        db.execute(
            "INSERT INTO usage_snapshots VALUES (?, ?, ?)",
            (who, json.dumps({"observed_at": observed, "windows": [window]}), observed),
        )
    found = daily.read_weekly(tmp_path, "grok", who)
    assert found is not None and found["percent"] == 40.0 and found["id"] == "credits"
    assert found["measure"] == "monthly credits"
    assert found["scale"] == pytest.approx(7 / 30)
    # An account with a weekly window still reads that, unscaled.
    weekly = {"id": "seven_day", "unit": "percent", "used": 20.0, "resets_at": RESET}
    with usage_db(tmp_path) as db:
        db.execute(
            "INSERT OR REPLACE INTO usage_snapshots VALUES (?, ?, ?)",
            (who, json.dumps({"observed_at": observed, "windows": [window, weekly]}), observed),
        )
    found = daily.read_weekly(tmp_path, "grok", who)
    assert found is not None and found["percent"] == 20.0 and "scale" not in found


# Self-work ------------------------------------------------------------------------------


def store_reading(data: Path, percent: float, minutes: float) -> None:
    observed = (BASE + timedelta(minutes=minutes)).isoformat()
    window = {"id": "seven_day", "unit": "percent", "used": percent, "resets_at": RESET}
    with usage_db(data) as db:
        db.execute(
            "INSERT OR REPLACE INTO usage_rate_limits VALUES (?, ?, ?, ?, ?)",
            ("claude_code", "", "", observed, json.dumps([window])),
        )


@pytest.fixture
def capped(metered, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(windows, "default_zone", lambda: "America/New_York")
    return metered


def test_self_work_stops_starting_work_at_its_daily_cap(capped):
    home, fake, data = capped
    fake.hold.clear()
    add(home, "First")
    add(home, "Second")
    set_options(home, max_threads=1, daily_allowance_percent=11)
    store_reading(data, 40, 14)
    at(home, 15)
    tick(home)
    assert ticket(home, "First")["status"] == "in_progress"
    wait_for(lambda: tick(home) or fake.working() == 1)
    # Claude's weekly allowance rose 12 points while self-work ran: past the 11% cap.
    store_reading(data, 52, 20)
    at(home, 21)
    tick(home)
    fake.hold.set()
    settle(home, "First")
    tick(home)
    assert ticket(home, "Second")["status"] == "todo"
    view = home.self_work.view()
    reason = view["state"]["limit_reason"]
    assert "about 12% of the Claude Code weekly allowance today" in reason
    assert "daily cap of 11%" in reason and reason in notes(home)
    assert view["state"]["daily_allowance"]["used_percent"] == 12.0
    assert view["state"]["daily_allowance"]["day"] == "2026-11-09"
    assert "Used today: about 12% of 11%" in view["state"]["daily_allowance"]["message"]
    # Rem's chat status says it too.
    with home.store.read() as db:
        line = home.self_work._daily_line(db, home.self_work.clock())
    assert "about 12%" in line and "daily cap is 11%" in line
    # A fresh SelfWork reads the same count from Rem's store, as after a restart.
    again = SelfWork(home)
    again.clock = home.self_work.clock
    settings = home.self_work.view()["settings"]
    store_reading(data, 52, 25)
    assert again._daily_limit(settings, clock(26)) is not None
    # Midnight in New York (05:00 UTC the next day) starts a new count; work starts again.
    store_reading(data, 52, 17 * 60 + 5)
    at(home, 17 * 60 + 10)
    tick(home)
    settle(home, "Second")
    assert home.self_work.view()["state"]["daily_allowance"]["day"] == "2026-11-10"


def test_without_a_usage_reading_self_work_starts_nothing_and_says_so(capped):
    home, _, data = capped
    add(home, "Waiting")
    set_options(home, daily_allowance_percent=11)
    at(home, 15)
    tick(home)
    assert ticket(home, "Waiting")["status"] == "todo"
    view = home.self_work.view()
    assert "needs a Claude Code weekly allowance reading" in view["state"]["limit_reason"]
    assert view["state"]["daily_allowance"]["available"] is False
    assert any("needs a Claude Code weekly allowance reading" in n for n in notes(home))
    # A reading comes in: work starts.
    store_reading(data, 10, 15)
    at(home, 16)
    tick(home)
    settle(home, "Waiting")
    assert not home.self_work.view()["state"]["limit_reason"]


def test_the_cap_is_off_by_default(capped):
    home, _, _ = capped
    view = home.self_work.view()
    assert view["settings"]["daily_allowance_percent"] == 0
    assert view["limits"]["daily_allowance_percent_max"] == 100
    assert view["state"]["daily_allowance"]["cap_percent"] == 0
    # Off, a missing reading doesn't hold self-work back.
    add(home, "Uncapped")
    at(home, 15)
    tick(home)
    settle(home, "Uncapped")


@pytest.mark.parametrize(
    "value", [-1, 101, 11.5, "11", True, None], ids=["neg", "big", "frac", "text", "bool", "none"]
)
def test_the_cap_setting_is_checked(capped, value):
    home, _, _ = capped
    with pytest.raises(KanbanRefused):
        set_options(home, daily_allowance_percent=value)


def test_the_cap_setting_is_saved(capped):
    home, _, _ = capped
    set_options(home, daily_allowance_percent=11)
    assert home.self_work.view()["settings"]["daily_allowance_percent"] == 11
    assert home.self_work.for_prompt()["daily_allowance"]["cap_percent"] == 11


def test_the_period_report_says_the_cap_stopped_new_work(capped):
    home, _, _ = capped
    from gofer.ui.rem_kanban import add_report

    period = {"id": "p1", "started_at": BASE.isoformat(), "from_seq": 0}
    with home.store.connect() as db:
        add_report(db, "note", text="Stopped starting new work: daily cap.", limit="daily")
        add_report(db, "note", text="Started after 15 quiet minutes.")
        report = windows.build_report(db, period, datetime.now(UTC), "America/New_York")
    assert report["limits"] == ["Stopped starting new work: daily cap."]
    # A period that started nothing because of the cap still gets its report.
    assert not windows.empty(report)
    text = windows.report_text(report, datetime.now(UTC), "window")
    assert "- Stopped starting new work: daily cap." in text
