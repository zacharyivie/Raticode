"""Fail a test that runs too long instead of letting it hang the whole run."""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

import pytest


class TookTooLong(BaseException):
    """Raised inside the timed block; a BaseException so `except Exception` can't swallow it."""


@contextmanager
def finishes_within(seconds: float) -> Iterator[None]:
    """Interrupt the block with a test failure after `seconds`.

    Uses a real-time timer signal, which reaches pure-Python loops in the main
    thread. Where that isn't available (Windows, or a worker thread) the block
    runs without a limit.
    """
    if not hasattr(signal, "setitimer") or threading.current_thread() is not (
        threading.main_thread()
    ):
        yield
        return

    def interrupt(_signum: int, _frame: FrameType | None) -> None:
        raise TookTooLong(f"took longer than {seconds} seconds")

    previous = signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    except TookTooLong as exc:
        pytest.fail(str(exc))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
