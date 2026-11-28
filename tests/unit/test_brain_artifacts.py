"""Report records, provenance and series helpers that live outside the BrainLibrary class.

The module functions reach the library's methods and state through the instance at call
time, so a patch on the library (or a replaced cache) is what they use.
"""

from __future__ import annotations

from typing import Any

from gofer.ui import brain_artifacts, brain_series
from tests.helpers.brain_library import REPO, build_brain, html, write
from tests.helpers.brain_library import library as library  # noqa: F401 - fixture


def test_record_artifact_calls_library_methods_through_the_instance(library, tmp_path, monkeypatch):
    brain, root = build_brain(library, tmp_path)
    path = write(root, "reports/rem/Reports/Raticode/split.html", html("Split check", "Body"))
    made: list[str] = []
    original = library._artifact_record

    def artifact_record(*args: Any) -> dict[str, Any]:
        record: dict[str, Any] = original(*args)
        made.append(record["artifactPath"])
        return record

    monkeypatch.setattr(library, "_artifact_record", artifact_record)
    record = brain_artifacts.record_artifact(
        library, path, {"savedVia": "rem_task", "projectRoot": REPO}, question="Why split?"
    )
    assert record is not None and record["question"] == "Why split?"
    assert made == ["reports/rem/Reports/Raticode/split.html"]
    # The delegate on the class returns the stored record, not a second one.
    assert library.record_artifact(path, {"savedVia": "rem_task"})["id"] == record["id"]
    assert made == ["reports/rem/Reports/Raticode/split.html"]
    assert library.record_artifact(tmp_path / "elsewhere.html", {}) is None


def test_backfill_check_reads_the_receipts_state_at_call_time(library, tmp_path, monkeypatch):
    brain, _root = build_brain(library, tmp_path)
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        library,
        "backfill_provenance",
        lambda brain_id, *, conversations: calls.append((brain_id, conversations)),
    )
    library._receipts_seen = {}
    library._maybe_backfill(brain["id"])
    library._maybe_backfill(brain["id"])
    assert calls == [(brain["id"], True)]
    # A fresh receipts map makes the next check a first one again.
    library._receipts_seen = {}
    brain_artifacts.maybe_backfill(library, brain["id"])
    assert calls == [(brain["id"], True), (brain["id"], True)]


def test_series_and_archive_caches_stay_on_the_library(library, tmp_path, monkeypatch):
    brain, _root = build_brain(library, tmp_path)
    key = (brain["id"],)
    index = library.series_index([brain["id"]])
    archived = library.unused_archive([brain["id"]])
    assert library._series[key][1] is index
    assert set(library._unused_archive[key][1]) == archived

    def unexpected(*args: Any) -> Any:
        raise AssertionError("a cached revision must not reread document headers")

    monkeypatch.setattr(library, "document_headers", unexpected)
    assert brain_series.library_series_index(library, [brain["id"]]) is index
    assert brain_series.unused_archive(library, [brain["id"]]) == archived
    # Replacing the cache on the instance is what the next call reads.
    library._series = {key: (library.revision(), {"doc-x": {"seriesId": "s"}})}
    assert library.series_index([brain["id"]]) == {"doc-x": {"seriesId": "s"}}
