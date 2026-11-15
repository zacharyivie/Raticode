"""A ticket branch's full patch, the clean-merge check, and landing it in its base."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gofer.ui import ticket_patch
from gofer.ui.rem_home_worktree import plan_worktree

ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@x",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@x",
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
}


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, env=ENV, capture_output=True, text=True
    )
    return result.stdout.strip()


def commit(cwd: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        (cwd / name).parent.mkdir(parents=True, exist_ok=True)
        (cwd / name).write_text(text)
    git(cwd, "add", "-A")
    git(cwd, "commit", "-qm", message)
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key, value in ENV.items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q", "-b", "dev")
    git(root, "config", "commit.gpgsign", "false")
    commit(root, {"app.py": "one\ntwo\nthree\n", "keep.txt": "same\n"}, "base")
    return root


def feature(repo: Path, tmp_path: Path, name: str = "feature") -> Path:
    checkout = tmp_path / name
    git(repo, "worktree", "add", "-q", "-b", name, str(checkout), "dev")
    return checkout


def test_patch_shows_each_changed_file_before_and_after(repo: Path, tmp_path: Path) -> None:
    work = feature(repo, tmp_path)
    commit(work, {"app.py": "one\nTWO\nthree\n", "new/doc.md": "hello\n"}, "change")
    git(work, "mv", "keep.txt", "kept.txt")
    git(work, "commit", "-qm", "rename")
    # Uncommitted work in the worker's checkout is part of what the user reviews.
    (work / "app.py").write_text("one\nTWO\nthree\nfour\n")
    (work / "scratch.txt").write_text("left over\n")

    patch = ticket_patch.read_patch(str(repo), "feature", base_branch="dev", checkout=str(work))
    files = {f["path"]: f for f in patch["files"]}
    assert files["app.py"]["original"] == "one\ntwo\nthree\n"
    assert files["app.py"]["modified"] == "one\nTWO\nthree\nfour\n"
    assert files["app.py"]["uncommitted"] is True
    assert files["new/doc.md"]["status"] == "A" and files["new/doc.md"]["original"] == ""
    assert files["kept.txt"]["status"] == "R" and files["kept.txt"]["oldPath"] == "keep.txt"
    assert files["scratch.txt"]["modified"] == "left over\n"
    assert patch["uncommitted"] == ["app.py", "scratch.txt"]
    assert patch["base"] == git(repo, "rev-parse", "dev")

    # Without the checkout, only what's committed shows; the revision names what was seen.
    committed = ticket_patch.read_patch(str(repo), "feature", base_branch="dev")
    assert {f["path"]: f["modified"] for f in committed["files"]}["app.py"] == "one\nTWO\nthree\n"
    assert committed["revision"] != patch["revision"]


def test_a_link_out_of_the_checkout_is_shown_as_a_link(repo: Path, tmp_path: Path) -> None:
    work = feature(repo, tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("private\n")
    (work / "peek").symlink_to(secret)
    patch = ticket_patch.read_patch(str(repo), "feature", base_branch="dev", checkout=str(work))
    shown = {f["path"]: f["modified"] for f in patch["files"]}
    assert shown["peek"] == str(secret)


def test_merge_state_names_conflicts_without_touching_anything(repo: Path, tmp_path: Path) -> None:
    work = feature(repo, tmp_path)
    tip = commit(work, {"app.py": "one\nfeature\nthree\n"}, "feature edit")
    assert ticket_patch.merge_state(str(repo), "dev", tip)["state"] == "fast_forward"
    commit(repo, {"other.txt": "x\n"}, "unrelated")
    assert ticket_patch.merge_state(str(repo), "dev", tip)["state"] == "clean"
    commit(repo, {"app.py": "one\nmain\nthree\n"}, "competing edit")
    before = git(repo, "rev-parse", "dev")
    state = ticket_patch.merge_state(str(repo), "dev", tip)
    assert state == {"state": "conflict", "baseTip": before, "conflicts": ["app.py"]}
    with pytest.raises(ticket_patch.LandConflict) as caught:
        ticket_patch.land(str(repo), "feature", "dev", expected_tip=tip, message="m")
    assert caught.value.files == ["app.py"]
    assert git(repo, "rev-parse", "dev") == before
    assert (repo / "app.py").read_text() == "one\nmain\nthree\n"
    assert not git(repo, "status", "--porcelain")


def test_land_merges_into_the_checked_out_base_and_its_files(repo: Path, tmp_path: Path) -> None:
    work = feature(repo, tmp_path)
    tip = commit(work, {"feature.txt": "new\n"}, "feature")
    base = commit(repo, {"other.txt": "x\n"}, "unrelated")
    with pytest.raises(ticket_patch.PatchError, match="changed since"):
        ticket_patch.land(str(repo), "feature", "dev", expected_tip=base, message="m")

    (repo / "app.py").write_text("dirty\n")
    with pytest.raises(ticket_patch.PatchError, match="uncommitted changes"):
        ticket_patch.land(str(repo), "feature", "dev", expected_tip=tip, message="m")
    git(repo, "checkout", "--", "app.py")

    result = ticket_patch.land(str(repo), "feature", "dev", expected_tip=tip, message="Merge it")
    assert result["state"] == "clean"
    head = git(repo, "rev-parse", "dev")
    assert head == result["commit"]
    assert git(repo, "log", "-1", "--format=%P", "dev").split() == [base, tip]
    assert (repo / "feature.txt").read_text() == "new\n"
    assert not git(repo, "status", "--porcelain")
    # Landing again finds it's already there.
    again = ticket_patch.land(str(repo), "feature", "dev", expected_tip=tip, message="m")
    assert again["state"] == "landed" and git(repo, "rev-parse", "dev") == head


def test_land_fast_forwards_a_base_that_isnt_checked_out(repo: Path, tmp_path: Path) -> None:
    git(repo, "switch", "-q", "-c", "elsewhere")
    work = feature(repo, tmp_path)
    tip = commit(work, {"feature.txt": "new\n"}, "feature")
    result = ticket_patch.land(str(repo), "feature", "dev", expected_tip=tip, message="m")
    assert result == {"state": "fast_forward", "commit": tip, "baseBranch": "dev"}
    assert git(repo, "rev-parse", "dev") == tip
    assert git(repo, "branch", "--show-current") == "elsewhere"


def test_a_landed_branch_still_shows_only_its_own_changes(repo: Path, tmp_path: Path) -> None:
    work = feature(repo, tmp_path)
    tip = commit(work, {"feature.txt": "new\n"}, "feature")
    git(repo, "merge", "-q", "--ff-only", "feature")
    commit(repo, {"later.txt": "after\n"}, "someone else, later")
    patch = ticket_patch.read_patch(str(repo), "feature", base_branch="dev")
    assert [f["path"] for f in patch["files"]] == ["feature.txt"]
    assert patch["tip"] == tip
    assert ticket_patch.merge_state(str(repo), "dev", tip)["state"] == "landed"


def test_planned_worktrees_remember_the_branch_they_came_from(repo: Path, tmp_path: Path) -> None:
    run = {"id": "abc12345", "recipe": {"project_dir": str(repo), "title": "Fix it"}}
    planned = plan_worktree(tmp_path / "data", run)
    assert planned is not None and planned["base_branch"] == "dev"
    git(repo, "switch", "-q", "--detach")
    detached = plan_worktree(tmp_path / "data", run)
    assert detached is not None and "base_branch" not in detached


def test_change_requests_read_as_one_message() -> None:
    text = ticket_patch.review_message(
        "Close, a few things.",
        [
            {"path": "src/app.py", "line": 12, "side": "modified", "body": "Handle None here"},
            {"path": "old.py", "line": 3, "side": "original", "body": "Why was this removed?"},
        ],
    )
    assert text == (
        "Close, a few things.\nLine comments on the patch:\n"
        "- src/app.py:12: Handle None here\n"
        "- old.py:3 (before the change): Why was this removed?"
    )
    for bad in (
        [{"path": "a", "line": 0, "body": "x"}],
        [{"path": "a", "line": 1, "body": " "}],
        [{"path": "a", "line": True, "body": "x"}],
        [{"path": "a", "line": 1, "side": "middle", "body": "x"}],
    ):
        with pytest.raises(ValueError):
            ticket_patch.review_message("", bad)
    with pytest.raises(ValueError, match="Write a note"):
        ticket_patch.review_message(" ", [])
    rebase = ticket_patch.rebase_message("rem/fix", "dev", ["a.py", "b.py"])
    assert "Rebase rem/fix onto the current dev" in rebase and "a.py, b.py" in rebase
