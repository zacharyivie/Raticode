"""Evidence policy shared by report generation and background research jobs."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from gofer.core.provider_profiles import load_provider_profiles


def local_profile(profile: Any) -> bool:
    if profile.subscription not in {"openai_api", "anthropic_api"} or not profile.api_base_url:
        return False
    parsed = urlparse(profile.api_base_url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return False
    if parsed.hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        return False


def research_profiles(data_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "id": profile.subscription,
            "model": profile.model,
            "local": local_profile(profile),
            "effort": profile.effort,
        }
        for name, profile in load_provider_profiles(data_dir).items()
    ]


def provider_is_local(provider: dict[str, Any], data_dir: Path) -> bool:
    profile = load_provider_profiles(data_dir).get(provider.get("profile", ""))
    return bool(profile and profile.subscription == provider.get("id") and local_profile(profile))
