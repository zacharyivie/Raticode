"""Clear temp files that workers' commands left in RAM-backed /tmp.

Claude Code's sandbox gives every shell command ``TMPDIR=/tmp/claude-<uid>`` and
makes the rest of /tmp and /var/tmp read-only, so test runs inside a worker write
browser profiles and builds there. /tmp is often a RAM-backed tmpfs, so anything a
killed or careless run leaves behind takes memory until it is deleted. The
workers can't be given anywhere else to write without widening their sandbox, so
Raticode, which runs outside it, clears the leftovers instead.

Only the known temp roots are looked at: ``/tmp/claude-<uid>`` and the
``raticode-tests-<uid>`` folders of the test temp helper
(``frontend/electron/tests/test-tmp.cjs``) in /var/tmp and /tmp. Each top-level
entry is judged on its own, except folders that hold other runs' entries: Claude
Code's per-project folders (named after the project path, so they start with
``-``) and ``raticode-tests-<uid>``, whose entries are judged one by one instead.

An entry is removed only when all of these hold:

- this account owns it and it is on the same file system as its root;
- nothing inside it has changed for ``STALE_SECONDS`` (``PRESSURE_STALE_SECONDS``
  while the root's file system is ``PRESSURE_PERCENT`` full), and not since a
  turn this process is still running began;
- no process refers to it by its working folder, an open file, a mapped file, its
  command line or its environment; and
- it holds no socket or pipe, unless it is ``SERVER_STALE_SECONDS`` old.

Links are removed as links and never followed, so nothing outside a root is ever
touched. The sweep runs when the backend or trigger scheduler starts, every
``SWEEP_INTERVAL_SECONDS`` after that, and shortly after a turn ends. It also
clears Rem's and workflow runs' scratch folders (:mod:`gofer.ui.scratch_cleanup`).
"""

from __future__ import annotations

import itertools
import os
import re
import shutil
import stat
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from gofer.utils.logging import get_logger

log = get_logger(__name__)

# Longer than a worker turn may run (two hours), so a running turn's files outlive it.
STALE_SECONDS = 3 * 60 * 60
# When the temp file system is this full, files idle for an hour go too.
PRESSURE_PERCENT = 80.0
PRESSURE_STALE_SECONDS = 60 * 60
# A folder with a socket or pipe may belong to an idle server nobody has open.
SERVER_STALE_SECONDS = 12 * 60 * 60
SWEEP_INTERVAL_SECONDS = 60 * 60
# A turn's end asks for a sweep, but a busy computer gets at most one this often.
TURN_END_SWEEP_GAP_SECONDS = 10 * 60
# Turns this old are stuck, and no longer hold back the files made after they began.
RUNNING_TURN_LIMIT_SECONDS = 12 * 60 * 60
TEST_RUNS = "raticode-tests-{uid}"


@dataclass(slots=True)
class TempCleanupResult:
    removed: list[Path] = field(default_factory=list)
    freed_bytes: int = 0
    kept: int = 0


@dataclass(frozen=True, slots=True)
class _Root:
    path: Path
    # True for a root whose own entries hold other runs' entries one level down.
    has_containers: bool


def temp_roots(uid: int | None = None) -> list[Path]:
    """The folders workers' temp files go to on this computer, existing or not."""
    uid = os.getuid() if uid is None else uid
    runs = TEST_RUNS.format(uid=uid)
    return [Path("/tmp") / f"claude-{uid}", Path("/var/tmp") / runs, Path("/tmp") / runs]


def clean_worker_temp(
    roots: Iterable[Path] | None = None,
    *,
    now: float | None = None,
    held: Iterable[str] | None = None,
    running_since: float | None = None,
    used_percent: Callable[[Path], float] | None = None,
    dry_run: bool = False,
) -> TempCleanupResult:
    """Remove stale, unused entries in the worker temp roots.

    ``held`` are the paths processes refer to (read from /proc when None).
    ``running_since`` is when the oldest turn still running began.
    """
    clock = time.time() if now is None else now
    uid = os.getuid()
    chosen = list(temp_roots(uid) if roots is None else roots)
    result = TempCleanupResult()
    opened = [(path, _open_root(path, uid)) for path in chosen]
    found = [(path, handle) for path, handle in opened if handle is not None]
    try:
        if not found:
            return result
        references = _process_references(
            [path for path, _ in found] if held is None else None, held
        )
        since = running_turns_since(clock) if running_since is None else running_since
        measure = _used_percent if used_percent is None else used_percent
        for path, handle in found:
            busy = measure(path) >= PRESSURE_PERCENT
            limit = PRESSURE_STALE_SECONDS if busy else STALE_SECONDS
            root = _Root(path, has_containers=path.name == f"claude-{uid}")
            for unit in _units(root, handle, uid):
                _settle(unit, uid, references, clock, limit, since, dry_run, result)
    finally:
        for _, handle in found:
            _close(handle)
    if result.removed:
        log.info(
            "%s %d leftover worker temp entr%s, freeing %s",
            "Would remove" if dry_run else "Removed",
            len(result.removed),
            "y" if len(result.removed) == 1 else "ies",
            _size_text(result.freed_bytes),
        )
    return result


