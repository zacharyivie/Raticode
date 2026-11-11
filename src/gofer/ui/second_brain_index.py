"""Root-scoped note indexing with native change notifications and recovery scans."""

from __future__ import annotations

import atexit
import hashlib
import os
import sqlite3
import stat
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import closing
from itertools import chain
from pathlib import Path

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from gofer.utils.atomic_output import open_binary_input, scandir_without_links
from gofer.utils.logging import RepeatingFailure, get_logger

log = get_logger(__name__)

MAX_NOTE_BYTES = 2 * 1024 * 1024
MAX_NOTES = 10_000
MAX_INDEX_ROOTS = 8
MAX_BATCH_BYTES = 16 * 1024 * 1024
MAX_BATCH_SECONDS = 0.1
MAX_SCAN_ENTRIES = 100_000
MAX_SCAN_SECONDS = 10.0
MAX_WATCH_DIRECTORIES = 512
MAX_WATCH_ENTRIES = 10_000
MAX_WATCH_PREFLIGHT_SECONDS = 0.1
RECOVERY_SECONDS = 60.0
FALLBACK_SECONDS = 1.0
NOTE_SUFFIXES = {".md", ".markdown", ".html", ".htm", ".txt"}


def _included(relative: Path) -> bool:
    return all(
        not part.startswith(".") and part not in {"node_modules", "__pycache__"}
        for part in relative.parts
    )


def _note_stat(root: Path, relative: Path) -> os.stat_result | None:
    """Exclude directory links as well as final-component links and special files."""
    current = root
    try:
        for part in relative.parts:
            current /= part
            result = current.lstat()
            if stat.S_ISLNK(result.st_mode):
                return None
        if not stat.S_ISREG(result.st_mode) or result.st_size > MAX_NOTE_BYTES:
            return None
        return result
    except OSError:
        return None


def read_note_bytes(root: Path, relative: Path) -> tuple[bytes, os.stat_result] | None:
    """Read a bounded regular file, refusing links throughout the path on POSIX."""
    before = _note_stat(root, relative)
    if before is None:
        return None
    try:
        with open_binary_input(root / relative) as source:
            opened = os.fstat(source.fileno())
            if (opened.st_dev, opened.st_ino) != (
                before.st_dev,
                before.st_ino,
            ) or opened.st_size > MAX_NOTE_BYTES:
                return None
            content = source.read(MAX_NOTE_BYTES + 1)
            after = os.fstat(source.fileno())
        if len(content) > MAX_NOTE_BYTES:
            return None
        if (after.st_mtime_ns, after.st_size) != (opened.st_mtime_ns, opened.st_size):
            return None  # The next change event/recovery pass retries an in-progress edit.
        return content, opened
    except OSError:
        return None


class _Events(FileSystemEventHandler):
    def __init__(self, index: NoteIndex) -> None:
        self.index = index

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in {"created", "modified", "deleted", "moved"}:
            return
        for raw in (event.src_path, event.dest_path):
            if raw:
                self.index.invalidate(Path(os.fsdecode(raw)), directory=event.is_directory)


