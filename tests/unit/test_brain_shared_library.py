"""Re-indexing keeps what another process wrote to the same library during the scan.

The desktop and Rem's Second Brain tools open one library database from two processes.
Here two BrainLibrary instances on one database stand in for them: the desktop's scan
pauses after it walked the folder (its progress callback), the tool side writes, and
then the desktop's scan publishes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.brain_library import BrainLibrary
from tests.helpers.brain_library import wait_idle


@pytest.fixture
def libraries(tmp_path: Path):
    desktop = BrainLibrary(tmp_path / "app", start=False)
    tool = BrainLibrary(tmp_path / "app", start=False, recover=False)
    yield desktop, tool
    tool.close()
    desktop.close()


def open_brain(desktop: BrainLibrary, root: Path) -> dict[str, Any]:
    brain = desktop.open({"root": str(root)})
    wait_idle(desktop)
    return desktop.brain(brain["id"])


def by_path(library: BrainLibrary, brain_id: str) -> dict[str, dict[str, Any]]:
    docs = library.entities("documents", brain_id)
    paths = [doc["path"] for doc in docs]
    assert len(paths) == len(set(paths)), f"two rows for one path: {sorted(paths)}"
    return {doc["path"]: doc for doc in docs}


def once(action: Callable[[], None]) -> Callable[[float], None]:
    """A scan progress callback that runs ``action`` the first time it's called."""
    ran: list[bool] = []

    def progress(_: float) -> None:
        if not ran:
            ran.append(True)
            action()

    return progress


def portable_ids(root: Path) -> set[str]:
    manifest = json.loads((root / ".raticode/brain.json").read_text(encoding="utf-8"))
    return {doc["id"] for doc in manifest["library"]["documents"]}


def test_tool_save_during_a_paused_desktop_scan_is_kept(libraries, tmp_path):
    desktop, tool = libraries
    root = tmp_path / "brain"
    (root / "Reports").mkdir(parents=True)
    (root / "notes.md").write_text("# Notes\n\nThe first note.\n", encoding="utf-8")
    brain = open_brain(desktop, root)
    notes_id = by_path(desktop, brain["id"])["notes.md"]["id"]
    # New files the desktop's scan lists when it walks the folder.
    (root / "draft.md").write_text("# Draft\n\nA draft about otters.\n", encoding="utf-8")
    (root / "Reports/both.md").write_text("# Both\n\nSeen by both scans.\n", encoding="utf-8")
    tool_ids: dict[str, str] = {}

    def save_from_tool() -> None:
        # save_note writes the file, then indexes it in the tool's own process.
        (root / "Reports/saved.md").write_text(
            "# Saved\n\nRem saved this about herons.\n", encoding="utf-8"
        )
        tool.scan(brain["id"])
        tool_ids.update({path: doc["id"] for path, doc in by_path(tool, brain["id"]).items()})

    desktop.scan(brain["id"], progress=once(save_from_tool))

    docs = by_path(desktop, brain["id"])
    assert set(docs) == {"notes.md", "draft.md", "Reports/both.md", "Reports/saved.md"}
    # The file saved after the desktop's walk keeps the row the tool published.
    assert docs["Reports/saved.md"]["id"] == tool_ids["Reports/saved.md"]
    # Files both scans found new keep the ID the tool published first.
    assert docs["draft.md"]["id"] == tool_ids["draft.md"]
    assert docs["Reports/both.md"]["id"] == tool_ids["Reports/both.md"]
    assert docs["notes.md"]["id"] == notes_id
    assert desktop.brain(brain["id"])["index"]["documents"] == 4
    hits = desktop.search({"brainId": brain["id"], "query": "herons"})["hits"]
    assert [hit["docId"] for hit in hits] == [tool_ids["Reports/saved.md"]]
    # brain.json was written with the published rows.
    assert portable_ids(root) == {doc["id"] for doc in docs.values()}


def test_document_edited_during_a_scan_keeps_the_newer_file_and_the_edits(libraries, tmp_path):
    desktop, tool = libraries
    root = tmp_path / "brain"
    root.mkdir()
    (root / "notes.md").write_text("# Notes\n\nFirst words about kestrels.\n", encoding="utf-8")
    (root / "other.md").write_text("# Other\n\nA newer report.\n", encoding="utf-8")
    brain = open_brain(desktop, root)
    docs = by_path(desktop, brain["id"])
    notes_id, other_id = docs["notes.md"]["id"], docs["other.md"]["id"]
    # The desktop's scan reads this version...
    (root / "notes.md").write_text(
        "# Notes\n\nSecond words about falcons, longer.\n", encoding="utf-8"
    )
    newest = "# Notes\n\nThird and last words about ospreys, the longest of all.\n"

    def edit_from_tool() -> None:
        # ...and while it extracts, the file changes again and the tool side indexes
        # that, retitles the document and marks it replaced.
        (root / "notes.md").write_text(newest, encoding="utf-8")
        tool.scan(brain["id"])
        tool.mutate({"kind": "documents", "id": notes_id, "item": {"title": "Edited title"}})
        tool.confirm_replacement(notes_id, other_id)

    desktop.scan(brain["id"], progress=once(edit_from_tool))

    doc = by_path(desktop, brain["id"])["notes.md"]
    assert doc["id"] == notes_id
    assert doc["version"] == hashlib.sha256(newest.encode()).hexdigest()
    assert "ospreys" in " ".join(block["text"] for block in doc["blocks"])
    assert doc["title"] == "Edited title"
    assert doc["replacedBy"]["docId"] == other_id
    hits = desktop.search({"brainId": brain["id"], "query": "ospreys"})["hits"]
    assert [hit["docId"] for hit in hits] == [notes_id]
    assert not desktop.search({"brainId": brain["id"], "query": "falcons"})["hits"]
    manifest = json.loads((root / ".raticode/brain.json").read_text(encoding="utf-8"))
    stored = next(d for d in manifest["library"]["documents"] if d["id"] == notes_id)
    assert stored["version"] == doc["version"]
    assert stored["replacedBy"]["docId"] == other_id


def test_single_field_edit_keeps_fields_another_process_published(libraries, tmp_path):
    desktop, tool = libraries
    root = tmp_path / "brain"
    root.mkdir()
    (root / "old.md").write_text("# Old\n\nThe old report.\n", encoding="utf-8")
    (root / "new.md").write_text("# New\n\nThe new report.\n", encoding="utf-8")
    brain = open_brain(desktop, root)
    docs = by_path(desktop, brain["id"])
    old_id, new_id = docs["old.md"]["id"], docs["new.md"]["id"]
    stale = desktop.entity("documents", old_id)
    # Another process re-indexes a changed file after this one read the record.
    (root / "old.md").write_text("# Old\n\nThe old report, revised.\n", encoding="utf-8")
    tool.scan(brain["id"])
    original_entity = desktop.entity
    desktop.entity = lambda kind, item_id: (  # type: ignore[method-assign]
        stale if item_id == old_id else original_entity(kind, item_id)
    )
    try:
        desktop.confirm_replacement(old_id, new_id)
    finally:
        desktop.entity = original_entity  # type: ignore[method-assign]
    doc = desktop.entity("documents", old_id)
    assert doc["replacedBy"]["docId"] == new_id
    assert "revised" in " ".join(block["text"] for block in doc["blocks"])
    assert doc["version"] != stale["version"]
