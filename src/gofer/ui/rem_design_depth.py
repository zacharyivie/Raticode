"""Self-work's design and module depth research: churn hotspots and deepening tickets.

A shallow module is one whose interface is nearly as large as what it hides, so callers
would get simpler if it were inlined. Large files that change often are where that costs
the most. Before the research run starts, code with no model call reads the last
HOTSPOT_DAYS of ``git log --numstat`` on the project's landing branch and ranks files by
their size times how often they changed. The top HOTSPOT_FILES go into the run's prompt,
which asks it to apply the deletion test and propose at most MAX_DEEPENING deepening
tickets.

Each deepening ticket carries a before/after interface sketch and the finish line
FINISH_LINE. It waits for open tickets that name the same files, so a refactor never races
the work it would move under, and self-work runs at most one deepening ticket at a time.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

from gofer.ui.rem_home_worktree import WorktreeError, _git

HOTSPOT_DAYS = 30
HOTSPOT_FILES = 10
# Files whose size is read at the branch tip, most-changed first; bounds the git work.
SIZED_FILES = 400
MAX_DEEPENING = 2
FINISH_LINE = "The project's decided check passes and public behavior is unchanged"
# Ticket statuses a deepening ticket counts as running in.
RUNNING = frozenset({"in_progress", "in_review"})
# Base names too common to say two tickets touch the same file.
_GENERIC = frozenset({"__init__", "__main__", "index", "main", "mod", "lib", "utils", "types"})
_PATHLIKE = re.compile(r"[\w.@-]+(?:/[\w.@-]+)*\.[A-Za-z0-9]{1,8}")


def hotspots(
    root: str,
    commit: str,
    clock: datetime,
    *,
    days: int = HOTSPOT_DAYS,
    limit: int = HOTSPOT_FILES,
) -> list[dict[str, Any]]:
    """The files at ``commit`` ranked by lines times churn over the last ``days``.

    Churn is the number of commits that changed the file in that time; lines is its length
    at ``commit``. Files deleted since, binary files and merges are left out. Returns []
    when the project isn't in git or has no recent history.
    """
    if not commit:
        return []
    since = (clock - timedelta(days=days)).isoformat()
    try:
        log = _git(
            root,
            "log",
            "--numstat",
            "--no-renames",
            "--no-merges",
            "--no-textconv",
            "--no-ext-diff",
            "--format=%x00",
            f"--since={since}",
            commit,
            "--",
        )
    except WorktreeError:
        return []
    churn: dict[str, dict[str, int]] = {}
    for line in log.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or parts[0] == "-" or parts[2].startswith('"'):
            continue
        try:
            added, deleted = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        entry = churn.setdefault(parts[2], {"commits": 0, "changed": 0})
        entry["commits"] += 1
        entry["changed"] += added + deleted
    if not churn:
        return []
    busiest = sorted(churn, key=lambda p: (-churn[p]["commits"], p))[:SIZED_FILES]
    sizes = _line_counts(root, commit, busiest)
    rows: list[dict[str, Any]] = [
        {
            "path": path,
            "lines": sizes[path],
            "commits": churn[path]["commits"],
            "changed": churn[path]["changed"],
            "score": sizes[path] * churn[path]["commits"],
        }
        for path in busiest
        if sizes.get(path)
    ]
    rows.sort(key=lambda r: (-int(r["score"]), str(r["path"])))
    return rows[:limit]


def _line_counts(root: str, commit: str, paths: list[str]) -> dict[str, int]:
    """How many lines each text file has at ``commit``; missing and binary files are absent."""
    counts: dict[str, int] = {}
    for start in range(0, len(paths), 100):
        chunk = paths[start : start + 100]
        try:
            out = _git(
                root,
                "grep",
                "-c",
                "-I",
                "--no-textconv",
                "-e",
                "",
                commit,
                "--",
                *(f":(literal){p}" for p in chunk),
            )
        except WorktreeError:
            # git grep exits 1 when nothing matched, such as every file being deleted.
            continue
        prefix = f"{commit}:"
        for line in out.splitlines():
            if not line.startswith(prefix):
                continue
            path, _, number = line[len(prefix) :].rpartition(":")
            if number.isdigit():
                counts[path] = int(number)
    return counts


def table(rows: list[dict[str, Any]]) -> str:
    """The ranked files as a Markdown table for the research prompt."""
    lines = [
        "| # | File | Lines | Commits | Lines changed | Lines x commits |",
        "|---|------|-------|---------|---------------|-----------------|",
    ]
    lines += [
        f"| {n} | {r['path']} | {r['lines']} | {r['commits']} | {r['changed']} | {r['score']} |"
        for n, r in enumerate(rows, 1)
    ]
    return "\n".join(lines)


def research_brief(rows: list[dict[str, Any]], branch: str, *, board: bool = True) -> str:
    """What the design and module depth run is told, ahead of the usual research prompt."""
    on = f"{branch}, the landing branch" if branch else "the checked-out branch"
    ranked = (
        f"These files changed most in the last {HOTSPOT_DAYS} days on {on}, ranked by "
        f"lines times commits:\n{table(rows)}\n"
        if rows
        else f"Git shows no file changes in the last {HOTSPOT_DAYS} days on {on}, so pick "
        "the largest modules the project's callers depend on.\n"
    )
    ask = (
        f"Propose at most {MAX_DEEPENING} deepening tickets, the ones that would simplify the "
        "most callers, and none if no module fails the test. Each description gives a "
        "before and after sketch of the interface (the signatures callers use today and "
        "after the change) and names the files it changes by their path. Each done_when "
        f"includes “{FINISH_LINE}”.\n"
        if board
        else f"List at most {MAX_DEEPENING} deepening ideas, each with a before and after "
        "sketch of the interface and the files it changes by their path.\n"
    )
    return (
        "Design and module depth: find shallow modules, whose interface is nearly as "
        "complex as what they hide, and propose deepening them.\n"
        + ranked
        + "For each file near the top, apply the deletion test: if this module were "
        "deleted and its code inlined into its callers, would the callers get simpler? If "
        "they would, the module is shallow; if the complexity would spread to every "
        "caller, it earns its place. A deeper module hides more behind a smaller "
        "interface. Read the callers before you judge.\n" + ask
    )


def finish_line(statements: list[str]) -> list[str]:
    """A deepening ticket's finish line: its statements with FINISH_LINE kept, at most three."""
    if any(FINISH_LINE.casefold() in s.casefold() for s in statements):
        return statements[:3]
    return [*statements[:2], FINISH_LINE]


