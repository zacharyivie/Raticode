"""Settings > Usage account cards: sign-in state per account and the "Rem uses next" badge."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from gofer.core import claude_accounts as accounts
from gofer.core.usage_accounts import with_account_glance
from gofer.core.usage_service import usage_overview
from gofer.ui.rem_claude_accounts import usage_next_account

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX fake CLI")


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def signed_in(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Stand in for `claude auth status`: the named accounts are signed in."""
    names: set[str] = set()

    def check(
        profile: Any, *, data_dir: Path | None = None, refresh: bool = False
    ) -> dict[str, Any]:
        state = accounts.SIGNED_IN if profile.name in names else accounts.NEEDS_SIGN_IN
        return {"state": state, "checked_at": "2026-11-17T12:00:00+00:00"}

    monkeypatch.setattr(accounts, "check_status", check)
    return names


def _rows(overview: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("profile") or row["provider"]): row
        for row in overview["accounts"]
        if row["provider"] == "claude_code"
    }


def test_each_claude_code_account_card_has_its_own_sign_in_state(
    data_dir: Path, signed_in: set[str]
) -> None:
    for name in ("work", "home"):
        accounts.create_account(name, data_dir=data_dir)
    signed_in.add("work")

    rows = _rows(with_account_glance(usage_overview(data_dir), data_dir))
    assert rows["work"]["sign_in_state"] == "signed_in"
    assert rows["work"]["sign_in_checked_at"] == "2026-11-17T12:00:00+00:00"
    assert rows["home"]["sign_in_state"] == "needs_sign_in"
    # The card keeps its Sign in action whatever the last usage reading said.
    assert rows["home"]["needs_sign_in"] is True
    assert "needs_sign_in" not in rows["work"]
    # The default sign-in has no reading yet, so its state is unknown rather than guessed.
    assert rows["claude_code"]["sign_in_state"] == "unknown"
    # Other providers get no Claude Code sign-in state.
    assert all("sign_in_state" not in row for row in usage_overview(data_dir)["accounts"])


def test_signing_in_again_clears_the_stored_needs_sign_in(
    data_dir: Path, signed_in: set[str]
) -> None:
    accounts.create_account("work", data_dir=data_dir)
    signed_in.add("work")
    stored = {"provider": "claude_code", "profile": "work", "status": "auth_required"}
    row = with_account_glance({"accounts": [{**stored, "needs_sign_in": True}]}, data_dir)
    assert row["accounts"][0]["sign_in_state"] == "signed_in"
    assert "needs_sign_in" not in row["accounts"][0]


def test_default_sign_in_state_follows_its_last_refresh() -> None:
    overview = {
        "accounts": [
            {"provider": "claude_code", "profile": None, "status": "auth_required", "windows": []},
            {"provider": "codex", "profile": None, "status": "available", "windows": [{}]},
        ]
    }
    rows = with_account_glance(overview, None, check=lambda *_a, **_k: {})["accounts"]
    assert rows[0]["sign_in_state"] == "needs_sign_in"
    assert "sign_in_state" not in rows[1]
    stale = {"provider": "claude_code", "status": "stale", "windows": [{"id": "five_hour"}]}
    assert with_account_glance({"accounts": [stale]}, None)["accounts"][0]["sign_in_state"] == (
        "signed_in"
    )


def test_rem_uses_next_badge_marks_only_the_account_pick_account_returns(
    data_dir: Path, signed_in: set[str]
) -> None:
    for name in ("work", "home", "spare"):
        accounts.create_account(name, data_dir=data_dir)
    signed_in.update({"work", "home"})
    auto = {"claude_account": "auto"}

    def choose(_data_dir: Path) -> dict[str, Any]:
        return {
            "name": "home",
            "why": "Most room left: 12% of its weekly allowance used.",
            "skipped": [{"name": "spare", "reason": "needs sign-in"}],
        }

    pick = usage_next_account(auto, data_dir, choose=choose)
    overview = with_account_glance(usage_overview(data_dir), data_dir, rem_pick=pick)
    rows = _rows(overview)
    assert rows["home"]["rem_next"] == {"why": "Most room left: 12% of its weekly allowance used."}
    assert [name for name, row in rows.items() if "rem_next" in row] == ["home"]
    assert overview["rem_claude_account"] == {
        "mode": "auto",
        "name": "home",
        "why": "Most room left: 12% of its weekly allowance used.",
        "skipped": [{"name": "spare", "reason": "needs sign-in"}],
    }
    # No percentage is added across accounts: each row keeps only its own windows.
    assert all(
        not {"total", "combined", "used_percent"} & row.keys() for row in overview["accounts"]
    )


def test_badge_falls_back_to_the_default_sign_in_card_when_no_account_has_room(
    data_dir: Path, signed_in: set[str]
) -> None:
    accounts.create_account("work", data_dir=data_dir)
    pick = usage_next_account({"claude_account": "auto"}, data_dir, choose=lambda _d: None)
    assert pick is not None and pick["name"] == ""
    rows = _rows(with_account_glance(usage_overview(data_dir), data_dir, rem_pick=pick))
    assert "default sign-in" in rows["claude_code"]["rem_next"]["why"]
    assert "rem_next" not in rows["work"]


def test_no_badge_unless_rem_chooses_by_usage(data_dir: Path, signed_in: set[str]) -> None:
    accounts.create_account("work", data_dir=data_dir)
    signed_in.add("work")

    def forbidden(_data_dir: Path) -> dict[str, Any]:
        raise AssertionError("Rem only chooses under 'choose by usage'")

    for setting in ({}, {"claude_account": ""}, {"claude_account": "work"}):
        assert usage_next_account(setting, data_dir, choose=forbidden) is None
    overview = with_account_glance(usage_overview(data_dir), data_dir, rem_pick=None)
    assert "rem_claude_account" not in overview
    assert all("rem_next" not in row for row in overview["accounts"])


def test_rem_pick_uses_real_readings_for_the_badge_reason(
    data_dir: Path, signed_in: set[str]
) -> None:
    from gofer.core.usage_ledger import record_invocation

    for name in ("busy", "roomy"):
        accounts.create_account(name, data_dir=data_dir)
        signed_in.add(name)
    for name, weekly in (("busy", 70), ("roomy", 15)):
        record_invocation(
            invocation_id=f"call-{name}",
            provider="claude_code",
            profile=name,
            model="claude-opus-5-5",
            metadata={
                "input_tokens": 1,
                "output_tokens": 1,
                "quota_windows": [
                    {"id": "seven_day", "label": "Weekly", "unit": "percent", "used": weekly},
                    {"id": "five_hour", "label": "5-hour", "unit": "percent", "used": 5},
                ],
            },
            data_dir=data_dir,
        )
    pick = usage_next_account({"claude_account": "auto"}, data_dir)
    rows = _rows(with_account_glance(usage_overview(data_dir), data_dir, rem_pick=pick))
    assert rows["roomy"]["rem_next"]["why"] == (
        "Most room left: 15% of its weekly allowance used, 5% of its 5-hour allowance used."
    )
    assert "rem_next" not in rows["busy"]
    # Each card's windows are that account's own readings.
    own = {
        name: next(w for w in rows[name]["windows"] if w["id"] == "seven_day")["used"]
        for name in ("busy", "roomy")
    }
    assert own == {"busy": 70, "roomy": 15}
