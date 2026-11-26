import asyncio
import secrets
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from gofer.core.command_slots import slots_root, validate_settings, write_settings
from gofer.core.worker_claims import WorkerClaims
from gofer.devices.application import DeviceApplication
from gofer.devices.registry import DeviceRegistry
from gofer.ui.chat_jobs import ChatJobs
from gofer.ui.chat_steering import ChatSteering
from gofer.ui.device_chat import DeviceChatBridge


class Work:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, str, str | None]] = []
        self.recorded: list[tuple[str, str, list[dict[str, Any]]]] = []
        self.done = threading.Event()
        self.granted = True

    def record_attachments(self, peer, request_id, attachments):
        self.recorded.append((peer, request_id, attachments))

    def claim(self, **_kinds):
        return None

    def incoming_waiting(self):
        return False

    def authorized(self, peer, thread):
        return self.granted

    def runnable(self, peer, request_id):
        return self.granted

    def complete(self, peer, request_id, text, error=None):
        self.results.append((peer, request_id, text, error))
        self.done.set()


def request(tmp_path: Path) -> dict[str, Any]:
    return {
        "peer": str(uuid4()),
        "turn_id": str(uuid4()),
        "event": {
            "thread_id": str(uuid4()),
            "request_id": str(uuid4()),
            "payload": {
                "text": "question",
                "permission_mode": "danger-full-access",
                "project_path": "/untrusted",
            },
        },
        "context": {"provider": "codex", "model": "cli-default", "project_path": str(tmp_path)},
        "messages": [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "question"},
        ],
    }


def test_existing_jobs_run_once_with_local_context_and_history(tmp_path):
    captured = []

    async def source(**kwargs):
        captured.append(kwargs)
        yield {"type": "final", "message": {"body": "answer"}}

    work = Work()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(work, jobs, steering, tmp_path, source=source)
    task = request(tmp_path)
    bridge.dispatch(task)
    assert work.done.wait(5)
    list(jobs.events(task["event"]["thread_id"], task["turn_id"]))
    assert work.results[0][2:] == ("answer", None)
    assert captured[0]["permission_mode"] == "workspace-write"
    assert captured[0]["working_dir"] == tmp_path
    assert captured[0]["messages"][1] == {"role": "assistant", "body": "old answer"}
    bridge.dispatch(task)
    assert len(captured) == 1
    bridge.close()
    jobs.close()
    steering.close()


def test_invalid_policy_and_revocation_never_launch_provider(tmp_path):
    launched = []

    async def source(**kwargs):
        launched.append(kwargs)
        yield {"type": "final", "message": {"body": "should not happen"}}

    for mode, granted in [("invalid-policy", True), ("read-only", False)]:
        work = Work()
        work.granted = granted
        jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
        bridge = DeviceChatBridge(work, jobs, steering, tmp_path, source=source)
        task = request(tmp_path)
        task["context"]["permission_mode"] = mode
        bridge.dispatch(task)
        assert work.done.wait(5)
        list(jobs.events(task["event"]["thread_id"], task["turn_id"]))
        assert work.results[0][3] == "dispatch_failed"
        bridge.close()
        jobs.close()
        steering.close()
    assert launched == []


async def test_revoking_grant_cancels_running_provider(tmp_path):
    started, cancelled = threading.Event(), threading.Event()

    async def source(**kwargs):
        started.set()
        while not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        cancelled.set()
        yield {"type": "stopped"}

    work = Work()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(work, jobs, steering, tmp_path, source=source)
    task = request(tmp_path)
    bridge.dispatch(task)
    assert await asyncio.to_thread(started.wait, 5)
    runner = asyncio.create_task(bridge.run())
    work.granted = False
    assert await asyncio.to_thread(cancelled.wait, 5)
    assert await asyncio.to_thread(work.done.wait, 5)
    bridge.close()
    await runner
    jobs.close()
    steering.close()


async def test_dispatch_recovers_after_transient_claim_failure(tmp_path):
    class IntermittentWork(Work):
        attempts = 0

        def claim(self, **_kinds):
            self.attempts += 1
            if self.attempts == 1:
                raise OSError("temporary storage failure")
            self.done.set()
            return None

    work = IntermittentWork()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(work, jobs, steering, tmp_path)
    runner = asyncio.create_task(bridge.run())
    assert await asyncio.to_thread(work.done.wait, 5)
    assert work.attempts >= 2
    bridge.close()
    await runner
    jobs.close()
    steering.close()


