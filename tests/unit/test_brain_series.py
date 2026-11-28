from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import brain_library
from gofer.ui.brain_library import BrainLibrary
from gofer.ui.brain_series import (
    detect_series,
    group_series,
    names_date,
    parse_edition,
    series_index,
)
from tests.helpers.brain_library import library as library  # noqa: F401 - fixture
from tests.helpers.brain_library import wait_idle


def header(doc_id: str, path: str, modified: str = "2026-11-01T00:00:00+00:00", **extra: Any):
    return {"id": doc_id, "brainId": "b", "path": path, "dates": {"modified": modified}, **extra}


def test_edition_names_drop_a_trailing_date_and_version():
    assert parse_edition("Reports/weekly-status-2026-11-20.html") == {
        "key": "weekly status",
        "stem": "weekly-status",
        "nameDate": "2026-11-20",
        "version": 0,
    }
    found = parse_edition("Reports/Weekly Status 2026-11-20-v3.md")
    assert found and found["key"] == "weekly status" and found["version"] == 3
    assert found["nameDate"] == "2026-11-20"
    undated = parse_edition("weekly_status.html")
    assert undated and undated["key"] == "weekly status" and undated["nameDate"] == ""
    # A one-word stem is too generic to be a series.
    assert parse_edition("Reports/summary-2026-11-20.html") is None
    assert parse_edition("2026-11-20.md") is None
    # A date that can't exist stays part of the name.
    bad = parse_edition("market-scan-2026-13-45.md")
    assert bad and bad["nameDate"] == "" and bad["key"] == "market scan 2026 13 45"


def test_series_need_two_editions_a_dated_name_and_one_brain():
    documents = [
        header("a", "Reports/market-scan-2026-11-13.html"),
        header("b", "Archive/market-scan-2026-11-20.html"),
        header("c", "Reports/market-scan.html", "2026-11-18T09:00:00+00:00"),
        # Same stem in another brain: its own group with one edition, so no series.
        {**header("d", "Reports/market-scan-2026-11-21.html"), "brainId": "other"},
        # Two undated files with one name are not a dated series.
        header("e", "One/meeting-notes.md"),
        header("f", "Two/meeting-notes.md"),
        # A missing file is not an edition.
        header("g", "Reports/market-scan-2026-11-25.html", missing=True),
    ]
    series = detect_series(documents)
    assert len(series) == 1
    group = series[0]
    assert group["stem"] == "market-scan" and group["brainId"] == "b"
    assert [item["docId"] for item in group["editions"]] == ["b", "c", "a"]
    assert [item["date"] for item in group["editions"]] == [
        "2026-11-20",
        "2026-11-18",
        "2026-11-13",
    ]
    assert group["editions"][1]["dateFrom"] == "modified"
    assert group["newestId"] == "b" and not group["notSeries"]


def test_versions_of_one_day_order_by_version_and_marks_exclude_the_group():
    documents = [
        header("v1", "daily-brief-2026-11-20.md"),
        header("v2", "daily-brief-2026-11-20-v2.md"),
        header("old", "daily-brief-2026-11-19.md"),
    ]
    group = detect_series(documents)[0]
    assert [item["docId"] for item in group["editions"]] == ["v2", "v1", "old"]
    # One marked member marks the group, so a later edition joins the mark.
    documents.append(header("new", "daily-brief-2026-11-21.md"))
    documents[0]["notSeries"] = True
    marked = detect_series(documents)
    assert marked[0]["notSeries"] and len(marked[0]["editions"]) == 4
    assert series_index(marked) == {}


def test_grouping_keeps_other_documents_in_score_order():
    documents = [
        header("new", "Reports/fleet-review-2026-11-20.html"),
        header("mid", "Reports/fleet-review-2026-11-13.html"),
        header("old", "Reports/fleet-review-2026-11-06.html"),
    ]
    index = series_index(detect_series(documents))
    hits = [
        {"docId": "x", "blockId": "x1"},
        {"docId": "old", "blockId": "o1"},
        {"docId": "y", "blockId": "y1"},
        {"docId": "new", "blockId": "n1"},
        {"docId": "old", "blockId": "o2"},
        {"docId": "z", "blockId": "z1"},
    ]
    grouped = group_series(hits, index)
    assert [hit["blockId"] for hit in grouped] == ["x1", "n1", "o1", "o2", "y1", "z1"]
    # One matching edition moves nothing.
    single = [hit for hit in hits if hit["docId"] != "new"]
    assert group_series(single, index) == single


@pytest.mark.parametrize(
    "query",
    [
        "fleet review 2026-11-13",
        "fleet review modified:2026-11..2026-11-14",
        "fleet review 11/13/2026",
        "fleet review Nov 13",
        "fleet review 13th of November",
        "fleet review november",
        "fleet review 2025",
    ],
)
def test_queries_that_name_a_date(query):
    assert names_date(query)


@pytest.mark.parametrize("query", ["fleet review", "may we march on", "plan0word1 rollout"])
def test_queries_without_a_date(query):
    assert not names_date(query)


def paragraph(topic: str, index: int, extra: str = "") -> str:
    words = " ".join(f"{topic}{index}word{n}" for n in range(30))
    return f"Finding {index} about {topic} {extra}: {words}."


# Writes a dated HTML report, unlike the shared write.
def write(path: Path, title: str, paragraphs: list[str], age_days: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"<p>{text}</p>" for text in paragraphs)
    path.write_text(
        f"<html><head><title>{title}</title></head><body><h1>{title}</h1>{body}</body></html>",
        encoding="utf-8",
    )
    moment = time.time() - 86400 * age_days
    os.utime(path, (moment, moment))


