from __future__ import annotations

from pathlib import Path
from typing import Any

from gofer.core.provider_capabilities import resolve_provider_executable
from gofer.core.provider_permissions import provider_permission_args
from gofer.core.provider_profiles import ResolvedProviderSettings
from gofer.subscriptions import base as subscription_base
from gofer.subscriptions.base import Subscription


class CodexSubscription(Subscription):
    provider = "codex"

    def _build_command(
        self,
        prompt: str,
        tools: list[str],
        mcp_servers: list[str],
        extra_paths: list[Path] | None = None,
        provider_settings: ResolvedProviderSettings | None = None,
    ) -> list[str]:
        _validate_codex_settings(provider_settings)
        cmd = [
            resolve_provider_executable("codex") or "codex",
            "exec",
            "--color",
            "never",
            "--skip-git-repo-check",
            *provider_permission_args(
                "codex", provider_settings.sandbox_mode if provider_settings else None
            ),
            "--json",
        ]
        if provider_settings:
            if provider_settings.model:
                cmd += ["--model", provider_settings.model]
            if provider_settings.effort:
                cmd += ["-c", f'model_reasoning_effort="{provider_settings.effort}"']
            cmd += provider_settings.extra_args
        for path in extra_paths or []:
            cmd += ["--add-dir", str(path)]
        cmd.append(prompt)
        return cmd

    def is_available(self) -> bool:
        return resolve_provider_executable("codex") is not None

    def _parse_provider_output(self, stdout: str, stderr: str) -> tuple[str, dict[str, object]]:
        payloads = subscription_base._json_payloads(stdout) + subscription_base._json_payloads(
            stderr
        )
        from gofer.subscriptions.usage import provider_payload_usage

        metadata = provider_payload_usage("codex", payloads)
        if metadata:
            metadata.setdefault("source", "provider_metadata")
        message = _codex_message_from_payloads(payloads)
        if message is None:
            message = _codex_transcript_final_message(stdout)
        if message is None:
            message = _codex_transcript_final_message(stderr)
        return message or stdout or stderr, metadata


def _codex_message_from_payloads(payloads: list[dict[str, Any]]) -> str | None:
    for payload in reversed(payloads):
        payload_type = payload.get("type")
        if payload_type == "result":
            for key in ("result", "output", "text"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value

        data = payload.get("data")
        if isinstance(data, dict):
            message = data.get("message")
            if isinstance(message, str) and message:
                return message

        item = payload.get("item")
        if isinstance(item, dict):
            message = _codex_message_from_item(item)
            if message:
                return message

        message = _codex_message_from_item(payload)
        if message:
            return message
    return None


def _codex_message_from_item(item: dict[str, Any]) -> str | None:
    item_type = item.get("type")
    role = item.get("role")
    if item_type == "agent_message":
        text = item.get("text")
        return text if isinstance(text, str) and text else None
    if role not in ("assistant", "codex"):
        return None
    text = item.get("text")
    if isinstance(text, str) and text:
        return text
    return _text_from_codex_content(item.get("content"))


def _text_from_codex_content(content: Any) -> str | None:
    if isinstance(content, str) and content:
        return content
    if not isinstance(content, list):
        return None
    texts: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            texts.append(text)
            continue
        text = part.get("content")
        if isinstance(text, str) and text:
            texts.append(text)
    return "\n".join(texts) if texts else None


_CODEX_TRANSCRIPT_MARKERS = {
    "assistant",
    "codex",
    "exec",
    "mcp",
    "system",
    "tokens used",
    "tool",
    "user",
}


def _codex_transcript_final_message(text: str) -> str | None:
    current_marker: str | None = None
    current_lines: list[str] = []
    assistant_blocks: list[str] = []

    def flush() -> None:
        if current_marker not in ("assistant", "codex"):
            return
        block = "\n".join(current_lines).strip()
        if block:
            assistant_blocks.append(block)

    for line in text.splitlines():
        stripped = line.strip()
        marker = stripped if stripped in _CODEX_TRANSCRIPT_MARKERS else None
        if marker is None and stripped.startswith("mcp: "):
            marker = "mcp"
        if marker is not None:
            flush()
            current_marker = marker
            current_lines = []
            continue
        if current_marker in ("assistant", "codex"):
            current_lines.append(line)

    flush()
    return assistant_blocks[-1] if assistant_blocks else None


def _validate_codex_settings(settings: ResolvedProviderSettings | None) -> None:
    if settings is None:
        return
    if settings.subscription != "codex":
        raise ValueError(
            f"Codex subscription cannot run provider profile for '{settings.subscription}'"
        )
    if settings.tools:
        raise ValueError("Codex profiles do not support default tools")
    if settings.mcp_servers:
        raise ValueError("Codex profiles do not support MCP server flags")
    if settings.approval_mode not in (None, "default"):
        raise ValueError("Codex profiles do not support approval_mode")