def test_phone_attachment_is_saved_with_the_desktop_thread(tmp_path):
    captured = []
    desktop_thread = "desktop-thread"

    async def source(**kwargs):
        captured.append(kwargs)
        assert "raticode_attachment" in kwargs["messages"][-1]["body"]
        yield {"type": "final", "message": {"body": "File read"}}

    work = Work()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(work, jobs, steering, tmp_path, source=source)
    task = request(tmp_path)
    task["context"]["desktop_thread_id"] = desktop_thread
    task["attachments"] = [
        {"id": "phone-file", "name": "sample.txt", "mime": "text/plain", "data": b"sample"}
    ]
    bridge.dispatch(task)
    assert work.done.wait(5)
    list(jobs.events(desktop_thread, task["turn_id"]))
    assert captured
    assert work.results[0][2:] == ("File read", None)
    # The file outlives the turn, under the desktop thread, so later desktop
    # turns that resend this message can still read it.
    files = list((tmp_path / "chat-attachments" / desktop_thread).iterdir())
    assert [path.read_bytes() for path in files] == [b"sample"]
    ((peer, request_id, saved),) = work.recorded
    assert (peer, request_id) == (task["peer"], task["event"]["request_id"])
    assert saved == [
        {
            "id": "phone-file",
            "name": "sample.txt",
            "size": 6,
            "type": "text/plain",
            "mime": "text/plain",
            "storageName": files[0].name,
        }
    ]
    bridge.close()
    jobs.close()
    steering.close()


def test_earlier_phone_attachments_reach_later_turns(tmp_path):
    from gofer.ui.chat_media import store_chat_attachments

    captured = []

    async def source(**kwargs):
        captured.append(kwargs)
        yield {"type": "final", "message": {"body": "ok"}}

    thread = "desktop-thread"
    stored = store_chat_attachments(
        {"threadId": thread, "files": [{"name": "photo.png", "type": "image/png", "data": ""}]},
        tmp_path,
    )["attachments"]
    work = Work()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(work, jobs, steering, tmp_path, source=source)
    task = request(tmp_path)
    task["context"]["desktop_thread_id"] = thread
    task["messages"] = [
        {"role": "user", "content": "look at this", "attachments": stored},
        {"role": "user", "content": "lost", "attachments": [{"name": "gone.png"}]},
        {"role": "assistant", "content": "seen"},
        {"role": "user", "content": "what was in the photo?"},
    ]
    bridge.dispatch(task)
    assert work.done.wait(5)
    list(jobs.events(thread, task["turn_id"]))
    first, lost = captured[0]["messages"][:2]
    assert (
        f'path="{tmp_path / "chat-attachments" / thread / stored[0]["storageName"]}"'
        in (first["body"])
    )
    assert "[Attachment no longer available: gone.png]" in lost["body"]
    bridge.close()
    jobs.close()
    steering.close()


def test_fleet_permission_requires_an_explicit_boolean(tmp_path):
    import pytest

    from gofer.ui.device_chat import validate_context

    context = request(tmp_path)["context"]
    for value in ("true", "false", 1, None):
        with pytest.raises(ValueError, match="invalid_fleet_permission"):
            validate_context({**context, "fleet_execute": value})
    assert validate_context({**context, "fleet_execute": True})["fleet_execute"] is True
    assert validate_context({**context, "fleet_execute": False})["fleet_execute"] is False


@pytest.mark.parametrize("provider", ["cursor", "copilot", "opencode"])
def test_additional_remote_providers_keep_existing_default_policy(tmp_path, provider):
    from gofer.ui.device_chat import validate_context

    context = request(tmp_path)["context"]
    assert (
        validate_context({**context, "provider": provider, "permission_mode": "default"})[
            "provider"
        ]
        == provider
    )
    with pytest.raises(ValueError, match="unsupported_provider_permission"):
        validate_context({**context, "provider": provider, "permission_mode": "danger-full-access"})


class Secrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, account: str) -> str | None:
        return self.values.get(account)

    def put(self, account: str, value: str) -> None:
        self.values[account] = value


