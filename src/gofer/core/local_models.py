"""Check a local model server profile: does the server answer, and which models it has.

Raticode doesn't bundle or install a model server. A local model server profile points at
one the user runs, such as Ollama, llama.cpp's llama-server or LM Studio, through its
OpenAI-compatible API. This module asks its ``GET {base}/models`` list, the same request
preflight makes before a run.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gofer.core.http import HttpRequest, HttpResponse, UrllibHttpClient
from gofer.core.provider_profiles import (
    ResolvedProviderSettings,
    is_loopback_base_url,
    local_server_warning,
    resolve_provider_settings,
    resolved_provider_env,
)

CHECK_TIMEOUT_SECONDS = 5.0
# Studio asks preflight again on every edit; a short memory keeps it from asking the
# server each time. A failed answer isn't kept, so starting the server fixes it at once.
_CACHE_SECONDS = 10.0
_cache: dict[tuple[str, str], tuple[float, list[str]]] = {}
_cache_lock = threading.Lock()


class LocalServerError(ValueError):
    """The server didn't answer, or didn't answer with a model list. Never has the key."""


@dataclass(slots=True)
class LocalServerCheck:
    profile: str | None
    base_url: str
    loopback: bool
    reachable: bool
    models: list[str] = field(default_factory=list)
    model: str | None = None
    model_available: bool | None = None
    error: str | None = None
    warning: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "baseUrl": self.base_url,
            "loopback": self.loopback,
            "reachable": self.reachable,
            "models": self.models,
            "model": self.model,
            "modelAvailable": self.model_available,
            "error": self.error,
            "warning": self.warning,
        }


def model_matches(requested: str, listed: list[str]) -> bool:
    """Whether the server lists the model. Ollama treats ``name`` as ``name:latest``."""
    if requested in listed:
        return True
    if ":" not in requested:
        return f"{requested}:latest" in listed
    if requested.endswith(":latest"):
        return requested.removesuffix(":latest") in listed
    return False


def list_server_models(settings: ResolvedProviderSettings, *, use_cache: bool = False) -> list[str]:
    """The model IDs ``GET {base}/models`` lists. Raises :class:`LocalServerError`."""
    from gofer.subscriptions.direct_api import local_server_allowlist

    base_url = (settings.api_base_url or "").rstrip("/")
    api_key = resolved_provider_env(settings).get("GOFER_DIRECT_API_KEY", "")
    cache_key = (base_url, str(hash(api_key)))
    if use_cache:
        with _cache_lock:
            cached = _cache.get(cache_key)
            if cached is not None and time.monotonic() - cached[0] < _CACHE_SECONDS:
                return list(cached[1])
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = HttpRequest(
        method="GET",
        url=f"{base_url}/models",
        headers=headers,
        timeout_seconds=CHECK_TIMEOUT_SECONDS,
        network_allowlist=local_server_allowlist(settings),
    )
    try:
        response: HttpResponse = UrllibHttpClient()._send_sync(request)
    except Exception as exc:  # noqa: BLE001 - every failure means "didn't answer"
        raise LocalServerError(
            f"The local model server at {base_url} didn't answer ({_reason(exc)}). "
            "Start the server, or check the address in the profile."
        ) from None
    if response.status in {401, 403}:
        raise LocalServerError(
            f"The local model server at {base_url} refused the API key (HTTP {response.status})."
        )
    if response.status >= 400:
        raise LocalServerError(
            f"The local model server at {base_url} answered HTTP {response.status} to "
            "GET /models. Check that the address ends in the API's /v1 path."
        )
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LocalServerError(
            f"The server at {base_url} didn't return a model list. Check that the address "
            "ends in the API's /v1 path."
        ) from None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise LocalServerError(
            f"The server at {base_url} didn't return an OpenAI-style model list."
        )
    models = sorted({str(item["id"]) for item in data if isinstance(item, dict) and item.get("id")})
    with _cache_lock:
        _cache[cache_key] = (time.monotonic(), list(models))
    return models


def check_local_server(
    profile_name: str,
    *,
    model: str | None = None,
    data_dir: Path | None = None,
) -> LocalServerCheck:
    """Ask a local model server profile's server for its models, and look for ``model``."""
    settings = resolve_provider_settings(
        agent_subscription="openai_api",
        profile_name=profile_name,
        data_dir=data_dir,
    )
    if not settings.local:
        raise ValueError(f"Provider profile '{profile_name}' isn't a local model server profile")
    requested = model or settings.model
    result = LocalServerCheck(
        profile=profile_name,
        base_url=settings.api_base_url or "",
        loopback=is_loopback_base_url(settings.api_base_url),
        reachable=False,
        model=requested,
        warning=local_server_warning(settings),
    )
    try:
        result.models = list_server_models(settings)
    except (LocalServerError, ValueError) as exc:
        result.error = str(exc)
        return result
    result.reachable = True
    if requested:
        result.model_available = model_matches(requested, result.models)
    return result


def preflight_problem(settings: ResolvedProviderSettings) -> str | None:
    """Why a run with these settings can't use its local model server, or None."""
    try:
        models = list_server_models(settings, use_cache=True)
    except (LocalServerError, ValueError) as exc:
        return str(exc)
    model = settings.model
    if model and not model_matches(model, models):
        listed = ", ".join(models[:20]) if models else "no models"
        more = f" and {len(models) - 20} more" if len(models) > 20 else ""
        return (
            f"The local model server at {settings.api_base_url} doesn't have model "
            f"{model!r}. It lists {listed}{more}. Set model: to one of these, or load the "
            "model in the server first (for Ollama: ollama pull MODEL)."
        )
    return None


def _reason(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    # Errors from the network layer never contain request headers, but keep them short.
    return text if len(text) <= 160 else text[:157] + "..."
