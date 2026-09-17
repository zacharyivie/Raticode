from __future__ import annotations

import json
import math
import shlex
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import anyio
import pytest

from gofer.core import executor as executor_module
from gofer.core.agent import AgentConfig, AgentResult
from gofer.core.approvals import ApprovalStore, RecordingNotificationAdapter
from gofer.core.executor import (
    ResumeOptions,
    WorkflowExecutor,
    WorkflowRunLog,
    command_shell_args,
)
from gofer.core.graph import EdgeConditionType, EdgeConfig, GraphNode
from gofer.core.http import HttpRequest, HttpResponse
from gofer.core.operations import (
    AgentOperation,
    ApprovalGateOperation,
    BashCommandOperation,
    BreakOperation,
    CommonLlmTaskOperation,
    CopyFileOperation,
    CountFanSource,
    DeleteFileOperation,
    DirectoryFanSource,
    FailOperation,
    FileOperation,
    FolderOperation,
    HttpRequestOperation,
    HttpRetryPolicy,
    InfiniteFanSource,
    LocalSearchOperation,
    LocalVectorizeOperation,
    LoopOperation,
    MoveFileOperation,
    NotificationOperation,
    OpenResourceOperation,
    OperationType,
    PassOperation,
    PromptFileOperation,
    PythonScriptOperation,
    ReadFileOperation,
    StartOperation,
    TriggerEventsFanSource,
    WriteFileOperation,
)
from gofer.core.planner import build_execution_plan
from gofer.core.provider_profiles import (
    ProviderProfile,
    ResolvedProviderSettings,
    save_provider_profiles,
)
from gofer.core.resources import ResourceLimits, byte_len
from gofer.core.usage import LlmPricing
from gofer.core.workflow import (
    AgenticWorkflow,
    FilesystemAccessEntry,
    WorkflowConfig,
    WorkflowParameterConfig,
    WorkflowVariableConfig,
)
from gofer.utils.run_state import (
    request_workflow_run_stop,
    request_workflow_stop,
    workflow_stop_path,
)
from tests.conftest import FakeSubscription, envelope_request


def _bash_node(node_id: str, command: str = "true") -> GraphNode:
    return GraphNode(
        node_id=node_id,
        operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command=command),
    )


def _make_workflow(wf_id: str = "test") -> AgenticWorkflow:
    return AgenticWorkflow(WorkflowConfig(id=wf_id, name="Test"))


class FakeHttpClient:
    def __init__(self, responses: list[HttpResponse | Exception]) -> None:
        self.responses = responses
        self.requests: list[HttpRequest] = []

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _agent_usage_workflow(
    tmp_path: Path,
    *,
    pricing: LlmPricing | None = None,
) -> AgenticWorkflow:
    wf = AgenticWorkflow(WorkflowConfig(id="usage", name="Usage"))
    wf.register_agent(
        AgentConfig(
            agent_id="agent",
            subscription="claude_code",
            working_dir=tmp_path,
            pricing=pricing or LlmPricing(chars_per_token=4.0),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="ask",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="agent",
                working_dir=tmp_path,
                skill_name="summarize",
            ),
        )
    )
    return wf


@pytest.mark.anyio
async def test_agent_usage_fallback_records_estimated_tokens_and_cost(tmp_path: Path) -> None:
    pricing = LlmPricing(
        input_cost_per_1k_tokens=1.0,
        output_cost_per_1k_tokens=2.0,
        chars_per_token=4.0,
    )
    wf = _agent_usage_workflow(tmp_path, pricing=pricing)
    sub = FakeSubscription(output="abcdefgh")

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    usage = cast(dict[str, Any], result.node_outputs["ask"].data["usage"])
    assert usage["estimated"] is True
    assert usage["source"] == "fallback_chars_per_token"
    assert usage["provider"] == "claude_code"
    input_tokens = math.ceil(len(str(sub.calls[0]["prompt"])) / 4)
    assert usage["input_tokens"] == input_tokens
    assert usage["output_tokens"] == 2
    assert usage["estimated_cost"] == pytest.approx(input_tokens / 1000 + 0.004)


@pytest.mark.anyio
async def test_agent_run_applies_profile_and_node_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    save_provider_profiles(
        {
            "fast": ProviderProfile(
                name="fast",
                subscription="claude_code",
                model="sonnet",
                timeout=60,
                tools=["Read"],
                env={"PROFILE_ENV": "1"},
                secret_refs={"PROFILE_SECRET": "PROFILE_TOKEN"},
            )
        },
        tmp_path,
    )
    monkeypatch.setenv("GOFER_SECRET_PROFILE_TOKEN", "secret-value")
    wf = AgenticWorkflow(WorkflowConfig(id="profiles", name="Profiles"))
    wf.register_agent(
        AgentConfig(
            agent_id="agent",
            subscription="claude_code",
            working_dir=tmp_path,
            profile="fast",
            env={"AGENT_ENV": "1"},
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="ask",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="agent",
                working_dir=tmp_path,
                skill_name="summarize",
                model="opus",
                timeout=30,
            ),
        )
    )
    sub = FakeSubscription(output="done")

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    call = sub.calls[0]
    settings = cast(Any, call["provider_settings"])
    assert result.success is True
    assert settings.profile_name == "fast"
    assert settings.model == "opus"
    assert settings.tools == ["Read"]
    assert call["tools"] == []
    assert call["env"] == {
        "PROFILE_ENV": "1",
        "PROFILE_SECRET": "secret-value",
        "AGENT_ENV": "1",
    }
    assert settings.timeout == 30
    totals = cast(dict[str, Any], result.usage_summary["totals"])
    assert totals["agent_calls"] == 1


@pytest.mark.anyio
async def test_agent_usage_ingests_provider_metadata(tmp_path: Path) -> None:
    class MetadataSubscription(FakeSubscription):
        async def execute(self, *args: Any, **kwargs: Any) -> AgentResult:
            result = await super().execute(*args, **kwargs)
            return result.model_copy(
                update={
                    "usage_metadata": {
                        "input_tokens": 11,
                        "output_tokens": 7,
                        "cost_usd": 0.123,
                        "model": "provider-model",
                        "source": "provider_metadata",
                        "estimated": False,
                    }
                }
            )

    wf = _agent_usage_workflow(tmp_path)
    result = await WorkflowExecutor(
        wf,
        {"claude_code": MetadataSubscription(output="short")},
        log_base_dir=tmp_path / "logs",
    ).run()

    usage = cast(dict[str, Any], result.node_outputs["ask"].data["usage"])
    assert usage["estimated"] is False
    assert usage["source"] == "provider_metadata"
    assert usage["input_tokens"] == 11
    assert usage["output_tokens"] == 7
    assert usage["model"] == "provider-model"
    assert usage["estimated_cost"] == pytest.approx(0.123)


@pytest.mark.anyio
async def test_agent_usage_does_not_persist_raw_provider_metadata(tmp_path: Path) -> None:
    class MetadataSubscription(FakeSubscription):
        async def execute(self, *args: Any, **kwargs: Any) -> AgentResult:
            result = await super().execute(*args, **kwargs)
            return result.model_copy(
                update={
                    "usage_metadata": {
                        "input_tokens": 3,
                        "output_tokens": 2,
                        "model": "provider-model",
                        "prompt": "api_key=secret-value",
                        "raw_request": {"authorization": "Bearer secret-value"},
                        "source": "provider_metadata",
                    }
                }
            )

    wf = _agent_usage_workflow(tmp_path)
    result = await WorkflowExecutor(
        wf,
        {"claude_code": MetadataSubscription(output="short")},
        log_base_dir=tmp_path / "logs",
    ).run()

    usage = cast(dict[str, Any], result.node_outputs["ask"].data["usage"])
    usage_json = json.dumps(usage, default=str)
    assert "secret-value" not in usage_json
    assert "raw_request" not in usage
    assert usage["input_tokens"] == 3
    assert usage["output_tokens"] == 2


@pytest.mark.anyio
async def test_agent_usage_prompt_length_includes_memory_context(tmp_path: Path) -> None:
    wf = _agent_usage_workflow(tmp_path, pricing=LlmPricing(chars_per_token=4.0))
    op = cast(AgentOperation, next(iter(wf.graph.topological_generations()))[0].operation)
    op.memory = "run"
    subscription = FakeSubscription(output="ok")
    executor = WorkflowExecutor(
        wf,
        {"claude_code": subscription},
        log_base_dir=tmp_path / "logs",
    )
    executor._agent_run_memory["ask"] = [
        {"role": "assistant", "body": "previous answer with useful detail"}
    ]

    result = await executor.run()

    usage = cast(dict[str, Any], result.node_outputs["ask"].data["usage"])
    prompt = cast(str, subscription.calls[0]["prompt"])
    assert "Previous conversation:" in prompt
    assert usage["prompt_length"] == len(prompt)


@pytest.mark.anyio
async def test_usage_summary_counts_each_fan_out_agent_run(tmp_path: Path) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text("Process item {{index}}.", encoding="utf-8")
    sub = FakeSubscription(output="done")
    save_provider_profiles(
        {
            "usage-profile": ProviderProfile(
                name="usage-profile",
                subscription="claude_code",
            )
        },
        tmp_path,
    )

    wf = _make_workflow()
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            profile="usage-profile",
            model="usage-model",
            prompt_path=prompt,
            pricing=LlmPricing(chars_per_token=4.0),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=CountFanSource(type="count", count=3, max_concurrency=1),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )
    wf.then("loop", "agent-step")

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    totals = cast(dict[str, Any], result.usage_summary["totals"])
    assert result.success
    assert len(result.node_runs["agent-step"]) == 3
    assert totals["agent_calls"] == 3
    nodes = cast(list[dict[str, object]], result.usage_summary["nodes"])
    assert len(nodes) == 3
    assert all(node["profile"] == "usage-profile" for node in nodes)
    assert all(node["model"] == "usage-model" for node in nodes)
    assert [node["prompt_length"] for node in nodes] == [
        len(str(call["prompt"])) for call in sub.calls
    ]
    assert all(node["output_length"] == len("done") for node in nodes)
    assert all(node["estimated"] is True for node in nodes)
    assert all(node["source"] == "fallback_chars_per_token" for node in nodes)


def test_dry_run_plan_projects_llm_usage(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("x" * 20, encoding="utf-8")
    wf = AgenticWorkflow(WorkflowConfig(id="plan-usage", name="Plan Usage"))
    wf.register_agent(
        AgentConfig(
            agent_id="agent",
            subscription="claude_code",
            working_dir=tmp_path,
            pricing=LlmPricing(chars_per_token=4.0),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="ask",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="agent",
                working_dir=tmp_path,
                prompt_path=prompt,
            ),
        )
    )

    plan = build_execution_plan(wf, workflow_path=tmp_path / "workflow.toml")

    assert plan["projectedLlmUsage"]["agent_calls"] == 1
    assert plan["projectedLlmUsage"]["input_tokens"] == 5


def test_dry_run_plan_uses_historical_averages(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("abcd", encoding="utf-8")
    log_dir = tmp_path / "logs" / "plan-node-usage"
    log_dir.mkdir(parents=True)
    (log_dir / "run.outputs.json").write_text(
        json.dumps(
            {
                "usageSummary": {
                    "nodes": [
                        {
                            "node_id": "ask",
                            "output_tokens": 20,
                            "duration_seconds": 7.0,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    wf = AgenticWorkflow(WorkflowConfig(id="plan-node-usage", name="Plan Node Usage"))
    wf.register_agent(
        AgentConfig(
            agent_id="agent",
            subscription="claude_code",
            working_dir=tmp_path,
            pricing=LlmPricing(
                input_cost_per_1k_tokens=1.0,
                output_cost_per_1k_tokens=2.0,
                chars_per_token=4.0,
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="ask",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="agent",
                working_dir=tmp_path,
                prompt_path=prompt,
            ),
        )
    )

    plan = build_execution_plan(wf, workflow_path=tmp_path / "workflow.toml")
    node_plan = plan["generations"][0]["nodes"][0]

    assert node_plan["projectedLlmUsage"]["output_tokens"] == 20
    assert node_plan["projectedLlmUsage"]["agent_time_seconds"] == 7.0
    assert node_plan["projectedLlmUsage"]["historical_samples"] == 1.0


def test_command_shell_args_uses_bash_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(executor_module.sys, "platform", "linux")

    assert command_shell_args("echo hello") == ["bash", "-c", "echo hello"]


def test_command_shell_args_uses_powershell_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(executor_module.sys, "platform", "win32")

    assert command_shell_args("Write-Output hello") == [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        "Write-Output hello",
    ]


async def test_single_bash_node_succeeds(tmp_path: Path) -> None:
    wf = _make_workflow()
    wf.add_operation(_bash_node("echo", "echo hello"))
    executor = WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs")
    result = await executor.run()
    assert result.success
    assert "echo" in result.node_outputs


async def test_workflow_executor_writes_structured_run_events(tmp_path: Path) -> None:
    wf = _make_workflow("timeline")
    wf.add_operation(_bash_node("start", "echo ok"))
    wf.add_operation(_bash_node("success", "echo success"))
    wf.add_operation(_bash_node("failure", "echo failure"))
    wf.add_operation(
        GraphNode(
            node_id="summary",
            operation=PassOperation(type=OperationType.PASS, message="done"),
        )
    )
    wf.then(
        "start",
        "success",
        EdgeConfig(
            from_node="start",
            to_node="success",
            condition=EdgeConditionType.ON_SUCCESS,
        ),
    )
    wf.then(
        "start",
        "failure",
        EdgeConfig(
            from_node="start",
            to_node="failure",
            condition=EdgeConditionType.ON_FAILURE,
        ),
    )
    wf.then("success", "summary")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.log_path is not None
    payload = json.loads(result.log_path.with_suffix(".events.json").read_text())
    events = payload["events"]
    assert any(event["nodeId"] == "start" and event["status"] == "started" for event in events)
    assert any(event["nodeId"] == "start" and event["status"] == "completed" for event in events)
    decisions = [
        event["data"]
        for event in events
        if event["nodeId"] == "start" and event["status"] == "edge_decision"
    ]
    assert {
        "from": "start",
        "to": "success",
        "condition": "on_success",
        "outputPattern": "",
        "matched": True,
    } in decisions
    assert {
        "from": "start",
        "to": "failure",
        "condition": "on_failure",
        "outputPattern": "",
        "matched": False,
    } in decisions
    assert payload["nodes"]["failure"]["status"] == "skipped"
    assert (
        payload["nodes"]["failure"]["data"]["skipReason"] == "start -> failure skipped (on_failure)"
    )
    assert payload["nodes"]["failure"]["data"]["incomingEdgeDecisions"] == [
        {
            "from": "start",
            "to": "failure",
            "condition": "on_failure",
            "outputPattern": "",
            "matched": False,
            "reason": "start -> failure skipped (on_failure)",
        }
    ]
    assert payload["nodes"]["start"]["data"]["edgeDecisions"][0]["to"] == "success"
    assert payload["nodes"]["summary"]["data"]["message"] == "done"


def test_workflow_run_log_matches_fan_out_attempt_completion_by_item(tmp_path: Path) -> None:
    run_log = WorkflowRunLog("attempt-order", tmp_path / "logs")

    run_log.event(
        "child",
        "started",
        attempt=1,
        run_number=1,
        fan_out_item={"index": "0"},
    )
    run_log.event(
        "child",
        "started",
        attempt=1,
        run_number=2,
        fan_out_item={"index": "1"},
    )
    run_log.event(
        "child",
        "completed",
        attempt=1,
        run_number=1,
        fan_out_item={"index": "0"},
        success=True,
        data={"output": "first"},
    )
    run_log.event(
        "child",
        "failed",
        attempt=1,
        run_number=2,
        fan_out_item={"index": "1"},
        success=False,
        data={"output": "second", "error": "second"},
    )

    payload = json.loads(run_log.events_path.read_text())
    attempts = payload["nodes"]["child"]["attempts"]
    assert attempts[0]["fanOutItem"] == {"index": "0"}
    assert attempts[0]["output"] == "first"
    assert attempts[0]["success"] is True
    assert attempts[1]["fanOutItem"] == {"index": "1"}
    assert attempts[1]["output"] == "second"
    assert attempts[1]["success"] is False


def test_workflow_run_log_retry_event_does_not_create_attempt(tmp_path: Path) -> None:
    run_log = WorkflowRunLog("retry-attempts", tmp_path / "logs")

    run_log.event("step", "started", attempt=1, run_number=1)
    run_log.event("step", "failed", attempt=1, run_number=1, success=False)
    run_log.event("step", "retried", attempt=2, run_number=1, message="retrying")
    run_log.event("step", "started", attempt=2, run_number=1)

    payload = json.loads(run_log.events_path.read_text())
    attempts = payload["nodes"]["step"]["attempts"]
    assert [attempt["attempt"] for attempt in attempts] == [1, 2]
    assert attempts[0]["finishedAt"]
    assert "finishedAt" not in attempts[1]


def test_workflow_run_log_keeps_fan_out_child_status_aggregated(tmp_path: Path) -> None:
    run_log = WorkflowRunLog("fanout-child-status", tmp_path / "logs")

    run_log.event("child", "started", attempt=1, run_number=1, fan_out_item={"index": "0"})
    run_log.event("child", "started", attempt=1, run_number=2, fan_out_item={"index": "1"})
    run_log.event(
        "child",
        "failed",
        attempt=1,
        run_number=1,
        fan_out_item={"index": "0"},
        success=False,
    )
    run_log.event(
        "child",
        "completed",
        attempt=1,
        run_number=2,
        fan_out_item={"index": "1"},
        success=True,
    )

    payload = json.loads(run_log.events_path.read_text())
    assert payload["nodes"]["child"]["status"] == "failed"
    assert payload["nodes"]["child"]["success"] is False


async def test_agent_structured_attempts_include_rendered_inputs_and_prompt(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text("Prompt {{mapped}} {{_piped_input}}")
    sub = FakeSubscription(output="agent output")
    wf = _make_workflow("agent-input-events")
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="source",
            pipe_output=True,
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command="printf from-source",
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
                input_mapping={"mapped": "source.output"},
            ),
        )
    )
    wf.then("source", "agent-step")

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert result.success
    assert result.log_path is not None
    payload = json.loads(result.log_path.with_suffix(".events.json").read_text())
    attempt = payload["nodes"]["agent-step"]["attempts"][0]
    assert attempt["inputs"]["mapped"] == "from-source"
    assert attempt["inputs"]["_piped_input"] == "from-source"
    assert attempt["prompt"] == "from-source\n\nPrompt from-source from-source"


async def test_loop_structured_events_include_fan_out_item_counts(tmp_path: Path) -> None:
    wf = _make_workflow("fanout-events")
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=CountFanSource(type="count", count=4, max_concurrency=4),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="child",
            allow_failure=True,
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command=(
                    'if [ "$INDEX" = "1" ]; then echo "bad-$INDEX"; exit 3; '
                    'else echo "ok-$INDEX"; fi'
                ),
            ),
        )
    )
    wf.then("loop", "child")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert result.log_path is not None
    payload = json.loads(result.log_path.with_suffix(".events.json").read_text())
    loop_completed = next(
        event
        for event in payload["events"]
        if event["nodeId"] == "loop" and event["status"] == "completed"
    )
    initial_fan_out = loop_completed["data"]["fanOut"]
    assert initial_fan_out["successCount"] == 0
    assert initial_fan_out["failureCount"] == 0
    assert [item["status"] for item in initial_fan_out["items"]] == ["queued"] * 4
    fan_out = payload["nodes"]["loop"]["data"]["fanOut"]
    assert fan_out["itemCount"] == 4
    assert fan_out["successCount"] == 3
    assert fan_out["failureCount"] == 1
    failed_items = [item for item in fan_out["items"] if item["status"] == "failed"]
    assert [item["index"] for item in failed_items] == [1]
    assert failed_items[0]["exitCode"] == 3
    assert "bad-1" in failed_items[0]["output"]


@pytest.mark.anyio
async def test_resume_from_failure_reuses_successful_upstream_node(tmp_path: Path) -> None:
    calls = tmp_path / "calls.txt"
    marker = tmp_path / "marker"
    wf = _make_workflow("resume-failure")
    wf.add_operation(_bash_node("first", f"echo first >> {calls}; echo first"))
    wf.add_operation(
        _bash_node(
            "second",
            (
                f"echo second >> {calls}; "
                f"if [ ! -f {marker} ]; then touch {marker}; echo failed; exit 7; fi; "
                "echo second-ok"
            ),
        )
    )
    wf.then("first", "second")

    first = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        data_dir=tmp_path,
    ).run()
    assert not first.success
    assert first.log_path is not None

    resumed = (
        await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            data_dir=tmp_path,
        )
        .with_resume_options(ResumeOptions(run_id=first.log_path.name))
        .run()
    )

    assert resumed.success
    assert calls.read_text(encoding="utf-8").splitlines() == ["first", "second", "second"]
    assert resumed.node_outputs["first"].data["reused"] is True
    assert resumed.node_outputs["second"].output.strip() == "second-ok"


