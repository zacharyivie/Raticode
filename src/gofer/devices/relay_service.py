"""ntfy carrier for the same pinned TLS sessions used on LAN.

The relay never sees decrypted application records or device names. Sessions
are disposable; durable application IDs survive relay cache loss and reconnect.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from gofer.devices.application import DeviceApplication
from gofer.devices.framing import Frame
from gofer.devices.pairing import Invitations
from gofer.devices.registry import DeviceRegistry
from gofer.devices.relay import NtfyRelay, RelayError
from gofer.devices.service import DeviceSession


class RelayService:
    def __init__(
        self, registry: DeviceRegistry, invitations: Invitations, application: DeviceApplication
    ) -> None:
        self.registry = registry
        self.invitations = invitations
        self.application = application
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.closed = False
        self.errors: dict[tuple[str, str], str] = {}

    @property
    def error(self) -> str | None:
        errors = tuple(self.errors.values())
        if "relay_rate_limited" in errors:
            return "relay_rate_limited"
        return next(iter(errors), None)

    def _succeeded(self, topic: str, operation: str) -> None:
        # A successful read cannot prove a previously rate-limited send recovered.
        self.errors.pop((topic, operation), None)

    async def run(self) -> None:
        try:
            while not self.closed:
                channels = [c for c in self.registry.relay_channels() if not c["outbound"]]
                with self.invitations.lock:
                    invitation = self.invitations.current
                    if invitation is not None and invitation.deadline > time.monotonic():
                        value = json.loads(invitation.raw)
                        channels.append(
                            {
                                "origin": value["relay"],
                                "receive": value["pair_responder_topic"],
                                "send": value["pair_initiator_topic"],
                                "peer": None,
                            }
                        )
                for channel in channels:
                    if channel["receive"] not in self.tasks:
                        self.tasks[channel["receive"]] = asyncio.create_task(self._channel(channel))
                for topic, task in list(self.tasks.items()):
                    # Keep claimed invitation channel alive through confirmation;
                    # the channel itself has a bounded enrollment/session deadline.
                    if task.done():
                        self.tasks.pop(topic)
                await asyncio.sleep(1)
        finally:
            for task in self.tasks.values():
                task.cancel()
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    def _backoff(
        self, error: RelayError, sessions: dict[str, DeviceSession], topic: str, operation: str
    ) -> float:
        self.errors[topic, operation] = error.code
        # A failed publish may have sent only part of a TLS flight. Never continue
        # that session with a missing frame, or retry its TLS bytes on a new stream.
        # Application records remain durable and can replay on a fresh session.
        for session in sessions.values():
            session.closed = True
        sessions.clear()
        return time.monotonic() + max(5, error.retry_after or 30)

    async def _channel(self, channel: dict[str, Any]) -> None:
        relay = NtfyRelay(channel["origin"])
        since = str(int(time.time()) - 30)
        sessions: dict[str, DeviceSession] = {}
        deadline = time.monotonic() + 600 if channel["peer"] is None else float("inf")
        next_poll = 0.0
        cooldown = 0.0
        try:
            generation = self.registry.generation(channel["peer"]) if channel["peer"] else None
            while not self.closed and time.monotonic() < deadline:
                if channel["peer"] is not None:
                    self.registry.check_generation(channel["peer"], generation)
                    self.registry.reconnect_pin(channel["peer"])
                if time.monotonic() < cooldown:
                    await asyncio.sleep(min(0.2, cooldown - time.monotonic()))
                    continue
                if time.monotonic() >= next_poll:
                    try:
                        messages = await relay.poll(channel["receive"], since=since)
                        if channel["peer"] is not None:
                            self.registry.check_generation(channel["peer"], generation)
                        self._succeeded(channel["receive"], "poll")
                        next_poll = time.monotonic() + 5
                        for message in messages:
                            since = message.id
                            session_id: str | None = None
                            try:
                                frame = Frame.decode(message.frame)
                                session_id = frame.session_id
                                session = sessions.get(session_id)
                                if session is None:
                                    if len(sessions) >= 4:
                                        continue
                                    session = DeviceSession(
                                        self.registry, self.invitations, frame, self.application
                                    )
                                    sessions[session_id] = session
                                output = session.receive(message.frame)
                                if (
                                    channel["peer"] is not None
                                    and session.peer is not None
                                    and session.peer != channel["peer"]
                                ):
                                    session.closed = True
                                    sessions.pop(session_id)
                                    continue
                                for outgoing in output:
                                    await relay.publish(channel["send"], outgoing)
                                    self._succeeded(channel["receive"], "publish")
                            except RelayError as exc:
                                cooldown = self._backoff(
                                    exc, sessions, channel["receive"], "publish"
                                )
                                next_poll = cooldown
                                break
                            except (ValueError, OSError):
                                if session_id is not None:
                                    failed = sessions.pop(session_id, None)
                                    if failed is not None:
                                        failed.closed = True
                    except RelayError as exc:
                        cooldown = self._backoff(exc, sessions, channel["receive"], "poll")
                        next_poll = cooldown
                if time.monotonic() < cooldown:
                    continue
                for key, session in list(sessions.items()):
                    try:
                        for outgoing in session.poll():
                            await relay.publish(channel["send"], outgoing)
                            self._succeeded(channel["receive"], "publish")
                    except RelayError as exc:
                        cooldown = self._backoff(exc, sessions, channel["receive"], "publish")
                        next_poll = cooldown
                        break
                    except (ValueError, OSError):
                        session.closed = True
                        sessions.pop(key)
                await asyncio.sleep(0.2)
        except (ValueError, OSError, RelayError):
            return
        finally:
            self.errors.pop((channel["receive"], "poll"), None)
            self.errors.pop((channel["receive"], "publish"), None)
            for session in sessions.values():
                session.closed = True
