"""Headless backend support: private token and endpoint files, agent tokens, Rem settings.

``Raticode serve --headless`` runs the backend with no desktop window. Clients on the
same computer find it through two files in ``<data>/headless/``, both readable only by
this account (mode 0600 in a 0700 folder):

- ``endpoint.json``: the URL, process ID and token file of the running backend.
- ``owner-token``: the full-access API token. It is never passed on a command line.

The desktop app's backend has neither file yet, but it takes the same data-folder lock
(``.backend.lock``) and writes ``backend.json`` beside it with only its kind and
process ID, so a headless start and the CLI can tell the desktop app is running.

Agent tokens are for scripts and agents that should not act as the owner. Only a hash
of each token is stored; the token itself is written once, to a file the owner names.
An agent token may read most state but makes only a short list of writes
(:func:`agent_request_refusal`): it cannot edit, run or delete workflows, change
triggers, start or control swarms, approve organization consent requests or Kanban
proposals, change Rem's settings, pair devices, sign in to providers, read the owner
token, or manage path grants. It reads only Second Brains with Allow cloud processing
on, unless the owner created it with ``--private-brains`` (gofer.ui.brain_privacy).

The backend never hands either kind of token to Rem workers, and the client helpers
here refuse to read the owner token inside a Rem worker session. Files readable by
this account are still readable by any process running as it, so keep agent tokens
out of folders Rem's workers use.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from gofer.utils.atomic_output import replace_file
from gofer.utils.private_files import private_file_problem

log = logging.getLogger(__name__)

HEADLESS_DIRNAME = "headless"
OWNER_TOKEN_FILENAME = "owner-token"
ENDPOINT_FILENAME = "endpoint.json"
AGENT_TOKENS_FILENAME = "agent-tokens.json"
REM_SETTINGS_FILENAME = "rem-settings.json"
AGENT_TOKEN_PREFIX = "rtk_agent_"
MAX_REM_SETTINGS_BYTES = 256 * 1024
_TOKEN_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class HeadlessError(ValueError):
    """A headless file is missing, unsafe, or malformed."""


def headless_dir(data_dir: Path) -> Path:
    return data_dir / HEADLESS_DIRNAME


def owner_token_path(data_dir: Path) -> Path:
    return headless_dir(data_dir) / OWNER_TOKEN_FILENAME


def endpoint_path(data_dir: Path) -> Path:
    return headless_dir(data_dir) / ENDPOINT_FILENAME


def agent_tokens_path(data_dir: Path) -> Path:
    return headless_dir(data_dir) / AGENT_TOKENS_FILENAME


def default_rem_settings_path(data_dir: Path) -> Path:
    return headless_dir(data_dir) / REM_SETTINGS_FILENAME


def is_loopback_host(host: str) -> bool:
    """Whether [host] binds only this computer. Wildcards like 0.0.0.0 do not count."""
    value = host.strip().strip("[]").lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def ensure_private_dir(path: Path) -> Path:
    """Create [path] if needed and make it 0700 on POSIX."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        info = path.stat()
        if info.st_uid != os.getuid():
            raise HeadlessError(f"{path} belongs to another account")
        if info.st_mode & 0o077:
            path.chmod(0o700)
    return path


BACKEND_LOCK_FILENAME = ".backend.lock"
# Which kind of backend holds the lock: {"kind": "desktop" | "headless", "pid": N}.
# Never a token or an address; trusted only while the lock is held.
BACKEND_RECORD_FILENAME = "backend.json"
BACKEND_KINDS = ("desktop", "headless")


def backend_record_path(data_dir: Path) -> Path:
    return headless_dir(data_dir) / BACKEND_RECORD_FILENAME


# flock's answers when another process holds the lock. Any other error, such as ENOLCK
# or EOPNOTSUPP on some NFS and SMB mounts, means the file system can't lock at all.
_LOCK_HELD_ERRNOS = frozenset({errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES})


