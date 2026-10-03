"""Rem reaches the same organization operations as the desktop, through scoped MCP."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gofer.core.prompt_envelope import AgentResources
from gofer.ui import server as ui
from gofer.ui.chat import _build_chat_command
from gofer.ui.organization_packages import zip_package
from gofer.ui.organization_tools import ACTION_HELP, READ_ACTIONS, TOOL
from gofer.ui.organizations import OrganizationManager


async def unused_stream(**kwargs):
    raise AssertionError("Management must not execute a provider")
    yield {}


def test_disabled_organizations_keep_rem_usable_without_management_tools(tmp_path):
    received = []

    async def source(**options):
        received.append(options)
        yield {"type": "final"}

    manager = OrganizationManager(
        tmp_path, stream=unused_stream, enabled=False, start_runtime=False
    )
    options = {"workflow": {"projectRoot": str(tmp_path)}}

    async def collect() -> list[dict[str, Any]]:
        return [event async for event in manager.rem_stream(source, **options)]

    try:
        assert asyncio.run(collect()) == [{"type": "final"}]
        assert received == [options]
        assert manager._http is None
        with pytest.raises(ValueError, match="experimental and disabled"):
            manager.call("", "Rem", {"action": "create", "params": {"config": {"name": "No"}}})
        manager.dispatch()
        assert not manager.store.summaries()
    finally:
        manager.close()


@pytest.fixture
def manager(tmp_path):
    instance = OrganizationManager(tmp_path / "data", stream=unused_stream, start_runtime=False)
    yield instance
    instance.close()


def rpc(url, method, params=None) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())["result"]


def action(url, name, oid=None, **params) -> Any:
    arguments = {"action": name, "params": params}
    if oid:
        arguments["organizationId"] = oid
    result = rpc(url, "tools/call", {"name": "organization_action", "arguments": arguments})
    if result.get("isError"):
        raise ValueError(result["content"][0]["text"])
    return json.loads(result["content"][0]["text"])


@pytest.mark.parametrize(
    "provider,permission,readonly",
    [
        ("codex", "workspace-write", False),
        ("codex", "read-only", True),
        ("claude_code", "dontAsk", False),
        ("claude_code", "plan", True),
    ],
)
@pytest.mark.asyncio
async def test_global_chat_manages_organizations_without_open_projects(
    manager, tmp_path, monkeypatch, provider, permission, readonly
):
    observed = []

    async def source(**kwargs):
        observed.append(kwargs)
        url = kwargs["trusted_organization_url"]
        help_result = action(url, "help")
        assert help_result["readOnly"] is readonly
        assert help_result["workspacePaths"] == []
        assert "organization_action" in kwargs["workflow"]["remOrganizationInstructions"]
        if readonly:
            listed = rpc(url, "tools/list")["tools"][0]
            assert listed["annotations"]["readOnlyHint"]
            assert set(listed["inputSchema"]["properties"]["action"]["enum"]) == READ_ACTIONS
            with pytest.raises(ValueError, match="read-only"):
                action(url, "create", config={"name": "Denied"})
        else:
            org = action(url, "create", config={"name": "Global studio"})
            assert org["config"]["projectRoots"] == []
            assert org["runtime"]["state"] == "paused"
            changed = action(
                url,
                "configure",
                org["id"],
                config={**org["config"], "name": "Renamed"},
                expectedRevision=org["revision"],
                reason="User requested rename",
            )
            assert changed["config"]["name"] == "Renamed"
            assert action(url, "history", org["id"])[0]["actor"] == "Rem:conversation"
        yield {"type": "final", "message": {"body": "Done"}}

    monkeypatch.setattr(ui, "stream_workflow_chat", source)
    handler: Any = object.__new__(ui.GoferUiRequestHandler)
    handler.server = SimpleNamespace(organizations=manager, devices=None)
    events: list[dict[str, Any]] = []
    await handler._stream_chat_response(
        {
            "provider": provider,
            "permissionMode": permission,
            "workflow": {
                "remThreads": {"global": True, "projects": []},
                "remResources": {"shell": True, "web": True},
            },
        },
        tmp_path,
        emit=events.append,
    )
    assert [event["type"] for event in events] == ["final"], events
    assert len(observed) == 1
    assert observed[0]["workflow"]["remResources"]["shell"] is True
    assert observed[0]["permission_mode"] == permission
    with pytest.raises(urllib.error.HTTPError) as expired:
        rpc(observed[0]["trusted_organization_url"], "tools/list")
    assert expired.value.code == 401


def test_claude_management_retains_selected_native_and_mcp_tools(tmp_path):
    command = _build_chat_command(
        provider="claude_code",
        model="cli-default",
        prompt="Manage company",
        permission_mode="dontAsk",
        data_dir=tmp_path,
        resources=AgentResources.model_validate(
            {
                "shell": True,
                "mcpServers": [
                    {"name": "organizations", "url": "http://127.0.0.1:1234/capability"}
                ],
            }
        ),
    )
    allowed = command[command.index("--allowedTools") + 1 : command.index("--add-dir")]
    assert {"Read", "Glob", "Grep", "Edit", "Write", "Bash"}.issubset(allowed)
    assert "mcp__organizations__*" in command
    assert "plan" not in command


@pytest.mark.asyncio
async def test_open_project_grants_do_not_depend_on_swarm_switch(manager, tmp_path):
    root = tmp_path / "root"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()

    async def source(**kwargs):
        url = kwargs["trusted_organization_url"]
        assert action(url, "help")["workspacePaths"] == sorted([str(root), str(other)])
        org = action(url, "create", config={"name": "Studio", "projectRoots": [str(other)]})
        assert org["config"]["projectRoots"] == [str(other)]
        with pytest.raises(ValueError, match="open in this Rem session"):
            action(
                url,
                "configure",
                org["id"],
                config={**org["config"], "projectRoots": [str(tmp_path)]},
                expectedRevision=1,
                reason="Not granted",
            )
        yield {"type": "final"}

    events = [
        event
        async for event in manager.rem_stream(
            source,
            permission_mode="workspace-write",
            workflow={
                "projectRoot": str(root),
                "remSwarmAccess": {"enabled": False},
                "remThreads": {"global": False, "projects": [{"root": str(other)}]},
            },
        )
    ]
    assert events == [{"type": "final"}]


def test_catalog_covers_tab_operations_and_editable_configuration(manager, tmp_path):
    components = Path(__file__).parents[2] / "frontend" / "src" / "components"
    used = set()
    for component in components.glob("Organization*.jsx"):
        used.update(re.findall(r'(?:act|onAction|inspect)\("([a-z_]+)"', component.read_text()))
    assert used <= ACTION_HELP.keys()
    assert {"routing_experiment", "execution_resolve", "import_github", "backup"} <= used
    with manager.session(str(tmp_path), "Rem:test") as url:
        help_result = action(url, "help")
        assert set(help_result["actions"]) == set(
            TOOL["inputSchema"]["properties"]["action"]["enum"]
        )
        config = help_result["configurationSchema"]["properties"]
        assert {
            "employees",
            "teams",
            "projects",
            "projectRoots",
            "goalRecords",
            "executionTargets",
            "remSecondBrain",
            "remReportTheme",
            "maxConcurrency",
            "monthlyBudgetUsd",
        } <= config.keys()
        fields = help_result["taskSchema"]["properties"]
        assert {"execution", "workflowContract", "dependsOn", "reviewer"} <= fields.keys()
        assert "claim" not in fields


def test_rem_mcp_roundtrips_settings_tasks_routines_and_recovery(manager, tmp_path):
    with manager.session(str(tmp_path), "Rem:test", workspace_paths={str(tmp_path)}) as url:
        org = action(url, "create", config={"name": "Studio"})
        config = {
            **org["config"],
            "employees": [
                {"id": "lead", "name": "Lead"},
                {
                    "id": "builder",
                    "name": "Builder",
                    "reportsTo": "lead",
                    "monthlyTurnLimit": 50,
                    "paused": True,
                },
            ],
            "teams": [{"id": "engineering", "name": "Engineering", "manager": "lead"}],
            "projects": [{"id": "release", "name": "Release", "owner": "lead"}],
            "goalRecords": [{"id": "ship", "title": "Ship release", "owner": "lead"}],
            "executionTargets": [
                {
                    "id": "team",
                    "kind": "swarm",
                    "swarmId": "test",
                    "employees": ["builder"],
                    "turnLimit": 2,
                }
            ],
            "remReportTheme": {"format": "html", "name": "Blueprint"},
            "maxConcurrency": 2,
            "monthlyBudgetUsd": 15,
        }
        org = action(
            url, "configure", org["id"], config=config, expectedRevision=1, reason="Set up"
        )
        with pytest.raises(ValueError, match="changed"):
            action(url, "configure", org["id"], config=config, expectedRevision=1, reason="Stale")
        task = action(
            url,
            "task_create",
            org["id"],
            title="Build",
            assignee="builder",
            project="release",
            goalId="ship",
            execution={"targetId": "team"},
        )
        task = action(
            url,
            "task_update",
            org["id"],
            taskId=task["id"],
            expectedRevision=1,
            changes={"status": "cancelled"},
        )
        action(url, "reopen", org["id"], taskId=task["id"], expectedRevision=task["revision"])
        routine = action(
            url,
            "routine_save",
            org["id"],
            routine={
                "name": "Daily",
                "task": {"title": "Check", "assignee": "lead"},
                "intervalSeconds": 3600,
            },
        )
        action(
            url,
            "routine_save",
            org["id"],
            routine={**routine, "enabled": False},
            expectedRevision=routine["revision"],
        )
        action(url, "resume_all", org["id"], expectedRevision=org["revision"])
        current = action(url, "read", org["id"])
        assert not current["config"]["employees"][1]["paused"]
        assert not current["runtime"]["routines"][routine["id"]]["enabled"]
        assert current["config"]["executionTargets"][0]["id"] == "team"
        # No running organization and no provider calls are needed for management.
        assert current["runtime"]["state"] == "paused"
        restored = action(
            url,
            "restore",
            org["id"],
            revision=1,
            expectedRevision=current["revision"],
            reason="Restore original",
        )
        assert restored["config"]["employees"] == []
        assert len(restored["runtime"]["tasks"]) == 1


def package_files() -> dict[str, str]:
    return {
        "COMPANY.md": base64.b64encode(
            b"---\nname: Imported\ndescription: Test company\nslug: imported\n---\nCompany.\n"
        ).decode()
    }


def test_zip_and_directory_imports_share_mcp_backend(manager, tmp_path):
    folder = tmp_path / "package"
    folder.mkdir()
    files = package_files()
    (folder / "COMPANY.md").write_bytes(base64.b64decode(files["COMPANY.md"]))
    archive = tmp_path / "company.zip"
    archive.write_bytes(base64.b64decode(zip_package(files)))
    with manager.session("", "Rem:test", workspace_paths={str(tmp_path)}) as url:
        preview = action(url, "import_preview", zip=zip_package(files))
        assert preview["config"]["name"] == "Imported"
        for source in ({"path": str(folder)}, {"path": str(archive)}, {"zip": zip_package(files)}):
            org = action(url, "import", **source)
            assert org["config"]["name"] == "Imported"
            assert org["runtime"]["state"] == "paused"
        with pytest.raises(ValueError, match="exactly one"):
            action(url, "import_preview", files=files, zip=zip_package(files))


def test_import_path_cannot_escape_grants_or_employee_scope(manager, tmp_path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    (outside / "COMPANY.md").write_bytes(base64.b64decode(package_files()["COMPANY.md"]))
    (allowed / "escape").symlink_to(outside, target_is_directory=True)
    with manager.session("", "Rem:test", workspace_paths={str(allowed)}) as url:
        for path in (outside, allowed / "escape"):
            with pytest.raises(ValueError, match="open in this Rem session"):
                action(url, "import_preview", path=str(path))
    with manager.session("", "employee:test", employee_id="test", bound_org="none") as url:
        with pytest.raises(ValueError, match="cannot configure"):
            action(url, "import_preview", path=str(outside))


def test_rem_import_accepts_package_larger_than_default_mcp_limit(manager):
    files = package_files()
    files["assets/data.txt"] = base64.b64encode(b"x" * 900_000).decode()
    with manager.session("", "Rem:test", workspace_paths=set()) as url:
        preview = action(url, "import_preview", files=files)
        assert preview["config"]["name"] == "Imported"


def test_rem_can_discover_employee_provider_choices_without_an_organization(manager, monkeypatch):
    from gofer.core import provider_capabilities

    calls = []
    catalog = {"providers": [{"id": "codex", "enabled": True, "models": [{"id": "test-model"}]}]}

    def discover(*, refresh):
        calls.append(refresh)
        return catalog

    monkeypatch.setattr(provider_capabilities, "provider_capabilities_payload", discover)
    with manager.session("", "Rem:test", read_only=True) as url:
        assert action(url, "providers") == catalog
        assert action(url, "providers", refresh=True) == catalog
        with pytest.raises(ValueError, match="boolean"):
            action(url, "providers", refresh="false")
    assert calls == [False, True]
