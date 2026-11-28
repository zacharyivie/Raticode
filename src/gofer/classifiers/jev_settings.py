"""The Jev key, kept in the OS keychain, and Jev's non-secret settings.

The keychain entry lives under the service "Raticode provider keys", in an account named
after this data folder, so a development data folder never reads the installed app's
key. A small JSON file in the data folder holds only an opaque revision, the on/off
switch and the last key check. Nothing here returns, logs or exports the key, and it is
never read from or written to an environment variable.

On a headless server with no keychain, the key can instead be the file ``jev-key`` in
the backend's secrets folder (``Raticode serve --headless --secrets-dir PATH``, see
:mod:`gofer.core.secrets_folder`). That file is only read: saving still needs the
keychain, and a key saved there is used first.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from gofer.classifiers.jev import (
    DEFAULT_JEV_MODEL,
    DEFAULT_JEV_THRESHOLD,
    JEV_DOCS_URL,
    JEV_ENDPOINT,
    JevClient,
    JevError,
)
from gofer.core.secrets_folder import (
    JEV_KEY_FILE,
    SAVE_HINT,
    SECRETS_DIR_OPTION,
    SecretsFolderError,
    folder_has_file,
    read_folder_secret,
)
from gofer.utils.atomic_output import replace_file
from gofer.utils.paths import get_data_dir

KEYCHAIN_SERVICE = "Raticode provider keys"
SETTINGS_FILE = "jev-settings.json"
MAX_KEY_LENGTH = 4096
STORE_ERROR = (
    "Raticode couldn't use the system keychain. Unlock it (on Linux, the Secret Service "
    "such as GNOME Keyring or KWallet) and try again."
)
SAVE_ERROR = f"{STORE_ERROR} {SAVE_HINT}"
READ_ERROR = (
    f"{STORE_ERROR} On a server without one, put the key in a file named {JEV_KEY_FILE} "
    f"in the secrets folder (Raticode serve --headless {SECRETS_DIR_OPTION} PATH)."
)
_lock = threading.RLock()
# (account, revision) -> key, so a workflow loop doesn't ask the keychain on every call.
_cache: dict[tuple[str, str], str] = {}


class JevSettingsError(ValueError):
    """A safe error for the Settings card and the CLI. Never contains the key."""


class _Store(Protocol):
    def get(self, account: str) -> str | None: ...
    def put(self, account: str, value: str) -> None: ...
    def delete(self, account: str) -> None: ...


def _secret_store() -> _Store:
    from gofer.devices.storage import OSSecretStore

    return OSSecretStore(service=KEYCHAIN_SERVICE)


def _root(data_dir: Path | None) -> Path:
    return (data_dir or get_data_dir()).expanduser().resolve()


def _account(data_dir: Path | None) -> str:
    return hashlib.sha256(str(_root(data_dir)).encode()).hexdigest() + ":jev"


def _settings_path(data_dir: Path | None) -> Path:
    return _root(data_dir) / SETTINGS_FILE


def _load(data_dir: Path | None) -> dict[str, Any]:
    try:
        raw = json.loads(_settings_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    models = raw.get("models")
    return {
        "revision": raw["revision"] if isinstance(raw.get("revision"), str) else "",
        "enabled": raw["enabled"] if isinstance(raw.get("enabled"), bool) else True,
        "models": [m for m in models if isinstance(m, str)] if isinstance(models, list) else [],
        "checked_at": raw["checked_at"] if isinstance(raw.get("checked_at"), str) else None,
        "check_error": raw["check_error"] if isinstance(raw.get("check_error"), str) else None,
    }


def _save(data_dir: Path | None, settings: dict[str, Any]) -> None:
    text = json.dumps(settings, indent=2) + "\n"
    replace_file(_settings_path(data_dir), text.encode("utf-8"))


def jev_key_configured(data_dir: Path | None = None) -> bool:
    """Whether a key was saved or is in the secrets folder, without touching the keychain."""
    return bool(_load(data_dir)["revision"]) or folder_has_file(JEV_KEY_FILE)


def jev_key_source(data_dir: Path | None = None) -> str | None:
    """Where the key comes from: "keychain", "secrets folder" or None.

    Reads neither the keychain nor the file.
    """
    if _load(data_dir)["revision"]:
        return "keychain"
    return "secrets folder" if folder_has_file(JEV_KEY_FILE) else None


def jev_enabled(data_dir: Path | None = None) -> bool:
    return bool(_load(data_dir)["enabled"])


def _folder_key() -> str | None:
    """The key from the secrets folder's jev-key file, read fresh so a rotated file counts."""
    try:
        value = read_folder_secret(JEV_KEY_FILE)
        return None if value is None else _clean_key(value)
    except SecretsFolderError as exc:
        raise JevError("not_configured", str(exc), retryable=False) from None
    except JevSettingsError:
        raise JevError(
            "not_configured",
            f"The {JEV_KEY_FILE} file in the secrets folder must hold one line with no spaces.",
            retryable=False,
        ) from None


