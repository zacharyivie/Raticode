"""Signing a Codex or Grok account in from a paired phone (provider.account.sign_in).

The phone asks; the desktop runs ``codex login --device-auth`` with the account's
CODEX_HOME (a stand-in codex, see test_codex_accounts.py), and the account's
provider.accounts row carries the link and one-time code Codex printed until the CLI
exits. A Grok account does the same with ``grok login --device-auth`` and its GROK_HOME
(a stand-in grok, see test_grok_accounts.py). Claude Code accounts keep signing in on
the computer.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.core import codex_accounts, grok_accounts
from gofer.core import provider_accounts as accounts
from tests.unit.test_codex_accounts import codex as codex
from tests.unit.test_codex_accounts import data_dir as data_dir
from tests.unit.test_device_application import application as application
from tests.unit.test_device_desktop_control import phone as phone
from tests.unit.test_grok_accounts import grok as grok

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX stand-in CLI")


def _row(send: Any, name: str) -> dict[str, Any]:
    view = send("desktop.read", "provider.accounts")["value"]
    rows = {account["name"]: account for menu in view["providers"] for account in menu["accounts"]}
    row: dict[str, Any] = rows[name]
    return row


def _until(send: Any, name: str, done: Any) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        accounts._status_cache.clear()
        row = _row(send, name)
        if done(row):
            return row
        time.sleep(0.05)
    raise AssertionError(f"{name} never got there: {row}")


@pytest.fixture
def desk(phone, data_dir: Path, codex: Path):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem.data_dir = data_dir
    app.desktop.rem._current_config = lambda: {}
    return app, send


@pytest.fixture
def grok_desk(phone, data_dir: Path, grok: Path):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem.data_dir = data_dir
    app.desktop.rem._current_config = lambda: {}
    return app, send


def test_phone_signs_a_codex_account_in_with_codexs_link_and_code(desk, data_dir: Path) -> None:
    app, send = desk
    accounts.create_account("work", provider="codex", data_dir=data_dir)
    accounts.create_account("laptop", provider="claude_code", data_dir=data_dir)
    assert send("desktop.read", "overview")["value"]["account_sign_in"] is True
    row = _row(send, "work")
    assert row["needs_sign_in"] is True and row["phone_sign_in"] is True
    assert "sign_in" not in row
    # Claude Code accounts aren't offered: the phone keeps "sign in on your computer".
    assert _row(send, "laptop")["phone_sign_in"] is False

    answer = send("desktop.action", "provider.account.sign_in", {"name": "work"})
    assert answer["ok"] is True, answer
    started = answer["value"]
    assert started["provider"] == "codex" and started["name"] == "work"
    assert started["sign_in"]["state"] == "waiting"

    row = _until(send, "work", lambda r: r.get("sign_in", {}).get("code"))
    assert row["sign_in"]["url"] == "https://auth.openai.com/codex/device"
    assert row["sign_in"]["code"] == "WXYZ-12345"
    assert "Settings › Security" in row["sign_in"]["help"]
    # No folder, ID or credential reaches the phone.
    text = json.dumps(row)
    assert str(data_dir) not in text and "account_id" not in text

    (codex_accounts.account_home("work", data_dir) / "approved").write_text("")
    row = _until(send, "work", lambda r: r.get("sign_in", {}).get("state") == "signed_in")
    assert row["sign_in"]["code"] == "" and row["sign_in"]["url"] == ""
    accounts._status_cache.clear()
    assert _row(send, "work")["state"] == "signed_in"


def test_phone_sees_codexs_refusal_and_where_to_turn_device_sign_in_on(
    desk, data_dir: Path
) -> None:
    _, send = desk
    accounts.create_account("off", provider="codex", data_dir=data_dir)
    (codex_accounts.account_home("off", data_dir) / "device-off").write_text("")
    assert send("desktop.action", "provider.account.sign_in", {"name": "off"})["ok"] is True
    row = _until(send, "off", lambda r: r.get("sign_in", {}).get("state") == "failed")
    assert row["sign_in"]["refused"] is True
    assert row["sign_in"]["reason"].startswith(
        "Error logging in with device code: device code login is not enabled"
    )
    assert "chatgpt.com/settings/security" in row["sign_in"]["help"]


def test_phone_cancels_a_sign_in_and_claude_code_accounts_are_refused(desk, data_dir: Path) -> None:
    _, send = desk
    accounts.create_account("work", provider="codex", data_dir=data_dir)
    accounts.create_account("laptop", provider="claude_code", data_dir=data_dir)
    send("desktop.action", "provider.account.sign_in", {"name": "work", "provider": "codex"})
    cancelled = send("desktop.action", "provider.account.sign_in", {"name": "work", "cancel": True})
    assert cancelled["ok"] is True and "sign_in" not in cancelled["value"]
    assert "sign_in" not in _row(send, "work")

    refused = send("desktop.action", "provider.account.sign_in", {"name": "laptop"})
    assert refused["ok"] is False and "on your computer" in refused["detail"]
    # The wrong provider for an account is refused too.
    wrong = send(
        "desktop.action", "provider.account.sign_in", {"name": "work", "provider": "claude_code"}
    )
    assert wrong["ok"] is False


def test_a_sign_in_started_on_the_computer_never_reaches_the_phone(desk, data_dir: Path) -> None:
    _, send = desk
    accounts.create_account("desk", provider="codex", data_dir=data_dir)
    (codex_accounts.account_home("desk", data_dir) / "hold").write_text("")
    accounts.sign_ins.start("desk", data_dir=data_dir)
    # The browser sign-in's page belongs to the computer's own browser.
    assert "sign_in" not in _row(send, "desk")
    accounts.sign_ins.cancel("desk")


def test_usage_cards_say_which_accounts_the_phone_can_sign_in(desk, data_dir: Path) -> None:
    from gofer.core.usage_accounts import with_account_glance

    app, send = desk
    accounts.create_account("work", provider="codex", data_dir=data_dir)
    accounts.create_account("laptop", provider="claude_code", data_dir=data_dir)
    rows = [
        {"provider": "codex", "profile": "work", "label": "Codex", "windows": []},
        {"provider": "claude_code", "profile": "laptop", "label": "Claude Code", "windows": []},
        {"provider": "codex", "profile": "", "label": "Codex", "windows": []},
    ]
    states = {"work": accounts.NEEDS_SIGN_IN, "laptop": accounts.NEEDS_SIGN_IN}
    app.desktop.usage = lambda **_: with_account_glance(
        {"accounts": rows},
        data_dir,
        check=lambda profile, **_: {"state": states[profile.name]},
    )
    cards = send("desktop.read", "usage.overview")["value"]["accounts"]
    assert [(c["profile"], c["phone_sign_in"]) for c in cards] == [
        ("work", True),
        ("laptop", False),
        ("", False),
    ]


def test_phone_signs_a_grok_account_in_with_groks_link_and_code(grok_desk, data_dir: Path) -> None:
    _, send = grok_desk
    accounts.create_account("xai", provider="grok", data_dir=data_dir)
    folder = grok_accounts.account_home("xai", data_dir)
    (folder / "device-wait").write_text("")
    row = _row(send, "xai")
    assert row["needs_sign_in"] is True and row["phone_sign_in"] is True

    answer = send("desktop.action", "provider.account.sign_in", {"name": "xai", "provider": "grok"})
    assert answer["ok"] is True, answer
    assert answer["value"]["provider"] == "grok"
    assert 0 < answer["value"]["sign_in"]["seconds_left"] <= 600

    row = _until(send, "xai", lambda r: r.get("sign_in", {}).get("code"))
    assert row["sign_in"]["url"] == "https://accounts.x.ai/device"
    assert row["sign_in"]["code"] == "ABCD-1234"
    assert "Settings › Providers › Grok" in row["sign_in"]["help"]
    assert str(data_dir) not in json.dumps(row)

    (folder / "approved").write_text("")
    row = _until(send, "xai", lambda r: r.get("sign_in", {}).get("state") == "signed_in")
    accounts._status_cache.clear()
    assert _row(send, "xai")["state"] == "signed_in"


def test_phone_sees_xai_refusing_grok_device_sign_in(grok_desk, data_dir: Path) -> None:
    _, send = grok_desk
    accounts.create_account("xoff", provider="grok", data_dir=data_dir)
    (grok_accounts.account_home("xoff", data_dir) / "device-off").write_text("")
    assert send("desktop.action", "provider.account.sign_in", {"name": "xoff"})["ok"] is True
    row = _until(send, "xoff", lambda r: r.get("sign_in", {}).get("state") == "failed")
    assert row["sign_in"]["refused"] is True
    assert row["sign_in"]["reason"].startswith("Device-code login is not available")
    assert row["sign_in"]["help"] == grok_accounts.DEVICE_SIGN_IN_HELP
