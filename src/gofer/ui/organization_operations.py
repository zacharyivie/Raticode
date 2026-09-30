"""Organization scheduling, evidence and read-only operating diagnostics."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, Field, model_validator

from gofer.ui.organization_store import OrganizationConfig, Task, now
from gofer.utils.atomic_output import open_binary_input


class Routine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(min_length=1, max_length=200)
    task: dict[str, Any]
    cron: str | None = None
    timezone: str = "UTC"
    intervalSeconds: int = Field(default=0, ge=0, le=31_536_000)
    overlap: str = "skip"
    missed: str = "coalesce"
    enabled: bool = True
    nextAt: str | None = None
    lastTaskId: str | None = None
    lastOccurrence: str | None = None
    revision: int = 1

    @model_validator(mode="after")
    def validate_schedule(self) -> Routine:
        ZoneInfo(self.timezone)
        if self.cron:
            CronTrigger.from_crontab(self.cron, timezone=self.timezone)
        if self.cron and self.intervalSeconds:
            raise ValueError("Choose cron or interval")
        if self.intervalSeconds and self.intervalSeconds < 60:
            raise ValueError("Routine interval must be at least 60 seconds")
        if self.overlap not in {"skip", "queue"} or self.missed not in {"skip", "coalesce"}:
            raise ValueError("Invalid overlap or missed-run policy")
        Task.model_validate(self.task)
        return self


def next_occurrence(routine: dict[str, Any], at: datetime) -> str | None:
    if routine.get("cron"):
        result = CronTrigger.from_crontab(
            routine["cron"], timezone=routine["timezone"]
        ).get_next_fire_time(None, at + timedelta(seconds=1))
        return result.astimezone(UTC).isoformat() if result else None
    if routine.get("intervalSeconds"):
        return (at + timedelta(seconds=routine["intervalSeconds"])).isoformat()
    return None


def trigger_routine(
    org: dict[str, Any], routine: dict[str, Any], occurrence: str
) -> dict[str, Any]:
    tasks = org["runtime"]["tasks"]
    receipts = org["runtime"].setdefault("occurrences", {})
    receipt_id = f"{routine['id']}:{occurrence}"
    if receipt_id in receipts:
        receipt = receipts[receipt_id]
        return dict(next((t for t in tasks if t["id"] == receipt.get("taskId")), receipt))
    existing = next(
        (
            t
            for t in tasks
            if t.get("occurrenceId") == occurrence and t.get("routineId") == routine["id"]
        ),
        None,
    )
    if existing:
        return dict(existing)
    pending = [
        t
        for t in tasks
        if t.get("routineId") == routine["id"] and t["status"] not in {"done", "cancelled"}
    ]
    if pending and routine["overlap"] == "skip":
        routine["lastOccurrence"] = occurrence
        receipt = {
            "id": receipt_id,
            "routineId": routine["id"],
            "at": now(),
            "skipped": True,
            "reason": "Previous occurrence is still open",
        }
        receipts[receipt_id] = receipt
        return receipt
    draft = {
        k: v
        for k, v in routine["task"].items()
        if k not in {"id", "claim", "comments", "turns", "approvedRevision", "acceptedRevision"}
    }
    draft.update(status="todo", routineId=routine["id"], occurrenceId=occurrence)
    if pending:
        draft["dependsOn"] = list(set(draft.get("dependsOn", [])) | {pending[-1]["id"]})
    task = Task.model_validate(draft).model_dump()
    tasks.append(task)
    receipts[receipt_id] = {
        "id": receipt_id,
        "taskId": task["id"],
        "routineId": routine["id"],
        "at": now(),
    }
    routine.update(lastTaskId=task["id"], lastOccurrence=occurrence)
    return task


def schedule_routines(org: dict[str, Any], at: datetime) -> list[dict[str, Any]]:
    receipts = []
    for routine in org["runtime"].get("routines", {}).values():
        due = routine.get("nextAt")
        if not routine["enabled"] or not due or datetime.fromisoformat(due) > at:
            continue
        late = (at - datetime.fromisoformat(due)).total_seconds() > 60
        if not (late and routine["missed"] == "skip"):
            receipts.append(trigger_routine(org, routine, due))
        routine["nextAt"] = next_occurrence(routine, at)
        routine["lastOccurrence"] = due
    return receipts


def workspace(org: dict[str, Any], task: dict[str, Any]) -> str:
    initiative: dict[str, Any] = next(
        (p for p in org["config"]["projects"] if p["id"] == task["project"]), {}
    )
    roots = org["config"].get("projectRoots", [])
    return (
        task.get("workspacePath") or initiative.get("workspacePath") or (roots[0] if roots else "")
    )


def file_evidence(org: dict[str, Any], path: str, employee_id: str | None = None) -> dict[str, Any]:
    source, content = _read_evidence(org, path, employee_id)
    return {"path": str(source), "sha256": hashlib.sha256(content).hexdigest()}


def _read_evidence(
    org: dict[str, Any], path: str, employee_id: str | None = None
) -> tuple[Path, bytes]:
    source = Path(path).resolve()
    roots = [Path(r).resolve() for r in org["config"]["projectRoots"]]
    if employee_id:
        employee = next((e for e in org["config"]["employees"] if e["id"] == employee_id), None)
        if employee is None:
            raise ValueError("Evidence owner is no longer an employee")
        if employee.get("workspacePaths") is not None:
            roots = [root for root in roots if str(root) in employee["workspacePaths"]]
    if not any(source.is_relative_to(root) for root in roots):
        raise ValueError("Evidence file must be inside an allowed organization workspace")
    if not source.is_file() or source.stat().st_size > 10_000_000:
        raise ValueError("Evidence must be an existing file no larger than 10 MB")
    try:
        with open_binary_input(source) as stream:
            content = stream.read(10_000_001)
    except OSError as exc:
        raise ValueError("Evidence file changed or cannot be read safely") from exc
    if len(content) > 10_000_000:
        raise ValueError("Evidence must be an existing file no larger than 10 MB")
    return source, content


def task_reason(org: dict[str, Any], task: dict[str, Any], at: datetime | None = None) -> str:
    at = at or datetime.now(UTC)
    config, runtime = org["config"], org["runtime"]
    if task["status"] in {"done", "cancelled"}:
        return str(task["status"])
    if task["claim"]:
        return "Running"
    if any(
        a.get("executionHandle")
        and not a["executionHandle"].get("settled")
        and a["status"] != "running"
        for a in runtime.get("attempts", {}).values()
    ):
        return "Waiting for delegated execution stop confirmation"
    if task["status"] == "in_review":
        return "Waiting for independent review"
    if task["approvalRequired"] and task["approvedRevision"] is None:
        return "Waiting for plan approval"
    done = {t["id"] for t in runtime["tasks"] if t["status"] == "done"}
    if set(task.get("dependsOn", [])) - done:
        return "Waiting for dependencies"
    if task["status"] != "todo":
        return str(task.get("blockReason") or task["status"].replace("_", " "))
    employee = next((e for e in config["employees"] if e["id"] == task["assignee"]), None)
    if not employee:
        return "Assign an employee"
    if employee["paused"]:
        return "Employee is held"
    if task["dueAt"] and datetime.fromisoformat(task["dueAt"]) > at:
        return "Scheduled for " + str(task["dueAt"])
    if task["turns"] >= config["maxTaskTurns"]:
        return "Task turn limit reached"
    root = workspace(org, task)
    if not root:
        return "Assign an owned workspace"
    if employee.get("workspacePaths") is not None and root not in employee["workspacePaths"]:
        return "Workspace is outside employee grants"
    accounts = runtime.get("usage", {}).get(at.strftime("%Y-%m"), {})
    initiative: dict[str, Any] = next(
        (p for p in config["projects"] if p["id"] == task["project"]), {}
    )
    for name, policy in (
        ("company", config),
        (f"employee:{employee['id']}", employee),
        (f"initiative:{task['project']}", initiative),
    ):
        account = accounts.get(name, {})
        if policy.get("monthlyTurnLimit") and account.get("turns", 0) >= policy["monthlyTurnLimit"]:
            return f"Monthly turn limit: {name}"
        if policy.get("monthlyBudgetUsd"):
            if account.get("unknownCostTurns"):
                return f"Cost coverage unavailable: {name}"
            if account.get("costUsd", 0) >= policy["monthlyBudgetUsd"]:
                return f"Monthly budget reached: {name}"
    if runtime["state"] != "running":
        return "Organization is paused"
    return "Ready"


def diagnostics(org: dict[str, Any]) -> dict[str, Any]:
    tasks = org["runtime"]["tasks"]
    reasons = {t["id"]: task_reason(org, t) for t in tasks}
    warnings = []
    accounts = org["runtime"].get("usage", {}).get(datetime.now(UTC).strftime("%Y-%m"), {})
    policies = {
        "company": org["config"],
        **{f"employee:{e['id']}": e for e in org["config"]["employees"]},
        **{f"initiative:{p['id']}": p for p in org["config"]["projects"]},
    }
    for key, policy in policies.items():
        account = accounts.get(key, {})
        budget = policy.get("monthlyBudgetUsd", 0)
        if (
            budget
            and account.get("costUsd", 0)
            >= budget * org["config"].get("budgetWarningPercent", 80) / 100
        ):
            warnings.append({"scope": key, "reason": "Budget warning threshold reached"})
        if account.get("unknownCostTurns"):
            warnings.append({"scope": key, "reason": "Some turns have unknown cost"})
    return {
        "configuredConcurrency": org["config"]["maxConcurrency"],
        "effectiveConcurrency": min(8, org["config"]["maxConcurrency"]),
        "serviceConcurrency": 8,
        "taskReasons": reasons,
        "budgetWarnings": warnings,
        "attention": [
            t["id"]
            for t in tasks
            if t["status"] in {"blocked", "in_review"}
            or (
                t["status"] not in {"done", "cancelled"}
                and t["approvalRequired"]
                and t["approvedRevision"] is None
            )
        ],
        "delegation": (
            "Employee and delegated tasks share turn reservations and cancellation lineage. "
            "Use owner-granted destinations for swarm, paired desktop or HTTPS gateway work."
        ),
        "isolation": (
            "Workspace grants restrict managed tools; "
            "provider shell permissions govern filesystem access."
        ),
    }


def outcomes(org: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for employee in org["config"]["employees"]:
        tasks = [t for t in org["runtime"]["tasks"] if t["assignee"] == employee["id"]]
        accepted = [
            t for t in tasks if t.get("acceptedRevision") is not None and t["status"] == "done"
        ]
        attempts = [
            a
            for a in org["runtime"].get("attempts", {}).values()
            if a["employeeId"] == employee["id"]
        ]
        costs = [a.get("costUsd") for a in attempts]
        known = [c for c in costs if c is not None]
        accepted_ids = {t["id"] for t in accepted}
        all_attempts = list(org["runtime"].get("attempts", {}).values())
        while True:
            parent_runs = {a["id"] for a in all_attempts if a["taskId"] in accepted_ids}
            children = {
                t["id"]
                for t in org["runtime"]["tasks"]
                if t.get("parentId") in accepted_ids or t.get("parentRunId") in parent_runs
            } - accepted_ids
            if not children:
                break
            accepted_ids |= children
        outcome_costs = [a.get("costUsd") for a in all_attempts if a["taskId"] in accepted_ids]
        outcome_known = [c for c in outcome_costs if c is not None]
        waits = [
            max(
                0,
                (
                    datetime.fromisoformat(t["acceptedAt"])
                    - datetime.fromisoformat(t["reviewRequestedAt"])
                ).total_seconds(),
            )
            for t in accepted
            if t.get("acceptedAt") and t.get("reviewRequestedAt")
        ]
        rows.append(
            {
                "employeeId": employee["id"],
                "humanWaitSeconds": sum(waits),
                "reviewedAccepted": len(accepted),
                "attempts": len(attempts),
                "rework": sum(c.get("decision") == "revise" for t in tasks for c in t["comments"]),
                "knownCostUsd": sum(known),
                "unknownCostAttempts": len(costs) - len(known),
                "acceptedOutcomeKnownCostUsd": sum(outcome_known),
                "acceptedOutcomeUnknownCostAttempts": len(outcome_costs) - len(outcome_known),
                "costPerAcceptedOutcome": sum(outcome_known) / len(accepted)
                if accepted and outcome_costs and len(outcome_costs) == len(outcome_known)
                else None,
                "runSeconds": sum(a.get("elapsedSeconds", 0) for a in attempts),
            }
        )
    goals = []
    for goal in org["config"].get("goalRecords", []):
        goal_ids = {goal["id"]}
        while True:
            children = {
                g["id"]
                for g in org["config"].get("goalRecords", [])
                if g.get("parentId") in goal_ids
            } - goal_ids
            if not children:
                break
            goal_ids |= children
        linked = [t for t in org["runtime"]["tasks"] if t.get("goalId") in goal_ids]
        goals.append(
            {
                **goal,
                "tasks": len(linked),
                "accepted": sum(
                    t["status"] == "done" and t.get("acceptedRevision") is not None for t in linked
                ),
                "blocked": sum(t["status"] == "blocked" for t in linked),
            }
        )
    cohort: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for task in org["runtime"]["tasks"]:
        if task["status"] != "done" or task.get("acceptedRevision") is None:
            continue
        cohort.setdefault((task.get("goalId") or "", task.get("project") or ""), []).append(task)
    suggestions = []
    for (goal_id, project), tasks in cohort.items():
        measures = []
        for employee in org["config"]["employees"]:
            accepted_tasks = [t for t in tasks if t["assignee"] == employee["id"]]
            if len(accepted_tasks) < 3:
                continue
            rework = sum(
                c.get("decision") == "revise" for t in accepted_tasks for c in t["comments"]
            )
            measures.append(
                {
                    "employeeId": employee["id"],
                    "accepted": len(accepted_tasks),
                    "reworkPerAccepted": rework / len(accepted_tasks),
                }
            )
        if len(measures) > 1:
            measures.sort(key=lambda m: m["reworkPerAccepted"])
            suggestions.append(
                {
                    "goalId": goal_id,
                    "project": project,
                    "candidate": measures[0]["employeeId"],
                    "evidence": measures,
                    "reason": (
                        "Lowest observed rework within this goal/initiative. "
                        "Task difficulty is unmeasured; "
                        "test a small cohort before changing routing."
                    ),
                }
            )
    experiments = []
    for experiment in org["runtime"].get("experiments", {}).values():
        selected = [t for t in org["runtime"]["tasks"] if t["id"] in experiment["taskIds"]]
        experiments.append(
            {
                **experiment,
                "accepted": sum(
                    t["status"] == "done" and t.get("acceptedRevision") is not None
                    for t in selected
                ),
                "rework": sum(
                    c.get("decision") == "revise" for t in selected for c in t["comments"]
                ),
            }
        )
    return {
        "experiments": experiments,
        "suggestions": suggestions,
        "employees": rows,
        "goals": goals,
        "routingAdvice": (
            "Compare reviewed work of similar difficulty. Cost and task counts alone "
            "do not measure quality; changes require a human-selected cohort."
        ),
    }


def rehearse(org: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    at = datetime.fromisoformat(params.get("at") or now())
    if at.tzinfo is None:
        raise ValueError("Rehearsal time needs a timezone")
    draft = copy.deepcopy(org)
    if params.get("config"):
        draft["config"] = OrganizationConfig.model_validate(params["config"]).model_dump()
    draft["runtime"]["state"] = "running"
    durations = [
        a["elapsedSeconds"]
        for a in org["runtime"].get("attempts", {}).values()
        if a.get("elapsedSeconds") and a.get("status") == "completed"
    ]
    duration = float(
        params.get("assumedDurationSeconds", sum(durations) / len(durations) if durations else 60)
    )
    horizon = int(params.get("horizonSeconds", 3600))
    if not 1 <= duration <= 86400 or not 1 <= horizon <= 604800:
        raise ValueError("Use duration 1..86400 seconds and horizon 1..604800 seconds")
    arrivals = params.get("arrivals", [])
    if not isinstance(arrivals, list) or len(arrivals) > 100:
        raise ValueError("Provide at most 100 simulated task arrivals")
    for arrival in arrivals:
        task = Task.model_validate(arrival).model_dump()
        draft["runtime"]["tasks"].append(task)
    # A fake clock produces real cron occurrences in the isolated copy only.
    until = at + timedelta(seconds=horizon)
    occurrences = 0
    for routine in draft["runtime"].get("routines", {}).values():
        due = routine.get("nextAt")
        while (
            routine["enabled"]
            and due
            and datetime.fromisoformat(due) <= until
            and occurrences < 500
        ):
            trigger_routine(draft, routine, "rehearsal:" + due)
            if routine.get("lastTaskId"):
                created = next(
                    t for t in draft["runtime"]["tasks"] if t["id"] == routine["lastTaskId"]
                )
                created["dueAt"] = due
            occurrences += 1
            due = next_occurrence(routine, max(at, datetime.fromisoformat(due)))
    concurrency = min(8, draft["config"]["maxConcurrency"])
    slots = [0.0] * concurrency
    roots: dict[str, float] = {}
    employees: dict[str, float] = {}
    finished = {
        t["id"]: 0.0 for t in draft["runtime"]["tasks"] if t["status"] == "done" and not t["claim"]
    }
    result = []
    pending = sorted(
        draft["runtime"]["tasks"],
        key=lambda t: {"critical": 0, "high": 1, "medium": 2, "low": 3}[t["priority"]],
    )
    while pending:
        progress = False
        for task in list(pending):
            dependencies = task.get("dependsOn", [])
            if any(dep not in finished for dep in dependencies):
                continue
            if task.get("blockReason") == "dependencies":
                task.update(status="todo", blockReason="")
            due_at = datetime.fromisoformat(task["dueAt"]) if task["dueAt"] else at
            candidate_time = max(at, due_at)
            reason = task_reason(draft, task, candidate_time)
            row: dict[str, Any] = {"taskId": task["id"], "title": task["title"], "reason": reason}
            if reason == "Ready":
                slot = min(range(concurrency), key=lambda i: slots[i])
                root = workspace(draft, task)
                start = max(
                    slots[slot],
                    roots.get(root, 0),
                    employees.get(task["assignee"], 0),
                    (candidate_time - at).total_seconds(),
                    max((finished[d] for d in dependencies), default=0),
                )
                if start > horizon:
                    row["reason"] = "Outside rehearsal horizon"
                else:
                    row.update(startSeconds=start, finishSeconds=start + duration)
                    slots[slot] = roots[root] = employees[task["assignee"]] = start + duration
                    task["status"] = "in_review" if task.get("reviewRequired") else "done"
                    if task["status"] == "done":
                        finished[task["id"]] = start + duration
                    month = candidate_time.strftime("%Y-%m")
                    accounts = draft["runtime"].setdefault("usage", {}).setdefault(month, {})
                    for key in (
                        "company",
                        f"employee:{task['assignee']}",
                        f"initiative:{task['project']}",
                    ):
                        account = accounts.setdefault(key, {})
                        account["turns"] = account.get("turns", 0) + 1
            elif reason == "Running":
                root = workspace(draft, task)
                roots[root] = duration
                employees[task["assignee"]] = duration
            result.append(row)
            pending.remove(task)
            progress = True
        if not progress:
            result.extend(
                {
                    "taskId": t["id"],
                    "title": t["title"],
                    "reason": "Waiting for dependencies or their review",
                }
                for t in pending
            )
            break
    return {
        "at": at.isoformat(),
        "tasks": result,
        "assumedDurationSeconds": duration,
        "historicalSamples": len(durations),
        "horizonSeconds": horizon,
        "simulatedOccurrences": occurrences,
        "assumptions": [
            "No provider calls or workflow execution",
            "Unreviewed completion is assumed after the stated duration; "
            "independent review remains pending",
            "Shared workspace and employee turns serialize; monthly turn reservations apply",
            "Known budget exhaustion blocks work; "
            "future dollar costs and external work are not forecast",
            "At most 500 routine occurrences and 100 supplied arrivals are simulated",
        ],
        "heartbeatTurnsPerDay": sum(
            86400 / e["heartbeatSeconds"]
            for e in draft["config"]["employees"]
            if e["heartbeatSeconds"] and not e["paused"]
        ),
    }


def workflow_preview(org: dict[str, Any], task: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    from gofer.rattish.artifacts import compile_rattish_source
    from gofer.rattish.preflight import run_preflight
    from gofer.rattish.runtime import _prepare_workflow_inputs
    from gofer.ui.organization_execution import required_resources

    contract = task.get("workflowContract") or {}
    path, content = _read_evidence(org, str(contract.get("path", "")), task["assignee"])
    evidence = {"path": str(path), "sha256": hashlib.sha256(content).hexdigest()}
    if path.name != "workflow.rattish":
        raise ValueError("Select a workflow.rattish file")
    compiled = compile_rattish_source(
        content.decode("utf-8"), path, data_dir=data_dir, project_root=Path(workspace(org, task))
    )
    _prepare_workflow_inputs(compiled.ir, contract.get("inputs", {}))
    preflight = run_preflight(compiled.ir, data_dir=data_dir)
    return {
        **evidence,
        "preflightReady": preflight.ready,
        "preflightDiagnostics": [d.message for d in preflight.diagnostics],
        "irSha256": hashlib.sha256(json.dumps(compiled.ir, sort_keys=True).encode()).hexdigest(),
        "inputs": contract.get("inputs", {}),
        "allowedResources": contract.get("allowedResources", []),
        "requiredResources": required_resources(compiled.ir, data_dir),
        "completionChecks": contract.get("completionChecks", []),
        "validated": True,
        "executed": False,
        "authorization": (
            "Use workflow_launch with this IR hash and task revision to authorize execution."
        ),
    }


READ_OPERATIONS = {
    "diagnostics",
    "tasks",
    "attempts",
    "routines",
    "outcomes",
    "rehearse",
    "workflow_preview",
}
WRITE_OPERATIONS = {
    "routine_save",
    "routine_trigger",
    "memory_save",
    "memory_check",
    "artifact_add",
    "resume_all",
    "secret_reference",
    "routing_experiment",
}


def operation(
    manager: Any,
    org: dict[str, Any],
    action: str,
    params: dict[str, Any],
    actor: str,
    employee_id: str | None,
    run_id: str | None = None,
) -> Any:
    """Called only after the manager checks the bound organization and turn capability."""
    if action == "diagnostics":
        return diagnostics(org)
    if action == "outcomes":
        return outcomes(org)
    if action == "rehearse":
        return rehearse(org, params)
    if action in {"tasks", "attempts", "routines"}:
        items = (
            org["runtime"]["tasks"]
            if action == "tasks"
            else list(org["runtime"].get(action, {}).values())
        )
        if action == "tasks":
            query = str(params.get("query", "")).casefold()
            attention = set(diagnostics(org)["attention"])
            items = [
                t
                for t in items
                if (not query or query in (t["title"] + "\n" + t["description"]).casefold())
                and all(
                    not params.get(k) or t.get(k) == params[k]
                    for k in ("assignee", "status", "project", "goalId")
                )
                and (not params.get("attention") or t["id"] in attention)
            ]
        elif params.get("taskId"):
            items = [i for i in items if i.get("taskId") == params["taskId"]]
        offset, limit = int(params.get("offset", 0)), int(params.get("limit", 50))
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("Use offset >= 0 and limit between 1 and 100")
        return {
            "items": items[offset : offset + limit],
            "total": len(items),
            "nextOffset": offset + limit if offset + limit < len(items) else None,
        }
    if action == "workflow_preview":
        task = next((t for t in org["runtime"]["tasks"] if t["id"] == params.get("taskId")), None)
        if not task:
            raise ValueError("Task not found")
        return workflow_preview(org, task, manager.data_dir)
    if employee_id and action in {
        "routine_save",
        "routine_trigger",
        "resume_all",
        "secret_reference",
        "routing_experiment",
    }:
        raise ValueError("Only the user or Rem can change organization operating policy")
    if action == "resume_all":
        if params.get("expectedRevision") != org["revision"]:
            raise ValueError("Configuration changed. Reload before resuming employees")
        config = copy.deepcopy(org["config"])
        for employee in config["employees"]:
            employee["paused"] = False
        return manager.store.configure(
            org["projectRoot"],
            org["id"],
            config,
            org["revision"],
            actor,
            "Explicitly resume all employees",
        )
    prepared: dict[str, Any] = {}
    if action == "routine_save":
        routine = Routine.model_validate(params.get("routine", {})).model_dump()
        task = Task.model_validate(routine["task"]).model_dump()
        manager.store.validate_task(task, org["config"], org["runtime"]["tasks"])
        manager._validate_workspace(org, task)
        routine["nextAt"] = next_occurrence(routine, datetime.now(UTC))
        prepared = routine
    elif action in {"memory_save", "artifact_add"}:
        prepared = file_evidence(org, str(params.get("path", "")), employee_id)
    result: dict[str, Any] = {}

    def mutate(current: dict[str, Any]) -> dict[str, Any]:
        runtime = current["runtime"]
        if run_id is not None and not any(t["claim"] == run_id for t in runtime["tasks"]):
            raise ValueError("Employee turn was revoked")
        if employee_id and current["runtime"]["generation"] != org["runtime"]["generation"]:
            raise ValueError("Employee turn was revoked")
        if action == "routine_save":
            previous = runtime["routines"].get(prepared["id"])
            if previous and previous["revision"] != params.get("expectedRevision"):
                raise ValueError("Routine changed. Reload before saving")
            prepared["revision"] = previous["revision"] + 1 if previous else 1
            runtime["routines"][prepared["id"]] = prepared
            result.update(prepared)
        elif action == "routine_trigger":
            routine = runtime["routines"].get(params.get("routineId"))
            if not routine or not routine["enabled"]:
                raise ValueError("Choose an enabled routine")
            key = params.get("idempotencyKey")
            if not isinstance(key, str) or not 1 <= len(key) <= 200:
                raise ValueError("API triggers require an idempotencyKey")
            result.update(trigger_routine(current, routine, "api:" + key))
        elif action == "memory_save":
            owner = employee_id or params.get("employeeId")
            if owner not in {e["id"] for e in current["config"]["employees"]}:
                raise ValueError("Choose an employee")
            body = params.get("body")
            if not isinstance(body, str) or not body.strip() or len(body) > 100_000:
                raise ValueError("Memory needs bounded text")
            expires = params.get("expiresAt")
            if expires and datetime.fromisoformat(expires).tzinfo is None:
                raise ValueError("Memory expiry needs a timezone")
            mid = str(uuid4())
            entry = {
                "id": mid,
                "employeeId": owner,
                "body": body,
                "source": prepared,
                "verifiedAt": now(),
                "expiresAt": expires,
                "stale": False,
                "author": actor,
            }
            runtime["memories"][mid] = entry
            result.update(entry)
        elif action == "memory_check":
            stale = []
            for memory in runtime["memories"].values():
                if employee_id and memory["employeeId"] != employee_id:
                    continue
                try:
                    evidence = file_evidence(
                        current, memory["source"]["path"], memory["employeeId"]
                    )
                    changed = evidence["sha256"] != memory["source"]["sha256"]
                except (OSError, ValueError):
                    changed = True
                expired = memory.get("expiresAt") and datetime.fromisoformat(
                    memory["expiresAt"]
                ) <= datetime.now(UTC)
                if changed or expired:
                    memory["stale"] = True
                    if not memory.get("recheckTaskId"):
                        task = Task(
                            title="Recheck memory evidence",
                            description=memory["body"] + "\nSource: " + memory["source"]["path"],
                            assignee=memory["employeeId"],
                            status="backlog",
                        ).model_dump()
                        runtime["tasks"].append(task)
                        memory["recheckTaskId"] = task["id"]
                    stale.append(memory["id"])
            result.update(stale=stale)
        elif action == "artifact_add":
            selected_task = next(
                (t for t in runtime["tasks"] if t["id"] == params.get("taskId")), None
            )
            if not selected_task or (
                employee_id and not manager._owns(current, employee_id, selected_task)
            ):
                raise ValueError("Choose a task you own")
            task = selected_task
            attempt = runtime["attempts"].get(params.get("attemptId"))
            if not attempt or attempt["taskId"] != task["id"]:
                raise ValueError("Artifact must reference this task's attempt")
            if len(task["artifacts"]) >= 100:
                raise ValueError("Task artifact limit reached")
            artifact = {
                "id": str(uuid4()),
                **prepared,
                "attemptId": attempt["id"],
                "kind": params.get("kind", "file"),
                "at": now(),
                "author": actor,
                "check": params.get("check"),
            }
            if artifact["kind"] not in {"file", "diff", "test", "document"}:
                raise ValueError("Artifact kind must be file, diff, test or document")
            task["artifacts"].append(artifact)
            task["acceptedRevision"] = None
            if task["status"] == "done":
                task["status"] = "in_review"
            task["revision"] += 1
            result.update(artifact)
        elif action == "routing_experiment":
            ids = params.get("taskIds")
            if not isinstance(ids, list) or not 1 <= len(ids) <= 10 or len(set(ids)) != len(ids):
                raise ValueError("Choose 1 to 10 unique pending tasks for an experiment")
            selected = [t for t in runtime["tasks"] if t["id"] in ids]
            if len(selected) != len(ids) or any(
                t["claim"] or t["status"] not in {"todo", "backlog"} for t in selected
            ):
                raise ValueError("Routing experiments require pending, unclaimed tasks")
            if not str(params.get("reason", "")).strip():
                raise ValueError("Record the experiment's hypothesis")
            experiment = {
                "id": str(uuid4()),
                "taskIds": ids,
                "employeeId": params.get("employeeId"),
                "reason": params["reason"],
                "at": now(),
                "actor": actor,
                "previousAssignees": {t["id"]: t["assignee"] for t in selected},
            }
            for task in selected:
                candidate = Task.model_validate(
                    {
                        **task,
                        "assignee": params.get("employeeId"),
                        "approvedRevision": None,
                        "acceptedRevision": None,
                        "reviewRequired": True,
                        "planRevision": task["planRevision"] + 1,
                        "revision": task["revision"] + 1,
                    }
                ).model_dump()
                manager.store.validate_task(candidate, current["config"], runtime["tasks"])
                manager._validate_workspace(current, candidate)
                task.update(candidate)
            runtime.setdefault("experiments", {})[experiment["id"]] = experiment
            result.update(experiment)
        elif action == "secret_reference":
            # Reference metadata only. Credentials stay in the OS-protected store.
            sid, account = params.get("id"), params.get("account")
            if not isinstance(sid, str) or not sid or not isinstance(account, str) or not account:
                raise ValueError("Secret reference needs id and OS keyring account")
            previous = runtime["secrets"].get(sid, {})
            entry = {
                "id": sid,
                "account": account,
                "version": previous.get("version", 0) + 1,
                "revoked": bool(params.get("revoked", False)),
                "at": now(),
            }
            runtime["secrets"][sid] = entry
            affected = {
                e["id"] for e in current["config"]["employees"] if sid in e.get("secretRefs", [])
            }
            for task in runtime["tasks"]:
                if task["claim"] and task["assignee"] in affected:
                    claim_id = task["claim"]
                    task.update(
                        claim=None,
                        status="blocked",
                        blockReason="Secret reference changed; review before retrying",
                        revision=task["revision"] + 1,
                    )
                    if claim_id in manager._active:
                        manager._active[claim_id][1].set()
            result.update(entry)
        else:
            raise ValueError("Unknown operation")
        return {"id": result.get("id"), "taskId": result.get("taskId"), "action": action}

    manager.store.mutate(org["projectRoot"], org["id"], actor, action, mutate)
    manager._wake.set()
    return result


def resolve_secrets(
    org: dict[str, Any], employee: dict[str, Any], resources: dict[str, Any], store: Any = None
) -> list[str]:
    """Resolve only explicitly granted references in MCP environment values."""
    from gofer.devices.storage import OSSecretStore

    resolved = []
    for server in resources.get("mcpServers", []):
        for key, value in server.get("env", {}).items():
            if not isinstance(value, str) or not value.startswith("secret://"):
                continue
            reference = value.removeprefix("secret://")
            record = org["runtime"].get("secrets", {}).get(reference)
            if reference not in employee.get("secretRefs", []) or not record or record["revoked"]:
                raise ValueError(
                    "MCP secret reference is missing, revoked or outside employee grants"
                )
            if store is None:
                store = OSSecretStore(service="Raticode organization secrets")
            secret = store.get(record["account"])
            if not secret:
                raise ValueError("Referenced secret is unavailable in the protected store")
            server["env"][key] = secret
            resolved.append(secret)
    return resolved
