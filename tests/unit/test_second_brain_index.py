from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from gofer.ui import second_brain_index as indexing
from gofer.ui.second_brain import SecondBrain


@pytest.fixture(autouse=True)
def close_indexes():
    indexing.close_note_indexes()
    yield
    indexing.close_note_indexes()


def eventually(check: Callable[[], None]) -> None:
    deadline = time.monotonic() + 2
    while True:
        try:
            check()
            return
        except AssertionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def test_repeated_queries_share_index_without_rewalking_tree(tmp_path, monkeypatch):
    (tmp_path / "knowledge.md").write_text("SQLite café 東京")
    brain = SecondBrain(tmp_path)
    assert brain.search("東京")
    index = indexing.note_index(tmp_path)
    # The initial scan completed; unchanged queries must use FTS directly.
    monkeypatch.setattr(index, "_paths", lambda: pytest.fail("Unchanged tree was walked again"))
    for _ in range(5):
        assert SecondBrain(tmp_path).search("café")[0]["path"] == "knowledge.md"


def test_native_events_reconcile_external_add_edit_rename_delete_and_symlinks(tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    brain = SecondBrain(root)
    assert not brain.search("")
    folder = root / "notes"
    folder.mkdir()
    note = folder / "first.md"
    note.write_text("Alpha")

    def alpha_found():
        assert [row["path"] for row in brain.search("Alpha")] == ["notes/first.md"]

    eventually(alpha_found)
    note.write_text("Beta")

    def edit_found():
        assert brain.search("Beta")
        assert not brain.search("Alpha")

    eventually(edit_found)
    renamed = folder / "renamed.md"
    note.rename(renamed)

    def rename_found():
        assert [row["path"] for row in brain.search("Beta")] == ["notes/renamed.md"]

    eventually(rename_found)
    renamed.unlink()
    outside = tmp_path / "outside.md"
    outside.write_text("Secret")
    renamed.symlink_to(outside)

    def deletion_found():
        assert not brain.search("Beta")
        assert not brain.search("Secret")

    eventually(deletion_found)
    with pytest.raises(ValueError):
        brain.call("read_note", {"path": "notes/renamed.md"})


def test_internal_directory_and_file_links_are_not_read_or_indexed(tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    (root / "original.md").write_text("Original")
    (root / "alias.md").symlink_to(root / "original.md")
    (root / "alias").symlink_to(root, target_is_directory=True)
    brain = SecondBrain(root)
    assert [row["path"] for row in brain.search("Original")] == ["original.md"]
    for path in ("alias.md", "alias/original.md"):
        with pytest.raises(ValueError, match="without symbolic links"):
            brain.call("read_note", {"path": path})


def test_concurrent_queries_coordinate_one_initial_scan(tmp_path, monkeypatch):
    (tmp_path / "note.md").write_text("Concurrent")
    index = indexing.note_index(tmp_path)
    original = index._paths
    walks = []

    def walk():
        walks.append(True)
        return original()

    monkeypatch.setattr(index, "_paths", walk)
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda _: SecondBrain(tmp_path).search("Concurrent"), range(8)))
    assert all(results)
    assert len(walks) == 1


def test_recovery_after_restart_or_missed_event_and_watcher_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing.NoteIndex, "_start_observer", lambda self: None)
    path = tmp_path / "note.md"
    path.write_text("Before")
    brain = SecondBrain(tmp_path)
    assert brain.search("Before")
    path.write_text("After")
    indexing.note_index(tmp_path).last_scan = float("-inf")
    assert brain.search("After")
    indexing.close_note_indexes()
    path.unlink()
    assert not SecondBrain(tmp_path).search("After")


def test_save_invalidates_immediately_without_waiting_for_native_event(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing.NoteIndex, "_start_observer", lambda self: None)
    brain = SecondBrain(tmp_path)
    assert not brain.search("")
    brain.call("save_note", {"path": "new.md", "content": "Immediate"})
    assert brain.search("Immediate")


