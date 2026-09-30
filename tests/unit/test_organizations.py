from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.organization_packages import export_package, parse_package, read_zip, zip_package
from gofer.ui.organization_store import OrganizationConflict, Task
from gofer.ui.organizations import OrganizationManager


async def fake_stream(**options) -> Any:
    yield {"type": "final", "message": {"body": "Implemented and verified the assigned change."}}


@pytest.fixture
def manager(tmp_path) -> Any:
    result = OrganizationManager(tmp_path / "data", stream=fake_stream, start_runtime=False)
    yield result
    result.close()


def company(manager, tmp_path) -> Any:
    return manager.call(
        str(tmp_path),
        "user",
        {
            "action": "create",
            "params": {
                "config": {
                    "name": "Studio",
                    "employees": [
                        {"id": "lead", "name": "Avery", "role": "Coordinate"},
                        {"id": "builder", "name": "Mira", "reportsTo": "lead", "role": "Build"},
                    ],
                }
            },
        },
    )


def call(manager, org, action, **params) -> Any:
    return manager.call(
        org["projectRoot"],
        "Rem:test-thread",
        {"organizationId": org["id"], "action": action, "params": params},
    )


def employee_call(manager, org, action, eid="builder", **params) -> Any:
    return manager.call(
        org["projectRoot"],
        f"employee:{eid}",
        {"organizationId": org["id"], "action": action, "params": params},
        employee_id=eid,
        bound_org=org["id"],
        generation=0,
    )


