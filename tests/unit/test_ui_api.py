from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import anyio
import pytest

from gofer.core.approvals import ApprovalRequest, ApprovalStore
from gofer.rattish.editor import RattishEditorError, RattishRevisionConflict
from gofer.ui import api as api_module
from gofer.ui.api import (
    WorkflowCreateError,
    WorkflowLogError,
    WorkflowRunError,
    WorkflowUpdateError,
    analyze_rattish_document_payload,
    create_registered_workflow_payload,
    decide_workflow_approval_payload,
    delete_workflow_chat_payload,
    delete_workflow_payload,
    duplicate_workflow_payload,
    latest_workflow_log_payload,
    list_workflow_approvals_payload,
    list_workflow_payloads,
    list_workflow_run_logs_payload,
    mutate_rattish_document_payload,
    open_project_payload,
    open_rattish_document_payload,
    rename_workflow_payload,
    resume_workflow_payload,
    retention_settings_payload,
    run_workflow_payload,
    save_rattish_document_payload,
    save_rattish_metadata_payload,
    stop_workflow_run_payload,
    update_retention_settings_payload,
    workflow_run_log_payload,
)
from gofer.ui.chat import workflow_chat_prompt_path


def _plant_project_checkout(folder: Path) -> None:
    folder.mkdir(parents=True)
    (folder / "pyproject.toml").write_text('[project]\nname = "app"\n')
    (folder / "ruff.toml").write_text("line-length = 100\n")


def test_workflow_discovery_skips_rem_and_organization_worktrees(tmp_path: Path) -> None:
    _plant_project_checkout(tmp_path / "rem-worktrees" / "run-1")
    _plant_project_checkout(tmp_path / "organization-shared-worktrees" / "org-1" / "task-1")
    _plant_project_checkout(tmp_path / "organization-worktrees" / "task-2")
    for folder in (
        "rem-work/turn-scratch/turn-1",
        "organization-runs/run-2",
        "chat-attachments/thread",
        "chat-something-new/thread",
        "trash/old",
        "second-brains/brain",
        "logs/flow",
        "command-slots/bin",
    ):
        _plant_project_checkout(tmp_path / folder)

    payload = list_workflow_payloads(tmp_path)

    assert payload["workflows"] == []
    assert payload["errors"] == []


def test_rattish_workflow_named_like_a_project_file_still_lists(tmp_path: Path) -> None:
    app_data = tmp_path / "app-data"
    project = tmp_path / "project"
    project.mkdir()
    _plant_project_checkout(app_data / "rem-worktrees" / "run-1")
    _plant_project_checkout(app_data / "organization-shared-worktrees" / "org-1" / "task-1")
    create_registered_workflow_payload("pyproject", project, registry_dir=app_data)

    payload = list_workflow_payloads(app_data)

    assert [(w["id"], w["sourceFormat"]) for w in payload["workflows"]] == [
        ("pyproject", "rattish")
    ]
    assert payload["errors"] == []


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


def test_workflow_summaries_count_nodes_and_routes_without_toml_fields(tmp_path: Path) -> None:
    app_data = tmp_path / "app-data"
    project = tmp_path / "project"
    project.mkdir()
    created = create_registered_workflow_payload("Review PR", project, registry_dir=app_data)
    Path(created["sourcePath"]).write_text(
        "Rattish: 1\nWorkflow:\n  name: Review PR\n"
        "Node first:\n  type: bash-command\n  command: printf one\n  to: second\n"
        "Node second:\n  type: bash-command\n  command: printf two\n",
        encoding="utf-8",
    )

    [summary] = list_workflow_payloads(app_data)["workflows"]

    toml_fields = {
        "schedule",
        "watch",
        "parameters",
        "agents",
        "nodes",
        "edges",
        "filesystemAccess",
    }
    assert not toml_fields & summary.keys()
    assert summary["status"] == "Ready"
    assert (summary["nodeCount"], summary["routeCount"]) == (2, 1)
    assert summary["description"] == "2 Rattish nodes, 1 routes."


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


