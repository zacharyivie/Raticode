"""Organization starts are placed like Rem's: each computer's worker limit is shared."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.core import placement_settings
from gofer.core.placement_reader import PlacementReader
from gofer.core.worker_claims import Claim, WorkerClaims
from gofer.ui.organizations import OrganizationManager
from gofer.ui.rem_placement import RemPlacement
from tests.helpers.organizations import act


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Registry:
    """The computer registry: this computer, its worker limit and Rem's workers, and a desktop.

    Every worker holds a real claim in ``claims``, which the manager's placement
    shares, as the server shares one set of claims with every consumer. ``rem`` sets
    how many Rem workers hold one.
    """

    def __init__(
        self,
        limit: int,
        rem: int,
        manager: OrganizationManager | None = None,
        desktop: str = "desk-b",
        catalog: dict[str, Any] | None = None,
    ) -> None:
        self.limit = limit
        self.claims = WorkerClaims()
        self.held: list[Claim] = []
        self.rem = rem
        self.manager = manager
        self.desktop = desktop
        # What Desktop B shared with this computer; None until it answers.
        self.catalog = catalog
        self.reads = 0

    @property
    def rem(self) -> int:
        return len(self.held)

    @rem.setter
    def rem(self, count: int) -> None:
        while len(self.held) > count:
            self.held.pop().release()
        while len(self.held) < count:
            self.held.append(self.claims.record("pc-1", "rem"))

    def read(self) -> dict[str, Any]:
        self.reads += 1
        kinds = self.claims.running("pc-1")
        running = sum(kinds.values())
        return {
            "computers": [
                {
                    "id": "pc-1",
                    "name": "Workstation",
                    "this_computer": True,
                    "state": "online",
                    "max_threads": self.limit,
                    "running_threads": running,
                    "cpu_pressure": None,
                    "memory_pressure": None,
                    "capacity": max(0, self.limit - running),
                    "running_by_kind": kinds,
                },
                {
                    "id": self.desktop,
                    "name": "Desktop B",
                    "this_computer": False,
                    "state": "online",
                    "max_threads": 4,
                    "running_threads": 0,
                    "capacity": 4,
                    "catalog": self.catalog,
                },
            ]
        }


class Gate:
    """A worker stream that stays running until released."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started: list[str] = []

    async def __call__(self, **options: Any) -> Any:
        self.started.append(options["workflow"]["organizationRun"]["taskId"])
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        yield {"type": "final", "message": {"body": "Done."}}


def manager_for(
    tmp_path: Path, stream: Any, registry: Registry, clock: Clock
) -> OrganizationManager:
    manager = OrganizationManager(tmp_path / "data", stream=stream, start_runtime=False)
    manager.placement.clock = clock
    manager.placement.computers = registry
    manager.placement.claims = registry.claims
    registry.manager = manager
    return manager


# Not the shared company: a named organization with any number of workers, for placement.
def company(manager: OrganizationManager, root: Path, name: str, workers: int = 1, **config: Any):
    root.mkdir(parents=True, exist_ok=True)
    return manager.call(
        str(root),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": name,
                    "employees": [{"id": f"w{i}", "name": f"Worker {i}"} for i in range(workers)],
                    "projects": [{"id": "main", "name": "Main"}],
                    **config,
                }
            },
        },
    )


