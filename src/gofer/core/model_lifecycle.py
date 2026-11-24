"""Catch retired and retiring cloud models before a run.

Two sources answer "can this model still run?" for the ``anthropic_api`` and
``openai_api`` providers:

* A pinned table of the providers' published deprecations, with retirement dates and
  replacements. It needs no network, so the Triggers section can show it while polling.
  Update it from https://platform.claude.com/docs/en/about-claude/model-deprecations and
  https://developers.openai.com/api/docs/deprecations.
* The provider's own ``GET /v1/models`` list for the profile's API key, kept for a day.
  When the list can't be fetched (offline, a bad key, an outage) that part is skipped.

Both apply only to the providers' own API hosts: a profile pointing at a proxy or another
compatible service may name models differently.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from gofer.core.http import HttpRequest, HttpResponse, UrllibHttpClient
from gofer.core.provider_profiles import ResolvedProviderSettings, resolved_provider_env

LIST_TIMEOUT_SECONDS = 5.0
# The model list changes when a provider launches or retires a model, a few times a year.
LIST_CACHE_SECONDS = 24 * 60 * 60
# After a failed fetch, wait this long before asking again, so an offline Studio doesn't
# wait for the network on every edit.
OFFLINE_RETRY_SECONDS = 5 * 60
_ANTHROPIC_PAGE_LIMIT = 1000
_ANTHROPIC_MAX_PAGES = 10

OFFICIAL_HOSTS = {
    "anthropic_api": "api.anthropic.com",
    "openai_api": "api.openai.com",
}
PROVIDER_LABELS = {
    "anthropic_api": "Anthropic API",
    "openai_api": "OpenAI API",
}


@dataclass(frozen=True, slots=True)
class PinnedModel:
    """One published deprecation. ``retires`` is None while the date isn't announced."""

    retires: date | None
    replacement: str


def _pin(retires: str | None, replacement: str, *models: str) -> dict[str, PinnedModel]:
    when = date.fromisoformat(retires) if retires else None
    return {model: PinnedModel(when, replacement) for model in models}


