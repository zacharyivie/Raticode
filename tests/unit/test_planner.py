from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from pytest import MonkeyPatch

from gofer.core.agent import AgentConfig
from gofer.core.graph import EdgeConditionType, EdgeConfig, GraphNode
from gofer.core.llm_prompts import common_llm_task_prompt
from gofer.core.operations import (
    AgentOperation,
    ApprovalGateOperation,
    BashCommandOperation,
    CommonLlmTaskOperation,
    CopyFileOperation,
    CountFanSource,
    DeleteFileOperation,
    DirectoryFanSource,
    HttpRequestOperation,
    LocalSearchOperation,
    LocalVectorizeOperation,
    LoopOperation,
    MoveFileOperation,
    NotificationOperation,
    OperationType,
    PromptFileOperation,
    PythonScriptOperation,
    ReadFileOperation,
    ShellScriptOperation,
    TabularFanSource,
    TriggerEventsFanSource,
    WriteFileOperation,
)
from gofer.core.planner import build_execution_plan
from gofer.core.resources import ResourceLimits
from gofer.core.workflow import (
    AgenticWorkflow,
    FilesystemAccessEntry,
    WatchConfig,
    WorkflowConfig,
)


def test_plan_command_script_file_agent_and_conditional_edges(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "gofer.core.provider_capabilities.shutil.which",
        lambda binary: f"/usr/bin/{binary}",
    )
    source = tmp_path / "input.txt"
    source.write_text("hello")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Summarize")
    extra_dir = tmp_path / "shared"
    extra_dir.mkdir()
    workflow = AgenticWorkflow(
        WorkflowConfig(
            id="plan-demo",
            name="Plan Demo",
            resource_limits=ResourceLimits(max_fanout_items=5, max_files_scanned=20),
            max_total_node_runs=25,
        )
    )
    workflow.register_agent(
        AgentConfig(
            agent_id="reviewer",
            subscription="codex",
            working_dir=tmp_path,
            prompt_path=prompt,
            env={"TOKEN": "env:MISSING_TOKEN"},
            extra_paths=[extra_dir],
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="command",
            inputs={"prior": "read.output"},
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command="rm -rf build && echo done",
                working_dir=tmp_path,
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="script",
            operation=PythonScriptOperation(
                type=OperationType.PYTHON_SCRIPT,
                script_path=tmp_path / "job.py",
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="read",
            operation=ReadFileOperation(type=OperationType.READ_FILE, path=source),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="write",
            operation=WriteFileOperation(
                type=OperationType.WRITE_FILE,
                path=tmp_path / "out.txt",
                overwrite=True,
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="delete",
            operation=DeleteFileOperation(
                type=OperationType.DELETE_FILE,
                path=tmp_path / "old",
                recursive=True,
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="copy",
            operation=CopyFileOperation(
                type=OperationType.COPY_FILE,
                source_path=source,
                destination_path=tmp_path / "copy.txt",
                overwrite=True,
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="move",
            operation=MoveFileOperation(
                type=OperationType.MOVE_FILE,
                source_path=source,
                destination_path=tmp_path / "moved.txt",
                overwrite=True,
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="reviewer",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )
    workflow.then(
        "command",
        "script",
        EdgeConfig(
            from_node="command",
            to_node="script",
            condition=EdgeConditionType.ON_SUCCESS,
        ),
    )
    workflow.then(
        "script",
        "agent",
        EdgeConfig(
            from_node="script",
            to_node="agent",
            condition=EdgeConditionType.OUTPUT_MATCHES,
            output_pattern="ok",
        ),
    )

    plan = build_execution_plan(workflow)

    assert [generation["index"] for generation in plan["generations"]] == [0, 1, 2]
    assert plan["startNodes"] == ["command", "copy", "delete", "move", "read", "write"]
    assert plan["resourceLimits"]["max_fanout_items"] == 5
    assert plan["executionLimits"]["maxTotalNodeRuns"] == 25
    assert plan["validation"]["ok"] is False
    assert any(
        item["message"].startswith("Script path") for item in plan["validation"]["diagnostics"]
    )
    assert plan["conditionalBranches"] == [
        {
            "from": "command",
            "to": "script",
            "condition": "on_success",
            "label": "on_success",
            "outputPattern": None,
        },
        {
            "from": "script",
            "to": "agent",
            "condition": "output_matches",
            "label": "output_matches:ok",
            "outputPattern": "ok",
        },
    ]
    assert "Shell command effects cannot be inferred" in plan["warnings"]
    assert "unknown shell command effects: rm -rf build && echo done" in plan["destructiveActions"]
    assert f"unknown python script effects: {tmp_path / 'job.py'}" in plan["destructiveActions"]
    assert f"overwrite file: {tmp_path / 'out.txt'}" in plan["destructiveActions"]
    assert f"overwrite copy destination: {tmp_path / 'copy.txt'}" in plan["destructiveActions"]
    assert f"move source: {source}" in plan["destructiveActions"]
    assert f"overwrite move destination: {tmp_path / 'moved.txt'}" in plan["destructiveActions"]
    assert f"recursive delete: {tmp_path / 'old'}" in plan["destructiveActions"]
    copy_node = next(
        node
        for generation in plan["generations"]
        for node in generation["nodes"]
        if node["id"] == "copy"
    )
    move_node = next(
        node
        for generation in plan["generations"]
        for node in generation["nodes"]
        if node["id"] == "move"
    )
    assert f"copy file: {source} -> {tmp_path / 'copy.txt'}" in copy_node["sideEffects"]
    assert f"move file: {source} -> {tmp_path / 'moved.txt'}" in move_node["sideEffects"]
    assert {
        "kind": "file",
        "action": "copy",
        "sourcePath": str(source),
        "sourceExists": True,
        "destinationPath": str(tmp_path / "copy.txt"),
        "destinationExists": False,
        "destructive": True,
        "effectsInferred": True,
        "overwrite": True,
    } in copy_node["sideEffectDetails"]
    assert {
        "kind": "file",
        "action": "overwrite_copy_destination",
        "path": str(tmp_path / "copy.txt"),
        "exists": False,
        "destructive": True,
        "effectsInferred": True,
        "overwrite": True,
    } in plan["destructiveActionDetails"]
    assert plan["requiredSecrets"] == ["MISSING_TOKEN"]
    assert plan["providerRequirements"] == [
        {
            "agentId": "reviewer",
            "subscription": "codex",
            "workingDir": str(tmp_path),
            "binary": "codex",
            "directApi": False,
            "apiBaseUrl": None,
            "available": True,
            "extraPaths": [str(extra_dir.resolve())],
        }
    ]
    prior_binding = next(
        binding
        for binding in plan["bindings"]
        if binding["destinationNode"] == "command" and binding["destinationField"] == "inputs.prior"
    )
    assert prior_binding["expression"] == "read.output"
    assert prior_binding["status"] == "invalid"
    command_node = plan["generations"][0]["nodes"][0]
    assert command_node["bindings"] == [prior_binding]
    assert {
        "from": "script",
        "to": "agent",
        "condition": "output_matches",
        "label": "output_matches:ok",
        "outputPattern": "ok",
    } in plan["edges"]


def test_plan_fan_out_sources_report_counts_samples_and_trigger_context(
    tmp_path: Path,
) -> None:
    rows = tmp_path / "rows.jsonl"
    rows.write_text('{"name": "a"}\n{"name": "b"}\n')
    files = tmp_path / "files"
    files.mkdir()
    (files / "one.txt").write_text("one")
    (files / "two.txt").write_text("two")
    workflow = AgenticWorkflow(
        WorkflowConfig(
            id="fan-plan",
            name="Fan Plan",
            watch=WatchConfig(path=files, glob="*.txt", mode="fanout"),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="count",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=CountFanSource(type="count", count=3),
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="tabular",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TabularFanSource(type="tabular", path=rows),
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="directory",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(type="directory", path=files, glob="*.txt"),
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="trigger",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TriggerEventsFanSource(
                    type="trigger_events",
                    include_content=True,
                ),
            ),
        )
    )

    plan = build_execution_plan(
        workflow,
        trigger_context={
            "events": [
                "ignored",
                {"path": str(files / "one.txt"), "event": "modified"},
            ]
        },
    )
    fan_outs = {
        node["id"]: node["fanOut"]
        for generation in plan["generations"]
        for node in generation["nodes"]
    }

    assert fan_outs["count"]["count"] == 3
    assert fan_outs["count"]["sampleItems"] == [
        {"index": "0"},
        {"index": "1"},
        {"index": "2"},
    ]
    assert fan_outs["tabular"]["count"] == 2
    assert fan_outs["tabular"]["countExact"] is True
    assert fan_outs["tabular"]["countLowerBound"] == 2
    assert fan_outs["tabular"]["sampleItems"][0]["name"] == "a"
    assert fan_outs["directory"]["count"] == 2
    assert fan_outs["directory"]["countExact"] is True
    assert fan_outs["directory"]["countLowerBound"] == 2
    assert fan_outs["directory"]["sampleItems"][0]["name"] == "one.txt"
    assert fan_outs["trigger"]["count"] == 1
    assert fan_outs["trigger"]["countExact"] is True
    assert fan_outs["trigger"]["countLowerBound"] == 1
    assert fan_outs["trigger"]["sampleItems"] == [
        {
            "path": str(files / "one.txt"),
            "event": "modified",
            "index": "1",
            "event_json": json.dumps(
                {
                    "path": str(files / "one.txt"),
                    "event": "modified",
                }
            ),
            "file_path": str(files / "one.txt"),
            "file_name": "one.txt",
            "file_stem": "one",
            "file_extension": ".txt",
            "parent_path": str(files),
            "directory": str(files),
            "name": "one.txt",
            "sizeBytes": 3,
            "contentIncluded": False,
        }
    ]
    assert "Skipped 1 non-object trigger event" in fan_outs["trigger"]["warnings"]
    assert "Trigger event file content omitted from plan preview" in fan_outs["trigger"]["warnings"]
    assert plan["triggerContext"]["watch"]["mode"] == "fanout"
    assert plan["triggerContext"]["provided"]["events"][1]["event"] == "modified"


def test_plan_trigger_event_fan_out_without_events_is_unknown() -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="trigger-no-events", name="Trigger No Events"))
    workflow.add_operation(
        GraphNode(
            node_id="trigger",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TriggerEventsFanSource(type="trigger_events"),
            ),
        )
    )

    plan = build_execution_plan(workflow, trigger_context={"type": "file_watch"})
    fan_out = plan["generations"][0]["nodes"][0]["fanOut"]

    assert fan_out["count"] is None
    assert fan_out["sampleItems"] == []
    assert (
        "No trigger context events provided; trigger-event fan-out count cannot be estimated"
        in fan_out["warnings"]
    )
    assert (
        "No trigger context events provided; trigger-event fan-out count cannot be estimated"
        in plan["warnings"]
    )


def test_plan_count_fan_out_resolves_numeric_string_count() -> None:
    workflow = AgenticWorkflow(
        WorkflowConfig(id="numeric-string-count", name="Numeric String Count")
    )
    workflow.add_operation(
        GraphNode(
            node_id="count",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=CountFanSource(type="count", count="3"),
            ),
        )
    )

    plan = build_execution_plan(workflow)
    fan_out = plan["generations"][0]["nodes"][0]["fanOut"]

    assert fan_out["count"] == 3
    assert fan_out["sampleItems"] == [
        {"index": "0"},
        {"index": "1"},
        {"index": "2"},
    ]
    assert fan_out["warnings"] == []
    assert plan["bindings"] == []


def test_plan_agent_dynamic_count_reports_fan_out_estimate() -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="agent-dynamic-count", name="Agent Dynamic Count"))
    workflow.agents["reviewer"] = AgentConfig(
        agent_id="reviewer",
        subscription="codex",
        working_dir=Path("."),
    )
    workflow.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="reviewer",
                working_dir=Path("."),
                dynamic_count="3",
            ),
        )
    )

    plan = build_execution_plan(workflow)
    fan_out = plan["generations"][0]["nodes"][0]["fanOut"]

    assert fan_out["sourceType"] == "agent_dynamic_count"
    assert fan_out["count"] == 3
    assert fan_out["sampleItems"] == [
        {"index": "0"},
        {"index": "1"},
        {"index": "2"},
    ]
    assert (
        "agent dynamic_count is deprecated; use a loop node feeding this agent"
        in fan_out["warnings"]
    )


