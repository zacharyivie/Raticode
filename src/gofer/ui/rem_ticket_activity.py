"""Each board ticket's timeline: what happened to it, in order, and who said what.

Rem's tickets keep their timeline here, in Rem's store. Organization tickets already keep an
append-only conversation; :func:`note_organization_transitions` adds the same kind of status
entries there, and :func:`organization_comment` reads them back in this module's shape.

Status entries are written by the code that moves a ticket, in the same transaction, from the
records that caused the move: the run's state, its stop reason, the question it asks, the
review's checks. Nothing here is made up or written by a model. Moves the user doesn't need
explained (assigning, or leaving Backlog or To do) get no entry.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from gofer.ui.rem_home_models import completion_recap, now
from gofer.ui.rem_home_sleep import asleep

# What one entry may hold. Longer text is kept in ``details``, which clients fold away.
BODY_CHARS = 1200
DETAIL_CHARS = 8000
# The newest entries a ticket's timeline shows; older ones stay stored.
TIMELINE_ENTRIES = 200
COMMENT_CHARS = 20_000
# Rem tries to get one of its blocked tickets going on its own at most this many times,
# until the user moves the ticket or follows it up.
AUTO_UNBLOCKS = 1

STATUS_LABELS = {
    "backlog": "Backlog",
    "todo": "To do",
    "in_progress": "In progress",
    "in_review": "In review",
    "blocked": "Blocked",
    "done": "Done",
    "cancelled": "Cancelled",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS kanban_comments(
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ticket TEXT NOT NULL, dedupe TEXT,
    body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS kanban_comments_ticket ON kanban_comments(ticket, seq);
CREATE UNIQUE INDEX IF NOT EXISTS kanban_comments_dedupe
    ON kanban_comments(ticket, dedupe) WHERE dedupe IS NOT NULL;
"""


