"""Read-only secrets from a mounted folder, for headless servers with no system keychain.

``Raticode serve --headless --secrets-dir PATH`` names a folder of one-value files, such
as systemd's ``$CREDENTIALS_DIRECTORY`` (the default when systemd sets it) or Docker's
``/run/secrets``. Callers of the system keychain read a secret here only when the
keychain can't give it to them:

- ``jev-key``: the Jev classifier key.
- ``org-secret-ACCOUNT``: an Organizations secret or webhook secret, where ACCOUNT is
  the secret reference's account with each ``/`` written as ``.``.
- ``usage-PROVIDER-FIELD`` or ``usage-PROVIDER-PROFILE-FIELD``: a usage reporting
  credential, such as ``usage-openai_api-admin_api_key``.
- ``workflow-secret-NAME``: the value Rattish ``secret["NAME"]`` reads when the keychain
  has no saved value, before the environment variable with the same name.

Each file must be a regular file that belongs to the backend's account, with no access
for group or others (mode 600 or 400): the same rule as ``--token-file``. Raticode never
writes this folder, never reads one of its secrets from an environment variable or
argument, and saving a secret still needs the keychain. The folder is set for this process only, so
nothing here applies to the desktop app's backend.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gofer.utils.private_files import private_file_problem

CREDENTIALS_DIRECTORY_ENV = "CREDENTIALS_DIRECTORY"
SECRETS_DIR_OPTION = "--secrets-dir"
MAX_SECRET_BYTES = 64 * 1024
JEV_KEY_FILE = "jev-key"
ORG_SECRET_PREFIX = "org-secret-"
USAGE_PREFIX = "usage-"
WORKFLOW_SECRET_PREFIX = "workflow-secret-"
_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")
_ACCOUNT_PART = re.compile(r"[A-Za-z0-9_-]{1,100}")
_PART = re.compile(r"[A-Za-z0-9_.-]{1,100}")

SAVE_HINT = (
    "On a server without a system keychain, put the value in a secrets folder instead and "
    f"start the backend with `Raticode serve --headless {SECRETS_DIR_OPTION} PATH`; Raticode "
    "reads it from there but can't save to it."
)

_lock = threading.Lock()
_folder: Path | None = None


class SecretsFolderError(ValueError):
    """A secrets folder or one of its files is unsafe or unreadable. Never holds a value."""


def resolve_secrets_dir(option: Path | None, environ: dict[str, str] | None = None) -> Path | None:
    """``--secrets-dir`` when given, else systemd's ``$CREDENTIALS_DIRECTORY``, else None.

    The environment variable holds only a folder path, never a secret.
    """
    if option is not None:
        return option
    value = (os.environ if environ is None else environ).get(CREDENTIALS_DIRECTORY_ENV, "")
    return Path(value) if value.strip() else None


def check_secrets_dir(path: Path) -> Path:
    """The absolute folder path, or an error when it isn't a folder only this account
    (or root, which mounts container secrets) can change."""
    folder = Path(os.path.abspath(path.expanduser()))
    try:
        info = folder.stat()
    except FileNotFoundError as exc:
        raise SecretsFolderError(f"The secrets folder {folder} doesn't exist") from exc
    except OSError as exc:
        raise SecretsFolderError(
            f"The secrets folder {folder} can't be read: {exc.strerror or exc}"
        ) from exc
    if not folder.is_dir():
        raise SecretsFolderError(f"The secrets folder {folder} isn't a folder")
    if sys.platform != "win32":
        if info.st_uid not in {os.getuid(), 0}:
            raise SecretsFolderError(f"The secrets folder {folder} belongs to another account")
        if info.st_mode & 0o022:
            raise SecretsFolderError(
                f"Other accounts can add files to the secrets folder {folder}; run chmod go-w on it"
            )
    if not os.access(folder, os.R_OK | os.X_OK):
        raise SecretsFolderError(f"The secrets folder {folder} can't be read by this account")
    return folder


def configure_secrets_dir(path: Path | None) -> Path | None:
    """Use [path] as this process's secrets folder (None turns it off)."""
    global _folder
    folder = check_secrets_dir(path) if path is not None else None
    with _lock:
        _folder = folder
    return folder


