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

Other start conditions (``waits_for``), each combined with the rest so all must hold
("whichever is later"):

- ``after_running: snapshot``: the runs that were queued or running when the task was
  saved, the user's and self-work's alike (paused ones aren't running and don't count).
  Each must have ended, however it ended: this is "after everything running now is done",
  not "after it all succeeds", so it never holds the task. Work started later doesn't count.
- ``after_running: idle``: a live check that no other Rem run is queued or running. While
  such a task's other conditions are met, self-work starts nothing new, so it can't starve.
- ``after_self_work``: the self-work window running when the task was saved (or the next
  one) has ended. A window that was replaced or extended is followed to its replacement.
- A clock time is the one-time schedule's ``at``: the task doesn't start before it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from gofer.ui import rem_self_work_windows as windows
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
    "results and threads. Other start conditions, which combine with after and with each "
    "other (all must hold, so 'at 7 AM or after X, whichever is later' sets both): a clock "
    "time is schedule:{kind:'once',at:ISO instant with offset,timezone}; "
    "after_running:'snapshot' waits for every task running or queued right now to end, "
    "however it ends ('after everything running is done', 'once the current tasks finish'); "
    "after_running:'idle' waits until nothing else is running at all, a live check, only when "
    "the user's words say so ('whenever nothing is running', 'when you're idle'); "
    "after_self_work:true waits for the self-work window (self_work.window or the next "
    "upcoming one) to end. 'At 7 AM, after all running tasks are done' is a clock time plus "
    "after_running:'snapshot'. These also work with type:reminder and ask_rem:true."
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


# How many running runs one task can wait for; more than Rem ever runs at once.
MAX_RUNNING = 40


def pending(task: dict[str, Any]) -> bool:
    """Whether the task still waits for its prerequisites before its first run."""
    return bool(task.get("prerequisites") or task.get("waits_for")) and not task.get(
        "after_started"
    )


def conditions(
    db: sqlite3.Connection, recipe: dict[str, Any], clock: datetime
) -> dict[str, Any] | None:
    """Pin a task's other start conditions as it is saved; None when it has none."""
    waits: dict[str, Any] = {}
    mode = recipe.get("after_running")
    if mode == "snapshot":
        waits["running"] = [
            {
                "run_id": run["id"],
                "task_id": run["task_id"],
                "title": str(run["recipe"].get("title") or "a task")[:160],
            }
            for run in windows.running_runs(db, users_only=False)
            # Not the task's own runs: it has none yet, but a re-saved one might.
            if run["task_id"] != recipe.get("id")
        ][:MAX_RUNNING]
        waits["running_at"] = clock.isoformat()
    elif mode == "idle":
        waits["idle"] = True
    if recipe.get("after_self_work"):
        window = windows.waitable(db, clock)
        if window is None:
            raise ValueError(
                "Self-work has no window that ends, so nothing can wait for it. Set one first, "
                "such as “self-work until 7 PM”."
            )
        waits["self_work_window"] = window["id"]
    return waits or None


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
            stack.extend(item["task_id"] for item in task.get("prerequisites") or [])


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
        if run["state"] == "paused":
            # Paused isn't over: the task waits for it to be resumed and finish.
            return {**item, "state": "waiting", "reason": f"Waiting for “{title}”, which is paused"}
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


def _condition_items(
    db: sqlite3.Connection, task: dict[str, Any], clock: datetime
) -> list[dict[str, Any]]:
    """One entry per start condition other than named tasks, in the prerequisites' shape."""
    waits = task.get("waits_for") or {}
    items: list[dict[str, Any]] = []
    for entry in waits.get("running") or []:
        row = db.execute("SELECT body FROM runs WHERE id=?", (entry["run_id"],)).fetchone()
        run = json.loads(row[0]) if row else None
        item: dict[str, Any] = {
            "kind": "run",
            "task_id": entry.get("task_id") or "",
            "title": entry.get("title") or "a task",
            "run_id": entry["run_id"],
            "retryable": False,
        }
        if run is not None:
            item.update(run_state=run["state"], thread_id=run.get("thread_id"))
        if run is not None and run["state"] in ACTIVE:
            item.update(
                state="waiting",
                reason=f"Waiting for “{item['title']}” (running when you asked) to finish",
            )
        elif run is not None and run["state"] == "paused":
            item.update(state="waiting", reason=f"Waiting for “{item['title']}”, which is paused")
        else:
            ended = (
                "finished"
                if run is not None and run["state"] == "succeeded"
                else f"ended ({ENDED.get(run['state'], 'ended')})"
                if run is not None
                else "ended"
            )
            item.update(state="met", reason=f"“{item['title']}” {ended}")
        items.append(item)
    if waits.get("idle"):
        others = [
            r for r in windows.running_runs(db, users_only=False) if r["task_id"] != task["id"]
        ]
        items.append(
            {
                "kind": "idle",
                "task_id": "",
                "title": "Nothing else running",
                "retryable": False,
                "state": "waiting" if others else "met",
                "reason": (
                    f"Waiting until no other task is running ({len(others)} now)"
                    if others
                    else "Nothing else is running"
                ),
            }
        )
    if waits.get("self_work_window"):
        done, window = windows.settled(db, waits["self_work_window"])
        if done or window is None:
            items.append(
                {
                    "kind": "self_work",
                    "task_id": "",
                    "title": "Self-work window",
                    "retryable": False,
                    "state": "met",
                    "reason": "The self-work window ended",
                }
            )
        else:
            described = windows.describe(window, clock, db)
            items.append(
                {
                    "kind": "self_work",
                    "task_id": "",
                    "title": "Self-work window",
                    "retryable": False,
                    "state": "waiting",
                    "reason": f"Waiting for self-work to end {described['end_label']}",
                }
            )
    schedule = task.get("schedule") or {}
    at = _instant(schedule.get("at")) if schedule.get("at") else None
    if at is not None:
        label = windows.when_label(at, str(schedule.get("timezone") or "UTC"), clock)
        items.append(
            {
                "kind": "time",
                "task_id": "",
                "title": label,
                "retryable": False,
                "state": "waiting" if at > clock else "met",
                "reason": f"Waiting until {label}" if at > clock else f"It's past {label}",
            }
        )
    return items