# Checked 2026-11-17 against the providers' deprecation pages. Aliases such as
# ``-latest`` and ``-4-0`` retire with the snapshot they point at.
PINNED_MODELS: dict[str, dict[str, PinnedModel]] = {
    "anthropic_api": {
        **_pin(
            "2024-11-06",
            "claude-haiku-4-5-20251001",
            "claude-1.0",
            "claude-1.1",
            "claude-1.2",
            "claude-1.3",
            "claude-instant-1.0",
            "claude-instant-1.1",
            "claude-instant-1.2",
        ),
        **_pin("2025-07-21", "claude-opus-4-8", "claude-2.0", "claude-2.1"),
        **_pin("2025-07-21", "claude-sonnet-5-5", "claude-3-sonnet-20240229"),
        **_pin(
            "2025-10-28",
            "claude-sonnet-5-5",
            "claude-3-5-sonnet-20240620",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-sonnet-latest",
        ),
        **_pin("2026-01-05", "claude-opus-4-8", "claude-3-opus-20240229", "claude-3-opus-latest"),
        **_pin(
            "2026-02-19",
            "claude-sonnet-5-5",
            "claude-3-7-sonnet-20250219",
            "claude-3-7-sonnet-latest",
        ),
        **_pin(
            "2026-02-19",
            "claude-haiku-4-5-20251001",
            "claude-3-5-haiku-20241022",
            "claude-3-5-haiku-latest",
        ),
        **_pin("2026-04-20", "claude-haiku-4-5-20251001", "claude-3-haiku-20240307"),
        **_pin("2026-06-15", "claude-sonnet-5-5", "claude-sonnet-4-20250514", "claude-sonnet-4-0"),
        **_pin("2026-06-15", "claude-opus-4-8", "claude-opus-4-20250514", "claude-opus-4-0"),
        **_pin("2026-08-05", "claude-opus-4-8", "claude-opus-4-1-20250805", "claude-opus-4-1"),
        **_pin(
            "2026-11-30", "claude-sonnet-5-5", "claude-sonnet-4-5-20250929", "claude-sonnet-4-5"
        ),
    },
    "openai_api": {
        **_pin(
            "2024-09-13",
            "gpt-3.5-turbo",
            "gpt-3.5-turbo-0613",
            "gpt-3.5-turbo-16k-0613",
            "gpt-3.5-turbo-0301",
        ),
        **_pin("2024-12-06", "gpt-4o", "gpt-4-vision-preview", "gpt-4-1106-vision-preview"),
        **_pin("2025-06-06", "gpt-4o", "gpt-4-32k", "gpt-4-32k-0613", "gpt-4-32k-0314"),
        **_pin("2025-07-14", "gpt-4.1", "gpt-4.5-preview"),
        **_pin("2025-07-28", "o3", "o1-preview"),
        **_pin("2025-10-27", "o4-mini", "o1-mini"),
        **_pin("2026-02-12", "gpt-5-codex-mini", "codex-mini-latest"),
        **_pin("2026-02-17", "gpt-5.1-chat-latest", "chatgpt-4o-latest"),
        **_pin(
            "2026-03-26",
            "gpt-5",
            "gpt-4-0314",
            "gpt-4-0125-preview",
            "gpt-4-turbo-preview",
        ),
        **_pin(
            "2026-07-23",
            "gpt-5.6-sol",
            "gpt-5-chat-latest",
            "gpt-5-codex",
            "gpt-5.1-chat-latest",
            "gpt-5.1-codex",
            "gpt-5.1-codex-max",
            "gpt-5.2-codex",
            "o3-deep-research",
            "o3-deep-research-2025-06-26",
            "o4-mini-deep-research",
            "o4-mini-deep-research-2025-06-26",
        ),
        **_pin(
            "2026-07-23",
            "gpt-5.6-terra",
            "gpt-5.1-codex-mini",
            "computer-use-preview",
            "computer-use-preview-2025-03-11",
            "gpt-4o-search-preview-2025-03-11",
            "gpt-4o-mini-search-preview-2025-03-11",
        ),
        **_pin("2026-08-10", "gpt-5.6-sol", "gpt-5.2-chat-latest", "gpt-5.3-chat-latest"),
        **_pin(
            "2026-09-28",
            "gpt-5.6-terra",
            "gpt-3.5-turbo-instruct",
            "gpt-3.5-turbo-1106",
            "babbage-002",
            "davinci-002",
        ),
        **_pin(
            "2026-10-23",
            "gpt-5.6-sol",
            "gpt-4",
            "gpt-4-0613",
            "gpt-4-1106-preview",
            "gpt-4-turbo",
            "gpt-4-turbo-2024-04-09",
            "gpt-4o-2024-05-13",
            "o1",
            "o1-2024-12-17",
            "o1-pro",
            "o1-pro-2025-03-19",
            "o3-mini",
            "o3-mini-2025-01-31",
        ),
        **_pin(
            "2026-10-23",
            "gpt-5.6-terra",
            "gpt-3.5-turbo",
            "gpt-3.5-turbo-0125",
            "o4-mini",
            "o4-mini-2025-04-16",
        ),
        **_pin("2026-10-23", "gpt-5.6-luna", "gpt-4.1-nano", "gpt-4.1-nano-2025-04-14"),
        **_pin(
            "2026-12-11",
            "gpt-6.1-sol",
            "gpt-5",
            "gpt-5-2025-08-07",
            "gpt-5-pro",
            "gpt-5-pro-2025-10-06",
            "o3",
            "o3-2025-04-16",
            "o3-pro",
            "o3-pro-2025-06-10",
        ),
        **_pin("2026-12-11", "gpt-6.1-terra", "gpt-5-mini", "gpt-5-mini-2025-08-07"),
        **_pin("2026-12-11", "gpt-6-luna", "gpt-5-nano", "gpt-5-nano-2025-08-07"),
        **_pin(
            "2027-01-14",
            "gpt-6-luna",
            "gpt-4o-2024-08-06",
            "gpt-4o-2024-11-20",
            "gpt-4o-mini-2024-07-18",
            "gpt-4.1-2025-04-14",
            "gpt-4.1-mini-2025-04-14",
        ),
        **_pin("2027-04-01", "gpt-6-sol", "gpt-5.3-codex", "gpt-5.1", "gpt-5.1-2025-11-13"),
        **_pin("2027-04-01", "gpt-6-luna", "gpt-5.4-nano", "gpt-5.4-nano-2026-03-17"),
    },
}


def today_utc() -> date:
    return datetime.now(UTC).date()


def format_day(day: date) -> str:
    return f"{day:%B} {day.day}, {day.year}"


def pinned_model(subscription: str, model: str) -> PinnedModel | None:
    return PINNED_MODELS.get(subscription, {}).get(model)


def replacement_for(subscription: str, model: str) -> str:
    """The pinned replacement, followed while it is itself retired or retiring."""
    current = model
    seen = {model}
    for _ in range(8):
        pinned = pinned_model(subscription, current)
        if pinned is None or pinned.replacement in seen:
            break
        current = pinned.replacement
        seen.add(current)
    return current


def retired_models(subscription: str, *, today: date | None = None) -> set[str]:
    """Model IDs the provider no longer serves on ``today``."""
    day = today or today_utc()
    return {
        model
        for model, pinned in PINNED_MODELS.get(subscription, {}).items()
        if pinned.retires is not None and pinned.retires <= day
    }


