from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from gofer.ui.organization_execution import WorkflowMeter, aggregate_usage
from gofer.ui.organization_packages import parse_package
from gofer.ui.organization_store import OrganizationConflict
from gofer.ui.organization_webhooks import receive
from gofer.ui.organizations import OrganizationManager


@pytest.fixture
def setup(tmp_path: Path) -> Any:
    async def no_provider(**kwargs: Any) -> Any:
        raise AssertionError("Managed execution must not start an employee chat")
        yield {}

    manager = OrganizationManager(tmp_path / "data", stream=no_provider, start_runtime=False)
    org = manager.call(
        str(tmp_path),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Execution lab",
                    "employees": [{"id": "worker", "name": "Worker"}],
                }
            },
        },
    )

    def call(action: str, **params: Any) -> Any:
        return manager.call(
            str(tmp_path), "user", {"organizationId": org["id"], "action": action, "params": params}
        )

    yield manager, org, call
    manager.close()


def drain(manager: OrganizationManager) -> None:
    deadline = time.monotonic() + 5
    while manager._active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not manager._active


def workflow_task(tmp_path: Path, call: Any, command: str = "echo checked") -> Any:
    path = tmp_path / "workflow.rattish"
    path.write_text(
        "Rattish: 1\nWorkflow:\n  name: Managed test\n"
        "Node check:\n  type: bash-command\n  command: " + command + "\n"
    )
    return call(
        "task_create",
        title="Check",
        assignee="worker",
        workflowContract={
            "path": str(path),
            "allowedResources": ["node:check"],
            "completionChecks": [],
            "turnLimit": 2,
        },
    )


def test_workflow_preview_hashes_the_bytes_it_compiles(setup, tmp_path, monkeypatch):
    from gofer.ui import organization_operations as operations

    _, _, call = setup
    task = workflow_task(tmp_path, call)
    source = tmp_path / "workflow.rattish"
    original = source.read_bytes()
    expected = call("workflow_preview", taskId=task["id"])
    changed = original.replace(b"echo checked", b"echo changed")
    read_bytes = Path.read_bytes
    open_input = operations.open_binary_input

    # Model an external writer replacing the source after its evidence read.
    def replace_after_read(path):
        content = read_bytes(path)
        if path == source:
            source.write_bytes(changed)
        return content

    @contextmanager
    def replace_after_close(path):
        with open_input(path) as stream:
            yield stream
        if path == source:
            source.write_bytes(changed)

    monkeypatch.setattr(Path, "read_bytes", replace_after_read)
    monkeypatch.setattr(operations, "open_binary_input", replace_after_close)
    preview = call("workflow_preview", taskId=task["id"])
    assert read_bytes(source) == changed
    assert preview["sha256"] == hashlib.sha256(original).hexdigest()
    assert preview["irSha256"] == expected["irSha256"]


def test_workflow_executes_authorized_snapshot_and_refunds_unused_turns(
    setup: Any, tmp_path: Path
) -> None:
    manager, org, call = setup
    task = workflow_task(tmp_path, call, "echo checked > proof.txt")
    preview = call("workflow_preview", taskId=task["id"])
    assert preview["requiredResources"] == ["node:check"]
    call(
        "workflow_launch",
        taskId=task["id"],
        expectedRevision=task["revision"],
        irSha256=preview["irSha256"],
    )
    assert not (tmp_path / "proof.txt").exists()
    call("control", state="running")
    manager.dispatch()
    drain(manager)
    current = call("read")
    assert (tmp_path / "proof.txt").read_text().strip() == "checked"
    assert current["runtime"]["tasks"][0]["status"] == "in_review"
    attempt = next(iter(current["runtime"]["attempts"].values()))
    assert attempt["status"] == "completed", attempt
    assert attempt["reservedTurns"] == 2 and attempt["usedTurns"] == 0
    assert attempt["costUsd"] == 0
    assert attempt["executionHandle"]["settled"]


