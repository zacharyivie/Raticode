"""Provider token semantics and the subscription invocation accounting boundary.

Input includes cached tokens; output includes reasoning tokens. Cache and reasoning
columns are breakdowns, never additional tokens to add to the total.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any, ParamSpec
from uuid import uuid4

from gofer.core.agent import AgentResult

_P = ParamSpec("_P")
logger = logging.getLogger(__name__)


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
        return value
    return None


def _tokens(value: Any) -> int | None:
    number = _number(value)
    if number is not None and int(number) == number:
        return int(number)
    return None


def api_quota_windows(provider: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    """Read short-window limits from an actual API response, without API calls."""
    headers = {key.lower(): value for key, value in headers.items()}
    prefixes = (
        [("x-ratelimit", "tokens"), ("x-ratelimit", "requests")]
        if provider == "openai_api"
        else [
            ("anthropic-ratelimit", suffix)
            for suffix in ("tokens", "input-tokens", "output-tokens", "requests")
        ]
    )
    windows = []
    for prefix, dimension in prefixes:
        if provider == "openai_api":
            keys = {key: f"{prefix}-{key}-{dimension}" for key in ("limit", "remaining", "reset")}
        else:
            keys = {key: f"{prefix}-{dimension}-{key}" for key in ("limit", "remaining", "reset")}
        values: dict[str, int | None] = {}
        for key in ("limit", "remaining"):
            text = headers.get(keys[key], "")
            values[key] = int(text) if text.isdigit() else None
        if values["remaining"] is None:
            continue
        reset: str | None = None
        raw_reset = headers.get(keys["reset"], "")
        try:
            if provider == "openai_api":
                parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)", raw_reset)
                if parts and "".join(value + unit for value, unit in parts) == raw_reset:
                    seconds = sum(
                        float(value) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
                        for value, unit in parts
                    )
                    reset = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
            elif raw_reset:
                timestamp = datetime.fromisoformat(raw_reset.replace("Z", "+00:00"))
                if timestamp.tzinfo is not None:
                    reset = timestamp.isoformat()
        except (ValueError, OverflowError):
            pass
        windows.append(
            {
                "id": dimension,
                "label": dimension.replace("-", " ").capitalize() + " rate limit",
                "unit": "requests" if dimension == "requests" else "tokens",
                "used": None,
                "limit": values["limit"],
                "remaining": values["remaining"],
                "remaining_percent": None,
                "resets_at": reset,
                "unlimited": False,
            }
        )
    return windows


def normalize_usage(provider: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Normalize one native total, without traversing overlapping model subtotals."""
    result = dict(metadata)
    aliases = {
        "input_tokens": (
            "input_tokens",
            "inputTokens",
            "prompt_tokens",
            "total_input_tokens",
            "input",
        ),
        "output_tokens": (
            "output_tokens",
            "outputTokens",
            "completion_tokens",
            "total_output_tokens",
            "output",
        ),
        "total_tokens": ("total_tokens", "totalTokens", "total"),
        "cache_read_tokens": (
            "cache_read_tokens",
            "cached_input_tokens",
            "cachedInputTokens",
            "cache_read_input_tokens",
            "cachedReadTokens",
            "cacheReadTokens",
            "cacheReadInputTokens",
        ),
        "cache_write_tokens": (
            "cache_write_tokens",
            "cache_creation_input_tokens",
            "cacheCreationTokens",
            "cacheCreationInputTokens",
            "cacheWriteTokens",
        ),
        "reasoning_tokens": (
            "reasoning_tokens",
            "reasoningTokens",
            "reasoningOutputTokens",
            "thinking_tokens",
            "reasoning",
        ),
    }
    for target, keys in aliases.items():
        result.pop(target, None)
        for key in keys:
            value = _tokens(metadata.get(key))
            if value is not None:
                result[target] = value
                break
    for detail_key, field, target in (
        ("input_tokens_details", "cached_tokens", "cache_read_tokens"),
        ("prompt_tokens_details", "cached_tokens", "cache_read_tokens"),
        ("output_tokens_details", "reasoning_tokens", "reasoning_tokens"),
        ("completion_tokens_details", "reasoning_tokens", "reasoning_tokens"),
        ("cache", "read", "cache_read_tokens"),
        ("cache", "write", "cache_write_tokens"),
    ):
        detail = metadata.get(detail_key)
        if isinstance(detail, dict) and (value := _tokens(detail.get(field))) is not None:
            result[target] = value
    # Anthropic, OpenCode and Cursor report disjoint input buckets. Their normalized output
    # can pass through this function again without adding cache a second time.
    disjoint = provider in {"claude_code", "anthropic_api"} and any(
        key in metadata
        for key in (
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "cacheReadInputTokens",
            "cacheCreationInputTokens",
        )
    )
    disjoint = disjoint or provider == "opencode" and "input" in metadata
    disjoint = (
        disjoint
        or provider == "cursor"
        and any(key in metadata for key in ("cacheReadTokens", "cacheWriteTokens"))
    )
    if disjoint and isinstance(result.get("input_tokens"), (int, float)):
        result["input_tokens"] += result.get("cache_read_tokens", 0) + result.get(
            "cache_write_tokens", 0
        )
        result.pop("total_tokens", None)
    # OpenCode stores text output and reasoning as disjoint buckets.
    if (
        provider == "opencode"
        and "output" in metadata
        and isinstance(result.get("output_tokens"), (int, float))
    ):
        result["output_tokens"] += result.get("reasoning_tokens", 0)
        result.pop("total_tokens", None)
    if "total_tokens" not in result and all(
        _number(result.get(k)) is not None for k in ("input_tokens", "output_tokens")
    ):
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    if metadata.get("usageIsIncomplete") is True:
        result["partial"] = True
    ticks = _number(metadata.get("costUsdTicks"))
    if (
        ticks is not None
        and not metadata.get("costIsPartial")
        and not metadata.get("usageIsIncomplete")
    ):
        result["cost_usd"] = ticks / 10_000_000_000
    for keys in aliases.values():
        for key in keys:
            if key not in aliases:
                result.pop(key, None)
    for key in (
        "cache",
        "input_tokens_details",
        "prompt_tokens_details",
        "output_tokens_details",
        "completion_tokens_details",
    ):
        result.pop(key, None)
    if any(_number(result.get(key)) is not None for key in aliases):
        result.setdefault("source", "provider_metadata")
    return result


