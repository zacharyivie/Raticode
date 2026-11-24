"""The provider app version a run used, as people read it, and whether it changed.

Rem run records keep a ``turns`` list whose entries carry ``provider`` and
``provider_version``. Rattish node records keep ``provider_version`` inside their
``provider`` mapping. Runs saved before versions were recorded have neither, and
every line here is left out for them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

ROLE_NAMES = {"execute": "worker", "verify": "reviewer"}


def provider_display_name(provider_id: str) -> str:
    """A provider as people know it: Claude Code for ``claude_code`` or ``claude-code``."""
    from gofer.core.provider_usage import PROVIDER_LABELS

    key = str(provider_id).strip().lower().replace("-", "_")
    return PROVIDER_LABELS.get(key, str(provider_id))


def provider_key(provider_id: str) -> str:
    """One spelling for a provider, so ``claude-code`` and ``claude_code`` match."""
    return str(provider_id).strip().lower().replace("-", "_")


def app_line(provider_id: str, version: str) -> str:
    """``Claude Code 2.3.1``."""
    return f"{provider_display_name(provider_id)} {version}"


def change_line(provider_id: str, before: str, after: str) -> str:
    """The sentence a failure adds when the provider changed since the last passing run."""
    return (
        f"{provider_display_name(provider_id)} changed from {before} to {after} "
        "since the last passing run."
    )


def versions_used(entries: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Each provider's last recorded version, keyed by provider_key, from turn entries
    or node provider mappings (both carry ``provider`` and ``provider_version``)."""
    found: dict[str, str] = {}
    for entry in entries:
        provider, version = entry.get("provider"), entry.get("provider_version")
        if isinstance(provider, str) and provider and isinstance(version, str) and version:
            found[provider_key(provider)] = version
    return found


def version_changes(failing: Mapping[str, str], passing: Mapping[str, str]) -> list[dict[str, str]]:
    """Providers whose version differs between a failing run and the last passing one."""
    return [
        {
            "provider": provider,
            "from": passing[provider],
            "to": version,
            "text": change_line(provider, passing[provider], version),
        }
        for provider, version in failing.items()
        if provider in passing and passing[provider] != version
    ]


def rem_turn_lines(turns: Iterable[Mapping[str, Any]]) -> list[str]:
    """``Provider: Claude Code 2.3.1 (worker, reviewer)``, one line per provider version.

    Turns without a recorded version are left out.
    """
    roles: dict[tuple[str, str], list[str]] = {}
    for turn in turns:
        provider, version = turn.get("provider"), turn.get("provider_version")
        if not isinstance(provider, str) or not isinstance(version, str) or not version:
            continue
        role = ROLE_NAMES.get(str(turn.get("role")), str(turn.get("role") or "turn"))
        named = roles.setdefault((provider, version), [])
        if role not in named:
            named.append(role)
    return [
        f"Provider: {app_line(provider, version)} ({', '.join(named)})"
        for (provider, version), named in roles.items()
    ]
