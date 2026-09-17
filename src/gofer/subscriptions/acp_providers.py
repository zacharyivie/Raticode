"""Official Grok Build through Raticode's ACP transport.

Native permission mode is an explicit opt-in. It retains CLI policies, ambient
tools and authentication. It does not promise Raticode resource filtering.
"""

from __future__ import annotations

import threading
import time
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any, cast

from gofer.core.agent import AgentResult
from gofer.core.prompt_envelope import AgentResources
from gofer.core.provider_capabilities import ProviderId, resolve_provider_executable
from gofer.core.provider_permissions import provider_permission_args
from gofer.core.provider_profiles import ResolvedProviderSettings, validate_provider_settings
from gofer.subscriptions.acp_config import acp_session_config, deny_acp_permission
from gofer.subscriptions.acp_session import initialize_session, prompt_session, wait_grok_mcp
from gofer.subscriptions.acp_transport import AcpTransportError, open_acp_transport
from gofer.subscriptions.base import Subscription
from gofer.subscriptions.grok_identity import grok_build_version

ACP_PROVIDERS = {"grok"}


def acp_command(
    provider: str, executable: str | None = None, plugin_dir: str | None = None
) -> list[str]:
    if provider not in ACP_PROVIDERS:
        raise ValueError(f"Unknown ACP provider '{provider}'")
    binary = executable or resolve_provider_executable(cast(ProviderId, provider))
    if not binary:
        raise ValueError(f"'{provider}' CLI is not available on PATH")
    plugin_args = ["--plugin-dir", plugin_dir] if plugin_dir else []
    return [binary, "agent", "--no-leader", *plugin_args, "stdio"]


def require_acp_permissions(provider: str, permission_mode: str | None) -> None:
    provider_permission_args(provider, permission_mode)
    if permission_mode != "cli-managed":
        raise ValueError(
            f"{provider} cannot enforce Raticode shell, web and exact MCP restrictions. "
            "Select CLI-managed permissions explicitly to use the CLI's own policies and tools."
        )


async def stream_acp(
    provider: str,
    prompt: str,
    *,
    cwd: Path,
    model: str | None = None,
    effort: str | None = None,
    permission_mode: str | None = None,
    resources: AgentResources | None = None,
    executable: str | None = None,
    env: dict[str, str] | None = None,
    cancel_event: threading.Event | None = None,
    timeout: float | None = None,
    max_output_bytes: int | None = None,
    trusted_swarm_url: str | None = None,
    second_brain_cli_path: Path | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    require_acp_permissions(provider, permission_mode)
    command = acp_command(provider, executable)
    if provider == "grok":
        await grok_build_version(command[0], cancel_event)
    # Native auto-approvals stay native. Requests which still require interactive
    # approval are denied; this adapter never silently chooses bypass/YOLO.
    with acp_session_config(
        provider,
        cwd,
        resources or AgentResources(),
        trusted_swarm_url=trusted_swarm_url,
        second_brain_cli_path=second_brain_cli_path,
    ) as config:
        if provider == "grok":
            # This process belongs to one turn. Startup injection also works when
            # Grok advertises session pluginDirs but does not load those servers.
            command = acp_command(provider, command[0], config.params["_meta"]["pluginDirs"][0])
        async with open_acp_transport(
            command,
            cwd=cwd,
            env=env,
            cancel_event=cancel_event,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            request_handler=deny_acp_permission,
        ) as rpc:
            selected_effort = effort if effort != "cli-default" else None
            if selected_effort and provider != "grok":
                raise ValueError(f"{provider} does not support effort selection")
            if selected_effort and (not model or model == "cli-default"):
                config.params.setdefault("_meta", {})["reasoningEffort"] = selected_effort
            session = await initialize_session(rpc, config.params)
            if model and model != "cli-default":
                selection: dict[str, Any] = {"sessionId": session, "modelId": model}
                if selected_effort:
                    selection["_meta"] = {"reasoningEffort": selected_effort}
                await rpc.request("session/set_model", selection, timeout=30)
            if provider == "grok":
                await wait_grok_mcp(rpc, session, config.http_servers)
            source = prompt_session(rpc, session, prompt, timeout=timeout)
            try:
                async for event in source:
                    yield event
            finally:
                close = getattr(source, "aclose", None)
                if close:
                    await close()


class AcpSubscription(Subscription):
    def __init__(self, provider: str) -> None:
        if provider not in ACP_PROVIDERS:
            raise ValueError(f"Unknown ACP provider '{provider}'")
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
                "ACP providers require structured MCP resources, not legacy tool flags"
            )
        if provider_settings:
            if provider_settings.subscription != self.provider:
                raise ValueError("Provider profile belongs to another CLI")
            validate_provider_settings(provider_settings)
        require_acp_permissions(
            self.provider, provider_settings.approval_mode if provider_settings else None
        )
        return acp_command(self.provider)

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
        started = time.monotonic()
        command = self._build_command(prompt, tools, mcp_servers, extra_paths, provider_settings)
        final: dict[str, Any] = {"error": "Provider ended without a result", "exitCode": 1}
        thoughts: list[str] = []
        source = stream_acp(
            self.provider,
            prompt,
            cwd=working_dir,
            executable=command[0],
            env=env,
            model=provider_settings.model if provider_settings else None,
            effort=provider_settings.effort if provider_settings else None,
            permission_mode=provider_settings.approval_mode if provider_settings else None,
            timeout=timeout,
            cancel_event=cancel_event,
            max_output_bytes=max_output_bytes,
        )
        try:
            async for event in source:
                if event["type"] == "thought":
                    thoughts.append(event["text"])
                    if on_thought:
                        on_thought(event["text"])
                else:
                    final = event
        except (ValueError, OSError, AcpTransportError) as exc:
            final = {"error": str(exc), "exitCode": 1}
        finally:
            await source.aclose()
        text = (
            (final.get("message") or {}).get("body")
            or "".join(thoughts)
            or final.get("error")
            or ""
        )
        return AgentResult(
            agent_id="",
            success=not final.get("error"),
            output=text,
            message=text,
            exit_code=final["exitCode"],
            duration_seconds=time.monotonic() - started,
            thoughts=thoughts,
        )
