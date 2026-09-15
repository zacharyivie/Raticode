from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gofer.core.agent import AgentResult
from gofer.core.provider_profiles import ResolvedProviderSettings
from gofer.rattish.artifacts import compile_rattish_file
from gofer.rattish.diagnostics import RattishCompileError
from gofer.rattish.preflight import run_preflight
from gofer.rattish.workflow_runtime import execute_workflow
from gofer.rattish.workspaces import create_registered_workflow
from gofer.subscriptions.base import Subscription


class FakeAgentSubscription(Subscription):
    def __init__(self, outputs: list[str], *, exit_code: int = 0) -> None:
        self.outputs = list(outputs)
        self.exit_code = exit_code
        self.calls: list[dict[str, Any]] = []

    def _build_command(
        self,
        prompt: str,
        tools: list[str],
        mcp_servers: list[str],
        extra_paths: list[Path] | None = None,
        provider_settings: ResolvedProviderSettings | None = None,
    ) -> list[str]:
        _ = prompt, tools, mcp_servers, extra_paths, provider_settings
        return ["fake-agent"]

    def is_available(self) -> bool:
        return True

    async def execute(
        self,
        prompt: str,
        working_dir: Path,
        tools: list[str],
        mcp_servers: list[str],
        env: dict[str, str],
        timeout: float | None = None,
        cancel_event: Any | None = None,
        extra_paths: list[Path] | None = None,
        max_output_bytes: int | None = None,
        on_thought: Callable[[str], None] | None = None,
        provider_settings: ResolvedProviderSettings | None = None,
    ) -> AgentResult:
        _ = cancel_event, on_thought
        self.calls.append(
            {
                "prompt": prompt,
                "working_dir": working_dir,
                "tools": tools,
                "mcp_servers": mcp_servers,
                "env": env,
                "timeout": timeout,
                "extra_paths": extra_paths,
                "max_output_bytes": max_output_bytes,
                "provider_settings": provider_settings,
            }
        )
        output = self.outputs.pop(0)
        return AgentResult(
            agent_id="",
            success=self.exit_code == 0,
            output=output,
            exit_code=self.exit_code,
            duration_seconds=0,
            message=output,
        )


def write_agent_source(tmp_path: Path, *, repair_attempts: int = 0) -> Path:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        f"""Rattish: 1

Workflow:
  name: Agent runtime
  inputs:
    topic:
      schema: {{"type": "string"}}
      required: true

Node review:
  type: agent
  provider: codex
  prompt: Review {{{{topic}}}}
  tools:
    - read
  repair-attempts: {repair_attempts}
  output-schema: {{
    "type": "object",
    "properties": {{"approved": {{"type": "boolean"}}}},
    "required": ["approved"],
    "additionalProperties": false
  }}
  with:
    topic: input.topic
""",
        encoding="utf-8",
    )
    return source


@pytest.mark.anyio
async def test_agent_source_compiles_preflights_and_executes_structured_output(
    tmp_path: Path,
) -> None:
    source = write_agent_source(tmp_path)
    artifact = compile_rattish_file(source, data_dir=tmp_path / "data")
    subscription = FakeAgentSubscription(['{"approved":true}'])

    preflight = run_preflight(
        artifact.ir,
        data_dir=tmp_path / "data",
        subscriptions={"codex": subscription},
    )
    result = await execute_workflow(
        artifact.ir,
        workflow_inputs={"topic": "the API"},
        subscriptions={"codex": subscription},
        data_dir=tmp_path / "data",
    )

    assert preflight.ready
    assert result.outcome == "pass"
    assert result.latest_node_outputs == {"review": {"approved": True}}
    assert "Review the API" in subscription.calls[0]["prompt"]
    assert "Return only one JSON value" in subscription.calls[0]["prompt"]
    settings = subscription.calls[0]["provider_settings"]
    assert isinstance(settings, ResolvedProviderSettings)
    assert settings.model == "gpt-5.6-sol"
    assert settings.effort == "high"


@pytest.mark.anyio
async def test_agent_repairs_invalid_structured_output_without_rerunning_graph_node(
    tmp_path: Path,
) -> None:
    source = write_agent_source(tmp_path, repair_attempts=1)
    artifact = compile_rattish_file(source, data_dir=tmp_path / "data")
    subscription = FakeAgentSubscription(["not json", '{"approved":false}'])

    result = await execute_workflow(
        artifact.ir,
        workflow_inputs={"topic": "repair"},
        subscriptions={"codex": subscription},
        data_dir=tmp_path / "data",
    )

    assert result.outcome == "pass"
    assert len(result.runs) == 1
    assert len(subscription.calls) == 2
    assert result.latest_node_outputs["review"] == {"approved": False}
    assert "previous response did not satisfy" in subscription.calls[1]["prompt"]


@pytest.mark.anyio
async def test_registered_workflow_agent_memory_is_stored_in_its_workspace(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "data"
    workflow = create_registered_workflow(project, "Agent memory", registry_dir=data_dir)
    workflow.entrypoint.write_text(
        """Rattish: 1
Workflow:
  name: Agent memory
Node review:
  type: agent
  provider: codex
  prompt: Remember this
  memory: all
""",
        encoding="utf-8",
    )
    artifact = compile_rattish_file(workflow.entrypoint, data_dir=data_dir)

    result = await execute_workflow(
        artifact.ir,
        subscriptions={"codex": FakeAgentSubscription(["remembered"])},
        data_dir=data_dir,
    )

    assert result.outcome == "pass"
    assert (workflow.workflow_root / "agent-memory" / "review.json").is_file()
    assert not (data_dir / "radish" / "agent-memory" / workflow.workflow_id).exists()


def test_agent_preflight_reports_provider_and_prompt_resources(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: Missing resources
Node review:
  type: agent
  provider: codex
  prompt-path: missing.md
""",
        encoding="utf-8",
    )
    artifact = compile_rattish_file(source, data_dir=tmp_path / "data")

    preflight = run_preflight(
        artifact.ir,
        data_dir=tmp_path / "data",
        subscriptions={},
    )

    assert not preflight.ready
    assert {item.code for item in preflight.diagnostics} == {
        "RATTISH_PREFLIGHT_PROVIDER_UNAVAILABLE",
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
    }


def test_agent_compiler_rejects_removed_configuration(
    tmp_path: Path,
) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: Advanced agent configuration
Node review:
  type: agent
  provider: codex
  llm-budget: {"max_agent_calls": 1}
""",
        encoding="utf-8",
    )
    with pytest.raises(RattishCompileError) as exc_info:
        compile_rattish_file(source, data_dir=tmp_path / "data")

    assert any(item.code == "RATTISH_UNKNOWN_FIELD" for item in exc_info.value.diagnostics)