def test_plan_loop_fan_out_projects_downstream_agent_usage() -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="loop-agent-usage", name="Loop Agent Usage"))
    workflow.agents["reviewer"] = AgentConfig(
        agent_id="reviewer",
        subscription="codex",
        working_dir=Path("."),
    )
    workflow.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=CountFanSource(type="count", count=3),
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="reviewer",
                working_dir=Path("."),
                skill_name="review",
            ),
        )
    )
    workflow.then("loop", "agent")

    plan = build_execution_plan(workflow)
    agent_plan = plan["generations"][1]["nodes"][0]

    assert agent_plan["projectedLlmUsage"]["agent_calls"] == 3
    assert plan["projectedLlmUsage"]["agent_calls"] == 3


def test_plan_agent_usage_falls_back_to_registered_prompt_path(tmp_path: Path) -> None:
    prompt = tmp_path / "agent-default.md"
    prompt.write_text("12345678", encoding="utf-8")
    workflow = AgenticWorkflow(
        WorkflowConfig(id="agent-default-prompt", name="Agent Default Prompt")
    )
    workflow.agents["reviewer"] = AgentConfig(
        agent_id="reviewer",
        subscription="codex",
        working_dir=tmp_path,
        prompt_path=prompt,
    )
    workflow.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="reviewer",
                working_dir=tmp_path,
            ),
        )
    )

    plan = build_execution_plan(workflow, workflow_path=tmp_path / "workflow.toml")
    agent_plan = plan["generations"][0]["nodes"][0]

    assert agent_plan["projectedLlmUsage"]["input_tokens"] == 2
    assert plan["projectedLlmUsage"]["input_tokens"] == 2


