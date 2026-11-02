"""Desktop-to-desktop fleet over real loopback TLS, with disposable identities."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
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
    invite = responder.invitations.create(name, {"host": "127.0.0.1", "port": responder.port})
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
    assert {e["type"] for e in received} <= {"sync.snapshot", "fleet.snapshot"}


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
    second.fleet_probed.clear()
    shared = wait(lambda: peer_row(second, first)["shared_threads"])
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
