"""Decision records: design decisions kept as short Markdown files in the project's repo.

Rem's decision store (``rem_decisions``) is the one book of decisions. A decision stored as
a record (``remember_as: "record"``) also gets a file in the project's decision folder, so
reviewers, every provider and the user's own editor read the same text:

- The folder is the project's ``decisions.folder`` decision (an ADR folder it already
  keeps), or ``.raticode/decisions`` when it has none.
- Files are written on the project's landing branch (``branch.integration``) with git
  plumbing, one commit per file, and a checkout of that branch is fast-forwarded only
  when it has no uncommitted changes. A project with no landing branch decision keeps its
  records in the store until it has one.
- The file is the source of truth for wording. An edit committed outside Raticode is read
  back into the store and marked as edited outside Raticode.
- Rem can only add records. A file Raticode wrote is rewritten only after a change the
  user made (``_may_rewrite``), so Rem never edits, replaces or removes an existing record.
- A written record is never deleted from the store, since ``sync`` would read its file back
  in as a new decision. The user retires it instead: it stays, turned down, and its file
  is rewritten with ``status: rejected``, so ``sync`` keeps matching the file to it.
- ``violations`` is the deterministic part of the check phase: a change that edits the
  decision folder itself, or adds a file a decision in force forbids, fails before review.
  ``matching`` picks the records whose paths a change touches; the reviewer gets their full
  text as an added check.
- ``path_counts`` says how many tracked files on the landing branch each ``applies_to`` and
  ``forbids`` glob matches, so a path that matches nothing (a typo, or a folder that moved)
  or a Never touch path that already has files shows before it misleads a reviewer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_FOLDER = ".raticode/decisions"
FOLDER_TOPIC = "decisions.folder"
BRANCH_TOPIC = "branch.integration"
# A record stays short: a long design belongs in a linked spec, not in every prompt.
RECORD_CHARS = 1500
MAX_FILES = 200
FILE_BYTES = 64_000
GIT_TIMEOUT = 20
# Store status for each status a record file can carry, and back.
FROM_FILE = {
    "accepted": "confirmed",
    "provisional": "provisional",
    "proposed": "pending",
    "superseded": "superseded",
    "deprecated": "superseded",
    "rejected": "rejected",
}
TO_FILE = {
    "confirmed": "accepted",
    "provisional": "provisional",
    "pending": "proposed",
    "superseded": "superseded",
    "rejected": "rejected",
}
SECTIONS = (
    ("context", "Context"),
    ("decision", "Decision"),
    ("options", "Options we didn't choose"),
    ("consequences", "Consequences"),
    ("check", "How we check it"),
)
GLOBS = 10


def _relative(text: str) -> str:
    """``src/a.py`` from ``./src/a.py`` or ``/src/a.py``: a leading ``/`` means the
    project's root. Only a leading ``./`` goes, so ``.env`` and ``.github/**`` keep
    their dots and match only dot paths."""
    while text.startswith(("./", "/")):
        text = text[2:] if text.startswith("./") else text[1:]
    return text


def _globs(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [v for v in re.split(r"[\n,]+", value)]
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value:
        text = _relative(str(item or "").strip().strip("\"'"))
        if text and len(text) <= 200 and ".." not in PurePosixPath(text).parts:
            cleaned.append(text)
    return list(dict.fromkeys(cleaned))[:GLOBS]


class RecordFields(BaseModel):
    """The parts of a decision record beyond the store's question and answer."""

    model_config = ConfigDict(extra="ignore")
    context: str = Field(default="", max_length=RECORD_CHARS)
    options: str = Field(default="", max_length=RECORD_CHARS)
    consequences: str = Field(default="", max_length=RECORD_CHARS)
    check: str = Field(default="", max_length=600)
    # Path globs the decision is about; a change touching one gets it as a review check.
    applies_to: list[str] = Field(default_factory=list)
    # Path globs a change must never add or modify while the decision is in force.
    forbids: list[str] = Field(default_factory=list)
    # Words that suggest a proposal contradicts it; they only flag, never block.
    signals: list[str] = Field(default_factory=list)

    @field_validator("applies_to", "forbids", mode="before")
    @classmethod
    def clean_globs(cls, value: Any) -> list[str]:
        return _globs(value)

    @field_validator("signals", mode="before")
    @classmethod
    def clean_signals(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            value = re.split(r"[\n,]+", value)
        if not isinstance(value, list):
            return []
        words = [" ".join(str(v).split())[:80] for v in value if len(str(v).strip()) >= 3]
        return list(dict.fromkeys(words))[:GLOBS]


def record_of(item: dict[str, Any]) -> dict[str, Any]:
    """An item's record fields, cleaned."""
    return RecordFields.model_validate(item.get("record") or {}).model_dump()


def is_record(item: dict[str, Any]) -> bool:
    return item.get("remember_as") == "record" and bool(item.get("project"))


# Globs --------------------------------------------------------------------------------


@lru_cache(maxsize=512)
def _glob_tokens(pattern: str) -> tuple[tuple[str, ...], bool]:
    """``pattern`` as tokens (``**/``, ``**``, ``*``, ``?`` or one literal character) and
    whether it may match a folder's contents: a plain path with no ``*`` also matches
    everything inside it, and a trailing ``/`` matches everything below."""
    tokens: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            tokens.append("**/")
            i += 3
        elif pattern.startswith("**", i):
            tokens.append("**")
            i += 2
        else:
            tokens.append(pattern[i])
            i += 1
    if pattern.endswith("/"):
        tokens.append("**")
    return tuple(tokens), "*" not in pattern


def _glob_matches(tokens: tuple[str, ...], inside: bool, path: str) -> bool:
    """One pass per token over the path's positions, so the time grows with the pattern's
    length times the path's, never exponentially (a regex built from ``**?**?…`` could
    run for minutes on one path)."""
    n = len(path)
    reach = [True] + [False] * n
    for token in tokens:
        after = [False] * (n + 1)
        if token == "**":
            for i in range(n + 1):
                after[i] = reach[i] or (i > 0 and after[i - 1])
        elif token == "*":
            for i in range(n + 1):
                after[i] = reach[i] or (i > 0 and after[i - 1] and path[i - 1] != "/")
        elif token == "**/":
            # Nothing, or any run of characters that ends with a slash.
            seen = False
            for i in range(n + 1):
                after[i] = reach[i] or (seen and path[i - 1] == "/")
                seen = seen or reach[i]
        else:
            for i in range(n):
                if reach[i] and (path[i] != "/" if token == "?" else path[i] == token):
                    after[i + 1] = True
        reach = after
        if not any(reach):
            return False
    if reach[n]:
        return True
    return inside and any(reach[i] and path[i] == "/" for i in range(n))


def glob_match(path: str, pattern: str) -> bool:
    path = _relative(path)
    pattern = _relative(pattern)
    if not pattern:
        return False
    tokens, inside = _glob_tokens(pattern)
    return _glob_matches(tokens, inside, path)


def any_match(path: str, patterns: list[str]) -> bool:
    return any(glob_match(path, p) for p in patterns)


# Markdown -----------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:50] or "decision"


def file_name(item: dict[str, Any], taken: set[str]) -> str:
    """``0012-workflows-are-rattish.md`` from D-12, unless another file has that number."""
    number = int(re.sub(r"\D", "", item["id"]) or 0)
    prefix = f"{number:04d}-"
    name = prefix + _slug(item.get("question") or item.get("answer") or "") + ".md"
    if any(t.startswith(prefix) for t in taken):
        name = f"{item['id'].lower()}-" + name[len(prefix) :]
    return name


def _yaml_list(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


def render(item: dict[str, Any]) -> str:
    """The record file for a store item, in MADR-style front matter and sections."""
    fields = record_of(item)
    status = TO_FILE.get(item["status"], "proposed")
    supersedes = item.get("supersedes_all") or (
        [item["supersedes"]] if item.get("supersedes") else []
    )
    decided = {"user": "the user", "rem": "Rem, in auto mode"}.get(
        str(item.get("decided_by") or ""), ""
    )
    lines = [
        "---",
        f"id: {item['id']}",
        f"title: {json.dumps(item.get('question') or item['answer'][:120], ensure_ascii=False)}",
        f"status: {status}",
        f"date: {str(item.get('decided_at') or item.get('created_at') or '')[:10]}",
        *([f"decided_by: {decided}"] if decided else []),
        f"supersedes: {_yaml_list(supersedes)}",
        f"superseded_by: {item.get('superseded_by') or 'null'}",
        f"applies_to: {_yaml_list(fields['applies_to'])}",
        f"forbids: {_yaml_list(fields['forbids'])}",
        f"signals: {_yaml_list(fields['signals'])}",
        "---",
        "",
    ]
    body = {**fields, "decision": item.get("answer") or ""}
    for key, heading in SECTIONS:
        text = str(body.get(key) or "").strip()
        if text:
            lines += [f"## {heading}", "", text, ""]
    return "\n".join(lines).rstrip() + "\n"


_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.S)
_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.M)
_TITLE = re.compile(r"^#\s+(?:\d+\.\s*)?(.+?)\s*$", re.M)