def configured_secrets_dir() -> Path | None:
    with _lock:
        return _folder


def read_folder_secret(name: str) -> str | None:
    """The value of file [name] in the secrets folder, or None when there's no folder or
    no such file. Raises :class:`SecretsFolderError` for a file that breaks the rules."""
    folder = configured_secrets_dir()
    if folder is None or not _FILE_NAME.fullmatch(name):
        return None
    path = folder / name
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError:
        # O_NOFOLLOW refuses a symlink with ELOOP.
        raise SecretsFolderError(
            f"The secrets folder file {path} must be a regular file Raticode can read"
        ) from None
    try:
        # Check the opened file itself, so it can't be swapped between check and read.
        problem = private_file_problem(os.fstat(descriptor), secret=True)
        if problem:
            raise SecretsFolderError(f"The secrets folder file {path} {problem}")
        content = b""
        while len(content) <= MAX_SECRET_BYTES:
            chunk = os.read(descriptor, MAX_SECRET_BYTES + 1 - len(content))
            if not chunk:
                break
            content += chunk
    finally:
        os.close(descriptor)
    if len(content) > MAX_SECRET_BYTES:
        raise SecretsFolderError(
            f"The secrets folder file {path} is larger than {MAX_SECRET_BYTES // 1024} KiB"
        )
    try:
        value = content.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise SecretsFolderError(f"The secrets folder file {path} isn't UTF-8 text") from None
    if not value or "\x00" in value:
        raise SecretsFolderError(f"The secrets folder file {path} is empty or holds a NUL byte")
    return value


def folder_has_file(name: str) -> bool:
    """Whether the secrets folder has an entry called [name], without reading it."""
    folder = configured_secrets_dir()
    if folder is None or not _FILE_NAME.fullmatch(name):
        return False
    return os.path.lexists(folder / name)


def folder_fingerprint(names: list[str]) -> str:
    """An opaque tag that changes when any of these files is replaced or edited.

    Built from file metadata only, never from a value. Empty when none of them exist.
    """
    folder = configured_secrets_dir()
    if folder is None:
        return ""
    parts = []
    for name in sorted(names):
        if not _FILE_NAME.fullmatch(name):
            continue
        try:
            info = os.lstat(folder / name)
        except OSError:
            continue
        parts.append(f"{name}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}")
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:32]


def folder_file_names() -> list[str]:
    """Names of the files in the secrets folder (never their contents)."""
    folder = configured_secrets_dir()
    if folder is None:
        return []
    try:
        return sorted(entry.name for entry in os.scandir(folder) if not entry.is_dir())
    except OSError:
        return []


def keychain_then_folder(
    read_keychain: Callable[[], str | None], file_name: str | None
) -> str | None:
    """A keychain read that falls back to the secrets folder file [file_name].

    The keychain wins when it has a value. When it can't be reached, or has nothing, the
    folder file is used. With neither, the keychain's own error is raised again (or None
    is returned when the keychain simply had no entry).
    """
    error: Exception | None = None
    try:
        value = read_keychain()
    except Exception as exc:
        value, error = None, exc
    if value:
        return value
    folder_value = read_folder_secret(file_name) if file_name else None
    if folder_value is not None:
        return folder_value
    if error is not None:
        raise error
    return value


def org_secret_file(account: str) -> str | None:
    """The file an Organizations secret account reads, or None for an account that can't
    be a file name. ``/`` becomes ``.``, which accounts may not otherwise contain, so two
    accounts never share a file."""
    parts = account.split("/")
    if not all(_ACCOUNT_PART.fullmatch(part) for part in parts):
        return None
    name = ORG_SECRET_PREFIX + ".".join(parts)
    return name if _FILE_NAME.fullmatch(name) else None


