from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import generation_jobs as module
from gofer.ui.generation_jobs import GenerationJobs, git


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "commit.gpgsign", "false")
    (root / "file").write_text("base\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return root


def staged(root: Path, text: str) -> None:
    (root / "file").write_text(text)
    git(root, "add", ".")


def finished(jobs: GenerationJobs, job: dict[str, Any]) -> dict[str, Any]:
    until = time.monotonic() + 10
    while time.monotonic() < until:
        current = jobs.list(job["kind"], job.get("projectRoot", ""), job.get("branch", ""))[0]
        if current["status"] not in {"running", "queued"}:
            return current
        time.sleep(0.01)
    raise AssertionError("Job did not finish")


def fake_commit(
    monkeypatch: pytest.MonkeyPatch, release: threading.Event, auto: bool = True
) -> None:
    monkeypatch.setattr(module, "commit_message_preference", lambda: {"autoCommit": auto})

    async def generate(**kwargs: Any) -> dict[str, str]:
        assert release.wait(10)
        assert kwargs["captured_diff"] is True
        return {"message": "fix: captured changes"}

    monkeypatch.setattr(module, "generate_commit_message", generate)


def test_three_branches_generate_concurrently_and_commit_original_snapshots(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    fake_commit(monkeypatch, release)
    jobs = GenerationJobs(tmp_path / "jobs")
    base = git(repo, "rev-parse", "HEAD")
    submitted = []
    for index in range(3):
        git(repo, "switch", "-c", f"feature-{index}", base)
        staged(repo, f"change-{index}\n")
        submitted.append(jobs.start("commit", {}, repo))
        git(repo, "reset", "--hard", base)
    git(repo, "switch", "main")
    release.set()
    for index, job in enumerate(submitted):
        result = finished(jobs, job)
        assert result["status"] == "completed", result
        assert result["result"]["commit"] == git(repo, "rev-parse", f"feature-{index}")
        assert git(repo, "show", f"feature-{index}:file") == f"change-{index}"
    assert git(repo, "branch", "--show-current") == "main"
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "rev-parse", "HEAD") == base
    assert len(GenerationJobs(tmp_path / "jobs").list("commit", str(repo), "feature-1")) == 1


def test_moved_branch_keeps_message_for_review(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    fake_commit(monkeypatch, release)
    staged(repo, "snapshot\n")
    jobs = GenerationJobs(tmp_path / "jobs")
    job = jobs.start("commit", {}, repo)
    git(repo, "commit", "-m", "external commit")
    head = git(repo, "rev-parse", "HEAD")
    release.set()
    result = finished(jobs, job)
    assert result["status"] == "needs_review"
    assert result["result"]["message"] == "fix: captured changes"
    assert git(repo, "rev-parse", "HEAD") == head


def test_hook_failure_preserves_message_and_worktree(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    fake_commit(monkeypatch, release)
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\necho 'Test hook denied commit' >&2\nexit 1\n")
    hook.chmod(0o755)
    staged(repo, "snapshot\n")
    jobs = GenerationJobs(tmp_path / "jobs")
    job = jobs.start("commit", {}, repo)
    release.set()
    result = finished(jobs, job)
    assert result["status"] == "needs_review"
    assert "Test hook denied" in result["error"]
    assert git(repo, "diff", "--cached")
    assert git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_auto_commit_disabled_retains_message_without_changing_git(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    fake_commit(monkeypatch, release, auto=False)
    staged(repo, "snapshot\n")
    before = git(repo, "rev-parse", "HEAD")
    jobs = GenerationJobs(tmp_path / "jobs")
    job = jobs.start("commit", {}, repo)
    assert jobs.start("commit", {}, repo)["id"] == job["id"]
    release.set()
    result = finished(jobs, job)
    assert result["status"] == "completed"
    assert "commit" not in result["result"]
    assert git(repo, "rev-parse", "HEAD") == before
    jobs.dismiss(job["id"])
    assert not jobs.list("commit", str(repo), "main")


def test_theme_progress_result_and_prompt_survive_page_departure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    progressing = threading.Event()
    draft = {"label": "Ocean", "instructions": "Navy", "html": "<html>Preview</html>"}

    async def generate(body: dict[str, Any], emit: Any, **kwargs: Any) -> dict[str, str]:
        emit({"text": "Composing the diagram"})
        progressing.set()
        assert release.wait(10)
        return draft

    monkeypatch.setattr(module, "generate_report_theme", generate)
    jobs = GenerationJobs(tmp_path)
    job = jobs.start("theme", {"description": "An ocean journal"})
    assert progressing.wait(5)
    assert jobs.list("theme")[0]["progress"] == "Composing the diagram"
    assert jobs.start("theme", {"description": "Duplicate"})["id"] == job["id"]
    release.set()
    assert finished(jobs, job)["result"] == draft
    restored = GenerationJobs(tmp_path).list("theme")[0]
    assert restored["description"] == "An ocean journal"
    assert restored["result"] == draft


def test_restart_marks_unfinished_jobs_interrupted(tmp_path: Path) -> None:
    folder = tmp_path / "generation-jobs"
    folder.mkdir()
    (folder / "job.json").write_text(
        json.dumps({"id": "job", "kind": "theme", "status": "running", "createdAt": 0})
    )
    jobs = GenerationJobs(tmp_path)
    assert jobs.list("theme")[0]["status"] == "interrupted"
    assert "stopped" in jobs.list("theme")[0]["error"]


def test_new_staging_is_preserved_while_auto_commit_uses_snapshot(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    fake_commit(monkeypatch, release)
    staged(repo, "snapshot\n")
    jobs = GenerationJobs(tmp_path / "jobs")
    job = jobs.start("commit", {}, repo)
    staged(repo, "later edits\n")
    release.set()
    assert finished(jobs, job)["status"] == "completed"
    assert git(repo, "show", "HEAD:file") == "snapshot"
    assert git(repo, "show", ":file") == "later edits"
    assert (repo / "file").read_text() == "later edits\n"


def test_shutdown_prevents_late_auto_commit(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    entered = threading.Event()
    fake_commit(monkeypatch, release)

    async def generate(**kwargs: Any) -> dict[str, str]:
        entered.set()
        assert release.wait(5)
        return {"message": "fix: generated after shutdown"}

    monkeypatch.setattr(module, "generate_commit_message", generate)
    staged(repo, "snapshot\n")
    jobs = GenerationJobs(tmp_path / "jobs")
    before = git(repo, "rev-parse", "HEAD")
    job = jobs.start("commit", {}, repo)
    assert entered.wait(5)
    jobs.close()
    release.set()
    assert finished(jobs, job)["status"] == "needs_review"
    assert git(repo, "rev-parse", "HEAD") == before


def test_dismiss_latest_theme_does_not_restore_previous_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def generate(*args: Any, **kwargs: Any) -> dict[str, str]:
        return {"label": "Ocean", "instructions": "Navy", "html": "<html>Preview</html>"}

    monkeypatch.setattr(module, "generate_report_theme", generate)
    jobs = GenerationJobs(tmp_path)
    finished(jobs, jobs.start("theme", {"description": "First"}))
    latest = finished(jobs, jobs.start("theme", {"description": "Second"}))
    jobs.dismiss(latest["id"])
    assert jobs.list("theme") == []
    assert GenerationJobs(tmp_path).list("theme") == []


def test_auto_commit_supports_unborn_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    fake_commit(monkeypatch, release)
    root = tmp_path / "new-repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "commit.gpgsign", "false")
    staged(root, "first commit\n")
    jobs = GenerationJobs(tmp_path / "jobs")
    job = jobs.start("commit", {}, root)
    release.set()
    result = finished(jobs, job)
    assert result["status"] == "completed", result
    assert git(root, "rev-list", "--count", "HEAD") == "1"
    assert git(root, "status", "--porcelain") == ""


def test_branch_switch_before_submission_is_rejected(repo: Path, tmp_path: Path) -> None:
    staged(repo, "snapshot\n")
    jobs = GenerationJobs(tmp_path / "jobs")
    with pytest.raises(ValueError, match="branch changed"):
        jobs.start("commit", {"branch": "a-different-branch"}, repo)
    assert jobs.list("commit", str(repo), "main") == []


def test_subfolder_commit_does_not_capture_changes_outside_selected_project(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = repo / "selected-project"
    project.mkdir()
    staged(repo, "private sibling changes\n")
    before = git(repo, "rev-parse", "HEAD")

    def unexpected_start(*args: Any, **kwargs: Any) -> None:
        pytest.fail("A subfolder request must not start a repository-wide generation job")

    monkeypatch.setattr(threading.Thread, "start", unexpected_start)
    jobs = GenerationJobs(tmp_path / "jobs")
    with pytest.raises(ValueError, match="repository root"):
        jobs.start("commit", {}, project)
    assert not jobs.jobs
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "show", ":file") == "private sibling changes"


def test_restart_recovers_commit_completed_before_receipt_was_saved(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    fake_commit(monkeypatch, release)
    staged(repo, "snapshot\n")
    jobs = GenerationJobs(tmp_path / "jobs")
    job = jobs.start("commit", {}, repo)
    release.set()
    result = finished(jobs, job)
    assert result["status"] == "completed"
    commit = result["result"].pop("commit")
    result["status"] = "running"
    (jobs.path / f"{job['id']}.json").write_text(json.dumps(result))
    restored = GenerationJobs(tmp_path / "jobs").list("commit", str(repo), "main")[0]
    assert restored["status"] == "completed"
    assert restored["result"]["commit"] == commit
