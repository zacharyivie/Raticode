"""Backend-owned device listener and bounded authenticated application pump.

This listener never routes requests into the loopback UI API. Application records
reach the durable handler only after local trust confirmation and ready acknowledgment.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections import OrderedDict
from typing import Any

from gofer.devices.application import DeviceApplication
from gofer.devices.binding import SessionBinding
from gofer.devices.diagnostics import (
    Throttle,
    address_kind,
    device_label,
    duration,
    ending,
    is_routine,
    repeated,
    route_label,
    stage_label,
)
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

logger = logging.getLogger(__name__)
# A phone stuck on one refusal reconnects every few seconds; name each reason rarely.
REFUSAL_LOG_INTERVAL = 600.0
# A phone that cannot sign in retries every 2-30 s. Say why once, then count repeats.
FAILED_SIGN_IN_LOG_INTERVAL = 300.0
TURNED_AWAY_LOG_INTERVAL = 60.0


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
        # Set when a newer connection from the same phone replaces this one.
        self.replaced = False

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
                # The cursor is what the peer already holds. Resume there: rewind over
                # events still in flight, and skip ahead of earlier sessions' events
                # instead of replaying the whole retained outbox at one per tick.
                self.sent_sequence = record["payload"]["after_sequence"] or 0
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
        # Optional extra addresses on the same port, such as a Tailscale address.
        self.extra: dict[str, asyncio.Server] = {}
        self.connections: dict[asyncio.StreamWriter, asyncio.Task[None]] = {}
        self.sessions: dict[asyncio.StreamWriter, DeviceSession] = {}
        self.attempts: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self.global_tokens = 32.0
        self.global_updated = time.monotonic()
        self.refusals_logged: dict[str, float] = {}
        self.failed_sign_ins = Throttle(FAILED_SIGN_IN_LOG_INTERVAL)
        self.turned_away = Throttle(TURNED_AWAY_LOG_INTERVAL)
        # When each device's last signed-in connection ended, to describe reconnects.
        self.last_ended: OrderedDict[str, tuple[float, str]] = OrderedDict()

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        if self.server is not None:
            raise PairingError("listener_already_started")
        self.server = await asyncio.start_server(self._connection, host, port, limit=MAX_FRAME + 4)
        assert self.server.sockets
        return int(self.server.sockets[0].getsockname()[1])

    async def add_host(self, host: str, port: int) -> None:
        """Also accept sessions on ``host``. Limits and authentication are shared."""
        if self.server is None:
            raise PairingError("listener_not_started")
        if host not in self.extra:
            self.extra[host] = await asyncio.start_server(
                self._connection, host, port, limit=MAX_FRAME + 4
            )

    async def remove_host(self, host: str) -> None:
        """Stop accepting on ``host`` and end the sessions that arrived through it."""
        server = self.extra.pop(host, None)
        if server is None:
            return
        server.close()
        await server.wait_closed()
        tasks = [
            task
            for writer, task in list(self.connections.items())
            if (writer.get_extra_info("sockname") or ("",))[0] == host
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

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
        local = writer.get_extra_info("sockname")
        route = route_label(str(local[0]) if local else None)
        if not address or not self._allow(str(address[0])):
            self._note_turned_away(route, str(address[0]) if address else None)
            writer.close()
            return
        task = asyncio.current_task()
        assert task is not None
        self.connections[writer] = task
        session: DeviceSession | None = None
        pending_read: asyncio.Task[bytes] | None = None
        started = time.monotonic()
        signed_in: float | None = None
        noted_pending = False
        failure: BaseException | None = None
        try:
            # Keep a single read task across polling ticks. Cancelling readexactly
            # on each tick could consume a partial prefix and desynchronize framing.
            pending_read = asyncio.create_task(read_frame(reader))
            first = await asyncio.wait_for(pending_read, timeout=10)
            session = DeviceSession(
                self.registry, self.invitations, Frame.decode(first), self.application
            )
            self.sessions[writer] = session
            output = session.receive(first)
            superseded = False
            while True:
                if not superseded and session.stage in ("ack", "active"):
                    superseded = True
                    self._supersede(writer, session)
                if signed_in is None and session.stage == "active":
                    signed_in = time.monotonic()
                    self._note_signed_in(session, route, signed_in - started)
                elif not noted_pending and session.stage == "pending":
                    noted_pending = True
                    logger.info(
                        "A new device started pairing over %s; confirm it in Paired devices "
                        "on this computer",
                        route,
                    )
                # One write per batch. A phone that drops mid-reply makes asyncio warn
                # "socket.send() raised exception." for every later write, and a reply
                # is often dozens of frames. drain() still raises, ending the session.
                if output:
                    writer.write(b"".join(packet(frame) for frame in output))
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
        ) as exc:
            # Never log peer input, invitation material, TLS exceptions or pins. Only
            # fixed codes and the kind of failure are named (see diagnostics.py).
            failure = exc
        except asyncio.CancelledError as exc:
            failure = exc
            raise
        finally:
            if session is not None:
                session.closed = True
            if pending_read is not None:
                pending_read.cancel()
                await asyncio.gather(pending_read, return_exceptions=True)
            self.connections.pop(writer, None)
            self.sessions.pop(writer, None)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            try:
                self._note_ended(session, route, started, signed_in, failure)
            except Exception:  # noqa: BLE001 - diagnostics must never break the listener
                logger.debug("Could not describe a closed device connection", exc_info=True)

    def _note_turned_away(self, route: str, host: str | None) -> None:
        allowed, skipped = self.turned_away.check(route)
        if allowed:
            logger.warning(
                "Turned away a connection over %s from a %s address: too many connection "
                "attempts or %d connections already open%s",
                route,
                address_kind(host),
                len(self.connections),
                repeated(skipped),
            )

    def _note_signed_in(self, session: DeviceSession, route: str, elapsed: float) -> None:
        label = device_label(self.registry, session.peer, session.role)
        how = "resumed" if session.mode == "resume" else "newly paired"
        gap = ""
        if session.peer is not None and session.peer in self.last_ended:
            ended, code = self.last_ended[session.peer]
            gap = f"; back {duration(time.monotonic() - ended)} after the last connection ended"
            if not is_routine(code):
                gap += f" ({code})"
        logger.info(
            "%s connected over %s (%s, signed in after %d ms)%s",
            label,
            route,
            how,
            int(elapsed * 1000),
            gap,
        )

    def _note_ended(
        self,
        session: DeviceSession | None,
        route: str,
        started: float,
        signed_in: float | None,
        failure: BaseException | None,
    ) -> None:
        now = time.monotonic()
        if session is None:
            # Nothing usable arrived: a port probe, a stalled network or another program.
            if isinstance(failure, asyncio.CancelledError):
                return
            code, reason = ending(failure)
            if code == "idle_timeout":
                reason = "it sent nothing within 10 s"
            allowed, skipped = self.failed_sign_ins.check(("unknown", route, code))
            if allowed:
                logger.debug(
                    "A connection over %s closed before identifying itself: %s%s",
                    route,
                    reason,
                    repeated(skipped),
                )
            return
        code, reason = ending(failure, replaced=session.replaced)
        label = device_label(self.registry, session.peer, session.role)
        if signed_in is not None and session.peer is not None:
            self.last_ended.pop(session.peer, None)
            self.last_ended[session.peer] = (now, code)
            while len(self.last_ended) > 64:
                self.last_ended.popitem(last=False)
            if is_routine(code):
                logger.info(
                    "%s disconnected from %s after %s: %s",
                    label,
                    route,
                    duration(now - signed_in),
                    reason,
                )
            else:
                self._note_refusal(code, label=label, route=route, reason_text=reason)
            return
        if session.stage == "pending" and code in ("closed_by_peer", "connection_reset"):
            logger.info(
                "%s stopped waiting for pairing confirmation over %s after %s",
                label if session.peer else "A new device",
                route,
                duration(now - started),
            )
            return
        allowed, skipped = self.failed_sign_ins.check((session.peer, route, code, session.stage))
        if allowed:
            logger.warning(
                "%s could not sign in over %s while %s: %s%s",
                label if session.peer else "A device",
                route,
                stage_label(session.stage),
                reason,
                repeated(skipped),
            )

    def _supersede(self, writer: asyncio.StreamWriter, session: DeviceSession) -> None:
        """End a phone's older sessions once a newer one has signed in.

        A phone keeps one connection. When its network changes, sleeps or Tailscale
        moves, the old socket can die without a close and would otherwise hold one
        of the 16 connection slots for up to five minutes. Only the same paired
        phone, proven by its pinned identity, can replace its own session. Desktops
        run several exchanges at once, so their sessions are left alone. Nothing is
        lost: the desktop's outbox is durable and resumes from the phone's cursor.
        """
        if session.role != "controller" or session.peer is None:
            return
        for other_writer, other in list(self.sessions.items()):
            if other_writer is writer or other.peer != session.peer or other.role != "controller":
                continue
            task = self.connections.get(other_writer)
            if task is not None:
                other.replaced = True
                task.cancel()

    def _note_refusal(
        self,
        reason: str,
        *,
        label: str = "Paired device",
        route: str = "",
        reason_text: str = "",
    ) -> None:
        """Explain a signed-in session the desktop ended, at most once per code per interval."""
        if reason in ("session_timeout", "session_closed"):
            return  # A phone that slept or closed the app; nothing to explain.
        now = time.monotonic()
        if now - self.refusals_logged.get(reason, -REFUSAL_LOG_INTERVAL) < REFUSAL_LOG_INTERVAL:
            return
        self.refusals_logged[reason] = now
        explained = reason_text
        where = f" over {route}" if route else ""
        if explained and explained != reason:
            logger.warning("%s session ended%s: %s (%s)", label, where, explained, reason)
        else:
            logger.warning("%s session ended%s: %s", label, where, reason)

    async def close(self) -> None:
        for host in list(self.extra):
            await self.remove_host(host)
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        tasks = list(self.connections.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
