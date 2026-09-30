from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest

from gofer.core import usage_credentials as credentials
from gofer.core.provider_profiles import ProviderProfile, save_provider_profiles
from gofer.core.usage_ledger import now_iso, record_invocation, usage_db
from gofer.core.usage_service import refresh_usage, usage_overview


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, account):
        return self.values.get(account)

    def put(self, account, value):
        self.values[account] = value

    def delete(self, account):
        self.values.pop(account, None)


@pytest.fixture
def store(monkeypatch):
    store = MemoryStore()
    monkeypatch.setattr(credentials, "_secret_store", lambda: store)
    return store


def test_secret_save_mask_preserve_replace_remove_and_no_disk_leak(tmp_path, store):
    def save(values: dict[str, str], remove: list[str] | None = None) -> dict[str, Any]:
        return credentials.save_usage_credentials("cursor", None, values, remove, data_dir=tmp_path)

    result = save({"admin_api_key": "private-1", "email": "me@example.com"})
    assert result["storage_available"]
    assert result["fields"][0]["configured"]
    assert "value" not in result["fields"][0]
    assert "private-1" not in json.dumps(result)
    revision = credentials.usage_credential_revision("cursor", data_dir=tmp_path)
    save({"admin_api_key": "", "email": "next@example.com"})
    assert credentials.resolve_usage_credentials("cursor", data_dir=tmp_path) == {
        "admin_api_key": "private-1",
        "email": "next@example.com",
    }
    assert credentials.usage_credential_revision("cursor", data_dir=tmp_path) != revision
    save({"admin_api_key": "private-2"})
    assert "private-1" not in json.dumps(store.values)
    result = save({}, ["admin_api_key"])
    assert not result["fields"][0]["configured"]
    assert "private-2" not in json.dumps(store.values)
    save({"email": ""})
    assert credentials.resolve_usage_credentials("cursor", data_dir=tmp_path) == {}
    for file in tmp_path.iterdir():
        if file.is_file():
            assert b"private-" not in file.read_bytes()


def test_profile_provider_and_directory_isolation(tmp_path, store):
    save_provider_profiles(
        {"work": ProviderProfile(name="work", subscription="claude_code")}, tmp_path
    )
    credentials.save_usage_credentials(
        "claude_code", None, {"oauth_token": "personal"}, data_dir=tmp_path
    )
    assert credentials.resolve_usage_credentials("claude_code", "work", data_dir=tmp_path) == {}
    credentials.save_usage_credentials(
        "claude_code", "work", {"oauth_token": "work"}, data_dir=tmp_path
    )
    assert (
        credentials.resolve_usage_credentials("claude_code", data_dir=tmp_path)["oauth_token"]
        == "personal"
    )
    assert (
        credentials.resolve_usage_credentials("claude_code", "work", data_dir=tmp_path)[
            "oauth_token"
        ]
        == "work"
    )
    assert credentials.resolve_usage_credentials("claude_code", data_dir=tmp_path / "other") == {}
    with pytest.raises(credentials.UsageCredentialError, match="does not match"):
        credentials.save_usage_credentials("cursor", "work", {}, data_dir=tmp_path)
    with pytest.raises(credentials.UsageCredentialError, match="does not match"):
        credentials.save_usage_credentials("claude_code", "missing", {}, data_dir=tmp_path)


@pytest.mark.parametrize(
    "values,remove",
    [
        ({"bogus": "secret"}, []),
        ({"oauth_token": 32}, []),
        ([], []),
        ({}, "oauth_token"),
        ({}, ["bogus"]),
        ({"oauth_token": "one\ntwo"}, []),
        ({"oauth_token": "x" * 16385}, []),
        ({"oauth_token": "new"}, ["oauth_token"]),
    ],
)
def test_invalid_credentials_never_reach_store(tmp_path, store, values, remove):
    with pytest.raises(credentials.UsageCredentialError):
        credentials.save_usage_credentials("claude_code", None, values, remove, data_dir=tmp_path)
    assert not store.values


def test_locked_store_has_safe_errors_and_preserves_previous_settings(tmp_path, store, monkeypatch):
    credentials.save_usage_credentials(
        "claude_code", None, {"oauth_token": "old"}, data_dir=tmp_path
    )
    previous = credentials.usage_credential_revision("claude_code", data_dir=tmp_path)

    def locked():
        raise RuntimeError("sensitive keyring diagnostic")

    monkeypatch.setattr(credentials, "_secret_store", locked)
    payload = credentials.usage_credentials_payload("claude_code", data_dir=tmp_path)
    assert payload["storage_available"] is False
    assert "sensitive" not in json.dumps(payload)
    with pytest.raises(credentials.UsageCredentialError, match="OS credential store") as exc:
        credentials.save_usage_credentials(
            "claude_code", None, {"oauth_token": "new"}, data_dir=tmp_path
        )
    assert "sensitive" not in str(exc.value)
    assert credentials.usage_credential_revision("claude_code", data_dir=tmp_path) == previous


