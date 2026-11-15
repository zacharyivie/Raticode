"""A ticket's full patch, a clean-merge check, and landing its branch in its base.

Workers finish on a branch: an organization ticket's own worktree branch, a Rem run's
temporary worktree branch, or a worktree skill's feature branch. The ticket sheet shows
that branch's patch in the same file format as the desktop's commit diff view, and the
user's Accept lands it in the branch it came from:

- a branch that already contains the base fast-forwards it;
- otherwise Raticode checks that the two merge without conflicts (``git merge-tree``,
  which touches no checkout) and records a merge commit;
- a conflict changes nothing and names the files, so the user can ask the worker to
  rebase.

Landing only moves the base forward from the commit Raticode checked (compare and swap).
When the base is checked out in a folder, that folder must have no uncommitted changes to
tracked files, and it is fast-forwarded there with ``merge --ff-only``. Nothing is pushed.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import threading
from pathlib import Path
from typing import Any

from gofer.utils.process import (
    NO_REPOSITORY_PROGRAMS,
    build_subprocess_env,
    untrusted_repository_git_env,
)

GIT_TIMEOUT_SECONDS = 60
# The desktop's commit diff view loads at most this much file text.
MAX_PATCH_BYTES = 16 * 1024 * 1024
MAX_FILES = 300
# How far back the base branch's reflog is searched for where a landed branch forked.
REFLOG_DEPTH = 500
MAX_COMMENTS = 100
MAX_COMMENT_CHARS = 4000
ZERO = re.compile(r"^0+$")
HASH = re.compile(r"^[0-9a-f]{40,64}$")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class PatchError(RuntimeError):
    pass


class LandConflict(PatchError):
    """The branch and its base don't merge cleanly; nothing was changed."""

    def __init__(self, message: str, files: list[str]) -> None:
        super().__init__(message)
        self.files = files


