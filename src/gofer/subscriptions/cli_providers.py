"""Raticode-owned adapters for documented headless CLI protocols.

Cursor wire format: official output reference, accessed 2026-09-16.
OpenCode wire format: v1.18.31 run.ts. Copilot uses documented silent text
output; its SDK events are not treated as a CLI stdout contract.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager, nullcontext
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
from gofer.subscriptions.tool_trace import normalize_tool_trace
from gofer.subscriptions.usage import normalize_usage, track_invocation
from gofer.utils.atomic_output import mkdir_without_links
from gofer.utils.process import env_with_executable_on_path, run_subprocess, stream_subprocess
from gofer.utils.protocol import ProtocolLines, ProtocolRecordLimitError, retained_text

ADDITIONAL_PROVIDERS = {"cursor", "copilot", "opencode"}


@dataclass
class CliOutput:
    provider: str
    json_output: bool = False
    text: str = ""
    session_id: str | None = None
    error: str | None = None
    parse_error: str | None = None
    error_kind: str | None = None
    completed: bool = False
    buffer: str = ""
    records: int = 0
    seen_parts: set[str] = field(default_factory=set)
    usage: dict[str, Any] = field(default_factory=dict)
    usage_parts: set[tuple[str, str, str]] = field(default_factory=set)
    answer_text: str = ""
    stream_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    events: list[dict[str, Any]] = field(default_factory=list)
    seen_tool_updates: set[tuple[str, str]] = field(default_factory=set)
    lines: ProtocolLines = field(default_factory=ProtocolLines)
    request_id: str | None = None
    tool_starts: dict[str, dict[str, Any]] = field(default_factory=dict)

    def feed(self, chunk: str) -> list[str]:
        if self.provider == "copilot" and not self.json_output:
            self.text = retained_text(self.text + chunk)
            return [chunk] if chunk else []
        lines = self.lines.feed(chunk)
        self.buffer = self.lines.buffer
        return [text for line in lines if (text := self._line(line))]

    def _line(self, line: str) -> str:
        if not line.strip():
            return ""
        try:
            payload = json.loads(line)
        except ValueError:
            self.parse_error = "Provider emitted malformed JSON output"
            return ""
        if not isinstance(payload, dict):
            self.parse_error = "Provider emitted a non-object JSON record"
            return ""
        kind = payload.get("type")
        if self.provider == "copilot":
            return self._copilot_line(payload)
        if isinstance(payload.get("request_id"), str):
            self.request_id = payload["request_id"][:256]
        if kind in {
            "system",
            "assistant",
            "result",
            "error",
            "text",
            "tool_use",
            "step_start",
            "step_finish",
            "tool_call",
            "reasoning",
        }:
            self.records += 1
        session = payload.get("session_id" if self.provider == "cursor" else "sessionID")
        if isinstance(session, str) and session:
            if self.session_id and self.session_id != session:
                self.error = "Provider changed session identity during the turn"
                self.error_kind = "session_identity"
            else:
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
                if isinstance(payload.get("usage"), dict):
                    # The terminal record totals the whole turn, including failed turns.
                    self.usage = normalize_usage("cursor", payload["usage"])
            elif kind == "tool_call":
                call = payload.get("tool_call")
                if isinstance(call, dict):
                    function = call.get("function")
                    if isinstance(function, dict):
                        name = str(function.get("name") or "Tool")
                        args = function.get("arguments")
                        result = function.get("result")
                    else:
                        name, detail = next(
                            ((k, v) for k, v in call.items() if isinstance(v, dict)), ("Tool", {})
                        )
                        args, result = detail.get("args"), detail.get("result")
                    status = "running" if payload.get("subtype") == "started" else "completed"
                    self._tool(str(payload.get("call_id") or ""), name, args, result, status)
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
            elif kind == "reasoning":
                part = payload.get("part")
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    identity = part.get("id")
                    if isinstance(identity, str):
                        if identity in self.seen_parts:
                            return ""
                        self.seen_parts.add(identity)
                    self.events.append({"type": "thought", "text": part["text"]})
            elif kind == "tool_use":
                part = payload.get("part")
                if isinstance(part, dict) and isinstance(part.get("state"), dict):
                    state = part["state"]
                    metadata = state.get("metadata") or {}
                    self._tool(
                        str(part.get("callID") or part.get("id") or ""),
                        str(part.get("tool") or "Tool"),
                        state.get("input"),
                        state.get("output") or state.get("error"),
                        str(state.get("status") or "completed"),
                        exit_code=metadata.get("exit") if isinstance(metadata, dict) else None,
                    )
            elif kind == "error":
                error = payload.get("error")
                data = error.get("data") if isinstance(error, dict) else None
                self.error = str(
                    data.get("message")
                    if isinstance(data, dict) and data.get("message")
                    else "OpenCode reported an error"
                )
            elif kind == "step_finish":
                part = payload.get("part")
                if isinstance(part, dict) and isinstance(part.get("tokens"), dict):
                    # Each part is a completed model call. Replayed parts must not
                    # count twice, and steps with missing identity cannot be deduped.
                    identity = (
                        str(part.get("sessionID") or self.session_id or ""),
                        str(part.get("messageID") or ""),
                        str(part.get("id") or ""),
                    )
                    if not identity[2]:
                        self.usage["partial"] = True
                    if identity[2] and identity not in self.usage_parts:
                        self.usage_parts.add(identity)
                        native = normalize_usage("opencode", part["tokens"])
                        if not all(key in native for key in ("input_tokens", "output_tokens")):
                            self.usage["partial"] = True
                        for key in (
                            "input_tokens",
                            "output_tokens",
                            "total_tokens",
                            "cache_read_tokens",
                            "cache_write_tokens",
                            "reasoning_tokens",
                        ):
                            value = native.get(key)
                            if isinstance(value, (int, float)) and not isinstance(value, bool):
                                self.usage[key] = self.usage.get(key, 0) + value
                        if self.usage:
                            self.usage["source"] = "provider_metadata"
            # step_finish describes one model step; only process exit ends a run.
        self.text = retained_text(self.text + text)
        self.answer_text = retained_text(self.answer_text + text)
        if text:
            self.events.append({"type": "thought", "text": text, "deltaStreamId": self.stream_id})
        return text

    def _tool(
        self,
        identity: str,
        name: str,
        args: Any,
        result: Any,
        status: str,
        *,
        exit_code: Any = None,
    ) -> None:
        key = (identity, status)
        if identity and key in self.seen_tool_updates:
            return
        if identity:
            self.seen_tool_updates.add(key)
        # The next assistant segment is the answer after this tool, while the
        # preceding text remains visible as progress in the thought timeline.
        if self.provider == "opencode" or status == "running":
            self.answer_text = ""
            self.stream_id = uuid.uuid4().hex
        self.events.append(
            {
                "type": "thought",
                "text": name,
                "trace": normalize_tool_trace(
                    {
                        "kind": "tool",
                        "id": identity,
                        "title": name,
                        "input": args,
                        "output": result,
                        "status": status,
                        **({"exitCode": exit_code} if type(exit_code) is int else {}),
                    }
                ),
            }
        )

    def _copilot_line(self, payload: dict[str, Any]) -> str:
        """Official Copilot SDK events; only the main agent's final answer."""
        kind, data = payload.get("type"), payload.get("data")
        if not isinstance(data, dict) or payload.get("agentId"):
            return ""
        self.records += 1
        if kind in {"session.start", "session.resume"}:
            session = data.get("sessionId")
            if isinstance(session, str):
                if self.session_id and session != self.session_id:
                    self.error = "Provider changed session identity during the turn"
                    self.error_kind = "session_identity"
                self.session_id = session
        elif kind == "tool.execution_start":
            identity = str(data.get("toolCallId") or "")
            self.tool_starts[identity] = data
            self.answer_text = ""
            self._tool(
                identity,
                str(data.get("toolName") or "Tool"),
                data.get("arguments"),
                None,
                "running",
            )
        elif kind == "tool.execution_complete":
            identity = str(data.get("toolCallId") or "")
            start = self.tool_starts.get(identity, {})
            result = data.get("result") or {}
            output = result.get("content") if isinstance(result, dict) else result
            shell = data.get("shellExecution") or {}
            self._tool(
                identity,
                str(start.get("toolName") or "Tool"),
                start.get("arguments"),
                data.get("error") or output,
                "completed" if data.get("success") is True and not data.get("error") else "failed",
                exit_code=shell.get("exitCode") if isinstance(shell, dict) else None,
            )
        elif kind == "assistant.message":
            identity = str(data.get("messageId") or payload.get("id") or "")
            if identity in self.seen_parts:
                return ""
            self.seen_parts.add(identity)
            text = data.get("content")
            if isinstance(text, str):
                self.text = retained_text(self.text + text)
                self.answer_text = retained_text(self.answer_text + text)
                return text
        elif kind == "assistant.reasoning" and isinstance(data.get("content"), str):
            self.events.append({"type": "thought", "text": data["content"]})
        elif kind == "assistant.usage":
            identity = str(payload.get("id") or "")
            key = ("copilot", "usage", identity)
            if identity and key not in self.usage_parts:
                self.usage_parts.add(key)
                for key_name, value in normalize_usage("copilot", data).items():
                    if key_name.endswith("tokens") and type(value) in {int, float}:
                        self.usage[key_name] = self.usage.get(key_name, 0) + value
                if self.usage:
                    self.usage["source"] = "provider_metadata"
        elif kind in {"session.error", "session.shutdown"}:
            if kind == "session.error" or data.get("shutdownType") == "error":
                self.error = str(data.get("message") or data.get("errorReason") or "Copilot failed")
            elif kind == "session.shutdown":
                self.completed = True
        elif kind == "session.idle":
            if data.get("aborted") is True:
                self.error = "Copilot turn cancelled"
            else:
                self.completed = True
        return ""

    def drain_events(self) -> list[dict[str, Any]]:
        events, self.events = self.events, []
        return events

    def finish(self, returncode: int, stderr: str, *, cancelled: bool = False) -> None:
        if self.buffer:
            self._line(self.buffer)
            self.buffer = ""
        if cancelled:
            self.error = "Provider turn cancelled"
            self.error_kind = "cancelled"
        elif returncode != 0:
            self.error = self.error or stderr.strip() or f"Provider exited with {returncode}"
            self.error_kind = self.error_kind or "provider"
        if self.error:
            self.error_kind = self.error_kind or "provider"
        elif self.parse_error:
            self.error = self.parse_error
            self.error_kind = "protocol"
        elif self.provider == "cursor" and not self.completed:
            self.error = "Cursor output ended without a successful result"
            self.error_kind = "incomplete"
        elif self.provider == "opencode" and not self.records:
            self.error = self.error or "OpenCode returned no protocol records"
            self.error_kind = "incomplete"
        elif self.provider == "copilot" and self.json_output and not self.completed:
            self.error = "Copilot output ended without a completed turn"
            self.error_kind = "incomplete"
        self.completed = self.error is None


