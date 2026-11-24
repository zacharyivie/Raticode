"""Tokens and cost of one Rattish run, per node, and the run's usage limits.

Every provider call the usage ledger records while a node runs reaches the run's meter
(see ``gofer.core.usage_context``). A value no provider reported stays ``None`` and
reads as unknown; it is never counted as zero.

``max-tokens`` and ``max-cost-usd`` are checked after each call ends, and also while a
call runs when its provider reports usage during the turn: the meter adds what running
calls have reported so far and asks them to stop once the total passes a limit. A
provider with its own spending cap (Claude Code's ``--max-budget-usd``) is started with
the run's remaining budget, and a call it stops there ends the run at its cost limit.
A provider that reports nothing until a call ends can still take the run past a limit.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from gofer.core.usage_context import (
    CostBudget,
    ProviderCall,
    UsageObserver,
    active_usage_observer,
)

TOKEN_LIMIT_EXCEEDED = "RATTISH_TOKEN_LIMIT_EXCEEDED"
COST_LIMIT_EXCEEDED = "RATTISH_COST_LIMIT_EXCEEDED"
TOKENS_UNREPORTED = "RATTISH_TOKEN_LIMIT_UNCHECKABLE"
COST_UNREPORTED = "RATTISH_COST_LIMIT_UNCHECKABLE"
PARENT_LIMIT_REACHED = "RATTISH_PARENT_USAGE_LIMIT"
USAGE_LIMIT_CODES = frozenset(
    {
        TOKEN_LIMIT_EXCEEDED,
        COST_LIMIT_EXCEEDED,
        TOKENS_UNREPORTED,
        COST_UNREPORTED,
        PARENT_LIMIT_REACHED,
    }
)
_OVERSHOOT = (
    "A call that was already running when the limit was crossed can go past it, "
    "unless its provider reports usage while it runs."
)
_LIVE_OVERSHOOT = (
    "Usage counts as the provider reports it, so the call may have gone a little past the "
    "limit before it stopped."
)
_PROVIDER_NAMES = {"claude_code": "Claude Code"}


def empty_usage() -> dict[str, Any]:
    return {
        "calls": 0,
        "reported_calls": 0,
        "partial_calls": 0,
        "unknown_calls": 0,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cost_usd": None,
        "cost_reported_calls": 0,
        "cost_unknown_calls": 0,
    }


def call_tokens_complete(call: ProviderCall) -> bool:
    tokens = call.tokens
    return (
        not call.estimated
        and not call.partial
        and ("total_tokens" in tokens or {"input_tokens", "output_tokens"} <= tokens.keys())
    )


def call_cost_complete(call: ProviderCall) -> bool:
    return call.cost_usd is not None and not call.estimated and not call.partial


def failed_without_report(call: ProviderCall) -> bool:
    """A failed or cancelled call that reported no tokens and no cost.

    Such calls, such as a rate-limited or signed-out provider answering with an error,
    don't end a run for not reporting its use: that would hide the provider's error and
    stop retries and fallback providers from taking over.
    """
    return call.failed and not call.tokens and call.cost_usd is None


def add_call(target: dict[str, Any], call: ProviderCall) -> None:
    """Add one call. Estimated counts are left out; partial ones count as a lower bound."""
    target["calls"] += 1
    tokens = {} if call.estimated else call.tokens
    if call_tokens_complete(call):
        target["reported_calls"] += 1
    elif tokens:
        target["partial_calls"] += 1
    else:
        target["unknown_calls"] += 1
    total = tokens.get("total_tokens")
    if total is None and tokens:
        total = tokens.get("input_tokens", 0) + tokens.get("output_tokens", 0)
    for field, value in (
        ("input_tokens", tokens.get("input_tokens")),
        ("output_tokens", tokens.get("output_tokens")),
        ("total_tokens", total),
    ):
        if value is not None:
            target[field] = (target[field] or 0) + value
    if call_cost_complete(call):
        target["cost_reported_calls"] += 1
    else:
        target["cost_unknown_calls"] += 1
    if call.cost_usd is not None and not call.estimated:
        target["cost_usd"] = (target["cost_usd"] or 0.0) + call.cost_usd


def add_cached_result(target: dict[str, Any]) -> None:
    """Count one answer reused from the result cache: no call, 0 tokens and $0."""
    target["cached_results"] = int(target.get("cached_results") or 0) + 1
    if target["calls"] == 0:
        # With no calls yet, a cached answer makes the totals known zeros, not unknown.
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            if target[field] is None:
                target[field] = 0
        if target["cost_usd"] is None:
            target["cost_usd"] = 0.0


def format_tokens(value: int) -> str:
    return f"{value:,}"


def format_usd(value: float) -> str:
    if value == 0:
        return "$0"
    if value < 0.01:
        return f"${value:.4f}"
    return f"${value:.2f}"


def _unreported(count: int, calls: int) -> str:
    return f"{count} of {calls} {'call' if calls == 1 else 'calls'} unreported"


def tokens_label(usage: Mapping[str, Any] | None) -> str:
    """Tokens for a run or node: exact, a lower bound, or unknown."""
    usage = usage or {}
    calls = int(usage.get("calls") or 0)
    if not calls:
        return "0"
    total = usage.get("total_tokens")
    if total is None:
        return "unknown"
    missing = int(usage.get("partial_calls") or 0) + int(usage.get("unknown_calls") or 0)
    if missing:
        return f"at least {format_tokens(int(total))} ({_unreported(missing, calls)})"
    return format_tokens(int(total))


def cost_label(usage: Mapping[str, Any] | None) -> str:
    """Estimated cost for a run or node: exact as reported, a lower bound, or unknown."""
    usage = usage or {}
    calls = int(usage.get("calls") or 0)
    if not calls:
        return "$0"
    cost = usage.get("cost_usd")
    if cost is None:
        return "unknown"
    missing = int(usage.get("cost_unknown_calls") or 0)
    if missing:
        return f"at least {format_usd(float(cost))} ({_unreported(missing, calls)})"
    return format_usd(float(cost))


def usage_line(usage: Mapping[str, Any] | None) -> str:
    """One line such as ``3 model calls, 12,345 tokens, estimated cost $0.12``."""
    usage = usage or {}
    calls = int(usage.get("calls") or 0)
    cached = int(usage.get("cached_results") or 0)
    cached_text = f"{cached} cached {'result' if cached == 1 else 'results'}"
    if not calls:
        if cached:
            return f"No model calls, {cached_text}, tokens 0, estimated cost $0"
        return "No model calls"
    return (
        f"{calls} model {'call' if calls == 1 else 'calls'}, tokens {tokens_label(usage)}, "
        f"estimated cost {cost_label(usage)}" + (f", {cached_text}" if cached else "")
    )


class RunUsageMeter:
    """Adds up one run's provider calls and decides when a usage limit ends the run.

    A child workflow's meter passes each call on to the calling run's node, so the
    calling run counts it too and its limits apply.
    """

    def __init__(
        self,
        run_id: str,
        *,
        max_tokens: int | None = None,
        max_cost_usd: float | None = None,
        parent: UsageObserver | None = None,
        daily_cost: bool = False,
        daily_tokens: bool = False,
    ) -> None:
        self.run_id = run_id
        self.max_tokens = max_tokens
        self.max_cost_usd = max_cost_usd
        self.parent = parent
        # A triggered run's cap came from what was left of a daily limit, not the workflow.
        self.daily_cost = daily_cost
        self.daily_tokens = daily_tokens
        self._cost_name = "remaining daily cost" if daily_cost else "max-cost-usd"
        self._token_name = "remaining daily token" if daily_tokens else "max-tokens"
        self._lock = threading.Lock()
        self._totals = empty_usage()
        self._nodes: dict[str, dict[str, Any]] = {}
        self._activations: dict[tuple[str, int], dict[str, Any]] = {}
        self._limit: dict[str, Any] | None = None
        # Usage reported so far by calls still running, by invocation.
        self._inflight: dict[str, ProviderCall] = {}

    @classmethod
    def for_ir(
        cls,
        ir: Mapping[str, Any],
        run_id: str,
        *,
        daily_max_cost_usd: float | None = None,
        daily_max_tokens: int | None = None,
    ) -> RunUsageMeter:
        """The workflow's own limits, or what is left of a daily limit when that is smaller."""
        workflow = ir["workflow"]
        max_tokens = workflow.get("max_tokens")
        max_cost_usd = workflow.get("max_cost_usd")
        daily_tokens = daily_max_tokens is not None and (
            max_tokens is None or daily_max_tokens < max_tokens
        )
        daily_cost = daily_max_cost_usd is not None and (
            max_cost_usd is None or daily_max_cost_usd < max_cost_usd
        )
        return cls(
            run_id,
            max_tokens=daily_max_tokens if daily_tokens else max_tokens,
            max_cost_usd=daily_max_cost_usd if daily_cost else max_cost_usd,
            parent=active_usage_observer(),
            daily_cost=daily_cost,
            daily_tokens=daily_tokens,
        )

    def observer(self, node_id: str, run_number: int) -> UsageObserver:
        """The observer for one node activation's calls."""

        def observe(call: ProviderCall) -> None:
            self.add(node_id, run_number, call)

        def observe_live(call: ProviderCall) -> bool:
            return self.add_live(node_id, call)

        run_id, reported_node = (
            (self.parent.run_id, self.parent.node_id)
            if self.parent is not None
            else (self.run_id, node_id)
        )
        return UsageObserver(
            run_id,
            reported_node,
            observe,
            self.stop_requested,
            observe_live,
            self.cost_budget,
            self.limit_reached,
        )

    def add(self, node_id: str, run_number: int, call: ProviderCall) -> None:
        with self._lock:
            if call.invocation_id is not None:
                self._inflight.pop(call.invocation_id, None)
            for target in (
                self._totals,
                self._nodes.setdefault(node_id, empty_usage()),
                self._activations.setdefault((node_id, run_number), empty_usage()),
            ):
                add_call(target, call)
            if self._limit is None:
                self._limit = self._check(node_id, call)
        if self.parent is not None:
            self.parent.observe(call)

    def add_cached(self, node_id: str, run_number: int) -> None:
        """Record an activation answered from the result cache: 0 tokens and $0."""
        with self._lock:
            for target in (
                self._totals,
                self._nodes.setdefault(node_id, empty_usage()),
                self._activations.setdefault((node_id, run_number), empty_usage()),
            ):
                add_cached_result(target)

    def add_live(self, node_id: str, call: ProviderCall) -> bool:
        """Count what a running call has reported so far. True means stop the call."""
        with self._lock:
            if call.invocation_id is not None:
                self._inflight[call.invocation_id] = call
            if self._limit is None:
                self._limit = self._check_live(node_id)
        parent_stop = self.parent.observe_live(call) if self.parent is not None else False
        return parent_stop or self.stop_requested()

    def _with_running_calls(self) -> dict[str, Any]:
        totals = dict(self._totals)
        for running in self._inflight.values():
            add_call(totals, running)
        return totals

    def _check_live(self, node_id: str) -> dict[str, Any] | None:
        totals = self._with_running_calls()
        used = totals["total_tokens"]
        if self.max_tokens is not None and used is not None and used > self.max_tokens:
            return {
                "code": TOKEN_LIMIT_EXCEEDED,
                "message": (
                    f"The run used at least {format_tokens(used)} tokens, over its "
                    f"{self._token_name} limit of {format_tokens(self.max_tokens)}, so Raticode "
                    f"stopped node {node_id!r} while its call was running. {_LIVE_OVERSHOOT}"
                ),
                "details": {
                    "scope": "workflow",
                    "limit": "max-tokens",
                    **self._daily(tokens=True),
                    "max_tokens": self.max_tokens,
                    "used_tokens": used,
                    "node_id": node_id,
                    "stopped_running_call": True,
                },
            }
        cost = totals["cost_usd"]
        if self.max_cost_usd is not None and cost is not None and cost > self.max_cost_usd:
            return {
                "code": COST_LIMIT_EXCEEDED,
                "message": (
                    f"The run's estimated cost reached at least {format_usd(cost)}, over its "
                    f"{self._cost_name} limit of {format_usd(self.max_cost_usd)}, so Raticode "
                    f"stopped node {node_id!r} while its call was running. {_LIVE_OVERSHOOT}"
                ),
                "details": {
                    "scope": "workflow",
                    "limit": "max-cost-usd",
                    **self._daily(tokens=False),
                    "max_cost_usd": self.max_cost_usd,
                    "cost_usd": cost,
                    "node_id": node_id,
                    "stopped_running_call": True,
                },
            }
        return None

    def cost_budget(self) -> CostBudget | None:
        """What a call started now may spend: the smallest budget left here or above."""
        own: CostBudget | None = None
        if self.max_cost_usd is not None:
            with self._lock:
                spent = self._with_running_calls()["cost_usd"] or 0.0
            own = CostBudget(max(0.0, self.max_cost_usd - spent), self.run_id)
        above = self.parent.cost_budget() if self.parent is not None else None
        if own is None or (above is not None and above.remaining_usd < own.remaining_usd):
            return above
        return own

    def _check(self, node_id: str, call: ProviderCall) -> dict[str, Any] | None:
        totals = self._totals
        if (
            call.stopped_at_cost_cap
            and call.cost_cap_run_id == self.run_id
            and self.max_cost_usd is not None
        ):
            provider = _PROVIDER_NAMES.get(call.provider, call.provider)
            cap = call.cost_cap_usd
            cost = totals["cost_usd"]
            spent = f" The run's estimated cost is {format_usd(cost)}." if cost is not None else ""
            return {
                "code": COST_LIMIT_EXCEEDED,
                "message": (
                    f"{provider} stopped node {node_id!r} at its spending cap"
                    + (f" of {format_usd(cap)}" if cap is not None else "")
                    + f", which Raticode set from what was left of the run's {self._cost_name} "
                    f"limit of {format_usd(self.max_cost_usd)}.{spent}"
                ),
                "details": {
                    "scope": "workflow",
                    "limit": "max-cost-usd",
                    **self._daily(tokens=False),
                    "max_cost_usd": self.max_cost_usd,
                    "cost_usd": cost,
                    "node_id": node_id,
                    "provider": call.provider,
                    "provider_cap_usd": cap,
                },
            }
        if self.max_tokens is not None:
            used = totals["total_tokens"]
            if used is not None and used > self.max_tokens:
                return {
                    "code": TOKEN_LIMIT_EXCEEDED,
                    "message": (
                        f"The run used {format_tokens(used)} tokens, over its {self._token_name} "
                        f"limit of {format_tokens(self.max_tokens)}, so it ended after a call "
                        f"from node {node_id!r}. {_OVERSHOOT}"
                    ),
                    "details": {
                        "scope": "workflow",
                        "limit": "max-tokens",
                        **self._daily(tokens=True),
                        "max_tokens": self.max_tokens,
                        "used_tokens": used,
                        "node_id": node_id,
                    },
                }
            if not call_tokens_complete(call) and not failed_without_report(call):
                return {
                    "code": TOKENS_UNREPORTED,
                    "message": (
                        f"A call from node {node_id!r} ({call.provider}) didn't report how many "
                        "tokens it used, so the run can't be kept under its "
                        f"{self._token_name} limit of {format_tokens(self.max_tokens)}. "
                        "The run ended to stay safe."
                    ),
                    "details": {
                        "scope": "workflow",
                        "limit": "max-tokens",
                        **self._daily(tokens=True),
                        "max_tokens": self.max_tokens,
                        "used_tokens": used,
                        "node_id": node_id,
                        "provider": call.provider,
                    },
                }
        if self.max_cost_usd is not None:
            cost = totals["cost_usd"]
            if cost is not None and cost > self.max_cost_usd:
                return {
                    "code": COST_LIMIT_EXCEEDED,
                    "message": (
                        f"The run's estimated cost reached {format_usd(cost)}, over its "
                        f"{self._cost_name} limit of {format_usd(self.max_cost_usd)}, so it "
                        f"ended after a call from node {node_id!r}. {_OVERSHOOT}"
                    ),
                    "details": {
                        "scope": "workflow",
                        "limit": "max-cost-usd",
                        **self._daily(tokens=False),
                        "max_cost_usd": self.max_cost_usd,
                        "cost_usd": cost,
                        "node_id": node_id,
                    },
                }
            if not call_cost_complete(call) and not failed_without_report(call):
                return {
                    "code": COST_UNREPORTED,
                    "message": (
                        f"A call from node {node_id!r} ({call.provider}) didn't report what it "
                        f"cost, so the run can't be kept under its {self._cost_name} limit of "
                        f"{format_usd(self.max_cost_usd)}. The run ended to stay safe. "
                        + (
                            "For a provider that doesn't report cost, use a daily token limit."
                            if self.daily_cost
                            else "For a provider that doesn't report cost, limit tokens with "
                            "max-tokens."
                        )
                    ),
                    "details": {
                        "scope": "workflow",
                        "limit": "max-cost-usd",
                        **self._daily(tokens=False),
                        "max_cost_usd": self.max_cost_usd,
                        "cost_usd": cost,
                        "node_id": node_id,
                        "provider": call.provider,
                    },
                }
        return None

    def _daily(self, *, tokens: bool) -> dict[str, Any]:
        daily = self.daily_tokens if tokens else self.daily_cost
        return {"daily": True} if daily else {}

    def stop_requested(self) -> bool:
        return self._limit is not None or (self.parent is not None and self.parent.stop_requested())

    def limit_reached(self) -> dict[str, Any] | None:
        """The ``budget`` error for a limit this run or its caller reached, if any."""
        with self._lock:
            if self._limit is not None:
                return dict(self._limit)
        if self.parent is not None and self.parent.stop_requested():
            return {
                "code": PARENT_LIMIT_REACHED,
                "message": "The workflow that called this one reached its usage limit.",
                "details": {"scope": "parent"},
            }
        return None

    def activation_usage(self, node_id: str, run_number: int) -> dict[str, Any] | None:
        with self._lock:
            usage = self._activations.get((node_id, run_number))
            return dict(usage) if usage is not None else None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                **self._totals,
                "nodes": {node: dict(usage) for node, usage in sorted(self._nodes.items())},
                "limits": {
                    "max_tokens": self.max_tokens,
                    "max_cost_usd": self.max_cost_usd,
                    **({"daily_tokens": True} if self.daily_tokens else {}),
                    **({"daily_cost": True} if self.daily_cost else {}),
                },
            }