Severity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class ModelProblem:
    code: str
    severity: Severity
    state: Literal["retired", "unlisted", "deprecated"]
    model: str
    message: str
    suggestion: str
    replacement: str | None = None
    retires: date | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "state": self.state,
            "model": self.model,
            "message": self.message,
            "replacement": self.replacement,
            "retires": self.retires.isoformat() if self.retires else None,
        }


def is_official_host(settings: ResolvedProviderSettings) -> bool:
    expected = OFFICIAL_HOSTS.get(settings.subscription)
    if expected is None or settings.local or not settings.api_base_url:
        return False
    return (urlsplit(settings.api_base_url).hostname or "").lower() == expected


def _default_model(subscription: str) -> str | None:
    from gofer.subscriptions.direct_api import AnthropicApiSubscription, OpenAiApiSubscription

    return {
        "anthropic_api": AnthropicApiSubscription.default_model,
        "openai_api": OpenAiApiSubscription.default_model,
    }.get(subscription)


def _use_instead(model: str, replacement: str) -> str:
    return f"Set model: {replacement} where this step (or its fallback entry) names {model}."


def pinned_problem(
    settings: ResolvedProviderSettings, *, today: date | None = None
) -> ModelProblem | None:
    """What the pinned deprecations say about the settings' model. No network."""
    model = settings.model
    if not model or not is_official_host(settings):
        return None
    pinned = pinned_model(settings.subscription, model)
    if pinned is None:
        return None
    day = today or today_utc()
    replacement = replacement_for(settings.subscription, model)
    label = PROVIDER_LABELS[settings.subscription]
    if pinned.retires is not None and pinned.retires <= day:
        return ModelProblem(
            code="RATTISH_PREFLIGHT_MODEL_RETIRED",
            severity="error",
            state="retired",
            model=model,
            message=(
                f"Model {model!r} was retired on {format_day(pinned.retires)}, so the "
                f"{label} no longer runs it. Use {replacement!r} instead."
            ),
            suggestion=_use_instead(model, replacement),
            replacement=replacement,
            retires=pinned.retires,
        )
    when = (
        f"retires on {format_day(pinned.retires)}"
        if pinned.retires is not None
        else "has no retirement date yet"
    )
    return ModelProblem(
        code="RATTISH_PREFLIGHT_MODEL_DEPRECATED",
        severity="warning",
        state="deprecated",
        model=model,
        message=(
            f"Model {model!r} is deprecated and {when}; after that, runs that use it fail. "
            f"Move to {replacement!r}."
        ),
        suggestion=_use_instead(model, replacement),
        replacement=replacement,
        retires=pinned.retires,
    )


def model_listed(subscription: str, model: str, listed: list[str] | set[str]) -> bool:
    """Whether the provider's list has the model, counting Anthropic's dated aliases.

    The Anthropic list names snapshots (``claude-sonnet-4-5-20250929``), while requests
    may use the alias without the date, the ``-latest`` form or a ``-0`` form.
    """
    names = set(listed)
    if model in names:
        return True
    if subscription != "anthropic_api":
        return False
    bases = {model}
    if model.endswith("-latest"):
        bases.add(model.removesuffix("-latest"))
    if model.endswith("-0"):
        bases.add(model.removesuffix("-0"))
    patterns = [re.compile(re.escape(base) + r"-\d{8}") for base in bases]
    return any(pattern.fullmatch(name) for pattern in patterns for name in names)


def check_model(
    settings: ResolvedProviderSettings,
    data_dir: Path,
    *,
    today: date | None = None,
) -> ModelProblem | None:
    """Preflight's model check for an ``anthropic_api`` or ``openai_api`` profile.

    A retired model, or one the provider's list doesn't have, is an error that names a
    replacement. A deprecated one is a warning with its retirement date. Skipped (None)
    for other hosts, and the list part is skipped when the list can't be fetched.
    """
    model = settings.model
    if not model or not is_official_host(settings):
        return None
    pinned = pinned_problem(settings, today=today)
    if pinned is not None and pinned.severity == "error":
        return pinned
    listed = list_models(settings, data_dir)
    if listed is not None and not model_listed(settings.subscription, model, listed):
        return _unlisted_problem(settings, model, listed, today=today)
    return pinned


def _unlisted_problem(
    settings: ResolvedProviderSettings,
    model: str,
    listed: list[str],
    *,
    today: date | None,
) -> ModelProblem:
    subscription = settings.subscription
    label = PROVIDER_LABELS[subscription]
    replacement: str | None = None
    if pinned_model(subscription, model) is not None:
        replacement = replacement_for(subscription, model)
    if replacement is None:
        retired = retired_models(subscription, today=today)
        close = difflib.get_close_matches(
            model, [item for item in listed if item not in retired], n=1, cutoff=0.6
        )
        replacement = close[0] if close else _default_model(subscription)
    tail = f" Use {replacement!r} instead." if replacement else ""
    return ModelProblem(
        code="RATTISH_PREFLIGHT_MODEL_UNAVAILABLE",
        severity="error",
        state="unlisted",
        model=model,
        message=(
            f"The {label} doesn't list model {model!r} for this API key, so a run with it "
            f"would fail.{tail}"
        ),
        suggestion=(
            _use_instead(model, replacement)
            if replacement
            else "Check the model ID against the provider's model list."
        ),
        replacement=replacement,
    )