def runtime(manager: OrganizationManager, org: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = manager.store.get("", org["id"])["runtime"]
    return result


def running(manager: OrganizationManager, org: dict[str, Any]) -> int:
    return sum(
        oid == org["id"] and thread.is_alive() for oid, _, thread in manager._active.values()
    )


# Waits until the computer's worker count is back to 0, which these placement tests check.
def wait_workers_stopped(manager: OrganizationManager) -> None:
    until = time.monotonic() + 10
    while manager.running_workers() and time.monotonic() < until:
        time.sleep(0.02)
    assert not manager.running_workers()


def test_dispatch_starts_no_organization_run_while_rem_fills_the_worker_limit(
    tmp_path: Path,
) -> None:
    clock = Clock()
    gate = Gate()
    # A worker limit of 2, and two Rem workers running on this computer.
    registry = Registry(limit=2, rem=2)
    manager = manager_for(tmp_path, gate, registry, clock)
    org = company(manager, tmp_path, "Studio")
    task = act(manager, org, "task_create", title="Ship", assignee="w0")
    act(manager, org, "control", state="running")
    try:
        # The first pass asks for a reading and waits for it silently.
        manager.dispatch()
        assert registry.reads == 0 and not runtime(manager, org).get("placementWait")
        clock.now += 1.5
        manager.dispatch()
        state = runtime(manager, org)
        assert manager.running_workers() == 0 and gate.started == []
        assert next(t for t in state["tasks"] if t["id"] == task["id"])["status"] == "todo"
        assert state["placementWait"] == (
            "Waiting for a free worker: Workstation is full (2 of 2 running: 2 Rem tasks); "
            "Waiting for Desktop B to share its projects"
        )
        # A Rem worker finishes: the organization's worker starts and keeps where it went.
        registry.rem = 1
        clock.now += 1.5
        manager.dispatch()
        state = runtime(manager, org)
        assert running(manager, org) == 1
        assert "placementWait" not in state
        [attempt] = state["attempts"].values()
        assert attempt["placement"]["computer_id"] == "pc-1"
        assert attempt["placement"]["computer_name"] == "Workstation"
        assert attempt["placement"]["strategy"] == "overflow"
        assert attempt["placement"]["consumer"] == f"org:{org['id']}"
        assert attempt["placement"]["reason"] == "Workstation is the first computer with room"
    finally:
        gate.release.set()
        wait_workers_stopped(manager)
        manager.close()


def test_manual_placement_on_a_computer_it_cant_use_starts_nothing(tmp_path: Path) -> None:
    clock = Clock()
    gate = Gate()
    registry = Registry(limit=4, rem=0)
    manager = manager_for(tmp_path, gate, registry, clock)
    org = company(manager, tmp_path, "Studio")
    placement_settings.save(
        manager.data_dir,
        f"org:{org['id']}",
        "manual",
        ["desk-b"],
        known_computers=registry.read()["computers"],
        organizations=[{"id": org["id"], "name": "Studio"}],
    )
    act(manager, org, "task_create", title="Ship", assignee="w0")
    act(manager, org, "control", state="running")
    try:
        for _ in range(3):
            manager.dispatch()
            clock.now += 1.5
        assert manager.running_workers() == 0 and gate.started == []
        assert runtime(manager, org)["placementWait"] == (
            "Waiting for a free worker: Waiting for Desktop B to share its projects"
        )
        # The ticket is cancelled: nothing waits to start, so the reason goes too.
        task = runtime(manager, org)["tasks"][0]
        act(
            manager,
            org,
            "task_update",
            taskId=task["id"],
            expectedRevision=task["revision"],
            changes={"status": "cancelled"},
        )
        manager.dispatch()
        assert "placementWait" not in runtime(manager, org)
    finally:
        gate.release.set()
        wait_workers_stopped(manager)
        manager.close()


def test_rem_and_two_organizations_share_one_computer(tmp_path: Path) -> None:
    clock = Clock()
    gate = Gate()
    # Worker limit 2 with one Rem worker running: room for one organization worker.
    registry = Registry(limit=2, rem=1)
    manager = manager_for(tmp_path, gate, registry, clock)
    first = company(manager, tmp_path / "a", "Alpha")
    second = company(manager, tmp_path / "b", "Beta")
    for org in (first, second):
        act(manager, org, "task_create", title="Ship", assignee="w0")
        act(manager, org, "control", state="running")
    try:
        for _ in range(4):
            manager.dispatch()
            clock.now += 1.5
        # Whichever organization went first took the last worker; the other waits.
        assert manager.running_workers() == 1
        [started] = [org for org in (first, second) if running(manager, org)]
        [held] = [org for org in (first, second) if org is not started]
        assert "placementWait" not in runtime(manager, started)
        # Settings › Computers names the worker by its organization and name.
        [work] = [w for w in registry.claims.running_work() if w["kind"] == "organizations"]
        assert work["label"] in ("Alpha · Worker 0", "Beta · Worker 0")
        assert runtime(manager, held)["placementWait"] == (
            "Waiting for a free worker: Workstation is full "
            "(2 of 2 running: 1 Rem task, 1 organization worker); "
            "Waiting for Desktop B to share its projects"
        )
        # Rem, reading the same computer, has no room either.
        rem = RemPlacement(manager.data_dir, clock=clock)
        rem.computers = registry
        rem.claims = registry.claims
        assert rem.snapshot(2) is None
        rem.refresh()
        snapshot = rem.snapshot(2)
        assert snapshot is not None
        assert snapshot.place().reason.startswith(
            "Waiting for a free worker: Workstation is full "
            "(2 of 2 running: 1 Rem task, 1 organization worker)"
        )
        # Rem's worker finishes and the waiting organization starts.
        registry.rem = 0
        for _ in range(2):
            manager.dispatch()
            clock.now += 1.5
        assert running(manager, first) == 1 and running(manager, second) == 1
        assert not any(runtime(manager, org).get("placementWait") for org in (first, second))
    finally:
        gate.release.set()
        wait_workers_stopped(manager)
        manager.close()


def test_every_consumer_on_this_computer_is_counted_live_from_the_claims(
    tmp_path: Path,
) -> None:
    clock = Clock()
    reader = PlacementReader(tmp_path, clock=clock)
    registry = Registry(limit=4, rem=1)
    reader.computers = registry
    reader.claims = registry.claims
    assert reader.registry_snapshot("org:x") is None
    reader.refresh()
    # The reading has one Rem worker; two organization workers started since.
    started = [registry.claims.record("pc-1", "organizations") for _ in range(2)]
    snapshot = reader.registry_snapshot("org:x")
    assert snapshot is not None
    local = snapshot.computers[0]
    assert (local.running, local.capacity) == (3, 1)
    assert snapshot.computers[1].unusable == "Waiting for Desktop B to share its projects"
    # The Rem worker ends: it stops counting at once, though the reading still has it.
    registry.rem = 0
    assert reader.registry_snapshot("org:x").computers[0].running == 2
    for claim in started:
        claim.release()
    # A stale reading is never used: the start waits and says why.
    clock.now += 5
    assert reader.registry_snapshot("org:x") is None
    clock.now += 2
    assert reader.registry_snapshot("org:x") is None
    assert reader.wait_reason() == (
        "Waiting for a fresh reading of the computers (the last is 7 s old)"
    )


DESK = "00000000-0000-4000-8000-0000000000d2"
THREAD = "00000000-0000-4000-8000-0000000000e1"
PROJECT_THERE = "00000000-0000-4000-8000-0000000000f1"


def fleet_target(**change: Any) -> dict[str, Any]:
    return {
        "id": "workstation",
        "kind": "fleet",
        "employees": ["w0"],
        "deviceId": DESK,
        "threadId": THREAD,
        "projectId": PROJECT_THERE,
        **change,
    }


@pytest.mark.parametrize(
    "targets,provider,reason",
    [
        ([], "codex", "No execution target on Desktop B is granted to Worker 0"),
        (
            [fleet_target(employees=["w1"])],
            "codex",
            "No execution target on Desktop B is granted to Worker 0",
        ),
        (
            [fleet_target(projectId="00000000-0000-4000-8000-0000000000f9")],
            "codex",
            "Worker 0's execution target on Desktop B is for another project than Raticode",
        ),
        (
            [fleet_target()],
            "claude_code",
            "Desktop B doesn't allow Claude Code for this computer's work",
        ),
        (
            [fleet_target()],
            "codex",
            "Desktop B could run this work in its Raticode project, "
            "but work doesn't start on other computers yet",
        ),
    ],
)
def test_an_organization_ticket_needs_the_owners_target_for_its_worker_on_the_desktop(
    tmp_path: Path, targets: list[dict[str, Any]], provider: str, reason: str
) -> None:
    from gofer.core import repository_fingerprint
    from tests.unit.test_rem_placement import git_repository, shared

    root = git_repository(tmp_path / "Raticode")
    here = repository_fingerprint.fingerprint(root)
    clock = Clock()
    gate = Gate()
    catalog = shared({"id": PROJECT_THERE, "name": "Raticode", "fingerprint": here})
    registry = Registry(limit=4, rem=0, desktop=DESK, catalog=catalog)
    manager = manager_for(tmp_path, gate, registry, clock)
    org = manager.call(
        str(root),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Studio",
                    "employees": [
                        {"id": "w0", "name": "Worker 0", "provider": provider},
                        {"id": "w1", "name": "Worker 1"},
                    ],
                    "projects": [{"id": "main", "name": "Main"}],
                    "executionTargets": targets,
                }
            },
        },
    )
    placement_settings.save(
        manager.data_dir,
        f"org:{org['id']}",
        "manual",
        [DESK],
        known_computers=registry.read()["computers"],
        organizations=[{"id": org["id"], "name": "Studio"}],
    )
    act(manager, org, "task_create", title="Ship", assignee="w0")
    act(manager, org, "control", state="running")
    try:
        for _ in range(3):
            manager.dispatch()
            clock.now += 1.5
        # The ticket waits with its own reason, and never starts here or there.
        assert manager.running_workers() == 0 and gate.started == []
        assert runtime(manager, org)["placementWait"] == "Waiting for a free worker: " + reason
        assert runtime(manager, org)["tasks"][0]["status"] == "todo"
    finally:
        gate.release.set()
        wait_workers_stopped(manager)
        manager.close()


