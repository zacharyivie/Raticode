from __future__ import annotations

import errno
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest

from gofer.core.approvals import ApprovalRequest, ApprovalStore
from gofer.core.executor import NodeOutput
from gofer.core.operations import OperationType
from gofer.core.resources import DEFAULT_RESOURCE_LIMITS, ResourceLimits, byte_len
from gofer.core.runner import RunnerQueueStore
from gofer.rattish.editor import RattishRevisionConflict
from gofer.ui import api as api_module
from gofer.ui.api import (
    WorkflowAlreadyExistsError,
    WorkflowCreateError,
    WorkflowLogError,
    WorkflowRunError,
    WorkflowUpdateError,
    analyze_rattish_document_payload,
    apply_workflow_validation_fix_payload,
    cancel_queued_run_payload,
    create_registered_workflow_payload,
    create_workflow_payload,
    decide_workflow_approval_payload,
    delete_workflow_chat_payload,
    delete_workflow_payload,
    duplicate_workflow_payload,
    import_workflow_payload,
    latest_workflow_log_payload,
    list_workflow_approvals_payload,
    list_workflow_payloads,
    list_workflow_run_logs_payload,
    mutate_rattish_document_payload,
    open_project_payload,
    open_rattish_document_payload,
    prune_workflow_run_logs_payload,
    queue_workflow_run_payload,
    rename_workflow_payload,
    resume_workflow_payload,
    retention_settings_payload,
    run_workflow_payload,
    runner_queue_payload,
    save_rattish_document_payload,
    save_rattish_metadata_payload,
    stop_workflow_run_payload,
    update_retention_settings_payload,
    update_workflow_payload,
    workflow_plan_payload,
    workflow_run_events_payload,
    workflow_run_log_payload,
)
from gofer.ui.chat import workflow_chat_prompt_path
from gofer.utils.run_state import workflow_run_stop_path


def test_workflow_discovery_prunes_worktrees_and_dependencies(tmp_path: Path) -> None:
    for folder in (
        "workspaces/run/attempt",
        "node_modules/package",
        ".venv/lib",
        ".git",
        "venv/lib",
        ".agents",
        "__pycache__",
        "nested/node_modules/package",
    ):
        directory = tmp_path / folder
        directory.mkdir(parents=True)
        (directory / "pyproject.toml").write_text("not a workflow")
    payload = list_workflow_payloads(tmp_path)
    assert payload["workflows"] == []
    assert payload["errors"] == []


@pytest.mark.parametrize(
    ("error_number", "reason", "preserve_index"),
    [
        (errno.ENOENT, "path disappeared", False),
        (errno.ENOTDIR, "path type changed", False),
        (errno.EACCES, "permission denied", True),
        (errno.EIO, "filesystem error; retry on refresh", True),
    ],
)
def test_workflow_discovery_survives_broken_subtree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
    reason: str,
    preserve_index: bool,
) -> None:
    broken = tmp_path / "a-broken"
    broken.mkdir()
    create_workflow_payload("Hidden", broken)
    create_workflow_payload("Healthy", tmp_path)
    create_workflow_payload("Stale", tmp_path)
    list_workflow_payloads(tmp_path)
    (tmp_path / "stale.toml").unlink()
    original_scandir = os.scandir

    def failing_scandir(path: Any) -> Any:
        if Path(path) == broken:
            raise OSError(error_number, "private OS detail\nsecret", str(broken))
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", failing_scandir)
    result = list_workflow_payloads(tmp_path)

    assert [workflow["id"] for workflow in result["workflows"]] == ["healthy"]
    assert result["errors"] == [{"path": "a-broken", "message": f"Skipped: {reason}."}]
    entries = api_module._read_workflow_index(tmp_path)["workflows"]
    assert ("a-broken/hidden.toml" in entries) is preserve_index
    assert "healthy.toml" in entries
    assert "stale.toml" not in entries
    monkeypatch.setattr(os, "scandir", original_scandir)
    assert len(list_workflow_payloads(tmp_path)["workflows"]) == 2


