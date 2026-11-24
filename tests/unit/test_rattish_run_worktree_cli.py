"""`rattish runs changes|land|discard` against a real backend, and the run log's worktree."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.unit.test_cli_api_commands import (  # noqa: F401 - fixtures
    Backend,
    backend,
    clean_environment,
    ok,
    run,
)

CHANGE = """Rattish: 1
Workflow:
  name: Changer
  workspace: worktree
Node change:
  type: bash-command
  command: echo changed > notes.txt
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def project(
    backend: Backend,  # noqa: F811 - the fixture imported above
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, Path]:
    for key, value in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
    }.items():
        monkeypatch.setenv(key, value)
    workflow_id = backend.workflow(CHANGE, name="Changer")
    root = backend.data / "projects" / "changer"
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "commit.gpgsign", "false")
    (root / "README").write_text("base\n")
    git(root, "add", "README")
    git(root, "commit", "-qm", "base")
    return workflow_id, root


def _start(workflow_id: str) -> str:
    result = ok("rattish", "start", workflow_id, "--wait", "--timeout", "60")
    assert result["status"] == "success", result
    return str(result["id"])


def test_review_and_land_a_worktree_run_from_the_cli(
    project: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id, root = project
    run_id = _start(workflow_id)

    log = ok("rattish", "logs", workflow_id, run_id)
    assert log["worktree"]["state"] == "pending"
    assert log["worktree"]["baseBranch"] == "main"
    branch = log["worktree"]["branch"]

    changes = ok("rattish", "runs", "changes", run_id)
    assert [item["path"] for item in changes["files"]] == ["notes.txt"]
    assert changes["merge"]["state"] == "fast_forward"
    assert changes["worktree"]["runId"] == run_id
    code, text, _ = run(
        "rattish", "runs", "changes", run_id, "--output", "text", "--diff", json_output=False
    )
    assert code == 0
    assert "Into main: lands as a fast-forward" in text
    assert "A  notes.txt" in text and "+changed" in text

    # Rem and its workers can't land or discard a run's changes for you.
    with monkeypatch.context() as agent:
        agent.setenv("RATICODE_AGENT_SESSION", "1")
        agent.delenv("RATICODE_TOKEN_FILE")
        agent.setenv("RATICODE_TOKEN", "rtk_agent_not-a-real-token")
        for action in ("land", "discard"):
            code, refused, _ = run("rattish", "runs", action, run_id)
            assert code == 5 and refused["error"]["code"] == "user_only"

    code, stale, _ = run("rattish", "runs", "land", run_id, "--expected-tip", "0" * 40)
    assert code != 0 and "changed since you opened its patch" in stale["error"]["message"]

    landed = ok("rattish", "runs", "land", run_id, "--expected-tip", changes["tip"])
    assert landed["landed"] is True and landed["baseBranch"] == "main"
    assert (root / "notes.txt").read_text() == "changed\n"
    assert branch not in git(root, "branch", "--format=%(refname:short)").splitlines()
    assert ok("rattish", "logs", workflow_id, run_id)["worktree"]["state"] == "landed"

    code, again, _ = run("rattish", "runs", "land", run_id)
    assert code != 0 and "already landed" in again["error"]["message"]


def test_discard_and_conflicts_from_the_cli(project: tuple[str, Path]) -> None:
    workflow_id, root = project
    first = _start(workflow_id)
    second = _start(workflow_id)

    discarded = ok("rattish", "runs", "discard", first)
    assert discarded["discarded"] is True
    assert ok("rattish", "runs", "changes", first)["worktree"]["state"] == "discarded"

    (root / "notes.txt").write_text("mine\n")
    git(root, "add", "notes.txt")
    git(root, "commit", "-qm", "mine")
    code, conflict, _ = run("rattish", "runs", "land", second)
    assert code == 4
    assert conflict["error"]["code"] == "conflict"
    assert (root / "notes.txt").read_text() == "mine\n"
    assert ok("rattish", "runs", "changes", second)["merge"]["conflicts"] == ["notes.txt"]

    code, missing, _ = run("rattish", "runs", "changes", "20260101T000000000000Z-nothere")
    assert code != 0 and "was not found" in missing["error"]["message"]


def test_workflow_rm_names_and_removes_waiting_worktrees(
    project: tuple[str, Path],
    backend: Backend,  # noqa: F811 - the fixture imported above
) -> None:
    from typer.testing import CliRunner

    from gofer.cli.main import app

    workflow_id, root = project
    first = _start(workflow_id)
    _start(workflow_id)
    branches = [
        item
        for item in git(root, "branch", "--format=%(refname:short)").splitlines()
        if item.startswith("raticode/run/")
    ]
    assert len(branches) == 2
    flags = ["--data-dir", str(backend.data)]

    kept = CliRunner().invoke(app, ["workflow", "rm", workflow_id, *flags], input="n\n")
    assert kept.exit_code != 0
    assert "It has 2 run worktrees waiting to be landed or discarded" in kept.output
    assert ok("rattish", "runs", "changes", first)["worktree"]["state"] == "pending"

    removed = CliRunner().invoke(app, ["workflow", "rm", workflow_id, *flags], input="y\n")
    assert removed.exit_code == 0, removed.output
    assert f"Deleted {workflow_id} and 2 run worktrees with their branches" in " ".join(
        removed.stdout.split()
    )
    assert git(root, "branch", "--format=%(refname:short)").splitlines() == ["main"]
    assert not (backend.data / "rattish-worktrees" / workflow_id).exists()
    assert git(root, "worktree", "list", "--porcelain").count("worktree ") == 1
