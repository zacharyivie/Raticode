"""Discoverable organization management actions shared by Rem and the desktop."""

from __future__ import annotations

from typing import Any

from gofer.ui.organization_operations import READ_OPERATIONS, Routine
from gofer.ui.organization_store import OrganizationConfig, Task

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
} | READ_OPERATIONS

TASK_EDIT_FIELDS = {
    "title",
    "description",
    "assignee",
    "project",
    "parentId",
    "goal",
    "priority",
    "status",
    "evidence",
    "dueAt",
    "recurring",
    "intervalSeconds",
    "approvalRequired",
    "workspacePath",
    "dependsOn",
    "blockReason",
    "reviewRequired",
    "reviewer",
    "goalId",
    "artifacts",
    "workflowContract",
    "execution",
}

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
    "events": "Optional after sequence cursor. Returns up to 500 activity entries.",
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
    "control": "state=running/paused. Running authorizes eligible work; individual holds remain.",
    "resume_all": "expectedRevision. Explicitly clear all employee holds.",
    "task_create": (
        "Task fields from taskSchema, required title, optional idempotencyKey. "
        "execution={targetId: destination ID} requests managed delegation. "
        "workflowContract defines path, inputs, allowedResources, turnLimit and completionChecks. "
        "Checks use {kind: file, path, sha256 (optional)} or {kind: output, name, equals}. "
        "Workflow execution still requires workflow_preview then workflow_launch."
    ),
    "task_update": (
        "taskId, expectedRevision, changes using taskSchema fields. "
        "status=cancelled stops a task. Changed plans invalidate approval. "
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
        "id, value. Store or rotate an OS keyring credential. Never echo the value or include it "
        "in notes. Prefer protected UI entry when the value has not already been supplied."
    ),
    "backup": (
        "No parameters. Returns filename, base64 content and sha256 for a consistent SQLite "
        "backup of ALL organizations. Credentials stay in the OS keyring."
    ),
    "memory": "Employee-only body. Stores the current employee's working memory.",
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