def test_an_organization_fleet_run_holds_no_worker_on_the_sending_computer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The paired desktop runs the worker and counts it there, so it counts once."""
    clock = Clock()
    gate = Gate()
    # A worker limit of 2, and two Rem workers fill this computer.
    registry = Registry(limit=2, rem=2, desktop=DESK)
    manager = manager_for(tmp_path, gate, registry, clock)
    org = company(manager, tmp_path / "studio", "Studio", executionTargets=[fleet_target()])
    release = threading.Event()
    polled = threading.Event()
    held_while_polling: list[dict[str, int]] = []

    class Gateway:
        """The paired desktop's job, as the run polls it."""

        def submit(self, request_id: str, text: str, lineage: Any) -> None:
            pass

        def status(self, request_id: str) -> dict[str, Any]:
            held_while_polling.append(registry.claims.running("pc-1"))
            polled.set()
            if not release.is_set():
                return {"state": "queued", "turnsUsed": 0, "usage": {}}
            return {"state": "completed", "text": "Done there.", "turnsUsed": 1, "usage": {}}

        def cancel(self, request_id: str) -> None:
            pass

    monkeypatch.setattr(manager.executions, "gateway", lambda *args: Gateway())
    act(
        manager,
        org,
        "task_create",
        title="Ship",
        assignee="w0",
        execution={"targetId": "workstation"},
    )
    act(manager, org, "control", state="running")
    try:
        manager.dispatch()
        assert polled.wait(10)
        # It runs although this computer is full, and takes no worker here.
        assert running(manager, org) == 1
        assert registry.claims.running("pc-1") == {
            "rem": 2,
            "phone": 0,
            "organizations": 0,
            "swarms": 0,
        }
        assert held_while_polling[0] == {"rem": 2, "phone": 0, "organizations": 0, "swarms": 0}
        assert manager.running_workers() == 0
        state = runtime(manager, org)
        assert "placementWait" not in state
        [attempt] = state["attempts"].values()
        assert attempt["status"] == "running"
        assert "placement" not in attempt
        # A Rem worker frees up: the room is Rem's or a local worker's, not the fleet run's.
        registry.rem = 1
        assert registry.claims.claim("pc-1", "rem", 2) is not None
    finally:
        release.set()
        gate.release.set()
        until = time.monotonic() + 10
        while running(manager, org) and time.monotonic() < until:
            time.sleep(0.02)
        manager.close()
    assert runtime(manager, org)["tasks"][0]["status"] != "in_progress"


