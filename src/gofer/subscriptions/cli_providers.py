"""Raticode-owned adapters for documented headless CLI protocols.

Cursor wire format: official output reference, accessed 2026-09-16.
OpenCode wire format: v1.18.31 run.ts. Copilot uses documented text output;
its SDK events are deliberately not treated as a CLI stdout contract.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from gofer.core.agent import AgentResult
from gofer.core.cursor_models import cursor_model_id
from gofer.core.prompt_envelope import AgentResources
from gofer.core.provider_capabilities import (
    PROVIDER_BINARIES,
    ProviderId,
    resolve_provider_executable,
)
from gofer.core.provider_profiles import ResolvedProviderSettings, validate_provider_settings
from gofer.core.resources import DEFAULT_RESOURCE_LIMITS
from gofer.subscriptions.base import Subscription
from gofer.utils.process import env_with_executable_on_path, run_subprocess, stream_subprocess

ADDITIONAL_PROVIDERS = {"cursor", "copilot", "opencode"}

# Resource filtering relies on private Cursor options, not just --plugin-dir.
# See docs/cli-provider-adapters.md for the inspected implementations.
CURSOR_RESOURCE_BUILD_FAMILIES = frozenset({"2026.09.10", "2026.09.15"})


@dataclass
class CliOutput:
    provider: str
    text: str = ""
    session_id: str | None = None
    error: str | None = None
    completed: bool = False
    buffer: str = ""
    records: int = 0
    seen_parts: set[str] = field(default_factory=set)

    def feed(self, chunk: str) -> list[str]:
        if self.provider == "copilot":
            self.text += chunk
            return [chunk] if chunk else []
        lines = (self.buffer + chunk).split("\n")
        self.buffer = lines.pop()
        return [text for line in lines if (text := self._line(line))]

    def _line(self, line: str) -> str:
        if not line.strip():
            return ""
        try:
            payload = json.loads(line)
        except ValueError:
            self.error = "Provider emitted malformed JSON output"
            return ""
        if not isinstance(payload, dict):
            self.error = "Provider emitted a non-object JSON record"
            return ""
        kind = payload.get("type")
        if kind in {
            "system",
            "assistant",
            "result",
            "error",
            "text",
            "tool_use",
            "step_start",
            "step_finish",
        }:
            self.records += 1
        session = payload.get("session_id" if self.provider == "cursor" else "sessionID")
        if isinstance(session, str) and session:
            if self.session_id and self.session_id != session:
                self.error = "Provider changed session identity during the turn"
            self.session_id = session
        text = ""
        if self.provider == "cursor":
            if kind == "assistant":
                # With partial output, buffered flushes carry model_call_id.
                # The adapter does not request partial mode.
                message = payload.get("message")
                if isinstance(message, dict):
                    identity = message.get("id")
                    if isinstance(identity, str):
                        if identity in self.seen_parts:
                            return ""
                        self.seen_parts.add(identity)
                    content = message.get("content")
                    if isinstance(content, list):
                        text = "".join(
                            p["text"]
                            for p in content
                            if isinstance(p, dict)
                            and p.get("type") == "text"
                            and isinstance(p.get("text"), str)
                        )
            elif kind == "result":
                if payload.get("is_error") is True or payload.get("subtype") != "success":
                    self.error = str(
                        payload.get("result") or "Cursor reported an unsuccessful result"
                    )
                else:
                    self.completed = True
                    if not self.text and isinstance(payload.get("result"), str):
                        text = payload["result"]
            elif kind == "error":
                self.error = str(payload.get("message") or "Cursor reported an error")
        else:
            if kind == "text":
                part = payload.get("part")
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    identity = part.get("id")
                    if isinstance(identity, str):
                        if identity in self.seen_parts:
                            return ""
                        self.seen_parts.add(identity)
                    text = part["text"]
            elif kind == "error":
                error = payload.get("error")
                data = error.get("data") if isinstance(error, dict) else None
                self.error = str(
                    data.get("message")
                    if isinstance(data, dict) and data.get("message")
                    else "OpenCode reported an error"
                )
            # step_finish describes one model step; only process exit ends a run.
        self.text += text
        return text

    def finish(self, returncode: int, stderr: str, *, cancelled: bool = False) -> None:
        if self.buffer:
            self._line(self.buffer)
            self.buffer = ""
        if cancelled:
            self.error = "Provider turn cancelled"
        elif returncode != 0:
            self.error = self.error or stderr.strip() or f"Provider exited with {returncode}"
        elif self.provider == "cursor" and not self.completed:
            self.error = self.error or "Cursor output ended without a successful result"
        elif self.provider == "opencode" and not self.records:
            self.error = self.error or "OpenCode returned no protocol records"
        self.completed = self.error is None


def cli_command(
    provider: str,
    prompt: str,
    *,
    model: str | None = None,
    effort: str | None = None,
    executable: str | None = None,
    extra_paths: list[Path] | None = None,
) -> list[str]:
    binary = (
        executable
        or resolve_provider_executable(cast(ProviderId, provider))
        or PROVIDER_BINARIES[provider]
    )
    if provider == "cursor":
        model = cursor_model_id(model, effort)
        command = [binary, "--print", "--output-format", "stream-json"]
        for path in extra_paths or []:
            command += ["--add-dir", str(path)]
    elif provider == "copilot":
        command = [binary, "--output-format", "text", "--silent"]
        if effort and effort != "cli-default":
            command += ["--reasoning-effort", effort]
        for path in extra_paths or []:
            command += ["--add-dir", str(path)]
    elif provider == "opencode":
        command = [binary, "run", "--format", "json"]
    else:
        raise ValueError(f"Unknown CLI provider '{provider}'")
    if model and model != "cli-default":
        command += ["--model", model]
    command += [prompt] if provider == "opencode" else ["-p", prompt]
    return command


async def check_cursor_plugins(
    executable: str, cancel_event: threading.Event | None = None
) -> None:
    code, version, _ = await run_subprocess(
        [executable, "--version"],
        timeout=10,
        max_output_bytes=128 * 1024,
        cancel_event=cancel_event,
        env=env_with_executable_on_path(executable),
    )
    version = version.strip()
    build = re.fullmatch(r"(\d{4}\.\d{2}\.\d{2})(?:[.-][A-Za-z0-9]+)*", version)
    if code or build is None or build.group(1) not in CURSOR_RESOURCE_BUILD_FAMILIES:
        supported = ", ".join(sorted(CURSOR_RESOURCE_BUILD_FAMILIES))
        detected = version[:100] if version else "unknown"
        raise ValueError(
            f"Cursor CLI build {detected!r} is not supported for Raticode resource controls. "
            f"Supported build families: {supported}. Update Raticode or select a supported "
            "Cursor executable in Settings > Providers."
        )
    if os.environ.get("CURSOR_ENABLE_BEDROCK") == "1" or os.environ.get(
        "CURSOR_LOCAL_AGENT_BASE_URL"
    ):
        raise ValueError(
            "Cursor per-run plugins require authenticated normal mode, not local/Bedrock mode"
        )
    code, help_text, _ = await run_subprocess(
        [executable, "--help"],
        timeout=10,
        cancel_event=cancel_event,
        max_output_bytes=128 * 1024,
        env=env_with_executable_on_path(executable),
    )
    if code or "Start the Cursor Agent" not in help_text or "--plugin-dir" not in help_text:
        raise ValueError(
            "Cursor CLI must support --plugin-dir for selected MCP resources; update Cursor Agent"
        )


@contextmanager
def cli_invocation(
    provider: str,
    command: list[str],
    resources: AgentResources | None,
    *,
    env: dict[str, str] | None = None,
    trusted_swarm_url: str | None = None,
    second_brain_cli_path: Path | None = None,
    extra_paths: list[Path] | None = None,
) -> Iterator[tuple[list[str], dict[str, str]]]:
    """Own additive per-run config through subprocess drain, preserving login stores."""
    child_env = env_with_executable_on_path(command[0], env)
    args = list(command)
    with tempfile.TemporaryDirectory(prefix="raticode-provider-") as directory:
        if resources is not None:
            alias_prefix = "raticode-" + uuid.uuid4().hex
            servers: dict[str, Any] = {}
            grants: dict[str, list[str]] = {}
            for server in resources.mcpServers:
                if not server.enabled:
                    continue
                alias = alias_prefix + "-" + server.name
                servers[alias] = (
                    {"command": server.command, "args": server.args}
                    if server.type == "stdio"
                    else {"url": server.url}
                )
                if server.name == "swarm":
                    if (
                        not trusted_swarm_url
                        or server.type != "http"
                        or server.url != trusted_swarm_url
                    ):
                        raise ValueError(
                            "Swarm MCP endpoint does not match the trusted running turn"
                        )
                    grants[alias] = ["swarm_action"]
                elif server.name == "second_brain":
                    if (
                        not second_brain_cli_path
                        or server.type != "stdio"
                        or server.command != str(second_brain_cli_path)
                        or server.args[:2] != ["ui", "second-brain"]
                    ):
                        raise ValueError(
                            "Second Brain MCP executable does not match the trusted installation"
                        )
                    grants[alias] = ["rules", "search", "read_note", "save_note"]
                else:
                    grants[alias] = ["*"]
            if provider == "cursor":
                root = Path(directory)
                (root / ".cursor-plugin").mkdir()
                (root / ".cursor-plugin/plugin.json").write_text(
                    json.dumps({"name": alias_prefix, "version": "1.0.0"})
                )
                for alias, entry in servers.items():
                    if grants[alias] != ["*"]:
                        entry["enabledTools"] = grants[alias]
                (root / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
                allow = ["Read(*)", "Write(*)"]
                deny = []
                native_tools = [
                    "read_tool_call",
                    "edit_tool_call",
                    "delete_tool_call",
                    "glob_tool_call",
                    "grep_tool_call",
                    "ls_tool_call",
                    "read_lints_tool_call",
                    "update_todos_tool_call",
                    "read_todos_tool_call",
                    "mcp_tool_call",
                    # Required by Cursor's deferred MCP tool discovery, even
                    # when this invocation has no selected MCP servers.
                    "get_mcp_tools_tool_call",
                ]
                if resources.shell:
                    allow.append("Shell(*)")
                    native_tools += ["shell_tool_call", "write_shell_stdin_tool_call"]
                else:
                    deny.append("Shell(*)")
                if resources.web:
                    allow.append("WebFetch(*)")
                    native_tools += [
                        "web_search_tool_call",
                        "fetch_tool_call",
                        "web_fetch_tool_call",
                    ]
                else:
                    deny.append("WebFetch(*)")
                for alias, tools in grants.items():
                    for tool in tools:
                        allow.append(f"Mcp(plugin-{alias_prefix}-{alias}:{tool})")
                config_dir = root / "config"
                config_dir.mkdir()
                (config_dir / "cli-config.json").write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "editor": {"vimMode": False},
                            "permissions": {"allow": allow, "deny": deny},
                            "approvalMode": "allowlist",
                            "autoAcceptWebSearch": resources.web,
                        }
                    )
                )
                child_env["CURSOR_CONFIG_DIR"] = str(config_dir)
                args += [
                    "--plugin-dir",
                    str(root),
                    "--disable-project-configs",
                    "--allowed-tools",
                    ",".join(native_tools),
                ]
            elif provider == "copilot":
                for alias, entry in servers.items():
                    entry["tools"] = grants[alias]
                    if "url" in entry:
                        entry["type"] = "http"
                config = Path(directory) / "mcp.json"
                config.write_text(json.dumps({"mcpServers": servers}))
                args += ["--additional-mcp-config", "@" + str(config)]
                args += ["--allow-tool", "read", "--allow-tool", "write"]
                args += ["--allow-tool" if resources.shell else "--deny-tool", "shell"]
                args += ["--allow-tool" if resources.web else "--deny-tool", "url"]
                for alias, tools in grants.items():
                    for tool in tools:
                        args += ["--allow-tool", alias if tool == "*" else f"{alias}({tool})"]
            else:
                mcp = {
                    alias: (
                        {"type": "local", "command": [entry["command"], *entry["args"]]}
                        if "command" in entry
                        else {"type": "remote", "url": entry["url"], "oauth": False}
                    )
                    for alias, entry in servers.items()
                }
                permission: dict[str, Any] = {
                    "*": "deny",
                    "read": "allow",
                    "edit": "allow",
                    "glob": "allow",
                    "grep": "allow",
                    "list": "allow",
                    "bash": "allow" if resources.shell else "deny",
                    "webfetch": "allow" if resources.web else "deny",
                    "websearch": "allow" if resources.web else "deny",
                }
                if extra_paths:
                    permission["external_directory"] = {"*": "deny"}
                    for path in extra_paths:
                        permission["external_directory"][str(path.resolve())] = "allow"
                        permission["external_directory"][str(path.resolve()) + "/**"] = "allow"
                for alias, tools in grants.items():
                    for tool in tools:
                        permission[f"{alias}_{tool}"] = "allow"
                # Inline config is additive; keep caller's unrelated inline settings.
                try:
                    config_value = json.loads(
                        child_env.get(
                            "OPENCODE_CONFIG_CONTENT",
                            os.environ.get("OPENCODE_CONFIG_CONTENT", "{}"),
                        )
                    )
                except ValueError as exc:
                    raise ValueError("OPENCODE_CONFIG_CONTENT is not valid JSON") from exc
                if not isinstance(config_value, dict):
                    raise ValueError("OPENCODE_CONFIG_CONTENT must be an object")
                inherited_mcp = config_value.get("mcp", {})
                if not isinstance(inherited_mcp, dict):
                    raise ValueError("OPENCODE_CONFIG_CONTENT.mcp must be an object")
                config_value["mcp"] = {**inherited_mcp, **mcp}
                child_env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config_value)
                child_env["OPENCODE_PERMISSION"] = json.dumps(permission)
        yield args, child_env


async def stream_cli(
    command: list[str],
    provider: str,
    *,
    cwd: Path,
    env: dict[str, str],
    cancel_event: threading.Event | None = None,
    timeout: float | None = None,
    max_output_bytes: int | None = DEFAULT_RESOURCE_LIMITS.max_subprocess_output_bytes,
) -> AsyncIterator[dict[str, Any]]:
    # Trust only the selected working directory for this invocation.
    # Tool permissions remain controlled by cli_invocation.
    if provider == "copilot":
        command = [*command, "--add-dir", str(cwd.resolve())]
    output = CliOutput(provider)
    stderr = ""
    source = stream_subprocess(
        command,
        cwd=cwd,
        env=env,
        cancel_event=cancel_event,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
    )
    try:
        async for event in source:
            if event["type"] == "chunk":
                if event["stream"] == "stderr":
                    stderr += event["text"] or ""
                else:
                    for text in output.feed(event["text"] or ""):
                        # Plain Copilot stdout is the answer, not a reasoning trace.
                        # Keep exact whitespace and publish it in the final message.
                        if provider != "copilot":
                            yield {"type": "thought", "text": text}
            else:
                code = event["returncode"] if event["returncode"] is not None else 1
                output.finish(code, stderr, cancelled=bool(cancel_event and cancel_event.is_set()))
                yield {
                    "type": "error" if output.error else "final",
                    "error": output.error,
                    "message": {"role": "assistant", "body": output.text},
                    "sessionId": output.session_id,
                    "exitCode": code if code else (1 if output.error else 0),
                }
                return
    finally:
        close = getattr(source, "aclose", None)
        if close is not None:
            await close()


class CliSubscription(Subscription):
    def __init__(self, provider: str) -> None:
        if provider not in ADDITIONAL_PROVIDERS:
            raise ValueError(f"Unknown CLI provider '{provider}'")
        self.provider = provider

    def _build_command(
        self,
        prompt: str,
        tools: list[str],
        mcp_servers: list[str],
        extra_paths: list[Path] | None = None,
        provider_settings: ResolvedProviderSettings | None = None,
    ) -> list[str]:
        if tools or mcp_servers:
            raise ValueError(
                f"{self.provider} does not support legacy tool/MCP string flags; "
                "use structured Rem resources"
            )
        if provider_settings:
            if provider_settings.subscription != self.provider:
                raise ValueError("Provider profile belongs to another CLI")
            validate_provider_settings(provider_settings)
        return cli_command(
            self.provider,
            prompt,
            model=provider_settings.model if provider_settings else None,
            effort=provider_settings.effort if provider_settings else None,
            extra_paths=extra_paths,
        )

    def is_available(self) -> bool:
        return resolve_provider_executable(cast(ProviderId, self.provider)) is not None

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
        start = time.monotonic()
        command = self._build_command(prompt, tools, mcp_servers, extra_paths, provider_settings)
        thoughts: list[str] = []
        final: dict[str, Any] = {
            "exitCode": 1,
            "error": "Provider ended without a completion event",
        }
        if self.provider == "cursor":
            await check_cursor_plugins(command[0], cancel_event)
        with cli_invocation(
            self.provider,
            command,
            AgentResources(shell=True, web=True),
            env=env,
            extra_paths=extra_paths,
        ) as (command, child_env):
            async for event in stream_cli(
                command,
                self.provider,
                cwd=working_dir,
                env=child_env,
                timeout=timeout,
                cancel_event=cancel_event,
                max_output_bytes=max_output_bytes,
            ):
                if event["type"] == "thought":
                    thoughts.append(event["text"])
                    if on_thought:
                        on_thought(event["text"])
                else:
                    final = event
        text = (final.get("message") or {}).get("body") or final.get("error") or ""
        return AgentResult(
            agent_id="",
            success=not final.get("error"),
            output=text,
            message=text,
            exit_code=final["exitCode"],
            duration_seconds=time.monotonic() - start,
            thoughts=thoughts,
        )