def _run(cwd: Path | str, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        env = untrusted_repository_git_env(cwd, build_subprocess_env())
        result = subprocess.run(
            ["git", *NO_REPOSITORY_PROGRAMS, "-C", str(cwd), *args],
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env={**env, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PatchError(str(exc)) from exc
    if check and result.returncode:
        message = result.stderr.decode(errors="replace").strip()
        raise PatchError(message or f"git {args[0]} failed")
    return result


def _out(cwd: Path | str, *args: str) -> str:
    return _run(cwd, *args).stdout.decode(errors="replace").strip()


def _ok(cwd: Path | str, *args: str) -> bool:
    return _run(cwd, *args, check=False).returncode == 0


def commit_of(repository: str, ref: str) -> str | None:
    result = _run(repository, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False)
    value = result.stdout.decode().strip()
    return value if result.returncode == 0 and HASH.match(value) else None


def branch_commit(repository: str, branch: str) -> str | None:
    if not branch or branch.startswith("-"):
        return None
    return commit_of(repository, f"refs/heads/{branch}")


def is_ancestor(repository: str, older: str, newer: str) -> bool:
    return _ok(repository, "merge-base", "--is-ancestor", older, newer)


def current_branch(directory: str | Path) -> str | None:
    """The branch checked out in ``directory``, or None when detached or not in git."""
    result = _run(directory, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    name = result.stdout.decode().strip()
    return name if result.returncode == 0 and name else None


def fork_point(
    repository: str,
    tip: str,
    base_branch: str | None,
    base_commit: str | None = None,
    branch: str | None = None,
) -> str | None:
    """Where the work on ``tip`` starts, so its patch shows only this branch's changes.

    Before landing, that's the merge base with the base branch. Once the base contains
    the branch (a worker fast-forwarded it), the base's reflog says what it was just before
    the branch arrived; the merge base with that is where the work began.
    """
    base_tip = branch_commit(repository, base_branch) if base_branch else None
    if base_tip and not is_ancestor(repository, tip, base_tip):
        return _out(repository, "merge-base", tip, base_tip) or None
    if base_tip:
        created = (
            _run(repository, "log", "-g", "--format=%H", f"refs/heads/{branch}", "--", check=False)
            .stdout.decode()
            .split()
            if branch
            else []
        )
        if created and created[-1] == tip:
            return tip  # The branch never moved from where it was made: no commits of its own.
        log = _run(
            repository,
            "log",
            "-g",
            "--format=%H",
            f"-n{REFLOG_DEPTH}",
            f"refs/heads/{base_branch}",
            "--",
            check=False,
        )
        for entry in log.stdout.decode().split():
            if HASH.match(entry) and not is_ancestor(repository, tip, entry):
                found = _run(repository, "merge-base", tip, entry, check=False)
                value = found.stdout.decode().strip()
                return value if found.returncode == 0 and value else None
    if base_commit and commit_of(repository, base_commit):
        found = _run(repository, "merge-base", tip, base_commit, check=False)
        value = found.stdout.decode().strip()
        return value if found.returncode == 0 and value else None
    return None


def _inside(checkout: Path, relative: str) -> Path | None:
    path = checkout / relative
    try:
        parent = path.parent.resolve(strict=True)
    except OSError:
        return None
    if not parent.is_relative_to(checkout.resolve()):
        return None
    return path


def _read_worktree_file(checkout: Path, relative: str, limit: int) -> bytes:
    """A file as the worker left it, without following links out of the checkout."""
    path = _inside(checkout, relative)
    if path is None:
        return b""
    try:
        info = os.lstat(path)
    except OSError:
        return b""
    if stat.S_ISLNK(info.st_mode):
        return os.fsencode(os.readlink(path))
    if not stat.S_ISREG(info.st_mode):
        return b""
    if info.st_size > limit:
        raise PatchError("This patch is too large to show here.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read(limit + 1)


def _uncommitted(checkout: Path) -> list[str]:
    """Tracked and untracked files the worker changed but didn't commit."""
    if not checkout.is_dir():
        return []
    result = _run(checkout, "status", "--porcelain=v1", "-z", "--untracked-files=all", check=False)
    if result.returncode:
        return []
    paths: list[str] = []
    records = result.stdout.decode(errors="replace").split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        paths.append(record[3:])
        if record[0] in "RC":
            index += 1  # The rename's source path follows.
    return sorted(set(paths))


def _untracked(checkout: Path) -> list[str]:
    result = _run(checkout, "ls-files", "-z", "--others", "--exclude-standard", check=False)
    return [p for p in result.stdout.decode(errors="replace").split("\0") if p]


def read_patch(
    repository: str,
    branch: str,
    *,
    base_branch: str | None = None,
    base_commit: str | None = None,
    checkout: str | None = None,
) -> dict[str, Any]:
    """The branch's patch since it forked, as the commit diff view's file list.

    When the worker's checkout still exists, uncommitted changes are part of the patch:
    accepting commits them before landing.
    """
    tip = branch_commit(repository, branch)
    if tip is None:
        raise PatchError(f"Branch {branch} no longer exists.")
    base = fork_point(repository, tip, base_branch, base_commit, branch)
    if base is None:
        raise PatchError(
            f"Raticode can't tell where {branch} started, so it can't show only its changes."
        )
    live = Path(checkout) if checkout and Path(checkout).is_dir() else None
    if live is not None and branch_commit(repository, branch) != commit_of(str(live), "HEAD"):
        live = None  # The checkout moved to another commit; show the branch as committed.
    pending = _uncommitted(live) if live else []
    where = str(live) if pending and live else repository
    args = ["diff", "--raw", "-z", "--no-abbrev", "--find-renames", "--no-ext-diff"]
    args += ["--no-textconv", "--no-color", base]
    if not pending:
        args.append(tip)
    raw = _run(where, *args, "--").stdout.decode(errors="surrogateescape")
    records = raw.split("\0")
    files: list[dict[str, Any]] = []
    total = 0
    truncated = False

    def blob(oid: str, mode: str, path: str) -> bytes:
        nonlocal total
        if mode == "160000":
            return f"Subproject commit {oid}\n".encode()
        if ZERO.match(oid):
            if live is None or mode == "000000":
                return b""
            content = _read_worktree_file(live, path, MAX_PATCH_BYTES - total)
        else:
            content = _run(repository, "cat-file", "blob", oid).stdout
        total += len(content)
        if total > MAX_PATCH_BYTES:
            raise PatchError("This patch is too large to show here.")
        return content

    def add(
        old_path: str, path: str, status: str, old_mode: str, new_mode: str, a: bytes, b: bytes
    ) -> None:
        binary = b"\0" in a or b"\0" in b
        files.append(
            {
                "oldPath": old_path,
                "path": path,
                "status": status,
                "oldMode": old_mode,
                "newMode": new_mode,
                "binary": binary,
                "submodule": "160000" in (old_mode, new_mode),
                "original": None if binary else a.decode(errors="replace"),
                "modified": None if binary else b.decode(errors="replace"),
                "uncommitted": path in pending or old_path in pending,
            }
        )

    index = 0
    while index < len(records) and records[index]:
        header = records[index].strip()
        index += 1
        old_mode, new_mode, old_oid, new_oid, status = header.lstrip(":").split(" ")[:5]
        old_path = _decode_path(records[index])
        index += 1
        path = old_path
        if status[:1] in {"R", "C"}:
            path = _decode_path(records[index])
            index += 1
        if len(files) >= MAX_FILES:
            truncated = True
            break
        add(
            old_path,
            path,
            status[:1],
            old_mode,
            new_mode,
            blob(old_oid, old_mode, old_path),
            blob(new_oid, new_mode, path),
        )
    if live is not None and pending:
        for path in _untracked(live):
            if len(files) >= MAX_FILES:
                truncated = True
                break
            add(path, path, "A", "000000", "100644", b"", blob("0", "100644", path))
    return {
        "branch": branch,
        "base": base,
        "tip": tip,
        "baseBranch": base_branch,
        "files": files,
        "uncommitted": pending,
        "truncated": truncated,
        "revision": patch_revision(tip, files),
    }


def _decode_path(value: str) -> str:
    return value.encode(errors="surrogateescape").decode(errors="replace")


def patch_revision(tip: str, files: list[dict[str, Any]]) -> str:
    """Names exactly what the user reviewed: the tip plus any uncommitted file contents."""
    digest = hashlib.sha256(tip.encode())
    for item in files:
        if item.get("uncommitted"):
            digest.update(b"\0" + item["path"].encode() + b"\0")
            digest.update(str(item.get("modified")).encode(errors="replace"))
    return digest.hexdigest()[:32]


def merge_state(repository: str, base_branch: str, tip: str) -> dict[str, Any]:
    """Whether ``tip`` can land in ``base_branch`` now, without touching any checkout."""
    base_tip = branch_commit(repository, base_branch)
    if base_tip is None:
        raise PatchError(f"The base branch {base_branch} no longer exists.")
    if is_ancestor(repository, tip, base_tip):
        return {"state": "landed", "baseTip": base_tip, "conflicts": []}
    if is_ancestor(repository, base_tip, tip):
        return {"state": "fast_forward", "baseTip": base_tip, "conflicts": [], "tree": None}
    result = _run(
        repository,
        "merge-tree",
        "--write-tree",
        "--name-only",
        "--no-messages",
        "-z",
        base_tip,
        tip,
        check=False,
    )
    parts = result.stdout.decode(errors="replace").split("\0")
    if result.returncode == 0:
        return {"state": "clean", "baseTip": base_tip, "conflicts": [], "tree": parts[0]}
    if result.returncode == 1:
        conflicts = sorted({p for p in parts[1:] if p})
        return {"state": "conflict", "baseTip": base_tip, "conflicts": conflicts}
    message = result.stderr.decode(errors="replace").strip()
    raise PatchError(message or "Raticode couldn't check whether the branch merges cleanly.")


def _checked_out(repository: str, branch: str) -> str | None:
    out = _out(repository, "worktree", "list", "--porcelain")
    path = ""
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line == f"branch refs/heads/{branch}":
            return path
    return None


def _lock(repository: str) -> threading.Lock:
    key = str(Path(repository).resolve())
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def commit_leftovers(checkout: str, message: str) -> bool:
    """Commit what the worker left uncommitted in its checkout. True when it committed."""
    path = Path(checkout)
    if not path.is_dir() or not _uncommitted(path):
        return False
    _run(path, "add", "-A")
    _run(path, "-c", "commit.gpgsign=false", "commit", "--no-verify", "-q", "-m", message[:200])
    return True


def land(
    repository: str,
    branch: str,
    base_branch: str,
    *,
    expected_tip: str,
    message: str,
) -> dict[str, Any]:
    """Land ``branch`` in ``base_branch`` if it still is what the user reviewed.

    Returns ``{"state": "landed"|"fast_forward"|"clean", "commit": ...}``. Raises
    LandConflict on conflicts and PatchError when the branch moved or the base's folder
    has uncommitted changes. Never pushes and never rewrites the base.
    """
    if not base_branch or base_branch == branch or base_branch.startswith("-"):
        raise PatchError("This ticket's branch has no base branch to land in.")
    with _lock(repository):
        tip = branch_commit(repository, branch)
        if tip is None:
            raise PatchError(f"Branch {branch} no longer exists.")
        if tip != expected_tip:
            raise PatchError(
                f"{branch} changed since you opened its patch. Review the new changes first."
            )
        state = merge_state(repository, base_branch, tip)
        if state["state"] == "landed":
            return {"state": "landed", "commit": tip, "baseBranch": base_branch}
        if state["state"] == "conflict":
            raise LandConflict(
                f"{branch} conflicts with {base_branch} in "
                f"{len(state['conflicts'])} file(s), so nothing was landed.",
                state["conflicts"],
            )
        base_tip = state["baseTip"]
        new = tip
        if state["state"] == "clean":
            new = _out(
                repository,
                "commit-tree",
                state["tree"],
                "-p",
                base_tip,
                "-p",
                tip,
                "-m",
                message[:2000],
            )
        folder = _checked_out(repository, base_branch)
        if folder:
            dirty = _run(
                folder, "status", "--porcelain", "--untracked-files=no", check=False
            ).stdout.strip()
            if dirty:
                raise PatchError(
                    f"{base_branch} is checked out at {folder} with uncommitted changes. "
                    "Commit or stash them there, then accept again."
                )
            # Fast-forwards the folder's files and branch together; refuses if the base moved.
            moved = _run(folder, "merge", "--ff-only", "-q", new, check=False)
            if moved.returncode:
                raise PatchError(
                    f"Couldn't move {base_branch} in {folder}: "
                    + (moved.stderr.decode(errors="replace").strip() or "git merge failed")
                )
        else:
            _run(
                repository,
                "update-ref",
                "-m",
                f"raticode: land {branch}",
                f"refs/heads/{base_branch}",
                new,
                base_tip,
            )
        return {"state": state["state"], "commit": new, "baseBranch": base_branch}


def review_message(note: str, comments: Any) -> str:
    """The request for changes the worker reads: the user's note, then each line comment."""
    if comments is not None and not isinstance(comments, list):
        raise ValueError("Line comments must be a list")
    items = list(comments or [])
    if len(items) > MAX_COMMENTS:
        raise ValueError(f"Send at most {MAX_COMMENTS} line comments at once")
    lines: list[str] = []
    if note.strip():
        lines.append(note.strip())
    if items:
        lines.append(
            "Line comments on the patch:"
            if lines
            else "Please address these line comments on the patch:"
        )
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each line comment needs path, line and body")
        path, body = item.get("path"), item.get("body")
        line, side = item.get("line"), item.get("side", "modified")
        if not isinstance(path, str) or not path or len(path) > 1000:
            raise ValueError("Each line comment needs a file path")
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            raise ValueError("Each line comment needs a line number")
        if side not in {"original", "modified"}:
            raise ValueError("A line comment is on the original or the modified side")
        if not isinstance(body, str) or not body.strip() or len(body) > MAX_COMMENT_CHARS:
            raise ValueError(f"Each line comment needs 1 to {MAX_COMMENT_CHARS} characters")
        where = f"{path}:{line}" + (" (before the change)" if side == "original" else "")
        lines.append(f"- {where}: {body.strip()}")
    if not lines:
        raise ValueError("Write a note or add a line comment")
    return "\n".join(lines)


def rebase_message(branch: str, base_branch: str, conflicts: list[str]) -> str:
    shown = ", ".join(conflicts[:20]) + (
        f" and {len(conflicts) - 20} more" if len(conflicts) > 20 else ""
    )
    return (
        f"Your branch {branch} conflicts with {base_branch}"
        + (f" in {shown}" if shown else "")
        + f". Rebase {branch} onto the current {base_branch}, resolve the conflicts, rerun the "
        "checks and commit, so the user can accept and land it."
    )
