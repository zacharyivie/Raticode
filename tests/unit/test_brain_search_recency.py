"""Exact search lifts recent documents a little, fused by rank with the match order."""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import brain_library
from gofer.ui.brain_library import BrainLibrary, appeared_at, recency_lift

BODY = (
    "## Bin\n\nCompost compost.\n\n"
    "## Wednesday\n\nTurned the compost pile and mixed in the dry autumn leaves today.\n"
)


@pytest.fixture
def library(tmp_path: Path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def wait_tasks(library: BrainLibrary) -> None:
    deadline = time.monotonic() + 15
    while any(task["state"] == "running" for task in library.snapshot()["tasks"]):
        if time.monotonic() > deadline:
            pytest.fail("Second Brain tasks did not finish")
        time.sleep(0.02)


def age(path: Path, days: float) -> None:
    moment = time.time() - 86400 * days
    os.utime(path, (moment, moment))


def equal_copies(library: BrainLibrary, tmp_path: Path) -> tuple[dict[str, Any], str, str]:
    """Two documents with the same text, so they match any query equally.

    Equal matches used to keep document ID order, so the copy whose ID sorts first is
    made the older one: only the recency lift lists the other first. Returns the brain,
    the older copy's ID and the newer copy's ID.
    """
    root = tmp_path / "copies"
    root.mkdir()
    for name in ("one", "two"):
        (root / f"{name}.md").write_text(f"---\ntitle: Copy {name}\n---\n{BODY}", encoding="utf-8")
    bike = "---\ntitle: Bike\n---\n## Chain\n\nOiled it.\n"
    (root / "bike.md").write_text(bike, encoding="utf-8")
    brain = library.open({"root": str(root)})
    wait_tasks(library)
    paths = {doc["id"]: doc["path"] for doc in library.entities("documents", brain["id"])}
    copies = sorted(doc_id for doc_id, path in paths.items() if path != "bike.md")
    older, newer = copies
    age(root / paths[older], 30)
    age(root / paths[newer], 1)
    library.scan(brain["id"])
    wait_tasks(library)
    return brain, older, newer


def document_order(library: BrainLibrary, params: dict[str, Any]) -> list[str]:
    hits = library.search(params)["hits"]
    return list(dict.fromkeys(hit["docId"] for hit in hits))


def test_equal_matches_list_the_newer_document_first(library, tmp_path):
    brain, older, newer = equal_copies(library, tmp_path)
    hits = library.search({"brainId": brain["id"], "query": "compost"})["hits"]
    assert [hit["docId"] for hit in hits] == [newer, newer, older, older]


def test_a_query_with_a_date_keeps_plain_match_order(library, tmp_path):
    brain, older, newer = equal_copies(library, tmp_path)
    # A date clause, a date in the words, or a caller asking for dated order skips the lift,
    # so equal matches keep document ID order.
    for params in (
        {"query": "compost added:2000.."},
        {"query": "compost 2026-11-13"},
        {"query": "compost", "dated": True},
    ):
        assert document_order(library, {"brainId": brain["id"], **params}) == [older, newer]


def test_combined_search_fuses_the_lifted_word_order(library, tmp_path, monkeypatch):
    brain, older, newer = equal_copies(library, tmp_path)
    current = library.brain(brain["id"])
    current["settings"]["semantic"] = True
    library.put_brain(current)
    # Nothing is related in meaning, so the fused order is the word order.
    monkeypatch.setattr(library, "_embed_many", lambda texts, *a: [[0.0, 1.0] for _ in texts])
    monkeypatch.setattr(library, "_embedding", lambda *a, **k: [1.0, 0.0])
    library.embed(brain["id"], threading.Event())
    params = {"brainId": brain["id"], "query": "compost", "mode": "hybrid"}
    assert document_order(library, params) == [newer, older]


def days_ago(days: int) -> datetime:
    return datetime(2026, 11, 23, tzinfo=UTC) - timedelta(days=days)


def test_lift_moves_a_much_newer_close_match_up_but_not_a_distant_one():
    ids = [f"d{n:02d}" for n in range(10)]
    scores = {doc_id: 100.0 - n for n, doc_id in enumerate(ids)}
    appeared = {doc_id: days_ago(100 + n) for n, doc_id in enumerate(ids)}
    # The second match is the newest document; the last is the second newest.
    appeared["d01"] = days_ago(1)
    appeared["d09"] = days_ago(2)
    lifted = recency_lift(ids, scores, appeared, weight=0.7)
    assert lifted[0] == "d01"
    # The ninth match moves up, but nowhere near the top.
    assert 2 < lifted.index("d09") < 9
    assert sorted(lifted) == ids
    # No weight, no lift.
    assert recency_lift(ids, scores, appeared, weight=0) == ids


def test_lift_ranks_only_the_first_thirty_documents():
    ids = [f"d{n:02d}" for n in range(40)]
    scores = {doc_id: 100.0 - n for n, doc_id in enumerate(ids)}
    appeared = {doc_id: days_ago(100 + n) for n, doc_id in enumerate(ids)}
    # The newest document matched 35th, and the 30th match is the newest in the window.
    appeared["d34"] = days_ago(0)
    appeared["d29"] = days_ago(1)
    lifted = recency_lift(ids, scores, appeared, weight=0.7)
    assert brain_library.RECENCY_WINDOW == 30
    assert sorted(lifted[:30]) == ids[:30]
    assert lifted[30:] == ids[30:]
    assert lifted.index("d29") < 29


def test_undated_documents_rank_after_dated_ones_for_recency():
    ids = ["old", "undated"]
    scores = {"old": 5.0, "undated": 5.0}
    lifted = recency_lift(ids, scores, {"old": days_ago(400), "undated": None}, weight=0.7)
    assert lifted == ["old", "undated"]
    lifted = recency_lift(ids[::-1], scores, {"old": days_ago(400)}, weight=0.7)
    assert lifted == ["old", "undated"]


def test_a_document_appears_at_the_earlier_of_added_and_modified():
    assert appeared_at(
        {"added": "2026-11-20T10:00:00+00:00", "modified": "2026-09-01T08:00:00Z"}
    ) == datetime(2026, 9, 1, 8, tzinfo=UTC)
    assert appeared_at({"added": "2026-11-20T10:00:00", "modified": "not a date"}) == datetime(
        2026, 11, 20, 10, tzinfo=UTC
    )
    assert appeared_at({"added": "", "modified": None}) is None
    assert appeared_at(None) is None
