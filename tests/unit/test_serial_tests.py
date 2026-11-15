"""Tests marked ``serial`` run alone when pytest-xdist runs the suite in parallel."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PROBE = """
import time
from pathlib import Path

import pytest

LOG = Path(__file__).with_name("log.txt")


def _run(name):
    with LOG.open("a") as log:
        log.write(f"start {name} {time.monotonic()}\\n")
    time.sleep(0.4)
    with LOG.open("a") as log:
        log.write(f"end {name} {time.monotonic()}\\n")


@pytest.mark.parametrize("n", range(6))
def test_ordinary(n):
    _run(f"ordinary-{n}")


@pytest.mark.serial
@pytest.mark.parametrize("n", range(2))
def test_alone(n):
    _run(f"serial-{n}")
"""


def test_serial_tests_never_overlap_another_test_in_a_parallel_run(tmp_path: Path) -> None:
    pytest.importorskip("xdist")
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "test_probe.py").write_text(PROBE)
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT), *filter(None, [os.environ.get("PYTHONPATH")])]),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "tests.helpers.serial_tests",
            "-n",
            "3",
            "--dist",
            "worksteal",
            str(probe),
        ],
        cwd=probe,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "8 passed" in result.stdout

    spans: dict[str, list[float]] = {}
    for line in (probe / "log.txt").read_text().splitlines():
        _, name, moment = line.split()
        spans.setdefault(name, []).append(float(moment))
    assert len(spans) == 8
    for name, (start, end) in spans.items():
        if not name.startswith("serial-"):
            continue
        for other, (other_start, other_end) in spans.items():
            if other != name:
                assert other_end <= start or end <= other_start, f"{other} overlapped {name}"


def test_plain_runs_take_no_locks(pytestconfig: pytest.Config) -> None:
    from tests.helpers import serial_tests

    if hasattr(pytestconfig, "workerinput"):
        pytest.skip("this run is parallel")
    assert serial_tests._lock_dir not in pytestconfig.stash
