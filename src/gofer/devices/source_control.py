"""Source control status on a paired phone, read-only.

The phone sees the Git state of the desktop projects its catalog names (open,
shared projects), the same scope as workflows and swarms. It names a project by
its catalog ID and never sends a path. Nothing here changes a repository: no
fetch, pull, push, commit, stage or branch switch. Only Git's read commands run,
with file system monitors off and optional locks skipped, as the desktop's own
automatic status refresh does.

Results carry repository-relative file names, branch names and commit subjects,
which travel inside the paired end-to-end session like every desktop result. No
absolute folders, remote URLs or author e-mail addresses are sent.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gofer.devices.desktop_control import Refused
from gofer.utils.process import NO_REPOSITORY_PROGRAMS, untrusted_repository_git_env

READS = frozenset(
    {
        "source_control.list",
        "source_control.read",
        "source_control.history",
        "source_control.commit",
        "source_control.diff",
    }
)
PROJECT_LIMIT = 24
FILE_LIMIT = 200
COMMIT_LIMIT = 15
WORKTREE_LIMIT = 12
BRANCH_LIMIT = 200
REF_LIMIT = 300
HISTORY_LIMIT = 400
HISTORY_STEP = 100
MESSAGE_LIMIT = 2000
COMMIT_FILE_LIMIT = 300
DIFF_LIMIT = 200_000
TIMEOUT = 10.0
OUTPUT_LIMIT = 4 * 1024 * 1024
NOT_OPEN = "This project is no longer open on your computer."
NO_GIT = "Git isn't installed on your computer."
NOT_REPOSITORY = "This project isn't a Git repository."
MISSING = "This project's folder is missing on your computer."
FAILED = "Your computer couldn't read this repository's status."
SLOW = "Git took too long to answer on your computer."
NO_FILE = "This change is no longer there. Refresh and try again."
NO_COMMIT = "This commit isn't in the repository on your computer."
NO_REFS = "Choose branches or tags that exist on your computer."
SENSITIVE = "This file may hold credentials, so its changes stay on your computer."
OPERATIONS = (
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
)
CONFLICTS = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


class GitError(Exception):
    pass


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _environment() -> dict[str, str]:
    # A desktop started from a Git hook or another tool can inherit GIT_DIR and
    # friends, which would point every read at the wrong repository.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    return env


def git(root: str, *args: str, codes: tuple[int, ...] = (0,)) -> str:
    """Run one read-only Git command in ``root`` and return its output."""
    try:
        done = subprocess.run(
            # Status runs clean filters on changed files; the repository's own filter
            # commands are blanked like the desktop's (git-status.cjs).
            [
                "git",
                *NO_REPOSITORY_PROGRAMS,
                "-c",
                "core.quotepath=false",
                "-C",
                root,
                *args,
            ],
            capture_output=True,
            timeout=TIMEOUT,
            env=untrusted_repository_git_env(root, _environment()),
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(SLOW) from exc
    except OSError as exc:
        raise GitError(NO_GIT) from exc
    if done.returncode not in codes:
        raise GitError(done.stderr.decode("utf-8", "replace").strip()[:300] or FAILED)
    return done.stdout[:OUTPUT_LIMIT].decode("utf-8", "replace")


def _status_kind(xy: str) -> str:
    if xy in CONFLICTS:
        return "conflict"
    if xy == "??":
        return "untracked"
    if "D" in xy:
        return "deleted"
    if "R" in xy:
        return "renamed"
    if "A" in xy or "C" in xy:
        return "added"
    return "modified"


def parse_status(output: str, prefix: str = "") -> dict[str, Any]:
    """Parse ``git status --porcelain=v2 --branch -z`` like the desktop's panel does."""
    records = output.split("\0")
    branch, oid, upstream = "", "", ""
    ahead: int | None = None
    behind: int | None = None
    files: list[dict[str, Any]] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if record.startswith("# branch.oid "):
            oid = record[13:]
        elif record.startswith("# branch.head "):
            branch = "" if record[14:] == "(detached)" else record[14:]
        elif record.startswith("# branch.upstream "):
            upstream = record[18:]
        elif record.startswith("# branch.ab "):
            counts = record[12:].split(" ")
            if len(counts) == 2:
                ahead, behind = abs(int(counts[0])), abs(int(counts[1]))
        elif record[:2] in {"1 ", "2 ", "u "}:
            fields = {"1": 8, "2": 9, "u": 10}[record[0]]
            parts = record.split(" ", fields)
            if len(parts) <= fields:
                continue
            xy = parts[1].replace(".", " ")
            path = parts[fields]
            if record[0] == "2":
                index += 1  # The original path of a rename or copy.
            kind = _status_kind(xy)
            files.append(
                {
                    "path": path,
                    "status": kind,
                    "staged": kind != "conflict" and xy[0] != " ",
                    "unstaged": kind == "conflict" or xy[1] != " ",
                }
            )
        elif record.startswith("? "):
            files.append(
                {"path": record[2:], "status": "untracked", "staged": False, "unstaged": True}
            )
    if prefix:
        files = [
            {**item, "path": item["path"][len(prefix) :]}
            for item in files
            if item["path"].startswith(prefix)
        ]
    return {
        "branch": branch,
        "detached": not branch and bool(oid) and oid != "(initial)",
        "head": "" if oid in {"", "(initial)"} else oid[:12],
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "files": files,
    }


