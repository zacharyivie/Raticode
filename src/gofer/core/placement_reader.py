"""The computers a start can be placed on, read from the registry for any consumer.

Rem (gofer.ui.rem_placement) and organizations (gofer.ui.organizations) both place
their starts with the placement engine (gofer.core.placement_engine). This module is
the part they share: it reads the computer registry (gofer.core.computers) and the
placement settings (gofer.core.placement_settings), and builds the engine's list of
computers for one consumer, ``rem`` or ``org:<organization ID>``.

Every consumer's workers count on the computer they share. This computer's workers
are counted live from the worker claims (gofer.core.worker_claims) by kind (``rem``,
``phone``, ``organizations``, ``swarms``), not from the registry's reading, which can be up to
``STALE_SECONDS`` old: a worker that started or ended since the reading is counted
as it is now. CPU and memory pressure lower the room through ``computers.capacity``,
and a computer short of memory with a worker running has none (its ``memory_hold``:
this computer's from the claims, a paired desktop's from the reading it shared), so
every start waits with "Waiting for free memory (…)".
Each paired desktop is checked for each start (gofer.core.placement_eligibility):
pairing, its status and worker limit, the catalog it shared, the start's provider and
project, and for an organization the owner's execution target. It stays unusable with
the first failed check as its reason, or, when every check passes, a reason saying
work doesn't start on other computers yet.

A start then takes its worker with ``Snapshot.claim``, under the claims' one lock, so
two starts on different threads can't both take the last worker. When another start
took it first, the computer counts as full and the engine places again or says why
it waits. The user's own Rem runs go first: while one waits for a worker on a
computer, every other start there waits too, and ``Snapshot.yielding`` says so.

The registry is read at most once a second and only after a consumer asked for a
snapshot (``wanted``), because it reads paired desktops under the device service's
lock. Callers read it with ``refresh`` outside their own locks and build snapshots
inside them. While no reading can be used, ``snapshot`` returns None and
``wait_reason`` says why once that lasts.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from gofer.core import placement_settings, repository_fingerprint
from gofer.core.computers import capacity
from gofer.core.placement_eligibility import Eligibility, Start, check, reason_for
from gofer.core.placement_engine import (
    MANUAL,
    Computer,
    Consumer,
    Placement,
    candidates,
    consumer_from,
    full_reason,
    in_user_order,
    place,
    room_for,
)
from gofer.core.worker_claims import CONSUMERS, Claim, WorkerClaims, waiting_reason

log = logging.getLogger(__name__)

# How often the registry is read while a start waits, and how old a reading may be.
REFRESH_SECONDS = 1.0
STALE_SECONDS = 3.0
# How long starts may wait for a reading before the waiting work shows why.
GRACE_SECONDS = 2.0


class Snapshot:
    """The computers for one placement pass; each start is added before the next.

    ``desktops`` holds the registry's reading of each paired desktop by ID, so each
    start gets its own reason for every desktop it can't use.
    """

    def __init__(
        self,
        consumer: Consumer,
        computers: list[Computer],
        desktops: Mapping[str, Mapping[str, Any]] | None = None,
        fingerprint: Callable[[str | None], str | None] = repository_fingerprint.known,
    ) -> None:
        self.consumer = consumer
        self.computers = computers
        self.desktops = dict(desktops or {})
        self.fingerprint = fingerprint

    def for_start(self, start: Start | None) -> list[Computer]:
        """The computers as this start sees them: each paired desktop with its reason."""
        if start is None or not self.desktops:
            return self.computers
        return [
            replace(c, unusable=reason_for(self.desktops[c.id], start, self.fingerprint))
            if c.id in self.desktops
            else c
            for c in self.computers
        ]

    def place(self, previous: str | None = None, start: Start | None = None) -> Placement:
        """Place one start; with ``start`` each paired desktop is checked for it.

        A run that started on a paired desktop has its work there, so it never moves:
        it waits for that desktop instead.
        """
        stay = previous is not None and previous in self.desktops
        return place(self.consumer, self.for_start(start), previous, stay=stay)

    def pinned(self, computer_id: str) -> Snapshot:
        """This snapshot for a start the user pinned to one computer: only that one."""
        consumer = Consumer(self.consumer.id, MANUAL, (computer_id,))
        return Snapshot(consumer, self.computers, self.desktops, self.fingerprint)

    def desktop(self, computer_id: str | None) -> bool:
        """Whether ``computer_id`` is a paired desktop rather than this computer."""
        return computer_id is not None and computer_id in self.desktops

    def eligibility(self, computer_id: str, start: Start) -> Eligibility:
        """Every check of ``start`` on one paired desktop, with the project it matched."""
        return check(self.desktops[computer_id], start, self.fingerprint)

    def room(self) -> int:
        """New workers this consumer could start now, summed over the computers it may use."""
        return room_for(self.consumer, self.computers)

    def full(self) -> str:
        """Why this consumer has no room, naming each full computer it may use."""
        return full_reason(self.consumer, self.computers)

    def usable(self) -> list[Computer]:
        """The computers this consumer's strategy may use and can run work on."""
        return [c for c in candidates(self.consumer, self.computers) if c.usable()]

    def yielding(self, claims: WorkerClaims) -> str | None:
        """Why this consumer's start waits for the user's own Rem runs, or None.

        Set while a run the user started waits for a worker on a computer this
        consumer could use; that computer goes to it first.
        """
        titles = claims.waiting(c.id for c in self.usable())
        return waiting_reason(titles) if titles else None

    def started(self, computer_id: str, kind: str | None = None) -> None:
        for index, computer in enumerate(self.computers):
            if computer.id == computer_id:
                running = computer.running + 1
                left = None if computer.capacity is None else max(0, computer.capacity - 1)
                kinds = dict(computer.running_by_kind)
                if kind is not None and kinds:
                    kinds[kind] = kinds.get(kind, 0) + 1
                self.computers[index] = replace(
                    computer,
                    running=running,
                    capacity=left,
                    running_by_kind=tuple(kinds.items()) if kind is not None else (),
                )

    def claim(
        self,
        placed: Placement,
        claims: WorkerClaims,
        kind: str,
        previous: str | None = None,
        start: Start | None = None,
        *,
        first: bool = False,
        label: str = "",
    ) -> tuple[Placement, Claim | None]:
        """Take the worker ``placed`` chose, right before the start.

        Returns the placement the start keeps and its claim, or a wait and None. When
        another start took the computer's last worker since this snapshot was built,
        or a run the user started waits there and this start isn't ``first`` (one the
        user started), that computer counts as full and the engine places again, so
        the start goes to another computer with room or waits with the engine's reason.
        ``start`` is the one ``placed`` was placed for, so placing again checks paired
        desktops for it too. ``label`` names the worker (see ``WorkerClaims.running_work``).
        """
        for _ in range(len(self.computers) + 1):
            if placed.wait or placed.computer_id is None:
                return placed, None
            computer = next(c for c in self.computers if c.id == placed.computer_id)
            claim = claims.claim(computer.id, kind, computer.max_threads, first=first, label=label)
            if claim is not None:
                self.started(computer.id, kind)
                return placed, claim
            index = self.computers.index(computer)
            memory = claims.memory_hold(computer.id)
            if memory and claims.held(computer.id) < (computer.max_threads or 0):
                # Short of memory since the snapshot: held, not full.
                self.computers[index] = replace(computer, memory_hold=memory)
            else:
                # Full now: count every worker claimed on it, then ask the engine again.
                running = max(computer.running, claims.held(computer.id), computer.max_threads or 0)
                # This computer's kinds are counted live; a paired desktop keeps its reading's.
                kinds = (
                    computer.running_by_kind
                    if self.desktop(computer.id)
                    else tuple(claims.running(computer.id).items())
                )
                self.computers[index] = replace(
                    computer, running=running, capacity=0, running_by_kind=kinds
                )
            placed = self.place(previous, start)
        return placed, None


