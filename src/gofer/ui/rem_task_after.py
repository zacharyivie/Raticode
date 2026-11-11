"""Start conditions that wait for other Rem tasks: "after task X, do task Y".

A one-time task can name prerequisite tasks or runs in ``after``. It stays unstarted
until every prerequisite has finished successfully, then starts once through the
normal run path (``RemHomeStore.add_run``), so worker limits, project checks and
approvals apply exactly as they do to any other run.

When the dependency is saved, each prerequisite is pinned to the moment whose run
decides it (``since``):

- a run named by its ID, or the prerequisite's run that is going now;
- otherwise its next scheduled run, when it has one;
- otherwise its latest run, so a task that already succeeded counts as done;
- otherwise its first run.

From then on the newest run of that task created at or after ``since`` decides. A
follow-up on a blocked run requeues that same run and a retry adds a newer one, so
retrying a prerequisite puts the dependent task back to waiting by itself.

A prerequisite that fails, is stopped, blocked or deleted, or will never run holds
the dependent task. Rem says why once in the chat, and the user can retry the
prerequisite or start the task anyway (``Run now``), which releases the condition.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from gofer.ui.rem_home_models import ACTIVE, now

MAX_AFTER = 10
# How deep a chain of waiting tasks is followed to explain a hold.
MAX_CHAIN = 10
ENDED = {
    "blocked": "was blocked",
    "failed": "didn't finish",
    "cancelled": "was stopped",
    "outcome_unknown": "ended with an unclear result",
}
# Rem's decision rules for start conditions.
RULES = (
    "To start work only after other work succeeds, give the new task after:[IDs]: exact "
    'task or run IDs from context, or "new:N" for the Nth task (counting from 0) earlier '
    "in this same tasks list. It stays queued until each prerequisite's run succeeds, then "
    "starts on its own, and a failed, stopped or deleted prerequisite holds it for the "
    "user. Only one-time tasks can wait. Use this whenever the user says things like "
    "'after X finishes, do Y' or 'when the audit is done, start ...'; never tell the user "
    "this can't be queued. When the follow-up depends on what a prerequisite finds, such "
    "as 'start up to 4 threads to fix the audit's findings', create one task with "
    "type:reminder, ask_rem:true and after set, whose prompt restates the user's request "
    "in their words; when the prerequisite succeeds, Raticode hands that request back to "
    "you with its results so you can split the work then. When the follow-up work is "
    "already clear, create those tasks directly with after. A task's dependency in context "
    "says whether it is waiting or on hold. A current_request with queued_after is the "
    "user's own earlier request that waited for those tasks: act on it now, using their "
    "results and threads."
)
# What a waiting task's next run is told about the work it waited for.
RESULT_LIMIT = 8000
PREREQUISITE_GUIDANCE = (
    "\nThis task waited for the tasks below and started when they succeeded. Their "
    "results are the starting point: read their reports and build on what they found "
    "instead of repeating that work.\nPrerequisite results:\n"
)


def _instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def pending(task: dict[str, Any]) -> bool:
    """Whether the task still waits for its prerequisites before its first run."""
    return bool(task.get("prerequisites")) and not task.get("after_started")


def _task(db: sqlite3.Connection, identity: str) -> dict[str, Any] | None:
    row = db.execute("SELECT body FROM tasks WHERE id=?", (identity,)).fetchone()
    return json.loads(row[0]) if row else None


def _runs(db: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT body FROM runs WHERE task=? ORDER BY json_extract(body, '$.created_at') DESC, "
        "rowid DESC",
        (task_id,),
    ).fetchall()
    return [json.loads(row[0]) for row in rows]


def _pin(db: sqlite3.Connection, prerequisite: dict[str, Any], clock: datetime) -> str:
    runs = _runs(db, prerequisite["id"])
    active = next((r for r in runs if r["state"] in ACTIVE), None)
    if active:
        return str(active["created_at"])
    if prerequisite.get("enabled") and prerequisite.get("next_occurrence_at"):
        return clock.isoformat()
    if runs:
        return str(runs[0]["created_at"])
    return ""


def resolve(
    db: sqlite3.Connection, task_id: str, refs: list[str], clock: datetime
) -> list[dict[str, Any]]:
    """Turn task or run IDs into pinned prerequisites, refusing self-waits and cycles."""
    if len(refs) > MAX_AFTER:
        raise ValueError(f"A task can wait for at most {MAX_AFTER} others")
    resolved: list[dict[str, Any]] = []
    for ref in refs:
        since: str | None = None
        prerequisite = _task(db, ref)
        if prerequisite is None:
            row = db.execute("SELECT body FROM runs WHERE id=?", (ref,)).fetchone()
            if row is None:
                raise ValueError(f"There is no task or run {ref} to wait for")
            run = json.loads(row[0])
            prerequisite = _task(db, run["task_id"])
            if prerequisite is None:
                raise ValueError(f"The task that run {ref} belongs to no longer exists")
            since = str(run["created_at"])
        if prerequisite["id"] == task_id:
            raise ValueError("A task can't wait for itself")
        if prerequisite.get("deleted_at"):
            raise ValueError(f"“{prerequisite['title']}” was deleted, so nothing can wait for it")
        if any(item["task_id"] == prerequisite["id"] for item in resolved):
            continue
        resolved.append(
            {
                "task_id": prerequisite["id"],
                "title": prerequisite["title"],
                "since": since if since is not None else _pin(db, prerequisite, clock),
            }
        )
    _refuse_cycle(db, task_id, [item["task_id"] for item in resolved])
    return resolved


def _refuse_cycle(db: sqlite3.Connection, task_id: str, start: list[str]) -> None:
    seen: set[str] = set()
    stack = list(start)
    while stack:
        current = stack.pop()
        if current == task_id:
            raise ValueError("These tasks would wait for each other, so none could start")
        if current in seen:
            continue
        seen.add(current)
        task = _task(db, current)
        if task and pending(task):
            stack.extend(item["task_id"] for item in task["prerequisites"])


def _newest_since(db: sqlite3.Connection, task_id: str, since: str) -> dict[str, Any] | None:
    runs = _runs(db, task_id)
    if not runs:
        return None
    # Runs are newest first, so only the newest can qualify.
    floor = _instant(since) if since else None
    started = _instant(runs[0].get("created_at"))
    if floor is None or (started is not None and started >= floor):
        return runs[0]
    return None


def _item(db: sqlite3.Connection, entry: dict[str, Any], depth: int) -> dict[str, Any]:
    item = _state(db, entry, depth)
    # The user can run a held prerequisite again unless it is gone or itself waiting.
    item["retryable"] = item["state"] == "held" and item.pop("_retryable", True)
    return item


def _state(db: sqlite3.Connection, entry: dict[str, Any], depth: int) -> dict[str, Any]:
    prerequisite = _task(db, entry["task_id"])
    title = (prerequisite or {}).get("title") or entry.get("title") or "a task"
    item: dict[str, Any] = {"task_id": entry["task_id"], "title": title}
    if prerequisite is None or prerequisite.get("deleted_at"):
        return {**item, "state": "held", "reason": f"“{title}” was deleted", "_retryable": False}
    run = _newest_since(db, prerequisite["id"], str(entry.get("since") or ""))
    if run:
        item.update(run_id=run["id"], run_state=run["state"], thread_id=run.get("thread_id"))
        if run["state"] == "succeeded":
            return {**item, "state": "met", "reason": f"“{title}” finished"}
        if run["state"] in ACTIVE:
            return {**item, "state": "waiting", "reason": f"Waiting for “{title}” to finish"}
        return {**item, "state": "held", "reason": f"“{title}” {ENDED.get(run['state'], 'ended')}"}
    if pending(prerequisite) and prerequisite.get("enabled", True):
        inner = evaluate(db, prerequisite, depth + 1)
        if inner["state"] == "held":
            return {**item, "state": "held", "reason": f"“{title}” is on hold", "_retryable": False}
        return {**item, "state": "waiting", "reason": f"Waiting for “{title}”, which waits too"}
    if prerequisite.get("enabled") and prerequisite.get("next_occurrence_at"):
        return {
            **item,
            "state": "waiting",
            "reason": f"Waiting for “{title}” to run",
            "next_at": prerequisite["next_occurrence_at"],
        }
    if not prerequisite.get("enabled", True):
        return {**item, "state": "held", "reason": f"“{title}” is paused"}
    return {**item, "state": "held", "reason": f"“{title}” isn't set to run"}


def evaluate(db: sqlite3.Connection, task: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Where a waiting task stands: ``waiting``, ``held`` with a reason, or ``met``."""
    if depth > MAX_CHAIN:
        return {"state": "waiting", "reason": "Waiting for earlier tasks", "prerequisites": []}
    items = [_item(db, entry, depth) for entry in task.get("prerequisites") or []]
    held = [item for item in items if item["state"] == "held"]
    waiting = [item for item in items if item["state"] == "waiting"]
    if held:
        reason = "; ".join(item["reason"] for item in held)
        state = "held"
        reason = f"On hold: {reason}. Retry it, or start this task now."
    elif waiting:
        state = "waiting"
        reason = (
            waiting[0]["reason"]
            if len(waiting) == 1
            else "Waiting for " + _names([item["title"] for item in waiting]) + " to finish"
        )
    else:
        state, reason = "met", "Ready to start"
    return {"state": state, "reason": reason, "prerequisites": items}