def _when(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def _location(root: str) -> tuple[str, str, str]:
    """The repository's top folder, its Git folder, and ``root`` relative to the top."""
    out = git(root, "rev-parse", "--show-toplevel", "--absolute-git-dir", "--show-prefix")
    lines = out.split("\n")
    if len(lines) < 3:
        raise GitError(FAILED)
    return lines[0], lines[1], lines[2]


def _common_dir(root: str) -> str:
    return git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()


def _operation(git_dir: str) -> str:
    for marker, kind in OPERATIONS:
        if os.path.exists(os.path.join(git_dir, marker)):
            return kind
    return ""


def _fetched_at(common: str) -> str:
    try:
        return _when(os.stat(os.path.join(common, "FETCH_HEAD")).st_mtime)
    except OSError:
        return ""


def _commits(root: str, limit: int) -> list[dict[str, str]]:
    try:
        out = git(
            root, "log", f"-n{limit}", "--no-color", "--format=%H%x1f%s%x1f%an%x1f%aI%x1e", "--"
        )
    except GitError:
        return []  # A new repository has no commits yet.
    commits = []
    for record in out.split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) == 4:
            commits.append(
                {
                    "hash": parts[0][:12],
                    "subject": _text(parts[1], 200),
                    "author": _text(parts[2], 80),
                    "at": parts[3],
                }
            )
    return commits


def _stashes(root: str) -> int:
    try:
        return len(
            [line for line in git(root, "stash", "list", "--format=%gd").split("\n") if line]
        )
    except GitError:
        return 0


def _worktree_name(path: str, top: str) -> str:
    try:
        relative = Path(path).resolve().relative_to(Path(top).resolve())
        return str(relative) if str(relative) != "." else Path(path).name
    except ValueError:
        return Path(path).name


def _worktrees(root: str, top: str) -> tuple[list[dict[str, Any]], int]:
    try:
        out = git(root, "worktree", "list", "--porcelain", "-z")
    except GitError:
        return [], 0
    trees: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for field in [*out.split("\0"), ""]:
        if not field:
            if current.get("path"):
                trees.append(current)
            current = {}
            continue
        key, _, value = field.partition(" ")
        if key == "worktree":
            current = {"path": value}
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached":
            current["detached"] = True
        elif key in {"locked", "prunable"}:
            current[key] = True
    top_real = os.path.realpath(top)
    shown = []
    for tree in trees[:WORKTREE_LIMIT]:
        path = tree["path"]
        entry: dict[str, Any] = {
            "name": _text(_worktree_name(path, top), 160),
            "branch": _text(tree.get("branch"), 200),
            "detached": bool(tree.get("detached")),
            "current": os.path.realpath(path) == top_real,
            "locked": bool(tree.get("locked")),
            "missing": not os.path.isdir(path),
            "changes": None,
        }
        if not entry["missing"]:
            try:
                status = git(path, "status", "--porcelain=v2", "-z", "--untracked-files=all")
                entry["changes"] = len(parse_status(status)["files"])
            except GitError:
                entry["changes"] = None
        shown.append(entry)
    return shown, max(0, len(trees) - WORKTREE_LIMIT)


