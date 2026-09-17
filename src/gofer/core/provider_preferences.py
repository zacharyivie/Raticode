"""Host provider preferences shared by discovery and Rem."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from gofer.utils.paths import get_data_dir

_lock = threading.RLock()


def provider_preferences() -> dict[str, Any]:
    try:
        value = json.loads((get_data_dir() / "providers.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def provider_preference(provider: str) -> dict[str, Any]:
    value = provider_preferences().get(provider, {})
    return value if isinstance(value, dict) else {}


def save_provider_preference(provider: str, changes: dict[str, Any]) -> None:
    from gofer.core.provider_capabilities import PROVIDER_BINARIES

    if not isinstance(provider, str) or provider not in PROVIDER_BINARIES:
        raise ValueError("Unknown provider")
    if set(changes) - {"enabled", "executable"}:
        raise ValueError("Unknown provider setting")
    if "enabled" in changes and not isinstance(changes["enabled"], bool):
        raise ValueError("Enabled must be a boolean")
    if "executable" in changes:
        value = changes["executable"]
        if not isinstance(value, str):
            raise ValueError("Executable must be a path")
        value = value.strip()
        if value:
            path = Path(value).expanduser()
            if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
                raise ValueError("Choose an existing executable file using an absolute path")
            value = str(path)
        changes = {**changes, "executable": value}
    with _lock:
        preferences = provider_preferences()
        preferences[provider] = {**provider_preference(provider), **changes}
        destination = get_data_dir() / "providers.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=destination.parent, delete=False
            ) as output:
                temporary = output.name
                json.dump(preferences, output, indent=2)
                output.write("\n")
            os.replace(temporary, destination)
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
