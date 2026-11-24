"""Which computer a new start of work goes to: the placement engine.

Every start of Rem work (a task, self-work, a run started from chat) asks ``place``
for a computer, and organization starts can ask it the same way. The answer is a
computer, or "wait" with the reason in words. Nothing here reads files or the clock,
so the same inputs always give the same answer.

A consumer (Rem, or one organization) has a strategy and the computers assigned to
it. The computers come in the user's order, each with its worker limit
(``max_threads``) and the workers running on it from every consumer that shares it.

- ``overflow``: the first computer with room, the assigned ones first in the user's
  order, then the rest in the user's order. With none assigned, every computer.
- ``balanced``: the assigned computer with room whose running/max_threads is lowest;
  ties go to the one earlier in the user's order. With none assigned, every computer.
- ``manual``: only the assigned computers, in the user's order; never anything else.

No start ever takes a computer past its worker limit, and an offline computer, or one
the consumer can't use, is skipped. When no computer has room the start waits and
the reason names each computer and why it was passed over; it never quietly runs
somewhere else.

Placement is decided when a run starts and stays for the whole run. A run that
resumes passes the computer it started on as ``previous``: it goes back there if
that computer is still usable for it (online, listed, allowed by the strategy, with a
known limit), waiting for room there if it is only full. Otherwise the engine picks
again and the answer says so in ``moved``. A run whose work stays on the computer it
started on (``stay``, a run on a paired desktop) never moves: while that computer
can't be used it waits for it, with the reason.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from gofer.core.command_slots import memory_wait_reason
from gofer.core.worker_claims import breakdown

OVERFLOW = "overflow"
BALANCED = "balanced"
MANUAL = "manual"
STRATEGIES = (OVERFLOW, BALANCED, MANUAL)
# Matches the placement settings: a consumer with no saved choice overflows.
DEFAULT_STRATEGY = OVERFLOW


class PlacementEngineError(ValueError):
    """A strategy or set of computers the engine can't place for."""


@dataclass(frozen=True)
class Computer:
    """One computer as placement sees it.

    ``running`` counts workers from every consumer on it. ``capacity`` is how many
    new workers it can take now when the caller knows better than
    ``max_threads - running`` (CPU or memory pressure, or a computer that just came
    back); the engine never lets it go past ``max_threads - running``. ``unusable``
    is a reason this consumer can't use the computer at all, such as work it can't
    run there yet. ``memory_hold`` is set while the computer is short of memory with a
    worker already running, the detail of its reason (e.g. "0.9 GB available"); it
    takes no new worker then, though it stays usable, so a resumed run waits there.
    ``running_by_kind`` counts ``running`` by kind (``(kind, count)`` pairs), so a full
    or busy computer's reason says what fills it; empty when they aren't known.
    """

    id: str
    name: str
    max_threads: int | None
    running: int = 0
    online: bool = True
    capacity: int | None = None
    unusable: str | None = None
    memory_hold: str | None = None
    running_by_kind: tuple[tuple[str, int], ...] = ()

    def room(self) -> int:
        """New workers this computer can take for this consumer now."""
        if not self.usable() or self.memory_hold:
            return 0
        free = max(0, (self.max_threads or 0) - max(0, self.running))
        return free if self.capacity is None else max(0, min(free, self.capacity))

    def usable(self) -> bool:
        return self.online and not self.unusable and self.max_threads is not None

    def state(self) -> str:
        """Why this computer has no room, in words."""
        if not self.online:
            return f"{self.name} is offline"
        if self.unusable:
            return self.unusable
        if self.max_threads is None:
            return f"{self.name} hasn't shared its worker limit"
        free = max(0, self.max_threads - max(0, self.running))
        if free and self.memory_hold:
            return f"{self.name} is waiting for free memory ({self.memory_hold})"
        load = f"{self.running} of {self.max_threads} running"
        kinds = dict(self.running_by_kind)
        # Only counts that add up to the total, so the reason never disagrees with it.
        words = breakdown(kinds) if sum(kinds.values()) == self.running else ""
        if words:
            load += f": {words}"
        if free and self.capacity is not None and self.capacity < free:
            return f"{self.name} is busy ({load})"
        return f"{self.name} is full ({load})"