# Running turns --------------------------------------------------------------------

_turns_lock = threading.Lock()
_turns: dict[int, float] = {}
_turn_ids = itertools.count(1)


def turn_started() -> int:
    """Note that a turn began now; its files are kept until it ends."""
    with _turns_lock:
        token = next(_turn_ids)
        _turns[token] = time.time()
    return token


def turn_finished(token: int) -> None:
    """Note that a turn ended, and ask for a sweep of what it may have left."""
    with _turns_lock:
        _turns.pop(token, None)
    request_temp_cleanup()


def running_turns_since(now: float | None = None) -> float | None:
    """When the oldest turn still running began, ignoring stuck ones."""
    clock = time.time() if now is None else now
    with _turns_lock:
        starts = [start for start in _turns.values() if clock - start < RUNNING_TURN_LIMIT_SECONDS]
    return min(starts) if starts else None


# Scheduling -----------------------------------------------------------------------

_schedule_lock = threading.Lock()
_wake: threading.Event | None = None


def start_temp_cleanup(
    data_dir: Path, *, interval: float = SWEEP_INTERVAL_SECONDS
) -> Callable[[], None]:
    """Sweep now, then every ``interval`` and after turns end. Returns a stop function."""
    global _wake
    stopped = threading.Event()
    wake = threading.Event()
    with _schedule_lock:
        _wake = wake

    def sweep() -> None:
        from gofer.ui.scratch_cleanup import clean_stale_scratch

        try:
            clean_stale_scratch(data_dir)
        except Exception:  # noqa: BLE001 - one failed sweep must not end the loop
            log.warning("Couldn't clean up leftover sandbox scratch folders", exc_info=True)
        if sweeps_worker_temp():
            try:
                clean_worker_temp()
            except Exception:  # noqa: BLE001
                log.warning("Couldn't clean up leftover worker temp files", exc_info=True)

    def loop() -> None:
        while not stopped.is_set():
            # Cleared first, so a turn that ends during the sweep still wakes the next.
            wake.clear()
            sweep()
            last = time.monotonic()
            # A turn's end wakes the loop early, but sweeps stay a gap apart.
            if wake.wait(interval):
                stopped.wait(max(0.0, last + TURN_END_SWEEP_GAP_SECONDS - time.monotonic()))

    def stop() -> None:
        global _wake
        stopped.set()
        wake.set()
        with _schedule_lock:
            if _wake is wake:
                _wake = None

    threading.Thread(target=loop, name="temp-cleanup", daemon=True).start()
    return stop


def sweeps_worker_temp() -> bool:
    """Whether this process may clear the shared worker temp roots.

    A backend that a worker started, such as a test's or a dev instance's, may sit
    in the worker's sandbox, where it sees only some processes and can't tell
    which files are in use. It leaves the roots to the user's own Raticode.
    """
    from gofer.cli.client import ENV_AGENT_SESSION
    from gofer.core.command_slots import SANDBOX_ENV

    if not sys.platform.startswith("linux") or any(
        os.environ.get(name) for name in (ENV_AGENT_SESSION, SANDBOX_ENV)
    ):
        return False
    return not _inside_own_process_namespace()


def _inside_own_process_namespace() -> bool:
    """True inside a sandbox or container this account started.

    There process 1 is the sandbox's own starter, so /proc shows only part of the
    computer. Outside one, process 1 is the system's and belongs to root.
    """
    uid = os.getuid()
    try:
        return uid != 0 and os.stat("/proc/1").st_uid == uid
    except OSError:
        return True


