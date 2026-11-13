"""A worker's timed sleep: park its run for an outside wait and resume the same thread later.

A Rem worker that waits for something outside Raticode, such as a CI run, can't stay silent:
Rem stops a turn after ``TURN_IDLE_SECONDS`` with no provider activity and after
``TURN_LIMIT_SECONDS`` in total. Instead the worker calls ``rem_wait.sleep`` and ends its
turn. Rem then parks the run the way a review's proof wait does: ``queued`` with
``not_before`` and ``waiting_for``. A parked run holds no worker, so its capacity slot is
free, and it keeps its thread, provider session and worktree. When the time comes the
normal admission path starts a new turn in the same thread with a wake message.

No provider can be paused inside a turn, so a sleep always takes effect at a turn boundary:
the tool only records the request, and the run parks once the turn ends.

Run fields:

``pending_sleep``
    What the tool recorded during the current turn, bound to the run's epoch. A Stop, edit
    or follow-up advances the epoch, which voids it.
``sleep``
    The run's sleep record: what it waits for, when it parked and wakes, how many times it
    woke and how much of its wait budget it used. ``asleep`` is true while parked.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from gofer.ui.swarm_tools import TOOL_FAILURE_HINT, SwarmToolServer

if TYPE_CHECKING:
    from gofer.ui.rem_home import RemHome

SERVER_NAME = "rem_wait"
# One sleep lasts this long. The minimum keeps a worker from spinning; the maximum keeps a
# worker from oversleeping a result it could act on.
SLEEP_MIN_MINUTES = 5
SLEEP_MAX_MINUTES = 120
# All of a run's sleeps together: the default budget, which the worker may change, and the
# hard limit, which matches the review's proof wait (PROOF_WAIT_LIMIT).
SLEEP_DEFAULT_TOTAL_HOURS = 12
SLEEP_HARD_CAP_HOURS = 8 * 24
MAX_WAKES = 60
WAITING_FOR_CHARS = 160

# How to wait for CI or another outside job. Every worker turn gets this; the sleep half
# applies only when the turn was given the tool.
WAIT_GUIDANCE = (
    "\nRem stops a turn after 20 minutes with no output from you, and after 2 hours in total. "
    "Background shell jobs, sleep and long silent commands produce no output, and a stopped "
    "turn ends its background jobs. To wait for CI or another outside job, check it in the "
    "foreground in bounded chunks: a loop that checks the status about every 60 seconds, "
    "stops at the first failed job, exits after at most 9 minutes and prints the status, run "
    "with a 10-minute tool timeout. Then decide and check again. Note run IDs in your "
    "messages so a resumed turn can find them. Never end a message with 'waiting for "
    "results' while a background job does the waiting, and never run gh run watch or a "
    "similar watcher without a time bound."
)
SLEEP_GUIDANCE = (
    " For a wait longer than about 10 minutes, call rem_wait.sleep with what you're waiting "
    f"for and when to check again ({SLEEP_MIN_MINUTES} to {SLEEP_MAX_MINUTES} minutes), then "
    "end your turn with one line of status. Rem frees your worker slot and resumes this same "
    "thread when the time is up. Sleeping is not a result: the task is reviewed only after "
    "your final answer."
)


def _instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def clock_time(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%H:%M UTC")


def offered(run: dict[str, Any], role: str) -> bool:
    """Whether this turn gets the sleep tool: worker and repair turns of real work only.

    Reviews keep their own wait (``wait_for``/``wait_until``), so each phase has one way to
    wait. Reminders and conversations have no worker turn to resume.
    """
    recipe = run.get("recipe") or {}
    return (
        role == "execute"
        and recipe.get("type") != "reminder"
        and recipe.get("execution_mode") != "conversation"
    )


def guidance(run: dict[str, Any], role: str) -> str:
    return WAIT_GUIDANCE + (SLEEP_GUIDANCE if offered(run, role) else "")


def asleep(run: dict[str, Any] | None) -> bool:
    """Whether a run is parked by a worker's sleep right now (not merely queued)."""
    return bool(
        run
        and run.get("state") == "queued"
        and isinstance(run.get("sleep"), dict)
        and run["sleep"].get("asleep")
    )


