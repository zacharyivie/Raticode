"""Every worker that starts on this computer holds a claim, taken under one lock.

Rem tasks, phone turns, organization workers and swarm turns share each computer's
worker limit. Placement (gofer.core.placement_engine) picks a computer from counts
read a moment earlier, so two starts on different threads could both see the last
free worker. A claim closes that gap: ``claim`` counts the live claims and adds the
new one under one lock, and returns None when the computer has no room, so only one
of them gets it.

A claim belongs to the thread that runs the worker. ``Claim.wrap`` binds it to that
thread when it starts and releases it in a ``finally`` block when it ends. Until then
it belongs to the thread that took it (Rem's tick, organization dispatch, the phone
bridge or the swarm runtime, which all keep running). Swarm turns run as tasks on
the swarm runtime's thread, so their claims stay with it and each turn releases its
own when it ends. A claim whose thread is no longer alive is dropped the next time
anything reads the claims, so a crash can't leak a worker.

The claims are the exact count of workers running here: the computer registry's
``running_by_kind`` reading (gofer.core.computers) and placement's counts for this
computer (gofer.core.placement_reader) both come from ``running``. The UI server
owns one ``WorkerClaims`` and gives it to every consumer; each consumer has its own
until then, so a test can build one alone. Paired desktops run nothing from here yet,
so every claim is for this computer.

The user's own Rem runs go first. While a run the user started waits for a worker on
a computer, Rem's tick records it with ``wait`` and the claims refuse every other
start there (organization workers, swarm turns and self-work) until it has its worker: only a
claim with ``first`` (a user start) passes. Rem's tick drops a wait when the run
starts or stops waiting (``drop_wait``, ``keep_waits``), and a wait nobody has
renewed for ``WAIT_SECONDS`` lapses on its own, so a stopped tick can't hold the
computer forever. Phone turns, which check only their own limit, never wait for it.

Every start also waits while the computer is short of memory: under
``WORKER_MEMORY_FLOOR`` of free memory or with swap at ``SWAP_USED_LIMIT``
(gofer.core.command_slots.memory_hold), ``claim`` refuses once a worker already runs
there, so the first worker on a computer always starts. ``held_back`` says why, the
same words for Rem tasks, self-work, organization workers, swarm turns and work from
another desktop. Phone turns and your own remote-thread turns (``own_only`` and
``record``) are never held, the user's own start (``first``) only by this.

Each claim carries a short label saying what its worker is: the Rem task's title, the
organization and worker name, the swarm's name, "phone turn" or "work from <desktop>".
``running_work`` lists them for Settings › Computers; ``breakdown`` puts the counts by
kind in words, such as "2 organization workers, 1 Rem task, 1 swarm turn", for the
computer rows and the placement engine's reasons.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from gofer.core import command_slots

__all__ = [
    "CONSUMERS",
    "KIND_WORDS",
    "LABEL_CHARS",
    "ORGANIZATIONS",
    "PHONE",
    "REM",
    "SWARMS",
    "RUNNING_WORK_LIMIT",
    "THIS_COMPUTER",
    "WAIT_SECONDS",
    "Claim",
    "Wait",
    "WorkerClaims",
    "breakdown",
    "clean_label",
    "waiting_reason",
]

REM = "rem"
PHONE = "phone"
ORGANIZATIONS = "organizations"
# Swarm turns outside an organization's run, including those Rem starts with swarm_action.
# A turn of an organization's swarm run counts as ORGANIZATIONS.
SWARMS = "swarms"
# The consumers that start workers here, as the registry counts them by kind.
CONSUMERS = (REM, PHONE, ORGANIZATIONS, SWARMS)
# This computer's ID while the computer registry isn't set (tests, early startup).
THIS_COMPUTER = "this"
# How long a user start's wait holds other starts back after Rem's tick last renewed it.
WAIT_SECONDS = 30.0
# The longest label a claim keeps, and how many labels a computer's reading lists.
LABEL_CHARS = 80
RUNNING_WORK_LIMIT = 20
# Each kind in words, one and many, in the order a breakdown lists equal counts.
KIND_WORDS = {
    REM: ("Rem task", "Rem tasks"),
    PHONE: ("phone turn", "phone turns"),
    ORGANIZATIONS: ("organization worker", "organization workers"),
    SWARMS: ("swarm turn", "swarm turns"),
}


def clean_label(value: object, fallback: str = "") -> str:
    """A claim's label as shown: one line of printable text, at most ``LABEL_CHARS``."""
    text = " ".join(str(value or "").split())
    text = "".join(ch for ch in text if ch.isprintable())
    if len(text) > LABEL_CHARS:
        text = text[: LABEL_CHARS - 1].rstrip() + "…"
    return text or fallback