def test_plan_common_llm_task_uses_runtime_prompt_template(tmp_path: Path) -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="common-task-prompt", name="Common Task Prompt"))
    workflow.agents["reviewer"] = AgentConfig(
        agent_id="reviewer",
        subscription="codex",
        working_dir=tmp_path,
    )
    workflow.add_operation(
        GraphNode(
            node_id="summarize",
            operation=CommonLlmTaskOperation(
                type=OperationType.COMMON_LLM_TASK,
                agent_id="reviewer",
                working_dir=tmp_path,
                task="review",
                target="notes.md",
                instructions="Focus on risks.",
            ),
        )
    )

    plan = build_execution_plan(workflow)
    node_plan = plan["generations"][0]["nodes"][0]
    expected_prompt = common_llm_task_prompt(
        "review",
        "notes.md",
        "Focus on risks.",
    )

    assert node_plan["projectedLlmUsage"]["input_tokens"] == (len(expected_prompt) + 3) // 4


def test_plan_provider_requirements_report_missing_binary(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr("gofer.core.provider_capabilities.shutil.which", lambda _binary: None)
    workflow = AgenticWorkflow(WorkflowConfig(id="provider-plan", name="Provider Plan"))
    workflow.register_agent(
        AgentConfig(agent_id="reviewer", subscription="codex", working_dir=tmp_path)
    )
    workflow.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="reviewer",
                working_dir=tmp_path,
            ),
        )
    )

    plan = build_execution_plan(workflow)

    assert plan["providerRequirements"] == [
        {
            "agentId": "reviewer",
            "subscription": "codex",
            "workingDir": str(tmp_path),
            "binary": "codex",
            "directApi": False,
            "apiBaseUrl": None,
            "available": False,
            "extraPaths": [],
        }
    ]
    assert "Provider CLI 'codex' is not available for agent reviewer" in plan["warnings"]