class PlacementReader:
    """Reads the registry for placement and builds each consumer's snapshot."""

    def __init__(self, data_dir: Path, clock: Callable[[], float] = time.monotonic) -> None:
        self.data_dir = data_dir
        self.clock = clock
        # This computer's running workers; the UI server shares one with every consumer.
        self.claims = WorkerClaims()
        # The UI server's ComputerService; None in tests and before the server sets it.
        self.computers: Any = None
        self.view: dict[str, Any] | None = None
        self.order: list[str] = []
        self.entries: dict[str, dict[str, Any]] = {}
        self.read_at = float("-inf")
        # The last attempt, failed or not, so a failing read is retried once a second.
        self.tried_at = float("-inf")
        self.wanted = False
        # Why the last snapshot had no computers, and since when it has had none.
        self.cause: str | None = None
        self.unready_since: float | None = None
        self.error: str | None = None
        self.asked_at = float("-inf")

    def refresh(self) -> None:
        """Read the registry if a start asked for placement since the last read."""
        if self.computers is None or not self.wanted:
            return
        now = self.clock()
        if now - self.tried_at < REFRESH_SECONDS:
            return
        self.wanted = False
        self.tried_at = now
        try:
            self.view = self.computers.read()
            self.order, self.entries = placement_settings.stored(self.data_dir)
            self.read_at = now
            self.error = None
        except Exception as exc:
            # Without a reading, starts wait rather than go past a limit.
            if self.error is None:
                log.warning("Couldn't read the computers for placement: %s", exc)
            self.view = None
            self.error = str(exc) or type(exc).__name__

    def ask(self) -> None:
        """Note that a start wants placing, so ``refresh`` reads the registry."""
        self.wanted = True
        now = self.clock()
        if now - self.asked_at > STALE_SECONDS:
            # Nothing asked for a while, so an old wait doesn't carry over.
            self.unready_since = None
        self.asked_at = now

    def registry_snapshot(
        self,
        consumer_id: str,
    ) -> Snapshot | None:
        """One consumer's computers from the latest reading, or None until one is in.

        Call it only when a start needs placing: it asks ``refresh`` for a reading.
        This computer's workers are counted now from ``claims``, every consumer's,
        instead of the reading's counts. Each paired desktop starts with the reason
        that doesn't depend on a start; ``Snapshot.place`` with a start checks it for
        that start.
        """
        self.ask()
        if self.view is None:
            self._unready(
                f"Waiting for the computers' reading: {self.error}"
                if self.error
                else "Waiting for the computers' reading"
            )
            return None
        age = self.clock() - self.read_at
        if age > STALE_SECONDS:
            self._unready(
                f"Waiting for a fresh reading of the computers (the last is {int(age)} s old)"
            )
            return None
        items: list[Computer] = []
        desktops: dict[str, Mapping[str, Any]] = {}
        local_id = None
        for reading in self.view.get("computers") or []:
            online = reading.get("state") == "online"
            if reading.get("this_computer"):
                local_id = reading["id"]
                kinds = reading.get("running_by_kind") or {}
                live = self.claims.running(local_id)
                # Kinds the claims don't know (none yet) still count from the reading.
                others = sum(int(v) for k, v in kinds.items() if k not in CONSUMERS)
                running = others + sum(live.values())
                items.append(
                    Computer(
                        reading["id"],
                        reading.get("name") or "This computer",
                        reading.get("max_threads"),
                        running,
                        online=True,
                        capacity=capacity(
                            online=True,
                            max_threads=reading.get("max_threads"),
                            running=running,
                            cpu_pressure=reading.get("cpu_pressure"),
                            memory_pressure=reading.get("memory_pressure"),
                        ),
                        memory_hold=self.claims.memory_hold(local_id),
                        running_by_kind=tuple(
                            {
                                **{k: int(v) for k, v in kinds.items() if k not in CONSUMERS},
                                **live,
                            }.items()
                        ),
                    )
                )
                continue
            name = reading.get("name") or "Desktop"
            desktops[reading["id"]] = reading
            items.append(
                Computer(
                    reading["id"],
                    name,
                    reading.get("max_threads"),
                    int(reading.get("running_threads") or 0),
                    online=online,
                    capacity=reading.get("capacity"),
                    unusable=reason_for(reading, None),
                    memory_hold=reading.get("memory_hold"),
                    # Counts only; None from an older desktop, so its reason gives the total.
                    running_by_kind=tuple((reading.get("running_by_kind") or {}).items()),
                )
            )
        if local_id is None:
            self._unready("Waiting for the computers' reading: it doesn't list this computer")
            return None
        consumer = consumer_from(self.entries.get(consumer_id), consumer_id)
        return self.ready(Snapshot(consumer, in_user_order(items, self.order), desktops))

    def ready(self, snapshot: Snapshot) -> Snapshot:
        self.cause, self.unready_since = None, None
        return snapshot

    def _unready(self, cause: str) -> None:
        self.cause = cause
        if self.unready_since is None:
            self.unready_since = self.clock()

    def wait_reason(self) -> str | None:
        """Why starts can't be placed, once that has lasted past the first pass.

        The first pass after a quiet spell has no fresh reading yet and waits silently
        for the next one. A failed read says so at once, and anything that lasts
        ``GRACE_SECONDS`` gets its cause shown on the waiting work.
        """
        if self.cause is None or self.unready_since is None:
            return None
        if self.error or self.clock() - self.unready_since >= GRACE_SECONDS:
            return self.cause
        return None