@pytest.mark.anyio
async def test_resume_checkpoint_moves_large_outputs_to_artifacts(tmp_path: Path) -> None:
    calls = tmp_path / "artifact-calls.txt"
    marker = tmp_path / "artifact-marker"
    large_output = "x" * 75_000
    large_file = tmp_path / "large.txt"
    large_file.write_text(large_output, encoding="utf-8")
    wf = _make_workflow("resume-artifacts")
    wf.add_operation(
        GraphNode(
            node_id="first",
            operation=ReadFileOperation(type=OperationType.READ_FILE, path=large_file),
        )
    )
    wf.add_operation(
        _bash_node(
            "second",
            (
                f"echo second >> {calls}; "
                f"if [ ! -f {marker} ]; then touch {marker}; echo failed; exit 7; fi; "
                "echo second-ok"
            ),
        )
    )
    wf.then("first", "second")

    first = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        data_dir=tmp_path,
    ).run()

    assert not first.success
    assert first.log_path is not None
    resume_path = first.log_path.with_suffix(".resume.json")
    resume_text = resume_path.read_text(encoding="utf-8")
    assert len(resume_text) < 20_000
    assert large_output not in resume_text
    artifacts = list((resume_path.parent / f"{resume_path.stem}.artifacts").rglob("*.json"))
    assert artifacts
    assert any(large_output in artifact.read_text(encoding="utf-8") for artifact in artifacts)

    resumed = (
        await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            data_dir=tmp_path,
        )
        .with_resume_options(ResumeOptions(run_id=first.log_path.name))
        .run()
    )

    assert resumed.success
    assert calls.read_text(encoding="utf-8").splitlines() == ["second", "second"]
    assert resumed.node_outputs["first"].data["reused"] is True
    assert resumed.node_outputs["first"].output == large_output
    assert resumed.node_outputs["second"].output.strip() == "second-ok"


@pytest.mark.anyio
async def test_global_cache_moves_large_outputs_to_artifacts(tmp_path: Path) -> None:
    large_output = "x" * 75_000
    large_file = tmp_path / "global-cache-large.txt"
    large_file.write_text(large_output, encoding="utf-8")
    wf = _make_workflow("global-cache-artifacts")
    wf.add_operation(
        GraphNode(
            node_id="read",
            operation=ReadFileOperation(type=OperationType.READ_FILE, path=large_file),
        )
    )

    first = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        data_dir=tmp_path,
    ).run()

    assert first.success
    cache_path = tmp_path / "node-cache" / wf.config.id / "cache.json"
    cache_text = cache_path.read_text(encoding="utf-8")
    assert len(cache_text) < 20_000
    assert large_output not in cache_text
    artifacts = list((cache_path.parent / f"{cache_path.stem}.artifacts").rglob("*.json"))
    assert artifacts
    assert any(large_output in artifact.read_text(encoding="utf-8") for artifact in artifacts)

    second = (
        await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            data_dir=tmp_path,
        )
        .with_resume_options(ResumeOptions(run_id="unused"))
        .run()
    )

    assert second.success
    assert second.node_outputs["read"].data["reused"] is True
    assert second.node_outputs["read"].output == large_output


@pytest.mark.anyio
async def test_resume_force_reruns_successful_upstream_node(tmp_path: Path) -> None:
    calls = tmp_path / "force-calls.txt"
    marker = tmp_path / "force-marker"
    wf = _make_workflow("resume-force")
    wf.add_operation(_bash_node("first", f"echo first >> {calls}; echo first"))
    wf.add_operation(
        _bash_node(
            "second",
            (
                f"echo second >> {calls}; "
                f"if [ ! -f {marker} ]; then touch {marker}; exit 9; fi; "
                "echo ok"
            ),
        )
    )
    wf.then("first", "second")

    first = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        data_dir=tmp_path,
    ).run()
    assert first.log_path is not None

    resumed = (
        await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            data_dir=tmp_path,
        )
        .with_resume_options(ResumeOptions(run_id=first.log_path.name, force=True))
        .run()
    )

    assert resumed.success
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "first",
        "second",
        "first",
        "second",
    ]


@pytest.mark.anyio
async def test_resume_from_node_reruns_selected_node_without_reusing_cache(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "from-node-calls.txt"
    wf = _make_workflow("resume-from-node-cache-bypass")
    wf.add_operation(_bash_node("first", f"echo first >> {calls}; echo first"))
    wf.add_operation(_bash_node("second", f"echo second >> {calls}; echo second"))
    wf.then("first", "second")

    first = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        data_dir=tmp_path,
    ).run()
    assert first.success
    assert first.log_path is not None

    resumed = (
        await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            data_dir=tmp_path,
        )
        .with_resume_options(ResumeOptions(run_id=first.log_path.name, from_node="first"))
        .run()
    )

    assert resumed.success
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "first",
        "second",
        "first",
        "second",
    ]
    assert resumed.node_outputs["first"].data.get("reused") is not True
    assert resumed.node_outputs["second"].data.get("reused") is not True


@pytest.mark.anyio
async def test_resume_only_node_reruns_selected_node_without_reusing_cache(
    tmp_path: Path,
) -> None:
    calls = tmp_path / "only-node-calls.txt"
    wf = _make_workflow("resume-only-node-cache-bypass")
    wf.add_operation(_bash_node("first", f"echo first >> {calls}; echo first"))
    wf.add_operation(_bash_node("second", f"echo second >> {calls}; echo second"))
    wf.add_operation(_bash_node("third", f"echo third >> {calls}; echo third"))
    wf.then("first", "second")
    wf.then("second", "third")

    first = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        data_dir=tmp_path,
    ).run()
    assert first.success
    assert first.log_path is not None

    resumed = (
        await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            data_dir=tmp_path,
        )
        .with_resume_options(ResumeOptions(run_id=first.log_path.name, only_node="second"))
        .run()
    )

    assert resumed.success
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "first",
        "second",
        "third",
        "second",
    ]
    assert resumed.node_outputs["first"].data["reused"] is True
    assert resumed.node_outputs["second"].data.get("reused") is not True
    assert resumed.node_outputs["third"].data["reused"] is True


@pytest.mark.anyio
async def test_resume_changed_file_input_invalidates_cached_output(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("old", encoding="utf-8")
    marker = tmp_path / "changed-marker"
    wf = _make_workflow("resume-cache-invalidates")
    wf.add_operation(
        GraphNode(
            node_id="read",
            operation=ReadFileOperation(type=OperationType.READ_FILE, path=source),
        )
    )
    wf.add_operation(
        _bash_node(
            "fail-once",
            f"if [ ! -f {marker} ]; then touch {marker}; exit 1; fi; echo ok",
        )
    )
    wf.then("read", "fail-once")

    first = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        data_dir=tmp_path,
    ).run()
    assert first.log_path is not None
    source.write_text("new", encoding="utf-8")

    resumed = (
        await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            data_dir=tmp_path,
        )
        .with_resume_options(ResumeOptions(run_id=first.log_path.name))
        .run()
    )

    assert resumed.success
    assert resumed.node_outputs["read"].output == "new"
    assert resumed.node_outputs["read"].data.get("reused") is not True


@pytest.mark.anyio
async def test_resume_replays_conditional_edges_from_reused_state(tmp_path: Path) -> None:
    marker = tmp_path / "conditional-marker"
    wf = _make_workflow("resume-conditional")
    wf.add_operation(_bash_node("decide", "echo go"))
    wf.add_operation(
        _bash_node(
            "matched",
            f"if [ ! -f {marker} ]; then touch {marker}; exit 1; fi; echo matched",
        )
    )
    wf.add_operation(_bash_node("unmatched", "echo unmatched"))
    wf.then(
        "decide",
        "matched",
        EdgeConfig(
            from_node="decide",
            to_node="matched",
            condition=EdgeConditionType.OUTPUT_MATCHES,
            output_pattern="go",
        ),
    )
    wf.then(
        "decide",
        "unmatched",
        EdgeConfig(
            from_node="decide",
            to_node="unmatched",
            condition=EdgeConditionType.OUTPUT_MATCHES,
            output_pattern="stop",
        ),
    )

    first = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        data_dir=tmp_path,
    ).run()
    assert first.log_path is not None

    resumed = (
        await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            data_dir=tmp_path,
        )
        .with_resume_options(ResumeOptions(run_id=first.log_path.name))
        .run()
    )

    assert resumed.success
    assert resumed.node_outputs["decide"].data["reused"] is True
    assert "matched" in resumed.node_outputs
    assert "unmatched" not in resumed.node_outputs


@pytest.mark.anyio
async def test_resume_after_stop_continues_from_checkpoint(tmp_path: Path) -> None:
    calls = tmp_path / "stop-calls.txt"
    stop_file = tmp_path / "state" / "stop"
    stop_file.parent.mkdir()
    wf = _make_workflow("resume-stop")
    wf.add_operation(_bash_node("first", f"echo first >> {calls}; touch {stop_file}; echo first"))
    wf.add_operation(_bash_node("second", f"echo second >> {calls}; echo second"))
    wf.then("first", "second")

    first = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        data_dir=tmp_path,
        stop_file=stop_file,
    ).run()
    assert not first.success
    assert first.log_path is not None

    resumed = (
        await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            data_dir=tmp_path,
        )
        .with_resume_options(ResumeOptions(run_id=first.log_path.name))
        .run()
    )

    assert resumed.success
    assert calls.read_text(encoding="utf-8").splitlines() == ["first", "second"]


@pytest.mark.anyio
async def test_resume_loop_retries_only_failed_fan_out_items(tmp_path: Path) -> None:
    calls = tmp_path / "fanout-calls.txt"
    marker = tmp_path / "fanout-marker"
    wf = _make_workflow("resume-fanout")
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=CountFanSource(type="count", count=2, max_concurrency=2),
            ),
        )
    )
    wf.add_operation(
        _bash_node(
            "child",
            (
                f"echo $INDEX >> {calls}; "
                f'if [ "$INDEX" = "1" ] && [ ! -f {marker} ]; '
                f"then touch {marker}; exit 1; fi; "
                "echo ok-$INDEX"
            ),
        )
    )
    wf.then("loop", "child")

    first = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        data_dir=tmp_path,
    ).run()
    assert not first.success
    assert first.log_path is not None

    resumed = (
        await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            data_dir=tmp_path,
        )
        .with_resume_options(ResumeOptions(run_id=first.log_path.name))
        .run()
    )

    assert resumed.success
    assert calls.read_text(encoding="utf-8").splitlines() == ["0", "1", "1"]


@pytest.mark.anyio
async def test_resume_loop_retries_failed_fan_out_agent_item_with_fake_subscription(
    tmp_path: Path,
) -> None:
    class FailsSecondCallOnceSubscription(FakeSubscription):
        def __init__(self) -> None:
            super().__init__(output="agent-ok")
            self.failed = False

        async def execute(
            self,
            prompt: str,
            working_dir: Path,
            tools: list[str],
            mcp_servers: list[str],
            env: dict[str, str],
            timeout: float | None = None,
            cancel_event: threading.Event | None = None,
            extra_paths: list[Path] | None = None,
            max_output_bytes: int | None = None,
            on_thought: Callable[[str], None] | None = None,
            provider_settings: ResolvedProviderSettings | None = None,
        ) -> AgentResult:
            self.calls.append(
                {
                    "prompt": prompt,
                    "working_dir": working_dir,
                    "tools": tools,
                    "mcp_servers": mcp_servers,
                    "env": env,
                    "extra_paths": extra_paths or [],
                    "max_output_bytes": max_output_bytes,
                    "provider_settings": provider_settings,
                }
            )
            if len(self.calls) == 2 and not self.failed:
                self.failed = True
                return AgentResult(
                    agent_id="",
                    success=False,
                    output="agent-failed",
                    exit_code=5,
                    duration_seconds=0.0,
                )
            return AgentResult(
                agent_id="",
                success=True,
                output=f"agent-ok-{len(self.calls)}",
                exit_code=0,
                duration_seconds=0.0,
            )

    wf = _make_workflow("resume-fanout-agent")
    wf.register_agent(
        AgentConfig(
            agent_id="agent",
            subscription="claude_code",
            working_dir=tmp_path,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=CountFanSource(type="count", count=2, max_concurrency=1),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-child",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="agent",
                working_dir=tmp_path,
                skill_name="summarize",
            ),
        )
    )
    wf.then("loop", "agent-child")
    sub = FailsSecondCallOnceSubscription()

    first = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
        data_dir=tmp_path,
    ).run()
    assert not first.success
    assert first.log_path is not None

    resumed = (
        await WorkflowExecutor(
            wf,
            {"claude_code": sub},
            log_base_dir=tmp_path / "logs",
            data_dir=tmp_path,
        )
        .with_resume_options(ResumeOptions(run_id=first.log_path.name))
        .run()
    )

    assert resumed.success
    assert len(sub.calls) == 3


async def test_start_node_routes_to_next_node(tmp_path: Path) -> None:
    wf = _make_workflow("start-node")
    wf.add_operation(
        GraphNode(
            node_id="start",
            operation=StartOperation(type=OperationType.START),
        )
    )
    wf.add_operation(_bash_node("next", "echo next"))
    wf.then("start", "next")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert result.node_outputs["start"].success
    assert result.node_outputs["next"].success


async def test_pass_node_stops_workflow_successfully(tmp_path: Path) -> None:
    wf = _make_workflow("pass-node")
    wf.add_operation(_bash_node("before", "echo before"))
    wf.add_operation(
        GraphNode(
            node_id="done",
            operation=PassOperation(type=OperationType.PASS, message="all good"),
        )
    )
    wf.add_operation(_bash_node("after", "echo after"))
    wf.then("before", "done")
    wf.then("done", "after")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert result.node_outputs["done"].terminal_status == "pass"
    assert "after" not in result.node_outputs
    assert "all good" in result.log_path.read_text()


async def test_fail_node_stops_workflow_with_error(tmp_path: Path) -> None:
    wf = _make_workflow("fail-node")
    wf.add_operation(_bash_node("before", "echo before"))
    wf.add_operation(
        GraphNode(
            node_id="fail",
            operation=FailOperation(type=OperationType.FAIL, message="not good"),
        )
    )
    wf.add_operation(_bash_node("after", "echo after"))
    wf.then("before", "fail")
    wf.then("fail", "after")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert result.node_outputs["fail"].terminal_status == "fail"
    assert "after" not in result.node_outputs
    log_text = result.log_path.read_text()
    assert "not good" in log_text
    assert "failed due to not good" in log_text


async def test_await_all_inputs_waits_for_each_upstream_node(tmp_path: Path) -> None:
    wf = _make_workflow("await-all")
    wf.add_operation(_bash_node("left", "echo left"))
    wf.add_operation(_bash_node("right", "echo right"))
    wf.add_operation(_bash_node("merge", "echo merge"))
    wf.then("left", "merge")
    wf.then("right", "merge")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert len(result.node_runs["merge"]) == 1
    log_text = result.log_path.read_text()
    assert log_text.index("NODE - right - attempt 1 finished") < log_text.index(
        "NODE - merge - attempt 1 started"
    )


