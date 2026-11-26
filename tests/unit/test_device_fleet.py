"""Desktop-to-desktop fleet over real loopback TLS, with disposable identities."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from gofer.devices.application import _VALIDATOR
from gofer.devices.control import DeviceControl
from gofer.devices.registry import PairingError


class TestSecrets:
    __test__ = False

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, account: str) -> str | None:
        return self.values.get(account)

    def put(self, account: str, value: str) -> None:
        self.values[account] = value


def wait(condition: Callable[[], Any], timeout: float = 15) -> Any:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if result := condition():
            return result
        time.sleep(0.05)
    raise AssertionError("condition not met")


def pair(initiator: DeviceControl, responder: DeviceControl, name: str = "Desk B") -> None:
    """``initiator`` pastes ``responder``'s invitation, as in Pair another desktop."""
    assert responder.invitations is not None and responder.port is not None
    invite = responder.invitations.create(
        name, {"host": "127.0.0.1", "port": responder.port}, role="desktop"
    )
    preview = initiator.action({"action": "preview", "uri": invite["uri"]})
    initiator.action(
        {"action": "pair", "uri": invite["uri"], "fingerprint": preview["fingerprint"]}
    )
    peer = initiator.registry.identity.device_id  # type: ignore[union-attr]
    candidate = wait(
        lambda: next((p for p in responder.status()["peers"] if p["device_id"] == peer), None)
    )
    responder.action(
        {"action": "confirm", "device_id": peer, "fingerprint": candidate["fingerprint"]}
    )
    wait(lambda: initiator.status()["outbound"] == "trust_saved")


def peer_row(control: DeviceControl, peer: DeviceControl) -> dict[str, Any]:
    devices = control.action({"action": "fleet_status"})["devices"]
    return next(d for d in devices if d["device_id"] == identity(peer))


def identity(control: DeviceControl) -> str:
    assert control.registry is not None
    return control.registry.identity.device_id