def evaluate(
    db: sqlite3.Connection,
    task: dict[str, Any],
    depth: int = 0,
    clock: datetime | None = None,
) -> dict[str, Any]:
    """Where a waiting task stands: ``waiting``, ``held`` with a reason, or ``met``."""
    if depth > MAX_CHAIN:
        return {"state": "waiting", "reason": "Waiting for earlier tasks", "prerequisites": []}
    clock = clock or datetime.now(UTC)
    items = [
        {"kind": "task", **_item(db, entry, depth)} for entry in task.get("prerequisites") or []
    ]
    items.extend(_condition_items(db, task, clock))
    held = [item for item in items if item["state"] == "held"]
    waiting = [item for item in items if item["state"] == "waiting"]
    if held:
        reason = "; ".join(item["reason"] for item in held)
        state = "held"
        reason = f"On hold: {reason}. Retry it, or start this task now."
    elif waiting:
        state = "waiting"
        runs = [item for item in waiting if item["kind"] == "run"]
        named = [item for item in waiting if item["kind"] == "task"]
        parts = []
        if len(named) == 1 and len(waiting) == 1:
            parts.append(named[0]["reason"])
        elif named:
            parts.append("Waiting for " + _names([item["title"] for item in named]) + " to finish")
        if runs:
            parts.append(
                runs[0]["reason"]
                if len(runs) == 1 and len(waiting) == 1
                else f"waiting for {len(runs)} task{'s' if len(runs) != 1 else ''} running when "
                "you asked to finish"
            )
        parts.extend(
            item["reason"][0].lower() + item["reason"][1:] if parts else item["reason"]
            for item in waiting
            if item["kind"] not in {"run", "task"}
        )
        reason = parts[0][0].upper() + parts[0][1:]
        if len(parts) > 1:
            reason = "; ".join([reason, *parts[1:]])
    else:
        state, reason = "met", "Ready to start"
    return {"state": state, "reason": reason, "prerequisites": items}


def idle_waiters(db: sqlite3.Connection, clock: datetime) -> list[str]:
    """Titles of tasks waiting only for nothing else to run, their other conditions met.

    Self-work starts nothing new while there are any, so the condition can come true.
    """
    found: list[str] = []
    for (body,) in db.execute(
        "SELECT body FROM tasks WHERE json_extract(body,'$.waits_for.idle') IS NOT NULL"
    ):
        task = json.loads(body)
        if not pending(task) or not task.get("enabled", True) or task.get("deleted_at"):
            continue
        status = evaluate(db, task, clock=clock)
        others = [item for item in status["prerequisites"] if item["kind"] != "idle"]
        if all(item["state"] == "met" for item in others):
            found.append(str(task["title"]))
    return found


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
        # A snapshot of "everything running" also pins self-work's own runs, which read
        # unvetted tickets and repositories. They only gate the start: their text is not
        # handed to a turn that can act as the user's request.
        if item.get("kind") == "run" and windows._self_started(run):
            continue
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
    items = status["prerequisites"]
    titles = [item["title"] for item in items if item.get("kind", "task") == "task"]
    parts = []
    if titles:
        parts.append(_names(titles) + (" finished" if len(titles) == 1 else " all finished"))
    runs = [item for item in items if item.get("kind") == "run"]
    if runs:
        unfinished = [item["title"] for item in runs if item.get("run_state") != "succeeded"]
        parts.append(
            "the work running when you asked has ended"
            + (f" ({_names(unfinished)} didn't succeed)" if unfinished else "")
        )
    if any(item.get("kind") == "idle" for item in items):
        parts.append("nothing else is running")
    if any(item.get("kind") == "self_work" for item in items):
        parts.append("the self-work window ended")
    if any(item.get("kind") == "time" for item in items):
        parts.append("it's time")
    lead = " and ".join(parts) if parts else "Its start conditions are met"
    return f"{lead[0].upper()}{lead[1:]}, so I started “{task['title']}”."


def queued_text(task: dict[str, Any]) -> str:
    """The chat line saying what a waiting task waits for."""
    titles = [item["title"] for item in task.get("prerequisites") or []]
    waits = task.get("waits_for") or {}
    parts = []
    if titles:
        parts.append(f"after {_names(titles)} {'finishes' if len(titles) == 1 else 'finish'}")
    if "running" in waits:
        running = len(waits["running"])
        parts.append(
            f"once the {running} task{'s' if running != 1 else ''} running now "
            f"{'has' if running == 1 else 'have'} ended"
            if running
            else "right away, since nothing else is running now"
        )
    if waits.get("idle"):
        parts.append("once nothing else is running")
    if waits.get("self_work_window"):
        parts.append("after the self-work window ends")
    schedule = task.get("schedule") or {}
    at = _instant(schedule.get("at")) if schedule.get("at") else None
    if at is not None:
        label = windows.when_label(at, str(schedule.get("timezone") or "UTC"), datetime.now(UTC))
        parts.append(f"not before {label}")
    if not parts:
        return f"“{task['title']}” will start soon."
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"“{task['title']}” will start {joined}."


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
