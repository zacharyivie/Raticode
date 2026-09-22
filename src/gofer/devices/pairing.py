"""Memory-only invitations and bounded enrollment attempts."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import secrets
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from gofer.devices.binding import SessionBinding
from gofer.devices.framing import strict_json
from gofer.devices.registry import DeviceRegistry, PairingError, device_id


def relay_origin(value: str) -> str:
    try:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.path
            or url.query
            or url.fragment
            or url.port not in (None, 443)
            or any(c.isspace() for c in value)
        ):
            raise ValueError()
    except ValueError:
        raise PairingError("invalid_relay_origin") from None
    return value


def label(value: str) -> str:
    if not 1 <= len(value) <= 80 or any(unicodedata.category(c).startswith("C") for c in value):
        raise PairingError("invalid_display_name")
    return value


def lan_endpoint(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        if (
            not isinstance(value, dict)
            or set(value) != {"host", "port"}
            or not isinstance(value["host"], str)
            or not 1 <= len(value["host"]) <= 253
            or "%" in value["host"]
            or type(value["port"]) is not int
            or not 1 <= value["port"] <= 65535
        ):
            raise ValueError()
        address = ipaddress.ip_address(value["host"])
        if (
            not (address.is_private or address.is_loopback)
            or address.is_unspecified
            or address.is_multicast
        ):
            raise ValueError()
    except (ValueError, TypeError):
        raise PairingError("invalid_lan_endpoint") from None
    return value


@dataclass(repr=False)
class Invitation:
    raw: bytes = field(repr=False)
    expires: float
    deadline: float
    failures: int = 0
    consumed: bool = False

    def payload(self) -> dict[str, Any]:
        uri = "raticode://pair?v=2&data=" + base64.urlsafe_b64encode(self.raw).rstrip(b"=").decode()
        return {"uri": uri, "expires_at": self.expires}


class Invitations:
    def __init__(
        self, registry: DeviceRegistry, *, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self.registry = registry
        self.monotonic = monotonic
        self.lock = threading.RLock()
        self.current: Invitation | None = None

    def create(
        self, name: str, relay: str, endpoint: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        with self.lock:
            now = self.registry.clock()
            data: dict[str, Any] = {
                "version": 2,
                "profile": "raticode-tls13-v2",
                "invitation_id": str(uuid4()),
                "responder_id": self.registry.identity.device_id,
                "responder_name": label(name),
                "responder_role": "desktop",
                "responder_spki_sha256": self.registry.identity.pin.hex(),
                "secret": secrets.token_urlsafe(32),
                "expires_at": int(now) + 300,
                "relay": relay_origin(relay),
                "pair_initiator_topic": secrets.token_urlsafe(32),
                "pair_responder_topic": secrets.token_urlsafe(32),
                "lan_endpoint": lan_endpoint(endpoint),
            }
            raw = json.dumps(data, separators=(",", ":")).encode()
            self.current = Invitation(raw, data["expires_at"], self.monotonic() + 300)
            return self.current.payload()

    def cancel(self) -> None:
        with self.lock:
            self.current = None

    def claim(self, record: dict[str, Any], session_id: str, pin: bytes) -> tuple[str, str]:
        with self.lock:
            invite = self.current
            if (
                invite is None
                or invite.consumed
                or invite.failures >= 5
                or invite.expires <= self.registry.clock()
                or invite.deadline <= self.monotonic()
            ):
                raise PairingError("invitation_unavailable")
            invite.failures += 1
            data = strict_json(invite.raw)
            peer, role = device_id(record.get("initiator_id")), record.get("initiator_role")
            if not isinstance(role, str):
                raise PairingError("invalid_role")
            expected = SessionBinding(
                session_id,
                "pair",
                peer,
                self.registry.identity.device_id,
                role,
                hashlib.sha256(invite.raw).hexdigest(),
                data["secret"],
            )
            expected.verify(record)
            self.registry.propose(peer, pin, role, invite.expires)
            self.registry.save_relay(peer, data["relay"])
            invite.consumed = True
            # Discard the only retained secret once a candidate has claimed it.
            self.current = None
            return peer, role


def parse_invitation(uri: str, now: float) -> tuple[bytes, dict[str, Any]]:
    """Parse only. The caller must obtain local consent before connecting."""
    import re

    prefix = "raticode://pair?v=2&data="
    if len(uri) > 4096 or not uri.startswith(prefix):
        raise PairingError("invalid_invitation")
    encoded = uri[len(prefix) :]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
        raise PairingError("invalid_invitation")
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != encoded:
            raise ValueError()
        data = strict_json(raw, limit=3072)
        fields = {
            "version",
            "profile",
            "invitation_id",
            "responder_id",
            "responder_name",
            "responder_role",
            "responder_spki_sha256",
            "secret",
            "expires_at",
            "relay",
            "pair_initiator_topic",
            "pair_responder_topic",
            "lan_endpoint",
        }
        if (
            set(data) != fields
            or type(data["version"]) is not int
            or data["version"] != 2
            or data["profile"] != "raticode-tls13-v2"
            or data["responder_role"] != "desktop"
        ):
            raise ValueError()
        device_id(data["invitation_id"])
        device_id(data["responder_id"])
        label(data["responder_name"])
        relay_origin(data["relay"])
        lan_endpoint(data["lan_endpoint"])
        if not re.fullmatch(r"[0-9a-f]{64}", data["responder_spki_sha256"]):
            raise ValueError()
        for name in ("secret", "pair_initiator_topic", "pair_responder_topic"):
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", data[name]):
                raise ValueError()
            decoded = base64.urlsafe_b64decode(data[name] + "=")
            if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode() != data[name]:
                raise ValueError()
        if type(data["expires_at"]) is not int or not now < data["expires_at"] <= now + 300:
            raise PairingError("invitation_expired_or_clock_mismatch")
        return raw, data
    except (ValueError, TypeError, KeyError):
        raise PairingError("invalid_or_expired_invitation") from None