async def test_node_can_run_without_awaiting_all_inputs(tmp_path: Path) -> None:
    wf = _make_workflow("no-await-all")
    wf.add_operation(
        GraphNode(
            node_id="start",
            operation=StartOperation(type=OperationType.START),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="loop-entry",
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command="echo loop",
            ),
            await_all_inputs=False,
        )
    )
    wf.add_operation(_bash_node("later", "echo later"))
    wf.add_operation(
        GraphNode(
            node_id="done",
            operation=PassOperation(type=OperationType.PASS, message="done"),
        )
    )
    wf.then("start", "loop-entry")
    wf.then("loop-entry", "done")
    wf.then("loop-entry", "later")
    wf.then("later", "loop-entry")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert len(result.node_runs["loop-entry"]) == 1
    assert "later" not in result.node_outputs


async def test_successor_nodes_from_same_parent_run_concurrently(tmp_path: Path) -> None:
    wf = _make_workflow("parallel-successors")
    wf.add_operation(
        GraphNode(
            node_id="start",
            operation=StartOperation(type=OperationType.START),
        )
    )
    sleep_command = shlex.join(
        [sys.executable, "-c", "import time; time.sleep(0.4); print('done')"]
    )
    for node_id in ["b", "c", "d"]:
        wf.add_operation(_bash_node(node_id, sleep_command))
        wf.then("start", node_id)

    started = time.monotonic()
    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()
    elapsed = time.monotonic() - started

    assert result.success
    assert {"start", "b", "c", "d"} <= set(result.node_outputs)
    assert elapsed < 0.95


async def test_bash_successor_nodes_from_same_parent_overlap(tmp_path: Path) -> None:
    stamp_file = tmp_path / "starts.txt"
    wf = _make_workflow("parallel-bash-successors")
    wf.add_operation(_bash_node("a", "echo ready"))
    for node_id in ["b", "c", "d"]:
        command = (
            f"{shlex.quote(sys.executable)} -c "
            f'"import pathlib,time; '
            f"p=pathlib.Path({str(stamp_file)!r}); "
            f"p.open('a').write('{node_id}:' + str(time.monotonic()) + '\\n'); "
            f"time.sleep(0.4); "
            f"print('{node_id} done')\""
        )
        wf.add_operation(_bash_node(node_id, command))
        wf.then("a", node_id)

    started = time.monotonic()
    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()
    elapsed = time.monotonic() - started

    assert result.success
    stamps = [float(line.split(":", 1)[1]) for line in stamp_file.read_text().splitlines()]
    assert len(stamps) == 3
    assert max(stamps) - min(stamps) < 0.25
    assert elapsed < 0.95


async def test_agent_successor_nodes_from_same_parent_run_concurrently(tmp_path: Path) -> None:
    class DelayedSubscription(FakeSubscription):
        async def execute(self, *args, **kwargs):
            self.calls.append(
                {
                    "prompt": kwargs.get("prompt", ""),
                    "started_at": time.monotonic(),
                }
            )
            await anyio.sleep(0.4)
            return AgentResult(
                agent_id="",
                success=True,
                output="done",
                exit_code=0,
                duration_seconds=0.4,
            )

    wf = _make_workflow("parallel-agent-successors")
    wf.add_operation(
        GraphNode(
            node_id="a",
            operation=StartOperation(type=OperationType.START),
        )
    )
    sub = DelayedSubscription()
    for node_id in ["b", "c", "d"]:
        prompt = tmp_path / f"{node_id}.md"
        prompt.write_text(f"{node_id} prompt")
        wf.register_agent(
            AgentConfig(
                agent_id=node_id,
                subscription="claude_code",
                working_dir=tmp_path,
                prompt_path=prompt,
            )
        )
        wf.add_operation(
            GraphNode(
                node_id=node_id,
                operation=AgentOperation(
                    type=OperationType.AGENT,
                    agent_id=node_id,
                    prompt_path=prompt,
                    working_dir=tmp_path,
                ),
            )
        )
        wf.then("a", node_id)

    started = time.monotonic()
    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()
    elapsed = time.monotonic() - started

    assert result.success
    start_times = [float(call["started_at"]) for call in sub.calls]
    assert len(start_times) == 3
    assert max(start_times) - min(start_times) < 0.25
    assert elapsed < 0.95


async def test_stop_marker_interrupts_running_workflow(tmp_path: Path) -> None:
    wf = _make_workflow("stop-marker")
    wf.add_operation(_bash_node("sleep", "sleep 5"))
    stop_file = workflow_stop_path("stop-marker", tmp_path)
    run_result = None

    async def run_workflow() -> None:
        nonlocal run_result
        run_result = await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            stop_file=stop_file,
        ).run()

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_workflow)
        await anyio.sleep(0.2)
        request_workflow_stop("stop-marker", tmp_path)

    assert run_result is not None
    assert not run_result.success
    assert "stopped by user" in run_result.log_path.read_text()
    payload = json.loads(run_result.log_path.with_suffix(".events.json").read_text())
    workflow_statuses = [
        event["status"] for event in payload["events"] if event["nodeId"] == "workflow"
    ]
    assert workflow_statuses[-1] == "stopped"
    assert "failed" not in workflow_statuses


async def test_stop_marker_marks_unstarted_successors_stopped(tmp_path: Path) -> None:
    wf = _make_workflow("stop-marker-unstarted")
    wf.add_operation(_bash_node("sleep", "sleep 5"))
    wf.add_operation(_bash_node("after", "echo after"))
    wf.then("sleep", "after")
    stop_file = workflow_stop_path("stop-marker-unstarted", tmp_path)
    run_result = None

    async def run_workflow() -> None:
        nonlocal run_result
        run_result = await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            stop_file=stop_file,
        ).run()

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_workflow)
        await anyio.sleep(0.2)
        request_workflow_stop("stop-marker-unstarted", tmp_path)

    assert run_result is not None
    assert not run_result.success
    assert run_result.log_path is not None
    payload = json.loads(run_result.log_path.with_suffix(".events.json").read_text())
    assert payload["nodes"]["after"]["status"] == "stopped"
    assert payload["nodes"]["after"]["skipped"] is False
    assert payload["nodes"]["after"]["data"]["stopReason"] == (
        "stopped by user before node started"
    )
    assert "skipReason" not in payload["nodes"]["after"]["data"]


async def test_run_stop_marker_interrupts_specific_running_workflow(tmp_path: Path) -> None:
    wf = _make_workflow("run-stop-marker")
    wf.add_operation(_bash_node("sleep", "sleep 5"))
    stop_file = workflow_stop_path("run-stop-marker", tmp_path)
    run_result = None

    async def run_workflow() -> None:
        nonlocal run_result
        run_result = await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            stop_file=stop_file,
        ).run()

    async with anyio.create_task_group() as tg:
        tg.start_soon(run_workflow)
        for _ in range(40):
            await anyio.sleep(0.05)
            log_files = list((tmp_path / "logs" / "run-stop-marker").glob("*.log"))
            if log_files:
                request_workflow_run_stop(
                    "run-stop-marker",
                    log_files[0].name,
                    tmp_path,
                )
                break
        else:  # pragma: no cover
            raise AssertionError("Run log was not created")

    assert run_result is not None
    assert not run_result.success
    assert "stopped by user" in run_result.log_path.read_text()


async def test_linear_execution_order(tmp_path: Path) -> None:
    wf = _make_workflow()
    wf.add_operation(_bash_node("a", "true"))
    wf.add_operation(_bash_node("b", "true"))
    wf.then("a", "b")

    executor = WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs")
    result = await executor.run()
    assert result.success
    assert set(result.node_outputs) == {"a", "b"}


async def test_failure_halts_workflow(tmp_path: Path) -> None:
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="fail",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="false"),
            on_failure="halt",
        )
    )
    wf.add_operation(_bash_node("after"))
    wf.then("fail", "after")

    executor = WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs")
    result = await executor.run()
    assert not result.success
    assert "after" not in result.node_outputs


async def test_failure_skip_continues(tmp_path: Path) -> None:
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="fail",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="false"),
            on_failure="skip",
        )
    )
    wf.add_operation(_bash_node("after"))
    wf.then("fail", "after")

    executor = WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs")
    result = await executor.run()
    assert "after" in result.node_outputs


async def test_failed_bash_command_routes_to_on_failure_edge(tmp_path: Path) -> None:
    wf = _make_workflow()
    wf.add_operation(_bash_node("fail", "1/0"))
    wf.add_operation(_bash_node("recover", "echo recovered"))
    wf.then(
        "fail",
        "recover",
        EdgeConfig(
            from_node="fail",
            to_node="recover",
            condition=EdgeConditionType.ON_FAILURE,
        ),
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert not result.node_outputs["fail"].success
    assert result.node_outputs["recover"].success
    assert "recovered" in result.node_outputs["recover"].output


async def test_allowed_failure_routes_to_on_failure_edge_without_failing_workflow(
    tmp_path: Path,
) -> None:
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="fail",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="1/0"),
            allow_failure=True,
        )
    )
    wf.add_operation(_bash_node("recover", "echo recovered"))
    wf.then(
        "fail",
        "recover",
        EdgeConfig(
            from_node="fail",
            to_node="recover",
            condition=EdgeConditionType.ON_FAILURE,
        ),
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert not result.node_outputs["fail"].success
    assert result.node_outputs["recover"].success
    assert "recovered" in result.node_outputs["recover"].output


async def test_allowed_failure_without_failure_route_does_not_fail_workflow(
    tmp_path: Path,
) -> None:
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="fail",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="exit 7"),
            allow_failure=True,
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert not result.node_outputs["fail"].success


async def test_uncaught_python_exception_routes_to_on_failure_edge(tmp_path: Path) -> None:
    script = tmp_path / "explode.py"
    script.write_text("1 / 0\n")

    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="explode",
            operation=PythonScriptOperation(
                type=OperationType.PYTHON_SCRIPT,
                script_path=script,
            ),
        )
    )
    wf.add_operation(_bash_node("recover", "echo python recovered"))
    wf.then(
        "explode",
        "recover",
        EdgeConfig(
            from_node="explode",
            to_node="recover",
            condition=EdgeConditionType.ON_FAILURE,
        ),
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert not result.node_outputs["explode"].success
    assert "ZeroDivisionError" in result.node_outputs["explode"].output
    assert result.node_outputs["recover"].success
    assert "python recovered" in result.node_outputs["recover"].output


async def test_read_file_outputs_file_content(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("hello from a file")
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="read",
            operation=ReadFileOperation(type=OperationType.READ_FILE, path=source),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert result.node_outputs["read"].output == "hello from a file"
    assert result.node_outputs["read"].data["file_name"] == "input.txt"
    assert result.node_outputs["read"].data["file_stem"] == "input"
    assert result.node_outputs["read"].data["file_extension"] == ".txt"
    assert result.node_outputs["read"].data["directory"] == str(tmp_path)


async def test_write_file_uses_piped_input_when_content_empty(tmp_path: Path) -> None:
    destination = tmp_path / "out" / "result.txt"
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="produce",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="printf piped"),
            pipe_output=True,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="write",
            operation=WriteFileOperation(type=OperationType.WRITE_FILE, path=destination),
        )
    )
    wf.then("produce", "write")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert destination.read_text() == "piped"
    assert "wrote 5 characters" in result.node_outputs["write"].output


async def test_file_node_requires_access_outside_workflow_project(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    external_dir = tmp_path / "external"
    project_dir.mkdir()
    external_dir.mkdir()
    source = external_dir / "input.txt"
    source.write_text("outside")
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="read",
            operation=ReadFileOperation(type=OperationType.READ_FILE, path=source),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        workflow_path=project_dir / "flow.toml",
    ).run()

    assert not result.success
    assert not result.node_outputs["read"].success
    assert "filesystem access denied for read" in result.node_outputs["read"].output


async def test_file_node_allows_configured_read_access_outside_workflow_project(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    external_dir = tmp_path / "external"
    project_dir.mkdir()
    external_dir.mkdir()
    source = external_dir / "input.txt"
    source.write_text("outside")
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="read-access",
            name="Read Access",
            filesystem_access=[FilesystemAccessEntry(path=external_dir, read=True, write=False)],
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="read",
            operation=ReadFileOperation(type=OperationType.READ_FILE, path=source),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        workflow_path=project_dir / "flow.toml",
    ).run()

    assert result.success
    assert result.node_outputs["read"].output == "outside"


async def test_write_file_requires_write_access_outside_workflow_project(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    external_dir = tmp_path / "external"
    project_dir.mkdir()
    external_dir.mkdir()
    destination = external_dir / "out.txt"
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="write-access",
            name="Write Access",
            filesystem_access=[FilesystemAccessEntry(path=external_dir, read=True, write=False)],
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="write",
            operation=WriteFileOperation(
                type=OperationType.WRITE_FILE,
                path=destination,
                content="blocked",
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        workflow_path=project_dir / "flow.toml",
    ).run()

    assert not result.success
    assert not destination.exists()
    assert "filesystem access denied for write" in result.node_outputs["write"].output


async def test_write_file_allows_configured_write_access_outside_workflow_project(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    external_dir = tmp_path / "external"
    project_dir.mkdir()
    external_dir.mkdir()
    destination = external_dir / "out.txt"
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="write-access",
            name="Write Access",
            filesystem_access=[FilesystemAccessEntry(path=external_dir, read=False, write=True)],
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="write",
            operation=WriteFileOperation(
                type=OperationType.WRITE_FILE,
                path=destination,
                content="allowed",
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        workflow_path=project_dir / "flow.toml",
    ).run()

    assert result.success
    assert destination.read_text() == "allowed"


async def test_project_root_access_entry_can_restrict_implicit_project_access(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    destination = project_dir / "out.txt"
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="project-access",
            name="Project Access",
            filesystem_access=[FilesystemAccessEntry(path=project_dir, read=True, write=False)],
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="write",
            operation=WriteFileOperation(
                type=OperationType.WRITE_FILE,
                path=destination,
                content="blocked",
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        workflow_path=project_dir / "flow.toml",
    ).run()

    assert not result.success
    assert not destination.exists()
    assert "filesystem access denied for write" in result.node_outputs["write"].output


async def test_relative_workflow_paths_match_plan_and_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_dir = tmp_path / "stored"
    workflow_dir.mkdir()
    (workflow_dir / "inputs").mkdir()
    (workflow_dir / "inputs" / "one.txt").write_text("from workflow dir")
    (workflow_dir / "rows.csv").write_text("name\nworkflow-row\n")
    (workflow_dir / "scripts").mkdir()
    (workflow_dir / "scripts" / "job.py").write_text("print('script ok')\n")

    caller_dir = tmp_path / "caller"
    caller_dir.mkdir()
    (caller_dir / "inputs").mkdir()
    (caller_dir / "inputs" / "one.txt").write_text("from caller cwd")
    (caller_dir / "rows.csv").write_text("name\ncaller-row\n")
    (caller_dir / "scripts").mkdir()
    (caller_dir / "scripts" / "job.py").write_text("raise SystemExit(99)\n")

    workflow_path = workflow_dir / "relative.toml"
    workflow_path.write_text(
        """
[workflow]
id = "relative-runtime"
name = "Relative Runtime"

[[nodes]]
id = "script"
type = "python_script"
script_path = "scripts/job.py"

[[nodes]]
id = "read"
type = "read_file"
path = "inputs/one.txt"

[[nodes]]
id = "write"
type = "write_file"
path = "outputs/out.txt"
content = "created"

[[nodes]]
id = "directory"
type = "loop"

[nodes.source]
type = "directory"
path = "inputs"
glob = "*.txt"

[[nodes]]
id = "tabular"
type = "loop"

[nodes.source]
type = "tabular"
path = "rows.csv"
""".strip()
    )

    monkeypatch.chdir(caller_dir)
    workflow = AgenticWorkflow.from_file(workflow_path)
    plan = build_execution_plan(workflow, workflow_path=workflow_path)
    plan_nodes: dict[str, dict[str, Any]] = {
        node["id"]: node for generation in plan["generations"] for node in generation["nodes"]
    }

    result = await WorkflowExecutor(
        workflow,
        {},
        log_base_dir=tmp_path / "logs",
        workflow_path=workflow_path,
    ).run()

    assert result.success
    assert plan_nodes["read"]["detail"] == str(workflow_dir / "inputs" / "one.txt")
    assert result.node_outputs["read"].output == "from workflow dir"
    assert result.node_outputs["read"].data["path"] == plan_nodes["read"]["detail"]

    assert plan_nodes["write"]["detail"] == f"write {workflow_dir / 'outputs/out.txt'}"
    assert (workflow_dir / "outputs" / "out.txt").read_text() == "created"
    assert not (caller_dir / "outputs" / "out.txt").exists()
    assert result.node_outputs["write"].data["path"] == str(workflow_dir / "outputs" / "out.txt")

    assert (
        f"python script: {workflow_dir / 'scripts/job.py'}" in plan_nodes["script"]["sideEffects"]
    )
    assert result.node_outputs["script"].data["script_path"] == str(
        workflow_dir / "scripts" / "job.py"
    )

    directory_plan = cast(dict[str, Any], plan_nodes["directory"]["fanOut"])
    assert directory_plan["path"] == str(workflow_dir / "inputs")
    assert directory_plan["sampleItems"][0]["path"] == str(workflow_dir / "inputs" / "one.txt")
    directory_item = cast(dict[str, Any], result.node_outputs["directory"].items[0])
    assert result.node_outputs["directory"].data["source_path"] == directory_plan["path"]
    assert directory_item["path"] == directory_plan["sampleItems"][0]["path"]

    tabular_plan = cast(dict[str, Any], plan_nodes["tabular"]["fanOut"])
    assert tabular_plan["path"] == str(workflow_dir / "rows.csv")
    assert tabular_plan["sampleItems"][0]["name"] == "workflow-row"
    tabular_item = cast(dict[str, Any], result.node_outputs["tabular"].items[0])
    assert result.node_outputs["tabular"].data["source_path"] == tabular_plan["path"]
    assert tabular_item["name"] == "workflow-row"


async def test_node_inputs_can_map_parent_contract_to_stdin(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("contract text")

    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="read",
            operation=ReadFileOperation(type=OperationType.READ_FILE, path=source),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="print",
            inputs={"stdin": "read.data.content"},
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="cat"),
        )
    )
    wf.then("read", "print")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert result.node_outputs["print"].output == "contract text"


async def test_node_inputs_can_map_parent_contract_to_env(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("env text")

    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="read",
            operation=ReadFileOperation(type=OperationType.READ_FILE, path=source),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="print",
            inputs={"env.CONTENT": "read.text"},
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command='printf "%s" "$CONTENT"',
            ),
        )
    )
    wf.then("read", "print")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert result.node_outputs["print"].output == "env text"