@dataclass(frozen=True)
class Consumer:
    """Rem or one organization: its strategy and the computers assigned to it."""

    id: str
    strategy: str = DEFAULT_STRATEGY
    computers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Placement:
    """The engine's answer: a computer, or a wait with its reason."""

    consumer: str
    strategy: str
    computer_id: str | None
    computer_name: str | None
    reason: str
    # Set when a resumed run couldn't go back to its computer: why it moved.
    moved: str | None = None
    # Each computer the strategy could use that had no room, with why.
    skipped: tuple[tuple[str, str], ...] = field(default=())

    @property
    def wait(self) -> bool:
        return self.computer_id is None

    def record(self, decided_at: str) -> dict[str, Any]:
        """What a run keeps about where it runs, for review."""
        entry: dict[str, Any] = {
            "computer_id": self.computer_id,
            "computer_name": self.computer_name,
            "strategy": self.strategy,
            "consumer": self.consumer,
            "decided_at": decided_at,
            "reason": self.reason,
        }
        if self.moved:
            entry["moved"] = self.moved
        return entry


def _candidates(consumer: Consumer, computers: Sequence[Computer]) -> list[Computer]:
    """The computers this consumer's strategy may use, in the order it tries them."""
    assigned = set(consumer.computers)
    mine = [c for c in computers if c.id in assigned]
    if consumer.strategy == MANUAL:
        return mine
    if consumer.strategy == BALANCED:
        return mine or list(computers)
    # Overflow: its own computers first, then the others, each in the user's order.
    return mine + [c for c in computers if c.id not in assigned]


def candidates(consumer: Consumer, computers: Sequence[Computer]) -> list[Computer]:
    """The computers this consumer's strategy may use, in the order it tries them."""
    return _candidates(consumer, computers)


def _choose(strategy: str, candidates: Sequence[Computer]) -> Computer | None:
    roomy = [c for c in candidates if c.room() > 0]
    if not roomy:
        return None
    if strategy == BALANCED:
        # min() keeps the first of equals, so ties go by the user's order.
        return min(roomy, key=lambda c: Fraction(max(0, c.running), c.max_threads or 1))
    return roomy[0]


