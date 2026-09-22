"""Disposable identities/loopback peers only. No OS user keys, relay or providers."""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import hashlib
import json
import secrets
from typing import Any
from uuid import uuid4

import pytest

from gofer.devices.binding import SessionBinding
from gofer.devices.client import pair_desktop
from gofer.devices.control import DeviceControl
from gofer.devices.framing import (
    MAX_CHUNK,
    Frame,
    FrameError,
    OrderedFrames,
    Records,
    encode_record,
)
from gofer.devices.pairing import Invitations, parse_invitation
from gofer.devices.registry import DeviceRegistry, PairingError
from gofer.devices.service import DeviceListener, DeviceSession
from gofer.devices.storage import Identity, StorageError
from gofer.devices.tls import DeviceTLS, DeviceTLSError


class TestSecrets:
    __test__ = False

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, account: str) -> str | None:
        return self.values.get(account)

    def put(self, account: str, value: str) -> None:
        self.values[account] = value


@pytest.fixture
def registry(tmp_path):
    value = DeviceRegistry(tmp_path / "registry", TestSecrets(), initialize=True)
    yield value
    value.close()


def invitation(
    registry: DeviceRegistry, invitations: Invitations | None = None
) -> tuple[Invitations, bytes, dict[str, Any]]:
    invitations = invitations or Invitations(registry)
    payload = invitations.create("Test desktop", "https://ntfy.sh")
    raw, value = parse_invitation(payload["uri"], registry.clock())
    return invitations, raw, value


def binding(
    registry: DeviceRegistry,
    raw: bytes,
    value: dict[str, Any],
    peer: str | None = None,
    role: str = "controller",
    session: str | None = None,
) -> SessionBinding:
    return SessionBinding(
        session or str(uuid4()),
        "pair",
        peer or str(uuid4()),
        registry.identity.device_id,
        role,
        hashlib.sha256(raw).hexdigest(),
        value["secret"],
    )


def test_identity_survives_restart_and_missing_key_never_replaced(tmp_path):
    store = TestSecrets()
    first = DeviceRegistry(tmp_path, store, initialize=True)
    identity = first.identity
    first.close()
    second = DeviceRegistry(tmp_path, store)
    assert second.identity == identity
    second.close()
    store.values.clear()
    with pytest.raises(StorageError, match="identity_missing"):
        DeviceRegistry(tmp_path, store, initialize=True)
    assert store.values == {}


def test_failed_first_credential_write_can_be_retried(tmp_path):
    class Locked(TestSecrets):
        def put(self, account: str, value: str) -> None:
            raise StorageError("locked")

    with pytest.raises(StorageError):
        DeviceRegistry(tmp_path, Locked(), initialize=True)
    recovered = DeviceRegistry(tmp_path, TestSecrets(), initialize=True)
    recovered.close()


def test_changed_or_corrupt_identity_fails_closed(tmp_path):
    store = TestSecrets()
    first = DeviceRegistry(tmp_path, store, initialize=True)
    first.close()
    key = next(iter(store.values))
    store.values[key] = Identity.create().encode()
    with pytest.raises(StorageError, match="mismatch"):
        DeviceRegistry(tmp_path, store)
    store.values[key] = "broken"
    with pytest.raises(StorageError, match="invalid"):
        DeviceRegistry(tmp_path, store)


def test_invitation_is_single_use_even_when_two_threads_race(registry):
    invites, raw, value = invitation(registry)
    one = binding(registry, raw, value)
    two = binding(registry, raw, value)

    def claim(b):
        try:
            invites.claim(b.record(), b.session_id, secrets.token_bytes(32))
            return True
        except PairingError:
            return False

    with concurrent.futures.ThreadPoolExecutor() as executor:
        assert sorted(executor.map(claim, (one, two))) == [False, True]
    assert len(registry.list()) == 1
    assert registry.list()[0]["state"] == "pending"
    assert not registry.trusted_pins()


@pytest.mark.parametrize(
    "mutation",
    ["secret", "invitation_sha256", "responder_id", "session_id", "initiator_role", "extra"],
)
def test_invalid_binding_never_establishes_trust(registry, mutation):
    invites, raw, value = invitation(registry)
    b = binding(registry, raw, value)
    record = b.record()
    record[mutation] = "wrong"
    with pytest.raises((FrameError, PairingError)):
        invites.claim(record, b.session_id, secrets.token_bytes(32))
    assert registry.list() == []