def test_discovered_rattish_workflows_report_declared_names_and_first_error(
    tmp_path: Path,
) -> None:
    app_data = tmp_path / "app-data"
    project = tmp_path / "project"
    valid_root = project / ".raticode" / "nightly-build"
    broken_root = project / ".raticode" / "broken-build"
    valid_root.mkdir(parents=True)
    broken_root.mkdir(parents=True)
    (valid_root / "workflow.rattish").write_text(
        'Rattish: 1\n\nWorkflow:\n  name: "Nightly Build"\n',
        encoding="utf-8",
    )
    (broken_root / "workflow.rattish").write_text(
        'Rattish: 1\n\nWorkflow:\n  name: "Broken Build"\n\nNode bad:\n  type: not-a-type\n',
        encoding="utf-8",
    )

    workflows = {
        workflow["id"]: workflow
        for workflow in open_project_payload(project, registry_dir=app_data)["workflows"]
    }

    # The folder name is only the ID; the list shows the name the file declares.
    assert workflows["nightly-build"]["name"] == "Nightly Build"
    assert workflows["broken-build"]["name"] == "Broken Build"
    assert workflows["broken-build"]["invalid"] is True
    assert workflows["broken-build"]["validationError"] == (
        "Line 7: No node contract is available for 'not-a-type'."
    )