def test_plan_http_request_reports_host_dynamic_values_and_missing_secret(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOFER_SECRET_API_TOKEN", raising=False)
    monkeypatch.delenv("API_TOKEN", raising=False)
    workflow = AgenticWorkflow(WorkflowConfig(id="http-plan", name="HTTP Plan"))
    workflow.add_operation(
        GraphNode(
            node_id="create",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method="POST",
                url="https://api.example.test/issues/{{trigger.issue_id}}",
                headers={"Authorization": "{{secret.API_TOKEN}}"},
                json={"title": "{{previous.output}}"},
                expected_statuses=[201],
            ),
        )
    )

    plan = build_execution_plan(workflow)
    node = plan["generations"][0]["nodes"][0]

    assert node["detail"] == "POST api.example.test"
    assert node["sideEffectDetails"][0]["kind"] == "network"
    assert node["sideEffectDetails"][0]["host"] == "api.example.test"
    assert "API_TOKEN" in plan["requiredSecrets"]
    assert {
        "name": "API_TOKEN",
        "status": "missing",
        "present": False,
        "sources": ["node:create"],
        "envNames": ["GOFER_SECRET_API_TOKEN", "API_TOKEN"],
    } in plan["secretReadiness"]
    issue_binding = next(
        binding for binding in plan["bindings"] if binding["destinationField"] == "operation.url"
    )
    assert issue_binding["expression"] == "trigger.issue_id"
    assert issue_binding["status"] == "optional"
    assert issue_binding["coercion"] == "string"


def test_plan_reports_present_secret_readiness(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("GOFER_SECRET_API_TOKEN", "not-shown")
    workflow = AgenticWorkflow(WorkflowConfig(id="secret-plan", name="Secret Plan"))
    workflow.add_operation(
        GraphNode(
            node_id="notify",
            operation=NotificationOperation(
                type=OperationType.NOTIFICATION,
                title="Deploy",
                body="Token {{secret.API_TOKEN}}",
            ),
        )
    )

    plan = build_execution_plan(workflow)

    assert plan["requiredSecrets"] == ["API_TOKEN"]
    assert plan["secretReadiness"] == [
        {
            "name": "API_TOKEN",
            "status": "present",
            "present": True,
            "sources": ["node:notify"],
            "envNames": ["GOFER_SECRET_API_TOKEN", "API_TOKEN"],
            "maskedValue": "***",
        }
    ]
    assert "not-shown" not in json.dumps(plan)


def test_plan_http_request_reports_network_policy_and_allowlist() -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="http-plan", name="HTTP Plan"))
    workflow.add_operation(
        GraphNode(
            node_id="internal",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method="GET",
                url="http://10.0.0.5/status",
                network_allowlist=["10.0.0.0/8"],
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="metadata",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method="GET",
                url="http://169.254.169.254/latest?token=secret",
            ),
        )
    )

    plan = build_execution_plan(workflow)
    nodes = {node["id"]: node for generation in plan["generations"] for node in generation["nodes"]}

    assert nodes["internal"]["sideEffectDetails"][0]["networkAllowlist"] == ["10.0.0.0/8"]
    assert nodes["metadata"]["sideEffectDetails"][0]["networkAllowlist"] == []
    assert any(
        "blocked private or local address" in warning for warning in nodes["metadata"]["warnings"]
    )
    assert all("secret" not in warning for warning in nodes["metadata"]["warnings"])


