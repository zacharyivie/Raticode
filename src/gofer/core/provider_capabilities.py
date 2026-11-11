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
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from gofer.core.antigravity_models import split_antigravity_model
from gofer.core.cursor_models import split_cursor_model
from gofer.core.provider_permissions import CLAUDE_PERMISSION_MODES, CODEX_PERMISSION_MODES
from gofer.core.provider_preferences import provider_preference
from gofer.utils.process import build_subprocess_env, env_with_executable_on_path, run_subprocess

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
# Providers whose Rem adapters can deliver images to the model.
IMAGE_ATTACHMENT_PROVIDERS = frozenset(
    {"codex", "claude_code", "cursor", "copilot", "opencode", "grok"}
)
# Checked against agy 1.5.4, which answers 'stream input content block type "image" is
# not supported (only "text")' to the input stream Raticode uses.
NO_IMAGE_ATTACHMENTS = {
    "antigravity": "Antigravity's command line only accepts text in the way Raticode talks to it"
}


def image_attachment_refusal(provider: str) -> str:
    """The message for an image sent to a thread whose provider can't receive it."""
    reason = NO_IMAGE_ATTACHMENTS.get(provider, f"{provider} can't receive images in Raticode")
    return f"{reason}, so it can't see attached images. Remove the image or switch provider."


BROWSER_LOGIN_COMMANDS = {
    "cursor": ("login",),
    "codex": ("login",),
    "claude_code": ("auth", "login"),
    "copilot": ("login", "--web-flow"),
}
DISCOVERY_TIMEOUT_SECONDS = 10
DISCOVERY_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
CAPABILITY_CACHE_SECONDS = 300
CLAUDE_CATALOG_REQUEST_ID = "raticode-model-catalog"
# The one message the catalog probe sends. It asks Claude Code to describe itself
# and never contains a user turn, so no model runs and no usage is spent.
CLAUDE_CATALOG_REQUEST = (
    json.dumps(
        {
            "type": "control_request",
            "request_id": CLAUDE_CATALOG_REQUEST_ID,
            "request": {"subtype": "initialize"},
        }
    )
    + "\n"
)
CLAUDE_ONE_MILLION_SUFFIX = "[1m]"
MAX_CLAUDE_MODELS = 100
# IDs reach the CLI as a --model value and the phone caps them at 160 characters.
_CLAUDE_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,159}")
_EFFORT_ID = re.compile(r"[a-z][a-z0-9_-]*")


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
    supports_images: bool | None = None
    # False for an alias of a model that is listed under its full ID. It still
    # validates and shows where it is already selected, but stays out of menus.
    listed: bool = True
    alias_of: str | None = None


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

    def to_ui_payload(
        self, *, executable: str | None = None, resolve_executable: bool = True
    ) -> dict[str, Any]:
        preference = provider_preference(self.id)
        if resolve_executable:
            executable = resolve_provider_executable(self.id)
        override_model = preference.get("defaultModel", "")
        override_effort = preference.get("defaultEffort", "")
        denied_models = preference.get("deniedModels", [])
        blocked_models = {*denied_models, *self._blocked_model_ids(denied_models)}
        allowed_models = [model for model in self.models if model.id not in blocked_models]
        default_model = next(
            (model.id for model in allowed_models if model.id == override_model),
            self.default_model
            if self.default_model not in blocked_models
            else next((model.id for model in allowed_models if model.listed), None),
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
        payload: dict[str, Any] = {
            "enabled": preference.get("enabled", executable is not None),
            "executable": executable,
            "executableOverride": preference.get("executable", ""),
            "detected": executable is not None,
            "id": self.id,
            "displayName": self.display_name,
            "available": self.available,
            "discoveryStatus": self.discovery_status,
            "supportsBrowserLogin": self.id in BROWSER_LOGIN_COMMANDS,
            "supportsImages": self.id in IMAGE_ATTACHMENT_PROVIDERS,
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
            "deniedModels": denied_models,
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
                    "supportsImages": model.supports_images,
                    "listed": model.listed,
                    "aliasOf": model.alias_of,
                }
                for model in self.models
            ],
            "error": self.error,
            "discoveredAt": self.discovered_at.isoformat(),
        }
        payload["discoveredModels"] = payload["models"]
        payload["models"] = [
            model for model in payload["models"] if model["id"] not in blocked_models
        ]
        return payload

    def alias_targets(self) -> dict[str, str]:
        return {model.id: model.alias_of for model in self.models if model.alias_of}

    def _blocked_model_ids(self, denied: list[str]) -> list[str]:
        aliases = self.alias_targets()
        return [
            model.id
            for model in self.models
            if model_block_applies(self.id, model.id, denied, aliases)
        ]


