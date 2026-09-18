"""Discover the models and effort levels exposed by local provider CLIs.

The catalog intentionally comes from the installed CLI and its authenticated
account, rather than from a static list.  A workflow can still contain a model
that is not present on the UI host (for example when it will run on a remote
runner), so callers should use this data for UX and local execution checks,
not as a portability constraint when saving workflows.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from gofer.core.antigravity_models import split_antigravity_model
from gofer.core.cursor_models import split_cursor_model
from gofer.core.provider_permissions import CLAUDE_PERMISSION_MODES, CODEX_PERMISSION_MODES
from gofer.core.provider_preferences import provider_preference
from gofer.utils.process import env_with_executable_on_path, run_subprocess

ProviderId = Literal["codex", "claude_code", "cursor", "copilot", "opencode", "antigravity", "grok"]
CLI_PROVIDERS: tuple[ProviderId, ...] = (
    "codex",
    "claude_code",
    "cursor",
    "copilot",
    "opencode",
    "antigravity",
    "grok",
)
PROVIDER_BINARIES = {
    "codex": "codex",
    "claude_code": "claude",
    "cursor": "cursor-agent",
    "copilot": "copilot",
    "opencode": "opencode",
    "antigravity": "agy",
    "grok": "grok",
}
BROWSER_LOGIN_COMMANDS = {
    "cursor": ("login",),
    "codex": ("login",),
    "claude_code": ("auth", "login"),
    "copilot": ("login", "--web-flow"),
}
DISCOVERY_TIMEOUT_SECONDS = 10
DISCOVERY_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
CAPABILITY_CACHE_SECONDS = 300


class EffortCapability(BaseModel):
    id: str
    display_name: str
    description: str | None = None


class ModelCapability(BaseModel):
    id: str
    display_name: str
    description: str | None = None
    default_effort: str | None = None
    efforts: list[EffortCapability] = Field(default_factory=list)
    is_default: bool = False


class ProviderCapability(BaseModel):
    id: ProviderId
    display_name: str
    available: bool
    discovery_status: Literal[
        "ready",
        "missing",
        "unauthenticated",
        "access_denied",
        "unsupported_cli_version",
        "timeout",
        "invalid_response",
        "error",
    ]
    supports_custom_model: bool = False
    version: str | None = None
    default_model: str | None = None
    models: list[ModelCapability] = Field(default_factory=list)
    error: str | None = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def to_ui_payload(self) -> dict[str, Any]:
        preference = provider_preference(self.id)
        executable = resolve_provider_executable(self.id)
        override_model = preference.get("defaultModel", "")
        override_effort = preference.get("defaultEffort", "")
        default_model = next(
            (model.id for model in self.models if model.id == override_model),
            self.default_model,
        )
        # Apply preferences to the response, never to the cached discovery result.
        # Stale overrides remain saved but cannot select an unavailable model/effort.
        effort_model = next((model for model in self.models if model.id == default_model), None)
        effective_effort = (
            override_effort
            if effort_model
            and (not override_model or override_model == default_model)
            and any(effort.id == override_effort for effort in effort_model.efforts)
            else None
        )
        return {
            "enabled": preference.get("enabled", executable is not None),
            "executable": executable,
            "executableOverride": preference.get("executable", ""),
            "detected": executable is not None,
            "id": self.id,
            "displayName": self.display_name,
            "available": self.available,
            "discoveryStatus": self.discovery_status,
            "supportsBrowserLogin": self.id in BROWSER_LOGIN_COMMANDS,
            "version": self.version,
            "supportsCustomModel": self.supports_custom_model
            or self.id in {"cursor", "copilot", "opencode", "antigravity", "grok"},
            "permissionModes": [
                {
                    "id": mode,
                    "displayName": (
                        "CLI-managed permissions"
                        if mode == "cli-managed"
                        else "Strict resources, unavailable"
                        if mode == "default" and self.id in {"antigravity", "grok"}
                        else "CLI default"
                        if mode == "default"
                        else mode
                    ),
                }
                for mode in (
                    CODEX_PERMISSION_MODES
                    if self.id == "codex"
                    else CLAUDE_PERMISSION_MODES
                    if self.id == "claude_code"
                    else ("default", "cli-managed")
                    if self.id in {"antigravity", "grok"}
                    else ("default",)
                )
            ],
            "defaultPermissionMode": "workspace-write"
            if self.id == "codex"
            else "dontAsk"
            if self.id == "claude_code"
            else "cli-managed"
            if self.id == "grok"
            else "default",
            "defaultModel": default_model,
            "providerDefaultModel": self.default_model,
            "defaultModelOverride": override_model,
            "defaultEffortOverride": override_effort,
            "models": [
                {
                    "id": model.id,
                    "displayName": model.display_name,
                    "description": model.description,
                    "defaultEffort": effective_effort
                    if model.id == default_model and effective_effort is not None
                    else model.default_effort,
                    "providerDefaultEffort": model.default_effort,
                    "efforts": [
                        {
                            "id": effort.id,
                            "displayName": effort.display_name,
                            "description": effort.description,
                        }
                        for effort in model.efforts
                    ],
                    "isDefault": model.id == default_model,
                }
                for model in self.models
            ],
            "error": self.error,
            "discoveredAt": self.discovered_at.isoformat(),
        }


class ProviderCapabilityError(ValueError):
    pass


@dataclass(frozen=True)
class _CacheKey:
    provider_id: ProviderId
    executable: str | None
    executable_mtime_ns: int | None


@dataclass
class _CacheEntry:
    expires_at: float
    capability: ProviderCapability


class ProviderCapabilityProbe:
    provider_id: ProviderId
    display_name: str
    binary_name: str

    async def discover(self, executable: str | None = None) -> ProviderCapability:
        binary = executable or resolve_provider_executable(self.provider_id)
        if binary is None:
            return ProviderCapability(
                id=self.provider_id,
                display_name=self.display_name,
                available=False,
                discovery_status="missing",
            )

        version = await _cli_version(
            binary, "version" if self.provider_id == "grok" else "--version"
        )
        try:
            return await self._discover_available(binary, version)
        except TimeoutError:
            return ProviderCapability(
                id=self.provider_id,
                display_name=self.display_name,
                available=True,
                discovery_status="timeout",
                version=version,
                error="Model discovery timed out.",
            )
        except _UnsupportedCliError:
            return ProviderCapability(
                id=self.provider_id,
                display_name=self.display_name,
                available=True,
                discovery_status="unsupported_cli_version",
                version=version,
                error="This CLI version does not expose a model catalog.",
            )
        except _AccessDeniedError as exc:
            return ProviderCapability(
                id=self.provider_id,
                display_name=self.display_name,
                available=True,
                discovery_status="access_denied",
                version=version,
                error=str(exc),
            )
        except _UnauthenticatedError as exc:
            return ProviderCapability(
                id=self.provider_id,
                display_name=self.display_name,
                available=True,
                discovery_status="unauthenticated",
                version=version,
                error=str(exc) or "Sign in to this provider CLI to discover available models.",
            )
        except _InvalidCatalogError:
            return ProviderCapability(
                id=self.provider_id,
                display_name=self.display_name,
                available=True,
                discovery_status="invalid_response",
                version=version,
                error="The provider returned an invalid model catalog.",
            )
        except OSError:
            return ProviderCapability(
                id=self.provider_id,
                display_name=self.display_name,
                available=True,
                discovery_status="error",
                version=version,
                error="The provider CLI could not be started.",
            )
        except Exception:  # noqa: BLE001
            return ProviderCapability(
                id=self.provider_id,
                display_name=self.display_name,
                available=True,
                discovery_status="error",
                version=version,
                error="Model discovery failed.",
            )

    async def _discover_available(
        self,
        executable: str,
        version: str | None,
    ) -> ProviderCapability:
        raise NotImplementedError


class CodexCapabilityProbe(ProviderCapabilityProbe):
    provider_id: ProviderId = "codex"
    display_name = "Codex"
    binary_name = "codex"

    async def _discover_available(
        self,
        executable: str,
        version: str | None,
    ) -> ProviderCapability:
        returncode, stdout, stderr = await _run_probe([executable, "debug", "models"])
        if returncode != 0:
            if _looks_unsupported(stdout, stderr):
                raise _UnsupportedCliError
            if _looks_unauthenticated(stdout, stderr):
                raise _UnauthenticatedError
            raise _InvalidCatalogError
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise _InvalidCatalogError from exc
        raw_models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(raw_models, list):
            raise _InvalidCatalogError

        models: list[tuple[int, ModelCapability]] = []
        for raw_model in raw_models:
            if not isinstance(raw_model, dict) or raw_model.get("visibility") != "list":
                continue
            model_id = _nonempty_string(raw_model.get("slug"))
            if model_id is None:
                continue
            efforts = _efforts_from_codex(raw_model.get("supported_reasoning_levels"))
            models.append(
                (
                    _positive_int(raw_model.get("priority")),
                    ModelCapability(
                        id=model_id,
                        display_name=_nonempty_string(raw_model.get("display_name")) or model_id,
                        description=_nonempty_string(raw_model.get("description")),
                        default_effort=_nonempty_string(raw_model.get("default_reasoning_level")),
                        efforts=efforts,
                    ),
                )
            )
        models.sort(key=lambda item: item[0])
        if not models:
            raise _InvalidCatalogError
        ordered_models = [model for _, model in models]
        default_model = ordered_models[0].id
        ordered_models[0].is_default = True
        return ProviderCapability(
            id=self.provider_id,
            display_name=self.display_name,
            available=True,
            discovery_status="ready",
            version=version,
            default_model=default_model,
            models=ordered_models,
        )


class ClaudeCodeCapabilityProbe(ProviderCapabilityProbe):
    """Discover Claude Code's account-scoped aliases with its slash commands."""

    provider_id: ProviderId = "claude_code"
    display_name = "Claude Code"
    binary_name = "claude"

    async def _discover_available(
        self,
        executable: str,
        version: str | None,
    ) -> ProviderCapability:
        command = [
            executable,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "/model",
        ]
        returncode, stdout, stderr = await _run_probe(command)
        if returncode != 0:
            if _looks_unsupported(stdout, stderr):
                raise _UnsupportedCliError
            if _looks_unauthenticated(stdout, stderr):
                raise _UnauthenticatedError
            raise _InvalidCatalogError
        current_model, aliases = _claude_models_from_slash_command(stdout)
        if current_model is None or not aliases:
            if _looks_unauthenticated(stdout, stderr):
                raise _UnauthenticatedError
            raise _InvalidCatalogError

        effort_command = [*command[:-1], "/effort"]
        returncode, effort_stdout, effort_stderr = await _run_probe(effort_command)
        if returncode != 0:
            if _looks_unauthenticated(effort_stdout, effort_stderr):
                raise _UnauthenticatedError
            raise _InvalidCatalogError
        efforts = _claude_efforts_from_slash_command(effort_stdout)
        if not efforts:
            raise _InvalidCatalogError

        model_ids = [current_model, *[alias for alias in aliases if alias != current_model]]
        models = [
            ModelCapability(
                id=model_id,
                display_name=_display_name(model_id),
                default_effort="high" if model_id == current_model else None,
                efforts=efforts,
                is_default=model_id == current_model,
            )
            for model_id in model_ids
        ]
        return ProviderCapability(
            id=self.provider_id,
            display_name=self.display_name,
            available=True,
            discovery_status="ready",
            version=version,
            default_model=current_model,
            models=models,
        )