def test_search_lists_editions_newest_first_at_the_best_place(library, tmp_path, monkeypatch):
    # This compares grouped order with plain score order, so the recency lift (which
    # dated queries skip too) stays off here; test_brain_search_recency covers it.
    monkeypatch.setattr(brain_library, "RECENCY_WEIGHT", 0.0)
    root = tmp_path / "brain"
    root.mkdir()
    reports = root / "Reports"
    # The oldest edition repeats the query words most, so it scores best.
    write(
        reports / "fleet-review-2026-11-06.html",
        "Fleet review November 6",
        [paragraph("fleet", 0, "turbine turbine turbine"), paragraph("fleet", 1, "turbine")],
        14,
    )
    write(
        reports / "fleet-review-2026-11-20.html",
        "Fleet review November 20",
        [paragraph("fleet", 2, "turbine"), paragraph("fleet", 3)],
        1,
    )
    write(
        reports / "fleet-review-2026-11-13.html",
        "Fleet review November 13",
        [paragraph("fleet", 4, "turbine"), paragraph("fleet", 5)],
        7,
    )
    write(
        root / "Notes" / "turbine-notes.html",
        "Turbine notes",
        [paragraph("notes", 0, "turbine turbine")],
        3,
    )
    files = {path: path.read_bytes() for path in root.rglob("*.html")}
    brain = library.open({"root": str(root)})
    wait_idle(library)
    docs = {item["path"]: item for item in library.entities("documents", brain["id"])}
    newest = docs["Reports/fleet-review-2026-11-20.html"]["id"]
    middle = docs["Reports/fleet-review-2026-11-13.html"]["id"]
    oldest = docs["Reports/fleet-review-2026-11-06.html"]["id"]
    notes = docs["Notes/turbine-notes.html"]["id"]
    editions = [newest, middle, oldest]

    def order(query: str, **extra: Any) -> list[str]:
        hits = library.search({"brainId": brain["id"], "query": query, **extra})["hits"]
        return list(dict.fromkeys(hit["docId"] for hit in hits))

    plain = order("turbine", dated=True)
    assert set(plain) == {*editions, notes}
    grouped = order("turbine")
    # The series sits where its best edition ranked, newest first; the note keeps its
    # place relative to that position.
    best = min(plain.index(doc_id) for doc_id in editions)
    expected = [doc_id for doc_id in plain if doc_id not in editions]
    expected[best:best] = editions
    assert grouped == expected
    assert plain != grouped
    # A query naming a date, or a modified: filter, keeps the score order.
    assert order("turbine 2026-11-13") == order("turbine 2026-11-13", dated=True)
    assert order("turbine modified:2026-11") == order("turbine modified:2026-11", dated=True)

    hits = library.search({"brainId": brain["id"], "query": "turbine"})["hits"]
    oldest_hit = next(hit for hit in hits if hit["docId"] == oldest)
    assert oldest_hit["series"] == {
        "stem": "fleet-review",
        "editionDate": "2026-11-06",
        "editions": 3,
        "newestDocumentId": newest,
    }
    # Older editions are dated records, not replaced versions.
    assert oldest_hit.get("status", "current") == "current"
    assert "replacedBy" not in oldest_hit
    assert "series" not in next(hit for hit in hits if hit["docId"] == notes)

    snapshot = library.snapshot()
    assert [group["newestId"] for group in snapshot["series"]] == [newest]
    series_id = snapshot["series"][0]["id"]

    from gofer.ui import second_brain

    library.update_brain({"id": brain["id"], "patch": {"settings": {"allowCloud": True}}})

    def serve(call, definitions, rules, name):
        assert "dated report series" in call("rules", {})
        results = call("search", {"format": "json", "query": "turbine"})["results"]
        assert [item["documentId"] for item in results] == grouped
        first = next(item for item in results if item["documentId"] == newest)
        assert first["series"]["editions"] == 3 and first["status"] == "current"
        listing = call("search", {"format": "json", "query": ""})["results"]
        assert next(item for item in listing if item["documentId"] == middle)["series"] == {
            "stem": "fleet-review",
            "editionDate": "2026-11-13",
            "editions": 3,
            "newestDocumentId": newest,
        }

    monkeypatch.setattr(second_brain, "serve_stdio", serve)
    second_brain.serve_registered_brains(tmp_path / "app", [brain["id"]], "html", "auto")

    # Not a series: search stops grouping, and the mark travels with the folder.
    library.request("mark_series", {"seriesId": series_id, "notSeries": True})
    assert order("turbine") == plain
    assert "series" not in library.search({"brainId": brain["id"], "query": "turbine"})["hits"][0]
    assert library.snapshot()["series"][0]["notSeries"] is True
    portable = json.loads((root / ".raticode" / "brain.json").read_text(encoding="utf-8"))
    marked = {doc["id"] for doc in portable["library"]["documents"] if doc.get("notSeries")}
    assert marked == set(editions)
    library.scan(brain["id"])
    assert library.entity("documents", oldest)["notSeries"] is True
    assert {path: path.read_bytes() for path in root.rglob("*.html")} == files

    library.request("remove", {"id": brain["id"]})
    elsewhere = BrainLibrary(tmp_path / "other-app", start=False)
    try:
        reopened = elsewhere.open({"root": str(root)})
        wait_idle(elsewhere)
        assert elsewhere.snapshot()["series"][0]["notSeries"] is True
        elsewhere.request("mark_series", {"seriesId": series_id, "notSeries": False})
        assert "notSeries" not in elsewhere.entity("documents", oldest)
        again = elsewhere.search({"brainId": reopened["id"], "query": "turbine"})["hits"]
        assert list(dict.fromkeys(hit["docId"] for hit in again)) == grouped
        with pytest.raises(ValueError, match="no longer in the library"):
            elsewhere.request("mark_series", {"seriesId": "series-gone"})
    finally:
        elsewhere.close()
