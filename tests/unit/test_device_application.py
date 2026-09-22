"""Durable application boundaries, with disposable identities and no providers."""

from __future__ import annotations

import secrets
from typing import Any
from uuid import uuid4

import pytest

from gofer.devices.application import DeviceApplication
from gofer.devices.registry import DeviceRegistry, PairingError


class TestSecrets:
    __test__ = False

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, account: str) -> str | None:
        return self.values.get(account)

    def put(self, account: str, value: str) -> None:
        self.values[account] = value


@pytest.fixture
def application(tmp_path):
    registry = DeviceRegistry(tmp_path, TestSecrets(), initialize=True)
    peer, pin = str(uuid4()), secrets.token_bytes(32)
    registry.propose(peer, pin, "controller", registry.clock() + 300)
    registry.confirm(peer, pin.hex())
    registry.acknowledge(
        peer,
        pin,
        "controller",
        {"type": "session.ready_ack", "device_id": peer, "trust_revision": 1},
    )
    app = DeviceApplication(registry)
    yield app, peer
    registry.close()


def event(kind: str = "chat.submit", sequence: int = 0, **changes: Any) -> dict[str, Any]:
    return {
        "version": 2,
        "id": str(uuid4()),
        "thread_id": str(uuid4()),
        "request_id": str(uuid4()),
        "sequence": sequence,
        "type": kind,
        "payload": {"text": "private message", "attachment_ids": []},
        **changes,
    }


def grant(app: DeviceApplication, peer: str, request: dict[str, Any]) -> None:
    app.authorize(
        peer,
        request["thread_id"],
        {"provider": "codex", "model": "cli-default", "permission_mode": "read-only"},
    )


def test_pairing_alone_never_dispatches(application):
    app, peer = application
    assert app.handle(peer, event())[0]["type"] == "error"
    assert app.claim() is None


def test_durable_acceptance_and_response_encrypted(application):
    app, peer = application
    request = event()
    grant(app, peer, request)
    response = app.handle(peer, request)
    assert response[0]["type"] == "chat.accepted"
    assert app.handle(peer, request) == response
    claim = app.claim()
    assert claim["event"] == request
    assert app.claim() is None
    app.complete(peer, request["request_id"], "private response")
    assert [e["type"] for e in app.pending(peer)] == [
        "chat.accepted",
        "request.status",
        "chat.message",
        "request.status",
    ]
    for row in app.registry.db.execute(
        "SELECT event FROM device_work UNION ALL SELECT event FROM device_outbox"
    ):
        assert b"private" not in row[0]


def test_conflicting_retry_and_sequence_fail_closed(application):
    app, peer = application
    request = event()
    grant(app, peer, request)
    app.handle(peer, request)
    with pytest.raises(PairingError, match="event_id_conflict"):
        app.handle(peer, request | {"payload": {"text": "changed", "attachment_ids": []}})
    with pytest.raises(PairingError, match="sequence_conflict"):
        app.handle(peer, event())


def test_new_event_id_same_operation_does_not_execute_twice(application):
    app, peer = application
    request = event()
    grant(app, peer, request)
    app.handle(peer, request)
    app.handle(peer, request | {"id": str(uuid4()), "sequence": 1})
    assert app.claim() is not None
    assert app.claim() is None


def test_restart_does_not_retry_uncertain_dispatch(application):
    app, peer = application
    request = event()
    grant(app, peer, request)
    app.handle(peer, request)
    app.claim()
    recovered = DeviceApplication(app.registry)
    assert recovered.claim() is None
    assert recovered.pending(peer)[-1]["payload"]["status"] == "outcome_unknown"


def test_revocation_blocks_queue_and_responses(application):
    app, peer = application
    request = event()
    grant(app, peer, request)
    app.handle(peer, request)
    app.registry.revoke(peer)
    assert app.claim() is None
    with pytest.raises(PairingError, match="peer_unavailable"):
        app.pending(peer)


def test_grant_revocation_blocks_queued_dispatch(application):
    app, peer = application
    request = event()
    grant(app, peer, request)
    app.handle(peer, request)
    app.revoke_grant(peer, request["thread_id"])
    assert not app.authorized(peer, request["thread_id"])
    assert app.claim() is None


