"""Cloud embeddings use existing API profiles; credentials never enter brain metadata."""

from __future__ import annotations

import asyncio
import json
import math
import threading
from pathlib import Path
from typing import Any

from gofer.core.http import HttpRequest, UrllibHttpClient
from gofer.core.provider_profiles import resolve_provider_settings, resolved_provider_env


def cloud_embedding(
    text: str, settings: dict[str, Any], data_dir: Path, cancel: threading.Event
) -> list[float]:
    if not settings.get("allowCloud"):
        raise ValueError("Cloud embedding access is disabled for this brain")
    profile = resolve_provider_settings(
        agent_subscription="openai_api",
        profile_name=settings.get("embeddingProfile") or None,
        data_dir=data_dir,
    )
    credentials = resolved_provider_env(profile)
    key = credentials.get("GOFER_DIRECT_API_KEY")
    if not key:
        raise ValueError("Configure an API provider profile with embedding credentials")
    url = (profile.api_base_url or "https://api.openai.com/v1").rstrip("/") + "/embeddings"
    request = HttpRequest(
        method="POST",
        url=url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        body=json.dumps(
            {
                "model": settings.get("embeddingModel") or "text-embedding-3-small",
                "input": text,
                "encoding_format": "float",
            }
        ).encode(),
        timeout_seconds=30,
        network_allowlist=profile.provider_options.get("network_allowlist", []),
    )
    if cancel.is_set():
        raise InterruptedError("Embedding cancelled")
    response = asyncio.run(UrllibHttpClient().send(request))
    if response.status != 200:
        # Provider bodies can contain request text or secrets. Keep the receipt bounded and safe.
        raise ValueError(f"Embedding provider returned HTTP {response.status}")
    payload = json.loads(response.body)
    vector = payload.get("data", [{}])[0].get("embedding")
    if (
        not isinstance(vector, list)
        or not vector
        or not all(isinstance(value, int | float) and math.isfinite(value) for value in vector)
    ):
        raise ValueError("Embedding provider returned an invalid vector")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise ValueError("Embedding provider returned an empty vector")
    return [float(value / norm) for value in vector]


def signature(settings: dict[str, Any]) -> str:
    profile = settings.get("embeddingProfile", "")
    model = settings.get("embeddingModel", "text-embedding-3-small")
    return (
        "local:bge-small-en-v1.5"
        if settings.get("embeddings", "local") == "local"
        else f"cloud:{profile}:{model}"
    )