def test_configuration_history_restore_is_append_only(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    original = org["config"]
    changed = call(
        manager,
        org,
        "configure",
        config={**original, "name": "Changed"},
        expectedRevision=1,
        reason="Rename company",
    )
    assert changed["revision"] == 2
    with pytest.raises(OrganizationConflict):
        call(manager, org, "configure", config=original, expectedRevision=1, reason="Stale editor")
    restored = call(manager, org, "restore", revision=1, expectedRevision=2, reason="Undo rename")
    assert restored["config"] == original
    assert restored["runtime"]["state"] == "paused"
    history = call(manager, org, "history")
    assert [h["revision"] for h in history] == [3, 2, 1]
    assert history[0]["restored_from"] == 1
    assert history[0]["actor"] == "Rem:test-thread"
    assert history[0]["previous_digest"] == history[1]["digest"]
    assert history[1]["changes"] == [{"path": "/name", "before": "Studio", "after": "Changed"}]
    with manager.store.connect() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM organization_revisions")


def test_claim_and_configuration_isolation(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    assert manager.store.get(str(tmp_path / "other"), org["id"])["id"] == org["id"]
    config = org["config"]
    config["employees"][0]["reportsTo"] = "builder"
    with pytest.raises(ValueError, match="cycles"):
        call(manager, org, "configure", config=config, expectedRevision=1, reason="Cycle")
    assert manager.store.get(str(tmp_path), org["id"])["revision"] == 1


def test_list_summarizes_staff_and_open_work(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    call(manager, org, "task_create", title="Ship", assignee="builder")
    done = call(manager, org, "task_create", title="Old", status="cancelled")
    assert done["status"] == "cancelled"
    listed = manager.call(str(tmp_path), "user", {"action": "list"})
    assert listed == [
        {
            "id": org["id"],
            "name": "Studio",
            "revision": 1,
            "state": "paused",
            "employees": 2,
            "openTasks": 1,
            "projectRoots": [str(tmp_path)],
        }
    ]


@pytest.mark.parametrize("action", ["create", "configure", "restore", "control", "import"])
def test_employee_cannot_configure(manager, tmp_path, action) -> Any:
    org = company(manager, tmp_path)
    call(manager, org, "control", state="running")
    with pytest.raises(ValueError, match="cannot configure"):
        employee_call(manager, org, action)


def test_employee_revocation_and_task_ownership(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    task = call(manager, org, "task_create", title="Plan", assignee="lead")
    call(manager, org, "control", state="running")
    with pytest.raises(ValueError, match="assignee"):
        employee_call(
            manager,
            org,
            "task_update",
            taskId=task["id"],
            expectedRevision=1,
            changes={"status": "blocked"},
        )
    employee_call(manager, org, "comment", taskId=task["id"], body="Need context")
    call(manager, org, "control", state="paused")
    with pytest.raises(OrganizationConflict, match="revoked"):
        employee_call(manager, org, "comment", taskId=task["id"], body="Stale turn")


def test_task_approval_binds_to_plan(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    task = call(
        manager, org, "task_create", title="Ship", assignee="builder", approvalRequired=True
    )
    call(manager, org, "control", state="running")
    manager.dispatch()
    assert not manager._active
    approved = call(manager, org, "approve", taskId=task["id"], expectedRevision=task["revision"])
    assert approved["approvedRevision"] == approved["planRevision"]
    changed = call(
        manager,
        org,
        "task_update",
        taskId=task["id"],
        expectedRevision=approved["revision"],
        changes={"description": "Different scope"},
    )
    assert changed["approvedRevision"] is None
    manager.dispatch()
    assert not manager._active


def test_concurrent_config_writers_only_one_wins(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)

    def write(index):
        try:
            return call(
                manager,
                org,
                "configure",
                config={**org["config"], "name": str(index)},
                expectedRevision=1,
                reason="Concurrent edit",
            )["revision"]
        except OrganizationConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [1, 2]))
    assert sorted(map(str, results)) == ["2", "conflict"]


def wait_idle(manager) -> Any:
    deadline = time.monotonic() + 5
    while manager._active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not manager._active


def test_explicit_resume_all_lifts_roster_holds_and_dispatches_approved_work(
    manager, tmp_path
) -> Any:
    org = company(manager, tmp_path)
    config = org["config"]
    for employee in config["employees"]:
        employee["paused"] = True
    org = call(manager, org, "configure", config=config, expectedRevision=1, reason="Pause roster")
    for employee in config["employees"]:
        task = call(
            manager,
            org,
            "task_create",
            title="Approved work",
            assignee=employee["id"],
            approvalRequired=True,
        )
        call(manager, org, "approve", taskId=task["id"], expectedRevision=task["revision"])
    call(
        manager,
        org,
        "task_create",
        title="Awaiting approval",
        assignee="builder",
        approvalRequired=True,
    )
    call(manager, org, "task_create", title="Blocked work", assignee="builder", status="blocked")

    held = call(manager, org, "control", state="running")
    assert all(employee["paused"] for employee in held["config"]["employees"])
    manager.dispatch()
    assert not manager._active
    call(manager, org, "resume_all", expectedRevision=org["revision"])
    started = call(manager, org, "control", state="running")
    assert started["runtime"]["state"] == "running"
    assert all(not employee["paused"] for employee in started["config"]["employees"])
    assert started["revision"] == org["revision"] + 1
    assert manager._wake.is_set()
    for _ in range(3):
        manager.dispatch()
        wait_idle(manager)
    current = call(manager, org, "read")
    assert [task["status"] for task in current["runtime"]["tasks"]] == [
        "in_review",
        "in_review",
        "todo",
        "blocked",
    ]
    history = call(manager, org, "history")
    assert history[0]["actor"] == "Rem:test-thread"
    assert {change["path"] for change in history[0]["changes"]} == {
        "/employees/lead/paused",
        "/employees/builder/paused",
    }
    with pytest.raises(OrganizationConflict):
        call(
            manager,
            org,
            "configure",
            config=config,
            expectedRevision=org["revision"],
            reason="Stale pause",
        )
    restarted = call(manager, org, "control", state="running")
    assert restarted["revision"] == started["revision"]


def test_start_work_without_project_leaves_roster_and_history_unchanged(manager, tmp_path) -> Any:
    org = manager.store.create(
        "",
        {"name": "Unbound", "employees": [{"id": "builder", "name": "Mira", "paused": True}]},
        "user",
        "Create unbound organization",
    )
    with pytest.raises(ValueError, match="Assign a Raticode project"):
        call(manager, org, "control", state="running")
    assert manager.store.get("", org["id"]) == org
    assert len(call(manager, org, "history")) == 1


@pytest.mark.parametrize("limit_scope", ["company", "employee"])
def test_resuming_employee_does_not_reset_exhausted_limits(manager, tmp_path, limit_scope) -> Any:
    org = company(manager, tmp_path)
    config = org["config"]
    if limit_scope == "company":
        config["monthlyTurnLimit"] = 1
    else:
        config["employees"][1]["monthlyTurnLimit"] = 1
    call(manager, org, "configure", config=config, expectedRevision=1, reason="Limit turns")
    call(manager, org, "task_create", title="First", assignee="builder")
    call(manager, org, "task_create", title="Second", assignee="builder")
    call(manager, org, "control", state="running")
    manager.dispatch()
    wait_idle(manager)
    current = call(manager, org, "read")
    usage = current["runtime"]["usage"]
    current["config"]["employees"][1]["paused"] = True
    call(
        manager,
        org,
        "configure",
        config=current["config"],
        expectedRevision=current["revision"],
        reason="Pause builder",
    )
    call(manager, org, "control", state="running")
    manager.dispatch()
    wait_idle(manager)
    current = call(manager, org, "read")
    assert current["runtime"]["usage"] == usage
    assert current["runtime"]["tasks"][1]["status"] == "todo"


def test_start_work_while_running_preserves_active_turn(manager, tmp_path) -> Any:
    entered = threading.Event()
    release = threading.Event()

    async def stream(**options):
        entered.set()
        while not release.is_set() and not options["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "final", "message": {"body": "Finished"}}

    manager.stream = stream
    org = company(manager, tmp_path)
    call(manager, org, "task_create", title="Build", assignee="builder")
    call(manager, org, "control", state="running")
    try:
        manager.dispatch()
        assert entered.wait(3)
        before = call(manager, org, "read")
        after = call(manager, org, "control", state="running")
        assert after == before
        assert all(not cancel.is_set() for _, cancel, _ in manager._active.values())
    finally:
        release.set()
        wait_idle(manager)
    assert call(manager, org, "read")["runtime"]["tasks"][0]["status"] == "in_review"


def test_execution_reuses_rem_and_retains_conversation(manager, tmp_path) -> Any:
    captured = []

    async def stream(**options):
        captured.append(options)
        yield {"type": "final", "message": {"body": "Checked the implementation"}}

    manager.stream = stream
    org = company(manager, tmp_path)
    task = call(manager, org, "task_create", title="Build", assignee="builder")
    call(manager, org, "control", state="running")
    manager.dispatch()
    wait_idle(manager)
    current = call(manager, org, "read")
    done = current["runtime"]["tasks"][0]
    assert done["status"] == "in_review" and done["claim"] is None
    assert done["comments"][-1]["body"] == "Checked the implementation"
    assert "Mira" in captured[0]["agent_instructions"]
    assert "cannot configure" in captured[0]["agent_instructions"]
    assert captured[0]["working_dir"] == tmp_path
    assert captured[0]["workflow"]["remResources"]["mcpServers"][-1]["name"] == "organizations"
    events = call(manager, org, "events")
    assert any(
        e["kind"] == "turn_finished" and e["payload"]["taskId"] == task["id"] for e in events
    )
    assert all("/mcp/" not in json.dumps(e) for e in events)
    from gofer.core.usage_ledger import activity_payload

    assert activity_payload(manager.data_dir)["activity"]["calls"] == 1


def test_restore_cancels_active_turn_and_preserves_tasks(manager, tmp_path) -> Any:
    entered = threading.Event()

    async def stream(**options):
        entered.set()
        while not options["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "final", "message": {"body": "Late response"}}

    manager.stream = stream
    org = company(manager, tmp_path)
    call(manager, org, "task_create", title="Build", assignee="builder")
    call(manager, org, "control", state="running")
    manager.dispatch()
    assert entered.wait(3)
    call(manager, org, "restore", revision=1, expectedRevision=1, reason="Recover healthy state")
    wait_idle(manager)
    task = call(manager, org, "read")["runtime"]["tasks"][0]
    assert task["status"] == "blocked" and task["claim"] is None
    assert not any(c["body"] == "Late response" for c in task["comments"])


def test_restart_blocks_unknown_effects(manager, tmp_path, monkeypatch) -> Any:
    from gofer.ui.organization_execution import OrganizationExecution

    def no_startup_network(*args):
        raise AssertionError("Remote reconciliation must not block backend construction")

    monkeypatch.setattr(OrganizationExecution, "reconcile", no_startup_network)
    org = company(manager, tmp_path)
    call(manager, org, "task_create", title="Work", assignee="builder")

    def interrupt(current):
        current["runtime"]["tasks"][0].update(claim="old-turn", status="in_progress")
        return {}

    manager.store.mutate(str(tmp_path), org["id"], "system", "test", interrupt)
    restarted = OrganizationManager(tmp_path / "data", stream=fake_stream, start_runtime=False)
    try:
        current = call(restarted, org, "read")
        assert current["runtime"]["state"] == "paused"
        assert current["runtime"]["tasks"][0]["status"] == "blocked"
    finally:
        restarted.close()


def fixture_files() -> Any:
    fixture = Path(__file__).parents[1] / "fixtures" / "agent-company"
    return {
        p.relative_to(fixture).as_posix(): base64.b64encode(p.read_bytes()).decode()
        for p in fixture.rglob("*")
        if p.is_file()
    }


def test_spec_package_roundtrip_preserves_identity_skills_and_attribution(manager, tmp_path) -> Any:
    package = parse_package(fixture_files())
    assert package["config"]["metadata"]["license"] == "MIT"
    assert package["config"]["employees"][1]["skills"] == ["review"]
    org = manager.store.create(str(tmp_path), package["config"], "user", "Import", package["tasks"])
    assert org["runtime"]["state"] == "paused"
    exported = export_package(org["config"], org["runtime"]["tasks"])
    assert read_zip(zip_package(exported)) == exported
    parsed = parse_package(exported)
    assert parsed["config"]["metadata"]["authors"] == [{"name": "Example Author"}]
    assert parsed["config"]["employees"][1]["reportsTo"] == "ceo"
    assert parsed["tasks"][0]["project"] == "launch"
    assert (
        parsed["config"]["packageFiles"]["skills/review/references/check.md"]
        == exported["skills/review/references/check.md"]
    )


@pytest.mark.parametrize(
    "identifiers",
    [
        ["Review", "review", "review-2"],
        ["review/task", "review task", "review-task"],
        ["x" * 100 + "a", "x" * 100 + "b", "x" * 100 + "c"],
    ],
)
def test_package_export_preserves_tasks_with_colliding_filename_slugs(identifiers) -> None:
    config = parse_package(fixture_files())["config"]
    tasks = [
        Task(id=identifier, title=f"Task {index}", recurring=True, intervalSeconds=index * 60)
        .model_dump()
        for index, identifier in enumerate(identifiers, 1)
    ]

    exported = export_package(config, tasks)
    imported = parse_package(read_zip(zip_package(exported)))

    assert len(imported["tasks"]) == len(tasks)
    assert {task["title"]: task["intervalSeconds"] for task in imported["tasks"]} == {
        task["title"]: task["intervalSeconds"] for task in tasks
    }
    assert len({task["id"] for task in imported["tasks"]}) == len(tasks)


@pytest.mark.parametrize(
    "bad_path", ["../AGENTS.md", "/AGENTS.md", "C:/AGENTS.md", "a/../AGENTS.md"]
)
def test_import_rejects_unsafe_paths(bad_path) -> Any:
    files = fixture_files()
    files[bad_path] = base64.b64encode(b"unsafe").decode()
    with pytest.raises(ValueError, match="Unsafe"):
        parse_package(files)


def test_missing_skills_and_external_includes_fail_before_creation() -> Any:
    files = fixture_files()
    del files["skills/review/SKILL.md"]
    with pytest.raises(ValueError, match="Unresolved skill"):
        parse_package(files)
    files = fixture_files()
    text = (
        base64.b64decode(files["COMPANY.md"])
        .decode()
        .replace(
            "name: Lean Dev Shop", "includes: [https://example.com/TEAM.md]\nname: Lean Dev Shop"
        )
    )
    files["COMPANY.md"] = base64.b64encode(text.encode()).decode()
    with pytest.raises(ValueError, match="external includes"):
        parse_package(files)


def test_budget_and_memory_survive_configuration_restore(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    configured = call(
        manager,
        org,
        "configure",
        config={**org["config"], "monthlyTurnLimit": 1},
        expectedRevision=1,
        reason="One turn budget",
    )
    call(manager, org, "task_create", title="First", assignee="builder")
    call(manager, org, "task_create", title="Second", assignee="builder")
    call(manager, org, "control", state="running")
    manager.dispatch()
    wait_idle(manager)
    manager.dispatch()
    current = call(manager, org, "read")
    assert current["runtime"]["state"] == "paused"
    assert current["runtime"]["tasks"][1]["status"] == "todo"
    usage = current["runtime"]["usage"]
    restored = call(
        manager,
        org,
        "restore",
        revision=1,
        expectedRevision=configured["revision"],
        reason="Restore settings",
    )
    assert restored["runtime"]["usage"] == usage


def test_working_memory_is_operational_and_isolated(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    call(manager, org, "control", state="running")
    employee_call(manager, org, "memory", body="Tests use the fixture database")
    state = call(manager, org, "read")
    assert state["revision"] == 1
    assert state["runtime"]["employeeMemory"] == {"builder": "Tests use the fixture database"}
    restored = call(manager, org, "restore", revision=1, expectedRevision=1, reason="Recover")
    assert restored["runtime"]["employeeMemory"] == state["runtime"]["employeeMemory"]


def test_portable_runtime_settings_roundtrip_without_local_resources(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    config = org["config"]
    config["employees"][0].update(
        provider="claude_code", permissionMode="default", monthlyTurnLimit=9, heartbeatSeconds=90
    )
    exported = export_package(config, [])
    assert ".raticode.yaml" in exported
    imported = parse_package(exported)
    employee = imported["config"]["employees"][0]
    assert employee["provider"] == "claude_code"
    assert employee["monthlyTurnLimit"] == 9
    assert employee["heartbeatSeconds"] == 90
    assert imported["config"]["remSecondBrain"] == {}


def test_rem_session_capability_expires_and_read_only_denies(manager, tmp_path) -> Any:
    import urllib.request

    org = company(manager, tmp_path)
    with manager.session(str(tmp_path), "Rem:trusted", read_only=True) as url:
        request = urllib.request.Request(
            url,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "organization_action",
                        "arguments": {
                            "action": "configure",
                            "organizationId": org["id"],
                            "params": {
                                "config": org["config"],
                                "expectedRevision": 1,
                                "reason": "Denied",
                            },
                        },
                    },
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as response:
            result = json.loads(response.read())
        assert "read-only" in json.dumps(result)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 401


@pytest.mark.parametrize("enabled", [False, True])
def test_http_organization_routes_authorize_and_attribute(tmp_path, monkeypatch, enabled) -> Any:
    import urllib.error
    import urllib.request

    from gofer.ui import server as ui

    monkeypatch.setattr(ui, "ensure_local_gofer_cli", lambda data_dir: None)
    if enabled:
        monkeypatch.setenv("RATICODE_EXPERIMENTAL_ORGANIZATIONS", "1")
    else:
        monkeypatch.delenv("RATICODE_EXPERIMENTAL_ORGANIZATIONS", raising=False)
    server = ui.create_server(port=0, data_dir=tmp_path, api_token="test-organization-api")
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}/api/organizations"

    def request(body=None, authenticated=True, suffix="") -> Any:
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = "Bearer test-organization-api"
        req = urllib.request.Request(
            origin + suffix,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read())["result"]

    try:
        with pytest.raises(urllib.error.HTTPError) as denied:
            request(authenticated=False)
        assert denied.value.code == 401
        assert request(suffix="?action=availability") == {"enabled": enabled, "experimental": True}
        if not enabled:
            assert not server.organizations.enabled
            for body in (None, {"action": "create", "params": {"config": {"name": "Blocked"}}}):
                with pytest.raises(urllib.error.HTTPError) as denied:
                    request(body)
                assert denied.value.code == 403
            webhook = urllib.request.Request(
                origin.replace("/organizations", "/organization-webhooks/org/routine"),
                data=b"{}",
            )
            with pytest.raises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(webhook)
            assert denied.value.code == 403
            return
        org = request(
            {
                "projectRoot": str(tmp_path),
                "actor": "fake-admin",
                "action": "create",
                "params": {"config": {"name": "API company"}},
            }
        )
        history = request(
            suffix=f"?projectRoot={tmp_path}&action=history&organizationId={org['id']}"
        )
        assert history[0]["actor"] == "user"
        with pytest.raises(urllib.error.HTTPError) as conflict:
            request(
                {
                    "projectRoot": str(tmp_path),
                    "organizationId": org["id"],
                    "action": "configure",
                    "params": {"config": org["config"], "expectedRevision": 0, "reason": "Stale"},
                }
            )
        assert conflict.value.code == 409
        with pytest.raises(urllib.error.HTTPError) as denied:
            request(
                {
                    "projectRoot": str(tmp_path.parent),
                    "action": "create",
                    "params": {"config": {"name": "Unapproved folder"}},
                }
            )
        assert denied.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_distinct_workspaces_run_concurrently_but_shared_workspaces_serialize(
    manager, tmp_path
) -> Any:
    entered = []
    release = threading.Event()

    async def stream(**options) -> Any:
        entered.append(options["working_dir"])
        while not release.is_set():
            await asyncio.sleep(0.01)
        yield {"type": "final", "message": {"body": "Finished"}}

    manager.stream = stream
    other = tmp_path / "other"
    other.mkdir()
    org = company(manager, tmp_path)
    config = {
        **org["config"],
        "maxConcurrency": 2,
        "projectRoots": [str(tmp_path), str(other)],
        "projects": [{"id": "other", "name": "Other", "workspacePath": str(other)}],
    }
    call(manager, org, "configure", config=config, expectedRevision=1, reason="Two projects")
    call(manager, org, "task_create", title="One", assignee="lead")
    call(manager, org, "task_create", title="Two", assignee="builder", project="other")
    call(manager, org, "control", state="running")
    try:
        manager.dispatch()
        manager.dispatch()
        deadline = time.monotonic() + 3
        while len(entered) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert set(entered) == {tmp_path, other}
    finally:
        release.set()
        wait_idle(manager)


def test_package_does_not_grant_local_workspace_paths() -> Any:
    files = fixture_files()
    path = "projects/launch/PROJECT.md"
    source = (
        base64.b64decode(files[path])
        .decode()
        .replace("owner: cto", "owner: cto\nworkspacePath: /etc")
    )
    files[path] = base64.b64encode(source.encode()).decode()
    package = parse_package(files)
    assert "workspacePath" not in package["config"]["projects"][0]


def test_codex_organization_auto_approval_is_bound_to_actual_server(tmp_path) -> Any:
    from gofer.core.prompt_envelope import AgentResources
    from gofer.ui.chat import _build_chat_command

    url = "http://127.0.0.1:12345/turn-capability"
    resources = AgentResources.model_validate(
        {"mcpServers": [{"name": "organizations", "url": url}]}
    )
    options = dict(
        provider="codex",
        model="cli-default",
        prompt="Task",
        working_dir=tmp_path,
        resources=resources,
        data_dir=tmp_path,
    )
    trusted = _build_chat_command(**options, trusted_organization_url=url)
    assert any('tools.organization_action.approval_mode="approve"' in arg for arg in trusted)
    untrusted = _build_chat_command(**options, trusted_organization_url=url + "-other")
    assert not any('tools.organization_action.approval_mode="approve"' in arg for arg in untrusted)


def test_ui_roundtrip_preserves_imported_skill_assets(manager, tmp_path) -> Any:
    package = parse_package(fixture_files())
    org = manager.store.create(str(tmp_path), package["config"], "user", "Import", package["tasks"])
    public = call(manager, org, "read")
    assert "packageFiles" not in public["config"]
    call(
        manager,
        org,
        "configure",
        config={**public["config"], "name": "Renamed"},
        expectedRevision=1,
        reason="Rename imported company",
    )
    assert manager.store.get(str(tmp_path), org["id"])["config"]["packageFiles"] == fixture_files()


def test_global_organizations_without_an_open_project(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    other = manager.call("", "user", {"action": "create", "params": {"config": {"name": "Global"}}})
    assert other["config"]["projectRoots"] == []
    for root in ("", str(tmp_path), str(tmp_path / "unrelated")):
        assert {o["id"] for o in manager.call(root, "user", {"action": "list"})} == {
            org["id"],
            other["id"],
        }
        assert (
            manager.call(root, "user", {"action": "read", "organizationId": org["id"]})["config"][
                "name"
            ]
            == "Studio"
        )
    with pytest.raises(ValueError, match="Assign a Raticode project"):
        call(manager, other, "control", state="running")
    # Global discovery does not broaden employee capabilities.
    call(manager, org, "control", state="running")
    assert [o["id"] for o in employee_call(manager, org, "list")] == [org["id"]]
    with pytest.raises(ValueError, match="own organization"):
        manager.call(
            "",
            "employee:builder",
            {"action": "read", "organizationId": other["id"]},
            employee_id="builder",
            bound_org=org["id"],
            generation=0,
        )


def test_project_ownership_covers_existing_and_future_worktrees(manager, tmp_path) -> Any:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> Any:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        )

    git("init")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--allow-empty",
        "-m",
        "Initial",
    )
    worktree = tmp_path / "feature"
    git("worktree", "add", "-b", "feature", str(worktree))
    org = company(manager, worktree)
    assert org["config"]["projectRoots"] == [str(repo)]
    future = tmp_path / "future"
    git("worktree", "add", "-b", "future", str(future))
    assert manager.store.owns_project(org["id"], str(future))
    second = company(manager, tmp_path)
    with pytest.raises(OrganizationConflict, match="already owned"):
        call(
            manager,
            second,
            "configure",
            config={**second["config"], "projectRoots": [str(future)]},
            expectedRevision=1,
            reason="Conflicting worktree",
        )
    assert call(manager, second, "read")["revision"] == 1
    linked = call(
        manager,
        org,
        "configure",
        config={
            **org["config"],
            "projects": [{"id": "ship", "name": "Ship feature", "workspacePath": str(future)}],
        },
        expectedRevision=1,
        reason="Work in owned worktree",
    )
    assert linked["config"]["projects"][0]["workspacePath"] == str(future)


def test_ownership_changes_are_atomic_and_restore_respects_current_owner(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    detached = call(
        manager,
        org,
        "configure",
        config={**org["config"], "projectRoots": []},
        expectedRevision=1,
        reason="Release project",
    )
    new_owner = company(manager, tmp_path)
    with pytest.raises(OrganizationConflict, match="already owned"):
        call(manager, detached, "restore", revision=1, expectedRevision=2, reason="Old ownership")
    assert call(manager, detached, "read")["revision"] == 2
    assert manager.store.owns_project(new_owner["id"], str(tmp_path))
    assert not manager.store.owns_project(org["id"], str(tmp_path))


def test_initiatives_must_use_owned_projects_and_grants(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    config = {**org["config"], "projects": [{"id": "release", "workspacePath": str(other)}]}
    with pytest.raises(ValueError, match="must belong"):
        call(manager, org, "configure", config=config, expectedRevision=1, reason="Unowned")
    with pytest.raises(ValueError, match="open in this Rem session"):
        manager.call(
            str(tmp_path),
            "Rem:test",
            {
                "action": "configure",
                "organizationId": org["id"],
                "params": {
                    "config": {**config, "projectRoots": [str(tmp_path), str(other)]},
                    "expectedRevision": 1,
                    "reason": "No grant",
                },
            },
            workspace_paths={str(tmp_path)},
        )
    assert call(manager, org, "read")["revision"] == 1


def test_global_dispatch_uses_owned_default_after_original_project_is_removed(
    manager, tmp_path
) -> Any:
    captured = []

    async def stream(**options) -> Any:
        captured.append(options)
        yield {"type": "final", "message": {"body": "Reviewed"}}

    manager.stream = stream
    org = company(manager, tmp_path)
    other = tmp_path / "owned"
    other.mkdir()
    call(
        manager,
        org,
        "configure",
        config={**org["config"], "projectRoots": [str(other)]},
        expectedRevision=1,
        reason="Change default",
    )
    call(manager, org, "task_create", title="Review", assignee="builder")
    call(manager, org, "control", state="running")
    manager.dispatch()
    wait_idle(manager)
    assert captured[0]["working_dir"] == other
    assert captured[0]["workflow"]["organizationWorkspacePaths"] == [str(other)]


def test_migration_preserves_history_and_resolves_legacy_duplicate_ownership(tmp_path) -> Any:
    from gofer.ui.organization_store import OrganizationConfig, OrganizationStore

    store = OrganizationStore(tmp_path / "data")
    config = OrganizationConfig(
        name="Legacy", projects=[{"id": "launch", "workspacePath": str(tmp_path)}]
    ).model_dump()
    config.pop("projectRoots")
    runtime = {"state": "paused", "tasks": [], "heartbeats": {}, "generation": 0}
    with store.connect() as db:
        for oid in ("first", "second"):
            db.execute(
                "INSERT INTO organizations VALUES (?, ?, 0, ?, ?)",
                (oid, str(tmp_path), "{}", json.dumps(runtime)),
            )
            store._revision(db, oid, config, "user", "Original", None)
    original_digest = store.history("", "first")[0]["digest"]
    migrated = OrganizationStore(tmp_path / "data")
    assert len(migrated.list_organizations()) == 2
    assert migrated.get("", "first")["config"]["projectRoots"] == [str(tmp_path)]
    assert migrated.get("", "second")["config"]["projectRoots"] == []
    assert "workspacePath" not in migrated.get("", "second")["config"]["projects"][0]
    assert "needs review" in migrated.get("", "second")["runtime"]["pauseReason"]
    assert migrated.history("", "second")[1]["config"]["projects"][0]["workspacePath"] == str(
        tmp_path
    )
    assert migrated.history("", "first")[1]["digest"] == original_digest
    assert "Already owned elsewhere" in migrated.history("", "second")[0]["reason"]
    # Reopening does not keep creating migration revisions.
    assert OrganizationStore(tmp_path / "data").get("", "first")["revision"] == 2


def test_project_initialized_as_git_keeps_ownership(manager, tmp_path) -> Any:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    org = company(manager, repo)
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "Initial",
        ],
        check=True,
        capture_output=True,
    )
    worktree = tmp_path / "worktree"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-b", "feature", str(worktree)],
        check=True,
        capture_output=True,
    )
    assert manager.store.owns_project(org["id"], str(worktree))
    with pytest.raises(OrganizationConflict, match="already owned"):
        company(manager, worktree)
    assert len(manager.store.list_organizations()) == 1


def test_audit_employees_cannot_approve_or_remove_gates(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    task = call(
        manager, org, "task_create", title="Gated", assignee="builder", approvalRequired=True
    )
    call(manager, org, "control", state="running")
    for eid in ("builder", "lead"):
        with pytest.raises(ValueError, match="independent reviewer"):
            employee_call(manager, org, "approve", eid=eid, taskId=task["id"], expectedRevision=1)
    with pytest.raises(ValueError, match="policy"):
        employee_call(
            manager,
            org,
            "task_update",
            taskId=task["id"],
            expectedRevision=1,
            changes={"approvalRequired": False},
        )
    task = call(manager, org, "approve", taskId=task["id"], expectedRevision=1)
    with pytest.raises(ValueError, match="approval"):
        call(
            manager,
            org,
            "task_update",
            taskId=task["id"],
            expectedRevision=task["revision"],
            changes={"description": "Different plan", "status": "done", "evidence": "done"},
        )
    unchanged = call(manager, org, "read")["runtime"]["tasks"][0]
    assert unchanged["status"] == "todo"
    with pytest.raises(ValueError, match="approval"):
        call(
            manager,
            org,
            "task_create",
            title="Bypass",
            status="done",
            approvalRequired=True,
            evidence="done",
        )


def test_audit_cancellation_reaches_provider_and_revokes_capability(manager, tmp_path) -> Any:
    entered = threading.Event()
    observed: dict[str, Any] = {}

    async def stream(**options):
        observed.update(options)
        entered.set()
        while not options["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "final", "message": {"body": "Late result"}}

    manager.stream = stream
    org = company(manager, tmp_path)
    call(manager, org, "task_create", title="Cancel", assignee="builder")
    call(manager, org, "control", state="running")
    manager.dispatch()
    assert entered.wait(3)
    task = call(manager, org, "read")["runtime"]["tasks"][0]
    turn = task["claim"]
    child = call(manager, org, "task_create", title="Child", parentId=task["id"], assignee="lead")
    result = call(
        manager,
        org,
        "task_update",
        taskId=task["id"],
        expectedRevision=task["revision"],
        changes={"status": "cancelled"},
    )
    assert result["claim"] is None
    assert observed["cancel_event"].is_set()
    with pytest.raises(OrganizationConflict, match="revoked"):
        manager.call(
            org["projectRoot"],
            "employee:builder",
            {
                "action": "comment",
                "organizationId": org["id"],
                "params": {"taskId": task["id"], "body": "late"},
            },
            employee_id="builder",
            bound_org=org["id"],
            generation=0,
            run_id=turn,
        )
    wait_idle(manager)
    current = call(manager, org, "read")
    assert all(t["status"] == "cancelled" for t in current["runtime"]["tasks"])
    assert current["runtime"]["tasks"][0]["cancellation"] == "acknowledged"
    assert current["runtime"]["attempts"][turn]["status"] == "cancelled"
    assert next(t for t in current["runtime"]["tasks"] if t["id"] == child["id"])["claim"] is None


def test_audit_setup_and_thread_start_failure_release_capacity(
    manager, tmp_path, monkeypatch
) -> Any:
    org = company(manager, tmp_path)
    call(manager, org, "task_create", title="Setup", assignee="builder")
    call(manager, org, "control", state="running")
    runtime_root = manager.data_dir.parent / ".raticode-employee-runtime"
    runtime_root.write_text("not a directory")
    manager.dispatch()
    wait_idle(manager)
    task = call(manager, org, "read")["runtime"]["tasks"][0]
    assert task["status"] == "blocked" and task["claim"] is None
    assert not manager._active_roots
    runtime_root.unlink()
    call(manager, org, "reopen", taskId=task["id"], expectedRevision=task["revision"])
    monkeypatch.setattr(
        threading.Thread, "start", lambda _: (_ for _ in ()).throw(RuntimeError("start failed"))
    )
    manager.dispatch()
    assert not manager._active
    task = call(manager, org, "read")["runtime"]["tasks"][0]
    assert task["claim"] is None and task["status"] == "blocked"
    assert "start failed" in task["comments"][-1]["body"]


def test_audit_followup_preserves_nondefault_workspace(manager, tmp_path) -> Any:
    roots = [tmp_path / "a", tmp_path / "b"]
    for root in roots:
        root.mkdir()
    org = manager.store.create(
        str(roots[0]),
        {
            "name": "Two",
            "projectRoots": list(map(str, roots)),
            "employees": [{"id": "builder", "name": "Builder"}],
        },
        "user",
        "Create",
    )
    observed = []

    async def stream(**options):
        observed.append(str(options["working_dir"]))
        if len(observed) == 1:
            yield {"type": "new-thread", "message": "Continue in B", "projectRoot": str(roots[1])}
        yield {"type": "final", "message": {"body": "ready"}}

    manager.stream = stream
    call(manager, org, "task_create", title="Start", assignee="builder")
    call(manager, org, "control", state="running")
    for _ in range(2):
        manager.dispatch()
        wait_idle(manager)
    assert observed == list(map(str, roots))
    followup = call(manager, org, "read")["runtime"]["tasks"][1]
    assert followup["workspacePath"] == str(roots[1])
    assert followup["parentRunId"]


def test_dependency_wakeups_reviews_and_cycle_rejection(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    first = call(
        manager,
        org,
        "task_create",
        title="Build",
        assignee="builder",
        reviewRequired=True,
        reviewer="lead",
    )
    second = call(
        manager, org, "task_create", title="Use build", assignee="lead", dependsOn=[first["id"]]
    )
    assert second["status"] == "blocked"
    with pytest.raises(ValueError, match="cycle"):
        call(
            manager,
            org,
            "task_update",
            taskId=first["id"],
            expectedRevision=1,
            changes={"dependsOn": [second["id"]]},
        )
    with pytest.raises(ValueError, match="reviewer"):
        call(
            manager,
            org,
            "task_update",
            taskId=first["id"],
            expectedRevision=1,
            changes={"status": "done", "evidence": "claimed"},
        )
    first = call(
        manager,
        org,
        "task_update",
        taskId=first["id"],
        expectedRevision=1,
        changes={"status": "in_review", "evidence": "Verified build"},
    )
    call(manager, org, "control", state="running")
    with pytest.raises(ValueError, match="independent"):
        employee_call(
            manager,
            org,
            "review",
            taskId=first["id"],
            expectedRevision=first["revision"],
            decision="accept",
            body="Self review",
        )
    employee_call(
        manager,
        org,
        "review",
        eid="lead",
        taskId=first["id"],
        expectedRevision=first["revision"],
        decision="accept",
        body="Checked output",
    )
    tasks = call(manager, org, "read")["runtime"]["tasks"]
    assert [t["status"] for t in tasks] == ["done", "todo"]
    before = tasks[1]["revision"]
    call(manager, org, "comment", taskId=first["id"], body="Extra context")
    assert call(manager, org, "read")["runtime"]["tasks"][1]["revision"] == before


def test_retry_keys_and_compact_comment_storage(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    first = call(manager, org, "task_create", title="Once", idempotencyKey="create-1")
    assert call(manager, org, "task_create", title="Once", idempotencyKey="create-1") == first
    with pytest.raises(OrganizationConflict):
        call(manager, org, "task_create", title="Different", idempotencyKey="create-1")
    for _ in range(2):
        call(manager, org, "comment", taskId=first["id"], body="one", idempotencyKey="comment-1")
    for i in range(100):
        call(manager, org, "comment", taskId=first["id"], body=str(i) + "x" * 1024)
    with manager.store.connect() as db:
        size = db.execute(
            "SELECT SUM(length(payload)) FROM organization_events WHERE kind='comment'"
        ).fetchone()[0]
        runtime = json.loads(db.execute("SELECT runtime FROM organizations").fetchone()[0])
        count = db.execute("SELECT COUNT(*) FROM organization_comments").fetchone()[0]
    assert size < 150_000
    assert "tasks" not in runtime and "requests" not in runtime
    assert count == 101
    assert "requests" not in call(manager, org, "read")["runtime"]


def test_cosmetic_and_instruction_edits_preserve_active_work(manager, tmp_path) -> Any:
    entered, release = threading.Event(), threading.Event()

    async def stream(**options):
        entered.set()
        while not release.is_set() and not options["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "final", "message": {"body": "done"}}

    manager.stream = stream
    org = company(manager, tmp_path)
    call(manager, org, "task_create", title="Build", assignee="builder")
    call(manager, org, "control", state="running")
    manager.dispatch()
    assert entered.wait(3)
    try:
        config = org["config"]
        config["description"] = "New description"
        config["employees"][1]["instructions"] = "Next run instructions"
        changed = call(
            manager, org, "configure", config=config, expectedRevision=1, reason="Metadata"
        )
        assert changed["runtime"]["state"] == "running"
        assert changed["runtime"]["tasks"][0]["claim"]
        assert not next(iter(manager._active.values()))[1].is_set()
    finally:
        release.set()
        wait_idle(manager)


def test_routines_are_durable_deduplicated_and_queue_dependencies(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    routine = call(
        manager,
        org,
        "routine_save",
        routine={
            "name": "Daily",
            "cron": "0 9 * * 1-5",
            "timezone": "America/New_York",
            "overlap": "queue",
            "task": {"title": "Review", "assignee": "lead"},
        },
    )
    assert routine["nextAt"]
    first = call(manager, org, "routine_trigger", routineId=routine["id"], idempotencyKey="one")
    assert (
        call(manager, org, "routine_trigger", routineId=routine["id"], idempotencyKey="one")["id"]
        == first["id"]
    )
    second = call(manager, org, "routine_trigger", routineId=routine["id"], idempotencyKey="two")
    assert second["dependsOn"] == [first["id"]]
    assert call(manager, org, "routines")["total"] == 1
    assert call(manager, org, "tasks")["total"] == 2


def test_memory_invalidation_queues_one_recheck_and_rehearsal_is_read_only(
    manager, tmp_path
) -> Any:
    org = company(manager, tmp_path)
    source = tmp_path / "contract.txt"
    source.write_text("version 1")
    memory = call(
        manager, org, "memory_save", employeeId="builder", body="Uses version 1", path=str(source)
    )
    assert not memory["stale"]
    source.write_text("version 2")
    for _ in range(2):
        assert call(manager, org, "memory_check")["stale"] == [memory["id"]]
    before = call(manager, org, "read")
    result = call(manager, org, "rehearse", assumedDurationSeconds=90)
    assert result["historicalSamples"] == 0
    assert before == call(manager, org, "read")
    assert len(before["runtime"]["tasks"]) == 1
    assert not manager._active


def test_goal_links_grants_and_search_pagination(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    config = org["config"]
    config["goalRecords"] = [{"id": "ship", "title": "Ship", "owner": "lead"}]
    config["employees"][1]["workspacePaths"] = []
    org = call(manager, org, "configure", config=config, expectedRevision=1, reason="Scope")
    with pytest.raises(ValueError, match="grants"):
        call(
            manager,
            org,
            "task_create",
            title="Denied",
            assignee="builder",
            workspacePath=str(tmp_path),
        )
    with pytest.raises(ValueError, match="goal"):
        call(manager, org, "task_create", title="Bad goal", goalId="missing")
    for i in range(3):
        call(manager, org, "task_create", title=f"Ship {i}", assignee="lead", goalId="ship")
    page = call(manager, org, "tasks", query="ship", assignee="lead", limit=2)
    assert page["total"] == 3 and page["nextOffset"] == 2
    assert call(manager, org, "outcomes")["goals"][0]["tasks"] == 3


def test_artifacts_belong_to_attempt_and_review_records_outcomes(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    task = call(
        manager, org, "task_create", title="Artifact", assignee="builder", reviewRequired=True
    )
    call(manager, org, "control", state="running")
    manager.dispatch()
    wait_idle(manager)
    current = call(manager, org, "read")
    attempt = next(iter(current["runtime"]["attempts"].values()))
    assert attempt["workspacePath"] == str(tmp_path)
    evidence = tmp_path / "result.txt"
    evidence.write_text("test passed")
    with pytest.raises(ValueError, match="attempt"):
        call(manager, org, "artifact_add", taskId=task["id"], attemptId="wrong", path=str(evidence))
    artifact = call(
        manager,
        org,
        "artifact_add",
        taskId=task["id"],
        attemptId=attempt["id"],
        path=str(evidence),
        kind="test",
        check={"claimedResult": "passed"},
    )
    assert len(artifact["sha256"]) == 64
    task = call(manager, org, "read")["runtime"]["tasks"][0]
    task = call(
        manager,
        org,
        "task_update",
        taskId=task["id"],
        expectedRevision=task["revision"],
        changes={"evidence": "Verified result.txt"},
    )
    call(
        manager,
        org,
        "review",
        taskId=task["id"],
        expectedRevision=task["revision"],
        decision="accept",
        body="Verified saved result",
    )
    outcomes = call(manager, org, "outcomes")
    builder = next(e for e in outcomes["employees"] if e["employeeId"] == "builder")
    assert builder["reviewedAccepted"] == 1
    assert builder["costPerAcceptedOutcome"] is None
    assert builder["humanWaitSeconds"] >= 0


def test_workflow_contract_preview_compiles_without_execution(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    workflow = tmp_path / "workflow.rattish"
    workflow.write_text(
        "Rattish: 1\n\nWorkflow:\n  name: Preview\n\n"
        "Node hello:\n  type: bash-command\n  command: echo never-executed\n"
    )
    task = call(
        manager, org, "task_create", title="Preview", workflowContract={"path": str(workflow)}
    )
    result = call(manager, org, "workflow_preview", taskId=task["id"])
    assert result["validated"] and not result["executed"]
    assert len(result["irSha256"]) == 64
    assert call(manager, org, "attempts")["items"] == []
    assert not manager._active


def test_secret_references_need_explicit_grants_and_never_store_values(manager, tmp_path) -> Any:
    from gofer.ui.organization_operations import resolve_secrets

    class Secrets:
        def get(self, account):
            assert account == "account"
            return "test-credential-value"

    org = company(manager, tmp_path)
    record = call(manager, org, "secret_reference", id="api", account="account")
    assert record["version"] == 1
    org = manager.store.get(org["projectRoot"], org["id"])
    employee = org["config"]["employees"][0]
    resources = {"mcpServers": [{"env": {"KEY": "secret://api"}}]}
    with pytest.raises(ValueError, match="grants"):
        resolve_secrets(org, employee, resources, Secrets())
    employee["secretRefs"] = ["api"]
    assert resolve_secrets(org, employee, resources, Secrets()) == ["test-credential-value"]
    assert resources["mcpServers"][0]["env"]["KEY"] == "test-credential-value"
    assert "test-credential-value" not in json.dumps(call(manager, org, "read"))
    call(manager, org, "secret_reference", id="api", account="account", revoked=True)
    org = manager.store.get(org["projectRoot"], org["id"])
    with pytest.raises(ValueError, match="revoked"):
        resolve_secrets(
            org, employee, {"mcpServers": [{"env": {"KEY": "secret://api"}}]}, Secrets()
        )


def test_skipped_routine_trigger_cannot_be_replayed_after_previous_finishes(
    manager, tmp_path
) -> Any:
    org = company(manager, tmp_path)
    routine = call(
        manager, org, "routine_save", routine={"name": "Skip", "task": {"title": "Once"}}
    )
    first = call(manager, org, "routine_trigger", routineId=routine["id"], idempotencyKey="one")
    skipped = call(manager, org, "routine_trigger", routineId=routine["id"], idempotencyKey="two")
    assert skipped["skipped"]
    call(
        manager,
        org,
        "task_update",
        taskId=first["id"],
        expectedRevision=first["revision"],
        changes={"status": "done", "evidence": "done"},
    )
    assert (
        call(manager, org, "routine_trigger", routineId=routine["id"], idempotencyKey="two")
        == skipped
    )
    assert call(manager, org, "tasks")["total"] == 1


def test_idle_dispatch_does_not_open_write_transaction(manager, tmp_path, monkeypatch) -> Any:
    org = company(manager, tmp_path)
    call(manager, org, "control", state="running")
    monkeypatch.setattr(manager.store, "mutate", lambda *a, **kw: pytest.fail("Idle write"))
    manager.dispatch()


def test_unchanged_read_returns_small_receipt(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    before = call(manager, org, "read")
    assert call(manager, org, "read", sinceVersion=before["version"])["notModified"]
    call(manager, org, "task_create", title="New")
    assert "notModified" not in call(manager, org, "read", sinceVersion=before["version"])


def test_routing_experiment_requires_authority_and_pending_cohort(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    task = call(
        manager, org, "task_create", title="Cohort", assignee="builder", approvalRequired=True
    )
    approved = call(manager, org, "approve", taskId=task["id"], expectedRevision=1)
    call(manager, org, "control", state="running")
    with pytest.raises(ValueError, match="policy"):
        employee_call(
            manager,
            org,
            "routing_experiment",
            taskIds=[task["id"]],
            employeeId="lead",
            reason="Test",
        )
    result = call(
        manager,
        org,
        "routing_experiment",
        taskIds=[task["id"]],
        employeeId="lead",
        reason="Test comparable work with a reviewer",
    )
    assert result["previousAssignees"][task["id"]] == "builder"
    task = call(manager, org, "read")["runtime"]["tasks"][0]
    assert task["revision"] == approved["revision"] + 1
    assert task["approvedRevision"] is None
    assert task["reviewRequired"]
    assert call(manager, org, "outcomes")["experiments"][0]["accepted"] == 0


def test_github_import_requires_pin_and_reads_only_selected_bounded_package(
    tmp_path, monkeypatch
) -> Any:
    import io
    import urllib.request
    import zipfile

    from gofer.ui.organization_packages import github_package

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("repository-sha/team/COMPANY.md", "---\nname: Example\n---\nPurpose")
        bundle.writestr("repository-sha/README.md", "outside selected package")
    seen = []

    class Opener:
        def open(self, url, timeout):
            seen.append(url)
            assert timeout == 20
            return io.BytesIO(archive.getvalue())

    monkeypatch.setattr(urllib.request, "build_opener", lambda *_: Opener())
    with pytest.raises(ValueError, match="immutable"):
        github_package("owner/repository", "main")
    with pytest.raises(ValueError, match="owner/name"):
        github_package("https://example.com/repo", "a" * 40)
    files = github_package("owner/repository", "a" * 40, "team")
    assert set(files) == {"COMPANY.md"}
    assert seen == ["https://codeload.github.com/owner/repository/zip/" + "a" * 40]


def test_rehearsal_reserves_turns_and_sequences_dependencies_without_mutation(
    manager, tmp_path
) -> Any:
    org = company(manager, tmp_path)
    org = call(
        manager,
        org,
        "configure",
        config={**org["config"], "monthlyTurnLimit": 2},
        expectedRevision=1,
        reason="Two simulated turns",
    )
    first = call(manager, org, "task_create", title="First", assignee="builder")
    second = call(
        manager, org, "task_create", title="Second", assignee="builder", dependsOn=[first["id"]]
    )
    third = call(
        manager, org, "task_create", title="Third", assignee="builder", dependsOn=[second["id"]]
    )
    before = call(manager, org, "read")
    result = call(manager, org, "rehearse", assumedDurationSeconds=30)
    rows = {t["taskId"]: t for t in result["tasks"]}
    assert rows[first["id"]]["startSeconds"] == 0
    assert rows[second["id"]]["startSeconds"] == 30
    assert rows[third["id"]]["reason"] == "Monthly turn limit: company"
    assert call(manager, org, "read") == before


def test_redaction_preserves_structure_and_masks_multiline_credentials(manager, tmp_path) -> Any:
    org = company(manager, tmp_path)
    task = call(manager, org, "task_create", title="No secrets")
    secret = 'test-secret"\nvalue'
    manager._secret_values["test"] = [secret]
    call(manager, org, "comment", taskId=task["id"], body="Provider echoed " + secret)
    assert (
        call(manager, org, "read")["runtime"]["tasks"][0]["comments"][-1]["body"]
        == "Provider echoed [secret]"
    )
    assert manager._clean({"type": "final", "message": {"body": secret}}) == {
        "type": "final",
        "message": {"body": "[secret]"},
    }


def test_employee_runtime_does_not_expose_unmetered_launch_tools(tmp_path, monkeypatch) -> Any:
    from contextlib import contextmanager
    from types import SimpleNamespace

    from gofer.ui import device_tools, rem_threads
    from gofer.ui.server import GoferUiServer

    observed = {}

    @contextmanager
    def session(project, **kwargs):
        observed["swarm"] = kwargs
        yield "http://127.0.0.1:1/mock"

    async def fleet(source, control, **kwargs):
        observed["fleet"] = kwargs
        yield {"type": "final", "message": {"body": "fake"}}

    async def threads(source, **kwargs):
        async for event in source(**kwargs):
            yield event

    monkeypatch.setattr(device_tools, "stream_with_fleet_tools", fleet)
    monkeypatch.setattr(rem_threads, "stream_with_thread_tools", threads)
    server = SimpleNamespace(
        swarms=SimpleNamespace(rem_session=session),
        path_grants=SimpleNamespace(register=lambda _: "fake-grant"),
        devices=None,
        resource_limits=None,
    )

    async def run() -> list[dict[str, Any]]:
        return [
            event
            async for event in GoferUiServer._employee_chat_source(
                server,
                workflow={
                    "projectRoot": str(tmp_path),
                    "organizationWorkspacePaths": [str(tmp_path)],
                },
                permission_mode="workspace-write",
            )
        ]

    assert asyncio.run(run())[-1]["type"] == "final"
    assert observed["swarm"]["read_only"] is True
    assert observed["fleet"]["read_only_override"] is True
