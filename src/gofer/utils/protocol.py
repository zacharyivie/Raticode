"""Bound wire records independently of retained provider logs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

MAX_PROTOCOL_RECORD_CHARS = 16 * 1024 * 1024
MAX_RETAINED_TEXT_CHARS = 2_000_000


class ProtocolRecordLimitError(ValueError):
    """A local framing limit, distinct from a provider-reported failure."""


async def read_protocol_line(reader: asyncio.StreamReader, provider: str) -> bytes:
    """Keep asyncio's framing overflow separate from JSON decoding errors."""
    try:
        return await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError as exc:
        return exc.partial
    except asyncio.LimitOverrunError as exc:
        raise ProtocolRecordLimitError(
            f"{provider} protocol record exceeded the configured output limit"
        ) from exc


@dataclass
class ProtocolLines:
    buffer: str = ""
    limit: int = MAX_PROTOCOL_RECORD_CHARS

    def feed(self, chunk: str) -> list[str]:
        lines = (self.buffer + chunk).split("\n")
        if any(len(line) > self.limit for line in lines):
            self.buffer = ""
            raise ProtocolRecordLimitError(
                f"Provider protocol record exceeded the {self.limit}-character limit"
            )
        self.buffer = lines.pop()
        return lines


def retained_text(text: str, limit: int = MAX_RETAINED_TEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    marker = "\n[retained provider content truncated]"
    return text[: max(0, limit - len(marker))] + marker