class ProviderCapabilityError(ValueError):
    pass


def model_block_applies(
    provider_id: str, model: str, denied: Collection[str], aliases: Mapping[str, str]
) -> bool:
    """Whether a saved block covers this model, directly or through its alias.

    Blocks match the exact ID saved. A hidden alias can't be blocked on its own,
    so it follows the full model it stands for. Without a catalog that names the
    aliases, one that may mean a blocked model's family is refused rather than run.
    """
    if model in denied:
        return True
    if provider_id != "claude_code":
        return False
    if model in aliases:
        return aliases[model] in denied
    if aliases or not re.fullmatch(r"[a-z]+", model):
        return False
    return any(item.startswith(f"claude-{model}-") for item in denied)


def _block_verdict_without_catalog(
    provider_id: str, model: str | None
) -> tuple[bool | None, list[str]]:
    denied = list(provider_preference(provider_id).get("deniedModels", []))
    if not model:
        return False, denied
    if model in denied:
        return True, denied
    # Only Claude Code has aliases, and with no blocks there is nothing to resolve.
    return (False if provider_id != "claude_code" or not denied else None), denied


def model_is_blocked(provider_id: str, model: str | None) -> bool:
    """Check a model against the saved blocks, discovering aliases only if needed."""
    verdict, denied = _block_verdict_without_catalog(provider_id, model)
    if verdict is not None:
        return verdict
    capability = provider_capability_service().provider(cast(ProviderId, provider_id))
    return model_block_applies(provider_id, str(model), denied, capability.alias_targets())


async def model_is_blocked_async(provider_id: str, model: str | None) -> bool:
    """model_is_blocked for callers already inside an event loop."""
    verdict, denied = _block_verdict_without_catalog(provider_id, model)
    if verdict is not None:
        return verdict
    capability = await provider_capability_service().provider_async(cast(ProviderId, provider_id))
    return model_block_applies(provider_id, str(model), denied, capability.alias_targets())


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
    """Discover the models Claude Code offers this account, by asking Claude Code.

    Its SDK handshake reports the same catalog as the interactive /model picker,
    with full model IDs. When that fails, the aliases printed by /model still
    give a usable list.
    """

    provider_id: ProviderId = "claude_code"
    display_name = "Claude Code"
    binary_name = "claude"

    async def _discover_available(
        self,
        executable: str,
        version: str | None,
    ) -> ProviderCapability:
        reported = await self._reported_models(executable)
        command = [executable, "--print", "--output-format", "stream-json", "--verbose"]
        current_model: str | None = None
        aliases: list[str] = []
        efforts: list[EffortCapability] = []
        try:
            current_model, aliases = await self._slash_models(command)
            efforts = await self._slash_efforts(command)
        except _UnauthenticatedError:
            raise
        except (TimeoutError, OSError, _UnsupportedCliError, _InvalidCatalogError):
            if not reported:
                raise

        default_model: str | None = current_model
        if reported:
            models, default_model = _claude_catalog(reported, current_model, aliases, efforts)
        else:
            models = [
                ModelCapability(
                    id=model_id,
                    display_name=_claude_alias_name(model_id),
                    default_effort="high" if model_id == current_model else None,
                    efforts=efforts,
                    is_default=model_id == current_model,
                )
                for model_id in [current_model, *[a for a in aliases if a != current_model]]
                if model_id is not None
            ]
        return ProviderCapability(
            id=self.provider_id,
            display_name=self.display_name,
            available=True,
            discovery_status="ready",
            # The alias list can't name every model, and the CLI accepts a full ID.
            supports_custom_model=not reported,
            version=version,
            default_model=default_model,
            models=models,
        )

    async def _reported_models(self, executable: str) -> list[_ClaudeReportedModel]:
        """Claude Code's own catalog, or [] so the caller can use the alias list."""
        command = [
            executable,
            "--print",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--no-session-persistence",
        ]
        try:
            returncode, stdout, _stderr = await _run_probe(
                command, stdin=CLAUDE_CATALOG_REQUEST.encode()
            )
        except Exception:  # noqa: BLE001 - any failure here falls back to the alias list
            return []
        return _claude_reported_models(stdout) if returncode == 0 else []

    async def _slash_models(self, command: list[str]) -> tuple[str, list[str]]:
        returncode, stdout, stderr = await _run_probe([*command, "/model"])
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
        return current_model, aliases

    async def _slash_efforts(self, command: list[str]) -> list[EffortCapability]:
        returncode, stdout, stderr = await _run_probe([*command, "/effort"])
        if returncode != 0:
            if _looks_unauthenticated(stdout, stderr):
                raise _UnauthenticatedError
            raise _InvalidCatalogError
        efforts = _claude_efforts_from_slash_command(stdout)
        if not efforts:
            raise _InvalidCatalogError
        return efforts


