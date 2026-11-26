"""Every start on this computer claims a worker under one lock, so two can't take the last."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.core.computers import ComputerService
from gofer.core.placement_engine import Computer, Consumer
from gofer.core.placement_reader import Snapshot
from gofer.core.worker_claims import WorkerClaims
from gofer.ui.chat_jobs import ChatJobs
from gofer.ui.chat_steering import ChatSteering
from gofer.ui.device_chat import DeviceChatBridge
from gofer.ui.organizations import OrganizationManager
from gofer.ui.rem_home import RemHome
from tests.helpers.organizations import act
from tests.unit.test_device_chat import Work, request
from tests.unit.test_organization_placement import company
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe, wait_run


def test_sixteen_threads_racing_never_hold_more_than_the_limit():
    claims = WorkerClaims()
    lock = threading.Lock()
    held = 0
    peak = 0
    won = 0
    for _ in range(20):
        start = threading.Barrier(16)

        def race() -> None:
            nonlocal held, peak, won
            start.wait()
            claim = claims.claim("pc-1", "rem", 3)
            if claim is None:
                return
            with lock:
                held += 1
                won += 1
                peak = max(peak, held, claims.held("pc-1"))
            time.sleep(0.002)
            with lock:
                held -= 1
            claim.release()

        threads = [threading.Thread(target=race) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        assert claims.held() == 0
    assert peak <= 3
    assert won >= 20 * 3  # the first three claims of every round get a worker


def test_a_claim_reads_memory_without_holding_the_claims_lock():
    # On macOS the memory reading starts a system command; every other start and count
    # would wait on the lock meanwhile.
    held_during_read: list[bool] = []

    def memory() -> str | None:
        held_during_read.append(claims.lock.locked())
        return "0.9 GB available"

    claims = WorkerClaims(memory=memory)
    first = claims.claim("pc-1", "rem", 4)
    assert first is not None
    # A worker runs and memory is short: the next start waits, as before.
    assert claims.claim("pc-1", "organizations", 4) is None
    assert held_during_read and not any(held_during_read)
    # Phone turns never read memory.
    reads = len(held_during_read)
    assert claims.claim("pc-1", "phone", 4, own_only=True) is not None
    assert len(held_during_read) == reads


def test_every_consumer_counts_against_the_limit_except_a_phone_checking_its_own():
    claims = WorkerClaims()
    rem = claims.claim("pc-1", "rem", 2)
    org = claims.claim("pc-1", "organizations", 2)
    assert rem is not None and org is not None
    assert claims.claim("pc-1", "rem", 2) is None
    assert claims.claim("pc-1", "organizations", 2) is None
    # Phone turns check only their own count, so other work never holds them back.
    phone = claims.claim("pc-1", "phone", 2, own_only=True)
    assert phone is not None
    assert claims.running("pc-1") == {"rem": 1, "phone": 1, "organizations": 1, "swarms": 0}
    # Another computer has its own limit, and a computer without one never has room.
    assert claims.claim("pc-2", "rem", 1) is not None
    assert claims.claim("pc-3", "rem", None) is None
    rem.release()
    rem.release()  # twice is harmless
    assert claims.held("pc-1") == 2


def test_a_worker_that_raises_releases_its_claim():
    claims = WorkerClaims()
    claim = claims.claim("pc-1", "organizations", 1)
    assert claim is not None
    entered = threading.Event()
    go = threading.Event()

    def work() -> None:
        entered.set()
        go.wait(5)
        raise RuntimeError("provider crashed")

    errors: list[BaseException] = []
    previous = threading.excepthook
    threading.excepthook = lambda hook: errors.append(hook.exc_value)
    try:
        thread = threading.Thread(target=claim.wrap(work))
        thread.start()
        assert entered.wait(5)
        # While it runs the claim belongs to the worker's thread and fills the computer.
        assert claim.thread is thread
        assert claims.claim("pc-1", "rem", 1) is None
        go.set()
        thread.join(5)
    finally:
        threading.excepthook = previous
    assert [str(e) for e in errors] == ["provider crashed"]
    assert claim.released
    assert claims.held() == 0
    assert claims.claim("pc-1", "rem", 1) is not None


def test_a_claim_whose_thread_died_is_dropped_on_the_next_read():
    claims = WorkerClaims()
    taken: list[Any] = []

    def leak() -> None:
        # Takes a worker and ends without releasing it, as a crash would.
        taken.append(claims.claim("pc-1", "rem", 1))

    thread = threading.Thread(target=leak)
    thread.start()
    thread.join(5)
    assert taken[0] is not None
    assert claims.running("pc-1") == {"rem": 0, "phone": 0, "organizations": 0, "swarms": 0}
    assert taken[0].released
    assert claims.claim("pc-1", "rem", 1) is not None


def test_a_start_whose_last_worker_was_taken_waits_with_the_engines_reason():
    claims = WorkerClaims()
    computers = [
        Computer("pc-1", "Workstation", 2, 1),
        Computer("desk", "Desk", 4, 0, unusable="x"),
    ]
    other = claims.record("pc-1", "organizations")
    # Two starts read the same counts: one worker left.
    first = Snapshot(Consumer("rem"), list(computers))
    second = Snapshot(Consumer("org:a"), list(computers))
    placed, claim = first.claim(first.place(), claims, "rem")
    assert claim is not None and placed.computer_id == "pc-1"
    waited, none = second.claim(second.place(), claims, "organizations")
    assert none is None and waited.wait
    # The reason says what fills the computer, counted from the claims now.
    assert waited.reason == (
        "Waiting for a free worker: Workstation is full "
        "(2 of 2 running: 1 Rem task, 1 organization worker); x"
    )
    other.release()


def test_phone_turns_hold_a_claim_while_they_run_and_ignore_other_work(tmp_path: Path):
    started, finish = threading.Event(), threading.Event()

    async def source(**kwargs: Any):
        started.set()
        while not finish.is_set():
            await asyncio.sleep(0.01)
        yield {"type": "final", "message": {"body": "answer"}}

    work = Work()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(work, jobs, steering, tmp_path, source=source)
    bridge.computer_id = lambda: "pc-1"
    # Rem and an organization fill this computer; a phone request still runs.
    others = [bridge.claims.record("pc-1", "rem"), bridge.claims.record("pc-1", "organizations")]
    queued = [request(tmp_path)]
    work.claim = lambda **_: queued.pop() if queued else None  # type: ignore[method-assign]
    try:
        assert bridge._tick()
        assert started.wait(5)
        assert bridge.claims.running("pc-1") == {
            "rem": 1,
            "phone": 1,
            "organizations": 1,
            "swarms": 0,
        }
        assert bridge.running() == 1
        finish.set()
        assert work.done.wait(5)
        deadline = time.monotonic() + 5
        while bridge.running() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert bridge.running() == 0
    finally:
        finish.set()
        for claim in others:
            claim.release()
        bridge.close()
        jobs.close()
        steering.close()


def test_a_rem_start_that_fails_before_its_thread_starts_gives_its_claim_back(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Rem's tick runs on a long-lived thread, so a claim it keeps would never be dropped."""
    original = home._update

    def update(run_id: str, **changes: Any) -> Any:
        if "placement" in changes:
            raise sqlite3.OperationalError("database is locked")
        return original(run_id, **changes)

    monkeypatch.setattr(home, "_update", update)
    task = home.create_task({"request_id": "locked", **recipe(tmp_path / "a")})["task"]
    with pytest.raises(sqlite3.OperationalError):
        home.tick()
    assert home.placement.claims.held() == 0
    assert not home.workers
    # Once the database is writable again, the run starts and holds its worker.
    monkeypatch.setattr(home, "_update", original)
    home.tick()
    assert len(home.workers) == 1
    assert wait_run(home, task["id"], {"succeeded", "blocked"})["state"] == "succeeded"
    assert home.placement.claims.held() == 0


