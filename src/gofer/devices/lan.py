"""Bounded TCP carrier shared by both device roles; payload remains TLS framing."""

from __future__ import annotations

import asyncio

from gofer.devices.framing import MAX_FRAME, FrameError


async def read_frame(reader: asyncio.StreamReader) -> bytes:
    # readexactly tolerates arbitrary split/coalesced TCP segments. Check before
    # requesting a body. The session owner supplies an absolute read deadline.
    length = int.from_bytes(await reader.readexactly(4), "big")
    if not 1 <= length <= MAX_FRAME:
        raise FrameError("frame_size_limit")
    return await reader.readexactly(length)


def packet(frame: bytes) -> bytes:
    if not 1 <= len(frame) <= MAX_FRAME:
        raise FrameError("frame_size_limit")
    return len(frame).to_bytes(4, "big") + frame
