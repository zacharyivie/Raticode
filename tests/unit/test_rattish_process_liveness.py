"""Whether the process that owns a run or test result is still going.

On Windows ``os.kill`` terminates its target whatever the signal number, so the
check there must only query the process.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, NoReturn

import pytest

import gofer.rattish.run_service as run_service
from gofer.rattish import test_sets
from gofer.rattish.run_service import (
    effective_run_status,
    process_is_running,
    process_start_token,
)


def _no_kill(*_args: Any) -> NoReturn:
    raise AssertionError("os.kill terminates the process on Windows")


def _running_run(pid: int, start: str | None) -> dict[str, Any]:
    document: dict[str, Any] = {"run_id": "r1", "status": "running", "runner_pid": pid}
    if start is not None:
        document["runner_start"] = start
    return document


def _running_result(pid: int, start: str | None) -> dict[str, Any]:
    return {"id": "t1", "status": "running", "runnerPid": pid, "runnerStart": start}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process queries")
def test_windows_check_follows_a_real_child_and_never_signals_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "kill", _no_kill)
    # A virtual environment's python.exe is a launcher with the interpreter as its
    # child; the base interpreter is one process, so terminate() ends all of it.
    python = getattr(sys, "_base_executable", None) or sys.executable
    child = subprocess.Popen([python, "-I", "-c", "import time; time.sleep(120)"])
    try:
        token = process_start_token(child.pid)
        assert token is not None and token.startswith("win:")
        assert process_start_token(child.pid) == token
        assert process_is_running(child.pid)
        assert process_is_running(child.pid, token)
        # A different start time is a later process given the same PID.
        assert not process_is_running(child.pid, "win:1")

        assert effective_run_status(_running_run(child.pid, token)) == "running"
        assert effective_run_status(_running_run(child.pid, None)) == "running"
        assert effective_run_status(_running_run(child.pid, "win:1")) == "interrupted"
        assert test_sets._is_running(_running_result(child.pid, token))
        assert not test_sets._is_running(_running_result(child.pid, "win:1"))

        # Checking left it running.
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=30)

    # Popen still holds a handle, so the PID isn't free yet; the exit code shows it ended.
    assert not process_is_running(child.pid)
    assert not process_is_running(child.pid, token)
    assert process_start_token(child.pid) is None
    assert effective_run_status(_running_run(child.pid, token)) == "interrupted"
    assert not test_sets._is_running(_running_result(child.pid, token))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process queries")
def test_windows_this_process_has_a_stable_start_token() -> None:
    token = process_start_token(os.getpid())
    assert token is not None and token.startswith("win:")
    assert process_start_token(os.getpid()) == token
    assert process_is_running(os.getpid(), token)


class _FakeWindows:
    """Stands in for the Windows process query on any operating system."""

    def __init__(self) -> None:
        self.processes: dict[int, str] = {}
        self.asked: list[int] = []

    def __call__(self, pid: int) -> tuple[bool, str | None]:
        self.asked.append(pid)
        start = self.processes.get(pid)
        return (start is not None, start)


@pytest.fixture
def fake_windows(monkeypatch: pytest.MonkeyPatch) -> _FakeWindows:
    fake = _FakeWindows()
    monkeypatch.setattr(run_service, "_WINDOWS", True)
    monkeypatch.setattr(run_service, "_windows_process_state", fake)
    monkeypatch.setattr(os, "kill", _no_kill)
    return fake


def test_windows_runs_owned_by_another_process_report_their_real_state(
    fake_windows: _FakeWindows,
) -> None:
    other = os.getpid() + 1
    fake_windows.processes[other] = "win:500"

    assert effective_run_status(_running_run(other, "win:500")) == "running"
    # Records saved before Windows runners saved a start time rely on the PID.
    assert effective_run_status(_running_run(other, None)) == "running"
    assert effective_run_status(_running_run(other, "win:400")) == "interrupted"
    assert effective_run_status(_running_run(other + 1, "win:500")) == "interrupted"
    assert fake_windows.asked
    assert process_start_token(other) == "win:500"
    assert process_start_token(other + 1) is None


def test_windows_test_results_owned_by_another_process_report_their_real_state(
    fake_windows: _FakeWindows,
) -> None:
    other = os.getpid() + 1
    fake_windows.processes[other] = "win:500"

    assert test_sets._is_running(_running_result(other, "win:500"))
    assert test_sets._is_running(_running_result(other, None))
    assert not test_sets._is_running(_running_result(other, "win:400"))
    assert not test_sets._is_running(_running_result(other + 1, None))


def test_runs_of_this_process_never_ask_the_operating_system(
    fake_windows: _FakeWindows,
) -> None:
    mine = _running_run(os.getpid(), "win:1")
    assert effective_run_status(mine) == "interrupted"  # Not in this process's active set.
    assert not fake_windows.asked


@pytest.mark.parametrize("pid", [0, -4, "12", None, True])
def test_bad_pids_are_never_running(pid: Any, fake_windows: _FakeWindows) -> None:
    assert not process_is_running(pid)
    assert effective_run_status(_running_run(pid, None)) == "interrupted"
    assert not fake_windows.asked


def test_the_real_query_is_off_outside_windows() -> None:
    if sys.platform == "win32":
        pytest.skip("Windows answers for real")
    assert run_service._windows_process_state(os.getpid()) == (False, None)


@pytest.mark.skipif(
    not (sys.platform in {"win32", "darwin"} or sys.platform.startswith("linux")),
    reason="start times are read on Linux, macOS and Windows",
)
def test_native_start_token_tells_a_later_process_apart() -> None:
    from gofer.utils.pids import process_start_token as native_start_token

    mine = native_start_token(os.getpid())
    assert mine is not None
    assert native_start_token(os.getpid()) == mine
    python = getattr(sys, "_base_executable", None) or sys.executable
    child = subprocess.Popen([python, "-I", "-c", "import time; time.sleep(120)"])
    try:
        token = native_start_token(child.pid)
        assert token is not None and token != mine
        assert native_start_token(child.pid) == token
    finally:
        child.terminate()
        child.wait(timeout=30)
