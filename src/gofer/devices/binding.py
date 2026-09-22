"""Exact first-record binding for the jointly specified TLS 1.3 v2 profile.

Call only with authenticated TLS plaintext. Matching a binding does not consume
an invitation or confer trust; the registry must enforce confirmation/revocation.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from typing import Any

from gofer.devices.framing import FrameError


@dataclass(frozen=True)
class SessionBinding:
    session_id: str
    mode: str
    initiator_id: str
    responder_id: str
    initiator_role: str
    invitation_sha256: str | None = field(default=None, repr=False)
    secret: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.mode not in ("pair", "resume"):
            raise FrameError("binding_mode")
        if self.initiator_role not in ("controller", "desktop"):
            raise FrameError("binding_role")
        if self.mode == "resume" and (
            self.invitation_sha256 is not None or self.secret is not None
        ):
            raise FrameError("binding_mode")
        if self.mode == "pair" and (self.invitation_sha256 is None or self.secret is None):
            raise FrameError("binding_mode")

    def record(self) -> dict[str, Any]:
        return {
            "type": "session.bind",
            "version": 2,
            "profile": "raticode-tls13-v2",
            "session_id": self.session_id,
            "mode": self.mode,
            "initiator_id": self.initiator_id,
            "responder_id": self.responder_id,
            "initiator_role": self.initiator_role,
            "responder_role": "desktop",
            "invitation_sha256": self.invitation_sha256,
            "secret": self.secret,
        }

    def verify(self, received: dict[str, Any]) -> None:
        # Canonical serialization here is solely equality checking of already
        # parsed records. Invitation hashing uses original decoded QR bytes.
        try:
            expected = json.dumps(self.record(), sort_keys=True, separators=(",", ":")).encode()
            actual = json.dumps(
                received, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        except (ValueError, TypeError, UnicodeError):
            raise FrameError("session_binding") from None
        if not hmac.compare_digest(expected, actual):
            raise FrameError("session_binding")