def _try_lock(descriptor: int) -> bool | None:
    """True when this took the lock, False when another process holds it, and None
    when the data folder's file system can't lock files."""
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            # Windows file systems lock; a failure means another process holds it.
            return False
        return True
    import fcntl

    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        return False if exc.errno in _LOCK_HELD_ERRNOS else None
    return True


def hold_backend_lock(data_dir: Path, *, kind: str = "headless", wait: float = 0.0) -> int:
    """Claim the data folder for this backend until it stops.

    The desktop app's backend and a headless one take the same lock. A second backend
    on the same folder would overwrite the owner token and endpoint file, run a second
    scheduler on the same stores, and leave the first one running with no published
    way in. [wait] is how many seconds to keep trying while an old backend shuts down.
    Returns the descriptor that holds the lock.

    On a file system that can't lock files (some network mounts), the backend still
    starts, with a warning: refusing would keep Raticode from ever running there, and
    the lock can't tell whether another backend uses the folder.
    """
    if kind not in BACKEND_KINDS:
        raise ValueError(f"Unknown backend kind {kind!r}")
    path = ensure_private_dir(headless_dir(data_dir)) / BACKEND_LOCK_FILENAME
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    deadline = time.monotonic() + max(wait, 0.0)
    while (taken := _try_lock(descriptor)) is False:
        if time.monotonic() >= deadline:
            os.close(descriptor)
            raise HeadlessError(_lock_refusal(data_dir, backend_lock_holder(data_dir), kind))
        time.sleep(0.1)
    if taken is None:
        log.warning(
            "The data folder %s can't be locked: its file system doesn't support file "
            "locks. Raticode starts anyway, but can't stop a second backend from using "
            "the same folder, so run only one.",
            data_dir,
        )
    try:
        record = json.dumps({"kind": kind, "pid": os.getpid()}) + "\n"
        write_private_file(backend_record_path(data_dir), record)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _lock_refusal(data_dir: Path, holder: str | None, kind: str) -> str:
    # The desktop app picks its data folder in Settings > General, not with --data-dir.
    elsewhere = (
        "choose another Data folder in Settings > General"
        if kind == "desktop"
        else "give this backend its own --data-dir"
    )
    if holder == "desktop":
        return (
            f"The Raticode desktop app is using the data folder {data_dir}. Quit it first, "
            f"or {elsewhere}."
        )
    which = "A headless Raticode backend" if holder == "headless" else "Another Raticode backend"
    return (
        f"{which} is already running with the data folder {data_dir}. Stop it first, "
        f"or {elsewhere}."
    )


def backend_lock_holder(data_dir: Path) -> str | None:
    """Which backend holds [data_dir]'s lock: "desktop", "headless", "unknown", or None.

    Only reads: a missing lock file or folder means no backend, and nothing is created.
    Where the file system can't lock, only a record naming a live process counts.
    """
    path = headless_dir(data_dir) / BACKEND_LOCK_FILENAME
    try:
        descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        taken = _try_lock(descriptor)
        if taken is None:
            kind = _recorded_kind(data_dir)
            return kind if kind in BACKEND_KINDS else None
        if taken:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            return None
    finally:
        os.close(descriptor)
    return _recorded_kind(data_dir)


def _recorded_kind(data_dir: Path) -> str:
    path = backend_record_path(data_dir)
    try:
        check_private_file(path, secret=False)
        record = json.loads(path.read_text(encoding="utf-8"))
    except (HeadlessError, OSError, ValueError):
        return "unknown"
    from gofer.utils.pids import pid_alive

    kind = record.get("kind") if isinstance(record, dict) else None
    # A record left by a crashed backend names a process that is gone.
    if kind in BACKEND_KINDS and pid_alive(record.get("pid")):
        return str(kind)
    return "unknown"


def release_backend_lock(descriptor: int | None) -> None:
    if descriptor is not None:
        with contextlib.suppress(OSError):
            os.close(descriptor)


def write_private_file(path: Path, text: str) -> None:
    """Atomically replace [path] with [text], readable only by this account (0600)."""
    ensure_private_dir(path.parent)
    replace_file(path, text.encode("utf-8"))


