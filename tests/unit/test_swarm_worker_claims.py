"""Swarm turns take a worker on this computer, sharing its limit with Rem and organizations."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from gofer.core.command_slots import SlotSettings, slots_root, write_settings
from gofer.core.computers import ComputerService
from gofer.core.worker_claims import ORGANIZATIONS, REM, SWARMS, Claim, WorkerClaims
from gofer.ui.rem_swarms import RemSwarmAccess
from gofer.ui.swarms import SwarmManager

COMPUTER = "00000000-0000-4000-8000-000000000001"
WAITING = "Waiting for a free worker on Studio"


def wait_for(predicate: Callable[[], bool], seconds: float = 6.0) -> float:
    """Wait until ``predicate`` holds; return how long it took."""
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if predicate():
            return time.monotonic() - started
        time.sleep(0.01)
    raise AssertionError("Timed out")


class RemWorker:
    """A Rem worker running on its own thread, holding its claim until told to end."""

    def __init__(self, claims: WorkerClaims, limit: int) -> None:
        self.claims = claims
        self.limit = limit
        self.claim: Claim | None = None
        self.ready = threading.Event()
        self.end = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self.ready.wait(3) and self.claim is not None

    def _run(self) -> None:
        self.claim = self.claims.claim(COMPUTER, REM, self.limit)
        self.ready.set()
        self.end.wait(30)
        if self.claim is not None:
            self.claim.release()

    def finish(self) -> None:
        self.end.set()
        self.thread.join(3)


class Turns:
    """A fake provider: each turn records itself and runs until released."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.release = threading.Event()

    async def stream(self, **kwargs: Any) -> Any:
        self.started.append(str(kwargs.get("agent_instructions", ""))[:40])
        while not self.release.is_set() and not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "final", "message": {"body": "Done"}}


@pytest.fixture
def one_worker(tmp_path: Path) -> Iterator[tuple[SwarmManager, WorkerClaims, Turns]]:
    """A swarm runtime on a computer with one worker, sharing the server's claims."""
    data = tmp_path / "data"
    data.mkdir()
    write_settings(slots_root(data), SlotSettings(max_workers=1))
    turns = Turns()
    claims = WorkerClaims()
    manager = SwarmManager(data, stream=turns.stream)
    manager.claims = claims
    manager.computer = lambda: (COMPUTER, "Studio")
    try:
        yield manager, claims, turns
    finally:
        turns.release.set()
        manager.close()


def team() -> dict[str, Any]:
    return {
        "name": "Build team",
        "agents": [
            {"id": "lead", "name": "Lead", "role": "Coordinate", "isOrchestrator": True},
            {"id": "worker", "name": "Worker", "role": "Implement"},
        ],
    }


def call(access: RemSwarmAccess, action: str, swarm_id: str = "", **params: Any) -> Any:
    return access.call("swarm_action", {"action": action, "swarmId": swarm_id, "params": params})


def in_worker_thread(work: Callable[[], Any], holding: RemWorker) -> Any:
    """Run ``work`` while ``holding``'s claim is held; it must return without waiting."""
    result: dict[str, Any] = {}

    def run() -> None:
        assert holding.claim is not None and not holding.claim.released
        result["value"] = work()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(3)
    assert not thread.is_alive(), "a swarm action waited on the swarm's own turns"
    return result["value"]


def test_a_queued_delivery_waits_for_the_rem_workers_claim_then_starts_within_a_tick(
    one_worker: tuple[SwarmManager, WorkerClaims, Turns], tmp_path: Path
) -> None:
    manager, claims, turns = one_worker
    rem = RemWorker(claims, limit=1)
    access = RemSwarmAccess(manager, str(tmp_path))
    try:
        # Rem's worker holds the computer's only worker while it starts the swarm. The
        # action returns at once instead of waiting for the swarm's first turn.
        sid = in_worker_thread(lambda: call(access, "create", **team())["id"], rem)
        in_worker_thread(lambda: call(access, "start", sid, task="Build"), rem)
        wait_for(lambda: manager.get(tmp_path, sid)["run"].get("waitReason") == WAITING)
        run = manager.get(tmp_path, sid)["run"]
        assert [d["state"] for m in run["messages"] for d in m["deliveries"]] == ["queued"]
        assert run["state"] == "running" and not turns.started
        assert claims.running(COMPUTER) == {"rem": 1, "phone": 0, "organizations": 0, "swarms": 0}
        # Rem sees why in its summary of the run.
        assert call(access, "read", sid)["run"]["waitReason"] == WAITING
        # More actions from the same worker still never wait on the turns.
        in_worker_thread(
            lambda: call(access, "message", sid, body="Also check docs", recipientId="worker"),
            rem,
        )
        time.sleep(0.3)
        assert not turns.started
        # The worker ends: the delivery starts on the runtime's next pass (at most 1 s).
        rem.finish()
        assert wait_for(lambda: len(turns.started) == 1) < 1.5
        assert claims.running(COMPUTER)["swarms"] == 1
        # Settings › Computers names the turn by its swarm.
        assert {"kind": "swarms", "label": "Build team"} in claims.running_work(COMPUTER)
        wait_for(lambda: "waitReason" not in manager.get(tmp_path, sid)["run"])
        # The second agent's delivery still waits: one worker, and the lead has it.
        time.sleep(0.3)
        assert len(turns.started) == 1
        turns.release.set()
        wait_for(lambda: len(turns.started) >= 2)
        wait_for(lambda: not manager._active)
        wait_for(lambda: claims.held() == 0)
    finally:
        rem.finish()


