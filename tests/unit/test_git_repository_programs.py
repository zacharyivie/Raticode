"""Raticode's own git commands in checkouts agents can edit run no repository-named programs.

A worker can write ``.git/config`` and ``.gitattributes`` in its checkout. Clean, smudge
and process filters, textconv and external diff drivers, and merge drivers named there
would otherwise run outside any sandbox when the backend stages, checks out, diffs or
reads status (CWE-78). Scratch repositories only.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from gofer.ui import organization_repository, swarm_workspaces
from gofer.ui.rem_home_worktree import ensure_worktree, release_worktree
from gofer.utils.process import untrusted_repository_git_env

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def run(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout


def plant(repo: Path, marker: Path) -> None:
    """Name a program in every repository-scoped setting git runs on file contents."""
    script = repo.parent / "planted.sh"
    script.write_text(f"#!/bin/sh\necho ran >> {marker}\ncat\n")
    script.chmod(0o755)
    for key in (
        "filter.evil.clean",
        "filter.evil.smudge",
        "diff.evil.textconv",
        "diff.evil.command",
        "merge.evil.driver",
    ):
        run(repo, "config", key, str(script))
    run(repo, "config", "filter.evil.required", "true")
    (repo / ".gitattributes").write_text("* filter=evil diff=evil merge=evil\n")


def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-q", "-b", "main")
    run(repo, "config", "user.name", "Test")
    run(repo, "config", "user.email", "test@example.invalid")
    (repo / "a.txt").write_text("one\n")
    run(repo, "add", "a.txt")
    run(repo, "commit", "-q", "-m", "first")
    return repo


def settings(env: dict[str, str]) -> dict[str, str]:
    return {
        env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
        for i in range(int(env.get("GIT_CONFIG_COUNT", "0")))
    }


def test_overrides_blank_repository_commands_but_keep_git_lfs(tmp_path):
    repo = repository(tmp_path)
    plant(repo, tmp_path / "marker")
    run(repo, "config", "filter.lfs.clean", "git-lfs clean -- %f")

    found = settings(untrusted_repository_git_env(repo, {}))

    assert found == {
        "filter.evil.clean": "",
        "filter.evil.smudge": "",
        "filter.evil.required": "false",
        "diff.evil.textconv": "cat",
        "diff.evil.command": "true",
        "merge.evil.driver": "false",
    }


def test_overrides_follow_existing_environment_settings(tmp_path):
    repo = repository(tmp_path)
    plant(repo, tmp_path / "marker")
    env = untrusted_repository_git_env(
        repo,
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "url.x.pushInsteadOf",
            "GIT_CONFIG_VALUE_0": "",
        },
    )
    assert env["GIT_CONFIG_KEY_0"] == "url.x.pushInsteadOf"
    assert settings(env)["filter.evil.clean"] == ""


def test_a_repository_without_commands_adds_no_settings(tmp_path):
    repo = repository(tmp_path)
    assert settings(untrusted_repository_git_env(repo, {})) == {}


def test_a_subsection_with_an_equals_sign_is_still_blocked(tmp_path):
    # Review of 49b224e4: git splits ``-c key=value`` at the first "=", so overriding
    # filter."x=y".clean on the command line set a different key and the filter ran.
    repo = repository(tmp_path)
    marker = tmp_path / "marker"
    script = tmp_path / "planted.sh"
    script.write_text(f"#!/bin/sh\necho ran >> {marker}\ncat\n")
    script.chmod(0o755)
    run(repo, "config", "filter.x=y.clean", str(script))
    (repo / ".gitattributes").write_text("* filter=x=y\n")
    (repo / "a.txt").write_text("changed\n")
    worktree = {
        "checkout": str(repo),
        "root": str(repo),
        "repository": str(repo),
        "branch": "main",
        "base": "",
    }

    assert settings(untrusted_repository_git_env(repo, {}))["filter.x=y.clean"] == ""
    organization_repository.git(repo, "add", "-A")
    swarm_workspaces.git(repo, "status", "--porcelain")
    assert not marker.exists(), marker.read_text()
    # release_worktree stages and commits with ``git add -A``, then removes the checkout;
    # point it at a throwaway checkout of the same repository.
    checkout = tmp_path / "data" / "rem-worktrees" / "eq"
    run(repo, "worktree", "add", "-q", "-b", "rem/eq", str(checkout))
    marker.unlink(missing_ok=True)
    (checkout / "b.txt").write_text("new\n")
    release_worktree({**worktree, "checkout": str(checkout), "branch": "rem/eq"}, "Task")
    assert not marker.exists(), marker.read_text()
    assert "b.txt" in run(repo, "show", "--name-only", "--format=", "rem/eq")


def test_repository_lfs_programs_turn_off_git_lfs_too(tmp_path):
    # git-lfs reads lfs.customtransfer and similar settings from the repository's own
    # config and runs what they name, so with any of them set no git-lfs filter is trusted.
    repo = repository(tmp_path)
    run(repo, "config", "filter.lfs.process", "git-lfs filter-process")
    run(repo, "config", "lfs.customtransfer.evil.path", "/tmp/evil")

    found = settings(untrusted_repository_git_env(repo, {}))

    assert found["filter.lfs.process"] == ""
    assert found["filter.lfs.clean"] == "" and found["filter.lfs.smudge"] == ""
    assert found["filter.lfs.required"] == "false"
    assert not any(key.startswith("lfs.") for key in found)


def test_unreadable_settings_fail_closed(tmp_path):
    with pytest.raises(OSError):
        untrusted_repository_git_env(tmp_path / "missing", {})


def test_rem_worktree_add_and_release_run_no_planted_filter(tmp_path):
    repo = repository(tmp_path)
    marker = tmp_path / "marker"
    plant(repo, marker)
    run(repo, "add", ".gitattributes")
    marker.unlink(missing_ok=True)
    run(repo, "-c", "filter.evil.clean=", "-c", "filter.evil.required=false", "commit", "-qm", "x")
    marker.unlink(missing_ok=True)
    worktree = {
        "checkout": str(tmp_path / "data" / "rem-worktrees" / "run"),
        "root": str(tmp_path / "data" / "rem-worktrees" / "run"),
        "repository": str(repo),
        "branch": "rem/task-run",
        "base": run(repo, "rev-parse", "HEAD").strip(),
    }

    ensure_worktree(worktree)  # worktree add checks files out: smudge
    checkout = Path(worktree["checkout"])
    (checkout / "a.txt").write_text("changed\n")
    (checkout / "b.txt").write_text("new\n")
    summary = release_worktree(worktree, "Task")  # status, add -A, commit

    assert not marker.exists(), marker.read_text()
    assert "couldn't commit" not in summary
    assert "b.txt" in run(repo, "show", "--name-only", "--format=", "rem/task-run")


def test_organization_and_swarm_git_run_no_planted_filter(tmp_path):
    repo = repository(tmp_path)
    marker = tmp_path / "marker"
    plant(repo, marker)
    (repo / "a.txt").write_text("changed\n")

    organization_repository.git(repo, "add", "-A")
    organization_repository.git(repo, "diff", "--cached")
    swarm_workspaces.git(repo, "status", "--porcelain")
    swarm_workspaces.git(repo, "diff", "HEAD")

    assert not marker.exists(), marker.read_text()
