"""Where a self-work research run starts: the last round's report, its commit and recent closes.

Each research round used to start from nothing, so it re-read the whole project and could
propose work the user had just declined. A new run now records the project's commit (the tip
of its integration branch, the project's ``branch.integration`` decision, when it has one,
since the checkout may sit on any branch) and is
told, in a few short lines of its prompt (shown in its task details), which report the last
finished run of the same project and angle saved, the commit that run started at, and the
project's tickets closed or declined lately.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from gofer.ui.rem_home_landing import branch_tips
from gofer.ui.rem_home_models import ACTIVE
from gofer.ui.rem_home_store import NEWEST_RUN
from gofer.ui.rem_home_worktree import WorktreeError, _git
from gofer.utils.instants import stored_instant

# Tickets closed or declined this recently are listed, newest first, at most CLOSED_LIMIT.
CLOSED_WITHIN = timedelta(days=14)
CLOSED_LIMIT = 30
# Earlier research runs of a project looked at to find the last report of an angle.
RUNS_SCANNED = 60
TITLE_CHARS = 90


def project_commit(root: str, integration: str | None = None) -> tuple[str, str]:
    """Where the project's work stands: its branch name and commit.

    Projects with an integration branch (``integration``, the project's decision) land
    finished work there, while the checkout may be on another branch, such as the one the
    user's app runs from. So that branch's tip is recorded, with its name. Without one it
    is the checkout's HEAD and an empty name, and ("", "") when the project isn't in git.
    """
    tips = branch_tips(root, integration) if integration else None
    if integration and tips and tips.get(integration):
        return integration, tips[integration]
    try:
        return "", _git(root, "rev-parse", "--verify", "-q", "HEAD^{commit}")
    except WorktreeError:
        return "", ""


def last_report(
    db: sqlite3.Connection, target: str, angle: int | str, title: str
) -> dict[str, str] | None:
    """The newest report a finished research run of this project and angle saved.

    Runs from before the angle was recorded match on their title instead.
    """
    rows = db.execute(
        "SELECT body FROM runs WHERE json_extract(body, '$.self_work.kind')='research' "
        "AND json_extract(body, '$.self_work.target')=? ORDER BY " + NEWEST_RUN + " LIMIT ?",
        (target, RUNS_SCANNED),
    )
    for (body,) in rows:
        run = json.loads(body)
        started = run.get("self_work") or {}
        same = (
            started.get("angle") == angle
            if "angle" in started
            else (run.get("recipe") or {}).get("title") == title
        )
        if not same or run.get("state") in ACTIVE | {"paused"}:
            continue
        saved = [
            str(a["path"])
            for a in run.get("artifacts") or []
            if a.get("path") and not a.get("superseded") and Path(str(a["path"])).is_file()
        ]
        if saved:
            return {
                "report": saved[-1],
                "commit": str(started.get("commit") or ""),
                "started_at": str(run.get("started_at") or run.get("created_at") or ""),
            }
    return None


def recently_closed(
    tickets: list[dict[str, Any]], target: str, clock: datetime
) -> list[dict[str, str]]:
    """The project's tickets closed or declined within CLOSED_WITHIN: key, title, status."""
    closed = []
    for ticket in tickets:
        if ticket.get("project_dir") != target or ticket.get("status") not in {"done", "cancelled"}:
            continue
        at = stored_instant(ticket.get("updated_at"), naive_as_utc=False)
        if at is None or clock - at > CLOSED_WITHIN:
            continue
        declined = (ticket.get("proposal") or {}).get("state") == "declined"
        closed.append((at, ticket, "declined" if declined else str(ticket["status"])))
    closed.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "key": f"REM-{ticket['number']}",
            "title": " ".join(str(ticket.get("title") or "").split())[:TITLE_CHARS],
            "status": status,
        }
        for _, ticket, status in closed[:CLOSED_LIMIT]
    ]


def brief(
    commit: str,
    previous: dict[str, str] | None,
    closed: list[dict[str, str]],
    branch: str = "",
) -> str:
    """A few short prompt lines on where this run starts; "" when there is nothing to say.

    ``branch`` names the branch ``commit`` is the tip of, such as dev, so the run reads
    that branch's history rather than whatever its checkout is on.
    """
    lines = []
    if previous:
        day = previous["started_at"][:10]
        since = previous["commit"][:12] or (f"its run of {day}" if day else "it")
        now_at = f" (now {commit[:12]})" if commit and commit != previous["commit"] else ""
        on = f"on {branch} " if branch else ""
        lines.append(
            f"- Start from the last report, {previous['report']}, and the commits {on}after "
            f"{since}{now_at}. Don't repeat what it covered."
        )
    if closed:
        listed = "; ".join(f"{t['key']} {t['title']} ({t['status']})" for t in closed)
        lines.append(f"- Closed in the last 14 days, don't re-propose declined work: {listed}")
    if not lines:
        return ""
    return "Where the last round stopped:\n" + "\n".join(lines)
