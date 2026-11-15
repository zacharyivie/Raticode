"""Organization work that gets going again without the user.

Two kinds of stuck work recover by themselves:

- A run that failed because of a temporary provider problem (a rate limit, an overloaded
  or unreachable service, a stream that ended without an answer) waits in Blocked with
  ``RETRY_REASON`` and goes back to To do after a short delay, up to ``len(RETRY_SECONDS)``
  times in a row. Each try is a normal run: it counts against the ticket's and the
  organization's run limits, which stay the user's. Delegated executions and check-ins
  never retry here: an execution may have had outside effects, and a failed check-in is
  replaced by the next one.
- In Auto mode, a ticket Raticode blocked for review after a failed, interrupted or
  rejected run goes to Rem (``organization_auto``), which reopens it or leaves it for
  the user with exactly what the user must do. ``rem_may_reopen`` says which tickets.

The retry state lives in ``runtime["retries"]`` and Rem's reopen count in
``runtime["autoReopens"]``, both keyed by ticket ID and cleared when a run finishes well.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from gofer.ui.rem_ticket_activity import RETRY_REASON

# Seconds to wait before each automatic retry after a temporary provider problem.
RETRY_SECONDS = (60, 300, 900)
# Times Rem may reopen one ticket in Auto mode before it waits for the user.
MAX_REM_REOPENS = 2
NO_FINAL = "The last run ended without a final response"
# Block reasons for runs that stopped without a clean result. Rem may look at these.
STOPPED_REASONS = frozenset(
    {
        NO_FINAL,
        "Worker stopped unexpectedly; review before retrying",
        "Interrupted when Raticode restarted; review it, then reopen",
    }
)
FAILED_PREFIX = "The last run failed"
REJECTED_PREFIX = "Rejected in review by "


def temporary(error: str | None, final: str) -> bool:
    """Whether a run's failure looks temporary, so trying again later may work.

    A run that ended without an answer and without an error is treated like a stream
    that ended early. A run stopped at its time limit is not temporary.
    """
    from gofer.ui.rem_home import transient

    if not error:
        return not final
    return transient(RuntimeError(error))


def schedule_retry(
    runtime: dict[str, Any], task: dict[str, Any], error: str | None, at: datetime | None = None
) -> dict[str, Any] | None:
    """Record the next automatic try for ``task``, or ``None`` when tries ran out.

    Running out clears the record, so a ticket the user or Rem reopens later gets its
    full set of tries again.
    """
    retries = runtime.setdefault("retries", {})
    entry = retries.get(task["id"]) or {}
    count = int(entry.get("count") or 0)
    if count >= len(RETRY_SECONDS):
        retries.pop(task["id"], None)
        return None
    delay = RETRY_SECONDS[count]
    entry = {
        "count": count + 1,
        "of": len(RETRY_SECONDS),
        "at": ((at or datetime.now(UTC)) + timedelta(seconds=delay)).isoformat(),
        "error": str(error or NO_FINAL)[:500],
    }
    retries[task["id"]] = entry
    return entry


def retry_due(org: dict[str, Any], task: dict[str, Any], instant: datetime) -> bool:
    """Whether a ticket waiting to retry may go back to To do now."""
    entry = (org["runtime"].get("retries") or {}).get(task["id"]) or {}
    try:
        return datetime.fromisoformat(str(entry["at"])) <= instant
    except (KeyError, ValueError):
        return True


def waiting_ready(org: dict[str, Any], task: dict[str, Any], instant: datetime) -> bool:
    """Whether blocked work that waits by itself (a pause, closing time or a retry) may
    go back to To do once the organization runs and is open."""
    if task.get("blockReason") == RETRY_REASON:
        return retry_due(org, task, instant)
    return True


def finished_well(runtime: dict[str, Any], task_id: str) -> None:
    """A run finished with a result: its next failure starts a fresh set of tries."""
    for key in ("retries", "autoReopens", "autoUnblocked"):
        (runtime.get(key) or {}).pop(task_id, None)


def blocked_spell(task: dict[str, Any]) -> str:
    """One blocked spell of a ticket: its reason and when it moved to Blocked."""
    at = next(
        (
            str(c.get("at") or "")
            for c in reversed(task.get("comments") or [])
            if c.get("kind") == "status" and c.get("to") == "blocked"
        ),
        "",
    )
    return f"{task.get('blockReason') or ''}|{at}"


def rem_unblock_pending(org: dict[str, Any], task: dict[str, Any]) -> bool:
    """Whether Rem still has to look at this blocked spell (Auto mode or not)."""
    looked = (org["runtime"].get("autoUnblocked") or {}).get(task["id"])
    return rem_may_reopen(org, task) and looked != blocked_spell(task)


def rem_may_reopen(org: dict[str, Any], task: dict[str, Any]) -> bool:
    """Whether Rem's Auto mode review may reopen this blocked ticket.

    Only tickets Raticode blocked after a failed, interrupted or empty run, or that a
    worker rejected in review. Never a ticket the user or Rem blocked or rejected, a
    delegated execution (it may have had outside effects), a check-in, a ticket at its
    run limit (only the user grants more runs), or one Rem already reopened
    ``MAX_REM_REOPENS`` times since its last good run.
    """
    if task.get("status") != "blocked" or task.get("claim") or task.get("heartbeat"):
        return False
    if task.get("execution"):
        return False
    reason = str(task.get("blockReason") or "")
    if not (
        reason in STOPPED_REASONS
        or reason.startswith(FAILED_PREFIX)
        or (
            reason.startswith(REJECTED_PREFIX)
            and not reason.startswith((REJECTED_PREFIX + "you", REJECTED_PREFIX + "Rem"))
        )
    ):
        return False
    if task.get("turns", 0) >= org["config"].get("maxTaskTurns", 0):
        return False
    return int((org["runtime"].get("autoReopens") or {}).get(task["id"], 0)) < MAX_REM_REOPENS


def review_marker(task: dict[str, Any]) -> str:
    """One review request of a ticket: when its result was handed in for review."""
    return str(task.get("reviewRequestedAt") or task.get("revision"))


def reviewer_can_check_in(org: dict[str, Any], task: dict[str, Any]) -> bool:
    """Whether the worker named as the ticket's reviewer can get a check-in to review it:
    an unheld worker other than its assignee, assigned to the ticket's project."""
    from gofer.ui.organization_store import has_assignment, task_root, within_self_work

    eid = task.get("reviewer")
    if not eid or eid == task.get("assignee"):
        return False
    employee = next((e for e in org["config"]["employees"] if e["id"] == eid), None)
    if employee is None or employee.get("paused"):
        return False
    root = task_root(org["config"], task)
    return bool(root) and has_assignment(org, eid) and within_self_work(org, eid, root)