def test_sync_exposes_only_locally_shared_threads(application):
    app, peer = application
    request = event()
    grant(app, peer, request)
    response = app.handle(
        peer, event("sync.request", thread_id=None, payload={"after_sequence": None, "limit": 100})
    )
    assert response[-1]["type"] == "sync.snapshot"
    assert response[-1]["payload"]["threads"][0]["thread_id"] == request["thread_id"]


def test_fleet_stale_identity_is_not_up(application):
    app, peer = application
    other, pin = str(uuid4()), secrets.token_bytes(32)
    app.registry.propose(other, pin, "desktop", app.registry.clock() + 300)
    app.registry.confirm(other, pin.hex())
    app.registry.acknowledge(
        other,
        pin,
        "desktop",
        {"type": "session.ready_ack", "device_id": other, "trust_revision": 1},
    )
    app.registry.db.execute("UPDATE peers SET last_seen=1 WHERE device_id=?", (other,))
    response = app.handle(peer, event("fleet.request", thread_id=None, payload={}))
    assert (
        next(d for d in response[0]["payload"]["devices"] if d["device_id"] == other)["status"]
        == "unknown"
    )


@pytest.mark.asyncio
async def test_real_desktop_exchange_dispatches_once_and_returns_response(tmp_path):
    import asyncio

    from gofer.devices.client import exchange, pair_desktop
    from gofer.devices.pairing import Invitations
    from gofer.devices.service import DeviceListener

    source = DeviceRegistry(tmp_path / "a", TestSecrets(), initialize=True)
    target = DeviceRegistry(tmp_path / "b", TestSecrets(), initialize=True)
    application = DeviceApplication(target)
    invitations = Invitations(target)
    listener = DeviceListener(target, invitations, application)
    port = await listener.start()
    invite = invitations.create("Test", "https://ntfy.sh", {"host": "127.0.0.1", "port": port})

    async def confirm() -> None:
        while not target.list():
            await asyncio.sleep(0.01)
        candidate = target.list()[0]
        target.confirm(candidate["device_id"], candidate["fingerprint"])

    confirmation = asyncio.create_task(confirm())
    try:
        await pair_desktop(source, invite["uri"])
        await confirmation

        # Draining ready_ack does not mean the responder has committed it yet.
        # Keep production grants restricted to fully active trust.
        async def wait_active() -> None:
            while target._row(source.identity.device_id)["state"] != "active":
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_active(), timeout=5)
        request = event()
        grant(application, source.identity.device_id, request)
        received: list[dict[str, Any]] = []

        async def worker() -> None:
            work = None
            while work is None:
                work = application.claim()
                await asyncio.sleep(0.01)
            application.complete(work["peer"], work["event"]["request_id"], "Real TLS response")

        task = asyncio.create_task(worker())
        await asyncio.wait_for(
            exchange(source, target.identity.device_id, request, received.append), 5
        )
        await task
        assert any(
            e["type"] == "chat.message" and e["payload"]["text"] == "Real TLS response"
            for e in received
        )
        await asyncio.wait_for(
            exchange(source, target.identity.device_id, request, received.append), 5
        )
        assert application.claim() is None
    finally:
        await listener.close()
        source.close()
        target.close()


def test_uploaded_file_is_available_to_authorized_rem_only(application):
    import base64
    import hashlib

    app, peer = application
    request = event()
    grant(app, peer, request)
    file_id = str(uuid4())
    metadata = {
        "file_id": file_id,
        "name": "note.txt",
        "mime": "text/plain",
        "size": 5,
        "sha256": hashlib.sha256(b"hello").hexdigest(),
        "expires_at": int(app.registry.clock()) + 300,
    }
    offered = event("file.offer", thread_id=request["thread_id"], payload=metadata)
    assert app.handle(peer, offered)[0]["payload"]["state"] == "receiving"
    data = event(
        "file.chunk",
        sequence=1,
        thread_id=request["thread_id"],
        payload={
            "file_id": file_id,
            "offset": 0,
            "data": base64.urlsafe_b64encode(b"hello").rstrip(b"=").decode(),
            "eof": True,
        },
    )
    assert app.handle(peer, data)[0]["payload"]["state"] == "available"
    request["sequence"] = 2
    request["payload"]["attachment_ids"] = [file_id]
    app.handle(peer, request)
    claim = app.claim()
    assert claim is not None
    assert claim["attachments"] == [{"name": "note.txt", "mime": "text/plain", "data": b"hello"}]