async def test_credentials_invalidate_cached_allowance_and_bypass_refresh_throttle(
    tmp_path, store, monkeypatch
):
    from gofer.core import provider_usage

    calls = []

    async def adapter(provider, profile, **kwargs):
        calls.append(provider)
        return {
            "status": "available",
            "observed_at": now_iso(),
            "windows": [{"id": "plan", "remaining_percent": 20}],
        }

    monkeypatch.setattr(provider_usage, "refresh_provider_usage", adapter)
    await refresh_usage(tmp_path)
    credentials.save_usage_credentials(
        "claude_code", None, {"oauth_token": "different-account"}, data_dir=tmp_path
    )
    row = next(
        row for row in usage_overview(tmp_path)["accounts"] if row["provider"] == "claude_code"
    )
    assert row["windows"] == []
    calls.clear()
    await refresh_usage(tmp_path)
    assert calls == ["claude_code"]


async def test_credentials_saved_during_refresh_discard_old_account_result(
    tmp_path, store, monkeypatch
):
    from gofer.core import provider_usage

    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def adapter(provider, profile, **kwargs):
        if provider != "claude_code":
            return {"status": "unavailable", "windows": []}
        config = credentials.resolve_usage_credentials(provider, profile, data_dir=tmp_path)
        seen.append(config.get("oauth_token"))
        if len(seen) == 1:
            entered.set()
            await release.wait()
        return {
            "status": "available",
            "observed_at": now_iso(),
            "windows": [{"id": "plan", "remaining_percent": 80 if config else 20}],
        }

    monkeypatch.setattr(provider_usage, "refresh_provider_usage", adapter)
    task = asyncio.create_task(refresh_usage(tmp_path))
    await entered.wait()
    credentials.save_usage_credentials(
        "claude_code", None, {"oauth_token": "new-account"}, data_dir=tmp_path
    )
    release.set()
    result = await task
    row = next(row for row in result["accounts"] if row["provider"] == "claude_code")
    assert seen == [None, "new-account"]
    assert row["windows"][0]["remaining_percent"] == 80


def test_polled_snapshot_wins_over_partial_events_and_credentials_isolate_accounts(tmp_path, store):
    record_invocation(
        invocation_id="event",
        provider="claude_code",
        data_dir=tmp_path,
        metadata={"quota_windows": [{"id": "five_hour", "unit": "percent", "remaining": 10}]},
    )
    with usage_db(tmp_path) as db:
        db.execute(
            "INSERT INTO usage_snapshots VALUES (?, ?, ?)",
            (
                "claude_code",
                json.dumps(
                    {
                        "status": "available",
                        "observed_at": now_iso(),
                        "windows": [{"id": "weekly", "remaining": 90}],
                    }
                ),
                now_iso(),
            ),
        )

    def account() -> dict[str, Any]:
        return next(
            r for r in usage_overview(tmp_path)["accounts"] if r["provider"] == "claude_code"
        )

    assert account()["windows"][0]["id"] == "weekly"
    credentials.save_usage_credentials(
        "claude_code", None, {"oauth_token": "other"}, data_dir=tmp_path
    )
    assert account()["windows"] == []


def test_credentials_api_auth_masking_validation_and_saved_values(tmp_path, store):
    from tests.unit.test_ui_server import _request

    url = "/api/usage/credentials"
    assert (
        _request(tmp_path, "GET", url + "?provider=claude_code", authenticated=False).status == 401
    )
    assert _request(tmp_path, "POST", url, body={}, authenticated=False).status == 401
    assert _request(tmp_path, "GET", url + "?provider=grok").status == 400
    result = _request(
        tmp_path, "POST", url, body={"provider": "claude_code", "values": {"oauth_token": "hidden"}}
    )
    assert result.status == 200
    assert "hidden" not in result.text()
    read = _request(tmp_path, "GET", url + "?provider=claude_code")
    assert read.status == 200
    assert cast(dict[str, Any], read.json())["fields"][0]["configured"]
    assert "hidden" not in read.text()
    assert _request(tmp_path, "POST", url, body=b'{"values": "hidden", invalid}').status == 400
    assert (
        "hidden"
        not in _request(tmp_path, "POST", url, body=b'{"values": "hidden", invalid}').text()
    )


async def test_activity_limits_keep_updating_after_failed_poll(tmp_path, monkeypatch):
    from gofer.core import provider_usage

    async def unavailable(*args, **kwargs):
        return {"status": "configuration_required", "windows": []}

    monkeypatch.setattr(provider_usage, "refresh_provider_usage", unavailable)
    for value in (10, 20):
        record_invocation(
            invocation_id=str(value),
            provider="openai_api",
            data_dir=tmp_path,
            metadata={"quota_windows": [{"id": "minute", "unit": "tokens", "remaining": value}]},
        )
        row = next(
            r for r in (await refresh_usage(tmp_path))["accounts"] if r["provider"] == "openai_api"
        )
        assert row["windows"][0]["remaining"] == value


def test_usage_credentials_use_a_separate_os_service(monkeypatch):
    from keyring.backends import SecretService

    from gofer.devices.storage import OSSecretStore

    values: dict[tuple[str, str], str] = {}

    class Backend:
        priority = 1

        def get_password(self, service, account):
            return values.get((service, account))

        def set_password(self, service, account, value):
            values[service, account] = value

    monkeypatch.setattr(SecretService, "Keyring", Backend)
    monkeypatch.setattr("gofer.devices.storage.sys.platform", "linux")
    device = OSSecretStore()
    reporting = credentials._secret_store()
    device.put("same-account", "identity")
    reporting.put("same-account", "reporting-key")
    assert device.get("same-account") == "identity"
    assert reporting.get("same-account") == "reporting-key"
