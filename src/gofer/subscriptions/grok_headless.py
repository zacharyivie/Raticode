"""Grok Build's headless tool filtering for isolated Rem conversation decisions.

ACP remains the session transport for delegated work. Root headless flags are
not forwarded by ACP, so conversation decisions use the official headless CLI.
https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/14-headless-mode.md
"""

from __future__ import annotations

from pathlib import Path

from gofer.core.provider_capabilities import resolve_provider_executable
from gofer.core.provider_profiles import ResolvedProviderSettings, validate_provider_settings
from gofer.subscriptions.base import Subscription


class GrokConversationSubscription(Subscription):
    provider = "grok"
    tool_free = True

    def is_available(self) -> bool:
        return resolve_provider_executable("grok") is not None

    def _prompt_file_instruction(self, prompt_path: Path) -> str:
        # The CLI itself reads this file before generation; no model tool call.
        return str(prompt_path)

    def _build_command(
        self,
        prompt: str,
        tools: list[str],
        mcp_servers: list[str],
        extra_paths: list[Path] | None = None,
        provider_settings: ResolvedProviderSettings | None = None,
    ) -> list[str]:
        if tools or mcp_servers or extra_paths:
            raise ValueError("Rem conversation cannot receive tools or workspace grants")
        if provider_settings:
            if provider_settings.subscription != self.provider:
                raise ValueError("Provider profile belongs to another CLI")
            validate_provider_settings(provider_settings)
        binary = resolve_provider_executable("grok")
        if not binary:
            raise ValueError("'grok' CLI is not available on PATH")
        command = [
            binary,
            "--prompt-file",
            prompt,
            "--output-format",
            "streaming-messages-json",
            "--tools",
            "",
            "--no-subagents",
            "--disable-web-search",
            "--permission-mode",
            "dontAsk",
        ]
        # Grok retains MCP meta-tools even with an empty built-in allowlist.
        for rule in ("Bash", "Edit", "Write", "Read", "Grep", "WebFetch", "MCPTool"):
            command += ["--deny", rule]
        if provider_settings:
            if provider_settings.model and provider_settings.model != "cli-default":
                command += ["--model", provider_settings.model]
            if provider_settings.effort and provider_settings.effort != "cli-default":
                command += ["--reasoning-effort", provider_settings.effort]
        return command
