"""Organization workers pulling approved Backlog tickets into To do when they run out of work.

A worker with nothing it can take (no run under way, no To do ticket ready, no blocked
ticket waiting for its look) pulls one Backlog ticket into To do, and the dispatcher then
runs it the usual way. Only tickets the user has already accepted move: proposals a worker
made, tickets that need plan approval and tickets with a request waiting for the user stay
in Backlog. So do tickets someone moved back to Backlog by hand, until someone moves them
out again. The organization setting ``pullBacklog`` turns this on or off.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from gofer.ui.organization_hours import is_open
from gofer.ui.organization_operations import task_reason
from gofer.ui.organization_schedule import active_at, ticket_text
from gofer.ui.organization_schedule import matches as fits_block
from gofer.ui.organization_store import (
    has_assignment,
    now,
    task_root,
    ticket_allowed,
    ticket_prefix,
    within_self_work,
)

PULL_TITLE = "Pulled into To do"
LEVELS = {"critical": 0, "high": 1, "medium": 2, "low": 3}
# A pass never moves more than this many tickets, whatever the concurrency setting says.
MAX_PULLS_PER_PASS = 8


def pulling_on(config: dict[str, Any]) -> bool:
    """Whether the organization lets workers pull Backlog tickets. On unless turned off."""
    return config.get("pullBacklog", True) is not False


def proposal_waiting(org: dict[str, Any], task: dict[str, Any]) -> str:
    """Why a ticket waits for the user's approval before it may leave Backlog, or ''."""
    if task.get("approvalRequired") and task.get("approvedRevision") is None:
        return "Waiting for plan approval"
    # A worker's idea is a proposal until the user moves it out of Backlog or approves it.
    if (task.get("proposedBy") or task.get("parentRunId") or task.get("selfWork")) and task.get(
        "approvedRevision"
    ) is None:
        return "Proposed by a worker. Move it to To do to accept it."
    for request in (org["runtime"].get("consentRequests") or {}).values():
        if (
            isinstance(request, dict)
            and request.get("status") == "pending"
            and (request.get("params") or {}).get("taskId") == task["id"]
        ):
            return "Waiting for your decision on a request about it"
    return ""


def backlog_reason(org: dict[str, Any], task: dict[str, Any]) -> str:
    """What happens next to a Backlog ticket, for the ticket's status line."""
    if waiting := proposal_waiting(org, task):
        return waiting
    if task.get("keepInBacklog"):
        return "Kept in Backlog: it was moved back here, so workers leave it"
    if not pulling_on(org["config"]):
        return "backlog"
    if not task.get("assignee"):
        return "In Backlog. Assign it, or a free worker assigned to its project takes it"
    return "In Backlog until its worker is free to pull it"


def _pullable(
    org: dict[str, Any],
    task: dict[str, Any],
    employee: dict[str, Any],
    instant: datetime,
    owns: Callable[[str], bool],
) -> bool:
    """Whether ``employee`` may pull ``task`` into To do right now."""
    eid = employee["id"]
    if (
        task["status"] != "backlog"
        or task.get("claim")
        or task.get("heartbeat")
        or task.get("keepInBacklog")
        or proposal_waiting(org, task)
    ):
        return False
    bounded = has_assignment(org, eid)
    if task.get("assignee") != eid:
        # Taking an unassigned ticket needs assigned projects and the right to move and
        # edit tickets in its project.
        if task.get("assignee") or not bounded:
            return False
        if not (
            ticket_allowed(employee, task.get("project"), "move")
            and ticket_allowed(employee, task.get("project"), "edit")
        ):
            return False
    root = task_root(org["config"], task)
    if not root or not owns(root):
        return False
    if bounded and not within_self_work(org, eid, root):
        return False
    # Everything else the dispatcher would check before starting it: dependencies, a
    # cancelled epic, a held worker, a start date, run, token and dollar limits, grants,
    # a paused or archived organization and working hours.
    return task_reason(org, {**task, "status": "todo", "assignee": eid}, instant) == "Ready"


def _focus(org: dict[str, Any], instant: datetime) -> dict[str, Any] | None:
    config = org["config"]
    block = active_at(
        (config.get("workSchedule") or {}).get("blocks") or [],
        ZoneInfo((config.get("operatingHours") or {}).get("timezone") or "UTC"),
        instant,
    )
    return block[0] if block else None


