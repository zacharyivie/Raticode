from __future__ import annotations

import copy
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.brain_job_sources import matches_source_rules, source_rules
from gofer.ui.brain_library import BrainLibrary
from tests.unit.test_brain_library import cited_output, opened, wait_task


@pytest.fixture
def library(tmp_path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def job_for(brain: dict[str, Any], **rule_patch: Any) -> dict[str, Any]:
    return {
        "id": "job-rules",
        "brainId": brain["id"],
        "name": "Evidence digest",
        "recipe": "digest",
        "scope": "All documents",
        "enabled": False,
        "trigger": {"kind": "manual"},
        "destination": "Summaries",
        "limits": {"documents": 2, "turns": 2},
        "review": True,
        "provider": {"id": "codex"},
        "sourceRules": {"location": {"kind": "all"}, **rule_patch},
    }


def save_job(library: BrainLibrary, job: dict[str, Any]) -> dict[str, Any]:
    return library.mutate({"kind": "jobs", "id": job["id"], "item": job})


def test_job_filters_compose_and_preview_never_calls_the_report_provider(library, tmp_path):
    brain, original = opened(library, tmp_path)
    moment = datetime(2026, 10, 6, 16, tzinfo=UTC)
    original.update(title="OpenAI Dots research", tags=["research", "approved"], project="Raticode")
    original["reading"]["starred"] = True
    original["dates"]["added"] = (moment - timedelta(hours=2)).isoformat()
    rules = source_rules(
        job_for(
            brain,
            query="OpenAI Dots",
            tags=["research"],
            excludeTags=["archived"],
            project="Raticode",
            types=["note"],
            unreadOnly=True,
            starredOnly=True,
            time={"mode": "window", "field": "added", "count": 24, "unit": "hours"},
        ),
        [],
        [],
    )
    job = job_for(brain)
    assert matches_source_rules(original, rules, job, {}, moment)
    changes: list[dict[str, Any]] = [
        {"title": "Other topic"},
        {"tags": ["research", "archived"]},
        {"project": "Other"},
        {"type": "pdf"},
        {"reading": {"read": True, "starred": True}},
        {"reading": {"read": False, "starred": False}},
        {"missing": True},
        {"excluded": True},
        {"extraction": "pending"},
        {"origin": "generated"},
        {"dates": {"added": (moment - timedelta(hours=25)).isoformat()}},
    ]
    for change in changes:
        assert not matches_source_rules({**original, **change}, rules, job, {}, moment), change
    library.put("documents", original)
    job = save_job(library, job_for(brain, query="OpenAI Dots"))
    library.generate = lambda *args: pytest.fail("Preview must not generate reports")
    preview = library.request("job_sources", {"job": job})
    assert preview["eligibleCount"] == preview["selectedCount"] == 1
    assert [source["id"] for source in preview["sources"]] == [original["id"]]


@pytest.mark.parametrize("unit,count", [("hours", 24), ("days", 7)])
def test_time_windows_use_real_datetimes_include_the_boundary_and_reject_unknowns(unit, count):
    moment = datetime(2026, 10, 6, 16, tzinfo=UTC)
    cutoff = moment - timedelta(**{unit: count})
    job = job_for(
        {"id": "brain"}, time={"mode": "window", "field": "published", "count": count, "unit": unit}
    )
    rules = source_rules(job, [], [])
    doc: dict[str, Any] = {
        "id": "doc",
        "extraction": "ready",
        "origin": "authored",
        "version": "v1",
        "title": "Evidence",
        "path": "evidence.md",
        "blocks": [],
        "dates": {"published": cutoff.astimezone().isoformat()},
        "tags": [],
    }
    assert matches_source_rules(doc, rules, job, {}, moment)
    for date in [
        "",
        "unknown",
        (cutoff - timedelta(seconds=1)).isoformat(),
        (moment + timedelta(hours=1)).isoformat(),
    ]:
        assert not matches_source_rules(
            {**doc, "dates": {"published": date}}, rules, job, {}, moment
        )
    doc["dates"]["published"] = cutoff.isoformat().replace("+00:00", "Z")
    assert matches_source_rules(doc, rules, job, {}, moment)


def test_since_success_drains_backlog_without_repeats_and_then_reads_changes(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    root = Path(brain["root"])
    for index in range(4):
        (root / f"source-{index}.md").write_text(
            f"# Evidence {index}\n\nIndependent evidence {index}."
        )
    library.scan(brain["id"])
    library.update_brain({"id": brain["id"], "patch": {"settings": {"allowCloud": True}}})
    library.generate = cited_output
    job = save_job(library, job_for(brain, time={"mode": "since-success", "field": "activity"}))
    results = [
        library.run_job({"jobId": job["id"]}, threading.Event(), lambda _: None) for _ in range(3)
    ]
    assert [result["remainingCount"] for result in results] == [3, 1, 0]
    reports = library.entities("reports")
    ids = [source["docId"] for report in reports for source in report["sourceVersions"]]
    assert len(ids) == len(set(ids)) == 5
    current = library.entity("jobs", job["id"])
    assert current["lastSuccessfulRunAt"]
    assert library.run_job({"jobId": job["id"]}, threading.Event(), lambda _: None)["skipped"]
    # A new version remains eligible even if its filesystem date was deliberately preserved.
    doc = library.entity("documents", doc["id"])
    doc["version"] = "changed-version"
    doc["dates"]["modified"] = "2020-01-01T00:00:00Z"
    library.put("documents", doc)
    preview = library.request("job_sources", {"job": job})
    assert [source["id"] for source in preview["sources"]] == [doc["id"]]


@pytest.mark.parametrize("cancelled", [False, True])
def test_partial_failure_keeps_completed_versions_but_does_not_advance_checkpoint(
    library, tmp_path, cancelled
):
    brain, _ = opened(library, tmp_path)
    (Path(brain["root"]) / "second.md").write_text("# Second\n\nAnother observation.")
    library.scan(brain["id"])
    library.update_brain({"id": brain["id"], "patch": {"settings": {"allowCloud": True}}})
    job = job_for(brain, time={"mode": "since-success"})
    job["recipe"] = "summarize-new"
    job = save_job(library, job)
    calls = []

    def generate(prompt, provider, cancel):
        calls.append(prompt)
        if len(calls) == 2:
            if cancelled:
                raise InterruptedError("Cancelled")
            raise ValueError("Provider unavailable")
        return cited_output(prompt, provider, cancel)

    library.generate = generate
    failed = wait_task(
        library, library.start_task("job", {"jobId": job["id"], "brainId": brain["id"]})
    )
    assert failed["state"] == ("cancelled" if cancelled else "failed")
    current = library.entity("jobs", job["id"])
    if not cancelled:
        assert current["lastRun"]["status"] == "failed"
    assert len(current["seenVersions"]) == 1
    assert not current.get("lastSuccessfulRunAt")
    assert library.request("job_sources", {"job": job})["eligibleCount"] == 1
    reopened = BrainLibrary(library.folder.parent, start=False)
    try:
        assert reopened.request("job_sources", {"job": job})["eligibleCount"] == 1
    finally:
        reopened.close()
    library.generate = cited_output
    library.run_job({"jobId": job["id"]}, threading.Event(), lambda _: None)
    assert len(library.entity("jobs", job["id"])["seenVersions"]) == 2
    assert library.entity("jobs", job["id"])["lastSuccessfulRunAt"]


def test_previous_outputs_are_opt_in_and_topic_guides_report_generation(library, tmp_path):
    brain, _ = opened(library, tmp_path)
    library.update_brain({"id": brain["id"], "patch": {"settings": {"allowCloud": True}}})
    prompts = []

    def generate(prompt, provider, cancel):
        prompts.append(prompt)
        return cited_output(prompt, provider, cancel)

    library.generate = generate
    job = save_job(library, job_for(brain, query="exact evidence", onlyUnprocessed=True))
    library.run_job({"jobId": job["id"]}, threading.Event(), lambda _: None)
    assert "Focus on the topic query: exact evidence" in prompts[0]
    assert library.request("job_sources", {"job": job})["eligibleCount"] == 0
    report = library.entities("reports")[0]
    prior = next(
        doc for doc in library.entities("documents") if doc["path"] == report["artifactPath"]
    )
    job["sourceRules"]["includeOutputs"] = True
    job = save_job(library, job)
    assert [source["id"] for source in library.request("job_sources", {"job": job})["sources"]] == [
        prior["id"]
    ]
    result = library.run_job({"jobId": job["id"]}, threading.Event(), lambda _: None)
    new_report = library.entity("reports", result["reportId"])
    assert new_report["sourceVersions"] == [{"docId": prior["id"], "version": prior["version"]}]


def test_sources_arriving_during_run_and_concurrent_editor_cannot_erase_state(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    library.update_brain({"id": brain["id"], "patch": {"settings": {"allowCloud": True}}})
    job = save_job(library, job_for(brain, time={"mode": "since-success"}))
    stale_editor = copy.deepcopy(job)

    def generate(prompt, provider, cancel):
        (Path(brain["root"]) / "during-run.md").write_text("# Arrival\n\nNew evidence.")
        library.scan(brain["id"])
        return cited_output(prompt, provider, cancel)

    library.generate = generate
    library.run_job({"jobId": job["id"]}, threading.Event(), lambda _: None)
    current = library.entity("jobs", job["id"])
    stale_editor["name"] = "Edited job name"
    save_job(library, stale_editor)
    saved = library.entity("jobs", job["id"])
    assert saved["lastSuccessfulRunAt"] == current["lastSuccessfulRunAt"]
    assert saved["seenVersions"][doc["id"]] == doc["version"]
    assert [
        source["path"] for source in library.request("job_sources", {"job": saved})["sources"]
    ] == ["during-run.md"]


def test_collection_identity_and_folder_boundaries_survive_renames(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    root = Path(brain["root"])
    for path in ["Research/one.md", "Research/sub/two.md", "Research-old/three.md"]:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("# Evidence\n\nAn observation.")
    library.scan(brain["id"])
    job = save_job(
        library, job_for(brain, location={"kind": "folder", "path": "Research", "recursive": True})
    )
    assert {
        source["path"] for source in library.request("job_sources", {"job": job})["sources"]
    } == {"Research/one.md", "Research/sub/two.md"}
    job["sourceRules"]["location"]["recursive"] = False
    assert library.request("job_sources", {"job": job})["eligibleCount"] == 1
    collection = {
        "id": "col-stable",
        "brainId": brain["id"],
        "name": "Before rename",
        "sections": [{"items": [{"docId": doc["id"]}]}],
    }
    library.put("collections", collection)
    job = save_job(
        library,
        {**job, "sourceRules": {"location": {"kind": "collection", "id": collection["id"]}}},
    )
    collection["name"] = "After rename"
    library.put("collections", collection)
    assert [source["id"] for source in library.request("job_sources", {"job": job})["sources"]] == [
        doc["id"]
    ]
    saved = {
        "id": "saved-stable",
        "brainId": brain["id"],
        "name": "Old search",
        "text": "exact evidence",
    }
    library.put("savedSearches", saved)
    job["sourceRules"]["location"] = {"kind": "saved-search", "id": saved["id"]}
    job = save_job(library, job)
    saved["name"] = "New search"
    library.put("savedSearches", saved)
    assert [source["id"] for source in library.request("job_sources", {"job": job})["sources"]] == [
        doc["id"]
    ]


@pytest.mark.parametrize(
    "patch",
    [
        {"location": {"kind": "folder", "path": "../outside"}},
        {"location": {"kind": "collection", "id": "missing"}},
        {"time": {"mode": "window", "count": 0}},
        {"time": {"mode": "window", "count": 1.5}},
        {"time": {"mode": "window", "unit": "minutes"}},
        {"time": {"field": "unknown"}},
        {"match": "unknown"},
        {"includeOutputs": "yes"},
        {"query": "unknown-field:value"},
        {"types": ["script"]},
    ],
)
def test_invalid_source_rules_are_rejected_at_save(library, tmp_path, patch):
    brain, _ = opened(library, tmp_path)
    with pytest.raises(ValueError):
        save_job(library, job_for(brain, **patch))
    assert not library.entities("jobs")