def usage_secret_file(provider: str, profile: str | None, field: str) -> str | None:
    parts = [provider, *([profile] if profile else []), field]
    if not all(_PART.fullmatch(part) for part in parts):
        return None
    name = USAGE_PREFIX + "-".join(parts)
    return name if _FILE_NAME.fullmatch(name) else None


def workflow_secret_file(name: str) -> str | None:
    """The file ``secret["NAME"]`` reads, or None for a name that can't be a file name.

    Workflow secret names already use only letters, digits, ``_``, ``.`` and ``-``, so the
    file is the name with a prefix and two names never share a file.
    """
    file_name = WORKFLOW_SECRET_PREFIX + name
    return file_name if _FILE_NAME.fullmatch(file_name) else None


def keychain_available() -> bool:
    """Whether the system keychain can be reached, without reading or unlocking anything."""
    try:
        from gofer.devices.storage import OSSecretStore

        OSSecretStore()
    except Exception:
        return False
    return True


@dataclass(frozen=True)
class SecretFeature:
    """Where one secret-backed feature gets its secrets on this computer."""

    id: str
    label: str
    source: str  # "keychain", "secrets folder" or "unavailable"
    detail: str

    @property
    def available(self) -> bool:
        return self.source != "unavailable"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "source": self.source,
            "available": self.available,
            "detail": self.detail,
        }


def secret_features(keychain: bool | None = None) -> list[SecretFeature]:
    """Each secret-backed feature and where it reads secrets, for doctor and the log.

    Reads secrets folder files only to check them; their values go nowhere.
    """
    if keychain is None:
        keychain = keychain_available()
    names = folder_file_names()
    folder = configured_secrets_dir()

    def folder_source(files: list[str], need: str) -> tuple[str, str]:
        usable = []
        for name in files:
            try:
                if read_folder_secret(name) is not None:
                    usable.append(name)
            except SecretsFolderError as exc:
                return "unavailable", str(exc)
        if usable:
            return "secrets folder", "Read-only from " + ", ".join(usable) + "."
        if folder is not None:
            return "unavailable", f"No system keychain. Add {need} to the secrets folder {folder}."
        return "unavailable", (
            f"No system keychain. Put {need} in a secrets folder and start the backend "
            f"with {SECRETS_DIR_OPTION}."
        )

    features: list[SecretFeature] = []
    for feature_id, label, files, need in (
        ("jev", "Jev classifier key", [n for n in names if n == JEV_KEY_FILE], JEV_KEY_FILE),
        (
            "organization_secrets",
            "Organizations secrets and webhook secrets",
            [n for n in names if n.startswith(ORG_SECRET_PREFIX)],
            ORG_SECRET_PREFIX + "ACCOUNT",
        ),
        (
            "usage_credentials",
            "Usage reporting credentials",
            [n for n in names if n.startswith(USAGE_PREFIX)],
            USAGE_PREFIX + "PROVIDER-FIELD",
        ),
        (
            "workflow_secrets",
            'Workflow secrets (secret["NAME"])',
            [n for n in names if n.startswith(WORKFLOW_SECRET_PREFIX)],
            WORKFLOW_SECRET_PREFIX + "NAME",
        ),
    ):
        if keychain:
            features.append(SecretFeature(feature_id, label, "keychain", "System keychain."))
            continue
        source, detail = folder_source(files, need)
        if feature_id == "workflow_secrets" and source == "unavailable" and not files:
            detail += ' secret["NAME"] still reads the environment variable with the same name.'
        features.append(SecretFeature(feature_id, label, source, detail))
    features.append(
        SecretFeature(
            "devices",
            "Phone pairing",
            "keychain" if keychain else "unavailable",
            "System keychain."
            if keychain
            else "No system keychain to keep the device identity in.",
        )
    )
    return features