def test_rem_and_organizations_ticking_on_separate_threads_never_pass_the_limit(
    home: RemHome, tmp_path: Path
):
    """Rem's tick and organization dispatch share one computer with a limit of 2."""
    home.configure(
        {"provider": "codex", "model": "cli-default", "command_slots": {"maxWorkers": 2}}
    )
    lock = threading.Lock()
    running = 0
    peak = 0
    starts = {"rem": 0, "organizations": 0}

    async def hold(kind: str) -> None:
        nonlocal running, peak
        with lock:
            running += 1
            starts[kind] += 1
            peak = max(peak, running)
        try:
            await asyncio.sleep(0.15)
        finally:
            with lock:
                running -= 1

    original = home.source

    async def rem_source(**kwargs: Any):
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            await hold("rem")
        async for event in original(**kwargs):
            yield event

    async def org_stream(**options: Any):
        await hold("organizations")
        yield {"type": "final", "message": {"body": "Done."}}

    home.source = rem_source
    manager = OrganizationManager(tmp_path / "orgs", stream=org_stream, start_runtime=False)
    # One computer registry and one set of claims, as the UI server wires them.
    claims = WorkerClaims()
    computers = ComputerService(
        home.data_dir,
        name=lambda: "Workstation",
        running=claims.running,
        pressure=lambda: (None, None),
    )
    for placement in (home.placement, manager.placement):
        placement.claims = claims
        placement.computers = computers
    org = company(manager, tmp_path / "studio", "Studio", workers=3, maxConcurrency=3)
    for index in range(3):
        act(manager, org, "task_create", title=f"Ship {index}", assignee=f"w{index}")
    act(manager, org, "control", state="running")
    tasks = [
        home.create_task({"request_id": f"r{i}", **recipe(tmp_path / f"p{i}")})["task"]
        for i in range(4)
    ]
    stop = threading.Event()
    failures: list[BaseException] = []

    def loop(step: Any) -> None:
        while not stop.is_set():
            try:
                step()
            except BaseException as exc:  # noqa: BLE001 - reported below
                failures.append(exc)
                return
            time.sleep(0.005)

    threads = [
        threading.Thread(target=loop, args=(home.tick,), name="rem-ticks"),
        threading.Thread(target=loop, args=(manager.dispatch,), name="org-ticks"),
    ]
    for thread in threads:
        thread.start()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not failures:
            with lock:
                done = starts == {"rem": 4, "organizations": 3} and running == 0
            if done and claims.held() == 0:
                break
            # Every live claim is a worker the computer counts; never more than its limit.
            assert claims.held() <= 2
            time.sleep(0.01)
    finally:
        stop.set()
        for thread in threads:
            thread.join(10)
        manager.close()
    assert not failures, failures
    assert starts == {"rem": 4, "organizations": 3}
    assert 1 <= peak <= 2
    states = {t["id"]: t["last_run"]["state"] for t in home.store.tasks()}
    assert all(states[t["id"]] != "queued" for t in tasks)


@pytest.fixture(autouse=True)
def _no_leaked_threads():
    yield
    # Workers from these tests end on their own; give them a moment.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(
        t.name.startswith(("rem-task-", "employee-")) for t in threading.enumerate()
    ):
        time.sleep(0.02)
