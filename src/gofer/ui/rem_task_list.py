"""Rem's task list as its clients poll it, built again only when something it shows changed.

The desktop's side pane (``GET /api/rem/tasks``) and the phone's ``rem.home`` poll the task
list every few seconds. Building it reads every task and its recent runs, so a large store
made every poll slow. ``TaskList`` keeps the last build and answers from it while:

- the store's ``tasks`` change count (SQLite triggers on tasks, runs and the self-work
  window schedule) is the one it was built at,
- the board's ticket revision (``RemKanban.ticket_revision``), the objectives' revision and
  each project's Ask Rem progress are the ones it was built at, and
- no time-based wait in it has come due since it was built (``next_due``): a waiting
  task's start time, a scheduled occurrence, a run waiting to retry or asleep, or a label
  such as "7:00 PM today" that reads the clock. A wait already past at the build doesn't
  count; the write that settles it does.

List views leave out each task's prompt; ``GET /api/rem/tasks/{id}`` and the phone's
``rem.task`` read one task with it.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from gofer.ui.rem_kanban import task_ticket_keys
from gofer.utils.instants import stored_instant

# How long a list stays good while a label or cost in it reads the clock or outside
# files: waits for a time or the self-work window, and a finished workflow draft's costs.
CLOCK_LABELS_FOR = timedelta(seconds=60)


def next_due(tasks: list[dict[str, Any]], clock: datetime) -> datetime | None:
    """The first moment after ``clock`` a list built then could read differently with no write.

    None when nothing in it depends on the time. Moments at or before ``clock`` are left
    out: the build already read the list as it is past them, and a wait that came due
    changes the list only through a write, which the change count catches. A run can stay
    queued past its ``not_before`` (a retry, a usage-limit wait, or a woken sleeper
    waiting for a free worker), and keeping that moment would build the list again on
    every poll.
    """
    moments: list[datetime | None] = []
    for task in tasks:
        if task.get("enabled", True):
            moments.append(stored_instant(task.get("next_occurrence_at")))
        run = task.get("active_run") or {}
        moments.append(stored_instant(run.get("not_before")))
        sleep = run.get("sleep")
        if isinstance(sleep, dict):
            moments.append(stored_instant(sleep.get("until")))
        for item in (task.get("dependency") or {}).get("prerequisites") or []:
            if item.get("kind") not in {"time", "self_work"}:
                continue
            # "Waiting until 7:00 PM today": the state and the day's name read the clock.
            moments.append(clock + CLOCK_LABELS_FOR)
            if item.get("state") == "waiting" and item.get("kind") == "time":
                moments.append(stored_instant((task.get("schedule") or {}).get("at")))
        draft = task.get("workflow_draft_result") or {}
        if "task_cost" in draft or "test_cost" in draft:
            # Read from the usage ledger and test results, which no trigger counts.
            moments.append(clock + CLOCK_LABELS_FOR)
    return min(
        (moment for moment in moments if moment is not None and moment > clock), default=None
    )


def version(result: dict[str, Any]) -> str:
    """The content version clients send back; the same digest as ``rem_poll_result``."""
    return hashlib.sha256(
        json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]


@dataclass
class Built:
    """One build of the list, and what it was built from."""

    counter: int
    due: datetime | None
    tasks: list[dict[str, Any]]
    # Ticket keys the tasks name, and whether any is an organization's.
    keys: list[str]
    organization_keys: bool
    # Bumped on every build, so callers can cache what they derive from ``tasks``.
    token: int
    tickets_at: tuple[int, int] = (-1, -1)
    tickets: dict[str, dict[str, Any]] = field(default_factory=dict)
    objectives_at: int = -1
    # Each project's Ask Rem progress as last built, which changes without a new revision.
    asking: Any = None
    objectives: dict[str, Any] = field(default_factory=dict)
    version: str = ""


class TaskList:
    """The cached task list ``GET /api/rem/tasks`` and the phone's Rem reads share."""

    def __init__(
        self,
        store: Any,
        kanban: Any,
        objectives: Callable[[], dict[str, Any]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store = store
        self.kanban = kanban
        self.objectives = objectives
        self.clock = clock
        self.builds = 0
        self._built: Built | None = None
        # One build at a time; a poll arriving during a build then reads that build.
        self._lock = threading.Lock()

    def tasks(self) -> Built:
        """The tasks as they are now, built again only when the count or the clock says so.

        Tickets and objectives aren't filled in here; ``view`` adds them.
        """
        with self._lock:
            return self._tasks()

    def _tasks(self) -> Built:
        built = self._built
        clock = self.clock()
        if (
            built is not None
            and built.counter == self.store.change_counts()["tasks"]
            and (built.due is None or clock < built.due)
        ):
            return built
        counter, tasks = self.store.task_list()
        keys = task_ticket_keys(tasks)
        self.builds += 1
        built = Built(
            counter=counter,
            due=next_due(tasks, clock),
            tasks=tasks,
            keys=keys,
            organization_keys=any(not key.startswith("REM-") for key in keys),
            token=self.builds,
        )
        self._built = built
        return built

    def view(self) -> tuple[dict[str, Any], str]:
        """The side pane's list: tasks, their tickets and the objectives, with its version."""
        with self._lock:
            built = self._tasks()
            stale = not built.version
            tickets_at = self.kanban.ticket_revision(organizations=built.organization_keys)
            if tickets_at != built.tickets_at:
                built.tickets = self.kanban.resolve(built.keys)
                built.tickets_at = tickets_at
                stale = True
            objectives = self.objectives()
            if (
                objectives.get("revision") != built.objectives_at
                or objectives.get("asking") != built.asking
            ):
                built.objectives = objectives
                built.objectives_at = int(objectives.get("revision") or 0)
                built.asking = objectives.get("asking")
                stale = True
            result = {
                "tasks": built.tasks,
                "tickets": built.tickets,
                "objectives": built.objectives,
            }
            if stale:
                built.version = version(result)
            return result, built.version