def test_expiry_monotonic_clock_rollback_cancel_and_restart(registry):
    ticks = [10.0]
    invites = Invitations(registry, monotonic=lambda: ticks[0])
    _, raw, value = invitation(registry, invites)
    b = binding(registry, raw, value)
    ticks[0] += 300
    with pytest.raises(PairingError, match="unavailable"):
        invites.claim(b.record(), b.session_id, secrets.token_bytes(32))
    _, raw, value = invitation(registry, invites)
    b = binding(registry, raw, value)
    invites.cancel()
    with pytest.raises(PairingError):
        invites.claim(b.record(), b.session_id, secrets.token_bytes(32))
    with pytest.raises(PairingError):
        Invitations(registry).claim(b.record(), b.session_id, secrets.token_bytes(32))


def test_five_failed_secrets_exhaust_invitation(registry):
    invites, raw, value = invitation(registry)
    b = binding(registry, raw, value)
    record = b.record() | {"secret": "wrong"}
    for _ in range(5):
        with pytest.raises(FrameError):
            invites.claim(record, b.session_id, secrets.token_bytes(32))
    with pytest.raises(PairingError, match="unavailable"):
        invites.claim(b.record(), b.session_id, secrets.token_bytes(32))


def test_confirmation_ack_restart_routes_protected_and_revocation(tmp_path):
    store = TestSecrets()
    registry = DeviceRegistry(tmp_path, store, initialize=True)
    peer, pin = str(uuid4()), secrets.token_bytes(32)
    registry.propose(peer, pin, "controller", registry.clock() + 300)
    with pytest.raises(PairingError):
        registry.ready(peer, pin, "controller")
    with pytest.raises(PairingError, match="identity_changed"):
        registry.confirm(peer, "0" * 64)
    registry.confirm(peer, pin.hex())
    ready = registry.ready(peer, pin, "controller")
    assert "worker.execute" not in ready["capabilities"]
    assert "remote_approval" not in ready["capabilities"]
    assert registry.list()[0]["last_seen"] is None
    registry.close()
    for path in tmp_path.glob("devices.sqlite3*"):
        assert ready["initiator_topic"].encode() not in path.read_bytes()
        assert b"PRIVATE KEY" not in path.read_bytes()
    registry = DeviceRegistry(tmp_path, store)
    assert registry.ready(peer, pin, "controller") == ready
    with pytest.raises(PairingError):
        registry.authenticate(peer, secrets.token_bytes(32), "controller")
    with pytest.raises(PairingError):
        registry.authenticate(peer, pin, "desktop")
    with pytest.raises(PairingError):
        registry.acknowledge(
            peer,
            pin,
            "controller",
            {"type": "session.ready_ack", "device_id": peer, "trust_revision": True},
        )
    ack = {"type": "session.ready_ack", "device_id": peer, "trust_revision": 1}
    registry.acknowledge(peer, pin, "controller", ack)
    assert registry.list()[0]["state"] == "active"
    registry.revoke(peer)
    assert not registry.trusted_pins()
    with pytest.raises(PairingError):
        registry.acknowledge(peer, pin, "controller", ack)
    with pytest.raises(PairingError):
        registry.propose(peer, pin, "controller", registry.clock() + 300)
    registry.close()
    registry = DeviceRegistry(tmp_path, store)
    assert registry.list()[0]["state"] == "revoked"
    registry.close()