@pytest.fixture
def desktops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[], Any]]:
    for name in ("RATICODE_DEVICE_EXPERIMENTAL_NETWORK", "RATICODE_DEVICE_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("RATICODE_DEVICE_PORT", raising=False)
    created: list[DeviceControl] = []

    def make() -> DeviceControl:
        control = DeviceControl(tmp_path / str(len(created)), store=TestSecrets())
        control.action({"action": "enable"})
        created.append(control)
        return control

    yield make
    for control in created:
        control.close()


def test_invitation_creator_learns_the_other_desktops_address(desktops):
    first, second = desktops(), desktops()
    pair(first, second)
    assert second.registry is not None and first.application is not None
    # Without the announcement the responder had no way to reach the initiator.
    wait(lambda: second.registry.db.execute("SELECT 1 FROM endpoints").fetchone())
    assert second.registry.endpoint(identity(first)) == first.application.listener_endpoint
    row = wait(lambda: (r := peer_row(second, first))["reachability"] == "reachable" and r)
    assert row["endpoint_known"] is True
    assert row["name"] == first.application.name
    # The name typed into the invitation outlives the peer's own announcement.
    assert peer_row(first, second)["name"] == "Desk B"


def test_desktop_sessions_acknowledge_and_never_replay_delivered_events(desktops):
    first, second = desktops(), desktops()
    pair(first, second)
    assert second.application is not None and second.registry is not None
    wait(lambda: peer_row(first, second)["reachability"] == "reachable")
    with second.registry.transaction():
        for _ in range(5):
            second.application._emit(
                identity(first),
                {"thread_id": str(uuid4()), "request_id": str(uuid4())},
                "request.status",
                {"status": "running", "detail": "backlog"},
            )
    received: list[dict[str, Any]] = []
    app = first.application
    assert app is not None
    original = app.remote_result

    def recording(peer: str, event: dict[str, Any]) -> None:
        received.append(event)
        original(peer, event)

    app.remote_result = recording  # type: ignore[method-assign]

    def probe() -> None:
        first.fleet_probed.clear()
        first.action({"action": "fleet_status"})
        wait(lambda: all(task.done() for task in first.remote_tasks.values()))

    probe()
    assert sum(e["payload"].get("detail") == "backlog" for e in received) == 5
    received.clear()
    probe()
    assert not any(e["payload"].get("detail") == "backlog" for e in received)
    # Each probe also asks for the catalog placement reads (catalog_probe).
    assert {e["type"] for e in received} <= {"sync.snapshot", "fleet.snapshot", "catalog.snapshot"}


def test_fleet_probes_are_not_journaled_as_work(desktops):
    first, second = desktops(), desktops()
    pair(first, second)
    for _ in range(3):
        first.fleet_probed.clear()
        assert peer_row(first, second)["reachability"] == "reachable"
    assert first.registry is not None
    db = first.registry.db
    # Status polling used to consume the 100000-request lifetime quota.
    assert db.execute("SELECT COUNT(*) FROM device_remote_requests").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM device_remote_results").fetchone()[0] == 0
    kinds = {r[0] for r in db.execute("SELECT kind FROM device_peer_snapshots")}
    assert kinds == {"sync.snapshot", "fleet.snapshot"}


def test_every_device_sees_desktops_paired_through_another_desktop(desktops):
    first, hub, third = desktops(), desktops(), desktops()
    pair(first, hub, "Hub")
    pair(third, hub, "Hub")
    assert first.application is not None and first.registry is not None
    wait(lambda: peer_row(hub, first)["reachability"] == "reachable")
    wait(lambda: peer_row(hub, third)["reachability"] == "reachable")
    first.fleet_probed.clear()
    devices = first.action({"action": "fleet_status"})["devices"]
    indirect = next(d for d in devices if d["device_id"] == identity(third))
    assert indirect["state"] == "indirect"
    assert indirect["via"] == identity(hub)
    assert indirect["reachability"] == "reachable"

    # A phone paired only with the first desktop sees the whole fleet.
    phone, pin = str(uuid4()), secrets.token_bytes(32)
    registry = first.registry
    registry.propose(phone, pin, "controller", registry.clock() + 300)
    registry.confirm(phone, pin.hex())
    registry.acknowledge(
        phone,
        pin,
        "controller",
        {"type": "session.ready_ack", "device_id": phone, "trust_revision": 1},
    )
    request = {
        "version": 2,
        "id": str(uuid4()),
        "thread_id": None,
        "request_id": str(uuid4()),
        "sequence": 1,
        "type": "fleet.request",
        "payload": {},
    }
    snapshot = first.application.handle(phone, request)[0]
    assert _VALIDATOR.is_valid(snapshot)
    listed = {d["device_id"]: d for d in snapshot["payload"]["devices"]}
    assert phone not in listed
    assert set(listed) == {identity(first), identity(hub), identity(third)}
    assert listed[identity(third)]["name"].endswith("(via Hub)")
    assert listed[identity(third)]["status"] == "reachable"


def test_invitation_creator_sends_work_using_shared_thread_ids(desktops):
    first, second = desktops(), desktops()
    pair(first, second)
    assert first.application is not None and second.application is not None
    wait(lambda: peer_row(second, first)["endpoint_known"])
    # The initiator grants the responder one thread, as in Desktop work settings.
    thread, project = str(uuid4()), str(uuid4())
    first.application.authorize(
        identity(second),
        thread,
        {
            "title": "Build checks",
            "provider": "codex",
            "model": "cli-default",
            "permission_mode": "read-only",
            "project_id": project,
        },
    )

    # A probe started before the grant may still be running and answer without the
    # thread, so ask again on every poll instead of waiting for the next refresh.
    def shared_threads() -> list[dict[str, Any]]:
        second.fleet_probed.clear()
        return cast(list[dict[str, Any]], peer_row(second, first)["shared_threads"])

    shared = wait(shared_threads)
    assert shared == [
        {
            "thread_id": thread,
            "title": "Build checks",
            "project_id": project,
            "provider": "codex",
            "model": "cli-default",
        }
    ]
    request = str(uuid4())
    queued = second.action(
        {
            "action": "send",
            "kind": "job.submit",
            "device_id": identity(first),
            "thread_id": thread,
            "project_id": project,
            "request_id": request,
            "text": "Run the checks",
        }
    )
    assert queued["state"] == "queued"
    work = wait(first.application.claim)
    assert work["event"]["payload"]["intent"] == "Run the checks"
    first.application.complete(work["peer"], request, "Checks passed")
    # The open session delivers the answer; no address was typed on either desktop.
    status = wait(
        lambda: (
            (
                s := second.action(
                    {"action": "work_status", "device_id": identity(first), "request_id": request}
                )
            )["state"]
            == "completed"
            and s
        )
    )
    assert any(e["payload"].get("text") == "Checks passed" for e in status["events"])


def test_older_desktop_that_rejects_the_announcement_still_answers(desktops, monkeypatch):
    first, second = desktops(), desktops()
    pair(first, second)
    assert second.application is not None

    def older(*_: Any) -> None:
        raise PairingError("invalid_application_event")

    monkeypatch.setattr(second.application, "_advertised", older)
    first.fleet_probed.clear()
    assert peer_row(first, second)["reachability"] == "reachable"
    assert all(task.exception() is None for task in first.remote_tasks.values() if task.done())


def test_fleet_send_matches_the_protocol_text_limit():
    from gofer.ui.device_tools import MAX_WORK_TEXT, FleetActions, fleet_tools

    class Control:
        def action(self, body: dict[str, Any]) -> dict[str, Any]:
            return body

    send = next(t for t in fleet_tools(False) if t["name"] == "fleet_send")
    assert send["inputSchema"]["properties"]["text"]["maxLength"] == MAX_WORK_TEXT == 4000
    arguments = {
        key: str(uuid4()) for key in ("device_id", "thread_id", "project_id", "request_id")
    }
    actions = FleetActions(Control(), read_only=False)
    with pytest.raises(ValueError, match="bounded"):
        actions.call("fleet_send", {**arguments, "text": "x" * 4001})
    assert actions.call("fleet_send", {**arguments, "text": "x" * 4000})["kind"] == "job.submit"


def host_for(worker: DeviceControl, main: DeviceControl, root: Path) -> None:
    """The worker allows the main desktop to run threads in one open project."""
    from gofer.ui.device_context import shared_context

    assert worker.application is not None
    worker.application.workspace.providers = [
        {"id": "codex", "models": ["cli-default"], "efforts": {}}
    ]
    worker.action({"action": "host_threads", "device_id": identity(main), "enabled": True})
    context = shared_context(
        {},
        {"remThreads": {"projects": [{"root": str(root), "name": root.name}]}},
        [{"id": "codex", "defaultPermissionMode": "read-only"}],
        None,
    )
    worker.action({"action": "host_template", "device_id": identity(main), "context": context})


def run_turn(main: DeviceControl, worker: DeviceControl, **options: Any) -> list[dict[str, Any]]:
    import asyncio

    from gofer.devices.remote_threads import RemoteThreads

    async def collect() -> list[dict[str, Any]]:
        return [
            event
            async for event in RemoteThreads(main).stream(
                machine=identity(worker),
                provider="codex",
                model="cli-default",
                **options,
            )
        ]

    return asyncio.run(collect())


def answer_on(worker: DeviceControl, reply: str) -> Any:
    """Stand in for the worker's provider: claim the turn, then finish it."""
    import threading

    assert worker.application is not None
    app = worker.application

    def work() -> None:
        claimed = wait(app.claim, timeout=20)
        time.sleep(1.5)  # long enough for a progress poll to see the running turn
        app.complete(claimed["peer"], claimed["event"]["request_id"], reply)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread


def test_main_desktop_runs_a_thread_on_another_desktop(desktops, tmp_path):
    main, worker = desktops(), desktops()
    pair(main, worker)
    wait(lambda: peer_row(worker, main)["endpoint_known"])
    project = tmp_path / "Raticode"
    project.mkdir()
    host_for(worker, main, project)
    assert worker.application is not None
    original = worker.application.workspace.desktop_messages

    def with_progress(peer: str, thread: str) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = original(peer, thread)
        if any(m["role"] == "user" for m in messages):
            messages.append(
                {"id": "step-1", "role": "assistant", "body": "Reading files", "kind": "thought"}
            )
        return messages

    worker.application.workspace.desktop_messages = with_progress  # type: ignore[method-assign]
    conversation = str(uuid4())
    answered = answer_on(worker, "All checks passed")
    events = run_turn(
        main,
        worker,
        messages=[{"role": "user", "body": "Run the checks"}],
        conversation_id=conversation,
        workflow={"projectRoot": "/home/someone/raticode"},
    )
    answered.join(5)
    assert events[0] == {
        "type": "thought",
        "text": "Running on Desk B in the Raticode project.",
    }
    assert {"type": "thought", "text": "Reading files"} in events
    assert events[-1]["type"] == "final"
    assert events[-1]["message"]["body"] == "All checks passed"

    # The next turn continues the same thread on the worker.
    answered = answer_on(worker, "Second answer")
    events = run_turn(
        main,
        worker,
        messages=[
            {"role": "user", "body": "Run the checks"},
            {"role": "assistant", "body": "All checks passed"},
            {"role": "user", "body": "Again"},
        ],
        conversation_id=conversation,
        workflow={"projectRoot": "/home/someone/raticode"},
    )
    answered.join(5)
    assert events[-1]["message"]["body"] == "Second answer"
    assert worker.registry is not None
    threads = worker.registry.db.execute(
        "SELECT COUNT(*) FROM device_grants WHERE peer=?", (identity(main),)
    ).fetchone()[0]
    assert threads == 2  # the hosting template and one conversation


def test_remote_thread_explains_setup_and_limits(desktops, tmp_path):
    main, worker = desktops(), desktops()
    pair(main, worker)
    wait(lambda: peer_row(worker, main)["endpoint_known"])
    events = run_turn(main, worker, messages=[{"role": "user", "body": "hello"}])
    assert events == [{"type": "error", "error": events[0]["error"]}]
    assert "turn on Run threads from this desktop" in events[0]["error"]
    events = run_turn(main, worker, messages=[{"role": "user", "body": "x" * 4001}])
    assert "up to 4000 characters" in events[0]["error"]
    events = run_turn(
        main, worker, messages=[{"role": "user", "body": "see file", "attachments": [{}]}]
    )
    assert "Attachments" in events[0]["error"]


def test_stopping_a_remote_turn_stops_it_on_the_other_desktop(desktops, tmp_path):
    import threading

    main, worker = desktops(), desktops()
    pair(main, worker)
    wait(lambda: peer_row(worker, main)["endpoint_known"])
    project = tmp_path / "work"
    project.mkdir()
    host_for(worker, main, project)
    assert worker.application is not None and worker.registry is not None
    app = worker.application
    stop = threading.Event()

    def work() -> None:
        claimed = wait(app.claim, timeout=20)
        stop.set()
        wait(lambda: not app.runnable(claimed["peer"], claimed["event"]["request_id"]))
        app.complete(claimed["peer"], claimed["event"]["request_id"], "", error="provider_stopped")

    worker_thread = threading.Thread(target=work, daemon=True)
    worker_thread.start()
    events = run_turn(
        main, worker, messages=[{"role": "user", "body": "long job"}], cancel_event=stop
    )
    worker_thread.join(10)
    assert [e for e in events if e["type"] in ("final", "error")] == []
    state = worker.registry.db.execute("SELECT state FROM device_work").fetchone()[0]
    assert state == "cancelled"


def test_turning_hosting_off_revokes_remote_threads(desktops, tmp_path):
    main, worker = desktops(), desktops()
    pair(main, worker)
    project = tmp_path / "work"
    project.mkdir()
    host_for(worker, main, project)
    assert worker.registry is not None
    worker.action({"action": "host_threads", "device_id": identity(main), "enabled": False})
    assert worker.status()["thread_host_peers"] == []
    assert worker.registry.db.execute("SELECT COUNT(*) FROM device_grants").fetchone()[0] == 0


def test_phone_turns_follow_the_threads_machine(tmp_path):
    from gofer.ui.device_context import shared_context

    machine = str(uuid4())
    workflow = {"remThreads": {"projects": [{"root": str(tmp_path), "name": "work"}]}}
    caps = [{"id": "codex", "defaultPermissionMode": "read-only"}]
    context = shared_context({"machine": machine}, workflow, caps, None)
    assert context["machine"] == machine
    assert "machine" not in shared_context({}, workflow, caps, None)
    with pytest.raises(PairingError):
        shared_context({"machine": "not-a-device"}, workflow, caps, None)


class LoadService:
    """Stands in for gofer.core.computers.ComputerService on the answering desktop."""

    def __init__(self, reading: dict[str, Any]) -> None:
        self.reading = reading

    def load_reading(self) -> dict[str, Any]:
        return self.reading


READING = {"max_threads": 6, "running_threads": 2, "cpu_pressure": 50.0, "memory_pressure": None}


def probe(control: DeviceControl) -> None:
    control.fleet_probed.clear()
    control.action({"action": "fleet_status"})
    wait(lambda: all(task.done() for task in control.remote_tasks.values()))


def test_paired_desktop_reports_its_worker_limit_and_load(desktops, tmp_path):
    from gofer.core.computers import ComputerService

    first, second = desktops(), desktops()
    pair(first, second)
    assert first.application is not None and second.application is not None
    second.application.desktop.computers = LoadService(READING)
    wait(lambda: peer_row(first, second)["reachability"] == "reachable")
    probe(first)
    row = wait(lambda: (r := peer_row(first, second))["load"] and r)
    assert row["load"] == READING
    registry = ComputerService(
        tmp_path / "registry",
        name=lambda: "Desk A",
        device_id=lambda: identity(first),
        fleet=first.application.fleet,
        pressure=lambda: (None, None),
    )
    paired = next(c for c in registry.read()["computers"] if c["id"] == identity(second))
    assert paired["state"] == "online" and paired["max_threads"] == 6
    assert paired["running_threads"] == 2 and paired["cpu_pressure"] == 50.0
    # 50% pressure lowers six workers to three; two run, so one more fits.
    assert paired["effective_threads"] == 3 and paired["capacity"] == 1


def test_a_desktop_without_the_registry_reports_no_load(desktops):
    first, second = desktops(), desktops()
    pair(first, second)
    wait(lambda: peer_row(first, second)["reachability"] == "reachable")
    probe(first)
    assert peer_row(first, second)["load"] is None


def test_older_desktop_that_refuses_the_load_request_gets_plain_announcements(desktops):
    first, second = desktops(), desktops()
    pair(first, second)
    assert first.application is not None and second.application is not None
    app = second.application
    original = app.handle

    def older(peer: str, event: dict[str, Any]) -> list[dict[str, Any]]:
        # An older desktop's schema has no `load`, so it refuses the whole announcement.
        if event["type"] == "fleet.request" and "load" in event["payload"].get("advertise", {}):
            return cast(
                list[dict[str, Any]], app._refuse(peer, event, None, "invalid_application_event")
            )
        return cast(list[dict[str, Any]], original(peer, event))

    app.handle = older  # type: ignore[method-assign]
    wait(lambda: peer_row(first, second)["reachability"] == "reachable")
    probe(first)
    # It refuses the counts by kind first, then the memory hold, then the load alone.
    wait(lambda: identity(second) in first.application._hold_only_announcements)
    probe(first)
    wait(lambda: identity(second) in first.application._load_only_announcements)
    probe(first)
    wait(lambda: identity(second) in first.application._plain_announcements)
    plain = first.application.advertisement(identity(second))
    assert "load" not in plain["payload"]["advertise"]
    # The refusal isn't kept as an answer to delegated work.
    assert first.registry is not None
    errors = [
        row
        for row in first.registry.db.execute("SELECT event FROM device_remote_results")
        if "invalid" in str(first.application._decode(identity(second), row[0]))
    ]
    assert errors == []
    probe(first)
    assert peer_row(first, second)["reachability"] == "reachable"


def test_a_desktop_that_refuses_only_the_memory_hold_still_shares_its_load(desktops):
    first, second = desktops(), desktops()
    pair(first, second)
    assert first.application is not None and second.application is not None
    app = second.application
    app.desktop.computers = LoadService({**READING, "memory_hold": "0.9 GB available"})
    original = app.handle
    asked: list[dict[str, Any]] = []

    def before_the_hold(peer: str, event: dict[str, Any]) -> list[dict[str, Any]]:
        # A desktop from before the memory hold knows `load` but not `memory_hold`.
        if event["type"] == "fleet.request":
            asked.append(dict(event["payload"].get("advertise", {})))
            if "memory_hold" in event["payload"].get("advertise", {}):
                return cast(
                    list[dict[str, Any]],
                    app._refuse(peer, event, None, "invalid_application_event"),
                )
        return cast(list[dict[str, Any]], original(peer, event))

    app.handle = before_the_hold  # type: ignore[method-assign]
    wait(lambda: peer_row(first, second)["reachability"] == "reachable")
    probe(first)
    # The ask for counts by kind carries the hold too, so it is refused first.
    wait(lambda: identity(second) in first.application._hold_only_announcements)
    probe(first)
    wait(lambda: identity(second) in first.application._load_only_announcements)
    probe(first)
    row = wait(lambda: (r := peer_row(first, second))["load"] and r)
    # Asked without the hold, it answers without it, and its load still counts.
    assert row["load"] == READING
    assert asked[-1].get("load") is True and "memory_hold" not in asked[-1]
    assert "running_by_kind" not in asked[-1]
    assert identity(second) not in first.application._plain_announcements


def test_a_desktop_shares_its_counts_by_kind_only_when_asked(desktops, tmp_path):
    from gofer.core.computers import ComputerService

    first, second = desktops(), desktops()
    pair(first, second)
    assert first.application is not None and second.application is not None
    kinds = {"rem": 1, "phone": 0, "organizations": 1, "swarms": 0}
    counted = {**READING, "memory_hold": None, "running_by_kind": kinds}
    second.application.desktop.computers = LoadService(counted)
    wait(lambda: peer_row(first, second)["reachability"] == "reachable")
    probe(first)
    row = wait(lambda: (r := peer_row(first, second))["load"] and r)
    assert row["load"] == counted
    registry = ComputerService(
        tmp_path / "registry",
        name=lambda: "Desk A",
        device_id=lambda: identity(first),
        fleet=first.application.fleet,
        pressure=lambda: (None, None),
    )
    paired = next(c for c in registry.read()["computers"] if c["id"] == identity(second))
    assert paired["running_by_kind"] == kinds and paired["running_work"] is None
    # An older asker, which never asks for the counts, gets a reading without them.
    assert "running_by_kind" not in second.application._load_reading(memory_hold=True)
    assert second.application._load_reading(memory_hold=True, running_by_kind=True) == counted


def test_a_desktop_that_refuses_only_the_counts_still_shares_its_load_and_hold(desktops):
    first, second = desktops(), desktops()
    pair(first, second)
    assert first.application is not None and second.application is not None
    app = second.application
    held = {**READING, "memory_hold": "0.9 GB available"}
    app.desktop.computers = LoadService({**held, "running_by_kind": {"rem": 2}})
    original = app.handle
    asked: list[dict[str, Any]] = []

    def before_the_counts(peer: str, event: dict[str, Any]) -> list[dict[str, Any]]:
        # A desktop from before the counts knows `load` and `memory_hold` only.
        if event["type"] == "fleet.request":
            asked.append(dict(event["payload"].get("advertise", {})))
            if "running_by_kind" in event["payload"].get("advertise", {}):
                return cast(
                    list[dict[str, Any]],
                    app._refuse(peer, event, None, "invalid_application_event"),
                )
        return cast(list[dict[str, Any]], original(peer, event))

    app.handle = before_the_counts  # type: ignore[method-assign]
    wait(lambda: peer_row(first, second)["reachability"] == "reachable")
    probe(first)
    wait(lambda: identity(second) in first.application._hold_only_announcements)
    probe(first)
    row = wait(lambda: (r := peer_row(first, second))["load"] and r)
    assert row["load"] == held
    assert asked[-1].get("memory_hold") is True and "running_by_kind" not in asked[-1]
    assert identity(second) not in first.application._load_only_announcements


def test_a_desktop_shares_its_memory_hold_only_when_asked(desktops, tmp_path):
    from gofer.core.computers import ComputerService

    first, second = desktops(), desktops()
    pair(first, second)
    assert first.application is not None and second.application is not None
    held = {**READING, "memory_hold": "0.9 GB available"}
    second.application.desktop.computers = LoadService(held)
    wait(lambda: peer_row(first, second)["reachability"] == "reachable")
    probe(first)
    row = wait(lambda: (r := peer_row(first, second))["load"] and r)
    assert row["load"] == held
    registry = ComputerService(
        tmp_path / "registry",
        name=lambda: "Desk A",
        device_id=lambda: identity(first),
        fleet=first.application.fleet,
        pressure=lambda: (None, None),
    )
    paired = next(c for c in registry.read()["computers"] if c["id"] == identity(second))
    # Room by its limit, but held for memory: none of this computer's work goes there.
    assert paired["capacity"] == 0 and paired["memory_hold"] == "0.9 GB available"
    # An older asker, which never asks for the hold, gets a reading without it.
    assert "memory_hold" not in second.application._load_reading()
    assert second.application._load_reading(memory_hold=True) == held


def test_load_stays_out_of_the_shared_phone_protocol():
    import json

    from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

    from gofer.devices.application import _SCHEMA

    fixture = Path(__file__).parents[2] / (
        "src/gofer/devices/protocol/v2/fixtures/fleet.snapshot.json"
    )
    snapshot = json.loads(fixture.read_text())
    snapshot["payload"]["devices"][0]["load"] = READING
    # Desktops accept it from each other; the schema phones and older desktops use doesn't.
    assert _VALIDATOR.is_valid(snapshot)
    assert not Draft202012Validator(_SCHEMA).is_valid(snapshot)
    snapshot["payload"]["devices"][0]["load"] = {**READING, "max_threads": 99}
    assert not _VALIDATOR.is_valid(snapshot)


def registry_row(first: DeviceControl, second: DeviceControl, tmp_path: Path) -> dict[str, Any]:
    from gofer.core.computers import ComputerService

    assert first.application is not None
    second.application.desktop.computers = LoadService(READING)  # type: ignore[union-attr]
    registry = ComputerService(
        tmp_path / "registry",
        name=lambda: "Desk A",
        device_id=lambda: identity(first),
        fleet=first.application.fleet,
        pressure=lambda: (None, None),
    )
    return next(c for c in registry.read()["computers"] if c["id"] == identity(second))


def test_paired_desktops_share_each_projects_repository_fingerprint(desktops, tmp_path):
    from gofer.core import repository_fingerprint
    from tests.unit.test_rem_placement import git_repository

    first, second = desktops(), desktops()
    pair(first, second)
    assert first.application is not None and second.application is not None
    root = git_repository(tmp_path / "work" / "Raticode")
    fingerprint = repository_fingerprint.fingerprint(root)
    host_for(second, first, root)
    second.application.desktop.computers = LoadService(READING)
    wait(lambda: peer_row(first, second)["reachability"] == "reachable")
    probe(first)
    row = wait(lambda: (r := peer_row(first, second))["catalog"] and r)
    [project] = row["catalog"]["projects"]
    assert row["catalog"]["state"] == "shared"
    assert project["name"] == "Raticode" and project["fingerprint"] == fingerprint
    assert row["catalog"]["providers"] == ["codex"]
    # Never a path.
    assert str(tmp_path) not in str(row["catalog"])
    paired = registry_row(first, second, tmp_path)
    assert paired["catalog"]["projects"][0]["fingerprint"] == fingerprint
    assert paired["work_reason"] is None
    # The catalog a phone or a plain request gets keeps the shared schema: no fingerprints.
    plain = second.application.workspace.catalog(identity(first))
    assert all("fingerprint" not in p for p in plain["projects"])


def test_a_desktop_without_hosting_shares_no_projects(desktops, tmp_path):
    first, second = desktops(), desktops()
    pair(first, second)
    assert first.application is not None and second.application is not None
    second.application.desktop.computers = LoadService(READING)
    wait(lambda: peer_row(first, second)["reachability"] == "reachable")
    probe(first)
    row = wait(lambda: (r := peer_row(first, second))["catalog"] and r)
    assert row["catalog"] == {"state": "shared", "projects": [], "providers": []}
    assert registry_row(first, second, tmp_path)["work_reason"] == (
        "Desk B hasn't allowed this computer to run work there"
    )


def test_older_desktop_that_refuses_fingerprints_is_listed_as_needing_an_update(desktops, tmp_path):
    first, second = desktops(), desktops()
    pair(first, second)
    assert first.application is not None and second.application is not None
    app = second.application
    app.desktop.computers = LoadService(READING)
    original = app.handle

    def older(peer: str, event: dict[str, Any]) -> list[dict[str, Any]]:
        # An older desktop's schema has no `fingerprints`, so it refuses the request.
        if event["type"] == "catalog.request" and "fingerprints" in event["payload"]:
            return cast(
                list[dict[str, Any]], app._refuse(peer, event, None, "invalid_application_event")
            )
        return cast(list[dict[str, Any]], original(peer, event))

    app.handle = older  # type: ignore[method-assign]
    wait(lambda: peer_row(first, second)["reachability"] == "reachable")
    probe(first)
    row = wait(lambda: (r := peer_row(first, second))["catalog"] and r)
    assert row["catalog"] == {"state": "unsupported"}
    assert registry_row(first, second, tmp_path)["work_reason"] == (
        "Update Raticode on Desk B so it can say which of its projects match"
    )
    # It isn't asked again on every probe, and pairing keeps working.
    assert first.application.catalog_probe(identity(second)) is None
    probe(first)
    assert peer_row(first, second)["reachability"] == "reachable"
    assert peer_row(first, second)["load"] == READING


# A research task pinned to a paired desktop -------------------------------------------


class Researcher:
    """The receiving desktop's provider: works until released, then saves a report.

    Its reviewer passes every check, as the report and answer are real.
    """

    def __init__(self) -> None:
        import threading

        self.release = threading.Event()
        self.working = threading.Event()
        self.runs: list[str] = []
        self.answer = "The slowdown has three causes."

    async def __call__(self, **kwargs: Any) -> Any:
        import asyncio
        import json

        from tests.unit.test_swarm_tools import request

        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            verdict = [
                {"id": c, "result": "pass", "evidence": "Read findings.html"}
                for c in ("answer", "report")
            ]
            body = json.dumps({"criteria": verdict, "feedback": ""})
            yield {
                "type": "thought",
                "text": "Read the report",
                "trace": {
                    "id": "review-read",
                    "category": "shell",
                    "phase": "result",
                    "status": "completed",
                    "exitCode": 0,
                    "output": "Three causes",
                },
            }
            yield {"type": "final", "message": {"body": body}}
            return
        self.runs.append(kwargs["workflow"]["remManagedTask"]["runId"])
        self.working.set()
        while not self.release.is_set():
            # A stopped turn ends, as a real provider's process does.
            cancel = kwargs.get("cancel_event")
            if cancel is not None and cancel.is_set():
                return
            await asyncio.sleep(0.02)
        artifact = next(
            s
            for s in kwargs["workflow"]["remResources"]["mcpServers"]
            if s["name"] == "rem_artifacts"
        )
        response = request(
            artifact["url"],
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "save_report",
                    "arguments": {
                        # Saved reports are never replaced, so a later run picks a new name.
                        "path": "findings.html"
                        if len(self.runs) == 1
                        else f"findings-{len(self.runs)}.html",
                        "content": "<!doctype html><html><body>Three causes</body></html>",
                    },
                },
            },
        )[1]["result"]
        assert not response.get("isError"), response
        yield {"type": "final", "message": {"body": self.answer}}