def request_temp_cleanup() -> None:
    """Ask the running sweeper for a sweep soon. Does nothing when none is running."""
    with _schedule_lock:
        wake = _wake
    if wake is not None:
        wake.set()


# Judging entries ------------------------------------------------------------------


# Every folder below is reached through an open handle, never by its path again: a
# worker that can write a root could otherwise swap a folder for a link to the user's
# home between the check and the delete, and the delete would follow it. Windows has
# none of these flags but O_RDONLY; the server imports this module there too, and the
# sweep itself runs only on Linux (sweeps_worker_temp).
_FOLDER_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


@dataclass(frozen=True, slots=True)
class _Unit:
    """One entry to judge: its path (for messages and process references), the open
    folder holding it and its name there."""

    path: Path
    parent: int
    name: str


def _open_root(path: Path, uid: int) -> int | None:
    """A handle on a root this account owns that no one else can write to, or None."""
    try:
        handle = os.open(path, _FOLDER_FLAGS)
    except OSError:
        return None
    info = os.fstat(handle)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
        _close(handle)
        return None
    return handle


def _open_child(parent: int, name: str) -> int | None:
    try:
        return os.open(name, _FOLDER_FLAGS, dir_fd=parent)
    except OSError:
        return None


def _close(handle: int) -> None:
    try:
        os.close(handle)
    except OSError:
        pass


def _units(root: _Root, handle: int, uid: int) -> Iterator[_Unit]:
    """The entries to judge: top-level ones, or the ones inside a container folder."""
    for name in _names(handle):
        if root.has_containers and _is_container_name(name, uid):
            inner = _open_child(handle, name)
            if inner is not None:
                try:
                    info = os.fstat(inner)
                    if stat.S_ISDIR(info.st_mode) and info.st_uid == uid:
                        for child in _names(inner):
                            yield _Unit(root.path / name / child, inner, child)
                        continue
                finally:
                    _close(inner)
        yield _Unit(root.path / name, handle, name)


def _is_container_name(name: str, uid: int) -> bool:
    return name.startswith("-") or name == TEST_RUNS.format(uid=uid)


def _names(folder: int) -> list[str]:
    try:
        with os.scandir(folder) as found:
            return sorted(entry.name for entry in found)
    except OSError:
        return []


def _settle(
    unit: _Unit,
    uid: int,
    references: _References,
    clock: float,
    limit: float,
    running_since: float | None,
    dry_run: bool,
    result: TempCleanupResult,
) -> None:
    survey = _survey(unit, uid, clock)
    if survey is None or references.holds(unit.path):
        result.kept += 1
        return
    newest, size, special, identity = survey
    age = clock - newest
    if age <= limit or (special and age <= SERVER_STALE_SECONDS):
        result.kept += 1
        return
    if running_since is not None and newest >= running_since:
        result.kept += 1
        return
    if not dry_run:
        _remove(unit, identity)
        if _lstat(unit) is not None:
            log.warning("Couldn't fully remove the worker temp entry %s", unit.path)
            result.kept += 1
            return
    result.removed.append(unit.path)
    result.freed_bytes += size


def _lstat(unit: _Unit) -> os.stat_result | None:
    try:
        return os.stat(unit.name, dir_fd=unit.parent, follow_symlinks=False)
    except OSError:
        return None


def _survey(unit: _Unit, uid: int, clock: float) -> tuple[float, int, bool, tuple[int, int]] | None:
    """Newest change, disk space, whether a socket or pipe is inside, and the entry's
    (device, inode) to check again just before it is removed.

    None means the entry must be kept: another account owns part of it, part of it
    is another file system, or it couldn't be read. A modification time in the
    future is set by hand and ignored; the change time can't be set that way.
    """
    info = _lstat(unit)
    if info is None:
        return None
    try:
        device = os.fstat(unit.parent).st_dev
    except OSError:
        return None
    if info.st_uid != uid or info.st_dev != device:
        return None
    identity = (info.st_dev, info.st_ino)
    newest = _changed(info, clock)
    size = _disk_bytes(info)
    special = stat.S_ISSOCK(info.st_mode) or stat.S_ISFIFO(info.st_mode)
    if not stat.S_ISDIR(info.st_mode):
        return newest, size, special, identity
    top = _open_child(unit.parent, unit.name)
    if top is None:
        return None
    pending = [top]
    try:
        if (os.fstat(top).st_dev, os.fstat(top).st_ino) != identity:
            return None
        while pending:
            folder = pending.pop()
            try:
                with os.scandir(folder) as found:
                    children = list(found)
            except OSError:
                return None
            try:
                for child in children:
                    try:
                        item = child.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if item.st_uid != uid or item.st_dev != device:
                        return None
                    newest = max(newest, _changed(item, clock))
                    size += _disk_bytes(item)
                    special = special or stat.S_ISSOCK(item.st_mode) or stat.S_ISFIFO(item.st_mode)
                    if stat.S_ISDIR(item.st_mode):
                        inner = _open_child(folder, child.name)
                        if inner is None:
                            return None
                        pending.append(inner)
            finally:
                _close(folder)
    finally:
        for left in pending:
            _close(left)
    return newest, size, special, identity


