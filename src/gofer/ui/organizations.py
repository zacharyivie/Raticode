"""Persistent Rem employees with bounded wakeups and capability-scoped tools."""

from __future__ import annotations

import asyncio
import base64
import copy
import gzip
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import aclosing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from gofer.core.usage_ledger import record_invocation
from gofer.subscriptions.usage import normalize_usage
from gofer.ui import organization_repository as repository
from gofer.ui.organization_execution import (
    OrganizationExecution,
    authorize_workflow,
    reservation,
    target_for,
    validate_execution,
)
from gofer.ui.organization_hours import is_open
from gofer.ui.organization_hours import widens as hours_widen
from gofer.ui.organization_knowledge import (
    ACTIONS as KNOWLEDGE_ACTIONS,
)
from gofer.ui.organization_knowledge import (
    RUN_GUIDE,
    acceptance_evidence,
    annotate,
    knowledge_action,
    run_context,
    start_checks,
    supersede_claims,
    ticket_key,
)
from gofer.ui.organization_operations import (
    READ_OPERATIONS,
    WRITE_OPERATIONS,
    diagnostics,
    operation,
    plan_hold,
    resolve_secrets,
    schedule_routines,
    task_reason,
)
from gofer.ui.organization_packages import (
    export_package,
    github_package,
    parse_package,
    read_directory,
    read_zip,
    safe_path,
    zip_package,
)
from gofer.ui.organization_store import (
    MIN_HEARTBEAT_SECONDS,
    OrganizationConfig,
    OrganizationConflict,
    OrganizationStore,
    Task,
    config_diff,
    now,
    number_tickets,
    project_identity,
    ticket_allowed,
)
from gofer.ui.organization_tools import READ_ACTIONS, TASK_EDIT_FIELDS, TOOL, management_help
from gofer.ui.rem_home_worktree import (
    WorktreeError,
    ensure_worktree,
    plan_worktree,
    release_worktree,
)
from gofer.ui.swarm_tools import TOOL_FAILURE_HINT, SwarmToolServer
from gofer.utils.atomic_output import atomic_binary_output, open_binary_input
from gofer.utils.process import build_subprocess_env

log = logging.getLogger(__name__)
HELP = """Organizations are persistent companies using the Agent Companies v1 draft format.
Use organization_action with action, organizationId and params. Organizations are global.
The actor and available workspace grants are fixed by this session. An organization owns
projectRoots, including all Git worktrees. The legacy config.projects and task.project
fields describe initiatives, not Raticode projects. Add owned projectRoots before binding
an initiative workspacePath. One project can belong to only one organization.
Read before editing; configuration requires expectedRevision
and reason. Display and instruction changes apply without stopping work. Permission changes
revoke affected turns. Restore
creates a new revision and preserves work records, comments and the full audit trail.
Actions:
- list: company summaries.
- providers: optional refresh boolean. Available providers, models and effort choices.
- read: complete configuration and current tasks.
- history: full configuration revisions and field differences, newest first, UTC timestamps.
- events: params.after is a sequence cursor, up to 500 durable activity entries.
- create: params.config is a company configuration; starts paused.
- configure: params.config is the complete replacement configuration, expectedRevision, reason.
- restore: params.revision, expectedRevision, reason.
- export: portable package files as base64 plus a base64 ZIP. No local runtime settings.
- import_preview / import: exactly one of params.files (package paths to base64 bytes),
  params.zip (base64 ZIP), or params.path (folder/ZIP inside an open project).
  Imported employees remain paused at company level. Unsupported includes fail explicitly.
- control: params.state=running or paused. Running preserves individual employee holds and
  authorizes eligible assigned todo tasks and heartbeats. Approval and budget limits still apply.
  When Rem asks to start a company, Raticode records a consent request instead; the user
  approves it in the Organizations tab. The same applies to Rem configuration or restore
  changes that grant danger-full-access or bypassPermissions, add secret grants or
  execution destinations, resume_all, and archive. Rem cannot store credential values.
- archive: params.archived=true/false. Archiving a paused company hides it and releases its
  projects; restoring reclaims them. History and work records are kept.
- request_decide: user-only requestId and decision=approve/decline for a consent request.
- run_events: taskId and turnId. The stored transcript events of one employee run.
- audit_verify: checks the configuration history chain, the hash-linked activity log and the
  anchor file that records the log's head outside the database.
- run_input: taskId and turnId. Exactly what one employee run was given, checked against
  the run's inputSha256.
Staged work: create planned tasks with status=backlog. Start never runs backlog; move a task
to todo to activate it. Express ordering with dependsOn task IDs, not prose, and do not write
"not authorized" into descriptions of work you expect to run.
Limits: monthlyTurnLimit (default 200 runs; 0 is unlimited), monthlyTokenLimit and
monthlyBudgetUsd at company, employee and initiative level. Codex does not report dollar
cost, so prefer token or run limits for Codex employees. maxChildTasksPerTurn (default 5)
and maxDelegationDepth (default 3) bound the tasks employees create for each other.
Isolation: workspaceIsolation=worktree (default) runs each task on its own git branch in a
separate worktree; checkout edits the project folder and requires a clean checkout.
blockGitPush (default true) makes git push fail inside employee runs.
- task_create: params includes title, description, assignee, optional parentId, project,
  goal, priority, approvalRequired, recurring and intervalSeconds. Returns the new task.
- task_update: params.taskId, expectedRevision and changes. Editable fields: title,
  description, assignee, project, parentId, goal, priority, status, evidence, dueAt,
  recurring, intervalSeconds, approvalRequired. Changed plans invalidate approvals.
- comment: params.taskId, body. Durable task conversation; does not automatically run work.
- approve: params.taskId, expectedRevision. Only the user/Rem or the designated independent
reviewer.
- review: taskId, expectedRevision, decision=accept/revise/reject and body. Requires finished work.
- reopen: taskId, expectedRevision. Explicitly reopen done, cancelled or blocked work.
- resume_all: expectedRevision. Explicitly lift all employee holds.
- tasks: query, assignee, project, goalId, status, attention, offset and limit.
- attempts / routines: paginated durable run/schedule records. Optional taskId for attempts.
- diagnostics: dispatch reasons, attention inbox, effective concurrency and budget warnings.
- outcomes: accepted results, rework and known cost coverage by employee and goal.
- rehearse: optional at and assumedDurationSeconds. No model calls or execution.
- routine_save: routine={name, task, cron or intervalSeconds, timezone, overlap=skip/queue,
  missed=skip/coalesce, enabled}. Update uses id and expectedRevision.
- routine_trigger: routineId and idempotencyKey. API trigger creates one durable occurrence.
- memory_save: employeeId, body, path, optional expiresAt. Stores source hash and verification time.
- memory_check: invalidate changed/expired evidence and queue a single backlog recheck.
- artifact_add: taskId, attemptId, path, kind=file/diff/test/document, optional check.
- workflow_preview: taskId with workflowContract.path, inputs, allowedResources, completionChecks.
  Compiles without executing; launch requires workflow authorization.
- secret_reference: id, account, optional revoked. Versioned OS keyring references, never values.
Task create/update also support workspacePath, goalId, dependsOn, reviewRequired and reviewer.
Creation, wake and comment accept idempotencyKey; reuse it on retries.
Managed delegation uses child employee tasks with parentId and parentRunId, under company limits.
To delegate to another runtime, task_create accepts execution={targetId: configured destination ID}.
Use the destination's granted assignee. This creates a managed child task; never launch it outside
organization accounting. Direct swarm/fleet mutation tools remain inspection-only in employee chat.
- workflow_launch: user/Rem-only taskId, expectedRevision, irSha256 from workflow_preview.
  Authorizes and queues the reviewed contract; changed source/contracts require new authorization.
- webhook_save: user/Rem-only routineId, expectedRevision, secretRef and enabled.
- execution_reconcile: user/Rem-only retry stop confirmation for interrupted external work.
- execution_resolve: attemptId, confirmStopped=true, reason with actual stop inspection evidence.
- routing_experiment: employeeId, taskIds (1..10 pending tasks), reason with trial hypothesis.
- import_github_preview / import_github: repository, pinned commit SHA, optional directory.
Configuration edits cover every settings field, including employees, teams, reporting lines,
initiatives, goals, projectRoots, budgets, resources, executionTargets, Second Brain and theme.
Call help for complete action parameters, configuration/task/routine schemas and workspacePaths.
- secret_store: user-only id and value; stores credentials in the OS keyring, never history.
- backup: user/Rem-only consistent SQLite backup of every organization and operational record.
- memory: employee-only params.body stores working memory, separate from configuration.
- wake: params.employeeId and message. Creates an assigned task; runs only if company is running.
Plans: config.mission, projects (shown as Projects), milestones (organization milestones have no
projectId; project milestones may serve one through parentId) and epics inside project milestones.
Use plan_save and plan_delete for single items. Tickets are tasks with epicId and keys like RAT-12;
doneWhen says what finished means and knowledge lists linked notes.
Knowledge: knowledge_search, knowledge_read, knowledge_save, knowledge_link and knowledge_import
(Second Brain notes). Runs post at most 5 claims with claim_add; Raticode checks their evidence.
Ticket permissions: employees[].ticketAccess maps project IDs (or "*" for tickets outside a
project) to allowed actions create, edit, move and cancel; null means unrestricted. Raticode
enforces them on every worker ticket action. Widening them from Rem needs the user's consent.
Shared repository: plan_save kind=repository turns on one Git repository per organization;
runs change it on their ticket's shared/ branch and accepting the ticket merges it.
Employees can read their company and coordinate tasks, but cannot configure, restore,
import, create companies, or start/stop the company. They may delegate work to
existing employees. Employee task updates require ownership or a reporting-line manager.
Do not edit organization storage through shell or filesystem. Use only these tools.
"""
CONFIG_ACTIONS = {
    "create",
    "configure",
    "restore",
    "import",
    "control",
    "import_github",
    "archive",
    "request_decide",
}
# Rem may ask for these; the user approves them in the Organizations tab.
CONSENT_ACTIONS = {
    "control",
    "configure",
    "restore",
    "resume_all",
    "archive",
    "approve",
    "workflow_launch",
    "webhook_save",
    "routine_save",
    "task_create",
    "task_update",
    "review",
    "execution_resolve",
    "secret_reference",
}
# A request Rem made more than this long ago can no longer be approved.
CONSENT_TTL = timedelta(hours=24)
# How long a retry can reuse an idempotency key.
REQUEST_RETENTION = timedelta(days=30)
# Turn events stored per run. Final, error and usage events are always kept.
MAX_TURN_EVENTS = 1000
# Reasons that let the scheduler claim, block or pause; anything else leaves the company idle.
DISPATCH_REASONS = {
    "Ready",
    "Task turn limit reached",
    "Workspace is outside employee grants",
    "Monthly turn limit: company",
    "Monthly token limit: company",
    "Monthly budget reached: company",
    "Cost coverage unavailable: company",
    "Token counts unavailable: company",
}


