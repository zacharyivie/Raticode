"""Failed integration checks can reuse provisioned, unchanged Git candidates."""

import sys
from pathlib import Path

import pytest

from gofer.ui.swarm_workspaces import git, integrate, prepare_attempt, prepare_run


@pytest.fixture
def integration_case(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init")
    (root / ".gitignore").write_text(".venv/\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Initial")
    workspace = prepare_run(root, tmp_path / "run")
    checkout = prepare_attempt(workspace, "worker")
    path = Path(checkout["path"])
    (path / "feature.txt").write_text("feature")
    git(path, "add", ".")
    git(path, "commit", "-m", "Feature")
    attempt = {"result": {"revision": git(path, "rev-parse", "HEAD")}}
    checks = [
        [sys.executable, "-c", "from pathlib import Path; assert Path('.venv/ready').is_file()"]
    ]
    return root, workspace, attempt, checks, tmp_path / "logs"


def test_retry_reuses_provisioned_candidate_and_reruns_checks(integration_case):
    root, workspace, attempt, checks, logs = integration_case
    failed = integrate(workspace, attempt, checks, logs)
    assert not failed["passed"]
    candidate = Path(failed["path"])
    (candidate / ".venv").mkdir()
    (candidate / ".venv" / "ready").write_text("local dependency")
    attempt["integration"] = failed
    trees_before = git(root, "worktree", "list", "--porcelain")
    retry = integrate(workspace, attempt, checks, logs)
    assert retry["passed"]
    assert retry["path"] == failed["path"]
    assert retry["checks"][0]["exitCode"] == 0
    assert retry["checks"][0]["logPath"] != failed["checks"][0]["logPath"]
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == trees_before.count(
        "worktree "
    )
    assert (candidate / ".venv" / "ready").read_text() == "local dependency"
    assert (Path(workspace["path"]) / "feature.txt").read_text() == "feature"


@pytest.mark.parametrize("changed", ["base", "dirty", "untracked", "head", "conflict"])
def test_retry_preserves_changed_candidate_and_creates_fresh(integration_case, changed):
    _, workspace, attempt, checks, logs = integration_case
    failed = integrate(workspace, attempt, checks, logs)
    candidate = Path(failed["path"])
    attempt["integration"] = failed
    if changed == "base":
        git(Path(workspace["path"]), "commit", "--allow-empty", "-m", "New base")
    elif changed == "dirty":
        (candidate / "feature.txt").write_text("local edits")
    elif changed == "untracked":
        (candidate / "notes.txt").write_text("untracked notes")
    elif changed == "head":
        git(candidate, "commit", "--allow-empty", "-m", "Unrecorded commit")
    else:
        failed["conflicts"] = ["feature.txt"]
    retry = integrate(workspace, attempt, checks, logs)
    assert retry["path"] != failed["path"]
    assert not retry["passed"]
    assert candidate.exists()