def _changed(info: os.stat_result, clock: float) -> float:
    return max(info.st_ctime, info.st_mtime if info.st_mtime <= clock else 0.0)


def _remove(unit: _Unit, identity: tuple[int, int]) -> None:
    """Remove the entry the survey judged, inside the folder it was found in.

    Nothing goes when the name now leads somewhere else (a folder swapped since the
    survey). A link goes as a link; what it points at stays. rmtree with ``dir_fd``
    works through open folder handles and refuses a link.
    """
    info = _lstat(unit)
    if info is None or (info.st_dev, info.st_ino) != identity:
        return
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(unit.name, dir_fd=unit.parent, ignore_errors=True)
        return
    try:
        os.unlink(unit.name, dir_fd=unit.parent)
    except OSError:
        pass


def _disk_bytes(info: os.stat_result) -> int:
    blocks = getattr(info, "st_blocks", None)
    return blocks * 512 if isinstance(blocks, int) else info.st_size


def _used_percent(path: Path) -> float:
    try:
        info = os.statvfs(path)
    except OSError:
        return 0.0
    return 100 * (1 - info.f_bavail / info.f_blocks) if info.f_blocks else 0.0


# Process references ---------------------------------------------------------------


class _References:
    """Paths under the roots that some process names, to answer "is this in use?"."""

    # Characters that may end a path in command lines, environments and maps. Each
    # reference is kept both cut there and whole to the end of its line, so a name
    # that holds one of them still matches.
    _ENDS = re.compile(r"[\0\n\s\"',:;=()\[\]{}<>|&]")

    def __init__(self, texts: Iterable[str]) -> None:
        self._held: set[str] = set()
        for text in texts:
            self._held.add(text.removesuffix(" (deleted)"))

    @classmethod
    def from_proc(cls, roots: list[Path]) -> _References:
        prefixes = [f"{root}/" for root in roots]
        found: list[str] = []
        for text in _process_texts():
            for prefix in prefixes:
                start = text.find(prefix)
                while start >= 0:
                    rest = text[start:]
                    whole = rest.split("\0", 1)[0].split("\n", 1)[0]
                    found.append(whole)
                    found.append(cls._ENDS.split(rest, maxsplit=1)[0])
                    start = text.find(prefix, start + len(prefix))
        return cls(found)

    def holds(self, entry: Path) -> bool:
        name = str(entry)
        below = f"{name}/"
        return any(held == name or held.startswith(below) for held in self._held)


def _process_references(roots: list[Path] | None, held: Iterable[str] | None) -> _References:
    if held is not None:
        return _References(held)
    return _References.from_proc(roots or [])


def _process_texts() -> Iterator[str]:
    """Everything each readable process says about the files it uses."""
    try:
        processes = [entry for entry in os.scandir("/proc") if entry.name.isdigit()]
    except OSError:
        return
    for process in processes:
        base = process.path
        for link in ("cwd", "root", "exe"):
            try:
                yield os.readlink(f"{base}/{link}")
            except OSError:
                pass
        try:
            with os.scandir(f"{base}/fd") as handles:
                for handle in handles:
                    try:
                        yield os.readlink(handle.path)
                    except OSError:
                        pass
        except OSError:
            pass
        for name in ("maps", "cmdline", "environ"):
            try:
                with open(f"{base}/{name}", "rb") as file:
                    yield file.read().decode(errors="replace")
            except OSError:
                pass


def _size_text(size: int) -> str:
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} bytes" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} bytes"