def paired(registry: DeviceRegistry, role: str) -> str:
    """A trusted peer: ``desktop`` for another desktop, ``controller`` for a phone."""
    peer, pin = str(uuid4()), secrets.token_bytes(32)
    registry.propose(peer, pin, "controller", registry.clock() + 300)
    registry.confirm(peer, pin.hex())
    registry.acknowledge(
        peer,
        pin,
        "controller",
        {"type": "session.ready_ack", "device_id": peer, "trust_revision": 1},
    )
    registry.db.execute("UPDATE peers SET role=? WHERE device_id=?", (role, peer))
    return peer


def submit(app: DeviceApplication, peer: str, project: Path, kind: str) -> dict[str, Any]:
    """A granted thread on this desktop and one request in it from ``peer``."""
    thread, project_id = str(uuid4()), str(uuid4())
    app.authorize(
        peer,
        thread,
        {
            "provider": "codex",
            "model": "cli-default",
            "project_id": project_id,
            "project_path": str(project),
        },
    )
    event: dict[str, Any] = {
        "version": 2,
        "id": str(uuid4()),
        "thread_id": thread,
        "request_id": str(uuid4()),
        "sequence": 0,
        "type": kind,
        "payload": {"text": "Run the checks", "attachment_ids": []},
    }
    if kind == "job.submit":
        event["payload"] = {
            "job_id": event["request_id"],
            "target_device_id": app.registry.identity.device_id,
            "project_id": project_id,
            "resource_ids": [],
            "intent": "Run the checks",
            "expected_policy_revision": 1,
            "organization": {"organizationId": "org-1", "taskId": "t-1", "parentRunId": "run-1"},
        }
    app.handle(peer, event)
    return event


def work_state(app: DeviceApplication, event: dict[str, Any]) -> str:
    row = app.registry.db.execute(
        "SELECT state FROM device_work WHERE request=?", (event["request_id"],)
    ).fetchone()
    return str(row[0])


