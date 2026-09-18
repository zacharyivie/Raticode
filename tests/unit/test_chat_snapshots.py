from __future__ import annotations

import json
import os
import tracemalloc
from pathlib import Path

import pytest

from gofer.ui import chat


def test_snapshots_spool_content_and_reuse_unchanged_files(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    for index in range(24):
        (project / f"{index}.bin").write_bytes(bytes([index]) * 1024 * 1024)
    tracemalloc.start()
    before = chat._capture_chat_project(project)
    after = chat._capture_chat_project(project, before)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(before) == len(after) == 24
    assert all(state.data is None and state.blob is not None for state in before.values())
    assert all(after[name] is state for name, state in before.items())
    assert peak < 8 * 1024 * 1024
    if os.name != "nt":
        assert all(state.blob.stat().st_mode & 0o777 == 0o600 for state in before.values())
    monkeypatch.setattr(
        chat,
        "_serialized_chat_file_state",
        lambda *a, **kw: pytest.fail("preview built undo payload"),
    )
    (project / "0.bin").write_bytes(b"changed")
    preview = chat._preview_chat_changes(project, before)
    assert preview is not None
    assert preview["fileCount"] == 1


def test_disk_snapshots_preserve_binary_modes_and_legacy_undo(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    file = project / "binary"
    file.write_bytes(b"\x00before")
    file.chmod(0o640)
    before = chat._capture_chat_project(project)
    file.write_bytes(b"\x00after")
    file.chmod(0o600)
    changes = chat._finalize_chat_changes(project, before, tmp_path / "data")
    assert changes is not None
    store = tmp_path / "data" / "chat-change-sets" / f"{changes['id']}.json"
    payload = json.loads(store.read_text())
    assert "blob" in payload["files"][0]["before"]
    assert "data" not in payload["files"][0]["before"]
    chat.undo_chat_changes(changes["id"], tmp_path / "data")
    assert file.read_bytes() == b"\x00before"
    if os.name != "nt":
        assert file.stat().st_mode & 0o777 == 0o640
    chat.redo_chat_changes(changes["id"], tmp_path / "data")
    assert file.read_bytes() == b"\x00after"
    # Previously saved schema 1 payloads remain readable.
    for entry in payload["files"]:
        for side in ("before", "after"):
            state = chat._chat_file_state_from_payload(entry[side], tmp_path / "data")
            entry[side] = chat._serialized_chat_file_state(state)
    payload["schemaVersion"] = 1
    store.write_text(json.dumps(payload))
    chat.undo_chat_changes(changes["id"], tmp_path / "data")
    assert file.read_bytes() == b"\x00before"


def test_scan_budget_never_reports_uncaptured_files_as_deletions(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    for index in range(3):
        (project / str(index)).write_text("original")
    before = chat._capture_chat_project(project)
    monkeypatch.setattr(chat, "CHAT_CHANGE_MAX_SCAN_BYTES", 1)
    (project / "0").write_text("changed")
    assert chat._preview_chat_changes(project, before) is None


def test_large_files_are_not_read_for_snapshots(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "large").write_bytes(b"1234")
    monkeypatch.setattr(chat, "CHAT_CHANGE_MAX_FILE_BYTES", 2)
    monkeypatch.setattr(Path, "open", lambda *a, **kw: pytest.fail("oversized file was read"))
    before = chat._capture_chat_project(project)
    assert before["large"].data is None
    assert before["large"].blob is None


class _FakeObserver:
    def __init__(self):
        self.alive = False
        self.emitters = set()
        self.stopped = False

    def schedule(self, *args, **kwargs):
        pass

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def stop(self):
        self.alive = False
        self.stopped = True

    def join(self, **kwargs):
        pass


@pytest.fixture
def tracked_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    for index in range(100):
        (project / f"{index}.txt").write_text(f"original {index}\n")
    monkeypatch.setattr(chat, "Observer", _FakeObserver)
    tracker = chat._ChatProjectTracker(project)
    tracker.start()
    tracker.capture()  # Initial reconciliation, before relying on events.
    yield project, tracker
    tracker.close()


def test_incremental_preview_reads_only_changed_files_and_remembers_prior_changes(
    tracked_project,
    monkeypatch,
):
    project, tracker = tracked_project
    monkeypatch.setattr(chat.os, "walk", lambda *a, **kw: pytest.fail("walked project"))
    read_paths = []
    real_open = chat.open_binary_input

    def record_open(path):
        if path.is_relative_to(project):
            read_paths.append(path.relative_to(project).as_posix())
        return real_open(path)

    monkeypatch.setattr(chat, "open_binary_input", record_open)
    for name in ("0.txt", "1.txt"):
        (project / name).write_text("updated\n")
        tracker.invalidate(project / name)
        preview = chat._preview_chat_changes(project, tracker.before, tracker)
        assert preview is not None
    assert read_paths == ["0.txt", "1.txt"]
    assert preview is not None
    assert {item["path"] for item in preview["files"]} == {"0.txt", "1.txt"}
    assert chat._preview_chat_changes(project, tracker.before, tracker) == preview
    assert read_paths == ["0.txt", "1.txt"]
    (project / "0.txt").write_text("original 0\n")
    tracker.invalidate(project / "0.txt")
    preview = chat._preview_chat_changes(project, tracker.before, tracker)
    assert preview is not None
    assert [item["path"] for item in preview["files"]] == ["1.txt"]


def test_incremental_capture_handles_external_directory_moves_and_deletions(
    tracked_project,
    tmp_path,
):
    from watchdog.events import DirMovedEvent, FileDeletedEvent

    project, tracker = tracked_project
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "nested.txt").write_text("outside edit\n")
    incoming.rename(project / "added")
    events = chat._ChatProjectEvents(tracker)
    events.on_any_event(DirMovedEvent(str(incoming), str(project / "added")))
    assert tracker.capture()["added/nested.txt"].size == len("outside edit\n")
    (project / "added").rename(project / "renamed")
    events.on_any_event(DirMovedEvent(str(project / "added"), str(project / "renamed")))
    (project / "0.txt").unlink()
    events.on_any_event(FileDeletedEvent(str(project / "0.txt")))
    preview = chat._preview_chat_changes(project, tracker.before, tracker)
    assert preview is not None
    assert {item["path"]: item["status"] for item in preview["files"]} == {
        "0.txt": "deleted",
        "renamed/nested.txt": "added",
    }
    changes = chat._finalize_chat_changes(project, tracker.before, tmp_path / "data")
    assert changes is not None
    chat.undo_chat_changes(changes["id"], tmp_path / "data")
    assert (project / "0.txt").read_text() == "original 0\n"
    assert not (project / "renamed/nested.txt").exists()
    chat.redo_chat_changes(changes["id"], tmp_path / "data")
    assert (project / "renamed/nested.txt").read_text() == "outside edit\n"


def test_lost_events_recover_and_final_undo_captures_unreported_edits(tracked_project, tmp_path):
    project, tracker = tracked_project
    (project / "0.txt").write_text("lost event\n")
    assert chat._preview_chat_changes(project, tracker.before, tracker) is None
    tracker.last_recovery -= chat.CHAT_CHANGE_RECOVERY_INTERVAL
    preview = chat._preview_chat_changes(project, tracker.before, tracker)
    assert preview is not None
    assert preview["files"][0]["path"] == "0.txt"
    (project / "1.txt").write_text("another lost event\n")
    changes = chat._finalize_chat_changes(project, tracker.before, tmp_path / "data")
    assert changes is not None
    assert changes["fileCount"] == 2
    chat.undo_chat_changes(changes["id"], tmp_path / "data")
    assert (project / "0.txt").read_text() == "original 0\n"
    assert (project / "1.txt").read_text() == "original 1\n"


def test_incomplete_incremental_scan_keeps_previous_state_and_recovers(
    tracked_project,
    monkeypatch,
):
    project, tracker = tracked_project
    original = tracker.current["0.txt"]
    (project / "0.txt").write_text("changed\n")
    tracker.invalidate(project / "0.txt")
    with monkeypatch.context() as patch:
        patch.setattr(chat, "CHAT_CHANGE_MAX_SCAN_BYTES", 1)
        assert chat._preview_chat_changes(project, tracker.before, tracker) is None
    assert tracker.current["0.txt"] is original
    assert tracker.recover is True
    preview = chat._preview_chat_changes(project, tracker.before, tracker)
    assert preview is not None and preview["fileCount"] == 1


def test_incremental_replacement_releases_obsolete_spool(tracked_project):
    import gc
    import weakref

    project, tracker = tracked_project
    file = project / "0.txt"
    file.write_text("first version")
    tracker.invalidate(file)
    tracker.capture()
    old_owner = weakref.ref(tracker.current["0.txt"].owner)
    old_blob = tracker.current["0.txt"].blob
    file.write_text("second version")
    tracker.invalidate(file)
    tracker.capture()
    gc.collect()
    assert old_owner() is None
    assert not old_blob.exists()
    assert chat._chat_file_bytes(tracker.before["0.txt"]) == b"original 0\n"


def test_incremental_capture_excludes_symlink_subtrees_and_ignored_paths(
    tracked_project,
    tmp_path,
):
    project, tracker = tracked_project
    external = tmp_path / "external"
    external.mkdir()
    (external / "private.txt").write_text("secret")
    try:
        (project / "link").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks unavailable")
    tracker.invalidate(project / "link/private.txt")
    tracker.invalidate(project / "node_modules/private.txt")
    assert tracker.capture() == tracker.before
    assert not tracker.changed


def test_dead_watcher_falls_back_to_full_reconciliation(tracked_project):
    project, tracker = tracked_project
    tracker.observer.alive = False
    (project / "0.txt").unlink()
    preview = chat._preview_chat_changes(project, tracker.before, tracker)
    assert preview is not None and preview["files"][0]["status"] == "deleted"


def test_watcher_preflight_is_bounded_including_ignored_trees(tmp_path, monkeypatch):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules/nested").mkdir()
    monkeypatch.setattr(chat, "CHAT_CHANGE_MAX_WATCH_DIRECTORIES", 1)
    tracker = chat._ChatProjectTracker(tmp_path)
    monkeypatch.setattr(chat, "Observer", lambda: pytest.fail("allocated unbounded watcher"))
    tracker.start()
    assert tracker.observer is None
    (tmp_path / "new.txt").write_text("new")
    captured = tracker.capture()
    assert captured is not None and "new.txt" in captured


def test_scan_error_is_incomplete_not_a_deletion(tmp_path, monkeypatch):
    (tmp_path / "existing.txt").write_text("original")
    before = chat._capture_chat_project(tmp_path)

    def unreadable(*args, **kwargs):
        kwargs["onerror"](PermissionError("unreadable subtree"))

    monkeypatch.setattr(chat.os, "walk", unreadable)
    assert chat._preview_chat_changes(tmp_path, before) is None


def test_native_watcher_captures_external_edit_without_recovery(tmp_path):
    import time

    file = tmp_path / "external.txt"
    file.write_text("before")
    tracker = chat._ChatProjectTracker(tmp_path)
    try:
        tracker.start()
        if tracker.observer is None:
            pytest.skip("Native filesystem watcher unavailable")
        tracker.capture()
        file.write_text("after")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with tracker.lock:
                if "external.txt" in tracker.pending:
                    break
            time.sleep(0.01)
        else:
            pytest.fail("Native watcher missed external file edit")
        assert tracker.recover is False
        preview = chat._preview_chat_changes(tmp_path, tracker.before, tracker)
        assert preview is not None
        assert preview["fileCount"] == 1
        assert "+after" in preview["files"][0]["diff"]
    finally:
        observer = tracker.observer
        tracker.close()
    assert observer is not None and not observer.is_alive()


def test_event_arriving_during_capture_is_retained_for_next_preview(tracked_project, monkeypatch):
    project, tracker = tracked_project
    first, second = project / "0.txt", project / "1.txt"
    first.write_text("first edit")
    tracker.invalidate(first)
    capture = chat._capture_chat_project

    def concurrent_capture(*args, **kwargs):
        result = capture(*args, **kwargs)
        second.write_text("concurrent edit")
        tracker.invalidate(second)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(chat, "_capture_chat_project", concurrent_capture)
        tracker.capture()
    assert tracker.pending == {"1.txt"}
    preview = chat._preview_chat_changes(project, tracker.before, tracker)
    assert preview is not None and preview["fileCount"] == 2


def test_event_overflow_requests_recovery_without_losing_changes(tracked_project, monkeypatch):
    project, tracker = tracked_project
    monkeypatch.setattr(chat, "CHAT_CHANGE_MAX_SCAN_FILES", 100)
    for index in range(101):
        tracker.invalidate(project / f"pending-{index}")
    assert tracker.recover is True
    assert not tracker.pending
    (project / "0.txt").write_text("edit during event overflow")
    preview = chat._preview_chat_changes(project, tracker.before, tracker)
    assert preview is not None and preview["fileCount"] == 1


def test_empty_directories_count_toward_capture_budget(tmp_path, monkeypatch):
    for index in range(4):
        (tmp_path / str(index)).mkdir()
    monkeypatch.setattr(chat, "CHAT_CHANGE_MAX_SCAN_DIRECTORIES", 2)
    snapshot = chat._capture_chat_project(tmp_path)
    assert not snapshot.complete
    assert not snapshot


def test_empty_directories_obey_capture_deadline(tmp_path, monkeypatch):
    for index in range(4):
        (tmp_path / str(index)).mkdir()
    ticks = iter([0.0, chat.CHAT_CHANGE_MAX_SCAN_SECONDS + 1])
    monkeypatch.setattr(chat, "monotonic", lambda: next(ticks))
    snapshot = chat._capture_chat_project(tmp_path)
    assert not snapshot.complete
    assert not snapshot


def test_empty_directory_watch_preflight_obeys_deadline(tmp_path, monkeypatch):
    tracker = chat._ChatProjectTracker(tmp_path)
    ticks = iter([0.0, 0.0, chat.CHAT_CHANGE_MAX_WATCH_SECONDS + 1])
    monkeypatch.setattr(chat, "monotonic", lambda: next(ticks))
    assert not tracker._watch_scope_is_bounded()


def test_recovery_releases_watcher_when_native_scope_outgrows_limit(
    tracked_project,
    monkeypatch,
):
    project, tracker = tracked_project
    observer = tracker.observer
    tracker.last_recovery -= chat.CHAT_CHANGE_RECOVERY_INTERVAL
    monkeypatch.setattr(tracker, "_watch_scope_is_bounded", lambda: False)
    (project / "0.txt").write_text("edit during dependency installation")
    preview = chat._preview_chat_changes(project, tracker.before, tracker)
    assert observer.stopped
    assert tracker.observer is None
    assert preview is not None and preview["fileCount"] == 1


def test_line_counts_include_diff_like_text_and_count_before_preview_truncation(
    tmp_path, monkeypatch
):
    file = tmp_path / "patch.txt"
    file.write_text("--old\n---separator\nold\n", encoding="utf-8")
    before = chat._capture_chat_project(tmp_path)
    file.write_text("++new\n+++separator\nnew\nlast", encoding="utf-8")
    monkeypatch.setattr(chat, "CHAT_CHANGE_MAX_DIFF_CHARS", 40)
    result = chat._preview_chat_changes(tmp_path, before)
    assert result is not None
    assert result["additions"] == 4
    assert result["deletions"] == 3
    assert result["files"][0]["binary"] is False
    assert "diff truncated" in result["files"][0]["diff"]


def test_turn_edits_only_include_reported_paths_and_preserve_dirty_baselines(tmp_path):
    (tmp_path / "dirty.txt").write_text("user's unsaved-to-git work\n")
    (tmp_path / "unrelated.txt").write_text("existing change\n")
    baseline = chat._capture_chat_project(tmp_path)
    edits = chat._ChatTurnEdits(tmp_path, baseline)
    (tmp_path / "dirty.txt").write_text("user's unsaved-to-git work\nRem's addition\n")
    (tmp_path / "unrelated.txt").write_text("another process changed this\n")
    edits.observe(
        {
            "id": "edit",
            "kind": "tool",
            "title": "Edit",
            "phase": "result",
            "input": json.dumps([{"path": str(tmp_path / "dirty.txt")}]),
        }
    )
    changes = chat._finalize_chat_changes(
        tmp_path, baseline, tmp_path / ".data", snapshots=edits.snapshots()
    )
    assert changes is not None
    assert [file["path"] for file in changes["files"]] == ["dirty.txt"]
    assert changes["additions"] == 1
    assert changes["deletions"] == 0
    chat.undo_chat_changes(changes["id"], tmp_path / ".data")
    assert (tmp_path / "dirty.txt").read_text() == "user's unsaved-to-git work\n"
    assert (tmp_path / "unrelated.txt").read_text() == "another process changed this\n"


def test_shell_edits_exclude_changes_outside_tool_execution(tmp_path):
    file = tmp_path / "script-output.txt"
    file.write_text("original\n")
    baseline = chat._capture_chat_project(tmp_path)
    edits = chat._ChatTurnEdits(tmp_path, baseline)
    (tmp_path / "outside.txt").write_text("written before command\n")
    trace = {"id": "shell", "kind": "tool", "title": "bash", "category": "shell"}
    edits.observe({**trace, "phase": "start"})
    file.write_text("command output\n")
    edits.observe({**trace, "phase": "result"})
    (tmp_path / "after.txt").write_text("written after command\n")
    changes = chat._finalize_chat_changes(
        tmp_path, baseline, tmp_path / ".data", snapshots=edits.snapshots()
    )
    assert changes is not None
    assert [file["path"] for file in changes["files"]] == ["script-output.txt"]
    # The next turn never inherits the previous turn's edited-file count.
    next_turn = chat._ChatTurnEdits(tmp_path, chat._capture_chat_project(tmp_path))
    assert next_turn.snapshots() == ({}, {})


def test_claude_tool_results_reuse_start_paths_and_do_not_include_other_files(tmp_path):
    (tmp_path / "own.txt").write_text("before\n")
    baseline = chat._capture_chat_project(tmp_path)
    edits = chat._ChatTurnEdits(tmp_path, baseline)
    edits.observe(
        {
            "id": "edit",
            "kind": "tool",
            "title": "Edit",
            "phase": "start",
            "input": json.dumps({"file_path": str(tmp_path / "own.txt")}),
        }
    )
    (tmp_path / "own.txt").write_text("after\n")
    (tmp_path / "other.txt").write_text("external\n")
    edits.observe({"id": "edit", "kind": "tool", "title": "Tool result", "phase": "result"})
    before, after = edits.snapshots()
    assert set(before) == set(after) == {"own.txt"}


def test_turn_edit_add_delete_recreate_keeps_original_absence(tmp_path):
    edits = chat._ChatTurnEdits(tmp_path, {})
    file = tmp_path / "new.txt"
    for index, content in enumerate(["first", None, "second"]):
        if content is None:
            file.unlink()
        else:
            file.write_text(content)
        edits.observe(
            {
                "id": str(index),
                "kind": "tool",
                "title": "Edit",
                "phase": "result",
                "input": json.dumps([{"path": "new.txt"}]),
            }
        )
    before, after = edits.snapshots()
    assert before == {}
    assert chat._chat_file_bytes(after["new.txt"]) == b"second"


def test_turn_edits_reject_incomplete_baseline(tmp_path):
    baseline = chat._ChatSnapshot()
    baseline.complete = False
    edits = chat._ChatTurnEdits(tmp_path, baseline)
    (tmp_path / "file.txt").write_text("changed")
    edits.observe(
        {
            "id": "edit",
            "kind": "tool",
            "title": "Edit",
            "phase": "result",
            "input": json.dumps([{"path": "file.txt"}]),
        }
    )
    assert edits.snapshots() == ({}, {})


def test_later_read_only_tool_does_not_claim_external_edit_to_previously_edited_file(tmp_path):
    file = tmp_path / "own.txt"
    file.write_text("original\n")
    edits = chat._ChatTurnEdits(tmp_path, chat._capture_chat_project(tmp_path))
    file.write_text("Rem\n")
    edits.observe(
        {
            "id": "edit",
            "kind": "tool",
            "title": "Edit",
            "phase": "result",
            "input": json.dumps([{"path": "own.txt"}]),
        }
    )
    file.write_text("external\n")
    trace = {"id": "read", "kind": "tool", "title": "bash", "category": "shell"}
    edits.observe({**trace, "phase": "start"})
    edits.observe({**trace, "phase": "result"})
    assert chat._chat_file_bytes(edits.snapshots()[1]["own.txt"]) == b"Rem\n"


def test_terminal_error_collects_partial_changes_from_unfinished_tool(tmp_path):
    file = tmp_path / "own.txt"
    file.write_text("original\n")
    edits = chat._ChatTurnEdits(tmp_path, chat._capture_chat_project(tmp_path))
    edits.observe(
        {"id": "shell", "kind": "tool", "title": "bash", "category": "shell", "phase": "start"}
    )
    file.write_text("partial\n")
    edits.finish()
    assert chat._chat_file_bytes(edits.snapshots()[1]["own.txt"]) == b"partial\n"


def test_reported_edit_outside_project_does_not_fall_back_to_whole_project(tmp_path):
    file = tmp_path / "own.txt"
    file.write_text("original\n")
    edits = chat._ChatTurnEdits(tmp_path, chat._capture_chat_project(tmp_path))
    file.write_text("external\n")
    edits.observe(
        {
            "id": "edit",
            "kind": "tool",
            "title": "Edit",
            "phase": "result",
            "input": json.dumps([{"path": str(tmp_path.parent / "elsewhere.txt")}]),
        }
    )
    assert edits.snapshots() == ({}, {})