def test_plan_http_request_masks_secret_url_and_params() -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="http-plan", name="HTTP Plan"))
    workflow.add_operation(
        GraphNode(
            node_id="notify",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method="POST",
                url="secret:WEBHOOK_URL",
                params={"token": "{{secret.API_TOKEN}}", "team": "ops"},
                secret_fields=["url"],
            ),
        )
    )

    plan = build_execution_plan(workflow)
    detail = plan["generations"][0]["nodes"][0]["sideEffectDetails"][0]

    assert detail["url"] == "***"
    assert detail["params"] == {"token": "***", "team": "ops"}


def test_plan_http_request_masks_secret_query_in_url() -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="http-plan", name="HTTP Plan"))
    workflow.add_operation(
        GraphNode(
            node_id="notify",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method="GET",
                url="https://api.example.test/hooks?token={{secret.API_TOKEN}}&team=ops",
            ),
        )
    )

    plan = build_execution_plan(workflow)
    detail = plan["generations"][0]["nodes"][0]["sideEffectDetails"][0]

    assert detail["url"] == "***"
    assert detail["host"] == "api.example.test"


def test_plan_http_request_masks_configured_secret_values_everywhere() -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="http-plan", name="HTTP Plan"))
    workflow.add_operation(
        GraphNode(
            node_id="notify",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method="POST",
                url=(
                    "https://api.example.test/hooks?password=cleartext-secret&echo=cleartext-secret"
                ),
                params={
                    "password": "cleartext-secret",
                    "echo": "cleartext-secret",
                    "team": "ops",
                },
                json={
                    "password": "cleartext-secret",
                    "nested": {"echo": "cleartext-secret"},
                },
                secret_fields=["password"],
            ),
        )
    )

    plan = build_execution_plan(workflow)
    detail = plan["generations"][0]["nodes"][0]["sideEffectDetails"][0]

    assert detail["params"] == {
        "password": "***",
        "echo": "***",
        "team": "ops",
    }
    assert "cleartext-secret" not in json.dumps(detail)


def test_plan_approval_gate_and_notification_side_effects() -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="human-plan", name="Human Plan"))
    workflow.add_operation(
        GraphNode(
            node_id="approve",
            operation=ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message="Approve {{trigger.change_id}}?",
                timeout_seconds=60,
                approvers=["ops"],
                notify=True,
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="notify",
            operation=NotificationOperation(
                type=OperationType.NOTIFICATION,
                title="Needs review",
                body="Run {{trigger.run_id}} needs attention",
            ),
        )
    )

    plan = build_execution_plan(workflow)
    approval = plan["generations"][0]["nodes"][0]
    notification = plan["generations"][0]["nodes"][1]

    assert approval["type"] == "approval_gate"
    assert approval["sideEffects"] == ["pause for approval"]
    assert approval["sideEffectDetails"][0]["approvers"] == ["ops"]
    assert "Approval message contains unresolved dynamic values" in " ".join(approval["warnings"])
    assert notification["type"] == "notification"
    assert notification["sideEffectDetails"][0]["channel"] == "desktop"


def test_plan_notification_masks_webhook_secret_details() -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="notify-plan", name="Notify Plan"))
    workflow.add_operation(
        GraphNode(
            node_id="notify",
            operation=NotificationOperation(
                type=OperationType.NOTIFICATION,
                channel="slack",
                webhook_url="{{secret.SLACK_WEBHOOK_URL}}",
                headers={"Authorization": "{{secret.API_TOKEN}}"},
                payload={"password": "literal-password", "safe": "ok"},
            ),
        )
    )

    plan = build_execution_plan(workflow)
    detail = plan["generations"][0]["nodes"][0]["sideEffectDetails"][0]

    assert detail["channel"] == "slack"
    assert detail["webhookUrl"] == "***"
    assert detail["headers"] == {"Authorization": "***"}
    assert detail["payload"] == {"password": "***", "safe": "ok"}
    assert sorted(plan["generations"][0]["nodes"][0]["requiredSecrets"]) == [
        "API_TOKEN",
        "SLACK_WEBHOOK_URL",
    ]
    assert sorted(plan["requiredSecrets"]) == ["API_TOKEN", "SLACK_WEBHOOK_URL"]
    assert "literal-password" not in json.dumps(plan)