class ProviderCapabilityService:
    """Host-scoped, thread-safe catalog cache for UI and execution validation."""

    def __init__(self) -> None:
        self._probes: dict[ProviderId, ProviderCapabilityProbe] = {
            "codex": CodexCapabilityProbe(),
            "claude_code": ClaudeCodeCapabilityProbe(),
            **{
                cast(ProviderId, provider): AdditionalCliCapabilityProbe(cast(ProviderId, provider))
                for provider in ("cursor", "copilot", "opencode", "antigravity", "grok")
            },
        }
        self._cache: dict[_CacheKey, _CacheEntry] = {}
        self._inflight: dict[_CacheKey, threading.Event] = {}
        self._lock = threading.RLock()

    def payload(self, *, refresh: bool = False) -> dict[str, list[dict[str, Any]]]:
        _require_sync_context("ProviderCapabilityService.payload", "payload_async")
        return asyncio.run(self.payload_async(refresh=refresh))

    def provider(
        self,
        provider_id: ProviderId,
        *,
        refresh: bool = False,
    ) -> ProviderCapability:
        _require_sync_context("ProviderCapabilityService.provider", "provider_async")
        probe = self._probes[provider_id]
        executable = resolve_provider_executable(provider_id)
        key = _CacheKey(provider_id, executable, _executable_mtime_ns(executable))
        now = time.monotonic()
        wait_for: threading.Event | None = None
        is_owner = False
        with self._lock:
            cached = self._cache.get(key)
            if not refresh and cached is not None and cached.expires_at > now:
                return cached.capability
            wait_for = self._inflight.get(key)
            if wait_for is None:
                wait_for = threading.Event()
                self._inflight[key] = wait_for
                is_owner = True
        if not is_owner:
            if wait_for is None:
                return self.provider(provider_id, refresh=False)
            wait_for.wait(DISCOVERY_TIMEOUT_SECONDS + 1)
            return self.provider(provider_id, refresh=False)

        try:
            capability = asyncio.run(probe.discover(executable))
            with self._lock:
                self._cache[key] = _CacheEntry(
                    expires_at=time.monotonic() + CAPABILITY_CACHE_SECONDS,
                    capability=capability,
                )
            return capability
        finally:
            with self._lock:
                event = self._inflight.pop(key, None)
                if event is not None:
                    event.set()

    async def payload_async(
        self,
        *,
        refresh: bool = False,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return UI capabilities without blocking or nesting an event loop."""
        # Each CLI has its own discovery deadline. Start them together so a
        # slow provider does not delay the start of every provider after it.
        capabilities = await asyncio.gather(
            *(self.provider_async(provider_id, refresh=refresh) for provider_id in self._probes)
        )
        return {"providers": [capability.to_ui_payload() for capability in capabilities]}

    async def provider_async(
        self,
        provider_id: ProviderId,
        *,
        refresh: bool = False,
    ) -> ProviderCapability:
        """Discover provider capabilities without nesting an event loop."""
        probe = self._probes[provider_id]
        executable = resolve_provider_executable(provider_id)
        key = _CacheKey(provider_id, executable, _executable_mtime_ns(executable))
        now = time.monotonic()
        wait_for: threading.Event | None = None
        is_owner = False
        with self._lock:
            cached = self._cache.get(key)
            if not refresh and cached is not None and cached.expires_at > now:
                return cached.capability
            wait_for = self._inflight.get(key)
            if wait_for is None:
                wait_for = threading.Event()
                self._inflight[key] = wait_for
                is_owner = True
        if not is_owner:
            deadline = time.monotonic() + DISCOVERY_TIMEOUT_SECONDS + 1
            while wait_for is not None and not wait_for.is_set():
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(0.01)
            return await self.provider_async(provider_id, refresh=False)

        try:
            capability = await probe.discover(executable)
            with self._lock:
                self._cache[key] = _CacheEntry(
                    expires_at=time.monotonic() + CAPABILITY_CACHE_SECONDS,
                    capability=capability,
                )
            return capability
        finally:
            with self._lock:
                event = self._inflight.pop(key, None)
                if event is not None:
                    event.set()


def _validate_capability_selection(
    capability: ProviderCapability,
    model: str | None,
    effort: str | None,
) -> None:
    if capability.id in {"cursor", "copilot", "antigravity", "grok"} and effort == "cli-default":
        effort = None
    if capability.id in {"cursor", "copilot", "antigravity", "grok"} and model == "cli-default":
        model = None
    if not capability.available:
        raise ProviderCapabilityError(f"{capability.display_name} CLI is not available on PATH")
    if capability.discovery_status != "ready":
        return
    selected_model = model or capability.default_model
    selected = next((item for item in capability.models if item.id == selected_model), None)
    if selected is None and capability.id == "antigravity" and selected_model:
        base, native_effort = split_antigravity_model(selected_model)
        selected = next(
            (
                item
                for item in capability.models
                if split_antigravity_model(item.id)[0] == base
                and native_effort in {entry.id for entry in item.efforts}
            ),
            None,
        )
    if selected is None and capability.supports_custom_model and not effort:
        return
    if selected is None:
        raise ProviderCapabilityError(
            f"Model '{selected_model}' is not available in {capability.display_name} on this host"
        )
    if effort and effort not in {item.id for item in selected.efforts}:
        raise ProviderCapabilityError(
            f"Effort '{effort}' is not supported by model '{selected_model}'"
        )


def validate_provider_selection(
    provider_id: str,
    model: str | None,
    effort: str | None,
    *,
    service: ProviderCapabilityService | None = None,
) -> None:
    """Strictly validate an immediate local execution selection.

    An unavailable discovery result is intentionally not treated as a failed
    selection: the provider CLI remains the final authority, and workflows need
    to be portable to other hosts.
    """

    if provider_id not in CLI_PROVIDERS:
        raise ProviderCapabilityError(f"Unknown provider '{provider_id}'")
    _require_sync_context("validate_provider_selection", "validate_provider_selection_async")
    capability = (service or provider_capability_service()).provider(provider_id)
    _validate_capability_selection(capability, model, effort)


async def validate_provider_selection_async(
    provider_id: str,
    model: str | None,
    effort: str | None,
    *,
    service: ProviderCapabilityService | None = None,
) -> None:
    """Validate a provider selection from within an active event loop."""
    if provider_id not in CLI_PROVIDERS:
        raise ProviderCapabilityError(f"Unknown provider '{provider_id}'")
    capability = await (service or provider_capability_service()).provider_async(
        provider_id
    )
    _validate_capability_selection(capability, model, effort)


_service: ProviderCapabilityService | None = None
_service_lock = threading.Lock()


def provider_capability_service() -> ProviderCapabilityService:
    global _service
    with _service_lock:
        if _service is None:
            _service = ProviderCapabilityService()
        return _service


def provider_capabilities_payload(*, refresh: bool = False) -> dict[str, list[dict[str, Any]]]:
    _require_sync_context("provider_capabilities_payload", "provider_capabilities_payload_async")
    return provider_capability_service().payload(refresh=refresh)


async def provider_capabilities_payload_async(
    *,
    refresh: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    """Return the shared provider payload from async server code."""
    return await provider_capability_service().payload_async(refresh=refresh)


def _require_sync_context(sync_name: str, async_name: str) -> None:
    """Reject sync discovery APIs before they can nest an asyncio event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        f"{sync_name}() cannot be called from a running event loop; await {async_name}() instead."
    )