def test_workflow_changed_source_does_not_execute(setup: Any, tmp_path: Path) -> None:
    manager, org, call = setup
    task = workflow_task(tmp_path, call)
    preview = call("workflow_preview", taskId=task["id"])
    call(
        "workflow_launch",
        taskId=task["id"],
        expectedRevision=task["revision"],
        irSha256=preview["irSha256"],
    )
    (tmp_path / "workflow.rattish").write_text(
        (tmp_path / "workflow.rattish").read_text().replace("echo checked", "touch bypass")
    )
    call("control", state="running")
    manager.dispatch()
    drain(manager)
    assert not (tmp_path / "bypass").exists()
    attempt = call("attempts")["items"][0]
    assert attempt["status"] == "failed"
    assert "changed" in attempt["error"]


@pytest.mark.parametrize("replacement", ["symlink", "oversized", "changed"])
def test_workflow_launch_rechecks_source_after_contract_validation(
    setup, tmp_path, monkeypatch, replacement
):
    from gofer.rattish import artifacts
    from gofer.ui import organization_execution as execution

    manager, _, call = setup
    task = workflow_task(tmp_path, call, "echo checked > proof.txt")
    preview = call("workflow_preview", taskId=task["id"])
    call(
        "workflow_launch",
        taskId=task["id"],
        expectedRevision=task["revision"],
        irSha256=preview["irSha256"],
    )
    source = tmp_path / "workflow.rattish"
    content = source.read_bytes()
    validate = execution.validate_contract
    compile_source = artifacts.compile_rattish_source
    replaced = False
    compiled_after_swap = []

    def observe_compile(content, *args, **kwargs):
        if replaced:
            compiled_after_swap.append(len(content))
        return compile_source(content, *args, **kwargs)

    def replace_after_validation(contract, checked):
        nonlocal replaced
        validate(contract, checked)
        if replacement == "symlink":
            other = tmp_path / "replacement.rattish"
            other.write_bytes(content)
            source.unlink()
            source.symlink_to(other)
        elif replacement == "oversized":
            source.write_bytes(content + b"#" + b"x" * 10_000_000 + b"\n")
        else:
            source.write_bytes(content.replace(b"echo checked", b"echo changed"))
        replaced = True

    monkeypatch.setattr(artifacts, "compile_rattish_source", observe_compile)
    monkeypatch.setattr(execution, "validate_contract", replace_after_validation)
    call("control", state="running")
    manager.dispatch()
    drain(manager)
    assert compiled_after_swap == [], "Unsafe replacement reached the compiler"
    assert not (tmp_path / "proof.txt").exists()
    attempt = call("attempts")["items"][0]
    assert attempt["status"] == "failed"
    assert "source changed" in attempt["error"]


def test_workflow_requires_resource_grants_and_independent_authorization(
    setup: Any, tmp_path: Path
) -> None:
    manager, org, call = setup
    task = workflow_task(tmp_path, call)
    task = call(
        "task_update",
        taskId=task["id"],
        expectedRevision=task["revision"],
        changes={"workflowContract": {**task["workflowContract"], "allowedResources": []}},
    )
    preview = call("workflow_preview", taskId=task["id"])
    with pytest.raises(ValueError, match="explicit grants"):
        call(
            "workflow_launch",
            taskId=task["id"],
            expectedRevision=task["revision"],
            irSha256=preview["irSha256"],
        )
    call("control", state="running")
    with pytest.raises(ValueError, match="Only the user"):
        manager.call(
            org["projectRoot"],
            "employee:worker",
            {"organizationId": org["id"], "action": "workflow_launch", "params": {}},
            employee_id="worker",
            bound_org=org["id"],
            generation=0,
        )


def test_workflow_failed_completion_check_keeps_task_blocked(setup: Any, tmp_path: Path) -> None:
    manager, org, call = setup
    task = workflow_task(tmp_path, call)
    task = call(
        "task_update",
        taskId=task["id"],
        expectedRevision=1,
        changes={
            "workflowContract": {
                **task["workflowContract"],
                "completionChecks": [{"kind": "file", "path": "missing.txt"}],
            }
        },
    )
    preview = call("workflow_preview", taskId=task["id"])
    call(
        "workflow_launch",
        taskId=task["id"],
        expectedRevision=task["revision"],
        irSha256=preview["irSha256"],
    )
    call("control", state="running")
    manager.dispatch()
    drain(manager)
    assert call("read")["runtime"]["tasks"][0]["status"] == "blocked"


