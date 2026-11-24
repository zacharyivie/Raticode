"""Self-work's daily cap and rate-limit stop watch the account each self-started run uses."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.core import provider_accounts
from gofer.core.usage_ledger import profile_usage_identity, usage_db
from gofer.ui import rem_self_work_daily as daily
from gofer.ui.rem_home import RemHome
from tests.unit.test_rem_kanban import BASE, add, at, set_options, settle, tick, ticket, wait_for
from tests.unit.test_self_work_daily_cap import NEW_YORK, RESET, store_reading
from tests.unit.test_self_work_daily_cap import capped as capped
from tests.unit.test_ticket_usage_and_limits import metered as metered
from tests.unit.test_ticket_usage_and_limits import notes


def account_reading(data: Path, name: str, percent: float, minutes: float) -> None:
    """A weekly reading of one Codex account, the way its turns report it."""
    observed = (BASE + timedelta(minutes=minutes)).isoformat()
    window = {
        "id": "secondary",
        "label": "Weekly",
        "unit": "percent",
        "used": percent,
        "resets_at": RESET,
    }
    with usage_db(data) as db:
        db.execute(
            "INSERT OR REPLACE INTO usage_rate_limits VALUES (?, ?, ?, ?, ?)",
            ("codex", profile_usage_identity(name, data), "", observed, json.dumps([window])),
        )


def default_window(data: Path, percent: float, minutes: float) -> None:
    """A rate-limit window of Codex's default sign-in."""
    observed = (BASE + timedelta(minutes=minutes)).isoformat()
    window = {"id": "primary", "label": "5-hour", "unit": "percent", "used": percent}
    with usage_db(data) as db:
        db.execute(
            "INSERT OR REPLACE INTO usage_rate_limits VALUES (?, ?, ?, ?, ?)",
            ("codex", "", "", observed, json.dumps([window])),
        )


def use_accounts(home: RemHome, setting: str) -> None:
    with home.store.read() as db:
        config = home.store.setting(db, "config")
    home.configure(
        {
            key: config[key]
            for key in ("provider", "model", "command_slots", "projects")
            if key in config
        }
        | {"provider_accounts": {"codex": setting}}
    )


def run_of(home: RemHome, title: str) -> dict[str, Any]:
    with home.store.read() as db:
        return home.store.get(db, "runs", ticket(home, title)["run_id"])


def account_used(home: RemHome, name: str) -> float:
    data = home.data_dir
    with home.store.read() as db:
        state = home.store.setting(db, daily.STATE_KEY)
    key = daily.account_key("codex", profile_usage_identity(name, data))
    return float(daily.account_state(state, key).get("used") or 0.0)


@pytest.fixture
def accounts(capped, monkeypatch: pytest.MonkeyPatch):
    home, fake, data = capped
    for name in ("home", "work"):
        provider_accounts.create_account(name, provider="codex", data_dir=data)
    monkeypatch.setattr(
        provider_accounts,
        "account_status",
        lambda profile, **_: {"state": provider_accounts.SIGNED_IN},
    )
    return home, fake, data


def test_a_run_on_an_account_counts_against_that_account(accounts):
    home, fake, data = accounts
    use_accounts(home, "work")
    fake.hold.clear()
    add(home, "First")
    add(home, "Second")
    set_options(home, max_threads=1, daily_allowance_percent=11)
    # Only the account has a reading: the default sign-in's absence holds nothing back.
    account_reading(data, "work", 40, 14)
    at(home, 15)
    tick(home)
    assert ticket(home, "First")["status"] == "in_progress"
    assert run_of(home, "First")["provider_account"]["name"] == "work"
    wait_for(lambda: tick(home) or fake.working() == 1)
    # work's weekly allowance rose 12 points while self-work ran on it: past the 11% cap.
    account_reading(data, "work", 52, 20)
    # The default sign-in rose too, but self-work didn't run on it.
    store_reading(data, 30, 14)
    store_reading(data, 45, 20)
    at(home, 21)
    tick(home)
    fake.hold.set()
    settle(home, "First")
    tick(home)
    assert account_used(home, "work") == 12.0
    assert account_used(home, "home") == 0.0
    with home.store.read() as db:
        assert daily.used_today(home.store.setting(db, daily.STATE_KEY), BASE, NEW_YORK) == 0.0
    assert ticket(home, "Second")["status"] == "todo"
    view = home.self_work.view()
    reason = view["state"]["limit_reason"]
    assert "about 12% of work's weekly allowance today" in reason
    assert "daily cap of 11%" in reason and reason in notes(home)
    today = view["state"]["daily_allowance"]
    assert today["account"] == "work" and today["used_percent"] == 12.0
    assert "Used today: about 12% of 11% of work's weekly allowance" in today["message"]
    with home.store.read() as db:
        line = home.self_work._daily_line(db, home.self_work.clock())
    assert line.startswith(
        "Today self-work used about 12% of work's weekly allowance; its daily cap is 11%."
    )