class Client:
    def __init__(
        self,
        registry: DeviceRegistry,
        role: str = "controller",
        identity: Identity | None = None,
        mode: str = "pair",
    ) -> None:
        self.identity = identity or Identity.create()
        self.session_id = str(uuid4())
        self.mode = mode
        self.role = role
        self.index = 0
        self.records = Records()
        self.frames = OrderedFrames(self.session_id, mode, "responder_to_initiator")
        self.tls = DeviceTLS(
            certificate_pem=self.identity.certificate,
            private_key_pem=self.identity.private_key,
            peer_pin=registry.identity.pin,
            server=False,
        )
        self.tls.feed(b"")

    def drain(self) -> list[bytes]:
        raw = self.tls.drain()
        result = []
        for offset in range(0, len(raw), MAX_CHUNK):
            result.append(
                Frame(
                    self.session_id,
                    self.mode,
                    "initiator_to_responder",
                    self.index,
                    raw[offset : offset + MAX_CHUNK],
                ).encode()
            )
            self.index += 1
        return result

    def receive(self, frames: list[bytes]) -> list[dict[str, Any]]:
        result = []
        for frame in frames:
            self.tls.feed(self.frames.accept(frame, now=0))
            while chunks := self.tls.receive():
                for chunk in chunks:
                    result.extend(self.records.feed(chunk))
        return result

    def start(self, registry: DeviceRegistry, invites: Invitations) -> DeviceSession:
        frames = self.drain()
        server = DeviceSession(registry, invites, Frame.decode(frames[0]))
        for _ in range(20):
            for frame in frames:
                self.receive(server.receive(frame))
            frames = self.drain()
            if server.tls.handshake_complete and self.tls.handshake_complete and not frames:
                return server
        pytest.fail("handshake did not complete")

    def send(self, server: DeviceSession, record: dict[str, Any]) -> list[dict[str, Any]]:
        self.tls.send(encode_record(record))
        result = []
        for frame in self.drain():
            result.extend(self.receive(server.receive(frame)))
        return result


def test_real_tls_pending_confirm_resume_after_lost_ready_then_revoke(registry):
    invites, raw, value = invitation(registry)
    client = Client(registry)
    server = client.start(registry, invites)
    b = binding(registry, raw, value, client.identity.device_id, session=client.session_id)
    reply = client.send(server, b.record())
    assert reply[0] == b.record()
    assert reply[1]["type"] == "session.pending"
    assert server.stage == "pending"
    registry.confirm(client.identity.device_id, client.identity.pin.hex())
    # Drop the ready flight and all old session state, like process death.
    server.poll()
    client = Client(registry, identity=client.identity, mode="resume")
    server = client.start(registry, Invitations(registry))
    b = SessionBinding(
        client.session_id,
        "resume",
        client.identity.device_id,
        registry.identity.device_id,
        "controller",
    )
    reply = client.send(server, b.record())
    assert reply[0] == b.record()
    ready = reply[1]
    assert ready["type"] == "session.ready"
    client.send(
        server,
        {
            "type": "session.ready_ack",
            "device_id": client.identity.device_id,
            "trust_revision": ready["trust_revision"],
        },
    )
    assert server.stage == "active"
    registry.revoke(client.identity.device_id)
    with pytest.raises(PairingError, match="unavailable"):
        server.poll()
    with pytest.raises(DeviceTLSError):
        Client(registry, identity=client.identity, mode="resume").start(registry, invites)


def test_controller_cannot_inject_jobs_before_confirmation(registry):
    invites, raw, value = invitation(registry)
    client = Client(registry)
    server = client.start(registry, invites)
    b = binding(registry, raw, value, client.identity.device_id, session=client.session_id)
    client.send(server, b.record())
    with pytest.raises(PairingError, match="application_transport_not_enabled"):
        client.send(server, {"type": "job.submit", "command": "must never run"})
    assert server.closed
    assert registry.list()[0]["state"] == "pending"


def test_resume_claim_cannot_use_another_peers_id(registry):
    identity = Identity.create()
    registry.propose(identity.device_id, identity.pin, "controller", registry.clock() + 300)
    registry.confirm(identity.device_id, identity.pin.hex())
    client = Client(registry, identity=identity, mode="resume")
    server = client.start(registry, Invitations(registry))
    forged = SessionBinding(
        client.session_id, "resume", str(uuid4()), registry.identity.device_id, "controller"
    )
    with pytest.raises(PairingError):
        client.send(server, forged.record())


