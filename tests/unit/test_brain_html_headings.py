"""HTML passages keep their section heading, short fragments join the next passage, and
citations on a re-read document are found again by their quote."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.brain_documents import (
    HTML_READER,
    ReadableHTML,
    blocks_for,
    extract,
    html_reader_stale,
    html_text,
    validate_portable,
)
from gofer.ui.brain_library import BrainLibrary
from tests.helpers.brain_library import wait_task

REPORT = """<!doctype html><html><head><title>Search report</title>
<style>h2 { color: red }</style></head><body>
<header><p>Second Brain · research · 14 November 2026</p>
<h1>Make search worth reading</h1>
<p>Two proposals for what Rem gets back when it searches the brain.</p></header>
<section><h2><span class="num">01</span>Short answer</h2>
<div class="kpi"><div class="value">96%</div>
<div class="label">of HTML passages are headed Document in the current index.</div></div>
<div class="kpi"><div class="value">2,778</div><div class="label">tiny passages crowd out
real search results.</div></div></section>
<section><h2>What I
  found</h2><h3>HTML <svg><text>icon</text></svg>loses its headings</h3>
<p>ReadableHTML turns headings into paragraph breaks and nothing else.</p></section>
<h5>Not a section</h5><p>A level-five heading stays body text under the h3.</p>
<p>Done</p></body></html>"""


@pytest.fixture
def library(tmp_path: Path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def by_text(blocks: list[dict[str, Any]]) -> dict[str, str]:
    return {block["text"]: block["heading"] for block in blocks}


def test_html_headings_name_the_passages_after_them():
    value = extract(REPORT.encode(), ".html")
    assert value["reader"] == HTML_READER
    headings = by_text(value["blocks"])
    # Text before the first heading has no section.
    assert headings["Second Brain · research · 14 November 2026"] == "Document"
    assert headings["Make search worth reading"] == "Make search worth reading"
    assert (
        headings["Two proposals for what Rem gets back when it searches the brain."]
        == "Make search worth reading"
    )
    # Inline elements in a heading are separated, and its line breaks don't split it.
    assert headings["01 Short answer"] == "01 Short answer"
    assert headings["What I found"] == "What I found"
    # Hidden content (an svg icon) stays out of the heading.
    assert headings["HTML loses its headings"] == "HTML loses its headings"
    assert (
        headings["ReadableHTML turns headings into paragraph breaks and nothing else."]
        == "HTML loses its headings"
    )
    # h5 and h6 are not sections: a short one is a fragment like any other.
    assert (
        headings["Not a section\n\nA level-five heading stays body text under the h3."]
        == "HTML loses its headings"
    )


def test_short_html_fragments_join_the_next_passage_in_their_section():
    value = extract(REPORT.encode(), ".html")
    texts = [block["text"] for block in value["blocks"]]
    assert "96%\n\nof HTML passages are headed Document in the current index." in texts
    assert "2,778\n\ntiny passages crowd out\nreal search results." in texts
    assert "96%" not in texts and "2,778" not in texts
    # Headings are never joined, even when they are short.
    assert "Make search worth reading" in texts
    assert "What I found" in texts
    # A fragment at the end of the document stays a passage of its own.
    assert texts[-1] == "Done"
    # A fragment never joins across a heading.
    split = extract(
        b"<h2>One</h2><p>Last words</p><h2>Two</h2><p>The next section starts here.</p>",
        ".html",
    )["blocks"]
    assert [(block["heading"], block["text"]) for block in split] == [
        ("One", "One"),
        ("One", "Last words"),
        ("Two", "Two"),
        ("Two", "The next section starts here."),
    ]
    # Chains of fragments join until the passage is long enough.
    chained = extract(
        b"<h2>Board</h2><div>01</div><div>0</div><div>~77%</div><div>Status</div>"
        b"<p>The rest of the sentence is long enough on its own.</p>",
        ".html",
    )["blocks"]
    assert [block["text"] for block in chained] == [
        "Board",
        "01\n\n0\n\n~77%\n\nStatus\n\nThe rest of the sentence is long enough on its own.",
    ]
    assert {block["heading"] for block in chained} == {"Board"}


def test_html_passage_offsets_slice_the_normalized_text():
    parser = ReadableHTML()
    parser.feed(REPORT)
    text, headings = html_text(parser.parts)
    assert "" not in text
    blocks = blocks_for(text, headings)
    for block in blocks:
        assert text[block["start"] : block["end"]] == block["text"]
    assert sorted(headings.values()) == sorted(
        [
            "Make search worth reading",
            "01 Short answer",
            "What I found",
            "HTML loses its headings",
        ]
    )


def test_html_heading_without_an_end_tag_does_not_swallow_the_page():
    blocks = extract(
        b"<h2>Findings<p>The body text after a heading whose end tag is missing.</p>",
        ".html",
    )["blocks"]
    assert [(block["heading"], block["text"]) for block in blocks] == [
        ("Findings", "Findings"),
        ("Findings", "The body text after a heading whose end tag is missing."),
    ]
    # A private-use character in the page is not mistaken for a heading mark.
    marked = extract("<p>Not a heading, just text in a paragraph.</p>".encode(), ".html")
    assert [block["heading"] for block in marked["blocks"]] == ["Document"]


def test_markdown_passages_are_unchanged():
    text = "# Findings\n\nShort.\n\nThe index preserves exact evidence.\n\n## Next\n\nMore."
    blocks = blocks_for(text)
    assert [(block["heading"], block["text"]) for block in blocks] == [
        ("Findings", "# Findings"),
        ("Findings", "Short."),
        ("Findings", "The index preserves exact evidence."),
        ("Next", "## Next"),
        ("Next", "More."),
    ]
    assert "reader" not in extract(text.encode(), ".md")


def old_blocks(paragraphs: list[str]) -> list[dict[str, Any]]:
    """Passages the way the reader before HTML_READER 2 stored them: one per paragraph,
    all under "Document"."""
    blocks = blocks_for("\n\n".join(paragraphs))
    for block in blocks:
        block["heading"] = "Document"
    return blocks


def test_rereading_html_refinds_current_citations_and_keeps_pinned_versions(library, tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    html = (
        "<html><head><title>Findings</title></head><body><h2>Results</h2>"
        "<div>42%</div><div>of the passages had no section heading at all.</div>"
        "<p>Rewritten text that no longer quotes the old claim.</p></body></html>"
    )
    (root / "report.html").write_text(html, encoding="utf-8")
    brain = library.open({"root": str(root)})
    for task in library.snapshot()["tasks"]:
        assert wait_task(library, task)["state"] == "done"
    current = library.entities("documents", brain["id"])[0]
    assert current["reader"] == HTML_READER
    assert not library.needs_scan(brain["id"])

    # The library as an older Raticode left it: the same file and version, read the old
    # way, with an earlier version pinned by one citation.
    stale = copy.deepcopy(current)
    stale.pop("reader")
    stale["blocks"] = old_blocks(
        [
            "Results",
            "42%",
            "of the passages had no section heading at all.",
            "The old claim was here.",
        ]
    )
    earlier = {"b-old-1": "An earlier version said something else entirely."}
    stale["versions"] = [{"hash": "0" * 64, "at": "2026-11-01", "note": "", "blocks": earlier}]
    library.put("documents", stale)
    assert html_reader_stale(stale)
    assert library.needs_scan(brain["id"])
    block = {item["text"]: item["id"] for item in stale["blocks"]}
    library.mutate(
        {
            "kind": "citations",
            "id": "c-current",
            "item": {
                "id": "c-current",
                "brainId": brain["id"],
                "docId": stale["id"],
                "version": stale["version"],
                "blockId": block["of the passages had no section heading at all."],
                "quote": "no section heading",
            },
        }
    )
    library.mutate(
        {
            "kind": "citations",
            "id": "c-pinned",
            "item": {
                "id": "c-pinned",
                "brainId": brain["id"],
                "docId": stale["id"],
                "version": "0" * 64,
                "blockId": "b-old-1",
                "quote": "something else",
            },
        }
    )
    lost_id = block["The old claim was here."]
    report = {
        "id": "r1",
        "brainId": brain["id"],
        "artifactPath": "Reports/r1.md",
        "sourceVersions": [{"docId": stale["id"], "version": stale["version"]}],
        "claims": [],
        "citations": [
            {
                "id": "rc1",
                "docId": stale["id"],
                "version": stale["version"],
                "blockId": block["42%"],
                "quote": "42%",
                "state": "found",
            },
            {
                "id": "rc2",
                "docId": stale["id"],
                "version": stale["version"],
                "blockId": lost_id,
                "quote": "The old claim",
                "state": "found",
            },
        ],
    }
    library.put("reports", report)
    collection = {
        "id": "col1",
        "brainId": brain["id"],
        "name": "Evidence",
        "sections": [
            {
                "id": "s1",
                "items": [
                    {
                        "docId": stale["id"],
                        "version": stale["version"],
                        "blockId": block["Results"],
                        "quote": "Results",
                    }
                ],
            }
        ],
    }
    library.put("collections", collection)

    library.scan(brain["id"])

    doc = library.entity("documents", stale["id"])
    assert doc["version"] == stale["version"]
    assert doc["reader"] == HTML_READER
    assert not library.needs_scan(brain["id"])
    new = {item["id"]: item for item in doc["blocks"]}
    merged = next(item for item in doc["blocks"] if item["text"].startswith("42%"))
    assert merged["heading"] == "Results"

    cited = library.entity("citations", "c-current")
    assert cited["blockId"] == merged["id"]
    assert cited["quote"] == "no section heading"
    assert cited["snapshot"] == merged["text"]
    assert merged["text"][cited["selector"]["start"] : cited["selector"]["end"]] == (
        "no section heading"
    )
    # Pinned past versions keep their old passages.
    pinned = library.entity("citations", "c-pinned")
    assert pinned["blockId"] == "b-old-1"
    assert doc["versions"][0]["blocks"] == earlier

    stored = library.entity("reports", "r1")["citations"]
    assert stored[0]["blockId"] == merged["id"]
    assert stored[0]["state"] == "found"
    # A quote that is gone keeps the old passage text and waits for relocation.
    assert stored[1]["blockId"] == lost_id
    assert stored[1]["state"] == "relocate"
    assert stored[1]["snapshot"] == "The old claim was here."
    item = library.entity("collections", "col1")["sections"][0]["items"][0]
    assert new[item["blockId"]]["text"] == "Results"

    # The portable copy checks out: every reference names a passage that exists, or
    # carries a snapshot for review.
    portable = json.loads((root / ".raticode/brain.json").read_text())
    validate_portable(portable)

    # Reading again doesn't move anything.
    before = library.entity("citations", "c-current")
    library.scan(brain["id"])
    assert library.entity("citations", "c-current") == before


def test_changed_html_file_keeps_the_old_passages_as_a_pinned_version(library, tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    path = root / "report.html"
    path.write_text("<h2>Results</h2><p>The first draft said one thing.</p>", encoding="utf-8")
    brain = library.open({"root": str(root)})
    for task in library.snapshot()["tasks"]:
        assert wait_task(library, task)["state"] == "done"
    doc = library.entities("documents", brain["id"])[0]
    stale = {**doc, "blocks": old_blocks(["Results", "The first draft said one thing."])}
    stale.pop("reader")
    library.put("documents", stale)
    path.write_text("<h2>Results</h2><p>The second draft says another.</p>", encoding="utf-8")
    library.scan(brain["id"])
    updated = library.entity("documents", doc["id"])
    assert updated["version"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert updated["versions"][0]["hash"] == doc["version"]
    assert updated["versions"][0]["blocks"] == {
        block["id"]: block["text"] for block in stale["blocks"]
    }