def results_to_review(org: dict[str, Any], eid: str) -> list[dict[str, Any]]:
    """Results waiting on worker ``eid`` as their reviewer that it hasn't been asked to
    look at yet. Each hand-in is asked about once."""
    asked = org["runtime"].get("reviewCheckins") or {}
    return [
        t
        for t in org["runtime"]["tasks"]
        if t["status"] == "in_review"
        and not t.get("claim")
        and t.get("reviewer") == eid
        and asked.get(t["id"]) != review_marker(t)
        and reviewer_can_check_in(org, t)
    ]


def worker_reviews(org: dict[str, Any], task: dict[str, Any]) -> bool:
    """Whether the ticket's result is still with a worker reviewer.

    True while its reviewer can be asked and hasn't been, or while the check-in that asks
    it is still open. Once that check-in ended without a review, or when the reviewer
    is held, gone or not assigned to the ticket's project, the result waits for the
    user, or for Rem in Auto mode.
    """
    if not reviewer_can_check_in(org, task):
        return False
    asked = org["runtime"].get("reviewCheckins") or {}
    if asked.get(task["id"]) != review_marker(task):
        return True
    return any(
        t.get("heartbeat")
        and t["assignee"] == task.get("reviewer")
        and t["status"] in {"backlog", "todo", "in_progress"}
        for t in org["runtime"]["tasks"]
    )


def retry_note(entry: dict[str, Any], error: str | None) -> str:
    at = datetime.fromisoformat(entry["at"]).strftime("%H:%M UTC")
    cause = f": {error}" if error else " (it ended without a final response)"
    return (
        f"The run hit what looks like a temporary provider problem{cause}. Raticode tries "
        f"again at {at} (try {entry['count']} of {entry['of']}). The run counts against "
        "this ticket's run limit."
    )