async def test_node_inputs_allow_literal_env_values(tmp_path: Path) -> None:
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="print",
            inputs={"env.MESSAGE": "literal text"},
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command='printf "%s" "$MESSAGE"',
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert result.node_outputs["print"].output == "literal text"


async def test_node_inputs_can_use_previous_text_alias(tmp_path: Path) -> None:
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="first",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="echo hello"),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="second",
            inputs={"stdin": "previous.text"},
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="cat"),
        )
    )
    wf.then("first", "second")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert result.node_outputs["second"].output.strip() == "hello"


async def test_copy_move_and_delete_file_nodes(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    copied = tmp_path / "copied.txt"
    moved = tmp_path / "moved.txt"
    source.write_text("contents")
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="copy",
            operation=CopyFileOperation(
                type=OperationType.COPY_FILE,
                source_path=source,
                destination_path=copied,
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="move",
            operation=MoveFileOperation(
                type=OperationType.MOVE_FILE,
                source_path=copied,
                destination_path=moved,
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="delete",
            operation=DeleteFileOperation(
                type=OperationType.DELETE_FILE,
                path=moved,
                use_trash=False,
            ),
        )
    )
    wf.then("copy", "move")
    wf.then("move", "delete")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert source.read_text() == "contents"
    assert not copied.exists()
    assert not moved.exists()


async def test_copy_file_destination_exists_without_overwrite_fails(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("new")
    destination.write_text("old")
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="copy",
            operation=CopyFileOperation(
                type=OperationType.COPY_FILE,
                source_path=source,
                destination_path=destination,
                overwrite=False,
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert not result.node_outputs["copy"].success
    assert result.node_outputs["copy"].exit_code == 1
    assert "already exists" in result.node_outputs["copy"].output
    assert destination.read_text() == "old"


async def test_move_file_destination_exists_without_overwrite_fails(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "destination.txt"
    source.write_text("new")
    destination.write_text("old")
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="move",
            operation=MoveFileOperation(
                type=OperationType.MOVE_FILE,
                source_path=source,
                destination_path=destination,
                overwrite=False,
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert not result.node_outputs["move"].success
    assert "already exists" in result.node_outputs["move"].output
    assert source.read_text() == "new"
    assert destination.read_text() == "old"


async def test_delete_file_missing_ok_controls_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    ok_wf = _make_workflow("delete-missing-ok")
    ok_wf.add_operation(
        GraphNode(
            node_id="delete",
            operation=DeleteFileOperation(
                type=OperationType.DELETE_FILE,
                path=missing,
                missing_ok=True,
            ),
        )
    )

    ok_result = await WorkflowExecutor(ok_wf, {}, log_base_dir=tmp_path / "logs").run()

    assert ok_result.success
    assert ok_result.node_outputs["delete"].data == {
        "path": str(missing),
        "missing": True,
    }

    fail_wf = _make_workflow("delete-missing-fail")
    fail_wf.add_operation(
        GraphNode(
            node_id="delete",
            operation=DeleteFileOperation(
                type=OperationType.DELETE_FILE,
                path=missing,
                missing_ok=False,
            ),
        )
    )

    fail_result = await WorkflowExecutor(fail_wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not fail_result.success
    assert not fail_result.node_outputs["delete"].success
    assert str(missing) in fail_result.node_outputs["delete"].output


async def test_delete_directory_requires_recursive_without_trash(tmp_path: Path) -> None:
    target = tmp_path / "folder"
    target.mkdir()
    (target / "file.txt").write_text("content")
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="delete",
            operation=DeleteFileOperation(
                type=OperationType.DELETE_FILE,
                path=target,
                use_trash=False,
                recursive=False,
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert target.exists()
    assert "enable recursive delete" in result.node_outputs["delete"].output


async def test_file_and_folder_nodes_output_paths(tmp_path: Path) -> None:
    source = tmp_path / "input.txt"
    folder = tmp_path / "docs"
    source.write_text("hello", encoding="utf-8")
    folder.mkdir()
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="file",
            operation=FileOperation(type=OperationType.FILE, path=source),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="folder",
            operation=FolderOperation(type=OperationType.FOLDER, path=folder),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert result.node_outputs["file"].output == str(source)
    assert result.node_outputs["folder"].output == str(folder)


async def test_delete_file_uses_gofer_trash_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    target = tmp_path / "delete-me.txt"
    target.write_text("trash me")
    monkeypatch.setattr("gofer.core.executor.get_data_dir", lambda: data_dir)
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="trash",
            operation=DeleteFileOperation(type=OperationType.DELETE_FILE, path=target),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert not target.exists()
    trashed = list((data_dir / "trash").iterdir())
    assert len(trashed) == 1
    assert trashed[0].read_text() == "trash me"


async def test_open_resource_url_success_and_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []

    def fake_open(target: str) -> bool:
        opened.append(target)
        return True

    executor_any = cast(Any, executor_module)
    monkeypatch.setattr(executor_any.webbrowser, "open", fake_open)
    wf = _make_workflow("open-url")
    wf.add_operation(
        GraphNode(
            node_id="open",
            operation=OpenResourceOperation(
                type=OperationType.OPEN_RESOURCE,
                target="https://example.com",
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert opened == ["https://example.com"]
    assert result.node_outputs["open"].data == {
        "target": "https://example.com",
        "resource_type": "auto",
    }

    def fake_open_failure(_target: str) -> bool:
        return False

    monkeypatch.setattr(executor_any.webbrowser, "open", fake_open_failure)
    fail_wf = _make_workflow("open-url-fail")
    fail_wf.add_operation(
        GraphNode(
            node_id="open",
            operation=OpenResourceOperation(
                type=OperationType.OPEN_RESOURCE,
                target="https://example.com/fail",
                resource_type="url",
            ),
        )
    )

    fail_result = await WorkflowExecutor(fail_wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not fail_result.success
    assert "Could not open URL" in fail_result.node_outputs["open"].output


async def test_open_resource_uses_platform_command_and_reports_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_subprocess(
        cmd: list[str],
        **kwargs: object,
    ) -> tuple[int, str, str]:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return 7, "", "no opener"

    executor_any = cast(Any, executor_module)
    monkeypatch.setattr(executor_any.sys, "platform", "linux")
    monkeypatch.setattr(executor_module, "run_subprocess", fake_run_subprocess)
    wf = _make_workflow("open-file-fail")
    wf.add_operation(
        GraphNode(
            node_id="open",
            operation=OpenResourceOperation(
                type=OperationType.OPEN_RESOURCE,
                target=str(tmp_path / "report.txt"),
                resource_type="file",
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert captured["cmd"] == ["xdg-open", str(tmp_path / "report.txt")]
    captured_kwargs = cast(dict[str, object], captured["kwargs"])
    assert captured_kwargs["timeout"] is None
    assert result.node_outputs["open"].exit_code == 7
    assert result.node_outputs["open"].error == "no opener"


async def test_open_resource_app_passes_args_to_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_subprocess(
        cmd: list[str],
        **kwargs: object,
    ) -> tuple[int, str, str]:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return 0, "opened", ""

    monkeypatch.setattr(executor_module, "run_subprocess", fake_run_subprocess)
    wf = _make_workflow("open-app")
    wf.add_operation(
        GraphNode(
            node_id="open",
            operation=OpenResourceOperation(
                type=OperationType.OPEN_RESOURCE,
                target="viewer",
                resource_type="app",
                args=["--safe", "report.txt"],
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert captured["cmd"] == ["viewer", "--safe", "report.txt"]
    assert result.node_outputs["open"].output == "opened viewer"


async def test_open_resource_windows_uses_startfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    executor_any = cast(Any, executor_module)
    monkeypatch.setattr(executor_any.sys, "platform", "win32")
    monkeypatch.setattr(
        executor_any.os,
        "startfile",
        lambda target: opened.append(target),
        raising=False,
    )
    wf = _make_workflow("open-windows")
    wf.add_operation(
        GraphNode(
            node_id="open",
            operation=OpenResourceOperation(
                type=OperationType.OPEN_RESOURCE,
                target=str(tmp_path / "report.txt"),
                resource_type="file",
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert opened == [str(tmp_path / "report.txt")]


async def test_prompt_file_node_renders_template_variables(tmp_path: Path) -> None:
    output = tmp_path / "prompts" / "generated.md"
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="make-prompt",
            operation=PromptFileOperation(
                type=OperationType.PROMPT_FILE,
                output_path=output,
                template="Summarize {{topic}}",
                variables={"topic": "gofer flow"},
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert output.read_text() == "Summarize gofer flow"


async def test_prompt_file_resolves_template_and_output_paths(tmp_path: Path) -> None:
    template = tmp_path / "template.md"
    template.write_text("Hello {{name}}", encoding="utf-8")
    output = tmp_path / "rendered.md"
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="prompt-path-inputs",
            name="Prompt Paths",
            inputs={
                "template": WorkflowParameterConfig(type="path", required=True),
                "output": WorkflowParameterConfig(type="path", required=True),
            },
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="render",
            operation=PromptFileOperation(
                type=OperationType.PROMPT_FILE,
                template_path=Path("{{inputs.template}}"),
                output_path=Path("{{inputs.output}}"),
                variables={"name": "Gofer"},
            ),
        )
    )

    result = await (
        WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs")
        .with_parameters({"template": template, "output": output})
        .run()
    )

    assert result.success
    assert output.read_text(encoding="utf-8") == "Hello Gofer"


async def test_prompt_file_falls_back_for_unresolved_variables_and_piped_input(
    tmp_path: Path,
) -> None:
    output = tmp_path / "prompts" / "generated.md"
    source = tmp_path / "source.txt"
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="source",
            pipe_output=True,
            operation=WriteFileOperation(
                type=OperationType.WRITE_FILE,
                path=source,
                content="piped text",
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="make-prompt",
            operation=PromptFileOperation(
                type=OperationType.PROMPT_FILE,
                output_path=output,
                template="Input={{_piped_input}} Missing={{topic}} Literal={{literal}}",
                variables={
                    "topic": "missing.node.output",
                    "literal": "plain value",
                },
            ),
        )
    )
    wf.then("source", "make-prompt")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert output.read_text() == (
        f"Input=wrote 10 characters to {source} Missing=missing.node.output Literal=plain value"
    )
    assert result.node_outputs["make-prompt"].data["content"] == output.read_text()


async def test_common_llm_task_uses_agent_subscription(tmp_path: Path) -> None:
    sub = FakeSubscription(output="summary")
    wf = _make_workflow()
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=tmp_path / "unused.md",
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="summarize",
            operation=CommonLlmTaskOperation(
                type=OperationType.COMMON_LLM_TASK,
                agent_id="bot",
                task="summarize",
                target="README.md",
                working_dir=tmp_path,
            ),
        )
    )

    result = await WorkflowExecutor(wf, {"claude_code": sub}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert result.node_outputs["summarize"].output == "summary"
    assert "Summarize" in str(sub.calls[0]["prompt"])
    assert "README.md" in str(sub.calls[0]["prompt"])


async def test_common_llm_task_missing_agent_reports_node_failure(tmp_path: Path) -> None:
    wf = _make_workflow("missing-task-agent")
    wf.add_operation(
        GraphNode(
            node_id="summarize",
            operation=CommonLlmTaskOperation(
                type=OperationType.COMMON_LLM_TASK,
                agent_id="missing",
                task="summarize",
                target="README.md",
                working_dir=tmp_path,
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert "Agent 'missing' not registered" in result.node_outputs["summarize"].output


async def test_common_llm_task_missing_subscription_reports_node_failure(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "unused.md"
    prompt.write_text("")
    wf = _make_workflow("missing-task-subscription")
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="summarize",
            operation=CommonLlmTaskOperation(
                type=OperationType.COMMON_LLM_TASK,
                agent_id="bot",
                task="summarize",
                target="README.md",
                working_dir=tmp_path,
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert "No subscription for 'claude_code'" in result.node_outputs["summarize"].output


async def test_agent_node_can_call_skill_without_prompt_path(tmp_path: Path) -> None:
    sub = FakeSubscription(output="done")
    wf = _make_workflow()
    wf.register_agent(
        AgentConfig(
            agent_id="builder",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=tmp_path / "unused.md",
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="skill",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="builder",
                working_dir=tmp_path,
                skill_name="gofer-flow-workflow-builder",
            ),
        )
    )

    result = await WorkflowExecutor(wf, {"claude_code": sub}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert sub.calls[0]["prompt"] == "/gofer-flow-workflow-builder"


async def test_agent_node_logs_thoughts_and_message_separately(tmp_path: Path) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text("Say hello.")
    sub = FakeSubscription(
        output="hello from agent",
        thoughts=["checking context\nchoosing answer"],
        message="hello from agent",
    )

    wf = _make_workflow("agent-log")
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert result.success
    assert result.node_outputs["agent-step"].output == "hello from agent"
    log_text = result.log_path.read_text()
    assert "agent-step - AGENT_THOUGHT:" in log_text
    assert "checking context" in log_text
    assert "choosing answer" in log_text
    assert "agent-step - AGENT_MESSAGE:" in log_text
    assert "hello from agent" in log_text
    assert "agent-step - node output:" in log_text
    assert "agent-step - node output:\nhello from agent\n" in log_text
    assert "node output:\nAGENT_MESSAGE" not in log_text


async def test_agent_node_streams_thoughts_to_log_before_completion(tmp_path: Path) -> None:
    class StreamingFakeSubscription(FakeSubscription):
        async def execute(
            self,
            prompt: str,
            working_dir: Path,
            tools: list[str],
            mcp_servers: list[str],
            env: dict[str, str],
            timeout: float | None = None,
            cancel_event: threading.Event | None = None,
            extra_paths: list[Path] | None = None,
            max_output_bytes: int | None = None,
            on_thought: Callable[[str], None] | None = None,
            provider_settings: ResolvedProviderSettings | None = None,
        ) -> AgentResult:
            self.calls.append(
                {
                    "prompt": prompt,
                    "working_dir": working_dir,
                    "extra_paths": extra_paths or [],
                    "max_output_bytes": max_output_bytes,
                }
            )
            if on_thought is not None:
                on_thought("live thought\n")
            log_paths = list((tmp_path / "logs" / "agent-live-log").glob("*.log"))
            assert len(log_paths) == 1
            assert "live thought" in log_paths[0].read_text(encoding="utf-8")
            return AgentResult(
                agent_id="",
                success=True,
                output="final answer",
                exit_code=0,
                duration_seconds=0.0,
                thoughts=["live thought\n"],
                message="final answer",
            )

    prompt = tmp_path / "p.md"
    prompt.write_text("Say hello.")
    sub = StreamingFakeSubscription()

    wf = _make_workflow("agent-live-log")
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert result.success
    log_text = result.log_path.read_text(encoding="utf-8")
    assert log_text.count("live thought") == 1
    assert "agent-step - AGENT_MESSAGE:" in log_text
    assert "agent-step - node output:" in log_text
    assert "agent-step - node output:\nfinal answer\n" in log_text
    assert "final answer" in log_text


async def test_agent_node_uses_node_prompt_path_over_agent_default(tmp_path: Path) -> None:
    default_prompt = tmp_path / "default.md"
    default_prompt.write_text("Default {{value}}")
    selected_prompt = tmp_path / "selected.md"
    selected_prompt.write_text("Selected {{value}}")
    node_working_dir = tmp_path / "node-workdir"
    node_working_dir.mkdir()
    sub = FakeSubscription(output="done")

    wf = _make_workflow("agent-prompt-override")
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=default_prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=selected_prompt,
                working_dir=node_working_dir,
                input_mapping={"value": "trigger.value"},
            ),
        )
    )

    result = (
        await WorkflowExecutor(
            wf,
            {"claude_code": sub},
            log_base_dir=tmp_path / "logs",
        )
        .with_trigger_context({"value": "input"})
        .run()
    )

    assert result.success
    assert envelope_request(sub.calls[0]["prompt"]) == "Selected input"
    assert sub.calls[0]["working_dir"] == node_working_dir


async def test_agent_node_memory_run_keeps_conversation_within_workflow_run(tmp_path: Path) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text("Review iteration.")
    sub = FakeSubscription(output="agent reply", message="agent reply")

    wf = _make_workflow("agent-run-memory")
    wf.config = wf.config.model_copy(update={"max_total_node_runs": 2})
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
                memory="run",
            ),
        )
    )
    wf.graph.add_edge(
        "agent-step",
        "agent-step",
        EdgeConfig(
            from_node="agent-step",
            to_node="agent-step",
            condition=EdgeConditionType.ON_SUCCESS,
        ),
    )

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert not result.success
    assert len(sub.calls) == 2
    assert "Previous conversation:" not in str(sub.calls[0]["prompt"])
    assert "Previous conversation:" in str(sub.calls[1]["prompt"])
    assert "agent reply" in str(sub.calls[1]["prompt"])


async def test_agent_node_memory_all_persists_between_workflow_runs(tmp_path: Path) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text("Review once.")
    sub = FakeSubscription(output="stored reply", message="stored reply")

    wf = _make_workflow("agent-all-memory")
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
                memory="all",
            ),
        )
    )

    await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()
    await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert len(sub.calls) == 2
    assert "Previous conversation:" not in str(sub.calls[0]["prompt"])
    assert "Previous conversation:" in str(sub.calls[1]["prompt"])
    assert "stored reply" in str(sub.calls[1]["prompt"])
    memory_path = tmp_path / "agent-memory" / "agent-all-memory" / "agent-step.json"
    assert memory_path.exists()


async def test_agent_node_memory_compaction_logs_info(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text("Review once.")

    class CompactingSubscription(FakeSubscription):
        async def execute(
            self,
            prompt: str,
            working_dir: Path,
            tools: list[str],
            mcp_servers: list[str],
            env: dict[str, str],
            timeout: float | None = None,
            cancel_event: threading.Event | None = None,
            extra_paths: list[Path] | None = None,
            max_output_bytes: int | None = None,
            on_thought: Callable[[str], None] | None = None,
            provider_settings: ResolvedProviderSettings | None = None,
        ) -> AgentResult:
            prompt_text = prompt
            self.calls.append(
                {
                    "prompt": prompt_text,
                    "working_dir": working_dir,
                    "extra_paths": extra_paths or [],
                }
            )
            if prompt_text.startswith("Compact this Raticode agent-node"):
                return AgentResult(
                    agent_id="",
                    success=True,
                    output="short memory",
                    exit_code=0,
                    duration_seconds=0.0,
                    message="short memory",
                )
            return AgentResult(
                agent_id="",
                success=True,
                output="fresh reply",
                exit_code=0,
                duration_seconds=0.0,
                message="fresh reply",
            )

    monkeypatch.setattr(executor_module, "AGENT_MEMORY_COMPACT_CHAR_LIMIT", 20)
    sub = CompactingSubscription()
    extra_dir = tmp_path.parent / "agent-compact-extra-path"
    extra_dir.mkdir(exist_ok=True)

    wf = _make_workflow("agent-compact-memory")
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
            extra_paths=[extra_dir],
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
                memory="all",
            ),
        )
    )

    memory_path = tmp_path / "agent-memory" / "agent-compact-memory" / "agent-step.json"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(
        '[{"role":"user","body":"very long previous prompt"},'
        '{"role":"assistant","body":"very long previous response"}]',
        encoding="utf-8",
    )

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert result.success
    assert len(sub.calls) == 2
    assert result.log_path is not None
    log_text = result.log_path.read_text(encoding="utf-8")
    assert "INFO - Compacting agent context for agent node agent-step" in log_text
    assert "Compacted prior agent node context" in str(sub.calls[1]["prompt"])
    assert "short memory" in str(sub.calls[1]["prompt"])
    assert sub.calls[0]["extra_paths"] == [extra_dir.resolve()]


@pytest.mark.parametrize("compact_mode", ["failed", "empty"])
async def test_agent_node_memory_compaction_uses_fallback_summary(
    compact_mode: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text("Review once.")

    class FallbackCompactingSubscription(FakeSubscription):
        async def execute(
            self,
            prompt: str,
            working_dir: Path,
            tools: list[str],
            mcp_servers: list[str],
            env: dict[str, str],
            timeout: float | None = None,
            cancel_event: threading.Event | None = None,
            extra_paths: list[Path] | None = None,
            max_output_bytes: int | None = None,
            on_thought: Callable[[str], None] | None = None,
            provider_settings: ResolvedProviderSettings | None = None,
        ) -> AgentResult:
            prompt_text = prompt
            self.calls.append({"prompt": prompt_text})
            if prompt_text.startswith("Compact this Raticode agent-node"):
                return AgentResult(
                    agent_id="",
                    success=compact_mode != "failed",
                    output="" if compact_mode == "empty" else "failed",
                    exit_code=0 if compact_mode == "empty" else 1,
                    duration_seconds=0.0,
                    message="" if compact_mode == "empty" else "failed",
                )
            return AgentResult(
                agent_id="",
                success=True,
                output="fresh reply",
                exit_code=0,
                duration_seconds=0.0,
                message="fresh reply",
            )

    monkeypatch.setattr(executor_module, "AGENT_MEMORY_COMPACT_CHAR_LIMIT", 20)
    sub = FallbackCompactingSubscription()
    wf = _make_workflow(f"agent-compact-fallback-{compact_mode}")
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
                memory="all",
            ),
        )
    )
    memory_path = tmp_path / "agent-memory" / wf.config.id / "agent-step.json"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(
        '[{"role":"user","body":"very long previous prompt"},'
        '{"role":"assistant","body":"very long previous response"}]',
        encoding="utf-8",
    )

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert result.success
    assert len(sub.calls) == 2
    final_prompt = envelope_request(sub.calls[1]["prompt"])
    assert "Compacted prior agent node context" in final_prompt
    assert "User:\nvery long previous prompt" in final_prompt
    assert "Assistant:\nvery long previous response" in final_prompt


async def test_local_vectorize_and_search_nodes(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha beta gofer workflow")
    (docs / "b.txt").write_text("zebra banana")
    index = tmp_path / "index.json"
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="index",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=docs,
                index_path=index,
                glob="*.txt",
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="search",
            operation=LocalSearchOperation(
                type=OperationType.LOCAL_SEARCH,
                index_path=index,
                query="gofer workflow",
                top_k=1,
            ),
        )
    )
    wf.then("index", "search")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert "a.txt" in result.node_outputs["search"].output


async def test_local_vectorize_incremental_unchanged_does_not_rewrite_index(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("alpha beta gofer workflow")
    index = tmp_path / "index.json"
    wf = _make_workflow("vector-current")
    wf.add_operation(
        GraphNode(
            node_id="index",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=docs,
                index_path=index,
                glob="*.txt",
            ),
        )
    )

    first = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs1").run()
    first_mtime = index.stat().st_mtime_ns
    second = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs2").run()

    assert first.success
    assert second.success
    assert index.stat().st_mtime_ns == first_mtime
    output = second.node_outputs["index"]
    assert output.data["current"] is True
    assert output.data["unchanged_files"] == 1
    assert output.data["updated_files"] == 0
    assert output.data["message"] == output.output
    assert output.data["last_update_time"] == first.node_outputs["index"].data["last_update_time"]
    assert "index current" in output.output


async def test_local_vectorize_incremental_add_update_delete(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    a_path = docs / "a.txt"
    b_path = docs / "b.txt"
    deleted_path = docs / "delete.txt"
    a_path.write_text("alpha gofer")
    deleted_path.write_text("remove me")
    index = tmp_path / "index.json"
    wf = _make_workflow("vector-changes")
    wf.add_operation(
        GraphNode(
            node_id="index",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=docs,
                index_path=index,
                glob="*.txt",
            ),
        )
    )

    first = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs1").run()
    assert first.success
    a_path.write_text("alpha changed gofer")
    b_path.write_text("banana added gofer")
    deleted_path.unlink()

    second = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs2").run()

    assert second.success
    output = second.node_outputs["index"]
    assert output.data["added_files"] == 1
    assert output.data["updated_files"] == 1
    assert output.data["deleted_files"] == 1
    document = json.loads(index.read_text(encoding="utf-8"))
    entries_path = index.parent / document["entries_file"]
    indexed_paths = {
        json.loads(line)["path"]
        for line in entries_path.read_text(encoding="utf-8").splitlines()
        if line
    }
    assert str(a_path) in indexed_paths
    assert str(b_path) in indexed_paths
    assert str(deleted_path) not in indexed_paths
    assert document["metadata"]["embedding_strategy"] == "hash_token_v1"


async def test_local_vectorize_compact_reindexes_modified_files(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    doc = docs / "a.txt"
    doc.write_text("alpha gofer")
    index = tmp_path / "index.json"
    wf = _make_workflow("vector-compact-initial")
    wf.add_operation(
        GraphNode(
            node_id="index",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=docs,
                index_path=index,
                glob="*.txt",
            ),
        )
    )

    initial = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs1").run()
    assert initial.success
    time.sleep(0.001)
    doc.write_text("alpha compact changed gofer")
    compact_wf = _make_workflow("vector-compact")
    compact_wf.add_operation(
        GraphNode(
            node_id="index",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=docs,
                index_path=index,
                glob="*.txt",
                mode="compact",
            ),
        )
    )

    result = await WorkflowExecutor(compact_wf, {}, log_base_dir=tmp_path / "logs2").run()

    assert result.success
    output = result.node_outputs["index"]
    assert output.data["current"] is False
    assert output.data["updated_files"] == 1
    assert output.data["unchanged_files"] == 0
    document = json.loads(index.read_text(encoding="utf-8"))
    entries_path = index.parent / document["entries_file"]
    entries = [
        json.loads(line) for line in entries_path.read_text(encoding="utf-8").splitlines() if line
    ]
    assert entries[0]["text"] == "alpha compact changed gofer"


async def test_local_vectorize_validate_reports_stale_without_rewriting(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    doc = docs / "a.txt"
    doc.write_text("alpha gofer")
    index = tmp_path / "index.json"
    wf = _make_workflow("vector-validate")
    wf.add_operation(
        GraphNode(
            node_id="index",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=docs,
                index_path=index,
                glob="*.txt",
            ),
        )
    )
    initial = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs1").run()
    assert initial.success
    before = index.read_text(encoding="utf-8")
    doc.write_text("alpha stale gofer")
    validate_wf = _make_workflow("vector-validate-only")
    validate_wf.add_operation(
        GraphNode(
            node_id="index",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=docs,
                index_path=index,
                glob="*.txt",
                mode="validate",
            ),
        )
    )

    result = await WorkflowExecutor(validate_wf, {}, log_base_dir=tmp_path / "logs2").run()

    assert result.success
    output = result.node_outputs["index"]
    assert output.data["status"] == "stale"
    assert output.data["stale_files"] == 1
    assert output.data["last_update_time"] == json.loads(before)["metadata"]["last_update_time"]
    assert index.read_text(encoding="utf-8") == before


async def test_local_search_returns_ranked_metadata_and_threshold(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.txt").write_text("gofer workflow search quality")
    (docs / "b.txt").write_text("unrelated banana")
    index = tmp_path / "index.json"
    wf = _make_workflow("vector-search-shape")
    wf.add_operation(
        GraphNode(
            node_id="index",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=docs,
                index_path=index,
                glob="*.txt",
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="search",
            operation=LocalSearchOperation(
                type=OperationType.LOCAL_SEARCH,
                index_path=index,
                query="gofer workflow",
                top_k=5,
                score_threshold=0.01,
            ),
        )
    )
    wf.then("index", "search")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    output = result.node_outputs["search"]
    assert len(output.items) == 1
    item = cast(dict[str, Any], output.items[0])
    assert item["path"].endswith("a.txt")
    assert item["score"] > 0
    assert item["text"] == item["snippet"]
    assert item["metadata"]["file_name"] == "a.txt"
    assert str(output.data["message"]).startswith("local_search returned 1 results")
    assert output.data["strategy"] == "cosine_v1"


async def test_local_search_reads_legacy_simple_index(tmp_path: Path) -> None:
    index = tmp_path / "legacy.json"
    index.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "path": str(tmp_path / "a.txt"),
                        "chunk": 0,
                        "text": "legacy gofer workflow",
                        "vector": executor_module._token_vector("legacy gofer workflow"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    wf = _make_workflow("legacy-search")
    wf.add_operation(
        GraphNode(
            node_id="search",
            operation=LocalSearchOperation(
                type=OperationType.LOCAL_SEARCH,
                index_path=index,
                query="gofer",
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    item = cast(dict[str, Any], result.node_outputs["search"].items[0])
    assert item["path"].endswith("a.txt")
    assert result.node_outputs["search"].data["embedding_strategy"] == "legacy_hash_token"


async def test_local_search_includes_scores_equal_to_threshold(tmp_path: Path) -> None:
    index = tmp_path / "threshold.json"
    index.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "path": str(tmp_path / "a.txt"),
                        "chunk": 0,
                        "text": "gofer",
                        "vector": executor_module._token_vector("gofer"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    wf = _make_workflow("threshold-search")
    wf.add_operation(
        GraphNode(
            node_id="search",
            operation=LocalSearchOperation(
                type=OperationType.LOCAL_SEARCH,
                index_path=index,
                query="gofer",
                score_threshold=1.0,
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert len(result.node_outputs["search"].items) == 1


async def test_local_search_rejects_invalid_index_file(tmp_path: Path) -> None:
    index = tmp_path / "bad.json"
    index.write_text("{not json", encoding="utf-8")
    wf = _make_workflow("bad-index")
    wf.add_operation(
        GraphNode(
            node_id="search",
            operation=LocalSearchOperation(
                type=OperationType.LOCAL_SEARCH,
                index_path=index,
                query="gofer",
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert "Invalid vector index JSON" in result.node_outputs["search"].output


async def test_local_vectorize_logs_unreadable_files_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    unreadable = docs / "bad.txt"
    readable = docs / "good.txt"
    unreadable.write_text("bad")
    readable.write_text("gofer workflow")
    index = tmp_path / "index.json"
    executor_any = cast(Any, executor_module)
    original_read_text_limited = executor_any.read_text_limited

    def fake_read_text_limited(
        path: Path,
        *,
        encoding: str = "utf-8",
        errors: str = "strict",
        max_bytes: int,
    ) -> str:
        if path == unreadable:
            raise OSError("permission denied")
        return cast(
            str,
            original_read_text_limited(
                path,
                encoding=encoding,
                errors=errors,
                max_bytes=max_bytes,
            ),
        )

    monkeypatch.setattr(executor_module, "read_text_limited", fake_read_text_limited)
    wf = _make_workflow("vector-unreadable")
    wf.add_operation(
        GraphNode(
            node_id="index",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=docs,
                index_path=index,
                glob="*.txt",
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    output = result.node_outputs["index"]
    assert output.data["file_count"] == 2
    assert output.data["chunk_count"] == 1
    assert result.log_path is not None
    log_text = result.log_path.read_text(encoding="utf-8")
    assert "could not read" in log_text
    assert "permission denied" in log_text


async def test_local_search_rejects_oversized_index(tmp_path: Path) -> None:
    index = tmp_path / "index.json"
    index.write_text("x" * 20)
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="search-index-limit",
            name="Search Index Limit",
            resource_limits=ResourceLimits(max_vector_index_bytes=10),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="search",
            operation=LocalSearchOperation(
                type=OperationType.LOCAL_SEARCH,
                index_path=index,
                query="gofer",
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert "limit 10 bytes" in result.node_outputs["search"].output


async def test_local_search_caps_result_count_and_text(tmp_path: Path) -> None:
    index = tmp_path / "index.json"
    entries = [
        {
            "path": str(tmp_path / f"{item}.txt"),
            "chunk": item,
            "text": "gofer " + ("x" * 120),
            "vector": executor_module._token_vector("gofer"),
        }
        for item in range(5)
    ]
    index.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="search-output-limit",
            name="Search Output Limit",
            resource_limits=ResourceLimits(
                max_fanout_items=2,
                max_file_read_bytes=80,
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="search",
            operation=LocalSearchOperation(
                type=OperationType.LOCAL_SEARCH,
                index_path=index,
                query="gofer",
                top_k=5,
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    output = result.node_outputs["search"]
    assert len(output.items) == 2
    for item in output.items:
        assert isinstance(item, dict)
        assert len(str(item["text"]).encode()) <= 80
    assert "truncated at 80 bytes" in output.output


async def test_local_vectorize_rejects_oversized_input(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "large.txt").write_text("x" * 20)
    index = tmp_path / "index.json"
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="vector-limit",
            name="Vector Limit",
            resource_limits=ResourceLimits(max_file_read_bytes=10),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="index",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=docs,
                index_path=index,
                glob="*.txt",
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert "limit 10 bytes" in result.node_outputs["index"].output
    assert not index.exists()


async def test_local_vectorize_rejects_exact_oversized_index_before_write(
    tmp_path: Path,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    files = []
    for name in ("a.txt", "b.txt", "c.txt"):
        path = docs / name
        path.write_text("alpha")
        files.append(path)
    index = tmp_path / "index.json"
    entries = [
        {
            **executor_module._file_path_data(path),
            "chunk": 0,
            "text": "alpha",
            "vector": executor_module._token_vector("alpha"),
        }
        for path in files
    ]
    approximate_size = len(
        json.dumps(
            {
                "version": 1,
                "source_path": str(docs),
                "glob": "*.txt",
                "entries": [],
            }
        ).encode("utf-8")
    ) + sum(len(json.dumps(entry, default=str).encode("utf-8")) for entry in entries)
    exact_size = len(
        json.dumps(
            {
                "version": 1,
                "source_path": str(docs),
                "glob": "*.txt",
                "entries": entries,
            }
        ).encode("utf-8")
    )
    assert exact_size > approximate_size
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="vector-index-limit",
            name="Vector Index Limit",
            resource_limits=ResourceLimits(max_vector_index_bytes=exact_size - 1),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="index",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=docs,
                index_path=index,
                glob="*.txt",
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert "local_vectorize index exceeded limit" in result.node_outputs["index"].output
    assert "got " in result.node_outputs["index"].output
    assert not index.exists()


async def test_local_vectorize_stops_before_consuming_all_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    files = []
    for index in range(5):
        path = docs / f"{index}.txt"
        path.write_text("x")
        files.append(path)
    index_path = tmp_path / "index.json"
    original_rglob = Path.rglob

    def bounded_rglob(path: Path, pattern: str) -> Iterator[Path]:
        if path != docs or pattern != "*.txt":
            yield from original_rglob(path, pattern)
            return
        for item_index, file_path in enumerate(files):
            if item_index > 2:
                raise AssertionError("local_vectorize consumed past the limit check")
            yield file_path

    monkeypatch.setattr(Path, "rglob", bounded_rglob)
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="vector-scan-limit",
            name="Vector Scan Limit",
            resource_limits=ResourceLimits(max_files_scanned=2),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="index",
            operation=LocalVectorizeOperation(
                type=OperationType.LOCAL_VECTORIZE,
                source_path=docs,
                index_path=index_path,
                glob="*.txt",
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert "scanned files exceeded limit 2" in result.node_outputs["index"].output
    assert not index_path.exists()


async def test_bash_output_is_truncated_by_resource_limit(tmp_path: Path) -> None:
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="output-limit",
            name="Output Limit",
            resource_limits=ResourceLimits(max_subprocess_output_bytes=80),
        )
    )
    wf.add_operation(_bash_node("chatty", "printf '%100s' | tr ' ' x"))

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    output = result.node_outputs["chatty"].output
    assert len(output.encode()) <= 80
    assert "truncated at 80 bytes" in output


def test_node_log_body_is_capped_after_line_serialization(tmp_path: Path) -> None:
    limits = ResourceLimits(max_log_bytes_per_node=80, max_log_bytes_per_run=10_000)
    run_log = WorkflowRunLog("log-limit", base_dir=tmp_path, limits=limits)

    run_log.node_output("node", "stdout", "\n".join(["x" * 20] * 20))

    body = run_log.path.read_text(encoding="utf-8").split("node - stdout:\n", 1)[1]
    assert byte_len(body) <= limits.max_log_bytes_per_node
    assert run_log._node_log_bytes[("node", None, None)] == byte_len(body)  # noqa: SLF001


def test_single_line_agent_event_is_capped_by_node_log_limit(tmp_path: Path) -> None:
    limits = ResourceLimits(max_log_bytes_per_node=80, max_log_bytes_per_run=10_000)
    run_log = WorkflowRunLog("agent-log-limit", base_dir=tmp_path, limits=limits)

    run_log.node_agent_event("agent", "message", "m" * 1000)

    body = run_log.path.read_text(encoding="utf-8").split("agent - message:\n", 1)[1]
    assert byte_len(body) <= limits.max_log_bytes_per_node
    assert run_log._node_log_bytes[("agent", None, None)] == byte_len(body)  # noqa: SLF001


def test_agent_thought_log_message_is_capped_before_node_log_limit(tmp_path: Path) -> None:
    limits = ResourceLimits(
        max_log_message_bytes=120,
        max_log_bytes_per_node=10_000,
        max_log_bytes_per_run=10_000,
    )
    run_log = WorkflowRunLog("message-log-limit", base_dir=tmp_path, limits=limits)

    run_log.node_agent_event("agent", "AGENT_THOUGHT", "x" * 1000)
    run_log.node_agent_event("agent", "AGENT_THOUGHT", "later visible")

    text = run_log.path.read_text(encoding="utf-8")
    first_body = text.split("agent - AGENT_THOUGHT:\n", 1)[1].split(
        f"{run_log.started_at.year}-", 1
    )[0]

    assert byte_len(first_body) <= limits.max_log_message_bytes
    assert "agent AGENT_THOUGHT truncated at 120 bytes" in text
    assert "later visible" in text


def test_agent_message_and_node_output_are_not_capped_by_message_limit(tmp_path: Path) -> None:
    limits = ResourceLimits(
        max_log_message_bytes=120,
        max_log_bytes_per_node=10_000,
        max_log_bytes_per_run=10_000,
    )
    run_log = WorkflowRunLog("message-log-limit", base_dir=tmp_path, limits=limits)
    final_message = "m" * 1000
    node_output = "o" * 1000

    run_log.node_agent_event("agent", "AGENT_MESSAGE", final_message)
    run_log.node_output("agent", "node output", node_output)

    text = run_log.path.read_text(encoding="utf-8")

    assert final_message in text
    assert node_output in text
    assert "agent AGENT_MESSAGE truncated at 120 bytes" not in text
    assert "agent node output truncated at 120 bytes" not in text
    assert "\n2026-" in text


def test_agent_message_and_node_output_can_exceed_log_budget(tmp_path: Path) -> None:
    limits = ResourceLimits(
        max_log_message_bytes=120,
        max_log_bytes_per_node=80,
        max_log_bytes_per_run=220,
    )
    run_log = WorkflowRunLog("agent-final-log-limit", base_dir=tmp_path, limits=limits)
    final_message = "final-" + ("m" * 500)
    node_output = "output-" + ("o" * 500)

    run_log.node_agent_event("agent", "AGENT_THOUGHT", "x" * 1000)
    run_log.node_agent_event("agent", "AGENT_MESSAGE", final_message)
    run_log.node_output("agent", "node output", node_output, uncapped=True)

    text = run_log.path.read_text(encoding="utf-8")

    assert final_message in text
    assert node_output in text
    assert "agent - AGENT_MESSAGE:" in text
    assert "agent - node output:" in text
    assert byte_len(text) > limits.max_log_bytes_per_run


def test_truncated_agent_thought_does_not_run_into_next_log_entry(tmp_path: Path) -> None:
    limits = ResourceLimits(
        max_log_message_bytes=120,
        max_log_bytes_per_node=10_000,
        max_log_bytes_per_run=10_000,
    )
    run_log = WorkflowRunLog("message-log-separation", base_dir=tmp_path, limits=limits)

    run_log.node_agent_event("agent", "AGENT_THOUGHT", "x" * 1000)
    run_log.node_agent_event("agent", "AGENT_MESSAGE", "final message")

    text = run_log.path.read_text(encoding="utf-8")

    assert "truncated at 120 bytes]\n2026-" in text
    assert "bytes]2026-" not in text
    assert "agent - AGENT_MESSAGE:\nfinal message\n" in text


def test_agent_events_update_live_node_event_data(tmp_path: Path) -> None:
    run_log = WorkflowRunLog("agent-event-data", base_dir=tmp_path)

    run_log.begin_node_attempt("node-4", 1, 1)
    run_log.event("node-4", "started", run_number=1, attempt=1)
    run_log.node_agent_event("node-4", "AGENT_THOUGHT", "first thought")
    run_log.node_agent_event("node-4", "AGENT_MESSAGE", "final message")

    payload = json.loads(run_log.events_path.read_text(encoding="utf-8"))
    node = payload["nodes"]["node-4"]
    assert node["status"] == "started"
    assert node["message"] == "final message"
    assert node["attempts"][0]["latestThought"] == "first thought"
    assert node["attempts"][0]["message"] == "final message"
    node_data = payload["nodes"]["node-4"]["data"]

    assert node_data["thoughts"] == ["first thought"]
    assert node_data["latestThought"] == "first thought"
    assert node_data["message"] == "final message"


def test_live_event_payloads_are_compacted_for_ui_state(tmp_path: Path) -> None:
    run_log = WorkflowRunLog("compact-event-data", base_dir=tmp_path)
    large_content = "x" * 20_000

    run_log.event(
        "agent",
        "started",
        attempt=1,
        run_number=1,
        fan_out_item={
            "file_name": "ticket.txt",
            "file_content": large_content,
        },
        data={
            "inputs": {
                "content": large_content,
            },
            "prompt": large_content,
        },
    )
    for index in range(25):
        run_log.node_agent_event("agent", "AGENT_THOUGHT", f"thought-{index}")

    payload = json.loads(run_log.events_path.read_text(encoding="utf-8"))
    node = payload["nodes"]["agent"]
    attempt = node["attempts"][0]

    assert len(payload["events"][0]["fanOutItem"]["file_content"]) < 2500
    assert len(attempt["fanOutItem"]["file_content"]) < 2500
    assert len(attempt["inputs"]["content"]) < 2500
    assert len(node["data"]["thoughts"]) == 20
    assert node["data"]["thoughts"][0] == "thought-5"
    assert node["data"]["latestThought"] == "thought-24"


def test_repeated_node_log_events_cannot_exceed_node_log_limit(tmp_path: Path) -> None:
    limits = ResourceLimits(max_log_bytes_per_node=80, max_log_bytes_per_run=10_000)
    run_log = WorkflowRunLog("repeat-log-limit", base_dir=tmp_path, limits=limits)

    for _ in range(5):
        run_log.node_output("node", "stdout", "x" * 200)

    assert run_log._node_log_bytes[("node", None, None)] <= limits.max_log_bytes_per_node  # noqa: SLF001
    text = run_log.path.read_text(encoding="utf-8")
    assert text.count("omitted; log limit exceeded") <= 1


def test_repeated_node_runs_each_get_log_body_budget(tmp_path: Path) -> None:
    limits = ResourceLimits(max_log_bytes_per_node=80, max_log_bytes_per_run=10_000)
    run_log = WorkflowRunLog("repeat-run-log-limit", base_dir=tmp_path, limits=limits)

    run_log.begin_node_attempt("node", 1, 1)
    run_log.node_output("node", "node output", "first-" + ("x" * 200))
    run_log.node_output("node", "node output", "first hidden")
    run_log.begin_node_attempt("node", 2, 1)
    run_log.node_output("node", "node output", "second visible")

    text = run_log.path.read_text(encoding="utf-8")

    assert "first-" in text
    assert "first hidden" not in text
    assert "second visible" in text
    assert "node - node output:\n2026-" not in text
    assert run_log._node_log_bytes[("node", 1, 1)] <= limits.max_log_bytes_per_node  # noqa: SLF001
    assert run_log._node_log_bytes[("node", 2, 1)] <= limits.max_log_bytes_per_node  # noqa: SLF001


async def test_agent_data_message_is_not_truncated_by_log_resource_limit(tmp_path: Path) -> None:
    sub = FakeSubscription(output="m" * 120)
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="agent-message-limit",
            name="Agent Message Limit",
            resource_limits=ResourceLimits(max_log_bytes_per_node=80),
        )
    )
    prompt = tmp_path / "prompt.md"
    prompt.write_text("hello")
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    message = result.node_outputs["agent"].data["message"]
    assert isinstance(message, str)
    assert message == "m" * 120
    assert result.node_outputs["agent"].output == "m" * 120


async def test_failure_route_runs_after_retries_are_exhausted(tmp_path: Path) -> None:
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="fail",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="false"),
            retry_count=2,
            retry_delay_seconds=0,
        )
    )
    wf.add_operation(_bash_node("recover", "echo recovered after retries"))
    wf.then(
        "fail",
        "recover",
        EdgeConfig(
            from_node="fail",
            to_node="recover",
            condition=EdgeConditionType.ON_FAILURE,
        ),
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert result.node_outputs["recover"].success
    assert "recovered after retries" in result.node_outputs["recover"].output
    assert result.log_path is not None
    text = result.log_path.read_text()
    assert "fail - attempt 1 started" in text
    assert "fail - attempt 2 started" in text
    assert "fail - attempt 3 started" in text
    assert text.index("fail - attempt 3 finished") < text.index("recover - attempt 1 started")


async def test_self_loop_repeats_until_output_no_longer_matches(tmp_path: Path) -> None:
    counter = tmp_path / "counter"
    command = (
        f"n=$(cat {counter} 2>/dev/null || echo 0); "
        "n=$((n + 1)); "
        f"echo $n > {counter}; "
        'if [ "$n" -lt 3 ]; then echo retry; else echo done; fi'
    )

    wf = _make_workflow("recursive")
    wf.add_operation(_bash_node("improve", command))
    wf.then(
        "improve",
        "improve",
        EdgeConfig(
            from_node="improve",
            to_node="improve",
            condition=EdgeConditionType.OUTPUT_MATCHES,
            output_pattern="retry",
        ),
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert result.node_outputs["improve"].output.strip() == "done"
    assert len(result.node_runs["improve"]) == 3
    assert result.log_path is not None
    text = result.log_path.read_text()
    assert "improve - run 2 attempt 1 started" in text
    assert "improve - run 3 attempt 1 finished success=True" in text


async def test_recursive_workflow_stops_at_max_total_node_runs(tmp_path: Path) -> None:
    wf = _make_workflow("runaway")
    wf.add_operation(_bash_node("loop", "echo again"))
    wf.then("loop", "loop")

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        max_total_node_runs=3,
    ).run()

    assert not result.success
    assert result.log_path is not None
    assert "maximum node run limit exceeded" in result.log_path.read_text()


async def test_dry_run_does_not_execute(tmp_path: Path) -> None:
    wf = _make_workflow()
    wf.add_operation(_bash_node("dangerous", "rm -rf /"))
    executor = WorkflowExecutor(wf, {}, dry_run=True, log_base_dir=tmp_path / "logs")
    result = await executor.run()
    assert result.success


async def test_approval_gate_pauses_and_resumes_after_approval(tmp_path: Path) -> None:
    wf = _make_workflow("approval-flow")
    wf.add_operation(_bash_node("plan", "echo deploy"))
    wf.add_operation(
        GraphNode(
            node_id="approve",
            operation=ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message="Approve {{plan.output}}?",
                approvers=["alice"],
                notify=True,
            ),
        )
    )
    wf.add_operation(_bash_node("deploy", "echo shipped"))
    wf.then("plan", "approve")
    wf.then(
        "approve",
        "deploy",
        EdgeConfig(
            from_node="approve",
            to_node="deploy",
            condition=EdgeConditionType.ON_SUCCESS,
        ),
    )
    store = ApprovalStore(tmp_path)
    notifications = RecordingNotificationAdapter()
    result = None

    async def decide_when_pending() -> None:
        while True:
            pending = store.list_pending("approval-flow")
            if pending:
                request = pending[0]
                store.decide(
                    request.workflow_id,
                    request.run_id,
                    request.node_id,
                    "approved",
                    decided_by="alice",
                    notes="ship it",
                )
                return
            await anyio.sleep(0.05)

    async with anyio.create_task_group() as tg:
        tg.start_soon(decide_when_pending)
        result = await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            approval_store=store,
            notification_adapter=notifications,
        ).run()

    assert result is not None
    assert result.success
    approval = result.node_outputs["approve"]
    assert approval.success
    assert approval.data["decision"] == "approved"
    assert approval.data["decidedBy"] == "alice"
    assert "deploy" in result.node_outputs
    assert notifications.notifications
    assert "Approve deploy" in notifications.notifications[0].body
    assert "Approve with: gof workflow approve" in notifications.notifications[0].body
    assert "--by alice" in notifications.notifications[0].body
    assert "Reject with: gof workflow reject" in notifications.notifications[0].body
    assert approval.data["approveCommand"]
    assert "--by alice" in str(approval.data["approveCommand"])
    assert "--by alice" in str(approval.data["rejectCommand"])
    assert result.log_path is not None
    log_text = result.log_path.read_text()
    assert "approval pending" in log_text
    assert "approval decision: decision=approved by=alice" in log_text


async def test_approval_gate_continues_when_notification_fails(
    tmp_path: Path,
) -> None:
    class FailingNotificationAdapter:
        async def send(self, _notification: object) -> None:
            raise RuntimeError("toast backend unavailable")

    wf = _make_workflow("approval-notify-failure")
    wf.add_operation(
        GraphNode(
            node_id="approve",
            operation=ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message="Approve deploy?",
                notify=True,
            ),
        )
    )
    store = ApprovalStore(tmp_path)
    result = None

    async def decide_when_pending() -> None:
        while True:
            pending = store.list_pending("approval-notify-failure")
            if pending:
                request = pending[0]
                store.decide(
                    request.workflow_id,
                    request.run_id,
                    request.node_id,
                    "approved",
                    decided_by="ui",
                )
                return
            await anyio.sleep(0.05)

    async with anyio.create_task_group() as tg:
        tg.start_soon(decide_when_pending)
        result = await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            approval_store=store,
            notification_adapter=FailingNotificationAdapter(),
        ).run()

    assert result is not None
    assert result.success
    assert result.node_outputs["approve"].data["decision"] == "approved"
    assert result.log_path is not None
    log_text = result.log_path.read_text()
    assert "approval notification failed: toast backend unavailable" in log_text
    assert "approval decision: decision=approved by=ui" in log_text


async def test_approval_gate_rejection_routes_on_failure(tmp_path: Path) -> None:
    wf = _make_workflow("approval-reject")
    wf.add_operation(
        GraphNode(
            node_id="approve",
            operation=ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message="Approve cleanup?",
            ),
        )
    )
    wf.add_operation(_bash_node("rejected", "echo rejected"))
    wf.then(
        "approve",
        "rejected",
        EdgeConfig(
            from_node="approve",
            to_node="rejected",
            condition=EdgeConditionType.ON_FAILURE,
        ),
    )
    store = ApprovalStore(tmp_path)

    async def reject_when_pending() -> None:
        while True:
            pending = store.list_pending("approval-reject")
            if pending:
                request = pending[0]
                store.decide(
                    request.workflow_id,
                    request.run_id,
                    request.node_id,
                    "rejected",
                    decided_by="bob",
                )
                return
            await anyio.sleep(0.05)

    async with anyio.create_task_group() as tg:
        tg.start_soon(reject_when_pending)
        result = await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            approval_store=store,
        ).run()

    assert result.success
    assert result.node_outputs["approve"].data["decision"] == "rejected"
    assert result.node_outputs["rejected"].success


async def test_approval_gate_timeout_records_persistent_request(tmp_path: Path) -> None:
    wf = _make_workflow("approval-timeout")
    wf.add_operation(
        GraphNode(
            node_id="approve",
            operation=ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message="Approve?",
                timeout_seconds=0.01,
            ),
            allow_failure=True,
        )
    )
    store = ApprovalStore(tmp_path)

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        approval_store=store,
    ).run()

    assert result.success
    output = result.node_outputs["approve"]
    assert output.data["decision"] == "timeout"
    request = store.get("approval-timeout", str(output.data["runId"]), "approve")
    assert request is not None
    assert request.decision is not None
    assert request.decision.decision == "timeout"


async def test_approval_gate_timeout_reject_persists_rejected_decision(
    tmp_path: Path,
) -> None:
    wf = _make_workflow("approval-timeout-reject")
    wf.add_operation(
        GraphNode(
            node_id="approve",
            operation=ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message="Approve?",
                timeout_seconds=0.01,
                timeout_decision="reject",
            ),
            allow_failure=True,
        )
    )
    store = ApprovalStore(tmp_path)

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        approval_store=store,
    ).run()

    output = result.node_outputs["approve"]
    assert output.data["decision"] == "rejected"
    request = store.get("approval-timeout-reject", str(output.data["runId"]), "approve")
    assert request is not None
    assert request.decision is not None
    assert request.decision.decision == "rejected"


async def test_approval_restart_resume_routes_expired_timeout(tmp_path: Path) -> None:
    wf = _make_workflow("approval-timeout-restart")
    wf.add_operation(
        GraphNode(
            node_id="approve",
            operation=ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message="Approve?",
                timeout_seconds=10,
            ),
        )
    )
    wf.add_operation(_bash_node("timedout", "echo timed out"))
    wf.then(
        "approve",
        "timedout",
        EdgeConfig(
            from_node="approve",
            to_node="timedout",
            condition=EdgeConditionType.ON_FAILURE,
        ),
    )
    store = ApprovalStore(tmp_path)

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            WorkflowExecutor(
                wf,
                {},
                log_base_dir=tmp_path / "logs",
                approval_store=store,
            ).run
        )
        while not store.list_pending("approval-timeout-restart"):
            await anyio.sleep(0.01)
        tg.cancel_scope.cancel()

    request = store.list_pending("approval-timeout-restart")[0]
    request.requested_at = (datetime.now(UTC) - timedelta(seconds=20)).isoformat(timespec="seconds")
    store.create_or_update(request)

    request = store.list_requests("approval-timeout-restart")[0]
    assert request.decision is not None
    assert request.decision.decision == "timeout"

    resumed = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        approval_store=store,
    ).resume_from_approval(request)

    assert resumed is not None
    assert resumed.success
    assert resumed.node_outputs["approve"].data["decision"] == "timeout"
    assert resumed.node_outputs["timedout"].output.strip() == "timed out"


async def test_approval_gate_can_resume_from_persisted_checkpoint_after_restart(
    tmp_path: Path,
) -> None:
    wf = _make_workflow("approval-restart")
    wf.add_operation(_bash_node("plan", "echo deploy"))
    wf.add_operation(
        GraphNode(
            node_id="approve",
            operation=ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message="Approve {{plan.output}}?",
            ),
        )
    )
    wf.add_operation(_bash_node("deploy", "echo shipped"))
    wf.then("plan", "approve")
    wf.then(
        "approve",
        "deploy",
        EdgeConfig(
            from_node="approve",
            to_node="deploy",
            condition=EdgeConditionType.ON_SUCCESS,
        ),
    )
    store = ApprovalStore(tmp_path)

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            WorkflowExecutor(
                wf,
                {},
                log_base_dir=tmp_path / "logs",
                approval_store=store,
            ).run
        )
        while not store.list_pending("approval-restart"):
            await anyio.sleep(0.05)
        tg.cancel_scope.cancel()

    request = store.list_pending("approval-restart")[0]
    store.decide(
        request.workflow_id,
        request.run_id,
        request.node_id,
        "approved",
        decided_by="alice",
        notes="after restart",
    )

    resumed = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        approval_store=store,
    ).resume_from_approval(request)

    assert resumed is not None
    assert resumed.success
    assert resumed.node_outputs["approve"].data["decision"] == "approved"
    assert resumed.node_outputs["deploy"].output.strip() == "shipped"


async def test_approval_restart_resume_releases_claim_when_checkpoint_missing(
    tmp_path: Path,
) -> None:
    wf = _make_workflow("approval-retry")
    wf.add_operation(
        GraphNode(
            node_id="approve",
            operation=ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message="Approve?",
            ),
        )
    )
    store = ApprovalStore(tmp_path)

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            WorkflowExecutor(
                wf,
                {},
                log_base_dir=tmp_path / "logs",
                approval_store=store,
            ).run
        )
        while not store.list_pending("approval-retry"):
            await anyio.sleep(0.05)
        tg.cancel_scope.cancel()

    request = store.list_pending("approval-retry")[0]
    store.decide(
        request.workflow_id,
        request.run_id,
        request.node_id,
        "approved",
        decided_by="alice",
    )
    decided = store.get(request.workflow_id, request.run_id, request.node_id)
    assert decided is not None
    decided.checkpoint_path = str(tmp_path / "missing.checkpoint.json")
    store.create_or_update(decided)

    resumed = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        approval_store=store,
    ).resume_from_approval(decided)

    assert resumed is None
    retryable = store.get(request.workflow_id, request.run_id, request.node_id)
    assert retryable is not None
    assert retryable.resume_claimed_at is None


