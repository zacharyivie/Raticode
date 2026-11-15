"""Keep tests marked ``serial`` apart from every other test in parallel runs.

``python -m pytest -n 8 --dist worksteal`` runs the suite on eight pytest-xdist
workers. Mark a test ``@pytest.mark.serial`` only when it can't be isolated, for
example because it needs a fixed port or another process-wide resource; fix the
collision instead whenever that is possible.

In a parallel run every other test holds a shared lock while it runs. A serial
test first closes a gate, so no new test starts, then waits for the exclusive
lock, so it runs once the tests already running have finished. A plain
``python -m pytest`` run is serial already and takes no locks. The locks use
``fcntl``, so on Windows parallel runs don't hold serial tests apart.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any

import pytest

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

WORKER_INPUT_KEY = "raticode_serial_lock_dir"
_lock_dir = pytest.StashKey[Path]()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "serial: runs alone in parallel pytest-xdist runs; only for tests that can't be isolated",
    )
    worker_input = getattr(config, "workerinput", None)
    if worker_input and worker_input.get(WORKER_INPUT_KEY) and fcntl is not None:
        config.stash[_lock_dir] = Path(worker_input[WORKER_INPUT_KEY])


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Any) -> None:
    """On the xdist controller: share one lock folder with every worker."""
    config = node.config
    if _lock_dir not in config.stash:
        config.stash[_lock_dir] = Path(tempfile.mkdtemp(prefix="raticode-pytest-serial-"))
    node.workerinput[WORKER_INPUT_KEY] = str(config.stash[_lock_dir])


def pytest_unconfigure(config: pytest.Config) -> None:
    if not hasattr(config, "workerinput") and _lock_dir in config.stash:
        shutil.rmtree(config.stash[_lock_dir], ignore_errors=True)


@contextlib.contextmanager
def _locked(path: Path, operation: int) -> Iterator[None]:
    with path.open("a+b") as handle:
        fcntl.flock(handle, operation)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Generator[None]:
    lock_dir = item.config.stash.get(_lock_dir, None)
    if lock_dir is None:
        return (yield)
    with contextlib.ExitStack() as held:
        if item.get_closest_marker("serial") is not None:
            held.enter_context(_locked(lock_dir / "gate", fcntl.LOCK_EX))
            held.enter_context(_locked(lock_dir / "tests", fcntl.LOCK_EX))
        else:
            with _locked(lock_dir / "gate", fcntl.LOCK_SH):
                held.enter_context(_locked(lock_dir / "tests", fcntl.LOCK_SH))
        return (yield)