def create_private_file(path: Path, text: str) -> None:
    """Write [text] to a new file only this account can read; refuse to overwrite."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise HeadlessError(f"{path} already exists; choose a new file") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)


def check_private_file(path: Path, *, secret: bool) -> None:
    """Refuse a file another account could change, or for a secret, read."""
    if sys.platform == "win32":
        return
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise HeadlessError(f"{path} doesn't exist") from exc
    # lstat: a symlink is not a regular file, so it is refused rather than followed.
    problem = private_file_problem(info, secret=secret)
    if problem:
        raise HeadlessError(f"{path} {problem}")


def prepare_secrets_dir(option: Path | None) -> Path | None:
    """Choose and check this backend's read-only secrets folder.

    ``--secrets-dir`` wins; otherwise systemd's ``$CREDENTIALS_DIRECTORY`` when it set one.
    The variable is then sealed out of this process's environment like the desktop's
    tokens, so the providers, workers and commands the backend starts aren't pointed at
    the folder.
    """
    from gofer.core import secrets_folder
    from gofer.utils.process import seal_credential_env

    folder = secrets_folder.resolve_secrets_dir(option)
    seal_credential_env((secrets_folder.CREDENTIALS_DIRECTORY_ENV,))
    try:
        return secrets_folder.configure_secrets_dir(folder)
    except secrets_folder.SecretsFolderError as exc:
        raise HeadlessError(str(exc)) from exc


def secret_feature_log_lines(keychain: bool | None = None) -> list[tuple[bool, str]]:
    """(is_warning, line) pairs saying where each secret-backed feature reads secrets."""
    from gofer.core import secrets_folder

    folder = secrets_folder.configured_secrets_dir()
    lines: list[tuple[bool, str]] = []
    if folder is not None:
        lines.append((False, f"Reading secrets read-only from the secrets folder {folder}"))
    features = secrets_folder.secret_features(keychain)
    if all(feature.source == "keychain" for feature in features):
        lines.append((False, "Secrets are kept in the system keychain"))
        return lines
    for feature in features:
        if feature.available:
            lines.append((False, f"{feature.label}: {feature.source}. {feature.detail}"))
        else:
            lines.append((True, f"Unavailable: {feature.label}. {feature.detail}"))
    return lines


def read_token_file(path: Path) -> str:
    """Read an owner-only token file, such as a mounted container secret."""
    check_private_file(path, secret=True)
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 24 or any(ch.isspace() for ch in token):
        raise HeadlessError(f"{path} doesn't hold a usable token (24 or more characters)")
    return token


def new_owner_token() -> str:
    return secrets.token_urlsafe(32)


def read_endpoint(data_dir: Path) -> dict[str, Any]:
    """The running headless backend's endpoint record, for clients on this computer."""
    path = endpoint_path(data_dir)
    check_private_file(path, secret=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
        raise HeadlessError(f"{path} isn't a Raticode endpoint record")
    return payload


INSTANCE_PROOF_LABEL = b"raticode-instance-proof:"
_INSTANCE_NONCE = re.compile(r"[0-9a-f]{64}")


def instance_proof(token: str, nonce: str) -> str | None:
    """HMAC-SHA256 of a client's nonce under the owner token, or None for a bad nonce.

    A client checks this before sending the owner token, so a program squatting on a
    stale endpoint's port can't get it: forging the answer takes the token, and each
    request carries a fresh nonce (32 random bytes as lowercase hex), so an earlier
    answer can't be replayed.
    """
    if not token or not _INSTANCE_NONCE.fullmatch(nonce):
        return None
    message = INSTANCE_PROOF_LABEL + nonce.encode("ascii")
    return hmac.new(token.encode("utf-8"), message, hashlib.sha256).hexdigest()


def publish_endpoint(
    data_dir: Path,
    *,
    host: str,
    port: int,
    token_file: Path | None,
    secrets_dir: Path | None = None,
) -> Path:
    url_host = f"[{host}]" if ":" in host else host
    record = {
        "url": f"http://{url_host}:{port}/api",
        "host": host,
        "port": port,
        "pid": os.getpid(),
        # Answers GET /api/instance/proof; clients check it before sending the token.
        "proof": 1,
        "dataDir": str(data_dir),
        "tokenFile": str(token_file) if token_file else None,
        # Only the folder's path, so `Raticode doctor` can check the same files.
        "secretsDir": str(secrets_dir) if secrets_dir else None,
        "startedAt": datetime.now(UTC).isoformat(),
    }
    path = endpoint_path(data_dir)
    write_private_file(path, json.dumps(record, indent=2) + "\n")
    return path


def remove_if_unchanged(path: Path, expected: str) -> None:
    """Remove a file this process wrote, unless another backend has replaced it."""
    with contextlib.suppress(OSError):
        if path.read_text(encoding="utf-8") == expected:
            path.unlink()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentToken:
    name: str
    created_at: str
    expires_at: str | None
    # Whether the token may read brains with Allow cloud processing off. Off unless
    # the owner passed `--private-brains` when creating it.
    private_brains: bool = False

    def expired(self, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        return datetime.fromisoformat(self.expires_at) <= (now or datetime.now(UTC))

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "scope": "agent",
            "createdAt": self.created_at,
            "expiresAt": self.expires_at,
            "expired": self.expired(),
            "privateBrains": self.private_brains,
        }


@dataclass(frozen=True)
class AgentGrant:
    """What a live agent token carries into a request."""

    name: str
    private_brains: bool = False


class AgentTokenStore:
    """Hashed agent tokens in ``<data>/headless/agent-tokens.json`` (0600)."""

    def __init__(self, data_dir: Path) -> None:
        self.path = agent_tokens_path(data_dir)
        self._lock = threading.Lock()
        self._cache: tuple[tuple[int, ...] | None, dict[str, dict[str, Any]]] = (None, {})

    @contextlib.contextmanager
    def _changing(self) -> Iterator[None]:
        """Hold the store for a read-change-write, across processes as well as threads.

        Without it, a `tokens revoke` racing a `tokens create` in another process could
        write back the revoked token, which then kept working.
        """
        with self._lock:
            ensure_private_dir(self.path.parent)
            lock_path = self.path.with_name(f".{self.path.name}.lock")
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(lock_path, flags, 0o600)
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                try:
                    # Read what's on disk now, never a copy cached before the lock.
                    self._cache = (None, {})
                    yield
                finally:
                    if sys.platform == "win32":
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(descriptor)

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            info = self.path.stat()
        except FileNotFoundError:
            return {}
        # Every write replaces the file, so a new inode or change time marks it even
        # when a same-size rewrite lands within the file system's timestamp tick.
        signature = (info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size)
        if self._cache[0] == signature:
            return self._cache[1]
        check_private_file(self.path, secret=True)
        document = json.loads(self.path.read_text(encoding="utf-8"))
        tokens = document.get("tokens") if isinstance(document, dict) else None
        if not isinstance(tokens, list):
            raise HeadlessError(f"{self.path} is malformed")
        entries = {
            str(item["name"]): item
            for item in tokens
            if isinstance(item, dict) and isinstance(item.get("sha256"), str) and item.get("name")
        }
        self._cache = (signature, entries)
        return entries

    def _write(self, entries: dict[str, dict[str, Any]]) -> None:
        document = {"version": 1, "tokens": sorted(entries.values(), key=lambda item: item["name"])}
        write_private_file(self.path, json.dumps(document, indent=2) + "\n")
        self._cache = (None, {})

    def list(self) -> list[AgentToken]:
        with self._lock:
            entries = self._read()
        return [
            AgentToken(
                name,
                str(item.get("created_at", "")),
                item.get("expires_at"),
                private_brains=item.get("private_brains") is True,
            )
            for name, item in sorted(entries.items())
        ]

    def create(
        self, name: str, *, expires_days: int | None = None, private_brains: bool = False
    ) -> str:
        if not _TOKEN_NAME.fullmatch(name):
            raise HeadlessError(
                "Token names use lowercase letters, digits, '.', '_' or '-' (64 at most)"
            )
        if expires_days is not None and expires_days < 1:
            raise HeadlessError("--expires-days must be 1 or more")
        token = AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        with self._changing():
            entries = dict(self._read())
            if name in entries:
                raise HeadlessError(f"An agent token named {name!r} exists; revoke it first")
            entries[name] = {
                "name": name,
                "sha256": _hash(token),
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=expires_days)).isoformat()
                if expires_days
                else None,
            }
            if private_brains:
                # Stored beside the hash, so only the owner's `tokens create` sets it.
                entries[name]["private_brains"] = True
            self._write(entries)
        return token

    def revoke(self, name: str) -> bool:
        with self._changing():
            entries = dict(self._read())
            if entries.pop(name, None) is None:
                return False
            self._write(entries)
        return True

    def match(self, supplied: str) -> str | None:
        """The name of the live agent token [supplied] matches, if any."""
        grant = self.grant(supplied)
        return grant.name if grant else None

    def grant(self, supplied: str) -> AgentGrant | None:
        """The live agent token [supplied] matches, with what it may read, if any."""
        if not supplied.startswith(AGENT_TOKEN_PREFIX):
            return None
        digest = _hash(supplied)
        try:
            with self._lock:
                entries = self._read()
        except (HeadlessError, OSError, ValueError):
            # An unreadable or unsafe store grants nothing.
            return None
        now = datetime.now(UTC)
        for name, item in entries.items():
            if hmac.compare_digest(str(item["sha256"]), digest):
                expires = item.get("expires_at")
                if expires and datetime.fromisoformat(str(expires)) <= now:
                    return None
                return AgentGrant(name, private_brains=item.get("private_brains") is True)
        return None


