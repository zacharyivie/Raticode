"""An HTML document's title is its page title, never a diagram's <title> caption, and the
one-time re-read corrects titles stored by the older reader."""

from __future__ import annotations

import copy
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.brain_documents import HTML_READER, extract, html_reader_stale
from gofer.ui.brain_library import BrainLibrary

CHART = (
    '<svg viewBox="0 0 10 10"><title>Pie chart of passages</title>'
    "<g><title>Headed: 96%</title><circle r='4'/></g></svg>"
)
REPORT = f"""<!doctype html><html><head><title>Search report</title></head><body>
<h1>Make search worth reading</h1>{CHART}<p>Most passages had no heading.</p>
<svg><title>Bar chart</title></svg></body></html>"""


def title(html: str) -> str:
    return str(extract(html.encode(), ".html")["title"])


def test_report_with_svg_titles_keeps_its_head_title():
    assert title(REPORT) == "Search report"


def test_svg_title_before_the_head_title_never_wins():
    html = (
        "<html><svg><title>Logo</title></svg><head><title> Quarterly review </title>"
        "</head><body><title>A stray second title</title><p>Text.</p></body></html>"
    )
    # Later titles are ignored too.
    assert title(html) == "Quarterly review"


def test_math_title_is_not_the_page_title():
    html = "<math><title>x squared</title><mi>x</mi></math><title>Algebra notes</title>"
    assert title(html) == "Algebra notes"


def test_page_without_a_head_title_uses_its_first_h1():
    html = (
        f"<html><body>{CHART}<h2>Intro</h2><h1><img src='logo.png'></h1>"
        "<h1>Make <span>search</span>\n worth reading</h1><h1>Second h1</h1></body></html>"
    )
    # An h1 with no text is skipped; inline tags and line breaks read as single spaces.
    assert title(html) == "Make search worth reading"
    unclosed = "<body><h1>Open heading"
    assert title(unclosed) == "Open heading"


def test_og_title_comes_before_the_first_h1():
    html = (
        '<head><meta property="og:title" content="Shared title"><title> </title></head>'
        "<body><h1>Heading</h1></body>"
    )
    assert title(html) == "Shared title"
    assert title("<body><p>No title anywhere.</p></body>") == ""


def wait_task(library: BrainLibrary, task: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        receipt = library.task(task["id"])
        if receipt["state"] != "running":
            return receipt
        time.sleep(0.02)
    pytest.fail("Second Brain task did not finish")


@pytest.fixture
def library(tmp_path: Path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def report_record(brain_id: str, report_id: str, path: str, title: str) -> dict[str, Any]:
    return {
        "id": report_id,
        "brainId": brain_id,
        "title": title,
        "artifactPath": path,
        "sourceVersions": [],
        "claims": [],
        "citations": [],
    }


def test_rereading_html_corrects_stored_titles_from_the_file_only(library, tmp_path):
    root = tmp_path / "brain"
    (root / "Reports").mkdir(parents=True)
    (root / "Reports" / "search.html").write_text(REPORT, encoding="utf-8")
    (root / "Reports" / "chosen.html").write_text(REPORT, encoding="utf-8")
    brain = library.open({"root": str(root)})
    for task in library.snapshot()["tasks"]:
        assert wait_task(library, task)["state"] == "done"
    documents = {doc["path"]: doc for doc in library.entities("documents", brain["id"])}
    assert documents["Reports/search.html"]["title"] == "Search report"

    # The library as the reader before HTML_READER 3 left it: every <title> joined.
    old_title = "Search reportPie chart of passagesHeaded: 96%Bar chart"
    for doc in documents.values():
        stale = copy.deepcopy(doc)
        stale.update(reader=HTML_READER - 1, title=old_title)
        library.put("documents", stale)
        assert html_reader_stale(stale)
    # One record took its title from the file; the other's save call named it.
    library.put("reports", report_record(brain["id"], "r-file", "Reports/search.html", old_title))
    library.put("reports", report_record(brain["id"], "r-chosen", "Reports/chosen", "Chosen name"))
    assert library.needs_scan(brain["id"])

    library.scan(brain["id"])

    documents = {doc["path"]: doc for doc in library.entities("documents", brain["id"])}
    assert {doc["title"] for doc in documents.values()} == {"Search report"}
    assert all(doc["reader"] == HTML_READER for doc in documents.values())
    assert library.entity("reports", "r-file")["title"] == "Search report"
    assert library.entity("reports", "r-chosen")["title"] == "Chosen name"
    assert not library.needs_scan(brain["id"])
