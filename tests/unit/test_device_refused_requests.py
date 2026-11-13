"""A phone request the desktop refuses must be answered, never retried forever.

The phone keeps each request queued until an event with its request ID arrives,
and resends the whole queue on every connection. A refusal that only closed the
session therefore blocked that phone on every reconnect until it was reinstalled.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from typing import Any
from uuid import uuid4

import pytest

from gofer.devices.application import DeviceApplication
from gofer.devices.registry import DeviceRegistry, PairingError
from tests.unit.test_device_application import TestSecrets, event, grant


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
    yield DeviceApplication(registry), peer
    registry.close()


def offered_file(app: DeviceApplication, peer: str, thread: str, lifetime: int = 300) -> str:
    """A complete file the desktop offers to the phone, expiring after ``lifetime``."""
    file_id, data = str(uuid4()), b"screenshot bytes"
    with app.registry.transaction():
        app.files.offer(
            peer,
            thread,
            {
                "file_id": file_id,
                "name": "screenshot.png",
                "mime": "image/png",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "expires_at": int(app.registry.clock()) + lifetime,
            },
        )
        app.files.receive(
            peer,
            thread,
            {
                "file_id": file_id,
                "offset": 0,
                "data": base64.urlsafe_b64encode(data).rstrip(b"=").decode(),
                "eof": True,
            },
        )
    return file_id


def errors_for(events: list[dict[str, Any]], request: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == "error" and e["request_id"] == request["request_id"]]


def test_receipt_for_a_download_that_expired_overnight_is_answered(application, monkeypatch):
    app, peer = application
    started = event("file.request", payload={"file_id": str(uuid4())})
    grant(app, peer, started)
    thread = started["thread_id"]
    file_id = offered_file(app, peer, thread)
    started["payload"]["file_id"] = file_id
    assert app.handle(peer, started)[0]["type"] == "file.chunk"

    # The phone slept mid-download. Its queued receipt outlived the desktop's offer.
    later = app.registry.clock() + 8 * 3600
    monkeypatch.setattr(app.registry, "clock", lambda: later)
    receipt = event(
        "file.status",
        sequence=1,
        thread_id=thread,
        payload={"file_id": file_id, "state": "receiving", "received_size": 4},
    )
    answered = app.handle(peer, receipt)
    [refusal] = errors_for(answered, receipt)
    assert refusal["thread_id"] == thread
    assert refusal["payload"]["code"] == "not_found"
    assert refusal["payload"]["retryable"] is False
    assert "Ask Rem to send it again" in refusal["payload"]["detail"]

    # Resending the same bytes on the next connection neither fails nor re-answers.
    resent = app.handle(peer, receipt)
    assert errors_for(resent, receipt) == [refusal]
    assert len(errors_for(app.pending(peer), receipt)) == 1

    # The connection keeps working: the phone's next sync is accepted and answered.
    sync = event(
        "sync.request", sequence=2, thread_id=None, payload={"after_sequence": None, "limit": 100}
    )
    assert any(e["type"] == "sync.snapshot" for e in app.handle(peer, sync))


def test_request_for_an_unknown_file_is_answered(application):
    app, peer = application
    request = event("file.request", payload={"file_id": str(uuid4())})
    grant(app, peer, request)
    [refusal] = errors_for(app.handle(peer, request), request)
    assert refusal["payload"]["code"] == "not_found"


def test_file_request_without_thread_access_is_answered(application):
    app, peer = application
    request = event("file.request", payload={"file_id": str(uuid4())})
    [refusal] = errors_for(app.handle(peer, request), request)
    assert refusal["payload"]["code"] == "unauthorized"


def test_reused_sequence_is_answered_without_running(application):
    """A phone whose saved state rolled back (power loss) reuses request numbers."""
    app, peer = application
    first = event()
    grant(app, peer, first)
    app.handle(peer, first)
    assert app.claim() is not None
    reused = event(thread_id=first["thread_id"])
    [refusal] = errors_for(app.handle(peer, reused), reused)
    assert refusal["payload"]["code"] == "conflict"
    assert app.claim() is None
    # Nothing was recorded under the reused number, and the original keeps it.
    assert (
        app.registry.db.execute(
            "SELECT id FROM device_inbox WHERE peer=? AND sequence=0", (peer,)
        ).fetchone()[0]
        == first["id"]
    )
    later = event(sequence=1, thread_id=first["thread_id"])
    assert not errors_for(app.handle(peer, later), later)


def test_changed_retry_is_answered_without_running(application):
    app, peer = application
    request = event()
    grant(app, peer, request)
    app.handle(peer, request)
    changed = request | {"payload": {"text": "changed", "attachment_ids": []}}
    [refusal] = errors_for(app.handle(peer, changed), changed)
    assert refusal["payload"]["code"] == "conflict"
    work = app.claim()
    assert work is not None and work["event"]["payload"]["text"] == "private message"
    assert app.claim() is None


def test_refusals_are_logged_by_code_only(application, caplog):
    app, peer = application
    request = event("file.request", payload={"file_id": str(uuid4())})
    grant(app, peer, request)
    with caplog.at_level(logging.WARNING, logger="gofer.devices.application"):
        app.handle(peer, request)
    [line] = [r.getMessage() for r in caplog.records]
    assert "file.request" in line and "file_unavailable" in line
    assert request["payload"]["file_id"] not in line and peer not in line


def test_lost_trust_still_ends_the_session(application):
    app, peer = application
    request = event("file.request", payload={"file_id": str(uuid4())})
    grant(app, peer, request)
    app.registry.revoke(peer)
    with pytest.raises(PairingError, match="peer_unavailable"):
        app.handle(peer, request)


def test_full_outbox_still_ends_the_session(application, monkeypatch):
    """No answer fits; the phone's next acknowledging sync frees room (see
    test_full_outbox_recovers_on_authenticated_sync_without_losing_dedup)."""
    import gofer.devices.application as module

    app, peer = application
    monkeypatch.setattr(module, "OUTBOX_EVENTS", 0)
    request = event("file.request", payload={"file_id": str(uuid4())})
    grant(app, peer, request)
    with pytest.raises(PairingError, match="outbox_quota"):
        app.handle(peer, request)


async def test_refused_request_no_longer_blocks_the_real_session(tmp_path):
    """Over real pinned TLS, the refused request is answered and the next request works."""
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
    invite = invitations.create("Test", {"host": "127.0.0.1", "port": port}, role="desktop")

    async def confirm() -> None:
        while not target.list():
            await asyncio.sleep(0.01)
        candidate = target.list()[0]
        target.confirm(candidate["device_id"], candidate["fingerprint"])

    confirmation = asyncio.create_task(confirm())
    try:
        await pair_desktop(source, invite["uri"])
        await confirmation
        peer = source.identity.device_id

        async def wait_active() -> None:
            while target._row(peer)["state"] != "active":
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_active(), timeout=5)
        stuck = event("file.request", payload={"file_id": str(uuid4())})
        grant(application, peer, stuck)
        for _attempt in range(2):  # The same queued bytes, resent on a new connection.
            received: list[dict[str, Any]] = []
            await asyncio.wait_for(
                exchange(source, target.identity.device_id, stuck, received.append), 5
            )
            assert errors_for(received, stuck)
        follow_up = event(thread_id=stuck["thread_id"], sequence=1)
        accepted: list[dict[str, Any]] = []
        await asyncio.wait_for(
            exchange(
                source,
                target.identity.device_id,
                follow_up,
                accepted.append,
                done=lambda record: record["type"] == "chat.accepted",
            ),
            5,
        )
        assert any(e["type"] == "chat.accepted" for e in accepted)
    finally:
        await listener.close()
        source.close()
        target.close()


def test_session_end_reason_is_logged_rarely_and_only_for_refusals(tmp_path, caplog):
    from gofer.devices.pairing import Invitations
    from gofer.devices.service import DeviceListener

    registry = DeviceRegistry(tmp_path, TestSecrets(), initialize=True)
    try:
        listener = DeviceListener(registry, Invitations(registry), DeviceApplication(registry))
        with caplog.at_level(logging.WARNING, logger="gofer.devices.service"):
            for reason in ("outbox_quota", "outbox_quota", "session_timeout", "peer_unavailable"):
                listener._note_refusal(reason)
        assert [r.getMessage() for r in caplog.records] == [
            "Paired device session ended: outbox_quota",
            "Paired device session ended: peer_unavailable",
        ]
    finally:
        registry.close()


def test_request_this_desktop_does_not_support_is_answered(application):
    """A newer phone's request used to close the session on an older desktop forever."""
    app, peer = application
    newer = event("thread.manage", payload={"action": "a_future_action"})
    [refusal] = errors_for(app.handle(peer, newer), newer)
    assert refusal["payload"]["code"] == "unsupported"
    assert "Update Raticode on your computer" in refusal["payload"]["detail"]
    # Nothing was recorded for it, so a resend is answered again rather than ignored.
    assert errors_for(app.handle(peer, newer), newer)
    assert app.registry.db.execute("SELECT COUNT(*) FROM device_inbox").fetchone()[0] == 0


def test_request_too_malformed_to_answer_still_ends_the_session(application):
    app, peer = application
    with pytest.raises(PairingError, match="invalid_application_event"):
        app.handle(peer, event(request_id="not-a-request-id", payload={"bogus": True}))
