"""A slow-drip server can't hold a page fetch open past its overall deadline (B-O21)."""

from __future__ import annotations

from typing import Any

import pytest

import gofer.utils.http_read as http_read


class Drip:
    """Sends one byte per read, each just inside the socket timeout."""

    def __init__(self, clock: list[float], total: int = 10_000) -> None:
        self.clock = clock
        self.left = total

    def read1(self, size: int) -> bytes:
        self.clock[0] += 5.0
        if not self.left:
            return b""
        self.left -= 1
        return b"x"


def test_a_dripping_body_stops_at_the_deadline(monkeypatch: Any) -> None:
    clock = [0.0]
    monkeypatch.setattr(http_read.time, "monotonic", lambda: clock[0])
    with pytest.raises(TimeoutError):
        http_read.read_before(Drip(clock), 1_000_000, deadline=20.0)  # type: ignore[arg-type]
    assert clock[0] <= 25.0


def test_a_normal_body_is_read_to_its_limit(monkeypatch: Any) -> None:
    clock = [0.0]
    monkeypatch.setattr(http_read.time, "monotonic", lambda: clock[0])
    body = http_read.read_before(Drip(clock, total=3), 10, deadline=100.0)  # type: ignore[arg-type]
    assert body == b"xxx"