def _front_value(raw: str) -> Any:
    raw = raw.strip()
    if raw in {"", "null", "~"}:
        return None
    if raw.startswith("[") or raw.startswith('"'):
        try:
            return json.loads(raw)
        except ValueError:
            pass
    if raw.startswith("[") and raw.endswith("]"):
        return [v.strip().strip("\"'") for v in raw[1:-1].split(",") if v.strip()]
    return raw.strip("\"'")


def parse(text: str) -> dict[str, Any]:
    """Read a record: MADR front matter, or a Nygard record with a ``## Status`` section."""
    front: dict[str, Any] = {}
    body = text
    found = _FRONT.match(text)
    if found:
        body = text[found.end() :]
        current = ""
        for line in found.group(1).splitlines():
            if re.match(r"^\s+-\s+", line) and current:
                front.setdefault(current, [])
                if isinstance(front[current], list):
                    front[current].append(line.strip()[1:].strip().strip("\"'"))
                continue
            key, sep, value = line.partition(":")
            if not sep or line.startswith((" ", "\t")):
                continue
            current = key.strip().lower()
            front[current] = _front_value(value.split(" #", 1)[0])
    sections: dict[str, str] = {}
    marks = list(_HEADING.finditer(body))
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(body)
        sections[mark.group(1).strip().casefold()] = body[mark.end() : end].strip()
    title = front.get("title")
    if not title:
        heading = _TITLE.search(body)
        title = heading.group(1) if heading else ""
    status = str(front.get("status") or "").strip().casefold()
    if not status and "status" in sections:
        status = sections["status"].split()[0].casefold() if sections["status"].split() else ""
    status = re.sub(r"[^a-z]", "", status)

    def section(*names: str) -> str:
        for name in names:
            for key, value in sections.items():
                if key.startswith(name):
                    return value[:RECORD_CHARS]
        return ""

    decision = section("decision outcome", "decision")
    return {
        "id": str(front.get("id") or "").strip(),
        "title": " ".join(str(title or "").split())[:500],
        "status": status if status in FROM_FILE else "accepted",
        "decision": decision,
        "supersedes": _globs(front.get("supersedes")) if front.get("supersedes") else [],
        "superseded_by": str(front.get("superseded_by") or "").strip() or None,
        "record": RecordFields.model_validate(
            {
                "context": section("context"),
                "options": section("options", "considered options"),
                "consequences": section("consequences"),
                "check": section("how we check", "confirmation"),
                "applies_to": front.get("applies_to") or [],
                "forbids": front.get("forbids") or [],
                "signals": front.get("signals") or [],
            }
        ).model_dump(),
    }