def resolve_provider_executable(provider_id: ProviderId) -> str | None:
    """Return the executable the backend will use for a local provider.

    Desktop applications do not source shell startup files, so an npm global
    install managed by nvm is often absent from their PATH.  Check nvm's
    selected/default Node installation as a narrow fallback for providers
    installed as npm-global packages (Claude Code, Codex).
    """

    override = provider_preference(provider_id).get("executable")
    if override:
        path = Path(override)
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None

    binary_name = PROVIDER_BINARIES[provider_id]
    if executable := shutil.which(binary_name):
        return executable

    if provider_id == "cursor":
        agent_executable = shutil.which("agent")
        if agent_executable and _is_cursor_agent(agent_executable):
            return agent_executable

    nvm_dir = Path(os.environ.get("NVM_DIR", Path.home() / ".nvm"))
    versions_dir = nvm_dir / "versions" / "node"
    candidates: list[Path] = []
    default_path = nvm_dir / "alias" / "default"
    try:
        default_version = default_path.read_text(encoding="utf-8").strip()
    except OSError:
        default_version = ""
    if default_version:
        candidates.append(versions_dir / default_version / "bin" / binary_name)
    try:
        candidates.extend(sorted(versions_dir.glob(f"*/bin/{binary_name}"), reverse=True))
    except OSError:
        pass
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