async def test_approval_restart_resume_reclaims_stale_resume_claim(
    tmp_path: Path,
) -> None:
    wf = _make_workflow("approval-stale-claim")
    wf.add_operation(
        GraphNode(
            node_id="approve",
            operation=ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message="Approve?",
            ),
        )
    )
    wf.add_operation(_bash_node("done", "echo done"))
    wf.then("approve", "done")
    store = ApprovalStore(tmp_path)

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            WorkflowExecutor(
                wf,
                {},
                log_base_dir=tmp_path / "logs",
                approval_store=store,
            ).run
        )
        while not store.list_pending("approval-stale-claim"):
            await anyio.sleep(0.05)
        tg.cancel_scope.cancel()

    request = store.list_pending("approval-stale-claim")[0]
    store.decide(
        request.workflow_id,
        request.run_id,
        request.node_id,
        "approved",
        decided_by="alice",
    )
    decided = store.get(request.workflow_id, request.run_id, request.node_id)
    assert decided is not None
    decided.resume_claimed_at = datetime.now(UTC).isoformat(timespec="seconds")
    decided.resume_claimed_by_pid = 999_999_999
    store.create_or_update(decided)

    resumed = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        approval_store=store,
    ).resume_from_approval(decided)

    assert resumed is not None
    assert resumed.success
    assert resumed.node_outputs["done"].output.strip() == "done"


