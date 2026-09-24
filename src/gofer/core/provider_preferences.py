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
    if set(changes) - {"enabled", "executable", "defaultModel", "defaultEffort", "deniedModels"}:
        raise ValueError("Unknown provider setting")
    changes = dict(changes)
    if "deniedModels" in changes:
        models = changes["deniedModels"]
        if not isinstance(models, list) or any(
            not isinstance(model, str) or not model.strip() or len(model) > 256 for model in models
        ):
            raise ValueError("Denied models must be a list of model IDs")
        changes["deniedModels"] = list(dict.fromkeys(model.strip() for model in models))
    for field in ("defaultModel", "defaultEffort"):
        if field in changes:
            value = changes[field]
            if not isinstance(value, str) or len(value) > 256:
                raise ValueError(f"{field} must be a string of at most 256 characters")
            changes[field] = value.strip()
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
    _save_preference(provider, changes)


def commit_message_preference() -> dict[str, Any]:
    return provider_preference("commitMessage")


def save_commit_message_preference(changes: dict[str, Any]) -> None:
    from gofer.core.provider_capabilities import PROVIDER_BINARIES

    if set(changes) != {"provider", "model"}:
        raise ValueError("Choose a commit provider and model")
    provider, model = changes["provider"], changes["model"]
    if not isinstance(provider, str) or (provider and provider not in PROVIDER_BINARIES):
        raise ValueError("Unknown commit provider")
    if not isinstance(model, str) or len(model) > 256 or (bool(provider) != bool(model.strip())):
        raise ValueError("Choose a commit provider and model")
    model = model.strip()
    if provider and model in provider_preference(provider).get("deniedModels", []):
        raise ValueError("Allow this model before selecting it for commit messages")
    _save_preference("commitMessage", {"provider": provider, "model": model})


def _save_preference(key: str, changes: dict[str, Any]) -> None:
    with _lock:
        preferences = provider_preferences()
        preferences[key] = {**provider_preference(key), **changes}
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