def named_files(text: str, known: Iterable[str] = ()) -> set[str]:
    """The file paths ``text`` names: paths from ``known`` it mentions, and path-like words."""
    found = {p for p in known if p and p in text}
    found |= {m.group(0) for m in _PATHLIKE.finditer(text) if "/" in m.group(0)}
    return found


def _ticket_text(ticket: dict[str, Any]) -> str:
    return "\n".join(
        str(ticket.get(field) or "") for field in ("title", "description", "done_when")
    )


def _names(text: str, path: str) -> bool:
    if path in text:
        return True
    base = PurePosixPath(path)
    if base.stem in _GENERIC or not base.suffix or len(base.name) < 6:
        return False
    return re.search(rf"(?<![\w/.-]){re.escape(base.name)}(?![\w-])", text) is not None


def same_file_tickets(
    files: Iterable[str], tickets: Iterable[dict[str, Any]], project_dir: str | None
) -> list[dict[str, Any]]:
    """Open tickets of the same project whose title, description or finish line names any
    of ``files``, by path or by a distinctive file name."""
    wanted = [f for f in files if f]
    if not wanted:
        return []
    return [
        t
        for t in tickets
        if t.get("project_dir") == project_dir
        and t.get("status") in {"backlog", "todo", "in_progress", "in_review", "blocked"}
        and any(_names(_ticket_text(t), f) for f in wanted)
    ]


def is_deepening(ticket: dict[str, Any]) -> bool:
    return bool(ticket.get("deepening"))


def other_deepening_running(ticket: dict[str, Any], tickets: Iterable[dict[str, Any]]) -> bool:
    """Whether a deepening ticket other than ``ticket`` is under way."""
    return any(
        t["id"] != ticket["id"]
        and is_deepening(t)
        and (t.get("status") in RUNNING or (t.get("claim") and t.get("status") != "done"))
        for t in tickets
    )