def rem_on(control: DeviceControl, data: Path, project: Path, source: Any) -> Any:
    """A desktop's own Rem, with the project open and its device service attached."""
    from gofer.ui.chat_jobs import ChatJobs
    from gofer.ui.chat_steering import ChatSteering
    from gofer.ui.rem_home import RemHome

    home = RemHome(
        data,
        source=source,
        jobs=ChatJobs(data),
        steering=ChatSteering(data),
        authorize=lambda c, r: None,
        generate=lambda p, c, e: {"reply": "Noted.", "tasks": [], "memories": []},
        start=False,
    )
    home.configure(
        {
            "provider": "codex",
            "model": "cli-default",
            "command_slots": {"maxWorkers": 4},
            "projects": [{"root": str(project), "name": project.name}],
            "resources": {"web": True},
        }
    )
    home.remote.fleet = control
    home.remote.follow_seconds = 0
    return home


def close_rem(home: Any) -> None:
    home.close()
    for worker, _ in list(home.workers.values()):
        worker.join(timeout=5)
    home.jobs.close()
    home.steering.close()


def run_body(home: Any, run_id: str) -> dict[str, Any]:
    with home.store.read() as db:
        return dict(home.store.get(db, "runs", run_id))


class Pair:
    """This computer (``main``) and a paired desktop (``worker``), each with its own Rem."""

    def __init__(self, desktops: Callable[[], Any], tmp_path: Path) -> None:
        from gofer.core.computers import ComputerService
        from tests.unit.test_rem_placement import git_repository

        # Free memory and no pressure come from the autouse host_independent_worker_room.
        self.main, self.worker = desktops(), desktops()
        pair(self.main, self.worker)
        assert self.main.application is not None and self.worker.application is not None
        # One repository, opened on both desktops (here, the same clone).
        self.project = git_repository(tmp_path / "code" / "Raticode")
        host_for(self.worker, self.main, self.project)
        self.worker.application.desktop.computers = LoadService(READING)
        wait(lambda: peer_row(self.main, self.worker)["reachability"] == "reachable")
        probe(self.main)
        wait(lambda: (peer_row(self.main, self.worker)["catalog"] or {}).get("projects"))
        self.researcher = Researcher()
        self.main_home = rem_on(self.main, tmp_path / "rem-main", self.project, self.researcher)
        self.worker_home = rem_on(
            self.worker, tmp_path / "rem-worker", self.project, self.researcher
        )
        app = self.main.application
        self.registry = ComputerService(
            tmp_path / "registry",
            name=lambda: "Desk A",
            device_id=lambda: identity(self.main),
            fleet=app.fleet,
            pressure=lambda: (None, None),
        )
        self.main_home.placement.computers = self.registry
        self.main_home.computer_id = lambda: identity(self.main)
        self.sends: list[dict[str, Any]] = []
        original = self.main.action

        def counting(body: dict[str, Any]) -> dict[str, Any]:
            if body.get("action") == "send":
                self.sends.append(body)
            return dict(original(body))

        self.main.action = counting  # type: ignore[method-assign]

    def close(self) -> None:
        self.researcher.release.set()
        close_rem(self.main_home)
        close_rem(self.worker_home)

    def pin(self, request: str = "pin", **fields: Any) -> dict[str, Any]:
        task = self.main_home.create_task(
            {
                "request_id": request,
                "title": "Find the slowdown",
                "type": "research",
                "prompt": "Why did startup slow down? " + "Read every log. " * 400,
                "project_dir": str(self.project),
                "computer": identity(self.worker),
                "acceptance_criteria": [
                    {"id": "answer", "description": "Name the causes with sources"},
                    {"id": "report", "description": "Save a readable report"},
                ],
                **fields,
            }
        )["task"]
        return dict(task["active_run"])

    def both(self, condition: Callable[[], Any], timeout: float = 30) -> Any:
        """Tick both desktops' Rem, and deliver the worker's answers, until it holds."""
        end = time.monotonic() + timeout
        last_probe = 0.0
        while time.monotonic() < end:
            self.main_home.tick()
            self.worker_home.tick()
            if result := condition():
                return result
            if time.monotonic() - last_probe > 0.5:
                last_probe = time.monotonic()
                self.deliver()
            time.sleep(0.05)
        import json

        seen = {}
        for label, home in (("main", self.main_home), ("worker", self.worker_home)):
            with home.store.read() as db:
                seen[label] = [
                    (r["state"], r.get("summary_short"), r.get("waiting_reason"))
                    for r in (json.loads(row[0]) for row in db.execute("SELECT body FROM runs"))
                ]
        raise AssertionError(f"condition not met: {seen}")

    def deliver(self) -> None:
        """What the device service's background loop does every 30 seconds, now.

        Each queued request or Stop is sent again on a fresh session, which also brings
        back what the worker sent this computer since. One session per desktop at a time,
        as the worker turns away a second.
        """
        import concurrent.futures

        app = self.main.application
        assert app is not None
        pending = [
            self.main._dispatch((peer, event["request_id"]), peer, event)
            for peer, event in app.queued_remote()
        ]
        if not pending:
            self.main.fleet_probed.clear()
            pending = self.main._refresh_fleet(force=True)
        concurrent.futures.wait(pending, timeout=10)

    def handed_over(self, run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """The stand-in once sent, and the worker's own run for it once running."""
        self.both(lambda: run_body(self.main_home, run_id).get("remote", {}).get("sent"))
        self.both(lambda: self.researcher.working.is_set())
        stand_in = run_body(self.main_home, run_id)
        [remote_run] = self.researcher.runs
        return stand_in, run_body(self.worker_home, remote_run)


@pytest.fixture
def fleet_pair(desktops, tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Pair]:
    # Both desktops share 127.0.0.1 here and deliver every half second, far more often
    # than a real pair. The per-address limit (5 attempts, one more every 10 seconds) can
    # then turn away the delivery a test waits for on a slow machine, so only the cap on
    # open connections applies to these pairs.
    from gofer.devices.service import DeviceListener

    monkeypatch.setattr(DeviceListener, "_allow", lambda self, host: len(self.connections) < 16)
    pair_ = Pair(desktops, tmp_path)
    yield pair_
    pair_.close()


def test_research_task_pinned_to_a_paired_desktop_runs_there_and_reports_back(fleet_pair):
    import sqlite3

    from gofer.core.usage_history import resolve_range, usage_history
    from gofer.core.usage_ledger import record_invocation, run_usage
    from gofer.core.worker_claims import THIS_COMPUTER

    p = fleet_pair
    run = p.pin()
    stand_in, remote_run = p.handed_over(run["id"])
    # This computer keeps a stand-in that holds no worker here.
    assert stand_in["state"] == "executing"
    assert stand_in["phase_label"] == "Running on Desk B"
    assert stand_in["placement"]["computer_id"] == identity(p.worker)
    assert stand_in["placement"]["chosen_by"] == "user"
    assert sum(p.main_home.placement.claims.running().values()) == 0
    # The worker runs it as its own Rem task in its matched project, counted as rem.
    assert remote_run["recipe"]["project_dir"] == str(p.project)
    assert remote_run["recipe"]["prompt"] == run["recipe"]["prompt"]
    assert len(remote_run["recipe"]["prompt"]) > 4000  # past job.submit's text cap
    assert remote_run["remote_origin"]["run_id"] == run["id"]
    assert p.worker_home.placement.claims.running(THIS_COMPUTER)["rem"] == 1
    # The provider calls there go to the worker's ledger under its own account.
    record_invocation(
        invocation_id="call-1",
        provider="claude_code",
        model="claude-opus-5-5",
        metadata={"input_tokens": 1200, "output_tokens": 300, "total_cost_usd": 0.42},
        data_dir=p.worker_home.data_dir,
        run_id=remote_run["id"],
    )
    p.researcher.release.set()
    done = p.both(lambda: (r := run_body(p.main_home, run["id"]))["state"] != "executing" and r)
    assert done["state"] == "succeeded", done["summary_short"]
    # The final answer and the report's path there reach the stand-in.
    assert done["worker_result"] == "The slowdown has three causes."
    report = done["remote"]["result"]["report"]
    assert report and report.endswith("findings.html") and not report.startswith("/")
    # The task was sent exactly once.
    assert len(p.sends) == 1 and p.sends[0]["rem_task"]["run_id"] == run["id"]
    # One summary row on this computer, tagged with the computer and the real provider.
    with sqlite3.connect(p.main_home.data_dir / "usage.sqlite3") as db:
        rows = db.execute(
            "SELECT provider, model, profile, run_id, computer_id, tokens, cost_usd "
            "FROM usage_invocations"
        ).fetchall()
    assert len(rows) == 1
    provider, model, profile, run_id, computer, tokens, cost = rows[0]
    assert (provider, model, profile, run_id, computer) == (
        "claude_code",
        "claude-opus-5-5",
        None,
        run["id"],
        identity(p.worker),
    )
    assert '"total_tokens": 1500' in tokens and cost == pytest.approx(0.42)
    # It counts for its run, not toward this computer's account totals.
    assert run_usage([run["id"]], p.main_home.data_dir)[run["id"]]["total_tokens"] == 1500
    assert usage_history(p.main_home.data_dir, resolve_range())["activity"]["calls"] == 0
    # The worker keeps its call-by-call row under its own account.
    assert usage_history(p.worker_home.data_dir, resolve_range())["activity"]["calls"] == 1
    # Another pass adds no second row.
    p.main_home.remote.follow()
    with sqlite3.connect(p.main_home.data_dir / "usage.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM usage_invocations").fetchone()[0] == 1


def self_work_pass(home: Any, run: dict[str, Any], project: Path, session: str) -> None:
    """File a finished run as a self-work research pass, as _start_research records one."""
    with home.store.connect() as db:
        state = home.store.setting(db, "self_work_state")
        state.setdefault("passes", []).append(
            {
                "kind": "research",
                "run_id": run["id"],
                "task_id": run["task_id"],
                "thread_id": run["thread_id"],
                "title": run["recipe"]["title"],
                "target": str(project),
                "key": f"{project}#0",
                "session_id": session,
                "board": True,
                "objective": "",
                "deepening": False,
                "hotspots": [],
            }
        )
        home.store.set_setting(db, "self_work_state", state)


def test_self_work_proposals_from_a_paired_desktop_wait_even_with_auto_approve(fleet_pair):
    """A self-work pass the user sent to a paired desktop (Run there) can't approve its
    own proposals here: they wait for the user and name that desktop. A pass that ran on
    this computer is still auto-approved."""
    import json

    p = fleet_pair
    home = p.main_home
    home.kanban.set_participant_projects(
        "user", {"participant_id": "rem", "projects": [str(p.project)], "expected_revision": 0}
    )
    revision = home.self_work.view()["settings"]["revision"]
    home.self_work.update_settings(
        {
            "auto_approve_proposals": True,
            "confirm_auto_approve": True,
            "expected_revision": revision,
        }
    )

    def ideas(title: str) -> str:
        body = {"proposals": [{"title": title, "description": f"Build {title}", "why": "Slow"}]}
        return f"Findings.\n```json\n{json.dumps(body)}\n```"

    def board() -> dict[str, dict[str, Any]]:
        return {t["title"]: t for t in home.kanban.board()["tickets"]}

    # On the paired desktop: its answer carries the proposal back to this computer.
    p.researcher.answer = ideas("Cache the plugin index")
    there = p.pin()
    p.handed_over(there["id"])
    p.researcher.release.set()
    there = p.both(lambda: (r := run_body(home, there["id"]))["state"] == "succeeded" and r)
    assert there["remote"]["computer_name"] == "Desk B"
    self_work_pass(home, there, p.project, "session-1")
    home.self_work.reconcile()
    remote = board()["Cache the plugin index"]
    assert remote["status"] == "backlog"
    assert remote["proposed"] is True and remote["auto_approved"] is False
    assert remote["proposed_on"] == "Desk B"
    comments = home.kanban.timeline({"ticket_id": remote["ticket_id"], "scope": "rem"})["comments"]
    titles = [c["title"] for c in comments]
    assert "Proposed on Desk B" in titles and "Auto-approved by self-work" not in titles
    added = home.self_work.view()["report"]["tickets_added"]
    assert [(a["proposed"], a["auto_approved"], a["computer_name"]) for a in added] == [
        (True, False, "Desk B")
    ]

    # On this computer: the same answer is approved on its own, as before.
    p.researcher.answer = ideas("Lazy-load the settings page")
    here = home.create_task(
        {
            "request_id": "here",
            "title": "Research here",
            "type": "research",
            "prompt": "What would make startup faster?",
            "project_dir": str(p.project),
            "acceptance_criteria": [
                {"id": "answer", "description": "Name the causes with sources"},
                {"id": "report", "description": "Save a readable report"},
            ],
        }
    )["task"]["active_run"]
    here = p.both(lambda: (r := run_body(home, here["id"]))["state"] == "succeeded" and r)
    assert not here.get("remote")
    self_work_pass(home, here, p.project, "session-1")
    home.self_work.reconcile()
    local = board()["Lazy-load the settings page"]
    assert local["proposed"] is False and local["auto_approved"] is True
    assert local["proposed_on"] == ""
    # The paired desktop's proposal still waits.
    assert board()["Cache the plugin index"]["proposed"] is True


def test_stop_here_stops_the_task_there_and_settles_as_stopped(fleet_pair):
    p = fleet_pair
    run = p.pin()
    _, remote_run = p.handed_over(run["id"])
    p.main_home.store.mutate_run(run["id"], {"request_id": "stop"}, "cancel")
    # Stop doesn't settle here until the other desktop reports the task cancelled.
    p.main_home.tick()
    assert run_body(p.main_home, run["id"])["state"] == "cancelling"
    stopped = p.both(lambda: (r := run_body(p.main_home, run["id"]))["state"] != "cancelling" and r)
    assert stopped["state"] == "cancelled"
    assert run_body(p.worker_home, remote_run["id"])["state"] == "cancelled"
    assert stopped["remote"]["cancel_sent"] is True
    assert stopped["remote"]["result"]["state"] == "cancelled"


def test_forgetting_the_sending_desktop_stops_the_task_it_handed_over(fleet_pair):
    """Nightly audit, 23 November: unpairing a desktop deleted its peer row, so the task it
    had handed over vanished from the list of handed-over tasks and kept running here as
    the user's own Rem task, with no one able to see or stop it from that desktop."""
    p = fleet_pair
    run = p.pin()
    _, remote_run = p.handed_over(run["id"])
    app = p.worker.application
    assert app is not None
    app.remove_peer(identity(p.main))
    listed = app.rem_tasks()
    assert [(t["run_id"], t["authorized"]) for t in listed] == [(remote_run["id"], False)]
    stopped = None
    end = time.monotonic() + 30
    while stopped is None and time.monotonic() < end:
        p.worker_home.tick()
        body = run_body(p.worker_home, remote_run["id"])
        if body["state"] not in {"executing", "cancelling", "queued"}:
            stopped = body
        time.sleep(0.05)
    assert stopped is not None and stopped["state"] == "cancelled", body["state"]
    # It settles here and nothing is queued for the forgotten desktop.
    end = time.monotonic() + 10
    while app.rem_tasks() and time.monotonic() < end:
        p.worker_home.tick()
        time.sleep(0.05)
    assert app.rem_tasks() == []
    with app.registry.lock:
        queued = app.registry.db.execute(
            "SELECT COUNT(*) FROM device_outbox WHERE peer=?", (identity(p.main),)
        ).fetchone()[0]
    assert queued == 0


def test_after_a_restart_the_stand_in_follows_the_same_task_and_never_sends_it_again(
    fleet_pair, tmp_path
):
    p = fleet_pair
    run = p.pin()
    stand_in, remote_run = p.handed_over(run["id"])
    assert len(p.sends) == 1
    # This computer's backend restarts: a new Rem on the same data folder.
    close_rem(p.main_home)
    p.main_home = rem_on(p.main, tmp_path / "rem-main", p.project, p.researcher)
    p.main_home.placement.computers = p.registry
    p.main_home.computer_id = lambda: identity(p.main)
    p.main_home._recover()
    after = run_body(p.main_home, run["id"])
    # Recovery left the stand-in as it was: the same handle, nothing queued again.
    assert after["state"] == "executing" and after["remote"] == stand_in["remote"]
    p.researcher.release.set()
    done = p.both(lambda: (r := run_body(p.main_home, run["id"]))["state"] == "succeeded" and r)
    assert done["worker_result"] == "The slowdown has three causes."
    assert len(p.sends) == 1
    # The worker made exactly one task for it.
    assert [t["title"] for t in p.worker_home.store.tasks()] == ["Find the slowdown"]
    assert run_body(p.worker_home, remote_run["id"])["state"] == "succeeded"


def test_while_the_desktop_is_offline_the_run_waits_for_it_instead_of_moving(fleet_pair):
    p = fleet_pair
    run = p.pin()
    stand_in, _ = p.handed_over(run["id"])
    real = p.registry.read

    def offline() -> dict[str, Any]:
        view = dict(real())
        for computer in view["computers"]:
            if computer["id"] == identity(p.worker):
                computer.update(state="offline", capacity=0)
        return view

    p.registry.read = offline  # type: ignore[method-assign]
    reason = "Waiting for Desk B to come back; this run's work is there"
    waiting = p.both(lambda: (r := run_body(p.main_home, run["id"]))["waiting_reason"] and r)
    assert waiting["waiting_reason"] == reason
    # It stays the same run on the same computer: not moved, not sent again.
    assert waiting["state"] == "executing"
    assert waiting["placement"] == stand_in["placement"]
    assert waiting["remote"]["request_id"] == stand_in["remote"]["request_id"]
    assert len(p.sends) == 1
    # Back online, it carries on there.
    p.registry.read = real  # type: ignore[method-assign]
    p.researcher.release.set()
    done = p.both(lambda: (r := run_body(p.main_home, run["id"]))["state"] == "succeeded" and r)
    assert done["placement"] == stand_in["placement"]


def test_a_develop_task_pinned_to_a_paired_desktop_is_refused_with_a_reason(fleet_pair):
    from gofer.devices.application import host_template

    p = fleet_pair
    with pytest.raises(ValueError, match="Only research and conversation tasks can run on another"):
        p.pin(type="develop")
    # The same task pinned to this computer is fine.
    run = p.main_home.create_task(
        {
            "request_id": "here",
            "title": "Fix it",
            "type": "develop",
            "prompt": "Fix the bug",
            "project_dir": str(p.project),
            "computer": identity(p.main),
        }
    )["task"]["active_run"]
    assert run["recipe"]["computer"] == identity(p.main)
    # The receiving desktop refuses a task that isn't read-only even if one is sent.
    request = str(uuid4())
    p.main.action(
        {
            "action": "send",
            "kind": "job.submit",
            "device_id": identity(p.worker),
            "thread_id": host_template(identity(p.main)),
            "project_id": peer_row(p.main, p.worker)["catalog"]["projects"][0]["id"],
            "request_id": request,
            "text": "Fix it",
            "rem_task": {
                "run_id": "run-x",
                "type": "errand",
                "execution_mode": "work",
                "title": "Fix it",
                "prompt": "Fix the bug",
                "checks": [],
            },
        }
    )
    status = p.both(
        lambda: (
            (
                s := p.main.action(
                    {
                        "action": "work_status",
                        "device_id": identity(p.worker),
                        "request_id": request,
                    }
                )
            )["state"]
            == "rejected"
            and s
        )
    )
    details = [e["payload"]["detail"] for e in status["events"] if e["type"] == "job.status"]
    assert "Only research and conversation tasks run on another computer for now" in details
    assert p.worker_home.store.tasks() == []


def on_main(p: Pair, condition: Callable[[], Any], timeout: float = 60) -> Any:
    """Tick only this computer's Rem, and deliver, until ``condition`` holds.

    The worker's Rem isn't ticked, so nothing handed over to it is admitted there.
    """
    end = time.monotonic() + timeout
    last_probe = 0.0
    while time.monotonic() < end:
        p.main_home.tick()
        if result := condition():
            return result
        if time.monotonic() - last_probe > 0.5:
            last_probe = time.monotonic()
            p.deliver()
        time.sleep(0.05)
    raise AssertionError("condition not met")


def held_over(p: Pair, count: int) -> list[dict[str, Any]]:
    """The tasks handed over to the worker, once it holds ``count`` of them."""
    app = p.worker.application
    assert app is not None
    return cast(list[dict[str, Any]], on_main(p, lambda: len(t := app.rem_tasks()) >= count and t))


def test_your_own_task_takes_the_free_worker_before_an_earlier_handed_over_one(fleet_pair):
    """A task another desktop hands over waits behind this computer's own work, as
    self-work does, even when it was queued first."""
    p = fleet_pair
    p.worker_home.configure(
        {
            "provider": "codex",
            "model": "cli-default",
            "command_slots": {"maxWorkers": 1},
            "projects": [{"root": str(p.project), "name": p.project.name}],
            "resources": {"web": True},
        }
    )
    p.pin()
    held_over(p, 1)
    # The worker's Rem makes its own run for the handed-over task; it is queued.
    p.worker_home.remote.receive()
    [held] = p.worker.application.rem_tasks()  # type: ignore[union-attr]
    handed = run_body(p.worker_home, held["run_id"])
    assert handed["state"] == "queued"
    # It names the desktop that sent it, and so do the checks that came with it.
    assert handed["remote_origin"]["computer_id"] == identity(p.main)
    assert "self_work" not in handed
    assert {c["authored_by"] for c in handed["recipe"]["acceptance_criteria"]} == {
        f"desktop:{identity(p.main)}"
    }
    # Then the user queues a task of their own here, with one worker free.
    own = p.worker_home.create_task(
        {
            "request_id": "own",
            "title": "My own question",
            "type": "research",
            "prompt": "What changed in the startup path?",
            "project_dir": str(p.project),
            "computer": p.worker_home.computer_id(),
        }
    )["task"]["active_run"]
    p.worker_home.tick()
    wait(lambda: p.researcher.runs)
    assert p.researcher.runs == [own["id"]]
    assert run_body(p.worker_home, own["id"])["state"] == "executing"
    assert run_body(p.worker_home, handed["id"])["state"] == "queued"
    # Once the user's task ends, the handed-over one gets the worker.
    p.researcher.release.set()
    p.both(lambda: len(p.researcher.runs) == 2)
    assert p.researcher.runs == [own["id"], handed["id"]]


def test_a_desktop_past_its_cap_of_handed_over_tasks_is_refused_with_the_reason(fleet_pair):
    from gofer.devices.application import REM_TASKS_PER_DESKTOP, host_template

    p = fleet_pair
    assert REM_TASKS_PER_DESKTOP == 10
    project_id = peer_row(p.main, p.worker)["catalog"]["projects"][0]["id"]
    for index in range(REM_TASKS_PER_DESKTOP):
        p.main.action(
            {
                "action": "send",
                "kind": "job.submit",
                "device_id": identity(p.worker),
                "thread_id": host_template(identity(p.main)),
                "project_id": project_id,
                "request_id": str(uuid4()),
                "text": f"Question {index}",
                "rem_task": {
                    "run_id": f"run-{index}",
                    "type": "research",
                    "execution_mode": "work",
                    "title": f"Question {index}",
                    "prompt": "Read the logs",
                    "checks": [],
                },
            }
        )
    # Ten are queued there, none of them started yet.
    assert len(held_over(p, REM_TASKS_PER_DESKTOP)) == REM_TASKS_PER_DESKTOP
    # The eleventh, handed over by this computer's Rem, is refused, and its run here
    # says why, naming the cap.
    run = p.pin()
    done = on_main(p, lambda: (r := run_body(p.main_home, run["id"]))["state"] == "failed" and r)
    assert "Desk B refused this task" in done["summary_short"]
    assert "already has 10 tasks from this computer queued or running" in done["summary_short"]
    assert "the most it takes from one computer at once" in done["summary_short"]
    assert len(p.worker.application.rem_tasks()) == REM_TASKS_PER_DESKTOP  # type: ignore[union-attr]
    assert p.worker_home.store.tasks() == []


class JournalFleet:
    """The device service as a stand-in sees it: a journal of what was queued."""

    def __init__(self, queued: bool) -> None:
        self.queued = queued
        self.sent: list[dict[str, Any]] = []
        self.application = self
        self.registry = None

    def rem_tasks(self) -> list[dict[str, Any]]:
        return []

    def action(self, body: dict[str, Any]) -> dict[str, Any]:
        if body["action"] == "send":
            self.sent.append(body)
            self.queued = True
            return {"state": "queued"}
        assert body["action"] == "work_status"
        state = "queued" if self.queued else "unknown"
        return {"state": state, "request_id": body["request_id"], "events": []}


@pytest.mark.parametrize("queued", [False, True])
def test_a_saved_hand_off_is_sent_only_if_it_never_reached_the_journal(tmp_path, queued):
    """A restart between saving the handle and sending sends it once; after, never again."""
    from tests.unit.test_rem_home import force_run

    project = tmp_path / "Raticode"
    project.mkdir()
    fleet = JournalFleet(queued)
    home = rem_on(cast(DeviceControl, fleet), tmp_path / "rem", project, Researcher())
    try:
        run = home.create_task(
            {
                "request_id": "pin",
                "title": "Find the slowdown",
                "type": "research",
                "prompt": "Why did startup slow down?",
                "project_dir": str(project),
            }
        )["task"]["active_run"]
        handle = {
            "computer_id": str(uuid4()),
            "computer_name": "Desk B",
            "request_id": str(uuid4()),
            "thread_id": str(uuid4()),
            "project_id": str(uuid4()),
            "provider": "codex",
            "sent": False,
        }
        force_run(home, run["id"], state="executing", remote=handle)
        home._recover()
        home.remote.follow()
        home.remote.follow()
        assert len(fleet.sent) == (0 if queued else 1)
        after = run_body(home, run["id"])
        assert after["remote"]["sent"] is True and after["state"] == "executing"
        assert after["remote"]["request_id"] == handle["request_id"]
    finally:
        close_rem(home)
