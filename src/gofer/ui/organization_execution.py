"""Organization-owned execution, with durable handles and reserved provider turns."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import threading
from collections.abc import AsyncGenerator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from gofer.ui.organization_operations import resolve_secrets, workflow_preview
from gofer.ui.organization_store import OrganizationConflict
from gofer.utils.atomic_output import open_binary_input


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def target_for(org: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    spec = task.get("execution") or {}
    target = next(
        (t for t in org["config"].get("executionTargets", []) if t["id"] == spec.get("targetId")),
        None,
    )
    if not target or task["assignee"] not in target["employees"]:
        raise ValueError("Execution target is missing or outside employee grants")
    return dict(target)


def reservation(org: dict[str, Any], task: dict[str, Any]) -> int:
    spec = task.get("execution")
    if not spec:
        return 1
    if spec.get("kind") == "workflow":
        limit = (task.get("workflowContract") or {}).get("turnLimit", 1)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Workflow turnLimit must be between 1 and 1000")
        return limit
    return int(target_for(org, task)["turnLimit"])


def validate_execution(org: dict[str, Any], task: dict[str, Any]) -> None:
    spec = task.get("execution")
    if spec is None:
        return
    if spec.get("kind") == "workflow":
        if set(spec) != {"kind"} or not task.get("workflowContract"):
            raise ValueError("Workflow execution requires a contract")
    elif set(spec) != {"targetId"}:
        raise ValueError("Execution must name an owner-granted targetId")
    else:
        target_for(org, task)
    employee = next(e for e in org["config"]["employees"] if e["id"] == task["assignee"])
    if employee["permissionMode"] in {"read-only", "plan"}:
        raise ValueError("Read-only employees cannot launch delegated execution")
    reservation(org, task)


def workflow_digest(preview: dict[str, Any], task: dict[str, Any]) -> str:
    return fingerprint([preview["sha256"], preview["irSha256"], task["workflowContract"]])


def authorize_workflow(
    manager: Any, org: dict[str, Any], params: dict[str, Any], actor: str
) -> Any:
    task = next(t for t in org["runtime"]["tasks"] if t["id"] == params.get("taskId"))
    preview = workflow_preview(org, task, manager.data_dir)
    if not preview["preflightReady"]:
        raise ValueError("Workflow preflight must pass before authorization")
    if params.get("irSha256") != preview["irSha256"]:
        raise OrganizationConflict("Workflow changed; validate and review the current preview")
    validate_contract(task["workflowContract"], preview)

    def authorize(current: dict[str, Any]) -> Any:
        current_task = next(t for t in current["runtime"]["tasks"] if t["id"] == task["id"])
        if current_task["revision"] != params.get("expectedRevision") or current_task["claim"]:
            raise OrganizationConflict("Task changed or is running")
        if current_task["status"] in {"done", "cancelled"}:
            raise ValueError("Reopen the task before authorizing another workflow run")
        current_task.update(
            execution={"kind": "workflow"},
            workflowAuthorization=workflow_digest(preview, task),
            status="todo",
            reviewRequired=True,
            revision=current_task["revision"] + 1,
        )
        validate_execution(current, current_task)
        return {"taskId": task["id"], "irSha256": preview["irSha256"], "authorizedBy": actor}

    result = manager.store.mutate(
        org["projectRoot"], org["id"], actor, "workflow_authorized", authorize
    )
    manager._wake.set()
    return manager.public(result)


def validate_contract(contract: dict[str, Any], preview: dict[str, Any]) -> None:
    allowed = contract.get("allowedResources", [])
    if not isinstance(allowed, list) or any(not isinstance(x, str) for x in allowed):
        raise ValueError("allowedResources must be a list of resource identifiers")
    missing = set(preview["requiredResources"]) - set(allowed)
    if missing:
        raise ValueError("Workflow resources need explicit grants: " + ", ".join(sorted(missing)))
    checks = contract.get("completionChecks", [])
    if not isinstance(checks, list) or len(checks) > 100:
        raise ValueError("Supply at most 100 completion checks")
    for check in checks:
        if not isinstance(check, dict) or check.get("kind") not in {"file", "output"}:
            raise ValueError(
                "Completion checks use kind=file with path or kind=output with name/equals"
            )
        if check["kind"] == "file" and (
            not isinstance(check.get("path"), str) or set(check) - {"kind", "path", "sha256"}
        ):
            raise ValueError("Invalid file completion check")
        if check["kind"] == "output" and (
            not isinstance(check.get("name"), str) or set(check) != {"kind", "name", "equals"}
        ):
            raise ValueError("Invalid output completion check")


def required_resources(
    ir: Any, data_dir: Path | None = None, seen: set[str] | None = None
) -> list[str]:
    """Node grants authorize the reviewed compiled configuration, including its bindings."""
    from gofer.rattish.artifacts import compile_rattish_file
    from gofer.rattish.workspaces import find_registered_workflow

    seen = set() if seen is None else seen
    digest = fingerprint(ir)
    if digest in seen:
        return []
    if len(seen) >= 100:
        raise ValueError("Workflow contract exceeds 100 nested workflows")
    seen.add(digest)
    resources = {"node:" + node["id"] for node in ir["nodes"]}
    for node in ir["nodes"]:
        resolution = node.get("resolutions", {}).get("workflow")
        if not resolution:
            continue
        root = Path(ir["source"]["project_root"])
        child_id = resolution["workflow_id"]
        if resolution["source_kind"] == "registry":
            child = find_registered_workflow(resolution["source"], registry_dir=data_dir)
            path, root, child_id = child.entrypoint, child.project_root, child.workflow_id
        else:
            path = root / resolution["source"]
        compiled = compile_rattish_file(
            path, project_root=root, data_dir=data_dir, workflow_id=child_id
        )
        resources.add("workspace:" + str(root.resolve()))
        resources.update(required_resources(compiled.ir, data_dir, seen))
    return sorted(resources)


class OrganizationExecution:
    def __init__(self, manager: Any, swarms: Any = None, fleet: Any = None) -> None:
        self.manager, self.swarms, self.fleet = manager, swarms, fleet

    def handle(self, chosen: dict[str, Any], handle: dict[str, Any]) -> None:
        org, task = chosen["org"], chosen["task"]

        def save(current: dict[str, Any]) -> Any:
            existing = next(t for t in current["runtime"]["tasks"] if t["id"] == task["id"])
            if existing["claim"] != task["claim"] or current["runtime"]["state"] != "running":
                raise OrganizationConflict("Execution authority was revoked before launch")
            attempt = current["runtime"]["attempts"][task["claim"]]
            attempt["executionHandle"] = handle
            return {"turnId": task["claim"], "handle": handle}

        self.manager.store.mutate(org["projectRoot"], org["id"], "system", "execution_linked", save)

    async def stream(
        self, chosen: dict[str, Any], cancel: threading.Event
    ) -> AsyncGenerator[dict[str, Any], None]:
        org, task = chosen["org"], chosen["task"]
        if cancel.is_set():
            return
        validate_execution(org, task)
        if task["execution"].get("kind") == "workflow":
            async for event in self.workflow(chosen, cancel):
                yield event
            return
        target = target_for(org, task)
        if target["kind"] == "swarm":
            async for event in self.swarm(chosen, target, cancel):
                yield event
        else:
            async for event in self.remote(chosen, target, cancel):
                yield event

    async def swarm(
        self, chosen: dict[str, Any], target: dict[str, Any], cancel: threading.Event
    ) -> AsyncGenerator[dict[str, Any], None]:
        if self.swarms is None:
            raise ValueError("Swarm runtime unavailable")
        org, task = chosen["org"], chosen["task"]
        root, sid = chosen["working_root"], target["swarmId"]
        swarm = self.swarms.get(root, sid, include_history=False)
        # Delegate with the employee's permissions and resources, never a stronger swarm profile.
        employee = chosen["employee"]
        from gofer.core.prompt_envelope import AgentResources

        for agent in swarm["agents"]:
            if agent.get("workspacePath", root) != root:
                raise ValueError("Managed swarm agents must use the task workspace")
            if (
                agent.get("permissionMode") != employee["permissionMode"]
                or AgentResources.model_validate(agent.get("resources") or {}).model_dump()
                != employee["resources"]
            ):
                raise ValueError(
                    "Swarm permission mode and resources must match the employee grant"
                )
        scope = {
            "organizationId": org["id"],
            "taskId": task["id"],
            "parentRunId": task["claim"],
            "turnLimit": target["turnLimit"],
            "employeeId": employee["id"],
            "budgetUsd": chosen.get("budget"),
        }
        # Persist intent before launch. Recovery can locate a run even if launch loses its reply.
        handle = {
            "kind": "swarm",
            "workspacePath": root,
            "swarmId": sid,
            "parentRunId": task["claim"],
        }
        self.handle(chosen, handle)
        try:
            if cancel.is_set():
                return
            run = self.swarms.start(
                root,
                sid,
                task["description"] or task["title"],
                organization_scope=scope,
                expected_configuration=self.swarms._configuration(swarm),
            )["run"]
            handle["runId"] = run["id"]
            self.handle(chosen, handle)
            while True:
                current = self.swarms.get(root, sid, include_history=False)["run"]
                if current["id"] != run["id"]:
                    raise OrganizationConflict("Managed swarm run was replaced")
                usage = swarm_usage(current)
                yield {"type": "usage", "usage": usage, "managedTurns": current["turnCount"]}
                if current["state"] == "completed":
                    yield {
                        "type": "final",
                        "usage": usage,
                        "message": {
                            "body": json.dumps(
                                {
                                    "swarmId": sid,
                                    "runId": run["id"],
                                    "objectives": current["objectives"],
                                }
                            )
                        },
                    }
                    return
                if current["state"] not in {"running", "completing", "stopping"}:
                    raise ValueError(
                        "Swarm requires review: " + current.get("pauseReason", current["state"])
                    )
                if cancel.is_set():
                    return
                await asyncio.sleep(0.2)
        finally:
            self.stop(handle)

    async def remote(
        self, chosen: dict[str, Any], target: dict[str, Any], cancel: threading.Event
    ) -> AsyncGenerator[dict[str, Any], None]:
        task, org = chosen["task"], chosen["org"]
        handle = {
            "kind": target["kind"],
            "requestId": task["claim"],
            "target": copy.deepcopy(target),
        }
        if target["kind"] == "remote":
            handle["target"]["cancellationAccount"] = (
                org["runtime"].get("secrets", {}).get(target["secretRef"], {}).get("account")
            )
        gateway = self.gateway(
            org, chosen["employee"], {**target, "budgetUsd": chosen.get("budget")}, task["claim"]
        )
        self.handle(chosen, handle)
        lineage = {"organizationId": org["id"], "taskId": task["id"], "parentRunId": task["claim"]}
        submitted = False
        completed = False
        try:
            if cancel.is_set():
                return
            submitted = True  # Delivery may have happened even if the reply is lost.
            await asyncio.to_thread(
                gateway.submit, task["claim"], task["description"] or task["title"], lineage
            )
            while not cancel.is_set():
                result = await asyncio.to_thread(gateway.status, task["claim"])
                used = result.get("turnsUsed")
                if type(used) is int and 0 <= used <= target["turnLimit"]:
                    yield {"type": "usage", "usage": result.get("usage", {}), "managedTurns": used}
                state = result.get("state")
                if state == "completed":
                    completed = True
                    yield {
                        "type": "final",
                        "usage": result.get("usage", {}),
                        "message": {
                            "body": result.get("text")
                            or "Remote work completed; inspect the linked receipt."
                        },
                    }
                    return
                if state in {"failed", "rejected", "cancelled", "outcome_unknown", "unknown"}:
                    raise ValueError("Remote work requires review: " + state)
                await asyncio.sleep(0.5)
        finally:
            if submitted and not completed:
                await asyncio.to_thread(gateway.cancel, task["claim"])

    def gateway(
        self, org: dict[str, Any], employee: dict[str, Any], target: dict[str, Any], run_id: str
    ) -> Any:
        from gofer.ui.organization_gateways import FleetGateway, HttpGateway

        if target["kind"] == "fleet":
            if self.fleet is None:
                raise ValueError("Fleet runtime unavailable")
            return FleetGateway(self.fleet, target)
        if target.get("cancellationAccount"):
            from gofer.devices.storage import OSSecretStore

            value = OSSecretStore(service="Raticode organization secrets").get(
                target["cancellationAccount"]
            )
            if not value:
                raise ValueError("Original cancellation credential unavailable")
            with self.manager._lock:
                self.manager._secret_values[run_id] = [value]
            return HttpGateway(target, value)
        resources = {"mcpServers": [{"env": {"TOKEN": "secret://" + target["secretRef"]}}]}
        values = resolve_secrets(org, employee, resources)
        with self.manager._lock:
            self.manager._secret_values[run_id] = values
        return HttpGateway(target, values[0])

    def stop(self, handle: dict[str, Any]) -> None:
        if handle["kind"] == "swarm" and self.swarms:
            current = self.swarms.get(
                handle["workspacePath"], handle["swarmId"], include_history=False
            ).get("run")
            if (
                current
                and current.get("organization", {}).get("parentRunId") == handle["parentRunId"]
                and current["state"] in {"running", "paused", "stopping"}
            ):
                self.swarms.control(handle["workspacePath"], handle["swarmId"], "stop")

    def reconcile(self, org: dict[str, Any]) -> list[dict[str, Any]]:
        """Retry stop requests after lost replies/restarts, never replay a launch."""
        results = []
        for attempt in org["runtime"].get("attempts", {}).values():
            handle = attempt.get("executionHandle")
            if not handle or handle.get("settled") or attempt["id"] in self.manager._active:
                continue
            try:
                reported: dict[str, Any] = {}
                turns_used = None
                if handle["kind"] == "swarm":
                    self.stop(handle)
                    run = self.swarms.get(
                        handle["workspacePath"], handle["swarmId"], include_history=False
                    ).get("run")
                    settled = (
                        not run
                        or run.get("organization", {}).get("parentRunId") != handle["parentRunId"]
                        or run["state"] in {"stopped", "completed", "failed"}
                    )
                    if (
                        run
                        and run.get("organization", {}).get("parentRunId") == handle["parentRunId"]
                    ):
                        reported, turns_used = swarm_usage(run), run["turnCount"]
                elif handle["kind"] in {"remote", "fleet"}:
                    employee: dict[str, Any] = next(
                        (e for e in org["config"]["employees"] if e["id"] == attempt["employeeId"]),
                        {},
                    )
                    gateway = self.gateway(org, employee, handle["target"], attempt["id"])
                    status = gateway.status(handle["requestId"])
                    reported = aggregate_usage([status.get("usage") or {}], 1)
                    turns_used = status.get("turnsUsed")
                    settled = status["state"] in {"completed", "failed", "cancelled", "rejected"}
                    if not settled:
                        gateway.cancel(handle["requestId"])
                else:
                    raise ValueError(
                        "Interrupted local workflow effects require inspection; "
                        "confirm stop after checking its processes and outputs"
                    )
                results.append(
                    {
                        "id": attempt["id"],
                        "settled": settled,
                        "usage": reported,
                        "turnsUsed": turns_used,
                    }
                )
            except Exception as exc:
                results.append(
                    {"id": attempt["id"], "settled": False, "error": self.manager._redact(str(exc))}
                )
            finally:
                self.manager._secret_values.pop(attempt["id"], None)
        if results:

            def save(current: dict[str, Any]) -> Any:
                for result in results:
                    attempt = current["runtime"]["attempts"][result["id"]]
                    attempt["executionHandle"].update(
                        {k: v for k, v in result.items() if k not in {"usage", "turnsUsed"}}
                    )
                    if result["settled"]:
                        task_id = attempt["taskId"]
                        task = next(t for t in current["runtime"]["tasks"] if t["id"] == task_id)
                        settle_usage(current, task, attempt, result)
                        if task.get("cancellation") == "requested":
                            task["cancellation"] = "acknowledged"
                return {"executions": results}

            self.manager.store.mutate(
                org["projectRoot"], org["id"], "system", "executions_reconciled", save
            )
            from gofer.core.usage_ledger import record_invocation

            fresh = self.manager.store.get(org["projectRoot"], org["id"])
            for result in results:
                attempt = fresh["runtime"]["attempts"][result["id"]]
                kind = attempt["executionHandle"]["kind"]
                if result["settled"] and kind in {"fleet", "remote"}:
                    record_invocation(
                        invocation_id=f"organization:{attempt['id']}",
                        provider=kind,
                        metadata=attempt.get("usage", {}),
                        status=attempt["status"],
                        data_dir=self.manager.data_dir,
                    )
        return results

    async def workflow(
        self, chosen: dict[str, Any], cancel: threading.Event
    ) -> AsyncGenerator[dict[str, Any], None]:
        from gofer.rattish.artifacts import compile_rattish_source
        from gofer.rattish.runtime import DEFAULT_NODE_HANDLERS, NodeHandlerRegistry
        from gofer.rattish.workflow_runtime import execute_workflow
        from gofer.ui.organization_operations import file_evidence

        org, task = chosen["org"], chosen["task"]
        contract = task["workflowContract"]
        preview = workflow_preview(org, task, self.manager.data_dir)
        if task.get("workflowAuthorization") != workflow_digest(preview, task):
            raise OrganizationConflict(
                "Workflow source or contract changed; authorize its current preview"
            )
        validate_contract(contract, preview)
        path = Path(preview["path"])
        try:
            with open_binary_input(path) as stream:
                source = stream.read(10_000_001)
        except OSError as exc:
            raise OrganizationConflict("Workflow source changed during launch") from exc
        if len(source) > 10_000_000 or hashlib.sha256(source).hexdigest() != preview["sha256"]:
            raise OrganizationConflict("Workflow source changed during launch")
        compiled = compile_rattish_source(
            source.decode("utf-8"),
            path,
            data_dir=self.manager.data_dir,
            project_root=Path(chosen["working_root"]),
        )
        if fingerprint(compiled.ir) != preview["irSha256"]:
            raise OrganizationConflict("Workflow changed during launch")
        semaphore = asyncio.Semaphore(1)
        allowed = set(contract["allowedResources"])

        class GrantedHandlers(NodeHandlerRegistry):
            def require(self, handler_id: str) -> Any:
                handler = DEFAULT_NODE_HANDLERS.require(handler_id)

                async def guarded(node: Any, context: Any, bindings: Any) -> Any:
                    root = str(context.project_root.resolve())
                    grants = chosen["employee"].get("workspacePaths")
                    if not self_outer.manager.store.owns_project(org["id"], root) or (
                        grants is not None and root not in grants
                    ):
                        raise ValueError("Nested workflow workspace is outside employee grants")
                    if "node:" + node["id"] not in allowed:
                        raise ValueError("Workflow node is outside its resource grants")
                    if node["type"] == "workflow":
                        return await handler(node, context, bindings)
                    if cancel.is_set():
                        raise asyncio.CancelledError
                    async with semaphore:
                        return await handler(node, context, bindings)

                return guarded

        self_outer = self
        meter = WorkflowMeter(reservation(org, task), cancel, chosen.get("budget"))
        self.handle(
            chosen, {"kind": "workflow", "irSha256": preview["irSha256"], "path": str(path)}
        )
        execution = asyncio.create_task(
            execute_workflow(
                compiled.ir,
                workflow_inputs=contract.get("inputs", {}),
                data_dir=self.manager.data_dir,
                handlers=GrantedHandlers(),
                subscriptions=meter.subscriptions(),
            )
        )
        try:
            while not execution.done():
                if cancel.is_set():
                    execution.cancel()
                    return
                yield {"type": "usage", "usage": meter.usage(), "managedTurns": meter.turns}
                await asyncio.sleep(0.1)
            result = await execution
            yield {"type": "usage", "usage": meter.usage(), "managedTurns": meter.turns}
            if result.outcome != "pass":
                raise ValueError("Workflow failed: " + str(result.error))
            artifacts = []
            for check in contract.get("completionChecks", []):
                if check["kind"] == "output":
                    if (
                        check["name"] not in result.outputs
                        or result.outputs[check["name"]] != check["equals"]
                    ):
                        raise ValueError("Workflow output check failed: " + check["name"])
                else:
                    artifact = file_evidence(
                        org, str(Path(chosen["working_root"]) / check["path"]), task["assignee"]
                    )
                    if check.get("sha256") and check["sha256"] != artifact["sha256"]:
                        raise ValueError("Workflow file hash check failed: " + check["path"])
                    artifacts.append(artifact)
            yield {
                "type": "final",
                "usage": meter.usage(),
                "message": {
                    "body": json.dumps(
                        {
                            "workflow": str(path),
                            "result": asdict(result),
                            "completionChecks": "passed",
                            "artifacts": artifacts,
                        },
                        default=str,
                    )
                },
            }
        finally:
            if not execution.done():
                execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)


def settle_usage(
    org: dict[str, Any], task: dict[str, Any], attempt: dict[str, Any], receipt: dict[str, Any]
) -> None:
    """Replace provisional accounting once, using the original run's month and scopes."""
    usage = receipt.get("usage") or {}
    old_cost = attempt.get("costUsd")
    new_cost = usage.get("cost_usd", old_cost)
    old_tokens = (attempt.get("usage") or {}).get("total_tokens")
    new_tokens = usage.get("total_tokens", old_tokens)
    old_turns = attempt.get("usedTurns", attempt.get("reservedTurns", 1))
    new_turns = receipt.get("turnsUsed")
    if type(new_turns) is not int or not 0 <= new_turns <= attempt.get("reservedTurns", 1):
        new_turns = old_turns
    month = attempt.get("reservationMonth") or attempt["startedAt"][:7]
    accounts = org["runtime"].setdefault("usage", {}).setdefault(month, {})
    for account_id in (
        "company",
        f"employee:{attempt['employeeId']}",
        f"initiative:{attempt.get('initiativeId', task['project'])}",
    ):
        account = accounts.setdefault(account_id, {})
        account["turns"] = max(0, account.get("turns", 0) + new_turns - old_turns)
        if new_cost is not None:
            account["costUsd"] = max(0, account.get("costUsd", 0) + new_cost - (old_cost or 0))
            if old_cost is None:
                account["unknownCostTurns"] = max(0, account.get("unknownCostTurns", 0) - 1)
        if new_tokens is not None:
            account["tokens"] = max(0, account.get("tokens", 0) + new_tokens - (old_tokens or 0))
            if old_tokens is None:
                account["unknownTokenTurns"] = max(0, account.get("unknownTokenTurns", 0) - 1)
    task["turns"] = max(0, task["turns"] + new_turns - old_turns)
    attempt.update(
        costUsd=new_cost, usedTurns=new_turns, usage={**attempt.get("usage", {}), **usage}
    )


