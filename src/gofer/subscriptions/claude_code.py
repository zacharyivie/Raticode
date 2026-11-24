from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from gofer.core.provider_capabilities import resolve_provider_executable
from gofer.core.provider_permissions import provider_permission_args
from gofer.core.provider_profiles import ResolvedProviderSettings
from gofer.core.usage_context import note_cost_cap, run_cost_budget
from gofer.subscriptions.base import Subscription

# Claude Code checks --max-budget-usd after each model call, so one call can go past it.
# Starting it a little under the run's remaining budget leaves room for that call.
BUDGET_HEADROOM = 0.10
# Claude Code ignores a cap of 0, so a spent budget still passes the smallest cap.
_MIN_BUDGET_USD = 0.0001


def claude_print_input(command: list[str]) -> tuple[list[str], bytes]:
    """Deliver print-mode prompts intact without OS command-line size limits."""
    index = command.index("-p")
    return [*command[:index], *command[index + 2 :]], command[index + 1].encode("utf-8")


# The result subtype Claude Code ends with when --max-budget-usd stops it.
BUDGET_RESULT = "error_max_budget_usd"


class ClaudeCodeSubscription(Subscription):
    provider = "claude_code"
    # Print mode cannot read a prompt file outside the workspace without extra permissions.
    prompt_via_stdin = True

    def _parse_provider_output(self, stdout: str, stderr: str) -> tuple[str, dict[str, object]]:
        from gofer.subscriptions.base import _json_payloads, _message_from_payloads
        from gofer.subscriptions.usage import provider_payload_usage

        payloads = _json_payloads(stdout) + _json_payloads(stderr)
        metadata = provider_payload_usage("claude_code", payloads)
        if any(
            payload.get("type") == "result" and payload.get("subtype") == BUDGET_RESULT
            for payload in payloads
        ):
            metadata["stopped_at_cost_cap"] = True
        return _message_from_payloads(payloads) or stdout or stderr, metadata

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
        if not self.prompt_via_stdin:
            cmd += ["-p", prompt]
        for tool in [*(provider_settings.tools if provider_settings else []), *tools]:
            cmd += ["--allowedTools", tool]
        for server in [
            *(provider_settings.mcp_servers if provider_settings else []),
            *mcp_servers,
        ]:
            # An agent's MCP servers are names of servers Claude Code already knows.
            # Claude Code has no option that picks a server by name (it rejects
            # --mcp-server); allowing the server's tools is what lets a print-mode
            # run use them.
            cmd += ["--allowedTools", mcp_tool_rule(server)]
        budget = run_cost_budget()
        if budget is not None:
            cap = claude_budget_cap(budget.remaining_usd)
            configured = _configured_budget(
                provider_settings.extra_args if provider_settings else []
            )
            # A smaller cap the profile already sets stays; it isn't the run's limit.
            if configured is None or cap < configured:
                cmd += ["--max-budget-usd", f"{cap:.4f}"]
                note_cost_cap(cap, budget)
        return cmd

    def _live_usage(self, payload: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
        """The turn's tokens so far: the latest usage of each model message, added up."""
        from gofer.subscriptions.usage import normalize_usage

        if payload.get("type") != "assistant":
            return None
        message = payload.get("message")
        if not isinstance(message, dict):
            return None
        identity, usage = message.get("id"), message.get("usage")
        if not isinstance(identity, str) or not identity or not isinstance(usage, dict):
            return None
        messages: dict[str, dict[str, Any]] = state.setdefault("messages", {})
        messages[identity] = normalize_usage("claude_code", usage)
        totals: dict[str, Any] = {}
        for counted in messages.values():
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                value = counted.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    totals[key] = totals.get(key, 0) + value
        return totals or None

    def is_available(self) -> bool:
        return resolve_provider_executable("claude_code") is not None


def mcp_tool_rule(server: str) -> str:
    """The --allowedTools rule that allows every tool of the named MCP server."""
    name = server.strip()
    return name if name.startswith("mcp__") else f"mcp__{name}"


def claude_budget_cap(remaining_usd: float) -> float:
    """The --max-budget-usd for a call: the run's remaining budget less the headroom."""
    cap = math.floor(max(0.0, remaining_usd) * (1 - BUDGET_HEADROOM) * 10_000) / 10_000
    return max(cap, _MIN_BUDGET_USD)


def _configured_budget(args: list[str]) -> float | None:
    """The --max-budget-usd a provider profile's extra arguments already pass, if any."""
    values: list[str] = []
    for index, arg in enumerate(args):
        if arg == "--max-budget-usd" and index + 1 < len(args):
            values.append(args[index + 1])
        elif arg.startswith("--max-budget-usd="):
            values.append(arg.split("=", 1)[1])
    parsed: list[float] = []
    for value in values:
        try:
            number = float(value)
        except ValueError:
            continue
        if math.isfinite(number) and number > 0:
            parsed.append(number)
    return min(parsed) if parsed else None


def _validate_claude_settings(settings: ResolvedProviderSettings | None) -> None:
    if settings is None:
        return
    if settings.subscription != "claude_code":
        raise ValueError(
            f"Claude Code subscription cannot run provider profile for '{settings.subscription}'"
        )
    if settings.sandbox_mode not in (None, "default"):
        raise ValueError("Claude Code profiles do not support sandbox_mode")