def test_plan_notification_masks_smtp_username() -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="notify-email-plan", name="Notify Email Plan"))
    workflow.add_operation(
        GraphNode(
            node_id="notify",
            operation=NotificationOperation(
                type=OperationType.NOTIFICATION,
                channel="email",
                email_from="gofer@example.test",
                email_to=["ops@example.test"],
                smtp_host="smtp.example.test",
                smtp_username="smtp-user",
                smtp_password="smtp-secret",
            ),
        )
    )

    plan = build_execution_plan(workflow)
    serialized = json.dumps(plan)
    detail = plan["generations"][0]["nodes"][0]["sideEffectDetails"][0]

    assert detail["smtpUsername"] == "***"
    assert "smtp-user" not in serialized
    assert "smtp-secret" not in serialized


def test_plan_directory_fan_out_scan_is_bounded(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    for index in range(5):
        (files / f"{index}.txt").write_text(str(index))

    workflow = AgenticWorkflow(
        WorkflowConfig(
            id="bounded-directory-plan",
            name="Bounded Directory Plan",
            resource_limits=ResourceLimits(max_fanout_items=10, max_files_scanned=3),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="directory",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(type="directory", path=files, glob="*.txt"),
            ),
        )
    )

    plan = build_execution_plan(workflow, sample_limit=2)
    fan_out = plan["generations"][0]["nodes"][0]["fanOut"]

    assert fan_out["count"] == 3
    assert fan_out["countExact"] is False
    assert fan_out["countLowerBound"] == 3
    assert fan_out["scannedPaths"] == 4
    assert len(fan_out["sampleItems"]) == 2
    assert (
        "Directory fan-out scan exceeded limit 3 paths; preview count is partial"
        in fan_out["warnings"]
    )
    assert (
        "Directory fan-out scan exceeded limit 3 paths; preview count is partial"
        in plan["warnings"]
    )


def test_plan_directory_fan_out_item_limit_is_bounded(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    for index in range(4):
        (files / f"{index}.txt").write_text(str(index))

    workflow = AgenticWorkflow(
        WorkflowConfig(
            id="directory-item-limit-plan",
            name="Directory Item Limit Plan",
            resource_limits=ResourceLimits(max_fanout_items=2, max_files_scanned=10),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="directory",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(type="directory", path=files, glob="*.txt"),
            ),
        )
    )

    plan = build_execution_plan(workflow, sample_limit=2)
    fan_out = plan["generations"][0]["nodes"][0]["fanOut"]

    assert fan_out["count"] == 3
    assert fan_out["countExact"] is False
    assert fan_out["countLowerBound"] == 3
    assert fan_out["scannedPaths"] == 3
    assert len(fan_out["sampleItems"]) == 2
    assert (
        "Directory fan-out count exceeds limit 2 items; preview count is partial"
        in fan_out["warnings"]
    )


def test_plan_tabular_fan_out_over_limit_keeps_count_samples_and_warning(
    tmp_path: Path,
) -> None:
    rows = tmp_path / "rows.csv"
    rows.write_text("name\none\ntwo\nthree\n")
    workflow = AgenticWorkflow(
        WorkflowConfig(
            id="large-tabular-plan",
            name="Large Tabular Plan",
            resource_limits=ResourceLimits(max_fanout_items=2),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="tabular",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TabularFanSource(type="tabular", path=rows),
            ),
        )
    )

    plan = build_execution_plan(workflow, sample_limit=2)
    fan_out = plan["generations"][0]["nodes"][0]["fanOut"]

    assert fan_out["count"] == 3
    assert fan_out["countExact"] is False
    assert fan_out["countLowerBound"] == 3
    assert fan_out["sampleItems"] == [
        {"name": "one", "_row": '{"name": "one"}'},
        {"name": "two", "_row": '{"name": "two"}'},
    ]
    assert (
        "Tabular fan-out count 3 exceeds limit 2; preview count is partial" in fan_out["warnings"]
    )
    assert "Tabular fan-out count 3 exceeds limit 2; preview count is partial" in plan["warnings"]


def test_plan_tabular_fan_out_xlsx_rows_with_dates_are_serializable(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    rows = tmp_path / "rows.xlsx"
    rows.write_bytes(b"placeholder")

    class Workbook:
        active = SimpleNamespace(
            iter_rows=lambda values_only=True: iter(
                [
                    ("name", "created"),
                    ("one", date(2026, 6, 24)),
                ]
            )
        )

        def close(self) -> None:
            return None

    monkeypatch.setitem(
        sys.modules,
        "openpyxl",
        SimpleNamespace(load_workbook=lambda *_args, **_kwargs: Workbook()),
    )
    workflow = AgenticWorkflow(WorkflowConfig(id="xlsx-plan", name="XLSX Plan"))
    workflow.add_operation(
        GraphNode(
            node_id="tabular",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TabularFanSource(type="tabular", path=rows),
            ),
        )
    )

    plan = build_execution_plan(workflow)
    fan_out = plan["generations"][0]["nodes"][0]["fanOut"]

    assert fan_out["count"] == 1
    assert fan_out["sampleItems"][0]["_row"] == ('{"name": "one", "created": "2026-06-24"}')


