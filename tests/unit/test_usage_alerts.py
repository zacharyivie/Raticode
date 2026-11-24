"""Limit alerts: one per account, window and level until the window resets."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from gofer.core import usage_alerts
from gofer.core.phone_alerts import PhoneAlertStore
from gofer.core.usage_alerts import (
    UsageAlertError,
    alert_settings,
    check_limit_alerts,
    reset_text,
    save_alert_settings,
    window_kind,
)
from gofer.core.usage_ledger import usage_db
from gofer.core.usage_service import refresh_usage

NOW = datetime(2026, 11, 17, 18, 0, tzinfo=UTC)


def _window(
    used: float, *, resets: datetime | None, window_id: str = "five_hour", label: str = ""
) -> dict[str, Any]:
    return {
        "id": window_id,
        "label": label or {"five_hour": "Five-hour allowance"}.get(window_id, "Weekly allowance"),
        "unit": "percent",
        "used": used,
        "limit": 100.0,
        "remaining_percent": 100.0 - used,
        "resets_at": resets.isoformat() if resets else None,
    }


def _account(
    *windows: dict[str, Any],
    now: datetime = NOW,
    provider: str = "claude_code",
    profile: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": provider if profile is None else f"profile:{profile}:x",
        "provider": provider,
        "profile": profile,
        "status": "available",
        "observed_at": (now - timedelta(seconds=30)).isoformat(),
        "windows": list(windows),
        **extra,
    }


class Desktop:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def __call__(self, title: str, body: str) -> None:
        self.sent.append((title, body))


async def _check(
    tmp_path: Path, desktop: Desktop, *accounts: dict[str, Any], now: datetime = NOW
) -> list[dict[str, Any]]:
    return await check_limit_alerts(tmp_path, accounts=list(accounts), desktop=desktop, now=now)


def _phone(tmp_path: Path) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], PhoneAlertStore(tmp_path).after(0)["alerts"])


async def test_one_alert_per_crossing_names_account_window_and_reset(tmp_path: Path) -> None:
    desktop = Desktop()
    resets = NOW + timedelta(hours=2)

    def work(used: float) -> dict[str, Any]:
        return _account(_window(used, resets=resets), profile="work")

    for used in (40, 79.9):
        assert await _check(tmp_path, desktop, work(used)) == []
    sent = await _check(tmp_path, desktop, work(81))
    assert [alert["threshold"] for alert in sent] == [80]
    title, body = desktop.sent[0]
    assert title == "Claude Code (work) is at 81% of its 5-hour limit"
    assert "used 81% of its 5-hour window, past your 80% alert" in body
    assert body.endswith(reset_text(resets.isoformat(), NOW))
    assert "resets" in body
    # Later readings in the same window stay quiet until the next level.
    for used in (84, 90, 94.9):
        assert await _check(tmp_path, desktop, work(used)) == []
    sent = await _check(tmp_path, desktop, work(96))
    assert [alert["threshold"] for alert in sent] == [95]
    assert await _check(tmp_path, desktop, work(100)) == []
    assert len(desktop.sent) == 2
    # The same notices reach the paired phone through the computer's alert store.
    phone = _phone(tmp_path)
    assert [(a["title"], a["body"]) for a in phone] == desktop.sent
    assert {a["source"] for a in phone} == {"usage-limit"}
    assert {a["workflow_name"] for a in phone} == {"Usage alerts"}


async def test_a_jump_past_both_levels_sends_one_alert(tmp_path: Path) -> None:
    desktop = Desktop()
    resets = NOW + timedelta(days=3)
    weekly = _window(97, resets=resets, window_id="seven_day")
    sent = await _check(tmp_path, desktop, _account(weekly))
    assert [(a["threshold"], a["kind"]) for a in sent] == [(95, "weekly")]
    assert desktop.sent[0][0] == "Claude Code is at 97% of its weekly limit"
    with usage_db(tmp_path) as db:
        levels = [row[0] for row in db.execute("SELECT threshold FROM usage_limit_alerts")]
    assert sorted(levels) == [80, 95]
    assert await _check(tmp_path, desktop, _account(weekly)) == []


async def test_each_account_and_window_alerts_on_its_own(tmp_path: Path) -> None:
    desktop = Desktop()
    resets = NOW + timedelta(hours=1)
    accounts = [
        _account(_window(85, resets=resets), profile="work"),
        _account(_window(85, resets=resets), profile="home"),
        _account(
            _window(85, resets=resets),
            _window(85, resets=resets + timedelta(days=4), window_id="seven_day"),
        ),
    ]
    sent = await _check(tmp_path, desktop, *accounts)
    assert sorted((a["account"], a["kind"]) for a in sent) == [
        ("Claude Code", "5-hour"),
        ("Claude Code", "weekly"),
        ("Claude Code (home)", "5-hour"),
        ("Claude Code (work)", "5-hour"),
    ]
    assert await _check(tmp_path, desktop, *accounts) == []


async def test_alerted_state_survives_a_restart(tmp_path: Path) -> None:
    resets = NOW + timedelta(hours=3)
    assert len(await _check(tmp_path, Desktop(), _account(_window(88, resets=resets)))) == 1
    # A new process reads the same database: nothing in memory carries over.
    later = NOW + timedelta(minutes=20)
    again = _account(_window(89, resets=resets), now=later)
    assert await _check(tmp_path, Desktop(), again, now=later) == []


async def test_a_reset_window_re_arms_its_levels(tmp_path: Path) -> None:
    desktop = Desktop()
    first = NOW + timedelta(hours=1)
    assert len(await _check(tmp_path, desktop, _account(_window(90, resets=first)))) == 1
    # Past the reset: a new window crossing 80% alerts again.
    after = first + timedelta(minutes=30)
    fresh = _account(_window(50, resets=after + timedelta(hours=4)), now=after)
    assert await _check(tmp_path, desktop, fresh, now=after) == []
    crossing = _account(_window(82, resets=after + timedelta(hours=4)), now=after)
    assert [a["threshold"] for a in await _check(tmp_path, desktop, crossing, now=after)] == [80]
    assert len(desktop.sent) == 2


async def test_a_reset_time_that_shifts_slightly_does_not_re_alert(tmp_path: Path) -> None:
    resets = NOW + timedelta(hours=1)
    assert len(await _check(tmp_path, Desktop(), _account(_window(85, resets=resets)))) == 1
    # Polled and activity readings can round the same reset time differently.
    nudged = _account(_window(86, resets=resets + timedelta(seconds=40)))
    assert await _check(tmp_path, Desktop(), nudged) == []


async def test_without_a_reset_time_a_drop_under_the_level_re_arms(tmp_path: Path) -> None:
    desktop = Desktop()
    assert len(await _check(tmp_path, desktop, _account(_window(85, resets=None)))) == 1
    assert await _check(tmp_path, desktop, _account(_window(86, resets=None))) == []
    assert await _check(tmp_path, desktop, _account(_window(10, resets=None))) == []
    assert len(await _check(tmp_path, desktop, _account(_window(81, resets=None)))) == 1


async def test_accounts_that_need_sign_in_never_alert(tmp_path: Path) -> None:
    desktop = Desktop()
    full = _window(99, resets=NOW + timedelta(hours=1))
    signed_out = [
        _account(full, profile="work", status="auth_required", needs_sign_in=True),
        _account(full, profile="home", status="auth_required"),
        # Newer turn activity can mark the row available; it still needs sign-in.
        _account(full, profile="old", needs_sign_in=True),
    ]
    assert await _check(tmp_path, desktop, *signed_out) == []
    assert desktop.sent == [] and _phone(tmp_path) == []


async def test_stale_unknown_and_other_windows_never_alert(tmp_path: Path) -> None:
    desktop = Desktop()
    resets = NOW + timedelta(hours=1)
    old = _account(_window(99, resets=resets))
    old["observed_at"] = (NOW - timedelta(hours=1)).isoformat()
    stale = _account(_window(99, resets=resets), status="stale")
    past_reset = _account(_window(99, resets=NOW - timedelta(minutes=1)))
    monthly = _account(
        _window(99, resets=resets, window_id="plan", label="Included usage · billing cycle")
    )
    no_percent = _account({"id": "five_hour", "label": "Five-hour allowance", "unit": "percent"})
    for account in (old, stale, past_reset, monthly, no_percent):
        assert await _check(tmp_path, desktop, account) == []
    assert desktop.sent == []


async def test_switch_and_levels_are_saved_and_checked(tmp_path: Path) -> None:
    assert alert_settings(tmp_path)["enabled"] is True
    assert alert_settings(tmp_path)["thresholds"] == [80, 95]
    resets = NOW + timedelta(hours=1)
    save_alert_settings(tmp_path, enabled=False)
    assert await _check(tmp_path, Desktop(), _account(_window(99, resets=resets))) == []
    saved = save_alert_settings(tmp_path, enabled=True, thresholds=[50, 70])
    assert saved["enabled"] is True and saved["thresholds"] == [50, 70]
    sent = await _check(tmp_path, Desktop(), _account(_window(60, resets=resets)))
    assert [a["threshold"] for a in sent] == [50]
    for bad in ([95, 80], [80, 80], [0, 50], [50, 101], [80.5, 95], [80], "80,95", [True, 95]):
        with pytest.raises(UsageAlertError):
            save_alert_settings(tmp_path, thresholds=bad)
    with pytest.raises(UsageAlertError):
        save_alert_settings(tmp_path, enabled="yes")
    assert alert_settings(tmp_path)["thresholds"] == [50, 70]
    # A damaged saved value falls back to the defaults instead of failing.
    with usage_db(tmp_path) as db:
        db.execute(
            "UPDATE usage_meta SET value=? WHERE key='limit_alerts'",
            (json.dumps({"enabled": "x", "thresholds": [3]}),),
        )
    assert alert_settings(tmp_path)["enabled"] is True
    assert alert_settings(tmp_path)["thresholds"] == [80, 95]


async def test_a_failed_desktop_notice_still_reaches_the_phone(tmp_path: Path) -> None:
    async def no_desktop(title: str, body: str) -> None:
        raise RuntimeError("Desktop notifications require DISPLAY")

    account = _account(_window(90, resets=NOW + timedelta(hours=1)))
    sent = await check_limit_alerts(tmp_path, accounts=[account], desktop=no_desktop, now=NOW)
    assert sent[0]["desktop"] is False and sent[0]["phone"] is True
    assert len(_phone(tmp_path)) == 1


async def test_refresh_sends_alerts_for_new_readings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.core import provider_usage

    shown: list[str] = []

    async def desktop(title: str, body: str) -> None:
        shown.append(title)

    used = {"value": 50.0}
    resets = (datetime.now(UTC) + timedelta(hours=2)).isoformat()

    async def adapter(provider: str, profile: str | None, **kwargs: Any) -> dict[str, Any]:
        if provider != "codex":
            return {"status": "configuration_required", "windows": []}
        return {
            "provider": provider,
            "status": "available",
            "observed_at": datetime.now(UTC).isoformat(),
            "windows": [
                {
                    "id": "codex:primary",
                    "label": "Codex · primary · 5-hour",
                    "unit": "percent",
                    "used": used["value"],
                    "limit": 100.0,
                    "resets_at": resets,
                }
            ],
        }

    monkeypatch.setattr(provider_usage, "refresh_provider_usage", adapter)
    monkeypatch.setattr(usage_alerts, "_desktop_notice", desktop)
    await refresh_usage(tmp_path, min_age=0)
    assert shown == []
    used["value"] = 83
    await refresh_usage(tmp_path, min_age=0)
    assert shown == ["Codex is at 83% of its 5-hour limit"]
    await refresh_usage(tmp_path, min_age=0)
    assert len(shown) == 1


def test_window_kinds() -> None:
    assert window_kind({"id": "five_hour"}) == "5-hour"
    assert window_kind({"id": "seven_day"}) == "weekly"
    assert window_kind({"id": "seven_day_opus", "label": "Weekly Opus allowance"}) == "weekly"
    assert window_kind({"id": "codex:primary", "label": "Codex · primary · 5-hour"}) == "5-hour"
    assert window_kind({"id": "codex:secondary", "label": "Codex · secondary · 7-day"}) == "weekly"
    assert window_kind({"id": "openai:primary_window", "label": "ChatGPT · 5-hour allowance"}) == (
        "5-hour"
    )
    assert window_kind({"id": "plan", "label": "Included usage · billing cycle"}) is None
    assert window_kind({"id": "codex:credits", "label": "Codex · credits"}) is None
    assert window_kind({"id": "extra_usage", "label": "Monthly extra usage"}) is None


def test_reset_text_reads_in_local_time() -> None:
    assert reset_text("") == "Its reset time wasn't reported."
    soon = (NOW + timedelta(minutes=5)).astimezone()
    text = reset_text(soon.isoformat(), NOW)
    hour = soon.hour % 12 or 12
    assert f"{hour}:{soon.minute:02d}" in text
    assert text.startswith(("It resets today at", "It resets tomorrow at"))


def test_alert_settings_api(tmp_path: Path) -> None:
    from tests.unit.test_ui_server import _request

    url = "/api/usage/alerts"
    assert _request(tmp_path, "GET", url, authenticated=False).status == 401
    assert _request(tmp_path, "POST", url, body={}, authenticated=False).status == 401
    read = _request(tmp_path, "GET", url)
    assert read.status == 200
    assert cast(dict[str, Any], read.json())["thresholds"] == [80, 95]
    saved = _request(tmp_path, "POST", url, body={"enabled": False, "thresholds": [70, 90]})
    assert saved.status == 200
    assert cast(dict[str, Any], saved.json())["enabled"] is False
    bad = _request(tmp_path, "POST", url, body={"thresholds": [90, 70]})
    assert bad.status == 400
    assert "higher" in cast(dict[str, Any], bad.json())["error"]
    assert _request(tmp_path, "POST", url, body=b"{not json").status == 400
    final = cast(dict[str, Any], _request(tmp_path, "GET", url).json())
    assert final["enabled"] is False and final["thresholds"] == [70, 90]