def breakdown(counts: Mapping[str, int] | None) -> str:
    """Running workers by kind in words, most first: "2 organization workers, 1 Rem task".

    Kinds with none, and kinds it has no words for, are left out; "" when none is left.
    """
    if not counts:
        return ""
    order = list(KIND_WORDS)
    items = [
        (kind, count)
        for kind, count in counts.items()
        if kind in KIND_WORDS and type(count) is int and count > 0
    ]
    items.sort(key=lambda item: (-item[1], order.index(item[0])))
    return ", ".join(
        f"{count} {KIND_WORDS[kind][0] if count == 1 else KIND_WORDS[kind][1]}"
        for kind, count in items
    )


def waiting_reason(titles: list[str]) -> str:
    """Why a start makes way: the user's own Rem tasks waiting for a worker, by title."""
    named = ", ".join(f"“{t}”" for t in titles[:3])
    if len(titles) > 3:
        named += f" and {len(titles) - 3} more"
    tasks = "task" if len(titles) == 1 else "tasks"
    return f"Waiting so your own Rem {tasks} can start first: {named}"


@dataclass
class Wait:
    """A Rem run the user started, waiting for a worker on these computers."""

    key: str
    computers: frozenset[str]
    title: str
    renewed_at: float


class Claim:
    """One worker on one computer, held until its thread ends or it is released."""

    def __init__(
        self,
        owner: WorkerClaims,
        claim_id: int,
        computer_id: str,
        consumer: str,
        label: str = "",
    ):
        self.owner = owner
        self.id = claim_id
        self.computer_id = computer_id
        self.consumer = consumer
        # What the worker is, in a few words (see ``describe``).
        self.label = clean_label(label, KIND_WORDS.get(consumer, ("worker",))[0])
        self.thread = threading.current_thread()
        self.released = False

    def describe(self, label: object) -> None:
        """Name the worker once its work is known, e.g. after an organization picks a ticket."""
        with self.owner.lock:
            self.label = clean_label(label, self.label)

    def bind(self, thread: threading.Thread | None = None) -> None:
        """Tie the claim to the thread running the worker (the current one by default)."""
        with self.owner.lock:
            self.thread = thread or threading.current_thread()

    def release(self) -> None:
        """Give the worker back. Releasing twice is harmless."""
        self.owner.release(self)

    def wrap(self, target: Callable[..., Any]) -> Callable[..., Any]:
        """``target`` as a thread's target that holds this claim while it runs."""

        def run(*args: Any, **kwargs: Any) -> Any:
            self.bind()
            try:
                return target(*args, **kwargs)
            finally:
                self.release()

        return run

    def __repr__(self) -> str:
        return f"Claim({self.consumer!r} on {self.computer_id!r}, id={self.id})"


