"""A search hit's passage matches the query; a title match raises its document, not its passages."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import second_brain
from gofer.ui.brain_library import BrainLibrary


@pytest.fixture
def library(tmp_path: Path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def build(library: BrainLibrary, tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "brain"
    root.mkdir()
    # Only the title says "blight"; none of the three passages does.
    (root / "title-only.md").write_text(
        "---\ntitle: Tomato blight log\n---\n"
        "## Planting\n\nSeedlings went out in May.\n\n"
        "## Watering\n\nWater at the base every morning.\n\n"
        "## Harvest\n\nThe first ripe fruit came in August.\n",
        encoding="utf-8",
    )
    # Passages say "blight"; the title doesn't.
    (root / "passage.md").write_text(
        "---\ntitle: Garden diary\n---\n"
        "## June\n\nEarly blight spots showed on the lower leaves.\n\n"
        "## July\n\nNothing new to report.\n",
        encoding="utf-8",
    )
    # Both the title and one passage say "blight".
    (root / "both.md").write_text(
        "---\ntitle: Blight treatments\n---\n"
        "## Copper\n\nCopper spray slows blight on wet days.\n\n"
        "## Pruning\n\nCut the lowest branches.\n",
        encoding="utf-8",
    )
    brain = library.open({"root": str(root)})
    deadline = time.monotonic() + 15
    while any(task["state"] == "running" for task in library.snapshot()["tasks"]):
        if time.monotonic() > deadline:
            pytest.fail("Second Brain tasks did not finish")
        time.sleep(0.02)
    return brain


def by_title(library: BrainLibrary, brain: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {doc["title"]: doc for doc in library.entities("documents", brain["id"])}


def test_title_only_document_returns_exactly_one_hit_at_its_first_passage(library, tmp_path):
    brain = build(library, tmp_path)
    docs = by_title(library, brain)
    title_only = docs["Tomato blight log"]
    hits = library.search({"brainId": brain["id"], "query": "blight"})["hits"]
    mine = [hit for hit in hits if hit["docId"] == title_only["id"]]
    assert len(mine) == 1
    assert mine[0]["blockId"] == title_only["blocks"][0]["id"]
    assert mine[0]["matchKind"] == "title"
    # Every other hit's own passage contains the word.
    blocks = {
        (doc["id"], block["id"]): block["text"] for doc in docs.values() for block in doc["blocks"]
    }
    for hit in hits:
        if hit["matchKind"] == "content":
            assert "blight" in blocks[(hit["docId"], hit["blockId"])].casefold()
    content = {(hit["docId"], hit["blockId"]) for hit in hits if hit["matchKind"] == "content"}
    assert len(content) == 2
    assert {doc_id for doc_id, _ in content} == {
        docs["Garden diary"]["id"],
        docs["Blight treatments"]["id"],
    }


def test_title_match_raises_its_document_without_adding_passages(library, tmp_path):
    brain = build(library, tmp_path)
    docs = by_title(library, brain)
    hits = library.search({"brainId": brain["id"], "query": "blight"})["hits"]
    order = list(dict.fromkeys(hit["docId"] for hit in hits))
    # Matched by title and a passage ranks first. The title weighs 5 times a match in
    # the text, so a title match alone outranks a single passage that has the word.
    assert order == [
        docs["Blight treatments"]["id"],
        docs["Tomato blight log"]["id"],
        docs["Garden diary"]["id"],
    ]
    # The title boosts its document's passages, not adds passages that don't match.
    assert [hit["docId"] for hit in hits].count(docs["Blight treatments"]["id"]) == 1


def test_rem_search_says_a_title_only_document_matched_by_title(library, tmp_path):
    brain = build(library, tmp_path)
    docs = by_title(library, brain)
    reply = second_brain.search_documents(
        library, [library.brain(brain["id"])], {"query": "blight"}, wrap=False
    )
    results = {result["documentId"]: result for result in reply["results"]}
    title_only = results[docs["Tomato blight log"]["id"]]
    assert title_only["matchedBy"] == ["title"]
    assert [passage["matchedBy"] for passage in title_only["passages"]] == [["title"]]
    assert results[docs["Garden diary"]["id"]]["matchedBy"] == ["content"]