async def test_approval_restart_resume_waits_for_join_inputs(tmp_path: Path) -> None:
    wf = _make_workflow("approval-join")
    other_ready = tmp_path / "other-ready"
    wf.add_operation(
        GraphNode(
            node_id="approve",
            operation=ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message="Approve?",
            ),
        )
    )
    wf.add_operation(
        _bash_node(
            "other",
            f'if [ -f "{other_ready}" ]; then echo other; else sleep 30; fi',
        )
    )
    wf.add_operation(_bash_node("join", "echo joined"))
    wf.then("approve", "join")
    wf.then("other", "join")
    store = ApprovalStore(tmp_path)

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            WorkflowExecutor(
                wf,
                {},
                log_base_dir=tmp_path / "logs",
                approval_store=store,
            ).run
        )
        while not store.list_pending("approval-join"):
            await anyio.sleep(0.05)
        request = store.list_pending("approval-join")[0]
        checkpoint_path = Path(request.checkpoint_path or "")
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        node_outputs = checkpoint.get("nodeOutputs")
        assert not (isinstance(node_outputs, dict) and "other" in node_outputs)
        tg.cancel_scope.cancel()

    request = store.list_pending("approval-join")[0]
    other_ready.write_text("ready", encoding="utf-8")
    store.decide(
        request.workflow_id,
        request.run_id,
        request.node_id,
        "approved",
        decided_by="alice",
    )

    resumed = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        approval_store=store,
    ).resume_from_approval(request)

    assert resumed is not None
    assert resumed.success
    assert resumed.node_outputs["other"].output.strip() == "other"
    assert resumed.node_outputs["join"].output.strip() == "joined"