async def _cli_version(executable: str, version_arg: str = "--version") -> str | None:
    try:
        returncode, stdout, _stderr = await _run_probe([executable, version_arg])
    except (OSError, TimeoutError):
        return None
    if returncode != 0:
        return None
    version = stdout.strip()
    return version or None


async def _run_probe(
    command: list[str],
    *,
    stdin: bytes | None = None,
) -> tuple[int, str, str]:
    try:
        return await run_subprocess(
            command,
            env=env_with_executable_on_path(command[0]),
            timeout=DISCOVERY_TIMEOUT_SECONDS,
            stdin=stdin,
            max_output_bytes=DISCOVERY_MAX_OUTPUT_BYTES,
        )
    except TimeoutError:
        raise


def _efforts_from_codex(value: Any) -> list[EffortCapability]:
    if not isinstance(value, list):
        return []
    efforts: list[EffortCapability] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        effort_id = _nonempty_string(item.get("effort"))
        if effort_id is None:
            continue
        efforts.append(
            EffortCapability(
                id=effort_id,
                display_name=effort_id.replace("_", " ").title(),
                description=_nonempty_string(item.get("description")),
            )
        )
    return efforts


def _claude_models_from_slash_command(stdout: str) -> tuple[str | None, list[str]]:
    current_model: str | None = None
    model_text = ""
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "system" and payload.get("subtype") == "init":
            current_model = _nonempty_string(payload.get("model")) or current_model
        model_text += _claude_response_text(payload)
    match = re.search(r"Available:\s*(.+?)(?:\.\s*)?$", model_text, re.MULTILINE)
    if match is None:
        return current_model, []
    available = match.group(1).removesuffix("or a full model ID").strip(" ,")
    aliases = [item.strip() for item in re.split(r",|\bor\b", available) if item.strip()]
    return current_model, aliases


