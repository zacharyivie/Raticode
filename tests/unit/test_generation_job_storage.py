from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.generation_jobs import GenerationJobs


def stored_job(**changes: Any) -> dict[str, Any]:
    return {
        "id": "job",
        "kind": "theme",
        "status": "running",
        "createdAt": 1,
        **changes,
    }


@pytest.mark.parametrize(
    "record",
    [
        None,
        [],
        {"status": []},
        stored_job(createdAt="yesterday"),
        stored_job(createdAt=float("nan")),
        stored_job(kind="unknown"),
        stored_job(status="unknown"),
        stored_job(kind="commit"),
        stored_job(result=[]),
    ],
)
def test_invalid_job_does_not_break_startup_or_hide_valid_jobs(tmp_path: Path, record: Any) -> None:
    folder = tmp_path / "generation-jobs"
    folder.mkdir()
    (folder / "job.json").write_text(json.dumps(record))
    (folder / "valid.json").write_text(json.dumps(stored_job(id="valid")))

    jobs = GenerationJobs(tmp_path)

    assert list(jobs.jobs) == ["valid"]
    assert jobs.list("theme")[0]["status"] == "interrupted"


@pytest.mark.parametrize("job_id", ["../outside", "different"])
def test_recovery_rejects_job_id_that_does_not_match_file(tmp_path: Path, job_id: str) -> None:
    folder = tmp_path / "generation-jobs"
    folder.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("keep this file")
    (folder / "job.json").write_text(json.dumps(stored_job(id=job_id)))

    jobs = GenerationJobs(tmp_path)

    assert outside.read_text() == "keep this file"
    assert not jobs.jobs
    assert not (folder / "different.json").exists()


def test_job_save_does_not_follow_predictable_temporary_link(tmp_path: Path) -> None:
    jobs = GenerationJobs(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep this file")
    (jobs.path / "job.tmp").symlink_to(outside)

    jobs._save(stored_job())

    assert outside.read_text() == "keep this file"
    assert json.loads((jobs.path / "job.json").read_text())["id"] == "job"
    assert not (jobs.path / "job.json").is_symlink()


def test_recovery_ignores_linked_job_record(tmp_path: Path) -> None:
    folder = tmp_path / "generation-jobs"
    folder.mkdir()
    outside = tmp_path / "outside.json"
    original = json.dumps(stored_job())
    outside.write_text(original)
    (folder / "job.json").symlink_to(outside)

    jobs = GenerationJobs(tmp_path)

    assert not jobs.jobs
    assert outside.read_text() == original