def test_a_held_local_ticket_doesnt_hold_back_a_lower_ranked_fleet_ticket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fleet ticket takes no worker here, so it doesn't wait for room here either."""
    clock = Clock()
    gate = Gate()
    # A worker limit of 2, and two Rem workers fill this computer.
    registry = Registry(limit=2, rem=2, desktop=DESK)
    manager = manager_for(tmp_path, gate, registry, clock)
    org = company(
        manager,
        tmp_path / "studio",
        "Studio",
        workers=2,
        # Room in the organization's own limit for both, so only the computer holds one.
        maxConcurrency=2,
        executionTargets=[fleet_target(employees=["w1"])],
    )
    release = threading.Event()
    submitted: list[str] = []

    class Gateway:
        def submit(self, request_id: str, text: str, lineage: Any) -> None:
            submitted.append(lineage["taskId"])

        def status(self, request_id: str) -> dict[str, Any]:
            if not release.is_set():
                return {"state": "queued", "turnsUsed": 0, "usage": {}}
            return {"state": "completed", "text": "Done there.", "turnsUsed": 1, "usage": {}}

        def cancel(self, request_id: str) -> None:
            pass

    monkeypatch.setattr(manager.executions, "gateway", lambda *args: Gateway())
    local = act(manager, org, "task_create", title="Here", assignee="w0", priority="critical")
    there = act(
        manager,
        org,
        "task_create",
        title="There",
        assignee="w1",
        priority="low",
        execution={"targetId": "workstation"},
    )
    act(manager, org, "control", state="running")
    # A fresh reading of the computers is in before the pass, so the hold is for room.
    assert manager.placement.registry_snapshot("org:warm-up") is None
    clock.now += 1.5
    manager.placement.refresh()
    try:
        # One pass: the local ticket ranks first and waits for room, and the pass goes
        # on to the fleet ticket, which starts anyway.
        manager.dispatch()
        state = runtime(manager, org)
        status = {t["id"]: t["status"] for t in state["tasks"]}
        assert status == {local["id"]: "todo", there["id"]: "in_progress"}
        deadline = time.monotonic() + 10
        while not submitted and time.monotonic() < deadline:
            time.sleep(0.02)
        assert submitted == [there["id"]]
        assert state["placementWait"].startswith(
            "Waiting for a free worker: Workstation is full (2 of 2 running: 2 Rem tasks)"
        )
        assert gate.started == []
        assert registry.claims.running("pc-1") == {
            "rem": 2,
            "phone": 0,
            "organizations": 0,
            "swarms": 0,
        }
    finally:
        release.set()
        gate.release.set()
        until = time.monotonic() + 10
        while running(manager, org) and time.monotonic() < until:
            time.sleep(0.02)
        manager.close()
