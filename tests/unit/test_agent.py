from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from gofer.core.agent import Agent, AgentConfig, AgentResult
from gofer.subscriptions.base import Subscription
from tests.conftest import FakeSubscription


@pytest.fixture
def prompt_file(tmp_path: Path) -> Path:
    p = tmp_path / "prompt.md"
    p.write_text("Summarize {{repo}}.")
    return p


@pytest.fixture
def agent_config(prompt_file: Path, tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        agent_id="test-agent",
        subscription="claude_code",
        working_dir=tmp_path,
        prompt_path=prompt_file,
    )


async def test_agent_delegates_to_subscription(agent_config: AgentConfig, tmp_path: Path) -> None:
    sub = FakeSubscription(output="summary output")
    agent = Agent(agent_config, sub)
    result = await agent.run({"repo": "myrepo"})
    assert result.success
    assert result.output == "summary output"
    assert result.agent_id == "test-agent"
    assert len(sub.calls) == 1


async def test_agent_passes_every_execute_argument(agent_config: AgentConfig) -> None:
    received: list[dict[str, object]] = []

    class Recording(FakeSubscription):
        async def execute(self, *args: object, **kwargs: object) -> AgentResult:
            received.append(kwargs)
            return await super().execute(*args, **kwargs)  # type: ignore[arg-type]

    await Agent(agent_config, Recording()).run({"repo": "myrepo"})

    expected = set(inspect.signature(Subscription.execute).parameters) - {"self"}
    assert set(received[0]) == expected
    assert received[0]["timeout"] is None
    assert received[0]["provider_settings"] is None


async def test_agent_interpolates_prompt(agent_config: AgentConfig) -> None:
    sub = FakeSubscription()
    agent = Agent(agent_config, sub)
    await agent.run({"repo": "awesome-project"})
    assert "awesome-project" in str(sub.calls[0]["prompt"])


async def test_agent_includes_memory_in_prompt(agent_config: AgentConfig) -> None:
    sub = FakeSubscription()
    agent = Agent(agent_config, sub)
    result = await agent.run(
        {"repo": "awesome-project"},
        memory=[
            {"role": "user", "body": "Previous question"},
            {"role": "assistant", "body": "Previous answer"},
        ],
    )

    prompt = str(sub.calls[0]["prompt"])
    assert "Previous conversation:" in prompt
    assert "Previous question" in prompt
    assert "Previous answer" in prompt
    assert "Current request:" in prompt
    assert "awesome-project" in prompt
    assert result.prompt == prompt
    assert result.current_prompt == "Summarize awesome-project."


async def test_agent_context_paths_do_not_expand_subscription_sandbox(
    agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    external_dir = tmp_path.parent / "ticket backlog"
    external_dir.mkdir(exist_ok=True)
    external_file = external_dir / "ticket.txt"
    external_file.write_text("ticket", encoding="utf-8")

    sub = FakeSubscription()
    agent = Agent(agent_config, sub)

    await agent.run(
        {
            "repo": "awesome-project",
            "_piped_input": str(external_dir),
            "file_path": str(external_file),
        }
    )

    assert str(external_dir) in str(sub.calls[0]["prompt"])
    assert sub.calls[0]["extra_paths"] == []


async def test_agent_passes_configured_extra_paths_to_subscription(
    agent_config: AgentConfig,
    tmp_path: Path,
) -> None:
    external_dir = tmp_path.parent / "shared context"
    external_dir.mkdir(exist_ok=True)
    config = agent_config.model_copy(update={"extra_paths": [external_dir]})

    sub = FakeSubscription()
    agent = Agent(config, sub)

    await agent.run({"repo": "awesome-project"})

    assert sub.calls[0]["extra_paths"] == [external_dir.resolve()]


async def test_agent_failure_propagates(agent_config: AgentConfig) -> None:
    sub = FakeSubscription(output="error", exit_code=1)
    agent = Agent(agent_config, sub)
    result = await agent.run()
    assert not result.success
    assert result.exit_code == 1
