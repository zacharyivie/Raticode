"""Self-work's Pace to reset: new starts follow a line from 0% to the aim before the reset."""

import json
import random
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.core import provider_accounts
from gofer.core.usage_ledger import profile_usage_identity, usage_db
from gofer.ui import rem_self_work_pace as pacing
from gofer.ui.rem_kanban import KanbanRefused
from tests.unit.test_rem_kanban import BASE, add, at, set_options, settle, tick, ticket, wait_for
from tests.unit.test_self_work_daily_cap import capped as capped
from tests.unit.test_ticket_usage_and_limits import metered as metered
from tests.unit.test_ticket_usage_and_limits import notes

START = datetime(2026, 11, 19, 5, tzinfo=UTC)
RESET_AT = START + timedelta(days=7)


def weekly(
    percent: float,
    observed: datetime,
    *,
    resets: datetime | None = RESET_AT,
    window_id: str = "seven_day",
    label: str = "Weekly",
    kind: str = pacing.WEEKLY,
    starts: datetime | None = None,
    period: timedelta = pacing.WEEK,
) -> pacing.Window:
    return pacing.Window(window_id, label, kind, percent, observed, resets, starts, period)


def hours(value: float) -> datetime:
    return START + timedelta(hours=value)


# The pace line ----------------------------------------------------------------------------


def test_the_line_runs_from_zero_to_the_aim_before_the_reset():
    finish = RESET_AT - timedelta(hours=3)
    assert pacing.line_at(START, START, finish, 97) == 0
    assert pacing.line_at(START + (finish - START) / 2, START, finish, 97) == pytest.approx(48.5)
    assert pacing.line_at(finish, START, finish, 97) == 97
    # Flat for the last hours, and 0 before the window began.
    assert pacing.line_at(RESET_AT - timedelta(minutes=1), START, finish, 97) == 97
    assert pacing.line_at(START - timedelta(hours=1), START, finish, 97) == 0


def test_on_an_even_pace_one_start_waits_for_the_line():
    # 82.5 hours in: the line is at 48.5%.
    clock = hours(82.5)
    found = pacing.plan([weekly(48.5, clock)], {}, 0, clock)
    assert found.state == pacing.AHEAD and found.starts == 0
    assert found.line == pytest.approx(48.5)
    # The next start comes once the line has room for one task: 1.5 points, about 2.6 hours.
    assert found.next_start_at is not None
    assert found.next_start_at - clock == pytest.approx(
        timedelta(hours=2.55), abs=timedelta(minutes=1)
    )


def test_behind_the_line_fills_threads_and_room_is_kept_for_running_tasks():
    clock = hours(82.5)
    found = pacing.plan([weekly(40, clock)], {}, 0, clock)
    # 8.5 points under the line, 1.5 per task: 5 may start.
    assert found.state == pacing.ROOM and found.starts == 5
    # Each running task keeps its 1.5 points.
    assert pacing.plan([weekly(40, clock)], {}, 2, clock).starts == 3
    assert pacing.plan([weekly(40, clock)], {}, 5, clock).starts == 0


def test_ahead_of_the_line_nothing_starts_and_the_sentence_says_when():
    clock = hours(82.5)
    found = pacing.plan([weekly(52, clock)], {}, 0, clock)
    assert found.held and found.state == pacing.AHEAD
    key, text = pacing.limit(found, "Claude Code", UTC, clock) or ("", "")
    assert key == "pace"
    assert text.startswith(
        "Holding new work: Claude Code's weekly allowance is 52% used, 3.5% ahead of its pace "
        "line (48.5%). Next start around "
    )
    assert text.endswith("Running work finishes normally.")


def test_a_flapping_reading_doesnt_flicker_between_start_and_hold():
    clock = hours(82.5)
    window = weekly(47, clock)
    # Without history, 47% leaves room for one start.
    assert pacing.plan([window], {}, 0, clock).starts == 1
    # It read 48 and 49 minutes ago in the same window: the highest of the last 30 minutes counts.
    history = {
        "seven_day": [
            {"used_percent": 49.0, "resets_at": RESET_AT.isoformat(), "observed_at": hours(82.2)},
            {"used_percent": 47.0, "resets_at": RESET_AT.isoformat(), "observed_at": hours(82.4)},
        ]
    }
    found = pacing.plan([window], history, 0, clock)
    assert found.used == 49 and found.starts == 0
    # A reading older than 30 minutes no longer counts.
    later = hours(82.2) + timedelta(minutes=31)
    assert pacing.plan([weekly(47, later)], history, 0, later).used == 47


