"""A note save_note wrote is reported as saved, even when the library is busy."""

from __future__ import annotations

import json
import sqlite3
import stat
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import brain_library, second_brain
from gofer.ui.brain_library import (
    PENDING_REPORTS,
    BrainLibrary,
    pending_reports,
    queue_pending_report,
)
from tests.helpers.brain_library import library as library  # noqa: F401 - fixture
from tests.helpers.brain_library import wait_idle

# Long enough for the library's own reads, short enough to keep the tests quick. The
# blocker below holds its write lock for longer than this, as a re-index would for 30 s.
TEST_BUSY_TIMEOUT = 0.4


def build(library: BrainLibrary, tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = tmp_path / "brain"
    root.mkdir()
    (root / "notes.md").write_text(
        "# Findings\n\nThe index preserves exact evidence.\n", encoding="utf-8"
    )
    brain = library.open({"root": str(root)})
    wait_idle(library)
    library.update_brain({"id": brain["id"], "patch": {"settings": {"allowCloud": True}}})
    doc = next(doc for doc in library.entities("documents") if doc["path"] == "notes.md")
    return library.brain(brain["id"]), doc


def note_arguments(doc: dict[str, Any], path: str = "Reports/busy.md") -> dict[str, Any]:
    return {
        "path": path,
        "content": "# Busy report\n\nThe index preserves exact evidence. [1]\n",
        "format": "md",
        "title": "Busy report",
        "evidence": [{"docId": doc["id"], "quote": "The index preserves exact evidence."}],
    }


def serve(tmp_path: Path, brain: dict[str, Any], monkeypatch, check) -> None:
    ran: list[bool] = []

    def fake(call, definitions, rules, name):
        check(call)
        ran.append(True)

    monkeypatch.setattr(second_brain, "serve_stdio", fake)
    second_brain.serve_registered_brains(
        tmp_path / "app", [brain["id"]], "md", "auto", {"threadId": "thread-1"}
    )
    assert ran == [True]


def hold_write_lock(library: BrainLibrary) -> sqlite3.Connection:
    """Another process's writer, holding the library's write lock until rollback."""
    blocker = sqlite3.connect(library.database, timeout=TEST_BUSY_TIMEOUT)
    blocker.execute("BEGIN IMMEDIATE")
    return blocker


def test_busy_library_saves_the_note_and_the_monitor_records_it(library, tmp_path, monkeypatch):
    monkeypatch.setattr(brain_library, "BUSY_TIMEOUT", TEST_BUSY_TIMEOUT)
    brain, doc = build(library, tmp_path)
    root = Path(brain["root"])

    def check(call):
        blocker = hold_write_lock(library)
        try:
            started = time.monotonic()
            saved = call("save_note", note_arguments(doc))
            # The save waited out the busy timeout on the record, then gave up on it.
            assert time.monotonic() - started >= TEST_BUSY_TIMEOUT
            assert saved["path"] == str(root / "Reports" / "busy.md")
            assert saved["link"] == f"[busy](<{root / 'Reports' / 'busy.md'}>)"
            assert Path(saved["path"]).read_text(encoding="utf-8").startswith("# Busy report")
            assert saved["indexed"] is False
            assert saved["warnings"] == [second_brain.LIBRARY_BUSY_WARNING]
            assert saved["warnings"][0] == (
                "Saved. The library was busy, so its citations will be recorded shortly. "
                "Don't save it again."
            )
            assert saved["evidence"][0]["status"] == "cited"
            assert library.entities("reports") == []

            # The record waits in an owner-only file under the library data folder.
            [(path, report)] = pending_reports(library.folder)
            assert path.parent == library.folder / PENDING_REPORTS
            if sys.platform != "win32":
                assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert report is not None
            assert report["artifactPath"] == "Reports/busy.md"
            assert report["citations"][0]["docId"] == doc["id"]
            assert report["provenance"]["threadId"] == "thread-1"

            # Saving again while it waits: the caller's own earlier save, not a clash.
            again = call("save_note", note_arguments(doc))
            assert again["alreadySaved"] is True and again["indexed"] is False
            assert again["path"] == saved["path"]
            assert "your own earlier save" in again["note"]
            assert "not a different file with the same name" in again["note"]
        finally:
            blocker.rollback()
            blocker.close()

        # One background pass after the library frees up stores the record.
        library._monitor_pass()
        wait_idle(library)
        assert pending_reports(library.folder) == []
        [report] = library.entities("reports", brain["id"])
        assert report["artifactPath"] == "Reports/busy.md"
        assert report["title"] == "Busy report"
        assert report["citations"][0]["docId"] == doc["id"]
        assert report["citations"][0]["quote"] == "The index preserves exact evidence."
        assert report["sourceVersions"] == [{"docId": doc["id"], "version": doc["version"]}]
        saved_doc = next(
            item for item in library.entities("documents") if item["path"] == "Reports/busy.md"
        )
        assert saved_doc["origin"] == "agent" and saved_doc["type"] == "report"
        assert report["outputHash"] == "sha256:" + saved_doc["version"]
        portable = json.loads((root / ".raticode" / "brain.json").read_text(encoding="utf-8"))
        assert [item["id"] for item in portable["library"]["reports"]] == [report["id"]]

        # A same-path retry now matches the stored record.
        again = call("save_note", note_arguments(doc))
        assert again["alreadySaved"] is True and again["indexed"] is True
        assert "your own earlier save" in again["note"]
        assert len(library.entities("reports")) == 1

    serve(tmp_path, brain, monkeypatch, check)


def test_a_different_file_at_the_path_is_still_a_name_clash(library, tmp_path, monkeypatch):
    brain, doc = build(library, tmp_path)
    (Path(brain["root"]) / "Reports").mkdir()
    (Path(brain["root"]) / "Reports" / "busy.md").write_text("# Someone else's\n")

    def check(call):
        with pytest.raises(ValueError, match="A report already exists at Reports/busy.md"):
            call("save_note", note_arguments(doc))

    serve(tmp_path, brain, monkeypatch, check)


def test_failed_checks_write_nothing(library, tmp_path, monkeypatch):
    brain, doc = build(library, tmp_path)

    def check(call):
        with pytest.raises(ValueError):
            call("save_note", {**note_arguments(doc), "format": "docx"})
        with pytest.raises(ValueError):
            call("save_note", {**note_arguments(doc, "Reports/busy.html")})
        assert not (Path(brain["root"]) / "Reports").exists()
        assert pending_reports(library.folder) == []

    serve(tmp_path, brain, monkeypatch, check)


def test_a_failed_index_after_the_record_is_stored_is_not_an_error(library, tmp_path, monkeypatch):
    brain, doc = build(library, tmp_path)

    def check(call):
        def busy(self, brain_id, *args):
            raise sqlite3.OperationalError("database is locked")

        with monkeypatch.context() as patch:
            patch.setattr(BrainLibrary, "scan", busy)
            saved = call("save_note", note_arguments(doc))
        assert Path(saved["path"]).is_file()
        assert saved["indexed"] is False
        assert saved["warnings"] == [second_brain.INDEX_BUSY_WARNING]
        # The record is stored, so nothing waits in the queue.
        assert library.entities("reports")[0]["citations"][0]["docId"] == doc["id"]
        assert pending_reports(library.folder) == []

    serve(tmp_path, brain, monkeypatch, check)


def test_a_successful_save_says_it_was_indexed(library, tmp_path, monkeypatch):
    brain, doc = build(library, tmp_path)

    def check(call):
        saved = call("save_note", note_arguments(doc))
        assert saved["indexed"] is True and "warnings" not in saved
        assert library.entities("reports")[0]["artifactPath"] == "Reports/busy.md"

    serve(tmp_path, brain, monkeypatch, check)


def test_queued_record_for_a_changed_or_deleted_file_is_dropped(library, tmp_path):
    brain, _ = build(library, tmp_path)
    root = Path(brain["root"])
    (root / "changed.md").write_text("# Changed later\n")
    for name in ("changed.md", "deleted.md"):
        queue_pending_report(
            library.folder,
            {
                "id": f"report-{name}",
                "brainId": brain["id"],
                "artifactPath": name,
                "outputHash": "sha256:" + "0" * 64,
                "citations": [],
            },
        )
    assert library.apply_pending_reports() == 0
    assert pending_reports(library.folder) == []
    assert library.entities("reports") == []


def test_tool_server_retries_a_busy_library_once(library, tmp_path, monkeypatch):
    monkeypatch.setattr(brain_library, "BUSY_TIMEOUT", TEST_BUSY_TIMEOUT)
    brain, _ = build(library, tmp_path)
    blocker = hold_write_lock(library)
    real_init = BrainLibrary.__init__
    attempts: list[str] = []

    def init(self, *args, **kwargs):
        try:
            real_init(self, *args, **kwargs)
        except sqlite3.OperationalError as exc:
            attempts.append(str(exc))
            # The writer finishes while the tool server waits for its second try.
            blocker.rollback()
            raise
        attempts.append("opened")

    monkeypatch.setattr(BrainLibrary, "__init__", init)
    try:
        serve(tmp_path, brain, monkeypatch, lambda call: call("rules", {}))
    finally:
        blocker.rollback()
        blocker.close()
    assert attempts == ["database is locked", "opened"]


def test_tool_server_does_not_retry_other_database_errors(tmp_path, monkeypatch):
    attempts: list[int] = []

    def broken(*args, **kwargs):
        attempts.append(1)
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(brain_library, "BrainLibrary", broken)
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        second_brain.serve_registered_brains(tmp_path / "app", ["brain"], "md", "auto")
    assert attempts == [1]
