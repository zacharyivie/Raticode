"""Search ranks unused conversation archive files after everything else."""

from __future__ import annotations

import json
import threading
import zipfile
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import second_brain
from gofer.ui.brain_library import BrainLibrary
from tests.helpers.brain_library import wait_idle

ARCHIVE = "Rem Conversations"
# The archive attachment says the query words more often, so on words alone it outranks
# the report. Only the archive rule can put it last. Neither title has the query words,
# since a title match would raise its document.
ATTACHMENT = "# Pasted log\n\n" + "Quarterly harbor survey harbor survey harbor survey.\n" * 3
REPORT = (
    "<html><head><title>Port plan</title></head><body><h1>Port plan</h1>"
    "<p>The harbor survey found two risks.</p></body></html>"
)


@pytest.fixture
def library(tmp_path: Path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def make_brain(library: BrainLibrary, tmp_path: Path, *, archive: bool = True) -> dict[str, Any]:
    root = tmp_path / "brain"
    (root / "Reports").mkdir(parents=True)
    (root / "Reports" / "harbor.html").write_text(REPORT, encoding="utf-8")
    folder = root / ARCHIVE
    (folder / "attachments").mkdir(parents=True)
    (folder / "attachments" / "log.md").write_text(ATTACHMENT, encoding="utf-8")
    if archive:
        (folder / "README.raticode.md").write_text("# Rem conversation archive\n")
        (folder / "index.json").write_text(json.dumps({"threads": []}))
    brain = library.open({"root": str(root)})
    wait_idle(library)
    library.update_brain({"id": brain["id"], "patch": {"settings": {"allowCloud": True}}})
    return brain


def doc_at(library: BrainLibrary, brain: dict[str, Any], path: str) -> dict[str, Any]:
    return next(doc for doc in library.entities("documents", brain["id"]) if doc["path"] == path)


def doc_order(hits: list[dict[str, Any]], key: str = "docId") -> list[str]:
    return list(dict.fromkeys(hit[key] for hit in hits))


def rem_calls(monkeypatch, tmp_path: Path, brain: dict[str, Any], check) -> None:
    """Run ``check`` against Rem's Second Brain tools for this brain."""
    ran = []

    def serve(call, definitions, rules, name):
        check(call, rules)
        ran.append(True)

    monkeypatch.setattr(second_brain, "serve_stdio", serve)
    second_brain.serve_registered_brains(tmp_path / "app", [brain["id"]], "html", "auto")
    assert ran


def cite(library: BrainLibrary, brain: dict[str, Any], doc: dict[str, Any], how: str) -> None:
    reference = {
        "docId": doc["id"],
        "version": doc["version"],
        "blockId": doc["blocks"][-1]["id"],
        "quote": "harbor survey",
    }
    if how == "citation":
        library.mutate(
            {
                "kind": "citations",
                "id": "c1",
                "item": {"id": "c1", "brainId": brain["id"], **reference},
            }
        )
    else:
        collection = {
            "id": "col1",
            "brainId": brain["id"],
            "name": "Harbor",
            "brief": "",
            "sections": [{"id": "s1", "title": "Evidence", "items": [{"id": "i1", **reference}]}],
        }
        library.mutate({"kind": "collections", "id": "col1", "item": collection})


def test_unused_archive_attachment_ranks_below_a_matching_report(library, tmp_path, monkeypatch):
    brain = make_brain(library, tmp_path)
    report = doc_at(library, brain, "Reports/harbor.html")
    attachment = doc_at(library, brain, f"{ARCHIVE}/attachments/log.md")

    hits = library.search({"brainId": brain["id"], "query": "harbor survey"})["hits"]
    assert doc_order(hits) == [report["id"], attachment["id"]]
    archived = [hit for hit in hits if hit["docId"] == attachment["id"]]
    current = [hit for hit in hits if hit["docId"] == report["id"]]
    # Its words alone score higher; the archive rule is what moves it down.
    assert max(hit["score"] for hit in archived) > max(hit["score"] for hit in current)
    assert {hit.get("source") for hit in archived} == {"conversation archive"}
    assert all("source" not in hit for hit in current)

    def check(call, rules):
        assert "source conversation archive" in call("rules", {})
        results = call("search", {"format": "json", "query": "harbor survey", "limit": 50})[
            "results"
        ]
        assert doc_order(results, "documentId") == [report["id"], attachment["id"]]
        assert results[-1]["source"] == "conversation archive"
        assert "source" not in results[0]
        # The listing puts the report first and every unused archive file (the
        # attachment and the archive's own index files) after it.
        listing = call("search", {"format": "json", "query": "", "limit": 50})["results"]
        marked = [item["path"].startswith(f"{ARCHIVE}/") for item in listing]
        assert marked == sorted(marked) and marked[0] is False
        assert attachment["id"] in [item["documentId"] for item in listing if item.get("source")]
        assert all(
            (item.get("source") == "conversation archive") == is_archive
            for item, is_archive in zip(listing, marked, strict=True)
        )
        # Reading it by ID still works.
        note = call("read_note", {"documentId": attachment["id"]})
        assert note["documentId"] == attachment["id"]

    rem_calls(monkeypatch, tmp_path, brain, check)
    # Nothing leaves the index.
    assert not library.entity("documents", attachment["id"]).get("missing")


@pytest.mark.parametrize("how", ["citation", "collection"])
def test_archive_file_that_evidence_uses_keeps_its_rank(library, tmp_path, monkeypatch, how):
    brain = make_brain(library, tmp_path)
    report = doc_at(library, brain, "Reports/harbor.html")
    attachment = doc_at(library, brain, f"{ARCHIVE}/attachments/log.md")
    cite(library, brain, attachment, how)

    hits = library.search({"brainId": brain["id"], "query": "harbor survey"})["hits"]
    assert doc_order(hits) == [attachment["id"], report["id"]]
    assert all("source" not in hit for hit in hits)

    def check(call, rules):
        results = call("search", {"format": "json", "query": "harbor survey", "limit": 50})[
            "results"
        ]
        assert doc_order(results, "documentId") == [attachment["id"], report["id"]]
        assert all("source" not in item for item in results)
        listing = {
            item["documentId"]: item
            for item in call("search", {"format": "json", "query": "", "limit": 50})["results"]
        }
        assert "source" not in listing[attachment["id"]]
        assert "source" not in listing[report["id"]]
        note = call("read_note", {"documentId": attachment["id"]})
        assert note["documentId"] == attachment["id"]
        assert note["version"] == attachment["version"]
        assert "harbor survey" in note["text"]

    rem_calls(monkeypatch, tmp_path, brain, check)


def test_export_without_the_archive_still_includes_the_cited_file(library, tmp_path):
    brain = make_brain(library, tmp_path)
    attachment = doc_at(library, brain, f"{ARCHIVE}/attachments/log.md")
    loose = Path(brain["root"]) / ARCHIVE / "attachments" / "unused.md"
    loose.write_text("# Unused\n\nNothing cites this harbor note.\n", encoding="utf-8")
    library.scan(brain["id"])
    unused = doc_at(library, brain, f"{ARCHIVE}/attachments/unused.md")
    cite(library, brain, attachment, "citation")

    hits = library.search({"brainId": brain["id"], "query": "harbor"})["hits"]
    assert doc_order(hits)[-1] == unused["id"]
    assert all(("source" in hit) == (hit["docId"] == unused["id"]) for hit in hits), (
        "only the unused archive file is marked"
    )

    target = tmp_path / "without.zip"
    result = library.export_package(
        {"brainId": brain["id"], "destination": str(target), "conversations": False},
        threading.Event(),
        lambda _: None,
    )
    assert result["verified"]
    with zipfile.ZipFile(target) as package:
        names = set(package.namelist())
    assert f"files/{ARCHIVE}/attachments/log.md" in names
    assert f"files/{ARCHIVE}/attachments/unused.md" not in names
    assert "files/Reports/harbor.html" in names


def test_brain_without_an_archive_ranks_as_before(library, tmp_path, monkeypatch):
    # The same folder without the archive marker files is ordinary notes.
    brain = make_brain(library, tmp_path, archive=False)
    report = doc_at(library, brain, "Reports/harbor.html")
    attachment = doc_at(library, brain, f"{ARCHIVE}/attachments/log.md")
    assert library.unused_archive_documents([brain["id"]], library.entities("documents")) == set()

    hits = library.search({"brainId": brain["id"], "query": "harbor survey"})["hits"]
    assert [hit["score"] for hit in hits] == sorted((hit["score"] for hit in hits), reverse=True)
    assert doc_order(hits) == [attachment["id"], report["id"]]
    assert all("source" not in hit for hit in hits)

    documents = library.entities("documents", brain["id"])

    def check(call, rules):
        results = call("search", {"format": "json", "query": "harbor survey", "limit": 50})[
            "results"
        ]
        assert doc_order(results, "documentId") == [attachment["id"], report["id"]]
        listing = call("search", {"format": "json", "query": "", "limit": 50})["results"]
        assert [item["documentId"] for item in listing] == [doc["id"] for doc in documents]
        assert all("source" not in item for item in [*results, *listing])

    rem_calls(monkeypatch, tmp_path, brain, check)


def test_rem_search_sees_new_evidence_on_an_archive_file(library, tmp_path, monkeypatch):
    """The remembered archive ranking ends when the app adds a citation."""
    brain = make_brain(library, tmp_path)
    report = doc_at(library, brain, "Reports/harbor.html")
    attachment = doc_at(library, brain, f"{ARCHIVE}/attachments/log.md")

    def check(call, rules):
        before = call("search", {"format": "json", "query": "harbor survey"})["results"]
        assert doc_order(before, "documentId") == [report["id"], attachment["id"]]
        assert before[-1]["source"] == "conversation archive"
        # The app's library (another connection) cites the archive file.
        cite(library, brain, attachment, "citation")
        after = call("search", {"format": "json", "query": "harbor survey"})["results"]
        assert doc_order(after, "documentId") == [attachment["id"], report["id"]]
        assert all("source" not in item for item in after)

    rem_calls(monkeypatch, tmp_path, brain, check)