@pytest.mark.parametrize("phase", ["parse", "payload", "index", "replace"])
def test_workflow_discovery_survives_file_races(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    create_workflow_payload("Broken", tmp_path)
    create_workflow_payload("Healthy", tmp_path)
    broken = tmp_path / "broken.toml"
    # Force parsing while retaining an old entry to exercise invalidation.
    broken.write_text(broken.read_text() + "\n# refresh\n")
    if phase == "parse":
        original_parse = api_module.AgenticWorkflow.from_file

        def disappearing_parse(path: Path) -> Any:
            if path == broken:
                path.unlink()
            return original_parse(path)

        monkeypatch.setattr(api_module.AgenticWorkflow, "from_file", disappearing_parse)
    elif phase == "payload":
        original_payload = api_module.workflow_to_payload

        def disappearing_payload(workflow: Any, path: Path, **kwargs: Any) -> Any:
            if path == broken:
                path.unlink()
            return original_payload(workflow, path, **kwargs)

        monkeypatch.setattr(api_module, "workflow_to_payload", disappearing_payload)
    else:
        original_entry = api_module._workflow_index_entry

        def changing_entry(path: Path, payload: Any) -> Any:
            if path == broken:
                path.unlink()
                if phase == "replace":
                    path.write_text('[workflow]\nid = "replacement"\nname = "Replacement"\n')
            return original_entry(path, payload)

        monkeypatch.setattr(api_module, "_workflow_index_entry", changing_entry)

    result = list_workflow_payloads(tmp_path)
    assert [workflow["id"] for workflow in result["workflows"]] == ["healthy"]
    assert len(result["errors"]) == 1
    assert result["errors"][0]["path"] == "broken.toml"
    assert "Skipped:" in result["errors"][0]["message"]
    assert "broken.toml" not in api_module._read_workflow_index(tmp_path)["workflows"]


def test_workflow_discovery_orders_workflows_and_traversal_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("Zebra", "Alpha"):
        create_workflow_payload(name, tmp_path)
    for name in ("z-broken", "a-broken"):
        (tmp_path / name).mkdir()
    original_scandir = os.scandir

    def failing_scandir(path: Any) -> Any:
        if Path(path).name.endswith("-broken"):
            raise PermissionError(errno.EACCES, "private", str(path))
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", failing_scandir)
    result = list_workflow_payloads(tmp_path)
    assert [workflow["id"] for workflow in result["workflows"]] == ["alpha", "zebra"]
    assert [error["path"] for error in result["errors"]] == ["a-broken", "z-broken"]


def test_list_workflow_payloads_serializes_real_nodes_and_edges(tmp_path: Path) -> None:
    (tmp_path / "daily.toml").write_text(
        """
[workflow]
id = "daily"
name = "Daily"

[[nodes]]
id = "collect"
type = "bash_command"
command = "echo hello"
pipe_output = true

[[nodes]]
id = "summarize"
type = "python_script"
script_path = "scripts/summarize.py"

[[edges]]
from = "collect"
to = "summarize"
condition = "on_success"
""".strip()
    )

    payload = list_workflow_payloads(tmp_path)

    assert payload["dataDir"] == str(tmp_path)
    assert payload["errors"] == []
    assert len(payload["workflows"]) == 1

    workflow = payload["workflows"][0]
    assert workflow["id"] == "daily"
    assert workflow["name"] == "Daily"
    assert workflow["sourcePath"] == "daily.toml"
    assert [node["id"] for node in workflow["nodes"]] == ["collect", "summarize"]
    assert workflow["nodes"][0]["meta"] == "echo hello"
    assert workflow["nodes"][0]["operation"]["command"] == "echo hello"
    assert workflow["nodes"][0]["settings"]["pipeOutput"] is True
    assert workflow["edges"] == [
        {
            "id": "collect-summarize-0",
            "from": "collect",
            "to": "summarize",
            "label": "on success",
            "condition": "on_success",
            "outputPattern": None,
        }
    ]


def test_ui_creation_registers_rattish_workflow_under_selected_project(tmp_path: Path) -> None:
    app_data = tmp_path / "app-data"
    project = tmp_path / "project"
    project.mkdir()

    created = create_registered_workflow_payload(
        "Review PR",
        project,
        registry_dir=app_data,
    )
    listed = list_workflow_payloads(app_data)

    assert created["id"] == "review-pr"
    assert created["projectRoot"] == str(project)
    assert created["sourcePath"] == str(project / ".raticode" / "review-pr" / "workflow.rattish")
    assert created["sourceFormat"] == "rattish"
    assert [workflow["id"] for workflow in listed["workflows"]] == ["review-pr"]


def test_open_project_payload_discovers_existing_rattish_workflows(tmp_path: Path) -> None:
    app_data = tmp_path / "app-data"
    project = tmp_path / "project"
    workflow_root = project / ".raticode" / "existing"
    workflow_root.mkdir(parents=True)
    source_path = workflow_root / "workflow.rattish"
    source_path.write_text(
        'Rattish: 1\n\nWorkflow:\n  name: "Existing"\n',
        encoding="utf-8",
    )

    payload = open_project_payload(project, registry_dir=app_data)

    assert payload["projectRoot"] == str(project)
    assert [(workflow["id"], workflow["sourcePath"]) for workflow in payload["workflows"]] == [
        ("existing", str(source_path)),
    ]


def test_rattish_document_api_analyzes_and_saves_with_revisions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    create_registered_workflow_payload("Editor API", project, registry_dir=tmp_path)
    opened = open_rattish_document_payload("editor-api", tmp_path)
    source = (
        opened["source"]
        + """Node prepare:
  type: bash-command
  command: echo ready
"""
    )

    analyzed = analyze_rattish_document_payload("editor-api", source, tmp_path)
    saved = save_rattish_document_payload(
        "editor-api",
        source,
        opened["sourceRevision"],
        tmp_path,
    )

    assert analyzed["dirty"] is True
    assert analyzed["graph"]["nodes"][0]["id"] == "prepare"
    assert saved["dirty"] is False
    with pytest.raises(RattishRevisionConflict):
        save_rattish_document_payload(
            "editor-api",
            source,
            opened["sourceRevision"],
            tmp_path,
        )

    metadata = saved["metadata"]
    metadata["canvas"]["nodes"]["prepare"] = {"x": 12, "y": 24}
    metadata_saved = save_rattish_metadata_payload(
        "editor-api",
        metadata,
        saved["metadataRevision"],
        tmp_path,
    )
    assert metadata_saved["metadata"]["canvas"]["nodes"]["prepare"] == {
        "x": 12,
        "y": 24,
    }


def test_rattish_document_mutations_preserve_unrelated_source_and_update_references(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registered = create_registered_workflow_payload("Mutation API", project, registry_dir=tmp_path)
    source_path = Path(registered["sourcePath"])
    source_path.write_text(
        """Rattish: 1

# Keep this workflow comment.
Workflow:
  name: Mutation API

Node prepare:
  type: bash-command
  command: echo ready # keep this comment
  to: review

Node review:
  type: agent
  provider: codex
  prompt: node.prepare.output.stdout
  needs: prepare
""",
        encoding="utf-8",
    )
    opened = open_rattish_document_payload("mutation-api", tmp_path)
    mutated = mutate_rattish_document_payload(
        "mutation-api",
        [
            {
                "kind": "set_field",
                "target": {"node": "prepare"},
                "field": "command",
                "value": "echo changed",
            },
            {"kind": "rename_node", "node": "prepare", "name": "build"},
            {"kind": "set_needs", "node": "review", "nodes": ["build"]},
        ],
        opened["sourceRevision"],
        tmp_path,
    )

    assert "# Keep this workflow comment." in mutated["source"]
    assert 'command: "echo changed" # keep this comment' in mutated["source"]
    assert "Node build:" in mutated["source"]
    assert "to: review" in mutated["source"]
    assert "needs: build" in mutated["source"]
    assert "node.build.output.stdout" in mutated["source"]
    assert mutated["dirty"] is False
    assert mutated["sourceEdits"]


def test_rattish_document_mutations_add_delete_and_rewrite_routes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    create_registered_workflow_payload("Graph API", project, registry_dir=tmp_path)
    opened = open_rattish_document_payload("graph-api", tmp_path)
    added = mutate_rattish_document_payload(
        "graph-api",
        [
            {"kind": "add_node", "node": "first", "node_type": "bash-command"},
            {"kind": "add_node", "node": "second", "node_type": "bash-command"},
            {"kind": "set_routes", "node": "first", "routes": ["second"]},
            {"kind": "set_needs", "node": "second", "nodes": ["first"]},
        ],
        opened["sourceRevision"],
        tmp_path,
    )
    deleted = mutate_rattish_document_payload(
        "graph-api",
        [{"kind": "delete_node", "node": "first"}],
        added["sourceRevision"],
        tmp_path,
    )

    assert "Node first:" not in deleted["source"]
    assert "needs:" not in deleted["source"]
    assert [node["id"] for node in deleted["graph"]["nodes"]] == ["second"]


def test_rattish_change_node_type_removes_only_previous_contract_fields(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registered = create_registered_workflow_payload(
        "Type Change",
        project,
        registry_dir=tmp_path,
    )
    source_path = Path(registered["sourcePath"])
    source_path.write_text(
        """Rattish: 1

Workflow:
  name: Something proper

Node asdf:
  type: agent
  provider: codex
  model: gpt-5.6-sol
  prompt: |
    Do some stuff and say the word cat!
  to:
    - things

Node things:
  type: agent
  provider: codex
  model: gpt-5.6-sol
  prompt: |
    Do some stuff and say the word cat!
  needs:
    - asdf
""",
        encoding="utf-8",
    )
    opened = open_rattish_document_payload("type-change", tmp_path)

    mutated = mutate_rattish_document_payload(
        "type-change",
        [{"kind": "change_node_type", "node": "asdf", "node_type": "approval-gate"}],
        opened["sourceRevision"],
        tmp_path,
    )

    assert """Node asdf:
  type: approval-gate
  to:
    - things

Node things:
  type: agent
  provider: codex
  model: gpt-5.6-sol
  prompt: |
    Do some stuff and say the word cat!
  needs:
    - asdf
""" in mutated["source"]
    assert [node["id"] for node in mutated["graph"]["nodes"]] == ["asdf", "things"]
    assert mutated["graph"]["nodes"][0]["type"] == "approval-gate"
    assert mutated["graph"]["edges"][0]["from"] == "asdf"
    assert mutated["graph"]["edges"][0]["to"] == "things"


def test_workflow_payload_includes_valid_node_binding_explanations(tmp_path: Path) -> None:
    (tmp_path / "bindings.toml").write_text(
        """
[workflow]
id = "bindings"
name = "Bindings"

[[nodes]]
id = "command"
type = "bash_command"
command = "echo {{trigger.name}}"
""".strip()
    )

    payload = list_workflow_payloads(tmp_path)
    workflow = payload["workflows"][0]

    assert workflow["validationBindings"][0]["destinationNode"] == "command"
    assert workflow["validationBindings"][0]["status"] == "optional"
    assert workflow["validationBindings"][0]["resolutionPhase"] == "run-start"


def test_workflow_payload_treats_agent_run_memory_as_a_literal(tmp_path: Path) -> None:
    (tmp_path / "memory.toml").write_text(
        """
[workflow]
id = "memory"
name = "Memory"

[agents.reviewer]
subscription = "codex"
working_dir = "."

[[nodes]]
id = "agent"
type = "agent"
agent_id = "reviewer"
working_dir = "."
memory = "run"

[[nodes]]
id = "common-task"
type = "common_llm_task"
agent_id = "reviewer"
working_dir = "."
memory = "run"
""".strip()
    )

    workflow = list_workflow_payloads(tmp_path)["workflows"][0]

    assert workflow["validationBindings"] == []
    assert all(
        diagnostic.get("field") != "operation.memory"
        for diagnostic in workflow["validationDiagnostics"]
    )


def test_workflow_payload_rebuilds_cached_memory_literal_diagnostics(
    tmp_path: Path,
) -> None:
    (tmp_path / "memory.toml").write_text(
        """
[workflow]
id = "memory"
name = "Memory"

[agents.reviewer]
subscription = "codex"
working_dir = "."

[[nodes]]
id = "agent"
type = "agent"
agent_id = "reviewer"
working_dir = "."
memory = "run"
""".strip()
    )
    list_workflow_payloads(tmp_path)
    index_path = tmp_path / "indexes" / "workflows.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entry = index["workflows"]["memory.toml"]
    stale_diagnostic = {
        "code": "workflow.binding_invalid",
        "severity": "error",
        "targetType": "node",
        "targetId": "agent",
        "field": "operation.memory",
        "message": ("Invalid binding at operation.memory: Unknown run field '<missing>'."),
    }
    entry["payloadVersion"] = 3
    entry["payload"]["validationDiagnostics"] = [stale_diagnostic]
    entry["payload"]["validationErrors"] = [stale_diagnostic]
    index_path.write_text(json.dumps(index), encoding="utf-8")

    workflow = list_workflow_payloads(tmp_path)["workflows"][0]

    assert workflow["validationDiagnostics"] == []
    assert workflow["validationErrors"] == []
    rebuilt_index = json.loads(index_path.read_text(encoding="utf-8"))
    assert (
        rebuilt_index["workflows"]["memory.toml"]["payloadVersion"]
        == api_module.WORKFLOW_INDEX_PAYLOAD_VERSION
    )


def test_runner_queue_payload_round_trip(tmp_path: Path) -> None:
    (tmp_path / "remote.toml").write_text(
        """
[workflow]
id = "remote"
name = "Remote"

[[nodes]]
id = "start"
type = "pass"
message = "ok"
""".strip(),
        encoding="utf-8",
    )
    RunnerQueueStore(tmp_path).register_runner(
        "runner-1",
        "CI",
        ["ci"],
        {"provider_clis": []},
    )

    queued = queue_workflow_run_payload(
        "remote",
        tmp_path,
        priority=3,
        trigger="ui",
        target_labels=["ci"],
    )
    listed = runner_queue_payload(tmp_path)
    canceled = cancel_queued_run_payload(queued["run"]["id"], tmp_path)

    assert queued["run"]["workflowId"] == "remote"
    assert queued["run"]["targetLabels"] == ["ci"]
    assert listed["executionModes"] == ["local", "remote"]
    assert listed["runners"][0]["id"] == "runner-1"
    assert listed["runs"][0]["id"] == queued["run"]["id"]
    assert canceled["run"]["status"] == "canceled"


def test_list_workflow_payloads_serializes_http_request_node(tmp_path: Path) -> None:
    (tmp_path / "api.toml").write_text(
        """
[workflow]
id = "api"
name = "API"

[[nodes]]
id = "create_issue"
type = "http_request"
method = "POST"
url = "https://api.example.test/issues"
expected_statuses = [201]
response_mode = "json"

[nodes.headers]
Authorization = "{{secret.API_TOKEN}}"

[nodes.json]
title = "Bug"

[nodes.output_mapping]
issue_id = "json.id"
""".strip()
    )

    payload = list_workflow_payloads(tmp_path)

    node = payload["workflows"][0]["nodes"][0]
    assert node["type"] == "http_request"
    assert node["meta"] == "POST https://api.example.test/issues"
    assert node["operation"]["headers"]["Authorization"] == "{{secret.API_TOKEN}}"
    assert node["operation"]["json"] == {"title": "Bug"}
    assert node["operation"]["output_mapping"] == {"issue_id": "json.id"}


def test_http_workflow_payload_masks_literal_secret_fields_and_preserves_on_save(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "api.toml"
    workflow_path.write_text(
        """
[workflow]
id = "api"
name = "API"

[[nodes]]
id = "post"
type = "http_request"
method = "POST"
url = "https://api.example.test/issues?token=real-token"
secret_fields = ["Authorization", "password", "token"]

[nodes.headers]
Authorization = "Bearer real-token"

[nodes.json]
title = "Bug"
password = "cleartext-secret"
""".strip()
    )

    payload = list_workflow_payloads(tmp_path)

    node = payload["workflows"][0]["nodes"][0]
    serialized = json.dumps(node)
    assert "Bearer real-token" not in serialized
    assert "cleartext-secret" not in serialized
    assert "token=real-token" not in serialized
    assert node["operation"]["headers"]["Authorization"] == "***"
    assert node["operation"]["json"]["password"] == "***"

    saved = update_workflow_payload("api", payload["workflows"][0], tmp_path)

    assert saved["nodes"][0]["operation"]["headers"]["Authorization"] == "***"
    assert "Bearer real-token" in workflow_path.read_text()
    assert "token=real-token" in workflow_path.read_text()
    assert "cleartext-secret" in workflow_path.read_text()


def test_notification_workflow_payload_masks_literal_secrets_and_preserves_on_save(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "notify-ui.toml"
    workflow_path.write_text(
        """
[workflow]
id = "notify-ui"
name = "Notify UI"

[[nodes]]
id = "notify"
type = "notification"
channel = "email"
webhook_url = "https://hooks.example.test/services/secret-token"
headers = { Authorization = "Bearer header-secret" }
payload = { password = "payload-secret", safe = "ok" }
email_from = "gofer@example.test"
email_to = ["ops@example.test"]
smtp_host = "smtp.example.test"
smtp_username = "smtp-user"
smtp_password = "smtp-secret"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    payload = list_workflow_payloads(tmp_path)
    workflow = payload["workflows"][0]
    node = workflow["nodes"][0]
    operation = node["operation"]

    serialized = json.dumps(workflow)
    assert "secret-token" not in serialized
    assert "header-secret" not in serialized
    assert "payload-secret" not in serialized
    assert "smtp-user" not in serialized
    assert "smtp-secret" not in serialized
    assert operation["webhook_url"] == "***"
    assert operation["headers"]["Authorization"] == "***"
    assert operation["payload"]["password"] == "***"
    assert operation["payload"]["safe"] == "ok"
    assert operation["smtp_username"] == "***"
    assert operation["smtp_password"] == "***"

    update_workflow_payload("notify-ui", workflow, tmp_path)

    stored = workflow_path.read_text(encoding="utf-8")
    assert "secret-token" in stored
    assert "header-secret" in stored
    assert "payload-secret" in stored
    assert "smtp-user" in stored
    assert "smtp-secret" in stored


def test_workflow_payload_masks_secret_declarations_and_preserves_autosave(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "secret-scope-ui.toml"
    workflow_path.write_text(
        """
[workflow]
id = "secret-scope-ui"
name = "Secret Scope UI"

[workflow.inputs.token]
type = "secret"
default = "input-secret-314"

[workflow.inputs.label]
type = "string"
default = "visible-label"

[workflow.parameters.legacy_token]
type = "secret"
default = "legacy-secret-653"

[workflow.variables.copy]
type = "string"
initial = "variable-secret-271"
secret = true

[workflow.schedule]
cron_expression = "0 9 * * *"
inputs = { token = "schedule-secret-111", label = "scheduled-label" }

[workflow.watch]
path = "."
inputs = { token = "watch-secret-222", label = "watched-label" }

[workflow.webhooks.default]
input_bindings = { token = "webhook-secret-333", label = "webhook-label" }

[component]
id = "secret-component"

[component.inputs.component_token]
type = "secret"
default = "component-secret-159"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    listed = list_workflow_payloads(tmp_path)["workflows"][0]
    serialized = json.dumps(listed)
    assert "input-secret-314" not in serialized
    assert "variable-secret-271" not in serialized
    assert "component-secret-159" not in serialized
    assert "legacy-secret-653" not in serialized
    assert "schedule-secret-111" not in serialized
    assert "watch-secret-222" not in serialized
    assert "webhook-secret-333" not in serialized
    assert listed["inputs"]["token"]["default"] == "***"
    assert listed["parameters"]["legacy_token"]["default"] == "***"
    assert listed["inputs"]["legacy_token"]["default"] == "***"
    assert listed["variables"]["copy"]["initial"] == "***"
    assert listed["component"]["inputs"]["component_token"]["default"] == "***"
    assert listed["schedule"]["inputs"] == {
        "token": "***",
        "label": "scheduled-label",
    }
    assert listed["watch"]["inputs"] == {
        "token": "***",
        "label": "watched-label",
    }
    assert listed["webhooks"]["default"]["input_bindings"] == {
        "token": "***",
        "label": "webhook-label",
    }

    index_path = tmp_path / "indexes" / "workflows.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    serialized_index = json.dumps(index)
    assert "input-secret-314" not in serialized_index
    assert "variable-secret-271" not in serialized_index
    assert "component-secret-159" not in serialized_index
    assert "schedule-secret-111" not in serialized_index
    assert "watch-secret-222" not in serialized_index
    assert "webhook-secret-333" not in serialized_index
    indexed_entry = index["workflows"]["secret-scope-ui.toml"]
    indexed_entry["payloadVersion"] = 2
    indexed_entry["payload"]["inputs"]["token"]["default"] = "input-secret-314"
    indexed_entry["payload"]["variables"]["copy"]["initial"] = "variable-secret-271"
    indexed_entry["payload"]["schedule"]["inputs"]["token"] = "schedule-secret-111"
    indexed_entry["payload"]["watch"]["inputs"]["token"] = "watch-secret-222"
    indexed_entry["payload"]["webhooks"]["default"]["input_bindings"]["token"] = (
        "webhook-secret-333"
    )
    index_path.write_text(json.dumps(index), encoding="utf-8")

    rebuilt = list_workflow_payloads(tmp_path)["workflows"][0]
    assert "input-secret-314" not in json.dumps(rebuilt)
    assert "variable-secret-271" not in json.dumps(rebuilt)
    assert "schedule-secret-111" not in json.dumps(rebuilt)
    assert "watch-secret-222" not in json.dumps(rebuilt)
    assert "webhook-secret-333" not in json.dumps(rebuilt)

    saved = update_workflow_payload("secret-scope-ui", rebuilt, tmp_path)
    stored = workflow_path.read_text(encoding="utf-8")
    assert "input-secret-314" in stored
    assert "variable-secret-271" in stored
    assert "component-secret-159" in stored
    assert "legacy-secret-653" in stored
    assert "schedule-secret-111" in stored
    assert "watch-secret-222" in stored
    assert "webhook-secret-333" in stored
    assert saved["inputs"]["token"]["default"] == "***"
    assert saved["variables"]["copy"]["initial"] == "***"
    assert saved["schedule"]["inputs"]["token"] == "***"
    assert saved["watch"]["inputs"]["token"] == "***"
    assert saved["webhooks"]["default"]["input_bindings"]["token"] == "***"

    rebuilt["inputs"]["token"]["default"] = "replacement-input-secret"
    rebuilt["variables"]["copy"]["initial"] = "replacement-variable-secret"
    rebuilt["schedule"]["inputs"]["token"] = "replacement-schedule-secret"
    rebuilt["watch"]["inputs"]["token"] = "replacement-watch-secret"
    rebuilt["webhooks"]["default"]["input_bindings"]["token"] = "replacement-webhook-secret"
    replaced = update_workflow_payload("secret-scope-ui", rebuilt, tmp_path)
    replaced_stored = workflow_path.read_text(encoding="utf-8")
    assert "replacement-input-secret" in replaced_stored
    assert "replacement-variable-secret" in replaced_stored
    assert "replacement-schedule-secret" in replaced_stored
    assert "replacement-watch-secret" in replaced_stored
    assert "replacement-webhook-secret" in replaced_stored
    assert replaced["inputs"]["token"]["default"] == "***"
    assert replaced["variables"]["copy"]["initial"] == "***"
    assert replaced["schedule"]["inputs"]["token"] == "***"
    assert replaced["watch"]["inputs"]["token"] == "***"
    assert replaced["webhooks"]["default"]["input_bindings"]["token"] == "***"

    replaced["inputs"]["token"]["type"] = "string"
    declassified = update_workflow_payload("secret-scope-ui", replaced, tmp_path)
    declassified_stored = workflow_path.read_text(encoding="utf-8")
    assert "replacement-schedule-secret" not in declassified_stored
    assert "replacement-watch-secret" not in declassified_stored
    assert "replacement-webhook-secret" not in declassified_stored
    assert declassified["schedule"]["inputs"]["token"] == "***"
    assert declassified["watch"]["inputs"]["token"] == "***"
    assert declassified["webhooks"]["default"]["input_bindings"]["token"] == "***"


def test_http_workflow_payload_preserves_masked_url_query_secrets_on_save(
    tmp_path: Path,
) -> None:
    workflow_path = tmp_path / "api.toml"
    workflow_path.write_text(
        """
[workflow]
id = "api"
name = "API"

[[nodes]]
id = "post"
type = "http_request"
method = "POST"
url = "https://api.example.test/issues?token=real-token&project=demo"
secret_fields = ["token"]
""".strip()
    )

    payload = list_workflow_payloads(tmp_path)
    node = payload["workflows"][0]["nodes"][0]

    assert node["operation"]["url"] == (
        "https://api.example.test/issues?token=%2A%2A%2A&project=demo"
    )

    saved = update_workflow_payload("api", payload["workflows"][0], tmp_path)

    assert saved["nodes"][0]["operation"]["url"] == (
        "https://api.example.test/issues?token=%2A%2A%2A&project=demo"
    )
    assert (
        'url = "https://api.example.test/issues?token=real-token&project=demo"'
        in workflow_path.read_text()
    )


def test_workflow_payload_round_trips_runtime_fanout_fields(
    tmp_path: Path,
) -> None:
    workflow = create_workflow_payload("Runtime Settings", tmp_path)
    workflow["inputs"] = {
        "workers": {"type": "number", "required": True},
        "stop_early": {"type": "boolean", "required": True},
        "project_dir": {"type": "path", "required": True},
    }
    workflow["variables"] = {
        "items": {"type": "array", "initial": ["a", "b"]},
    }
    workflow["nodes"] = [
        {
            "id": "visit",
            "type": "pass",
            "operation": {"type": "pass", "message": "{{loop.value}}"},
            "settings": {
                "forEach": "{{vars.items}}",
                "maxConcurrency": "{{inputs.workers}}",
                "failFast": "{{inputs.stop_early}}",
            },
        },
    ]

    saved = update_workflow_payload("runtime-settings", workflow, tmp_path)

    assert saved["nodes"][0]["settings"]["maxConcurrency"] == "{{inputs.workers}}"
    assert saved["nodes"][0]["settings"]["failFast"] == "{{inputs.stop_early}}"
    stored = (tmp_path / "runtime-settings.toml").read_text(encoding="utf-8")
    assert 'max_concurrency = "{{inputs.workers}}"' in stored
    assert 'fail_fast = "{{inputs.stop_early}}"' in stored


def test_list_workflow_payloads_reports_invalid_workflows(tmp_path: Path) -> None:
    (tmp_path / "broken.toml").write_text("[workflow]\nid = 1\n")

    payload = list_workflow_payloads(tmp_path)

    assert len(payload["workflows"]) == 1
    workflow = payload["workflows"][0]
    assert workflow["id"] == "broken"
    assert workflow["name"] == "Broken"
    assert workflow["invalid"] is True
    assert workflow["status"] == "Error"
    assert workflow["sourcePath"] == "broken.toml"
    assert workflow["validationError"]
    assert payload["errors"][0]["path"] == "broken.toml"


def test_list_workflow_payloads_includes_empty_workflows(tmp_path: Path) -> None:
    (tmp_path / "empty-with-agent.toml").write_text(
        """
[workflow]
id = "empty-with-agent"
name = "Empty With Agent"

[agents.codex_agent]
subscription = "codex"
working_dir = "."
""".strip()
    )

    payload = list_workflow_payloads(tmp_path)

    assert len(payload["workflows"]) == 1
    workflow = payload["workflows"][0]
    assert workflow["id"] == "empty-with-agent"
    assert workflow["name"] == "Empty With Agent"
    assert workflow["nodes"] == []
    assert workflow["edges"] == []


def test_list_workflow_payloads_uses_index_for_large_cached_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(150):
        (tmp_path / f"flow-{index:03d}.toml").write_text(
            f"""
[workflow]
id = "flow-{index:03d}"
name = "Flow {index:03d}"
""".strip(),
            encoding="utf-8",
        )

    first = list_workflow_payloads(tmp_path)
    assert len(first["workflows"]) == 150

    def fail_parse(path: Path) -> object:
        raise AssertionError(f"uncached workflow parse for {path.name}")

    monkeypatch.setattr(api_module.AgenticWorkflow, "from_file", fail_parse)

    second = list_workflow_payloads(tmp_path)

    assert len(second["workflows"]) == 150
    assert second["workflows"][0]["id"] == "flow-000"


def test_cached_workflow_payloads_do_not_scan_run_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_workflow_payload("Indexed Runs", tmp_path)
    log_dir = tmp_path / "logs" / "indexed-runs"
    log_dir.mkdir(parents=True)
    for index in range(25):
        (log_dir / f"2026-06-13T10-{index:02d}-00-0400.log").write_text(
            "2026-06-13T10:00:00-04:00 - indexed-runs started successfully\n"
            "2026-06-13T10:00:01-04:00 - INFO - indexed-runs completed successfully\n",
            encoding="utf-8",
        )
    list_workflow_run_logs_payload("indexed-runs", tmp_path)

    def fail_log_sort(log_dir: Path, *, reverse: bool) -> list[Path]:
        raise AssertionError(f"workflow list scanned logs in {log_dir}")

    def fail_parse(path: Path) -> object:
        raise AssertionError(f"uncached workflow parse for {path.name}")

    monkeypatch.setattr(api_module, "_sorted_run_log_paths", fail_log_sort)
    monkeypatch.setattr(api_module.AgenticWorkflow, "from_file", fail_parse)

    payload = list_workflow_payloads(tmp_path)

    assert payload["workflows"][0]["status"] == "Success"


def test_rebuilt_workflow_payload_uses_run_index_for_latest_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_workflow_payload("Indexed Status", tmp_path)
    log_dir = tmp_path / "logs" / "indexed-status"
    log_dir.mkdir(parents=True)
    run = log_dir / "2026-06-13T10-00-00-0400.log"
    run.write_text(
        "2026-06-13T10:00:00-04:00 - indexed-status started successfully\n"
        "2026-06-13T10:00:01-04:00 - INFO - indexed-status completed successfully\n",
        encoding="utf-8",
    )
    list_workflow_run_logs_payload("indexed-status", tmp_path, status="success")
    (tmp_path / "indexes" / "workflows.json").unlink()

    def fail_log_sort(log_dir: Path, *, reverse: bool) -> list[Path]:
        raise AssertionError(f"workflow summary scanned logs in {log_dir}")

    monkeypatch.setattr(api_module, "_sorted_run_log_paths", fail_log_sort)

    payload = list_workflow_payloads(tmp_path)

    assert payload["workflows"][0]["status"] == "Success"


def test_latest_workflow_log_payload_uses_index_without_scanning_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_workflow_payload("Indexed Latest", tmp_path)
    log_dir = tmp_path / "logs" / "indexed-latest"
    log_dir.mkdir(parents=True)
    latest = log_dir / "2026-06-13T10-00-00-0400.log"
    old = log_dir / "2026-06-13T11-00-00-0400.log"
    latest.write_text(
        "2026-06-13T10:00:00-04:00 - indexed-latest started successfully\n"
        "2026-06-13T10:00:01-04:00 - INFO - indexed-latest completed successfully newest-mtime\n",
        encoding="utf-8",
    )
    old.write_text(
        "2026-06-13T11:00:00-04:00 - indexed-latest started successfully\n"
        "2026-06-13T11:00:01-04:00 - INFO - indexed-latest completed successfully\n",
        encoding="utf-8",
    )
    os.utime(old, ns=(1_000_000_000, 1_000_000_000))
    os.utime(latest, ns=(2_000_000_000, 2_000_000_000))
    list_workflow_run_logs_payload("indexed-latest", tmp_path, status="success")

    def fail_log_sort(log_dir: Path, *, reverse: bool) -> list[Path]:
        raise AssertionError(f"latest scanned logs in {log_dir}")

    monkeypatch.setattr(api_module, "_sorted_run_log_paths", fail_log_sort)

    payload = latest_workflow_log_payload("indexed-latest", tmp_path)

    assert payload["logPath"] == f"logs/indexed-latest/{latest.name}"
    assert "newest-mtime" in payload["logText"]


def test_workflow_index_corruption_falls_back_to_rescan(tmp_path: Path) -> None:
    (tmp_path / "rescan.toml").write_text(
        """
[workflow]
id = "rescan"
name = "Rescan"
""".strip(),
        encoding="utf-8",
    )
    index_dir = tmp_path / "indexes"
    index_dir.mkdir()
    (index_dir / "workflows.json").write_text("{not json", encoding="utf-8")

    payload = list_workflow_payloads(tmp_path)

    assert payload["workflows"][0]["id"] == "rescan"


def test_list_workflow_payloads_reports_prompt_agent_ids(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "agent-3.md").write_text("old prompt\n")
    (prompts_dir / "agent-1.md").write_text("old prompt\n")
    (prompts_dir / "reviewer.md").write_text("not an auto-generated agent id\n")

    payload = list_workflow_payloads(tmp_path)

    assert payload["promptAgentIds"] == ["agent-1", "agent-3"]


def test_create_workflow_payload_writes_real_workflow(tmp_path: Path) -> None:
    workflow = create_workflow_payload("My New Workflow!", tmp_path)

    assert workflow["id"] == "my-new-workflow"
    assert workflow["name"] == "My New Workflow!"
    assert workflow["nodes"] == []
    assert workflow["edges"] == []
    assert [saved["id"] for saved in list_workflow_payloads(tmp_path)["workflows"]] == [
        "my-new-workflow"
    ]


def test_create_workflow_payload_rejects_blank_name(tmp_path: Path) -> None:
    with pytest.raises(WorkflowCreateError):
        create_workflow_payload("   ", tmp_path)


def test_create_workflow_payload_rejects_existing_workflow(tmp_path: Path) -> None:
    create_workflow_payload("Duplicate", tmp_path)

    with pytest.raises(WorkflowAlreadyExistsError):
        create_workflow_payload("Duplicate", tmp_path)


def test_import_workflow_payload_writes_toml(tmp_path: Path) -> None:
    workflow = import_workflow_payload(
        """
[workflow]
id = "imported"
name = "Imported"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
""".strip(),
        tmp_path,
    )

    assert workflow["id"] == "imported"
    assert (tmp_path / "imported.toml").exists()


@pytest.mark.parametrize("workflow_id", ["../outside", "bad/id", "bad\\id", "Bad"])
def test_import_workflow_payload_rejects_unsafe_workflow_ids(
    tmp_path: Path,
    workflow_id: str,
) -> None:
    data_dir = tmp_path / "data"
    with pytest.raises(WorkflowCreateError, match="Workflow id"):
        import_workflow_payload(
            f"""
[workflow]
id = '{workflow_id}'
name = "Unsafe"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
""".strip(),
            data_dir,
        )

    assert not data_dir.exists()
    assert not (tmp_path / "outside.toml").exists()


def test_rename_workflow_payload_updates_label_without_changing_id(tmp_path: Path) -> None:
    create_workflow_payload("Original", tmp_path)

    renamed = rename_workflow_payload("original", "Better Name", tmp_path)

    assert renamed["id"] == "original"
    assert renamed["name"] == "Better Name"
    assert (tmp_path / "original.toml").exists()
    assert 'name = "Better Name"' in (tmp_path / "original.toml").read_text()


def test_duplicate_workflow_payload_creates_copy(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Original", tmp_path)
    workflow["nodes"] = [
        {
            "id": "hello",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "echo hello",
            },
            "settings": {},
        }
    ]
    update_workflow_payload("original", workflow, tmp_path)

    source_text = (tmp_path / "original.toml").read_text()
    duplicated = duplicate_workflow_payload("original", None, tmp_path)

    assert duplicated["id"] == "original-2"
    assert duplicated["name"] == "Original-2"
    assert (tmp_path / "original.toml").exists()
    duplicate_text = (tmp_path / "original-2.toml").read_text()
    assert duplicate_text == source_text.replace(
        'id = "original"',
        'id = "original-2"',
        1,
    ).replace('name = "Original"', 'name = "Original-2"', 1)
    assert duplicated["nodes"][0]["id"] == "hello"


@pytest.mark.parametrize("workflow_id", ["../original", "original/../x", "Bad"])
def test_duplicate_workflow_payload_rejects_unsafe_workflow_ids(
    tmp_path: Path,
    workflow_id: str,
) -> None:
    create_workflow_payload("Original", tmp_path)

    with pytest.raises(WorkflowUpdateError, match="Invalid workflow id"):
        duplicate_workflow_payload(workflow_id, None, tmp_path)

    assert not (tmp_path.parent / "original-2.toml").exists()


def test_delete_workflow_payload_removes_toml_and_logs(tmp_path: Path) -> None:
    create_workflow_payload("Delete Me", tmp_path)
    log_dir = tmp_path / "logs" / "delete-me"
    log_dir.mkdir(parents=True)
    (log_dir / "2026-06-13T10-00-00-0400.log").write_text("old run\n")
    memory_dir = tmp_path / "agent-memory" / "delete-me"
    memory_dir.mkdir(parents=True)
    (memory_dir / "agent-step.json").write_text("[]\n")
    chat_prompt_path = workflow_chat_prompt_path(tmp_path, "delete-me")
    chat_prompt_path.parent.mkdir(parents=True)
    chat_prompt_path.write_text("old chat prompt\n")

    result = delete_workflow_payload("delete-me", tmp_path)

    assert result == {"workflowId": "delete-me", "deleted": True}
    assert not (tmp_path / "delete-me.toml").exists()
    assert not log_dir.exists()
    assert not memory_dir.exists()
    assert not chat_prompt_path.exists()


def test_delete_workflow_payload_removes_registered_rattish_workspace(tmp_path: Path) -> None:
    app_data = tmp_path / "app-data"
    project = tmp_path / "project"
    project.mkdir()
    created = create_registered_workflow_payload(
        "Delete Rattish",
        project,
        registry_dir=app_data,
    )
    workflow_root = Path(created["workflowRoot"])
    source_path = Path(created["sourcePath"])
    source_path.chmod(0o444)

    result = delete_workflow_payload(
        "delete-rattish",
        app_data,
        source_format="rattish",
    )

    assert result == {"workflowId": "delete-rattish", "deleted": True}
    assert not workflow_root.exists()
    assert list_workflow_payloads(app_data)["workflows"] == []


@pytest.mark.parametrize("workflow_id", ["../delete-me", "delete/me", "Delete-Me"])
def test_delete_workflow_payload_rejects_unsafe_workflow_ids(
    tmp_path: Path,
    workflow_id: str,
) -> None:
    create_workflow_payload("Delete Me", tmp_path)

    with pytest.raises(WorkflowUpdateError, match="Invalid workflow id"):
        delete_workflow_payload(workflow_id, tmp_path)

    assert (tmp_path / "delete-me.toml").exists()


def test_delete_workflow_chat_payload_removes_prompt_handoff_file(tmp_path: Path) -> None:
    chat_prompt_path = workflow_chat_prompt_path(tmp_path, "chatty")
    chat_prompt_path.parent.mkdir(parents=True)
    chat_prompt_path.write_text("old chat prompt\n")

    result = delete_workflow_chat_payload("chatty", tmp_path)

    assert result == {"workflowId": "chatty", "deleted": True}
    assert not chat_prompt_path.exists()


@pytest.mark.parametrize(
    "prompt_id",
    ["../chatty", "chatty/path", "workflow-assistant:../thread", "workflow-assistant:bad/thread"],
)
def test_delete_workflow_chat_payload_rejects_unsafe_ids(
    tmp_path: Path,
    prompt_id: str,
) -> None:
    with pytest.raises(WorkflowUpdateError):
        delete_workflow_chat_payload(prompt_id, tmp_path)


def test_latest_workflow_log_payload_returns_bounded_tail(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "chatty"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "2026-06-13T10-00-00-0400.log"
    log_path.write_text("a" * (DEFAULT_RESOURCE_LIMITS.max_api_log_response_bytes + 10))

    payload = latest_workflow_log_payload("chatty", tmp_path)

    assert payload["truncated"] is True
    assert len(payload["logText"].encode()) <= DEFAULT_RESOURCE_LIMITS.max_api_log_response_bytes


@pytest.mark.parametrize("workflow_id", ["../chatty", "chatty/path", "Chatty"])
def test_latest_workflow_log_payload_rejects_unsafe_workflow_ids(
    tmp_path: Path,
    workflow_id: str,
) -> None:
    with pytest.raises(WorkflowLogError, match="Invalid workflow id"):
        latest_workflow_log_payload(workflow_id, tmp_path)


@pytest.mark.parametrize("run_id", ["../run.log", "nested/run.log", "run.txt"])
def test_workflow_run_log_payload_rejects_unsafe_run_ids(
    tmp_path: Path,
    run_id: str,
) -> None:
    with pytest.raises(WorkflowLogError, match="Invalid run log id"):
        workflow_run_log_payload("chatty", run_id, tmp_path)


def test_log_payloads_use_workflow_resource_limit_override(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Chatty", tmp_path)
    workflow["resourceLimits"] = ResourceLimits(max_api_log_response_bytes=5).model_dump()
    update_workflow_payload("chatty", workflow, tmp_path)
    log_dir = tmp_path / "logs" / "chatty"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "2026-06-13T10-00-00-0400.log"
    log_path.write_text("0123456789")

    latest = latest_workflow_log_payload("chatty", tmp_path)
    specific = workflow_run_log_payload("chatty", log_path.name, tmp_path)

    assert latest["maxBytes"] == 5
    assert specific["maxBytes"] == 5
    assert latest["truncated"] is True
    assert specific["truncated"] is True
    assert len(latest["logText"].encode()) <= 5
    assert len(specific["logText"].encode()) <= 5


def test_update_workflow_payload_persists_nodes_edges_and_agents(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Autosave", tmp_path)
    workflow["agents"] = {
        "reviewer": {
            "agent_id": "reviewer",
            "subscription": "codex",
            "working_dir": ".",
            "prompt_path": "prompts/reviewer.md",
            "tools": ["Read"],
            "mcp_servers": [],
            "extra_paths": [str(tmp_path.parent)],
            "env": {"MODE": "review"},
        }
    }
    workflow["nodes"] = [
        {
            "id": "collect",
            "label": "Collect Git Diff",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "git diff --stat",
                "env": {"A": "1"},
            },
            "settings": {
                "pipeOutput": True,
                "retryCount": 1,
                "retryDelaySeconds": 2,
                "timeoutSeconds": 30,
            },
        },
        {
            "id": "review",
            "label": "Review Changes",
            "type": "agent",
            "operation": {
                "type": "agent",
                "agent_id": "reviewer",
                "prompt_path": "prompts/reviewer.md",
                "working_dir": ".",
                "dynamic_count": 1,
                "input_mapping": {"diff": "collect.output"},
                "fan_source": None,
            },
            "inputs": {"summary": "collect.text"},
            "settings": {},
        },
    ]
    workflow["edges"] = [
        {
            "from": "collect",
            "to": "review",
            "condition": "on_success",
            "outputPattern": None,
        }
    ]

    saved = update_workflow_payload("autosave", workflow, tmp_path)
    reloaded = list_workflow_payloads(tmp_path)["workflows"][0]

    assert saved["nodes"][0]["operation"]["command"] == "git diff --stat"
    assert saved["nodes"][0]["label"] == "Collect Git Diff"
    assert reloaded["nodes"][0]["label"] == "Collect Git Diff"
    assert reloaded["nodes"][1]["label"] == "Review Changes"
    assert reloaded["nodes"][0]["settings"]["pipeOutput"] is True
    assert reloaded["nodes"][0]["settings"]["timeoutSeconds"] == 30.0
    assert reloaded["nodes"][1]["operation"]["input_mapping"] == {"diff": "collect.output"}
    assert reloaded["nodes"][1]["inputs"] == {"summary": "collect.text"}
    assert reloaded["agents"]["reviewer"]["subscription"] == "codex"
    assert reloaded["agents"]["reviewer"]["env"] == {"MODE": "review"}
    assert reloaded["agents"]["reviewer"]["extra_paths"] == [str(tmp_path.parent)]
    assert "outside working_dir" in reloaded["resourceWarnings"][0]
    assert reloaded["edges"][0]["condition"] == "on_success"
    text = (tmp_path / "autosave.toml").read_text(encoding="utf-8")
    assert 'label = "Collect Git Diff"' in text
    assert 'label = "Review Changes"' in text
    assert "[nodes.inputs]" in text
    assert 'summary = "collect.text"' in text


def test_update_workflow_payload_edits_nested_source_path(tmp_path: Path) -> None:
    components_dir = tmp_path / "components"
    components_dir.mkdir()
    (components_dir / "review.toml").write_text(
        """
[workflow]
id = "review-workflow"
name = "Review Workflow"

[[nodes]]
id = "summarize"
type = "bash_command"
command = "echo summarize"
""".strip(),
        encoding="utf-8",
    )
    workflow = list_workflow_payloads(tmp_path)["workflows"][0]
    assert workflow["sourcePath"] == "components/review.toml"

    workflow["name"] = "Edited Review Workflow"
    workflow["nodes"][0]["label"] = "Summarize Changes"
    saved = update_workflow_payload("review-workflow", workflow, tmp_path)

    assert saved["sourcePath"] == "components/review.toml"
    assert not (tmp_path / "review-workflow.toml").exists()
    text = (components_dir / "review.toml").read_text(encoding="utf-8")
    assert 'name = "Edited Review Workflow"' in text
    assert 'label = "Summarize Changes"' in text
    index = json.loads((tmp_path / "indexes" / "workflows.json").read_text(encoding="utf-8"))
    assert "components/review.toml" in index["workflows"]
    assert "review.toml" not in index["workflows"]


def test_update_workflow_payload_records_chat_patch_revision_metadata(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Chat Patch", tmp_path)
    workflow["nodes"] = [
        {
            "id": "patched",
            "label": "Patched",
            "type": "bash_command",
            "operation": {"type": "bash_command", "command": "echo patched"},
        }
    ]
    workflow["auditMetadata"] = {
        "source": "chat_patch",
        "patchTitle": "Patch from chat",
        "appliedHunkIds": ["hunk-1"],
        "prompt": "Add a patch",
        "response": "Structured patch response",
        "threadId": "thread-1",
        "messageId": "message-1",
        "ignored": "not persisted",
    }

    update_workflow_payload("chat-patch", workflow, tmp_path)

    revision_dir = tmp_path / "workflow-revisions" / "chat-patch"
    revisions = sorted(revision_dir.glob("*.json"))
    latest = json.loads(revisions[-1].read_text(encoding="utf-8"))
    assert latest["source"] == "chat_patch"
    assert latest["metadata"]["patchTitle"] == "Patch from chat"
    assert latest["metadata"]["appliedHunkIds"] == ["hunk-1"]
    assert latest["metadata"]["prompt"] == "Add a patch"
    assert latest["metadata"]["response"] == "Structured patch response"
    assert latest["metadata"]["threadId"] == "thread-1"
    assert "ignored" not in latest["metadata"]


def test_update_workflow_payload_persists_ui_node_positions(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Positions", tmp_path)
    workflow["nodes"] = [
        {
            "id": "collect",
            "label": "Collect",
            "type": "bash_command",
            "operation": {"type": "bash_command", "command": "echo collect"},
            "x": 480,
            "y": 160,
        },
        {
            "id": "review",
            "label": "Review",
            "type": "bash_command",
            "operation": {"type": "bash_command", "command": "echo review"},
            "x": 1280.4,
            "y": 420.6,
        },
    ]
    workflow["edges"] = [{"from": "collect", "to": "review", "condition": "always"}]

    saved = update_workflow_payload("positions", workflow, tmp_path)
    reloaded = list_workflow_payloads(tmp_path)["workflows"][0]

    assert [(node["id"], node["x"], node["y"]) for node in saved["nodes"]] == [
        ("collect", 480, 160),
        ("review", 1280, 421),
    ]
    assert [(node["id"], node["x"], node["y"]) for node in reloaded["nodes"]] == [
        ("collect", 480, 160),
        ("review", 1280, 421),
    ]
    text = (tmp_path / "positions.toml").read_text(encoding="utf-8")
    assert "[ui.node_positions.collect]" in text
    assert "x = 480" in text
    assert "y = 160" in text
    assert "[ui.node_positions.review]" in text
    assert "x = 1280" in text
    assert "y = 421" in text


@pytest.mark.parametrize("workflow_id", ["../autosave", "autosave/path", "Autosave"])
def test_update_workflow_payload_rejects_unsafe_workflow_ids(
    tmp_path: Path,
    workflow_id: str,
) -> None:
    workflow = create_workflow_payload("Autosave", tmp_path)
    workflow["id"] = workflow_id

    with pytest.raises(WorkflowUpdateError):
        update_workflow_payload(workflow_id, workflow, tmp_path)


@pytest.mark.parametrize("workflow_id", ["../runnable", "runnable/path", "Runnable"])
def test_run_workflow_payload_rejects_unsafe_workflow_ids(
    tmp_path: Path,
    workflow_id: str,
) -> None:
    with pytest.raises(WorkflowRunError, match="Invalid workflow id"):
        anyio.run(run_workflow_payload, workflow_id, tmp_path, True)


def test_workflow_plan_payload_returns_execution_preview(tmp_path: Path) -> None:
    (tmp_path / "preview.toml").write_text(
        """
[workflow]
id = "preview"
name = "Preview"

[[nodes]]
id = "write"
type = "write_file"
path = "out.txt"
content = "hello"
overwrite = true
""".strip()
    )

    plan = workflow_plan_payload("preview", tmp_path)

    assert plan["workflowId"] == "preview"
    assert plan["startNodes"] == ["write"]
    assert plan["validation"]["ok"] is True
    assert plan["resourceLimits"]["max_fanout_items"] == 1000
    assert plan["generations"][0]["nodes"][0]["type"] == "write_file"
    assert f"overwrite file: {tmp_path / 'out.txt'}" in plan["destructiveActions"]


def test_workflow_plan_payload_returns_validation_errors_without_running(
    tmp_path: Path,
) -> None:
    (tmp_path / "invalid-preview.toml").write_text(
        """
[workflow]
id = "invalid-preview"
name = "Invalid Preview"

[[nodes]]
id = "first"
type = "start"

[[nodes]]
id = "second"
type = "start"
""".strip()
    )

    plan = workflow_plan_payload("invalid-preview", tmp_path)

    assert plan["workflowId"] == "invalid-preview"
    assert plan["validation"]["ok"] is False
    assert "Workflow can only contain one START node" in plan["validation"]["errors"][0]["message"]
    assert [node["id"] for node in plan["generations"][0]["nodes"]] == [
        "first",
        "second",
    ]


def test_workflow_plan_payload_uses_workflow_relative_agent_extra_paths(
    tmp_path: Path,
) -> None:
    (tmp_path / "shared").mkdir()
    (tmp_path / "relative-agent-paths.toml").write_text(
        """
[workflow]
id = "relative-agent-paths"
name = "Relative Agent Paths"

[agents.reviewer]
subscription = "codex"
working_dir = "."
extra_paths = ["shared"]

[[nodes]]
id = "review"
type = "agent"
agent_id = "reviewer"
working_dir = "."
""".strip()
    )

    plan = workflow_plan_payload("relative-agent-paths", tmp_path)

    assert plan["providerRequirements"][0]["extraPaths"] == [str((tmp_path / "shared").resolve())]


def test_update_workflow_payload_allows_empty_agent_prompt_path(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Empty Agent Prompt", tmp_path)
    workflow["agents"] = {
        "agent-1": {
            "agent_id": "agent-1",
            "subscription": "codex",
            "working_dir": ".",
            "prompt_path": "",
            "tools": [],
            "mcp_servers": [],
            "env": {},
        }
    }
    workflow["nodes"] = [
        {
            "id": "agent-node",
            "type": "agent",
            "operation": {
                "type": "agent",
                "agent_id": "agent-1",
                "prompt_path": "",
                "working_dir": ".",
                "dynamic_count": 1,
                "input_mapping": {},
                "fan_source": None,
            },
            "settings": {},
        },
    ]

    update_workflow_payload("empty-agent-prompt", workflow, tmp_path)
    text = (tmp_path / "empty-agent-prompt.toml").read_text()
    reloaded = list_workflow_payloads(tmp_path)["workflows"][0]

    assert "prompt_path" not in text
    assert reloaded["agents"]["agent-1"].get("prompt_path") is None
    assert reloaded["nodes"][0]["operation"].get("prompt_path") is None


def test_apply_workflow_validation_fix_creates_prompt_file(tmp_path: Path) -> None:
    create_workflow_payload("Prompt Fix", tmp_path)

    result = apply_workflow_validation_fix_payload(
        "prompt-fix",
        {
            "action": "create_prompt_file",
            "payload": {"path": "prompts/reviewer.md"},
        },
        tmp_path,
    )

    assert result == {"applied": True, "path": "prompts/reviewer.md"}
    assert (tmp_path / "prompts" / "reviewer.md").read_text(encoding="utf-8") == ""


def test_update_workflow_payload_persists_file_watcher(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Watched", tmp_path)
    workflow["watch"] = {
        "path": "inputs",
        "glob": "*.txt",
        "recursive": True,
        "debounce_seconds": 0.5,
        "mode": "queue",
        "max_concurrency": 2,
    }

    saved = update_workflow_payload("watched", workflow, tmp_path)
    reloaded = list_workflow_payloads(tmp_path)["workflows"][0]

    assert saved["watch"] == workflow["watch"]
    assert reloaded["watch"] == workflow["watch"]
    assert "Watching inputs" in reloaded["description"]


def test_update_workflow_payload_persists_allow_failure(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Allowed Failure", tmp_path)
    workflow["nodes"] = [
        {
            "id": "may-fail",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "exit 1",
            },
            "settings": {
                "allowFailure": True,
                "awaitAllInputs": False,
            },
        }
    ]

    saved = update_workflow_payload("allowed-failure", workflow, tmp_path)
    reloaded = list_workflow_payloads(tmp_path)["workflows"][0]
    text = (tmp_path / "allowed-failure.toml").read_text(encoding="utf-8")

    assert saved["nodes"][0]["settings"]["allowFailure"] is True
    assert saved["nodes"][0]["settings"]["awaitAllInputs"] is False
    assert reloaded["nodes"][0]["settings"]["allowFailure"] is True
    assert reloaded["nodes"][0]["settings"]["awaitAllInputs"] is False
    assert "allow_failure = true" in text
    assert "await_all_inputs = false" in text


def test_update_workflow_payload_persists_run_continuously(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Continuous", tmp_path)
    workflow["runContinuously"] = True

    saved = update_workflow_payload("continuous", workflow, tmp_path)
    reloaded = list_workflow_payloads(tmp_path)["workflows"][0]
    text = (tmp_path / "continuous.toml").read_text(encoding="utf-8")

    assert saved["runContinuously"] is True
    assert reloaded["runContinuously"] is True
    assert "run_continuously = true" in text


@pytest.mark.parametrize(
    ("read", "write", "execute"),
    [
        (False, False, False),
        (False, False, True),
        (False, True, False),
        (False, True, True),
        (True, False, False),
        (True, False, True),
        (True, True, False),
        (True, True, True),
    ],
)
def test_update_workflow_payload_round_trips_filesystem_permissions(
    tmp_path: Path,
    read: bool,
    write: bool,
    execute: bool,
) -> None:
    workflow = create_workflow_payload("Filesystem Permissions", tmp_path)
    expected = {
        "path": str(tmp_path / "shared"),
        "read": read,
        "write": write,
        "execute": execute,
    }
    workflow["filesystemAccess"] = [expected]

    saved = update_workflow_payload("filesystem-permissions", workflow, tmp_path)
    reloaded = list_workflow_payloads(tmp_path)["workflows"][0]

    assert saved["filesystemAccess"] == [expected]
    assert reloaded["filesystemAccess"] == [expected]


def test_update_workflow_payload_applies_filesystem_permission_defaults(
    tmp_path: Path,
) -> None:
    workflow = create_workflow_payload("Filesystem Defaults", tmp_path)
    workflow["filesystemAccess"] = [{"path": str(tmp_path / "shared")}]

    saved = update_workflow_payload("filesystem-defaults", workflow, tmp_path)
    reloaded = list_workflow_payloads(tmp_path)["workflows"][0]
    expected = {
        "path": str(tmp_path / "shared"),
        "read": True,
        "write": True,
        "execute": False,
    }

    assert saved["filesystemAccess"] == [expected]
    assert reloaded["filesystemAccess"] == [expected]


def test_run_workflow_payload_supports_dry_run(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Runnable", tmp_path)
    workflow["nodes"] = [
        {
            "id": "hello",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "echo hello",
            },
            "settings": {},
        }
    ]
    update_workflow_payload("runnable", workflow, tmp_path)

    plan = anyio.run(
        run_workflow_payload,
        "runnable",
        tmp_path,
        True,
        {"event": {"kind": "manual"}},
    )

    assert plan["workflowId"] == "runnable"
    assert plan["generations"][0]["nodes"][0]["id"] == "hello"
    assert plan["generations"][0]["nodes"][0]["type"] == "bash_command"
    assert "unknown shell command effects: echo hello" in plan["destructiveActions"]
    assert plan["triggerContext"]["provided"]["event"]["kind"] == "manual"
    assert not (tmp_path / "logs" / "runnable").exists()


def test_run_workflow_dry_run_returns_plan_with_validation_errors(
    tmp_path: Path,
) -> None:
    (tmp_path / "invalid-dry-run.toml").write_text(
        """
[workflow]
id = "invalid-dry-run"
name = "Invalid Dry Run"

[[nodes]]
id = "first"
type = "start"

[[nodes]]
id = "second"
type = "start"
""".strip()
    )

    plan = anyio.run(run_workflow_payload, "invalid-dry-run", tmp_path, True)

    assert plan["validation"]["ok"] is False
    assert "Workflow can only contain one START node" in plan["validation"]["errors"][0]["message"]


def test_run_workflow_blocks_execution_with_validation_errors(tmp_path: Path) -> None:
    (tmp_path / "invalid-run.toml").write_text(
        """
[workflow]
id = "invalid-run"
name = "Invalid Run"

[[nodes]]
id = "first"
type = "start"

[[nodes]]
id = "second"
type = "start"
""".strip()
    )

    with pytest.raises(WorkflowRunError, match="Workflow can only contain one START node"):
        anyio.run(run_workflow_payload, "invalid-run", tmp_path, False)


def test_run_workflow_payload_fails_before_execution_for_missing_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOFER_SECRET_API_TOKEN", raising=False)
    monkeypatch.delenv("API_TOKEN", raising=False)
    workflow = create_workflow_payload("Secret Runnable", tmp_path)
    workflow["nodes"] = [
        {
            "id": "secret",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "echo should-not-run",
                "env": {"TOKEN": "{{secret.API_TOKEN}}"},
            },
            "settings": {},
        }
    ]
    update_workflow_payload("secret-runnable", workflow, tmp_path)

    with pytest.raises(WorkflowRunError, match="Missing required secret"):
        anyio.run(run_workflow_payload, "secret-runnable", tmp_path, False)

    log_dir = tmp_path / "logs" / "secret-runnable"
    if log_dir.exists():
        assert not list(log_dir.glob("*.log"))


def test_run_workflow_payload_includes_external_agent_access_warnings(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "work"
    extra_dir = tmp_path.parent / "ui-run-extra-access"
    work_dir.mkdir()
    extra_dir.mkdir(exist_ok=True)
    workflow = create_workflow_payload("Agent Access", tmp_path)
    workflow["agents"] = {
        "reviewer": {
            "agent_id": "reviewer",
            "subscription": "codex",
            "working_dir": str(work_dir),
            "extra_paths": [str(extra_dir)],
        }
    }
    workflow["nodes"] = [
        {
            "id": "review",
            "type": "agent",
            "operation": {
                "type": "agent",
                "agent_id": "reviewer",
                "working_dir": str(work_dir),
            },
            "settings": {},
        }
    ]
    update_workflow_payload("agent-access", workflow, tmp_path)

    plan = anyio.run(run_workflow_payload, "agent-access", tmp_path, True)

    assert "outside working_dir" in plan["warnings"][0]
    assert str(extra_dir.resolve()) in plan["warnings"][0]
    assert plan["providerRequirements"][0]["extraPaths"] == [str(extra_dir.resolve())]


def test_run_workflow_payload_writes_node_output_to_log(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Logged Runnable", tmp_path)
    workflow["nodes"] = [
        {
            "id": "hello",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "echo hello",
            },
            "settings": {},
        }
    ]
    update_workflow_payload("logged-runnable", workflow, tmp_path)

    run = anyio.run(run_workflow_payload, "logged-runnable", tmp_path, False)

    assert run["success"] is True
    log_path = tmp_path / str(run["logPath"])
    text = log_path.read_text()
    assert "hello - stdout:" in text
    assert "hello" in text
    assert "hello - node output:" in text


def test_resume_workflow_payload_resumes_existing_run(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    calls = tmp_path / "calls.txt"
    workflow = create_workflow_payload("Resume Runnable", tmp_path)
    workflow["nodes"] = [
        {
            "id": "first",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": f"echo first >> {calls}; echo first",
            },
            "settings": {},
        },
        {
            "id": "second",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": (
                    f"echo second >> {calls}; "
                    f"if [ ! -f {marker} ]; then touch {marker}; exit 1; fi; "
                    "echo second-ok"
                ),
            },
            "settings": {},
        },
    ]
    workflow["edges"] = [{"from": "first", "to": "second"}]
    update_workflow_payload("resume-runnable", workflow, tmp_path)

    first = anyio.run(run_workflow_payload, "resume-runnable", tmp_path, False)

    async def run_resume() -> dict[str, object]:
        return await resume_workflow_payload(
            "resume-runnable",
            tmp_path,
            run_id=Path(str(first["logPath"])).name,
        )

    resumed = anyio.run(run_resume)

    assert resumed["success"] is True
    assert calls.read_text(encoding="utf-8").splitlines() == ["first", "second", "second"]


def test_workflow_log_payload_includes_structured_run_events(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Timeline Runnable", tmp_path)
    workflow["nodes"] = [
        {
            "id": "hello",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "echo hello",
            },
            "settings": {},
        }
    ]
    update_workflow_payload("timeline-runnable", workflow, tmp_path)

    run = anyio.run(run_workflow_payload, "timeline-runnable", tmp_path, False)
    run_id = Path(str(run["logPath"])).name
    latest = latest_workflow_log_payload("timeline-runnable", tmp_path)
    selected = workflow_run_log_payload("timeline-runnable", run_id, tmp_path)
    events = workflow_run_events_payload("timeline-runnable", run_id, tmp_path)

    assert run["runEvents"]
    assert latest["runEvents"] == selected["runEvents"] == events["runEvents"]
    assert latest["runNodes"]["hello"]["status"] == "completed"
    assert events["runNodes"]["hello"]["attempts"][0]["inputs"] == {}


def test_run_workflow_payload_applies_trigger_context_to_execution(
    tmp_path: Path,
) -> None:
    workflow = create_workflow_payload("Triggered Runnable", tmp_path)
    workflow["nodes"] = [
        {
            "id": "hello",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "printf '%s' \"$EVENT_KIND\"",
            },
            "inputs": {"env.EVENT_KIND": "trigger.event.kind"},
            "settings": {},
        }
    ]
    update_workflow_payload("triggered-runnable", workflow, tmp_path)

    run = anyio.run(
        run_workflow_payload,
        "triggered-runnable",
        tmp_path,
        False,
        {"event": {"kind": "watch"}},
    )

    assert run["success"] is True
    assert run["nodeOutputs"]["hello"]["output"] == "watch"


def test_http_node_output_payload_uses_masked_response_preview() -> None:
    node_output = NodeOutput(
        node_id="api",
        success=True,
        output='{"access_token": "real-token"}',
        exit_code=0,
        duration_seconds=0.1,
        type="http_request",
        value={"access_token": "real-token"},
        data={
            "status": 200,
            "headers": {"Authorization": "real-token"},
            "body": '{"access_token": "real-token"}',
            "json": {"access_token": "real-token"},
            "selected": {"token": "real-token"},
            "responsePreview": {
                "status": 200,
                "headers": {"Authorization": "***"},
                "body": '{"access_token": "***"}',
                "json": {"access_token": "***"},
                "selected": {"token": "***"},
                "url": "***",
                "method": "POST",
            },
        },
    )

    payload, truncated = api_module._node_outputs_payload(
        {"api": node_output},
        DEFAULT_RESOURCE_LIMITS,
    )

    assert truncated is False
    assert payload["api"]["output"] == '{"access_token": "***"}'
    assert payload["api"]["data"]["json"] == {"access_token": "***"}
    assert payload["api"]["data"]["selected"] == {"token": "***"}
    assert "real-token" not in json.dumps(payload)


def test_agent_node_output_payload_preserves_full_data_message() -> None:
    final_message = "final-" + ("m" * 200)
    node_output = NodeOutput(
        node_id="node-4",
        success=True,
        output=final_message,
        exit_code=0,
        duration_seconds=0.1,
        type=str(OperationType.AGENT),
        data={
            "message": final_message,
            "thoughts": ["thought-" + ("t" * 200)],
        },
    )

    payload, truncated = api_module._node_outputs_payload(
        {"node-4": node_output},
        ResourceLimits(
            max_api_log_response_bytes=2_000,
            max_log_bytes_per_node=80,
        ),
    )

    assert truncated is True
    assert payload["node-4"]["data"]["message"] == final_message
    assert "node-4 data.message truncated" not in payload["node-4"]["data"]["message"]


def test_agent_node_output_payload_keeps_data_message_when_data_exceeds_budget() -> None:
    final_message = "final-" + ("m" * 500)
    node_output = NodeOutput(
        node_id="node-4",
        success=True,
        output=final_message,
        exit_code=0,
        duration_seconds=0.1,
        type=str(OperationType.AGENT),
        data={
            "message": final_message,
            "prompt": "prompt-" + ("p" * 500),
            "thoughts": ["thought-" + ("t" * 500)],
        },
    )

    payload, truncated = api_module._node_outputs_payload(
        {"node-4": node_output},
        ResourceLimits(
            max_api_log_response_bytes=180,
            max_log_bytes_per_node=80,
        ),
    )

    assert truncated is True
    assert payload["node-4"]["data"] == {"message": final_message}


def test_run_workflow_payload_bounds_aggregate_node_output_text(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Bounded Response", tmp_path)
    workflow["resourceLimits"] = ResourceLimits(
        max_api_log_response_bytes=120,
        max_log_bytes_per_node=1_000,
    ).model_dump()
    workflow["nodes"] = [
        {
            "id": "first",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "printf '%200s' | tr ' ' a",
            },
            "settings": {},
        },
        {
            "id": "second",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "printf '%200s' | tr ' ' b",
            },
            "settings": {},
        },
    ]
    update_workflow_payload("bounded-response", workflow, tmp_path)

    run = anyio.run(run_workflow_payload, "bounded-response", tmp_path, False)

    output_bytes = sum(
        byte_len(node["output"]) + sum(byte_len(fan["output"]) for fan in node["fanOutputs"])
        for node in run["nodeOutputs"].values()
    )
    assert run["success"] is True
    assert run["nodeOutputsTruncated"] is True
    assert run["nodeOutputsMaxBytes"] == 120
    assert output_bytes <= 120
    assert len(json.dumps(run["nodeOutputs"], separators=(",", ":")).encode()) <= 120


def test_stop_workflow_run_payload_reports_no_active_run(tmp_path: Path) -> None:
    result = stop_workflow_run_payload("not-running", tmp_path)

    assert result == {
        "workflowId": "not-running",
        "stopped": False,
        "message": "No active run",
    }


def test_stop_workflow_run_payload_disables_run_continuously(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Continuous Stop", tmp_path)
    workflow["runContinuously"] = True
    update_workflow_payload("continuous-stop", workflow, tmp_path)

    result = stop_workflow_run_payload("continuous-stop", tmp_path)
    reloaded = list_workflow_payloads(tmp_path)["workflows"][0]

    assert result["stopped"] is True
    assert reloaded["runContinuously"] is False
    assert "run_continuously" not in (tmp_path / "continuous-stop.toml").read_text(encoding="utf-8")


def test_stop_workflow_run_payload_stops_specific_running_log(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "stop-me"
    log_dir.mkdir(parents=True)
    run_id = "2026-06-17T12-00-00-0400.log"
    (log_dir / run_id).write_text(
        "2026-06-17T12:00:00-04:00 - stop-me started successfully\n",
        encoding="utf-8",
    )

    result = stop_workflow_run_payload("stop-me", tmp_path, run_id=run_id)

    assert result == {
        "workflowId": "stop-me",
        "runId": run_id,
        "stopped": True,
        "message": "Stop requested",
    }
    assert workflow_run_stop_path("stop-me", run_id, tmp_path).exists()


async def test_stop_workflow_run_payload_stops_active_run(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Stop Me", tmp_path)
    workflow["nodes"] = [
        {
            "id": "sleep",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "sleep 5",
            },
            "settings": {},
        }
    ]
    update_workflow_payload("stop-me", workflow, tmp_path)
    run_result = None

    async def run_workflow() -> None:
        nonlocal run_result
        run_result = await run_workflow_payload("stop-me", tmp_path, False)

    with anyio.fail_after(3):
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_workflow)
            for _ in range(30):
                await anyio.sleep(0.05)
                stop_result = stop_workflow_run_payload("stop-me", tmp_path)
                if stop_result["stopped"]:
                    break
            else:  # pragma: no cover
                raise AssertionError("Run did not become active")

    assert run_result is not None
    assert run_result["success"] is False
    assert (
        "stopped by user" in run_result["logText"]
        or "Process stopped by user" in run_result["logText"]
    )


async def test_run_workflow_payload_allows_concurrent_runs(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Concurrent", tmp_path)
    workflow["nodes"] = [
        {
            "id": "sleep",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "sleep 5",
            },
            "settings": {},
        }
    ]
    update_workflow_payload("concurrent", workflow, tmp_path)
    run_results = []

    async def run_workflow() -> None:
        run_results.append(await run_workflow_payload("concurrent", tmp_path, False))

    with anyio.fail_after(4):
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_workflow)
            tg.start_soon(run_workflow)
            for _ in range(40):
                await anyio.sleep(0.05)
                logs = list_workflow_run_logs_payload("concurrent", tmp_path)["runs"]
                if sum(run["status"] == "running" for run in logs) == 2:
                    break
            else:  # pragma: no cover
                raise AssertionError("Concurrent workflow runs did not both become active")
            stop_workflow_run_payload("concurrent", tmp_path)

    assert len(run_results) == 2
    assert all(run["success"] is False for run in run_results)
    assert len(list_workflow_run_logs_payload("concurrent", tmp_path)["runs"]) == 2


async def test_run_workflow_payload_rejects_concurrent_continuous_runs(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Continuous Concurrent", tmp_path)
    workflow["runContinuously"] = True
    workflow["nodes"] = [
        {
            "id": "sleep",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "sleep 5",
            },
            "settings": {},
        }
    ]
    update_workflow_payload("continuous-concurrent", workflow, tmp_path)
    first_run = None
    second_error = None

    async def run_first() -> None:
        nonlocal first_run
        first_run = await run_workflow_payload("continuous-concurrent", tmp_path, False)

    with anyio.fail_after(4):
        async with anyio.create_task_group() as tg:
            tg.start_soon(run_first)
            for _ in range(40):
                await anyio.sleep(0.05)
                logs = list_workflow_run_logs_payload("continuous-concurrent", tmp_path)["runs"]
                if any(run["status"] == "running" for run in logs):
                    break
            else:  # pragma: no cover
                raise AssertionError("Continuous run did not become active")
            try:
                await run_workflow_payload("continuous-concurrent", tmp_path, False)
            except WorkflowRunError as exc:
                second_error = str(exc)
            stop_workflow_run_payload("continuous-concurrent", tmp_path)

    assert first_run is not None
    assert first_run["success"] is False
    assert second_error is not None
    assert "already running" in second_error


def test_latest_workflow_log_payload_reads_last_run(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Latest Log", tmp_path)
    workflow["nodes"] = [
        {
            "id": "hello",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "echo hello",
            },
            "settings": {},
        }
    ]
    update_workflow_payload("latest-log", workflow, tmp_path)
    anyio.run(run_workflow_payload, "latest-log", tmp_path, False)

    payload = latest_workflow_log_payload("latest-log", tmp_path)

    assert payload["logPath"]
    assert "latest-log started successfully" in payload["logText"]
    assert "hello" in payload["logText"]


def test_workflow_log_payloads_include_historical_http_response_preview(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs" / "api-history"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "2026-06-13T10-00-00-0400.log"
    log_path.write_text(
        "2026-06-13T10:00:00-04:00 - api-history started successfully\n"
        "2026-06-13T10:00:00-04:00 - INFO - api-history completed successfully\n"
    )
    node_output = NodeOutput(
        node_id="api",
        success=True,
        output='{"access_token": "real-token"}',
        exit_code=0,
        duration_seconds=0.1,
        type="http_request",
        data={
            "status": 200,
            "body": '{"access_token": "real-token"}',
            "responsePreview": {
                "status": 200,
                "method": "POST",
                "url": "https://api.example.test/issues",
                "body": '{"access_token": "***"}',
                "json": {"access_token": "***"},
                "selected": {"token": "***"},
            },
        },
    )
    node_outputs, truncated = api_module._node_outputs_payload(
        {"api": node_output},
        DEFAULT_RESOURCE_LIMITS,
    )
    api_module._write_run_node_outputs_payload(
        log_path,
        workflow_id="api-history",
        limits=DEFAULT_RESOURCE_LIMITS,
        node_outputs=node_outputs,
        node_outputs_truncated=truncated,
    )

    latest = latest_workflow_log_payload("api-history", tmp_path)
    selected = workflow_run_log_payload("api-history", log_path.name, tmp_path)

    assert latest["nodeOutputs"]["api"]["data"]["body"] == '{"access_token": "***"}'
    assert selected["nodeOutputs"]["api"]["data"]["selected"] == {"token": "***"}
    assert "real-token" not in json.dumps(latest["nodeOutputs"])
    assert "real-token" not in json.dumps(selected["nodeOutputs"])


def test_list_workflow_run_logs_payload_returns_runs_newest_first(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    first = log_dir / "2026-06-13T10-00-00-0400.log"
    second = log_dir / "2026-06-13T11-00-00-0400.log"
    first.write_text(
        "2026-06-13T10:00:00-04:00 - history-flow started successfully\n"
        "2026-06-13T10:00:00-04:00 - INFO - history-flow completed successfully\n"
    )
    second.write_text(
        "2026-06-13T11:00:00-04:00 - history-flow started successfully\n"
        "2026-06-13T11:00:00-04:00 - ERROR - history-flow failed due to bad\n"
    )

    payload = list_workflow_run_logs_payload("history-flow", tmp_path)

    assert [run["id"] for run in payload["runs"]] == [second.name, first.name]
    assert payload["runs"][0]["status"] == "error"
    assert payload["runs"][1]["status"] == "success"


def test_list_workflow_run_logs_payload_paginates_and_filters(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    for index in range(5):
        status_line = (
            "INFO - history-flow completed successfully"
            if index % 2 == 0
            else "ERROR - history-flow failed due to bad"
        )
        run = log_dir / f"2026-06-13T1{index}-00-00-0400.log"
        run.write_text(
            f"2026-06-13T1{index}:00:00-04:00 - history-flow started successfully\n"
            f"2026-06-13T1{index}:00:01-04:00 - {status_line}\n",
            encoding="utf-8",
        )

    payload = list_workflow_run_logs_payload(
        "history-flow",
        tmp_path,
        offset=1,
        limit=2,
        status="success",
    )

    assert payload["pagination"] == {"offset": 1, "limit": 2, "total": 3}
    assert len(payload["runs"]) == 2
    assert all(run["status"] == "success" for run in payload["runs"])


def test_list_workflow_run_logs_payload_filters_dates_with_timezone(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    old = log_dir / "2026-06-13T10-00-00-0400.log"
    old.write_text(
        "2026-06-13T10:00:00-04:00 - history-flow started successfully\n"
        "2026-06-13T10:00:01-04:00 - INFO - history-flow completed successfully\n",
        encoding="utf-8",
    )
    newer = log_dir / "2026-06-13T12-00-00-0400.log"
    newer.write_text(
        "2026-06-13T12:00:00-04:00 - history-flow started successfully\n"
        "2026-06-13T12:00:01-04:00 - INFO - history-flow completed successfully\n",
        encoding="utf-8",
    )

    payload = list_workflow_run_logs_payload(
        "history-flow",
        tmp_path,
        started_after=datetime(2026, 6, 13, 15, 0, tzinfo=UTC),
    )

    assert [run["id"] for run in payload["runs"]] == [newer.name]


@pytest.mark.parametrize("workflow_id", ["../history-flow", "history/flow", "History-Flow"])
def test_list_workflow_run_logs_payload_rejects_unsafe_workflow_ids(
    tmp_path: Path,
    workflow_id: str,
) -> None:
    with pytest.raises(WorkflowLogError, match="Invalid workflow id"):
        list_workflow_run_logs_payload(workflow_id, tmp_path)


def test_list_workflow_run_logs_payload_detects_multiline_failure(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "gofer-demo"
    log_dir.mkdir(parents=True)
    run = log_dir / "2026-06-18T09-52-26-0400.log"
    run.write_text(
        "2026-06-18T09:52:26-04:00 - gofer-demo started successfully\n"
        "2026-06-18T09:52:26-04:00 - NODE - summarize-demo - attempt 1 finished "
        "success=False exit_code=1 duration=0.08s\n"
        "2026-06-18T09:52:26-04:00 - ERROR - gofer-demo failed due to node "
        "summarize-demo failed: WARNING: proceeding\n"
        "Reading additional input from stdin...\n"
        "Error: failed to initialize in-process app-server client: Read-only file system "
        "(os error 30)\n",
        encoding="utf-8",
    )

    payload = list_workflow_run_logs_payload("gofer-demo", tmp_path)

    assert payload["runs"][0]["status"] == "error"


def test_workflow_run_log_payload_reads_specific_run(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    run = log_dir / "2026-06-13T10-00-00-0400.log"
    run.write_text(
        "2026-06-13T10:00:00-04:00 - history-flow started successfully\n"
        "custom output\n"
        "2026-06-13T10:00:00-04:00 - INFO - history-flow completed successfully\n"
    )

    payload = workflow_run_log_payload("history-flow", run.name, tmp_path)

    assert payload["runId"] == run.name
    assert payload["status"] == "success"
    assert "custom output" in payload["logText"]


def test_workflow_run_log_payload_reads_byte_range(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    run = log_dir / "2026-06-13T10-00-00-0400.log"
    run.write_text("0123456789", encoding="utf-8")

    payload = workflow_run_log_payload(
        "history-flow",
        run.name,
        tmp_path,
        offset=3,
        limit=4,
        include_details=False,
    )

    assert payload["logText"] == "3456"
    assert payload["logStart"] == 3
    assert payload["logEnd"] == 7
    assert payload["logSize"] == 10
    assert payload["hasMoreBefore"] is True
    assert payload["hasMoreAfter"] is True


def test_workflow_run_log_payload_uses_bounded_status_without_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    run = log_dir / "2026-06-13T10-00-00-0400.log"
    run.write_text("running\n", encoding="utf-8")

    def fail_unbounded_status(path: Path) -> str:
        raise AssertionError(f"unbounded status read for {path.name}")

    monkeypatch.setattr(api_module, "_log_status", fail_unbounded_status)

    payload = workflow_run_log_payload(
        "history-flow",
        run.name,
        tmp_path,
        tail_bytes=4,
        include_details=False,
    )

    assert payload["logText"] == "ing\n"
    assert payload["status"] == "running"


def test_list_workflow_run_logs_payload_reads_trigger_from_bounded_head(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    run = log_dir / "2026-06-13T10-00-00-0400.log"
    run.write_text(
        "2026-06-13T10:00:00-04:00 - INFO - trigger=schedule=nightly\n" + ("x" * 200_000),
        encoding="utf-8",
    )

    payload = list_workflow_run_logs_payload("history-flow", tmp_path)

    assert payload["runs"][0]["triggerType"] == "schedule"
    assert payload["runs"][0]["logSizeBytes"] > 64 * 1024


def test_list_workflow_run_logs_payload_summarizes_only_requested_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    for index in range(10):
        run = log_dir / f"2026-06-13T10-00-{index:02d}-0400.log"
        run.write_text(
            f"2026-06-13T10:00:{index:02d}-04:00 - history-flow started successfully\n",
            encoding="utf-8",
        )

    calls: list[str] = []

    def fake_log_status(path: Path) -> str:
        calls.append(path.name)
        return "running"

    monkeypatch.setattr(api_module, "_log_status", fake_log_status)

    payload = list_workflow_run_logs_payload("history-flow", tmp_path, limit=3)

    assert len(payload["runs"]) == 3
    assert len(calls) == 3


def test_list_workflow_run_logs_payload_uses_summary_sidecar_for_cheap_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    run = log_dir / "2026-06-13T10-00-00-0400.log"
    run.write_text(
        "2026-06-13T10:00:00-04:00 - history-flow started successfully\n",
        encoding="utf-8",
    )
    run.with_suffix(".summary.json").write_text(
        json.dumps(
            {
                "startedAt": "2026-06-13T10:00:00-04:00",
                "finishedAt": "2026-06-13T10:00:01-04:00",
                "durationSeconds": 1,
                "status": "success",
                "success": True,
                "triggerType": "manual",
                "nodeCount": 2,
            }
        ),
        encoding="utf-8",
    )

    def fail_events_read(path: Path) -> dict[str, object]:
        raise AssertionError(f"events read for cheap summary {path.name}")

    def fail_unbounded_status(path: Path) -> str:
        raise AssertionError(f"unbounded status read for cheap summary {path.name}")

    monkeypatch.setattr(api_module, "_read_run_events_document", fail_events_read)
    monkeypatch.setattr(api_module, "_log_status", fail_unbounded_status)

    payload = list_workflow_run_logs_payload("history-flow", tmp_path, status="success")

    assert payload["runs"][0]["id"] == run.name
    assert payload["runs"][0]["nodeCount"] == 2
    assert payload["runs"][0]["status"] == "success"


def test_list_workflow_run_logs_payload_uses_index_for_large_cached_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    for index in range(1000):
        run = log_dir / f"2026-06-13T10-{index // 60:02d}-{index % 60:02d}-0400.log"
        run.write_text(
            f"2026-06-13T10:{index // 60:02d}:{index % 60:02d}-04:00 - "
            "history-flow started successfully\n"
            f"2026-06-13T10:{index // 60:02d}:{index % 60:02d}-04:00 - "
            "INFO - history-flow completed successfully\n",
            encoding="utf-8",
        )

    first = list_workflow_run_logs_payload(
        "history-flow",
        tmp_path,
        limit=5,
        status="success",
    )
    assert len(first["runs"]) == 5

    def fail_bounded_status(path: Path, max_bytes: int = 64 * 1024) -> str:
        raise AssertionError(f"uncached status read for {path.name}")

    def fail_log_sort(log_dir: Path, *, reverse: bool) -> list[Path]:
        raise AssertionError(f"cached history scanned logs in {log_dir}")

    monkeypatch.setattr(api_module, "_log_status_bounded", fail_bounded_status)
    monkeypatch.setattr(api_module, "_sorted_run_log_paths", fail_log_sort)

    second = list_workflow_run_logs_payload(
        "history-flow",
        tmp_path,
        limit=5,
        status="success",
    )

    assert len(second["runs"]) == 5
    assert second["pagination"]["total"] == 1000

    unfiltered = list_workflow_run_logs_payload("history-flow", tmp_path, limit=5)

    assert len(unfiltered["runs"]) == 5
    assert unfiltered["pagination"]["total"] == 1000


def test_paginated_run_history_does_not_prune_index_entries(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    for index in range(5):
        run = log_dir / f"2026-06-13T1{index}-00-00-0400.log"
        run.write_text(
            f"2026-06-13T1{index}:00:00-04:00 - history-flow started successfully\n"
            f"2026-06-13T1{index}:00:01-04:00 - INFO - history-flow completed successfully\n",
            encoding="utf-8",
        )

    indexed = list_workflow_run_logs_payload("history-flow", tmp_path, status="success")
    assert indexed["pagination"]["total"] == 5

    index_path = tmp_path / "indexes" / "runs-history-flow.json"
    assert len(json.loads(index_path.read_text(encoding="utf-8"))["runs"]) == 5

    first_page = list_workflow_run_logs_payload("history-flow", tmp_path, limit=2)
    assert [run["id"] for run in first_page["runs"]] == [
        "2026-06-13T14-00-00-0400.log",
        "2026-06-13T13-00-00-0400.log",
    ]
    assert len(json.loads(index_path.read_text(encoding="utf-8"))["runs"]) == 5

    older_page = list_workflow_run_logs_payload("history-flow", tmp_path, offset=4, limit=1)
    assert [run["id"] for run in older_page["runs"]] == ["2026-06-13T10-00-00-0400.log"]
    assert len(json.loads(index_path.read_text(encoding="utf-8"))["runs"]) == 5


def test_run_index_corruption_falls_back_to_rescan(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    run = log_dir / "2026-06-13T10-00-00-0400.log"
    run.write_text(
        "2026-06-13T10:00:00-04:00 - history-flow started successfully\n"
        "2026-06-13T10:00:01-04:00 - INFO - history-flow completed successfully\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / "indexes"
    index_dir.mkdir()
    (index_dir / "runs-history-flow.json").write_text("{not json", encoding="utf-8")

    payload = list_workflow_run_logs_payload("history-flow", tmp_path)

    assert payload["runs"][0]["id"] == run.name
    assert payload["runs"][0]["status"] == "success"


def test_run_log_search_is_bounded_by_resource_limits(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Search Bound", tmp_path)
    workflow["resourceLimits"] = ResourceLimits(max_api_log_response_bytes=20).model_dump()
    update_workflow_payload("search-bound", workflow, tmp_path)
    log_dir = tmp_path / "logs" / "search-bound"
    log_dir.mkdir(parents=True)
    first = log_dir / "2026-06-13T10-00-00-0400.log"
    second = log_dir / "2026-06-13T11-00-00-0400.log"
    first.write_text("a" * 100 + "needle\n", encoding="utf-8")
    second.write_text("b" * 100 + "needle\n", encoding="utf-8")

    payload = list_workflow_run_logs_payload("search-bound", tmp_path, search="needle")

    assert payload["runs"] == []
    assert payload["pagination"]["searchTruncated"] is True
    assert payload["pagination"]["searchScannedBytes"] == 20


def test_prune_workflow_run_logs_payload_updates_run_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    old = log_dir / "2020-01-01T10-00-00-0000.log"
    old.write_text(
        "2020-01-01T10:00:00+00:00 - history-flow started successfully\n"
        "2020-01-01T10:00:01+00:00 - INFO - history-flow completed successfully\n",
        encoding="utf-8",
    )
    sidecars = [
        old.with_suffix(".events.json"),
        old.with_suffix(".outputs.json"),
        old.with_suffix(".summary.json"),
        old.with_suffix(".trigger.json"),
        old.with_suffix(".resume.json"),
    ]
    for sidecar in sidecars:
        sidecar.write_text("{}", encoding="utf-8")
    list_workflow_run_logs_payload("history-flow", tmp_path, status="success")

    def fail_log_sort(log_dir: Path, *, reverse: bool) -> list[Path]:
        raise AssertionError(f"prune scanned logs in {log_dir}")

    monkeypatch.setattr(api_module, "_sorted_run_log_paths", fail_log_sort)

    prune_workflow_run_logs_payload(
        "history-flow",
        tmp_path,
        keep_last=0,
        keep_days=0,
        dry_run=False,
    )

    index = json.loads((tmp_path / "indexes" / "runs-history-flow.json").read_text())
    assert old.name not in index["runs"]
    assert not old.exists()
    assert all(not sidecar.exists() for sidecar in sidecars)


def test_retention_settings_persist_and_prune_uses_saved_defaults(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    old = log_dir / "2020-01-01T10-00-00-0000.log"
    old.write_text(
        "2020-01-01T10:00:00+00:00 - history-flow started successfully\n"
        "2020-01-01T10:00:01+00:00 - INFO - history-flow completed successfully\n",
        encoding="utf-8",
    )

    saved = update_retention_settings_payload(
        tmp_path,
        workflow_id="history-flow",
        settings={"keepDays": 1, "keepFailedDays": 2, "keepLast": 0},
    )
    preview = prune_workflow_run_logs_payload("history-flow", tmp_path, dry_run=True)

    assert saved["settings"] == {"keepDays": 1, "keepFailedDays": 2, "keepLast": 0}
    assert retention_settings_payload(tmp_path, "history-flow")["settings"] == saved["settings"]
    assert [run["id"] for run in preview["runs"]] == [old.name]


def test_run_workflow_payload_applies_saved_retention_policy(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Auto Retention", tmp_path)
    workflow["nodes"] = [
        {
            "id": "hello",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "echo hello",
            },
            "settings": {},
        }
    ]
    update_workflow_payload("auto-retention", workflow, tmp_path)
    log_dir = tmp_path / "logs" / "auto-retention"
    log_dir.mkdir(parents=True)
    old = log_dir / "2020-01-01T10-00-00-0000.log"
    old.write_text(
        "2020-01-01T10:00:00+00:00 - auto-retention started successfully\n"
        "2020-01-01T10:00:01+00:00 - INFO - auto-retention completed successfully\n",
        encoding="utf-8",
    )
    update_retention_settings_payload(
        tmp_path,
        workflow_id="auto-retention",
        settings={"keepDays": 1, "keepFailedDays": 2, "keepLast": 1},
    )

    result = anyio.run(run_workflow_payload, "auto-retention", tmp_path, False)

    assert result["success"] is True
    assert not old.exists()
    assert result["logPath"]
    assert (tmp_path / result["logPath"]).exists()


def test_prune_workflow_run_logs_payload_previews_and_preserves_running(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    old = log_dir / "2020-01-01T10-00-00-0000.log"
    old.write_text(
        "2020-01-01T10:00:00+00:00 - history-flow started successfully\n"
        "2020-01-01T10:00:01+00:00 - INFO - history-flow completed successfully\n",
        encoding="utf-8",
    )
    old.with_suffix(".events.json").write_text("{}", encoding="utf-8")
    running = log_dir / "2020-01-02T10-00-00-0000.log"
    running.write_text(
        "2020-01-02T10:00:00+00:00 - history-flow started successfully\n",
        encoding="utf-8",
    )

    preview = prune_workflow_run_logs_payload(
        "history-flow",
        tmp_path,
        keep_days=1,
        dry_run=True,
    )
    applied = prune_workflow_run_logs_payload(
        "history-flow",
        tmp_path,
        keep_days=1,
        dry_run=False,
    )

    assert [run["id"] for run in preview["runs"]] == [old.name]
    assert old.name in applied["deleted"]
    assert not old.exists()
    assert not old.with_suffix(".events.json").exists()
    assert running.exists()


def test_prune_workflow_run_logs_payload_preserves_active_registry_run(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs" / "history-flow"
    log_dir.mkdir(parents=True)
    misleading_active = log_dir / "2020-01-01T10-00-00-0000.log"
    misleading_active.write_text(
        "2020-01-01T10:00:00+00:00 - history-flow started successfully\n"
        "2020-01-01T10:00:01+00:00 - INFO - history-flow completed successfully\n",
        encoding="utf-8",
    )
    newer_completed = log_dir / "2020-01-02T10-00-00-0000.log"
    newer_completed.write_text(
        "2020-01-02T10:00:00+00:00 - history-flow started successfully\n"
        "2020-01-02T10:00:01+00:00 - INFO - history-flow completed successfully\n",
        encoding="utf-8",
    )
    key = api_module._run_key(tmp_path, "history-flow")
    event = api_module.threading.Event()
    with api_module._active_run_lock:
        api_module._active_run_stop_events[key] = {event}
        api_module._active_run_log_paths[key] = {event: misleading_active}
    try:
        applied = prune_workflow_run_logs_payload(
            "history-flow",
            tmp_path,
            keep_days=1,
            dry_run=False,
        )
    finally:
        with api_module._active_run_lock:
            api_module._active_run_stop_events.pop(key, None)
            api_module._active_run_log_paths.pop(key, None)

    assert applied["deleted"] == [newer_completed.name]
    assert misleading_active.exists()
    assert not newer_completed.exists()


def test_list_workflow_payloads_uses_latest_run_status(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Status Flow", tmp_path)
    workflow["nodes"] = [
        {
            "id": "hello",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "echo hello",
            },
            "settings": {},
        }
    ]
    update_workflow_payload("status-flow", workflow, tmp_path)

    assert list_workflow_payloads(tmp_path)["workflows"][0]["status"] == "Ready"

    log_dir = tmp_path / "logs" / "status-flow"
    log_dir.mkdir(parents=True)
    (log_dir / "2026-06-13T10-00-00-0400.log").write_text(
        "2026-06-13T10:00:00-04:00 - status-flow started successfully\n"
        "2026-06-13T10:00:00-04:00 - INFO - dry_run=False\n"
    )
    list_workflow_run_logs_payload("status-flow", tmp_path)

    assert list_workflow_payloads(tmp_path)["workflows"][0]["status"] == "Running"

    anyio.run(run_workflow_payload, "status-flow", tmp_path, False)

    assert list_workflow_payloads(tmp_path)["workflows"][0]["status"] == "Success"

    workflow["nodes"][0]["operation"]["command"] = "exit 2"
    update_workflow_payload("status-flow", workflow, tmp_path)
    anyio.run(run_workflow_payload, "status-flow", tmp_path, False)

    assert list_workflow_payloads(tmp_path)["workflows"][0]["status"] == "Error"

    stopped_log = log_dir / "2099-06-13T10-00-02-0400.log"
    stopped_log.write_text(
        "2026-06-13T10:00:02-04:00 - status-flow started successfully\n"
        "2026-06-13T10:00:02-04:00 - WARNING - status-flow stopped by user\n"
    )
    stopped_log.with_suffix(".events.json").write_text(
        json.dumps({"events": [{"nodeId": "workflow", "status": "stopped"}], "nodes": {}})
    )
    list_workflow_run_logs_payload("status-flow", tmp_path)

    assert list_workflow_payloads(tmp_path)["workflows"][0]["status"] == "Stopped"


def test_list_workflow_payloads_rescans_stale_cached_latest_run(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Indexed Status", tmp_path)
    workflow["nodes"] = [
        {
            "id": "hello",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "echo hello",
            },
            "settings": {},
        }
    ]
    update_workflow_payload("indexed-status", workflow, tmp_path)

    log_dir = tmp_path / "logs" / "indexed-status"
    log_dir.mkdir(parents=True)
    latest = log_dir / "2026-06-13T10-00-00-0400.log"
    latest.write_text(
        "2026-06-13T10:00:00-04:00 - indexed-status started successfully\n"
        "2026-06-13T10:00:01-04:00 - INFO - indexed-status completed successfully\n",
        encoding="utf-8",
    )
    list_workflow_run_logs_payload("indexed-status", tmp_path)

    assert list_workflow_payloads(tmp_path)["workflows"][0]["status"] == "Success"

    latest.unlink()

    assert list_workflow_payloads(tmp_path)["workflows"][0]["status"] == "Ready"


def test_workflow_upsert_preserves_latest_run_validation_metadata(tmp_path: Path) -> None:
    workflow = create_workflow_payload("Edited Indexed Status", tmp_path)
    workflow["nodes"] = [
        {
            "id": "hello",
            "type": "bash_command",
            "operation": {
                "type": "bash_command",
                "command": "echo hello",
            },
            "settings": {},
        }
    ]
    update_workflow_payload("edited-indexed-status", workflow, tmp_path)

    log_dir = tmp_path / "logs" / "edited-indexed-status"
    log_dir.mkdir(parents=True)
    latest = log_dir / "2026-06-13T10-00-00-0400.log"
    latest.write_text(
        "2026-06-13T10:00:00-04:00 - edited-indexed-status started successfully\n"
        "2026-06-13T10:00:01-04:00 - INFO - edited-indexed-status completed successfully\n",
        encoding="utf-8",
    )
    list_workflow_run_logs_payload("edited-indexed-status", tmp_path)

    assert list_workflow_payloads(tmp_path)["workflows"][0]["status"] == "Success"

    workflow["description"] = "Edited after a successful run"
    update_workflow_payload("edited-indexed-status", workflow, tmp_path)

    workflow_index = json.loads((tmp_path / "indexes" / "workflows.json").read_text())
    latest_run = workflow_index["workflows"]["edited-indexed-status.toml"]["latestRun"]
    assert latest_run["id"] == latest.name

    latest.unlink()

    assert list_workflow_payloads(tmp_path)["workflows"][0]["status"] == "Ready"


def test_workflow_approval_payloads_list_and_decide_requests(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    store.create_or_update(
        ApprovalRequest(
            workflow_id="approval-flow",
            run_id="run.log",
            node_id="approve",
            message="Approve deploy?",
            approvers=["ops"],
            timeout_seconds=30,
            timeout_decision="reject",
        )
    )

    listed = list_workflow_approvals_payload("approval-flow", tmp_path)

    assert listed["approvals"][0]["status"] == "pending"
    assert listed["approvals"][0]["approvers"] == ["ops"]
    assert listed["approvals"][0]["timeoutSeconds"] == 30
    assert listed["approvals"][0]["timeoutDecision"] == "reject"

    decided = decide_workflow_approval_payload(
        "approval-flow",
        "run.log",
        "approve",
        "approved",
        tmp_path,
        decided_by="ops",
        notes="ship it",
    )

    approval = decided["approval"]
    assert approval["status"] == "decided"
    assert approval["decision"]["decision"] == "approved"
    assert approval["decision"]["decidedBy"] == "ops"
    assert approval["decision"]["notes"] == "ship it"
    assert decided["resumed"] is False


def test_workflow_approval_payload_enforces_configured_approvers(tmp_path: Path) -> None:
    store = ApprovalStore(tmp_path)
    store.create_or_update(
        ApprovalRequest(
            workflow_id="approval-flow",
            run_id="run.log",
            node_id="approve",
            message="Approve deploy?",
            approvers=["ops"],
        )
    )

    with pytest.raises(api_module.WorkflowApprovalError, match="not allowed"):
        decide_workflow_approval_payload(
            "approval-flow",
            "run.log",
            "approve",
            "approved",
            tmp_path,
            decided_by="ui",
        )

    request = store.get("approval-flow", "run.log", "approve")
    assert request is not None
    assert request.decision is None


def test_workflow_approval_payload_rejects_already_decided_request(
    tmp_path: Path,
) -> None:
    store = ApprovalStore(tmp_path)
    store.create_or_update(
        ApprovalRequest(
            workflow_id="approval-flow",
            run_id="run.log",
            node_id="approve",
            message="Approve deploy?",
        )
    )
    store.decide(
        "approval-flow",
        "run.log",
        "approve",
        "rejected",
        decided_by="ops",
    )

    with pytest.raises(api_module.WorkflowApprovalError, match="Pending approval"):
        decide_workflow_approval_payload(
            "approval-flow",
            "run.log",
            "approve",
            "approved",
            tmp_path,
            decided_by="ui",
        )

    decided = store.get("approval-flow", "run.log", "approve")
    assert decided is not None
    assert decided.decision is not None
    assert decided.decision.decision == "rejected"
    assert decided.decision.decided_by == "ops"


def test_workflow_approval_payload_resumes_checkpointed_request(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def fake_send(_adapter, _notification) -> None:
        return None

    monkeypatch.setattr("gofer.core.approvals.DesktopNotificationAdapter.send", fake_send)
    workflow_path = tmp_path / "approval-flow.toml"
    workflow_path.write_text(
        """
[workflow]
id = "approval-flow"
name = "Approval Flow"

[[nodes]]
id = "plan"
type = "bash_command"
command = "echo deploy"

[[nodes]]
id = "approve"
type = "approval_gate"
message = "Approve {{plan.output}}?"

[[nodes]]
id = "notify"
type = "notification"
title = "Approval"
body = "Gate: {{approve.data.message}}"

[[edges]]
from = "plan"
to = "approve"

[[edges]]
from = "approve"
to = "notify"
condition = "on_success"
""",
        encoding="utf-8",
    )
    store = ApprovalStore(tmp_path)
    run_id = "run.log"
    log_path = tmp_path / "logs" / "approval-flow" / run_id
    checkpoint_path = store.request_path(
        "approval-flow",
        run_id,
        "approve",
    ).with_suffix(".checkpoint.json")
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "workflowId": "approval-flow",
                "nodeId": "approve",
                "trigger": {},
                "nodeOutputs": {
                    "plan": NodeOutput(
                        node_id="plan",
                        success=True,
                        output="deploy",
                        exit_code=0,
                        duration_seconds=0,
                        type="bash_command",
                        data={"stdout": "deploy", "stderr": "", "command": "echo deploy"},
                    ).contract(),
                },
            }
        ),
        encoding="utf-8",
    )
    store.create_or_update(
        ApprovalRequest(
            workflow_id="approval-flow",
            run_id=run_id,
            node_id="approve",
            message="Approve deploy?",
            workflow_path=str(workflow_path),
            log_path=str(log_path),
            checkpoint_path=str(checkpoint_path),
            waiter_seen_at="2099-01-01T00:00:00+00:00",
        )
    )

    decided = decide_workflow_approval_payload(
        "approval-flow",
        run_id,
        "approve",
        "approved",
        tmp_path,
        decided_by="ui",
    )

    assert decided["resumed"] is True
    run = workflow_run_log_payload("approval-flow", run_id, tmp_path)
    assert run["nodeOutputs"]["approve"]["data"]["message"] == "Approve deploy?"
    assert run["nodeOutputs"]["notify"]["data"]["body"] == "Gate: Approve deploy?"


def test_workflow_approval_payload_list_resumes_expired_timeout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def fake_send(_adapter, _notification) -> None:
        return None

    monkeypatch.setattr("gofer.core.approvals.DesktopNotificationAdapter.send", fake_send)
    workflow_path = tmp_path / "approval-flow.toml"
    workflow_path.write_text(
        """
[workflow]
id = "approval-flow"
name = "Approval Flow"

[[nodes]]
id = "approve"
type = "approval_gate"
message = "Approve deploy?"
timeout_seconds = 1

[[nodes]]
id = "notify"
type = "notification"
title = "Timed out"
body = "Decision: {{approve.data.decision}}"

[[edges]]
from = "approve"
to = "notify"
condition = "on_failure"
""",
        encoding="utf-8",
    )
    store = ApprovalStore(tmp_path)
    run_id = "run.log"
    log_path = tmp_path / "logs" / "approval-flow" / run_id
    checkpoint_path = store.request_path(
        "approval-flow",
        run_id,
        "approve",
    ).with_suffix(".checkpoint.json")
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "workflowId": "approval-flow",
                "nodeId": "approve",
                "trigger": {},
                "nodeOutputs": {},
            }
        ),
        encoding="utf-8",
    )
    store.create_or_update(
        ApprovalRequest(
            workflow_id="approval-flow",
            run_id=run_id,
            node_id="approve",
            message="Approve deploy?",
            timeout_seconds=1,
            workflow_path=str(workflow_path),
            log_path=str(log_path),
            checkpoint_path=str(checkpoint_path),
            requested_at=(datetime.now(UTC) - timedelta(seconds=5)).isoformat(timespec="seconds"),
        )
    )

    listed = list_workflow_approvals_payload("approval-flow", tmp_path)

    approval = listed["approvals"][0]
    assert approval["decision"]["decision"] == "timeout"
    run = workflow_run_log_payload("approval-flow", run_id, tmp_path)
    assert run["nodeOutputs"]["approve"]["data"]["decision"] == "timeout"
    assert run["nodeOutputs"]["notify"]["data"]["body"] == "Decision: timeout"


def test_cancelled_legacy_traversal_preserves_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_workflow_payload("Keep", tmp_path)
    list_workflow_payloads(tmp_path)
    original = api_module._read_workflow_index(tmp_path)
    cancelled = False
    real_walk = os.walk

    def walk(*args: Any, **kwargs: Any) -> Any:
        nonlocal cancelled
        for entry in real_walk(*args, **kwargs):
            cancelled = True
            yield entry

    def check() -> None:
        if cancelled:
            raise api_module.DiscoveryCancelled()

    monkeypatch.setattr(os, "walk", walk)
    with pytest.raises(api_module.DiscoveryCancelled):
        list_workflow_payloads(tmp_path, check_cancelled=check)
    assert api_module._read_workflow_index(tmp_path) == original


@pytest.mark.parametrize("phase", ["traversal", "compilation"])
def test_project_discovery_cancellation_stops_remaining_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    project = tmp_path / "project"
    registry = tmp_path / "registry"
    for name in ("a", "b"):
        root = project / ".raticode" / name
        root.mkdir(parents=True)
        (root / "workflow.rattish").write_text(f"Rattish: 1\nWorkflow:\n  name: {name}\n")
    cancelled = False
    compiled: list[str] = []
    real_compile = api_module.compile_rattish_file
    real_iterdir = Path.iterdir

    def iterdir(path: Path) -> Any:
        nonlocal cancelled
        for entry in real_iterdir(path):
            if path == project / ".raticode" and phase == "traversal":
                cancelled = True
            yield entry

    def compile_file(path: Path, **kwargs: Any) -> Any:
        nonlocal cancelled
        compiled.append(str(path))
        artifact = real_compile(path, **kwargs)
        cancelled = True
        return artifact

    def check() -> None:
        if cancelled:
            raise api_module.DiscoveryCancelled()

    monkeypatch.setattr(Path, "iterdir", iterdir)
    monkeypatch.setattr(api_module, "compile_rattish_file", compile_file)
    with pytest.raises(api_module.DiscoveryCancelled):
        open_project_payload(project, registry_dir=registry, check_cancelled=check)
    assert len(compiled) == (1 if phase == "compilation" else 0)
    if phase == "traversal":
        assert not registry.exists()


def test_project_discovery_reports_real_filesystem_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError("secret backend detail")

    monkeypatch.setattr(api_module, "discover_registered_workflows", fail)
    with pytest.raises(WorkflowCreateError, match="permission denied") as error:
        open_project_payload(tmp_path)
    assert "secret" not in str(error.value)


def test_project_discovery_keeps_compile_errors_in_project_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    registry = tmp_path / "registry"
    for name in ("broken", "healthy"):
        root = project / ".raticode" / name
        root.mkdir(parents=True)
        (root / "workflow.rattish").write_text(f"Rattish: 1\nWorkflow:\n  name: {name}\n")
    real_compile = api_module.compile_rattish_file

    def compile_file(path: Path, **kwargs: Any) -> Any:
        if path.parent.name == "broken":
            raise PermissionError("private OS detail\nsecret")
        return real_compile(path, **kwargs)

    monkeypatch.setattr(api_module, "compile_rattish_file", compile_file)
    payload = open_project_payload(project, registry_dir=registry)
    broken, healthy = payload["workflows"]
    assert broken["projectRoot"] == str(project)
    assert broken["invalid"]
    assert "permission denied" in broken["validationError"]
    assert "secret" not in broken["validationError"]
    assert not healthy["invalid"]