# Path prefixes an agent token may never use, not even to read.
_AGENT_DENIED_PREFIXES: tuple[str, ...] = (
    "/api/session",  # returns the owner token
    "/api/desktop/",  # path grants and trusted roots
    "/api/devices",  # pairing and paired-device control
    "/api/provider/auth",  # provider sign-in
    # Accounts, their emails and plans, and a phone sign-in's one-time code and link,
    # which an agent could hand to someone else to sign the account in to theirs.
    "/api/provider/accounts",
    "/api/provider/claude-accounts",  # the first version's route to the same list
    "/api/usage/credentials",
    "/api/rem/context",  # Rem's projects, permissions and resources
    "/api/rem-resources",  # MCP server URLs and arguments, which can carry keys
    "/api/rem/reset",
    "/api/rem/memory-mode",
    "/api/rem/kanban/settings",  # self-work spends provider quota unattended
    "/api/rem/kanban/participant",
)

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_SEGMENT = r"[^/]+"

# The only writes an agent token may make; everything else that changes state is
# refused. An allow-list, so a route added later stays closed until someone decides an
# agent may use it. Most of these are POSTs that only read or check something. Each
# entry must match a route (tests/unit/test_cli_agent_safety.py), so none waits for one.
_AGENT_ALLOWED_WRITES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (method, re.compile(pattern))
    for method, pattern in (
        # One ticket's comments and status history; a read sent with its address.
        ("POST", r"/api/rem/kanban/timeline"),
        # Check a workflow draft or document without saving it.
        ("POST", rf"/api/workflows/{_SEGMENT}/document/analyze"),
        ("POST", r"/api/rattish/workflows/import/preview"),
        ("POST", r"/api/rattish/triggers/preview"),
        # Organizations act on the caller's authority: an agent token acts as Rem, so
        # starting a company, granting full access or lifting holds records a consent
        # request, and deciding one is the user's (gofer.ui.organizations).
        ("POST", r"/api/organizations"),
        # Second Brain reads; the handler refuses the other actions
        # (AGENT_SECOND_BRAIN_ACTIONS).
        ("POST", r"/api/second-brains"),
        # Social posts: an agent token acts as Rem, which may propose a post and revise
        # its own proposals before the user decides. Approving, publishing, accounts and
        # settings are the user's (gofer.social.api).
        ("POST", r"/api/social/posts"),
        ("POST", rf"/api/social/posts/{_SEGMENT}/edit"),
    )
)