async def test_approval_restart_resume_preserves_loop_context_after_approval(
    tmp_path: Path,
) -> None:
    wf = _make_workflow("approval-loop")
    wf.add_operation(
        GraphNode(
            node_id="approve",
            operation=ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message="Approve loop?",
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=CountFanSource(type="count", count=2, max_concurrency=1),
            ),
        )
    )
    wf.add_operation(_bash_node("print", 'printf "%s" "$INDEX"'))
    wf.add_operation(_bash_node("after", "echo after"))
    wf.then("approve", "loop")
    wf.then("loop", "print")
    wf.then(
        "loop",
        "after",
        EdgeConfig(
            from_node="loop",
            to_node="after",
            condition=EdgeConditionType.AFTER_LOOP,
        ),
    )
    store = ApprovalStore(tmp_path)

    async with anyio.create_task_group() as tg:
        tg.start_soon(
            WorkflowExecutor(
                wf,
                {},
                log_base_dir=tmp_path / "logs",
                approval_store=store,
            ).run
        )
        while not store.list_pending("approval-loop"):
            await anyio.sleep(0.05)
        tg.cancel_scope.cancel()

    request = store.list_pending("approval-loop")[0]
    store.decide(
        request.workflow_id,
        request.run_id,
        request.node_id,
        "approved",
        decided_by="alice",
    )

    resumed = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        approval_store=store,
    ).resume_from_approval(request)

    assert resumed is not None
    assert resumed.success
    assert [run.output for run in resumed.node_runs["print"]] == ["0", "1"]
    assert len(resumed.node_runs["after"]) == 1


async def test_notification_operation_interpolates_and_uses_adapter(tmp_path: Path) -> None:
    wf = _make_workflow("notify-flow")
    wf.add_operation(_bash_node("summary", "echo done"))
    wf.add_operation(
        GraphNode(
            node_id="notify",
            operation=NotificationOperation(
                type=OperationType.NOTIFICATION,
                title="Workflow {{workflow.id}}",
                body="Run {{run.id}} at {{run.logPath}}: {{summary.output}}",
            ),
        )
    )
    wf.then("summary", "notify")
    notifications = RecordingNotificationAdapter()

    result = await (
        WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            notification_adapter=notifications,
        )
        .with_trigger_context({"workflow_id": "notify-flow"})
        .run()
    )

    assert result.success
    assert len(notifications.notifications) == 1
    assert notifications.notifications[0].title == "Workflow notify-flow"
    assert result.log_path is not None
    assert notifications.notifications[0].body == (
        f"Run {result.log_path.name} at {result.log_path}: done\n"
    )


async def test_notification_resolves_typed_configuration_fields(tmp_path: Path) -> None:
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="typed-notification",
            name="Typed Notification",
            inputs={
                "port": WorkflowParameterConfig(type="number", default=2525),
                "tls": WorkflowParameterConfig(type="boolean", default=False),
                "attempts": WorkflowParameterConfig(type="number", default=3),
            },
            variables={
                "statuses": WorkflowVariableConfig(type="array", initial=[200, 202]),
                "allowlist": WorkflowVariableConfig(type="array", initial=["127.0.0.1"]),
                "headers": WorkflowVariableConfig(type="object", initial={"X-Workflow": "typed"}),
            },
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="notify",
            operation=NotificationOperation(
                type=OperationType.NOTIFICATION,
                smtp_port="{{inputs.port}}",
                headers="{{vars.headers}}",
                smtp_starttls="{{inputs.tls}}",
                timeout_seconds="{{inputs.port}}",
                retry=HttpRetryPolicy(
                    attempts="{{inputs.attempts}}",
                    backoff_seconds="{{inputs.attempts}}",
                    retry_on_statuses="{{vars.statuses}}",
                ),
                expected_statuses="{{vars.statuses}}",
                network_allowlist="{{vars.allowlist}}",
            ),
        )
    )
    notifications = RecordingNotificationAdapter()

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        notification_adapter=notifications,
    ).run()

    assert result.success
    sent = notifications.notifications[0]
    assert sent.smtp_port == 2525
    assert sent.headers == {"X-Workflow": "typed"}
    assert sent.smtp_starttls is False
    assert sent.timeout_seconds == 2525
    assert sent.retry.attempts == 3
    assert sent.retry.backoff_seconds == 3
    assert sent.retry.retry_on_statuses == [200, 202]
    assert sent.expected_statuses == [200, 202]
    assert sent.network_allowlist == ["127.0.0.1"]


async def test_approval_resolves_typed_configuration_fields(tmp_path: Path) -> None:
    wf = AgenticWorkflow(
        WorkflowConfig(
            id="typed-approval",
            name="Typed Approval",
            inputs={
                "timeout": WorkflowParameterConfig(type="number", default=0),
                "notify": WorkflowParameterConfig(type="boolean", default=False),
                "decision": WorkflowParameterConfig(default="reject"),
            },
            variables={"approvers": WorkflowVariableConfig(type="array", initial=["release-team"])},
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="approve",
            operation=ApprovalGateOperation(
                type=OperationType.APPROVAL_GATE,
                message="Continue?",
                timeout_seconds="{{inputs.timeout}}",
                timeout_decision="{{inputs.decision}}",
                approvers="{{vars.approvers}}",
                notify="{{inputs.notify}}",
            ),
        )
    )

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert result.node_outputs["approve"].data["approvers"] == ["release-team"]
    assert result.node_outputs["approve"].data["timeoutSeconds"] == 0


async def test_http_request_builds_request_and_extracts_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOFER_SECRET_API_TOKEN", "token-123")
    http = FakeHttpClient(
        [
            HttpResponse(
                status=201,
                headers={"Content-Type": "application/json"},
                body=b'{"id": 42, "url": "https://api.example.test/issues/42"}',
            )
        ]
    )
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method="POST",
                url="https://api.example.test/issues",
                headers={"Authorization": "{{secret.API_TOKEN}}"},
                params={"project": "gofer"},
                json={"title": "Bug"},
                expected_statuses=[201],
                response_mode="json",
                output_mapping={"issue_id": "json.id"},
                secret_fields=["Authorization"],
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        http_client=http,
    ).run()

    assert result.success
    assert len(http.requests) == 1
    request = http.requests[0]
    assert request.method == "POST"
    assert request.url == "https://api.example.test/issues?project=gofer"
    assert request.headers["Authorization"] == "token-123"
    assert request.body == b'{"title": "Bug"}'
    output = result.node_outputs["api"]
    assert output.data["status"] == 201
    assert output.data["selected"] == {"issue_id": 42}
    assert output.value == {"id": 42, "url": "https://api.example.test/issues/42"}
    assert result.log_path is not None
    assert "token-123" not in result.log_path.read_text()


async def test_http_request_retries_and_fails_on_unexpected_status(tmp_path: Path) -> None:
    http = FakeHttpClient(
        [
            HttpResponse(status=503, headers={}, body=b"try again"),
            HttpResponse(status=500, headers={}, body=b"nope"),
        ]
    )
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                url="https://api.example.test/status",
                retry=HttpRetryPolicy(attempts=2, retry_on_statuses=[503]),
                expected_statuses=[200],
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        http_client=http,
    ).run()

    assert not result.success
    assert len(http.requests) == 2
    assert result.node_outputs["api"].exit_code == 1
    assert result.node_outputs["api"].output == "nope"


async def test_http_request_masks_secret_url_params_and_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOFER_SECRET_WEBHOOK_URL", "https://hooks.example.test/send")
    monkeypatch.setenv("GOFER_SECRET_API_TOKEN", "token-123")
    http = FakeHttpClient(
        [
            HttpResponse(
                status=200,
                headers={"X-Api-Key": "token-123", "Content-Type": "application/json"},
                body=b'{"ok": true, "password": "returned-password", "echo": "token-123"}',
            )
        ]
    )
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method="POST",
                url="secret:WEBHOOK_URL",
                params={"token": "{{secret.API_TOKEN}}", "team": "ops"},
                json={"api_token": "{{secret.API_TOKEN}}", "message": "hi"},
                response_mode="json",
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        http_client=http,
    ).run()

    assert result.success
    assert http.requests[0].url == "https://hooks.example.test/send?token=token-123&team=ops"
    output = result.node_outputs["api"]
    assert output.data["url"] == "***"
    assert output.data["json"] == {
        "ok": True,
        "password": "***",
        "echo": "***",
    }
    assert output.output == '{"ok": true, "password": "***", "echo": "***"}'
    preview = cast(dict[str, object], output.data["responsePreview"])
    headers = cast(dict[str, object], preview["headers"])
    assert headers["X-Api-Key"] == "***"
    assert preview["json"] == {
        "ok": True,
        "password": "***",
        "echo": "***",
    }
    assert preview["body"] == '{"ok": true, "password": "***", "echo": "***"}'
    assert result.log_path is not None
    log_text = result.log_path.read_text()
    assert "https://hooks.example.test/send" not in log_text
    assert "token-123" not in log_text
    assert "returned-password" not in log_text


async def test_http_request_masks_secret_fields_in_raw_request_body(
    tmp_path: Path,
) -> None:
    http = FakeHttpClient([HttpResponse(status=200, headers={}, body=b"ok")])
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method="POST",
                url="https://api.example.test/login",
                body='{"password":"cleartext-secret","user":"doonk"}',
                secret_fields=["password"],
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        http_client=http,
    ).run()

    assert result.success
    assert http.requests[0].body == b'{"password":"cleartext-secret","user":"doonk"}'
    assert result.log_path is not None
    log_text = result.log_path.read_text()
    assert "cleartext-secret" not in log_text
    assert '\\"password\\": \\"***\\"' in log_text


async def test_http_request_masks_secret_fields_in_text_response_preview(
    tmp_path: Path,
) -> None:
    http = FakeHttpClient(
        [
            HttpResponse(
                status=200,
                headers={},
                body=b'{"password":"returned-secret","ok":true}',
            )
        ]
    )
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                url="https://api.example.test/login",
                response_mode="text",
                secret_fields=["password"],
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        http_client=http,
    ).run()

    assert result.success
    output = result.node_outputs["api"]
    assert output.output == '{"password":"***","ok":true}'
    preview = cast(dict[str, object], output.data["responsePreview"])
    assert preview["body"] == '{"password":"***","ok":true}'
    assert result.log_path is not None
    assert "returned-secret" not in result.log_path.read_text()


async def test_http_request_masks_configured_secret_echoed_under_other_key(
    tmp_path: Path,
) -> None:
    http = FakeHttpClient(
        [
            HttpResponse(
                status=200,
                headers={"Content-Type": "application/json"},
                body=b'{"echo":"cleartext-secret","ok":true}',
            )
        ]
    )
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method="POST",
                url="https://api.example.test/login",
                json={"password": "cleartext-secret", "user": "doonk"},
                response_mode="json",
                secret_fields=["password"],
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        http_client=http,
    ).run()

    assert result.success
    output = result.node_outputs["api"]
    assert output.data["json"] == {"echo": "***", "ok": True}
    assert output.output == '{"echo": "***", "ok": true}'
    preview = cast(dict[str, object], output.data["responsePreview"])
    assert preview["json"] == {"echo": "***", "ok": True}
    assert preview["body"] == '{"echo":"***","ok":true}'
    assert result.log_path is not None
    assert "cleartext-secret" not in result.log_path.read_text()


async def test_http_request_masks_trigger_secrets_in_initial_run_log(
    tmp_path: Path,
) -> None:
    http = FakeHttpClient([HttpResponse(status=200, headers={}, body=b"ok")])
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method="POST",
                url="{{trigger.callback_url}}",
                headers={"Authorization": "Bearer {{trigger.token}}"},
                json={
                    "password": "{{trigger.password}}",
                    "message": "hello",
                },
                secret_fields=["url", "Authorization", "password"],
            ),
        )
    )

    result = await (
        WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            http_client=http,
        )
        .with_trigger_context(
            {
                "callback_url": "https://hooks.example.test/callback/real-secret",
                "token": "trigger-token-123",
                "password": "trigger-password-123",
                "event": "created",
            }
        )
        .run()
    )

    assert result.success
    assert result.log_path is not None
    log_text = result.log_path.read_text()
    assert "https://hooks.example.test/callback/real-secret" not in log_text
    assert "trigger-token-123" not in log_text
    assert "trigger-password-123" not in log_text
    assert '"event": "created"' in log_text