def test_budget_reservation_is_atomic_before_workflow_launch(setup: Any, tmp_path: Path) -> None:
    manager, org, call = setup
    call(
        "configure",
        expectedRevision=1,
        reason="Reserve budget",
        config={**org["config"], "monthlyTurnLimit": 1},
    )
    task = workflow_task(tmp_path, call, "touch should-not-run")
    preview = call("workflow_preview", taskId=task["id"])
    call("workflow_launch", taskId=task["id"], expectedRevision=1, irSha256=preview["irSha256"])
    call("control", state="running")
    manager.dispatch()
    assert not manager._active
    assert not (tmp_path / "should-not-run").exists()
    assert "reservation" in call("read")["runtime"]["tasks"][0]["blockReason"]


def test_workflow_meter_counts_failed_invocations_and_refuses_extra(monkeypatch: Any) -> None:
    from gofer.rattish import provider_runtime

    class Provider:
        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("Failure after provider started")

    monkeypatch.setattr(
        provider_runtime, "default_provider_subscriptions", lambda: {"test": Provider()}
    )
    meter = WorkflowMeter(1, threading.Event())
    subscription = meter.subscriptions()["test"]
    with pytest.raises(RuntimeError):
        asyncio.run(subscription.execute())
    with pytest.raises(ValueError, match="reservation exhausted"):
        asyncio.run(subscription.execute())
    assert meter.turns == 1 and meter.usage() == {}


@pytest.mark.parametrize("cancel_before_submit", [False, True])
def test_remote_target_reserves_usage_and_links_parent(
    setup: Any, monkeypatch: Any, cancel_before_submit: bool
) -> None:
    manager, org, call = setup
    target = {
        "id": "desktop",
        "kind": "fleet",
        "employees": ["worker"],
        "deviceId": str(uuid4()),
        "threadId": str(uuid4()),
        "projectId": str(uuid4()),
    }
    call(
        "configure",
        config={**org["config"], "executionTargets": [target]},
        expectedRevision=1,
        reason="Grant target",
    )
    submitted = []

    class Gateway:
        def submit(self, request_id: str, text: str, lineage: Any) -> None:
            submitted.append((request_id, lineage))

        def status(self, request_id: str) -> Any:
            return {
                "state": "completed",
                "text": "Remote evidence",
                "turnsUsed": 1,
                "usage": {"cost_usd": 0.25, "total_tokens": 10},
            }

        def cancel(self, request_id: str) -> None:
            raise AssertionError("Do not cancel completed work")

    monkeypatch.setattr(manager.executions, "gateway", lambda *args: Gateway())
    task = call("task_create", title="Remote", assignee="worker", execution={"targetId": "desktop"})
    call("control", state="running")
    if cancel_before_submit:
        original_handle = manager.executions.handle

        def revoke(chosen: Any, handle: Any) -> None:
            original_handle(chosen, handle)
            call("control", state="paused")

        monkeypatch.setattr(manager.executions, "handle", revoke)
    manager.dispatch()
    drain(manager)
    attempt = call("attempts")["items"][0]
    if cancel_before_submit:
        assert not submitted
        assert attempt["status"] == "cancelled"
        return
    assert attempt["status"] == "completed", attempt
    assert submitted[0][1]["taskId"] == task["id"]
    assert submitted[0][0] == attempt["id"]
    assert attempt["costUsd"] == 0.25
    assert attempt["executionHandle"]["settled"]


def test_execution_target_grants_cannot_be_supplied_by_employee(setup: Any) -> None:
    manager, org, call = setup
    with pytest.raises(ValueError, match="outside employee grants"):
        call("task_create", title="Bypass", assignee="worker", execution={"targetId": "arbitrary"})
    with pytest.raises(ValueError, match="targetId"):
        call(
            "task_create",
            title="Bypass",
            assignee="worker",
            execution={"url": "https://elsewhere.test"},
        )


