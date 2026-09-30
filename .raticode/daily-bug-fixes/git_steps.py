"""Git operations for the daily workflow; state stays in the worktree's Git directory."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

BRANCH = "daily-bug-fixes"
PASSES = 25


def git(repo: Path, *args: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.rstrip("\n")


def state_path(worktree: Path) -> Path:
    return Path(git(worktree, "rev-parse", "--absolute-git-dir")) / "raticode-daily-bug-fixes.json"


def save_state(worktree: Path, state: dict[str, Any]) -> None:
    target = state_path(worktree)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(target)


def checked_state(worktree: Path, phase: str) -> dict[str, Any]:
    state: dict[str, Any] = json.loads(state_path(worktree).read_text(encoding="utf-8"))
    if git(worktree, "rev-parse", "--show-toplevel") != str(worktree.resolve()):
        raise RuntimeError("The requested path is not the worktree root.")
    if git(worktree, "branch", "--show-current") != BRANCH:
        raise RuntimeError("The worktree is no longer on daily-bug-fixes.")
    if state["phase"] != phase or git(worktree, "rev-parse", "HEAD") != state["head"]:
        raise RuntimeError("Workflow state or HEAD changed unexpectedly; refusing to continue.")
    return state


def create(repo: Path) -> Path:
    repo = repo.resolve()
    worktree = repo / ".worktrees" / BRANCH
    refs = git(repo, "for-each-ref", "--format=%(refname)", "refs/heads/", "refs/remotes/")
    if any(ref.endswith("/" + BRANCH) for ref in refs.splitlines()):
        raise RuntimeError("Branch daily-bug-fixes already exists; refusing to reuse it.")
    records = git(repo, "worktree", "list", "--porcelain", "-z").split("\0")
    if os.path.lexists(worktree) or any(
        record.startswith("worktree ") and Path(record[9:]).name == BRANCH for record in records
    ):
        raise RuntimeError("Worktree daily-bug-fixes already exists; refusing to reuse it.")
    base = git(repo, "rev-parse", "HEAD")
    # Git creates the branch atomically and refuses races or preexisting paths.
    git(repo, "worktree", "add", "-b", BRANCH, str(worktree), base)
    save_state(worktree, {"base": base, "head": base, "completed": 0, "phase": "scanning"})
    return worktree


def commit_pass(worktree: Path, index: int) -> None:
    state = checked_state(worktree, "scanning")
    if index != state["completed"] or not 0 <= index < PASSES:
        raise RuntimeError("Scans must commit once each, in order from 0 through 24.")
    git(worktree, "add", "--all")
    # A clean scan still counts as one completed pass.
    git(worktree, "commit", "--allow-empty", "-m", "wip: but fix")
    state.update(head=git(worktree, "rev-parse", "HEAD"), completed=index + 1)
    save_state(worktree, state)
    sys.stdout.write("all-complete" if index + 1 == PASSES else "scan-complete")


def soft_reset(worktree: Path) -> None:
    state = checked_state(worktree, "scanning")
    if state["completed"] != PASSES:
        raise RuntimeError("All 25 scans must finish before the soft reset.")
    if git(worktree, "status", "--porcelain"):
        raise RuntimeError("Unexpected uncommitted changes before the soft reset.")
    state["last_wip_head"] = state["head"]
    # Persist recovery information before changing the branch tip.
    save_state(worktree, state)
    git(worktree, "reset", "--soft", state["base"])
    state.update(head=state["base"], phase="summarizing")
    save_state(worktree, state)


def final_commit(worktree: Path, message: str) -> None:
    state = checked_state(worktree, "summarizing")
    if not message.strip() or "\0" in message:
        raise RuntimeError("The generated commit message is empty or invalid.")
    # The summarizer must not change the staged tree or introduce untracked edits.
    staged_tree = git(worktree, "write-tree")
    expected_tree = git(worktree, "rev-parse", state["last_wip_head"] + "^{tree}")
    if staged_tree != expected_tree or git(worktree, "diff", "--name-only"):
        raise RuntimeError("The summarizer changed the reviewed tree; refusing to commit.")
    if git(worktree, "ls-files", "--others", "--exclude-standard"):
        raise RuntimeError("The summarizer introduced untracked files; refusing to commit.")
    git(worktree, "commit", "--allow-empty", "--file=-", stdin=message.strip() + "\n")
    state.update(head=git(worktree, "rev-parse", "HEAD"), phase="complete")
    save_state(worktree, state)
    print(state["head"])


def main() -> None:
    action = sys.argv[1]
    if action == "create":
        sys.stdout.write(str(create(Path.cwd())))
        return
    worktree = Path(os.environ["WORKTREE"])
    if action == "commit-pass":
        commit_pass(worktree, int(os.environ["SCAN_INDEX"]))
    elif action == "soft-reset":
        soft_reset(worktree)
    elif action == "final-commit":
        final_commit(worktree, os.environ["COMMIT_MESSAGE"])
    else:
        raise ValueError(f"Unknown action: {action}")


if __name__ == "__main__":
    main()
