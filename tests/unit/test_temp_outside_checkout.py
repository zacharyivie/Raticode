"""pytest's temp folders must never land in the checkout the user's app runs from."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.conftest import checkouts, temp_inside_checkout


def test_a_temp_folder_inside_a_checkout_is_found(tmp_path: Path) -> None:
    main = tmp_path / "Raticode"
    worktree = main / ".worktrees" / "change"
    roots = [worktree, main]
    # A worker's TMPDIR=$PWD/../../.scratch from a worktree is the main checkout.
    assert temp_inside_checkout(worktree / "../../.scratch-x", roots) == main
    assert temp_inside_checkout(worktree / ".scratch", roots) == worktree
    assert temp_inside_checkout(tmp_path / "elsewhere", roots) is None


def test_a_worktree_also_guards_its_main_checkout(tmp_path: Path) -> None:
    main = tmp_path / "repo"
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q", str(main)], check=True)
    subprocess.run([*git, "-C", str(main), "commit", "-q", "--allow-empty", "-m", "i"], check=True)
    worktree = main / ".worktrees" / "change"
    subprocess.run(
        [*git, "-C", str(main), "worktree", "add", "-q", "-b", "change", str(worktree)],
        check=True,
    )
    assert checkouts(worktree) == [worktree, main.resolve()]
    assert checkouts(tmp_path / "not-a-repo") == [tmp_path / "not-a-repo"]


def test_this_run_keeps_its_temp_folder_outside_the_repository(tmp_path: Path) -> None:
    assert temp_inside_checkout(tmp_path, checkouts()) is None