def test_a_reset_mid_history_starts_a_new_line():
    # The week reset an hour ago (a paid reset moved it): last window's 95% doesn't count.
    new_reset = hours(1) + timedelta(days=7)
    clock = hours(1)
    history = {
        "seven_day": [
            {"used_percent": 95.0, "resets_at": START.isoformat(), "observed_at": hours(0.9)},
        ]
    }
    found = pacing.plan([weekly(0, clock, resets=new_reset)], history, 0, clock)
    assert found.used == 0
    # The new line starts at the new window's start, so it is still near 0: one start begins it.
    assert found.state == pacing.NOT_STARTED and found.starts == 1


def test_a_stale_or_missing_reading_starts_nothing():
    clock = hours(50)
    found = pacing.plan([weekly(10, clock - timedelta(minutes=31))], {}, 0, clock)
    assert found.state == pacing.UNKNOWN and found.starts == 0
    key, text = pacing.limit(found, "work", UTC, clock) or ("", "")
    assert key == "pace_unknown" and "needs a reading of work's weekly allowance" in text
    assert "the last one is from 06:29" in text
    found = pacing.plan([], {}, 0, clock)
    assert found.state == pacing.UNKNOWN and found.starts == 0
    assert "there hasn't been one yet" in (pacing.limit(found, "work", UTC, clock) or ("", ""))[1]


def test_a_stale_window_still_holds_at_its_last_reading_beside_a_fresh_one():
    """Nightly audit, 23 November: an overall weekly window at 99% read two hours ago was
    dropped because a fresh per-model window read 1%, and 55 tasks could start. Use only
    rises until the reset, so the old reading is a floor."""
    clock = hours(82.5)
    overall = weekly(99, clock - timedelta(hours=2))
    model = weekly(1, clock, window_id="seven_day_opus", label="Weekly Opus")
    found = pacing.plan([overall, model], {}, 0, clock)
    assert found.starts == 0 and found.window == overall
    assert pacing.limit(found, "Claude Code", UTC, clock) is not None
    # A stale window that was low still leaves room: it binds only at its own floor.
    low = weekly(5, clock - timedelta(hours=2))
    found = pacing.plan([low, model], {}, 0, clock)
    assert found.state == pacing.ROOM and found.window == low
    fresh_only = pacing.plan([model], {}, 0, clock).starts
    assert found.starts is not None and fresh_only is not None
    assert 0 < found.starts <= fresh_only


def test_a_window_with_no_reset_time_stops_starting_at_the_aim():
    """Nightly audit, 23 November: with no reset time a window at 99%, or credits at 100%,
    still let one task at a time start."""
    clock = hours(50)
    for used in (97, 99, 100):
        found = pacing.plan([weekly(used, clock, resets=None)], {}, 0, clock)
        assert found.state == pacing.AHEAD and found.starts == 0, used
        key, text = pacing.limit(found, "work", UTC, clock) or ("", "")
        assert key == "pace" and f"{used}% used" in text
    credits = weekly(100, clock, resets=None, window_id="credits", kind=pacing.CREDITS)
    assert pacing.plan([credits], {}, 0, clock).starts == 0
    # Below the aim it still starts one task at a time.
    assert pacing.plan([weekly(96, clock, resets=None)], {}, 0, clock).starts == 1


def test_a_window_that_hasnt_started_allows_one_start():
    # Codex at 0% with a reset that slides to "now + 7 days" while nobody uses it.
    clock = hours(50)
    sliding = weekly(0, clock, resets=clock + pacing.WEEK, window_id="secondary")
    found = pacing.plan([sliding], {}, 0, clock)
    assert found.state == pacing.NOT_STARTED and found.starts == 1
    # One task already running starts the window: no second one until a reading shows it.
    assert pacing.plan([sliding], {}, 1, clock).starts == 0
    # No reset time at all: one task at a time.
    unknown_reset = weekly(30, clock, resets=None)
    assert pacing.plan([unknown_reset], {}, 0, clock).starts == 1
    assert pacing.plan([unknown_reset], {}, 1, clock).starts == 0