def test_plan_bindings_ignore_literal_dotted_strings(
    tmp_path: Path,
) -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="dotted-literals", name="Dotted Literals"))
    workflow.register_agent(
        AgentConfig(agent_id="reviewer", subscription="codex", working_dir=tmp_path)
    )
    workflow.add_operation(
        GraphNode(
            node_id="read",
            operation=ReadFileOperation(
                type=OperationType.READ_FILE,
                path=tmp_path / "file.txt",
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="command",
            inputs={
                "file": "file.txt",
                "version": "v1.2",
                "domain": "example.com",
                "prior": "read.output",
                "event": "trigger.value",
            },
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command="echo hello",
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="reviewer",
                working_dir=tmp_path,
                input_mapping={
                    "literal": "notes.md",
                    "prior": "read.text",
                    "loop_path": "loop.current.path",
                },
            ),
        )
    )

    plan = build_execution_plan(workflow)

    assert {
        (binding["destinationNode"], binding["destinationField"], binding["expression"])
        for binding in plan["bindings"]
    } == {
        ("agent", "operation.input_mapping.loop_path", "loop.current.path"),
        ("agent", "operation.input_mapping.prior", "read.text"),
        ("command", "inputs.event", "trigger.value"),
        ("command", "inputs.prior", "read.output"),
    }
    assert all("notes.md" not in binding["expression"] for binding in plan["bindings"])


