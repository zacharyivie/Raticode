"""PID checks never signal a process on Windows, where os.kill(pid, 0) terminates it."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from gofer.cli import client
from gofer.cli.commands import workflow as workflow_cmd
from gofer.core import approvals
from gofer.ui import api
from gofer.utils import pids


def test_pid_alive_on_posix() -> None:
    if sys.platform == "win32":
        pytest.skip("POSIX check")
    assert pids.pid_alive(os.getpid())
    assert not pids.pid_alive(0)
    assert not pids.pid_alive(-1)
    assert not pids.pid_alive(None)
    assert not pids.pid_alive(True)


def test_no_pid_check_signals_a_process_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    def kill(pid: int, sig: int) -> None:
        raise AssertionError(f"os.kill({pid}, {sig}) would terminate the process on Windows")

    asked: list[int] = []

    def query(pid: int) -> bool:
        asked.append(pid)
        return True

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(pids, "_windows_pid_alive", query)
    request: Any = SimpleNamespace(waiter_pid=4321)

    assert pids.pid_alive(4321)
    assert client._pid_alive(4322)
    assert approvals._pid_is_live(4323)
    assert api._approval_waiter_is_live(request)
    assert workflow_cmd._approval_waiter_is_live(request)
    assert asked == [4321, 4322, 4323, 4321, 4321]
