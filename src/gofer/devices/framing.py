"""Bounded v2 carriers and record framing. Only TLS authenticates ciphertext."""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from dataclasses import dataclass
from typing import Any

MAX_FRAME = 3072
MAX_CHUNK = 2048
MAX_REORDER = 32
MAX_REORDER_BYTES = 65536
GAP_SECONDS = 30.0
MAX_RECORD = 65536
MAX_COLLECTION = 1024
MAX_INTEGER = 2**53 - 1
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_DIRECTIONS = {"initiator_to_responder", "responder_to_initiator"}


class FrameError(ValueError):
    """Discard the carrier session; never skip a missing TLS byte range."""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FrameError("duplicate_json_key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise FrameError("nonfinite_json_number")


def strict_json(data: bytes, *, limit: int = MAX_RECORD) -> dict[str, Any]:
    if not data or len(data) > limit:
        raise FrameError("json_size_limit")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise FrameError("invalid_json") from None
    if not isinstance(value, dict):
        raise FrameError("json_object_required")
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > 16:
            raise FrameError("json_depth_limit")
        if isinstance(item, dict):
            if len(item) > MAX_COLLECTION:
                raise FrameError("json_collection_limit")
            stack.extend((key, depth + 1) for key in item)
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            if len(item) > MAX_COLLECTION:
                raise FrameError("json_collection_limit")
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError:
                raise FrameError("invalid_unicode") from None
        elif isinstance(item, float) and not math.isfinite(item):
            raise FrameError("nonfinite_json_number")
    return value


@dataclass(frozen=True)
class Frame:
    session_id: str
    mode: str
    direction: str
    index: int
    body: bytes

    def encode(self) -> bytes:
        data = json.dumps(
            {
                "version": 2,
                "session_id": self.session_id,
                "mode": self.mode,
                "direction": self.direction,
                "index": self.index,
                "body": base64.urlsafe_b64encode(self.body).rstrip(b"=").decode("ascii"),
            },
            separators=(",", ":"),
        ).encode()
        self.decode(data)
        return data

    @classmethod
    def decode(cls, data: bytes) -> Frame:
        value = strict_json(data, limit=MAX_FRAME)
        if set(value) != {"version", "session_id", "mode", "direction", "index", "body"}:
            raise FrameError("frame_fields")
        if type(value["version"]) is not int or value["version"] != 2:
            raise FrameError("frame_version")
        if not isinstance(value["session_id"], str) or not _UUID.fullmatch(value["session_id"]):
            raise FrameError("frame_session")
        if value["mode"] not in ("pair", "resume"):
            raise FrameError("frame_mode")
        if not isinstance(value["direction"], str) or value["direction"] not in _DIRECTIONS:
            raise FrameError("frame_direction")
        if type(value["index"]) is not int or not 0 <= value["index"] <= MAX_INTEGER:
            raise FrameError("frame_index")
        encoded = value["body"]
        if not isinstance(encoded, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
            raise FrameError("frame_base64")
        try:
            body = base64.b64decode(
                encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
            )
        except (ValueError, binascii.Error):
            raise FrameError("frame_base64") from None
        if base64.urlsafe_b64encode(body).rstrip(b"=").decode() != encoded:
            raise FrameError("frame_base64")
        if not 1 <= len(body) <= MAX_CHUNK:
            raise FrameError("frame_chunk_limit")
        return cls(value["session_id"], value["mode"], value["direction"], value["index"], body)


class OrderedFrames:
    """One direction of a session, with bounded gap buffering and a fixed deadline."""

    def __init__(self, session_id: str, mode: str, direction: str) -> None:
        self.session_id = session_id
        self.mode = mode
        self.direction = direction
        self.expected = 0
        self._buffer: dict[int, bytes] = {}
        self._gap_since: float | None = None
        self._failed = False

    def accept(self, raw: bytes, *, now: float) -> bytes:
        if self._failed:
            raise FrameError("session_closed")
        try:
            self.check_deadline(now)
            frame = Frame.decode(raw)
            if (frame.session_id, frame.mode, frame.direction) != (
                self.session_id,
                self.mode,
                self.direction,
            ):
                raise FrameError("session_binding")
            if frame.index < self.expected:
                # Already-consumed carrier data cannot reach TLS a second time.
                return b""
            if frame.index > self.expected + MAX_REORDER:
                raise FrameError("reorder_limit")
            existing = self._buffer.get(frame.index)
            if existing is not None and existing != frame.body:
                raise FrameError("conflicting_frame")
            self._buffer[frame.index] = frame.body
            result: list[bytes] = []
            while self.expected in self._buffer:
                result.append(self._buffer.pop(self.expected))
                self.expected += 1
            if len(self._buffer) > MAX_REORDER:
                raise FrameError("reorder_limit")
            if sum(map(len, self._buffer.values())) > MAX_REORDER_BYTES:
                raise FrameError("reorder_limit")
            if not self._buffer:
                self._gap_since = None
            elif self._gap_since is None:
                self._gap_since = now
            return b"".join(result)
        except FrameError:
            self._failed = True
            self._buffer.clear()
            raise

    def check_deadline(self, now: float) -> None:
        if self._failed:
            raise FrameError("session_closed")
        if self._gap_since is not None and now - self._gap_since >= GAP_SECONDS:
            self._failed = True
            self._buffer.clear()
            raise FrameError("gap_timeout")


def encode_record(record: dict[str, Any]) -> bytes:
    body = json.dumps(record, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    strict_json(body)
    return len(body).to_bytes(4, "big") + body


class Records:
    """Incremental parser of authenticated TLS plaintext, never raw network input."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._failed = False

    def feed(self, plaintext: bytes) -> list[dict[str, Any]]:
        if self._failed:
            raise FrameError("session_closed")
        if len(self._buffer) + len(plaintext) > 2 * (MAX_RECORD + 4):
            self._failed = True
            raise FrameError("record_buffer_limit")
        self._buffer.extend(plaintext)
        result = []
        try:
            while len(self._buffer) >= 4:
                length = int.from_bytes(self._buffer[:4], "big")
                if not 1 <= length <= MAX_RECORD:
                    raise FrameError("record_size_limit")
                if len(self._buffer) < 4 + length:
                    break
                result.append(strict_json(bytes(self._buffer[4 : 4 + length])))
                del self._buffer[: 4 + length]
            return result
        except FrameError:
            self._failed = True
            self._buffer.clear()
            raise