async def test_failed_http_request_masks_secret_body_in_workflow_failure_reason(
    tmp_path: Path,
) -> None:
    http = FakeHttpClient(
        [
            HttpResponse(
                status=500,
                headers={"Content-Type": "application/json"},
                body=b'{"password":"returned-secret","ok":false}',
            )
        ]
    )
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                url="https://api.example.test/login",
                response_mode="text",
                expected_statuses=[200],
                secret_fields=["password"],
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        http_client=http,
    ).run()

    assert not result.success
    output = result.node_outputs["api"]
    assert output.output == '{"password":"***","ok":false}'
    assert output.error == '{"password":"***","ok":false}'
    assert result.log_path is not None
    log_text = result.log_path.read_text()
    assert "returned-secret" not in log_text
    assert 'failed due to node api failed: {"password":"***","ok":false}' in log_text


async def test_http_request_json_mode_invalid_json_returns_structured_failure(
    tmp_path: Path,
) -> None:
    http = FakeHttpClient(
        [
            HttpResponse(
                status=200,
                headers={"Content-Type": "application/json"},
                body=b"not-json",
            )
        ]
    )
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                url="https://api.example.test/status",
                response_mode="json",
                expected_statuses=[200],
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        http_client=http,
    ).run()

    assert not result.success
    output = result.node_outputs["api"]
    assert output.type == str(OperationType.HTTP_REQUEST)
    assert output.exit_code == 1
    assert output.output == "not-json"
    assert output.value == "not-json"
    assert output.data["status"] == 200
    assert output.data["headers"] == {"Content-Type": "application/json"}
    assert output.data["body"] == "not-json"
    assert output.data["json"] is None
    preview = cast(dict[str, object], output.data["responsePreview"])
    assert preview["body"] == "not-json"
    assert isinstance(output.error, str)
    assert output.error.startswith("Invalid JSON response:")
    assert result.log_path is not None
    assert "raised exception" not in result.log_path.read_text()


async def test_http_request_selected_secret_can_feed_downstream_request(
    tmp_path: Path,
) -> None:
    http = FakeHttpClient(
        [
            HttpResponse(
                status=200,
                headers={"Content-Type": "application/json"},
                body=b'{"access_token": "real-token"}',
            ),
            HttpResponse(status=200, headers={}, body=b"ok"),
        ]
    )
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="auth",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                url="https://api.example.test/auth",
                response_mode="json",
                output_mapping={"token": "json.access_token"},
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="use_token",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                url="https://api.example.test/resource",
                headers={"Authorization": "Bearer {{auth.data.selected.token}}"},
                secret_fields=["Authorization"],
            ),
        )
    )
    wf.graph.add_edge(
        "auth",
        "use_token",
        EdgeConfig(from_node="auth", to_node="use_token"),
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        http_client=http,
    ).run()

    assert result.success
    assert http.requests[1].headers["Authorization"] == "Bearer real-token"
    assert result.node_outputs["auth"].data["selected"] == {"token": "***"}
    preview = cast(dict[str, object], result.node_outputs["auth"].data["responsePreview"])
    assert preview["selected"] == {"token": "***"}
    assert result.log_path is not None
    resume_path = result.log_path.with_suffix(".resume.json")
    assert "real-token" not in resume_path.read_text(encoding="utf-8")

    resumed_http = FakeHttpClient([HttpResponse(status=200, headers={}, body=b"ok")])
    resumed = (
        await WorkflowExecutor(
            wf,
            {},
            log_base_dir=tmp_path / "logs",
            http_client=resumed_http,
        )
        .with_resume_options(ResumeOptions(run_id=result.log_path.name, from_node="use_token"))
        .run()
    )

    assert resumed.success
    assert resumed_http.requests[0].headers["Authorization"] == "Bearer real-token"


async def test_http_request_selected_output_can_drive_output_matches_edge(
    tmp_path: Path,
) -> None:
    http = FakeHttpClient(
        [
            HttpResponse(
                status=200,
                headers={"Content-Type": "application/json"},
                body=b'{"state": "ready"}',
            )
        ]
    )
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method="GET",
                url="https://api.example.test/status",
                response_mode="none",
                output_mapping={"state": "json.state"},
            ),
        )
    )
    wf.add_operation(_bash_node("next", "echo matched"))
    wf.graph.add_edge(
        "api",
        "next",
        EdgeConfig(
            from_node="api",
            to_node="next",
            condition=EdgeConditionType.OUTPUT_MATCHES,
            output_pattern="ready",
        ),
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        http_client=http,
    ).run()

    assert result.success
    assert result.node_outputs["api"].output == ""
    assert result.node_outputs["next"].output.strip() == "matched"


async def test_failed_http_request_selected_output_can_drive_output_matches_edge(
    tmp_path: Path,
) -> None:
    http = FakeHttpClient(
        [
            HttpResponse(
                status=500,
                headers={"Content-Type": "application/json"},
                body=b'{"error": {"code": "retryable"}}',
            )
        ]
    )
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                method="GET",
                url="https://api.example.test/status",
                response_mode="none",
                output_mapping={"code": "json.error.code"},
            ),
        )
    )
    wf.add_operation(_bash_node("recover", "echo matched failure"))
    wf.graph.add_edge(
        "api",
        "recover",
        EdgeConfig(
            from_node="api",
            to_node="recover",
            condition=EdgeConditionType.OUTPUT_MATCHES,
            output_pattern="retryable",
        ),
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        http_client=http,
    ).run()

    assert not result.success
    assert not result.node_outputs["api"].success
    assert result.node_outputs["api"].output == ""
    assert result.node_outputs["api"].data["selected"] == {"code": "retryable"}
    assert result.node_outputs["recover"].output.strip() == "matched failure"


async def test_http_request_retries_transport_errors(tmp_path: Path) -> None:
    http = FakeHttpClient(
        [
            TimeoutError("request timed out"),
            HttpResponse(status=200, headers={}, body=b"ok"),
        ]
    )
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                url="https://api.example.test/status",
                retry=HttpRetryPolicy(attempts=2),
                expected_statuses=[200],
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        http_client=http,
    ).run()

    assert result.success
    assert len(http.requests) == 2
    assert result.node_outputs["api"].output == "ok"


async def test_http_request_reports_final_transport_error_masked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOFER_SECRET_WEBHOOK_URL", "https://hooks.example.test/secret")
    http = FakeHttpClient(
        [
            TimeoutError("failed https://hooks.example.test/secret"),
            TimeoutError("failed https://hooks.example.test/secret"),
        ]
    )
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="api",
            operation=HttpRequestOperation(
                type=OperationType.HTTP_REQUEST,
                url="secret:WEBHOOK_URL",
                retry=HttpRetryPolicy(attempts=2),
            ),
        )
    )

    result = await WorkflowExecutor(
        wf,
        {},
        log_base_dir=tmp_path / "logs",
        http_client=http,
    ).run()

    assert not result.success
    assert len(http.requests) == 2
    output = result.node_outputs["api"]
    assert output.data["url"] == "***"
    assert "https://hooks.example.test/secret" not in output.output
    assert result.log_path is not None
    assert "https://hooks.example.test/secret" not in result.log_path.read_text()


async def test_agent_node_uses_subscription(tmp_path: Path) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text("Do something.")
    sub = FakeSubscription(output="agent output")

    wf = _make_workflow()
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )
    executor = WorkflowExecutor(wf, {"claude_code": sub}, log_base_dir=tmp_path / "logs")
    result = await executor.run()
    assert result.success
    assert "agent output" in result.node_outputs["agent-step"].output
    assert len(sub.calls) == 1


async def test_agent_input_mapping_can_read_trigger_event_path(tmp_path: Path) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text("Summarize {{file_path}}.")
    sub = FakeSubscription(output="done")

    wf = _make_workflow()
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
                input_mapping={"file_path": "trigger.events.0.path"},
            ),
        )
    )
    result = (
        await WorkflowExecutor(
            wf,
            {"claude_code": sub},
            log_base_dir=tmp_path / "logs",
        )
        .with_trigger_context(
            {
                "type": "file_watch",
                "events": [{"path": str(tmp_path / "input.txt"), "kind": "created"}],
            }
        )
        .run()
    )

    assert result.success
    assert str(tmp_path / "input.txt") in str(sub.calls[0]["prompt"])
    assert sub.calls[0]["extra_paths"] == []


async def test_agent_piped_absolute_path_does_not_expand_subscription_sandbox(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text("Summarize piped path.")
    external_file = tmp_path.parent / "piped-output-path.txt"
    external_file.write_text("secret", encoding="utf-8")
    sub = FakeSubscription(output="done")

    wf = _make_workflow()
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="producer",
            pipe_output=True,
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command=f"printf '%s' '{external_file}'",
                working_dir=tmp_path,
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )
    wf.then("producer", "agent-step")

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert result.success
    assert str(external_file) in str(sub.calls[0]["prompt"])
    assert sub.calls[0]["extra_paths"] == []


async def test_agent_trigger_events_fan_source_is_deprecated_and_runs_once(tmp_path: Path) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text("Summarize {{path}}.")
    sub = FakeSubscription(output="done")

    wf = _make_workflow()
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
                fan_source=TriggerEventsFanSource(type="trigger_events"),
            ),
        )
    )
    result = (
        await WorkflowExecutor(
            wf,
            {"claude_code": sub},
            log_base_dir=tmp_path / "logs",
        )
        .with_trigger_context(
            {
                "type": "file_watch",
                "events": [
                    {"path": str(tmp_path / "a.txt"), "kind": "created"},
                    {"path": str(tmp_path / "b.txt"), "kind": "created"},
                ],
            }
        )
        .run()
    )

    assert result.success
    assert len(sub.calls) == 1


async def test_loop_count_runs_downstream_agent_once_per_item(tmp_path: Path) -> None:
    prompt = tmp_path / "p.md"
    prompt.write_text("Process item {{index}}.")
    sub = FakeSubscription(output="done")

    wf = _make_workflow()
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=CountFanSource(type="count", count=3, max_concurrency=1),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )
    wf.then("loop", "agent-step")

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert result.success
    assert len(sub.calls) == 3
    prompts = [str(call["prompt"]) for call in sub.calls]
    assert "Process item 0." in prompts[0]
    assert "Process item 1." in prompts[1]
    assert "Process item 2." in prompts[2]
    assert len(result.node_runs["agent-step"]) == 3


async def test_agent_loop_child_with_explicit_inputs_does_not_prepend_loop_json(
    tmp_path: Path,
) -> None:
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    (files_dir / "ticket.md").write_text("ticket body")
    prompt = tmp_path / "p.md"
    prompt.write_text("Implement the following ticket.\n{{content}}")
    sub = FakeSubscription(output="done")

    wf = _make_workflow()
    wf.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="loop",
            pipe_output=True,
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(
                    type="directory",
                    path=files_dir,
                    include_content=True,
                ),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="agent-step",
            inputs={"content": "loop.current.file_content"},
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="bot",
                prompt_path=prompt,
                working_dir=tmp_path,
            ),
        )
    )
    wf.then("loop", "agent-step")

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert result.success
    agent_prompt = envelope_request(sub.calls[0]["prompt"])
    assert agent_prompt == "Implement the following ticket.\nticket body"
    assert agent_prompt.count("ticket body") == 1
    assert "file_path" not in agent_prompt


async def test_loop_runs_entire_child_chain_before_next_item(tmp_path: Path) -> None:
    prompt_a = tmp_path / "a.md"
    prompt_b = tmp_path / "b.md"
    prompt_a.write_text("A{{index}}")
    prompt_b.write_text("B{{index}}")
    sub = FakeSubscription(output="done")

    wf = _make_workflow()
    wf.register_agent(
        AgentConfig(
            agent_id="a",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt_a,
        )
    )
    wf.register_agent(
        AgentConfig(
            agent_id="b",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt_b,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=CountFanSource(type="count", count=3, max_concurrency=1),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="a",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="a",
                prompt_path=prompt_a,
                working_dir=tmp_path,
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="b",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="b",
                prompt_path=prompt_b,
                working_dir=tmp_path,
            ),
        )
    )
    wf.then("loop", "a")
    wf.then("a", "b")

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert result.success
    assert [envelope_request(call["prompt"]).splitlines()[0] for call in sub.calls] == [
        "A0",
        "B0",
        "A1",
        "B1",
        "A2",
        "B2",
    ]


async def test_loop_pipe_output_sends_current_item_to_direct_child(tmp_path: Path) -> None:
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    (files_dir / "a.txt").write_text("alpha")
    (files_dir / "b.txt").write_text("bravo")

    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="loop",
            pipe_output=True,
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(type="directory", path=files_dir),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="print",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="cat"),
        )
    )
    wf.then("loop", "print")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert result.node_outputs["loop"].data["source_type"] == "directory"
    assert result.node_outputs["loop"].data["source_path"] == str(files_dir)
    assert result.node_outputs["loop"].data["count"] == 2
    outputs = [json.loads(run.output) for run in result.node_runs["print"]]
    assert [output["file_name"] for output in outputs] == ["a.txt", "b.txt"]
    assert [Path(output["file_path"]).name for output in outputs] == ["a.txt", "b.txt"]
    assert [output["file_stem"] for output in outputs] == ["a", "b"]
    assert [output["file_extension"] for output in outputs] == [".txt", ".txt"]
    assert [output["directory"] for output in outputs] == [str(files_dir), str(files_dir)]
    assert all(not isinstance(output, list) for output in outputs)


async def test_loop_child_bash_receives_current_item_env(tmp_path: Path) -> None:
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    (files_dir / "a.txt").write_text("alpha")

    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="loop",
            pipe_output=True,
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=DirectoryFanSource(type="directory", path=files_dir),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="print",
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND,
                command='printf "%s\\n%s\\n" "$FILE_NAME" "$FILE_PATH"',
            ),
        )
    )
    wf.then("loop", "print")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert result.node_outputs["print"].output.splitlines() == [
        "a.txt",
        str(files_dir / "a.txt"),
    ]


async def test_after_loop_edge_runs_once_after_loop_body_finishes(tmp_path: Path) -> None:
    prompt_a = tmp_path / "a.md"
    prompt_after = tmp_path / "after.md"
    prompt_a.write_text("A{{index}}")
    prompt_after.write_text("after")
    sub = FakeSubscription(output="done")

    wf = _make_workflow()
    for agent_id, prompt in {"a": prompt_a, "after": prompt_after}.items():
        wf.register_agent(
            AgentConfig(
                agent_id=agent_id,
                subscription="claude_code",
                working_dir=tmp_path,
                prompt_path=prompt,
            )
        )
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=CountFanSource(type="count", count=2),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="a",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="a",
                prompt_path=prompt_a,
                working_dir=tmp_path,
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="after",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="after",
                prompt_path=prompt_after,
                working_dir=tmp_path,
            ),
        )
    )
    wf.then("loop", "a")
    wf.then(
        "loop",
        "after",
        EdgeConfig(
            from_node="loop",
            to_node="after",
            condition=EdgeConditionType.AFTER_LOOP,
        ),
    )

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert result.success
    assert [envelope_request(call["prompt"]).splitlines()[0] for call in sub.calls] == [
        "A0",
        "A1",
        "after",
    ]
    assert len(result.node_runs["after"]) == 1
    assert result.log_path is not None
    payload = json.loads(result.log_path.with_suffix(".events.json").read_text())
    assert {
        "from": "loop",
        "to": "after",
        "condition": "after_loop",
        "outputPattern": "",
        "matched": True,
    } in [
        event["data"]
        for event in payload["events"]
        if event["nodeId"] == "loop" and event["status"] == "edge_decision"
    ]


async def test_loop_break_stops_loop_without_failing(tmp_path: Path) -> None:
    wf = _make_workflow()
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=InfiniteFanSource(type="infinite"),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="break",
            operation=BreakOperation(type=OperationType.BREAK, message="enough"),
        )
    )
    wf.then("loop", "break")

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.success
    assert len(result.node_runs["break"]) == 1
    assert result.node_runs["break"][0].terminal_status == "break"


async def test_after_loop_edge_runs_after_break(tmp_path: Path) -> None:
    prompt_after = tmp_path / "after.md"
    prompt_after.write_text("after")
    sub = FakeSubscription(output="done")

    wf = _make_workflow()
    wf.register_agent(
        AgentConfig(
            agent_id="after",
            subscription="claude_code",
            working_dir=tmp_path,
            prompt_path=prompt_after,
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="loop",
            operation=LoopOperation(
                type=OperationType.LOOP,
                source=InfiniteFanSource(type="infinite"),
            ),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="break",
            operation=BreakOperation(type=OperationType.BREAK, message="enough"),
        )
    )
    wf.add_operation(
        GraphNode(
            node_id="after",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="after",
                prompt_path=prompt_after,
                working_dir=tmp_path,
            ),
        )
    )
    wf.then("loop", "break")
    wf.then(
        "loop",
        "after",
        EdgeConfig(
            from_node="loop",
            to_node="after",
            condition=EdgeConditionType.AFTER_LOOP,
        ),
    )

    result = await WorkflowExecutor(
        wf,
        {"claude_code": sub},
        log_base_dir=tmp_path / "logs",
    ).run()

    assert result.success
    assert len(result.node_runs["break"]) == 1
    assert len(result.node_runs["after"]) == 1
    assert envelope_request(sub.calls[0]["prompt"]).splitlines()[0] == "after"


async def test_workflow_run_writes_success_log(tmp_path: Path) -> None:
    wf = _make_workflow("logged")
    wf.add_operation(_bash_node("echo", "echo hello"))

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.log_path is not None
    assert result.log_path.parent == tmp_path / "logs" / "logged"
    assert result.log_path.exists()
    lines = result.log_path.read_text().splitlines()
    assert lines[0].endswith(" - logged started successfully")
    assert "echo - stdout:" in result.log_path.read_text()
    assert "hello" in result.log_path.read_text()
    assert lines[-1].endswith(" - INFO - logged completed successfully")


async def test_workflow_run_writes_failure_log(tmp_path: Path) -> None:
    wf = _make_workflow("broken")
    wf.add_operation(_bash_node("fail", "echo bad >&2; exit 3"))

    result = await WorkflowExecutor(wf, {}, log_base_dir=tmp_path / "logs").run()

    assert result.log_path is not None
    text = result.log_path.read_text()
    lines = text.splitlines()
    assert not result.success
    assert lines[0].endswith(" - broken started successfully")
    assert "fail - stderr:" in text
    assert "bad" in text
    assert "broken failed due to node fail failed" in lines[-1]