@pytest.mark.parametrize("invalid_signature", ["bad", "invalid-\u00e9"])
def test_webhook_signatures_retries_conflicts_and_revocation(
    setup: Any, monkeypatch: Any, invalid_signature: str
) -> None:
    manager, org, call = setup
    from gofer.ui import organization_webhooks

    class Secrets:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def get(self, account: str) -> str:
            return "test-only-signing-key"

    monkeypatch.setattr(organization_webhooks, "OSSecretStore", Secrets)
    call("secret_reference", id="hook", account="test")
    routine = call(
        "routine_save",
        routine={"name": "Hook", "task": {"title": "Fixed template", "assignee": "worker"}},
    )
    call(
        "webhook_save", routineId=routine["id"], expectedRevision=1, secretRef="hook", enabled=True
    )
    body = b'{"title":"Untrusted title","assignee":"evil"}'
    timestamp = str(int(time.time()))

    def headers(raw: bytes) -> Any:
        return {
            "X-Raticode-Timestamp": timestamp,
            "X-Raticode-Delivery": "delivery-1",
            "X-Raticode-Signature": "sha256="
            + hmac.new(
                b"test-only-signing-key", timestamp.encode() + b".delivery-1." + raw, hashlib.sha256
            ).hexdigest(),
        }

    receipt = receive(manager, org["id"], routine["id"], headers(body), body)
    assert receive(manager, org["id"], routine["id"], headers(body), body) == receipt
    assert call("read")["runtime"]["tasks"][0]["title"] == "Fixed template"
    assert len(call("read")["runtime"]["tasks"]) == 1
    with pytest.raises(OrganizationConflict):
        receive(manager, org["id"], routine["id"], headers(b"{}"), b"{}")
    with pytest.raises(ValueError, match="signature"):
        receive(
            manager,
            org["id"],
            routine["id"],
            {**headers(body), "X-Raticode-Signature": invalid_signature},
            body,
        )
    call("secret_reference", id="hook", account="test", revoked=True)
    with pytest.raises(ValueError, match="unavailable"):
        receive(manager, org["id"], routine["id"], headers(body), body)


@pytest.mark.parametrize("root", ["TEAM.md", "AGENTS.md"])
def test_standalone_packages_import_paused_template(root: str) -> None:
    files = {
        root: base64.b64encode(b"---\nname: Solo\nslug: solo\n---\nUseful instructions\n").decode()
    }
    parsed = parse_package(files)
    assert parsed["config"]["name"] == "Solo"
    assert parsed["warnings"]
    if root == "AGENTS.md":
        assert parsed["config"]["employees"][0]["id"] == "solo"
    else:
        assert parsed["config"]["teams"][0]["id"] == "solo"


def test_operational_backup_contains_tasks_and_integrity_chain(setup: Any) -> None:
    manager, org, call = setup
    task = call("task_create", title="Backup evidence")
    call("comment", taskId=task["id"], body="Keep this operational evidence")
    result = call("backup")
    content = base64.b64decode(result["content"])
    assert hashlib.sha256(content).hexdigest() == result["sha256"]
    with sqlite3.connect(":memory:") as db:
        db.deserialize(content)
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT COUNT(*) FROM organization_comments").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM organization_revisions").fetchone()[0] == 1


def test_aggregate_usage_does_not_treat_unknown_as_zero() -> None:
    assert aggregate_usage([{"cost_usd": 1}, {}], 2).get("cost_usd") is None
    assert aggregate_usage([{"cost_usd": float("nan")}], 1).get("cost_usd") is None
    assert aggregate_usage([{"cost_usd": 1}, {"cost_usd": 2}], 2)["cost_usd"] == 3


