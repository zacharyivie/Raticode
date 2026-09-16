from __future__ import annotations

import os
from pathlib import Path

import pytest

from gofer.core.agent import AgentConfig
from gofer.core.graph import GraphNode
from gofer.core.health import _configured_provider_diagnostics, _workflow_provider_diagnostics
from gofer.core.operations import AgentOperation, OperationType
from gofer.core.planner import build_execution_plan
from gofer.core.provider_capabilities import resolve_provider_executable
from gofer.core.workflow import AgenticWorkflow, WorkflowConfig
from gofer.rattish.preflight import (
    DEFAULT_PREFLIGHT_CHECKS,
    PreflightContext,
    _agent_provider_available,
)
from gofer.rattish.runtime import DEFAULT_NODE_HANDLERS
from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.codex import CodexSubscription


@pytest.mark.parametrize("provider,binary", [("codex", "codex"), ("claude_code", "claude")])
@pytest.mark.parametrize("location", ["path", "nvm", "missing", "not-executable"])
def test_workflow_provider_discovery_agrees_across_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider, binary, location
) -> None:
    if location == "not-executable" and os.name == "nt":
        pytest.skip("Windows does not enforce POSIX executable permission bits")
    nvm = tmp_path / "nvm"
    executable = nvm / "versions/node/v22.12.0/bin" / binary
    executable.parent.mkdir(parents=True)
    if location != "missing":
        executable.touch()
        executable.chmod(0o644 if location == "not-executable" else 0o755)
    monkeypatch.setenv("NVM_DIR", str(nvm))
    monkeypatch.setattr(
        "gofer.core.provider_capabilities.shutil.which",
        lambda name: "/usr/bin/" + binary if location == "path" and name == binary else None,
    )
    monkeypatch.setattr("gofer.core.health._configured_providers_in_data_dir", lambda _: {provider})
    workflow = AgenticWorkflow(WorkflowConfig(id="provider-check", name="Provider check"))
    workflow.register_agent(
        AgentConfig(agent_id="agent", subscription=provider, working_dir=tmp_path)
    )
    workflow.add_operation(
        GraphNode(
            node_id="agent",
            operation=AgentOperation(
                type=OperationType.AGENT, agent_id="agent", working_dir=tmp_path
            ),
        )
    )
    expected = (
        "/usr/bin/" + binary
        if location == "path"
        else str(executable)
        if location == "nvm"
        else None
    )
    assert resolve_provider_executable(provider) == expected
    checks = _configured_provider_diagnostics(tmp_path) + _workflow_provider_diagnostics(workflow)
    assert len(checks) == 2
    for check in checks:
        assert check.detail == {"binary": binary, "path": expected}
        assert (check.severity == "ok") == (expected is not None)
    assert build_execution_plan(workflow)["providerRequirements"][0]["available"] == (
        expected is not None
    )
    subscription = CodexSubscription() if provider == "codex" else ClaudeCodeSubscription()
    assert subscription.is_available() == (expected is not None)
    context = PreflightContext(
        tmp_path,
        tmp_path,
        {provider: subscription},
        DEFAULT_NODE_HANDLERS,
        DEFAULT_PREFLIGHT_CHECKS,
    )
    node = {"resolutions": {"provider": {"provider_id": provider}}}
    assert (_agent_provider_available(node, context) is None) == (expected is not None)
    assert subscription._build_command("hello", [], [])[0] == (expected or binary)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,binary", [("codex", "codex"), ("claude_code", "claude")])
async def test_workflow_launch_adds_nvm_node_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider, binary
) -> None:
    executable = tmp_path / "versions/node/v22.12.0/bin" / binary
    executable.parent.mkdir(parents=True)
    executable.touch()
    executable.chmod(0o755)
    monkeypatch.setenv("NVM_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr("gofer.core.provider_capabilities.shutil.which", lambda _: None)
    calls = []

    async def stream(command, **kwargs):
        calls.append((command, kwargs))
        yield {"type": "exit", "stream": None, "text": "", "returncode": 0}

    monkeypatch.setattr("gofer.subscriptions.base.stream_subprocess", stream)
    subscription = CodexSubscription() if provider == "codex" else ClaudeCodeSubscription()
    await subscription.execute("hello", tmp_path, [], [], {})
    assert calls[0][0][0] == str(executable)
    assert calls[0][1]["env"]["PATH"].split(os.pathsep)[0] == str(executable.parent)