def test_a_credits_period_paces_over_its_own_length():
    # Grok's monthly credits: a 30-day period, half gone.
    start = START
    end = START + timedelta(days=30)
    clock = START + timedelta(days=15)
    credits = weekly(
        30,
        clock,
        resets=end,
        window_id="credits",
        label="Monthly credits",
        kind=pacing.CREDITS,
        starts=start,
        period=timedelta(days=30),
    )
    found = pacing.plan([credits], {}, 0, clock)
    assert found.line == pytest.approx(97 * 15 / (30 - 3 / 24), rel=1e-3)
    assert found.state == pacing.ROOM and found.starts == 12
    assert "home's monthly credits" in pacing.status(found, "home", UTC, clock)
    # A weekly window wins over credits when an account reports both.
    both = pacing.plan([credits, weekly(10, clock, resets=clock + timedelta(days=3))], {}, 0, clock)
    assert both.window is not None and both.window.kind == pacing.WEEKLY


def test_a_nearly_full_five_hour_window_holds_starts():
    clock = hours(82.5)
    five = weekly(
        91,
        clock,
        resets=clock + timedelta(hours=2),
        window_id="five_hour",
        label="5-hour",
        kind=pacing.FIVE_HOUR,
        period=timedelta(hours=5),
    )
    found = pacing.plan([weekly(30, clock), five], {}, 0, clock)
    assert found.state == pacing.CEILING and found.starts == 0
    key, text = pacing.limit(found, "Claude Code", UTC, clock) or ("", "")
    assert key == "pace_ceiling" and "Claude Code's 5-hour limit is 91% used" in text
    # Under 90%, or no current reading of it, the weekly line decides.
    calm = pacing.Window(**{**five.__dict__, "percent": 80.0})
    assert pacing.plan([weekly(30, clock), calm], {}, 0, clock).starts == 12
    old = pacing.Window(**{**five.__dict__, "observed_at": clock - timedelta(hours=1)})
    assert pacing.plan([weekly(30, clock), old], {}, 0, clock).starts == 12


def test_the_furthest_ahead_weekly_window_binds():
    clock = hours(82.5)
    overall = weekly(30, clock)
    model = weekly(50, clock, window_id="seven_day_opus", label="Weekly Opus")
    found = pacing.plan([overall, model], {}, 0, clock)
    assert found.window == model and found.starts == 0 and found.several
    assert (
        "weekly allowance (Weekly Opus)"
        in (pacing.limit(found, "Claude Code", UTC, clock) or ("", ""))[1]
    )


def test_metered_api_keys_have_nothing_to_pace():
    found = pacing.plan([], {}, 3, hours(10), provider="anthropic_api")
    assert found.state == pacing.NO_ALLOWANCE and found.starts is None and not found.held
    assert pacing.limit(found, "Anthropic API", UTC, hours(10)) is None


# A simulated week ---------------------------------------------------------------------------


