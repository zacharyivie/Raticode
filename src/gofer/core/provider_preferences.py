"""Host provider preferences shared by discovery and Rem."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from gofer.core.commit_message_format import (
    DEFAULT_COMMIT_MESSAGE_TEMPLATE,
    MAX_COMMIT_TEMPLATE_LENGTH,
)
from gofer.utils.atomic_output import replace_file
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


def app_default_selection(
    provider: str, model: str | None, effort: str | None
) -> tuple[str | None, str | None]:
    """The model and effort a call runs with when its own choice leaves them to the app.

    An empty or ``cli-default`` model takes Settings > Providers > <app> > Default model,
    and an empty effort its Default effort, under the rules that screen shows them with
    (ProviderCapability.to_ui_payload): a blocked model, or one the app's catalog doesn't
    list, is skipped, and the saved effort is kept only when the model that runs offers it
    and the saved model is empty or is that model. Never runs the app's CLI: without a
    catalog the saved model still applies and the saved effort doesn't. A None part stays
    with the app's own default.
    """
    from gofer.core.provider_capabilities import (
        CLI_PROVIDERS,
        model_block_applies,
        provider_capability_service,
    )

    chosen = "" if (model or "").strip() == "cli-default" else (model or "").strip()
    wanted = "" if (effort or "").strip() == "cli-default" else (effort or "").strip()
    preference = provider_preference(provider)
    saved_model = str(preference.get("defaultModel") or "").strip()
    saved_effort = str(preference.get("defaultEffort") or "").strip()
    if (chosen and wanted) or provider not in CLI_PROVIDERS or not (saved_model or saved_effort):
        return chosen or None, wanted or None
    capability = provider_capability_service().cached(provider)
    catalog = capability.models if capability and capability.discovery_status == "ready" else None
    if not chosen and saved_model:
        denied = list(preference.get("deniedModels", []))
        aliases = capability.alias_targets() if capability else {}
        listed = catalog is None or any(item.id == saved_model for item in catalog)
        if listed and not model_block_applies(provider, saved_model, denied, aliases):
            chosen = saved_model
    runs = chosen or (capability.default_model if capability else None)
    if not wanted and saved_effort and (not saved_model or saved_model == runs):
        offered = next((item for item in catalog or [] if item.id == runs), None)
        if offered and any(item.id == saved_effort for item in offered.efforts):
            wanted = saved_effort
    return chosen or None, wanted or None


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
    # stripCoAuthors is off until the user checks Settings > Rem > Remove
    # Co-authored-by lines; see gofer.core.commit_attribution.
    return {
        "autoCommit": False,
        "stripCoAuthors": False,
        "template": DEFAULT_COMMIT_MESSAGE_TEMPLATE,
        **provider_preference("commitMessage"),
    }


def strip_co_authors_enabled() -> bool:
    return commit_message_preference().get("stripCoAuthors") is True


def save_commit_message_preference(changes: dict[str, Any]) -> None:
    from gofer.core.provider_capabilities import PROVIDER_BINARIES, model_is_blocked

    if not changes or set(changes) - {
        "provider",
        "model",
        "template",
        "autoCommit",
        "stripCoAuthors",
    }:
        raise ValueError("Unknown commit message setting")
    changes = dict(changes)
    if "autoCommit" in changes and not isinstance(changes["autoCommit"], bool):
        raise ValueError("Auto commit must be a boolean")
    if "stripCoAuthors" in changes and not isinstance(changes["stripCoAuthors"], bool):
        raise ValueError("Remove Co-authored-by lines must be a boolean")
    if "template" in changes:
        template = changes["template"]
        if not isinstance(template, str) or len(template) > MAX_COMMIT_TEMPLATE_LENGTH:
            raise ValueError(
                "Commit template must be a string of at most "
                f"{MAX_COMMIT_TEMPLATE_LENGTH} characters"
            )
        changes["template"] = template if template.strip() else DEFAULT_COMMIT_MESSAGE_TEMPLATE
    selection = set(changes) & {"provider", "model"}
    if not selection:
        _save_preference("commitMessage", changes)
        return
    if selection != {"provider", "model"}:
        raise ValueError("Choose a commit provider and model")
    provider, model = changes["provider"], changes["model"]
    if not isinstance(provider, str) or (provider and provider not in PROVIDER_BINARIES):
        raise ValueError("Unknown commit provider")
    if not isinstance(model, str) or len(model) > 256 or (bool(provider) != bool(model.strip())):
        raise ValueError("Choose a commit provider and model")
    model = model.strip()
    if provider and model_is_blocked(provider, model):
        raise ValueError("Allow this model before selecting it for commit messages")
    _save_preference("commitMessage", {**changes, "provider": provider, "model": model})


def voice_tidy_preference() -> dict[str, Any]:
    """Voice tidying is off until the user turns it on.

    An empty provider follows the default provider in Settings, and an empty
    model uses that provider's fast model, so the first choice tracks the
    default provider until the user picks one.
    """
    return {
        "enabled": False,
        "provider": "",
        "model": "",
        "effort": "",
        "context": True,
        **provider_preference("voiceTidy"),
    }


def save_voice_tidy_preference(changes: dict[str, Any]) -> None:
    from gofer.core.provider_capabilities import PROVIDER_BINARIES, model_is_blocked

    if not changes or set(changes) - {"enabled", "provider", "model", "effort", "context"}:
        raise ValueError("Unknown voice tidy setting")
    changes = dict(changes)
    for field in ("enabled", "context"):
        if field in changes and not isinstance(changes[field], bool):
            raise ValueError(f"{field} must be a boolean")
    for field in ("provider", "model", "effort"):
        if field in changes:
            value = changes[field]
            if not isinstance(value, str) or len(value) > 256:
                raise ValueError(f"{field} must be a string of at most 256 characters")
            changes[field] = value.strip()
    if "provider" in changes:
        if changes["provider"] and changes["provider"] not in PROVIDER_BINARIES:
            raise ValueError("Unknown voice tidy provider")
        # A new provider starts again from its own fast model.
        changes.setdefault("model", "")
        changes.setdefault("effort", "")
    provider = changes.get("provider", voice_tidy_preference()["provider"])
    model = changes.get("model", "")
    if model and not provider:
        raise ValueError("Choose a provider before choosing a voice tidy model")
    if model and model_is_blocked(provider, model):
        raise ValueError("Allow this model before selecting it for voice tidying")
    _save_preference("voiceTidy", changes)


def speech_preference() -> dict[str, Any]:
    """Which speech-to-text dictation uses: the OS's own recognizer, or Raticode's offline model."""
    engine = provider_preference("speech").get("engine")
    chosen = engine in {"system", "offline"}
    # "chosen" is False until the user picks one; Windows then prefers a downloaded
    # offline model over its own recognizer (chat_media.prefers_offline_model).
    return {"engine": engine if chosen else "system", "chosen": chosen}


def save_speech_preference(changes: dict[str, Any]) -> None:
    if not changes or set(changes) - {"engine"}:
        raise ValueError("Unknown speech setting")
    if changes.get("engine") not in {"system", "offline"}:
        raise ValueError("Choose system or offline speech recognition")
    _save_preference("speech", {"engine": changes["engine"]})


def _save_preference(key: str, changes: dict[str, Any]) -> None:
    with _lock:
        preferences = provider_preferences()
        preferences[key] = {**provider_preference(key), **changes}
        text = json.dumps(preferences, indent=2) + "\n"
        replace_file(get_data_dir() / "providers.json", text.encode("utf-8"))