def test_rename_and_duplicate_registered_rattish_workflows(tmp_path: Path) -> None:
    app_data = tmp_path / "app-data"
    project = tmp_path / "project"
    project.mkdir()
    created = create_registered_workflow_payload("Release", project, registry_dir=app_data)
    workflow_root = Path(created["workflowRoot"])
    (workflow_root / "notes.md").write_text("keep me\n", encoding="utf-8")
    (workflow_root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

    renamed = rename_workflow_payload("release", "Release Train", app_data)

    assert (renamed["id"], renamed["name"]) == ("release", "Release Train")
    assert 'name: "Release Train"' in Path(created["sourcePath"]).read_text(encoding="utf-8")

    first = duplicate_workflow_payload("release", None, app_data)
    second = duplicate_workflow_payload("release", None, app_data)

    assert (first["id"], first["name"]) == ("release-train-copy", "Release Train copy")
    assert second["name"] == "Release Train copy 2"
    first_root = Path(first["workflowRoot"])
    assert first_root.parent == workflow_root.parent
    assert 'name: "Release Train copy"' in (first_root / "workflow.rattish").read_text(
        encoding="utf-8"
    )
    assert (first_root / "notes.md").read_text(encoding="utf-8") == "keep me\n"
    # Duplicates carry the same files a bundle would, so ignored secrets stay behind.
    assert not (first_root / ".env").exists()
    assert {workflow["name"] for workflow in list_workflow_payloads(app_data)["workflows"]} == {
        "Release Train",
        "Release Train copy",
        "Release Train copy 2",
    }


def test_duplicate_invalid_rattish_workflow_explains_the_error(tmp_path: Path) -> None:
    app_data = tmp_path / "app-data"
    project = tmp_path / "project"
    project.mkdir()
    created = create_registered_workflow_payload("Fragile", project, registry_dir=app_data)
    Path(created["sourcePath"]).write_text(
        'Rattish: 1\n\nWorkflow:\n  name: "Fragile"\n\nNode bad:\n  type: not-a-type\n',
        encoding="utf-8",
    )

    with pytest.raises(WorkflowCreateError, match="Fix this workflow before duplicating it"):
        duplicate_workflow_payload("fragile", None, app_data)


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
  message: "Approve this step to continue."
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
    assert mutated["compilation"]["state"] == "valid"


def _graph_editor_workflow(tmp_path: Path, name: str, source: str) -> dict[str, Any]:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    registered = create_registered_workflow_payload(name, project, registry_dir=tmp_path)
    Path(registered["sourcePath"]).write_text(source, encoding="utf-8")
    return open_rattish_document_payload(registered["id"], tmp_path)


_GRAPH_EDITOR_SOURCE = """Rattish: 1

Workflow:
  name: Graph Only

Node prepare:
  type: bash-command
  command: echo ready
  to: review

Node review:
  type: bash-command
  command: echo review
"""


def test_rattish_graph_add_node_compiles_without_writing_rattish(tmp_path: Path) -> None:
    opened = _graph_editor_workflow(tmp_path, "Graph Only", _GRAPH_EDITOR_SOURCE)
    assert opened["compilation"]["state"] == "valid"

    # Older clients sent an empty command; the editor must still produce compiling source.
    added = mutate_rattish_document_payload(
        "graph-only",
        [
            {
                "kind": "add_node",
                "node": "node-3",
                "node_type": "bash-command",
                "fields": {"command": ""},
            }
        ],
        opened["sourceRevision"],
        tmp_path,
    )

    assert added["compilation"]["state"] == "valid"
    assert 'command: "echo \\"node-3 ran\\""' in added["source"]
    assert [node["id"] for node in added["graph"]["nodes"]] == ["prepare", "review", "node-3"]


def test_rattish_graph_type_changes_always_compile(tmp_path: Path) -> None:
    opened = _graph_editor_workflow(tmp_path, "Graph Only", _GRAPH_EDITOR_SOURCE)
    create_registered_workflow_payload("Child", tmp_path / "project", registry_dir=tmp_path)
    with pytest.raises(RattishEditorError, match="set its Interface version"):
        mutate_rattish_document_payload(
            "graph-only",
            [{"kind": "change_node_type", "node": "review", "node_type": "workflow"}],
            opened["sourceRevision"],
            tmp_path,
        )
    child = open_rattish_document_payload("child", tmp_path)
    mutate_rattish_document_payload(
        "child",
        [
            {
                "kind": "set_field",
                "target": "workflow",
                "field": "interface-version",
                "valueSource": "1",
            }
        ],
        child["sourceRevision"],
        tmp_path,
    )
    node_types = [contract["nodeType"] for contract in opened["nodeContracts"]]
    assert {"agent", "loop", "workflow", "notification", "rem-task"} <= set(node_types)
    # A Rem task node needs one of Rem's tasks to start, like a Workflow node needs a child.
    with pytest.raises(RattishEditorError, match="Ask Rem to create the task first"):
        mutate_rattish_document_payload(
            "graph-only",
            [{"kind": "change_node_type", "node": "review", "node_type": "rem-task"}],
            opened["sourceRevision"],
            tmp_path,
        )
    from gofer.ui.rem_home_models import Recipe
    from gofer.ui.rem_home_store import RemHomeStore

    rem = RemHomeStore(tmp_path)
    with rem.connect() as db:
        rem.add_task(
            db,
            Recipe.model_validate(
                {
                    "title": "Graph task",
                    "type": "errand",
                    "prompt": "Check the graph.",
                    "schedule": {"kind": "once", "at": "2099-01-01T00:00:00+00:00"},
                }
            ),
        )

    revision = opened["sourceRevision"]
    for node_type in node_types:
        if node_type == "break":
            continue
        mutated = mutate_rattish_document_payload(
            "graph-only",
            [{"kind": "change_node_type", "node": "review", "node_type": node_type}],
            revision,
            tmp_path,
        )
        assert mutated["compilation"]["state"] == "valid", (node_type, mutated["diagnostics"])
        assert mutated["graph"]["nodes"][1]["type"] == node_type
        revision = mutated["sourceRevision"]


def test_rattish_graph_break_uses_the_upstream_loop(tmp_path: Path) -> None:
    opened = _graph_editor_workflow(tmp_path, "Graph Only", _GRAPH_EDITOR_SOURCE)
    looped = mutate_rattish_document_payload(
        "graph-only",
        [{"kind": "change_node_type", "node": "prepare", "node_type": "loop"}],
        opened["sourceRevision"],
        tmp_path,
    )
    broken = mutate_rattish_document_payload(
        "graph-only",
        [{"kind": "change_node_type", "node": "review", "node_type": "break"}],
        looped["sourceRevision"],
        tmp_path,
    )

    assert broken["compilation"]["state"] == "valid"
    assert "loop: prepare" in broken["source"]


def test_rattish_graph_edits_cannot_make_a_valid_workflow_invalid(tmp_path: Path) -> None:
    opened = _graph_editor_workflow(tmp_path, "Graph Only", _GRAPH_EDITOR_SOURCE)
    source_path = Path(opened["sourcePath"])

    with pytest.raises(RattishEditorError, match="would make the workflow invalid"):
        mutate_rattish_document_payload(
            "graph-only",
            [{"kind": "set_field", "target": {"node": "prepare"}, "field": "command", "value": ""}],
            opened["sourceRevision"],
            tmp_path,
        )
    with pytest.raises(RattishEditorError, match="must run after a Loop node"):
        mutate_rattish_document_payload(
            "graph-only",
            [{"kind": "change_node_type", "node": "prepare", "node_type": "break"}],
            opened["sourceRevision"],
            tmp_path,
        )

    assert source_path.read_text(encoding="utf-8") == _GRAPH_EDITOR_SOURCE


def test_rattish_graph_edits_can_repair_an_invalid_workflow(tmp_path: Path) -> None:
    invalid = _GRAPH_EDITOR_SOURCE.replace("command: echo review", 'command: ""')
    opened = _graph_editor_workflow(tmp_path, "Graph Only", invalid)
    assert opened["compilation"]["state"] == "invalid"

    repaired = mutate_rattish_document_payload(
        "graph-only",
        [
            {
                "kind": "set_field",
                "target": {"node": "review"},
                "field": "command",
                "value": "echo ok",
            }
        ],
        opened["sourceRevision"],
        tmp_path,
    )

    assert repaired["compilation"]["state"] == "valid"


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
    )

    assert result == {
        "workflowId": "delete-rattish",
        "deleted": True,
        "runWorktreesRemoved": 0,
        "runWorktreeErrors": [],
    }
    assert not workflow_root.exists()
    assert list_workflow_payloads(app_data)["workflows"] == []


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
    with pytest.raises(WorkflowLogError, match="Invalid run ID|not found"):
        workflow_run_log_payload("chatty", run_id, tmp_path)