def simulate_week(seed: int, *, threads: int = 4, aim: int = 97) -> dict[str, Any]:
    """One week of self-work paced to the reset, minute by minute.

    Tasks cost 0.5 to 3 points each, reported as they build up over 20 to 120 minutes. The
    user's own work adds a dozen bursts of 1 to 2.5 points, none in the last 6 hours: there the
    3 points the aim leaves are the user's, and nothing self-work does can stop the user spending
    more. The app is closed for 10 hours on the second night: no checks
    and no new starts, though work the user does meanwhile still counts. The provider reports
    whole percents every few minutes, sometimes a point low (flapping), and the planner sees
    only those readings.
    """
    rng = random.Random(seed)
    true_used = 0.0
    tasks: list[list[float]] = []  # [remaining cost, per-minute cost]
    readings: list[dict[str, Any]] = []
    last: pacing.Window | None = None
    next_reading = START
    peak_before_reset = 0.0
    closed = (START + timedelta(hours=40), START + timedelta(hours=50))
    user_bursts = {rng.randrange(0, (7 * 24 - 6) * 60) for _ in range(12)}
    started = 0
    minute = START
    while minute < RESET_AT:
        # Work in flight reports its cost as it builds up.
        for task in tasks:
            spend = min(task[0], task[1])
            task[0] -= spend
            true_used += spend
        tasks = [t for t in tasks if t[0] > 1e-9]
        offset = int((minute - START).total_seconds() // 60)
        if offset in user_bursts:
            true_used += rng.uniform(1.0, 2.5)
        peak_before_reset = max(peak_before_reset, true_used)
        if minute >= next_reading:
            shown = max(0, int(true_used) - (1 if rng.random() < 0.2 else 0))
            last = weekly(float(shown), minute)
            readings.append(
                {
                    "used_percent": float(shown),
                    "resets_at": RESET_AT.isoformat(),
                    "observed_at": minute,
                }
            )
            next_reading = minute + timedelta(minutes=rng.randint(2, 10))
        app_open = not closed[0] <= minute < closed[1]
        if app_open and last is not None:
            recent = [r for r in readings[-20:] if minute - r["observed_at"] <= pacing.PEAK_OVER]
            found = pacing.plan([last], {"seven_day": recent}, len(tasks), minute, target=aim)
            for _ in range(min(found.starts or 0, threads - len(tasks))):
                cost = rng.uniform(0.5, 3.0)
                tasks.append([cost, cost / rng.randint(20, 120)])
                started += 1
        minute += timedelta(minutes=1)
    return {"end": true_used, "peak": peak_before_reset, "started": started}


@pytest.mark.parametrize("seed", range(1, 11))
def test_a_simulated_week_ends_near_the_aim_without_running_out(seed: int):
    result = simulate_week(seed)
    # It never reaches 100% before the reset, and ends within 5 points of the aim.
    assert result["peak"] < 100
    assert 97 - 5 <= result["end"] < 100, result
    assert result["started"] > 20


# Settings ---------------------------------------------------------------------------------


def test_settings_saved_before_the_choice_keep_their_daily_cap():
    assert pacing.mode({}) == pacing.OFF
    assert pacing.mode({"daily_allowance_percent": 12}) == pacing.DAILY
    assert pacing.daily_cap({"daily_allowance_percent": 12}) == 12
    assert pacing.daily_cap({"daily_allowance_percent": 12, "allowance_mode": "pace"}) == 0
    assert pacing.aim({}) == 97 and pacing.lead({}) == 3


def test_daily_cap_and_pace_are_one_choice():
    off: dict[str, Any] = {}
    assert pacing.resolve(off, {"allowance_mode": "pace"}) == {
        "allowance_mode": "pace",
        "daily_allowance_percent": 0,
    }
    # Choosing Daily cap keeps a percent, or starts at a seventh of a week.
    assert pacing.resolve(off, {"allowance_mode": "daily"})["daily_allowance_percent"] == 14
    assert (
        pacing.resolve({"daily_allowance_percent": 9}, {"allowance_mode": "daily"})[
            "daily_allowance_percent"
        ]
        == 9
    )
    paced = {"allowance_mode": "pace"}
    with pytest.raises(ValueError, match="can't both be on"):
        pacing.resolve(off, {"allowance_mode": "pace", "daily_allowance_percent": 10})
    with pytest.raises(ValueError, match="can't both be on"):
        pacing.resolve(paced, {"daily_allowance_percent": 10})
    # An older client that only knows the percent still turns the daily cap on and off.
    assert pacing.resolve(off, {"daily_allowance_percent": 10})["allowance_mode"] == "daily"
    daily_on = {"allowance_mode": "daily", "daily_allowance_percent": 10}
    assert pacing.resolve(daily_on, {"daily_allowance_percent": 0})["allowance_mode"] == "off"
    assert pacing.resolve(paced, {"daily_allowance_percent": 0})["allowance_mode"] == "pace"
    for bad in (
        {"pace_aim_percent": 0},
        {"pace_aim_percent": 101},
        {"pace_lead_hours": 25},
        {"allowance_mode": "x"},
    ):
        with pytest.raises(ValueError):
            pacing.resolve(off, bad)


# Self-work --------------------------------------------------------------------------------

# Readings are stored with the test clock (BASE), so the window is placed around it: it began
# 4 days before BASE and resets 3 days after, putting the line at about 56.6% at BASE + 15 min.
TICK_RESET = BASE + timedelta(days=3)


def store_weekly(
    data: Path,
    percent: float,
    minutes: float,
    *,
    profile: str = "",
    provider: str = "codex",
    resets: datetime = TICK_RESET,
) -> None:
    observed = (BASE + timedelta(minutes=minutes)).isoformat()
    window = {
        "id": "secondary",
        "label": "Weekly",
        "unit": "percent",
        "used": percent,
        "resets_at": resets.isoformat(),
    }
    with usage_db(data) as db:
        db.execute(
            "INSERT OR REPLACE INTO usage_rate_limits VALUES (?, ?, ?, ?, ?)",
            (provider, profile, "", observed, json.dumps([window])),
        )


def in_progress(home: Any) -> list[str]:
    return [t["title"] for t in home.kanban.board()["tickets"] if t["status"] == "in_progress"]


def test_self_work_starts_only_what_fits_under_the_line(capped):
    home, fake, data = capped
    fake.hold.clear()
    for title in ("First", "Second", "Third"):
        add(home, title)
    set_options(home, max_threads=3, allowance_mode="pace")
    # 60% is ahead of the 56.6% line: nothing starts, and the report says why once.
    store_weekly(data, 60, 14)
    at(home, 15)
    tick(home)
    assert in_progress(home) == []
    view = home.self_work.view()
    reason = view["state"]["limit_reason"]
    assert reason.startswith("Holding new work: Codex's weekly allowance is 60% used, 3.4% ahead")
    assert reason in notes(home)
    paced = view["state"]["allowance_pace"]
    assert paced["mode"] == "pace" and paced["state"] == "ahead" and paced["starts"] == 0
    assert paced["line_percent"] == pytest.approx(56.6, abs=0.1)
    # 54% leaves room for one task (2.6 points), though three threads are free.
    store_weekly(data, 54, 20)
    at(home, 21)
    tick(home)
    wait_for(lambda: tick(home) or fake.working() == 1)
    at(home, 22)
    tick(home)
    tick(home)
    assert len(in_progress(home)) == 1
    # The running task keeps its room, so the next check starts nothing more.
    assert home.self_work.view()["state"]["allowance_pace"]["starts"] == 0
    # Going ahead of the line never stops work already running.
    store_weekly(data, 70, 23)
    at(home, 24)
    tick(home)
    assert fake.working() == 1 and len(in_progress(home)) == 1
    fake.hold.set()
    settle(home, in_progress(home)[0])
    # Holding again after a release adds no second note.
    assert sum("Holding new work" in n for n in notes(home)) == 1


def test_pacing_never_holds_back_the_users_own_runs(capped):
    home, _, data = capped
    set_options(home, allowance_mode="pace")
    store_weekly(data, 90, 14)
    created = add(home, "Mine")
    at(home, 15)
    tick(home)
    assert ticket(home, "Mine")["status"] == "todo"
    # The user starts it: it runs whatever the pace says.
    home.kanban.move(
        "user", {"ticket_id": created["id"], "expected_revision": 1, "column": "in_progress"}
    )
    assert ticket(home, "Mine")["running"]
    settle(home, "Mine")


def test_without_a_current_reading_pacing_starts_nothing(capped):
    home, _, data = capped
    add(home, "Waiting")
    set_options(home, allowance_mode="pace")
    at(home, 15)
    tick(home)
    assert ticket(home, "Waiting")["status"] == "todo"
    view = home.self_work.view()
    assert (
        "Pace to reset needs a reading of Codex's weekly allowance" in view["state"]["limit_reason"]
    )
    assert view["state"]["allowance_pace"]["state"] == "unknown"
    # A reading comes in, behind the line: work starts.
    store_weekly(data, 20, 15)
    at(home, 16)
    tick(home)
    settle(home, "Waiting")


def test_the_settings_offer_one_choice(capped):
    home, _, _ = capped
    view = home.self_work.view()
    assert view["settings"]["allowance_mode"] == "off"
    assert view["settings"]["pace_aim_percent"] == 97
    assert view["settings"]["pace_lead_hours"] == 3
    assert view["limits"]["pace_aim_percent_min"] == 1
    assert view["limits"]["pace_aim_percent_max"] == 100
    set_options(home, allowance_mode="pace", pace_aim_percent=90, pace_lead_hours=6)
    settings = home.self_work.view()["settings"]
    assert settings["allowance_mode"] == "pace" and settings["daily_allowance_percent"] == 0
    assert settings["pace_aim_percent"] == 90 and settings["pace_lead_hours"] == 6
    with pytest.raises(KanbanRefused, match="can't both be on"):
        set_options(home, daily_allowance_percent=10)
    set_options(home, allowance_mode="daily", daily_allowance_percent=10)
    settings = home.self_work.view()["settings"]
    assert settings["allowance_mode"] == "daily" and settings["daily_allowance_percent"] == 10
    for bad in (0, 101):
        with pytest.raises(KanbanRefused, match="aim from 1 to 100%"):
            set_options(home, pace_aim_percent=bad)


def test_any_aim_from_1_to_100_paces_the_line_and_status(capped):
    """The aim isn't held to 80% or more: giving self-work half the week works."""
    for chosen in (1, 50, 79, 80, 100):
        assert pacing.resolve({}, {"pace_aim_percent": chosen}) == {"pace_aim_percent": chosen}
        assert pacing.aim({"pace_aim_percent": chosen}) == chosen
    # Values saved before stay valid; one that isn't a percent falls back to the default.
    assert pacing.aim({"pace_aim_percent": 85}) == 85
    assert pacing.aim({"pace_aim_percent": 0}) == pacing.aim({"pace_aim_percent": "x"}) == 97
    home, _, data = capped
    add(home, "Half")
    set_options(home, allowance_mode="pace", pace_aim_percent=50)
    # At BASE + 15 min the line to 50% is at about 29.2%, half the 97% line's 56.6%, so 40%
    # used, which is behind a 97% line, is ahead of a 50% one.
    store_weekly(data, 40, 14)
    at(home, 15)
    tick(home)
    assert ticket(home, "Half")["status"] == "todo"
    state = home.self_work.view()["state"]
    paced = state["allowance_pace"]
    assert paced["aim_percent"] == 50 and paced["state"] == "ahead" and paced["starts"] == 0
    assert paced["line_percent"] == pytest.approx(56.6 * 50 / 97, abs=0.1)
    assert "line (29.2%)" in state["limit_reason"]
    assert "line (29.2%)" in paced["message"]
    # Behind the 50% line, work starts.
    store_weekly(data, 20, 15)
    at(home, 16)
    tick(home)
    settle(home, "Half")


@pytest.mark.parametrize("seed", range(1, 4))
def test_a_simulated_week_with_half_the_allowance_ends_near_half(seed: int):
    result = simulate_week(seed, aim=50)
    assert 50 - 5 <= result["end"] < 50 + 5, result


@pytest.fixture
def paced_accounts(capped, monkeypatch: pytest.MonkeyPatch):
    home, fake, data = capped
    for name in ("home", "work"):
        provider_accounts.create_account(name, provider="codex", data_dir=data)
    monkeypatch.setattr(
        provider_accounts,
        "account_status",
        lambda profile, **_: {"state": provider_accounts.SIGNED_IN},
    )
    with home.store.read() as db:
        config = home.store.setting(db, "config")
    home.configure(
        {k: config[k] for k in ("provider", "model", "command_slots", "projects") if k in config}
        | {"provider_accounts": {"codex": "auto"}}
    )
    return home, fake, data


def test_each_account_has_its_own_line_and_choose_by_usage_skips_one_ahead(paced_accounts):
    home, _, data = paced_accounts
    set_options(home, max_threads=1, allowance_mode="pace")
    # "home" has the lower percent, so it would come first, but its week began only half a day
    # ago: 20% is far ahead of its line (about 7%). "work" resets in 3 days and is behind its own.
    later = BASE + timedelta(days=6, hours=12)
    store_weekly(data, 20, 14, profile=profile_usage_identity("home", data), resets=later)
    store_weekly(data, 30, 14, profile=profile_usage_identity("work", data))
    add(home, "Paced")
    at(home, 15)
    tick(home)
    settle(home, "Paced")
    with home.store.read() as db:
        run = home.store.get(db, "runs", ticket(home, "Paced")["run_id"])
    assert run["provider_account"]["name"] == "work"
    assert {"name": "home", "reason": "home is ahead of its pace line"} in run["provider_account"][
        "skipped"
    ]
    # Both ahead: nothing starts, and the reason names both.
    store_weekly(data, 58, 20, profile=profile_usage_identity("work", data))
    add(home, "Nowhere")
    at(home, 21)
    tick(home)
    assert ticket(home, "Nowhere")["status"] == "todo"
    reason = home.self_work.view()["state"]["limit_reason"]
    assert "home is ahead of its pace line" in reason and "work is ahead of its pace line" in reason


def test_a_paused_runs_account_check_fails_safe_when_the_store_cant_be_read(
    paced_accounts, monkeypatch: pytest.MonkeyPatch
):
    home, _, _ = paced_accounts
    run = {"provider_account": {"provider": "codex", "name": "work"}}
    clock = BASE + timedelta(minutes=15)

    def unreadable() -> Any:
        raise sqlite3.OperationalError("database is locked")

    # The settings themselves can't be read: no UnboundLocalError, and the run isn't held,
    # as before Pace to reset.
    monkeypatch.setattr(home.store, "read", unreadable)
    assert home.self_work._account_limited(run, clock) is False
    monkeypatch.undo()
    # The settings say Pace to reset but the account can't be checked: the run waits.
    set_options(home, allowance_mode="pace")

    def broken(*_: Any, **__: Any) -> Any:
        raise sqlite3.OperationalError("usage store is locked")

    from gofer.ui import rem_self_work_accounts

    monkeypatch.setattr(rem_self_work_accounts, "account_limit", broken)
    assert home.self_work._account_limited(run, clock) is True