# Model list ---------------------------------------------------------------------------

_memory: dict[str, tuple[float, list[str] | None]] = {}
_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _send(request: HttpRequest) -> HttpResponse:
    """One HTTP request. Tests replace this; ``tests/conftest.py`` makes it offline."""
    return UrllibHttpClient()._send_sync(request)


def _cache_file(data_dir: Path) -> Path:
    return data_dir / "provider-model-lists.json"


def _cache_key(settings: ResolvedProviderSettings, api_key: str) -> str:
    # The list depends on the account, so the key is part of it; only its hash is kept.
    raw = "\0".join(
        [settings.subscription, settings.api_base_url or "", settings.organization or "", api_key]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _read_disk(data_dir: Path) -> dict[str, Any]:
    try:
        payload = json.loads(_cache_file(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_disk(data_dir: Path, key: str, models: list[str]) -> None:
    path = _cache_file(data_dir)
    now = _now()
    try:
        entries = {
            name: entry
            for name, entry in _read_disk(data_dir).items()
            if isinstance(entry, dict)
            and now - float(entry.get("fetchedAt") or 0) < LIST_CACHE_SECONDS
        }
        entries[key] = {"fetchedAt": now, "models": models}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
        temporary.write_text(json.dumps(entries), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        pass


def clear_cache(data_dir: Path | None = None) -> None:
    with _lock:
        _memory.clear()
    if data_dir is not None:
        try:
            _cache_file(data_dir).unlink()
        except OSError:
            pass


def list_models(settings: ResolvedProviderSettings, data_dir: Path) -> list[str] | None:
    """The provider's model IDs for the profile's key, or None when they can't be fetched.

    Kept for a day in memory and in the data folder. A failed fetch is retried after
    five minutes.
    """
    if not is_official_host(settings):
        return None
    try:
        api_key = resolved_provider_env(settings).get("GOFER_DIRECT_API_KEY", "")
    except ValueError:
        return None
    if not api_key:
        return None
    key = _cache_key(settings, api_key)
    now = _now()
    with _lock:
        remembered = _memory.get(key)
    if remembered is not None:
        fetched_at, models = remembered
        limit = LIST_CACHE_SECONDS if models is not None else OFFLINE_RETRY_SECONDS
        if now - fetched_at < limit:
            return list(models) if models is not None else None
    entry = _read_disk(data_dir).get(key)
    if isinstance(entry, dict) and isinstance(entry.get("models"), list):
        fetched_at = float(entry.get("fetchedAt") or 0)
        if 0 <= now - fetched_at < LIST_CACHE_SECONDS:
            models = [str(item) for item in entry["models"]]
            with _lock:
                _memory[key] = (fetched_at, models)
            return list(models)
    fetched = _fetch(settings, api_key)
    with _lock:
        _memory[key] = (now, fetched)
    if fetched is not None:
        _write_disk(data_dir, key, fetched)
    return list(fetched) if fetched is not None else None


def _fetch(settings: ResolvedProviderSettings, api_key: str) -> list[str] | None:
    base_url = (settings.api_base_url or "").rstrip("/")
    headers = {"Accept": "application/json"}
    if settings.subscription == "anthropic_api":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = str(
            settings.provider_options.get("anthropic_version") or "2023-06-01"
        )
    else:
        headers["Authorization"] = f"Bearer {api_key}"
        if settings.organization:
            headers["OpenAI-Organization"] = settings.organization
    models: set[str] = set()
    after: str | None = None
    for _ in range(_ANTHROPIC_MAX_PAGES):
        url = f"{base_url}/models"
        if settings.subscription == "anthropic_api":
            url += f"?limit={_ANTHROPIC_PAGE_LIMIT}" + (f"&after_id={after}" if after else "")
        try:
            response = _send(
                HttpRequest(
                    method="GET", url=url, headers=headers, timeout_seconds=LIST_TIMEOUT_SECONDS
                )
            )
        except Exception:  # noqa: BLE001 - offline or unreachable: skip the check
            return None
        if response.status >= 400:
            # A bad key, an outage or a rate limit: the run reports those itself.
            return None
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return None
        models.update(str(item["id"]) for item in data if isinstance(item, dict) and item.get("id"))
        if not (payload.get("has_more") and payload.get("last_id")):
            break
        after = str(payload["last_id"])
    return sorted(models)
