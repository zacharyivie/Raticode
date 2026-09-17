"""Google Antigravity's native headless protocol (agy 1.2.x).

https://antigravity.google/docs/cli/headless
This is not Gemini CLI's ACP protocol. Native permissions remain explicit.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from gofer.core.agent import AgentResult
from gofer.core.antigravity_models import split_antigravity_model
from gofer.core.prompt_envelope import AgentResources
from gofer.core.provider_capabilities import resolve_provider_executable
from gofer.core.provider_profiles import ResolvedProviderSettings, validate_provider_settings
from gofer.core.resources import DEFAULT_RESOURCE_LIMITS
from gofer.subscriptions.acp_config import acp_session_config
from gofer.subscriptions.acp_providers import require_acp_permissions
from gofer.subscriptions.base import Subscription
from gofer.utils.process import env_with_executable_on_path, stream_subprocess


def antigravity_command(
    executable: str | None = None,
    *,
    model: str | None = None,
    effort: str | None = None,
    permission_mode: str | None = None,
) -> list[str]:
    require_acp_permissions("antigravity", permission_mode)
    binary = executable or resolve_provider_executable("antigravity")
    if not binary:
        raise ValueError("'agy' CLI is not available on PATH")
    command = [binary, "--input-format", "stream-json", "--output-format", "stream-json"]
    if effort and effort != "cli-default":
        if effort not in {"low", "medium", "high"}:
            raise ValueError(f"Unsupported Antigravity effort '{effort}'")
        if model and split_antigravity_model(model)[1]:
            model = f"{split_antigravity_model(model)[0]}-{effort}"
        else:
            command += ["--effort", effort]
    if model and model != "cli-default":
        command += ["--model", model]
    return command


@contextmanager
def antigravity_workspace(
    cwd: Path,
    resources: AgentResources,
    *,
    trusted_swarm_url: str | None = None,
    second_brain_cli_path: Path | None = None,
) -> Iterator[tuple[Path, list[str]]]:
    """Stage turn MCP servers without modifying project or global configuration.

    agy has no per-invocation MCP flag. A temporary workspace holds the MCP
    file, while --add-dir grants access to the actual project. Authentication
    and CLI permission settings continue to come from the user's normal home.
    """
    if not any(server.enabled for server in resources.mcpServers):
        yield cwd, []
        return
    # Reuse the provider-independent trusted resource checks and ACP-to-native
    # metadata conversion. Antigravity uses its own native transport.
    with (
        acp_session_config(
            "antigravity",
            cwd,
            resources,
            trusted_swarm_url=trusted_swarm_url,
            second_brain_cli_path=second_brain_cli_path,
        ) as config,
        tempfile.TemporaryDirectory(prefix="raticode-antigravity-") as directory,
    ):
        root = Path(directory)
        folder = root / ".agents"
        folder.mkdir()
        servers = {}
        selected = [server for server in resources.mcpServers if server.enabled]
        for server, entry in zip(selected, config.params["mcpServers"], strict=True):
            # Stable names preserve the user's native mcp(server/tool) policies.
            if server.name in servers:
                raise ValueError(f"Duplicate Antigravity MCP server {server.name!r}")
            if entry.get("type") == "http":
                servers[server.name] = {"serverUrl": entry["url"]}
            else:
                servers[server.name] = {
                    "command": entry["command"],
                    "args": entry["args"],
                    "cwd": str(cwd.resolve()),
                }
        (folder / "mcp_config.json").write_text(
            json.dumps({"mcpServers": servers}), encoding="utf-8"
        )
        yield root, ["--add-dir", str(cwd.resolve())]


async def stream_antigravity(
    prompt: str,
    *,
    cwd: Path,
    executable: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    permission_mode: str | None = None,
    resources: AgentResources | None = None,
    env: dict[str, str] | None = None,
    cancel_event: threading.Event | None = None,
    timeout: float | None = None,
    max_output_bytes: int | None = None,
    trusted_swarm_url: str | None = None,
    second_brain_cli_path: Path | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    command = antigravity_command(
        executable, model=model, effort=effort, permission_mode=permission_mode
    )
    with antigravity_workspace(
        cwd,
        resources or AgentResources(),
        trusted_swarm_url=trusted_swarm_url,
        second_brain_cli_path=second_brain_cli_path,
    ) as (process_cwd, workspace_args):
        command += workspace_args
        if workspace_args:
            prompt = (
                f"The user's project and working directory is {cwd.resolve()}. "
                "Run project commands there and resolve project paths against it. "
                "Your process directory is temporary MCP configuration, not the user's project.\n\n"
                + prompt
            )
        # stdin avoids argv size limits and preserves quotes/newlines literally.
        stdin = (json.dumps({"event": "user", "message": {"content": prompt}}) + "\n").encode()
        source = stream_subprocess(
            command,
            cwd=process_cwd,
            env=env_with_executable_on_path(command[0], env),
            stdin=stdin,
            timeout=timeout,
            cancel_event=cancel_event,
            max_output_bytes=max_output_bytes,
        )
        buffer = ""
        stderr = ""
        final: dict[str, Any] | None = None
        error: str | None = None
        code = 1
        seen_steps: set[int] = set()
        try:
            async for event in source:
                if event["type"] == "exit":
                    code = event["returncode"] if event["returncode"] is not None else 1
                    continue
                if event["stream"] == "stderr":
                    stderr = (stderr + event["text"])[-8192:]
                    continue
                buffer += event["text"]
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        if not isinstance(record, dict):
                            raise ValueError
                        kind = record.get("event")
                        if kind == "result":
                            result = record.get("result")
                            if not isinstance(result, dict) or not isinstance(
                                result.get("response"), str
                            ):
                                raise ValueError
                            final = result
                        elif kind == "step_update":
                            step = record.get("step_update")
                            if not isinstance(step, dict):
                                raise ValueError
                            # One tool summary per step. Answer deltas are never thoughts.
                            index = step.get("step_index")
                            if (
                                step.get("step_type") == "tool"
                                and step.get("state") == "DONE"
                                and isinstance(index, int)
                                and index not in seen_steps
                            ):
                                seen_steps.add(index)
                                name = step.get("tool_name")
                                if isinstance(name, str) and name:
                                    yield {"type": "thought", "text": name.replace("_", " ")}
                    except (ValueError, TypeError):
                        error = "Antigravity emitted malformed stream output"
            if buffer.strip():
                # NDJSON requires a complete terminal record, not a truncated tail.
                error = "Antigravity emitted an incomplete stream record"
            if not final:
                error = error or stderr.strip() or "Antigravity ended without a result"
            elif final.get("status") != "SUCCESS":
                error = error or str(
                    final.get("error") or f"Antigravity ended with {final.get('status')}"
                )
            if code:
                error = error or stderr.strip() or f"Antigravity exited with code {code}"
            yield {
                "type": "error" if error else "final",
                "error": error,
                "message": {"role": "assistant", "body": (final or {}).get("response", "")},
                "sessionId": (final or {}).get("conversation_id"),
                "usage": (final or {}).get("usage", {}),
                "exitCode": code or (1 if error else 0),
            }
        finally:
            close = getattr(source, "aclose", None)
            if close is not None:
                await close()


class AntigravitySubscription(Subscription):
    def is_available(self) -> bool:
        return resolve_provider_executable("antigravity") is not None

    def _build_command(
        self,
        prompt: str,
        tools: list[str],
        mcp_servers: list[str],
        extra_paths: list[Path] | None = None,
        provider_settings: ResolvedProviderSettings | None = None,
    ) -> list[str]:
        if tools or mcp_servers:
            raise ValueError("Antigravity requires structured MCP resources, not legacy tool flags")
        if provider_settings:
            if provider_settings.subscription != "antigravity":
                raise ValueError("Provider profile belongs to another CLI")
            validate_provider_settings(provider_settings)
        return antigravity_command(
            model=provider_settings.model if provider_settings else None,
            effort=provider_settings.effort if provider_settings else None,
            permission_mode=provider_settings.approval_mode if provider_settings else None,
        )

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
        final: dict[str, Any] = {"error": "Antigravity ended without a result", "exitCode": 1}
        thoughts: list[str] = []
        source = stream_antigravity(
            prompt,
            cwd=working_dir,
            executable=command[0],
            env=env,
            model=provider_settings.model if provider_settings else None,
            effort=provider_settings.effort if provider_settings else None,
            permission_mode=provider_settings.approval_mode if provider_settings else None,
            timeout=timeout,
            cancel_event=cancel_event,
            max_output_bytes=max_output_bytes
            if max_output_bytes is not None
            else DEFAULT_RESOURCE_LIMITS.max_subprocess_output_bytes,
        )
        try:
            async for event in source:
                if event["type"] == "thought":
                    thoughts.append(event["text"])
                    if on_thought:
                        on_thought(event["text"])
                else:
                    final = event
        finally:
            close = getattr(source, "aclose", None)
            if close is not None:
                await close()
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
