"""A re-index writes only the documents that changed.

Publishing a scan compares each document's encoded row and search passages with what
is stored and writes only the differences, so a scan that found nothing new writes no
rows, moves no revision and leaves brain.json alone. The idle refresh check reuses the
stored fingerprints until the library revision moves.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import brain_library
from gofer.ui.brain_library import BrainLibrary, Published, search_passages
from tests.helpers.brain_library import library as library  # noqa: F401 - fixture
from tests.helpers.brain_library import wait_idle


@pytest.fixture
def published(monkeypatch) -> list[Published]:
    """Every publish result, in order."""
    results: list[Published] = []
    original = brain_library.publish_documents

    def capture(*args: Any, **kwargs: Any) -> Published:
        result = original(*args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(brain_library, "publish_documents", capture)
    return results


def opened(library: BrainLibrary, tmp_path: Path) -> tuple[dict[str, Any], Path]:
    root = tmp_path / "brain"
    root.mkdir()
    (root / "notes.md").write_text(
        "# Notes\n\nThe river plan is in [the plan](plan.md) and [later](later.md).\n",
        encoding="utf-8",
    )
    (root / "plan.md").write_text("# Plan\n\nShip the river crossing.\n", encoding="utf-8")
    (root / "other.md").write_text("# Other\n\nA lake, not a river.\n", encoding="utf-8")
    brain = library.open({"root": str(root)})
    wait_idle(library)
    return brain, root


def ids(library: BrainLibrary, brain_id: str) -> dict[str, str]:
    return {doc["path"]: doc["id"] for doc in library.entities("documents", brain_id)}


def stored_rows(library: BrainLibrary, brain_id: str) -> tuple[list[Any], list[Any]]:
    """Document rows and passage rows with their rowids, as stored."""
    with library.connect() as db:
        documents = db.execute(
            "SELECT rowid,id,body FROM entities WHERE kind='documents' AND brain=? ORDER BY rowid",
            (brain_id,),
        ).fetchall()
        passages = db.execute(
            "SELECT rowid,doc,block,title,content FROM passages WHERE brain=? ORDER BY rowid",
            (brain_id,),
        ).fetchall()
    return [tuple(row) for row in documents], [tuple(row) for row in passages]


# Takes one path and makes sure the scan fingerprint moves, unlike the shared write.
def write(path: Path, text: str) -> None:
    """Write a file so its scan fingerprint surely moves."""
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(text, encoding="utf-8")
    if path.stat().st_mtime_ns == before:
        time.sleep(0.01)
        path.write_text(text, encoding="utf-8")


def test_an_unchanged_scan_writes_nothing(library, tmp_path, published, monkeypatch):
    brain, root = opened(library, tmp_path)
    rows = stored_rows(library, brain["id"])
    revision = library.revision()
    generation = library.brain(brain["id"])["index"]["generation"]
    portable = root / ".raticode" / "brain.json"
    portable_stamp = portable.stat().st_mtime_ns

    def no_citations(_brain_id: str) -> None:
        raise AssertionError("citations were resolved after a scan that wrote nothing")

    monkeypatch.setattr(library, "_resolve_citations", no_citations)
    result = library.scan(brain["id"])

    assert result == {"documents": 3, "added": 0, "changed": 0}
    assert published[-1] == Published([], [], [])
    assert stored_rows(library, brain["id"]) == rows
    assert library.revision() == revision
    assert library.brain(brain["id"])["index"]["generation"] == generation
    assert portable.stat().st_mtime_ns == portable_stamp


def test_an_unchanged_scan_still_marks_a_failed_index_ready(library, tmp_path, published):
    brain, _ = opened(library, tmp_path)
    stored = library.brain(brain["id"])
    generation = stored["index"]["generation"]
    stored["index"].update(state="failed", error="Disk was away")
    library.put_brain(stored)

    library.scan(brain["id"])

    assert not published[-1]
    index = library.brain(brain["id"])["index"]
    assert index["state"] == "ready" and "error" not in index
    assert index["generation"] == generation


def test_an_edited_file_rewrites_it_and_documents_whose_cites_changed(library, tmp_path, published):
    brain, root = opened(library, tmp_path)
    doc = ids(library, brain["id"])
    generation = library.brain(brain["id"])["index"]["generation"]

    write(root / "plan.md", "# Plan\n\nShip the river crossing by boat.\n")
    library.scan(brain["id"])

    assert published[-1] == Published([doc["plan.md"]], [], [doc["plan.md"]])
    assert library.brain(brain["id"])["index"]["generation"] == generation + 1
    hits = library.search({"brainId": brain["id"], "query": "boat"})["hits"]
    assert [hit["docId"] for hit in hits] == [doc["plan.md"]]

    # A new file: the note that links to it now cites it, so its row changes too,
    # but its passages (same blocks, same title) stay as stored.
    write(root / "later.md", "# Later\n\nAfter the river.\n")
    library.scan(brain["id"])

    later = ids(library, brain["id"])["later.md"]
    assert sorted(published[-1].written) == sorted([doc["notes.md"], later])
    assert published[-1].passages == [later]
    assert published[-1].deleted == []
    notes = library.entity("documents", doc["notes.md"])
    assert sorted(notes["cites"]) == sorted([doc["plan.md"], later])


def test_a_deleted_file_is_removed(library, tmp_path, published):
    brain, root = opened(library, tmp_path)
    doc = ids(library, brain["id"])

    (root / "other.md").unlink()
    library.scan(brain["id"])

    assert published[-1] == Published([], [doc["other.md"]], [doc["other.md"]])
    assert "other.md" not in ids(library, brain["id"])
    _, passages = stored_rows(library, brain["id"])
    assert all(row[1] != doc["other.md"] for row in passages)
    assert not library.search({"brainId": brain["id"], "query": "lake"})["hits"]


def test_passages_follow_a_document_changed_outside_the_scan(library, tmp_path, published):
    """Extract again and OCR store new blocks themselves and leave passages to the scan."""
    brain, _ = opened(library, tmp_path)
    doc = library.entity("documents", ids(library, brain["id"])["other.md"])
    doc["blocks"] = [{**doc["blocks"][0], "id": "b-ocr-1", "text": "Recognised harbour text"}]
    library.put("documents", doc)

    library.scan(brain["id"])

    assert published[-1] == Published([], [], [doc["id"]])
    hits = library.search({"brainId": brain["id"], "query": "harbour"})["hits"]
    assert [(hit["docId"], hit["blockId"]) for hit in hits] == [(doc["id"], "b-ocr-1")]


def test_search_hits_match_a_full_rebuild(library, tmp_path):
    brain, root = opened(library, tmp_path)
    write(root / "plan.md", "# Plan\n\nShip the river crossing by boat.\n\nThen the lake.\n")
    write(root / "later.md", "# Later\n\nAfter the river, the lake and the boat.\n")
    (root / "other.md").unlink()
    write(root / "extra.md", "# Extra\n\nA river note about [the plan](plan.md).\n")
    library.scan(brain["id"])
    queries = ["river", "lake", "boat", "plan", "notes", "crossing OR extra"]

    def hits() -> list[list[tuple[str, str, float]]]:
        return [
            [
                (hit["docId"], hit["blockId"], round(hit["score"], 9))
                for hit in library.search({"brainId": brain["id"], "query": query})["hits"]
            ]
            for query in queries
        ]

    incremental = hits()
    assert all(incremental)
    # What the scan used to publish: every passage of the brain inserted again.
    with library.connect() as db:
        db.execute("DELETE FROM passages WHERE brain=?", (brain["id"],))
        for doc in library.entities("documents", brain["id"]):
            db.executemany(
                "INSERT INTO passages VALUES(?,?,?,?,?)",
                [(brain["id"], doc["id"], *passage) for passage in search_passages(doc)],
            )
    assert hits() == incremental


def test_the_idle_check_reuses_fingerprints_until_the_revision_moves(
    library, tmp_path, monkeypatch
):
    brain, root = opened(library, tmp_path)
    decoded: list[str] = []
    entities: Callable[[str, str | None], list[dict[str, Any]]] = library.entities

    def counting(kind: str, brain_id: str | None = None) -> list[dict[str, Any]]:
        if kind == "documents":
            decoded.append(kind)
        return entities(kind, brain_id)

    monkeypatch.setattr(library, "entities", counting)
    assert not library.needs_scan(brain["id"])
    assert not library.needs_scan(brain["id"])
    assert len(decoded) == 1

    # A file edit is found by the folder walk alone.
    write(root / "plan.md", "# Plan\n\nChanged.\n")
    assert library.needs_scan(brain["id"])
    assert len(decoded) == 1

    library.scan(brain["id"])
    decoded.clear()
    assert not library.needs_scan(brain["id"])
    assert len(decoded) == 1
