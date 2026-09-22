"""Backend-owned device listener and bounded authenticated application pump.

This listener never routes requests into the loopback UI API. Application records
reach the durable handler only after local trust confirmation and ready acknowledgment.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections import OrderedDict
from typing import Any

from gofer.devices.application import DeviceApplication
from gofer.devices.binding import SessionBinding
from gofer.devices.framing import (
    MAX_CHUNK,
    MAX_FRAME,
    Frame,
    FrameError,
    OrderedFrames,
    Records,
    encode_record,
)
from gofer.devices.lan import packet, read_frame
from gofer.devices.pairing import Invitations
from gofer.devices.registry import DeviceRegistry, PairingError, device_id
from gofer.devices.storage import StorageError
from gofer.devices.tls import DeviceTLS, DeviceTLSError


class DeviceSession:
    def __init__(
        self,
        registry: DeviceRegistry,
        invitations: Invitations,
        first: Frame,
        application: DeviceApplication | None = None,
    ) -> None:
        if first.direction != "initiator_to_responder" or first.index != 0:
            raise PairingError("invalid_first_frame")
        self.registry = registry
        self.application = application
        self.sent_sequence = 0
        # A freshly paired identity must never revive an older TLS session.
        with registry.lock:
            self.generations = {
                p["device_id"]: registry.generation(p["device_id"]) for p in registry.list()
            }
        self.invitations = invitations
        self.session_id = first.session_id
        self.mode = first.mode
        self.frames = OrderedFrames(first.session_id, first.mode, first.direction)
        self.records = Records()
        self.tls = DeviceTLS(
            certificate_pem=registry.identity.certificate,
            private_key_pem=registry.identity.private_key,
            peer_pin=None,
            server=True,
            enrollment=first.mode == "pair",
            trusted_pins=registry.trusted_pins() if first.mode == "resume" else None,
        )
        self.peer: str | None = None
        self.role: str | None = None
        self.stage = "binding"
        self.deadline = time.monotonic() + 30
        self.index = 0
        self.closed = False

    def _authority(self) -> None:
        if self.peer is not None and self.role is not None and self.stage != "pending":
            self.registry.check_generation(self.peer, self.generations.get(self.peer))
            self.registry.authenticate(self.peer, self.tls.peer_pin or b"", self.role)

    def receive(self, raw: bytes) -> list[bytes]:
        with self.invitations.lock, self.registry.lock:
            return self._receive(raw)

    def _receive(self, raw: bytes) -> list[bytes]:
        if self.closed:
            raise PairingError("session_closed")
        try:
            if time.monotonic() >= self.deadline:
                raise PairingError("session_timeout")
            self._authority()
            ciphertext = self.frames.accept(raw, now=time.monotonic())
            self.tls.feed(ciphertext)
            while chunks := self.tls.receive():
                for chunk in chunks:
                    for record in self.records.feed(chunk):
                        self._record(record)
            return self.poll()
        except (ValueError, TypeError):
            self.closed = True
            raise

    def _record(self, record: dict[str, Any]) -> None:
        pin = self.tls.peer_pin
        if pin is None:
            raise PairingError("identity_missing")
        if self.stage == "binding":
            if self.mode == "pair":
                with self.registry.lock:
                    self.peer, self.role = self.invitations.claim(record, self.session_id, pin)
                    self.generations[self.peer] = self.registry.generation(self.peer)
                self.tls.send(encode_record(record))
                self.stage = "pending"
                expires = next(
                    p["expires_at"] for p in self.registry.list() if p["device_id"] == self.peer
                )
                self.deadline = time.monotonic() + max(0, expires - self.registry.clock())
                self.tls.send(
                    encode_record(
                        {
                            "type": "session.pending",
                            "device_id": self.peer,
                            "expires_at": int(expires),
                        }
                    )
                )
            else:
                self.peer = device_id(record.get("initiator_id"))
                self.role = record.get("initiator_role")
                if self.role not in ("controller", "desktop"):
                    raise PairingError("invalid_role")
                SessionBinding(
                    self.session_id,
                    "resume",
                    self.peer,
                    self.registry.identity.device_id,
                    self.role,
                ).verify(record)
                self.registry.check_generation(self.peer, self.generations.get(self.peer))
                self.registry.authenticate(self.peer, pin, self.role)
                self.tls.send(encode_record(record))
                self._ready()
        elif self.stage == "ack" and self.peer is not None and self.role is not None:
            self.registry.acknowledge(self.peer, pin, self.role, record)
            self.stage = "active"
            self.deadline = time.monotonic() + 60
        elif self.stage == "active" and self.peer is not None and self.application is not None:
            self.application.handle(self.peer, record)
            if record.get("type") == "sync.request":
                cursor = record["payload"]["after_sequence"] or 0
                self.sent_sequence = min(self.sent_sequence, cursor)
            self.deadline = time.monotonic() + 300
        else:
            raise PairingError("application_transport_not_enabled")

    def _ready(self) -> None:
        assert self.peer is not None and self.role is not None and self.tls.peer_pin is not None
        self.tls.send(encode_record(self.registry.ready(self.peer, self.tls.peer_pin, self.role)))
        self.stage = "ack"
        self.deadline = time.monotonic() + 30

    def poll(self) -> list[bytes]:
        with self.registry.lock:
            return self._poll()

    def _poll(self) -> list[bytes]:
        if self.closed:
            raise PairingError("session_closed")
        try:
            self.frames.check_deadline(time.monotonic())
            if time.monotonic() >= self.deadline:
                raise PairingError("session_timeout")
            if self.stage == "pending":
                assert self.peer is not None
                self.registry.check_generation(self.peer, self.generations.get(self.peer))
                peer = next((p for p in self.registry.list() if p["device_id"] == self.peer), None)
                if peer is None or peer["state"] in ("expired", "revoked"):
                    raise PairingError("enrollment_unavailable")
                if peer["state"] == "confirmed":
                    self._ready()
            self._authority()
            if self.stage == "active" and self.peer is not None and self.application is not None:
                for response in self.application.pending(self.peer, self.sent_sequence, 1):
                    self.tls.send(encode_record(response))
                    self.sent_sequence = response["sequence"]
            result = []
            ciphertext = self.tls.drain()
            for offset in range(0, len(ciphertext), MAX_CHUNK):
                result.append(
                    Frame(
                        self.session_id,
                        self.mode,
                        "responder_to_initiator",
                        self.index,
                        ciphertext[offset : offset + MAX_CHUNK],
                    ).encode()
                )
                self.index += 1
            return result
        except ValueError:
            self.closed = True
            raise


class DeviceListener:
    def __init__(
        self,
        registry: DeviceRegistry,
        invitations: Invitations,
        application: DeviceApplication | None = None,
    ) -> None:
        self.application = application
        self.registry = registry
        self.invitations = invitations
        self.server: asyncio.Server | None = None
        self.connections: dict[asyncio.StreamWriter, asyncio.Task[None]] = {}
        self.attempts: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self.global_tokens = 32.0
        self.global_updated = time.monotonic()

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        if self.server is not None:
            raise PairingError("listener_already_started")
        self.server = await asyncio.start_server(self._connection, host, port, limit=MAX_FRAME + 4)
        assert self.server.sockets
        return int(self.server.sockets[0].getsockname()[1])

    def _allow(self, host: str) -> bool:
        now = time.monotonic()
        self.global_tokens = min(32.0, self.global_tokens + (now - self.global_updated) / 2)
        self.global_updated = now
        tokens, updated = self.attempts.pop(host, (5.0, now))
        tokens = min(5.0, tokens + (now - updated) / 10)
        allowed = self.global_tokens >= 1 and tokens >= 1 and len(self.connections) < 16
        self.attempts[host] = (tokens - 1 if allowed else tokens, now)
        while len(self.attempts) > 256:
            self.attempts.popitem(last=False)
        if allowed:
            self.global_tokens -= 1
        return allowed

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        address = writer.get_extra_info("peername")
        if not address or not self._allow(str(address[0])):
            writer.close()
            return
        task = asyncio.current_task()
        assert task is not None
        self.connections[writer] = task
        session: DeviceSession | None = None
        pending_read: asyncio.Task[bytes] | None = None
        try:
            # Keep a single read task across polling ticks. Cancelling readexactly
            # on each tick could consume a partial prefix and desynchronize framing.
            pending_read = asyncio.create_task(read_frame(reader))
            first = await asyncio.wait_for(pending_read, timeout=10)
            session = DeviceSession(
                self.registry, self.invitations, Frame.decode(first), self.application
            )
            output = session.receive(first)
            while True:
                for frame in output:
                    writer.write(packet(frame))
                await asyncio.wait_for(writer.drain(), timeout=5)
                if pending_read is None or pending_read.done():
                    pending_read = asyncio.create_task(read_frame(reader))
                done, _ = await asyncio.wait({pending_read}, timeout=0.2)
                if done:
                    output = session.receive(pending_read.result())
                else:
                    output = session.poll()
        except (
            PairingError,
            FrameError,
            DeviceTLSError,
            StorageError,
            sqlite3.Error,
            OSError,
            asyncio.IncompleteReadError,
            TimeoutError,
            ValueError,
            TypeError,
        ):
            # Never log peer input, invitation material, TLS exceptions or pins.
            pass
        finally:
            if session is not None:
                session.closed = True
            if pending_read is not None:
                pending_read.cancel()
                await asyncio.gather(pending_read, return_exceptions=True)
            self.connections.pop(writer, None)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        tasks = list(self.connections.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
