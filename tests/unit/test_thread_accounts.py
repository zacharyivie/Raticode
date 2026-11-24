"""A thread's own account, chosen in the model picker's Account menu.

The choice is kept per provider on the thread. It wins over Rem's setting, "@default"
names the provider's default sign-in, and an empty choice follows Rem's setting. The
desktop window sends it as providerProfile; turns sent from the phone resolve it from
the shared thread's context.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gofer.core import provider_accounts as accounts
from gofer.ui import rem_provider_accounts as rem_accounts
from gofer.ui.rem_provider_accounts import (
    DEFAULT_SIGN_IN,
    account_menus,
    clean_thread_accounts,
    thread_account,
    turn_account,
    valid_thread_choice,
)
from tests.unit.test_device_application import application as application
from tests.unit.test_device_application import event
from tests.unit.test_device_desktop_control import phone as phone
from tests.unit.test_device_parity import parity

STATES: dict[str, str] = {}


def _status(profile: Any, **_: Any) -> dict[str, Any]:
    return {"state": STATES.get(profile.name, accounts.SIGNED_IN), "plan": None}


@pytest.fixture
def codex_accounts(tmp_path: Path) -> Iterator[Path]:
    """A stand-in Codex adapter whose sign-in states the test sets in STATES."""
    real = accounts.account_adapter("codex")
    accounts.register_adapter(
        accounts.AccountAdapter(
            provider="codex",
            label="Codex",
            cli_name="codex",
            folder_env="CODEX_HOME",
            sign_in_args=("login",),
            status_args=("login", "status"),
            read_status=lambda output: {"state": accounts.SIGNED_IN},
            status_check=_status,
        )
    )
    STATES.clear()
    data_dir = tmp_path / "data"
    try:
        yield data_dir
    finally:
        accounts.unregister_adapter("codex")
        # Put the real adapter back, or later tests in this process lose Codex accounts.
        if real is not None:
            accounts.register_adapter(real)
        STATES.clear()


def test_thread_choices_are_rem_setting_default_sign_in_or_an_account() -> None:
    assert valid_thread_choice("") and valid_thread_choice(DEFAULT_SIGN_IN)
    assert valid_thread_choice("work.2")
    for bad in ("@other", "-work", "a/b", "x" * 129, None, 3):
        assert not valid_thread_choice(bad)
    assert clean_thread_accounts(
        {"codex": "work", "claude_code": "", "Bad-Id": "x", "grok": "@nope", "cursor": 1}
    ) == {"codex": "work"}
    assert clean_thread_accounts(["codex"]) == {}
    # "@default" can never be an account's name, so it can't be mistaken for one.
    with pytest.raises(accounts.AccountError):
        accounts.create_account(DEFAULT_SIGN_IN, provider="claude_code")


def test_the_threads_choice_wins_over_rems_setting(codex_accounts: Path) -> None:
    data_dir = codex_accounts
    accounts.create_account("work", provider="codex", data_dir=data_dir)
    accounts.create_account("spare", provider="codex", data_dir=data_dir)
    setting = {"provider_accounts": {"codex": "work"}}

    assert turn_account("t1", "", setting, data_dir, provider="codex") == "work"
    assert turn_account("t1", "spare", setting, data_dir, provider="codex") == "spare"
    assert turn_account("t1", DEFAULT_SIGN_IN, setting, data_dir, provider="codex") is None
    assert turn_account("t1", "", {}, data_dir, provider="codex") is None
    # A provider without accounts never names one, whatever was saved.
    assert turn_account("t1", "spare", setting, data_dir, provider="opencode") is None
    # The chat endpoint gets "@default" as providerProfile from the window.
    assert thread_account("t1", DEFAULT_SIGN_IN, data_dir, provider="codex") is None


def test_account_menus_list_state_and_last_reading(
    codex_accounts: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = codex_accounts
    accounts.create_account("work", provider="codex", data_dir=data_dir)
    accounts.create_account("old", provider="codex", data_dir=data_dir)
    STATES["old"] = accounts.NEEDS_SIGN_IN
    reading = {"seven_day": 42.0, "five_hour": 10.0, "observed_at": "2026-11-17T10:00:00+00:00"}
    monkeypatch.setattr(
        accounts,
        "last_reading",
        lambda profile, data_dir=None: reading if profile.name == "work" else None,
    )

    menus = account_menus({"provider_accounts": {"codex": "auto"}}, data_dir)
    codex = next(menu for menu in menus if menu["provider"] == "codex")
    assert codex["label"] == "Codex"
    assert codex["rem_setting"] == "auto" and codex["rem_setting_label"] == "Choose by usage"
    by_name = {item["name"]: item for item in codex["accounts"]}
    assert by_name["work"] == {
        "name": "work",
        "state": accounts.SIGNED_IN,
        "needs_sign_in": False,
        "last_reading": reading,
        # This stand-in adapter has no device sign-in, and none is running.
        "phone_sign_in": False,
        "sign_in": None,
    }
    assert by_name["old"]["needs_sign_in"] is True and by_name["old"]["last_reading"] is None
    # A provider with no accounts gets no menu.
    assert all(menu["provider"] != "claude_code" for menu in menus)


def test_last_reading_is_none_without_readings(codex_accounts: Path) -> None:
    profile = accounts.create_account("fresh", provider="codex", data_dir=codex_accounts)
    assert accounts.last_reading(profile, codex_accounts) is None


def test_phone_sets_and_sends_the_threads_account(
    application, codex_accounts: Path, tmp_path: Path
) -> None:
    app, peer = application
    data_dir = codex_accounts
    app.remote.data_dir = data_dir
    accounts.create_account("work", provider="codex", data_dir=data_dir)
    accounts.create_account("old", provider="codex", data_dir=data_dir)
    STATES["old"] = accounts.NEEDS_SIGN_IN
    _, meta, shared, _ = parity(app, peer, tmp_path)
    thread = shared["thread_id"]
    payload: dict[str, Any] = {
        "expected_revision": 1,
        "project_id": None,
        "resource_ids": [],
        "provider": "codex",
        "model": "model-a",
    }

    def update(revision: int, account: str, sequence: int) -> dict[str, Any]:
        reply: dict[str, Any] = app.handle(
            peer,
            event(
                "thread.context.update",
                sequence=sequence,
                thread_id=thread,
                payload={**payload, "expected_revision": revision, "account": account},
            ),
        )[0]
        return reply

    refused = update(1, "old", 0)
    assert refused["type"] == "error" and "Sign in" in refused["payload"]["detail"]
    assert update(1, "missing", 1)["type"] == "error"
    assert update(1, "work", 2)["type"] == "thread.snapshot"
    exported = app.workspace.export(peer, thread)["metadata"]
    assert exported["accountsByProvider"] == {"codex": "work"}

    # Turns sent from the phone run on it; Rem's setting is only the fallback.
    claimed = None
    assert app.handle(peer, event(thread_id=thread, sequence=3))[0]["type"] == "chat.accepted"
    claimed = app.claim()
    assert claimed["context"]["provider_accounts"] == {"codex": "work"}

    assert update(2, "", 4)["type"] == "thread.snapshot"
    assert app.workspace.export(peer, thread)["metadata"]["accountsByProvider"] == {}
    assert update(3, DEFAULT_SIGN_IN, 5)["type"] == "thread.snapshot"
    assert app.workspace.export(peer, thread)["metadata"]["accountsByProvider"] == {
        "codex": DEFAULT_SIGN_IN
    }
    assert meta["id"]


def test_phone_turn_account_resolution(codex_accounts: Path) -> None:
    from gofer.ui.device_chat import DeviceChatBridge

    data_dir = codex_accounts
    accounts.create_account("work", provider="codex", data_dir=data_dir)
    accounts.create_account("home", provider="codex", data_dir=data_dir)

    class Rem:
        @staticmethod
        def _current_config() -> dict[str, Any]:
            return {"provider_accounts": {"codex": "home"}}

    class Control:
        services = {"rem": Rem()}

    bridge = DeviceChatBridge.__new__(DeviceChatBridge)
    bridge.fleet_control = Control()  # type: ignore[assignment]
    bridge.data_dir = data_dir
    turn = bridge._turn_account
    assert turn("t", {"provider_accounts": {"codex": "work"}}, "codex") == "work"
    assert turn("t", {"provider_accounts": {}}, "codex") == "home"
    assert turn("t", {"provider_accounts": {"codex": DEFAULT_SIGN_IN}}, "codex") is None
    Control.services = {}
    assert turn("t", {}, "codex") is None


def test_phone_reads_account_menus(phone, codex_accounts: Path) -> None:
    app, peer, _, send = phone
    data_dir = codex_accounts
    app.desktop.enable(peer, True)
    app.desktop.rem.data_dir = data_dir
    app.desktop.rem._current_config = lambda: {"provider_accounts": {"codex": "work"}}
    accounts.create_account("work", provider="codex", data_dir=data_dir)
    accounts.create_account("old", provider="codex", data_dir=data_dir)
    STATES["old"] = accounts.NEEDS_SIGN_IN

    assert send("desktop.read", "overview")["value"]["thread_accounts"] is True
    view = send("desktop.read", "provider.accounts")["value"]
    codex = next(item for item in view["providers"] if item["provider"] == "codex")
    assert codex["rem_setting"] == "work" and codex["rem_setting_label"] == "work"
    assert codex["choice"] == ""
    names = {item["name"]: item for item in codex["accounts"]}
    assert names["old"]["needs_sign_in"] is True
    # Nothing about folders, IDs or sign-ins reaches the phone.
    assert "account_id" not in json.dumps(view) and str(data_dir) not in json.dumps(view)
    missing = send(
        "desktop.read", "provider.accounts", {"thread_id": "0" * 8 + "-0000-4000-8000-" + "0" * 12}
    )
    assert missing["ok"] is False and missing["code"] == "not_found"


def test_reading_fields_round_and_skip_unknowns() -> None:
    from gofer.devices.desktop_control import _reading_fields

    assert _reading_fields(None) == {}
    assert _reading_fields({"seven_day": 42.04, "five_hour": None, "observed_at": "t"}) == {
        "week_used": 42.0,
        "observed_at": "t",
    }
    assert _reading_fields({"seven_day": True}) == {}


def test_rem_setting_labels() -> None:
    assert rem_accounts._setting_label("") == "Default sign-in"
    assert rem_accounts._setting_label("auto") == "Choose by usage"
    assert rem_accounts._setting_label("work") == "work"