class NoteIndex:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.lock = threading.RLock()
        self.events_lock = threading.Lock()
        self.pending: set[str] = set()
        self.full_scan = True
        self.last_scan = 0.0
        self.observer: BaseObserver | None = None
        self.observer_started = False
        self.condition = threading.Condition(self.lock)
        self.worker: threading.Thread | None = None
        self.stopping = False
        self.wake = threading.Event()
        self.error: Exception | None = None
        self.work: Iterator[str | None] | None = None
        self.next_path: str | None = None
        self.scanning = False
        self.seen: set[str] = set()
        self.deferred: set[str] = set()

    def close(self) -> None:
        with self.condition:
            self.stopping = True
            worker = self.worker
            self.wake.set()
            self.condition.notify_all()
        self._stop_observer()
        if worker is not None:
            worker.join(timeout=2)

    def _stop_observer(self) -> None:
        if self.observer is not None:
            self.observer.stop()
            self.observer.join(timeout=2)
            self.observer = None

    def invalidate(self, path: Path, *, directory: bool = False) -> None:
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return
        if not _included(relative):
            return
        with self.events_lock:
            if directory or len(self.pending) >= MAX_NOTES:
                self.full_scan = True
            elif relative.suffix.lower() in NOTE_SUFFIXES:
                self.pending.add(relative.as_posix())
        self.wake.set()

    def _native_watch_scope_is_bounded(self, pass_deadline: float | None = None) -> bool:
        # Watchdog filters delivered events, not native recursive registration.
        # Include ignored subtrees in this cheap preflight so a large .git or
        # node_modules tree cannot allocate thousands of otherwise unused watches.
        directories = [self.root]
        directory_count = 0
        entries = 0
        deadline = time.monotonic() + MAX_WATCH_PREFLIGHT_SECONDS
        if pass_deadline is not None:
            deadline = min(deadline, pass_deadline)
        try:
            while directories:
                directory_count += 1
                if directory_count > MAX_WATCH_DIRECTORIES or time.monotonic() >= deadline:
                    return False
                current = directories.pop()
                with scandir_without_links(current) as children:
                    for entry in children:
                        entries += 1
                        if entries > MAX_WATCH_ENTRIES or time.monotonic() >= deadline:
                            return False
                        if entry.is_dir(follow_symlinks=False):
                            # scandir on a descriptor returns entry.name as its path.
                            # Resolve against the pinned directory we just enumerated.
                            directories.append(current / entry.name)
            return True
        except OSError:
            return False

    def _start_observer(self) -> None:
        if self.observer_started:
            return
        self.observer_started = True
        if not self._native_watch_scope_is_bounded():
            return
        observer = Observer()
        try:
            # Start before scanning so changes during the scan remain queued.
            observer.schedule(
                _Events(self),
                str(self.root),
                recursive=True,
                event_filter=[
                    FileCreatedEvent,
                    FileDeletedEvent,
                    FileModifiedEvent,
                    FileMovedEvent,
                    DirCreatedEvent,
                    DirDeletedEvent,
                    DirModifiedEvent,
                    DirMovedEvent,
                ],
            )
            observer.start()
        except OSError:
            observer.stop()
            if observer.is_alive():
                observer.join(timeout=2)
            # Network filesystems or exhausted OS watches retain bounded polling.
            return
        self.observer = observer

    def _paths(self) -> Iterator[str | None]:
        """Yield between directory operations and entries, sharing the pass deadline."""
        directories = [self.root]
        entries = 0
        scan_seconds = 0.0

        def check_budget() -> None:
            if entries > MAX_SCAN_ENTRIES or scan_seconds >= MAX_SCAN_SECONDS:
                raise ValueError(
                    "Second Brain directory scan exceeded its entry/time budget. "
                    "Choose a smaller knowledge root."
                )

        while directories:
            directory = directories.pop()
            started = time.monotonic()
            try:
                with scandir_without_links(directory) as children:
                    scan_seconds += time.monotonic() - started
                    check_budget()
                    yield None  # Opening an empty/unreadable subtree also consumes a pass.
                    while True:
                        started = time.monotonic()
                        entry = next(children, None)
                        if entry is None:
                            scan_seconds += time.monotonic() - started
                            check_budget()
                            break
                        entries += 1
                        relative = (directory / entry.name).relative_to(self.root)
                        path = None
                        if _included(relative):
                            if entry.is_dir(follow_symlinks=False):
                                directories.append(directory / entry.name)
                            elif (
                                relative.suffix.lower() in NOTE_SUFFIXES
                                and _note_stat(self.root, relative) is not None
                            ):
                                path = relative.as_posix()
                        scan_seconds += time.monotonic() - started
                        check_budget()
                        yield path
                    started = time.monotonic()
                scan_seconds += time.monotonic() - started
                check_budget()
                yield None
            except OSError:
                scan_seconds += time.monotonic() - started
                check_budget()
                yield None

    def _needs_work(self) -> bool:
        interval = RECOVERY_SECONDS if self.observer is not None else FALLBACK_SECONDS
        with self.events_lock:
            return bool(
                self.work is not None
                or self.full_scan
                or self.pending
                or time.monotonic() - self.last_scan >= interval
            )

    def synchronize(self, database: Path) -> None:
        """Wait for complete results; the worker also reconciles changes between queries."""
        with self.condition:
            if self.worker is None:
                self.worker = threading.Thread(
                    target=self._run, args=(database,), name="second-brain-index", daemon=True
                )
                self.worker.start()
            self.wake.set()
            while self._needs_work() and self.error is None and not self.stopping:
                self.condition.wait()
            if self.error is not None:
                error, self.error = self.error, None
                self.wake.set()
                raise error
            if self.stopping:
                raise ValueError("The Second Brain index is closed. Retry the search.")

    def _run(self, database: Path) -> None:
        failure = RepeatingFailure(log, "Second Brain search index update")
        try:
            while True:
                self.wake.clear()
                with self.condition:
                    if self.stopping:
                        return
                    if self.error is not None or not self._needs_work():
                        delay = RECOVERY_SECONDS if self.observer is not None else FALLBACK_SECONDS
                    else:
                        try:
                            # Reopen between passes so replacing/recreating the index file
                            # cannot leave a worker writing to an unlinked SQLite database.
                            with closing(sqlite3.connect(database, timeout=5)) as connection:
                                self.reconcile(connection)
                            failure.succeeded()
                        except Exception as exc:
                            # Searches report it too, but only while one is waiting.
                            failure.failed(exc)
                            self.error = exc
                        self.condition.notify_all()
                        delay = 0.001
                # Release the lock and SQLite transaction between bounded passes.
                self.wake.wait(timeout=delay)
        except Exception as exc:
            log.exception("Second Brain search index worker stopped")
            with self.condition:
                self.error = exc
        finally:
            with self.condition:
                if self.work is not None:
                    close = getattr(self.work, "close", None)
                    if close is not None:
                        close()
                    self.work = None
                self.worker = None
                self.condition.notify_all()

    def reconcile(self, connection: sqlite3.Connection) -> bool:
        """Perform one aggregate byte/time-bounded pass, retaining unfinished work.

        The deadline is cooperative: one filesystem/SQLite operation may finish after it.
        A note read reserves MAX_NOTE_BYTES + 1, so ingestion never exceeds the byte budget.
        Results are only queried after all passes and queued invalidations complete.
        """
        started = time.monotonic()
        observer_was_started = self.observer_started
        self._start_observer()
        try:
            if self.work is None:
                if not self._needs_work():
                    return True
                interval = RECOVERY_SECONDS if self.observer is not None else FALLBACK_SECONDS
                with self.events_lock:
                    self.scanning = self.full_scan or time.monotonic() - self.last_scan >= interval
                    pending, self.pending = self.pending, set()
                    self.full_scan = False
                if self.scanning:
                    if (
                        observer_was_started
                        and self.observer is not None
                        and not self._native_watch_scope_is_bounded(started + MAX_BATCH_SECONDS)
                    ):
                        self._stop_observer()
                    self.seen = set()
                    self.deferred.clear()
                    self.work = self._paths()
                else:
                    self.work = iter(sorted(pending))
            connection.execute("BEGIN IMMEDIATE")
            old = {
                path: (note_id, mtime, size)
                for note_id, path, mtime, size in connection.execute(
                    "SELECT notes.id, notes.path, note_state.mtime_ns, note_state.size "
                    "FROM notes JOIN note_state ON notes.id = note_state.id"
                )
            }
            note_count = len(old)
            processed_bytes = 0
            # A configured budget must permit at least one maximum-sized note.
            byte_budget = max(MAX_BATCH_BYTES, MAX_NOTE_BYTES + 1)
            progressed = False
            while not progressed or time.monotonic() - started < MAX_BATCH_SECONDS:
                progressed = True
                path, self.next_path = self.next_path, None
                if path is None:
                    try:
                        path = next(self.work)
                    except StopIteration:
                        if self.scanning:
                            self.scanning = False
                            self.work = chain(sorted(old.keys() - self.seen), sorted(self.deferred))
                            self.deferred.clear()
                            self.seen.clear()
                            self.last_scan = time.monotonic()
                            continue
                        self.work = None
                        break
                if path is None:
                    continue
                if self.scanning:
                    self.seen.add(path)
                    if len(self.seen) > MAX_NOTES:
                        raise ValueError(
                            "Second Brain has more than 10,000 notes. Choose a smaller root."
                        )
                relative = Path(path)
                info = _note_stat(self.root, relative)
                previous = old.get(path)
                if (
                    info is not None
                    and previous
                    and previous[1:] == (info.st_mtime_ns, info.st_size)
                ):
                    continue
                if info is not None and previous is None and note_count >= MAX_NOTES:
                    if self.scanning:
                        # At capacity, delete absent old paths before adding their replacements.
                        self.deferred.add(path)
                        continue
                    with self.events_lock:
                        self.full_scan = True
                    self.work = None
                    break
                if info is not None and processed_bytes + MAX_NOTE_BYTES + 1 > byte_budget:
                    self.next_path = path
                    break
                note_id = hashlib.sha256(path.encode()).hexdigest()
                data = read_note_bytes(self.root, relative) if info is not None else None
                # Charge a failed read too: an in-progress edit may have consumed the limit.
                if info is not None:
                    processed_bytes += len(data[0]) if data is not None else MAX_NOTE_BYTES + 1
                if data is None:
                    if previous:
                        note_count -= 1
                    connection.execute("DELETE FROM notes WHERE id=?", (note_id,))
                    connection.execute("DELETE FROM note_state WHERE id=?", (note_id,))
                else:
                    if previous is None:
                        if note_count >= MAX_NOTES:
                            raise ValueError(
                                "Second Brain has more than 10,000 notes. Choose a smaller root."
                            )
                        note_count += 1
                    content, info = data
                    connection.execute("DELETE FROM notes WHERE id=?", (note_id,))
                    connection.execute(
                        "INSERT INTO notes VALUES (?, ?, ?)",
                        (note_id, path, content.decode("utf8", errors="replace")),
                    )
                    connection.execute(
                        "INSERT OR REPLACE INTO note_state VALUES (?, ?, ?)",
                        (note_id, info.st_mtime_ns, info.st_size),
                    )
            connection.commit()
            return not self._needs_work()
        except BaseException:
            connection.rollback()
            if self.work is not None:
                close = getattr(self.work, "close", None)
                if close is not None:
                    close()
            self.work = None
            self.next_path = None
            with self.events_lock:
                self.full_scan = True
            raise


_indexes: OrderedDict[Path, NoteIndex] = OrderedDict()
_indexes_lock = threading.Lock()


def note_index(root: Path) -> NoteIndex:
    with _indexes_lock:
        index = _indexes.get(root)
        if index is None:
            index = NoteIndex(root)
            _indexes[root] = index
        _indexes.move_to_end(root)
        while len(_indexes) > MAX_INDEX_ROOTS:
            _, expired = _indexes.popitem(last=False)
            expired.close()
        return index


def close_note_indexes() -> None:
    with _indexes_lock:
        for index in _indexes.values():
            index.close()
        _indexes.clear()


atexit.register(close_note_indexes)
