"""Usage transport fixtures never invoke a model or require provider logins."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from gofer.core.agent import AgentResult
from gofer.subscriptions.cli_providers import CliOutput
from gofer.subscriptions.usage import (
    api_quota_windows,
    normalize_usage,
    provider_payload_usage,
    track_invocation,
)


def test_anthropic_caches_are_disjoint_but_openai_caches_are_inclusive() -> None:
    anthropic = normalize_usage(
        "anthropic_api",
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 20,
        },
    )
    assert anthropic["input_tokens"] == 130
    assert anthropic["total_tokens"] == 135
    assert normalize_usage("anthropic_api", anthropic) == anthropic
    openai = normalize_usage(
        "openai_api",
        {
            "input_tokens": 130,
            "output_tokens": 5,
            "input_tokens_details": {"cached_tokens": 100},
            "output_tokens_details": {"reasoning_tokens": 3},
        },
    )
    assert openai["total_tokens"] == 135
    assert openai["cache_read_tokens"] == 100
    assert openai["reasoning_tokens"] == 3


def test_terminal_claude_totals_override_messages_and_model_subtotals() -> None:
    result = provider_payload_usage(
        "claude_code",
        [
            {"type": "assistant", "usage": {"input_tokens": 7, "output_tokens": 8}},
            {
                "type": "result",
                "usage": {"input_tokens": 20, "output_tokens": 30, "cache_read_input_tokens": 100},
                "modelUsage": {"small": {"inputTokens": 2, "outputTokens": 3}},
            },
        ],
    )
    assert result["input_tokens"] == 120
    assert result["output_tokens"] == 30
    assert result["total_tokens"] == 150


def test_opencode_steps_sum_once_with_inclusive_breakdowns() -> None:
    parser = CliOutput("opencode")

    def step(identity: str) -> str:
        return (
            json.dumps(
                {
                    "type": "step_finish",
                    "sessionID": "s",
                    "part": {
                        "id": identity,
                        "messageID": "m",
                        "tokens": {
                            "input": 10,
                            "output": 5,
                            "reasoning": 3,
                            "cache": {"read": 100, "write": 20},
                        },
                    },
                }
            )
            + "\n"
        )

    parser.feed(step("a") + step("a") + step("b"))
    assert parser.usage["input_tokens"] == 260
    assert parser.usage["output_tokens"] == 16
    assert parser.usage["total_tokens"] == 276
    assert parser.usage["reasoning_tokens"] == 6
    assert parser.usage["cache_read_tokens"] == 200
    assert normalize_usage("opencode", parser.usage) == parser.usage


def test_antigravity_and_grok_breakdowns_are_already_inclusive() -> None:
    for provider, raw in [
        (
            "antigravity",
            {"input_tokens": 20, "output_tokens": 10, "thinking_tokens": 3, "cache_read_tokens": 8},
        ),
        (
            "grok",
            {
                "inputTokens": 20,
                "outputTokens": 10,
                "reasoningTokens": 3,
                "cachedReadTokens": 8,
                "cacheCreationTokens": 2,
                "costUsdTicks": 10_000_000,
            },
        ),
    ]:
        result = normalize_usage(provider, raw)
        assert result["total_tokens"] == 30
        assert result["cache_read_tokens"] == 8
        assert result["reasoning_tokens"] == 3
    assert result["cost_usd"] == 0.001
    partial = normalize_usage(
        "grok", {"usageIsIncomplete": True, "costUsdTicks": 100, "inputTokens": 5}
    )
    assert partial["partial"] is True
    assert "cost_usd" not in partial
    assert "total_tokens" not in partial


@pytest.mark.parametrize("value", [True, -1, 3.5, float("nan"), float("inf"), "30"])
def test_malformed_tokens_remain_unknown(value: Any) -> None:
    result = normalize_usage("codex", {"input_tokens": value, "output_tokens": 5})
    assert "input_tokens" not in result
    assert "total_tokens" not in result


def test_api_headers_preserve_units_and_parse_reset_windows() -> None:
    windows = api_quota_windows(
        "openai_api",
        {
            "X-Ratelimit-Remaining-Tokens": "980",
            "x-ratelimit-limit-tokens": "1000",
            "x-ratelimit-reset-tokens": "1m30s",
            "authorization": "never retained",
        },
    )
    assert windows[0]["remaining"] == 980
    assert windows[0]["limit"] == 1000
    assert windows[0]["resets_at"] is not None
    assert windows[0]["unit"] == "tokens"
    assert "never retained" not in str(windows)
    windows = api_quota_windows(
        "anthropic_api",
        {
            "anthropic-ratelimit-input-tokens-remaining": "1000",
            "anthropic-ratelimit-input-tokens-reset": "2026-09-24T12:00:00Z",
        },
    )
    assert windows[0]["id"] == "input-tokens"
    assert windows[0]["resets_at"] == "2026-09-24T12:00:00+00:00"
    assert api_quota_windows("openai_api", {"x-ratelimit-remaining-tokens": "bad"}) == []


@pytest.mark.parametrize("ending", ["success", "failed", "raised", "cancelled"])
async def test_invocation_is_recorded_once_on_every_exit(monkeypatch, ending: str) -> None:
    from gofer.core import usage_ledger

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(usage_ledger, "record_invocation", lambda **kw: calls.append(kw))

    class Fake:
        provider = "codex"

        @track_invocation
        async def execute(self, prompt: str, working_dir: Path) -> AgentResult:
            if ending == "raised":
                raise ValueError("failure")
            if ending == "cancelled":
                raise asyncio.CancelledError
            return AgentResult(
                agent_id="",
                success=ending == "success",
                output="answer",
                exit_code=0,
                duration_seconds=0,
                usage_metadata={"input_tokens": 10, "output_tokens": 5},
            )

    try:
        await Fake().execute("private prompt", Path("."))
    except (ValueError, asyncio.CancelledError):
        pass
    assert len(calls) == 1
    call = calls[0]
    assert (
        call["status"]
        == {
            "success": "completed",
            "failed": "failed",
            "raised": "failed",
            "cancelled": "cancelled",
        }[ending]
    )
    assert call["invocation_id"]
    assert "private prompt" not in str(call)
    assert "answer" not in str(call)
    if ending in {"success", "failed"}:
        assert call["metadata"]["total_tokens"] == 15


def test_claude_model_usage_fallback_sums_cache_once() -> None:
    result = provider_payload_usage(
        "claude_code",
        [
            {
                "type": "result",
                "modelUsage": {
                    "a": {"inputTokens": 10, "outputTokens": 5, "cacheReadInputTokens": 20},
                    "b": {"inputTokens": 7, "outputTokens": 3, "cacheCreationInputTokens": 8},
                },
            }
        ],
    )
    assert result["input_tokens"] == 45
    assert result["output_tokens"] == 8
    assert result["total_tokens"] == 53


async def test_grok_queries_usage_before_session_closes(monkeypatch, tmp_path) -> None:
    from contextlib import asynccontextmanager

    from gofer.subscriptions import acp_providers
    from gofer.subscriptions.acp_transport import AcpTransportError

    calls: list[str] = []

    class Rpc:
        async def request(
            self, method: str, params: dict[str, Any], **kwargs: Any
        ) -> dict[str, Any]:
            calls.append(method)
            if method == "_x.ai/session/usage":
                assert params == {"sessionId": "fresh"}
                return {
                    "usage": {
                        "inputTokens": 20,
                        "outputTokens": 5,
                        "cachedReadTokens": 10,
                        "modelUsage": {"child": {"inputTokens": 3, "outputTokens": 2}},
                    }
                }
            raise AcpTransportError("unexpected request")

    @asynccontextmanager
    async def transport(*args, **kwargs):
        yield Rpc()
        calls.append("closed")

    async def identity(*args):
        return "version"

    async def session(*args):
        return "fresh"

    async def readiness(*args):
        return None

    async def prompt(*args, **kwargs):
        yield {"type": "final", "exitCode": 0, "error": None}

    monkeypatch.setattr(acp_providers, "acp_command", lambda *args: ["/fake/grok"])
    monkeypatch.setattr(acp_providers, "grok_build_version", identity)
    monkeypatch.setattr(acp_providers, "open_acp_transport", transport)
    monkeypatch.setattr(acp_providers, "initialize_session", session)
    monkeypatch.setattr(acp_providers, "wait_grok_mcp", readiness)
    monkeypatch.setattr(acp_providers, "prompt_session", prompt)
    events = [
        e
        async for e in acp_providers.stream_acp(
            "grok", "fixture prompt", cwd=tmp_path, permission_mode="cli-managed"
        )
    ]
    assert calls == ["_x.ai/session/usage", "closed"]
    assert events[-1]["usage"]["input_tokens"] == 20
    assert events[-1]["usage"]["total_tokens"] == 25


def test_claude_quota_events_keep_reset_without_assuming_utilization_units() -> None:
    result = provider_payload_usage(
        "claude_code",
        [
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "allowed_warning",
                    "utilization": 0.8,
                    "resetsAt": 1790251200,
                },
            },
            {"type": "result", "usage": {"input_tokens": 10, "output_tokens": 5}},
        ],
    )
    window = result["quota_windows"][0]
    assert window["remaining_percent"] is None
    assert window["remaining"] is None
    assert window["limit"] is None
    assert "allowed warning" in window["label"]
    assert window["resets_at"] is not None
    assert result["total_tokens"] == 15


def test_codex_cumulative_snapshots_replace_earlier_totals() -> None:
    result = provider_payload_usage(
        "codex",
        [
            {
                "type": "turn.usage",
                "usage": {
                    "inputTokens": 100,
                    "outputTokens": 20,
                    "cachedInputTokens": 50,
                    "reasoningOutputTokens": 10,
                },
            },
            {
                "type": "turn.completed",
                "usage": {
                    "inputTokens": 300,
                    "outputTokens": 50,
                    "cachedInputTokens": 80,
                    "reasoningOutputTokens": 20,
                },
            },
        ],
    )
    assert result["input_tokens"] == 300
    assert result["output_tokens"] == 50
    assert result["total_tokens"] == 350
    assert result["cache_read_tokens"] == 80
    assert result["reasoning_tokens"] == 20