@pytest.mark.asyncio
async def test_two_desktops_pair_over_actual_bounded_loopback_stream(tmp_path):
    a = DeviceRegistry(tmp_path / "a", TestSecrets(), initialize=True)
    b = DeviceRegistry(tmp_path / "b", TestSecrets(), initialize=True)
    invites = Invitations(a)
    listener = DeviceListener(a, invites)
    port = await listener.start()
    payload = invites.create("Desktop A", "https://ntfy.sh", {"host": "127.0.0.1", "port": port})
    task = asyncio.create_task(pair_desktop(b, payload["uri"]))
    try:
        async with asyncio.timeout(5):
            while not a.list():
                await asyncio.sleep(0.01)
            assert a.list()[0]["state"] == "pending"
            a.confirm(b.identity.device_id, b.identity.pin.hex())
            assert await task == a.identity.device_id
            while a.list()[0]["state"] != "active":
                await asyncio.sleep(0.01)
        assert b.list()[0]["state"] == "active"
        assert a.list()[0]["role"] == b.list()[0]["role"] == "desktop"
        assert all("worker.execute" not in peer["capabilities"] for peer in a.list() + b.list())
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await listener.close()
        a.close()
        b.close()


@pytest.mark.asyncio
async def test_listener_rejects_unbounded_input_and_rate_limits(registry):
    listener = DeviceListener(registry, Invitations(registry))
    port = await listener.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write((3073).to_bytes(4, "big"))
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await writer.wait_closed()
        assert all(listener._allow("test") for _ in range(5))
        assert not listener._allow("test")
        for n in range(300):
            listener._allow(str(n))
        assert len(listener.attempts) <= 256
    finally:
        await listener.close()


def test_local_control_gate_and_no_secrets_in_status(tmp_path):
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        control.action({"action": "enable"})
        with pytest.raises(PairingError, match="experimental_network_opt_in_required"):
            control.action({"action": "invite"})
        assert control.invitations.current is None
        assert not control.status()["relay_enabled"]
        assert not control.status()["lan_available"]
        # Local probes bypass the user-facing phone invitation API deliberately.
        payload = control.invitations.create(
            "Local test", "https://ntfy.sh", {"host": "127.0.0.1", "port": control.port}
        )
        raw, data = parse_invitation(payload["uri"], control.registry.clock())
        assert data["secret"] not in json.dumps(control.status())
        assert not control.status()["network_release"]
        data["lan_endpoint"]["host"] = "192.168.1.10"
        uri = (
            "raticode://pair?v=2&data="
            + base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
        )
        with pytest.raises(PairingError, match="opt_in_required"):
            control.action({"action": "preview", "uri": uri})
        assert control.registry.list() == []
    finally:
        control.close()


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "192.168.1.20"])
def test_phone_invitation_advertises_only_reachable_routes(tmp_path, monkeypatch, host):
    from gofer.devices.relay_service import RelayService

    async def idle_relay(self):
        await asyncio.Event().wait()

    real_start = DeviceListener.start

    async def local_listener(self, requested_host, port):
        assert requested_host == host
        return await real_start(self, "127.0.0.1", port)

    monkeypatch.setenv("RATICODE_DEVICE_EXPERIMENTAL_NETWORK", "1")
    monkeypatch.setenv("RATICODE_DEVICE_HOST", host)
    monkeypatch.setattr(RelayService, "run", idle_relay)
    monkeypatch.setattr(DeviceListener, "start", local_listener)
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        control.action({"action": "enable"})
        payload = control.action({"action": "invite"})
        _, data = parse_invitation(payload["uri"], control.registry.clock())
        assert data["lan_endpoint"] == (
            {"host": host, "port": control.port} if host == "192.168.1.20" else None
        )
        status = control.status()
        assert status["relay_enabled"]
        assert status["lan_available"] == (host == "192.168.1.20")
        assert data["secret"] not in json.dumps(status)
        assert "data:image/svg+xml" in payload["qr"]
    finally:
        control.close()


def test_local_ui_routes_require_authentication(tmp_path):
    from tests.unit.test_ui_server import _request

    assert _request(tmp_path, "GET", "/api/devices", authenticated=False).status == 401
    assert (
        _request(
            tmp_path, "POST", "/api/devices", body={"action": "enable"}, authenticated=False
        ).status
        == 401
    )


