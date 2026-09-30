"""Persistent Rem employees with bounded wakeups and capability-scoped tools."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import logging
import math
import re
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
from gofer.ui.organization_execution import (
    OrganizationExecution,
    authorize_workflow,
    reservation,
    target_for,
    validate_execution,
)
from gofer.ui.organization_operations import (
    READ_OPERATIONS,
    WRITE_OPERATIONS,
    diagnostics,
    operation,
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
    OrganizationConfig,
    OrganizationConflict,
    OrganizationStore,
    Task,
    config_diff,
    now,
    project_identity,
)
from gofer.ui.organization_tools import READ_ACTIONS, TASK_EDIT_FIELDS, TOOL, management_help
from gofer.ui.swarm_tools import SwarmToolServer
from gofer.utils.atomic_output import atomic_binary_output, open_binary_input

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
- secret_store: user/Rem-only id and value; stores credentials in the OS keyring, never history.
- backup: user/Rem-only consistent SQLite backup of every organization and operational record.
- memory: employee-only params.body stores working memory, separate from configuration.
- wake: params.employeeId and message. Creates an assigned task; runs only if company is running.
Employees can read their company and coordinate tasks, but cannot configure, restore,
import, create companies, or start/stop the company. They may delegate work to
existing employees. Employee task updates require ownership or a reporting-line manager.
Do not edit organization storage through shell or filesystem. Use only these tools.
"""
CONFIG_ACTIONS = {"create", "configure", "restore", "import", "control", "import_github"}


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
        org["runtime"]["state"] = "paused"
        for task in org["runtime"]["tasks"]:
            if task["claim"]:
                attempt = org["runtime"].get("attempts", {}).get(task["claim"])
                if attempt:
                    attempt.update(status="interrupted", finishedAt=now())
                task.update(claim=None, status="blocked", revision=task["revision"] + 1)
                task["comments"].append(
                    {
                        "actor": "system",
                        "at": now(),
                        "body": "Raticode restarted. Review interrupted work before retrying.",
                    }
                )
        return {"reason": "Runtime started paused; interrupted work requires review"}

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

    def cancel(self, oid: str) -> None:
        with self._lock:
            for organization, cancel, _ in self._active.values():
                if organization == oid:
                    cancel.set()

    @staticmethod
    def public(org: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(org)
        files = result["config"].pop("packageFiles", {})
        result["runtime"].pop("requests", None)
        result["diagnostics"] = diagnostics(org)
        result["version"] = hashlib.sha256(json.dumps(org, sort_keys=True).encode()).hexdigest()
        result["packageFileCount"] = len(files)
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
                "Launch Raticode with RATICODE_EXPERIMENTAL_ORGANIZATIONS=1 to opt in."
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
        if action in READ_OPERATIONS | WRITE_OPERATIONS:
            return operation(self, org, action, params, actor, employee_id, run_id)
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
            return self.store.events(project, oid, int(params.get("after", 0)))
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
                valid_claims = {t["claim"] for t in result["runtime"]["tasks"] if t["claim"]}
                for claim_id, (active_oid, cancel, _) in self._active.items():
                    if active_oid == oid and claim_id not in valid_claims:
                        cancel.set()
                return self.public(result)
        if action == "control":
            state = params.get("state")
            if state not in {"running", "paused"}:
                raise ValueError("State must be running or paused")

            with self._lock:
                result = self.store.control(project, oid, actor, state)
                if state == "paused":
                    self.cancel(oid)
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
                self._validate_workspace(current, task, employee_id)
                validate_execution(current, task)
                self.store.validate_task(task, current["config"], tasks)
                tasks.append(task)
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
                    elif employee_id and not self._owns(current, employee_id, task):
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
                        task.update(
                            status="todo", cancellation=None, blockReason="", acceptedRevision=None
                        )
                    else:
                        changes = params.get("changes", {})
                        if not isinstance(changes, dict) or set(changes) - TASK_EDIT_FIELDS:
                            raise ValueError("Unsupported task fields")
                        changes = {
                            key: value for key, value in changes.items() if task.get(key) != value
                        }
                        if task["claim"] and set(changes) - {"status", "evidence"}:
                            raise OrganizationConflict(
                                "Pause and wait for the turn before editing its plan"
                            )
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
                        candidate = Task.model_validate({**task, **changes}).model_dump()
                        if set(changes) - {"status", "evidence", "artifacts", "blockReason"}:
                            candidate["approvedRevision"] = None
                            candidate["workflowAuthorization"] = None
                            candidate["planRevision"] += 1
                        if set(changes) - {"status", "blockReason"}:
                            candidate["acceptedRevision"] = None
                        if changes.get("status") == "in_review":
                            candidate["reviewRequestedAt"] = now()
                        if changes.get("status") == "cancelled" and task["claim"]:
                            revoked.append(task["claim"])
                            candidate.update(claim=None, cancellation="requested")
                        self._validate_workspace(current, candidate, employee_id)
                        validate_execution(current, candidate)
                        self.store.validate_task(candidate, current["config"], tasks)
                        task.update(candidate)
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
            if request_key and action == "comment":
                result_task.clear()
                result_task.update(id=task["id"], revision=task["revision"], status=task["status"])
            if request_key:
                requests[scoped_key] = {"hash": request_hash, "result": copy.deepcopy(result_task)}
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
        self._wake.set()
        return result_task

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
                lambda name, args: self.call(project, actor, args, **scope), [tool], HELP
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
            else options.get("permission_mode") in {"read-only", "plan"},
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
                        self.executions.reconcile(self.store.get(row["project"], row["id"]))
                    next_reconcile = time.monotonic() + 30
            except Exception:
                log.exception("Organization dispatch failed")
            self._wake.wait(2)
            self._wake.clear()

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
            project, oid = row["project"], row["id"]
            with self._lock:
                # Shared project writes are serialized. Employees can use swarms for isolated work.
                if self._closed.is_set() or len(self._active) >= 8:
                    continue
                chosen: dict[str, Any] = {}

                def claim(org: dict[str, Any]) -> dict[str, Any]:
                    runtime, config = org["runtime"], org["config"]
                    self._unblock(org)
                    for task in runtime["tasks"]:
                        claim_id = task["claim"]
                        if claim_id and (
                            claim_id not in self._active or not self._active[claim_id][2].is_alive()
                        ):
                            task.update(
                                claim=None,
                                status="blocked",
                                blockReason="Worker stopped unexpectedly; review before retrying",
                                revision=task["revision"] + 1,
                            )
                            attempt = runtime.get("attempts", {}).get(claim_id)
                            if attempt:
                                attempt.update(status="interrupted", finishedAt=now())
                            self._active.pop(claim_id, None)
                            self._active_roots.pop(claim_id, None)
                    if (
                        runtime["state"] != "running"
                        or sum(active_oid == oid for active_oid, _, _ in self._active.values())
                        >= config["maxConcurrency"]
                    ):
                        return {}
                    schedule_routines(org, datetime.now(UTC))
                    self._unblock(org)
                    month = datetime.now(UTC).strftime("%Y-%m")
                    usage = runtime.setdefault("usage", {}).setdefault(month, {})

                    def exhausted(policy: dict[str, Any], account: dict[str, Any]) -> bool:
                        return bool(
                            (
                                policy.get("monthlyTurnLimit")
                                and account.get("turns", 0) >= policy["monthlyTurnLimit"]
                            )
                            or (
                                policy.get("monthlyBudgetUsd")
                                and (
                                    account.get("costUsd", 0) >= policy["monthlyBudgetUsd"]
                                    or account.get("unknownCostTurns", 0)
                                )
                            )
                        )

                    if exhausted(config, usage.get("company", {})):
                        runtime["state"] = "paused"
                        runtime["pauseReason"] = (
                            "Monthly limit reached or provider cost is unavailable"
                        )
                        return {"reason": "Monthly limit reached or provider cost is unavailable"}
                    employees = {e["id"]: e for e in config["employees"] if not e["paused"]}
                    employees = {
                        eid: e
                        for eid, e in employees.items()
                        if not exhausted(e, usage.get(f"employee:{eid}", {}))
                    }
                    for eid, employee in employees.items():
                        interval = employee["heartbeatSeconds"]
                        last = runtime["heartbeats"].get(eid)
                        if interval and (
                            not last or datetime.fromisoformat(last) <= datetime.now(UTC)
                        ):
                            if not any(
                                t["assignee"] == eid
                                and (
                                    t["claim"]
                                    or task_reason(org, t) == "Ready"
                                    or (
                                        t["title"] == "Heartbeat: review assignments"
                                        and t["status"] not in {"done", "cancelled"}
                                    )
                                )
                                for t in runtime["tasks"]
                            ):
                                runtime["tasks"].append(
                                    Task(
                                        title="Heartbeat: review assignments",
                                        assignee=eid,
                                        description="Review organization goals and blocked work. "
                                        "Delegate or record the next useful action.",
                                    ).model_dump()
                                )
                            runtime["heartbeats"][eid] = (
                                datetime.now(UTC) + timedelta(seconds=interval)
                            ).isoformat()
                    for task in sorted(
                        runtime["tasks"],
                        key=lambda t: (
                            {"critical": 0, "high": 1, "medium": 2, "low": 3}[t["priority"]]
                            - (
                                datetime.now(UTC) - datetime.fromisoformat(t["createdAt"])
                            ).total_seconds()
                            / 86400
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
                        if task["dueAt"] and datetime.fromisoformat(task["dueAt"]) > datetime.now(
                            UTC
                        ):
                            continue
                        if task["turns"] >= config["maxTaskTurns"]:
                            task.update(status="blocked", evidence="Task turn limit reached")
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
                            task.update(
                                status="blocked",
                                evidence="Assign an owned Raticode project before retrying",
                            )
                            continue
                        grants = employees[task["assignee"]].get("workspacePaths")
                        if grants is not None and working_root not in grants:
                            task.update(
                                status="blocked",
                                blockReason="workspace_grant",
                                evidence="Workspace is outside employee grants",
                            )
                            continue
                        if working_root in self._active_roots.values() or any(
                            t["claim"] and t["assignee"] == task["assignee"]
                            for t in runtime["tasks"]
                        ):
                            continue
                        try:
                            reserved = reservation(org, task)
                            validate_execution(org, task)
                        except ValueError as exc:
                            task.update(status="blocked", blockReason=str(exc))
                            continue
                        if (
                            task.get("execution", {})
                            and task["execution"].get("kind") == "workflow"
                            and not task.get("workflowAuthorization")
                        ):
                            task.update(
                                status="blocked",
                                blockReason="Authorize the workflow preview before launch",
                            )
                            continue
                        if task["turns"] + reserved > config["maxTaskTurns"] or any(
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
                            task.update(
                                status="blocked",
                                blockReason="Insufficient turn capacity for execution reservation",
                            )
                            continue
                        task.update(
                            claim=str(uuid4()),
                            status="in_progress",
                            turns=task["turns"] + reserved,
                            revision=task["revision"] + 1,
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

                # Avoid activity noise on every scheduler tick.
                org = self.store.get(project, oid)
                if org["runtime"]["state"] != "running":
                    continue
                if any(
                    a.get("executionHandle")
                    and not a["executionHandle"].get("settled")
                    and a["id"] not in self._active
                    for a in org["runtime"].get("attempts", {}).values()
                ):
                    continue
                for root in {
                    *(org["config"].get("projectRoots", [])),
                    *(
                        t.get("workspacePath")
                        for t in org["runtime"]["tasks"]
                        if t.get("workspacePath")
                    ),
                    *(
                        p["workspacePath"]
                        for p in org["config"]["projects"]
                        if p.get("workspacePath")
                    ),
                }:
                    project_identity(root)
                instant = datetime.now(UTC)
                runtime = org["runtime"]
                if not (
                    any(
                        (
                            task_reason(org, t, instant) == "Ready"
                            or task_reason(org, t, instant)
                            in {
                                "Monthly turn limit: company",
                                "Monthly budget reached: company",
                                "Cost coverage unavailable: company",
                            }
                        )
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
                    continue
                self.store.mutate(project, oid, "system", "dispatch", claim)
                if not chosen:
                    continue
                cancel = threading.Event()
                turn_id = chosen["task"]["claim"]
                thread = threading.Thread(
                    target=self._run,
                    args=(chosen, cancel),
                    daemon=True,
                    name=f"employee-{turn_id[:8]}",
                )
                self._active[turn_id] = (oid, cancel, thread)
                self._active_roots[turn_id] = chosen["working_root"]
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

            async def execute() -> None:
                nonlocal final, usage, managed_turns
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
                    identity = (
                        f"You are {employee['name']}, a Rem employee of {org['config']['name']}.\n"
                        f"Role: {employee['role']}. Title: {employee['title']}. "
                        f"Manager: {employee['reportsTo'] or 'organization owner'}.\n"
                        f"Organization purpose: {org['config']['description']}\n"
                        f"Organization instructions: {org['config']['instructions']}\n"
                        f"Your instructions: {employee['instructions']}\n"
                        f"Persistent memory: {employee['memory']}\n"
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
                    )
                    workflow = {
                        "id": f"organization:{oid}:{employee['id']}:{task['id']}",
                        "chatThreadId": f"organization:{oid}:{employee['id']}:{task['id']}",
                        "projectRoot": working_root,
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
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]

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
                                working_dir=Path(working_root),
                                cancel_event=cancel,
                            )
                        ) as direct:
                            async for item in direct:
                                yield item

                    async with asyncio.timeout(org["config"]["turnTimeoutSeconds"]):
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
                                    break
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
                                                "workspacePath": event["projectRoot"],
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
                                clean = self._clean(event)
                                with self.store.connect() as db:
                                    self.store.event(
                                        db,
                                        oid,
                                        f"employee:{employee['id']}",
                                        "turn_event",
                                        {"turnId": turn_id, "taskId": task["id"], "event": clean},
                                    )

            asyncio.run(execute())
        except Exception as exc:
            error = self._redact(str(exc)) or type(exc).__name__
        finally:
            final = self._redact(final)

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
                        status="cancelled"
                        if cancel.is_set()
                        else "failed"
                        if error or not final
                        else "completed",
                        error=error,
                        costUsd=cost if known_cost else None,
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
                if failed:
                    existing.update(status="blocked")
                    existing["comments"].append(
                        {
                            "actor": "system",
                            "at": now(),
                            "body": error
                            or "Turn ended without a final response; review before retrying",
                        }
                    )
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