def index_connection(root: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(root / "index.sqlite3")
    connection.execute("CREATE VIRTUAL TABLE notes USING fts5(id UNINDEXED, path, content)")
    connection.execute(
        "CREATE TABLE note_state (id TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER)"
    )
    connection.commit()
    return connection


def test_reconcile_caps_aggregate_reads_and_resumes_without_partial_search(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing.NoteIndex, "_start_observer", lambda self: None)
    monkeypatch.setattr(indexing, "MAX_NOTE_BYTES", 7)
    monkeypatch.setattr(indexing, "MAX_BATCH_BYTES", 15)
    for number in range(4):
        (tmp_path / f"note-{number}.md").write_text("Content")
    index = indexing.NoteIndex(tmp_path)
    reads = []
    original_read = indexing.read_note_bytes

    def read(root, relative):
        result = original_read(root, relative)
        reads.append(len(result[0]) if result else 0)
        return result

    monkeypatch.setattr(indexing, "read_note_bytes", read)
    statements: list[str] = []
    with index_connection(tmp_path) as connection:
        connection.set_trace_callback(statements.append)
        complete = index.reconcile(connection)
        assert not complete
        assert sum(reads) <= 15
        assert len(reads) == 2
        assert statements.count("COMMIT") == 1
        while not complete:
            reads.clear()
            complete = index.reconcile(connection)
            assert sum(reads) <= 15
        assert connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 4
    # The public search waits automatically for every pass, keeping its complete API.
    assert len(SecondBrain(tmp_path).search("Content")) == 4


def test_reconcile_deadline_also_bounds_incremental_directory_enumeration(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing.NoteIndex, "_start_observer", lambda self: None)
    monkeypatch.setattr(indexing, "MAX_BATCH_SECONDS", 0.005)
    for number in range(10):
        (tmp_path / f"note-{number}.md").write_text("Content")
    index = indexing.NoteIndex(tmp_path)
    original_paths = index._paths
    visited = []

    def slow_paths():
        for path in original_paths():
            visited.append(path)
            time.sleep(0.006)
            yield path

    monkeypatch.setattr(index, "_paths", slow_paths)
    with index_connection(tmp_path) as connection:
        assert not index.reconcile(connection)
        assert len(visited) == 1
        while not index.reconcile(connection):
            pass
        assert connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 10


def test_background_recovery_finishes_before_next_query(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing.NoteIndex, "_start_observer", lambda self: None)
    monkeypatch.setattr(indexing, "FALLBACK_SECONDS", 0.02)
    note = tmp_path / "note.md"
    note.write_text("Before")
    brain = SecondBrain(tmp_path)
    assert brain.search("Before")
    note.write_text("After")
    database = tmp_path / ".raticode/second-brain.sqlite3"

    def recovered():
        # Inspect SQLite directly: calling search here would itself request work.
        with closing(sqlite3.connect(database)) as connection, connection:
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM notes WHERE notes MATCH 'After'"
                ).fetchone()[0]
                == 1
            )

    eventually(recovered)
    assert brain.search("After")
    assert not brain.search("Before")


def test_search_never_exposes_intermediate_recovery_results(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing.NoteIndex, "_start_observer", lambda self: None)
    monkeypatch.setattr(indexing, "MAX_NOTE_BYTES", 7)
    monkeypatch.setattr(indexing, "MAX_BATCH_BYTES", 8)
    for number in range(5):
        (tmp_path / f"note-{number}.md").write_text("Before")
    brain = SecondBrain(tmp_path)
    assert len(brain.search("Before")) == 5
    index = indexing.note_index(tmp_path)
    with index.lock:
        for number in range(5):
            note = tmp_path / f"note-{number}.md"
            note.write_text("After")
            index.invalidate(note)
    assert len(brain.search("After")) == 5
    assert not brain.search("Before")


def test_closing_index_stops_background_worker(tmp_path):
    SecondBrain(tmp_path).search("")
    index = indexing.note_index(tmp_path)
    worker = index.worker
    assert worker is not None and worker.is_alive()
    indexing.close_note_indexes()
    assert not worker.is_alive()