class Busy:
    """A provider that keeps each turn running until released."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started: list[str] = []

    async def __call__(self, **kwargs: Any):
        self.started.append(kwargs["messages"][-1]["body"])
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        yield {"type": "final", "message": {"body": "done"}}


@pytest.fixture
def full_desktop(tmp_path: Path):
    """This desktop with a worker limit of 2 and two Rem workers claimed."""
    write_settings(slots_root(tmp_path), validate_settings({"maxWorkers": 2}))
    registry = DeviceRegistry(tmp_path / "devices", Secrets(), initialize=True)
    app = DeviceApplication(registry)
    app.name = "Workstation"
    provider = Busy()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(app, jobs, steering, tmp_path, source=provider)
    bridge.claims = WorkerClaims()
    bridge.computer_id = lambda: "pc-1"
    rem = [bridge.claims.claim("pc-1", "rem", 2) for _ in range(2)]
    assert all(rem)
    try:
        yield app, bridge, provider, rem
    finally:
        provider.release.set()
        bridge.close()
        deadline = time.monotonic() + 5
        while bridge.claims.held(consumer="phone") and time.monotonic() < deadline:
            time.sleep(0.01)
        jobs.close()
        steering.close()
        registry.close()


def until(condition: Any, timeout: float = 5) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := condition():
            return value
        time.sleep(0.01)
    raise AssertionError("condition not met")


def test_organization_job_from_a_paired_desktop_waits_for_a_free_worker(full_desktop, tmp_path):
    app, bridge, provider, rem = full_desktop
    desktop = paired(app.registry, "desktop")
    job = submit(app, desktop, tmp_path, "job.submit")
    # No room under the worker limit: nothing starts and the job stays accepted.
    assert not bridge._tick()
    assert not bridge._tick()
    assert work_state(app, job) == "accepted"
    assert provider.started == []
    assert bridge.claims.running("pc-1") == {"rem": 2, "phone": 0, "organizations": 0, "swarms": 0}
    # The sending desktop is told once why it waits.
    waiting = [
        e["payload"]
        for e in app.pending(desktop)
        if e["type"] == "job.status" and e["request_id"] == job["request_id"]
    ]
    assert waiting[-1] == {
        "job_id": job["payload"]["job_id"],
        "state": "accepted",
        "revision": 1,
        "detail": "Waiting for a free worker on Workstation",
    }
    assert len([p for p in waiting if p["detail"].startswith("Waiting")]) == 1
    # A Rem worker ends: the job starts and takes that worker.
    rem[0].release()
    assert bridge._tick()
    until(lambda: provider.started)
    assert work_state(app, job) == "running"
    assert bridge.claims.running("pc-1") == {"rem": 1, "phone": 0, "organizations": 1, "swarms": 0}
    # Settings › Computers names it as work from the desktop that sent it.
    assert [w for w in bridge.claims.running_work("pc-1") if w["kind"] == "organizations"] == [
        {"kind": "organizations", "label": "work from another desktop"}
    ]
    # The computer is full again, counted once: no other start fits.
    assert bridge.claims.claim("pc-1", "rem", 2) is None
    provider.release.set()
    until(lambda: work_state(app, job) == "completed")
    until(lambda: bridge.claims.held("pc-1") == 1)


def test_phone_turn_still_starts_on_a_full_desktop_and_is_counted(full_desktop, tmp_path):
    app, bridge, provider, rem = full_desktop
    desktop, phone = paired(app.registry, "desktop"), paired(app.registry, "controller")
    job = submit(app, desktop, tmp_path, "job.submit")
    turn = submit(app, phone, tmp_path, "chat.submit")
    # Your own turn waits only for your other turns, even behind a waiting job.
    assert bridge._tick()
    until(lambda: provider.started)
    assert work_state(app, turn) == "running"
    assert work_state(app, job) == "accepted"
    assert bridge.claims.running("pc-1") == {"rem": 2, "phone": 1, "organizations": 0, "swarms": 0}
    assert {"kind": "phone", "label": "phone turn"} in bridge.claims.running_work("pc-1")
    assert bridge.running() == 1
    provider.release.set()
    until(lambda: work_state(app, turn) == "completed")
    until(lambda: bridge.running() == 0)


@pytest.fixture
def idle_desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """This desktop with room for work, counting worker claims and room reads."""
    from gofer.ui import device_chat

    write_settings(slots_root(tmp_path), validate_settings({"maxWorkers": 4}))
    registry = DeviceRegistry(tmp_path / "devices", Secrets(), initialize=True)
    app = DeviceApplication(registry)
    provider = Busy()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(app, jobs, steering, tmp_path, source=provider)
    bridge.claims = WorkerClaims()
    bridge.computer_id = lambda: "pc-1"
    calls: dict[str, list[Any]] = {"claim": [], "room": []}
    claim = bridge.claims.claim

    def counted_claim(*args: Any, **kwargs: Any) -> Any:
        calls["claim"].append(args)
        return claim(*args, **kwargs)

    def counted_room(data_dir: Path) -> int:
        calls["room"].append(data_dir)
        return 4

    monkeypatch.setattr(bridge.claims, "claim", counted_claim)
    monkeypatch.setattr(device_chat, "local_room_limit", counted_room)
    try:
        yield app, bridge, provider, calls
    finally:
        provider.release.set()
        bridge.close()
        deadline = time.monotonic() + 5
        while bridge.claims.held("pc-1") and time.monotonic() < deadline:
            time.sleep(0.01)
        jobs.close()
        steering.close()
        registry.close()


def test_empty_ticks_never_take_a_worker(idle_desktop):
    app, bridge, provider, calls = idle_desktop
    assert not any(bridge._tick() for _ in range(20))
    # No worker was claimed, even for a moment, and the room was never read.
    assert calls == {"claim": [], "room": []}
    assert bridge.claims.held("pc-1") == 0
    assert provider.started == []


def test_waiting_organization_job_takes_exactly_one_worker(idle_desktop, tmp_path):
    app, bridge, provider, calls = idle_desktop
    desktop = paired(app.registry, "desktop")
    app.registry.save_name(desktop, "Laptop")
    job = submit(app, desktop, tmp_path, "job.submit")
    assert bridge._tick()
    until(lambda: provider.started)
    # One worker, claimed for organizations on this computer and labelled by sender.
    assert calls["claim"] == [("pc-1", "organizations", 4)]
    assert len(calls["room"]) == 1
    assert work_state(app, job) == "running"
    assert bridge.claims.running("pc-1") == {"rem": 0, "phone": 0, "organizations": 1, "swarms": 0}
    assert bridge.claims.running_work("pc-1") == [
        {"kind": "organizations", "label": "work from Laptop"}
    ]
    # With nothing else waiting, later ticks take no more workers.
    assert not bridge._tick()
    assert len(calls["claim"]) == 1
    provider.release.set()
    until(lambda: work_state(app, job) == "completed")
    until(lambda: bridge.claims.held("pc-1") == 0)


def test_older_device_database_marks_its_waiting_organization_jobs(tmp_path):
    registry = DeviceRegistry(tmp_path / "devices", Secrets(), initialize=True)
    app = DeviceApplication(registry)
    desktop, phone = paired(registry, "desktop"), paired(registry, "controller")
    job = submit(app, desktop, tmp_path, "job.submit")
    submit(app, phone, tmp_path, "chat.submit")
    # A database from before the incoming column: drop it, keeping the queued work.
    registry.db.execute("DROP INDEX device_work_incoming_queued")
    registry.db.execute("ALTER TABLE device_work DROP COLUMN incoming")
    reopened = DeviceApplication(registry)
    assert reopened.incoming_waiting()
    marked = registry.db.execute("SELECT request FROM device_work WHERE incoming=1").fetchall()
    assert [row[0] for row in marked] == [job["request_id"]]
    registry.close()


def test_organization_job_from_another_desktop_runs_under_unattended_limits(tmp_path):
    """Work a paired desktop's organization sends gets a handed-over task's limits.

    A phone turn on the same grant keeps the grant's Second Brain and resources.
    """
    captured: list[dict[str, Any]] = []

    async def source(**kwargs):
        captured.append(kwargs)
        yield {"type": "final", "message": {"body": "done"}}

    brain = {"enabled": True, "root": str(tmp_path / "brain")}
    resources = {
        "shell": True,
        "web": True,
        "agents": True,
        "browser": True,
        "mcpServers": [{"name": "notes", "type": "stdio", "command": "notes"}],
    }
    grant = {
        "provider": "claude_code",
        "model": "cli-default",
        "project_path": str(tmp_path),
        "workflow": {
            "remSecondBrain": brain,
            "remResources": resources,
            "remBrowser": {"target": "chromium"},
            "remSwarmAccess": {"enabled": True, "grantId": "grant-1"},
        },
    }
    work = Work()
    jobs, steering = ChatJobs(tmp_path), ChatSteering(tmp_path)
    bridge = DeviceChatBridge(work, jobs, steering, tmp_path, source=source)

    def run(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        task = request(tmp_path)
        task["context"] = grant
        task["peer_role"] = "desktop" if kind == "job.submit" else "controller"
        task["event"]["type"] = kind
        task["event"]["payload"] = payload
        task["messages"] = []
        work.done.clear()
        bridge.dispatch(task)
        assert work.done.wait(5)
        list(jobs.events(task["event"]["thread_id"], task["turn_id"]))
        return captured[-1]

    organization = {"organizationId": "org-1", "taskId": "t-1", "parentRunId": "run-1"}
    job = run("job.submit", {"text": "Run the checks", "organization": organization})
    workflow = job["workflow"]
    assert workflow["remManagedTask"] == {
        "readOnly": False,
        "unattended": True,
        "networkAccess": True,
        "claudeSandbox": True,
        "limitDataFolder": True,
    }
    assert workflow["remBlockGitPush"] is True
    assert workflow["remUnattended"] is True
    assert "remSecondBrain" not in workflow
    assert "remBrowser" not in workflow
    assert workflow["remResources"] == {
        **resources,
        "agents": False,
        "browser": False,
        "mcpServers": [],
    }
    assert workflow["remSwarmAccess"] == {"enabled": False, "grantId": "grant-1"}
    assert workflow["organizationRun"] == organization
    assert "An organization on a paired desktop sent this work" in job["agent_instructions"]
    assert "Nobody is watching this run" in job["agent_instructions"]

    phone = run("chat.submit", {"text": "How is it going?"})
    assert phone["workflow"]["remSecondBrain"] == brain
    assert phone["workflow"]["remResources"] == resources
    assert phone["workflow"]["remBrowser"] == {"target": "chromium"}
    assert phone["workflow"]["remSwarmAccess"] == {"enabled": True, "grantId": "grant-1"}
    assert "remManagedTask" not in phone["workflow"]
    assert "remBlockGitPush" not in phone["workflow"]
    assert "agent_instructions" not in phone
    assert [result[3] for result in work.results] == [None, None]
    bridge.close()
    jobs.close()
    steering.close()