def _counts(files: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(files),
        "staged": sum(1 for f in files if f["staged"]),
        "unstaged": sum(1 for f in files if f["unstaged"] and f["status"] != "untracked"),
        "untracked": sum(1 for f in files if f["status"] == "untracked"),
        "conflicts": sum(1 for f in files if f["status"] == "conflict"),
    }


def _lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if line]


def _quiet(root: str, *args: str) -> str:
    try:
        return git(root, *args)
    except GitError:
        return ""


def _refs(root: str) -> list[dict[str, Any]]:
    """Branches, remote branches and tags, as the desktop's history filter lists them."""
    head_ref = _quiet(root, "symbolic-ref", "-q", "HEAD").strip()
    out = _quiet(
        root,
        "for-each-ref",
        "--format=%(refname)%09%(objectname)%09%(*objectname)%09%(symref)"
        "%09%(upstream:short)%09%(upstream:track)",
        "refs/heads",
        "refs/remotes",
        "refs/tags",
    )
    refs = []
    for line in _lines(out):
        parts = (line.split("\t") + [""] * 6)[:6]
        ident, obj, peeled, symbolic, upstream, track = parts
        if symbolic:
            continue
        kind = (
            "local"
            if ident.startswith("refs/heads/")
            else "remote"
            if ident.startswith("refs/remotes/")
            else "tag"
        )
        ahead = re.search(r"ahead (\d+)", track)
        behind = re.search(r"behind (\d+)", track)
        refs.append(
            {
                "id": _text(ident, 300),
                "name": _text(re.sub(r"^refs/(heads|remotes|tags)/", "", ident), 250),
                "hash": (peeled or obj)[:40],
                "type": kind,
                "current": ident == head_ref,
                "upstream": _text(upstream, 250),
                "upstream_gone": "[gone]" in track,
                "ahead": int(ahead.group(1)) if ahead else 0,
                "behind": int(behind.group(1)) if behind else 0,
            }
        )
    return refs[:REF_LIMIT]


def parse_history(output: str) -> list[dict[str, Any]]:
    """Parse the desktop's history log format (``git-status.cjs`` parseGitHistory)."""
    commits = []
    for record in output.split("\0"):
        record = record.lstrip("\n")
        if not record:
            continue
        fields = (record.split("\x1f") + [""] * 8)[:8]
        full, short, author, at, subject, message, _refs_text, rest = fields
        parents, *stats = rest.split("\n")
        insertions = deletions = binary = 0
        for line in stats:
            added, _, rest_line = line.partition("\t")
            deleted = rest_line.partition("\t")[0]
            if added == "-" and deleted == "-":
                binary += 1
            if added.isdigit():
                insertions += int(added)
            if deleted.isdigit():
                deletions += int(deleted)
        body = message.strip()
        body = body[len(subject) :].strip() if body.startswith(subject) else body
        commits.append(
            {
                "hash": full[:40],
                "short": short[:12],
                "author": _text(author, 80),
                "at": at[:40],
                "subject": _text(subject, 300),
                "body": _text(body, MESSAGE_LIMIT),
                "parents": [item[:40] for item in parents.split(" ") if item][:8],
                "insertions": insertions,
                "deletions": deletions,
                "binary_files": binary,
            }
        )
    return commits