def test_expired_confirmation_and_corrupt_routes_fail_closed(registry):
    peer, pin = str(uuid4()), secrets.token_bytes(32)
    registry.propose(peer, pin, "controller", registry.clock() - 1)
    with pytest.raises(PairingError, match="expired"):
        registry.confirm(peer, pin.hex())
    peer = str(uuid4())
    registry.propose(peer, pin, "controller", registry.clock() + 300)
    registry.confirm(peer, pin.hex())
    registry.db.execute("UPDATE peers SET routes=? WHERE device_id=?", (b"x" * 50, peer))
    with pytest.raises(StorageError, match="routes_invalid"):
        registry.ready(peer, pin, "controller")


def test_pending_enrollment_cannot_resume_and_role_cannot_gain_worker(registry):
    identity = Identity.create()
    registry.propose(identity.device_id, identity.pin, "controller", registry.clock() + 300)
    with pytest.raises(DeviceTLSError):
        Client(registry, identity=identity, mode="resume").start(registry, Invitations(registry))
    registry.confirm(identity.device_id, identity.pin.hex())
    client = Client(registry, identity=identity, mode="resume")
    server = client.start(registry, Invitations(registry))
    forged = SessionBinding(
        client.session_id, "resume", identity.device_id, registry.identity.device_id, "desktop"
    )
    with pytest.raises(PairingError):
        client.send(server, forged.record())


def test_transaction_rollback_does_not_leave_partial_confirmation(registry):
    peer, pin = str(uuid4()), secrets.token_bytes(32)
    registry.propose(peer, pin, "desktop", registry.clock() + 300)
    with pytest.raises(RuntimeError):
        with registry.transaction():
            registry.db.execute("UPDATE peers SET state='active' WHERE device_id=?", (peer,))
            raise RuntimeError("simulated crash before commit")
    assert registry.list()[0]["state"] == "pending"
    assert registry.trusted_pins() == frozenset()


@pytest.mark.asyncio
async def test_two_desktop_reconnect_survives_registry_restart(tmp_path):
    from gofer.devices.client import reconnect_desktop

    sa, sb = TestSecrets(), TestSecrets()
    a = DeviceRegistry(tmp_path / "a", sa, initialize=True)
    b = DeviceRegistry(tmp_path / "b", sb, initialize=True)
    invites = Invitations(a)
    listener = DeviceListener(a, invites)
    port = await listener.start()
    endpoint = {"host": "127.0.0.1", "port": port}
    task = asyncio.create_task(
        pair_desktop(b, invites.create("A", "https://ntfy.sh", endpoint)["uri"])
    )
    try:
        async with asyncio.timeout(5):
            while not a.list():
                await asyncio.sleep(0.01)
            a.confirm(b.identity.device_id, b.identity.pin.hex())
            await task
        b.close()
        b = DeviceRegistry(tmp_path / "b", sb)
        assert b.endpoint(a.identity.device_id) == endpoint
        assert (
            await reconnect_desktop(b, a.identity.device_id, b.endpoint(a.identity.device_id))
            == a.identity.device_id
        )
        a.revoke(b.identity.device_id)
        with pytest.raises((DeviceTLSError, asyncio.IncompleteReadError)):
            await reconnect_desktop(b, a.identity.device_id, endpoint)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await listener.close()
        a.close()
        b.close()


def test_protected_store_never_uses_configured_plaintext_backend(monkeypatch):
    import keyring
    from keyring.backends import SecretService

    from gofer.devices.storage import OSSecretStore

    class FakeProtected:
        priority = 1

        def get_password(self, service, account):
            return None

        def set_password(self, service, account, value):
            raise RuntimeError("locked with sensitive detail")

    monkeypatch.setattr(keyring, "get_keyring", lambda: pytest.fail("configured backend accessed"))
    monkeypatch.setattr(SecretService, "Keyring", FakeProtected)
    monkeypatch.setattr("gofer.devices.storage.sys.platform", "linux")
    store = OSSecretStore()
    assert store.get("test") is None
    with pytest.raises(StorageError, match="^protected_store_write_failed$"):
        store.put("test", "test-only")


