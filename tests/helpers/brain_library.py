"""Shared waits for Second Brain library tasks in tests."""

from __future__ import annotations

import time
from typing import Any

import pytest

from gofer.ui.brain_library import BrainLibrary


def wait_idle(library: BrainLibrary, timeout: float = 15) -> None:
    """Wait until no library task is running, failing the test after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(task["state"] != "running" for task in library.snapshot()["tasks"]):
            return
        time.sleep(0.02)
    pytest.fail("Second Brain tasks did not finish")


def wait_task(library: BrainLibrary, task: dict[str, Any], timeout: float = 15) -> dict[str, Any]:
    """Wait for one library task to stop running and return its receipt."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        receipt = library.task(task["id"])
        if receipt["state"] != "running":
            return receipt
        time.sleep(0.02)
    pytest.fail("Second Brain task did not finish")
