"""Check that a writer task's worktree branch reached the project's integration branch.

A project's worktree skill (such as ``worktree-dev-flow``) has each change made on its
own branch and then fast-forwarded into a local integration branch, ``dev``. Workers
sometimes stopped after committing, so a task could succeed while its work never
reached ``dev``. Rem records the repository's branch tips when a run starts and, before
accepting the run, looks for branches this run created or moved, and named in its own
commands or result, that ``dev`` does not contain.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

INTEGRATION_BRANCH = "dev"
GIT_TIMEOUT_SECONDS = 30
# Branches that are never a single change's feature branch.
SHARED_BRANCHES = frozenset({"dev", "main", "master", "stage", "test", "release"})

# Tasks that ask for the work to stay off dev, such as a prototype for the user to try.
_OPT_OUT = re.compile(
    r"\b(?:do not|don't|dont|never|without)\s+(?:\w+\s+){0,3}?"
    r"(?:merg\w*|land\w*|fast-forward\w*|integrat\w*)\b[^.\n]{0,40}\bdev\b"
    r"|\bleave\s+(?:it|them|the\s+(?:work|change|changes|branch))\s+on\s+(?:its|the|a)\s+"
    r"(?:own\s+)?branch\b",
    re.IGNORECASE,
)


def _git(cwd: Path | str, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def landing_opted_out(prompt: str) -> bool:
    """True when the task says its work must not be merged into dev."""
    return bool(_OPT_OUT.search(prompt or ""))


def branch_tips(project: str | Path) -> dict[str, str] | None:
    """Local branch tips of the project's repository, or None without a dev branch."""
    if not project or not Path(project).is_dir():
        return None
    out = _git(project, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads")
    if out is None:
        return None
    tips = dict(line.split(" ", 1) for line in out.splitlines() if " " in line)
    return tips if INTEGRATION_BRANCH in tips else None


def _worktrees(project: str | Path) -> tuple[str | None, dict[str, str]]:
    """The main checkout's path and each checked-out branch's worktree path."""
    out = _git(project, "worktree", "list", "--porcelain") or ""
    main: str | None = None
    paths: dict[str, str] = {}
    path = ""
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
            if main is None:
                main = path
        elif line.startswith("branch refs/heads/"):
            paths[line[len("branch refs/heads/") :]] = path
    return main, paths


# Git commands that create or change a branch. Reading one (log, diff, status) doesn't
# make it this run's branch.
_WRITES = re.compile(
    r"\bgit\b[^\n;&|]*?\b(?:worktree\s+add|commit|rebase|merge|cherry-pick|am|reset"
    r"|checkout\s+-[bB]|switch\s+-[cC]|branch)\b"
)


def _mentioned(name: str, text: str) -> bool:
    return bool(re.search(rf"(?<![\w./-]){re.escape(name)}(?![\w/-])", text))


def _written_by(branch: str, path: str | None, commands: list[str]) -> bool:
    """True when one of the run's git commands that writes names the branch or its folder."""
    for command in commands:
        if not _WRITES.search(command):
            continue
        if _mentioned(branch, command) or (path and path in command):
            return True
    return False


def unlanded_branches(
    project: str | Path,
    before: dict[str, str],
    commands: list[str],
    known: list[str] | tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Branches this run wrote to that dev does not fully contain.

    ``before`` is :func:`branch_tips` from when the run started. ``commands`` are the
    run's own shell command inputs. A branch counts only when it was created or moved
    during the run and one of those commands wrote to it by name or folder, so branches
    of tasks working at the same time, which this run may have read, aren't its own.
    ``known`` are branches an earlier turn of the same run was already found to own;
    a repair turn that runs no git command doesn't make them someone else's.
    """
    now = branch_tips(project)
    if now is None:
        return []
    main, paths = _worktrees(project)
    found: list[dict[str, Any]] = []
    for branch, tip in sorted(now.items()):
        if branch in SHARED_BRANCHES or before.get(branch) == tip:
            continue
        path = paths.get(branch)
        if path and path == main:
            # The project's own checkout is the user's, never a task's feature branch.
            continue
        if branch not in known and not _written_by(branch, path, commands):
            continue
        ahead = _git(project, "rev-list", "--count", f"{INTEGRATION_BRANCH}..{tip}")
        commits = int(ahead.strip()) if ahead and ahead.strip().isdigit() else 0
        dirty = bool(
            path and (_git(path, "status", "--porcelain", "--untracked-files=no") or "").strip()
        )
        if commits or dirty:
            found.append(
                {"branch": branch, "worktree": path, "commits": commits, "uncommitted": dirty}
            )
    return found


def landing_feedback(branches: list[dict[str, Any]]) -> str:
    """Repair instructions for the worker, naming each branch and what is missing."""
    parts = []
    for item in branches:
        where = f" (worktree {item['worktree']})" if item.get("worktree") else ""
        problems = []
        if item["commits"]:
            problems.append(f"{item['commits']} commit(s) not on {INTEGRATION_BRANCH}")
        if item["uncommitted"]:
            problems.append("uncommitted changes to tracked files")
        parts.append(f"{item['branch']}{where}: {', '.join(problems)}")
    return (
        f"The work hasn't landed on local {INTEGRATION_BRANCH}: "
        + "; ".join(parts)
        + ". Finishing the task includes the worktree skill's land step, and this task "
        "already authorizes it: commit the change, rebase onto "
        f"{INTEGRATION_BRANCH}, rerun the checks, then fast-forward {INTEGRATION_BRANCH} "
        f"(`git -C <folder where {INTEGRATION_BRANCH} is checked out> merge --ff-only "
        f"<branch>`, or `git fetch . <branch>:{INTEGRATION_BRANCH}` when it isn't checked "
        "out). That folder may be the project's own checkout; fast-forwarding it there is "
        "allowed when it has no uncommitted changes. Don't push. If it can't land, for "
        "example because that folder has uncommitted changes or the rebase conflicts, say "
        "so plainly as the first line of your result."
    )