def test_plan_reports_missing_paths_without_executing(tmp_path: Path) -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="missing-plan", name="Missing Plan"))
    workflow.add_operation(
        GraphNode(
            node_id="missing-read",
            operation=ReadFileOperation(
                type=OperationType.READ_FILE,
                path=tmp_path / "missing.txt",
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="missing-fan",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(
                    type="directory",
                    path=tmp_path / "missing-dir",
                ),
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="missing-python-script",
            operation=PythonScriptOperation(
                type=OperationType.PYTHON_SCRIPT,
                script_path=tmp_path / "missing.py",
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="missing-shell-script",
            operation=ShellScriptOperation(
                type=OperationType.SHELL_SCRIPT,
                script_path=tmp_path / "missing.sh",
            ),
        )
    )

    plan = build_execution_plan(workflow)

    assert f"Missing read target: {tmp_path / 'missing.txt'}" in plan["warnings"]
    assert f"Missing directory fan-out source: {tmp_path / 'missing-dir'}" in plan["warnings"]
    assert f"Missing python script: {tmp_path / 'missing.py'}" in plan["warnings"]
    assert f"Missing shell script: {tmp_path / 'missing.sh'}" in plan["warnings"]
    missing_fan = [
        node
        for generation in plan["generations"]
        for node in generation["nodes"]
        if node["id"] == "missing-fan"
    ][0]
    assert missing_fan["fanOut"]["warnings"] == [
        f"Missing directory fan-out source: {tmp_path / 'missing-dir'}"
    ]


def test_plan_resolves_relative_paths_from_workflow_path(tmp_path: Path) -> None:
    workflow_dir = tmp_path / "stored"
    workflow_dir.mkdir()
    inputs = workflow_dir / "inputs"
    inputs.mkdir()
    (inputs / "one.txt").write_text("one")
    rows = workflow_dir / "rows.csv"
    rows.write_text("name\none\n")
    script = workflow_dir / "scripts" / "job.py"
    script.parent.mkdir()
    script.write_text("print('ok')\n")
    workflow_path = workflow_dir / "relative.toml"

    workflow = AgenticWorkflow(
        WorkflowConfig(
            id="relative-plan",
            name="Relative Plan",
            watch=WatchConfig(path=Path("inputs"), glob="*.txt"),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="script",
            operation=PythonScriptOperation(
                type=OperationType.PYTHON_SCRIPT,
                script_path=Path("scripts/job.py"),
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="read",
            operation=ReadFileOperation(
                type=OperationType.READ_FILE,
                path=Path("inputs/one.txt"),
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="directory",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(
                    type="directory",
                    path=Path("inputs"),
                    glob="*.txt",
                ),
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="tabular",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TabularFanSource(type="tabular", path=Path("rows.csv")),
            ),
        )
    )

    plan = build_execution_plan(workflow, workflow_path=workflow_path)
    fan_outs = {
        node["id"]: node["fanOut"]
        for generation in plan["generations"]
        for node in generation["nodes"]
    }
    nodes = {node["id"]: node for generation in plan["generations"] for node in generation["nodes"]}

    assert plan["pathResolutionBase"] == str(workflow_dir)
    assert plan["triggerContext"]["watch"]["path"] == str(inputs)
    assert f"Missing read target: {workflow_dir / 'inputs/one.txt'}" not in plan["warnings"]
    assert f"python script: {script}" in nodes["script"]["sideEffects"]
    assert fan_outs["directory"]["path"] == str(inputs)
    assert fan_outs["directory"]["count"] == 1
    assert fan_outs["directory"]["sampleItems"][0]["path"] == str(inputs / "one.txt")
    assert fan_outs["tabular"]["path"] == str(rows)
    assert fan_outs["tabular"]["count"] == 1


def test_plan_warns_for_ungranted_managed_paths_outside_workflow_project(
    tmp_path: Path,
) -> None:
    workflow_dir = tmp_path / "project"
    external_dir = tmp_path / "external"
    workflow_dir.mkdir()
    external_dir.mkdir()
    (external_dir / "rows.csv").write_text("name\noutside\n")
    (external_dir / "template.md").write_text("Hello")
    (external_dir / "docs").mkdir()
    (external_dir / "docs" / "one.txt").write_text("outside")
    index_path = external_dir / "index.json"
    workflow = AgenticWorkflow(
        WorkflowConfig(
            id="access-plan",
            name="Access Plan",
            filesystem_access=[
                FilesystemAccessEntry(
                    path=external_dir / "template.md",
                    read=True,
                    write=False,
                )
            ],
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="tabular",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TabularFanSource(type="tabular", path=external_dir / "rows.csv"),
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="prompt",
            operation=PromptFileOperation(
                type=OperationType.PROMPT_FILE,
                template_path=external_dir / "template.md",
                output_path=external_dir / "out.md",
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="vectorize",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=external_dir / "docs",
                index_path=index_path,
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="search",
            operation=LocalSearchOperation(
                type=OperationType.LOCAL_SEARCH,
                index_path=index_path,
                query="outside",
            ),
        )
    )

    plan = build_execution_plan(
        workflow,
        workflow_path=workflow_dir / "flow.toml",
    )

    warnings = "\n".join(plan["warnings"])
    assert "Node 'tabular' tabular fan-out path requires read access" in warnings
    assert "Node 'prompt' prompt output path requires write access" in warnings
    assert "Node 'prompt' prompt template path requires read access" not in warnings
    assert "Node 'vectorize' local_vectorize source path requires read access" in warnings
    assert "Node 'vectorize' local_vectorize index path requires write access" in warnings
    assert "Node 'search' local_search index path requires read access" in warnings


def test_plan_skips_ungranted_outside_fanout_previews(tmp_path: Path) -> None:
    workflow_dir = tmp_path / "project"
    external_dir = tmp_path / "external"
    workflow_dir.mkdir()
    external_dir.mkdir()
    rows = external_dir / "rows.csv"
    rows.write_text("name\nleaked-row\n")
    directory = external_dir / "docs"
    directory.mkdir()
    (directory / "leaked-file.txt").write_text("outside")
    workflow = AgenticWorkflow(WorkflowConfig(id="access-plan", name="Access Plan"))
    workflow.add_operation(
        GraphNode(
            node_id="tabular",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=TabularFanSource(type="tabular", path=rows),
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="directory",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(type="directory", path=directory),
            ),
        )
    )

    plan = build_execution_plan(
        workflow,
        workflow_path=workflow_dir / "flow.toml",
    )
    plan_nodes = {
        node["id"]: node for generation in plan["generations"] for node in generation["nodes"]
    }
    tabular_fan_out = plan_nodes["tabular"]["fanOut"]
    directory_fan_out = plan_nodes["directory"]["fanOut"]

    assert tabular_fan_out["sampleItems"] == []
    assert tabular_fan_out["count"] is None
    assert directory_fan_out["sampleItems"] == []
    assert directory_fan_out["count"] is None
    assert "scannedPaths" not in directory_fan_out
    plan_json = json.dumps(plan, default=str)
    assert "leaked-row" not in plan_json
    assert "leaked-file.txt" not in plan_json
    assert "Tabular fan-out preview skipped because read access is not granted" in plan_json
    assert "Directory fan-out preview skipped because read access is not granted" in plan_json


def test_plan_reports_unregistered_agent() -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="missing-agent", name="Missing Agent"))
    workflow.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="reviewer",
                working_dir=Path("."),
            ),
        )
    )

    plan = build_execution_plan(workflow)

    assert "Agent 'reviewer' is not registered in workflow" in plan["warnings"]
    assert plan["providerRequirements"] == []


def test_plan_json_is_serializable(tmp_path: Path) -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="json-plan", name="JSON Plan"))
    workflow.add_operation(
        GraphNode(
            node_id="command",
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command="echo hello",
            ),
        )
    )

    encoded = json.dumps(build_execution_plan(workflow), sort_keys=True)

    assert '"workflowId": "json-plan"' in encoded
