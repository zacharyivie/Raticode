"""Rem's search shows a matching section's text, not its bare heading, marked matchedBy heading."""

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
    # Only the h2 says "mulching"; the title and the text under it don't.
    (root / "garden.html").write_text(
        "<html><head><title>Garden plan</title></head><body>"
        "<h1>Spring jobs</h1><p>Turn the beds over once the frost has gone for good.</p>"
        "<h2>Mulching</h2><p>Spread a thick layer of straw around every plant in June.</p>"
        "<p>Top it up again after the August storms.</p>"
        "<h2>Watering</h2><p>Water at the base of each plant every morning.</p>"
        "</body></html>",
        encoding="utf-8",
    )
    # An h2 with no text under it before the next heading.
    (root / "empty.html").write_text(
        "<html><head><title>Shed notes</title></head><body>"
        "<h2>Composting</h2>"
        "<h2>Tools</h2><p>Oil the shears and hang them on the left wall.</p>"
        "</body></html>",
        encoding="utf-8",
    )
    # The h2 and the text under it both say "pruning".
    (root / "both.html").write_text(
        "<html><head><title>Orchard log</title></head><body>"
        "<h2>Pruning</h2><p>Pruning the apple trees waits until late winter.</p>"
        "<h2>Picking</h2><p>The first apples were ready in September this year.</p>"
        "</body></html>",
        encoding="utf-8",
    )
    brain = library.open({"root": str(root)})
    deadline = time.monotonic() + 15
    while any(task["state"] == "running" for task in library.snapshot()["tasks"]):
        if time.monotonic() > deadline:
            pytest.fail("Second Brain tasks did not finish")
        time.sleep(0.02)
    return brain


def document(library: BrainLibrary, brain: dict[str, Any], title: str) -> dict[str, Any]:
    return next(doc for doc in library.entities("documents", brain["id"]) if doc["title"] == title)


def search(library: BrainLibrary, brain: dict[str, Any], query: str) -> dict[str, Any]:
    reply = second_brain.search_documents(
        library, [library.brain(brain["id"])], {"query": query}, wrap=False
    )
    return {result["title"]: result for result in reply["results"]}


def test_a_heading_only_match_lists_its_sections_first_text(library, tmp_path):
    brain = build(library, tmp_path)
    garden = document(library, brain, "Garden plan")
    blocks = garden["blocks"]
    heading = next(block for block in blocks if block["text"] == "Mulching")
    text = blocks[blocks.index(heading) + 1]
    assert text["heading"] == "Mulching" and "mulching" not in text["text"].casefold()
    # The library's own hit is still the heading, as Raticode brain search shows it.
    hits = library.search({"brainId": brain["id"], "query": "mulching"})["hits"]
    assert [(hit["blockId"], hit["matchKind"]) for hit in hits] == [(heading["id"], "content")]

    result = search(library, brain, "mulching")["Garden plan"]
    assert result["passages"] == [
        {
            "blockId": text["id"],
            "heading": "Mulching",
            "text": text["text"],
            "matchedBy": ["heading"],
        }
    ]
    assert "heading" in result["matchedBy"]


def test_a_heading_with_no_text_under_it_stays_listed_as_content(library, tmp_path):
    brain = build(library, tmp_path)
    shed = document(library, brain, "Shed notes")
    heading = next(block for block in shed["blocks"] if block["text"] == "Composting")
    result = search(library, brain, "composting")["Shed notes"]
    assert [(p["blockId"], p["text"], p["matchedBy"]) for p in result["passages"]] == [
        (heading["id"], "Composting", ["content"])
    ]
    assert result["matchedBy"] == ["content"]


def test_a_section_whose_text_also_matches_is_listed_once_as_content(library, tmp_path):
    brain = build(library, tmp_path)
    orchard = document(library, brain, "Orchard log")
    text = next(block for block in orchard["blocks"] if block["text"].startswith("Pruning the"))
    result = search(library, brain, "pruning")["Orchard log"]
    assert [(p["blockId"], p["matchedBy"]) for p in result["passages"]] == [
        (text["id"], ["content"])
    ]
    assert result["matchedBy"] == ["content"]


def test_the_text_reply_says_heading_and_the_passage_cites(library, tmp_path):
    brain = build(library, tmp_path)
    garden = document(library, brain, "Garden plan")
    text = next(block for block in garden["blocks"] if block["text"].startswith("Spread"))
    reply = second_brain.search_text(
        second_brain.search_documents(
            library, [library.brain(brain["id"])], {"query": "mulching"}, wrap=False
        ),
        listing=False,
    )
    # The text reply drops each passage's matchedBy, so the document's says heading.
    assert "matched by content, heading" in reply
    assert f"[{text['id']}] {text['text']}" in reply