def cli_command(
    provider: str,
    prompt: str,
    *,
    model: str | None = None,
    effort: str | None = None,
    executable: str | None = None,
    extra_paths: list[Path] | None = None,
    image_paths: list[Path] | None = None,
) -> list[str]:
    binary = (
        executable
        or resolve_provider_executable(cast(ProviderId, provider))
        or PROVIDER_BINARIES[provider]
    )
    if provider == "cursor":
        model = cursor_model_id(model, effort)
        # Selecting a working directory in Raticode grants workspace trust.
        # Keep tool permissions in the per-invocation resource configuration.
        command = [binary, "--trust", "--print", "--output-format", "stream-json"]
        for path in extra_paths or []:
            command += ["--add-dir", str(path)]
    elif provider == "copilot":
        command = [binary, "--output-format", "text", "--silent"]
        if effort and effort != "cli-default":
            command += ["--reasoning-effort", effort]
        for path in extra_paths or []:
            command += ["--add-dir", str(path)]
    elif provider == "opencode":
        command = [binary, "run", "--format", "json", "--thinking"]
    else:
        raise ValueError(f"Unknown CLI provider '{provider}'")
    if model and model != "cli-default":
        command += ["--model", model]
    for path in image_paths or []:
        # Cursor's installed headless parser exposes --image as a hidden flag.
        flag = {"cursor": "--image", "copilot": "--attachment", "opencode": "--file"}[provider]
        command.append(f"{flag}={path}")
    if provider == "opencode" and image_paths:
        # --file is an array option; end options before the positional prompt.
        command.append("--")
    command += [prompt] if provider == "opencode" else ["-p", prompt]
    return command


