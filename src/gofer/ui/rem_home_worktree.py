"""Temporary git worktrees that let Rem change one project from several tasks at once."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

GIT_TIMEOUT_SECONDS = 60


class WorktreeError(RuntimeError):
    pass


def _git(cwd: Path | str, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError(str(exc)) from exc
    if result.returncode:
        raise WorktreeError(result.stderr.strip() or result.stdout.strip() or "git failed")
    return result.stdout.strip()


def plan_worktree(
    data_dir: Path,
    run: dict[str, Any],
    *,
    folder: str = "rem-worktrees",
    branch_prefix: str = "rem",
) -> dict[str, str] | None:
    """Describe an isolated checkout for a run, or None when the project is not in git."""
    project = Path(run["recipe"]["project_dir"])
    try:
        top = Path(_git(project, "rev-parse", "--show-toplevel")).resolve()
        base = _git(project, "rev-parse", "--verify", "HEAD")
    except WorktreeError:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", str(run["recipe"].get("title") or "task").lower())
    slug = slug.strip("-")[:40] or "task"
    checkout = data_dir / folder / str(run["id"])
    return {
        "checkout": str(checkout),
        "root": str(checkout / project.resolve().relative_to(top)),
        "repository": str(top),
        "branch": f"{branch_prefix}/{slug}-{str(run['id'])[:8]}",
        "base": base,
    }


def ensure_worktree(worktree: dict[str, str]) -> None:
    """Create the checkout, reusing its branch when Rem restarts mid-run."""
    checkout = Path(worktree["checkout"])
    if checkout.exists():
        return
    checkout.parent.mkdir(parents=True, exist_ok=True)
    repository = worktree["repository"]
    _git(repository, "worktree", "prune")
    try:
        _git(repository, "rev-parse", "--verify", f"refs/heads/{worktree['branch']}")
        _git(repository, "worktree", "add", str(checkout), worktree["branch"])
    except WorktreeError:
        _git(
            repository,
            "worktree",
            "add",
            "-b",
            worktree["branch"],
            str(checkout),
            worktree["base"],
        )


def release_worktree(worktree: dict[str, str], title: str, *, label: str = "Rem") -> str:
    """Keep the run's changes on its branch, remove the checkout, and describe the result."""
    checkout = Path(worktree["checkout"])
    repository = worktree["repository"]
    branch = worktree["branch"]
    if checkout.exists():
        try:
            if _git(checkout, "status", "--porcelain"):
                _git(checkout, "add", "-A")
                _git(
                    checkout,
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "--no-verify",
                    "-m",
                    f"{label}: {title}"[:200],
                )
        except WorktreeError as exc:
            # Never discard work that could not be committed.
            return (
                f"{label} couldn't commit the remaining changes ({exc}). They are still in "
                f"the temporary worktree at {checkout} on branch {branch}."
            )
        try:
            _git(repository, "worktree", "remove", "--force", str(checkout))
        except WorktreeError:
            shutil.rmtree(checkout, ignore_errors=True)
            try:
                _git(repository, "worktree", "prune")
            except WorktreeError:
                pass
    try:
        changed = _git(repository, "rev-list", "--count", f"{worktree['base']}..{branch}")
    except WorktreeError:
        return ""
    if changed == "0":
        try:
            _git(repository, "branch", "-D", branch)
        except WorktreeError:
            pass
        return ""
    return (
        f"Another task was changing this project at the same time, so this work ran in a "
        f"temporary worktree. Its changes are on branch {branch}; merge it when you're ready."
    )
