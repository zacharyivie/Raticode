"""Workflow secrets kept in the OS keychain, read by ``secret["NAME"]`` in Rattish.

Each saved value is one keychain entry under the service "Raticode workflow secrets", in
an account named after this data folder and the secret, so a development data folder
never reads the installed app's secrets. A small JSON file in the data folder lists the
saved names with an opaque revision and the time they were saved. It never holds a value.

A reference checks the keychain first. When the keychain has no saved value, or can't give
it, a headless backend with a secrets folder (:mod:`gofer.core.secrets_folder`) reads the
read-only file ``workflow-secret-NAME``. Last comes the environment variable with the same
name, so workflows that read secrets from the environment keep working. The keychain is
only asked about names the index lists, so a workflow that uses only environment
variables never touches it. Saving still needs the keychain; Raticode never writes the
folder. Nothing here returns, logs or exports a saved value except to the
runtime binding that asked for it, which redacts it from node results.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from gofer.core.secrets_folder import (
    SECRETS_DIR_OPTION,
    SecretsFolderError,
    configured_secrets_dir,
    folder_file_names,
    folder_has_file,
    read_folder_secret,
    workflow_secret_file,
)
from gofer.core.secrets_folder import WORKFLOW_SECRET_PREFIX as FOLDER_FILE_PREFIX
from gofer.utils.paths import get_data_dir

KEYCHAIN_SERVICE = "Raticode workflow secrets"
INDEX_FILE = "workflow-secrets.json"
MAX_VALUE_BYTES = 16 * 1024
MAX_SECRETS = 500
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}")
STORE_ERROR = (
    "Raticode couldn't use the system keychain. Unlock it (on Linux, the Secret Service "
    "such as GNOME Keyring or KWallet) and try again."
)
SECRETS_FOLDER = "secrets folder"
_lock = threading.RLock()
# (account, revision) -> value, so a loop doesn't ask the keychain on every iteration.
_cache: dict[tuple[str, str], str] = {}


class WorkflowSecretError(ValueError):
    """A safe error for the CLI, the settings section and runs. Never contains a value.

    ``source`` names where a read failed: "keychain", "secrets folder" or None.
    """

    def __init__(self, message: str, source: str | None = None) -> None:
        super().__init__(message)
        self.source = source


class _Store(Protocol):
    def get(self, account: str) -> str | None: ...
    def put(self, account: str, value: str) -> None: ...
    def delete(self, account: str) -> None: ...


def _secret_store() -> _Store:
    from gofer.devices.storage import OSSecretStore

    return OSSecretStore(service=KEYCHAIN_SERVICE)


def how_to_add(name: str) -> str:
    """The sentence every missing-secret message ends with."""
    folder = configured_secrets_dir()
    file_name = workflow_secret_file(name)
    if folder is not None and file_name:
        return (
            f"Add it in Workflow settings > Secrets, run `Raticode rattish secrets set {name}`, "
            f"put it in a {file_name} file in the secrets folder {folder}, "
            f"or set the {name} environment variable for Raticode."
        )
    return (
        f"Add it in Workflow settings > Secrets, run `Raticode rattish secrets set {name}`, "
        f"or set the {name} environment variable for Raticode."
    )


def save_error(name: str) -> str:
    """Why saving failed. Saving is keychain-only, so name the read-only alternative."""
    return (
        f"{STORE_ERROR} On a server without a system keychain, put the value in a file named "
        f"{FOLDER_FILE_PREFIX}{name} in a secrets folder and start the backend with "
        f"`Raticode serve --headless {SECRETS_DIR_OPTION} PATH`; Raticode reads it from there "
        "but can't save to it."
    )


def not_found_message(name: str) -> str:
    """Why ``secret["NAME"]`` has no value, ending with how to add one."""
    places = (
        "saved in Raticode, in the secrets folder or set in the environment"
        if configured_secrets_dir() is not None
        else "saved in Raticode or set in the environment"
    )
    return f"Workflow secret {name!r} isn't {places}. {how_to_add(name)}"


def check_name(name: Any) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise WorkflowSecretError(
            "A secret name starts with a letter or underscore and uses only letters, "
            "digits, _, . and - (up to 128 characters), such as SERVICE_TOKEN."
        )
    return name


def _check_value(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowSecretError("Enter a value for the secret.")
    if "\x00" in value:
        raise WorkflowSecretError("A secret value can't contain a NUL character.")
    if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
        raise WorkflowSecretError(f"A secret value is at most {MAX_VALUE_BYTES // 1024} KiB.")
    return value


def _root(data_dir: Path | None) -> Path:
    return (data_dir or get_data_dir()).expanduser().resolve()


def _account(data_dir: Path | None, name: str) -> str:
    return hashlib.sha256(str(_root(data_dir)).encode()).hexdigest() + ":workflow:" + name


def _index_path(data_dir: Path | None) -> Path:
    return _root(data_dir) / INDEX_FILE


def _load(data_dir: Path | None) -> dict[str, dict[str, str]]:
    try:
        raw = json.loads(_index_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    secrets = raw.get("secrets") if isinstance(raw, dict) else None
    if not isinstance(secrets, dict):
        return {}
    clean: dict[str, dict[str, str]] = {}
    for name, entry in secrets.items():
        if (
            isinstance(name, str)
            and _NAME.fullmatch(name)
            and isinstance(entry, dict)
            and isinstance(entry.get("revision"), str)
            and entry["revision"]
        ):
            updated = entry.get("updatedAt")
            clean[name] = {
                "revision": entry["revision"],
                "updatedAt": updated if isinstance(updated, str) else "",
            }
    return clean


def _save(data_dir: Path | None, secrets: dict[str, dict[str, str]]) -> None:
    path = _index_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    body = {"version": 1, "secrets": dict(sorted(secrets.items()))}
    try:
        temporary.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _index_lock(data_dir: Path | None) -> Iterator[None]:
    """Serialize index changes across processes (the app and the CLI) and threads."""
    path = _index_path(data_dir).with_name(f".{INDEX_FILE}.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if sys.platform == "win32":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)


def _in_folder(name: str) -> bool:
    file_name = workflow_secret_file(name)
    return file_name is not None and folder_has_file(file_name)


def _entry(name: str, record: Mapping[str, str], environ: Mapping[str, str]) -> dict[str, Any]:
    return {
        "name": name,
        "updatedAt": record.get("updatedAt") or None,
        "inEnvironment": name in environ,
        "inSecretsFolder": _in_folder(name),
    }


def list_workflow_secrets(
    data_dir: Path | None = None, environ: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    """Saved names and when they were saved. Never reads the keychain or a value."""
    env = os.environ if environ is None else environ
    return [_entry(name, record, env) for name, record in sorted(_load(data_dir).items())]


def list_folder_workflow_secrets(data_dir: Path | None = None) -> list[dict[str, Any]]:
    """Names the secrets folder holds a ``workflow-secret-NAME`` file for.

    Lists file names only, never reads a file. Empty without a secrets folder.
    """
    saved = _load(data_dir)
    entries = []
    for file_name in folder_file_names():
        if not file_name.startswith(FOLDER_FILE_PREFIX):
            continue
        name = file_name[len(FOLDER_FILE_PREFIX) :]
        if _NAME.fullmatch(name):
            entries.append({"name": name, "file": file_name, "alsoSaved": name in saved})
    return entries


def workflow_secret_saved(name: str, data_dir: Path | None = None) -> bool:
    return name in _load(data_dir)


def save_workflow_secret(name: Any, value: Any, data_dir: Path | None = None) -> dict[str, Any]:
    name = check_name(name)
    value = _check_value(value)
    revision = uuid4().hex
    with _index_lock(data_dir):
        secrets = _load(data_dir)
        if name not in secrets and len(secrets) >= MAX_SECRETS:
            raise WorkflowSecretError(f"Raticode keeps at most {MAX_SECRETS} workflow secrets.")
        try:
            _secret_store().put(
                _account(data_dir, name), json.dumps({"revision": revision, "value": value})
            )
        except Exception:
            raise WorkflowSecretError(save_error(name)) from None
        secrets[name] = {"revision": revision, "updatedAt": datetime.now(UTC).isoformat()}
        _save(data_dir, secrets)
        _cache.clear()
        return _entry(name, secrets[name], os.environ)


def remove_workflow_secret(name: Any, data_dir: Path | None = None) -> dict[str, Any]:
    name = check_name(name)
    with _index_lock(data_dir):
        secrets = _load(data_dir)
        if name not in secrets:
            raise WorkflowSecretError(f"No saved workflow secret is named {name}.")
        try:
            _secret_store().delete(_account(data_dir, name))
        except Exception:
            raise WorkflowSecretError(STORE_ERROR) from None
        del secrets[name]
        _save(data_dir, secrets)
        _cache.clear()
    return {"name": name, "removed": True}


def _read_saved(name: str, revision: str, data_dir: Path | None) -> str:
    account = _account(data_dir, name)
    with _lock:
        cached = _cache.get((account, revision))
        if cached is not None:
            return cached
        try:
            record = json.loads(_secret_store().get(account) or "{}")
        except Exception:
            raise WorkflowSecretError(
                f"Workflow secret {name!r} is saved. {STORE_ERROR}", "keychain"
            ) from None
        value = record.get("value") if isinstance(record, dict) else None
        if record.get("revision") != revision or not isinstance(value, str):
            raise WorkflowSecretError(
                f"Workflow secret {name!r} is missing from the system keychain. Save it again "
                f"in Workflow settings > Secrets or with `Raticode rattish secrets set {name}`.",
                "keychain",
            )
        _cache[(account, revision)] = value
        return value


def _read_folder(name: str) -> str | None:
    """The secrets folder's ``workflow-secret-NAME`` file, read fresh so a replaced file
    counts. None without a folder or file; an unsafe file is an error, never skipped."""
    file_name = workflow_secret_file(name)
    if not file_name:
        return None
    try:
        return read_folder_secret(file_name)
    except SecretsFolderError as exc:
        raise WorkflowSecretError(
            f"Workflow secret {name!r} can't be read: {exc}.", SECRETS_FOLDER
        ) from None


def workflow_secret_value(
    name: str, data_dir: Path | None = None, environ: Mapping[str, str] | None = None
) -> str | None:
    """The value ``secret["NAME"]`` resolves to: the keychain, then the secrets folder's
    ``workflow-secret-NAME`` file, then the environment.

    Returns None when none has it. Raises :class:`WorkflowSecretError` when the folder file
    breaks the folder's rules, or when the name is saved but the keychain can't give the
    value and neither the folder nor the environment has one.
    """
    env = os.environ if environ is None else environ
    record = _load(data_dir).get(name)
    keychain_error: WorkflowSecretError | None = None
    if record is not None:
        try:
            return _read_saved(name, record["revision"], data_dir)
        except WorkflowSecretError as exc:
            keychain_error = exc
    folder_value = _read_folder(name)
    if folder_value is not None:
        return folder_value
    if name in env:
        return env[name]
    if keychain_error is not None:
        raise keychain_error
    return None


def secret_status(
    name: str, data_dir: Path | None = None, environ: Mapping[str, str] | None = None
) -> str:
    """Where a reference would get its value: "saved", "secrets folder", "environment"
    or "missing".

    Reads only the index, the folder's file names and the environment, never the keychain
    or a file.
    """
    env = os.environ if environ is None else environ
    if workflow_secret_saved(name, data_dir):
        return "saved"
    if _in_folder(name):
        return SECRETS_FOLDER
    return "environment" if name in env else "missing"