class ProviderCapabilityService:
    """Host-scoped, thread-safe catalog cache for UI and execution validation."""

    def __init__(self) -> None:
        additional: tuple[ProviderId, ...] = (
            "cursor",
            "copilot",
            "opencode",
            "antigravity",
            "grok",
        )
        self._probes: dict[ProviderId, ProviderCapabilityProbe] = {
            "codex": CodexCapabilityProbe(),
            "claude_code": ClaudeCodeCapabilityProbe(),
            **{provider: AdditionalCliCapabilityProbe(provider) for provider in additional},
        }
        self._cache: dict[_CacheKey, _CacheEntry] = {}
        self._inflight: dict[_CacheKey, threading.Event] = {}
        self._lock = threading.RLock()

    def payload(self, *, refresh: bool = False) -> dict[str, list[dict[str, Any]]]:
        _require_sync_context("ProviderCapabilityService.payload", "payload_async")
        return asyncio.run(self.payload_async(refresh=refresh))

    def snapshot_payload(self) -> dict[str, list[dict[str, Any]]]:
        """Return settings and cached catalogs immediately, without running CLI probes.

        Expired catalogs remain useful for display while the UI refreshes each
        provider. Execution validation still uses the normal cache deadlines.
        """
        providers = []
        for provider_id, probe in self._probes.items():
            executable = resolve_provider_executable(provider_id, probe_alias=False)
            key = _CacheKey(provider_id, executable, _executable_mtime_ns(executable))
            with self._lock:
                cached = self._cache.get(key)
            # Cursor's generic `agent` alias needs a CLI identity check. Only
            # reuse it here if discovery already verified this executable.
            if (
                executable is None
                and provider_id == "cursor"
                and not provider_preference(provider_id).get("executable")
            ):
                alias = shutil.which("agent")
                alias_key = _CacheKey(provider_id, alias, _executable_mtime_ns(alias))
                with self._lock:
                    alias_cached = self._cache.get(alias_key) if alias else None
                if alias_cached:
                    executable, cached = alias, alias_cached
            capability = (
                cached.capability
                if cached
                else ProviderCapability(
                    id=provider_id,
                    display_name=probe.display_name,
                    available=executable is not None,
                    discovery_status="missing",
                )
            )
            payload = capability.to_ui_payload(executable=executable, resolve_executable=False)
            if cached is None:
                payload.update(discoveryStatus="pending", discoveredAt=None)
            providers.append(payload)
        return {"providers": providers}

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
    if (
        selected is None
        and capability.supports_custom_model
        and (not effort or capability.id == "claude_code")
    ):
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
    capability = await (service or provider_capability_service()).provider_async(provider_id)
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


def resolve_provider_executable(provider_id: ProviderId, *, probe_alias: bool = True) -> str | None:
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

    if provider_id == "cursor" and probe_alias:
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
    # The usage text also lists switches such as "ultracode [on|off]", which aren't efforts.
    return [
        EffortCapability(id=effort, display_name=_display_name(effort))
        for item in match.group(1).split("|")
        if _EFFORT_ID.fullmatch(effort := item.strip())
    ]


@dataclass(frozen=True)
class _ClaudeReportedModel:
    value: str
    model_id: str
    name: str
    description: str | None
    efforts: tuple[str, ...]
    supports_effort: bool


def _claude_model_id(value: Any) -> str | None:
    text = _nonempty_string(value)
    return text if text is not None and _CLAUDE_MODEL_ID.fullmatch(text) else None


