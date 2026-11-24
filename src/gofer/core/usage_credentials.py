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
from gofer.core.secrets_folder import (
    SAVE_HINT,
    SecretsFolderError,
    configured_secrets_dir,
    folder_fingerprint,
    read_folder_secret,
    usage_secret_file,
)
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
    "cursor": "Optional. Raticode reads plan usage from your Cursor sign-in automatically. "
    "Connect team admin reporting only to track a team member's on-demand spend instead.",
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


class _NoProfile:
    account_id = None


_NO = _NoProfile()


def _validate_account(provider: Any, profile: Any, data_dir: Path | None) -> None:
    if not isinstance(provider, str) or provider not in _FIELDS:
        raise UsageCredentialError("This provider has no configurable reporting credentials.")
    if profile is not None:
        if not isinstance(profile, str) or not profile:
            raise UsageCredentialError("Invalid provider profile.")
        configured = load_provider_profiles(data_dir).get(profile)
        if configured is None or configured.subscription != provider:
            raise UsageCredentialError("The provider profile does not match this account.")


def _saved_revision(provider: str, profile: str | None, data_dir: Path | None) -> str:
    with usage_db(data_dir) as db:
        row = db.execute(
            "SELECT value FROM usage_meta WHERE key=?", ("reporting:" + _scope(provider, profile),)
        ).fetchone()
    return str(row[0]) if row else ""


def _folder_files(provider: str, profile: str | None) -> dict[str, str]:
    """Field name -> secrets folder file name for this account."""
    files = {}
    for field in _FIELDS[provider]:
        name = usage_secret_file(provider, profile, field["name"])
        if name is not None:
            files[field["name"]] = name
    return files


def _folder_values(provider: str, profile: str | None) -> dict[str, str]:
    """Read-only values from the headless secrets folder, if the backend has one."""
    values = {}
    for field, name in _folder_files(provider, profile).items():
        try:
            value = read_folder_secret(name)
        except SecretsFolderError as exc:
            raise UsageCredentialError(str(exc)) from None
        if value is not None:
            values[field] = value
    return values


def usage_credential_revision(
    provider: str, profile: str | None = None, *, data_dir: Path | None = None
) -> str:
    """The saved credentials' revision, else one for the secrets folder's files.

    A folder revision changes when one of the files does, so measurements taken with old
    credentials aren't shown as the new account's. It never depends on a value.
    """
    if provider not in _FIELDS:
        return ""
    saved = _saved_revision(provider, profile, data_dir)
    if saved:
        return saved
    fingerprint = folder_fingerprint(list(_folder_files(provider, profile).values()))
    return "secrets-folder:" + fingerprint if fingerprint else ""


def _keychain_values(
    provider: str, profile: str | None, revision: str, data_dir: Path | None
) -> dict[str, str]:
    raw = _secret_store().get(_account(provider, profile, data_dir))
    record = json.loads(raw or "{}")
    if record.get("revision") != revision:
        raise ValueError("Credential revision mismatch")
    values = record["values"]
    if not isinstance(values, dict) or any(
        name not in {field["name"] for field in _FIELDS[provider]} or not isinstance(value, str)
        for name, value in values.items()
    ):
        raise ValueError("Invalid stored credential")
    return values


def resolve_usage_credentials(
    provider: str, profile: str | None = None, *, data_dir: Path | None = None
) -> dict[str, str]:
    """Saved credentials from the keychain; when it can't give them, the secrets folder."""
    if provider not in _FIELDS:
        return {}
    _validate_account(provider, profile, data_dir)
    with _lock:
        revision = _saved_revision(provider, profile, data_dir)
        if not revision:
            return _folder_values(provider, profile)
        try:
            return _keychain_values(provider, profile, revision, data_dir)
        except Exception:
            folder = _folder_values(provider, profile)
            if folder:
                return folder
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
    except Exception:
        error = _STORE_ERROR + " " + SAVE_HINT
    try:
        # Without a keychain, a headless backend can still read its secrets folder. With
        # no folder, a failed probe means the read would fail too (and could prompt again).
        if error is None or configured_secrets_dir() is not None:
            values = resolve_usage_credentials(provider, profile, data_dir=data_dir)
    except UsageCredentialError as exc:
        error = error or str(exc)
    except Exception:
        error = error or _STORE_ERROR
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
    if profile is not None and (load_provider_profiles(data_dir).get(profile) or _NO).account_id:
        # An account reports through its own CLI; Raticode keeps no token for it.
        from gofer.core.provider_accounts import account_adapter

        adapter = account_adapter(provider)
        label = adapter.label if adapter else "its provider's CLI"
        raise UsageCredentialError(
            f"{label} accounts report usage through {label}. No token is needed."
        )
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
        # Only the keychain's values: secrets folder files are never copied into it.
        saved = _saved_revision(provider, profile, data_dir)
        try:
            current = _keychain_values(provider, profile, saved, data_dir) if saved else {}
        except Exception:
            raise UsageCredentialError(
                "Could not save reporting credentials. " + _STORE_ERROR + " " + SAVE_HINT
            ) from None
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
                "Could not save reporting credentials. " + _STORE_ERROR + " " + SAVE_HINT
            ) from None
    return usage_credentials_payload(provider, profile, data_dir=data_dir)