@pytest.mark.parametrize("launch_fault", [None, "permissions_changed", "lost_reply", "revoked"])
def test_managed_swarm_stops_at_reserved_turn_limit(
    setup: Any, tmp_path: Path, monkeypatch: Any, launch_fault: str | None
) -> None:
    from gofer.ui.swarm_workspaces import git
    from gofer.ui.swarms import SwarmManager

    manager, org, call = setup
    git(tmp_path, "init")
    (tmp_path / "tracked.txt").write_text("base")
    git(tmp_path, "add", "tracked.txt")
    git(tmp_path, "commit", "-m", "Fixture")
    # Git initialization changes the ownership identity. Re-save the same explicit grant.
    call("configure", config=org["config"], expectedRevision=1, reason="Fixture git root")
    calls = []

    async def worker(**kwargs: Any) -> Any:
        calls.append(kwargs)
        yield {"type": "final", "message": {"body": "One bounded turn"}, "usage": {"cost_usd": 0.1}}

    swarms = SwarmManager(tmp_path / "swarms", stream=worker)
    manager.executions.swarms = swarms
    try:
        swarm = swarms.create(
            tmp_path,
            {
                "name": "Managed",
                "agents": [
                    {
                        "id": "lead",
                        "name": "Lead",
                        "role": "Coordinate",
                        "isOrchestrator": True,
                        "permissionMode": "workspace-write",
                        "resources": org["config"]["employees"][0]["resources"],
                    }
                ],
            },
        )
        current = call("read")
        call(
            "configure",
            config={
                **current["config"],
                "executionTargets": [
                    {
                        "id": "team",
                        "kind": "swarm",
                        "employees": ["worker"],
                        "swarmId": swarm["id"],
                        "turnLimit": 1,
                    }
                ],
            },
            expectedRevision=current["revision"],
            reason="Grant swarm",
        )
        call(
            "task_create", title="Bounded swarm", assignee="worker", execution={"targetId": "team"}
        )
        call("control", state="running")
        original_start = swarms.start

        def start(*args: Any, **kwargs: Any) -> Any:
            if launch_fault == "permissions_changed":
                changed = swarms.get(tmp_path, swarm["id"])
                changed["agents"][0]["permissionMode"] = "danger-full-access"
                swarms.update(tmp_path, swarm["id"], changed)
            result = original_start(*args, **kwargs)
            if launch_fault == "lost_reply":
                raise ValueError("Launch reply lost")
            if launch_fault == "revoked":
                call("control", state="paused")
            return result

        monkeypatch.setattr(swarms, "start", start)
        manager.dispatch()
        drain(manager)
        attempt = call("attempts")["items"][0]
        if launch_fault:
            assert attempt["status"] in {"failed", "cancelled"}, attempt
            run = swarms.get(tmp_path, swarm["id"], include_history=False).get("run")
            if launch_fault == "permissions_changed":
                assert not run
                assert not calls
                assert "configuration changed" in attempt["error"].lower()
            else:
                assert run is not None
                assert run["state"] in {"stopped", "stopping"}
            return
        assert len(calls) == 1, attempt
        assert "reservation exhausted" in attempt["error"]
        run = swarms.get(tmp_path, swarm["id"], include_history=False)["run"]
        assert run["organization"]["parentRunId"] == attempt["id"]
        assert run["turnCount"] == 1
        assert run["state"] == "stopped"
        assert call("execution_reconcile")[0]["settled"]
    finally:
        swarms.close()


def test_lost_remote_submit_reply_requests_stop_and_blocks_relaunch(
    setup: Any, monkeypatch: Any
) -> None:
    manager, org, call = setup
    target = {
        "id": "desktop",
        "kind": "fleet",
        "employees": ["worker"],
        "deviceId": str(uuid4()),
        "threadId": str(uuid4()),
        "projectId": str(uuid4()),
    }
    call(
        "configure",
        config={**org["config"], "executionTargets": [target]},
        expectedRevision=1,
        reason="Grant",
    )
    stops = []

    class Gateway:
        def submit(self, *args: Any) -> None:
            raise TimeoutError("Reply lost after target accepted")

        def cancel(self, request_id: str) -> Any:
            stops.append(request_id)
            return {"state": "cancel_requested"}

        def status(self, request_id: str) -> Any:
            return {
                "state": "cancelled",
                "turnsUsed": 1,
                "usage": {"cost_usd": 0.4, "total_tokens": 12},
            }

    monkeypatch.setattr(manager.executions, "gateway", lambda *args: Gateway())
    call("task_create", title="Remote", assignee="worker", execution={"targetId": "desktop"})
    call("control", state="running")
    manager.dispatch()
    drain(manager)
    assert len(stops) == 1
    attempt = call("attempts")["items"][0]
    assert attempt["reservedTurns"] == attempt["usedTurns"] == 1
    assert attempt["costUsd"] is None
    call("task_create", title="Another", assignee="worker", execution={"targetId": "desktop"})
    manager.dispatch()
    assert not manager._active
    assert call("execution_reconcile")[0]["settled"]
    settled = call("attempts")["items"][0]
    assert settled["costUsd"] == 0.4
    assert call("execution_reconcile") == []
    month = next(iter(call("read")["runtime"]["usage"].values()))
    assert month["company"]["costUsd"] == 0.4
    assert month["company"]["unknownCostTurns"] == 0