def interrupted(run: dict[str, Any]) -> bool:
    """A sleeping run wakes early when the user edits the task or sends it a message."""
    return bool(run.get("pending_recipe") or run.get("user_prompts"))


def used_seconds(record: dict[str, Any]) -> float:
    return float(record.get("slept_seconds") or 0)


def budget_hours(record: dict[str, Any]) -> float:
    return float(record.get("budget_hours") or SLEEP_DEFAULT_TOTAL_HOURS)


def _hours(seconds: float) -> str:
    hours = seconds / 3600
    if hours < 1:
        minutes = round(seconds / 60)
        return f"{minutes} minute" + ("" if minutes == 1 else "s")
    shown = f"{hours:.1f}".rstrip("0").rstrip(".")
    return f"{shown} hour" + ("" if shown == "1" else "s")


def request(
    run: dict[str, Any], arguments: dict[str, Any], at: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check one sleep call; return what to record on the run and the tool's answer.

    Raises ValueError with a message the worker can act on when the call is out of bounds.
    """
    if set(arguments) - {"waiting_for", "recheck_minutes", "max_total_hours"}:
        raise ValueError("sleep takes waiting_for, recheck_minutes and max_total_hours only")
    waiting_for = " ".join(str(arguments.get("waiting_for") or "").split())[:WAITING_FOR_CHARS]
    if not waiting_for:
        raise ValueError("Say what you are waiting for in waiting_for, such as 'CI run 4294'.")
    minutes = arguments.get("recheck_minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, int | float) or minutes != int(minutes):
        raise ValueError("recheck_minutes must be a whole number of minutes")
    minutes = int(minutes)
    if not SLEEP_MIN_MINUTES <= minutes <= SLEEP_MAX_MINUTES:
        raise ValueError(
            f"recheck_minutes must be between {SLEEP_MIN_MINUTES} and {SLEEP_MAX_MINUTES}. "
            "For a shorter wait, check in the foreground instead."
        )
    total = arguments.get("max_total_hours", SLEEP_DEFAULT_TOTAL_HOURS)
    if (
        isinstance(total, bool)
        or not isinstance(total, int | float)
        or not math.isfinite(total)
        or total <= 0
    ):
        raise ValueError("max_total_hours must be a positive number of hours")
    if total > SLEEP_HARD_CAP_HOURS:
        raise ValueError(
            f"max_total_hours can be at most {SLEEP_HARD_CAP_HOURS} ({SLEEP_HARD_CAP_HOURS // 24} "
            "days)."
        )
    record: dict[str, Any] = run["sleep"] if isinstance(run.get("sleep"), dict) else {}
    wakes = int(record.get("wakes") or 0)
    if wakes >= MAX_WAKES:
        raise ValueError(
            f"This task has already slept {MAX_WAKES} times, the most one run may. Don't wait "
            "any longer: finish what you can and report what is still pending."
        )
    used = used_seconds(record)
    if used + minutes * 60 > total * 3600:
        raise ValueError(
            f"Sleeping {minutes} more minutes would pass this task's wait budget of "
            f"{_hours(total * 3600)} ({_hours(used)} used). Don't wait any longer: finish what "
            "you can and report what is still pending, or check once more in the foreground."
        )
    wake_at = at + timedelta(minutes=minutes)
    pending = {
        "epoch": run["epoch"],
        "waiting_for": waiting_for,
        "minutes": minutes,
        "budget_hours": float(total),
        "at": at.isoformat(),
    }
    answer = {
        "recorded": True,
        "wake_at": wake_at.isoformat(),
        "message": (
            "Wait recorded. End your turn now with one line of status. Rem frees your slot "
            f"and resumes this thread at about {clock_time(wake_at)}. If you keep working "
            "instead, the sleep starts when your turn ends."
        ),
    }
    return pending, answer


def label(record: dict[str, Any]) -> str:
    until = _instant(record.get("until"))
    when = f"Sleeping until {clock_time(until)}" if until else "Sleeping"
    return f"{when} · waiting for {record.get('waiting_for') or 'an outside event'}"


def park(run: dict[str, Any], at: datetime, *, recovered: bool = False) -> dict[str, Any] | None:
    """The fields that park a run whose turn asked to sleep, or None to block it instead.

    Counts start when the turn ends, not when the tool was called. A sleep that would pass
    the run's budget or wake limit returns None, and the caller blocks the run with
    ``cap_reason``.
    """
    pending = run.get("pending_sleep") or {}
    record = dict(run["sleep"]) if isinstance(run.get("sleep"), dict) else {}
    minutes = int(pending.get("minutes") or SLEEP_MIN_MINUTES)
    budget = min(float(pending.get("budget_hours") or budget_hours(record)), SLEEP_HARD_CAP_HOURS)
    if (
        int(record.get("wakes") or 0) >= MAX_WAKES
        or used_seconds(record) + minutes * 60 > budget * 3600
    ):
        return None
    until = at + timedelta(minutes=minutes)
    record.update(
        asleep=True,
        waiting_for=pending.get("waiting_for") or record.get("waiting_for") or "",
        since=record.get("since") or at.isoformat(),
        parked_at=at.isoformat(),
        until=until.isoformat(),
        minutes=minutes,
        budget_hours=budget,
        wakes=int(record.get("wakes") or 0),
        slept_seconds=used_seconds(record),
        woken_by=None,
        recovered=recovered,
    )
    return {
        "state": "queued",
        "not_before": until.isoformat(),
        "waiting_for": record["waiting_for"],
        "waiting_reason": None,
        "phase_label": label(record),
        "pending_sleep": None,
        "sleep": record,
        "active_turn_id": None,
        "active_thread_id": None,
    }


def cap_reason(run: dict[str, Any]) -> str:
    record: dict[str, Any] = run["sleep"] if isinstance(run.get("sleep"), dict) else {}
    awaited = (run.get("pending_sleep") or {}).get("waiting_for") or record.get("waiting_for")
    used = _hours(used_seconds(record))
    if int(record.get("wakes") or 0) >= MAX_WAKES:
        return (
            f"The worker slept {MAX_WAKES} times waiting for {awaited or 'an outside event'}, "
            "the most one run may. Its progress is saved in the thread; ask me to continue."
        )
    return (
        f"Waited {used} for {awaited or 'an outside event'} and it never finished, which is "
        "this task's wait budget. Its progress is saved in the thread; ask me to continue."
    )


def wake(run: dict[str, Any], at: datetime) -> tuple[dict[str, Any], str]:
    """The fields that end a sleep as its worker turn starts, and the message for that turn.

    The time counted against the budget is the sleep asked for, or less if it woke early,
    so time spent waiting for a free worker or paused doesn't use the budget.
    """
    record = dict(run["sleep"])
    parked = _instant(record.get("parked_at")) or at
    slept = min(max((at - parked).total_seconds(), 0), int(record.get("minutes") or 0) * 60)
    by = record.get("woken_by") or "timer"
    record.update(
        asleep=False,
        wakes=int(record.get("wakes") or 0) + 1,
        slept_seconds=used_seconds(record) + slept,
        woke_at=at.isoformat(),
        woken_by=by,
    )
    awaited = record.get("waiting_for") or "the outside event"
    if by == "user":
        opening = f"The user woke you early from your wait for {awaited}."
    elif record.get("recovered"):
        opening = (
            f"Raticode restarted after you asked to sleep while waiting for {awaited}, so "
            "first check what you already did before the restart."
        )
    else:
        opening = f"Your {record.get('minutes')}-minute wait for {awaited} is over."
    budget = budget_hours(record) * 3600
    message = (
        f"{opening} Check it now. If it still isn't finished, call rem_wait.sleep again; "
        "otherwise continue the task. "
        f"Wake {record['wakes']} of at most {MAX_WAKES}; {_hours(record['slept_seconds'])} of "
        f"the {_hours(budget)} wait budget used."
    )
    return {"sleep": record, "phase_label": None}, message


def wake_now(run: dict[str, Any], at: datetime) -> bool:
    """Make a sleeping run due now (Wake now). Returns whether it was asleep."""
    if not asleep(run):
        return False
    run["sleep"] = {**run["sleep"], "woken_by": "user"}
    run["not_before"] = at.isoformat()
    return True


def public(run: dict[str, Any] | None) -> dict[str, Any] | None:
    """What desktop, phone and CLI show of a sleeping run, or None when it isn't asleep."""
    if not asleep(run):
        return None
    assert run is not None
    record = run["sleep"]
    return {
        "waiting_for": str(record.get("waiting_for") or "")[:WAITING_FOR_CHARS],
        "until": str(record.get("until") or ""),
        "since": str(record.get("since") or ""),
        "wakes": int(record.get("wakes") or 0),
        "max_wakes": MAX_WAKES,
        "slept_minutes": int(used_seconds(record) // 60),
        "budget_minutes": int(budget_hours(record) * 60),
    }


@contextmanager
def sleep_tools(home: RemHome, run: dict[str, Any], role: str) -> Iterator[dict[str, Any] | None]:
    """Serve ``rem_wait.sleep`` for one worker turn; revoked when the turn ends.

    Every call checks that the run is still at this turn's epoch, so a Stop, an edit or a
    follow-up during the turn voids the grant.
    """
    if not offered(run, role):
        yield None
        return

    def call(name: str, arguments: dict[str, Any]) -> Any:
        if name != "sleep":
            raise ValueError("Unknown tool")
        with home.store.connect() as db:
            current = home.store.get(db, "runs", run["id"])
            if current["epoch"] != run["epoch"] or current["state"] == "cancelling":
                raise ValueError("This sleep grant was revoked; the task was stopped or changed.")
            pending, answer = request(current, arguments, datetime.now(UTC))
            current["pending_sleep"] = pending
            home.store.put(db, "runs", current)
            home.store.event(
                db,
                "run.sleep_requested",
                {"run_id": run["id"], "minutes": pending["minutes"]},
            )
        return answer

    server = SwarmToolServer()
    url = server.register(
        call,
        [
            {
                "name": "sleep",
                "description": (
                    "Put this task to sleep while you wait for something outside Raticode, "
                    "such as a CI run. Rem frees your worker slot and resumes this same thread "
                    "after recheck_minutes. Call it, then end your turn with one line of "
                    f"status. recheck_minutes is {SLEEP_MIN_MINUTES} to {SLEEP_MAX_MINUTES}. "
                    "max_total_hours is the budget for all sleeps of this task (default "
                    f"{SLEEP_DEFAULT_TOTAL_HOURS}, at most {SLEEP_HARD_CAP_HOURS})."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "waiting_for": {
                            "type": "string",
                            "description": "What you are waiting for, e.g. 'CI run 4294'.",
                        },
                        "recheck_minutes": {
                            "type": "integer",
                            "minimum": SLEEP_MIN_MINUTES,
                            "maximum": SLEEP_MAX_MINUTES,
                        },
                        "max_total_hours": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "maximum": SLEEP_HARD_CAP_HOURS,
                        },
                    },
                    "required": ["waiting_for", "recheck_minutes"],
                    "additionalProperties": False,
                },
                "annotations": {"readOnlyHint": False},
            }
        ],
        "Use sleep only for waits on things outside Raticode that take longer than about "
        "10 minutes.",
        label="Wait",
        failure_hint=TOOL_FAILURE_HINT,
    )
    try:
        yield {"name": SERVER_NAME, "type": "http", "url": url}
    finally:
        server.revoke(url)
        server.close()