def test_acknowledged_chunks_pruned_without_reusing_sequence(application):
    app, peer = application
    request = event()
    grant(app, peer, request)
    file_id = str(uuid4())
    with app.registry.transaction():
        first = app._emit(peer, request, "chat.accepted", {"message_id": request["request_id"]})
        app.registry.db.execute(
            "INSERT INTO device_chunk_receipts VALUES (?,?,?,?)",
            (peer, file_id, first["sequence"], 5),
        )
    status = event(
        "file.status",
        thread_id=request["thread_id"],
        payload={"file_id": file_id, "state": "available", "received_size": 5},
    )
    response = app.handle(peer, status)
    assert response[0]["sequence"] > first["sequence"]
    assert all(e["id"] != first["id"] for e in app.pending(peer))


def test_provider_history_survives_outbox_retention(application):
    app, peer = application
    request = event()
    grant(app, peer, request)
    app.handle(peer, request)
    app.claim()
    app.complete(peer, request["request_id"], "Keep this answer")
    with app.registry.transaction():
        app.registry.db.execute("DELETE FROM device_outbox WHERE peer=?", (peer,))
    history = app.history(peer, request["thread_id"])
    assert history[-1] == {"role": "assistant", "content": "Keep this answer"}
    next_request = event(sequence=1, thread_id=request["thread_id"])
    response = app.handle(peer, next_request)
    assert response[0]["sequence"] > 4


def test_desktop_job_get_and_cancel_use_stable_job_mapping(application):
    app, peer = application
    app.registry.db.execute("UPDATE peers SET role='desktop' WHERE device_id=?", (peer,))
    request = event()
    project, job_id = str(uuid4()), str(uuid4())
    app.authorize(
        peer,
        request["thread_id"],
        {"provider": "codex", "model": "cli-default", "project_id": project},
    )
    request["type"] = "job.submit"
    request["payload"] = {
        "job_id": job_id,
        "target_device_id": app.registry.identity.device_id,
        "project_id": project,
        "resource_ids": [],
        "intent": "test",
        "expected_policy_revision": 1,
    }
    app.handle(peer, request)
    status = app.handle(
        peer,
        event("job.get", sequence=1, thread_id=request["thread_id"], payload={"job_id": job_id}),
    )
    assert status[0]["payload"]["state"] == "accepted"
    status = app.handle(
        peer,
        event("job.cancel", sequence=2, thread_id=request["thread_id"], payload={"job_id": job_id}),
    )
    assert status[0]["payload"]["state"] == "cancel_requested"
    assert app.claim() is None


def test_full_outbox_recovers_on_authenticated_sync_without_losing_dedup(application, monkeypatch):
    import gofer.devices.application as module

    app, peer = application
    monkeypatch.setattr(module, "OUTBOX_EVENTS", 3)
    monkeypatch.setattr(module, "RETAIN_ACKNOWLEDGED", 1)
    source = event()
    grant(app, peer, source)
    accepted = app.handle(peer, source)
    with app.registry.transaction():
        for _ in range(3 - len(accepted)):
            app._status(peer, source, "running", "Test status")
    before = app.pending(peer)
    rejected = event(sequence=1, thread_id=source["thread_id"])
    with pytest.raises(PairingError, match="outbox_quota"):
        app.handle(peer, rejected)
    assert (
        app.registry.db.execute(
            "SELECT 1 FROM device_work WHERE request=?", (rejected["request_id"],)
        ).fetchone()
        is None
    )
    cursor = before[-1]["sequence"]
    app.handle(
        peer,
        event(
            "sync.request",
            sequence=1,
            thread_id=None,
            payload={"after_sequence": cursor, "limit": 100},
        ),
    )
    remaining = app.pending(peer)
    assert remaining[-1]["sequence"] > cursor
    assert remaining[0]["sequence"] == cursor
    app.handle(peer, source)  # Retained inbox identity still prevents a second dispatch.
    work = app.claim()
    assert work is not None and work["event"]["request_id"] == source["request_id"]
    assert app.claim() is None


