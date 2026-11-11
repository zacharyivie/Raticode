"""Headless backend support: private token and endpoint files, agent tokens, Rem settings.

``Raticode serve --headless`` runs the backend with no desktop window. Clients on the
same computer find it through two files in ``<data>/headless/``, both readable only by
this account (mode 0600 in a 0700 folder):

- ``endpoint.json``: the URL, process ID and token file of the running backend.
- ``owner-token``: the full-access API token. It is never passed on a command line.

Agent tokens are for scripts and agents that should not act as the owner. Only a hash
of each token is stored; the token itself is written once, to a file the owner names.
An agent token may read most state but makes only a short list of writes
(:func:`agent_request_refusal`): it cannot edit, run or delete workflows, change
triggers, start or control swarms, approve organization consent requests or Kanban
proposals, change Rem's settings, pair devices, sign in to providers, read the owner
token, or manage path grants.

The backend never hands either kind of token to Rem workers, and the client helpers
here refuse to read the owner token inside a Rem worker session. Files readable by
this account are still readable by any process running as it, so keep agent tokens
out of folders Rem's workers use.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HEADLESS_DIRNAME = "headless"
OWNER_TOKEN_FILENAME = "owner-token"
ENDPOINT_FILENAME = "endpoint.json"
AGENT_TOKENS_FILENAME = "agent-tokens.json"
REM_SETTINGS_FILENAME = "rem-settings.json"
# Set in the environment of everything the backend starts (providers, Rem workers,
# workflow commands). The client helpers refuse the owner token when it is present.
AGENT_SESSION_ENV = "RATICODE_AGENT_SESSION"
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


def write_private_file(path: Path, text: str) -> None:
    """Atomically replace [path] with [text], readable only by this account (0600)."""
    ensure_private_dir(path.parent)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


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
    if not path.is_file() or path.is_symlink():
        raise HeadlessError(f"{path} must be a regular file")
    if info.st_uid != os.getuid():
        raise HeadlessError(f"{path} belongs to another account")
    mask = 0o077 if secret else 0o022
    if info.st_mode & mask:
        needed = "600" if secret else "go-w"
        raise HeadlessError(f"{path} is open to other accounts; run chmod {needed} on it")


def read_token_file(path: Path) -> str:
    """Read an owner-only token file, such as a mounted container secret."""
    check_private_file(path, secret=True)
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 24 or any(ch.isspace() for ch in token):
        raise HeadlessError(f"{path} doesn't hold a usable token (24 or more characters)")
    return token


def new_owner_token() -> str:
    return secrets.token_urlsafe(32)


def in_agent_session(environ: dict[str, str] | None = None) -> bool:
    return bool((os.environ if environ is None else environ).get(AGENT_SESSION_ENV))


def read_endpoint(data_dir: Path) -> dict[str, Any]:
    """The running headless backend's endpoint record, for clients on this computer."""
    path = endpoint_path(data_dir)
    check_private_file(path, secret=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
        raise HeadlessError(f"{path} isn't a Raticode endpoint record")
    return payload


def read_owner_token(data_dir: Path, environ: dict[str, str] | None = None) -> str:
    """The owner token for a client the user runs; refused inside Rem worker sessions."""
    if in_agent_session(environ):
        raise HeadlessError(
            "This command is running inside a Raticode agent session, which can't use the "
            "owner token. Use an agent token file instead."
        )
    return read_token_file(owner_token_path(data_dir))


def publish_endpoint(
    data_dir: Path,
    *,
    host: str,
    port: int,
    token_file: Path | None,
) -> Path:
    url_host = f"[{host}]" if ":" in host else host
    record = {
        "url": f"http://{url_host}:{port}/api",
        "host": host,
        "port": port,
        "pid": os.getpid(),
        "dataDir": str(data_dir),
        "tokenFile": str(token_file) if token_file else None,
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
        }


class AgentTokenStore:
    """Hashed agent tokens in ``<data>/headless/agent-tokens.json`` (0600)."""

    def __init__(self, data_dir: Path) -> None:
        self.path = agent_tokens_path(data_dir)
        self._lock = threading.Lock()
        self._cache: tuple[tuple[int, int] | None, dict[str, dict[str, Any]]] = (None, {})

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            info = self.path.stat()
        except FileNotFoundError:
            return {}
        signature = (info.st_mtime_ns, info.st_size)
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
            AgentToken(name, str(item.get("created_at", "")), item.get("expires_at"))
            for name, item in sorted(entries.items())
        ]

    def create(self, name: str, *, expires_days: int | None = None) -> str:
        if not _TOKEN_NAME.fullmatch(name):
            raise HeadlessError(
                "Token names use lowercase letters, digits, '.', '_' or '-' (64 at most)"
            )
        if expires_days is not None and expires_days < 1:
            raise HeadlessError("--expires-days must be 1 or more")
        token = AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        with self._lock:
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
            self._write(entries)
        return token

    def revoke(self, name: str) -> bool:
        with self._lock:
            entries = dict(self._read())
            if entries.pop(name, None) is None:
                return False
            self._write(entries)
        return True

    def match(self, supplied: str) -> str | None:
        """The name of the live agent token [supplied] matches, if any."""
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
                return name
        return None


# Path prefixes an agent token may never use, not even to read.
_AGENT_DENIED_PREFIXES: tuple[str, ...] = (
    "/api/session",  # returns the owner token
    "/api/desktop/",  # path grants and trusted roots
    "/api/devices",  # pairing and paired-device control
    "/api/provider/auth",  # provider sign-in
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
# agent may use it. Most of these are POSTs that only read or check something.
_AGENT_ALLOWED_WRITES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (method, re.compile(pattern))
    for method, pattern in (
        # One ticket's comments and status history; a read sent with its address.
        ("POST", r"/api/rem/kanban/timeline"),
        # Check a workflow draft or document without saving it.
        ("POST", rf"/api/workflows/{_SEGMENT}/validate"),
        ("POST", rf"/api/workflows/{_SEGMENT}/document/analyze"),
        ("POST", r"/api/workflows/import/preview"),
        ("POST", r"/api/rattish/workflows/import/preview"),
        ("POST", r"/api/rattish/triggers/preview"),
        # Organizations act on the caller's authority: an agent token acts as Rem, so
        # starting a company, granting full access or lifting holds records a consent
        # request, and deciding one is the user's (gofer.ui.organizations).
        ("POST", r"/api/organizations"),
        # Second Brain reads; the handler refuses the other actions
        # (AGENT_SECOND_BRAIN_ACTIONS).
        ("POST", r"/api/second-brains"),
    )
)

# Second Brain actions an agent token may send to POST /api/second-brains: reads only.
AGENT_SECOND_BRAIN_ACTIONS = frozenset(
    {
        "snapshot",
        "search",
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