def _names(titles: list[str]) -> str:
    quoted = [f"“{title}”" for title in titles]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


def results(db: sqlite3.Connection, status: dict[str, Any]) -> list[dict[str, Any]]:
    """What each prerequisite's deciding run found, for the run that waited for it."""
    found = []
    for item in status["prerequisites"]:
        row = db.execute("SELECT body FROM runs WHERE id=?", (item.get("run_id") or "",)).fetchone()
        if not row:
            continue
        run = json.loads(row[0])
        found.append(
            {
                "task_id": item["task_id"],
                "title": item["title"],
                "run_id": run["id"],
                "thread_id": run.get("thread_id"),
                "finished_at": run.get("finished_at"),
                "summary": run.get("summary_short") or "",
                "result": str(run.get("worker_result") or "")[:RESULT_LIMIT],
                "reports": [
                    a["path"]
                    for a in run.get("artifacts", [])
                    if a.get("epoch") == run.get("epoch")
                    and not a.get("superseded")
                    and a.get("path")
                ],
            }
        )
    return found


def started_text(task: dict[str, Any], status: dict[str, Any]) -> str:
    titles = [item["title"] for item in status["prerequisites"]]
    finished = _names(titles) + (" finished" if len(titles) == 1 else " all finished")
    return f"{finished}, so I started “{task['title']}”."


def queued_text(task: dict[str, Any]) -> str:
    titles = [item["title"] for item in task.get("prerequisites") or []]
    return (
        f"“{task['title']}” will start after {_names(titles)} finishes."
        if len(titles) == 1
        else f"“{task['title']}” will start after {_names(titles)} finish."
    )


def hold_notice(task: dict[str, Any], status: dict[str, Any]) -> tuple[str, str]:
    """The chat note for a hold, and an ID that is the same for as long as the hold is."""
    held = [item for item in status["prerequisites"] if item["state"] == "held"]
    key = json.dumps(
        [[item["task_id"], item.get("run_id"), item.get("run_state")] for item in held],
        sort_keys=True,
    )
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    reasons = "; ".join(item["reason"] for item in held)
    body = (
        f"“{task['title']}” is on hold: {reasons}. "
        "Retry the earlier task, or start this one now from its card."
    )
    return body, f"after-held-{task['id']}-{digest}"


def release(task: dict[str, Any], how: str, run_id: str | None = None) -> None:
    """Mark the condition settled so the task never starts from it again."""
    task["after_started"] = {"how": how, "at": now(), "run_id": run_id}