def test_outbox_byte_quota_counts_new_encrypted_payload_and_rolls_back(application, monkeypatch):
    import gofer.devices.application as module

    app, peer = application
    source = event()
    grant(app, peer, source)
    monkeypatch.setattr(module, "OUTBOX_BYTES", 1)
    with pytest.raises(PairingError, match="outbox_byte_quota"):
        app.handle(peer, source)
    assert not app.pending(peer)
    assert app.claim() is None
    monkeypatch.setattr(module, "OUTBOX_BYTES", 64 * 1024 * 1024)
    assert app.handle(peer, source)[0]["type"] == "chat.accepted"


def test_fleet_grant_revocation_cancels_work_and_regrant_does_not_replay(application):
    app, peer = application
    source = event()
    context = {"provider": "codex", "model": "cli-default", "fleet_execute": True}
    app.authorize(peer, source["thread_id"], context)
    app.handle(peer, source)
    assert app.claim()["context"]["fleet_execute"] is True
    app.revoke_grant(peer, source["thread_id"])
    assert not app.runnable(peer, source["request_id"])
    app.authorize(peer, source["thread_id"], {**context, "fleet_execute": False})
    app.handle(peer, source)
    assert app.claim() is None


def test_connection_sync_is_opt_in_and_keeps_thread_context(application):
    app, peer = application
    thread, project, resource = str(uuid4()), str(uuid4()), str(uuid4())
    app.authorize(
        peer,
        thread,
        {
            "title": "My project",
            "provider": "codex",
            "model": "local-model",
            "project_id": project,
            "resource_ids": [resource],
            "allow_thread_create": True,
        },
    )
    app.lan_endpoint = {"host": "192.168.1.20", "port": 18766}
    app.rem_ready = lambda: True
    legacy = app.handle(
        peer, event("sync.request", thread_id=None, payload={"after_sequence": None, "limit": 100})
    )[-1]["payload"]
    assert "connection" not in legacy
    assert "can_create" not in legacy["threads"][0]
    result = app.handle(
        peer,
        event(
            "sync.request",
            sequence=1,
            thread_id=None,
            payload={"after_sequence": None, "limit": 100, "include_connection": True},
        ),
    )[-1]["payload"]
    assert result["connection"] == {"lan_endpoint": app.lan_endpoint, "rem_ready": True}
    assert result["threads"][0]["project_id"] == project
    assert result["threads"][0]["resource_ids"] == [resource]
    assert result["threads"][0]["can_create"] is True


def test_phone_creation_inherits_only_local_grant_and_is_durable(application, tmp_path):
    app, peer = application
    parent, project, resource = str(uuid4()), str(uuid4()), str(uuid4())
    context = {
        "title": "Allowed project",
        "provider": "codex",
        "model": "local-model",
        "permission_mode": "read-only",
        "project_path": str(tmp_path),
        "project_id": project,
        "resource_ids": [resource],
        "allow_thread_create": True,
    }
    create = event(
        "thread.create",
        thread_id=None,
        payload={
            "title": "New real conversation",
            "project_id": project,
            "resource_ids": [resource],
            "provider": "codex",
            "model": "local-model",
            "template_thread_id": parent,
        },
    )
    assert app.handle(peer, create)[0]["payload"]["code"] == "unauthorized"
    app.authorize(peer, parent, context)
    create = {**create, "id": str(uuid4()), "request_id": str(uuid4()), "sequence": 1}
    created = app.handle(peer, create)[0]
    assert created["type"] == "thread.snapshot"
    thread = created["thread_id"]
    assert thread != parent
    # Same request, even a fresh envelope after restart, identifies the same conversation.
    reopened = DeviceApplication(app.registry)
    again = reopened.handle(peer, {**create, "id": str(uuid4()), "sequence": 2})[-1]
    assert again["thread_id"] == thread
    assert len(reopened.grants()) == 2
    child = next(g for g in reopened.grants() if g["thread_id"] == thread)
    assert child["permission_mode"] == "read-only"
    assert child["project_path"] == str(tmp_path)
    assert child["allow_thread_create"] is False
    assert child["parent_thread"] == parent
    altered = {
        **create,
        "id": str(uuid4()),
        "sequence": 3,
        "payload": {**create["payload"], "model": "ungranted-model"},
    }
    assert reopened.handle(peer, altered)[0]["payload"]["code"] == "conflict"
    altered["request_id"] = str(uuid4())
    altered["id"] = str(uuid4())
    altered["sequence"] = 4
    assert reopened.handle(peer, altered)[0]["payload"]["code"] == "unauthorized"
    submitted = event(sequence=5, thread_id=thread)
    assert reopened.handle(peer, submitted)[0]["type"] == "chat.accepted"
    claimed = reopened.claim()
    assert claimed is not None
    assert claimed["context"]["permission_mode"] == "read-only"
    reopened.revoke_grant(peer, parent)
    assert not reopened.runnable(peer, submitted["request_id"])
    assert not reopened.authorized(peer, thread)
    retry = reopened.handle(peer, {**create, "id": str(uuid4()), "sequence": 6})[0]
    assert retry["payload"]["code"] == "unauthorized"