def test_secret_store_keeps_credential_out_of_database(setup: Any, monkeypatch: Any) -> None:
    from gofer.devices import storage

    manager, org, call = setup
    values = {}

    class Secrets:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def put(self, account: str, value: str) -> None:
            values[account] = value

    monkeypatch.setattr(storage, "OSSecretStore", Secrets)
    result = call("secret_store", id="gateway", value="private-test-credential")
    assert values[result["account"]] == "private-test-credential"
    assert "private-test-credential" not in json.dumps(call("read"))
    assert "private-test-credential" not in json.dumps(call("events"))


def test_workflow_cancel_stops_subprocess_before_late_effect(setup: Any, tmp_path: Path) -> None:
    manager, org, call = setup
    task = workflow_task(tmp_path, call, "sleep 2; touch late-effect")
    preview = call("workflow_preview", taskId=task["id"])
    call("workflow_launch", taskId=task["id"], expectedRevision=1, irSha256=preview["irSha256"])
    call("control", state="running")
    manager.dispatch()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        attempts = call("attempts")["items"]
        if attempts and attempts[0].get("executionHandle"):
            break
        time.sleep(0.01)
    current = call("read")["runtime"]["tasks"][0]
    call(
        "task_update",
        taskId=task["id"],
        expectedRevision=current["revision"],
        changes={"status": "cancelled"},
    )
    drain(manager)
    assert call("read")["runtime"]["tasks"][0]["status"] == "cancelled"
    assert not (tmp_path / "late-effect").exists()


def test_nested_workflow_grants_and_completion_checks(setup: Any, tmp_path: Path) -> None:
    manager, org, call = setup
    child = tmp_path / "child"
    child.mkdir()
    (child / "workflow.rattish").write_text(
        "Rattish: 1\nWorkflow:\n  name: Child\n  interface-version: 1\n"
        "Node child-step:\n  type: bash-command\n  command: echo nested > nested.txt\n"
    )
    task = workflow_task(tmp_path, call)
    (tmp_path / "workflow.rattish").write_text(
        "Rattish: 1\nWorkflow:\n  name: Parent\n"
        "Node child:\n  type: workflow\n  workflow-path: child/workflow.rattish\n"
    )
    preview = call("workflow_preview", taskId=task["id"])
    task = call(
        "task_update",
        taskId=task["id"],
        expectedRevision=1,
        changes={
            "workflowContract": {
                **task["workflowContract"],
                "allowedResources": preview["requiredResources"],
                "completionChecks": [{"kind": "file", "path": "nested.txt"}],
            }
        },
    )
    call(
        "workflow_launch",
        taskId=task["id"],
        expectedRevision=task["revision"],
        irSha256=preview["irSha256"],
    )
    call("control", state="running")
    manager.dispatch()
    drain(manager)
    attempt = call("attempts")["items"][0]
    assert attempt["status"] == "completed", attempt
    assert (tmp_path / "nested.txt").read_text().strip() == "nested"


def test_http_gateway_uses_bounded_idempotent_protocol(monkeypatch: Any) -> None:
    from gofer.ui import organization_gateways as module

    requests = []
    request_id = str(uuid4())

    class Response:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def read(self, limit: int) -> bytes:
            assert limit == 1_048_577
            return json.dumps({"requestId": request_id, "state": "queued"}).encode()

    class Opener:
        def open(self, request: Any, timeout: int) -> Any:
            assert timeout == 15
            requests.append(request)
            return Response()

    monkeypatch.setattr(module, "build_opener", lambda *args: Opener())
    gateway = module.HttpGateway(
        {"url": "https://gateway.example/v1", "turnLimit": 4, "budgetUsd": 2}, "test-only-token"
    )
    gateway.submit(request_id, "Do work", {"taskId": "task"})
    gateway.status(request_id)
    gateway.cancel(request_id)
    assert [r.method for r in requests] == ["PUT", "GET", "DELETE"]
    assert len({r.full_url for r in requests}) == 1
    payload = json.loads(requests[0].data)
    assert payload["turnLimit"] == 4 and payload["maxConcurrency"] == 1
    assert payload["budgetUsd"] == 2
    assert requests[0].headers["Authorization"] == "Bearer test-only-token"
    with pytest.raises(ValueError, match="redirect"):
        module.NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.test")