# Second Brain actions an agent token may send to POST /api/second-brains: reads only.
AGENT_SECOND_BRAIN_ACTIONS = frozenset(
    {
        "snapshot",
        "search",
        "passages",
        "original",
        "related",
        "projects",
        "connections",
        "topics",
        "report_sources",
        "job_sources",
    }
)


def agent_request_refusal(method: str, path: str) -> str | None:
    """Why an agent token can't make this request, or None when it may.

    Agent tokens may read most state and make only the writes in
    ``_AGENT_ALLOWED_WRITES``. They can't edit, run, queue or delete workflows, change
    triggers, start or control swarms, change Second Brain libraries, talk to Rem or
    change tickets, settings, providers, retention or usage polling.
    """
    method = method.upper()
    write = method not in _READ_METHODS
    for prefix in _AGENT_DENIED_PREFIXES:
        if path == prefix.rstrip("/") or path.startswith(prefix):
            return "Agent tokens can't use this part of the API. Use the owner's session."
    if not write:
        return None
    if any(
        allowed == method and pattern.fullmatch(path) for allowed, pattern in _AGENT_ALLOWED_WRITES
    ):
        return None
    if path.startswith("/api/rem/"):
        return (
            "Agent tokens can't send Rem messages, change tickets, or control Rem's tasks "
            "and memories, because Rem treats those as the owner's requests."
        )
    if path == "/api/chat" or path.startswith("/api/chat/"):
        return "Agent tokens can't start or steer chat threads."
    if path.endswith(("/approve", "/reject")):
        return "Only the owner can approve or reject a workflow approval."
    if path == "/api/swarms" or path.startswith("/api/swarms/"):
        return "Agent tokens can't create, start or control swarms."
    if path.startswith(("/api/workflows", "/api/rattish/", "/api/queue/")):
        return (
            "Agent tokens can read workflows, runs and triggers but can't edit, run, queue "
            "or delete workflows or change triggers."
        )
    return "Agent tokens can't change this. Use the owner's session."


