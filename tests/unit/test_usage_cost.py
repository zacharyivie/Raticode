"""Estimated API cost of Raticode's tokens and cache-aware pricing."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.core.api_prices import PRICES, price_for
from gofer.core.usage_cost import cost_groups, estimate_api_cost
from gofer.core.usage_ledger import record_invocation, usage_db
from gofer.core.usage_service import usage_overview
from gofer.subscriptions.usage import normalize_usage, provider_payload_usage

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
OPUS = price_for("claude_code", "claude-opus-5-5")
GPT = price_for("codex", "gpt-6.1-terra")


def _record(data_dir: Path, name: str, provider: str, model: str | None, **tokens: Any) -> None:
    record_invocation(
        invocation_id=name, provider=provider, model=model, metadata=tokens, data_dir=data_dir
    )
    with usage_db(data_dir) as db:
        db.execute(
            "UPDATE usage_invocations SET observed_at=? WHERE id=?",
            ((NOW - timedelta(hours=1)).isoformat(), name),
        )


def _estimate(data_dir: Path) -> dict[str, Any]:
    with usage_db(data_dir) as db:
        groups = cost_groups(db, NOW - timedelta(days=30), NOW)
    return estimate_api_cost(groups)


def _account(cost: dict[str, Any], provider: str) -> dict[str, Any]:
    found: dict[str, Any] = next(a for a in cost["providers"] if a["provider"] == provider)
    return found


def _usd(tokens: int, per_million: float) -> float:
    return tokens * per_million / 1_000_000


def test_claude_codes_result_keeps_its_one_hour_cache_writes(tmp_path: Path) -> None:
    # The shape Claude Code's stream-json result event had on 2026-11-17.
    result = {
        "type": "result",
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 10327,
            "cache_read_input_tokens": 11382,
            "output_tokens": 4,
            "cache_creation": {"ephemeral_1h_input_tokens": 10327, "ephemeral_5m_input_tokens": 0},
        },
    }
    usage = provider_payload_usage("claude_code", [result])
    assert usage["input_tokens"] == 2 + 10327 + 11382
    assert usage["cache_read_tokens"] == 11382
    assert usage["cache_write_tokens"] == 10327
    assert usage["cache_write_1h_tokens"] == 10327
    assert "cache_creation" not in usage
    # Normalizing again (as the ledger path does) neither drops nor doubles anything.
    assert normalize_usage("claude_code", usage)["input_tokens"] == usage["input_tokens"]
    record_invocation(
        invocation_id="c1", provider="claude_code", model="haiku", metadata=usage, data_dir=tmp_path
    )
    with usage_db(tmp_path) as db:
        stored = json.loads(db.execute("SELECT tokens FROM usage_invocations").fetchone()[0])
    assert stored["cache_write_1h_tokens"] == 10327
    # The history's totals keep their fields; the one-hour part is a breakdown only.
    overview = usage_overview(tmp_path, period="24h")
    assert "cache_write_1h_tokens" not in overview["activity"]
    assert overview["activity"]["total_tokens"] == usage["total_tokens"]


def test_codex_cache_write_field_is_read() -> None:
    usage = normalize_usage(
        "codex",
        {
            "input_tokens": 1000,
            "cached_input_tokens": 600,
            "cache_write_input_tokens": 100,
            "output_tokens": 50,
        },
    )
    assert usage["cache_read_tokens"] == 600
    assert usage["cache_write_tokens"] == 100
    assert usage["input_tokens"] == 1000


def test_cache_reads_writes_and_cold_input_are_priced_apart(tmp_path: Path) -> None:
    assert OPUS is not None and GPT is not None
    _record(
        tmp_path,
        "claude",
        "claude_code",
        "claude-opus-5-5",
        input_tokens=1_000_000,
        cache_read_tokens=700_000,
        cache_write_tokens=200_000,
        cache_write_1h_tokens=150_000,
        output_tokens=10_000,
    )
    _record(
        tmp_path,
        "codex",
        "codex",
        "gpt-6.1-terra",
        input_tokens=500_000,
        cache_read_tokens=400_000,
        output_tokens=20_000,
    )
    cost = _estimate(tmp_path)
    claude, codex = _account(cost, "claude_code"), _account(cost, "codex")
    expected_claude = {
        "input": _usd(100_000, OPUS.input),
        "cache_read": _usd(700_000, OPUS.cache_read_price),
        "cache_write": _usd(50_000, OPUS.cache_write_price)
        + _usd(150_000, OPUS.cache_write_long_price),
        "output": _usd(10_000, OPUS.output),
    }
    assert claude["breakdown"] == pytest.approx(expected_claude)
    assert claude["cost_usd"] == pytest.approx(sum(expected_claude.values()))
    assert codex["breakdown"] == pytest.approx(
        {
            "input": _usd(100_000, GPT.input),
            "cache_read": _usd(400_000, GPT.cache_read_price),
            "cache_write": 0.0,
            "output": _usd(20_000, GPT.output),
        }
    )
    assert cost["total"]["cost_usd"] == pytest.approx(claude["cost_usd"] + codex["cost_usd"])
    assert cost["total"]["lower_bound"] is False
    assert cost["total"]["unsplit_calls"] == 0
    # Cached input costs less than the same input uncached.
    assert claude["cost_usd"] < _usd(1_000_000, OPUS.input) + _usd(10_000, OPUS.output)


def test_writes_without_a_lifetime_use_the_providers_usual_one(tmp_path: Path) -> None:
    assert OPUS is not None
    for name, provider in (("claude", "claude_code"), ("api", "anthropic_api")):
        _record(
            tmp_path,
            name,
            provider,
            "claude-opus-5-5",
            input_tokens=100_000,
            cache_read_tokens=0,
            cache_write_tokens=100_000,
            output_tokens=0,
        )
    cost = _estimate(tmp_path)
    claude, api = _account(cost, "claude_code"), _account(cost, "anthropic_api")
    # Claude Code writes one-hour entries; the API's default is five minutes.
    assert claude["breakdown"]["cache_write"] == pytest.approx(
        _usd(100_000, OPUS.cache_write_long_price)
    )
    assert api["breakdown"]["cache_write"] == pytest.approx(_usd(100_000, OPUS.cache_write_price))


def test_input_without_a_cache_split_is_priced_as_uncached(tmp_path: Path) -> None:
    assert OPUS is not None
    # Older stopped Claude Code turns saved input and output only.
    _record(tmp_path, "old", "claude_code", "claude-opus-5-5", input_tokens=40_000, output_tokens=0)
    account = _estimate(tmp_path)["providers"][0]
    assert account["unsplit_calls"] == 1
    assert account["breakdown"]["input"] == pytest.approx(_usd(40_000, OPUS.input))
    assert account["breakdown"]["cache_read"] == 0


def test_unknown_models_and_providers_are_unknown_never_zero(tmp_path: Path) -> None:
    _record(tmp_path, "a", "claude_code", "cli-default", input_tokens=5_000, output_tokens=10)
    _record(tmp_path, "b", "copilot", "auto", input_tokens=5_000, output_tokens=10)
    _record(tmp_path, "c", "codex", None, input_tokens=5_000, output_tokens=10)
    cost = _estimate(tmp_path)
    for account in cost["providers"]:
        assert account["cost_usd"] is None
        assert account["breakdown"] is None
        assert account["unpriced_calls"] == 1
    assert _account(cost, "claude_code")["unpriced_models"] == ["cli-default"]
    assert _account(cost, "copilot")["unpriced_models"] == ["auto"]
    assert _account(cost, "codex")["unpriced_models"] == ["model not recorded"]
    assert cost["total"]["cost_usd"] is None
    # One priced call makes the total known, but only as a floor.
    _record(tmp_path, "d", "claude_code", "claude-opus-5-5", input_tokens=1000, output_tokens=1)
    total = _estimate(tmp_path)["total"]
    assert total["cost_usd"] is not None and total["cost_usd"] > 0
    assert total["lower_bound"] is True
    assert total["unpriced_calls"] == 3


def test_calls_without_tokens_and_estimates_are_left_out_as_a_floor(tmp_path: Path) -> None:
    _record(tmp_path, "a", "claude_code", "claude-opus-5-5", input_tokens=1000, output_tokens=1)
    _record(tmp_path, "b", "claude_code", "claude-opus-5-5")
    _record(
        tmp_path,
        "c",
        "claude_code",
        "claude-opus-5-5",
        input_tokens=9_000_000,
        output_tokens=9,
        estimated=True,
    )
    account = _estimate(tmp_path)["providers"][0]
    assert account["no_token_calls"] == 1
    assert account["estimated_calls"] == 1
    assert account["priced_calls"] == 1
    assert account["lower_bound"] is True
    assert OPUS is not None
    assert account["cost_usd"] == pytest.approx(
        _usd(1000, OPUS.input) + _usd(1, OPUS.output), rel=1e-6
    )


def test_model_names_resolve_aliases_contexts_dates_and_effort() -> None:
    assert price_for("claude_code", "claude-opus-5-5[1m]") is OPUS
    assert price_for("claude_code", "CLAUDE-OPUS-5-5") is OPUS
    assert price_for("opencode", "anthropic/claude-opus-5-5") is OPUS
    haiku = price_for("claude_code", "claude-haiku-4-5-20251001")
    assert haiku is not None and haiku.model == "claude-haiku-4-5"
    assert price_for("claude_code", "cli-default") is None
    assert price_for("copilot", "gpt-6.1-terra") is None
    assert price_for("claude_code", None) is None
    assert price_for("unknown_provider", "claude-opus-5-5") is None


def test_every_price_row_is_complete_dated_and_sourced() -> None:
    seen = set()
    for row in PRICES:
        assert (row.vendor, row.model) not in seen
        seen.add((row.vendor, row.model))
        assert row.source.startswith("https://")
        datetime.strptime(row.checked, "%Y-%m-%d")
        assert row.input >= 0 and row.output >= 0
        for optional in (row.cache_read, row.cache_write, row.cache_write_long):
            assert optional is None or optional >= 0
        # A cache read never costs more than uncached input.
        assert row.cache_read_price <= row.input


def test_overview_carries_the_estimate_without_plan_prices(tmp_path: Path) -> None:
    record_invocation(
        invocation_id="x",
        provider="claude_code",
        model="claude-opus-5-5",
        metadata={"input_tokens": 2000, "cache_read_tokens": 1000, "output_tokens": 10},
        data_dir=tmp_path,
    )
    # A plan-price file from an earlier build is left on disk as it was and never read.
    old_plans = tmp_path / "usage-plans.json"
    old_plans.write_text('{"version": 1, "plans": {"claude_code": {"monthly_usd": 100}}}')
    before = old_plans.read_bytes()
    cost = usage_overview(tmp_path, period="7d")["api_cost"]
    assert cost["total"]["cost_usd"] > 0
    assert not {"savings", "plans", "plan_providers"} & cost.keys()
    assert old_plans.read_bytes() == before
    assert cost["prices"] and all(row["source"] for row in cost["prices"])
    # A second read with nothing new reuses the kept sums and gives the same answer.
    again = usage_overview(tmp_path, period="7d")["api_cost"]
    assert again["total"] == cost["total"]
    record_invocation(
        invocation_id="y",
        provider="claude_code",
        model="claude-opus-5-5",
        metadata={"input_tokens": 2000, "output_tokens": 10},
        data_dir=tmp_path,
    )
    after = usage_overview(tmp_path, period="7d")["api_cost"]
    assert after["total"]["calls"] == 2
    assert after["total"]["unsplit_calls"] == 1


def test_a_model_without_a_cache_has_nothing_to_split(tmp_path: Path) -> None:
    jev = price_for("jev", "jev-1.14.0")
    assert jev is not None and not jev.caches
    _record(tmp_path, "j", "jev", "jev-1.14.0", input_tokens=1_000_000, output_tokens=3)
    account = _account(_estimate(tmp_path), "jev")
    assert account["unsplit_calls"] == 0
    assert account["cost_usd"] == pytest.approx(_usd(1_000_000, jev.input))


def test_codex_cached_input_is_inside_input_tokens_and_priced_once(tmp_path: Path) -> None:
    # A real `token_count` total from a Codex 0.156.1 session log (2026-11-11):
    # total_tokens = input + output, so the cached tokens are part of input_tokens.
    exec_total = {
        "input_tokens": 17491,
        "cached_input_tokens": 9088,
        "cache_write_input_tokens": 0,
        "output_tokens": 157,
        "reasoning_output_tokens": 0,
        "total_tokens": 17648,
    }
    usage = normalize_usage("codex", exec_total)
    assert usage["input_tokens"] == 17491  # not 17491 + 9088
    assert usage["cache_read_tokens"] == 9088
    assert usage["cache_write_tokens"] == 0
    assert usage["total_tokens"] == 17648
    _record(tmp_path, "exec", "codex", "gpt-6.1-terra", **usage)
    assert GPT is not None
    breakdown = _account(_estimate(tmp_path), "codex")["breakdown"]
    # Dollar figures are kept to a millionth of a dollar.
    assert breakdown["input"] == pytest.approx(_usd(17491 - 9088, GPT.input), abs=1e-6)
    assert breakdown["cache_read"] == pytest.approx(_usd(9088, GPT.cache_read_price), abs=1e-6)
    assert breakdown["output"] == pytest.approx(_usd(157, GPT.output), abs=1e-6)


def test_codex_app_server_cache_writes_are_priced_as_writes(tmp_path: Path) -> None:
    # Codex's app-server reports the same total in camelCase (thread/tokenUsage/updated).
    total = {
        "inputTokens": 100_000,
        "cachedInputTokens": 60_000,
        "cacheWriteInputTokens": 30_000,
        "outputTokens": 1_000,
        "reasoningOutputTokens": 0,
        "totalTokens": 101_000,
    }
    usage = normalize_usage("codex", total)
    assert usage["input_tokens"] == 100_000
    assert usage["cache_read_tokens"] == 60_000
    assert usage["cache_write_tokens"] == 30_000
    _record(tmp_path, "app", "codex", "gpt-6.1-terra", **usage)
    assert GPT is not None and GPT.cache_write is not None
    breakdown = _account(_estimate(tmp_path), "codex")["breakdown"]
    # Only 10,000 tokens were neither read from nor written to the cache.
    assert breakdown["input"] == pytest.approx(_usd(10_000, GPT.input))
    assert breakdown["cache_write"] == pytest.approx(_usd(30_000, GPT.cache_write))
    assert breakdown["cache_read"] == pytest.approx(_usd(60_000, GPT.cache_read_price))


def test_openai_usage_details_split_reads_writes_and_long_writes(tmp_path: Path) -> None:
    # The example in OpenAI's prompt-caching guide: 12000 - 4000 - 7000 = 1000 ordinary.
    usage = normalize_usage(
        "openai_api",
        {
            "input_tokens": 12000,
            "input_tokens_details": {
                "cached_tokens": 4000,
                "cache_write_tokens": 7000,
                "cache_write_tokens_details": {"ttl_30m_tokens": 5000, "ttl_12h_tokens": 2000},
            },
            "output_tokens": 100,
        },
    )
    assert usage["cache_read_tokens"] == 4000
    assert usage["cache_write_tokens"] == 7000
    assert usage["cache_write_1h_tokens"] == 2000
    _record(tmp_path, "api", "openai_api", "gpt-6.1-terra", **usage)
    assert GPT is not None and GPT.cache_write is not None and GPT.cache_write_long
    breakdown = _account(_estimate(tmp_path), "openai_api")["breakdown"]
    assert breakdown["input"] == pytest.approx(_usd(1000, GPT.input))
    assert breakdown["cache_write"] == pytest.approx(
        _usd(5000, GPT.cache_write) + _usd(2000, GPT.cache_write_long)
    )


def test_a_cli_default_call_records_the_model_the_provider_named(tmp_path: Path) -> None:
    # Claude Code names the model that answered even when Raticode let it choose.
    record_invocation(
        invocation_id="d",
        provider="claude_code",
        model="cli-default",
        metadata={"model": "claude-opus-5-5", "input_tokens": 10, "output_tokens": 1},
        data_dir=tmp_path,
    )
    # A model Raticode chose is kept as chosen.
    record_invocation(
        invocation_id="s",
        provider="claude_code",
        model="sonnet[1m]",
        metadata={"model": "claude-sonnet-5-5", "input_tokens": 10, "output_tokens": 1},
        data_dir=tmp_path,
    )
    record_invocation(
        invocation_id="n",
        provider="codex",
        model="cli-default",
        metadata={"input_tokens": 10, "output_tokens": 1},
        data_dir=tmp_path,
    )
    with usage_db(tmp_path) as db:
        models = dict(db.execute("SELECT id, model FROM usage_invocations").fetchall())
    assert models == {"d": "claude-opus-5-5", "s": "sonnet[1m]", "n": "cli-default"}


@pytest.mark.parametrize(
    ("provider", "model", "row"),
    [
        ("codex", "gpt-5.6-sol", (4, 20, 0.40, 5, 10)),
        ("codex", "gpt-5.6-terra", (2, 12, 0.20, 2.50, 5)),
        ("codex", "gpt-6-sol", (2, 10, 0.20, 2.50, 5)),
        ("codex", "gpt-6.1-terra", (1, 5, 0.05, 1.25, 2.50)),
        ("codex", "gpt-5.5", (5, 30, 0.50, None, None)),
        ("claude_code", "claude-opus-4-8", (5, 25, 0.50, 6.25, 10)),
        ("claude_code", "claude-sonnet-4-5-20250929", (3, 15, 0.30, 3.75, 6)),
        ("claude_code", "claude-opus-5-5[1m]", (4, 20, 0.20, 5, 8)),
        ("claude_code", "sonnet[1m]", (2, 10, 0.10, 2.50, 4)),
        ("grok", "grok-4.6", (2, 6, 0.50, None, None)),
    ],
)
def test_list_prices_checked_on_2026_11_18(
    provider: str, model: str, row: tuple[float, float, float, float | None, float | None]
) -> None:
    price = price_for(provider, model)
    assert price is not None
    assert (price.input, price.output, price.cache_read, price.cache_write) == row[:4]
    assert price.cache_write_long == row[4]