@pytest.mark.parametrize("workflow_id", ["../runnable", "runnable/path", "Runnable"])
def test_run_workflow_payload_rejects_unsafe_workflow_ids(
    tmp_path: Path,
    workflow_id: str,
) -> None:
    # An ID that isn't a registered workflow's never reaches the file system.
    with pytest.raises(WorkflowRunError, match="not found"):
        anyio.run(run_workflow_payload, workflow_id, tmp_path, True)


def test_stop_workflow_run_payload_reports_no_active_run(tmp_path: Path) -> None:
    result = stop_workflow_run_payload("not-running", tmp_path)

    assert result == {
        "workflowId": "not-running",
        "stopped": False,
        "message": "No active run",
    }


@pytest.mark.parametrize("workflow_id", ["../history-flow", "history/flow", "History-Flow"])
def test_list_workflow_run_logs_payload_rejects_unsafe_workflow_ids(
    tmp_path: Path,
    workflow_id: str,
) -> None:
    with pytest.raises(WorkflowLogError, match="Invalid workflow id"):
        list_workflow_run_logs_payload(workflow_id, tmp_path)


def test_retention_settings_persist(tmp_path: Path) -> None:
    saved = update_retention_settings_payload(
        tmp_path,
        workflow_id="history-flow",
        settings={"keepDays": 1, "keepFailedDays": 2, "keepLast": 0},
    )

    assert saved["settings"] == {"keepDays": 1, "keepFailedDays": 2, "keepLast": 0}
    assert retention_settings_payload(tmp_path, "history-flow")["settings"] == saved["settings"]


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

    monkeypatch.setattr(api_module, "discover_project_workflows", fail)
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


def _registered_rattish(tmp_path: Path, source: str) -> tuple[Path, str]:
    app_data = tmp_path / "app-data"
    project = tmp_path / "project"
    project.mkdir()
    created = create_registered_workflow_payload("Audit", project, registry_dir=app_data)
    Path(created["sourcePath"]).write_text(source, encoding="utf-8")
    return app_data, created["id"]


def test_rattish_resume_refuses_node_options_and_missing_runs_without_running(
    tmp_path: Path,
) -> None:
    app_data, workflow_id = _registered_rattish(
        tmp_path,
        "Rattish: 1\n\nWorkflow:\n  name: Audit\n\nNode first:\n"
        "  type: bash-command\n  command: printf ok\n",
    )

    with pytest.raises(WorkflowRunError, match="resume where they stopped"):
        anyio.run(
            lambda: resume_workflow_payload(
                workflow_id, app_data, run_id="does-not-exist", from_node="no-such-node"
            )
        )
    with pytest.raises(WorkflowRunError, match="does-not-exist"):
        anyio.run(lambda: resume_workflow_payload(workflow_id, app_data, run_id="does-not-exist"))

    assert list_workflow_run_logs_payload(workflow_id, app_data)["runs"] == []


