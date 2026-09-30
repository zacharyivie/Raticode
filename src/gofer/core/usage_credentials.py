"""Usage-only credentials, isolated from inference and stored in the OS secret store.

SQLite holds opaque revisions only. Neither settings responses nor usage history
contain reporting keys. A revision change invalidates even an in-flight snapshot.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from gofer.core.provider_profiles import load_provider_profiles
from gofer.core.usage_ledger import usage_db
from gofer.utils.paths import get_data_dir

_lock = threading.RLock()
_STORE_ERROR = (
    "Unlock the OS credential store to save or read reporting credentials. "
    "This installation also needs the Raticode device security dependencies."
)
_FIELDS: dict[str, list[dict[str, Any]]] = {
    "claude_code": [
        {
            "name": "oauth_token",
            "label": "Claude OAuth access token",
            "secret": True,
            "help": "Optional override for Claude Code sign-in. Needs user:profile scope. "
            "A regular Anthropic API key cannot read Claude subscription limits. "
            "Access tokens expire; sign in again or replace the token when access is denied.",
        },
    ],
    "cursor": [
        {
            "name": "admin_api_key",
            "label": "Cursor Admin API key",
            "secret": True,
            "help": "Create an Admin API key in your Cursor team dashboard. "
            "Requires team admin access.",
        },
        {
            "name": "email",
            "label": "Team member email",
            "secret": False,
            "help": "Only this exact team member's on-demand spending is retrieved.",
        },
    ],
    "openai_api": [
        {
            "name": "admin_api_key",
            "label": "OpenAI Admin API key",
            "secret": True,
            "help": "Create a separate organization Admin API key with usage reporting access. "
            "An ordinary project inference key is not sufficient.",
        },
        {
            "name": "project_id",
            "label": "Project ID (optional)",
            "secret": False,
            "help": "Leave blank for organization-wide reporting. "
            "This key does not change your inference credentials.",
        },
    ],
    "anthropic_api": [
        {
            "name": "admin_api_key",
            "label": "Anthropic Admin API key",
            "secret": True,
            "help": "Create an Admin API key in an eligible Anthropic Console organization. "
            "This reports API usage, separate from Claude Pro or Max subscription limits.",
        },
        {
            "name": "workspace_id",
            "label": "Workspace ID (optional)",
            "secret": False,
            "help": "Leave blank for organization-wide reporting.",
        },
    ],
}
_DESCRIPTIONS = {
    "claude_code": "Read Claude subscription windows using Claude Code's OAuth sign-in. "
    "Raticode tries local Claude Code credentials automatically; an override is optional. "
    "This endpoint is used by Claude clients but is not a documented public reporting API.",
    "cursor": "Connect team admin reporting for on-demand spend. "
    "Cursor does not offer a verified personal subscription quota feed through this API.",
    "openai_api": "Read month-to-date API token usage. Reporting totals can lag behind requests "
    "and do not represent a remaining token allowance.",
    "anthropic_api": "Read month-to-date API token usage. These totals do not include "
    "Claude subscription allowances and do not represent tokens left.",
}
_HELP_URLS = {
    "claude_code": "https://code.claude.com/docs/en/authentication",
    "cursor": "https://cursor.com/docs/account/teams/admin-api",
    "openai_api": "https://platform.openai.com/settings/organization/admin-keys",
    "anthropic_api": "https://platform.claude.com/docs/en/api/usage-cost-api",
}


class UsageCredentialError(ValueError):
    """A safe error that can be returned to the settings UI."""


class _Store(Protocol):
    def get(self, account: str) -> str | None: ...
    def put(self, account: str, value: str) -> None: ...


def _secret_store() -> _Store:
    # Keep security dependencies optional for CLI-only installations.
    from gofer.devices.storage import OSSecretStore

    return OSSecretStore(service="Raticode usage reporting")


def _scope(provider: str, profile: str | None) -> str:
    return hashlib.sha256(json.dumps([provider, profile]).encode()).hexdigest()


def _account(provider: str, profile: str | None, data_dir: Path | None) -> str:
    root = str((data_dir or get_data_dir()).resolve())
    return hashlib.sha256(root.encode()).hexdigest() + ":" + _scope(provider, profile)


def _validate_account(provider: Any, profile: Any, data_dir: Path | None) -> None:
    if not isinstance(provider, str) or provider not in _FIELDS:
        raise UsageCredentialError("This provider has no configurable reporting credentials.")
    if profile is not None:
        if not isinstance(profile, str) or not profile:
            raise UsageCredentialError("Invalid provider profile.")
        configured = load_provider_profiles(data_dir).get(profile)
        if configured is None or configured.subscription != provider:
            raise UsageCredentialError("The provider profile does not match this account.")


def usage_credential_revision(
    provider: str, profile: str | None = None, *, data_dir: Path | None = None
) -> str:
    if provider not in _FIELDS:
        return ""
    with usage_db(data_dir) as db:
        row = db.execute(
            "SELECT value FROM usage_meta WHERE key=?", ("reporting:" + _scope(provider, profile),)
        ).fetchone()
    return str(row[0]) if row else ""


def resolve_usage_credentials(
    provider: str, profile: str | None = None, *, data_dir: Path | None = None
) -> dict[str, str]:
    if provider not in _FIELDS:
        return {}
    _validate_account(provider, profile, data_dir)
    with _lock:
        revision = usage_credential_revision(provider, profile, data_dir=data_dir)
        if not revision:
            return {}
        try:
            raw = _secret_store().get(_account(provider, profile, data_dir))
            record = json.loads(raw or "{}")
            if record.get("revision") != revision:
                raise ValueError("Credential revision mismatch")
            values = record["values"]
            if not isinstance(values, dict) or any(
                name not in {field["name"] for field in _FIELDS[provider]}
                or not isinstance(value, str)
                for name, value in values.items()
            ):
                raise ValueError("Invalid stored credential")
            return values
        except Exception:
            raise UsageCredentialError(_STORE_ERROR) from None


def usage_credentials_payload(
    provider: str, profile: str | None = None, *, data_dir: Path | None = None
) -> dict[str, Any]:
    _validate_account(provider, profile, data_dir)
    error = None
    values: dict[str, str] = {}
    try:
        # A read also checks that an installed store is actually unlocked.
        _secret_store().get(_account(provider, profile, data_dir))
        values = resolve_usage_credentials(provider, profile, data_dir=data_dir)
    except Exception:
        error = _STORE_ERROR
    return {
        "provider": provider,
        "profile": profile,
        "description": _DESCRIPTIONS[provider],
        "help_url": _HELP_URLS[provider],
        "storage_available": error is None,
        "storage_error": error,
        "fields": [
            {
                **field,
                "configured": bool(values.get(field["name"])),
                **({"value": values.get(field["name"], "")} if not field["secret"] else {}),
            }
            for field in _FIELDS[provider]
        ],
    }


def save_usage_credentials(
    provider: str,
    profile: str | None,
    values: Any,
    remove: Any = None,
    *,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    _validate_account(provider, profile, data_dir)
    fields = {field["name"]: field for field in _FIELDS[provider]}
    remove = [] if remove is None else remove
    if (
        not isinstance(values, dict)
        or not isinstance(remove, list)
        or any(not isinstance(name, str) or name not in fields for name in remove)
        or any(name not in fields or not isinstance(value, str) for name, value in values.items())
    ):
        raise UsageCredentialError("Invalid reporting credential fields.")
    if any(len(value) > 16384 or any(ord(c) < 32 for c in value) for value in values.values()):
        raise UsageCredentialError("Reporting fields must be single-line values under 16 KB.")
    if any(name in remove and value.strip() for name, value in values.items()):
        raise UsageCredentialError("Choose either replace or remove for each credential.")
    with _lock:
        current = resolve_usage_credentials(provider, profile, data_dir=data_dir)
        updated = dict(current)
        for name, value in values.items():
            value = value.strip()
            if value:
                updated[name] = value
            elif not fields[name]["secret"]:
                updated.pop(name, None)
        for name in remove:
            updated.pop(name, None)
        if updated == current:
            return usage_credentials_payload(provider, profile, data_dir=data_dir)
        revision = uuid4().hex
        account = _account(provider, profile, data_dir)
        try:
            store = _secret_store()
            # Save first. A failed DB write leaves a revision mismatch, so old
            # measurements cannot be presented as belonging to the new account.
            store.put(account, json.dumps({"revision": revision, "values": updated}))
            with usage_db(data_dir) as db:
                db.execute(
                    "INSERT OR REPLACE INTO usage_meta VALUES (?, ?)",
                    ("reporting:" + _scope(provider, profile), revision),
                )
        except Exception:
            raise UsageCredentialError(
                "Could not save reporting credentials. " + _STORE_ERROR
            ) from None
    return usage_credentials_payload(provider, profile, data_dir=data_dir)
