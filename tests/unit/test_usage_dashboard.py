from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from gofer.core import usage_service
from gofer.core.usage_ledger import activity_payload, record_invocation, track_chat_usage, usage_db
from gofer.core.usage_service import refresh_usage, usage_overview


def test_ledger_deduplicates_and_keeps_unknowns_and_estimates_separate(tmp_path: Path) -> None:
    for _ in range(2):
        record_invocation(
            invocation_id="same",
            provider="codex",
            data_dir=tmp_path,
            metadata={
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 80,
                "reasoning_tokens": 10,
                "secret": "do not store",
            },
        )
    record_invocation(
        invocation_id="retry",
        provider="codex",
        data_dir=tmp_path,
        metadata={"input_tokens": 10, "output_tokens": 2},
    )
    record_invocation(invocation_id="unknown", provider="copilot", data_dir=tmp_path)
    record_invocation(
        invocation_id="estimate",
        provider="cursor",
        data_dir=tmp_path,
        metadata={"input_tokens": 40, "output_tokens": 10, "estimated": True},
    )
    result = activity_payload(tmp_path)["activity"]
    assert result["calls"] == 4
    assert result["reported_calls"] == 2
    assert result["unknown_calls"] == 1
    assert result["estimated_calls"] == 1
    assert result["total_tokens"] == 132
    assert result["estimated_total_tokens"] == 50
    assert result["providers"][1]["total_tokens"] is None
    with usage_db(tmp_path) as db:
        stored = db.execute("SELECT tokens FROM usage_invocations WHERE id='same'").fetchone()[0]
    assert "secret" not in stored


def test_empty_and_period_filters(tmp_path: Path) -> None:
    assert activity_payload(tmp_path)["activity"]["total_tokens"] is None
    record_invocation(
        invocation_id="old",
        provider="codex",
        data_dir=tmp_path,
        metadata={"input_tokens": 10, "output_tokens": 2},
    )
    with usage_db(tmp_path) as db:
        db.execute("UPDATE usage_invocations SET observed_at='2020-01-01T00:00:00+00:00'")
    assert activity_payload(tmp_path, days=1)["activity"]["calls"] == 0
    with pytest.raises(ValueError):
        usage_overview(tmp_path, days=-1)