def test_the_api_counts_a_running_swarm_turn_as_swarms(
    one_worker: tuple[SwarmManager, WorkerClaims, Turns], tmp_path: Path, monkeypatch
) -> None:
    from tests.unit import test_ui_server as ui_tests

    manager, claims, turns = one_worker
    real = ui_tests._fake_server

    def with_computers(path: Path, **options: Any) -> Any:
        server = real(path, **options)
        server.worker_claims = claims
        server.computers = ComputerService(
            manager.data_dir,
            name=lambda: "Studio",
            device_id=lambda: COMPUTER,
            running=server._running_workers,
            pressure=lambda: (None, None),
        )
        return server

    monkeypatch.setattr(ui_tests, "_fake_server", with_computers)
    swarm = manager.create(tmp_path, team())
    manager.start(tmp_path, swarm["id"], "Build")
    wait_for(lambda: len(turns.started) == 1)
    listed = ui_tests._request(tmp_path, "GET", "/api/computers")
    assert listed.status == 200
    [this] = cast(dict[str, Any], listed.json())["computers"]
    assert this["running_by_kind"] == {"rem": 0, "phone": 0, "organizations": 0, "swarms": 1}
    assert this["running_threads"] == 1 and this["capacity"] == 0
    turns.release.set()
    wait_for(lambda: claims.held() == 0)

    def swarms_running() -> int:
        body = cast(dict[str, Any], ui_tests._request(tmp_path, "GET", "/api/computers").json())
        return int(body["computers"][0]["running_by_kind"]["swarms"])

    # The turn's own record ends just after its worker claim, so a busy computer can
    # still count it for a moment.
    wait_for(lambda: swarms_running() == 0)


def test_an_organizations_swarm_turn_counts_as_organizations(tmp_path: Path) -> None:
    manager = SwarmManager(tmp_path / "data", start_runtime=False)
    try:
        claims = WorkerClaims()
        manager.claims = claims
        manager.computer = lambda: (COMPUTER, "Studio")
        manager.worker_limit = lambda: 2
        scoped = {"organization": {"organizationId": "org-1", "turnLimit": 5}}
        org_turn, reason = manager._take_worker(scoped)
        assert org_turn is not None and org_turn.consumer == ORGANIZATIONS and reason is None
        own_turn, reason = manager._take_worker({})
        assert own_turn is not None and own_turn.consumer == SWARMS and reason is None
        # The computer is full now: the next turn waits and says where.
        assert manager._take_worker({}) == (None, WAITING)
        # The user's own Rem task waiting there comes first, and the reason says so.
        org_turn.release()
        claims.wait("run-1", [COMPUTER], "Fix the login page")
        blocked, reason = manager._take_worker({})
        assert blocked is None and reason is not None and "Fix the login page" in reason
    finally:
        manager.close()


def test_a_manager_without_shared_claims_is_limited_only_by_its_own_cap(tmp_path: Path) -> None:
    manager = SwarmManager(tmp_path / "data", start_runtime=False, max_concurrency=4)
    try:
        assert manager.claims is None
        assert manager._take_worker({}) == (None, None)
    finally:
        manager.close()


def test_explaining_an_idle_run_waits_for_a_worker_and_gives_it_back(
    one_worker: tuple[SwarmManager, WorkerClaims, Turns], tmp_path: Path
) -> None:
    manager, claims, turns = one_worker
    rem = RemWorker(claims, limit=1)
    try:
        swarm = manager.create(tmp_path, team())
        sid = swarm["id"]
        manager.start(tmp_path, sid, "Build")
        with manager._lock:
            current = manager._get(tmp_path, sid)
            for message in current["run"]["messages"]:
                for delivery in message["deliveries"]:
                    delivery["state"] = "board_only"
            current["run"]["state"] = "failed"
            current["run"]["idleDiagnosis"] = {"state": "pending"}
            manager._save(current)
        time.sleep(0.5)
        assert not turns.started
        assert manager.get(tmp_path, sid)["run"]["idleDiagnosis"]["state"] == "pending"
        turns.release.set()
        rem.finish()
        wait_for(lambda: manager.get(tmp_path, sid)["run"]["idleDiagnosis"]["state"] != "pending")
        assert len(turns.started) == 1
        wait_for(lambda: claims.held() == 0)
    finally:
        rem.finish()


def test_a_turns_worker_is_given_back_when_saving_its_wait_fails(
    one_worker: tuple[SwarmManager, WorkerClaims, Turns], tmp_path: Path
) -> None:
    manager, claims, turns = one_worker
    seen: list[tuple[int, str | None]] = []
    real = manager._show_wait

    def failing(swarm: dict[str, Any], reason: str | None) -> None:
        # Saving fails (e.g. "database is locked") right after the turn took its worker.
        seen.append((claims.held(COMPUTER), reason))
        raise RuntimeError("database is locked")

    manager._show_wait = failing  # type: ignore[method-assign]
    swarm = manager.create(tmp_path, team())
    manager.start(tmp_path, swarm["id"], "Build")
    wait_for(lambda: len(seen) >= 3)
    manager._show_wait = real  # type: ignore[method-assign]
    # Every pass found the computer's one worker free: no failed pass kept it.
    assert all(reason is None for _, reason in seen[:3]), seen
    wait_for(lambda: len(turns.started) == 1)
    turns.release.set()
    wait_for(lambda: claims.held() == 0)