def test_rattish_sync_run_uses_cli_run_ids_and_omits_duplicate_log_text(tmp_path: Path) -> None:
    app_data, workflow_id = _registered_rattish(
        tmp_path,
        "Rattish: 1\n\nWorkflow:\n  name: Audit\n\nNode first:\n"
        "  type: bash-command\n  command: printf ok\n",
    )

    run = anyio.run(lambda: run_workflow_payload(workflow_id, app_data))

    assert re.fullmatch(r"\d{8}T\d{12}Z-[0-9a-f]{12}", run["runId"])
    assert "logText" not in run
    assert run["rattishRun"]["status"] == "passed"


def test_rattish_node_status_keeps_allowed_and_earlier_failures_visible(tmp_path: Path) -> None:
    app_data, workflow_id = _registered_rattish(
        tmp_path,
        """Rattish: 1

Workflow:
  name: Audit

Node risky:
  type: bash-command
  command: exit 3
  allow-fail: true
  to: items

Node items:
  type: loop
  needs: risky
  source: {"type": "count", "count": 3}
  to: work

Node work:
  type: bash-command
  needs: items
  with:
    index: node.items.output.index
  command: test "$INDEX" != 0
""",
    )

    run = anyio.run(lambda: run_workflow_payload(workflow_id, app_data))
    nodes = run["runNodes"]

    assert run["success"] is False
    assert nodes["risky"]["status"] == "allowed_failure"
    assert nodes["work"]["status"] == "error"
    assert nodes["work"]["activationCounts"]["total"] == 3
    assert nodes["work"]["activationSummary"] == "1 of 3 failed"
    failed = [event for event in run["runEvents"] if event["status"] == "failed"]
    assert failed and all(event["message"] != "Activation 1 failure." for event in failed)
    risky_completed = [
        event
        for event in run["runEvents"]
        if event["nodeId"] == "risky" and event["type"] == "node_completed"
    ]
    assert [event["status"] for event in risky_completed] == ["allowed_failure"]


def test_rattish_run_level_failure_reason_reaches_the_timeline(tmp_path: Path) -> None:
    app_data, workflow_id = _registered_rattish(
        tmp_path,
        "Rattish: 1\n\nWorkflow:\n  name: Audit\n\nNode first:\n"
        "  type: bash-command\n  command: printf ok\n",
    )

    run = anyio.run(lambda: run_workflow_payload(workflow_id, app_data, inputs={"nope": 1}))

    [completed] = [event for event in run["runEvents"] if event["type"] == "workflow_completed"]
    assert completed["status"] == "failed"
    assert completed["errorCode"] == "RATTISH_WORKFLOW_INPUT_INVALID"
    assert completed["message"].startswith("Workflow rejected its inputs: ")
    assert "nope" in completed["message"]


def test_rattish_plan_orders_nodes_by_routes_and_needs_and_lists_bindings(
    tmp_path: Path,
) -> None:
    app_data, workflow_id = _registered_rattish(
        tmp_path,
        """Rattish: 1

Workflow:
  name: Audit

Node build:
  type: bash-command
  command: printf ok
  to: test

Node test:
  type: bash-command
  command: printf ok
  needs: build
  with:
    built: node.build.output.stdout
  to: publish

Node publish:
  type: bash-command
  command: printf ok
  needs: test
""",
    )

    plan = anyio.run(lambda: run_workflow_payload(workflow_id, app_data, dry_run=True))

    assert [[node["id"] for node in item["nodes"]] for item in plan["generations"]] == [
        ["build"],
        ["test"],
        ["publish"],
    ]
    [binding] = plan["bindings"]
    assert binding["destinationNode"] == "test"
    assert binding["expression"] == "node.build.output.stdout"
    assert plan["generations"][1]["nodes"][0]["bindings"] == [binding]


