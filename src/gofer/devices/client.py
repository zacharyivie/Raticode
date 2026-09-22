"""Desktop initiator for explicit QR-consented local enrollment."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from gofer.devices.binding import SessionBinding
from gofer.devices.framing import MAX_CHUNK, Frame, OrderedFrames, Records, encode_record
from gofer.devices.lan import packet, read_frame
from gofer.devices.pairing import parse_invitation
from gofer.devices.registry import DeviceRegistry, PairingError, device_id
from gofer.devices.tls import DeviceTLS


async def pair_desktop(registry: DeviceRegistry, uri: str) -> str:
    raw, invite = parse_invitation(uri, registry.clock())
    endpoint = invite["lan_endpoint"]
    if endpoint is None:
        raise PairingError("lan_endpoint_required")
    peer = device_id(invite["responder_id"])
    pin = bytes.fromhex(invite["responder_spki_sha256"])
    # User explicitly consented to this QR identity before calling.
    registry.propose(peer, pin, "desktop", invite["expires_at"], outbound=True)
    registry.save_endpoint(peer, endpoint)
    registry.save_relay(peer, invite["relay"], outbound=True)
    return await _connect(registry, peer, pin, endpoint, "pair", raw, invite)


async def reconnect_desktop(registry: DeviceRegistry, peer: str, endpoint: dict[str, Any]) -> str:
    from gofer.devices.pairing import lan_endpoint

    checked = lan_endpoint(endpoint)
    assert checked is not None
    return await _connect(registry, peer, registry.reconnect_pin(peer), checked, "resume")


async def _connect(
    registry: DeviceRegistry,
    peer: str,
    pin: bytes,
    endpoint: dict[str, Any],
    mode: str,
    raw: bytes | None = None,
    invite: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
    sink: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    generation = registry.generation(peer)
    tls = DeviceTLS(
        certificate_pem=registry.identity.certificate,
        private_key_pem=registry.identity.private_key,
        peer_pin=pin,
        server=False,
    )
    session = str(uuid4())
    frames = OrderedFrames(session, mode, "responder_to_initiator")
    records = Records()
    index = 0
    bound = False
    verified_binding = False
    ready_received = False
    binding: dict[str, Any] | None = None
    deadline = time.monotonic() + (
        min(300, invite["expires_at"] - registry.clock()) if invite else 30
    )
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(endpoint["host"], endpoint["port"]), 5
    )
    pending_read: asyncio.Task[bytes] | None = None
    try:
        tls.feed(b"")
        while True:
            registry.check_generation(peer, generation)
            if tls.handshake_complete and not bound:
                binding = SessionBinding(
                    session,
                    mode,
                    registry.identity.device_id,
                    peer,
                    "desktop",
                    hashlib.sha256(raw).hexdigest() if raw is not None else None,
                    invite["secret"] if invite else None,
                ).record()
                tls.send(encode_record(binding))
                bound = True
            outgoing = tls.drain()
            for offset in range(0, len(outgoing), MAX_CHUNK):
                frame = Frame(
                    session,
                    mode,
                    "initiator_to_responder",
                    index,
                    outgoing[offset : offset + MAX_CHUNK],
                ).encode()
                writer.write(packet(frame))
                index += 1
            await asyncio.wait_for(writer.drain(), 5)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PairingError("enrollment_expired")
            pending_read = asyncio.create_task(read_frame(reader))
            while not pending_read.done():
                if time.monotonic() >= deadline:
                    raise PairingError("enrollment_expired")
                frames.check_deadline(time.monotonic())
                # Recheck revocation while waiting for human confirmation or a flight.
                registry.check_generation(peer, generation)
                registry.reconnect_pin(peer)
                await asyncio.wait({pending_read}, timeout=min(0.2, remaining))
            registry.check_generation(peer, generation)
            incoming = pending_read.result()
            tls.feed(frames.accept(incoming, now=time.monotonic()))
            while chunks := tls.receive():
                for chunk in chunks:
                    for record in records.feed(chunk):
                        if not verified_binding:
                            if record != binding:
                                raise PairingError("responder_binding_mismatch")
                            verified_binding = True
                            continue
                        if record.get("type") == "session.pending":
                            if (
                                set(record) != {"type", "device_id", "expires_at"}
                                or record["device_id"] != registry.identity.device_id
                                or type(record["expires_at"]) is not int
                            ):
                                raise PairingError("invalid_pending")
                            continue
                        if ready_received:
                            if sink is not None:
                                with registry.lock:
                                    registry.check_generation(peer, generation)
                                    sink(record)
                            if record.get("request_id") == (request or {}).get("request_id") and (
                                record.get("type") in ("error", "fleet.snapshot")
                                or record.get("type") == "request.status"
                                and record.get("payload", {}).get("status")
                                in ("completed", "failed", "cancelled", "outcome_unknown")
                                or record.get("type") == "job.status"
                                and record.get("payload", {}).get("state")
                                in (
                                    "completed",
                                    "failed",
                                    "cancelled",
                                    "outcome_unknown",
                                    "rejected",
                                )
                            ):
                                return peer
                            continue
                        with registry.lock:
                            registry.check_generation(peer, generation)
                            registry.recover_remote(peer, pin, record)
                        ack: dict[str, Any] = {
                            "type": "session.ready_ack",
                            "device_id": registry.identity.device_id,
                            "trust_revision": record["trust_revision"],
                        }
                        tls.send(encode_record(ack))
                        ready_received = True
                        if request is not None:
                            tls.send(encode_record(request))
                            deadline = time.monotonic() + 300
                        outgoing = tls.drain()
                        for offset in range(0, len(outgoing), MAX_CHUNK):
                            frame = Frame(
                                session,
                                mode,
                                "initiator_to_responder",
                                index,
                                outgoing[offset : offset + MAX_CHUNK],
                            ).encode()
                            writer.write(packet(frame))
                            index += 1
                        await asyncio.wait_for(writer.drain(), 5)
                        if request is None:
                            return peer
    finally:
        if pending_read is not None:
            pending_read.cancel()
            await asyncio.gather(pending_read, return_exceptions=True)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def exchange(
    registry: DeviceRegistry,
    peer: str,
    request: dict[str, Any],
    sink: Callable[[dict[str, Any]], None],
) -> str:
    """A fresh pinned LAN session; durable retries reuse the exact request."""
    endpoint = registry.endpoint(peer)
    return await _connect(
        registry, peer, registry.reconnect_pin(peer), endpoint, "resume", request=request, sink=sink
    )
