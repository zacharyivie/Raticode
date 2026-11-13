"""Read an HTTP response body within an overall deadline.

``HTTPResponse.read(n)`` only bounds each socket read, so a server that sends a byte
just before every timeout can hold a fetch open for hours (CWE-400). This reads in
chunks and gives up once the whole fetch has run past its deadline.
"""

from __future__ import annotations

import http.client
import socket
import time

CHUNK_BYTES = 64 * 1024


def read_before(
    response: http.client.HTTPResponse,
    limit: int,
    deadline: float,
    sock: socket.socket | None = None,
) -> bytes:
    """Up to ``limit`` bytes of the body, or TimeoutError once ``deadline`` (monotonic) passes."""
    data = bytearray()
    while len(data) < limit:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("The site took too long to send the page")
        if sock is not None:
            sock.settimeout(remaining)
        chunk = response.read1(min(CHUNK_BYTES, limit - len(data)))
        if not chunk:
            break
        data += chunk
    return bytes(data)