def usage_payload(usage: Any) -> dict[str, Any] | None:
    """Usage for API clients and the CLI, or None for a run saved before usage tracking."""
    if not isinstance(usage, Mapping):
        return None
    payload: dict[str, Any] = {
        "calls": int(usage.get("calls") or 0),
        "inputTokens": usage.get("input_tokens"),
        "outputTokens": usage.get("output_tokens"),
        "totalTokens": usage.get("total_tokens"),
        "costUsd": usage.get("cost_usd"),
        "unreportedTokenCalls": int(usage.get("partial_calls") or 0)
        + int(usage.get("unknown_calls") or 0),
        "unreportedCostCalls": int(usage.get("cost_unknown_calls") or 0),
        "cachedResults": int(usage.get("cached_results") or 0),
        "tokensLabel": tokens_label(usage),
        "costLabel": cost_label(usage),
        "summary": usage_line(usage),
    }
    limits = usage.get("limits")
    if isinstance(limits, Mapping):
        payload["limits"] = {
            "maxTokens": limits.get("max_tokens"),
            "maxCostUsd": limits.get("max_cost_usd"),
        }
        # Set when a triggered run's cap came from what was left of a daily limit.
        for source, target in (("daily_tokens", "dailyTokens"), ("daily_cost", "dailyCost")):
            if limits.get(source) is True:
                payload["limits"][target] = True
    nodes = usage.get("nodes")
    if isinstance(nodes, Mapping):
        payload["nodes"] = {
            str(node): node_payload
            for node, item in nodes.items()
            if (node_payload := usage_payload(item)) is not None
        }
    return payload