def test_choose_by_usage_skips_an_account_at_the_cap(accounts):
    home, _, data = accounts
    use_accounts(home, "auto")
    set_options(home, max_threads=1, daily_allowance_percent=11)
    # "home" comes first by name, but self-work already used 12% of it today.
    key = daily.account_key("codex", profile_usage_identity("home", data))
    with home.store.connect() as db:
        home.store.set_setting(
            db,
            daily.STATE_KEY,
            {
                "accounts": {
                    key: {"day": "2026-11-09", "used": 12.0, "provider": "codex", "name": "home"}
                }
            },
        )
    account_reading(data, "home", 60, 14)
    account_reading(data, "work", 20, 14)
    add(home, "Work on the roomy account")
    at(home, 15)
    tick(home)
    settle(home, "Work on the roomy account")
    chosen = run_of(home, "Work on the roomy account")["provider_account"]
    assert chosen["name"] == "work" and chosen["source"] == "auto"
    assert "home reached the daily cap of 11%" in chosen["why"]
    assert {"name": "home", "reason": "home reached the daily cap of 11%"} in chosen["skipped"]
    # With every account at the cap, nothing new starts, and the reason names them.
    with home.store.connect() as db:
        state = home.store.setting(db, daily.STATE_KEY)
        work = daily.account_key("codex", profile_usage_identity("work", data))
        state["accounts"][work] = {
            **state["accounts"].get(work, {}),
            "day": "2026-11-09",
            "used": 11.5,
            "provider": "codex",
            "name": "work",
        }
        home.store.set_setting(db, daily.STATE_KEY, state)
    add(home, "Nowhere to run")
    at(home, 20)
    tick(home)
    assert ticket(home, "Nowhere to run")["status"] == "todo"
    reason = home.self_work.view()["state"]["limit_reason"]
    assert reason.startswith("Stopped starting new work: no Codex account has room for self-work.")
    assert "home reached the daily cap" in reason and "work reached the daily cap" in reason


def test_a_stale_default_sign_in_reading_doesnt_block_a_run_on_an_account(accounts):
    home, _, data = accounts
    use_accounts(home, "work")
    set_options(home, daily_allowance_percent=11, rate_limit_percent=80)
    # The default sign-ins have only an old weekly reading and a nearly full window.
    store_reading(data, 30, 0)
    default_window(data, 95, 0)
    account_reading(data, "work", 10, 58)
    add(home, "Runs on work")
    at(home, 60)
    tick(home)
    settle(home, "Runs on work")
    assert run_of(home, "Runs on work")["provider_account"]["name"] == "work"
    assert not home.self_work.view()["state"]["limit_reason"]


def test_an_accounts_own_rate_limit_stops_self_work_on_it(accounts):
    home, _, data = accounts
    use_accounts(home, "work")
    set_options(home, rate_limit_percent=80)
    account_reading(data, "work", 85, 14)
    add(home, "Held back")
    at(home, 15)
    tick(home)
    assert ticket(home, "Held back")["status"] == "todo"
    reason = home.self_work.view()["state"]["limit_reason"]
    assert "work's Weekly limit is 85% used" in reason


def test_choose_by_usage_stops_instead_of_using_the_default_sign_in_when_every_account_is_full(
    accounts, monkeypatch: pytest.MonkeyPatch
):
    """A full account is a limit like one at 95%: runs don't move to the default sign-in."""
    home, _, data = accounts
    # The readings are on the test's clock; the account chooser ages them by the real one.
    monkeypatch.setattr(provider_accounts, "_reading_age", lambda _observed: 0.0)
    use_accounts(home, "auto")
    set_options(home, max_threads=1)
    account_reading(data, "home", 100, 14)
    account_reading(data, "work", 100, 14)
    add(home, "Nowhere to go")
    at(home, 15)
    tick(home)
    assert ticket(home, "Nowhere to go")["status"] == "todo"
    reason = home.self_work.view()["state"]["limit_reason"]
    assert reason.startswith("Stopped starting new work: no Codex account has room for self-work.")
    assert "home's usage limit is reached" in reason and "work's" in reason


def test_without_accounts_the_default_sign_in_is_watched_as_before(capped):
    home, _, data = capped
    use_accounts(home, "auto")
    set_options(home, daily_allowance_percent=11)
    add(home, "Waiting")
    at(home, 15)
    tick(home)
    # No account exists, so the default sign-in's missing reading still holds work back.
    assert ticket(home, "Waiting")["status"] == "todo"
    assert (
        "needs a Claude Code weekly allowance reading"
        in (home.self_work.view()["state"]["limit_reason"])
    )
    store_reading(data, 10, 15)
    at(home, 16)
    tick(home)
    settle(home, "Waiting")
    assert "provider_account" not in run_of(home, "Waiting") or not (
        run_of(home, "Waiting")["provider_account"].get("name")
    )