def _clip(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def split_text(text: Any) -> tuple[str, str]:
    """A short body and, for long text, the whole of it as folded details."""
    value = str(text or "").strip()
    if len(value) <= BODY_CHARS:
        return value, ""
    return completion_recap(value), _clip(value, DETAIL_CHARS)


# Rem's tickets -------------------------------------------------------------------------------


def add_comment(
    db: sqlite3.Connection,
    ticket_id: str,
    *,
    author: str,
    body: str,
    kind: str = "comment",
    title: str = "",
    status_from: str = "",
    status_to: str = "",
    details: str = "",
    checks: list[dict[str, Any]] | None = None,
    links: dict[str, Any] | None = None,
    dedupe: str | None = None,
) -> dict[str, Any] | None:
    """Append one entry to a Rem ticket's timeline; returns it, or None when it repeats.

    ``dedupe`` names the event, so the same transition seen again after a restart or by a
    second poll is written once. A status entry that says exactly what the last one said is
    also skipped, so a ticket that keeps hitting the same wall doesn't fill its timeline.
    """
    entry = {
        "id": str(uuid4()),
        "at": now(),
        "author": author,
        "kind": kind,
        "title": _clip(title, 200),
        "body": _clip(body, COMMENT_CHARS if kind == "comment" else BODY_CHARS),
        "from": status_from,
        "to": status_to,
        "details": _clip(details, DETAIL_CHARS),
        "checks": (checks or [])[:30],
        **{k: str(v) for k, v in (links or {}).items() if v},
    }
    if kind == "status":
        last = db.execute(
            "SELECT body FROM kanban_comments WHERE ticket=? ORDER BY seq DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()
        previous = json.loads(last[0]) if last else {}
        if (
            previous.get("kind") == "status"
            and previous.get("to") == status_to
            and previous.get("title") == entry["title"]
            and previous.get("body") == entry["body"]
        ):
            return None
    cursor = db.execute(
        "INSERT OR IGNORE INTO kanban_comments(ticket, dedupe, body) VALUES(?,?,?)",
        (ticket_id, dedupe, json.dumps(entry, sort_keys=True)),
    )
    return entry if cursor.rowcount else None


def read_comments(db: sqlite3.Connection, ticket_id: str) -> tuple[list[dict[str, Any]], int]:
    """A Rem ticket's newest entries, oldest first, and how many older ones were left out."""
    total = int(
        db.execute("SELECT COUNT(*) FROM kanban_comments WHERE ticket=?", (ticket_id,)).fetchone()[
            0
        ]
    )
    rows = db.execute(
        "SELECT body FROM kanban_comments WHERE ticket=? ORDER BY seq DESC LIMIT ?",
        (ticket_id, TIMELINE_ENTRIES),
    ).fetchall()
    return [json.loads(r[0]) for r in reversed(rows)], max(0, total - len(rows))


def links(run: dict[str, Any] | None, ticket: dict[str, Any] | None = None) -> dict[str, Any]:
    """The task, run and thread a status entry points to."""
    source = run or {}
    fallback = ticket or {}
    return {
        "task_id": source.get("task_id") or fallback.get("task_id") or "",
        "run_id": source.get("id") or fallback.get("run_id") or "",
        "thread_id": source.get("thread_id") or fallback.get("thread_id") or "",
    }


def run_checks(run: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The run's acceptance checks as its review last judged them, by name."""
    if not run:
        return []
    names = {
        c.get("id"): str(c.get("description") or "")
        for c in (run.get("recipe") or {}).get("acceptance_criteria") or []
    }
    checks = []
    for criterion in run.get("criteria") or []:
        name = names.get(criterion.get("id"))
        if name:
            checks.append({"name": _clip(name, 300), "result": str(criterion.get("result") or "")})
    return checks


def failed_checks(run: dict[str, Any] | None) -> list[str]:
    return [c["name"] for c in run_checks(run) if c["result"] == "fail"]


# What a run says about its ticket ---------------------------------------------------------------


def run_phase(run: dict[str, Any] | None, at: datetime | None = None) -> str:
    """Where a ticket's run is, in the board's terms.

    ``settling`` is a run being stopped: the ticket waits for the outcome rather than moving
    twice. ``waiting`` is finished work whose proof is a future event the review waits for.
    """
    if run is None:
        return "missing"
    state = run.get("state")
    if state == "cancelling":
        return "settling"
    if state == "paused":
        return "paused"
    if state == "queued":
        until = _instant(run.get("not_before")) if run.get("not_before") else None
        if run.get("waiting_for") and until and until > (at or datetime.now(UTC)):
            return "waiting"
        return "review" if run.get("resume_review") else "working"
    if state in {"executing", "repairing"}:
        return "working"
    if state == "verifying":
        return "review"
    if state == "succeeded":
        return "done"
    if state == "blocked":
        return "needs_user" if run.get("needs_user") else "failed"
    if state == "cancelled":
        return "stopped"
    if state == "outcome_unknown":
        return "unknown"
    return "failed"


PHASE_STATUS = {
    "working": "in_progress",
    "review": "in_review",
    "waiting": "blocked",
    # Paused keeps the ticket out of Done and off the "needs you" list; its comment says why.
    "paused": "blocked",
    "done": "done",
    "needs_user": "blocked",
    "failed": "blocked",
    "stopped": "blocked",
    "unknown": "blocked",
    "missing": "blocked",
}


def target_status(
    ticket: dict[str, Any], run: dict[str, Any] | None, at: datetime | None = None
) -> str | None:
    """The column a ticket following its run belongs in now, or None to leave it.

    Only a run that finished after a block can mark a blocked ticket done, so an older
    result never clears a newer problem.
    """
    phase = run_phase(run, at)
    if phase == "settling":
        return None
    target = PHASE_STATUS[phase]
    if ticket["status"] == "blocked" and target == "done" and run is not None:
        finished, blocked = _instant(run.get("finished_at")), _instant(ticket.get("blocked_at"))
        if not finished or (blocked is not None and finished <= blocked):
            return None
    if ticket["status"] == "blocked" and phase == "missing":
        return None
    return target


def sync_key(run: dict[str, Any] | None, at: datetime | None = None) -> str:
    """Names one state of one run, so a status change for it is applied and noted once."""
    if run is None:
        return "missing"
    return f"{run.get('id')}:{run.get('epoch', 0)}:{run_phase(run, at)}"


def block_reason(run: dict[str, Any] | None, at: datetime | None = None) -> tuple[str, str, str]:
    """Why a run leaves its ticket blocked: a headline, the reason and folded details."""
    phase = run_phase(run, at)
    if run is None:
        return "Task record removed", "The record of this ticket's run was removed.", ""
    summary = str(run.get("summary_short") or "").strip()
    if phase == "waiting" and asleep(run):
        # A worker's sleep (rem_home_sleep). Its next check time changes with every sleep,
        # so it stays out of the entry: one entry covers every sleep of this stretch of work.
        awaited = str(run.get("waiting_for") or "an outside event")
        return (
            "Sleeping",
            f"The worker is waiting for {awaited}. Rem wakes it in the same thread to check "
            "again; it holds no worker meanwhile.",
            "",
        )
    if phase == "waiting":
        awaited = str(run.get("waiting_for") or "an outside event")
        return (
            "Waiting",
            f"The work is done and the review is waiting for {awaited} before checking again.",
            "",
        )
    if phase == "paused":
        if (run.get("paused") or {}).get("by") == "priority":
            return (
                "Paused for your work",
                "Paused so your own work could run first. Rem resumes it in the same thread "
                "once your work is done and you've been away 15 minutes.",
                "",
            )
        return (
            "Paused",
            "Paused by you. Resume the task to continue in the same thread.",
            "",
        )
    if phase == "needs_user":
        question = str(run.get("user_question") or summary or "The task needs your decision.")
        body, details = split_text(question)
        return "Waiting for your answer", body, details
    if phase == "stopped":
        return "Task stopped", "The task was stopped before it finished.", ""
    if phase == "unknown":
        body, details = split_text(summary or "The run timed out.")
        return "Outcome unknown", f"The run stopped without a known outcome. {body}", details
    failed = failed_checks(run)
    if failed:
        body = "The review left these checks unresolved: " + "; ".join(failed[:6])
        if len(failed) > 6:
            body += f"; and {len(failed) - 6} more"
        return "Review checks failed", body, _clip(run.get("review_feedback"), DETAIL_CHARS)
    stop = str(run.get("stop_reason") or "")
    if stop:
        return "Task stopped", stop, ""
    body, details = split_text(summary or "The task couldn't finish.")
    return "Task failed", body, details


def transition_entry(
    ticket: dict[str, Any],
    target: str,
    run: dict[str, Any] | None,
    *,
    resumed_by: str = "",
    at: datetime | None = None,
) -> dict[str, Any]:
    """The status entry for a ticket that follows its run from its status to ``target``.

    Returns ``title``, ``body``, ``details`` and ``checks`` taken from the run's records.
    ``resumed_by`` says what Rem did to get a blocked ticket going, when it did.
    """
    before = ticket["status"]
    if target == "blocked":
        headline, body, details = block_reason(run, at)
        return {
            "title": f"Blocked: {headline.lower()}" if headline else "Blocked",
            "body": body,
            "details": details,
            "checks": run_checks(run) if headline == "Review checks failed" else [],
        }
    if target == "in_progress":
        if before == "in_review":
            failed = failed_checks(run)
            body = (
                "The review asked for changes to: " + "; ".join(failed[:6])
                if failed
                else "The review asked for changes, so the worker is fixing them."
            )
            return {
                "title": "Moved back to In progress: changes requested",
                "body": body,
                "details": _clip((run or {}).get("review_feedback"), DETAIL_CHARS),
                "checks": run_checks(run),
            }
        same_run = run is not None and run.get("id") == ticket.get("run_id")
        woke = same_run and before == "blocked" and isinstance((run or {}).get("sleep"), dict)
        why = resumed_by or (
            "The worker woke from its sleep and is checking again in its thread."
            if woke
            else "The task resumed in its thread."
            if same_run
            else "The task was run again."
        )
        return {
            "title": "Moved to In progress: task " + ("resumed" if same_run else "retried"),
            "body": why,
            "details": "",
            "checks": [],
        }
    if target == "in_review":
        result = str((run or {}).get("worker_result") or "").strip()
        body, details = split_text(result or "The worker finished; the review is checking it.")
        return {
            "title": "Work complete, moved to In review",
            "body": body,
            "details": details,
            "checks": [],
        }
    if target == "done":
        verdict = str((run or {}).get("summary_short") or "").strip()
        return {
            "title": "Verified complete, moved to Done",
            "body": verdict or "The independent review accepted the work.",
            "details": "",
            "checks": run_checks(run),
        }
    return {"title": f"Moved to {STATUS_LABELS.get(target, target)}", "body": "", "details": ""}


def note_move(
    db: sqlite3.Connection,
    ticket: dict[str, Any],
    before: str,
    *,
    author: str,
    title: str,
    body: str = "",
    run: dict[str, Any] | None = None,
    dedupe: str | None = None,
    details: str = "",
    checks: list[dict[str, Any]] | None = None,
) -> None:
    """Note a status change of a Rem ticket, unless it only left Backlog or To do."""
    if before == ticket["status"] or (
        before in {"backlog", "todo"} and ticket["status"] in {"backlog", "todo", "in_progress"}
    ):
        return
    add_comment(
        db,
        ticket["id"],
        author=author,
        kind="status",
        title=title,
        body=body,
        details=details,
        checks=checks,
        status_from=before,
        status_to=ticket["status"],
        links=links(run, ticket),
        dedupe=dedupe,
    )


# Organization tickets ---------------------------------------------------------------------------


def _actor_kind(actor: str) -> str:
    if actor == "user":
        return "user"
    if actor.startswith("employee:"):
        return "worker"
    if actor.lower().startswith("rem"):
        return "rem"
    return "system"


def _who(actor: str, employees: dict[str, str]) -> str:
    kind = _actor_kind(actor)
    if kind == "user":
        return "You"
    if kind == "worker":
        identity = actor.split(":", 1)[1]
        return employees.get(identity, identity)
    return "Rem" if kind == "rem" else "Raticode"


def organization_transition(
    org: dict[str, Any], task: dict[str, Any], before: str | None, actor: str
) -> dict[str, Any] | None:
    """The status entry for an organization ticket that moved from ``before``, or None.

    Taken from the ticket itself: its block reason, the dependencies it waits for, its
    evidence and its latest review. Assigning a ticket, moving it out of Backlog or To do, a
    repeating ticket coming round again and heartbeat check-ins get no entry.
    """
    after = task["status"]
    if before is None or before == after or task.get("heartbeat"):
        return None
    if before in {"backlog", "todo"} and after in {"backlog", "todo", "in_progress"}:
        return None
    if before == "done" and after == "todo" and task.get("recurring"):
        return None
    employees = {e["id"]: e["name"] for e in org["config"].get("employees", [])}
    who = _who(actor, employees)
    title, body = f"Moved to {STATUS_LABELS.get(after, after)}", ""
    if after == "blocked":
        reason = str(task.get("blockReason") or "")
        if reason == "dependencies":
            tasks = {t["id"]: t for t in org["runtime"]["tasks"]}
            waiting = [
                tasks[d]["title"]
                for d in task.get("dependsOn") or []
                if d in tasks and tasks[d]["status"] != "done"
            ]
            title = "Blocked: waiting for other tickets"
            body = "Waits for " + "; ".join(f"“{w}”" for w in waiting[:6]) if waiting else ""
        else:
            latest = next(
                (c for c in reversed(task.get("comments") or []) if c.get("kind") != "status"),
                None,
            )
            cause = (
                "review rejected the work"
                if latest and latest.get("decision") == "reject"
                else "waiting"
                if reason in WAITING_REASONS
                else "task failed"
                if reason.startswith("The last run failed")
                else "task stopped"
                if "stopped" in reason.lower() or "cancelled" in reason.lower()
                else ""
            )
            title = f"Blocked: {cause}" if cause else "Blocked"
            body = reason or "No reason was recorded."
    elif after == "in_progress":
        title = (
            "Moved to In progress: resumed"
            if before == "blocked"
            else f"Moved to In progress from {STATUS_LABELS.get(before, before)}"
        )
        body = f"{who} picked it up again." if who != "Raticode" else "Its run started again."
    elif after == "todo" and before == "blocked":
        title = "Unblocked, back in To do"
        body = (
            "The tickets it waited for are done."
            if task.get("dependsOn") and actor != "user"
            else f"Reopened by {who.lower() if who == 'You' else who}."
        )
    elif after == "todo" and before == "in_review":
        # The review's own comment, just above, says what should change.
        title = "Sent back to To do: changes requested"
    elif after == "in_review":
        title = "Work complete, moved to In review"
        final = next(
            (
                c.get("body")
                for c in reversed(task.get("comments") or [])
                if str(c.get("actor", "")).startswith("employee:") and c.get("kind") != "status"
            ),
            "",
        )
        # The worker's report is already in the conversation, so the entry doesn't repeat it.
        body = "" if final else completion_recap(str(task.get("evidence") or "The run finished."))
    elif after == "done":
        review = _latest_review(task)
        title = "Verified complete, moved to Done" if review else "Moved to Done"
        # A review is a comment of its own, just above; only a move without one explains itself.
        evidence = str(task.get("evidence") or "")
        body = "" if review or not evidence else completion_recap(evidence)
        if not review and who != "Raticode":
            body = (body + f" Marked done by {who}.").strip()
    elif after == "cancelled":
        title = "Cancelled"
        body = f"Cancelled by {who}." if who != "Raticode" else ""
    elif after in {"todo", "backlog"}:
        body = f"Moved by {who}." if who != "Raticode" else ""
    return {
        "actor": actor if _actor_kind(actor) != "system" else "system",
        "at": now(),
        "kind": "status",
        "from": before,
        "to": after,
        "title": _clip(title, 200),
        "body": _clip(body, BODY_CHARS),
    }


# An organization ticket blocked with one of these waits for the company to run again or for
# its working hours; Raticode puts it back in To do by itself then.
PAUSED_REASON = "Waiting for the organization to start again"
HOURS_REASON = "Waiting for working hours"
WAITING_REASONS = frozenset({PAUSED_REASON, HOURS_REASON})


def _latest_review(task: dict[str, Any]) -> str:
    review = next((c for c in reversed(task.get("comments") or []) if c.get("decision")), None)
    if not review:
        return ""
    return completion_recap(str(review.get("body") or ""))


def note_organization_transitions(
    org: dict[str, Any], statuses: dict[str, str], actor: str
) -> None:
    """Add a status entry to each of ``org``'s tickets whose status changed from ``statuses``."""
    for task in org["runtime"]["tasks"]:
        entry = organization_transition(org, task, statuses.get(task["id"]), actor)
        if entry is None:
            continue
        comments = task.setdefault("comments", [])
        # The code that moved it may have explained the move itself.
        if (
            comments
            and comments[-1].get("kind") == "status"
            and (
                comments[-1].get("from"),
                comments[-1].get("to"),
            )
            == (entry["from"], entry["to"])
        ):
            continue
        last = next((c for c in reversed(comments) if c.get("kind") == "status"), None)
        if last and all(last.get(k) == entry[k] for k in ("to", "title", "body")):
            continue
        comments.append(entry)


# A comment that says exactly what the same author's previous comment said, within this long,
# is the same comment written twice (a retried request, or a report sent as both a comment
# and the run's final message). The same words said again later are a new comment.
REPEAT_WINDOW_SECONDS = 120


def _repeats(body: str, said: str) -> bool:
    """Whether ``body`` only repeats ``said``: the same text or its opening, as recaps clip it."""
    text = body.strip().removesuffix("…").rstrip()
    return bool(text) and said.strip().startswith(text)


def visible_comments(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """An organization ticket's conversation as people should read it. Stored entries stay.

    Older Raticode wrote a status entry whose text repeated the comment just above it (the
    worker's report, or the review's note), so the same words showed twice. Such an entry
    keeps its headline and loses the repeated text. A comment that repeats the same author's
    previous comment within :data:`REPEAT_WINDOW_SECONDS` is left out; anything said again
    later, by someone else, or with a different review decision is kept.
    """
    shown: list[dict[str, Any]] = []
    # Comments since the last status entry: what a status entry may have copied.
    since_status: list[dict[str, Any]] = []
    for comment in comments:
        body = str(comment.get("body") or "")
        if comment.get("kind") == "status":
            if body and any(_repeats(body, str(c.get("body") or "")) for c in since_status):
                comment = {**comment, "body": ""}
            shown.append(comment)
            since_status = []
            continue
        previous = next((c for c in reversed(shown) if c.get("kind") != "status"), None)
        if previous is not None and _same_comment(previous, comment):
            continue
        shown.append(comment)
        since_status.append(comment)
    return shown


def _same_comment(earlier: dict[str, Any], later: dict[str, Any]) -> bool:
    if any(
        str(earlier.get(k) or "") != str(later.get(k) or "") for k in ("actor", "body", "decision")
    ):
        return False
    first, second = _instant(earlier.get("at")), _instant(later.get("at"))
    return (
        first is not None
        and second is not None
        and abs((second - first).total_seconds()) <= REPEAT_WINDOW_SECONDS
    )


def organization_comment(comment: dict[str, Any], org: dict[str, Any]) -> dict[str, Any]:
    """An organization ticket's comment in the timeline's shape."""
    actor = str(comment.get("actor") or "system")
    employees = {e["id"]: e["name"] for e in org["config"].get("employees", [])}
    kind = "status" if comment.get("kind") == "status" else "comment"
    body, details = (
        (str(comment.get("body") or ""), "")
        if kind == "status"
        else split_text(comment.get("body"))
    )
    decision = comment.get("decision")
    title = str(comment.get("title") or "")
    if decision and not title:
        title = {"accept": "Review: accepted", "revise": "Review: changes requested"}.get(
            str(decision), "Review: rejected"
        )
    return {
        "id": hashlib.sha256(
            f"{comment.get('at', '')}\n{actor}\n{comment.get('body', '')}".encode()
        ).hexdigest()[:20],
        "at": str(comment.get("at") or ""),
        "author": _actor_kind(actor),
        "author_name": _who(actor, employees),
        "kind": kind,
        "title": title,
        "body": body,
        "details": details,
        "from": str(comment.get("from") or ""),
        "to": str(comment.get("to") or ""),
        "checks": [],
    }


def rem_comment(entry: dict[str, Any]) -> dict[str, Any]:
    """A stored Rem ticket entry as clients see it."""
    author = str(entry.get("author") or "system")
    return {
        "id": entry.get("id") or "",
        "at": entry.get("at") or "",
        "author": author if author in {"user", "rem", "system"} else "system",
        "author_name": {"user": "You", "rem": "Rem"}.get(author, "Raticode"),
        "kind": entry.get("kind") or "comment",
        "title": entry.get("title") or "",
        "body": entry.get("body") or "",
        "details": entry.get("details") or "",
        "from": entry.get("from") or "",
        "to": entry.get("to") or "",
        "checks": [
            {"name": str(c.get("name") or ""), "result": str(c.get("result") or "")}
            for c in entry.get("checks") or []
            if isinstance(c, dict)
        ],
        "task_id": entry.get("task_id") or "",
        "run_id": entry.get("run_id") or "",
        "thread_id": entry.get("thread_id") or "",
    }