def plan_pulls(
    org: dict[str, Any],
    *,
    running: int,
    owns: Callable[[str], bool],
    instant: datetime,
    skip: Collection[str] = (),
) -> list[tuple[dict[str, Any], str]]:
    """The Backlog tickets to pull now, each with the worker it goes to.

    ``running`` is how many runs the organization has under way and ``skip`` the workers
    that have blocked tickets to look at first. Each free worker gets at most one ticket,
    and no more tickets move than the organization has free run slots for.
    """
    config, runtime = org["config"], org["runtime"]
    if not pulling_on(config) or runtime["state"] != "running" or runtime.get("archived"):
        return []
    if not is_open(config.get("operatingHours"), instant):
        return []
    tasks = runtime["tasks"]
    busy = {t["assignee"] for t in tasks if t.get("claim")}
    # Workers with a To do ticket they can start, not counting check-ins, have work.
    queued = {
        t["assignee"]
        for t in tasks
        if t["status"] == "todo"
        and not t.get("claim")
        and not t.get("heartbeat")
        and task_reason(org, t, instant) == "Ready"
    }
    free = min(config["maxConcurrency"] - running - len(queued - busy), MAX_PULLS_PER_PASS)
    if free <= 0:
        return []
    idle = [
        e
        for e in config["employees"]
        if not e.get("paused") and e["id"] not in busy | queued | set(skip)
    ]
    if not idle:
        return []
    focus = _focus(org, instant)

    def rank(pair: tuple[dict[str, Any], dict[str, Any]]) -> tuple[int, int, str, int]:
        task, employee = pair
        fits = fits_block(focus, ticket_text(config, task))
        return (
            LEVELS.get(task.get("priority", "medium"), 2),
            0 if fits else 1,
            str(task.get("createdAt") or ""),
            # A ticket goes to its own worker before anyone takes it unassigned.
            0 if task.get("assignee") == employee["id"] else 1,
        )

    pairs = [
        (task, employee)
        for task in tasks
        if task["status"] == "backlog"
        for employee in idle
        if _pullable(org, task, employee, instant, owns)
    ]
    chosen: list[tuple[dict[str, Any], str]] = []
    for task, employee in sorted(pairs, key=rank):
        if len(chosen) >= free:
            break
        if any(t["id"] == task["id"] or e == employee["id"] for t, e in chosen):
            continue
        chosen.append((task, employee["id"]))
    return chosen


def apply_pulls(
    org: dict[str, Any],
    *,
    running: int,
    owns: Callable[[str], bool],
    instant: datetime,
    skip: Collection[str] = (),
) -> list[dict[str, Any]]:
    """Move the planned tickets into To do, each with one timeline entry. Returns them."""
    config = org["config"]
    names = {e["id"]: e["name"] for e in config["employees"]}
    focus = _focus(org, instant)
    pulled = []
    for task, eid in plan_pulls(org, running=running, owns=owns, instant=instant, skip=skip):
        key = f"pull:{task['id']}:{task['revision']}"
        took = not task.get("assignee")
        task.update(
            status="todo",
            assignee=eid,
            keepInBacklog=False,
            backlogPulledAt=now(),
            revision=task["revision"] + 1,
        )
        why = [f"{task.get('priority', 'medium')} priority"]
        if focus and fits_block(focus, ticket_text(config, task)):
            why.append(f"fits the {focus['label']} block")
        if took:
            why.append("it had no assignee and is in their projects")
        comments = task.setdefault("comments", [])
        # One entry per pull, even if the same write were ever applied twice.
        if not any(c.get("pull") == key for c in comments):
            comments.append(
                {
                    "actor": "system",
                    "at": now(),
                    "kind": "status",
                    "from": "backlog",
                    "to": "todo",
                    "title": PULL_TITLE,
                    "body": f"{names.get(eid, eid)} had no other work it could take, so "
                    f"Raticode moved this from Backlog ({', '.join(why)}). Move it back to "
                    "Backlog to keep it there.",
                    "pull": key,
                }
            )
        number = task.get("number")
        pulled.append(
            {
                "taskId": task["id"],
                "key": f"{ticket_prefix(config)}-{number}" if number else "",
                "title": task["title"][:200],
                "employeeId": eid,
                "employee": names.get(eid, eid),
                "assigned": took,
            }
        )
    return pulled