def test_self_works_own_account_choice_runs_and_counts_before_rems_setting(accounts):
    home, fake, data = accounts
    # Rem's setting names work; self-work's own choice names home.
    use_accounts(home, "work")
    set_options(home, provider_accounts={"codex": "home"})
    fake.hold.clear()
    add(home, "First")
    set_options(home, max_threads=1, daily_allowance_percent=11)
    account_reading(data, "home", 40, 14)
    at(home, 15)
    tick(home)
    assert ticket(home, "First")["status"] == "in_progress"
    assert run_of(home, "First")["provider_account"]["name"] == "home"
    view = home.self_work.view()
    assert view["state"]["daily_allowance"]["account"] == "home"
    fake.hold.set()
    settle(home, "First")


def grok_credits(data: Path, name: str, percent: float, minutes: float) -> None:
    """A Grok account's monthly credits, as its usage refresh stores them: a 30-day period."""
    observed = (BASE + timedelta(minutes=minutes)).isoformat()
    window = {
        "id": "credits",
        "label": "Monthly credits",
        "unit": "percent",
        "used": percent,
        "limit": 100.0,
        "remaining_percent": 100.0 - percent,
        "resets_at": RESET,
        "starts_at": (datetime.fromisoformat(RESET) - timedelta(days=30)).isoformat(),
    }
    with usage_db(data) as db:
        db.execute(
            "INSERT OR REPLACE INTO usage_snapshots VALUES (?, ?, ?)",
            (
                profile_usage_identity(name, data),
                json.dumps({"observed_at": observed, "windows": [window]}),
                observed,
            ),
        )


@pytest.fixture
def grok_accounts(capped, monkeypatch: pytest.MonkeyPatch):
    home, fake, data = capped
    for name in ("home", "work"):
        provider_accounts.create_account(name, provider="grok", data_dir=data)
    monkeypatch.setattr(
        provider_accounts,
        "account_status",
        lambda profile, **_: {"state": provider_accounts.SIGNED_IN},
    )
    with home.store.read() as db:
        config = home.store.setting(db, "config")
    home.configure(
        {key: config[key] for key in ("model", "command_slots", "projects") if key in config}
        | {"provider": "grok", "provider_accounts": {"grok": "work"}}
    )
    return home, fake, data


def test_a_grok_accounts_credits_count_against_the_scaled_daily_cap(grok_accounts):
    home, fake, data = grok_accounts
    fake.hold.clear()
    add(home, "First")
    add(home, "Second")
    set_options(home, max_threads=1, daily_allowance_percent=10)
    # Grok reports monthly credits and no weekly window: that reading is enough to start.
    grok_credits(data, "work", 40, 14)
    at(home, 15)
    tick(home)
    assert ticket(home, "First")["status"] == "in_progress"
    assert run_of(home, "First")["provider_account"]["name"] == "work"
    assert "no current weekly reading" not in home.self_work.view()["state"]["limit_reason"]
    wait_for(lambda: tick(home) or fake.working() == 1)
    # 3% of a 30-day month is past 10% of a week (about 2.33% of the month).
    grok_credits(data, "work", 43, 20)
    at(home, 21)
    tick(home)
    fake.hold.set()
    settle(home, "First")
    tick(home)
    with home.store.read() as db:
        state = home.store.setting(db, daily.STATE_KEY)
    key = daily.account_key("grok", profile_usage_identity("work", data))
    assert daily.account_state(state, key)["used"] == 3.0
    assert ticket(home, "Second")["status"] == "todo"
    view = home.self_work.view()
    reason = view["state"]["limit_reason"]
    assert "about 3% of work's monthly credits today" in reason
    assert "daily cap of 2.3% (the 10% weekly cap over a 30-day period)" in reason
    today = view["state"]["daily_allowance"]
    assert today["account"] == "work" and today["measure"] == "monthly credits"
    assert today["message"].startswith("Used today: about 3% of work's monthly credits")
    with home.store.read() as db:
        line = home.self_work._daily_line(db, home.self_work.clock())
    assert line.startswith(
        "Today self-work used about 3% of work's monthly credits; its daily cap is 2.3%"
    )


def test_choose_by_usage_skips_a_grok_account_at_its_scaled_cap(grok_accounts):
    home, _, data = grok_accounts
    set_options(home, max_threads=1, daily_allowance_percent=10, provider_accounts={"grok": "auto"})
    key = daily.account_key("grok", profile_usage_identity("home", data))
    with home.store.connect() as db:
        home.store.set_setting(
            db,
            daily.STATE_KEY,
            {
                "accounts": {
                    key: {"day": "2026-11-09", "used": 2.5, "provider": "grok", "name": "home"}
                }
            },
        )
    grok_credits(data, "home", 20, 14)
    grok_credits(data, "work", 60, 14)
    add(home, "Runs on work")
    at(home, 15)
    tick(home)
    settle(home, "Runs on work")
    chosen = run_of(home, "Runs on work")["provider_account"]
    assert chosen["name"] == "work"
    short = "home reached the daily cap of 2.3% (the 10% weekly cap over a 30-day period)"
    assert {"name": "home", "reason": short} in chosen["skipped"]