def _claude_efforts_from_slash_command(stdout: str) -> list[EffortCapability]:
    text = "".join(
        _claude_response_text(payload)
        for line in stdout.splitlines()
        if (payload := _json_object(line)) is not None
    )
    match = re.search(r"Usage:\s*/effort\s*<([^>]+)>", text)
    if match is None:
        return []
    return [
        EffortCapability(id=effort, display_name=_display_name(effort))
        for item in match.group(1).split("|")
        if (effort := _nonempty_string(item)) is not None
    ]


def _json_object(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _claude_response_text(payload: dict[str, Any]) -> str:
    result = _nonempty_string(payload.get("result"))
    if result is not None:
        return f"{result}\n"
    message = payload.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(
        f"{text}\n"
        for block in content
        if isinstance(block, dict) and (text := _nonempty_string(block.get("text"))) is not None
    )


def _display_name(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()


def _looks_unsupported(stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    return "unknown option" in text or "unknown command" in text or "unsupported" in text


def _looks_unauthenticated(stdout: str, stderr: str) -> bool:
    text = f"{stdout}\n{stderr}".lower()
    return (
        "not logged" in text
        or "not authenticated" in text
        or "authentication required" in text
        or ("login" in text and "required" in text)
    )


def _nonempty_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _positive_int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 10_000


def _executable_mtime_ns(executable: str | None) -> int | None:
    if executable is None:
        return None
    try:
        return Path(executable).stat().st_mtime_ns
    except OSError:
        return None


class _UnsupportedCliError(Exception):
    pass


class _UnauthenticatedError(Exception):
    pass


class _AccessDeniedError(Exception):
    pass


class _InvalidCatalogError(Exception):
    pass


def _is_cursor_agent(executable: str) -> bool:
    import subprocess

    try:
        result = subprocess.run(
            [executable, "--help"],
            capture_output=True,
            text=True,
            timeout=DISCOVERY_TIMEOUT_SECONDS,
            env={**os.environ, **env_with_executable_on_path(executable)},
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "Start the Cursor Agent" in result.stdout


def _cursor_models_from_listing(stdout: str) -> list[ModelCapability]:
    """Parse Cursor's human catalog, preserving native variant suffixes."""
    groups: dict[str, ModelCapability] = {}
    seen: set[str] = set()
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", stdout)
    for line in text.splitlines():
        match = re.fullmatch(r"\s*([\w][\w.-]*)\s+-\s+(\S.*?)\s*", line)
        if not match:
            continue
        native_id, label = match.groups()
        marker = re.search(r"\s+\((?:current|default)(?:,\s*(?:current|default))*\)$", label)
        is_default = bool(marker and "default" in marker[0])
        if marker:
            label = label[: marker.start()]
        base, suffix = split_cursor_model(native_id)
        effort = suffix or "cli-default"
        display_name = label
        if suffix:
            for word in suffix.replace("extra-high", "Extra High").split("-"):
                word = "Extra High" if word == "xhigh" else word
                display_name = re.sub(rf"\b{re.escape(word)}\b", "", display_name, flags=re.I)
            display_name = " ".join(display_name.split())
        model = groups.setdefault(base, ModelCapability(id=base, display_name=display_name or base))
        if native_id not in seen:
            model.efforts.append(
                EffortCapability(
                    id=effort,
                    display_name="CLI default" if not suffix else suffix.replace("-", " ").title(),
                    description=label,
                )
            )
            seen.add(native_id)
        # Prefer a real unsuffixed entry; otherwise use the first advertised
        # variant. An explicit default marker takes precedence over both.
        if model.default_effort is None or (not suffix and not model.is_default) or is_default:
            model.default_effort = effort
        if not suffix:
            model.display_name = label
        model.is_default |= is_default
    for model in groups.values():
        if [effort.id for effort in model.efforts] == ["cli-default"]:
            model.efforts = []
            model.default_effort = None
    return list(groups.values())


def _acp_models_from_catalog(payload: dict[str, Any]) -> list[ModelCapability]:
    state = payload.get("models")
    if not isinstance(state, dict) or not isinstance(state.get("availableModels"), list):
        raise _InvalidCatalogError
    current = _nonempty_string(state.get("currentModelId"))
    models: dict[str, ModelCapability] = {}
    for raw in state["availableModels"]:
        if not isinstance(raw, dict) or not (model_id := _nonempty_string(raw.get("modelId"))):
            continue
        models.setdefault(
            model_id,
            ModelCapability(
                id=model_id,
                display_name=_nonempty_string(raw.get("name")) or model_id,
                is_default=model_id == current,
            ),
        )
    if not models:
        raise _InvalidCatalogError
    return list(models.values())


async def _copilot_model_catalog(executable: str) -> dict[str, Any]:
    """Use the CLI's SDK catalog RPC without creating a chat or sending a prompt."""
    import tempfile

    from gofer.subscriptions.acp_transport import (
        AcpRpcError,
        AcpTransportError,
        open_acp_transport,
    )

    with tempfile.TemporaryDirectory(prefix="raticode-copilot-catalog-") as directory:
        async with open_acp_transport(
            [
                executable,
                "--headless",
                "--stdio",
                "--no-auto-update",
                "--log-level",
                "none",
                "--add-dir",
                directory,
            ],
            cwd=Path(directory),
            timeout=DISCOVERY_TIMEOUT_SECONDS,
            max_output_bytes=DISCOVERY_MAX_OUTPUT_BYTES,
            content_length_framing=True,
        ) as rpc:
            try:
                return await rpc.request("models.list", {}, timeout=DISCOVERY_TIMEOUT_SECONDS)
            except AcpRpcError as exc:
                message = str(exc).lower()
                if "not authorized to use this copilot feature" in message:
                    raise _AccessDeniedError(
                        "GitHub denied access to Copilot's model catalog. Open Copilot CLI in "
                        "your project and complete its directory trust and account setup, then "
                        "refresh providers. If access is still denied, check your GitHub "
                        "Copilot settings or organization policy."
                    ) from exc
                if _looks_unauthenticated(message, "") or any(
                    word in message
                    for word in (
                        "unauthorized",
                        "not authorized",
                        "not signed in",
                        "no authentication",
                    )
                ):
                    raise _UnauthenticatedError from exc
                if exc.code == -32601:
                    raise _UnsupportedCliError from exc
                raise _InvalidCatalogError from exc
            except AcpTransportError as exc:
                if any(word in str(exc).lower() for word in ("timed out", "time limit")):
                    raise TimeoutError from exc
                if _looks_unsupported("", rpc.stderr):
                    raise _UnsupportedCliError from exc
                raise _InvalidCatalogError from exc


def _copilot_models_from_catalog(payload: dict[str, Any]) -> list[ModelCapability]:
    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        raise _InvalidCatalogError
    models: dict[str, ModelCapability] = {}
    for raw in raw_models:
        if not isinstance(raw, dict):
            continue
        model_id = _nonempty_string(raw.get("id"))
        policy = raw.get("policy")
        if not model_id or (isinstance(policy, dict) and policy.get("state") == "disabled"):
            continue
        raw_efforts = raw.get("supportedReasoningEfforts")
        efforts = list(
            dict.fromkeys(
                effort
                for value in (raw_efforts if isinstance(raw_efforts, list) else [])
                if (effort := _nonempty_string(value)) is not None
            )
        )
        default = _nonempty_string(raw.get("defaultReasoningEffort"))
        models.setdefault(
            model_id,
            ModelCapability(
                id=model_id,
                display_name=_nonempty_string(raw.get("name")) or model_id,
                default_effort=default if default in efforts else None,
                efforts=[
                    EffortCapability(id=value, display_name=_display_name(value))
                    for value in efforts
                ],
            ),
        )
    if not models:
        raise _InvalidCatalogError
    return list(models.values())


class AdditionalCliCapabilityProbe(ProviderCapabilityProbe):
    """Read documented text catalogs without inventing JSON discovery options."""

    def __init__(self, provider_id: ProviderId) -> None:
        self.provider_id = provider_id
        self.display_name = {
            "cursor": "Cursor",
            "copilot": "GitHub Copilot",
            "opencode": "OpenCode",
            "antigravity": "Antigravity",
            "grok": "xAI Grok",
        }[provider_id]
        self.binary_name = PROVIDER_BINARIES[provider_id]

    async def _discover_available(self, executable: str, version: str | None) -> ProviderCapability:
        capability = ProviderCapability(
            id=self.provider_id,
            display_name=self.display_name,
            available=True,
            discovery_status="unsupported_cli_version",
            supports_custom_model=True,
            version=version,
            error=(
                "This CLI does not expose a model catalog. Refresh after updating the provider CLI."
            ),
        )
        if self.provider_id == "antigravity":
            code, stdout, stderr = await _run_probe([executable, "models"])
            if code != 0:
                if "sign in" in (stdout + stderr).lower() or _looks_unauthenticated(stdout, stderr):
                    raise _UnauthenticatedError(
                        "Run agy in a terminal to sign in to Antigravity, then refresh providers."
                    )
                if _looks_unsupported(stdout, stderr):
                    raise _UnsupportedCliError
                raise _InvalidCatalogError
            capability.models = _antigravity_models_from_listing(stdout)
            capability.discovery_status = "ready"
            capability.error = None
            return capability
        if self.provider_id == "copilot":
            capability.models = _copilot_models_from_catalog(
                await _copilot_model_catalog(executable)
            )
            capability.discovery_status = "ready"
            capability.error = None
            return capability
        code, stdout, stderr = await _run_probe([executable, "models"])
        if code != 0:
            if _looks_unauthenticated(stdout, stderr):
                raise _UnauthenticatedError
            if _looks_unsupported(stdout, stderr):
                return capability
            raise _InvalidCatalogError
        models = _cursor_models_from_listing(stdout) if self.provider_id == "cursor" else []
        if self.provider_id == "grok":
            models = _grok_models_from_listing(stdout)
            if not models:
                raise _InvalidCatalogError
            try:
                models = _grok_models_from_catalog(await _grok_model_catalog(executable))
            except (OSError, TimeoutError, _InvalidCatalogError, _UnsupportedCliError):
                # Older CLIs still provide a usable list, without invented efforts.
                pass
        for line in stdout.splitlines():
            value = line.strip()
            if self.provider_id == "opencode" and re.fullmatch(r"[\w.-]+/[^\s]+", value):
                models.append(ModelCapability(id=value, display_name=value))
        if models:
            capability.models = models
            capability.default_model = next(
                (model.id for model in models if model.is_default), None
            )
            capability.discovery_status = "ready"
            capability.error = None
        return capability


def _antigravity_models_from_listing(stdout: str) -> list[ModelCapability]:
    """Group advertised effort variants, retaining a native ID as the default."""
    models: dict[str, ModelCapability] = {}
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", stdout)
    for line in clean.splitlines():
        match = re.fullmatch(r"\s*([a-z0-9][a-z0-9._/-]*)\s+(\S.*?)\s*", line)
        if not match:
            continue
        slug, label = match.groups()
        base, effort = split_antigravity_model(slug)
        display = re.sub(r"\s*\((?:High|Medium|Low)\)$", "", label) if effort else label
        model = models.setdefault(
            base, ModelCapability(id=slug, display_name=display, default_effort=effort)
        )
        if effort and effort not in {item.id for item in model.efforts}:
            model.efforts.append(EffortCapability(id=effort, display_name=effort.title()))
    if not models:
        raise _InvalidCatalogError
    for model in models.values():
        model.efforts.sort(key=lambda item: ["low", "medium", "high"].index(item.id))
    return list(models.values())


async def _grok_model_catalog(executable: str) -> dict[str, Any]:
    """Read the metadata behind grok models without creating a session or prompt."""
    import tempfile

    from gofer.subscriptions.acp_transport import AcpTransportError, open_acp_transport

    with tempfile.TemporaryDirectory(prefix="raticode-grok-catalog-") as directory:
        try:
            async with open_acp_transport(
                [executable, "agent", "--no-leader", "stdio"],
                cwd=Path(directory),
                timeout=DISCOVERY_TIMEOUT_SECONDS,
                max_output_bytes=DISCOVERY_MAX_OUTPUT_BYTES,
            ) as rpc:
                hello = await rpc.request(
                    "initialize",
                    {"protocolVersion": 1, "clientCapabilities": {}},
                    timeout=DISCOVERY_TIMEOUT_SECONDS,
                )
                if hello.get("protocolVersion") != 1:
                    raise _UnsupportedCliError
                return await rpc.request("_x.ai/models/list", {}, timeout=DISCOVERY_TIMEOUT_SECONDS)
        except AcpTransportError as exc:
            raise _InvalidCatalogError from exc


def _grok_models_from_listing(stdout: str) -> list[ModelCapability]:
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", stdout)
    default = re.search(r"^Default model: (\S+)\s*$", clean, re.MULTILINE)
    models: dict[str, ModelCapability] = {}
    in_models = False
    for line in clean.splitlines():
        if line.strip() == "Available models:":
            in_models = True
            continue
        match = re.fullmatch(r"\s*[*-]\s+(\S+?)(?:\s+\(default\))?\s*", line)
        if in_models and match:
            model_id = match[1]
            models.setdefault(
                model_id,
                ModelCapability(
                    id=model_id,
                    display_name=model_id,
                    is_default=model_id == (default[1] if default else None)
                    or line.rstrip().endswith("(default)"),
                ),
            )
    return list(models.values())


def _grok_models_from_catalog(payload: dict[str, Any]) -> list[ModelCapability]:
    state = payload.get("result")
    if payload.get("error") or not isinstance(state, dict):
        raise _InvalidCatalogError
    models = _acp_models_from_catalog({"models": state})
    by_id = {model.id: model for model in models}
    for raw in state["availableModels"]:
        if not isinstance(raw, dict):
            continue
        model_id = _nonempty_string(raw.get("modelId"))
        if model_id not in by_id:
            continue
        model = by_id[model_id]
        meta = raw.get("_meta")
        if not isinstance(meta, dict) or meta.get("supportsReasoningEffort") is not True:
            continue
        options = meta.get("reasoningEfforts")
        if not isinstance(options, list):
            continue
        for option in options:
            if isinstance(option, str):
                option = {"value": option}
            if not isinstance(option, dict) or not (value := _nonempty_string(option.get("value"))):
                continue
            if value not in {item.id for item in model.efforts}:
                model.efforts.append(
                    EffortCapability(
                        id=value,
                        display_name=_nonempty_string(option.get("label")) or _display_name(value),
                        description=_nonempty_string(option.get("description")),
                    )
                )
            if option.get("default") is True:
                model.default_effort = value
        current = _nonempty_string(meta.get("reasoningEffort"))
        if current in {item.id for item in model.efforts}:
            model.default_effort = current
    return models