def test_real_process_death_preserves_confirmed_enrollment(tmp_path):
    import subprocess
    import sys

    store = TestSecrets()
    registry = DeviceRegistry(tmp_path, store, initialize=True)
    peer, pin = str(uuid4()), secrets.token_bytes(32)
    registry.propose(peer, pin, "controller", registry.clock() + 300)
    identity = next(iter(store.values.values()))
    registry.close()
    # Disposable fixture secret travels through stdin, never argv, logs or a file.
    code = """
import json, os, sys
from pathlib import Path
from gofer.devices.registry import DeviceRegistry
class Store:
    def get(self, account): return payload['identity']
    def put(self, account, value): raise AssertionError('replacement')
payload=json.load(sys.stdin)
r=DeviceRegistry(Path(payload['path']), Store())
r.confirm(payload['peer'], payload['pin'])
os._exit(17)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps(
            {"path": str(tmp_path), "identity": identity, "peer": peer, "pin": pin.hex()}
        ),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 17
    assert result.stdout == result.stderr == ""
    registry = DeviceRegistry(tmp_path, store)
    assert registry.ready(peer, pin, "controller")["trust_revision"] == 1
    assert registry.list()[0]["state"] == "confirmed"
    registry.close()


def test_experimental_network_requires_explicit_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("RATICODE_DEVICE_HOST", "192.168.1.10")
    monkeypatch.delenv("RATICODE_DEVICE_EXPERIMENTAL_NETWORK", raising=False)
    disabled = DeviceControl(tmp_path, store=TestSecrets())
    try:
        assert disabled.status()["error"]
        with pytest.raises(PairingError, match="configuration_invalid"):
            disabled.action({"action": "enable"})
        assert disabled.listener is None
    finally:
        disabled.close()
    monkeypatch.setenv("RATICODE_DEVICE_EXPERIMENTAL_NETWORK", "1")
    # Constructing does not bind or create real credentials; explicit setup required.
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        assert control.experimental_network
        assert control.status()["network_release"] is False
    finally:
        control.close()


def test_listener_port_and_identity_survive_service_restart(tmp_path):
    store = TestSecrets()
    first = DeviceControl(tmp_path, store=store)
    first.action({"action": "enable"})
    port, pin = first.port, first.status()["fingerprint"]
    first.close()
    second = DeviceControl(tmp_path, store=store)
    try:
        assert second.port == port
        assert second.status()["fingerprint"] == pin
    finally:
        second.close()


@pytest.mark.asyncio
async def test_lan_split_prefix_coalesced_frames_and_truncation(registry):
    invites, raw, data = invitation(registry)
    listener = DeviceListener(registry, invites)
    port = await listener.start()
    client = Client(registry)
    reader, writer = await asyncio.open_connection("127.0.0.1", port)

    async def transmit(frames: list[bytes]) -> None:
        data = b"".join(len(frame).to_bytes(4, "big") + frame for frame in frames)
        for byte in data[:4]:
            writer.write(bytes([byte]))
            await writer.drain()
            await asyncio.sleep(0.001)
        writer.write(data[4:])
        await writer.drain()

    async def receive() -> list[dict[str, Any]]:
        length = int.from_bytes(await reader.readexactly(4), "big")
        return client.receive([await reader.readexactly(length)])

    try:
        async with asyncio.timeout(5):
            await transmit(client.drain())
            while not client.tls.handshake_complete:
                await receive()
            await transmit(client.drain())
            b = binding(registry, raw, data, client.identity.device_id, session=client.session_id)
            client.tls.send(encode_record(b.record()))
            await transmit(client.drain())
            records: list[dict[str, Any]] = []
            while not records:
                records.extend(await receive())
            assert records[0] == b.record()
            while len(records) < 2:
                records.extend(await receive())
            assert records[1]["type"] == "session.pending"
            writer.write((20).to_bytes(4, "big") + b"truncated")
            await writer.drain()
            writer.write_eof()
            assert await reader.read() == b""
    finally:
        writer.close()
        await writer.wait_closed()
        await listener.close()


def test_late_input_cannot_extend_session_deadline(registry):
    invites, raw, data = invitation(registry)
    client = Client(registry)
    session = client.start(registry, invites)
    session.deadline = 0
    b = binding(registry, raw, data, client.identity.device_id, session=client.session_id)
    with pytest.raises(PairingError, match="session_timeout"):
        client.send(session, b.record())
    assert registry.list() == []


@pytest.mark.asyncio
async def test_shared_lan_wire_fixtures():
    from pathlib import Path

    from gofer.devices.lan import read_frame

    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/device_pairing/lan.json").read_text()
    )
    reader = asyncio.StreamReader()
    wire = bytes.fromhex(fixture["coalesced_hex"])
    offset = 0
    for end in fixture["split_offsets"] + [len(wire)]:
        reader.feed_data(wire[offset:end])
        offset = end
    reader.feed_eof()
    assert await read_frame(reader) == fixture["frame_utf8"].encode()
    assert await read_frame(reader) == fixture["frame_utf8"].encode()
    for bad in fixture["negative_prefix_hex"].values():
        reader = asyncio.StreamReader()
        reader.feed_data(bytes.fromhex(bad))
        with pytest.raises(FrameError, match="size_limit"):
            await read_frame(reader)
    reader = asyncio.StreamReader()
    reader.feed_data(bytes.fromhex(fixture["truncated_hex"]))
    reader.feed_eof()
    with pytest.raises(asyncio.IncompleteReadError):
        await read_frame(reader)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(read_frame(asyncio.StreamReader()), 0.01)


def test_corrupt_registry_disables_service_without_replacing_keys(tmp_path):
    store = TestSecrets()
    control = DeviceControl(tmp_path, store=store)
    control.action({"action": "enable"})
    control.close()
    keys = dict(store.values)
    (tmp_path / "devices/devices.sqlite3").write_bytes(b"corrupt registry")
    recovered = DeviceControl(tmp_path, store=store)
    try:
        assert recovered.status()["enabled"] is False
        assert recovered.status()["error"]
        assert store.values == keys
    finally:
        recovered.close()


@pytest.mark.parametrize("role", ["controller", "desktop"])
def test_enrollment_persists_empty_capabilities_on_resume(tmp_path, role):
    store = TestSecrets()
    registry = DeviceRegistry(tmp_path, store, initialize=True)
    peer, pin = str(uuid4()), secrets.token_bytes(32)
    registry.propose(peer, pin, role, registry.clock() + 300)
    registry.confirm(peer, pin.hex())
    assert registry.ready(peer, pin, role)["capabilities"] == []
    registry.close()
    registry = DeviceRegistry(tmp_path, store)
    try:
        assert registry.ready(peer, pin, role)["capabilities"] == []
        assert registry.list()[0]["capabilities"] == []
    finally:
        registry.close()


@pytest.mark.parametrize(
    "caps",
    [["chat"], ["files"], ["fleet.read"], ["worker.execute"], ["remote_approval"], [["chat"]]],
)
def test_enrollment_rejects_unsupported_remote_grants(registry, caps):
    peer, pin = str(uuid4()), secrets.token_bytes(32)
    registry.propose(peer, pin, "desktop", registry.clock() + 300, outbound=True)
    record = {
        "type": "session.ready",
        "device_id": registry.identity.device_id,
        "role": "desktop",
        "capabilities": caps,
        "trust_revision": 1,
        "initiator_topic": "a" * 43,
        "responder_topic": "b" * 43,
    }
    with pytest.raises(PairingError, match="invalid_remote_ready"):
        registry.accept_remote(peer, pin, record)
    assert registry.list()[0]["state"] == "outbound_pending"
    assert registry.list()[0]["capabilities"] == []


@pytest.mark.parametrize(
    "host,opt_in",
    [
        ("not-an-ip", "1"),
        ("0.0.0.0", "1"),
        ("::", "1"),
        ("8.8.8.8", "1"),
        ("224.0.0.1", "1"),
        ("192.168.1.10", "0"),
    ],
)
def test_invalid_device_host_keeps_authenticated_ui_available(tmp_path, monkeypatch, host, opt_in):
    import http.client
    import threading

    from gofer.ui.server import GoferUiServer

    monkeypatch.setenv("RATICODE_DEVICE_HOST", host)
    monkeypatch.setenv("RATICODE_DEVICE_EXPERIMENTAL_NETWORK", opt_in)
    monkeypatch.setattr(DeviceListener, "start", lambda *a: pytest.fail("device bind attempted"))
    server = GoferUiServer(("127.0.0.1", 0), tmp_path, api_token="local-test")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    try:
        assert server.devices.listener is None
        assert server.devices.registry is None
        conn.request("GET", "/api/devices")
        response = conn.getresponse()
        assert response.status == 401
        response.read()
        for path in ("/api/health", "/api/devices"):
            conn.request("GET", path, headers={"Authorization": "Bearer local-test"})
            response = conn.getresponse()
            assert response.status == 200
            body = json.loads(response.read())
            if path == "/api/devices":
                assert body["enabled"] is False
                assert body["error"].startswith("Device listener disabled.")
                assert host not in body["error"]
            else:
                assert body == {"ok": True}
        with pytest.raises(PairingError, match="configuration_invalid"):
            server.devices.action({"action": "enable"})
        assert not (tmp_path / "devices" / "devices.sqlite3").exists()
    finally:
        conn.close()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize(
    "address,expected",
    [("192.168.1.20", "192.168.1.20"), ("8.8.8.8", "127.0.0.1"), ("0.0.0.0", "127.0.0.1")],
)
def test_default_lan_host_requires_opt_in_and_private_route(monkeypatch, address, expected):
    from gofer.devices import control as module

    calls = []

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def connect(self, target):
            calls.append(target)

        def getsockname(self):
            return address, 0

    monkeypatch.setattr(module.socket, "socket", lambda *_args: Probe())
    assert module.default_device_host(False) == "127.0.0.1"
    assert calls == []
    assert module.default_device_host(True) == expected
    assert calls == [("192.0.2.1", 9)]


@pytest.mark.parametrize("stage", ["binding", "pending", "active"])
def test_old_tls_session_cannot_survive_unpair_and_same_identity_reenrollment(registry, stage):
    from gofer.devices.application import DeviceApplication

    app = DeviceApplication(registry)
    invites, raw, value = invitation(registry)
    client = Client(registry)
    peer, pin = client.identity.device_id, client.identity.pin
    server = client.start(registry, invites)
    b = binding(registry, raw, value, peer, session=client.session_id)
    client.send(server, b.record())
    if stage != "pending":
        registry.confirm(peer, pin.hex())
        ready = client.receive(server.poll())[0]
        client.send(
            server,
            {
                "type": "session.ready_ack",
                "device_id": peer,
                "trust_revision": ready["trust_revision"],
            },
        )
    if stage == "binding":
        client = Client(registry, identity=client.identity, mode="resume")
        server = client.start(registry, invites)
    app.remove_peer(peer)
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
    with pytest.raises(PairingError, match="pairing_changed"):
        if stage == "binding":
            client.send(
                server,
                SessionBinding(
                    client.session_id,
                    "resume",
                    peer,
                    registry.identity.device_id,
                    "controller",
                ).record(),
            )
        else:
            server.poll()


@pytest.mark.parametrize("action", ["unpair", "remove_revoked"])
def test_control_removes_device_without_restoring_trust(tmp_path, action):
    control = DeviceControl(tmp_path, store=TestSecrets())
    try:
        control.action({"action": "enable"})
        registry = control.registry
        assert registry is not None
        peer, pin = str(uuid4()), secrets.token_bytes(32)
        registry.propose(peer, pin, "controller", registry.clock() + 300)
        registry.confirm(peer, pin.hex())
        if action == "remove_revoked":
            control.action({"action": "revoke", "device_id": peer})
        result = control.action({"action": action, "device_id": peer})
        assert result["peers"] == []
        assert not registry.trusted_pins()
    finally:
        control.close()


def test_legacy_registry_gains_stable_generation_without_changing_trust(tmp_path):
    store = TestSecrets()
    registry = DeviceRegistry(tmp_path, store, initialize=True)
    peer, pin = str(uuid4()), secrets.token_bytes(32)
    registry.propose(peer, pin, "controller", registry.clock() + 300)
    registry.confirm(peer, pin.hex())
    before = registry.ready(peer, pin, "controller")
    registry.db.execute("ALTER TABLE peers DROP COLUMN generation")
    registry.close()
    registry = DeviceRegistry(tmp_path, store)
    generation = registry.generation(peer)
    assert generation
    assert registry.ready(peer, pin, "controller") == before
    registry.close()
    registry = DeviceRegistry(tmp_path, store)
    assert registry.generation(peer) == generation
    assert registry.ready(peer, pin, "controller") == before
    registry.close()