class WorkerClaims:
    """The claims of every worker running in this process, counted under one lock."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        memory: Callable[[], str | None] | None = None,
    ) -> None:
        self.lock = threading.Lock()
        self.clock = clock
        # Why this computer is short of memory, or None; read only once a worker runs.
        # None reads command_slots.memory_hold when asked, so tests can pin it there.
        self.memory = memory
        self._claims: dict[int, Claim] = {}
        self._ids = itertools.count(1)
        # The user's Rem runs waiting for a worker, by run ID.
        self._waits: dict[str, Wait] = {}

    def _live(self) -> list[Claim]:
        """The claims whose thread is still alive. Called with ``lock`` held."""
        dead = [key for key, claim in self._claims.items() if not claim.thread.is_alive()]
        for key in dead:
            # Its thread ended without releasing it (a crash); the worker is gone.
            self._claims.pop(key).released = True
        return list(self._claims.values())

    def _live_waits(self) -> list[Wait]:
        """The waits renewed within ``WAIT_SECONDS``. Called with ``lock`` held."""
        now = self.clock()
        for key in [k for k, w in self._waits.items() if now - w.renewed_at > WAIT_SECONDS]:
            del self._waits[key]
        return list(self._waits.values())

    def claim(
        self,
        computer_id: str,
        consumer: str,
        limit: int | None,
        *,
        own_only: bool = False,
        first: bool = False,
        label: str = "",
    ) -> Claim | None:
        """Take a worker on a computer, or None when it has no room.

        Every consumer's claims on the computer count against ``limit``. With
        ``own_only`` only this consumer's claims count, as phone turns check theirs.
        A limit of None (a computer that hasn't shared one) never has room. While a
        user start waits on the computer, only a start with ``first`` (one the user
        started) may take a worker there. Unless ``own_only``, a computer short of
        memory takes no new worker once one runs there (``held_back`` says why).
        """
        # Read memory before taking the lock: on macOS it starts a system command, and
        # every other start and count waits on this lock meanwhile.
        short = None if own_only or limit is None else self._memory()
        with self.lock:
            if limit is None:
                return None
            if (
                not first
                and not own_only
                and any(computer_id in w.computers for w in self._live_waits())
            ):
                return None
            here = [c for c in self._live() if c.computer_id == computer_id]
            held = sum(1 for c in here if not own_only or c.consumer == consumer)
            if held >= limit:
                return None
            if here and short is not None:
                return None
            claim = Claim(self, next(self._ids), computer_id, consumer, label)
            self._claims[claim.id] = claim
            return claim

    def _memory(self) -> str | None:
        """Why the computer is short of memory now, or None."""
        if self.memory is not None:
            return self.memory()
        return command_slots.memory_hold()

    def memory_hold(self, computer_id: str) -> str | None:
        """The memory hold's detail on this computer, e.g. "0.9 GB available", or None.

        Set only while a worker already runs there and memory is short, so the first
        worker on a computer always starts.
        """
        with self.lock:
            running = any(c.computer_id == computer_id for c in self._live())
        return self._memory() if running else None

    def held_back(self, computer_id: str) -> str | None:
        """Why a start other than a phone turn waits for memory on this computer, or None.

        For example "Waiting for free memory (0.9 GB available)".
        """
        detail = self.memory_hold(computer_id)
        return command_slots.memory_wait_reason(detail) if detail else None

    def record(self, computer_id: str, consumer: str, label: str = "") -> Claim:
        """Count a worker without checking any limit (the caller checked its own)."""
        with self.lock:
            claim = Claim(self, next(self._ids), computer_id, consumer, label)
            self._claims[claim.id] = claim
            return claim

    def release(self, claim: Claim) -> None:
        with self.lock:
            self._claims.pop(claim.id, None)
            claim.released = True

    def running(self, computer_id: str | None = None) -> dict[str, int]:
        """Live claims by consumer, on one computer or, with None, on all of them.

        Every consumer in ``CONSUMERS`` is listed, with 0 when it has none.
        """
        counts = dict.fromkeys(CONSUMERS, 0)
        with self.lock:
            for claim in self._live():
                if computer_id is None or claim.computer_id == computer_id:
                    counts[claim.consumer] = counts.get(claim.consumer, 0) + 1
        return counts

    def running_work(
        self, computer_id: str | None = None, limit: int = RUNNING_WORK_LIMIT
    ) -> list[dict[str, str]]:
        """What each live worker is, oldest first: ``{"kind", "label"}``, at most ``limit``."""
        with self.lock:
            live = [c for c in self._live() if computer_id is None or c.computer_id == computer_id]
        live.sort(key=lambda c: c.id)
        return [{"kind": c.consumer, "label": c.label} for c in live[: max(0, limit)]]

    def wait(self, key: str, computers: Iterable[str], title: str) -> None:
        """Record (or renew) that the user's run ``key`` waits for a worker on ``computers``."""
        ids = frozenset(computers)
        with self.lock:
            if ids:
                self._waits[key] = Wait(key, ids, title, self.clock())
            else:
                self._waits.pop(key, None)

    def drop_wait(self, key: str) -> None:
        """The run got its worker or no longer waits for one."""
        with self.lock:
            self._waits.pop(key, None)

    def keep_waits(self, keys: Iterable[str]) -> None:
        """Drop every wait but these: the runs still waiting after a pass of Rem's tick."""
        kept = set(keys)
        with self.lock:
            for key in [k for k in self._waits if k not in kept]:
                del self._waits[key]

    def waiting(self, computers: Iterable[str] | None = None) -> list[str]:
        """Titles of the user's runs waiting on any of ``computers`` (None: on any).

        Oldest first, so the reason a start makes way names the run waiting longest.
        """
        wanted = None if computers is None else set(computers)
        with self.lock:
            waits = [w for w in self._live_waits() if wanted is None or w.computers & wanted]
        return [w.title for w in waits]

    def waiting_runs(self) -> list[str]:
        """The IDs of the user's runs waiting for a worker, oldest first."""
        with self.lock:
            return [w.key for w in self._live_waits()]

    def held(self, computer_id: str | None = None, consumer: str | None = None) -> int:
        """How many live claims match the computer and consumer (None matches any)."""
        with self.lock:
            return sum(
                1
                for c in self._live()
                if (computer_id is None or c.computer_id == computer_id)
                and (consumer is None or c.consumer == consumer)
            )