async def check_cursor_plugins(
    executable: str, cancel_event: threading.Event | None = None
) -> None:
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
    session_dir: Path | None = None,
    read_only: bool = False,
    tool_free: bool = False,
    structured_output: bool = False,
) -> Iterator[tuple[list[str], dict[str, str]]]:
    """Own additive per-run config through subprocess drain, preserving login stores."""
    child_env = env_with_executable_on_path(command[0], env)
    args = list(command)
    if provider == "copilot" and structured_output:
        if "--output-format" in args:
            args[args.index("--output-format") + 1] = "json"
        else:
            args += ["--output-format", "json"]
        args += ["--stream", "off"]
    if session_dir is not None:
        mkdir_without_links(session_dir)
    directory_context = (
        nullcontext(str(session_dir))
        if session_dir is not None
        else tempfile.TemporaryDirectory(prefix="raticode-provider-")
    )
    with directory_context as directory:
        if resources is not None:
            alias_prefix = "raticode-" + (session_dir.name if session_dir else uuid.uuid4().hex)
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
                # Cursor rejects plugin roots with fewer than three path
                # components, including /tmp/raticode-provider-*. Keep the
                # plugin inside our private invocation directory.
                # Extension plugins also derive their identity from the folder
                # name. Match the manifest and the exact MCP permission grants.
                root = Path(directory) / alias_prefix
                root.mkdir(exist_ok=True)
                (root / ".cursor-plugin").mkdir(exist_ok=True)
                (root / ".cursor-plugin/plugin.json").write_text(
                    json.dumps({"name": alias_prefix, "version": "1.0.0"})
                )
                for alias, entry in servers.items():
                    if grants[alias] != ["*"]:
                        entry["enabledTools"] = grants[alias]
                (root / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
                allow = ["Read(*)"] if read_only else ["Read(*)", "Write(*)"]
                deny = ["Write(*)"] if read_only else []
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
                if read_only:
                    native_tools = [
                        tool
                        for tool in native_tools
                        if tool not in {"edit_tool_call", "delete_tool_call"}
                    ]
                if tool_free:
                    native_tools = []
                    allow = []
                    deny = ["Read(*)", "Write(*)", "Shell(*)", "WebSearch(*)", "Mcp(*)"]
                if resources.shell and not read_only:
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
                if resources.agents and not tool_free:
                    native_tools.append("task_tool_call")
                for alias, tools in grants.items():
                    for tool in tools:
                        allow.append(f"Mcp(plugin-{alias_prefix}-{alias}:{tool})")
                config_dir = root / "config"
                config_dir.mkdir(exist_ok=True)
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
                args += ["--allow-tool", "read"]
                args += ["--deny-tool" if read_only else "--allow-tool", "write"]
                args += [
                    "--allow-tool" if resources.shell and not read_only else "--deny-tool",
                    "shell",
                ]
                args += ["--allow-tool" if resources.web else "--deny-tool", "url"]
                if not resources.agents:
                    args += ["--excluded-tools", "task"]
                if tool_free:
                    args += ["--available-tools", "", "--deny-tool", "read"]
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
                    "edit": "deny" if read_only else "allow",
                    "glob": "allow",
                    "grep": "allow",
                    "list": "allow",
                    "lsp": "allow",
                    "task": "allow" if resources.agents else "deny",
                    "bash": "allow" if resources.shell and not read_only else "deny",
                    "webfetch": "allow" if resources.web else "deny",
                    "websearch": "allow" if resources.web else "deny",
                }
                if tool_free:
                    permission = {"*": "deny"}
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
    json_output = (
        "--output-format" in command and command[command.index("--output-format") + 1] == "json"
    )
    output = CliOutput(provider, json_output=json_output)
    stderr = ""
    source = stream_subprocess(
        command,
        cwd=cwd,
        env=env,
        cancel_event=cancel_event,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
        protocol_stdout=provider != "copilot" or json_output,
    )
    try:
        async for event in source:
            if event["type"] == "chunk":
                if event["stream"] == "stderr":
                    stderr += event["text"] or ""
                else:
                    output.feed(event["text"] or "")
                    if output.session_id:
                        yield {"type": "session", "sessionId": output.session_id}
                    for trace_event in output.drain_events():
                        yield trace_event
            else:
                code = event["returncode"] if event["returncode"] is not None else 1
                output.finish(code, stderr, cancelled=bool(cancel_event and cancel_event.is_set()))
                if (
                    provider == "copilot"
                    and not json_output
                    and event.get("output_truncated")
                    and not output.error
                ):
                    output.error = "Copilot text output exceeded the configured output limit"
                    output.error_kind = "output_limit"
                for trace_event in output.drain_events():
                    yield trace_event
                yield {
                    "type": "error" if output.error else "final",
                    "error": output.error,
                    "message": {
                        "role": "assistant",
                        "body": output.text
                        if provider == "copilot" and not json_output
                        else output.answer_text,
                    },
                    "sessionId": output.session_id,
                    "usage": output.usage,
                    "exitCode": code if code else (1 if output.error else 0),
                    "processExitCode": code,
                    "errorKind": output.error_kind,
                    "parseError": output.parse_error,
                    "retainedOutputTruncated": event.get("output_truncated", False),
                    "protocolRecords": output.records,
                    "requestId": output.request_id,
                }
                return
    except ProtocolRecordLimitError as exc:
        yield {
            "type": "error",
            "error": str(exc),
            "errorKind": "protocol_limit",
            "message": {"role": "assistant", "body": output.answer_text},
            "sessionId": output.session_id,
            "exitCode": 1,
        }
    finally:
        close = getattr(source, "aclose", None)
        if close is not None:
            await close()


class CliSubscription(Subscription):
    def __init__(
        self,
        provider: str,
        *,
        resources: AgentResources | None = None,
        read_only: bool = False,
        tool_free: bool = False,
    ) -> None:
        if provider not in ADDITIONAL_PROVIDERS:
            raise ValueError(f"Unknown CLI provider '{provider}'")
        self.provider = provider
        self.resources = resources
        self.read_only = read_only
        self.tool_free = tool_free

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

    @track_invocation
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
            self.resources if self.resources is not None else AgentResources(shell=True, web=True),
            env=env,
            extra_paths=extra_paths,
            read_only=self.read_only,
            tool_free=self.tool_free,
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
            usage_metadata=final.get("usage", {}),
        )