def test_changing_parent_grant_revokes_derived_conversations(application, tmp_path):
    app, peer = application
    parent, project = str(uuid4()), str(uuid4())
    context = {
        "title": "Project",
        "provider": "codex",
        "model": "local-model",
        "permission_mode": "workspace-write",
        "project_path": str(tmp_path),
        "project_id": project,
        "allow_thread_create": True,
    }
    app.authorize(peer, parent, context)
    created = app.handle(
        peer,
        event(
            "thread.create",
            thread_id=None,
            payload={
                "title": "Child",
                "project_id": project,
                "resource_ids": [],
                "provider": "codex",
                "model": "local-model",
                "template_thread_id": parent,
            },
        ),
    )[0]
    app.authorize(peer, parent, {**context, "permission_mode": "read-only"})
    assert not app.authorized(peer, created["thread_id"])
    assert app.authorized(peer, parent)


@pytest.mark.parametrize("revoked", [False, True])
def test_remove_pairing_clears_access_but_retains_request_deduplication(application, revoked):
    app, peer = application
    registry = app.registry
    pin = registry.reconnect_pin(peer)
    generation = registry.generation(peer)
    request = event()
    grant(app, peer, request)
    app.handle(peer, request)
    app.workspace.enable(peer, True)
    registry.save_endpoint(peer, {"host": "192.168.1.10", "port": 44233})
    registry.save_relay(peer, "https://ntfy.sh")
    if revoked:
        registry.revoke(peer)
    app.remove_peer(peer, revoked_only=revoked)
    assert registry.list() == []
    assert registry.trusted_pins() == frozenset()
    assert registry.relay_channels() == []
    assert app.grants() == []
    assert app.workspace.enabled() == []
    assert app.claim() is None
    assert registry.db.execute("SELECT COUNT(*) FROM device_outbox").fetchone()[0] == 0
    assert registry.db.execute("SELECT COUNT(*) FROM endpoints").fetchone()[0] == 0
    with pytest.raises(PairingError):
        registry.authenticate(peer, pin, "controller")
    # Fresh enrollment can reuse the phone identity, but no session, grant,
    # shared history or queued work is restored. The new pairing enables sync.
    registry.propose(peer, pin, "controller", registry.clock() + 300)
    registry.confirm(peer, pin.hex())
    registry.acknowledge(
        peer,
        pin,
        "controller",
        {
            "type": "session.ready_ack",
            "device_id": peer,
            "trust_revision": 1,
        },
    )
    assert registry.generation(peer) != generation
    with pytest.raises(PairingError, match="pairing_changed"):
        registry.check_generation(peer, generation)
    assert app.grants() == []
    assert app.workspace.enabled() == [peer]
    assert app.claim() is None
    grant(app, peer, request)
    app.handle(peer, request)
    assert app.claim() is None
    assert (
        registry.db.execute(
            "SELECT state FROM device_work WHERE peer=? AND request=?",
            (peer, request["request_id"]),
        ).fetchone()[0]
        == "cancelled"
    )


def test_remove_revoked_does_not_unpair_active_device(application):
    app, peer = application
    with pytest.raises(PairingError, match="revoked_peer_required"):
        app.remove_peer(peer, revoked_only=True)
    assert app.registry.list()[0]["state"] == "active"
    app.registry.revoke(peer)
    with pytest.raises(PairingError, match="use_remove_revoked"):
        app.remove_peer(peer)
    assert app.registry.list()[0]["state"] == "revoked"