def _dirty(root: str) -> bool:
    """Whether a Git checkout has uncommitted changes. Unknown counts as dirty."""
    try:
        result = subprocess.run(
            ["git", "-C", root, "status", "--porcelain", "--untracked-files=normal"],
            env=build_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return result.returncode != 0 or bool(result.stdout.strip())


def _worktree_state(root: str) -> dict[str, Any]:
    """The worktree's commit after a run and whether it left uncommitted changes."""
    try:
        head = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            env=build_subprocess_env(),
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {}
    return {"headCommit": head, "uncommittedChanges": _dirty(root)}


def actor_name(org: dict[str, Any], actor: str) -> str:
    eid = actor.removeprefix("employee:")
    return next((e["name"] for e in org["config"]["employees"] if e["id"] == eid), eid)


# Permission modes that only read. Any other change of mode can widen what an employee does.
READ_ONLY_MODES = frozenset({"read-only", "plan"})
# Limits where 0 means "no limit" and a larger number allows more.
OPEN_ENDED_LIMITS = ("monthlyTurnLimit", "monthlyTokenLimit", "monthlyBudgetUsd")
COMPANY_CAPS = (
    "maxTaskTurns",
    "maxConcurrency",
    "turnTimeoutSeconds",
    "maxChildTasksPerTurn",
    "maxDelegationDepth",
)


MAX_RUN_INPUT_BYTES = 16 * 1024 * 1024


def write_run_input(path: Path, content: bytes) -> None:
    """Store a run's input compressed and private to this user."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as handle:
        handle.write(content)


def more_often(previous: dict[str, Any], routine: dict[str, Any]) -> bool:
    """Whether a routine edit schedules work sooner or keeps more of it."""
    old, new = previous.get("intervalSeconds") or 0, routine.get("intervalSeconds") or 0
    return bool(
        (new and (not old or new < old))
        or (routine.get("cron") and routine.get("cron") != previous.get("cron"))
        or (routine.get("overlap") == "queue" and previous.get("overlap") != "queue")
        or (routine.get("missed") == "coalesce" and previous.get("missed") == "skip")
    )


# Blocks set by Raticode, the user or Rem; employees may lift only their own kind.
def takes_unassigned(task: dict[str, Any], params: dict[str, Any], employee_id: str) -> bool:
    """A worker may take an unassigned ticket for itself, and nothing more in that change."""
    changes = params.get("changes") or {}
    return (
        not task.get("assignee")
        and changes.get("assignee") == employee_id
        and set(changes) <= {"assignee", "status"}
    )


HEARTBEAT_TITLE = "Heartbeat: review assignments"
# A worker's heartbeat stops replacing its failed check-ins after this many in a row.
MAX_HEARTBEAT_FAILURES = 3


def is_heartbeat(task: dict[str, Any]) -> bool:
    # Check-ins saved before the heartbeat flag existed are known by their title.
    return bool(task.get("heartbeat")) or task.get("title") == HEARTBEAT_TITLE


def heartbeat_due(org: dict[str, Any], eid: str) -> str:
    """Give a worker a check-in when it has nothing to do; never pile them up.

    Returns what happened: created, busy, waiting, replaced or stalled.
    """
    runtime = org["runtime"]
    mine = [t for t in runtime["tasks"] if t["assignee"] == eid]
    if any(t["claim"] or (t["status"] == "todo" and task_reason(org, t) == "Ready") for t in mine):
        return "busy"
    open_checkins = [
        t for t in mine if is_heartbeat(t) and t["status"] in {"backlog", "todo", "in_progress"}
    ]
    if open_checkins:
        return "waiting"
    failures = runtime.setdefault("heartbeatFailures", {})
    stalls = runtime.setdefault("heartbeatStalls", {})
    blocked = [t for t in mine if is_heartbeat(t) and t["status"] == "blocked"]
    outcome = "created"
    if blocked:
        if failures.get(eid, 0) >= MAX_HEARTBEAT_FAILURES:
            if eid not in stalls:
                stalls[eid] = {
                    "taskId": blocked[-1]["id"],
                    "at": now(),
                    "reason": blocked[-1].get("blockReason") or "The check-in was blocked",
                }
            return "stalled"
        # A failed check-in is replaced by a fresh one instead of silently stopping
        # heartbeats; a run of failures stops and asks for attention.
        for task in blocked:
            task.update(
                status="cancelled", cancellation="acknowledged", revision=task["revision"] + 1
            )
            task["comments"].append(
                {"actor": "system", "at": now(), "body": "Replaced by the next heartbeat check-in."}
            )
        failures[eid] = failures.get(eid, 0) + 1
        outcome = "replaced"
    stalls.pop(eid, None)
    runtime["tasks"].append(
        Task(
            title=HEARTBEAT_TITLE,
            assignee=eid,
            heartbeat=True,
            description="Review organization goals and blocked work. "
            "Delegate or record the next useful action.",
        ).model_dump()
    )
    return outcome


def recurring_widened(before: dict[str, Any] | None, after: dict[str, Any]) -> bool:
    """Whether a change makes a ticket repeat, or repeat more often."""
    if not (after.get("recurring") and after.get("intervalSeconds")):
        return False
    if not before or not (before.get("recurring") and before.get("intervalSeconds")):
        return True
    return int(after["intervalSeconds"]) < int(before["intervalSeconds"])


def check_recurring(
    before: dict[str, Any] | None, after: dict[str, Any], employee_id: str | None
) -> None:
    """Repeating work is scheduled by the user or Rem, never more often than every 15 minutes.

    Saved schedules keep working; the floor applies when a schedule is created or changed.
    """
    changed = before is None or any(
        before.get(key) != after.get(key) for key in ("recurring", "intervalSeconds")
    )
    if not changed or not after.get("recurring"):
        return
    if employee_id:
        raise ValueError(
            "Workers can't schedule recurring work. Ask the user or Rem to set up a routine."
        )
    interval = after.get("intervalSeconds") or 0
    if 0 < interval < MIN_HEARTBEAT_SECONDS:
        raise ValueError("Recurring work must be at least 15 minutes (900 seconds) apart")


def employee_may_unblock(task: dict[str, Any]) -> bool:
    reason = task.get("blockReason") or ""
    return reason in {"", "dependencies"} or (
        reason.startswith("Blocked by ")
        and not reason.startswith(("Blocked by you", "Blocked by Rem"))
    )


def expire_requests(requests: dict[str, Any]) -> list[str]:
    """Mark pending consent requests older than CONSENT_TTL as expired."""
    cutoff = datetime.now(UTC) - CONSENT_TTL
    expired = [
        r["id"]
        for r in requests.values()
        if r["status"] == "pending" and datetime.fromisoformat(r["at"]) < cutoff
    ]
    for rid in expired:
        requests[rid].update(status="expired", decidedAt=now())
    return expired


def _loosened(before: dict[str, Any], after: dict[str, Any], keys: tuple[str, ...]) -> list[str]:
    """Open-ended limits that were removed (set to 0) or raised."""
    changed = []
    for key in keys:
        old, new = before.get(key) or 0, after.get(key) or 0
        if old and (not new or new > old):
            changed.append(key)
    return changed


def _tool_names(resources: dict[str, Any]) -> set[str]:
    resources = resources or {}
    names = {
        flag
        for flag in ("shell", "web", "agents", "browser")
        if resources.get(flag, flag != "browser")
    }
    names |= {f"mcp:{server.get('name')}" for server in resources.get("mcpServers", [])}
    names |= {f"skill:{skill.get('path')}" for skill in resources.get("skills", [])}
    return names


def escalation(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Describe configuration changes that widen what employees may do, or return "".

    Rem may make any other change directly. These need the user's approval because they
    let employees run more, run sooner, reach more tools or folders, or spend more.
    """
    reasons = []
    old = {e.get("id"): e for e in before.get("employees", [])}
    for employee in after.get("employees", []):
        name = employee.get("name") or employee.get("id")
        previous = old.get(employee.get("id"))
        if previous is None:
            if not employee.get("paused"):
                reasons.append(f"add {name} as an active employee")
            previous = {"paused": True, "permissionMode": "read-only", "resources": {}}
        elif previous.get("paused") and not employee.get("paused"):
            reasons.append(f"resume {name}")
        mode = employee.get("permissionMode")
        if mode != previous.get("permissionMode") and mode not in READ_ONLY_MODES:
            reasons.append(f"change {name}'s permissions to {mode}")
        if _tool_names(employee.get("resources", {})) - _tool_names(previous.get("resources", {})):
            reasons.append(f"give {name} more tools")
        if set(employee.get("skills") or []) - set(previous.get("skills") or []):
            reasons.append(f"give {name} more skills")
        grants, old_grants = employee.get("workspacePaths"), previous.get("workspacePaths", [])
        if previous.get("id") and (
            (grants is None and old_grants is not None) or set(grants or []) - set(old_grants or [])
        ):
            reasons.append(f"let {name} work in more folders")
        if set(employee.get("secretRefs") or []) - set(previous.get("secretRefs") or []):
            reasons.append(f"grant credentials to {name}")
        access, old_access = employee.get("ticketAccess"), previous.get("ticketAccess")
        if (
            previous.get("id")
            and old_access is not None
            and (
                access is None
                or any(
                    set(actions) - set(old_access.get(project, []))
                    for project, actions in access.items()
                )
            )
        ):
            reasons.append(f"let {name} do more with tickets")
        if _loosened(previous, employee, OPEN_ENDED_LIMITS):
            reasons.append(f"raise or remove {name}'s monthly limits")
        beat, old_beat = (
            employee.get("heartbeatSeconds") or 0,
            previous.get("heartbeatSeconds") or 0,
        )
        if beat and (not old_beat or beat < old_beat):
            reasons.append(f"wake {name} more often")
    if _loosened(before, after, OPEN_ENDED_LIMITS):
        reasons.append("raise or remove the organization's monthly limits")
    if any((after.get(key) or 0) > (before.get(key) or 0) for key in COMPANY_CAPS if key in before):
        reasons.append("raise operating limits")
    initiatives = {p.get("id"): p for p in before.get("projects", [])}
    if any(
        _loosened(initiatives.get(p.get("id"), {}), p, OPEN_ENDED_LIMITS)
        for p in after.get("projects", [])
    ):
        reasons.append("raise or remove initiative limits")
    if set(after.get("projectRoots", [])) - set(before.get("projectRoots", [])):
        reasons.append("add project folders")
    targets = {t.get("id"): t for t in before.get("executionTargets", [])}
    if any(targets.get(t.get("id")) != t for t in after.get("executionTargets", [])):
        reasons.append("add or change execution destinations")
    if any(
        (p.get("checks") or []) != (initiatives.get(p.get("id"), {}).get("checks") or [])
        and any(
            c not in (initiatives.get(p.get("id"), {}).get("checks") or [])
            for c in p.get("checks") or []
        )
        for p in after.get("projects", [])
    ):
        # Raticode runs project checks itself, outside the employees' sandbox.
        reasons.append("add commands Raticode runs to check claims")
    if any(
        p.get("autoRunChecks") and not initiatives.get(p.get("id"), {}).get("autoRunChecks")
        for p in after.get("projects", [])
    ):
        reasons.append("rerun project checks automatically after every run")
    if after.get("knowledgeToSecondBrain") and not before.get("knowledgeToSecondBrain"):
        reasons.append("write organization knowledge into the Second Brain folder")
    if after.get("sharedRepository") and not before.get("sharedRepository"):
        reasons.append("give employees a shared organization repository")
    if hours_widen(before.get("operatingHours"), after.get("operatingHours")):
        reasons.append("let employees work at more hours")
    if after.get("resumeAfterRestart") and not before.get("resumeAfterRestart"):
        reasons.append("keep the organization running after Raticode restarts")
    if before.get("blockGitPush", True) and after.get("blockGitPush", True) is False:
        reasons.append("allow git push")
    if (
        before.get("workspaceIsolation", "worktree") == "worktree"
        and after.get("workspaceIsolation", "worktree") == "checkout"
    ):
        reasons.append("let employees edit the project folder directly")
    return ("Change settings to " + ", ".join(dict.fromkeys(reasons))) if reasons else ""


class OrganizationManager:
    def __init__(
        self,
        data_dir: Path,
        *,
        stream: Callable[..., AsyncGenerator[dict[str, Any], None]],
        start_runtime: bool = True,
        enabled: bool = True,
        swarms: Any = None,
        fleet: Any = None,
    ) -> None:
        self.enabled = enabled
        self.store = OrganizationStore(data_dir)
        self.data_dir = data_dir
        self.stream = stream
        self.executions = OrganizationExecution(self, swarms, fleet)
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._wake = threading.Event()
        self._active: dict[str, tuple[str, threading.Event, threading.Thread]] = {}
        self._active_roots: dict[str, str] = {}
        self._cancel_reasons: dict[str, str] = {}
        self._dispatch_cursor = 0
        self._secret_values: dict[str, list[str]] = {}
        self._http: SwarmToolServer | None = None
        self._thread: threading.Thread | None = None
        # Never replay work with unknown external effects after process loss.
        with self.store.connect() as db:
            rows = db.execute("SELECT project, id FROM organizations").fetchall()
        for row in rows:
            self.store.mutate(
                row["project"], row["id"], "system", "runtime_recovered", self._recover
            )
        # Remote reconciliation belongs to the worker, never backend startup.
        # Even while disabled, finish cancellation recovery for previously authorized jobs.
        if start_runtime:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="organizations")
            self._thread.start()

    @staticmethod
    def _recover(org: dict[str, Any]) -> dict[str, Any]:
        # A request that was being applied when Raticode stopped may be half done.
        for request in (org["runtime"].get("consentRequests") or {}).values():
            if request["status"] == "applying":
                request.update(
                    status="failed",
                    error="Raticode restarted while applying this request; check the result",
                )
        # A company that was already paused with nothing running has nothing to recover.
        if org["runtime"]["state"] == "paused" and not any(
            task["claim"] for task in org["runtime"]["tasks"]
        ):
            return {}
        # Self-running organizations keep going; interrupted tickets still wait for review.
        keep_running = (
            org["config"].get("resumeAfterRestart") is True
            and org["runtime"]["state"] == "running"
            and not org["runtime"].get("archived")
        )
        if not keep_running:
            org["runtime"]["state"] = "paused"
            org["runtime"]["pauseReason"] = "Paused because Raticode restarted"
        for task in org["runtime"]["tasks"]:
            if task["claim"]:
                attempt = org["runtime"].get("attempts", {}).get(task["claim"])
                if attempt:
                    attempt.update(status="interrupted", finishedAt=now())
                task.update(
                    claim=None,
                    status="blocked",
                    blockReason="Interrupted when Raticode restarted; review it, then reopen",
                    revision=task["revision"] + 1,
                )
                task["comments"].append(
                    {
                        "actor": "system",
                        "at": now(),
                        "body": "Raticode restarted. Review interrupted work before retrying.",
                    }
                )
        return {
            "reason": "Kept running after a restart; interrupted work requires review"
            if keep_running
            else "Runtime started paused; interrupted work requires review"
        }

    def close(self) -> None:
        self._closed.set()
        self._wake.set()
        with self._lock:
            active = list(self._active.values())
            for _, cancel, _ in active:
                cancel.set()
        if self._thread:
            self._thread.join(timeout=3)
        for _, _, thread in active:
            thread.join(timeout=2)
        if self._http:
            self._http.close()

    def cancel(self, oid: str, reason: str = "cancelled") -> None:
        with self._lock:
            for turn_id, (organization, cancel, _) in self._active.items():
                if organization == oid:
                    self._cancel_reasons.setdefault(turn_id, reason)
                    cancel.set()

    def _consent_reason(
        self, project: str, org: dict[str, Any], action: str, params: dict[str, Any]
    ) -> str:
        """Why an action from Rem needs the user's approval, or "" when it does not."""
        if action == "control":
            return "Start the organization" if params.get("state") == "running" else ""
        if action == "resume_all":
            return "Lift every employee hold"
        if action == "archive":
            return "Archive the organization" if params.get("archived", True) else ""
        if action == "approve":
            # Plan approval is the human gate on approval-required work.
            task = next(
                (t for t in org["runtime"]["tasks"] if t["id"] == params.get("taskId")), None
            )
            return f"Approve the plan for \u201c{task['title']}\u201d" if task else ""
        if action == "workflow_launch":
            return "Authorize a workflow to run"
        if action == "webhook_save":
            return "Let an outside service trigger a routine" if params.get("enabled") else ""
        if action == "routine_save":
            routine = params.get("routine") or {}
            previous = org["runtime"].get("routines", {}).get(routine.get("id"), {})
            name = routine.get("name", "routine")
            if not routine.get("enabled", True):
                return ""
            if not previous.get("enabled", False):
                return f"Schedule recurring work: {name}"
            return f"Run the routine {name} more often" if more_often(previous, routine) else ""
        task = next((t for t in org["runtime"]["tasks"] if t["id"] == params.get("taskId")), None)
        title = f"\u201c{task['title']}\u201d" if task else "a task"
        if action == "task_create":
            if params.get("recurring") and params.get("intervalSeconds"):
                return f"Schedule recurring work: \u201c{params.get('title') or 'a task'}\u201d"
            return ""
        if action == "task_update" and task:
            changes = params.get("changes") or {}
            if recurring_widened(task, {**task, **changes}):
                return f"Run {title} on a schedule, or more often"
            if task["approvalRequired"] and changes.get("approvalRequired") is False:
                return f"Remove the approval step from {title}"
            if task.get("reviewRequired") and changes.get("reviewRequired") is False:
                return f"Remove independent review from {title}"
            return ""
        if action == "review":
            accepting = params.get("decision") == "accept" and task and task.get("reviewRequired")
            return f"Accept the result of {title}" if accepting else ""
        if action == "execution_resolve":
            return "Confirm that interrupted outside work has stopped"
        if action == "secret_reference":
            previous = org["runtime"].get("secrets", {}).get(params.get("id"), {})
            if (
                previous
                and not params.get("revoked")
                and (previous.get("revoked") or previous.get("account") != params.get("account"))
            ):
                return f"Re-enable or repoint the credential {params.get('id')}"
            return ""
        after = params.get("config")
        if action == "restore":
            after = next(
                (
                    r["config"]
                    for r in self.store.history(project, org["id"])
                    if r["revision"] == params.get("revision")
                ),
                None,
            )
        # Stale or invalid requests fail normally instead of waiting for consent.
        if not isinstance(after, dict) or params.get("expectedRevision") != org["revision"]:
            return ""
        try:
            after = OrganizationConfig.model_validate(after).model_dump()
        except ValueError:
            return ""
        reason = escalation(org["config"], after)
        if reason:
            # Refuse now what would fail on approval, such as a folder another company owns.
            self.store.configure(
                project, org["id"], after, org["revision"], "Rem", "Consent check", dry_run=True
            )
        return reason

    def _request_consent(
        self,
        org: dict[str, Any],
        actor: str,
        action: str,
        params: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        request = {
            "id": str(uuid4()),
            "action": action,
            "params": copy.deepcopy(params),
            "actor": actor,
            "at": now(),
            "summary": reason,
            "status": "pending",
        }

        def record(current: dict[str, Any]) -> dict[str, Any]:
            requests = current["runtime"].setdefault("consentRequests", {})
            expire_requests(requests)
            fingerprint = json.dumps([action, params], sort_keys=True, default=str)
            for existing in requests.values():
                # Asking again for the same thing returns the request already waiting.
                if existing["status"] == "pending" and existing.get("fingerprint") == fingerprint:
                    request.update(existing)
                    return {}
            request["fingerprint"] = fingerprint
            pending = [r for r in requests.values() if r["status"] == "pending"]
            if len(pending) >= 20:
                raise ValueError("Twenty requests already wait for the user's decision")
            # Keep the latest decided requests for context; drop older ones.
            decided = sorted(
                (r for r in requests.values() if r["status"] != "pending"), key=lambda r: r["at"]
            )
            for old in decided[:-20]:
                requests.pop(old["id"], None)
            requests[request["id"]] = request
            return {"requestId": request["id"], "action": action, "summary": reason}

        self.store.mutate(org["projectRoot"], org["id"], actor, "consent_requested", record)
        return {
            "needsUserConsent": True,
            "requestId": request["id"],
            "summary": reason,
            "message": (
                "Nothing changed yet. The user must approve this request in the "
                "Organizations tab before it takes effect."
            ),
        }

    def _decide(self, project: str, org: dict[str, Any], params: dict[str, Any]) -> Any:
        decision = params.get("decision")
        if decision not in {"approve", "decline"}:
            raise ValueError("Choose approve or decline")
        claimed: dict[str, Any] = {}

        def claim(current: dict[str, Any]) -> dict[str, Any]:
            # Claim the request in one transaction so a double click can't apply it twice.
            requests = current["runtime"].get("consentRequests") or {}
            expired = expire_requests(requests)
            item = requests.get(params.get("requestId"))
            if item and item["status"] == "pending":
                item["status"] = "applying" if decision == "approve" else "declined"
                item["decidedAt"] = now()
                claimed.update(copy.deepcopy(item))
            return {"expired": expired} if expired else {}

        self.store.mutate(project, org["id"], "user", "consent_checked", claim)
        if not claimed:
            raise ValueError(
                "This request is no longer waiting. It was decided already or is more than a "
                "day old; ask Rem again if you still want it."
            )
        request = claimed
        if decision == "decline":
            self.store.mutate(
                project,
                org["id"],
                "user",
                "consent_decided",
                lambda _: {"requestId": request["id"], "decision": decision, "error": ""},
            )
            return self.public(self.store.get(project, org["id"]))
        error = ""
        if decision == "approve":
            try:
                self.call(
                    project,
                    "user",
                    {
                        "action": request["action"],
                        "organizationId": org["id"],
                        "params": request["params"],
                    },
                )
            except ValueError as exc:
                error = str(exc)

        def settle(current: dict[str, Any]) -> dict[str, Any]:
            item = current["runtime"].get("consentRequests", {}).get(request["id"])
            if item:
                item.update(status="failed" if error else "approved", decidedAt=now(), error=error)
            return {"requestId": request["id"], "decision": decision, "error": error}

        self.store.mutate(project, org["id"], "user", "consent_decided", settle)
        if error:
            raise OrganizationConflict(
                f"Rem's request could not be applied: {error}. It has been closed; ask Rem again."
            )
        return self.public(self.store.get(project, org["id"]))

    @staticmethod
    def public(org: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(org)
        files = result["config"].pop("packageFiles", {})
        result["runtime"].pop("requests", None)
        result["diagnostics"] = diagnostics(org)
        result["version"] = hashlib.sha256(json.dumps(org, sort_keys=True).encode()).hexdigest()
        result["packageFileCount"] = len(files)
        # Ticket keys and the current state of each claim's evidence.
        annotate(result)
        result["packageFiles"] = {
            path: hashlib.sha256(content.encode()).hexdigest() for path, content in files.items()
        }
        return result

    def _redact(self, text: str) -> str:
        with self._lock:
            values = [value for secrets in self._secret_values.values() for value in secrets]
        for value in sorted(set(values), key=len, reverse=True):
            text = text.replace(value, "[secret]")
        return re.sub(r"http://127\.0\.0\.1:[0-9]+/[A-Za-z0-9_-]{32,}", "[turn capability]", text)

    def _clean(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact(value)
        if isinstance(value, dict):
            return {key: self._clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._clean(item) for item in value]
        return value

    def call(
        self,
        project: str,
        actor: str,
        arguments: dict[str, Any],
        *,
        employee_id: str | None = None,
        bound_org: str | None = None,
        generation: int | None = None,
        read_only: bool = False,
        workspace_paths: set[str] | None = None,
        run_id: str | None = None,
    ) -> Any:
        if not self.enabled:
            raise ValueError(
                "Organizations is experimental and disabled. "
                "Turn it on in Raticode Settings > Developer > Feature Preview."
            )
        credential = (
            (arguments.get("params") or {}).get("value")
            if arguments.get("action") == "secret_store"
            and isinstance(arguments.get("params"), dict)
            else None
        )
        arguments = self._clean(arguments)
        if set(arguments) - {"action", "organizationId", "params"}:
            raise ValueError("Use action, organizationId and params")
        action = arguments.get("action")
        params = arguments.get("params", {})
        oid = arguments.get("organizationId", bound_org)
        if not isinstance(action, str) or not isinstance(params, dict):
            raise ValueError("Expected action and params object")
        required = {
            "create": {"config"},
            "configure": {"config", "expectedRevision", "reason"},
            "restore": {"revision", "expectedRevision", "reason"},
            "control": {"state"},
            "task_update": {"taskId", "expectedRevision", "changes"},
            "approve": {"taskId", "expectedRevision"},
            "comment": {"taskId", "body"},
            "wake": {"employeeId", "message"},
        }
        # Check privilege before inspecting configuration payloads from employees.
        if employee_id and action in CONFIG_ACTIONS:
            raise ValueError("Employees cannot configure organizations; ask Rem or the user")
        if missing := required.get(action, set()) - params.keys():
            raise ValueError("Missing parameters: " + ", ".join(sorted(missing)))
        if read_only and action not in READ_ACTIONS:
            raise ValueError("Organization changes are unavailable in read-only/plan mode")
        if employee_id:
            if self._closed.is_set():
                raise OrganizationConflict("This employee turn has been revoked")
            if action in CONFIG_ACTIONS or action in {"import_preview", "export", "history"}:
                raise ValueError("Employees cannot configure organizations; ask Rem or the user")
            if oid != bound_org:
                raise ValueError("Employee access is limited to its own organization")
            current = self.store.get(project, str(oid))
            if (
                current["runtime"]["generation"] != generation
                or current["runtime"]["state"] != "running"
                or (
                    run_id is not None
                    and not any(t["claim"] == run_id for t in current["runtime"]["tasks"])
                )
            ):
                raise OrganizationConflict("This employee turn has been revoked")
        if action == "help":
            return {
                **management_help(),
                "instructions": HELP,
                "employee": employee_id,
                "readOnly": read_only,
                "workspacePaths": sorted(workspace_paths or []),
                "configurationSchema": OrganizationConfig.model_json_schema()
                if not employee_id
                else None,
            }
        if action == "list":
            return [
                item for item in self.store.summaries() if not bound_org or item["id"] == bound_org
            ]
        if action == "providers":
            from gofer.core.provider_capabilities import provider_capabilities_payload

            refresh = params.get("refresh", False)
            if not isinstance(refresh, bool):
                raise ValueError("refresh must be a boolean")
            return provider_capabilities_payload(refresh=refresh)
        if action in {"import_github", "import_github_preview"}:
            if employee_id:
                raise ValueError("Employees cannot import organization packages")
            files = github_package(
                params.get("repository", ""), params.get("commit", ""), params.get("directory", "")
            )
            preview = parse_package(files)
            preview["provenance"] = {
                "repository": params["repository"],
                "commit": params["commit"],
                "directory": params.get("directory", ""),
                "files": {
                    path: hashlib.sha256(base64.b64decode(content)).hexdigest()
                    for path, content in files.items()
                },
            }
            if action == "import_github_preview":
                return preview
            preview["config"]["metadata"]["raticodeImport"] = preview["provenance"]
            return self.public(
                self.store.create(
                    project,
                    preview["config"],
                    actor,
                    "Imported pinned GitHub company template",
                    preview["tasks"],
                )
            )
        if action in {"import", "import_preview"}:
            sources = [key for key in ("files", "zip", "path") if key in params]
            if len(sources) != 1:
                raise ValueError("Supply exactly one package source: files, zip or path")
            package_files = params.get("files")
            if "zip" in params:
                if not isinstance(params["zip"], str):
                    raise ValueError("Package ZIP must be base64 text")
                package_files = read_zip(params["zip"])
            elif "path" in params:
                value = params["path"]
                if not isinstance(value, str) or not Path(value).is_absolute():
                    raise ValueError("Package path must be an absolute path")
                path = Path(value).resolve()
                if workspace_paths is None or not any(
                    path.is_relative_to(Path(root).resolve()) for root in workspace_paths
                ):
                    raise ValueError("Choose a package inside a project open in this Rem session")
                if path.is_dir():
                    package_files = read_directory(path)
                else:
                    from gofer.ui.organization_packages import MAX_PACKAGE_BYTES

                    try:
                        with open_binary_input(path) as package_file:
                            content = package_file.read(MAX_PACKAGE_BYTES + 1)
                    except OSError as exc:
                        raise ValueError("Package ZIP changed or cannot be read safely") from exc
                    if len(content) > MAX_PACKAGE_BYTES:
                        raise ValueError("Package exceeds 16 MiB")
                    package_files = read_zip(base64.b64encode(content).decode())
            if not isinstance(package_files, dict):
                raise ValueError("Package files must be a map of relative paths to base64 content")
            package = parse_package(package_files)
            if action == "import_preview":
                return package
            return self.public(
                self.store.create(
                    project,
                    package["config"],
                    actor,
                    params.get("reason", "Imported Agent Companies package"),
                    package["tasks"],
                )
            )
        if action in {"create", "configure"} and workspace_paths is not None:
            existing = self.store.get(project, str(oid))["config"] if action == "configure" else {}
            previous = set(existing.get("projectRoots", [])) | {
                p["workspacePath"] for p in existing.get("projects", []) if p.get("workspacePath")
            }
            paths = list(params["config"].get("projectRoots", [])) + [
                p["workspacePath"]
                for p in params["config"].get("projects", [])
                if p.get("workspacePath")
            ]
            for path in paths:
                if path not in previous and str(Path(path).resolve()) not in workspace_paths:
                    raise ValueError(
                        "Choose a workspace from the projects open in this Rem session"
                    )
        if action == "create":
            if project and (not Path(project).is_absolute() or not Path(project).is_dir()):
                raise ValueError("Organization project must be an existing absolute directory")
            return self.public(
                self.store.create(
                    project, params["config"], actor, params.get("reason", "Created organization")
                )
            )
        if not isinstance(oid, str):
            raise ValueError("organizationId is required")
        org = self.store.get(project, oid)
        if action == "request_decide":
            if actor != "user":
                raise ValueError("Only the user can approve or decline Rem's requests")
            return self._decide(project, org, params)
        if action == "secret_store" and actor != "user":
            raise ValueError(
                "Enter credential values in the Organizations tab. Rem can only reference "
                "credentials the user stored."
            )
        if (
            actor != "user"
            and not employee_id
            and action in CONSENT_ACTIONS
            and (reason := self._consent_reason(project, org, action, params))
        ):
            return self._request_consent(org, actor, action, params, reason)
        if action == "archive":
            archived = params.get("archived", True)
            if not isinstance(archived, bool):
                raise ValueError("archived must be true or false")
            with self._lock:
                result = self.store.archive(oid, actor, archived)
            return self.public(result)
        if action == "audit_verify":
            report = self.store.verify_audit(oid)
            if report["ok"]:
                # A verified head is worth remembering outside the database.
                self.store.anchor(oid)
            return report
        if action == "run_input":
            turn = params.get("turnId")
            attempt = org["runtime"].get("attempts", {}).get(turn)
            if (
                not isinstance(turn, str)
                or not attempt
                or attempt["taskId"] != params.get("taskId")
            ):
                raise ValueError("Choose a run of this task")
            try:
                with gzip.open(self.run_input_path(oid, turn), "rb") as handle:
                    stored = handle.read(MAX_RUN_INPUT_BYTES + 1)
            except OSError as exc:
                raise ValueError("This run's input wasn't kept; it predates input records") from exc
            return {
                "turnId": turn,
                "input": json.loads(stored),
                "sha256": hashlib.sha256(stored).hexdigest(),
                "matchesRecord": hashlib.sha256(stored).hexdigest() == attempt.get("inputSha256"),
            }
        if action == "run_events":
            turn = params.get("turnId")
            attempt = org["runtime"].get("attempts", {}).get(turn)
            if (
                not isinstance(turn, str)
                or not attempt
                or attempt["taskId"] != params.get("taskId")
            ):
                raise ValueError("Choose a run of this task")
            return {"turnId": turn, "events": self.store.turn_events(oid, turn)}
        if action == "execution_reconcile":
            if employee_id:
                raise ValueError("Only the user or Rem may reconcile stopped executions")
            return self.executions.reconcile(org)
        if action == "execution_resolve":
            if (
                employee_id
                or params.get("confirmStopped") is not True
                or not str(params.get("reason", "")).strip()
            ):
                raise ValueError(
                    "The user or Rem must confirm stopped work and supply inspection evidence"
                )

            def resolve(current: dict[str, Any]) -> Any:
                attempt = current["runtime"].get("attempts", {}).get(params.get("attemptId"))
                if (
                    not attempt
                    or not attempt.get("executionHandle")
                    or attempt["id"] in self._active
                ):
                    raise ValueError("Select a stopped execution record")
                attempt["executionHandle"].update(
                    settled=True, resolvedBy=actor, resolution=params["reason"], resolvedAt=now()
                )
                return {"attemptId": attempt["id"], "reason": params["reason"]}

            return self.public(
                self.store.mutate(project, oid, actor, "execution_resolved", resolve)
            )
        if action == "webhook_save":
            from gofer.ui.organization_webhooks import configure

            if employee_id:
                raise ValueError("Only the user or Rem may configure webhooks")
            return configure(self, org, params, actor)
        if action == "backup":
            import sqlite3
            import tempfile

            if employee_id:
                raise ValueError("Only the user or Rem may export an operational backup")
            with tempfile.TemporaryDirectory(prefix="raticode-org-backup-") as directory:
                path = Path(directory) / "organizations.sqlite3"
                with self.store.connect() as source, sqlite3.connect(path) as destination:
                    source.backup(destination)
                    destination.execute("PRAGMA journal_mode=DELETE")
                content = path.read_bytes()
            return {
                "filename": "organizations.sqlite3",
                "content": base64.b64encode(content).decode(),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        if action == "secret_store":
            from gofer.devices.storage import OSSecretStore

            if employee_id:
                raise ValueError("Only the user or Rem may store credentials")
            sid, value = params.get("id"), credential
            if (
                not isinstance(sid, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", sid)
                or not isinstance(value, str)
                or not 1 <= len(value) <= 16384
            ):
                raise ValueError("Supply a reference ID and a credential up to 16384 characters")
            account = f"{oid}/{sid}/{uuid4()}"
            OSSecretStore(service="Raticode organization secrets").put(account, value)
            return operation(
                self, org, "secret_reference", {"id": sid, "account": account}, actor, None
            )
        if action == "workflow_launch":
            if employee_id:
                raise ValueError("Only the user or Rem may authorize a workflow launch")
            return authorize_workflow(self, org, params, actor)
        if (
            action == "secret_reference"
            and actor != "user"
            and not str(params.get("account", "")).startswith(f"{oid}/")
        ):
            raise ValueError("Rem can only reference credentials stored for this organization")
        if action in READ_OPERATIONS | WRITE_OPERATIONS:
            return operation(self, org, action, params, actor, employee_id, run_id)
        if action in KNOWLEDGE_ACTIONS:
            return knowledge_action(self, project, org, action, params, actor, employee_id, run_id)
        if action == "read":
            result = self.public(org)
            if params.get("sinceVersion") == result["version"]:
                return {"notModified": True, "version": result["version"]}
            if employee_id:
                result["runtime"]["employeeMemory"] = {
                    employee_id: result["runtime"].get("employeeMemory", {}).get(employee_id, "")
                }
                result["runtime"]["memories"] = {
                    key: value
                    for key, value in result["runtime"].get("memories", {}).items()
                    if value["employeeId"] == employee_id
                }
            result["runtime"].pop("requests", None)
            return result
        if action == "history":
            history = self.store.history(project, oid)
            for entry in history:
                files = entry["config"].pop("packageFiles", {})
                entry["packageFiles"] = {
                    path: hashlib.sha256(content.encode()).hexdigest()
                    for path, content in files.items()
                }
            for index, entry in enumerate(history):
                before = history[index + 1]["config"] if index + 1 < len(history) else {}
                entry["changes"] = config_diff(before, entry["config"])
                previous_files = (
                    history[index + 1]["packageFiles"] if index + 1 < len(history) else {}
                )
                entry["changes"] += config_diff(
                    previous_files, entry["packageFiles"], "/packageFiles"
                )
            offset, limit = int(params.get("offset", 0)), int(params.get("limit", 50))
            if offset < 0 or not 1 <= limit <= 100:
                raise ValueError("Use offset >= 0 and limit between 1 and 100")
            return history[offset : offset + limit]
        if action == "events":
            return self.store.events(
                project, oid, int(params.get("after", 0)), int(params.get("recent", 0))
            )
        if action == "export":
            files = export_package(org["config"], org["runtime"]["tasks"])
            return {"files": files, "zip": zip_package(files)}
        if action in {"configure", "restore"}:
            with self._lock:
                if action == "configure":
                    result = self.store.configure(
                        project,
                        oid,
                        params["config"],
                        params.get("expectedRevision", -1),
                        actor,
                        params.get("reason", ""),
                    )
                else:
                    result = self.store.restore(
                        project,
                        oid,
                        params["revision"],
                        params.get("expectedRevision", -1),
                        actor,
                        params.get("reason", ""),
                    )
                self.ensure_repository(result)
                valid_claims = {t["claim"] for t in result["runtime"]["tasks"] if t["claim"]}
                for claim_id, (active_oid, cancel, _) in self._active.items():
                    if active_oid == oid and claim_id not in valid_claims:
                        cancel.set()
                return self.public(result)
        if action == "control":
            state = params.get("state")
            if state not in {"running", "paused"}:
                raise ValueError("State must be running or paused")

            who = "you" if actor == "user" else "Rem" if actor.startswith("Rem") else actor
            with self._lock:
                result = self.store.control(project, oid, actor, state, reason=f"Paused by {who}")
                if state == "paused":
                    self.cancel(oid, "paused")
            self._wake.set()
            return self.public(result)
        if action == "memory":
            if not employee_id:
                raise ValueError(
                    "Working memory is written by the employee; edit persona memory in settings"
                )
            body = params.get("body")
            if not isinstance(body, str) or len(body) > 100_000:
                raise ValueError("Memory must be text up to 100000 characters")

            def remember(current: dict[str, Any]) -> dict[str, Any]:
                if (
                    current["runtime"]["generation"] != generation
                    or current["runtime"]["state"] != "running"
                    or (
                        run_id is not None
                        and not any(t["claim"] == run_id for t in current["runtime"]["tasks"])
                    )
                ):
                    raise OrganizationConflict("This employee turn has been revoked")
                current["runtime"].setdefault("employeeMemory", {})[employee_id] = body
                return {"employeeId": employee_id, "body": body}

            self.store.mutate(project, oid, actor, "memory_updated", remember)
            return {"saved": True}
        request_action = action
        if action == "wake":
            params = {
                "title": params.get("message", "Employee heartbeat"),
                "assignee": params["employeeId"],
                **(
                    {"idempotencyKey": params["idempotencyKey"]}
                    if "idempotencyKey" in params
                    else {}
                ),
            }
            action = "task_create"
        if action not in {"task_create", "task_update", "comment", "approve", "review", "reopen"}:
            raise ValueError("Unknown organization action; call help")
        for root in org["config"].get("projectRoots", []):
            project_identity(root)
        proposed_root = params.get("workspacePath") or (params.get("changes") or {}).get(
            "workspacePath"
        )
        if isinstance(proposed_root, str):
            project_identity(proposed_root)
        result_task: dict[str, Any] = {}
        revoked: list[str] = []
        request_key = params.get("idempotencyKey")
        if request_key is not None and (
            not isinstance(request_key, str) or not 1 <= len(request_key) <= 200
        ):
            raise ValueError("Idempotency key must be between 1 and 200 characters")
        request_hash = hashlib.sha256(
            json.dumps([request_action, params], sort_keys=True).encode()
        ).hexdigest()

        def update(current: dict[str, Any]) -> dict[str, Any]:
            if employee_id and (
                current["runtime"]["generation"] != generation
                or current["runtime"]["state"] != "running"
                or (
                    run_id is not None
                    and not any(t["claim"] == run_id for t in current["runtime"]["tasks"])
                )
            ):
                raise OrganizationConflict("This employee turn has been revoked")
            if run_id is not None and not any(
                t["claim"] == run_id for t in current["runtime"]["tasks"]
            ):
                raise OrganizationConflict("This employee turn has been revoked")
            requests = current["runtime"].setdefault("requests", {})
            scoped_key = f"{actor}:{request_action}:{request_key}"
            if request_key and scoped_key in requests:
                saved = requests[scoped_key]
                if saved["hash"] != request_hash:
                    raise OrganizationConflict(
                        "Idempotency key was already used with different parameters"
                    )
                result_task.update(saved["result"])
                return {}
            tasks = current["runtime"]["tasks"]
            if action == "task_create":
                allowed = {
                    k: v
                    for k, v in params.items()
                    if k
                    not in {
                        "id",
                        "revision",
                        "comments",
                        "turns",
                        "claim",
                        "approvedRevision",
                        "acceptedRevision",
                        "planRevision",
                        "cancellation",
                        "reviewRequestedAt",
                        "acceptedAt",
                        "idempotencyKey",
                        "workflowAuthorization",
                    }
                }
                task = Task.model_validate(allowed).model_dump()
                check_recurring(None, task, employee_id)
                if employee_id:
                    task["parentRunId"] = run_id
                    if task["status"] not in {"backlog", "todo"}:
                        raise ValueError("Employees must create pending work")
                    parent = next(
                        (
                            t
                            for t in tasks
                            if t["id"] == task.get("parentId") or (run_id and t["claim"] == run_id)
                        ),
                        None,
                    )
                    if parent:
                        if run_id and not task.get("parentId"):
                            task["parentId"] = parent["id"]
                        task["approvalRequired"] = (
                            task["approvalRequired"] or parent["approvalRequired"]
                        )
                        task["reviewRequired"] = task["reviewRequired"] or parent.get(
                            "reviewRequired", False
                        )
                    self._limit_delegation(current, task, run_id)
                self._validate_workspace(current, task, employee_id)
                validate_execution(current, task)
                self.store.validate_task(task, current["config"], tasks)
                if employee_id:
                    self._ticket_access(current, employee_id, task.get("project"), "create")
                tasks.append(task)
                number_tickets(tasks)
            else:
                found = next((t for t in tasks if t["id"] == params.get("taskId")), None)
                if found is None:
                    raise ValueError("Task not found")
                task = found
                if action == "comment":
                    body = params.get("body")
                    if not isinstance(body, str) or not body.strip() or len(body) > 100_000:
                        raise ValueError("Supply a comment between 1 and 100000 characters")
                    task["comments"].append({"actor": actor, "at": now(), "body": body})
                    if params.get("unblock"):
                        if employee_id and not self._owns(current, employee_id, task):
                            raise ValueError("Only an owner or manager may unblock work")
                        if employee_id:
                            self._ticket_access(current, employee_id, task.get("project"), "move")
                        if employee_id and not employee_may_unblock(task):
                            raise ValueError(
                                "Raticode, the user or Rem blocked this task for review; "
                                "ask them to reopen it"
                            )
                        if task["status"] != "blocked" or task["claim"]:
                            raise ValueError("Only stopped blocked work can be unblocked")
                        task.update(status="todo", blockReason="")
                    task["revision"] += 1
                else:
                    if task["revision"] != params.get("expectedRevision", -1):
                        raise OrganizationConflict("Task changed. Read it before updating.")
                    if action in {"approve", "review"} and employee_id:
                        if employee_id == task["assignee"] or employee_id != task.get("reviewer"):
                            raise ValueError(
                                "Only the designated independent reviewer or the user can approve"
                            )
                    elif (
                        employee_id
                        and not self._owns(current, employee_id, task)
                        and not takes_unassigned(task, params, employee_id)
                    ):
                        raise ValueError("Only the assignee or their manager can change this task")
                    if action in {"approve", "review"} and task["status"] == "cancelled":
                        raise ValueError("Cancelled work must be reopened first")
                    if action == "approve":
                        task["approvedRevision"] = task.get("planRevision", 1)
                    elif action == "review":
                        if task["claim"] or task["status"] != "in_review":
                            raise ValueError("Wait for the run to finish before reviewing")
                        decision = params.get("decision")
                        if decision not in {"accept", "revise", "reject"}:
                            raise ValueError("Choose accept, revise or reject")
                        if not str(params.get("body", "")).strip():
                            raise ValueError("Review needs a reason")
                        task["comments"].append(
                            {
                                "actor": actor,
                                "at": now(),
                                "body": params["body"],
                                "decision": decision,
                            }
                        )
                        task["acceptedAt"] = now() if decision == "accept" else None
                        if decision != "accept":
                            supersede_claims(current, task["id"], actor)
                        elif not task["evidence"].strip():
                            # The accepted claims and their checked states become the record.
                            task["evidence"] = acceptance_evidence(current, task, params["body"])
                        task.update(
                            acceptedRevision=task["revision"] if decision == "accept" else None,
                            status={"accept": "done", "revise": "todo", "reject": "blocked"}[
                                decision
                            ],
                        )
                        self.store.validate_task(task, current["config"], tasks)
                    elif action == "reopen":
                        if task["claim"] or task["status"] not in {"done", "cancelled", "blocked"}:
                            raise ValueError("Only stopped work can be reopened")
                        if employee_id:
                            self._ticket_access(current, employee_id, task.get("project"), "move")
                        if employee_id and (
                            task["status"] == "cancelled"
                            or (task["status"] == "blocked" and not employee_may_unblock(task))
                        ):
                            raise ValueError(
                                "Only the user or Rem can reopen work that was cancelled or "
                                "blocked for review"
                            )
                        task.update(
                            status="todo", cancellation=None, blockReason="", acceptedRevision=None
                        )
                        # Only the user's reopen grants a fresh run allowance.
                        if actor == "user" and task["turns"] >= current["config"]["maxTaskTurns"]:
                            task["turns"] = 0
                    else:
                        changes = params.get("changes", {})
                        if not isinstance(changes, dict) or set(changes) - TASK_EDIT_FIELDS:
                            raise ValueError("Unsupported task fields")
                        changes = {
                            key: value for key, value in changes.items() if task.get(key) != value
                        }
                        if (
                            employee_id
                            and task["status"] == "blocked"
                            and changes.get("status") not in {None, "blocked", "cancelled"}
                            and not employee_may_unblock(task)
                        ):
                            raise ValueError(
                                "Raticode, the user or Rem blocked this task for review; "
                                "ask them to reopen it"
                            )
                        if task["claim"] and set(changes) - {"status", "evidence"}:
                            raise OrganizationConflict(
                                "Pause and wait for the turn before editing its plan"
                            )
                        check_recurring(task, {**task, **changes}, employee_id)
                        if employee_id and set(changes) & {
                            "approvalRequired",
                            "reviewRequired",
                            "reviewer",
                        }:
                            raise ValueError("Employees cannot change approval or review policy")
                        if (
                            task["status"] == "cancelled"
                            and changes.get("status", "cancelled") != "cancelled"
                        ):
                            raise ValueError("Use reopen to retry cancelled work")
                        # Raticode starts work itself, so moving a ticket to In progress
                        # means "work on this next": it waits at the front of the queue.
                        pulled = changes.get("status") == "in_progress" and not task["claim"]
                        if pulled:
                            changes = {**changes, "status": "todo"}
                        candidate = Task.model_validate({**task, **changes}).model_dump()
                        if pulled:
                            candidate["pulledAt"] = now()
                            if candidate.get("dueAt") and datetime.fromisoformat(
                                candidate["dueAt"]
                            ) > datetime.now(UTC):
                                candidate["dueAt"] = None
                        if set(changes) - {"status", "evidence", "artifacts", "blockReason"}:
                            candidate["approvedRevision"] = None
                            candidate["workflowAuthorization"] = None
                            candidate["planRevision"] += 1
                        if set(changes) - {"status", "blockReason"}:
                            candidate["acceptedRevision"] = None
                        if changes.get("status") == "in_review":
                            candidate["reviewRequestedAt"] = now()
                        if changes.get("status") == "blocked" and not candidate["blockReason"]:
                            who = (
                                "you"
                                if actor == "user"
                                else "Rem"
                                if actor.startswith("Rem")
                                else actor_name(current, actor)
                            )
                            candidate["blockReason"] = f"Blocked by {who}; see the conversation"
                        elif changes.get("status") not in {None, "blocked"} and (
                            "blockReason" not in changes
                        ):
                            candidate["blockReason"] = ""
                        if changes.get("status") == "cancelled" and task["claim"]:
                            revoked.append(task["claim"])
                            candidate.update(claim=None, cancellation="requested")
                        self._validate_workspace(current, candidate, employee_id)
                        validate_execution(current, candidate)
                        self.store.validate_task(candidate, current["config"], tasks)
                        if employee_id:
                            needed = set()
                            if "status" in changes:
                                needed.add("cancel" if changes["status"] == "cancelled" else "move")
                            # Evidence and artifacts are a run's own output, not plan edits.
                            if set(changes) - {"status", "evidence", "artifacts", "blockReason"}:
                                needed.add("edit")
                            for where in {task.get("project"), candidate.get("project")}:
                                for permission in sorted(needed):
                                    self._ticket_access(current, employee_id, where, permission)
                        task.update(candidate)
                        if pulled:
                            who = (
                                "You"
                                if actor == "user"
                                else "Rem"
                                if actor.startswith("Rem")
                                else actor_name(current, actor)
                            )
                            task["comments"].append(
                                {
                                    "actor": "system",
                                    "at": now(),
                                    "body": f"{who} moved this to In progress. It starts as soon "
                                    "as its worker is free and working hours allow.",
                                }
                            )
                    task["revision"] += 1
                    if (
                        task["status"] == "done"
                        and task["recurring"]
                        and task["intervalSeconds"]
                        and not task["claim"]
                    ):
                        self._record_occurrence(current, task)
                        task["comments"].append(
                            {
                                "actor": actor,
                                "at": now(),
                                "body": "Completed occurrence: " + task["evidence"],
                            }
                        )
                        task.update(
                            status="todo",
                            turns=0,
                            dueAt=(
                                datetime.now(UTC) + timedelta(seconds=task["intervalSeconds"])
                            ).isoformat(),
                        )
            if task["status"] == "cancelled":
                parents = {task["id"]}
                while True:
                    parent_runs = {
                        a["id"]
                        for a in current["runtime"].get("attempts", {}).values()
                        if a["taskId"] in parents
                    }
                    children = {
                        t["id"]
                        for t in tasks
                        if t.get("parentId") in parents or t.get("parentRunId") in parent_runs
                    } - parents
                    if not children:
                        break
                    parents |= children
                for child in tasks:
                    if child["id"] in parents - {task["id"]} and child["status"] not in {
                        "done",
                        "cancelled",
                    }:
                        if child["claim"]:
                            revoked.append(child["claim"])
                        child.update(
                            status="cancelled",
                            cancellation="requested" if child["claim"] else "acknowledged",
                            claim=None,
                            revision=child["revision"] + 1,
                        )
            self._unblock(current)
            result_task.update(copy.deepcopy(task))
            result_task["key"] = ticket_key(current["config"], task)
            if request_key and action == "comment":
                result_task.clear()
                result_task.update(id=task["id"], revision=task["revision"], status=task["status"])
            if request_key:
                # Retries arrive within minutes; a month is ample and keeps the table bounded.
                cutoff = (datetime.now(UTC) - REQUEST_RETENTION).isoformat()
                for stale in [k for k, v in requests.items() if v.get("at", "") < cutoff]:
                    requests.pop(stale)
                requests[scoped_key] = {
                    "hash": request_hash,
                    "result": copy.deepcopy(result_task),
                    "at": now(),
                }
            return {
                "taskId": task["id"],
                "revision": task["revision"],
                "status": task["status"],
                **({"comment": task["comments"][-1]} if action == "comment" else {}),
                **({"changes": params["changes"]} if action == "task_update" else {}),
                **(
                    {"decision": params["decision"], "body": params["body"]}
                    if action == "review"
                    else {}
                ),
            }

        with self._lock:
            self.store.mutate(project, oid, actor, action, update)
            for claim_id in revoked:
                if claim_id in self._active:
                    self._active[claim_id][1].set()
        if action in {"review", "task_update"} and result_task.get("sharedWorktree"):
            # Accepting merges the ticket's shared changes right away.
            self.release_shared(self.store.get(project, oid))
            result_task.update(
                next(
                    t
                    for t in self.store.get(project, oid)["runtime"]["tasks"]
                    if t["id"] == result_task["id"]
                ),
                key=result_task.get("key"),
            )
        self._wake.set()
        return result_task

    @staticmethod
    def _ticket_access(
        org: dict[str, Any], employee_id: str, project: str | None, action: str
    ) -> None:
        """Refuse a worker's ticket action that its per-project permissions don't allow."""
        employee = next((e for e in org["config"]["employees"] if e["id"] == employee_id), None)
        if employee is not None and ticket_allowed(employee, project, action):
            return
        name = (employee or {}).get("name") or employee_id
        where = next(
            (p.get("name") or p["id"] for p in org["config"]["projects"] if p["id"] == project),
            None,
        )
        verb = {"move": "change the status of", "cancel": "cancel"}.get(action, action)
        raise ValueError(
            f"{name} can't {verb} tickets "
            + (f"in {where}" if where else "outside a project")
            + ". The user can change this in the worker's ticket permissions."
        )

    @staticmethod
    def _record_occurrence(org: dict[str, Any], task: dict[str, Any]) -> None:
        key = task["claim"] or f"{task['id']}:{task['revision']}"
        org["runtime"].setdefault("occurrences", {})[key] = {
            "id": key,
            "taskId": task["id"],
            "at": now(),
            "evidence": task["evidence"],
            "artifacts": copy.deepcopy(task.get("artifacts", [])),
            "acceptedRevision": task.get("acceptedRevision"),
            "status": "done",
        }

    def _validate_workspace(
        self, org: dict[str, Any], task: dict[str, Any], actor: str | None = None
    ) -> None:
        root = task.get("workspacePath")
        if root:
            if not Path(root).is_absolute() or not self.store.owns_project(org["id"], root):
                raise ValueError("Task workspace must be owned by this organization")
            task["workspacePath"] = str(Path(root).resolve())
            for eid in {actor, task.get("assignee")} - {None}:
                employee = next(e for e in org["config"]["employees"] if e["id"] == eid)
                grants = employee.get("workspacePaths")
                if grants is not None and root not in grants:
                    raise ValueError("Task workspace is outside employee grants")

    @staticmethod
    def _limit_delegation(org: dict[str, Any], task: dict[str, Any], run_id: str | None) -> None:
        """Bound how much work one employee run can create, and how deep delegation goes."""
        config, tasks = org["config"], org["runtime"]["tasks"]
        limit = config.get("maxChildTasksPerTurn", 5)
        if run_id and sum(t.get("parentRunId") == run_id for t in tasks) >= limit:
            raise ValueError(
                f"This run already created {limit} tasks, the organization's limit per run. "
                "Record the remaining ideas in a comment for the owner to schedule."
            )
        parents = {t["id"]: t.get("parentId") for t in tasks}
        depth, parent = 0, task.get("parentId")
        while parent and depth <= 100:
            depth += 1
            parent = parents.get(parent)
        if depth > config.get("maxDelegationDepth", 3):
            raise ValueError(
                "Delegation is nested too deeply for this organization. Finish or report "
                "the work instead of delegating it further."
            )

    @staticmethod
    def _unblock(org: dict[str, Any]) -> None:
        tasks = org["runtime"]["tasks"]
        done = {t["id"] for t in tasks if t["status"] == "done" and not t["claim"]}
        for task in tasks:
            if task.get("dependsOn") and task["status"] in {"todo", "blocked"}:
                missing = set(task["dependsOn"]) - done
                if missing and task["status"] == "todo":
                    task.update(
                        status="blocked", blockReason="dependencies", revision=task["revision"] + 1
                    )
                elif not missing and task.get("blockReason") == "dependencies":
                    task.update(status="todo", blockReason="", revision=task["revision"] + 1)

    @staticmethod
    def _owns(org: dict[str, Any], employee: str, task: dict[str, Any]) -> bool:
        reports = {e["id"]: e["reportsTo"] for e in org["config"]["employees"]}
        owner = task["assignee"]
        while owner:
            if owner == employee:
                return True
            owner = reports.get(owner)
        return False

    @contextmanager
    def session(self, project: str, actor: str, **scope: Any) -> Iterator[str]:
        with self._lock:
            if self._closed.is_set():
                raise ValueError("Organization runtime is closed")
            if self._http is None:
                self._http = SwarmToolServer(max_request_bytes=24 * 1024 * 1024)
            http = self._http
            tool = copy.deepcopy(TOOL)
            if scope.get("read_only"):
                tool["annotations"] = {"readOnlyHint": True}
                tool["inputSchema"]["properties"]["action"]["enum"] = sorted(READ_ACTIONS)
            url = http.register(
                lambda name, args: self.call(project, actor, args, **scope),
                [tool],
                HELP,
                label="Organization",
                failure_hint=TOOL_FAILURE_HINT,
            )
        try:
            yield url
        finally:
            http.revoke(url)

    async def rem_stream(
        self,
        source: Callable[..., AsyncGenerator[dict[str, Any], None]],
        actor: str = "Rem",
        management_read_only: bool | None = None,
        **options: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        workflow = copy.deepcopy(options.get("workflow") or {})
        project = workflow.get("projectRoot")
        global_scope = (workflow.get("remThreads") or {}).get("global") is True
        if not self.enabled or (not project and not global_scope):
            async with aclosing(source(**options)) as stream:
                async for event in stream:
                    yield event
            return
        with self.session(
            str(Path(project).resolve()) if project else "",
            actor,
            read_only=management_read_only
            if global_scope and management_read_only is not None
            else options.get("permission_mode") in {"read-only", "plan"}
            or (workflow.get("remManagedTask") or {}).get("readOnly") is True,
            workspace_paths={
                *([str(Path(project).resolve())] if project else []),
                *(
                    str(Path(item["root"]).resolve())
                    for item in (workflow.get("remThreads") or {}).get("projects", [])
                ),
            },
        ) as url:
            resources = workflow.setdefault("remResources", {})
            resources["mcpServers"] = [
                s for s in resources.get("mcpServers", []) if s["name"] != "organizations"
            ] + [{"name": "organizations", "type": "http", "url": url}]
            workflow["remOrganizationInstructions"] = (
                "Use the organizations MCP organization_action tool to manage the Organizations "
                "tab. Call action=help for all actions, settings schemas and granted workspace "
                "paths. Organizations are global; no project selection is needed for management. "
                "Read before editing and retain unrelated settings. Use these tools, never edit "
                "organization storage directly."
            )
            async with aclosing(
                source(**{**options, "workflow": workflow, "trusted_organization_url": url})
            ) as stream:
                async for event in stream:
                    yield event

    def _loop(self) -> None:
        next_reconcile = 0.0
        while not self._closed.is_set():
            try:
                self.dispatch()
                if time.monotonic() >= next_reconcile:
                    with self.store.connect() as db:
                        rows = db.execute("SELECT project, id FROM organizations").fetchall()
                    for row in rows:
                        if self._closed.is_set():
                            break
                        try:
                            current = self.store.get(row["project"], row["id"])
                            self.executions.reconcile(current)
                            self.release_worktrees(current)
                            self.release_shared(current)
                            self.store.anchor(row["id"])
                        except Exception:
                            log.exception("Organization upkeep failed for %s", row["id"])
                    next_reconcile = time.monotonic() + 30
            except Exception:
                log.exception("Organization dispatch failed")
            self._wake.wait(2)
            self._wake.clear()

    def _task_worktree(
        self, project: str, oid: str, task: dict[str, Any], root: str, turn_id: str
    ) -> dict[str, str]:
        """Create or reuse the task's own worktree and record it on the task."""
        worktree = task.get("worktree") or plan_worktree(
            self.data_dir,
            {"id": task["id"], "recipe": {"project_dir": root, "title": task["title"]}},
            folder="organization-worktrees",
            branch_prefix="org",
        )
        if worktree is None:
            raise ValueError("This project is not a Git repository, so it can't be isolated")

        # Record the worktree before creating it, so a revoked run can't leave one untracked.
        def record(current: dict[str, Any]) -> dict[str, Any]:
            existing = next(t for t in current["runtime"]["tasks"] if t["id"] == task["id"])
            if existing["claim"] != turn_id:
                raise OrganizationConflict("This employee turn has been revoked")
            if existing.get("worktree") == worktree:
                return {}
            existing["worktree"] = worktree
            return {"taskId": task["id"], "branch": worktree["branch"]}

        self.store.mutate(project, oid, "system", "worktree_created", record)
        try:
            ensure_worktree(worktree)
        except WorktreeError as exc:
            raise ValueError(f"Raticode couldn't create the task's worktree: {exc}") from exc
        return worktree

    def _shared_worktree(
        self, project: str, oid: str, task: dict[str, Any], turn_id: str
    ) -> dict[str, str] | None:
        """Give the run its own worktree of the shared repository, when there is one."""
        org = self.store.get(project, oid)
        path = repository.path_for(self.data_dir, oid)
        if not org["config"].get("sharedRepository") or not repository.exists(path):
            return None
        existing = next(t for t in org["runtime"]["tasks"] if t["id"] == task["id"])
        worktree = existing.get("sharedWorktree") or repository.plan_worktree(
            self.data_dir, org, task
        )

        def record(current: dict[str, Any]) -> dict[str, Any]:
            found = next(t for t in current["runtime"]["tasks"] if t["id"] == task["id"])
            if found["claim"] != turn_id:
                raise OrganizationConflict("This employee turn has been revoked")
            if found.get("sharedWorktree") == worktree:
                return {}
            found["sharedWorktree"] = worktree
            return {"taskId": task["id"], "branch": worktree["branch"], "shared": True}

        self.store.mutate(project, oid, "system", "worktree_created", record)
        try:
            repository.ensure_worktree(worktree, oid)
        except repository.RepositoryError as exc:
            raise ValueError(f"Raticode couldn't check out the shared repository: {exc}") from exc
        return worktree

    def release_shared(self, org: dict[str, Any]) -> None:
        """Merge accepted tickets' shared changes; keep others' on their branch."""
        with self._lock:
            in_use = set(self._active_roots.values())
        for task in org["runtime"]["tasks"]:
            worktree = task.get("sharedWorktree")
            if (
                not worktree
                or task["claim"]
                or task["status"] not in {"done", "cancelled"}
                or f"task:{task['id']}" in in_use
            ):
                continue
            accepted = task["status"] == "done" and task.get("acceptedRevision") is not None
            try:
                note = repository.finish(worktree, org["id"], task["title"], accepted)
            except repository.RepositoryError as exc:
                note = f"Raticode couldn't finish the shared repository changes: {exc}"

            def clear(
                current: dict[str, Any], task: dict[str, Any] = task, note: str = note
            ) -> Any:
                found = next(t for t in current["runtime"]["tasks"] if t["id"] == task["id"])
                if found.get("sharedWorktree") != task["sharedWorktree"] or found["claim"]:
                    return {}
                found["sharedWorktree"] = None
                if note:
                    found["comments"].append({"actor": "system", "at": now(), "body": note})
                found["revision"] += 1
                return {
                    "taskId": task["id"],
                    "branch": task["sharedWorktree"]["branch"],
                    "shared": True,
                    "merged": note.startswith("Merged"),
                }

            self.store.mutate(org["projectRoot"], org["id"], "system", "worktree_released", clear)

    def ensure_repository(self, org: dict[str, Any]) -> None:
        """Create the shared repository when it's turned on and mirror existing knowledge."""
        if not org["config"].get("sharedRepository"):
            return
        path = repository.path_for(self.data_dir, org["id"])
        if repository.exists(path):
            return
        repository.ensure(self.data_dir, org)
        for entry in org["runtime"].get("knowledge", {}).values():
            repository.sync_entry(self.data_dir, org, entry)

    def release_worktrees(self, org: dict[str, Any]) -> None:
        """Commit leftovers and remove worktrees of finished tasks; branches stay for review."""
        with self._lock:
            # A cancelled run clears its claim at once but may still be writing for a moment.
            in_use = set(self._active_roots.values())
        finished = [
            t
            for t in org["runtime"]["tasks"]
            if t.get("worktree")
            and not t["claim"]
            and t["status"] in {"done", "cancelled"}
            and f"task:{t['id']}" not in in_use
        ]
        for task in finished:
            worktree = task["worktree"]
            note = release_worktree(worktree, task["title"], label="Organization")
            if Path(worktree["checkout"]).exists():
                continue  # Keep the record so the remaining changes stay discoverable.

            def clear(
                current: dict[str, Any], task: dict[str, Any] = task, note: str = note
            ) -> Any:
                existing = next(t for t in current["runtime"]["tasks"] if t["id"] == task["id"])
                if existing.get("worktree") != task["worktree"] or existing["claim"]:
                    return {}
                existing["releasedWorktree"] = {
                    key: task["worktree"][key]
                    for key in ("branch", "base", "repository")
                    if key in task["worktree"]
                }
                existing["worktree"] = None
                existing["comments"].append(
                    {
                        "actor": "system",
                        "at": now(),
                        "body": (
                            f"Changes are on branch {task['worktree']['branch']}. Review and "
                            "merge it from Source Control when you're ready."
                            if note
                            else "The task's worktree had no changes and was removed."
                        ),
                    }
                )
                existing["revision"] += 1
                return {"taskId": task["id"], "branch": task["worktree"]["branch"]}

            self.store.mutate(org["projectRoot"], org["id"], "system", "worktree_released", clear)

    def run_input_path(self, oid: str, turn: str) -> Path:
        if not re.fullmatch(r"[0-9a-f-]{1,64}", turn) or not re.fullmatch(r"[0-9a-f-]{1,64}", oid):
            raise ValueError("Invalid run identifier")
        return self.data_dir / "organization-runs" / oid / f"{turn}.json.gz"

    @staticmethod
    def _block(task: dict[str, Any], reason: str) -> None:
        """Stop a task with a reason the user can act on. Evidence is never overwritten."""
        if task["status"] == "blocked" and task.get("blockReason") == reason:
            return
        task.update(status="blocked", blockReason=reason, revision=task["revision"] + 1)
        task["comments"].append({"actor": "system", "at": now(), "body": reason})

    @staticmethod
    def _exhausted(policy: dict[str, Any], account: dict[str, Any]) -> bool:
        return bool(
            (
                policy.get("monthlyTurnLimit")
                and account.get("turns", 0) >= policy["monthlyTurnLimit"]
            )
            or (
                policy.get("monthlyTokenLimit")
                and (
                    account.get("tokens", 0) >= policy["monthlyTokenLimit"]
                    or account.get("unknownTokenTurns", 0)
                )
            )
            or (
                policy.get("monthlyBudgetUsd")
                and (
                    account.get("costUsd", 0) >= policy["monthlyBudgetUsd"]
                    or account.get("unknownCostTurns", 0)
                )
            )
        )

    def _isolated(self, config: dict[str, Any], root: str) -> bool:
        """Whether a task on this root runs in its own git worktree."""
        return config.get("workspaceIsolation", "worktree") == "worktree" and project_identity(
            root
        )[0].startswith("git:")

    def dispatch(self) -> None:
        if not self.enabled:
            return
        with self.store.connect() as db:
            rows = db.execute("SELECT project, id FROM organizations").fetchall()
        if rows:
            offset = self._dispatch_cursor % len(rows)
            rows = rows[offset:] + rows[:offset]
            self._dispatch_cursor += 1
        for row in rows:
            # One organization's failure must not stall the others.
            try:
                self._dispatch_org(row["project"], row["id"])
            except Exception:
                log.exception("Organization dispatch failed for %s", row["id"])

    def _dispatch_org(self, project: str, oid: str) -> None:
        # Read and resolve Git identities before taking the lock that UI saves wait on.
        org = self.store.get(project, oid)
        if org["runtime"]["state"] != "running" or org["runtime"].get("archived"):
            return
        hours = org["config"].get("operatingHours")
        if not is_open(hours, datetime.now(UTC)):
            # Outside working hours nothing starts; running work stops unless set to finish.
            if (hours or {}).get("atClose", "stop") == "stop":
                self.cancel(oid, "hours")
            return
        for root in {
            *(org["config"].get("projectRoots", [])),
            *(t.get("workspacePath") for t in org["runtime"]["tasks"] if t.get("workspacePath")),
            *(p["workspacePath"] for p in org["config"]["projects"] if p.get("workspacePath")),
        }:
            project_identity(root)
        with self._lock:
            # Shared project writes are serialized unless each task has its own worktree.
            if self._closed.is_set() or len(self._active) >= 8:
                return
            chosen: dict[str, Any] = {}

            def claim(org: dict[str, Any]) -> dict[str, Any]:
                runtime, config = org["runtime"], org["config"]
                self._unblock(org)
                for task in runtime["tasks"]:
                    claim_id = task["claim"]
                    if claim_id and (
                        claim_id not in self._active or not self._active[claim_id][2].is_alive()
                    ):
                        task.update(claim=None)
                        self._block(task, "Worker stopped unexpectedly; review before retrying")
                        attempt = runtime.get("attempts", {}).get(claim_id)
                        if attempt:
                            attempt.update(status="interrupted", finishedAt=now())
                        self._active.pop(claim_id, None)
                        self._active_roots.pop(claim_id, None)
                if (
                    runtime["state"] != "running"
                    or not is_open(config.get("operatingHours"), datetime.now(UTC))
                    or sum(active_oid == oid for active_oid, _, _ in self._active.values())
                    >= config["maxConcurrency"]
                ):
                    return {}
                schedule_routines(org, datetime.now(UTC))
                self._unblock(org)
                month = datetime.now(UTC).strftime("%Y-%m")
                usage = runtime.setdefault("usage", {}).setdefault(month, {})
                exhausted = self._exhausted

                if exhausted(config, usage.get("company", {})):
                    runtime["state"] = "paused"
                    runtime["pauseReason"] = (
                        "Monthly limit reached, or the provider doesn't report the cost or "
                        "tokens a limit needs. Raise the limit or use a run limit instead."
                    )
                    return {"reason": runtime["pauseReason"]}
                employees = {e["id"]: e for e in config["employees"] if not e["paused"]}
                employees = {
                    eid: e
                    for eid, e in employees.items()
                    if not exhausted(e, usage.get(f"employee:{eid}", {}))
                }
                for eid, employee in employees.items():
                    interval = employee["heartbeatSeconds"]
                    last = runtime["heartbeats"].get(eid)
                    if interval and (not last or datetime.fromisoformat(last) <= datetime.now(UTC)):
                        heartbeat_due(org, eid)
                        runtime["heartbeats"][eid] = (
                            datetime.now(UTC) + timedelta(seconds=interval)
                        ).isoformat()
                # Priority first. Waiting work gains up to one priority level per day, so
                # it isn't starved, but an old low-priority ticket never jumps a critical one.
                # Tickets someone moved to In progress go first, in the order they were moved.
                for task in sorted(
                    runtime["tasks"],
                    key=lambda t: (
                        0 if t.get("pulledAt") else 1,
                        t.get("pulledAt") or "",
                        {"critical": 0, "high": 1, "medium": 2, "low": 3}[t["priority"]]
                        - min(
                            1.0,
                            (
                                datetime.now(UTC) - datetime.fromisoformat(t["createdAt"])
                            ).total_seconds()
                            / 86400,
                        ),
                    ),
                ):
                    if (
                        task["status"] != "todo"
                        or task["claim"]
                        or task["assignee"] not in employees
                    ):
                        continue
                    if task["approvalRequired"] and task["approvedRevision"] is None:
                        continue
                    if task["dueAt"] and datetime.fromisoformat(task["dueAt"]) > datetime.now(UTC):
                        continue
                    # A cancelled epic or milestone holds its tickets until it's reopened.
                    if plan_hold(config, task):
                        continue
                    if task["turns"] >= config["maxTaskTurns"]:
                        self._block(
                            task,
                            "Run limit for this task reached. Reopen it to allow more runs.",
                        )
                        continue
                    task_project: dict[str, Any] = next(
                        (p for p in config["projects"] if p["id"] == task["project"]), {}
                    )
                    if exhausted(task_project, usage.get(f"initiative:{task['project']}", {})):
                        continue
                    roots = config.get("projectRoots", [])
                    working_root = (
                        task.get("workspacePath")
                        or task_project.get("workspacePath")
                        or (roots[0] if roots else "")
                    )
                    if not working_root or not self.store.owns_project(oid, working_root):
                        self._block(task, "Assign an owned Raticode project before retrying")
                        continue
                    grants = employees[task["assignee"]].get("workspacePaths")
                    if grants is not None and working_root not in grants:
                        self._block(task, "Workspace is outside this employee's grants")
                        continue
                    isolated = self._isolated(config, working_root)
                    root_key = f"task:{task['id']}" if isolated else working_root
                    if root_key in self._active_roots.values() or any(
                        t["claim"] and t["assignee"] == task["assignee"] for t in runtime["tasks"]
                    ):
                        continue
                    try:
                        reserved = reservation(org, task)
                        validate_execution(org, task)
                    except ValueError as exc:
                        self._block(task, str(exc))
                        continue
                    if (
                        task.get("execution", {})
                        and task["execution"].get("kind") == "workflow"
                        and not task.get("workflowAuthorization")
                    ):
                        self._block(task, "Authorize the workflow preview before launch")
                        continue
                    if task["turns"] + reserved > config["maxTaskTurns"]:
                        self._block(
                            task,
                            "This task's run limit is too small for the runs it reserves",
                        )
                        continue
                    # A monthly limit is temporary: wait for capacity instead of blocking.
                    if any(
                        policy.get("monthlyTurnLimit")
                        and account.get("turns", 0) + reserved > policy["monthlyTurnLimit"]
                        for policy, account in (
                            (config, usage.get("company", {})),
                            (
                                employees[task["assignee"]],
                                usage.get(f"employee:{task['assignee']}", {}),
                            ),
                            (task_project, usage.get(f"initiative:{task['project']}", {})),
                        )
                    ):
                        continue
                    task.update(
                        claim=str(uuid4()),
                        status="in_progress",
                        turns=task["turns"] + reserved,
                        revision=task["revision"] + 1,
                        pulledAt=None,
                    )
                    for account_id in (
                        "company",
                        f"employee:{task['assignee']}",
                        f"initiative:{task['project']}",
                    ):
                        account = usage.setdefault(account_id, {})
                        account["turns"] = account.get("turns", 0) + reserved
                    runtime.setdefault("attempts", {})[task["claim"]] = {
                        "id": task["claim"],
                        "organizationId": oid,
                        "taskId": task["id"],
                        "employeeId": task["assignee"],
                        "parentRunId": task.get("parentRunId"),
                        "workspacePath": working_root,
                        "startedAt": now(),
                        "status": "running",
                        "configRevision": org["revision"],
                        "planRevision": task.get("planRevision", 1),
                        "costUsd": None,
                        "reservedTurns": reserved,
                        "reservationMonth": month,
                        "initiativeId": task["project"],
                        # What ran, so a run can be explained and repeated later.
                        "provider": employees[task["assignee"]]["provider"],
                        "model": employees[task["assignee"]]["model"],
                        "effort": employees[task["assignee"]].get("effort"),
                        "permissionMode": employees[task["assignee"]]["permissionMode"],
                        "isolation": "worktree" if isolated else "folder",
                        "blockGitPush": config.get("blockGitPush", True),
                    }
                    chosen.update(
                        budget=min(
                            (
                                policy["monthlyBudgetUsd"]
                                - usage.get(account_id, {}).get("costUsd", 0)
                                for policy, account_id in (
                                    (config, "company"),
                                    (
                                        employees[task["assignee"]],
                                        f"employee:{task['assignee']}",
                                    ),
                                    (task_project, f"initiative:{task['project']}"),
                                )
                                if policy.get("monthlyBudgetUsd")
                            ),
                            default=None,
                        ),
                        month=month,
                        working_root=working_root,
                        root_key=root_key,
                        isolated=isolated,
                        org=copy.deepcopy(org),
                        task=copy.deepcopy(task),
                        employee=copy.deepcopy(employees[task["assignee"]]),
                        reserved=reserved,
                    )
                    return {
                        "taskId": task["id"],
                        "turnId": task["claim"],
                        "employeeId": task["assignee"],
                        "configRevision": org["revision"],
                    }
                return {}

            if any(
                a.get("executionHandle")
                and not a["executionHandle"].get("settled")
                and a["id"] not in self._active
                for a in org["runtime"].get("attempts", {}).values()
            ):
                return
            instant = datetime.now(UTC)
            runtime = org["runtime"]
            # Avoid activity noise on every scheduler tick: only claim when something can move.
            if not (
                any(
                    task_reason(org, t, instant) in DISPATCH_REASONS
                    or (
                        t["claim"]
                        and (
                            t["claim"] not in self._active
                            or not self._active[t["claim"]][2].is_alive()
                        )
                    )
                    for t in runtime["tasks"]
                )
                or any(
                    e["heartbeatSeconds"]
                    and not e["paused"]
                    and (
                        not runtime["heartbeats"].get(e["id"])
                        or datetime.fromisoformat(runtime["heartbeats"][e["id"]]) <= instant
                    )
                    for e in org["config"]["employees"]
                )
                or any(
                    r["enabled"]
                    and r.get("nextAt")
                    and datetime.fromisoformat(r["nextAt"]) <= instant
                    for r in runtime.get("routines", {}).values()
                )
            ):
                return
            self.store.mutate(project, oid, "system", "dispatch", claim)
            if not chosen:
                return
            cancel = threading.Event()
            turn_id = chosen["task"]["claim"]
            thread = threading.Thread(
                target=self._run,
                args=(chosen, cancel),
                daemon=True,
                name=f"employee-{turn_id[:8]}",
            )
            self._active[turn_id] = (oid, cancel, thread)
            self._active_roots[turn_id] = chosen["root_key"]
            try:
                thread.start()
                self._wake.set()
            except Exception as exc:
                chosen["setup_error"] = str(exc)
                self._run(chosen, cancel)

    def _run(self, chosen: dict[str, Any], cancel: threading.Event) -> None:
        org, task, employee = chosen["org"], chosen["task"], chosen["employee"]
        project, oid, turn_id = org["projectRoot"], org["id"], task["claim"]
        error: str | None = None
        final = ""
        working_root = chosen["working_root"]
        usage: dict[str, Any] = {}
        managed_turns: int | None = None
        # Fingerprint of exactly what the employee was given, and where its work ended up.
        provenance: dict[str, Any] = {}
        try:
            if chosen.get("setup_error"):
                raise RuntimeError(chosen["setup_error"])
            workspace_paths = sorted(
                {
                    *org["config"].get("projectRoots", []),
                    *(
                        p.get("workspacePath")
                        for p in org["config"]["projects"]
                        if p.get("workspacePath")
                        and self.store.owns_project(oid, p["workspacePath"])
                    ),
                }
            )
            runtime_dir = self.data_dir.parent / ".raticode-employee-runtime" / oid / employee["id"]
            runtime_dir.mkdir(parents=True, exist_ok=True)
            if employee.get("workspacePaths") is not None:
                workspace_paths = [
                    root for root in workspace_paths if root in employee["workspacePaths"]
                ]

            operation(self, org, "memory_check", {}, "system", employee["id"])
            fresh = self.store.get(project, oid)
            verified_memory = [
                m["body"]
                for m in fresh["runtime"].get("memories", {}).values()
                if m["employeeId"] == employee["id"] and not m["stale"]
            ]
            run_root = working_root
            workspace_note = ""
            if not task.get("execution"):
                if chosen.get("isolated"):
                    worktree = self._task_worktree(project, oid, task, working_root, turn_id)
                    run_root = worktree["root"]
                    workspace_note = (
                        f"Work only in your task's git worktree at {run_root}, on branch "
                        f"{worktree['branch']}. It was created from the project's commit "
                        f"{worktree['base'][:12]}. Never edit {working_root} itself. Commit "
                        "finished changes to your branch; the user reviews and merges it.\n"
                    )
                elif project_identity(working_root)[0].startswith("git:") and _dirty(working_root):
                    raise ValueError(
                        "The project folder has uncommitted changes. Commit or stash them, or "
                        "switch the organization to worktree isolation, then reopen this task."
                    )
                shared = self._shared_worktree(project, oid, task, turn_id)
                if shared:
                    shared_root = str(Path(shared["root"]).resolve())
                    workspace_paths = sorted({*workspace_paths, shared_root})
                    workspace_note += (
                        f"The organization's shared repository is checked out for this ticket at "
                        f"{shared_root} on branch {shared['branch']}. Put organization-wide "
                        "decisions, notes and files every project should share there, and commit "
                        "them; they merge into the shared repository when the user accepts this "
                        "ticket. Raticode writes knowledge/ from knowledge entries, so use "
                        "knowledge_save for notes instead of editing that folder.\n"
                    )
            if org["config"].get("blockGitPush", True):
                workspace_note += "git push is blocked in this run. Leave publishing to the user.\n"
            run_root = str(Path(run_root).resolve())
            if run_root not in workspace_paths:
                workspace_paths = sorted({*workspace_paths, run_root})

            async def execute() -> None:
                nonlocal final, usage, managed_turns
                provenance["runRoot"] = run_root
                with self.session(
                    project,
                    f"employee:{employee['id']}",
                    employee_id=employee["id"],
                    bound_org=oid,
                    generation=org["runtime"]["generation"],
                    run_id=turn_id,
                    read_only=employee["permissionMode"] in {"read-only", "plan"},
                ) as url:
                    resources = copy.deepcopy(employee["resources"])
                    secret_values = resolve_secrets(fresh, employee, resources)
                    with self._lock:
                        self._secret_values[turn_id] = secret_values
                    resources.setdefault("mcpServers", []).append(
                        {"name": "organizations", "type": "http", "url": url}
                    )
                    if employee["skills"]:
                        for name, content in org["config"]["packageFiles"].items():
                            safe_path(name)
                            if not name.startswith("skills/"):
                                continue
                            destination = (
                                self.data_dir
                                / "organization-skills"
                                / oid
                                / str(org["revision"])
                                / name
                            )
                            with atomic_binary_output(destination) as output:
                                output.write(base64.b64decode(content, validate=True))
                        # Older revisions' copies are no longer used by any run.
                        copies = self.data_dir / "organization-skills" / oid
                        for child in copies.iterdir() if copies.is_dir() else []:
                            if child.name.isdigit() and int(child.name) < org["revision"] - 1:
                                shutil.rmtree(child, ignore_errors=True)
                        resources.setdefault("skills", []).extend(
                            {
                                "path": str(
                                    self.data_dir
                                    / "organization-skills"
                                    / oid
                                    / str(org["revision"])
                                    / "skills"
                                    / skill
                                    / "SKILL.md"
                                )
                            }
                            for skill in employee["skills"]
                        )
                    persona = (
                        f"Persona: {employee['persona']}\n" if employee.get("persona") else ""
                    ) + (
                        "Responsibilities: " + "; ".join(employee["responsibilities"]) + "\n"
                        if employee.get("responsibilities")
                        else ""
                    )
                    identity = (
                        f"You are {employee['name']}, a Rem employee of {org['config']['name']}.\n"
                        f"Role: {employee['role']}. Title: {employee['title']}. "
                        f"Manager: {employee['reportsTo'] or 'organization owner'}.\n"
                        + persona
                        + (
                            f"Organization mission: {org['config']['mission']}\n"
                            if org["config"].get("mission")
                            else ""
                        )
                        + f"Organization purpose: {org['config']['description']}\n"
                        f"Organization instructions: {org['config']['instructions']}\n"
                        f"Your instructions: {employee['instructions']}\n"
                        + workspace_note
                        + f"Persistent memory: {employee['memory']}\n"
                        "Working memory: "
                        + org["runtime"].get("employeeMemory", {}).get(employee["id"], "")
                        + "\nVerified source-linked memory: "
                        + "\n".join(verified_memory)
                        + "\n"
                        + "You have coding and research tools. "
                        "Delegate through organization tasks. "
                        "For swarm, fleet or remote work, create an organization task with "
                        "execution={targetId: owner-granted destination ID}. "
                        "Use organization tools to read assignments, delegate, and save evidence. "
                        "You cannot configure any organization. "
                        "Ask Rem or the user for configuration changes. Never edit organization "
                        "databases, history or configuration files through shell. "
                        "Do not delegate configuration changes to other agents. "
                        "Record findings as task comments; update task status with evidence. "
                        "Use action=memory for useful knowledge to retain across assignments. "
                        "If you finish without updating status, your work goes to review.\n"
                        + RUN_GUIDE
                    )
                    workflow = {
                        "id": f"organization:{oid}:{employee['id']}:{task['id']}",
                        "chatThreadId": f"organization:{oid}:{employee['id']}:{task['id']}",
                        "projectRoot": run_root,
                        "remBlockGitPush": org["config"].get("blockGitPush", True),
                        "organizationWorkspacePaths": workspace_paths,
                        "organizationRun": {
                            "organizationId": oid,
                            "taskId": task["id"],
                            "parentRunId": turn_id,
                        },
                        "remResources": resources,
                        "remSecondBrain": org["config"]["remSecondBrain"],
                        "remReportTheme": org["config"]["remReportTheme"],
                    }
                    task_context = copy.deepcopy(task)
                    task_context["comments"] = task_context["comments"][-20:]
                    remaining = 48000
                    for comment in reversed(task_context["comments"]):
                        comment["body"] = (
                            comment["body"][-max(0, remaining) :] if remaining > 0 else ""
                        )
                        remaining -= len(comment["body"])
                    messages = [
                        {
                            "role": "user",
                            "body": json.dumps(
                                {
                                    "organizationId": oid,
                                    "goals": org["config"]["goals"],
                                    "task": task_context,
                                    "plan": run_context(fresh, task),
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]

                    granted = copy.deepcopy(resources)
                    granted["mcpServers"] = [
                        server
                        for server in granted.get("mcpServers", [])
                        if server.get("name") != "organizations"
                    ]
                    for server in granted["mcpServers"]:
                        server.pop("env", None)
                    # Keep exactly what the employee was given, so the run can be inspected,
                    # compared or repeated. The fingerprint covers the stored bytes.
                    run_input = self._redact(
                        json.dumps(
                            {
                                "provider": employee["provider"],
                                "model": employee["model"],
                                "effort": employee["effort"],
                                "permissionMode": employee["permissionMode"],
                                "instructions": identity,
                                "messages": messages,
                                "resources": granted,
                                "workingDirectory": run_root,
                            },
                            sort_keys=True,
                            ensure_ascii=False,
                            default=str,
                        )
                    ).encode()
                    provenance["inputSha256"] = hashlib.sha256(run_input).hexdigest()
                    write_run_input(self.run_input_path(oid, turn_id), run_input)

                    async def source() -> AsyncGenerator[dict[str, Any], None]:
                        if task.get("execution"):
                            async with aclosing(self.executions.stream(chosen, cancel)) as managed:
                                async for item in managed:
                                    yield item
                            return
                        async with aclosing(
                            self.stream(
                                provider=employee["provider"],
                                model=employee["model"],
                                effort=employee["effort"],
                                permission_mode=employee["permissionMode"],
                                messages=messages,
                                workflow=workflow,
                                trusted_organization_url=url,
                                agent_instructions=identity,
                                data_dir=runtime_dir,
                                working_dir=Path(run_root),
                                cancel_event=cancel,
                            )
                        ) as direct:
                            async for item in direct:
                                yield item

                    def owned_root(path: str) -> str:
                        # Follow-ups from the task's worktree belong to the original project.
                        inside = Path(path).resolve().is_relative_to(run_root)
                        return working_root if inside else path

                    stored = 0

                    async def consume() -> None:
                        nonlocal final, usage, managed_turns, stored
                        async with aclosing(source()) as stream:
                            async for event in stream:
                                if type(event.get("managedTurns")) is int:
                                    managed_turns = event["managedTurns"]
                                if isinstance(event.get("usage"), dict):
                                    usage = (
                                        event["usage"]
                                        if task.get("execution")
                                        else normalize_usage(employee["provider"], event["usage"])
                                    )
                                if cancel.is_set():
                                    return
                                if event.get("type") == "new-thread":
                                    self.call(
                                        project,
                                        f"employee:{employee['id']}",
                                        {
                                            "action": "task_create",
                                            "organizationId": oid,
                                            "params": {
                                                "title": "Follow-up thread",
                                                "description": event["message"],
                                                "assignee": employee["id"],
                                                "workspacePath": owned_root(event["projectRoot"]),
                                                "parentId": task["id"],
                                                "project": next(
                                                    (
                                                        p["id"]
                                                        for p in org["config"]["projects"]
                                                        if p.get("workspacePath")
                                                        == event["projectRoot"]
                                                    ),
                                                    None,
                                                ),
                                            },
                                        },
                                        employee_id=employee["id"],
                                        bound_org=oid,
                                        generation=org["runtime"]["generation"],
                                        run_id=turn_id,
                                    )
                                if event.get("type") == "error":
                                    raise ValueError(str(event.get("error", "Provider failed")))
                                if event.get("type") == "final":
                                    final = str((event.get("message") or {}).get("body", ""))
                                # Store durable events, not ephemeral capability URLs.
                                stored += 1
                                if stored > MAX_TURN_EVENTS and event.get("type") not in {
                                    "final",
                                    "error",
                                    "usage",
                                }:
                                    continue
                                clean = self._clean(event)
                                with self.store.connect() as db:
                                    self.store.event(
                                        db,
                                        oid,
                                        f"employee:{employee['id']}",
                                        "turn_event",
                                        {"turnId": turn_id, "taskId": task["id"], "event": clean},
                                    )

                    # Watch for cancellation even while a provider is silent.
                    consumer = asyncio.ensure_future(consume())
                    try:
                        async with asyncio.timeout(org["config"]["turnTimeoutSeconds"]):
                            while not consumer.done():
                                if cancel.is_set():
                                    consumer.cancel()
                                    break
                                await asyncio.wait({consumer}, timeout=0.25)
                    finally:
                        if not consumer.done():
                            consumer.cancel()
                        try:
                            await consumer
                        except asyncio.CancelledError:
                            if not cancel.is_set():
                                raise

            asyncio.run(execute())
        except Exception as exc:
            error = self._redact(str(exc)) or type(exc).__name__
        finally:
            final = self._redact(final)
            if chosen.get("isolated") and provenance.get("runRoot"):
                provenance.update(_worktree_state(provenance["runRoot"]))
            with self._lock:
                cancel_reason = self._cancel_reasons.pop(turn_id, "cancelled")
            # Delegated executions may have external effects, so they stay blocked for review.
            paused = (
                cancel.is_set()
                and cancel_reason in {"paused", "hours"}
                and not task.get("execution")
            )

            def finish(current: dict[str, Any]) -> dict[str, Any]:
                existing = next(t for t in current["runtime"]["tasks"] if t["id"] == task["id"])
                month = chosen["month"]
                accounts = current["runtime"].setdefault("usage", {}).setdefault(month, {})
                reserved = chosen.get("reserved", 1)
                # Only a completed execution proves unused reservations can be returned.
                used = (
                    managed_turns
                    if final and not error and not cancel.is_set() and managed_turns is not None
                    else reserved
                )
                used = min(reserved, max(0, used))
                existing["turns"] -= reserved - used
                cost = usage.get("cost_usd", usage.get("total_cost_usd"))
                known_cost = (
                    isinstance(cost, (int, float))
                    and not isinstance(cost, bool)
                    and math.isfinite(cost)
                    and cost >= 0
                )
                for account_id in (
                    "company",
                    f"employee:{employee['id']}",
                    f"initiative:{task['project']}",
                ):
                    account = accounts.setdefault(account_id, {})
                    account["turns"] = max(0, account.get("turns", reserved) - (reserved - used))
                    account["costUsd"] = account.get("costUsd", 0) + (cost if known_cost else 0)
                    account["unknownCostTurns"] = account.get("unknownCostTurns", 0) + int(
                        not known_cost
                    )
                    tokens = usage.get("total_tokens")
                    if type(tokens) is int and tokens >= 0:
                        account["tokens"] = account.get("tokens", 0) + tokens
                    else:
                        account["unknownTokenTurns"] = account.get("unknownTokenTurns", 0) + 1
                attempt = current["runtime"].setdefault("attempts", {}).get(turn_id)
                if attempt:
                    if attempt.get("executionHandle") and (
                        final
                        and not error
                        and not cancel.is_set()
                        or attempt["executionHandle"]["kind"] == "workflow"
                    ):
                        attempt["executionHandle"]["settled"] = True
                    attempt.update(
                        finishedAt=now(),
                        elapsedSeconds=(
                            datetime.now(UTC) - datetime.fromisoformat(attempt["startedAt"])
                        ).total_seconds(),
                        status="paused"
                        if paused
                        else "cancelled"
                        if cancel.is_set()
                        else "failed"
                        if error or not final
                        else "completed",
                        error=error,
                        costUsd=cost if known_cost else None,
                        **{k: v for k, v in provenance.items() if k != "runRoot"},
                        usage=usage,
                        usedTurns=used,
                    )
                if (
                    existing["status"] == "cancelled"
                    and existing.get("cancellation") == "requested"
                ):
                    existing["cancellation"] = (
                        "acknowledged"
                        if not attempt
                        or not attempt.get("executionHandle")
                        or attempt["executionHandle"].get("settled")
                        else "requested"
                    )
                if existing["claim"] != turn_id:
                    return {"turnId": turn_id, "discarded": True, "reason": "Configuration changed"}
                if final:
                    existing["comments"].append(
                        {"actor": f"employee:{employee['id']}", "at": now(), "body": final}
                    )
                failed = error or cancel.is_set() or not final
                if paused and existing["status"] == "in_progress":
                    # Pausing is not a failure: the task waits for the next Start, and the
                    # interrupted run doesn't count against its run limit.
                    existing.update(status="todo", turns=max(0, existing["turns"] - used))
                    existing["comments"].append(
                        {
                            "actor": "system",
                            "at": now(),
                            "body": "Working hours ended before this run finished. It runs "
                            "again when hours resume."
                            if cancel_reason == "hours"
                            else "The organization was paused before this run finished. "
                            "The task runs again when the organization starts.",
                        }
                    )
                elif failed:
                    existing.update(
                        status="blocked",
                        blockReason=(
                            "The last run failed; see the conversation"
                            if error
                            else "Cancelled before it finished"
                            if cancel.is_set()
                            else "The last run ended without a final response"
                        ),
                    )
                    existing["comments"].append(
                        {
                            "actor": "system",
                            "at": now(),
                            "body": error
                            or "Turn ended without a final response; review before retrying",
                        }
                    )
                elif existing["status"] == "in_progress" and is_heartbeat(existing):
                    # A check-in is not deliverable work: it closes itself, so the next
                    # heartbeat isn't held up waiting for a review nobody asked for.
                    existing.update(
                        status="done", evidence="Check-in: " + (final[:4000] or "finished")
                    )
                    current["runtime"].setdefault("heartbeatFailures", {}).pop(employee["id"], None)
                elif existing["status"] == "in_progress":
                    existing["status"] = "in_review"
                    existing["reviewRequestedAt"] = now()
                elif (
                    existing["status"] == "done"
                    and existing["recurring"]
                    and existing["intervalSeconds"]
                ):
                    self._record_occurrence(current, existing)
                    existing.update(
                        status="todo",
                        turns=0,
                        dueAt=(
                            datetime.now(UTC) + timedelta(seconds=existing["intervalSeconds"])
                        ).isoformat(),
                    )
                existing.update(claim=None, revision=existing["revision"] + 1)
                self._unblock(current)
                return {
                    "turnId": turn_id,
                    "taskId": task["id"],
                    "error": error,
                    "usage": usage,
                    "status": existing["status"],
                }

            try:
                self.store.mutate(
                    project, oid, f"employee:{employee['id']}", "turn_finished", finish
                )
                # Raticode reruns the project checks this run cited, in its worktree.
                try:
                    start_checks(self, project, oid, task["id"])
                except Exception:  # noqa: BLE001 - checks never decide the run's outcome
                    log.exception("ORGANIZATION_CHECKS_FAILED organization=%s", oid)
                execution_kind = None
                if task.get("execution"):
                    execution_kind = task["execution"].get("kind") or target_for(org, task)["kind"]
                # Local managed providers record each invocation in their own runtime.
                # Keep organization aggregates in attempts without double-counting the ledger.
                if execution_kind not in {"swarm", "workflow"}:
                    record_invocation(
                        invocation_id=f"organization:{turn_id}",
                        provider=employee["provider"] if execution_kind is None else execution_kind,
                        model=employee["model"] if execution_kind is None else None,
                        metadata=usage,
                        status="stopped"
                        if cancel.is_set()
                        else "failed"
                        if error or not final
                        else "completed",
                        data_dir=self.data_dir,
                    )
            finally:
                with self._lock:
                    self._secret_values.pop(turn_id, None)
                    self._active.pop(turn_id, None)
                    self._active_roots.pop(turn_id, None)
                self._wake.set()