def load_rem_settings(path: Path) -> dict[str, Any]:
    """Read Rem's settings file: the same fields the desktop window sends.

    The file grants Rem access to the folders it lists, so it must belong to this
    account and must not be writable by others.
    """
    check_private_file(path, secret=False)
    if path.stat().st_size > MAX_REM_SETTINGS_BYTES:
        raise HeadlessError(f"{path} is larger than {MAX_REM_SETTINGS_BYTES} bytes")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HeadlessError(f"{path} isn't valid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise HeadlessError(f"{path} must hold a JSON object")
    projects = config.get("projects", [])
    if not isinstance(projects, list):
        raise HeadlessError("projects must be a list of folders")
    normalized = []
    for item in projects:
        entry = {"root": item} if isinstance(item, str) else item
        if not isinstance(entry, dict) or not isinstance(entry.get("root"), str):
            raise HeadlessError("Each project is a folder path or an object with a root")
        root = Path(entry["root"]).expanduser()
        if not root.is_absolute() or not root.is_dir():
            raise HeadlessError(
                f"Project folder {entry['root']!r} must be an existing absolute path"
            )
        resolved = root.resolve()
        normalized.append(
            {
                **{key: value for key, value in entry.items() if key != "grantId"},
                "root": str(resolved),
                "name": str(entry.get("name") or resolved.name),
            }
        )
    config["projects"] = normalized
    brain = config.get("brain")
    if brain is not None:
        if not isinstance(brain, dict):
            raise HeadlessError("brain must be an object")
        brain = {key: value for key, value in brain.items() if key != "grantId"}
        if brain.get("enabled"):
            root = Path(str(brain.get("root", ""))).expanduser()
            if not root.is_absolute() or not root.is_dir():
                raise HeadlessError("brain.root must be an existing absolute folder")
            brain["root"] = str(root.resolve())
        brain.pop("workspaceGrants", None)
        config["brain"] = brain
    return config