async def test_provider_failures_are_independent_and_stale_readings_survive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gofer.core import provider_usage

    calls: list[str] = []

    async def adapter(provider: str, profile: str | None, **kwargs: Any) -> dict[str, Any]:
        calls.append(provider)
        if provider == "copilot":
            raise RuntimeError("sensitive body")
        return {
            "provider": provider,
            "status": "available",
            "observed_at": "2000-01-01",
            "windows": [{"id": "weekly", "remaining_percent": 25}],
        }

    monkeypatch.setattr(provider_usage, "refresh_provider_usage", adapter)
    result = await refresh_usage(tmp_path)
    assert len(calls) == 9
    assert result["accounts"][0]["status"] == "stale"
    assert next(r for r in result["accounts"] if r["provider"] == "copilot")["status"] == "error"
    assert "sensitive body" not in json.dumps(result)
    await refresh_usage(tmp_path)
    assert len(calls) == 9  # minimum polling interval
    with usage_db(tmp_path) as db:
        db.execute("UPDATE usage_snapshots SET checked_at='2000-01-01'")

    async def failing(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise TimeoutError

    monkeypatch.setattr(provider_usage, "refresh_provider_usage", failing)
    result = await refresh_usage(tmp_path)
    assert result["accounts"][0]["windows"][0]["remaining_percent"] == 25
    assert result["accounts"][0]["status"] == "stale"


async def test_concurrent_refresh_is_coalesced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.core import provider_usage

    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def adapter(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return {"status": "unavailable", "windows": []}

    monkeypatch.setattr(provider_usage, "refresh_provider_usage", adapter)
    task = asyncio.create_task(refresh_usage(tmp_path))
    await entered.wait()
    assert (await refresh_usage(tmp_path))["refreshing"] is True
    release.set()
    await task
    assert calls == 9
    assert not usage_service._refreshing


async def test_stream_provider_switches_and_closing_records_usage(tmp_path: Path) -> None:
    @track_chat_usage
    async def stream(provider: str, model: str, data_dir: Path):
        yield {"type": "final", "usage": {"input_tokens": 12, "output_tokens": 3}}

    for provider in ("codex", "claude_code"):
        source = stream(provider, "model", tmp_path)
        await anext(source)
        await source.aclose()
    result = activity_payload(tmp_path)["activity"]
    assert result["calls"] == 2
    assert result["total_tokens"] == 30
    assert {r["provider"] for r in result["providers"]} == {"codex", "claude_code"}


def test_api_headers_are_scoped_to_profile_configuration(tmp_path: Path) -> None:
    from gofer.core.provider_profiles import ProviderProfile, save_provider_profiles

    profile = ProviderProfile(name="api", subscription="openai_api", organization="first")
    save_provider_profiles({"api": profile}, tmp_path)
    record_invocation(
        invocation_id="api-call",
        provider="openai_api",
        profile="api",
        model="model-a",
        data_dir=tmp_path,
        metadata={
            "quota_windows": [
                {
                    "id": "tokens",
                    "label": "Tokens per minute",
                    "unit": "tokens",
                    "remaining": 100,
                    "limit": 1000,
                    "resets_at": "2000-01-01T00:00:00+00:00",
                }
            ]
        },
    )
    row = usage_overview(tmp_path)["accounts"][-1]
    assert row["status"] == "stale"
    assert row["windows"][0]["remaining"] == 100
    assert row["windows"][0]["model"] == "model-a"
    profile.organization = "second"
    save_provider_profiles({"api": profile}, tmp_path)
    assert usage_overview(tmp_path)["accounts"][-1]["windows"] == []


def test_partial_usage_and_invalid_quota_numbers(tmp_path: Path) -> None:
    record_invocation(
        invocation_id="partial",
        provider="claude_code",
        data_dir=tmp_path,
        metadata={
            "input_tokens": 100,
            "output_tokens": 20,
            "partial": True,
            "quota_windows": [
                {
                    "id": "five_hour",
                    "label": "Five hour",
                    "unit": "percent",
                    "remaining": float("inf"),
                    "used": True,
                    "remaining_percent": 110,
                }
            ],
        },
    )
    payload = usage_overview(tmp_path)
    assert payload["activity"]["partial_calls"] == 1
    assert payload["activity"]["reported_calls"] == 0
    assert payload["activity"]["total_tokens"] == 120
    window = next(r for r in payload["accounts"] if r["provider"] == "claude_code")["windows"][0]
    assert window["remaining"] is None
    assert window["used"] is None
    assert window["remaining_percent"] is None
    json.dumps(payload, allow_nan=False)


@pytest.mark.parametrize("mode", ["early_error", "close_error", "cancel"])
async def test_stream_records_even_when_no_output_or_cleanup_fails(
    tmp_path: Path, mode: str
) -> None:
    @track_chat_usage
    async def stream(provider: str, data_dir: Path):
        if mode == "early_error":
            raise ValueError("provider failed")
        try:
            yield {"type": "usage", "usage": {"input_tokens": 12, "output_tokens": 3}}
        finally:
            if mode == "close_error":
                raise ValueError("close failed")

    source = stream("codex", tmp_path)
    try:
        await anext(source)
    except ValueError:
        pass
    try:
        await source.aclose()
    except ValueError:
        pass
    result = activity_payload(tmp_path)["activity"]
    assert result["calls"] == 1
    assert result["unknown_calls"] == (1 if mode == "early_error" else 0)
    assert result["partial_calls"] == (0 if mode == "early_error" else 1)


def test_old_header_replay_does_not_renew_or_replace_limits(tmp_path: Path) -> None:
    def save(identity: str, stamp: str, remaining: int) -> None:
        record_invocation(
            invocation_id=identity,
            provider="openai_api",
            model="model-a",
            data_dir=tmp_path,
            metadata={
                "quota_observed_at": stamp,
                "quota_windows": [
                    {
                        "id": "tokens",
                        "label": "Token window",
                        "unit": "tokens",
                        "remaining": remaining,
                    }
                ],
            },
        )

    save("newer", "2020-02-01T00:00:00+00:00", 10)
    save("older", "2020-01-01T00:00:00+00:00", 100)
    row = next(r for r in usage_overview(tmp_path)["accounts"] if r["provider"] == "openai_api")
    assert row["status"] == "stale"
    assert row["observed_at"] == "2020-02-01T00:00:00+00:00"
    assert row["windows"][0]["remaining"] == 10


def test_polled_reset_does_not_imply_new_balance(tmp_path: Path) -> None:
    from gofer.core.usage_ledger import now_iso

    snapshot = {
        "status": "available",
        "observed_at": now_iso(),
        "windows": [
            {
                "id": "primary",
                "unit": "percent",
                "remaining": 0,
                "resets_at": "2000-01-01T00:00:00+00:00",
            }
        ],
    }
    with usage_db(tmp_path) as db:
        db.execute(
            "INSERT INTO usage_snapshots VALUES (?, ?, ?)",
            ("codex", json.dumps(snapshot), now_iso()),
        )
    row = usage_overview(tmp_path)["accounts"][0]
    assert row["status"] == "stale"
    assert row["windows"][0]["remaining"] == 0