def history(root: str, refs: Any = None, limit: Any = None) -> dict[str, Any]:
    """Commits for the branch graph: newest first in topological order, with parents."""
    available = _refs(root)
    head = _quiet(root, "rev-parse", "--verify", "-q", "HEAD").strip()
    categories = {"local": "--branches", "remote": "--remotes", "tag": "--tags"}
    if refs is None:
        revisions = ["--branches", "--remotes", "--tags", *(["HEAD"] if head else [])]
    else:
        if not isinstance(refs, list) or len(refs) > 50:
            raise Refused("invalid", NO_REFS)
        known = {ref["id"] for ref in available}
        revisions = []
        for ref in dict.fromkeys(refs):
            if ref == "HEAD":
                revisions += ["HEAD"] if head else []
            elif ref in categories:
                if any(item["type"] == ref for item in available):
                    revisions.append(categories[ref])
            elif isinstance(ref, str) and ref in known:
                revisions.append(ref)
            else:
                raise Refused("invalid", NO_REFS)
    count = limit if type(limit) is int else HISTORY_STEP
    count = max(HISTORY_STEP, min(HISTORY_LIMIT, count))
    if not revisions or (not head and not available):
        return {"refs": available, "head": head, "commits": [], "has_more": False}
    output = git(
        root,
        "log",
        "--topo-order",
        f"--max-count={count + 1}",
        "--date=iso-strict",
        "--numstat",
        "--diff-merges=first-parent",
        "--no-color",
        "--pretty=format:%x00%H%x1f%h%x1f%an%x1f%aI%x1f%s%x1f%B%x1f%D%x1f%P",
        *revisions,
        "--",
    )
    commits = parse_history(output)
    labels: dict[str, list[str]] = {}
    for ref in available:
        labels.setdefault(ref["hash"], []).append(ref["id"])
    shown = [
        {**commit, "labels": labels.get(commit["hash"], []), "is_head": commit["hash"] == head}
        for commit in commits[:count]
    ]
    return {"refs": available, "head": head, "commits": shown, "has_more": len(commits) > count}


