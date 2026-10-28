"""Tool-free structured conversation decisions, isolated from coding workspaces."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from gofer.core.agent import Agent, AgentConfig
from gofer.core.prompt_envelope import AgentResources, resource_cli_args
from gofer.core.provider_capabilities import CLI_PROVIDERS
from gofer.core.provider_profiles import resolve_provider_settings
from gofer.subscriptions.antigravity import AntigravitySubscription
from gofer.subscriptions.base import Subscription
from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.cli_providers import CliSubscription
from gofer.subscriptions.codex import CodexSubscription
from gofer.subscriptions.grok_headless import GrokConversationSubscription


def generate_decision(
    prompt: str,
    config: dict[str, Any],
    cancel: threading.Event,
    data_dir: Path,
) -> dict[str, Any]:
    name = config.get("provider", "codex")
    if name not in CLI_PROVIDERS:
        raise ValueError(f"Unknown provider '{name}'")
    settings = resolve_provider_settings(
        agent_subscription=name,
        agent_model=config.get("model") if config.get("model") != "cli-default" else None,
        agent_effort=config.get("effort") or None,
        profile_name=None,
        data_dir=data_dir,
    )
    with tempfile.TemporaryDirectory(prefix="raticode-rem-conversation-") as directory:
        root = Path(directory)
        extra: list[str] = []
        subscription: Subscription
        if name == "codex":
            extra = resource_cli_args(name, AgentResources.none(), root)
            extra += [
                "-c",
                'approval_policy="never"',
                # resource_cli_args turns image viewing on with file reading.
                "-c",
                "tools.view_image=false",
                "-c",
                "features.plugins=false",
                "-c",
                "features.hooks=false",
                "-c",
                "features.goals=false",
                "-c",
                "features.skill_mcp_dependency_install=false",
            ]
            subscription = CodexSubscription()
            subscription.prompt_via_stdin = True
        elif name == "claude_code":
            extra = ["--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
            subscription = ClaudeCodeSubscription()
            subscription.prompt_via_stdin = True
        elif name == "antigravity":
            subscription = AntigravitySubscription(tool_free=True)
        elif name == "grok":
            subscription = GrokConversationSubscription()
        else:
            subscription = CliSubscription(
                name,
                resources=AgentResources.none(),
                read_only=True,
                tool_free=True,
            )
        settings = settings.model_copy(
            update={
                "tools": [],
                "mcp_servers": [],
                "extra_args": extra,
                "sandbox_mode": "read-only" if name == "codex" else None,
                "approval_mode": "dontAsk"
                if name == "claude_code"
                else "cli-managed"
                if name in {"antigravity", "grok"}
                else None,
            }
        )
        result = asyncio.run(
            Agent(
                AgentConfig(agent_id="Rem", subscription=name, working_dir=root), subscription
            ).run(
                prompt_override=prompt,
                provider_settings=settings,
                cancel_event=cancel,
                timeout=180,
                max_output_bytes=100000,
            )
        )
    if cancel.is_set():
        raise InterruptedError("Rem stopped")
    if not result.success:
        raise ValueError(result.message or result.output or "Rem could not reply")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (result.message or result.output).strip())
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Rem did not return a structured decision")
    return value