def read_jev_key(data_dir: Path | None = None) -> str | None:
    """The saved key, or None when there is none. Raises when Jev is turned off.

    The keychain comes first. When it can't give a key, the secrets folder's
    ``jev-key`` file is used, if the backend has a secrets folder.
    """
    settings = _load(data_dir)
    revision = settings["revision"]
    if not revision and not folder_has_file(JEV_KEY_FILE):
        return None
    if not settings["enabled"]:
        raise JevError(
            "not_configured", "Jev is turned off in Settings > Providers > Jev.", retryable=False
        )
    if not revision:
        return _folder_key()
    account = _account(data_dir)
    with _lock:
        cached = _cache.get((account, revision))
        if cached is not None:
            return cached
        try:
            record = json.loads(_secret_store().get(account) or "{}")
        except Exception:
            folder_key = _folder_key()
            if folder_key is not None:
                return folder_key
            raise JevError("not_configured", READ_ERROR, retryable=False) from None
        key = record.get("key") if isinstance(record, dict) else None
        if record.get("revision") != revision or not isinstance(key, str) or not key:
            folder_key = _folder_key()
            if folder_key is not None:
                return folder_key
            raise JevError(
                "not_configured",
                "The saved Jev key is missing from the keychain. Save it again in "
                "Settings > Providers > Jev.",
            )
        _cache.clear()
        _cache[(account, revision)] = key
        return key


def _clean_key(value: Any) -> str:
    if not isinstance(value, str):
        raise JevSettingsError("Enter a Jev API key.")
    key = value.strip()
    if not key:
        raise JevSettingsError("Enter a Jev API key.")
    if len(key) > MAX_KEY_LENGTH or any(ch.isspace() or ord(ch) < 32 for ch in key):
        raise JevSettingsError("A Jev API key is one line with no spaces.")
    return key


def save_jev_key(value: Any, data_dir: Path | None = None) -> dict[str, Any]:
    key = _clean_key(value)
    revision = uuid4().hex
    with _lock:
        try:
            _secret_store().put(_account(data_dir), json.dumps({"revision": revision, "key": key}))
        except Exception:
            raise JevSettingsError(SAVE_ERROR) from None
        settings = _load(data_dir)
        settings.update(revision=revision, checked_at=None, check_error=None, models=[])
        _save(data_dir, settings)
        _cache.clear()
    return jev_status(data_dir)


def remove_jev_key(data_dir: Path | None = None) -> dict[str, Any]:
    with _lock:
        try:
            _secret_store().delete(_account(data_dir))
        except Exception:
            raise JevSettingsError(SAVE_ERROR) from None
        settings = _load(data_dir)
        settings.update(revision="", checked_at=None, check_error=None, models=[])
        _save(data_dir, settings)
        _cache.clear()
    return jev_status(data_dir)


def save_jev_settings(changes: Any, data_dir: Path | None = None) -> dict[str, Any]:
    if not isinstance(changes, dict) or set(changes) - {"enabled"}:
        raise JevSettingsError("Unknown Jev setting.")
    if "enabled" in changes and not isinstance(changes["enabled"], bool):
        raise JevSettingsError("enabled must be true or false.")
    with _lock:
        settings = _load(data_dir)
        settings.update(changes)
        _save(data_dir, settings)
    return jev_status(data_dir)


def jev_status(data_dir: Path | None = None) -> dict[str, Any]:
    """Everything the Settings card and CLI show. Never includes the key."""
    settings = _load(data_dir)
    return {
        "provider": "jev",
        "label": "Jev (TypeSafe)",
        "kind": "classifier",
        "configured": jev_key_configured(data_dir),
        "keySource": jev_key_source(data_dir),
        "enabled": settings["enabled"],
        "models": settings["models"],
        "checkedAt": settings["checked_at"],
        "checkError": settings["check_error"],
        "defaultModel": DEFAULT_JEV_MODEL,
        "defaultThreshold": DEFAULT_JEV_THRESHOLD,
        "endpoint": JEV_ENDPOINT,
        "docsUrl": JEV_DOCS_URL,
    }


def default_jev_client(data_dir: Path | None = None) -> JevClient:
    """The client workflow nodes use: key from the keychain, shared call limits."""
    root = _root(data_dir)
    return JevClient(key=lambda: read_jev_key(root), data_dir=root)


async def check_jev_key(
    data_dir: Path | None = None, client: JevClient | None = None
) -> dict[str, Any]:
    """Ask Jev for its model list with the saved key and remember the outcome."""
    if not jev_key_configured(data_dir):
        raise JevSettingsError("Save a Jev key first.")
    if not jev_enabled(data_dir):
        raise JevSettingsError("Jev is turned off. Turn it on to check the key.")
    client = client or default_jev_client(data_dir)
    error: str | None = None
    models: list[str] | None = None
    try:
        models = await client.list_models()
    except JevError as exc:
        error = exc.message
    with _lock:
        settings = _load(data_dir)
        settings.update(checked_at=datetime.now(UTC).isoformat(), check_error=error)
        if models is not None:
            settings["models"] = models
        _save(data_dir, settings)
    return jev_status(data_dir)
