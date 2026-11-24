"""Every start on a computer waits while it's short of memory, not only Rem tasks.

With 0.9 GB free and one worker already running, an organization worker, a swarm turn
and organization work another desktop sent each stay waiting with the reason Rem
tasks show, "Waiting for free memory (0.9 GB available)". With no worker running the
first one starts, whatever the memory. Phone turns and your own remote-thread turns
are never held. Swarm turns and incoming work claim against the worker limit lowered
by pressure, as placed starts do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gofer.core import command_slots, computers
from gofer.core.command_slots import SlotSettings, slots_root, write_settings
from gofer.core.worker_claims import ORGANIZATIONS, PHONE, REM, SWARMS, WorkerClaims
from gofer.ui.swarms import SwarmManager
from tests.unit.test_device_chat import full_desktop as full_desktop
from tests.unit.test_device_chat import paired, submit, until, work_state
from tests.unit.test_organization_placement import (
    Clock,
    Gate,
    Registry,
    act,
    company,
    manager_for,
    running,
    runtime,
    wait_idle,
)

# Read when pytest collects this file, before the suite's fixture pins the hold off.
REAL_MEMORY_HOLD = command_slots.memory_hold
HELD = "Waiting for free memory (0.9 GB available)"


@pytest.fixture
def low_memory(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """This computer has 0.9 GB free; set ``[0]`` to change it."""
    free = [int(0.9 * 1024**3)]
    # The suite pins the hold off for host independence; this reads it for real.
    monkeypatch.setattr(command_slots, "memory_hold", REAL_MEMORY_HOLD)
    monkeypatch.setattr(command_slots, "swap_blocked", lambda: None)
    monkeypatch.setattr(command_slots, "memory_available_bytes", lambda: free[0])
    return free


def test_the_claims_hold_every_start_but_the_first_and_your_own_turns(low_memory) -> None:
    claims = WorkerClaims()
    # The first worker on a computer always starts.
    assert claims.held_back("pc-1") is None
    first = claims.claim("pc-1", REM, 4)
    assert first is not None
    assert claims.held_back("pc-1") == HELD
    # Every other start waits, the user's own Rem task too.
    for consumer in (REM, ORGANIZATIONS, SWARMS):
        assert claims.claim("pc-1", consumer, 4) is None
    assert claims.claim("pc-1", REM, 4, first=True) is None
    # Phone turns and your own remote-thread turns are never held.
    phone = claims.claim("pc-1", PHONE, 4, own_only=True)
    assert phone is not None
    own = claims.record("pc-1", PHONE)
    assert claims.running("pc-1") == {"rem": 1, "phone": 2, "organizations": 0, "swarms": 0}
    # Memory frees up: the next start goes ahead.
    low_memory[0] = 8 * 1024**3
    assert claims.held_back("pc-1") is None
    assert claims.claim("pc-1", ORGANIZATIONS, 4) is not None
    for claim in (first, phone, own):
        claim.release()


def dispatch(manager: Any, clock: Clock) -> None:
    """Two dispatch passes: the first asks for the computers' reading, the second uses it."""
    for _ in range(2):
        manager.dispatch()
        clock.now += 1.5


def test_an_organization_worker_waits_for_free_memory(tmp_path: Path, low_memory) -> None:
    clock = Clock()
    gate = Gate()
    # A worker limit of 4 and one Rem worker running: room by the limit, not by memory.
    registry = Registry(limit=4, rem=1)
    manager = manager_for(tmp_path, gate, registry, clock)
    org = company(manager, tmp_path, "Studio")
    task = act(manager, org, "task_create", title="Ship", assignee="w0")
    act(manager, org, "control", state="running")
    try:
        dispatch(manager, clock)
        state = runtime(manager, org)
        assert state["placementWait"] == HELD
        assert gate.started == [] and manager.running_workers() == 0
        assert next(t for t in state["tasks"] if t["id"] == task["id"])["status"] == "todo"
        # The Rem worker ends: the first worker on the computer starts, memory or not.
        registry.rem = 0
        dispatch(manager, clock)
        assert running(manager, org) == 1
        assert "placementWait" not in runtime(manager, org)
    finally:
        gate.release.set()
        wait_idle(manager)
        manager.close()


