"""PID checks never signal a process on Windows, where os.kill(pid, 0) terminates it."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from gofer.cli import client
from gofer.cli.commands import workflow as workflow_cmd
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
    assert workflow_cmd._approval_waiter_is_live(request)
    assert asked == [4321, 4322, 4321]


def test_mac_start_token_reads_the_start_time_sysctl_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes
    import struct

    asked: list[list[int]] = []
    processes = {4321: (1_700_000_000, 123)}

    class FakeLibc:
        def sysctl(self, mib: Any, count: int, buffer: Any, size: Any, *_rest: Any) -> int:
            asked.append(list(mib)[:count])
            start = processes.get(mib[3])
            if start is None:
                size._obj.value = 0  # No such process: success, nothing written.
                return 0
            # kinfo_proc starts with kp_proc.p_starttime, a timeval.
            data = struct.pack("=qi", *start) + b"\0" * 636
            ctypes.memmove(buffer, data, len(data))
            size._obj.value = len(data)
            return 0

    monkeypatch.setattr(pids, "_libc", FakeLibc)
    monkeypatch.setattr(sys, "platform", "darwin")

    assert pids.process_start_token(4321) == "mac:1700000000.000123"
    assert pids.process_start_token(4322) is None
    assert pids.process_start_token(0) is None
    assert pids.process_start_token(True) is None
    # CTL_KERN, KERN_PROC, KERN_PROC_PID, then the PID.
    assert asked == [[1, 14, 1, 4321], [1, 14, 1, 4322]]


def test_start_token_is_none_where_no_start_time_can_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "freebsd14")
    assert pids.process_start_token(os.getpid()) is None
