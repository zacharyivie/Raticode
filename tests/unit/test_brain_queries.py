"""Rem's Second Brain search reads the Library's query syntax."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import second_brain
from gofer.ui.brain_library import BrainLibrary
from gofer.ui.brain_queries import (
    QUERY_FIELDS,
    QuerySyntaxError,
    parse_query,
    saved_matches,
    semantic_text,
)
from tests.helpers.brain_library import wait_idle

TABLE = json.loads(
    (Path(__file__).parents[1] / "fixtures/brain-query-syntax.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", TABLE["cases"], ids=[case["name"] for case in TABLE["cases"]])
def test_python_parser_matches_the_shared_table(case):
    # frontend/src/lib/secondBrain.test.mjs runs the same table through parseQuery.
    assert parse_query(case["query"]) == case["expected"]


def test_shared_table_names_every_field():
    queried = " ".join(case["query"].casefold() for case in TABLE["cases"])
    assert all(f"{field}:" in queried for field in QUERY_FIELDS)


def test_semantic_text_keeps_words_and_phrases_as_typed():
    assert semantic_text('Event "Append only" -draft tag:x OR type:note') == "Event Append only"


NOTES = {
    "Projects/log.md": (
        "---\ntitle: Event log\nauthor: Ada Lovelace\ntags: [architecture]\n"
        "project: Raticode\n---\nThe event log is append only and replayed.\n",
        "2026-11-10",
    ),
    "Projects/draft.md": (
        "---\ntitle: Event log draft\ntags: [architecture]\n---\n"
        "The event log draft is append only.\n",
        "2026-11-12",
    ),
    "Archive/old.md": (
        "---\ntitle: Old event log\ntags: [history]\n---\n"
        "The first event log was not append only.\n",
        "2026-10-01",
    ),
    "Reports/summary.html": (
        "<html><head><title>Summary</title></head><body>"
        "<p>Event log summary: only one append.</p></body></html>",
        "2026-11-13",
    ),
}


@pytest.fixture
def library(tmp_path: Path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


@pytest.fixture
def brain(library: BrainLibrary, tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "brain"
    for relative, (text, day) in NOTES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        stamp = datetime.fromisoformat(f"{day}T12:00:00+00:00").timestamp()
        os.utime(path, (stamp, stamp))
    opened = library.open({"root": str(root), "name": "Research notes"})
    wait_idle(library)
    library.update_brain({"id": opened["id"], "patch": {"settings": {"allowCloud": True}}})
    return library.brain(opened["id"])


def ids(library: BrainLibrary, brain: dict[str, Any]) -> dict[str, str]:
    return {doc["path"]: doc["id"] for doc in library.entities("documents", brain["id"])}


def found(library: BrainLibrary, brain: dict[str, Any], query: str) -> list[str]:
    """Paths of the documents a search returns, in rank order."""
    paths = {doc_id: path for path, doc_id in ids(library, brain).items()}
    hits = library.search({"brainId": brain["id"], "query": query})["hits"]
    return list(dict.fromkeys(paths[hit["docId"]] for hit in hits))


def test_plain_words_rank_exactly_as_before(library, brain):
    # Words still go to FTS5 one by one, so punctuation around them changes nothing.
    plain = library.search({"brainId": brain["id"], "query": "event log"})["hits"]
    noisy = library.search({"brainId": brain["id"], "query": "event, log!"})["hits"]
    assert plain == noisy
    assert {hit["docId"] for hit in plain} == set(ids(library, brain).values())
    assert all(hit["matchKind"] == "content" for hit in plain)


def test_phrases_and_exclusions_filter_hits(library, brain):
    assert set(found(library, brain, '"append only"')) == {
        "Projects/log.md",
        "Projects/draft.md",
        "Archive/old.md",
    }
    assert set(found(library, brain, '"append only" -draft')) == {
        "Projects/log.md",
        "Archive/old.md",
    }
    assert set(found(library, brain, 'event -"not append"')) == {
        "Projects/log.md",
        "Projects/draft.md",
        "Reports/summary.html",
    }


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("event tag:architecture", {"Projects/log.md", "Projects/draft.md"}),
        (
            "event tag:Architecture tag:history",
            {"Projects/log.md", "Projects/draft.md", "Archive/old.md"},
        ),
        ("event folder:projects", {"Projects/log.md", "Projects/draft.md"}),
        ("event type:html", {"Reports/summary.html"}),
        ("event author:ada", {"Projects/log.md"}),
        ("event project:raticode", {"Projects/log.md"}),
        (
            "event origin:authored type:note",
            {"Projects/log.md", "Projects/draft.md", "Archive/old.md"},
        ),
        (
            "event modified:2026-11..2026-11-14",
            {"Projects/log.md", "Projects/draft.md", "Reports/summary.html"},
        ),
        ("event modified:..2026-10", {"Archive/old.md"}),
        (
            'event brain:"research notes"',
            {"Projects/log.md", "Projects/draft.md", "Archive/old.md", "Reports/summary.html"},
        ),
        ("event brain:elsewhere", set()),
    ],
)
def test_field_clauses_narrow_hits(library, brain, query, expected):
    assert set(found(library, brain, query)) == expected


def test_reading_state_and_collection_clauses(library, brain):
    docs = ids(library, brain)
    old = library.entity("documents", docs["Archive/old.md"])
    library.mutate(
        {
            "kind": "documents",
            "id": old["id"],
            "item": {"reading": {**old["reading"], "starred": True}},
        }
    )
    assert found(library, brain, "event state:starred") == ["Archive/old.md"]
    log = library.entity("documents", docs["Projects/log.md"])
    library.mutate(
        {
            "kind": "collections",
            "id": "col1",
            "item": {
                "id": "col1",
                "brainId": brain["id"],
                "name": "Reading list",
                "brief": "",
                "sections": [
                    {
                        "id": "s1",
                        "title": "Evidence",
                        "items": [
                            {
                                "id": "i1",
                                "docId": log["id"],
                                "version": log["version"],
                                "blockId": log["blocks"][0]["id"],
                                "quote": "append only",
                            }
                        ],
                    }
                ],
            },
        }
    )
    assert found(library, brain, 'event in:"reading list"') == ["Projects/log.md"]


def test_fields_without_words_list_matching_documents_newest_first(library, brain):
    hits = library.search({"brainId": brain["id"], "query": "type:note modified:2026-11"})["hits"]
    paths = {doc_id: path for path, doc_id in ids(library, brain).items()}
    assert [paths[hit["docId"]] for hit in hits] == ["Projects/draft.md", "Projects/log.md"]
    assert {hit["matchKind"] for hit in hits} == {"filter"}
    assert all(hit["blockId"] for hit in hits)
    assert found(library, brain, "-draft tag:architecture") == ["Projects/log.md"]


@pytest.mark.parametrize(
    ("query", "named"),
    [
        ("event colour:red", ["colour:red", "type:", "generated:"]),
        ("event -tag:draft", ["-tag:draft", "Remove the minus sign"]),
        ("event modified:2026-02-30", ["2026-02-30", "is not a date range"]),
        ('"open phrase', ["Close the quotation mark"]),
    ],
)
def test_invalid_clauses_return_an_error_naming_the_fix(library, brain, query, named):
    with pytest.raises(QuerySyntaxError) as raised:
        library.search({"brainId": brain["id"], "query": query})
    assert all(text in str(raised.value) for text in named)


def test_saved_searches_compare_typed_values_without_case(library, brain):
    doc = library.entity("documents", ids(library, brain)["Projects/log.md"])
    scope = {"brains": [brain], "collections": [], "reports": [], "citations": []}
    assert saved_matches(doc, {"text": "project:RATICODE folder:projects/"}, scope)
    assert not saved_matches(doc, {"text": "project:other"}, scope)
    with pytest.raises(ValueError, match="not a search field"):
        saved_matches(doc, {"text": "colour:red"}, scope)


def rem_search(monkeypatch, tmp_path: Path, brain: dict[str, Any]):
    captured: dict[str, Any] = {}

    def serve(call, definitions, rules, name):
        captured.update(call=call, definitions=definitions)

    monkeypatch.setattr(second_brain, "serve_stdio", serve)
    second_brain.serve_registered_brains(tmp_path / "app", [brain["id"]], "html", "auto")
    return captured


def test_rem_search_tool_reads_the_syntax_and_says_so(library, brain, monkeypatch, tmp_path):
    tools = rem_search(monkeypatch, tmp_path, brain)
    search = next(item for item in tools["definitions"] if item["name"] == "search")
    for text in ("modified:2026-11..2026-11-14", "tag:", "in:", "brain:", "-word", '"Quoted'):
        assert text in search["description"]
    reply = tools["call"]("search", {"format": "json", "query": '"append only" -draft type:note'})
    assert {item["path"] for item in reply["results"]} == {"Projects/log.md", "Archive/old.md"}
    listed = tools["call"]("search", {"format": "json", "query": "tag:architecture", "limit": 1})
    assert [item["path"] for item in listed["results"]] == ["Projects/draft.md"]
    assert listed["results"][0]["matchedBy"] == ["filter"]
    more = tools["call"](
        "search",
        {"format": "json", "query": "tag:architecture", "limit": 1, "cursor": listed["nextCursor"]},
    )
    assert [item["path"] for item in more["results"]] == ["Projects/log.md"]
    with pytest.raises(ValueError, match="Use one of type:, tag:"):
        tools["call"]("search", {"format": "json", "query": "color:blue"})


def test_rem_chat_lookup_reports_a_bad_clause(library, brain, tmp_path):
    from gofer.ui.rem_home_lookups import Lookup, RemLookups

    lookups = RemLookups(
        config={"brain": {"enabled": True, "root": brain["root"], "brainIds": [brain["id"]]}},
        tasks=lambda: [],
        brain_library=library,
        data_dir=tmp_path / "app",
    )
    [bad] = lookups.run([Lookup(kind="notes", query="event colour:red")])
    assert "not a search field" in bad["unavailable"]
    [good] = lookups.run([Lookup(kind="notes", query="event tag:history")])
    assert [item["path"] for item in good["notes"]] == ["Archive/old.md"]


def test_dates_are_compared_as_utc_days():
    doc = {
        "id": "d",
        "brainId": "b",
        "title": "t",
        "path": "t.md",
        "folder": "",
        "type": "report",
        "origin": "authored",
        "extraction": "ready",
        "tags": [],
        "source": {},
        "reading": {},
        "blocks": [],
        "dates": {"added": datetime(2026, 3, 5, tzinfo=UTC).isoformat(), "modified": ""},
    }
    assert saved_matches(doc, {"text": "generated:2026-03"}, {})
    assert not saved_matches(doc, {"text": "generated:2026-04"}, {})
    assert not saved_matches({**doc, "type": "note"}, {"text": "generated:2026-03"}, {})