def test_root_eviction_stops_observer_threads(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing, "MAX_INDEX_ROOTS", 1)
    first = tmp_path / "one"
    first.mkdir()
    SecondBrain(first).search("")
    index = indexing.note_index(first)
    observer = index.observer
    assert observer is not None and observer.is_alive()
    second = tmp_path / "two"
    second.mkdir()
    SecondBrain(second).search("")
    assert not observer.is_alive()
    assert len(indexing._indexes) == 1


def test_note_count_limit_and_oversized_body_still_apply(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing, "MAX_NOTES", 2)
    for number in range(3):
        (tmp_path / f"{number}.md").write_text("Note")
    with pytest.raises(ValueError, match="10,000"):
        SecondBrain(tmp_path).search("")
    oversized = tmp_path / "big.md"
    with oversized.open("wb") as output:
        output.truncate(indexing.MAX_NOTE_BYTES + 1)
    assert indexing.read_note_bytes(tmp_path, Path("big.md")) is None


def test_save_note_refuses_parent_swapped_after_authorization(tmp_path, monkeypatch):
    root = tmp_path / "brain"
    root.mkdir()
    approved = root / "notes"
    approved.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    brain = SecondBrain(root)
    original_resolve = brain.resolve

    def resolve_then_swap(relative: str) -> Path:
        result = original_resolve(relative)
        approved.rename(root / "original")
        approved.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(brain, "resolve", resolve_then_swap)
    with pytest.raises(OSError):
        brain.call("save_note", {"path": "notes/report.md", "content": "Fixture"})
    assert not list(outside.iterdir())
    assert not list((root / "original").iterdir())


def test_save_note_collision_keeps_knowledge_and_removes_temporary_files(tmp_path):
    brain = SecondBrain(tmp_path)
    brain.call("save_note", {"path": "notes/report.md", "content": "Original"})
    with pytest.raises(FileExistsError):
        brain.call("save_note", {"path": "notes/report.md", "content": "Replacement"})
    assert (tmp_path / "notes/report.md").read_text() == "Original"
    assert list((tmp_path / "notes").iterdir()) == [tmp_path / "notes/report.md"]


@pytest.mark.parametrize("budget", ["MAX_SCAN_ENTRIES", "MAX_SCAN_SECONDS"])
def test_tree_scan_budget_fails_without_returning_partial_results(tmp_path, monkeypatch, budget):
    monkeypatch.setattr(indexing.NoteIndex, "_start_observer", lambda self: None)
    monkeypatch.setattr(indexing, budget, 0)
    (tmp_path / "first.md").write_text("Fixture")
    with pytest.raises(ValueError, match="entry/time budget"):
        SecondBrain(tmp_path).search("Fixture")


def test_save_note_does_not_follow_existing_dangling_link(tmp_path):
    target = tmp_path / "absent.md"
    alias = tmp_path / "alias.md"
    alias.symlink_to(target)
    with pytest.raises(FileExistsError):
        SecondBrain(tmp_path).call("save_note", {"path": "alias.md", "content": "Fixture"})
    assert alias.is_symlink()
    assert not target.exists()


def test_large_ignored_subtree_skips_recursive_native_watch_registration(tmp_path, monkeypatch):
    ignored = tmp_path / "node_modules"
    ignored.mkdir()
    for number in range(12):
        (ignored / str(number)).write_text("ignored")
    (tmp_path / "note.md").write_text("Knowledge")
    monkeypatch.setattr(indexing, "MAX_WATCH_ENTRIES", 10)
    monkeypatch.setattr(indexing, "Observer", lambda: pytest.fail("Oversized native watch tree"))
    brain = SecondBrain(tmp_path)
    assert len(brain.search("Knowledge")) == 1
    index = indexing.note_index(tmp_path)
    assert index.observer is None
    monkeypatch.setattr(index, "_paths", lambda: pytest.fail("Polling must respect its interval"))
    assert len(brain.search("Knowledge")) == 1