def _claude_reported_models(stdout: str) -> list[_ClaudeReportedModel]:
    """Read the model list from the handshake reply, and nothing else in it.

    The same reply carries the signed-in account, which is deliberately ignored.
    """
    for line in stdout.splitlines():
        payload = _json_object(line)
        response = payload.get("response") if payload is not None else None
        if (
            payload is None
            or payload.get("type") != "control_response"
            or not isinstance(response, dict)
            or response.get("request_id") != CLAUDE_CATALOG_REQUEST_ID
            or response.get("subtype") != "success"
        ):
            continue
        body = response.get("response")
        raw_models = body.get("models") if isinstance(body, dict) else None
        if not isinstance(raw_models, list):
            return []
        models: list[_ClaudeReportedModel] = []
        for raw in raw_models[:MAX_CLAUDE_MODELS]:
            if not isinstance(raw, dict) or (value := _claude_model_id(raw.get("value"))) is None:
                continue
            levels = raw.get("supportedEffortLevels")
            description = _nonempty_string(raw.get("description"))
            name = _nonempty_string(raw.get("displayName")) or _claude_alias_name(value)
            # "Default (recommended)" names a slot, and its description names the model.
            if value == "default" and description and " · " in description:
                name = description.split(" · ", 1)[0]
            models.append(
                _ClaudeReportedModel(
                    value=value,
                    model_id=_claude_model_id(raw.get("resolvedModel")) or value,
                    name=name[:160],
                    description=description[:500] if description else None,
                    efforts=tuple(
                        level
                        for level in (levels if isinstance(levels, list) else [])
                        if isinstance(level, str) and _EFFORT_ID.fullmatch(level)
                    ),
                    supports_effort=raw.get("supportsEffort") is True,
                )
            )
        return models
    return []


def _claude_catalog(
    reported: list[_ClaudeReportedModel],
    current_model: str | None,
    aliases: list[str],
    slash_efforts: list[EffortCapability],
) -> tuple[list[ModelCapability], str]:
    """One entry per real model, full IDs first.

    Aliases that only restate a full ID ("haiku") become hidden entries, because
    saved work and blocked-model settings may still name them. Ones with no full
    ID of their own ("sonnet[1m]", "best") stay listed under the name the CLI takes.
    """
    known_levels = {level for item in reported for level in item.efforts}
    session_efforts = [effort for effort in slash_efforts if effort.id not in known_levels]

    def efforts_for(item: _ClaudeReportedModel) -> list[EffortCapability]:
        if item.efforts:
            named = [EffortCapability(id=e, display_name=_display_name(e)) for e in item.efforts]
            return [*named, *session_efforts]
        return list(slash_efforts) if item.supports_effort else []

    by_id: dict[str, ModelCapability] = {}
    targets: dict[str, str] = {}
    # "default" goes last so a model takes its name from its own entry.
    for item in sorted(reported, key=lambda entry: entry.value == "default"):
        if item.model_id not in by_id:
            by_id[item.model_id] = ModelCapability(
                id=item.model_id,
                display_name=item.name,
                description=item.description,
                efforts=efforts_for(item),
            )
        if item.value != item.model_id:
            targets[item.value] = item.model_id
    first = next(iter(by_id.values()))

    def variant(alias: str) -> ModelCapability:
        stem = alias.removesuffix(CLAUDE_ONE_MILLION_SUFFIX)
        base = by_id.get(targets.get(stem, stem))
        if base is None:
            return ModelCapability(
                id=alias, display_name=_claude_alias_name(alias), efforts=first.efforts
            )
        return ModelCapability(
            id=alias,
            display_name=f"{base.display_name} (1M context)",
            description=f"{base.id} with the 1M context window",
            efforts=base.efforts,
        )

    variants: dict[str, ModelCapability] = {}
    for alias in aliases:
        valid = _claude_model_id(alias)
        if valid is not None and valid not in targets and valid not in by_id:
            variants[valid] = variant(valid)
    current = _claude_model_id(current_model)
    default_model = targets.get(current, current) if current else targets.get("default", first.id)
    if default_model not in by_id and default_model not in variants:
        variants[default_model] = variant(default_model)

    default_entry = by_id.get(default_model) or variants[default_model]
    default_entry.is_default = True
    default_entry.default_effort = "high"
    hidden = [
        ModelCapability(
            id=alias,
            display_name=f"{_display_name(alias)} alias ({by_id[target].display_name})",
            description=f"Claude Code alias for {target}",
            efforts=by_id[target].efforts,
            listed=False,
            alias_of=target,
        )
        for alias, target in targets.items()
    ]
    ordered = [default_entry]
    ordered.extend(m for m in [*by_id.values(), *variants.values()] if m.id != default_model)
    return [*ordered, *hidden], default_model


def _claude_alias_name(alias: str) -> str:
    if alias.endswith(CLAUDE_ONE_MILLION_SUFFIX):
        return f"{_display_name(alias.removesuffix(CLAUDE_ONE_MILLION_SUFFIX))} (1M context)"
    return _display_name(alias)


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
            env=build_subprocess_env(env_with_executable_on_path(executable)),
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
                supports_images=(
                    raw.get("capabilities", {}).get("supports", {}).get("vision")
                    if isinstance(raw.get("capabilities"), dict)
                    and isinstance(raw["capabilities"].get("supports"), dict)
                    and isinstance(raw["capabilities"]["supports"].get("vision"), bool)
                    else None
                ),
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