def provider_payload_usage(provider: str, payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """Prefer terminal aggregate usage over the messages and models it summarizes."""
    from gofer.subscriptions.base import _usage_metadata_from_payloads

    terminal = [p for p in payloads if p.get("type") == "result"]
    selected = terminal[-1:] if terminal else payloads
    result = _usage_metadata_from_payloads(selected)
    # The terminal usage object is authoritative. Model rows describe the same
    # consumption and must not overwrite it or be added to it.
    native_found = False
    for payload in selected:
        raw = payload.get("usage")
        if isinstance(raw, dict):
            result.update(normalize_usage(provider, raw))
            native_found = True
    if provider == "claude_code" and terminal and not native_found:
        models = terminal[-1].get("modelUsage")
        if isinstance(models, dict):
            totals: dict[str, Any] = {}
            for raw in models.values():
                if not isinstance(raw, dict):
                    continue
                for key, value in normalize_usage(provider, raw).items():
                    if key.endswith("_tokens") and _tokens(value) is not None:
                        totals[key] = totals.get(key, 0) + value
            result.update(totals)
    if provider == "claude_code":
        windows = claude_quota_windows(payloads)
        if windows:
            result["quota_windows"] = windows
            result["quota_observed_at"] = datetime.now(UTC).isoformat()
        if not terminal and any(key in result for key in ("input_tokens", "output_tokens")):
            result["partial"] = True
    return normalize_usage(provider, result)


CLAUDE_WINDOW_LABELS = {
    "five_hour": "Five-hour allowance",
    "seven_day": "Weekly allowance",
    "seven_day_opus": "Weekly Opus allowance",
    "seven_day_sonnet": "Weekly Sonnet allowance",
    "seven_day_overage_included": "Weekly allowance with overage",
}


def _claude_window(identity: str, status: Any, utilization: Any, resets_at: Any) -> dict[str, Any]:
    reset = None
    timestamp = _number(resets_at)
    if timestamp is not None:
        try:
            reset = datetime.fromtimestamp(timestamp, UTC).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    # Claude Code reports the fraction of a window used. It can exceed 1 past a cap.
    fraction = _number(utilization)
    used = min(100.0, fraction * 100) if fraction is not None else None
    label = CLAUDE_WINDOW_LABELS.get(identity, identity.replace("_", " "))
    if used is None:
        # Status only: the window is known to exist, but not how much is used.
        label = (label if identity != "unspecified" else "Plan status") + ": " + str(status)
    return {
        "id": identity,
        "status": status,
        "label": label.replace("_", " "),
        "unit": "percent",
        "used": used,
        "limit": 100.0 if used is not None else None,
        "remaining": 100.0 - used if used is not None else None,
        "remaining_percent": 100.0 - used if used is not None else None,
        "resets_at": reset,
        "unlimited": False,
    }


def claude_quota_windows(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Subscription windows from Claude Code's rate-limit events on each response.

    These come from the response headers of turns Raticode already runs, so they
    cost no request and need no credentials.
    """
    windows: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        if payload.get("type") != "rate_limit_event":
            continue
        info = payload.get("rate_limit_info")
        if not isinstance(info, dict):
            continue
        status = info.get("status")
        if status not in {"allowed", "allowed_warning", "rejected"}:
            continue
        identity = info.get("rateLimitType")
        if not isinstance(identity, str) or not identity:
            identity = "unspecified"
        # The top-level utilization describes only the currently limiting window,
        # so it is a measurement only when that window is named.
        windows[identity] = _claude_window(
            identity,
            status,
            info.get("utilization") if identity != "unspecified" else None,
            info.get("resetsAt"),
        )
        unified = info.get("unifiedWindows")
        for name, raw in unified.items() if isinstance(unified, dict) else ():
            if isinstance(name, str) and isinstance(raw, dict):
                window = _claude_window(
                    name,
                    status if name == identity else None,
                    raw.get("utilization"),
                    raw.get("resetsAt"),
                )
                if window["used"] is not None:
                    windows[name] = window
    return list(windows.values())


def track_invocation(
    function: Callable[_P, Awaitable[AgentResult]],
) -> Callable[_P, Awaitable[AgentResult]]:
    """Record each executed subscription once, including unreported/failed calls."""
    signature = inspect.signature(function)

    @wraps(function)
    async def tracked(*args: _P.args, **kwargs: _P.kwargs) -> AgentResult:
        from gofer.core.usage_ledger import record_invocation

        bound = signature.bind(*args, **kwargs).arguments
        subscription = bound.get("self")
        settings = bound.get("provider_settings")
        provider = (
            getattr(settings, "subscription", None)
            or getattr(subscription, "provider", None)
            or getattr(subscription, "subscription_name", None)
        )
        invocation_id = uuid4().hex
        result: AgentResult | None = None
        status = "failed"
        try:
            result = await function(*args, **kwargs)
            status = "completed" if result.success else "failed"
            return result
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        finally:
            cancellation = bound.get("cancel_event")
            if cancellation is not None and cancellation.is_set():
                status = "cancelled"
            if provider:
                try:
                    record_invocation(
                        invocation_id=invocation_id,
                        provider=provider,
                        profile=getattr(settings, "profile_name", None),
                        model=(result.model if result else None)
                        or getattr(settings, "model", None),
                        metadata=normalize_usage(provider, result.usage_metadata) if result else {},
                        status=status,
                    )
                except Exception:
                    # Accounting must not turn a successful provider call into a
                    # failed call. Avoid logging prompts, keys, or response bodies.
                    logger.warning("Unable to persist provider usage", exc_info=False)

    return tracked