def aggregate_usage(items: list[dict[str, Any]], turns: int) -> dict[str, Any]:
    import math

    result: dict[str, Any] = {}
    for key in ("cost_usd", "total_tokens", "input_tokens", "output_tokens"):
        values = [u.get(key, u.get("total_cost_usd") if key == "cost_usd" else None) for u in items]
        if len(values) == turns and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0
            for v in values
        ):
            result[key] = sum(values)
    return result


def swarm_usage(run: dict[str, Any]) -> dict[str, Any]:
    items = [
        u or {}
        for a in run.get("attempts", [])
        for u in [*a.get("usageHistory", []), a.get("usage")]
    ]
    return aggregate_usage(items, run["turnCount"])


class WorkflowMeter:
    def __init__(self, limit: int, cancel: threading.Event, budget: float | None = None) -> None:
        self.limit, self.cancel, self.turns = limit, cancel, 0
        self.budget = budget
        self.items: list[dict[str, Any]] = []

    def usage(self) -> dict[str, Any]:
        return aggregate_usage(self.items, self.turns)

    def subscriptions(self) -> Any:
        from gofer.rattish.provider_runtime import default_provider_subscriptions
        from gofer.subscriptions.base import Subscription

        meter = self

        class Metered(Subscription):
            def __init__(self, underlying: Subscription) -> None:
                self.underlying = underlying

            def is_available(self) -> bool:
                return self.underlying.is_available()

            def _build_command(self, *args: Any, **kwargs: Any) -> list[str]:
                raise NotImplementedError

            async def execute(self, *args: Any, **kwargs: Any) -> Any:
                if meter.turns >= meter.limit or meter.cancel.is_set():
                    raise ValueError("Organization workflow turn reservation exhausted")
                cost = meter.usage().get("cost_usd")
                if (
                    meter.budget is not None
                    and meter.turns
                    and (cost is None or cost >= meter.budget)
                ):
                    raise ValueError(
                        "Organization workflow dollar budget reached or cost unavailable"
                    )
                meter.turns += 1
                result = await self.underlying.execute(
                    *args, **{**kwargs, "cancel_event": meter.cancel}
                )
                meter.items.append(dict(result.usage_metadata))
                return result

        return {key: Metered(value) for key, value in default_provider_subscriptions().items()}