@pytest.fixture
def swarm_runtime(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    write_settings(slots_root(data), SlotSettings(max_workers=4))
    manager = SwarmManager(data, stream=lambda **_: None)  # type: ignore[arg-type,return-value]
    manager.claims = WorkerClaims()
    manager.computer = lambda: ("pc-1", "Studio")
    try:
        yield manager
    finally:
        manager.close()


def test_a_swarm_turn_waits_for_free_memory(swarm_runtime: SwarmManager, low_memory) -> None:
    claims = swarm_runtime.claims
    assert claims is not None
    # With no worker running, the first turn starts.
    first, waiting = swarm_runtime._take_worker({"organization": None})
    assert first is not None and waiting is None
    # One worker runs: the next turn, and an organization's swarm turn, wait for memory.
    assert swarm_runtime._take_worker({"organization": None}) == (None, HELD)
    assert swarm_runtime._take_worker({"organization": {"id": "org"}}) == (None, HELD)
    first.release()
    # The swarm page shows the run's wait reason.
    swarm: dict[str, Any] = {"run": {}}
    saved: list[str | None] = []

    def save(swarm: dict[str, Any]) -> None:
        saved.append(swarm["run"].get("waitReason"))

    swarm_runtime._save = save  # type: ignore[method-assign]
    swarm_runtime._show_wait(swarm, HELD)
    assert saved == [HELD]


def test_swarm_turns_claim_against_the_limit_lowered_by_pressure(
    swarm_runtime: SwarmManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    claims = swarm_runtime.claims
    assert claims is not None
    # 50% CPU pressure lowers four workers to two, as placement's room does.
    monkeypatch.setattr(computers, "local_pressure", lambda: (50.0, None))
    assert computers.local_room_limit(swarm_runtime.data_dir) == 2
    held = [swarm_runtime._take_worker({"organization": None})[0] for _ in range(2)]
    assert all(held)
    assert swarm_runtime._take_worker({"organization": None}) == (
        None,
        "Waiting for a free worker on Studio",
    )
    for claim in held:
        assert claim is not None
        claim.release()


def job_details(app: Any, desktop: str, job: dict[str, Any]) -> list[str]:
    return [
        e["payload"]["detail"]
        for e in app.pending(desktop)
        if e["type"] == "job.status" and e["request_id"] == job["request_id"]
    ]


def test_incoming_organization_work_waits_for_free_memory(
    full_desktop, tmp_path: Path, low_memory
) -> None:
    app, bridge, provider, rem = full_desktop
    # One of the two Rem workers ends: room by the limit of two, not by memory.
    rem[0].release()
    desktop = paired(app.registry, "desktop")
    job = submit(app, desktop, tmp_path, "job.submit")
    assert not bridge._tick()
    assert not bridge._tick()
    assert work_state(app, job) == "accepted" and provider.started == []
    # The sending desktop is told once why it waits, in Rem's words.
    assert [d for d in job_details(app, desktop, job) if d.startswith("Waiting")] == [HELD]
    # The other Rem worker ends: the first worker on the computer starts.
    rem[1].release()
    assert bridge._tick()
    until(lambda: provider.started)
    assert work_state(app, job) == "running"
    assert bridge.claims.running("pc-1")["organizations"] == 1
    provider.release.set()
    until(lambda: work_state(app, job) == "completed")


def test_incoming_work_is_told_again_when_its_reason_changes(
    full_desktop, tmp_path: Path, low_memory
) -> None:
    app, bridge, _provider, rem = full_desktop
    desktop = paired(app.registry, "desktop")
    job = submit(app, desktop, tmp_path, "job.submit")
    # Full by the limit: it waits for a free worker.
    assert not bridge._tick()
    rem[0].release()
    # A worker is free but memory is short: the reason changes, and is sent once.
    assert not bridge._tick()
    assert not bridge._tick()
    waits = [d for d in job_details(app, desktop, job) if d.startswith("Waiting")]
    assert waits == ["Waiting for a free worker on Workstation", HELD]


def test_incoming_work_claims_against_the_limit_lowered_by_pressure(
    full_desktop, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, bridge, provider, rem = full_desktop
    rem[0].release()
    # One of two workers is free, but 90% memory pressure lowers the limit to one.
    monkeypatch.setattr(computers, "local_pressure", lambda: (None, 90.0))
    desktop = paired(app.registry, "desktop")
    job = submit(app, desktop, tmp_path, "job.submit")
    assert not bridge._tick()
    assert work_state(app, job) == "accepted" and provider.started == []
    monkeypatch.setattr(computers, "local_pressure", lambda: (None, None))
    assert bridge._tick()
    until(lambda: provider.started)
    provider.release.set()
    until(lambda: work_state(app, job) == "completed")


def test_a_rem_task_and_an_organization_share_one_memory_hold(tmp_path: Path, low_memory) -> None:
    """Rem's placement reads the same hold, so its waiting run shows the same reason."""
    from gofer.ui.rem_placement import RemPlacement

    placement = RemPlacement(tmp_path)
    worker = placement.claims.record("this", ORGANIZATIONS)
    try:
        snapshot = placement.snapshot(4)
        assert snapshot is not None
        assert snapshot.room() == 0
        placed = snapshot.place()
        assert placed.wait and placed.reason == HELD
        assert snapshot.full() == HELD
    finally:
        worker.release()
    snapshot = placement.snapshot(4)
    assert snapshot is not None and snapshot.room() == 4