def test_rattish_run_list_reports_runs_waiting_for_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_data, workflow_id = _registered_rattish(
        tmp_path,
        "Rattish: 1\n\nWorkflow:\n  name: Audit\n\nNode first:\n"
        "  type: bash-command\n  command: printf ok\n",
    )
    run = anyio.run(lambda: run_workflow_payload(workflow_id, app_data))
    path = Path(run["logPath"])
    document = json.loads(path.read_text(encoding="utf-8"))
    document.update(status="running", runner_pid=os.getpid())
    path.write_text(json.dumps(document), encoding="utf-8")
    import gofer.rattish.run_service as run_service

    monkeypatch.setattr(run_service, "is_rattish_run_active", lambda run_id: True)
    ApprovalStore(app_data).create_or_update(
        ApprovalRequest(
            workflow_id=workflow_id, run_id=run["runId"], node_id="gate", message="Ship?"
        )
    )

    [listed] = list_workflow_run_logs_payload(workflow_id, app_data)["runs"]

    assert listed["status"] == "running"
    assert listed["awaitingApproval"] is True


def test_rattish_approval_for_a_finished_run_is_closed_and_refused(tmp_path: Path) -> None:
    app_data, workflow_id = _registered_rattish(
        tmp_path,
        "Rattish: 1\n\nWorkflow:\n  name: Audit\n\nNode first:\n"
        "  type: bash-command\n  command: exit 4\n",
    )
    run = anyio.run(lambda: run_workflow_payload(workflow_id, app_data))
    # A request left behind by an older runner or a crash.
    ApprovalStore(app_data).create_or_update(
        ApprovalRequest(
            workflow_id=workflow_id, run_id=run["runId"], node_id="gate", message="Ship?"
        )
    )

    with pytest.raises(api_module.WorkflowApprovalError, match="run ended"):
        decide_workflow_approval_payload(
            workflow_id, run["runId"], "gate", "rejected", app_data, notes="late"
        )
    [approval] = list_workflow_approvals_payload(workflow_id, app_data)["approvals"]
    assert approval["status"] == "cancelled"
    assert approval["decision"] is None
    assert list_workflow_approvals_payload(workflow_id, app_data, include_decided=False) == {
        "workflowId": workflow_id,
        "approvals": [],
    }


_OLD_TOML_WORKFLOW = """
[workflow]
id = "old-flow"
name = "Old flow"

[[nodes]]
id = "hello"
type = "bash_command"
command = "echo hello"
"""


def test_old_toml_workflows_are_listed_for_conversion_and_only_they_convert(
    tmp_path: Path,
) -> None:
    from gofer.ui.api import convert_toml_workflow_payload

    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    data_dir.mkdir()
    project.mkdir()
    old = data_dir / "old-flow.toml"
    old.write_text(_OLD_TOML_WORKFLOW, encoding="utf-8")
    # TOML files that aren't workflows, and ones below the top of the data folder, aren't listed.
    (data_dir / "settings.toml").write_text('theme = "dark"\n', encoding="utf-8")
    (data_dir / "nested").mkdir()
    (data_dir / "nested" / "deep.toml").write_text(_OLD_TOML_WORKFLOW, encoding="utf-8")

    listed = list_workflow_payloads(data_dir)
    assert listed["workflows"] == []
    assert listed["legacyTomlFiles"] == [{"name": "old-flow.toml", "path": str(old)}]

    # The route converts only a file the list names, so it can't be used to read others.
    for path in (data_dir / "settings.toml", data_dir / "nested" / "deep.toml"):
        with pytest.raises(WorkflowCreateError, match="Only a TOML workflow listed"):
            convert_toml_workflow_payload(path, project, data_dir)

    converted = convert_toml_workflow_payload(old, project, data_dir)
    assert converted["workflow"]["sourceFormat"] == "rattish"
    assert converted["workflow"]["name"] == "Old flow"
    assert Path(converted["workflow"]["sourcePath"]).is_relative_to(project)
    assert old.read_text(encoding="utf-8") == _OLD_TOML_WORKFLOW
    after = list_workflow_payloads(data_dir)
    assert [item["id"] for item in after["workflows"]] == [converted["workflow"]["id"]]
    # The file stays until the user removes it, so the notice still names it.
    assert after["legacyTomlFiles"] == listed["legacyTomlFiles"]
