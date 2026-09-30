from __future__ import annotations

from pathlib import Path

from gofer.core.provider_capabilities import resolve_provider_executable
from gofer.core.provider_permissions import provider_permission_args
from gofer.core.provider_profiles import ResolvedProviderSettings
from gofer.subscriptions.base import Subscription


class ClaudeCodeSubscription(Subscription):
    provider = "claude_code"

    def _parse_provider_output(self, stdout: str, stderr: str) -> tuple[str, dict[str, object]]:
        from gofer.subscriptions.base import _json_payloads, _message_from_payloads
        from gofer.subscriptions.usage import provider_payload_usage

        payloads = _json_payloads(stdout) + _json_payloads(stderr)
        return (
            _message_from_payloads(payloads) or stdout or stderr,
            provider_payload_usage("claude_code", payloads),
        )

    def _build_command(
        self,
        prompt: str,
        tools: list[str],
        mcp_servers: list[str],
        extra_paths: list[Path] | None = None,
        provider_settings: ResolvedProviderSettings | None = None,
    ) -> list[str]:
        _validate_claude_settings(provider_settings)
        cmd = [
            resolve_provider_executable("claude_code") or "claude",
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if provider_settings:
            if provider_settings.model:
                cmd += ["--model", provider_settings.model]
            if provider_settings.effort:
                cmd += ["--effort", provider_settings.effort]
            cmd += provider_permission_args("claude_code", provider_settings.approval_mode)
            cmd += provider_settings.extra_args
        for path in extra_paths or []:
            cmd += ["--add-dir", str(path)]
        cmd += ["-p", prompt]
        for tool in [*(provider_settings.tools if provider_settings else []), *tools]:
            cmd += ["--allowedTools", tool]
        for server in [
            *(provider_settings.mcp_servers if provider_settings else []),
            *mcp_servers,
        ]:
            cmd += ["--mcp-server", server]
        return cmd

    def is_available(self) -> bool:
        return resolve_provider_executable("claude_code") is not None


def _validate_claude_settings(settings: ResolvedProviderSettings | None) -> None:
    if settings is None:
        return
    if settings.subscription != "claude_code":
        raise ValueError(
            f"Claude Code subscription cannot run provider profile for '{settings.subscription}'"
        )
    if settings.sandbox_mode not in (None, "default"):
        raise ValueError("Claude Code profiles do not support sandbox_mode")
