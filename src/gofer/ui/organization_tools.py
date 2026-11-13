"""Discoverable organization management actions shared by Rem and the desktop."""

from __future__ import annotations

from typing import Any

from gofer.ui.organization_operations import READ_OPERATIONS, Routine
from gofer.ui.organization_store import TASK_EDIT_FIELDS, OrganizationConfig, Task

READ_ACTIONS = {
    "help",
    "list",
    "read",
    "history",
    "events",
    "export",
    "import_preview",
    "import_github_preview",
    "providers",
    "run_events",
    "audit_verify",
    "run_input",
    "knowledge_search",
    "knowledge_read",
    "ticket_changes",
    "repository",
} | READ_OPERATIONS


# Parameters live under params; organizationId is a top-level tool argument.
ACTION_HELP = {
    "help": "No parameters. Returns all actions, schemas, workspace grants and session limits.",
    "list": "No parameters. Lists global organizations, including paused organizations.",
    "providers": (
        "Optional refresh (boolean). Discover installed providers, enabled state, models and "
        "effort options using the same catalog as the employee settings picker. No organizationId "
        "is required. Read this before changing provider/model/effort."
    ),
    "read": "Optional sinceVersion. Returns configuration, runtime and dispatch diagnostics.",
    "history": "Optional offset, limit (1..100, default 50). Configuration revisions and diffs.",
    "events": (
        "Optional after sequence cursor. Returns up to 500 activity entries. Or recent (1..500) "
        "for the latest entries without run transcripts."
    ),
    "create": "config, optional reason. Creates a paused organization; no project is required.",
    "configure": (
        "config, expectedRevision, reason. Replace the full configuration from read. "
        "Manage employees, reporting lines, teams, initiatives (projects), projectRoots, goals "
        "(goalRecords), executionTargets, budgets, concurrency, provider/model/effort, "
        "permissions, "
        "resources, skills, workspace/secret grants, persona memory, Second Brain and theme. "
        "Preserve unrelated fields. Remove collection members by ID, retaining "
        "valid references. Teams use id/name/description/manager; initiatives use "
        "id/name/description/owner/workspacePath/monthlyBudgetUsd/monthlyTurnLimit. "
        "Set employees[].paused for individual holds. New projectRoots must be "
        "in workspacePaths. Changes are audited and stale revisions are rejected."
    ),
    "restore": "revision, expectedRevision, reason. Restore as a new paused revision.",
    "export": "No parameters. Returns portable package files and ZIP as base64; excludes runtime.",
    "import_preview": (
        "Exactly one of files (relative path to base64 map), zip (base64 ZIP), or path "
        "(absolute folder or ZIP inside workspacePaths). Preview without creating an organization."
    ),
    "import": "Same sources as import_preview, optional reason. Creates a paused organization.",
    "import_github_preview": (
        "repository (owner/name), commit (40-character SHA), optional directory. Preview pinned "
        "public GitHub template, bounded to 16 MiB and 1,000 files."
    ),
    "import_github": "Same parameters as import_github_preview. Import a paused template.",
    "control": (
        "state=running/paused. Running authorizes eligible work; individual holds remain. "
        "From Rem, starting returns needsUserConsent: the user approves it in the "
        "Organizations tab within 24 hours. Tell the user that nothing has started yet. "
        "The same applies to approve, workflow_launch, enabling routines or webhooks, and "
        "configure/restore changes that add or resume employees, widen permissions, tools, "
        "folders or credentials, or raise or remove limits."
    ),
    "resume_all": (
        "expectedRevision. Explicitly clear all employee holds. From Rem, needs user consent."
    ),
    "archive": (
        "archived (boolean, default true). Archive a paused company and release its projects, "
        "or restore it. History is kept. From Rem, archiving needs user consent."
    ),
    "request_decide": "User-only requestId, decision=approve/decline for Rem's consent requests.",
    "auto_mode": (
        "User-only enabled (bool), confirm (true to turn on). Auto mode: Rem's review decides "
        "this organization's requests; Rem, employees and agent tokens are refused."
    ),
    "request_approve_all": (
        "User-only requestIds: the exact list the user confirmed in Approve all. Approves them "
        "in a safe order and reports each; Rem and employees are refused."
    ),
    "run_events": "taskId, turnId (an attempt ID). Stored transcript events of one employee run.",
    "run_input": "taskId, turnId. The stored input of one employee run and whether it matches.",
    "audit_verify": (
        "No parameters. Verifies the configuration history hash chain and the append-only, "
        "hash-linked activity log; reports events written before linking began."
    ),
    "task_create": (
        "Task fields from taskSchema, required title, optional idempotencyKey. "
        "execution={targetId: destination ID} requests managed delegation. "
        "workflowContract defines path, inputs, allowedResources, turnLimit and completionChecks. "
        "Checks use {kind: file, path, sha256 (optional)} or {kind: output, name, equals}. "
        "Workflow execution still requires workflow_preview then workflow_launch."
    ),
    "task_update": (
        "taskId, expectedRevision, changes using taskSchema fields. "
        "status=cancelled stops a task. status=in_progress on a waiting ticket queues it "
        "next for its worker; Raticode starts the run. Changed plans invalidate approval. "
        "Pause and wait before editing a running plan; use reopen to retry terminal work."
    ),
    "comment": "taskId, body, optional idempotencyKey, unblock. Adds an attributed comment.",
    "approve": "taskId, expectedRevision. Approve the current task plan.",
    "review": "taskId, expectedRevision, decision=accept/revise/reject, body (review reason).",
    "reopen": "taskId, expectedRevision. Reopen done, cancelled or blocked work.",
    "wake": "employeeId, message, optional idempotencyKey. Queue an assigned task.",
    "tasks": (
        "Optional query, assignee, project (initiative ID), goalId, status, attention (boolean), "
        "offset, limit (1..100). Search and paginate work."
    ),
    "attempts": "Optional taskId, offset, limit (1..100). Read run receipts and usage.",
    "routines": "Optional offset, limit (1..100). Read durable schedules.",
    "diagnostics": "No parameters. Dispatch reasons, attention inbox and capacity warnings.",
    "outcomes": "No parameters. Accepted outcomes, rework, costs and coverage.",
    "rehearse": (
        "Optional at (ISO timestamp with timezone), assumedDurationSeconds (1..86400), "
        "horizonSeconds (1..604800), config, arrivals (up to 100 task objects). "
        "Simulates a copy without provider calls or real execution."
    ),
    "routine_save": (
        "routine using routineSchema. Updates include routine.id and expectedRevision. "
        "Set enabled=false to disable. task uses taskSchema."
    ),
    "routine_trigger": "routineId, idempotencyKey. Queue one occurrence of an enabled routine.",
    "memory_save": "employeeId, body, path (source file), optional expiresAt. Save a sourced fact.",
    "memory_check": "No parameters. Invalidate stale source evidence and queue rechecks.",
    "artifact_add": "taskId, attemptId, path, kind=file/diff/test/document, optional check.",
    "routing_experiment": (
        "employeeId, taskIds (1..10 unique pending tasks), reason (hypothesis). "
        "Reassign the trial tasks, require review and retain their previous assignments."
    ),
    "workflow_preview": "taskId. Compile the contract and return preflight and required grants.",
    "workflow_launch": (
        "taskId, expectedRevision, irSha256 from workflow_preview. Authorize reviewed execution; "
        "the organization must be running for dispatch. Obtain user authorization before launch."
    ),
    "webhook_save": "routineId, expectedRevision (routine), secretRef, enabled (boolean).",
    "execution_reconcile": "No parameters. Retry stop confirmation for uncertain executions.",
    "execution_resolve": (
        "attemptId, confirmStopped=true, reason (inspection evidence). "
        "Resolve an inactive execution only after verifying that external work has stopped."
    ),
    "secret_reference": "id, account, optional revoked (boolean). Version an OS keyring reference.",
    "secret_store": (
        "User-only id, value. Rem cannot store credential values: ask the user to enter them "
        "under Operations > Protected credentials, then reference the stored ID."
    ),
    "backup": (
        "No parameters. Returns filename, base64 content and sha256 for a consistent SQLite "
        "backup of ALL organizations. Credentials stay in the OS keyring."
    ),
    "memory": "Employee-only body. Stores the current employee's working memory.",
    "heartbeat_resume": (
        "employeeId. Resume a worker's heartbeats after repeated failed check-ins: cancels its "
        "blocked check-in tickets and schedules the next one now."
    ),
    "plan_save": (
        "kind=mission/project/milestone/epic/operations, item, expectedRevision, optional reason. "
        "Saves one plan item as a configuration revision. mission: {mission}. project: "
        "{id?, name, description, owner, color, workspacePath, checks: [{name, command}], "
        "autoRunChecks}. Checks are commands Raticode runs in the ticket's worktree to "
        "verify claims, which runs the branch's code as the user, so adding them or turning "
        "on autoRunChecks needs the user's consent. milestone: {id?, title, short, dueAt "
        "(YYYY-MM-DD), projectId (empty for organization milestones), parentId (the "
        "organization milestone a project milestone serves), status}. epic: {id?, title, "
        "milestoneId (a project milestone), owner, description, status}. Tickets are tasks "
        "with epicId. operations: any of {operatingHours, workSchedule, resumeAfterRestart, "
        "pullBacklog}; pullBacklog (default true) lets a worker with nothing to do pull an "
        "accepted Backlog ticket into To do, and turning it on from Rem needs consent."
    ),
    "plan_delete": (
        "kind=project/milestone/epic, id, expectedRevision. Refused while tickets, epics or "
        "milestones still depend on it."
    ),
    "knowledge_search": (
        "Optional query, projectId, kind, limit (1..50). Shared organization knowledge."
    ),
    "knowledge_read": (
        "id. Full knowledge entry. During a run, Raticode records that your ticket read it."
    ),
    "knowledge_save": (
        "entry {id?, kind=decision/note/report/spec/finding, title, summary, body, topic, "
        "projectId, links (related entry IDs)}, expectedVersion when updating. Employees "
        "update only entries they wrote."
    ),
    "knowledge_import": (
        "path (a Markdown, text or HTML note in the organization's Second Brain folder), "
        "optional kind, topic, projectId. Adds or refreshes a Second Brain note."
    ),
    "knowledge_refresh": "No parameters. Reread Second Brain notes and flag changed ones.",
    "knowledge_link": (
        "from, to (entry IDs), quote (exact text from either entry supporting the link). "
        "Or linkId with remove=true."
    ),
    "claim_add": (
        "taskId (ID or ticket key), text (at most 120 characters), evidence: {kind: check, "
        "command} | {kind: quote, entryId, quote} | {kind: commit, sha} | {kind: file, path}. "
        "At most 5 per run. Raticode reruns project checks and checks quotes, commits and files."
    ),
    "check_run": (
        "User-only claimId, or taskId to run every check waiting on that ticket. Runs in the "
        "ticket's worktree in the background."
    ),
    "ticket_changes": (
        "taskId. The ticket's branch, commits and changed files, plus its shared repository "
        "changes."
    ),
    "repository": (
        "No parameters. The organization's shared repository: whether it's on, its folder, "
        "recent commits, top-level files and open ticket branches. Turn it on with plan_save "
        "kind=repository item={enabled: true}; from Rem that needs the user's consent."
    ),
}


def management_help() -> dict[str, Any]:
    task_schema = Task.model_json_schema()
    task_schema["properties"] = {
        key: value for key, value in task_schema["properties"].items() if key in TASK_EDIT_FIELDS
    }
    return {
        "actions": {
            name: {"parameters": description, "readOnly": name in READ_ACTIONS}
            for name, description in ACTION_HELP.items()
        },
        "configurationSchema": OrganizationConfig.model_json_schema(),
        "taskSchema": task_schema,
        "routineSchema": Routine.model_json_schema(),
    }


TOOL: dict[str, Any] = {
    "name": "organization_action",
    "description": (
        "Manage everything in Organizations: companies, employees, reporting lines, teams, "
        "projects, goals, tasks, reviews, schedules, execution destinations, workflows, webhooks, "
        "credentials, imports, backups and recovery. Available in global and project threads. "
        "Call action=help for parameters and configuration/task/routine schemas."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": sorted(ACTION_HELP)},
            "organizationId": {"type": "string"},
            "params": {"type": "object"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}
