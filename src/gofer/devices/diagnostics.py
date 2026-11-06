"""Readable, privacy-safe descriptions of paired-device connections for the app log.

These lines are meant for a person reading Settings > Developer > Application log
while a phone will not connect. They name the route, the stage and a fixed reason.
They never include peer input, pairing secrets, pins, fingerprints, TLS details,
message content or the phone's network address; an address is only described by
its kind, such as "Tailscale" or "local network".
"""

from __future__ import annotations

import asyncio
import errno
import ipaddress
import sqlite3
import time
from typing import Any

from gofer.devices.framing import FrameError
from gofer.devices.registry import PairingError
from gofer.devices.storage import StorageError
from gofer.devices.tailnet import is_tailnet_address
from gofer.devices.tls import DeviceTLSError

# Fixed refusal codes that come with a plain explanation. Anything else is shown as
# its code, which is a fixed identifier chosen by the desktop, never peer input.
_REASONS = {
    "session_timeout": "no traffic for too long",
    "session_closed": "the session was already closed",
    "peer_unavailable": "this device is no longer paired",
    "peer_revoked": "this device was removed from Paired devices",
    "trust_generation_changed": "this device was paired again",
    "enrollment_unavailable": "the pairing request expired or was removed",
    "invalid_first_frame": "the first message was not a Raticode connection",
    "outbox_quota": "the desktop's queue for this device is full",
    "inbox_quota": "the desktop's record of this device's requests is full",
    "identity_missing": "the device did not present its identity",
    "invalid_role": "the device asked for an unsupported role",
    "application_transport_not_enabled": "a request arrived before sign-in finished",
}
# Endings that are normal for a phone: it slept, lost the network or closed the app.
_ROUTINE = frozenset(
    {
        "closed_by_peer",
        "connection_reset",
        "session_timeout",
        "session_closed",
        "replaced",
        "listener_closed",
        "idle_timeout",
    }
)
# DeviceTLSError messages are fixed, sanitized codes.
_TLS_REASONS = {
    "tls_authentication_failed": (
        "the encrypted handshake failed; the device may still hold an old pairing with "
        "this desktop, or reached a different computer"
    ),
    "unsupported_tls_version": "the device does not support TLS 1.3",
    "unsupported_application_protocol": "the device is not a compatible Raticode app",
    "peer_identity_missing": "the device did not present its identity",
    "ciphertext_limit": "the device sent too much data before it was read",
    "session_closed": "the encrypted session had already failed",
}
_STAGES = {
    "binding": "checking the device's identity",
    "pending": "waiting for pairing to be confirmed on this computer",
    "ack": "finishing sign-in",
    "active": "signed in",
}


def route_label(host: str | None) -> str:
    """How a connection reached this desktop, from the desktop's own listening address."""
    if not host:
        return "an unknown route"
    if is_tailnet_address(host):
        return "Tailscale"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "an unknown route"
    if address.is_loopback:
        return "this computer"
    return "the local network"


def address_kind(host: str | None) -> str:
    """Describe a remote address without revealing it."""
    if not host:
        return "unknown"
    if is_tailnet_address(host):
        return "Tailscale"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "unknown"
    if address.is_loopback:
        return "this computer"
    if address.is_link_local:
        return "link-local"
    if address.is_private:
        return "local network"
    return "public internet"


def stage_label(stage: str | None) -> str:
    return _STAGES.get(stage or "", "connecting")


def duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds} s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {seconds} s" if seconds else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    return f"{hours // 24} days"


def device_label(registry: Any, peer: str | None, role: str | None) -> str:
    """The user's name for a paired device, or a short form of its public ID."""
    kind = "Paired desktop" if role == "desktop" else "Phone"
    if not peer:
        return kind
    name = None
    try:
        with registry.lock:
            row = registry.db.execute(
                "SELECT name FROM peer_names WHERE device_id=?", (peer,)
            ).fetchone()
        name = row[0] if row else None
    except (sqlite3.Error, AttributeError):
        name = None
    if isinstance(name, str) and name.strip():
        cleaned = " ".join(name.split())[:60]
        return f'{kind} "{cleaned}"'
    return f"{kind} {peer[:8]}"


def ending(exc: BaseException | None, *, replaced: bool = False) -> tuple[str, str]:
    """A fixed code and a plain reason for why a connection ended."""
    if replaced:
        return "replaced", "replaced by a newer connection from the same device"
    if exc is None:
        return "closed_by_peer", "the device closed the connection"
    if isinstance(exc, asyncio.CancelledError):
        return "listener_closed", "the desktop stopped listening on this address"
    if isinstance(exc, asyncio.IncompleteReadError):
        return "closed_by_peer", "the device closed the connection"
    if isinstance(exc, TimeoutError):
        return "idle_timeout", "the device stopped responding"
    if isinstance(exc, PairingError):
        code = str(exc) or "refused"
        return code, _REASONS.get(code, f"refused ({code})")
    if isinstance(exc, DeviceTLSError):
        code = str(exc) or "tls_failed"
        return code, _TLS_REASONS.get(code, f"the encrypted connection failed ({code})")
    if isinstance(exc, FrameError):
        return "bad_frame", f"the device sent data the desktop could not read ({exc})"
    if isinstance(exc, StorageError):
        return "storage", "the desktop's protected device storage failed"
    if isinstance(exc, sqlite3.Error):
        return "database", f"the desktop's device database failed ({type(exc).__name__})"
    if isinstance(exc, ConnectionResetError | BrokenPipeError):
        return "connection_reset", "the network connection was reset"
    if isinstance(exc, OSError):
        name = errno.errorcode.get(exc.errno or 0, type(exc).__name__)
        return "network", f"network error {name}"
    return "error", f"unexpected {type(exc).__name__}"


def is_routine(code: str) -> bool:
    return code in _ROUTINE


class Throttle:
    """Let one message per key through each interval and count the rest.

    A phone stuck on one problem reconnects every few seconds. The first message says
    what is wrong; the next one says how many times it happened since.
    """

    def __init__(self, interval: float, *, limit: int = 256) -> None:
        self.interval = interval
        self.limit = limit
        self.last: dict[Any, float] = {}
        self.suppressed: dict[Any, int] = {}

    def check(self, key: Any, now: float | None = None) -> tuple[bool, int]:
        now = time.monotonic() if now is None else now
        last = self.last.get(key)
        if last is not None and now - last < self.interval:
            self.suppressed[key] = self.suppressed.get(key, 0) + 1
            return False, 0
        self.last.pop(key, None)
        self.last[key] = now
        while len(self.last) > self.limit:
            oldest = next(iter(self.last))
            self.last.pop(oldest)
            self.suppressed.pop(oldest, None)
        return True, self.suppressed.pop(key, 0)


def repeated(count: int) -> str:
    if not count:
        return ""
    return f" ({count} similar {'event' if count == 1 else 'events'} since the last report)"
