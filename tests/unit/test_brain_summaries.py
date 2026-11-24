"""Every Second Brain document can carry a short summary that Rem sees first.

Savers write the summary into the file; indexing reads it back without a model call:
front matter summary/description, then the HTML meta description, then the first
paragraph under a summary heading. A summary is never a passage, so it can't be cited.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import second_brain
from gofer.ui.brain_documents import extract
from gofer.ui.brain_library import BrainLibrary
from gofer.ui.brain_summaries import (
    SUMMARY_MAX_CHARS,
    capped_summary,
    checked_summary,
    with_summary,
)
from gofer.ui.report_outputs import ReportTools, save_report

SECTION = "## Short answer\n\nThe section paragraph answers the question.\n"


def summary_of(text: str, suffix: str = ".md") -> tuple[str, str]:
    extracted = extract(text.encode("utf-8"), suffix)
    return extracted.get("summary", ""), extracted.get("summarySource", "")


# Extraction order


def test_front_matter_summary_comes_first_then_description_then_section() -> None:
    both = "---\ndescription: From description.\nsummary: From summary.\n---\n# T\n\n" + SECTION
    assert summary_of(both) == ("From summary.", "file")
    described = "---\ndescription: From description.\n---\n# T\n\n" + SECTION
    assert summary_of(described) == ("From description.", "file")
    assert summary_of("# T\n\n" + SECTION) == (
        "The section paragraph answers the question.",
        "section",
    )


def test_html_meta_description_comes_before_a_summary_section() -> None:
    page = (
        '<html><head><meta name="description" content="From the meta tag."></head>'
        "<body><h2>Summary</h2><p>From the section paragraph here.</p></body></html>"
    )
    assert summary_of(page, ".html") == ("From the meta tag.", "file")
    without = page.replace('<meta name="description" content="From the meta tag.">', "")
    assert summary_of(without, ".html") == ("From the section paragraph here.", "section")


@pytest.mark.parametrize(
    "heading", ["Short answer", "Summary", "TL;DR", "Abstract", "Key findings", "01 · Summary:"]
)
def test_each_summary_heading_names_a_section(heading: str) -> None:
    html = f"<html><body><h1>T</h1><h2>{heading}</h2><p>The answer paragraph.</p></body></html>"
    assert summary_of(html, ".html") == ("The answer paragraph.", "section")
    assert summary_of(f"# T\n\n## {heading}\n\nThe answer paragraph.\n") == (
        "The answer paragraph.",
        "section",
    )


def test_section_summary_uses_the_first_paragraph_and_joins_list_items() -> None:
    note = (
        "# T\n\n## Background\n\nNot this one.\n\n## Key findings\n\n- First finding.\n"
        "- Second finding.\n\nA later paragraph.\n\n## Summary\n\nToo late.\n"
    )
    assert summary_of(note) == ("First finding. Second finding.", "section")
    # A summary heading with no paragraph of its own doesn't take the next section's.
    empty = "# T\n\n## Summary\n\n## Details\n\nNot a summary.\n"
    assert summary_of(empty) == ("", "")


def test_a_document_without_any_of_them_has_no_summary() -> None:
    extracted = extract(b"# Plain\n\n## Results\n\nNothing named a summary.\n", ".md")
    assert "summary" not in extracted and "summarySource" not in extracted
    assert "summary" not in extract(b"<p>Just text.</p>", ".html")


# Length cap


def test_saver_refuses_a_summary_over_600_characters(tmp_path: Path) -> None:
    assert checked_summary("x" * SUMMARY_MAX_CHARS) == "x" * SUMMARY_MAX_CHARS
    with pytest.raises(ValueError, match="at most 600 characters; this one has 601"):
        checked_summary("x" * (SUMMARY_MAX_CHARS + 1))
    with pytest.raises(ValueError, match="at most 600"):
        save_report(tmp_path, "r.md", "# R\n", "md", "y" * 601)
    assert not (tmp_path / "r.md").exists()
    with pytest.raises(ValueError, match="plain text"):
        checked_summary(["not", "text"])


def test_a_long_summary_in_a_file_is_cut_to_600_characters_at_a_word() -> None:
    words = " ".join(["word"] * 400)
    text, source = summary_of(f'<meta name="description" content="{words}"><p>Body</p>', ".html")
    assert source == "file" and len(text) <= SUMMARY_MAX_CHARS
    assert text.endswith("word…")
    assert capped_summary("a  b\n\tc") == "a b c"


# HTML escaping and where savers write the summary


def test_html_summary_is_escaped_in_the_meta_tag_and_reads_back_as_written(
    tmp_path: Path,
) -> None:
    summary = "Use <script>alert(\"x\")</script> & 'quotes' > all"
    page = (
        "<!doctype html><html><head><title>R</title>"
        '<meta content="old" name=description></head><body><h1>R</h1></body></html>'
    )
    save_report(tmp_path, "r.html", page, "html", summary)
    saved = (tmp_path / "r.html").read_text(encoding="utf-8")
    assert "<script>" not in saved and "&lt;script&gt;" in saved and "&quot;x&quot;" in saved
    assert 'content="old"' not in saved
    assert len(re.findall(r'<meta name="description"', saved)) == 1
    assert summary_of(saved, ".html") == (summary, "file")


def test_summary_goes_into_a_head_when_the_html_has_none() -> None:
    assert with_summary("<html><body>x</body></html>", "S", markdown=False).startswith(
        '<html><head><meta name="description" content="S"></head>'
    )
    assert with_summary("<p>x</p>", "S", markdown=False).startswith(
        '<meta name="description" content="S">'
    )


def test_markdown_summary_goes_into_front_matter_and_replaces_an_old_one(tmp_path: Path) -> None:
    summary = 'Colons: "quotes", #hash and a\\backslash'
    note = "---\ntitle: R\nsummary: >\n  old folded\n  summary\ntags: [a]\n---\n# R\n\nBody.\n"
    save_report(tmp_path, "r.md", note, "md", summary)
    saved = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "old folded" not in saved and "tags: [a]" in saved
    extracted = extract(saved.encode(), ".md")
    assert (extracted["summary"], extracted["title"], extracted["tags"]) == (summary, "R", ["a"])
    save_report(tmp_path, "plain.md", "# P\n\nBody.\n", "md", "New summary.")
    assert (tmp_path / "plain.md").read_text().startswith('---\nsummary: "New summary."\n---\n')


def test_report_tools_pass_the_summary_and_offer_it_in_the_schema(tmp_path: Path) -> None:
    tools = ReportTools(tmp_path, "html")
    tools.call("save_report", {"path": "a.html", "content": "<p>A</p>", "summary": "Sum."})
    assert '<meta name="description" content="Sum.">' in (tmp_path / "a.html").read_text()
    definitions = second_brain.tool_definitions()
    save_note = next(item for item in definitions if item["name"] == "save_note")
    assert "at most 600" in save_note["inputSchema"]["properties"]["summary"]["description"]
    assert "summary" not in save_note["inputSchema"]["required"]


def test_local_second_brain_save_note_writes_the_summary(tmp_path: Path) -> None:
    brain = second_brain.SecondBrain(tmp_path, "md")
    brain.call("save_note", {"path": "Topic/n.md", "content": "# N\n", "summary": "Short."})
    assert (tmp_path / "Topic/n.md").read_text().startswith('---\nsummary: "Short."\n---')


# Library: stored beside the passages, returned to Rem, never cited


def wait_idle(library: BrainLibrary) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if all(task["state"] != "running" for task in library.snapshot()["tasks"]):
            return
        time.sleep(0.02)
    pytest.fail("Second Brain tasks did not finish")


@pytest.fixture
def library(tmp_path: Path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def build(library: BrainLibrary, tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "brain"
    (root / "Notes").mkdir(parents=True)
    (root / "Notes" / "front.md").write_text(
        "---\nsummary: Zephyrine kites fly best in autumn.\n---\n# Kites\n\n"
        "## Method\n\nWe flew kites every week of the year.\n",
        encoding="utf-8",
    )
    (root / "Notes" / "section.html").write_text(
        "<html><head><title>Boats</title></head><body><h1>Boats</h1>"
        "<h2>Short answer</h2><p>Boats float because of displacement.</p>"
        "<h2>Detail</h2><p>The kites topic is unrelated here.</p></body></html>",
        encoding="utf-8",
    )
    (root / "Notes" / "none.md").write_text("# None\n\nNo kites summary at all.\n", "utf-8")
    brain = library.open({"root": str(root)})
    wait_idle(library)
    library.update_brain({"id": brain["id"], "patch": {"settings": {"allowCloud": True}}})
    return brain


def doc_at(library: BrainLibrary, path: str) -> dict[str, Any]:
    return next(doc for doc in library.entities("documents") if doc["path"] == path)


def serve(tmp_path: Path, brain: dict[str, Any], monkeypatch, check) -> None:
    ran: list[bool] = []

    def fake(call, definitions, rules, name):
        check(call, definitions, rules)
        ran.append(True)

    monkeypatch.setattr(second_brain, "serve_stdio", fake)
    second_brain.serve_registered_brains(tmp_path / "app", [brain["id"]], "md", "auto")
    assert ran == [True]


def test_index_stores_summary_and_source_outside_the_passages(library, tmp_path) -> None:
    build(library, tmp_path)
    front = doc_at(library, "Notes/front.md")
    assert (front["summary"], front["summarySource"]) == (
        "Zephyrine kites fly best in autumn.",
        "file",
    )
    assert all("Zephyrine" not in block["text"] for block in front["blocks"])
    section = doc_at(library, "Notes/section.html")
    assert (section["summary"], section["summarySource"]) == (
        "Boats float because of displacement.",
        "section",
    )
    assert "summary" not in doc_at(library, "Notes/none.md")


def test_a_summary_cannot_be_cited_or_found_as_a_passage(library, tmp_path) -> None:
    brain = build(library, tmp_path)
    front = doc_at(library, "Notes/front.md")
    hits = library.search({"brainIds": [brain["id"]], "query": "Zephyrine", "mode": "exact"})
    assert hits["hits"] == []
    citations, marks = library.resolve_evidence(
        [{"docId": front["id"], "quote": "Zephyrine kites fly best in autumn."}],
        lambda brain_id: None,
    )
    assert citations == [] and marks[0]["status"] == "rejected"
    # The section a summary was taken from is still the document's own citable text.
    section = doc_at(library, "Notes/section.html")
    citations, _ = library.resolve_evidence(
        [{"docId": section["id"], "quote": "Boats float because of displacement."}],
        lambda brain_id: None,
    )
    assert len(citations) == 1


def test_rem_search_and_listing_return_the_summary_once_per_document(
    library, tmp_path, monkeypatch
) -> None:
    brain = build(library, tmp_path)

    def check(call, definitions, rules):
        full = call("rules", {})
        assert "summary" in full and "can't be cited" in full
        found = {
            item["path"]: item
            for item in call("search", {"format": "json", "query": "kites"})["results"]
        }
        assert found["Notes/front.md"]["summary"] == "Zephyrine kites fly best in autumn."
        assert found["Notes/section.html"]["summary"] == "Boats float because of displacement."
        assert "summary" not in found["Notes/none.md"]
        for item in found.values():
            assert all(item.get("summary", "") != p["text"] for p in item["passages"])
            assert all("summary" not in passage for passage in item["passages"])
        listed = {
            item["path"]: item
            for item in call("search", {"format": "json", "query": ""})["results"]
        }
        assert listed["Notes/front.md"]["summary"] == "Zephyrine kites fly best in autumn."
        assert "summary" not in listed["Notes/none.md"]
        read = next(item for item in definitions if item["name"] == "read_note")
        assert read["inputSchema"]["properties"]["summaryOnly"] == {"type": "boolean"}

    serve(tmp_path, brain, monkeypatch, check)


def test_read_note_summary_only_returns_an_outline(library, tmp_path, monkeypatch) -> None:
    brain = build(library, tmp_path)
    section = doc_at(library, "Notes/section.html")
    library.mutate(
        {"kind": "documents", "id": section["id"], "item": {**section, "checkBy": "2027-01-02"}}
    )

    def check(call, definitions, rules):
        outline = call("read_note", {"documentId": section["id"], "summaryOnly": True})
        assert outline["title"] == "Boats" and outline["status"] == "current"
        assert outline["checkBy"] == "2027-01-02"
        assert outline["summary"] == "Boats float because of displacement."
        assert outline["headings"] == ["Boats", "Short answer", "Detail"]
        assert outline["textLength"] == sum(len(b["text"]) for b in section["blocks"])
        assert "blocks" not in outline and outline["path"] == "Notes/section.html"
        assert outline["file"].endswith("/brain/Notes/section.html")
        none = doc_at(library, "Notes/none.md")
        assert call("read_note", {"documentId": none["id"], "summaryOnly": True})["summary"] == ""

    serve(tmp_path, brain, monkeypatch, check)


def test_save_note_writes_the_summary_and_indexing_reads_it_back(
    library, tmp_path, monkeypatch
) -> None:
    brain = build(library, tmp_path)

    def check(call, definitions, rules):
        save = next(item for item in definitions if item["name"] == "save_note")
        assert "summary" in save["inputSchema"]["properties"]
        call(
            "save_note",
            {"path": "Reports/r.md", "content": "# R\n\nBody text.\n", "summary": "R in brief."},
        )

    serve(tmp_path, brain, monkeypatch, check)
    saved = doc_at(BrainLibrary(tmp_path / "app", start=False), "Reports/r.md")
    assert (saved["summary"], saved["summarySource"]) == ("R in brief.", "file")


def test_edit_metadata_sets_a_summary_and_clearing_it_brings_back_the_files(
    library, tmp_path
) -> None:
    brain = build(library, tmp_path)
    front = doc_at(library, "Notes/front.md")

    def edit(**fields: Any) -> dict[str, Any]:
        library.mutate(
            {
                "kind": "documents",
                "id": front["id"],
                "item": {**doc_at(library, front["path"]), **fields},
            }
        )
        return doc_at(library, front["path"])

    edited = edit(summary="My own words.")
    assert (edited["summary"], edited["summarySource"]) == ("My own words.", "metadata")
    # An unrelated edit sends the summary it shows and changes nothing.
    assert edit(reading={"read": True, "starred": False, "reviewed": False})["summary"] == (
        "My own words."
    )
    library.scan(brain["id"])
    assert doc_at(library, front["path"])["summarySource"] == "metadata"
    with pytest.raises(ValueError, match="at most 600"):
        edit(summary="z" * 601)
    # Setting the file's own text again removes the edit, as clearing does.
    same = edit(summary="Zephyrine kites fly best in autumn.")
    assert same["summarySource"] == "file" and "summary" not in same["metadata"]
    edit(summary="My own words.")
    cleared = edit(summary="")
    assert (cleared["summary"], cleared["summarySource"]) == (
        "Zephyrine kites fly best in autumn.",
        "file",
    )
    assert "fileSummary" not in cleared


def test_documents_indexed_before_summaries_are_read_again_once(library, tmp_path) -> None:
    brain = build(library, tmp_path)
    front = doc_at(library, "Notes/front.md")
    old = {
        key: value
        for key, value in front.items()
        if key not in {"summary", "summarySource", "summaryReader"}
    }
    library.put("documents", old)
    library.scan(brain["id"])
    again = doc_at(library, "Notes/front.md")
    assert again["summary"] == "Zephyrine kites fly best in autumn."
    assert again["summaryReader"] == 1 and again["blocks"] == front["blocks"]


def test_a_long_summary_through_the_tool_server_names_its_length(tmp_path: Path) -> None:
    import io
    import json

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "save_note",
            "arguments": {"path": "n.md", "content": "# N\n", "summary": "w " * 400},
        },
    }
    output = io.StringIO()
    second_brain.serve_second_brain(
        tmp_path, "md", input_stream=io.StringIO(json.dumps(request) + "\n"), output_stream=output
    )
    reply = json.loads(output.getvalue())["result"]
    assert reply["isError"] is True
    assert "at most 600 characters; this one has 799" in reply["content"][0]["text"]
    assert not (tmp_path / "n.md").exists()