def test_invalidation_during_bounded_scan_is_reconciled_before_completion(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing.NoteIndex, "_start_observer", lambda self: None)
    monkeypatch.setattr(indexing, "MAX_NOTE_BYTES", 7)
    monkeypatch.setattr(indexing, "MAX_BATCH_BYTES", 8)
    for number in range(4):
        (tmp_path / f"{number}.md").write_text("Before")
    index = indexing.NoteIndex(tmp_path)
    with index_connection(tmp_path) as connection:
        assert not index.reconcile(connection)
        indexed_path = connection.execute("SELECT path FROM notes").fetchone()[0]
        changed = tmp_path / indexed_path
        changed.write_text("After")
        index.invalidate(changed)
        while not index.reconcile(connection):
            pass
        assert connection.execute(
            "SELECT path FROM notes WHERE notes MATCH 'After'"
        ).fetchall() == [(indexed_path,)]
        assert connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 4


def test_background_error_reaches_search_and_next_search_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing.NoteIndex, "_start_observer", lambda self: None)
    (tmp_path / "note.md").write_text("Recovered")
    original = indexing.read_note_bytes
    attempts = 0

    def fail_once(root, relative):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("temporary read failure")
        return original(root, relative)

    monkeypatch.setattr(indexing, "read_note_bytes", fail_once)
    brain = SecondBrain(tmp_path)
    with pytest.raises(OSError, match="temporary read failure"):
        brain.search("Recovered")
    assert brain.search("Recovered")


def test_recreated_database_reindexes_with_existing_background_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing.NoteIndex, "_start_observer", lambda self: None)
    (tmp_path / "note.md").write_text("Knowledge")
    brain = SecondBrain(tmp_path)
    assert brain.search("Knowledge")
    (tmp_path / ".raticode/second-brain.sqlite3").unlink()
    assert brain.search("Knowledge")


def test_search_and_background_passes_close_their_sqlite_connections(tmp_path, monkeypatch):
    monkeypatch.setattr(indexing.NoteIndex, "_start_observer", lambda self: None)
    monkeypatch.setattr(indexing, "MAX_NOTE_BYTES", 7)
    monkeypatch.setattr(indexing, "MAX_BATCH_BYTES", 8)
    connections = []
    original_connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            return super().close()

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs, factory=TrackedConnection)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    for number in range(5):
        (tmp_path / f"note-{number}.md").write_text("Content")
    assert len(SecondBrain(tmp_path).search("Content")) == 5
    indexing.close_note_indexes()
    assert len(connections) >= 6
    assert all(connection.closed for connection in connections)


def test_empty_directory_opens_yield_and_count_toward_scan_budget(tmp_path, monkeypatch):
    from contextlib import contextmanager

    for number in range(12):
        (tmp_path / str(number)).mkdir()
    elapsed = 0.0
    opened = []
    original = indexing.scandir_without_links

    @contextmanager
    def slow_scandir(directory):
        nonlocal elapsed
        elapsed += 0.006
        opened.append(directory)
        with original(directory) as entries:
            yield entries

    monkeypatch.setattr(indexing, "scandir_without_links", slow_scandir)
    monkeypatch.setattr(indexing.time, "monotonic", lambda: elapsed)
    monkeypatch.setattr(indexing, "MAX_SCAN_SECONDS", 0.02)
    paths = indexing.NoteIndex(tmp_path)._paths()
    with pytest.raises(ValueError, match="entry/time budget"):
        while True:
            before = len(opened)
            next(paths)
            assert len(opened) - before <= 1
    assert len(opened) == 4


@pytest.mark.parametrize("full_scan", [True, False])
def test_replacement_at_note_limit_removes_stale_rows_before_adding(
    tmp_path, monkeypatch, full_scan
):
    monkeypatch.setattr(indexing.NoteIndex, "_start_observer", lambda self: None)
    monkeypatch.setattr(indexing, "MAX_NOTES", 2)
    (tmp_path / "old-z.md").write_text("Old")
    (tmp_path / "kept.md").write_text("Kept")
    brain = SecondBrain(tmp_path)
    assert len(brain.search("")) == 2
    index = indexing.note_index(tmp_path)
    with index.lock:
        (tmp_path / "old-z.md").unlink()
        (tmp_path / "new-a.md").write_text("New")
        if full_scan:
            index.invalidate(tmp_path, directory=True)
        else:
            index.invalidate(tmp_path / "new-a.md")
            index.invalidate(tmp_path / "old-z.md")
    assert [row["path"] for row in brain.search("")] == ["kept.md", "new-a.md"]
