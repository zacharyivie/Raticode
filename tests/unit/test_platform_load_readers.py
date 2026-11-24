"""CPU pressure, memory pressure and free memory on macOS and Windows, from faked readers."""

from __future__ import annotations

import pytest

from gofer.core import command_slots
from gofer.core import computers as module
from gofer.core.computers import effective_threads

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               12000.
Pages active:                            400000.
Pages inactive:                           88000.
Pages speculative:                         3000.
Pages wired down:                         90000.
"""
# Captured at import, before the autouse fixture replaces them for every test.
REAL_MEMORY_HOLD = command_slots.memory_hold
REAL_LOCAL_PRESSURE = module.local_pressure


@pytest.fixture
def mac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_slots.sys, "platform", "darwin")
    monkeypatch.setattr(command_slots, "memory_hold", REAL_MEMORY_HOLD)


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_slots.sys, "platform", "win32")
    monkeypatch.setattr(command_slots, "memory_hold", REAL_MEMORY_HOLD)
    monkeypatch.setattr(module, "local_pressure", REAL_LOCAL_PRESSURE)
    monkeypatch.setattr(module, "_cpu_sample", None)
    monkeypatch.setattr(module, "_cpu_last", None)


@pytest.mark.parametrize(("level", "pressure"), [("1\n", 0.0), ("2\n", 50.0), ("4\n", 90.0)])
def test_mac_memory_pressure_comes_from_the_kernel_pressure_level(
    mac, monkeypatch: pytest.MonkeyPatch, level: str, pressure: float
) -> None:
    monkeypatch.setattr(command_slots, "_mac_pressure_level", lambda: level)
    assert command_slots.memory_pressure() == pressure
    if level.strip() == "4":
        assert command_slots.memory_pressure() >= 80  # type: ignore[operator]
        assert effective_threads(8, None, command_slots.memory_pressure()) == 1


@pytest.mark.parametrize("output", [None, "", "3\n", "critical\n"])
def test_mac_memory_pressure_is_none_without_a_known_level(
    mac, monkeypatch: pytest.MonkeyPatch, output: str | None
) -> None:
    monkeypatch.setattr(command_slots, "_mac_pressure_level", lambda: output)
    assert command_slots.memory_pressure() is None


def test_mac_free_memory_is_free_plus_inactive_pages(mac, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_slots, "_mac_vm_stat", lambda: VM_STAT)
    assert command_slots.memory_available_bytes() == (12000 + 88000) * 16384
    monkeypatch.setattr(command_slots, "_mac_vm_stat", lambda: "Pages free: 5.\n")
    assert command_slots.memory_available_bytes() is None
    monkeypatch.setattr(command_slots, "_mac_vm_stat", lambda: None)
    assert command_slots.memory_available_bytes() is None


def test_mac_memory_hold_reads_vm_stat(mac, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_slots, "swap_used_share", lambda: None)
    monkeypatch.setattr(command_slots, "_mac_vm_stat", lambda: VM_STAT.replace("88000", "40000"))
    assert command_slots.memory_hold() == "0.8 GB available"
    monkeypatch.setattr(command_slots, "_mac_vm_stat", lambda: VM_STAT)
    assert command_slots.memory_hold() is None


def test_windows_memory_comes_from_global_memory_status(
    windows, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(command_slots, "_windows_memory_status", lambda: (90, 6 * 1024**3))
    assert command_slots.memory_available_bytes() == 6 * 1024**3
    assert command_slots.memory_pressure() == 50.0
    monkeypatch.setattr(command_slots, "_windows_memory_status", lambda: (60, 1024**3))
    assert command_slots.memory_pressure() == 0.0
    assert command_slots.memory_hold() == "1.0 GB available"
    monkeypatch.setattr(command_slots, "_windows_memory_status", lambda: None)
    assert command_slots.memory_available_bytes() is None
    assert command_slots.memory_pressure() is None


def _feed(monkeypatch: pytest.MonkeyPatch, samples: list[tuple[float, tuple[int, int, int]]]):
    """Fake GetSystemTimes and the clock with (seconds, (idle, kernel, user)) samples."""
    queue = list(samples)
    current: list[float] = [0.0]

    def times() -> tuple[int, int, int]:
        current[0], value = queue.pop(0)
        return value

    monkeypatch.setattr(module, "_windows_system_times", times)
    monkeypatch.setattr(module, "_monotonic", lambda: current[0])


def test_windows_cpu_pressure_follows_busy_time_between_reads(
    windows, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Kernel time includes idle time. Each step adds 1000 units of kernel + user time.
    _feed(
        monkeypatch,
        [
            (0.0, (0, 0, 0)),
            (2.0, (200, 600, 400)),  # 20% idle: 80% busy
            (4.0, (300, 1100, 900)),  # 10% idle: 90% busy
            (6.0, (300, 1600, 1400)),  # no idle: 100% busy
            (6.5, (300, 1700, 1500)),  # too soon: keeps the last value
            (8.0, (600, 2100, 1900)),  # 30% idle since 6.0: 70% busy
        ],
    )
    readings = [module.cpu_pressure() for _ in range(6)]
    assert readings == [None, 0.0, 50.0, 100.0, 100.0, 0.0]


def test_windows_cpu_pressure_is_none_without_system_times(
    windows, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "_windows_system_times", lambda: None)
    monkeypatch.setattr(command_slots, "_windows_memory_status", lambda: None)
    assert module.cpu_pressure() is None
    assert module.local_pressure() == (None, None)


def test_windows_busy_room_starts_to_fall_above_84_and_reaches_one_worker_at_96() -> None:
    def room(busy: float) -> int:
        return effective_threads(10, command_slots.busy_pressure(busy))

    assert command_slots.busy_pressure(80) == 0
    assert command_slots.busy_pressure(90) == 50
    assert command_slots.busy_pressure(100) == 100
    assert room(84) == 10
    assert room(85) < 10
    assert room(96) == 1


def test_the_real_readers_give_none_off_their_own_system(monkeypatch: pytest.MonkeyPatch) -> None:
    # Off Windows there is no windll and the macOS tools may be missing: no error, no reading.
    if command_slots.sys.platform != "win32":
        assert command_slots._windows_memory_status() is None
        assert module._windows_system_times() is None
    assert command_slots._command_output(["/nonexistent/raticode-tool"]) is None


def test_a_system_command_reading_is_reused_for_a_moment(monkeypatch: pytest.MonkeyPatch) -> None:
    # The phone bridge and every start read macOS memory several times a second; each
    # read must not start vm_stat or sysctl again.
    runs: list[list[str]] = []
    now = [100.0]

    class Done:
        returncode = 0
        stdout = "4\n"

    def run(argv: list[str], **_: object) -> Done:
        runs.append(argv)
        return Done()

    monkeypatch.setattr(command_slots.subprocess, "run", run)
    monkeypatch.setattr(command_slots.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(command_slots, "_command_cache", {})
    argv = ["/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level"]
    assert command_slots._command_output(argv) == "4\n"
    now[0] += 1.0
    assert command_slots._command_output(argv) == "4\n"
    assert len(runs) == 1
    # Another command has its own reading.
    assert command_slots._command_output(["/usr/bin/vm_stat"]) == "4\n"
    assert len(runs) == 2
    # Once the reading is older than the reuse window it is read again.
    now[0] += command_slots._COMMAND_CACHE_SECONDS
    command_slots._command_output(argv)
    assert len(runs) == 3