def _names(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _memory_wait(candidates: Sequence[Computer]) -> str | None:
    """The memory hold's reason when every computer that could take the start is held.

    A computer held for memory still has room by its limit; one that is full, offline
    or unusable isn't what the start waits for. Only held computers left means the
    start waits for memory, the same reason on every kind of start.
    """
    usable = [c for c in candidates if c.usable()]
    held = [c for c in usable if c.memory_hold and c.running < (c.max_threads or 0)]
    if held and len(held) == len(usable):
        return memory_wait_reason(str(held[0].memory_hold))
    return None


def _wait_reason(consumer: Consumer, candidates: Sequence[Computer], missing: list[str]) -> str:
    if memory := _memory_wait(candidates):
        return memory
    if not candidates:
        if consumer.strategy == MANUAL and not consumer.computers:
            return "Waiting for a computer: none is assigned to this work"
        listed = f" ({_names(missing)} isn't listed)" if missing else ""
        return f"Waiting for a computer: none of its computers is available{listed}"
    states = [c.state() for c in candidates]
    states += [f"{name} isn't listed" for name in missing]
    return "Waiting for a free worker: " + "; ".join(states)


def place(
    consumer: Consumer,
    computers: Sequence[Computer],
    previous: str | None = None,
    *,
    stay: bool = False,
) -> Placement:
    """Pick a computer for one new start, or say why it must wait.

    ``computers`` is in the user's order. ``previous`` is the computer a resumed
    run started on. With ``stay`` the run's work is on that computer, so it never
    moves: it waits for that computer to come back or have room. Each call places one
    start; a caller placing several in a row adds each start to that computer's
    ``running`` before the next call.
    """
    if consumer.strategy not in STRATEGIES:
        raise PlacementEngineError(
            f"Unknown strategy {consumer.strategy!r}; use overflow, balanced or manual"
        )
    ids = [c.id for c in computers]
    if len(set(ids)) != len(ids):
        raise PlacementEngineError("Each computer may appear only once")
    candidates = _candidates(consumer, computers)
    allowed = {c.id for c in candidates}
    missing = [cid for cid in consumer.computers if cid not in ids]
    skipped = tuple((c.id, c.state()) for c in candidates if c.room() <= 0)

    def answer(chosen: Computer | None, reason: str, moved: str | None = None) -> Placement:
        return Placement(
            consumer=consumer.id,
            strategy=consumer.strategy,
            computer_id=chosen.id if chosen else None,
            computer_name=chosen.name if chosen else None,
            reason=reason,
            moved=moved,
            skipped=skipped,
        )

    moved = None
    if previous:
        before = next((c for c in computers if c.id == previous), None)
        if before is not None and before.id in allowed and before.usable():
            if before.room() > 0:
                return answer(before, f"Back on {before.name}, where this run started")
            # Its work is on that computer, so it waits there rather than moving.
            if memory := _memory_wait([before]):
                return answer(None, memory)
            return answer(
                None,
                f"Waiting for a free worker on {before.name}, where this run started: "
                + before.state(),
            )
        if stay:
            # Its work is on that computer, so it waits for it instead of moving.
            if before is None:
                reason = "Waiting for the computer this run started on; it isn't listed anymore"
            elif not before.online:
                reason = f"Waiting for {before.name} to come back; this run's work is there"
            else:
                reason = f"Waiting for {before.name}, where this run's work is: {before.state()}"
            return answer(None, reason)
        if before is None:
            moved = "The computer this run started on isn't listed anymore"
        elif before.id not in allowed:
            moved = f"{before.name} isn't assigned to this work anymore"
        else:
            moved = before.state()

    chosen = _choose(consumer.strategy, candidates)
    if chosen is None:
        reason = _wait_reason(consumer, candidates, missing)
        if moved:
            reason = f"{moved}, so it needs another computer. {reason}"
        return answer(None, reason, moved)
    if consumer.strategy == BALANCED:
        why = (
            f"{chosen.name} has the lightest load "
            f"({chosen.running} of {chosen.max_threads} running)"
        )
    elif consumer.strategy == MANUAL or chosen.id in consumer.computers or not consumer.computers:
        why = f"{chosen.name} is the first computer with room"
    else:
        why = f"Its own computers are full, so it overflowed to {chosen.name}"
    if moved:
        why = f"{moved}, so it moved to {chosen.name}. {why}"
    return answer(chosen, why, moved)


def room_for(consumer: Consumer, computers: Sequence[Computer]) -> int:
    """How many new workers this consumer could start now, over every computer its
    strategy may use: the sum of each one's room, so several starts in a row never ask
    for more than ``place`` would give them."""
    return sum(c.room() for c in _candidates(consumer, computers))


def full_reason(consumer: Consumer, computers: Sequence[Computer]) -> str:
    """Why this consumer has no room, naming each computer it may use that is full.

    For example "Computers full: Workstation 4 of 4 running". With none it may use at
    all (offline, unusable or not listed) it is ``place``'s wait reason instead.
    """
    usable = [c for c in _candidates(consumer, computers) if c.usable()]
    if not usable:
        return place(consumer, computers).reason
    if memory := _memory_wait(usable):
        return memory
    parts = []
    for c in usable:
        free = max(0, (c.max_threads or 0) - max(0, c.running))
        busy = free and c.capacity is not None and c.capacity < free
        load = f"{c.running} of {c.max_threads} running"
        if free and c.memory_hold:
            parts.append(f"{c.name} waiting for free memory ({c.memory_hold})")
        else:
            parts.append(f"{c.name} busy at {load}" if busy else f"{c.name} {load}")
    return "Computers full: " + "; ".join(parts)


def consumer_from(value: Any, consumer_id: str) -> Consumer:
    """A consumer from a saved placement entry; a missing or bad entry is the default.

    A manual entry with no computers would leave nowhere to run, so like the
    placement settings it reads as the default instead.
    """
    if isinstance(value, dict) and value.get("strategy") in STRATEGIES:
        assigned = value.get("computers", [])
        if isinstance(assigned, list) and all(isinstance(i, str) and i for i in assigned):
            computers = tuple(dict.fromkeys(assigned))
            if computers or value["strategy"] != MANUAL:
                return Consumer(consumer_id, value["strategy"], computers)
    return Consumer(consumer_id)


def in_user_order(computers: Sequence[Computer], order: Sequence[str]) -> list[Computer]:
    """Computers in the user's saved order; ones it doesn't name keep their place after."""
    rank = {cid: index for index, cid in enumerate(order)}
    keyed = [
        ((0, rank[c.id]) if c.id in rank else (1, index), c) for index, c in enumerate(computers)
    ]
    return [c for _, c in sorted(keyed, key=lambda item: item[0])]