def digest(text: str | bytes) -> str:
    data = text.encode() if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


# Git ----------------------------------------------------------------------------------


def _git(
    project: str | Path,
    *args: str,
    env: dict[str, str] | None = None,
    stdin: bytes | None = None,
) -> subprocess.CompletedProcess[bytes] | None:
    from gofer.utils.process import build_subprocess_env, run_repository_git

    try:
        return run_repository_git(
            project,
            *args,
            timeout=GIT_TIMEOUT,
            input=stdin,
            env={**build_subprocess_env(), **(env or {})},
            text=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _out(project: str | Path, *args: str, **kwargs: Any) -> str | None:
    result = _git(project, *args, **kwargs)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.decode(errors="replace")


def first_commit(project: str, branch: str, path: str) -> dict[str, str] | None:
    """The commit that first added ``path`` on ``branch``: short hash, author name, date.

    ``{"commit", "author", "at"}`` with ``at`` the author date in UTC; None when git can't
    tell. Only the author's name is read, never the email.
    """
    out = _out(
        project,
        "log",
        "--diff-filter=A",
        "--no-renames",
        "--format=%H%x00%an%x00%at",
        f"refs/heads/{branch}",
        "--",
        path,
    )
    # Newest first: the last line is the first time the file was added.
    lines = [line for line in (out or "").splitlines() if line.count("\0") == 2]
    if not lines:
        return None
    commit, author, stamp = lines[-1].split("\0")
    try:
        at = datetime.fromtimestamp(int(stamp), UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None
    name = " ".join("".join(c for c in author if c.isprintable()).split())[:100]
    return {"commit": commit[:12], "author": name, "at": at}


def folder_files(project: str, branch: str, folder: str) -> dict[str, str] | None:
    """Markdown files in ``folder`` on ``branch``: path -> text. None without the branch."""
    tip = _out(project, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}")
    if tip is None:
        return None
    listed = _out(project, "ls-tree", "-r", "-z", "--name-only", tip.strip(), "--", folder)
    files: dict[str, str] = {}
    for path in sorted((listed or "").split("\0"))[:MAX_FILES]:
        if not path.endswith(".md") or Path(path).name.casefold() in {"readme.md", "index.md"}:
            continue
        text = _out(project, "cat-file", "blob", f"{tip.strip()}:{path}")
        if text is not None:
            files[path] = text[:FILE_BYTES]
    return files


# Tracked files on a branch tip, and each glob's count there. A tip never changes, so both
# are kept by commit and only the newest few tips stay.
TRACKED_TIPS = 8
TRACKED_FILES = 500_000
COUNTS_KEPT = 4000
_tracked: OrderedDict[tuple[str, str], tuple[str, ...]] = OrderedDict()
_counts: OrderedDict[tuple[str, str, str], int] = OrderedDict()
_cache_lock = threading.Lock()


def _keep(cache: OrderedDict[Any, Any], key: Any, value: Any, limit: int) -> None:
    with _cache_lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)


def _branch_tip(project: str, branch: str) -> str | None:
    tip = _out(project, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}")
    return tip.strip() if tip else None


def tracked_files(project: str, tip: str) -> tuple[str, ...] | None:
    """Every file tracked at commit ``tip`` (``git ls-tree``), cached by commit."""
    key = (str(project), tip)
    with _cache_lock:
        cached = _tracked.get(key)
    if cached is not None:
        return cached
    listed = _out(project, "ls-tree", "-r", "-z", "--name-only", "--full-tree", tip)
    if listed is None:
        return None
    files = tuple(p for p in listed.split("\0") if p)[:TRACKED_FILES]
    _keep(_tracked, key, files, TRACKED_TIPS)
    return files


def glob_count(project: str, tip: str, pattern: str) -> int | None:
    """How many files tracked at ``tip`` match ``pattern``; None when git can't list them."""
    pattern = _relative(pattern)
    key = (str(project), tip, pattern)
    with _cache_lock:
        cached = _counts.get(key)
    if cached is not None:
        return cached
    files = tracked_files(project, tip)
    if files is None:
        return None
    count = sum(1 for path in files if glob_match(path, pattern)) if pattern else 0
    _keep(_counts, key, count, COUNTS_KEPT)
    return count


def path_counts(
    project: str | None,
    branch: str | None,
    applies_to: list[str],
    forbids: list[str],
    *,
    tip: str | None = None,
) -> dict[str, Any] | None:
    """How many tracked files on ``branch``'s tip each glob matches.

    ``{"branch", "commit", "applies_to": {glob: n}, "forbids": {glob: n}}``, or None when
    the project has no landing branch, the branch doesn't exist or git can't list it.
    ``tip`` is the branch's commit when the caller already read it.
    """
    if not project or not branch:
        return None
    tip = tip or _branch_tip(project, branch)
    if tip is None or tracked_files(project, tip) is None:
        return None
    counted: dict[str, Any] = {"branch": branch, "commit": tip[:12]}
    for key, globs in (("applies_to", applies_to), ("forbids", forbids)):
        counts: dict[str, int] = {}
        for pattern in _globs(globs):
            count = glob_count(project, tip, pattern)
            if count is not None:
                counts[pattern] = count
        counted[key] = counts
    return counted


def count_note(kind: str, count: int | None) -> str:
    """What a path's count says, as the desktop's chips and the phone say it.

    ``kind`` is ``applies_to`` or ``forbids``. An Applies to path that matches nothing and a
    Never touch path that matches files are warnings; "" when there is no count.
    """
    if count is None:
        return ""
    files = "1 file" if count == 1 else f"{count} files"
    if kind == "forbids":
        verb = "exists" if count == 1 else "exist"
        return f"matches {files} that {verb} now" if count else "no files now"
    return f"{files}" if count else "matches no files"


def _identity(project: str) -> dict[str, str]:
    """Commit as the repository's own author; Raticode's name only when it has none."""
    env: dict[str, str] = {}
    if not (_out(project, "config", "user.name") or "").strip():
        env.update(GIT_AUTHOR_NAME="Raticode", GIT_COMMITTER_NAME="Raticode")
    if not (_out(project, "config", "user.email") or "").strip():
        env.update(
            GIT_AUTHOR_EMAIL="support@raticode.dev", GIT_COMMITTER_EMAIL="support@raticode.dev"
        )
    return env


def _checkout_of(project: str, branch: str) -> str | None:
    out = _out(project, "worktree", "list", "--porcelain") or ""
    path = ""
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line == f"branch refs/heads/{branch}":
            return path
    return None


def write_state(project: str, branch: str) -> str | None:
    """What a record write depends on: the branch's tip and its checkout's git status.

    A write that failed is tried again only once this differs from when it failed, so a
    checkout left dirty doesn't make Raticode try (and change the record's reason) on
    every look. None when the branch doesn't exist.
    """
    tip = _branch_tip(project, branch)
    if tip is None:
        return None
    checkout = _checkout_of(project, branch)
    if checkout is None:
        return tip
    status = _out(checkout, "status", "--porcelain", "--untracked-files=no")
    return f"{tip}\n{checkout}\n{status if status is not None else '?'}"


def commit_file(project: str, branch: str, path: str, text: str, message: str) -> dict[str, Any]:
    """Commit one file on ``branch`` without touching any checkout's other files.

    Builds the commit with a private index, then moves the branch: a checkout of it is
    fast-forwarded only when it has no uncommitted changes (``merge --ff-only``), and a
    branch nobody has checked out moves with a compare-and-swap ``update-ref``. Returns
    ``{"ok": True, "commit"}`` or ``{"ok": False, "reason"}``; nothing is half-done.
    """
    tip = _out(project, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}")
    if tip is None:
        return {"ok": False, "reason": f"The branch {branch} doesn't exist."}
    tip = tip.strip()
    checkout = _checkout_of(project, branch)
    if checkout is not None:
        status = _out(checkout, "status", "--porcelain", "--untracked-files=no")
        if status is None or status.strip():
            return {
                "ok": False,
                "reason": f"{checkout} has uncommitted changes on {branch}; "
                "the record is kept and written once it's clean.",
            }
    with tempfile.TemporaryDirectory(prefix="raticode-record-") as scratch:
        env = {"GIT_INDEX_FILE": str(Path(scratch) / "index"), **_identity(project)}
        done = _git(project, "read-tree", tip, env=env)
        if done is None or done.returncode != 0:
            return {"ok": False, "reason": "git couldn't read the branch."}
        blob = _out(project, "hash-object", "-w", "--stdin", env=env, stdin=text.encode())
        if blob is None:
            return {"ok": False, "reason": "git couldn't store the record."}
        added = _git(
            project,
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{blob.strip()},{path}",
            env=env,
        )
        tree = _out(project, "write-tree", env=env) if added and added.returncode == 0 else None
        if tree is None:
            return {"ok": False, "reason": "git couldn't build the commit."}
        commit = _out(project, "commit-tree", tree.strip(), "-p", tip, "-m", message, env=env)
        if commit is None:
            return {"ok": False, "reason": "git couldn't make the commit."}
    commit = commit.strip()
    if checkout is not None:
        moved = _git(checkout, "merge", "--ff-only", "--quiet", commit)
    else:
        moved = _git(project, "update-ref", f"refs/heads/{branch}", commit, tip)
    if moved is None or moved.returncode != 0:
        return {
            "ok": False,
            "reason": f"{branch} moved while the record was written; retried later.",
        }
    return {"ok": True, "commit": commit}


def work_commits(
    folder: str, base: str, *, since: str | None = None, exclude: frozenset[str] = frozenset()
) -> list[str] | None:
    """The commits the work in ``folder`` added, newest first, or None outside a checkout.

    They are the commits reachable from ``HEAD`` but not from ``since`` (the landing
    branch's tip when the run started), so work that was already fast-forwarded into the
    landing branch still counts. Without ``since`` they start at the merge base with
    ``base``. Left out: commits from other local branches that ``HEAD`` doesn't contain,
    the commits in ``exclude`` (Raticode's own record commits), and any commit the landing
    branch's reflog shows being made on it (``made_on``) that the checkout's own reflogs
    don't (``made_here``), such as one the user committed on the landing branch while the
    run went on. A commit with no such record anywhere counts as the work's: a worker
    that builds one with ``commit-tree`` and moves to it with ``reset`` leaves no entry
    that says it was made, and must not slip past the decision check that way.
    """
    start = None
    if since and COMMIT_ID.match(since):
        ancestor = _git(folder, "merge-base", "--is-ancestor", since, "HEAD")
        if ancestor is not None and ancestor.returncode == 0:
            start = since
    if start is None:
        merge_base = _out(folder, "merge-base", base, "HEAD")
        if merge_base is None:
            return None
        start = merge_base.strip()
    others = _out(
        folder, "for-each-ref", "--no-contains", "HEAD", "--format=%(objectname)", "refs/heads"
    )
    hidden = [f"^{tip}" for tip in (others or "").split() if COMMIT_ID.match(tip)]
    listed = _out(folder, "rev-list", "HEAD", f"^{start}", *hidden)
    if listed is None:
        return None
    made = made_here(folder, base) or set()
    elsewhere = made_on(folder, base) - made
    return [c for c in listed.split() if c not in exclude and c not in elsewhere]


COMMIT_ID = re.compile(r"^[0-9a-f]{7,64}$")
# Reflog entries that record a commit made right there; checkouts, fast-forwards, resets
# and the start or end of a rebase only move to commits made elsewhere.
MADE = re.compile(
    r"^(?:commit(?: \((?:amend|initial|merge)\))?|cherry-pick|revert"
    r"|rebase(?: -i)? \((?:pick|reword|edit|squash|fixup|continue)\)"
    r"|merge [^:]*: Merge made by)",
)


def made_here(folder: str, base: str) -> set[str] | None:
    """Commits created in ``folder``'s checkout, read from its reflogs; None without any.

    The checkout's own HEAD reflog, plus its branch's when that isn't the landing branch
    ``base``: a commit the user made on the landing branch elsewhere, which the work then
    rebased onto or fast-forwarded past, never appears there as made.
    """
    logs = [_out(folder, "reflog", "show", "--format=%H%x00%gs", "HEAD")]
    branch = (_out(folder, "symbolic-ref", "--quiet", "--short", "HEAD") or "").strip()
    if branch and branch != base:
        logs.append(_out(folder, "reflog", "show", "--format=%H%x00%gs", f"refs/heads/{branch}"))
    entries = [line.split("\0", 1) for log in logs if log for line in log.splitlines()]
    if not entries:
        return None
    return {sha for sha, *subject in entries if subject and MADE.match(subject[0])}


def made_on(folder: str, base: str) -> set[str]:
    """Commits the landing branch ``base``'s reflog shows being made on it; empty without one.

    Branch reflogs live in the repository's shared git folder, so this reads the same log
    from any of its checkouts: a commit made on ``base`` anywhere, such as the user's
    record edit in the main checkout, is listed here.
    """
    log = _out(folder, "reflog", "show", "--format=%H%x00%gs", f"refs/heads/{base}") or ""
    entries = [line.split("\0", 1) for line in log.splitlines()]
    return {sha for sha, *subject in entries if subject and MADE.match(subject[0])}


def changes(
    folder: str, base: str, *, since: str | None = None, exclude: frozenset[str] = frozenset()
) -> tuple[list[tuple[str, str]], list[str]] | None:
    """``(status, path)`` for each file the work in ``folder`` changed, and its commits.

    Status is git's letter (A, M, D). The work's commits (``work_commits``) count, applied
    oldest first, and so do uncommitted and untracked files. None when ``folder`` isn't a
    checkout or the starting point doesn't exist there.
    """
    commits = work_commits(folder, base, since=since, exclude=exclude)
    if commits is None:
        return None
    found: dict[str, str] = {}

    def take(out: str) -> None:
        parts = [p for p in out.split("\0") if p]
        for status, path in zip(parts[::2], parts[1::2], strict=False):
            found[path] = status[:1]

    for commit in reversed(commits):
        take(
            _out(
                folder,
                "diff-tree",
                "--no-commit-id",
                "--root",
                "-r",
                "-z",
                "--name-status",
                "--no-renames",
                commit,
            )
            or ""
        )
    take(_out(folder, "diff", "--name-status", "-z", "--no-renames", "HEAD") or "")
    untracked = _out(folder, "ls-files", "--others", "--exclude-standard", "-z") or ""
    for path in untracked.split("\0"):
        if path:
            found.setdefault(path, "A")
    return sorted((status, path) for path, status in found.items()), commits


# Commits Raticode wrote itself, and commits Rem's runs made, so a record file changed by a
# run is never mistaken for the user's own edit.
LEDGER = "rem_decision_commits"
LEDGER_KEEP = 4000


def ledger(db: Any, store: Any) -> dict[str, set[str]]:
    stored = store.setting(db, LEDGER)
    return {
        "raticode": set(stored.get("raticode") or []),
        "runs": set(stored.get("runs") or []),
    }


def remember_commits(db: Any, store: Any, kind: str, commits: list[str]) -> None:
    if not commits:
        return
    stored = store.setting(db, LEDGER)
    known = [c for c in stored.get(kind) or [] if c not in set(commits)]
    stored[kind] = [*known, *commits][-LEDGER_KEEP:]
    store.set_setting(db, LEDGER, stored)


# Store sync ---------------------------------------------------------------------------


def folder_of(book: dict[str, Any], project: str) -> str:
    from gofer.ui import rem_decisions as decision_book

    item = decision_book.in_force(book, project, FOLDER_TOPIC)
    answer = str((item or {}).get("answer") or "").strip().strip("/")
    if answer and ".." not in PurePosixPath(answer).parts and not os.path.isabs(answer):
        return answer
    return DEFAULT_FOLDER


def branch_of(book: dict[str, Any], project: str) -> str | None:
    from gofer.ui import rem_decisions as decision_book

    item = decision_book.in_force(book, project, BRANCH_TOPIC)
    answer = str((item or {}).get("answer") or "").strip()
    return answer or None


def _may_rewrite(item: dict[str, Any]) -> bool:
    """A file Raticode already wrote changes only after the user changed its decision.

    Rem's requests never change an existing item, so its last history entry is the
    user's (confirm, edit, replace, turn down, retire) or Raticode reading the user's own
    edit.
    """
    history = item.get("history") or []
    return bool(history) and history[-1].get("actor") == "user"


def sync(
    db: Any,
    store: Any,
    project: str,
    *,
    files: dict[str, str],
    branch: str | None = None,
    by_runs: frozenset[str] = frozenset(),
    deferred: frozenset[str] = frozenset(),
) -> list[str]:
    """Read the project's record files into the store; returns the IDs that changed.

    A file Raticode doesn't know yet (written by the user or another tool) becomes a
    decision as its status says. Its ``source`` names the commit that added it on the
    landing ``branch`` and that commit's author, and it counts as decided on that commit's
    date rather than when Raticode read it. A known file whose text differs from what Raticode last
    wrote or read was edited outside Raticode: the store takes its wording and status, and
    the item is marked ``edited_outside`` so the Decisions view says so.

    Except changes Rem's own runs made (``by_runs``: paths whose last commit came from a
    run). Those are never the user's approval: a new file is ignored, and a known one is
    marked ``record_restore`` so Raticode commits the approved text back. ``deferred``
    paths changed while a writer run in the project is still going; they wait until it
    has finished and its commits are known.
    """
    from gofer.ui import rem_decisions as decision_book

    key = decision_book.project_key(project)
    book = decision_book.read(db, store)
    by_path = {i.get("record_path"): i for i in book["items"] if i.get("project") == key}
    by_id = {i["id"]: i for i in book["items"] if i.get("project") == key}
    changed: list[str] = []
    stamp = decision_book.now()
    for path, text in files.items():
        parsed = parse(text)
        if not parsed["title"] and not parsed["decision"]:
            continue
        item = by_path.get(path)
        hashed = digest(text)
        if item is not None and item.get("record_hash") == hashed:
            continue
        if path in deferred:
            continue
        if path in by_runs:
            # A Rem run changed the decision folder: not an approval, so it isn't read in.
            if item is not None and item.get("record_restore") != hashed:
                item["record_restore"] = hashed
                item.setdefault("history", []).append(
                    {
                        "at": stamp,
                        "actor": "raticode",
                        "action": "kept the approved text",
                        "note": f"A Rem run changed {path}; only the user changes decisions.",
                    }
                )
                item["history"] = item["history"][-20:]
                changed.append(item["id"])
            continue
        claimed = by_id.get(parsed["id"])
        if (
            item is None
            and claimed is not None
            and claimed.get("remember_as") == "record"
            and not claimed.get("record_path")
        ):
            # Only a record decision whose file isn't written yet can take a file by its id:
            # a file naming a fact decision (the check command, the landing branch) is read
            # in as a record of its own and never rewrites that fact.
            item = claimed
            item["record_path"] = path
        status = FROM_FILE[parsed["status"]]
        if item is None:
            added = first_commit(project, branch, path) if branch else None
            decided = status in decision_book.IN_FORCE
            item = {
                "id": f"D-{book['next_number']}",
                "project": key,
                "topic": "adr:" + _slug(parsed["title"] or Path(path).stem),
                "question": parsed["title"] or Path(path).stem,
                "answer": parsed["decision"][:1000] or parsed["title"],
                "recommended": "",
                "why": "",
                "evidence": [],
                "alternatives": [],
                "remember_as": "record",
                "reversible": True,
                "tried": "",
                "status": status,
                "decided_by": "user" if decided else None,
                "decided_at": ((added or {}).get("at") or stamp) if decided else None,
                "source": {
                    "via": "repository",
                    **({"commit": added["commit"], "author": added["author"]} if added else {}),
                },
                "asked_by": [],
                "evidence_hashes": {},
                "failures": 0,
                "created_at": stamp,
                "updated_at": stamp,
                "record_path": path,
                "history": [
                    {
                        "at": stamp,
                        "actor": "raticode",
                        "action": "read",
                        "note": f"{path}, added in {added['commit']}" if added else path,
                    }
                ],
            }
            book["next_number"] += 1
            book["items"].append(item)
            by_path[path] = item
        else:
            item.update(
                question=parsed["title"] or item["question"],
                answer=parsed["decision"][:1000] or item["answer"],
                status=status,
                edited_outside=True,
                updated_at=stamp,
            )
            item.setdefault("history", []).append(
                {"at": stamp, "actor": "raticode", "action": "edited outside", "note": path}
            )
            item["history"] = item["history"][-20:]
        if parsed["superseded_by"]:
            item["superseded_by"] = parsed["superseded_by"]
        item["record"] = parsed["record"]
        item["record_hash"] = hashed
        item.pop("record_error", None)
        changed.append(item["id"])
    if changed:
        decision_book._save(db, store, book)
    return changed


def pending_writes(
    book: dict[str, Any], project: str, files: dict[str, str]
) -> list[dict[str, Any]]:
    """The files to write so the repo matches the store: ``{id, path, text, new}``.

    Only records with a decision (in force or closed after being written) get a file;
    waiting proposals stay in the store. An existing file is rewritten only when the user
    changed its decision since (``_may_rewrite``).
    """
    from gofer.ui import rem_decisions as decision_book

    key = decision_book.project_key(project)
    folder = folder_of(book, project)
    taken = {Path(p).name for p in files}
    writes = []
    for item in book["items"]:
        if item.get("project") != key or not is_record(item):
            continue
        path = item.get("record_path")
        if not path:
            if item["status"] not in decision_book.IN_FORCE:
                continue
            name = file_name(item, taken)
            taken.add(name)
            path = f"{folder}/{name}"
        text = render(item)
        current = files.get(path)
        if current is not None and digest(current) == digest(text):
            continue
        new = current is None
        # A file a Rem run changed goes back to the approved text.
        restore = not new and item.get("record_restore") == digest(current or "")
        if (
            not new
            and not restore
            and (item.get("record_hash") != digest(current or "") or not _may_rewrite(item))
        ):
            continue
        writes.append({"id": item["id"], "path": path, "text": text, "new": new})
    return writes


def mark_written(
    db: Any, store: Any, identity: str, write: dict[str, Any], outcome: dict[str, Any]
) -> None:
    from gofer.ui import rem_decisions as decision_book

    book = decision_book.read(db, store)
    item = next((i for i in book["items"] if i["id"] == identity), None)
    if item is None:
        return
    if outcome.get("ok"):
        item.update(
            record_path=write["path"],
            record_hash=digest(write["text"]),
            record_commit=outcome.get("commit"),
        )
        item.pop("record_error", None)
        item.pop("record_restore", None)
        if outcome.get("commit"):
            remember_commits(db, store, "raticode", [str(outcome["commit"])])
    else:
        reason = str(outcome.get("reason") or "")[:300]
        if item.get("record_error") == reason:
            # Nothing changed: the book's revision stays, so polls answer unchanged.
            return
        item["record_error"] = reason
    decision_book._save(db, store, book)


# Workers and reviewers ----------------------------------------------------------------


def in_force_records(book: dict[str, Any], project: str | None) -> list[dict[str, Any]]:
    from gofer.ui import rem_decisions as decision_book

    key = decision_book.project_key(project)
    return [
        i
        for i in book["items"]
        if i.get("project") == key and is_record(i) and i["status"] in decision_book.IN_FORCE
    ]


def add_path_counts(book: dict[str, Any], items: list[dict[str, Any]]) -> None:
    """Give each record in force or waiting its ``path_counts`` on the landing branch.

    Each item gets ``path_counts`` (see ``path_counts``), or None when its project has no
    landing branch to count on. Other items are left as they are.
    """
    from gofer.ui import rem_decisions as decision_book

    shown = decision_book.IN_FORCE | {"pending"}
    # Each project's landing branch and its tip, read once per call, not once per record.
    branches: dict[str, tuple[str | None, str | None]] = {}
    for item in items:
        project = item.get("project")
        if not is_record(item) or item.get("status") not in shown or not project:
            continue
        if project not in branches:
            branch = branch_of(book, project)
            branches[project] = (branch, _branch_tip(project, branch) if branch else None)
        branch, tip = branches[project]
        fields = record_of(item)
        item["path_counts"] = (
            path_counts(project, branch, fields["applies_to"], fields["forbids"], tip=tip)
            if tip
            else None
        )


def matching(items: list[dict[str, Any]], paths: list[str]) -> list[dict[str, Any]]:
    """Records whose ``applies_to`` globs match any changed path."""
    return [i for i in items if any(any_match(p, record_of(i)["applies_to"]) for p in paths)]


def full_text(item: dict[str, Any]) -> dict[str, Any]:
    """What a reviewer reads for one record."""
    fields = record_of(item)
    return {
        "id": item["id"],
        "title": item.get("question") or "",
        "decision": item.get("answer") or "",
        "status": TO_FILE.get(item["status"], item["status"]),
        "path": item.get("record_path") or "",
        "applies_to": fields["applies_to"],
        "forbids": fields["forbids"],
        "context": fields["context"],
        "how_we_check_it": fields["check"],
    }


def violations(
    book: dict[str, Any], project: str, found: list[tuple[str, str]]
) -> list[dict[str, str]]:
    """What a change breaks with no judgement needed; each ``{id, path, why}``.

    - Any change inside the decision folder: decisions change only through Rem's
      requests and the user's approval, never by editing the files in a work branch.
    - Adding or modifying a file a decision in force forbids (its ``forbids`` globs).
    """
    folder = folder_of(book, project).strip("/") + "/"
    broken: list[dict[str, str]] = []
    for status, path in found:
        if path.startswith(folder):
            broken.append(
                {
                    "id": "",
                    "path": path,
                    "why": f"{path} is a decision record. Decisions change only through Rem's "
                    "decision requests and the user's approval; undo this change and file a "
                    "decision request (remember_as: record) instead.",
                }
            )
    for item in in_force_records(book, project):
        forbids = record_of(item)["forbids"]
        for status, path in found:
            if status != "D" and any_match(path, forbids):
                broken.append(
                    {
                        "id": item["id"],
                        "path": path,
                        "why": f"{path} breaks {item['id']} ({item.get('question') or ''}): "
                        f"{item.get('answer') or ''}",
                    }
                )
    return broken


def feedback(broken: list[dict[str, str]]) -> str:
    lines = [b["why"] for b in broken[:10]]
    return (
        "Raticode's decision check failed. Change the work so it respects the project's "
        "decisions; if a decision itself is wrong, don't work around it: say so with a "
        "decision request recommending one clean change to the decisions.\n- " + "\n- ".join(lines)
    )


def signal_in(signal: str, text: str) -> bool:
    """Whether a signal word or phrase appears in the text as whole words, any case:
    ``rust`` matches "use Rust here" but not "trust"."""
    words = signal.split()
    if not words:
        return False
    body = r"\s+".join(re.escape(w) for w in words)
    return re.search(rf"(?<!\w){body}(?!\w)", text, re.IGNORECASE) is not None


def signal_hits(items: list[dict[str, Any]], text: str) -> list[str]:
    """IDs of records whose signal words appear in a proposal's text."""
    return [i["id"] for i in items if any(signal_in(s, text) for s in record_of(i)["signals"])]