def _commit_id(root: str, value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{7,64}", value):
        raise Refused("not_found", NO_COMMIT)
    try:
        return git(root, "rev-parse", "--verify", "-q", f"{value}^{{commit}}").strip()
    except GitError:
        raise Refused("not_found", NO_COMMIT) from None


def commit_files(top: str, full: str) -> list[dict[str, Any]]:
    """Files one commit changed, against its first parent, with line counts."""
    common = ("--format=", "-z", "--find-renames", "--diff-merges=first-parent", "--root")
    names = git(top, "show", *common, "--name-status", full, "--").split("\0")
    stats = git(top, "show", *common, "--numstat", full, "--").split("\0")
    counts: dict[str, tuple[str, str]] = {}
    index = 0
    while index < len(stats):
        record = stats[index].lstrip("\n")
        index += 1
        if not record:
            continue
        added, deleted, path = (record.split("\t", 2) + ["", ""])[:3]
        if not path and index + 1 < len(stats):  # A rename: old and new paths follow.
            path = stats[index + 1]
            index += 2
        counts[path] = (added, deleted)
    files = []
    index = 0
    while index < len(names):
        code = names[index].lstrip("\n")
        index += 1
        if not code:
            continue
        old = ""
        if code[0] in "RC" and index + 1 < len(names):
            old, path = names[index], names[index + 1]
            index += 2
        elif index < len(names):
            path = names[index]
            index += 1
        else:
            break
        added, deleted = counts.get(path, ("0", "0"))
        files.append(
            {
                "path": _text(path, 400),
                "old_path": _text(old, 400),
                "status": {"A": "added", "D": "deleted", "R": "renamed", "C": "added"}.get(
                    code[0], "modified"
                ),
                "insertions": int(added) if added.isdigit() else 0,
                "deletions": int(deleted) if deleted.isdigit() else 0,
                "binary": added == "-" and deleted == "-",
            }
        )
    return files


def commit_detail(root: str, value: Any) -> dict[str, Any]:
    top, _, _ = _location(root)
    full = _commit_id(root, value)
    meta = git(
        top,
        "show",
        "--no-patch",
        "--date=iso-strict",
        "--pretty=format:%x00%H%x1f%h%x1f%an%x1f%aI%x1f%s%x1f%B%x1f%D%x1f%P",
        full,
        "--",
    )
    commit = parse_history(meta)[0]
    files = commit_files(top, full)
    commit["files"] = files[:COMMIT_FILE_LIMIT]
    commit["more_files"] = max(0, len(files) - COMMIT_FILE_LIMIT)
    commit["insertions"] = sum(f["insertions"] for f in files)
    commit["deletions"] = sum(f["deletions"] for f in files)
    commit["binary_files"] = sum(1 for f in files if f["binary"])
    return commit


def _diff_text(output: str) -> dict[str, Any]:
    truncated = len(output) > DIFF_LIMIT
    text = output[:DIFF_LIMIT]
    if truncated:
        text = text[: text.rfind("\n") + 1]
    binary = "\nBinary files " in "\n" + output[:4000] or "GIT binary patch" in output[:4000]
    return {"text": "" if binary else text, "truncated": truncated, "binary": binary}


def diff(root: str, params: dict[str, Any], data_dir: Path | None = None) -> dict[str, Any]:
    """One file's changes: in the working tree, staged, or in one commit.

    Only a file the current status or that commit lists can be read, and never one
    that looks like it holds credentials."""
    from gofer.ui.rem_home import sensitive_path

    path = params.get("path")
    source = params.get("source")
    if not isinstance(path, str) or not path or len(path) > 400 or "\0" in path:
        raise Refused("not_found", NO_FILE)
    top, _, prefix = _location(root)
    flags = ("--no-ext-diff", "--no-textconv", "--no-color", "-U3")
    if source == "commit":
        full = _commit_id(root, params.get("hash"))
        if not any(f["path"] == path for f in commit_files(top, full)):
            raise Refused("not_found", NO_FILE)
        target = Path(top) / path
        args: tuple[str, ...] = (
            "show",
            "--format=",
            *flags,
            "--find-renames",
            "--diff-merges=first-parent",
            "--root",
            full,
            "--",
            f":(top,literal){path}",
        )
        cwd = top
    elif source in {"staged", "unstaged"}:
        status = git(root, "status", "--porcelain=v2", "-z", "--untracked-files=all", "--", ".")
        entry = next((f for f in parse_status(status, prefix)["files"] if f["path"] == path), None)
        if entry is None or not entry[source]:
            raise Refused("not_found", NO_FILE)
        target = Path(root) / path
        cwd = root
        if entry["status"] == "untracked":
            args = ("diff", "--no-index", *flags, "--", os.devnull, path)
        elif source == "staged":
            args = ("diff", "--cached", *flags, "--find-renames", "--", f":(literal){path}")
        else:
            args = ("diff", *flags, "--", f":(literal){path}")
    else:
        raise Refused("invalid", NO_FILE)
    if sensitive_path(target.resolve(), data_dir, opened=Path(root).resolve()):
        raise Refused("unauthorized", SENSITIVE)
    try:
        output = git(cwd, *args, codes=(0, 1))
    except GitError as exc:
        raise Refused("unavailable", SLOW if str(exc) == SLOW else FAILED) from None
    return {"path": path, "source": source, **_diff_text(output)}


def project_status(root: str, detail: bool = False) -> dict[str, Any]:
    """One project's Git state for the phone. Never raises for repository problems."""
    if shutil.which("git") is None:
        return {"state": "unavailable", "detail": NO_GIT}
    if not os.path.isdir(root):
        return {"state": "missing", "detail": MISSING}
    try:
        top, git_dir, prefix = _location(root)
    except GitError as exc:
        message = str(exc)
        if message in {NO_GIT, SLOW}:
            return {"state": "unavailable", "detail": message}
        if "not a git repository" in message.lower():
            return {"state": "not_repository", "detail": NOT_REPOSITORY}
        return {"state": "unavailable", "detail": FAILED}
    try:
        status = parse_status(
            git(
                root,
                "status",
                "--porcelain=v2",
                "--branch",
                "-z",
                "--untracked-files=all",
                "--",
                ".",
            ),
            prefix,
        )
    except GitError as exc:
        return {"state": "unavailable", "detail": SLOW if str(exc) == SLOW else FAILED}
    try:
        common = _common_dir(root)
    except GitError:
        common = git_dir
    commits = _commits(root, COMMIT_LIMIT if detail else 1)
    files = status.pop("files")
    result: dict[str, Any] = {
        "state": "ok",
        **status,
        "changes": _counts(files),
        "operation": _operation(git_dir),
        "fetched_at": _fetched_at(common),
        "last_commit": commits[0] if commits else None,
        "subfolder": prefix.rstrip("/"),
    }
    if detail:
        ordered = sorted(files, key=lambda f: (f["status"] != "conflict", f["path"]))
        result["files"] = [{**f, "path": _text(f["path"], 400)} for f in ordered[:FILE_LIMIT]]
        result["more_files"] = max(0, len(files) - FILE_LIMIT)
        result["commits"] = commits
        result["stashes"] = _stashes(root)
        result["worktrees"], result["more_worktrees"] = _worktrees(root, top)
        refs = _refs(root)
        checked_out = {tree["branch"] for tree in result["worktrees"] if tree["branch"]}
        result["branches"] = [
            {
                "name": ref["name"],
                "current": ref["current"],
                "worktree": ref["name"] in checked_out and not ref["current"],
                "upstream": ref["upstream"],
                "ahead": ref["ahead"],
                "behind": ref["behind"],
                "upstream_gone": ref["upstream_gone"],
            }
            for ref in refs
            if ref["type"] == "local"
        ][:BRANCH_LIMIT]
        result["remotes"] = len(_lines(_quiet(root, "remote")))
    return result


def _repository(root: str) -> None:
    """Refuses a read that needs a working repository, with the list's sentence for why not."""
    state = project_status(root) if not os.path.isdir(root) or shutil.which("git") is None else None
    if state is not None:
        raise Refused("unavailable", state["detail"])
    try:
        _location(root)
    except GitError as exc:
        missing = "not a git repository" in str(exc).lower()
        raise Refused("unavailable", NOT_REPOSITORY if missing else FAILED) from None


def gather(
    op: str,
    params: dict[str, Any],
    projects: dict[str, dict[str, str]],
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Read Git for a phone view. Runs outside the device lock."""
    if op != "source_control.list":
        identifier = _text(params.get("project_id"), 128)
        project = projects.get(identifier)
        if project is None:
            raise Refused("not_found", NOT_OPEN)
        root = project["root"]
        if op == "source_control.read":
            value: dict[str, Any] = project_status(root, True)
        else:
            _repository(root)
            try:
                if op == "source_control.history":
                    value = history(root, params.get("refs"), params.get("limit"))
                elif op == "source_control.commit":
                    value = commit_detail(root, params.get("hash"))
                else:
                    value = diff(root, params, data_dir)
            except GitError as exc:
                raise Refused("unavailable", SLOW if str(exc) == SLOW else FAILED) from None
        return {"project_id": identifier, "root": root, "status": value}
    chosen = list(projects.items())[:PROJECT_LIMIT]
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda item: project_status(item[1]["root"]), chosen))
    return {
        "projects": [
            {"project_id": key, "root": value["root"], "status": status}
            for (key, value), status in zip(chosen, statuses, strict=True)
        ],
        "more": max(0, len(projects) - PROJECT_LIMIT),
    }


def read(op: str, data: dict[str, Any], projects: dict[str, dict[str, str]]) -> dict[str, Any]:
    """The phone's answer, keeping only projects that are still open under the same folder."""

    def still_open(entry: dict[str, Any]) -> bool:
        project = projects.get(entry["project_id"])
        return project is not None and project["root"] == entry["root"]

    def shown(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": entry["project_id"],
            "name": projects[entry["project_id"]]["name"] or Path(entry["root"]).name[:160],
            **entry["status"],
        }

    if op != "source_control.list":
        if not still_open(data):
            raise Refused("not_found", NOT_OPEN)
        return shown(data)
    return {
        "projects": [shown(entry) for entry in data["projects"] if still_open(entry)],
        "more": data["more"],
    }
