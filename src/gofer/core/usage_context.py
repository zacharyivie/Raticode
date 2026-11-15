"""Which run a provider call belongs to, for calls made inside that run's work.

A Rattish run sets an observer around each node it runs. The usage ledger tags every
call it records while one is set with the observer's run and node, and hands the call
to it, so the run can add up its own use and stop at a limit.

A provider that reports usage while its turn is still running passes each report to
the run through ``LiveCall``, so the run can stop the call once it passes a limit
instead of waiting for the call to end.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderCall:
    """What one provider call reported. Missing values were not reported.

    ``invocation_id`` ties the usage a running call reports to the call's final record.
    ``cost_cap_run_id`` names the run whose remaining budget set the provider's own
    spending cap, and ``stopped_at_cost_cap`` says the provider ended the call there.
    ``failed`` says the call failed or was cancelled instead of completing.
    """

    provider: str
    model: str | None
    tokens: dict[str, int]
    cost_usd: float | None
    partial: bool
    estimated: bool
    invocation_id: str | None = None
    cost_cap_usd: float | None = None
    cost_cap_run_id: str | None = None
    stopped_at_cost_cap: bool = False
    # True when the call failed or was cancelled rather than completing.
    failed: bool = False


@dataclass(frozen=True, slots=True)
class CostBudget:
    """What a run can still spend, and which run's limit that is."""

    remaining_usd: float
    run_id: str


@dataclass(frozen=True, slots=True)
class UsageObserver:
    run_id: str
    node_id: str | None
    observe: Callable[[ProviderCall], None]
    # True once the run has passed a usage limit, so retries and repairs stop calling.
    stop_requested: Callable[[], bool] = lambda: False
    # Usage so far of a call still running. True means the run wants the call stopped.
    observe_live: Callable[[ProviderCall], bool] = lambda call: False
    # The smallest budget left among this run and the runs that called it, if any is set.
    cost_budget: Callable[[], CostBudget | None] = lambda: None
    # The run's budget error once a limit ended it.
    limit_reached: Callable[[], dict[str, Any] | None] = lambda: None


@dataclass(slots=True)
class InvocationContext:
    """One provider call in progress, shared by the call and its usage record."""

    invocation_id: str
    cost_cap_usd: float | None = None
    cost_cap_run_id: str | None = None
    live_tokens: dict[str, int] = field(default_factory=dict)
    live_cost_usd: float | None = None
    stopped_by_run: bool = False


_current: ContextVar[UsageObserver | None] = ContextVar("raticode_usage_observer", default=None)
_invocation: ContextVar[InvocationContext | None] = ContextVar(
    "raticode_provider_invocation", default=None
)


def active_usage_observer() -> UsageObserver | None:
    return _current.get()


@contextmanager
def observe_usage(observer: UsageObserver) -> Iterator[None]:
    token = _current.set(observer)
    try:
        yield
    finally:
        _current.reset(token)


def current_invocation() -> InvocationContext | None:
    return _invocation.get()


@contextmanager
def provider_invocation(invocation_id: str) -> Iterator[InvocationContext]:
    context = InvocationContext(invocation_id)
    token = _invocation.set(context)
    try:
        yield context
    finally:
        _invocation.reset(token)


def run_cost_budget() -> CostBudget | None:
    """The cost budget left for a call made now, or None outside a run with max-cost-usd."""
    observer = active_usage_observer()
    return observer.cost_budget() if observer is not None else None


def note_cost_cap(cap_usd: float, budget: CostBudget) -> None:
    """Remember the spending cap a provider was started with for this call's record."""
    invocation = current_invocation()
    if invocation is not None:
        invocation.cost_cap_usd = cap_usd
        invocation.cost_cap_run_id = budget.run_id


class StopEvent(threading.Event):
    """A stop signal set by the run's usage limit or by the caller's own cancel event."""

    def __init__(self, other: threading.Event | None) -> None:
        super().__init__()
        self._other = other

    def is_set(self) -> bool:
        return super().is_set() or (self._other is not None and self._other.is_set())


class LiveCall:
    """Hands the usage a running call reports to its run, and stops it past a limit.

    Outside a run, or for a run with no observer, every method does nothing, so the
    provider runs exactly as before.
    """

    def __init__(self, provider: str, model: str | None, cancel_event: threading.Event | None):
        self.provider = provider
        self.model = model
        self.observer = active_usage_observer()
        self.invocation = current_invocation()
        self.cancel_event: threading.Event | None = cancel_event
        self._stop: StopEvent | None = None
        if self.observer is not None:
            self._stop = StopEvent(cancel_event)
            self.cancel_event = self._stop
        self.usage: dict[str, Any] = {}

    @property
    def stopped(self) -> bool:
        return self.invocation is not None and self.invocation.stopped_by_run

    def report(self, metadata: Mapping[str, Any]) -> bool:
        """Count the turn's usage so far. True once the run has asked to stop the call."""
        if self.observer is None or self._stop is None:
            return False
        if self.stopped:
            return True
        from gofer.core.usage_ledger import ledger_tokens, reported_cost

        tokens = ledger_tokens(metadata)
        cost = reported_cost(dict(metadata))
        if not tokens and cost is None:
            return False
        self.usage = {**dict(metadata), "partial": True}
        if self.invocation is not None:
            self.invocation.live_tokens = tokens
            self.invocation.live_cost_usd = cost
        call = ProviderCall(
            provider=self.provider,
            model=self.model,
            tokens=tokens,
            cost_usd=cost,
            partial=True,
            estimated=False,
            invocation_id=self.invocation.invocation_id if self.invocation else None,
        )
        if self.observer.observe_live(call):
            if self.invocation is not None:
                self.invocation.stopped_by_run = True
            self._stop.set()
            return True
        return False

    def stop_message(self) -> str:
        reached = self.observer.limit_reached() if self.observer is not None else None
        if reached is not None:
            return str(reached["message"])
        return "Raticode stopped this call because its workflow run reached a usage limit."
