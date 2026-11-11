"""Shared client core for CLI commands that talk to a local Raticode backend.

Resource commands (``Raticode rem …``, ``Raticode kanban …``, ``Raticode org …`` and
so on) build on this module instead of opening the backend's stores directly, so the
backend stays the only writer. The interface below is stable; add to it rather than
changing existing names or signatures.

Script contract
    * One JSON envelope per command on stdout:
      ``{"ok", "command", "data", "error": {"code", "message", "http"}, "warnings"}``,
      plus ``request_id`` when the command sent one. Human text goes to stderr.
    * ``--follow`` writes NDJSON: ``{"kind": "event", "data": …}`` per event, then the
      envelope with ``"kind": "result"`` as the last line.
    * Exit codes are :class:`ExitCode` (0-8).

Backend discovery (local only, the token is never taken from argv)
    1. Endpoint: ``--endpoint`` / ``endpoint=``, else ``RATICODE_URL``, else
       ``RATICODE_SOCKET``, else ``<data>/headless/endpoint.json``, else
       ``http://127.0.0.1:8765``.
    2. Token: ``--token-file`` / ``token_file=``, else ``RATICODE_TOKEN``, else
       ``RATICODE_TOKEN_FILE``, else the endpoint record's ``tokenFile``, else
       ``<data>/headless/owner-token``. The last two are skipped inside a Rem worker
       session (:func:`in_agent_session`), and there only a limited agent token
       (``rtk_agent_…``) is accepted from any source: an owner token found in the
       environment or a ``--token-file`` is refused with exit 5.
    3. Data folder: ``--data-dir`` / ``data_dir=``, else ``RATICODE_DATA_DIR``, else
       ``GOFER_DATA_DIR``, else :func:`gofer.utils.paths.get_data_dir`.

    Token files must belong to the current user (or root) and be unreadable by group
    and others; the endpoint file must not be writable by others, since it decides
    where the token is sent. Non-loopback endpoints are refused.

The headless backend (``Raticode serve --headless``) publishes
``<data>/headless/endpoint.json`` (``{"url": "http://127.0.0.1:PORT/api", "host",
"port", "pid", "tokenFile", …}``) and ``<data>/headless/owner-token``.
:func:`write_api_files` writes the same layout, for tests and other publishers.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import socket
import stat
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import IO, Any, TypeVar
from urllib.parse import parse_qsl, urlencode, urlsplit

from gofer.utils.paths import get_data_dir

__all__ = [
    "API_DIR_NAME",
    "DEFAULT_URL",
    "ENDPOINT_FILE_NAME",
    "AGENT_SESSION_ENVS",
    "AGENT_TOKEN_PREFIX",
    "ENV_AGENT_SESSION",
    "ENV_DATA_DIRS",
    "ENV_NON_INTERACTIVE",
    "ENV_SOCKET",
    "ENV_TOKEN",
    "ENV_TOKEN_FILE",
    "ENV_URL",
    "TOKEN_FILE_NAME",
    "ApiClient",
    "ApiError",
    "ApiFiles",
    "ApiResponse",
    "CliError",
    "CommandResult",
    "Endpoint",
    "ExitCode",
    "Output",
    "OutputFormat",
    "WaitResult",
    "api_files",
    "api_path",
    "complete_from_api",
    "dedupes_request_id",
    "confirm",
    "discover_endpoint",
    "envelope",
    "exit_code_for_status",
    "field_classifier",
    "in_agent_session",
    "is_non_interactive",
    "new_request_id",
    "parse_duration",
    "read_secret_file",
    "remove_api_files",
    "resolve_data_dir",
    "revision_field",
    "run_command",
    "safe_to_resend",
    "wait_until",
    "write_api_files",
]

ENV_URL = "RATICODE_URL"
ENV_SOCKET = "RATICODE_SOCKET"
ENV_TOKEN = "RATICODE_TOKEN"
ENV_TOKEN_FILE = "RATICODE_TOKEN_FILE"
ENV_DATA_DIRS = ("RATICODE_DATA_DIR", "GOFER_DATA_DIR")
ENV_NON_INTERACTIVE = "RATICODE_NON_INTERACTIVE"
# Set for everything the backend starts (Rem workers, providers). Inside one, the owner
# token is never discovered from the data folder; pass an agent token explicitly.
ENV_AGENT_SESSION = "RATICODE_AGENT_SESSION"
# Any of these marks a provider turn Raticode started (``gofer.utils.process`` and
# ``gofer.core.command_slots``).
AGENT_SESSION_ENVS = (
    ENV_AGENT_SESSION,
    "RATICODE_COMMAND_ACTIVITY",
    "RATICODE_COMMAND_SLOT_LABEL",
    "RATICODE_AGENT_SLICE",
)
# Limited agent tokens start with this (gofer.ui.headless.AGENT_TOKEN_PREFIX).
AGENT_TOKEN_PREFIX = "rtk_agent_"

# The layout `Raticode serve --headless` publishes (gofer.ui.headless).
API_DIR_NAME = "headless"
ENDPOINT_FILE_NAME = "endpoint.json"
TOKEN_FILE_NAME = "owner-token"
DEFAULT_URL = "http://127.0.0.1:8765"

MAX_TOKEN_BYTES = 4096
MAX_ENDPOINT_FILE_BYTES = 65536
DEFAULT_REQUEST_TIMEOUT = 30.0
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

T = TypeVar("T")


# --- Exit codes and errors -------------------------------------------------------


class ExitCode(IntEnum):
    """Process exit codes shared by every backend-client command."""

    OK = 0
    FAILED = 1  # The operation ran and failed, or the backend returned a 5xx.
    USAGE = 2  # Bad arguments or invalid input (HTTP 400/405/413/422).
    NOT_FOUND = 3  # HTTP 404.
    CONFLICT = 4  # Conflict or stale revision (HTTP 409/412).
    AUTH = 5  # Missing or rejected token, forbidden (HTTP 401/403).
    UNREACHABLE = 6  # No backend answered at the discovered endpoint.
    NEEDS_USER = 7  # Waiting on the user: consent, approval or input pending.
    UNKNOWN = 8  # Timeout, or the request may or may not have taken effect.


class CliError(Exception):
    """A failure with a stable error code and exit code.

    ``data`` is included as the envelope's ``data`` (for example the last state a
    ``wait`` saw before it timed out).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "failed",
        exit_code: ExitCode = ExitCode.FAILED,
        http_status: int | None = None,
        data: Any = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.message = message
        self.code = code
        self.exit_code = exit_code
        self.http_status = http_status
        self.data = data

    def to_error(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "http": self.http_status}


class ApiError(CliError):
    """The backend answered with an HTTP error status."""

    def __init__(self, status: int, message: str, *, code: str | None = None, data: Any = None):
        exit_code, default_code = exit_code_for_status(status)
        super().__init__(
            message,
            code=code or default_code,
            exit_code=exit_code,
            http_status=status,
            data=data,
        )


def exit_code_for_status(status: int) -> tuple[ExitCode, str]:
    """Map an HTTP status to an exit code and a stable error code."""
    if status < 400:
        return ExitCode.OK, "ok"
    if status == 401:
        return ExitCode.AUTH, "unauthorized"
    if status == 403:
        return ExitCode.AUTH, "forbidden"
    if status == 404:
        return ExitCode.NOT_FOUND, "not_found"
    if status in {409, 412}:
        return ExitCode.CONFLICT, "conflict"
    if status in {408, 504}:
        return ExitCode.UNKNOWN, "timeout"
    if status == 503:
        return ExitCode.FAILED, "busy"
    if 400 <= status < 500:
        return ExitCode.USAGE, "invalid_request"
    return ExitCode.FAILED, "backend_error"


# --- Discovery ---------------------------------------------------------------------


@dataclass(frozen=True)
class ApiFiles:
    directory: Path
    endpoint_file: Path
    token_file: Path


def resolve_data_dir(
    data_dir: Path | str | None = None, env: Mapping[str, str] | None = None
) -> Path:
    """The Raticode data folder the client should read discovery files from."""
    environ = os.environ if env is None else env
    if data_dir:
        return Path(data_dir).expanduser()
    for name in ENV_DATA_DIRS:
        value = environ.get(name, "").strip()
        if value:
            return Path(value).expanduser()
    return get_data_dir()


def api_files(data_dir: Path | str | None = None) -> ApiFiles:
    directory = resolve_data_dir(data_dir) / API_DIR_NAME
    return ApiFiles(directory, directory / ENDPOINT_FILE_NAME, directory / TOKEN_FILE_NAME)


@dataclass(frozen=True)
class Endpoint:
    """Where the backend listens and how to authenticate.

    ``url`` is ``http://host:port`` (loopback only). ``socket_path`` is set for a Unix
    socket, in which case ``url`` is only used for the Host header. The token never
    appears in ``repr``.
    """

    url: str
    token: str = field(default="", repr=False)
    socket_path: str | None = None
    source: str = "default"
    token_source: str = "none"
    pid: int | None = None

    def describe(self) -> dict[str, Any]:
        """Safe-to-print description; never includes the token."""
        return {
            "url": self.url,
            "socket": self.socket_path,
            "source": self.source,
            "token": self.token_source if self.token else "none",
            "pid": self.pid,
        }


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _normalize_url(url: str, source: str) -> str:
    raw = url.strip().rstrip("/")
    if "://" not in raw:
        raw = "http://" + raw
    parts = urlsplit(raw)
    if parts.scheme != "http":
        raise CliError(
            f"Unsupported endpoint scheme in {source}: use http:// on loopback or a Unix socket.",
            code="remote_refused",
            exit_code=ExitCode.USAGE,
        )
    host = parts.hostname or ""
    if parts.username or parts.password or (parts.path not in {"", "/", "/api"}) or parts.query:
        raise CliError(
            f"Endpoint in {source} must be just http://host:port.",
            code="invalid_endpoint",
            exit_code=ExitCode.USAGE,
        )
    if not _is_loopback_host(host):
        raise CliError(
            f"Refusing non-loopback endpoint {host!r} from {source}. The CLI only talks to a "
            "backend on this computer; reach a remote one through an SSH tunnel.",
            code="remote_refused",
            exit_code=ExitCode.USAGE,
        )
    try:
        port = parts.port or 80
    except ValueError as exc:
        raise CliError(
            f"Invalid port in {source}.", code="invalid_endpoint", exit_code=ExitCode.USAGE
        ) from exc
    netloc = f"[{host}]" if ":" in host else host
    return f"http://{netloc}:{port}"


def _read_private_file(path: Path, *, limit: int, what: str, secret: bool = True) -> str:
    """Read a small file that only this user (or root) can change.

    A ``secret`` file must also be unreadable by group and others, unless root owns
    it (a secret mounted into a container). Symlinks are followed, and the file they
    point to is what gets checked.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise CliError(
            f"Could not read the {what} {path}: {exc.strerror or exc}",
            code="token_unreadable",
            exit_code=ExitCode.AUTH,
        ) from exc
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise CliError(
                f"The {what} {path} is not a regular file.",
                code="token_unreadable",
                exit_code=ExitCode.AUTH,
            )
        if sys.platform != "win32":
            if info.st_uid not in {os.getuid(), 0}:
                raise CliError(
                    f"The {what} {path} belongs to another user; refusing to use it.",
                    code="insecure_token_file",
                    exit_code=ExitCode.AUTH,
                )
            if info.st_mode & 0o022:
                raise CliError(
                    f"Other users can change the {what} {path}. Run: chmod 600 {path}",
                    code="insecure_token_file",
                    exit_code=ExitCode.AUTH,
                )
            if secret and info.st_uid != 0 and info.st_mode & 0o044:
                raise CliError(
                    f"The {what} {path} is readable by other users. Run: chmod 600 {path}",
                    code="insecure_token_file",
                    exit_code=ExitCode.AUTH,
                )
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise CliError(
            f"The {what} {path} is too large.", code="token_unreadable", exit_code=ExitCode.AUTH
        )
    return content.decode("utf-8", errors="strict").strip()


def read_secret_file(path: Path | str, *, what: str = "token file") -> str:
    """Read a token from a file only this user (or root) controls; see discovery rules."""
    target = Path(path).expanduser()
    try:
        return _read_private_file(target, limit=MAX_TOKEN_BYTES, what=what)
    except FileNotFoundError as exc:
        raise CliError(
            f"The {what} {target} does not exist.",
            code="token_missing",
            exit_code=ExitCode.AUTH,
        ) from exc


def _read_endpoint_file(path: Path) -> dict[str, Any] | None:
    try:
        text = _read_private_file(
            path, limit=MAX_ENDPOINT_FILE_BYTES, what="endpoint file", secret=False
        )
    except FileNotFoundError:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliError(
            f"The endpoint file {path} is not valid JSON.",
            code="invalid_endpoint",
            exit_code=ExitCode.USAGE,
        ) from exc
    return payload if isinstance(payload, dict) else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def in_agent_session(env: Mapping[str, str] | None = None) -> bool:
    """Whether this process runs inside a provider turn Raticode started for Rem or a worker."""
    environ = os.environ if env is None else env
    return any(environ.get(name, "").strip() for name in AGENT_SESSION_ENVS)


def discover_endpoint(
    *,
    endpoint: str | None = None,
    token_file: Path | str | None = None,
    data_dir: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> Endpoint:
    """Find the local backend and its token. See the module docstring for the order."""
    environ = os.environ if env is None else env
    files = api_files(resolve_data_dir(data_dir, environ))
    url = DEFAULT_URL
    socket_path: str | None = None
    source = "default"
    pid: int | None = None
    published_token: Path | None = None

    if endpoint:
        if endpoint.startswith("unix:"):
            socket_path = endpoint.removeprefix("unix:").removeprefix("//") or None
            source = "--endpoint"
        else:
            url, source = _normalize_url(endpoint, "--endpoint"), "--endpoint"
    elif environ.get(ENV_URL, "").strip():
        url, source = _normalize_url(environ[ENV_URL], ENV_URL), ENV_URL
    elif environ.get(ENV_SOCKET, "").strip():
        socket_path, source = environ[ENV_SOCKET].strip(), ENV_SOCKET
    else:
        published = _read_endpoint_file(files.endpoint_file)
        if published is not None:
            source = str(files.endpoint_file)
            if isinstance(published.get("socket"), str) and published["socket"]:
                socket_path = published["socket"]
            if isinstance(published.get("url"), str) and published["url"]:
                url = _normalize_url(published["url"], source)
            elif published.get("port"):
                host = str(published.get("host") or "127.0.0.1")
                url = _normalize_url(f"{host}:{published['port']}", source)
            raw_pid = published.get("pid")
            pid = raw_pid if isinstance(raw_pid, int) and not isinstance(raw_pid, bool) else None
            if isinstance(published.get("tokenFile"), str) and published["tokenFile"]:
                published_token = Path(published["tokenFile"]).expanduser()

    token = ""
    token_source = "none"
    agent = in_agent_session(environ)
    if token_file:
        try:
            token = _read_private_file(Path(token_file), limit=MAX_TOKEN_BYTES, what="token file")
        except FileNotFoundError as exc:
            raise CliError(
                f"Token file {token_file} does not exist.",
                code="token_missing",
                exit_code=ExitCode.AUTH,
            ) from exc
        token_source = "--token-file"
    elif environ.get(ENV_TOKEN, "").strip():
        token, token_source = environ[ENV_TOKEN].strip(), ENV_TOKEN
    elif environ.get(ENV_TOKEN_FILE, "").strip():
        path = Path(environ[ENV_TOKEN_FILE].strip()).expanduser()
        try:
            token = _read_private_file(path, limit=MAX_TOKEN_BYTES, what="token file")
        except FileNotFoundError as exc:
            raise CliError(
                f"{ENV_TOKEN_FILE} points to {path}, which does not exist.",
                code="token_missing",
                exit_code=ExitCode.AUTH,
            ) from exc
        token_source = ENV_TOKEN_FILE
    elif not agent:
        for candidate in (published_token, files.token_file):
            if candidate is None:
                continue
            try:
                token = _read_private_file(candidate, limit=MAX_TOKEN_BYTES, what="token file")
            except FileNotFoundError:
                continue
            token_source = str(candidate)
            break
    if any(ch in token for ch in "\r\n\0"):
        raise CliError(
            "The API token contains invalid characters.",
            code="token_unreadable",
            exit_code=ExitCode.AUTH,
        )
    if agent and token and not token.startswith(AGENT_TOKEN_PREFIX):
        # A worker inherits its parent's environment and can read the user's files, so
        # an owner token reaching it from anywhere would act with the user's authority.
        raise CliError(
            f"This command is running inside a Raticode agent turn, which can't use the "
            f"owner's API token (from {token_source}). Pass an agent token "
            "(Raticode tokens create) with --token-file, or run it from your own terminal.",
            code="owner_token_in_agent_session",
            exit_code=ExitCode.AUTH,
        )
    return Endpoint(
        url=url,
        token=token,
        socket_path=socket_path,
        source=source,
        token_source=token_source,
        pid=pid,
    )


def _write_private(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        if sys.platform != "win32":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_api_files(
    data_dir: Path | str,
    *,
    url: str,
    token: str,
    socket_path: str | None = None,
    pid: int | None = None,
) -> ApiFiles:
    """Publish the backend's endpoint and token for local clients (owner-only files)."""
    files = api_files(data_dir)
    files.directory.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(files.directory, 0o700)
    _write_private(files.token_file, token + "\n")
    base = _normalize_url(url, "write_api_files")
    parts = urlsplit(base)
    payload: dict[str, Any] = {
        "url": base + "/api",
        "host": parts.hostname,
        "port": parts.port,
        "pid": os.getpid() if pid is None else pid,
        "dataDir": str(resolve_data_dir(data_dir)),
        "tokenFile": str(files.token_file),
    }
    if socket_path:
        payload["socket"] = socket_path
    _write_private(files.endpoint_file, json.dumps(payload, indent=2) + "\n")
    return files


def remove_api_files(data_dir: Path | str, *, pid: int | None = None) -> None:
    """Remove published files, only if they still belong to ``pid`` (default: this one)."""
    files = api_files(data_dir)
    owner = os.getpid() if pid is None else pid
    try:
        published = json.loads(files.endpoint_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        published = None
    if isinstance(published, dict) and published.get("pid") not in {owner, None}:
        return
    files.endpoint_file.unlink(missing_ok=True)
    files.token_file.unlink(missing_ok=True)


# --- HTTP transport ----------------------------------------------------------------


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float | None) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        if self.timeout is not None:
            sock.settimeout(self.timeout)
        try:
            sock.connect(self._socket_path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


def new_request_id() -> str:
    return str(uuid.uuid4())


def api_path(path: str) -> str:
    """Normalize ``rem/kanban``, ``/rem/kanban`` or ``/api/rem/kanban`` to ``/api/…``."""
    raw = path.strip()
    if not raw or "://" in raw or raw.startswith("//") or any(c in raw for c in "\r\n\t #"):
        raise CliError(
            f"Invalid API path {path!r}. Use a path like /rem/kanban.",
            code="invalid_path",
            exit_code=ExitCode.USAGE,
        )
    if not raw.startswith("/"):
        raw = "/" + raw
    if raw != "/api" and not raw.startswith("/api/") and not raw.startswith("/api?"):
        raw = "/api" + raw
    route = raw.split("?", 1)[0]
    if any(segment in {".", ".."} for segment in route.split("/")):
        raise CliError(f"Invalid API path {path!r}.", code="invalid_path", exit_code=ExitCode.USAGE)
    return raw


def revision_field(path: str) -> str:
    """The body field an endpoint reads its expected revision from.

    Rem's Kanban and self-work settings use ``expected_revision``; organizations
    (inside ``params``) and Rattish documents use ``expectedRevision``.
    """
    route = api_path(path).split("?", 1)[0]
    return "expected_revision" if route.startswith("/api/rem/") else "expectedRevision"


def _with_query(path: str, query: Mapping[str, Any] | None) -> str:
    if not query:
        return path
    route, _, existing = path.partition("?")
    pairs = parse_qsl(existing, keep_blank_values=True)
    for key, value in query.items():
        if value is None:
            continue
        values = value if isinstance(value, list | tuple) else [value]
        for item in values:
            if isinstance(item, bool):
                item = "true" if item else "false"
            pairs.append((str(key), str(item)))
    return f"{route}?{urlencode(pairs)}" if pairs else route


# Endpoints that store a receipt per request_id and answer a repeat with the first
# result instead of doing the work again (RemHomeStore.command and the Kanban's
# create and comment dedupe). Every other endpoint ignores request_id.
_DEDUPING_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"/api/rem/messages")),
    ("POST", re.compile(r"/api/rem/tasks")),
    ("PATCH", re.compile(r"/api/rem/tasks/[^/]+")),
    ("DELETE", re.compile(r"/api/rem/tasks/[^/]+")),
    ("POST", re.compile(r"/api/rem/tasks/[^/]+/(run|pause)")),
    ("POST", re.compile(r"/api/rem/runs/[^/]+/(cancel|acknowledge|retry_review)")),
    ("POST", re.compile(r"/api/rem/threads/[^/]+/messages")),
)
_DEDUPING_KANBAN_ACTIONS = frozenset({"create", "comment"})


def dedupes_request_id(method: str, path: str, body: Any = None) -> bool:
    """Whether the backend deduplicates this request on its ``request_id``.

    True for Rem's messages, tasks (create, edit, run, pause, delete), run controls
    and thread messages, and for the Kanban's ticket ``create`` and ``comment``. Other
    endpoints ignore ``request_id``, so resending them could repeat the work.
    """
    verb = method.upper()
    route = api_path(path).split("?", 1)[0]
    if verb == "POST" and route == "/api/rem/kanban/ticket":
        return isinstance(body, dict) and body.get("action") in _DEDUPING_KANBAN_ACTIONS
    return any(verb == m and pattern.fullmatch(route) for m, pattern in _DEDUPING_ROUTES)


def safe_to_resend(method: str, path: str, body: Any = None, request_id: str | None = None) -> bool:
    """Whether a request that already reached the backend may be sent again."""
    if method.upper() in IDEMPOTENT_METHODS:
        return True
    return bool(request_id) and dedupes_request_id(method, path, body)


@dataclass
class ApiResponse:
    status: int
    headers: dict[str, str]
    data: Any
    request_id: str | None = None

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _error_message(status: int, data: Any) -> tuple[str, str | None]:
    if isinstance(data, dict):
        message = data.get("error") or data.get("message") or data.get("detail")
        code = data.get("code")
        if isinstance(message, dict):
            code = code or message.get("code")
            message = message.get("message")
        return (
            str(message or f"Backend returned HTTP {status}"),
            str(code) if isinstance(code, str) and code else None,
        )
    if isinstance(data, str) and data.strip():
        return data.strip()[:500], None
    return f"Backend returned HTTP {status}", None


def _decode_body(content_type: str, raw: bytes) -> Any:
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    if content_type == "application/x-ndjson":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if content_type == "application/json" or content_type.endswith("+json"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


class ApiClient:
    """A small HTTP client for the local backend.

    Every request opens its own connection, so a client can be shared between
    threads. A request that never reached the backend (connection refused) is retried.
    Once sent, only requests that are safe to repeat (:func:`safe_to_resend`: GET,
    HEAD, OPTIONS, or a request ID on an endpoint in :func:`dedupes_request_id`) are
    retried after a timeout, dropped connection or HTTP 503. Anything else raises
    ``outcome_unknown`` (exit 8) without being sent again.
    """

    def __init__(
        self,
        endpoint: Endpoint,
        *,
        timeout: float | None = DEFAULT_REQUEST_TIMEOUT,
        retries: int = 2,
        retry_delay: float = 0.5,
        user_agent: str | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.retries = max(0, retries)
        self.retry_delay = max(0.0, retry_delay)
        self.user_agent = user_agent or _user_agent()

    @classmethod
    def discover(
        cls,
        *,
        endpoint: str | None = None,
        token_file: Path | str | None = None,
        data_dir: Path | str | None = None,
        timeout: float | None = DEFAULT_REQUEST_TIMEOUT,
        retries: int = 2,
    ) -> ApiClient:
        return cls(
            discover_endpoint(endpoint=endpoint, token_file=token_file, data_dir=data_dir),
            timeout=timeout,
            retries=retries,
        )

    # Connection plumbing -------------------------------------------------------

    def _connection(self, timeout: float | None) -> http.client.HTTPConnection:
        if self.endpoint.socket_path:
            return _UnixHTTPConnection(self.endpoint.socket_path, timeout)
        parts = urlsplit(self.endpoint.url)
        return http.client.HTTPConnection(
            parts.hostname or "127.0.0.1", parts.port, timeout=timeout
        )

    def _headers(
        self,
        *,
        accept: str,
        has_body: bool,
        request_id: str | None,
        expected_revision: int | str | None,
        extra: Mapping[str, str] | None,
    ) -> dict[str, str]:
        headers = {"Accept": accept, "User-Agent": self.user_agent}
        if has_body:
            headers["Content-Type"] = "application/json"
        if self.endpoint.token:
            headers["Authorization"] = f"Bearer {self.endpoint.token}"
        if request_id:
            headers["X-Request-Id"] = request_id
        if expected_revision is not None:
            headers["If-Match"] = f'"{expected_revision}"'
        if extra:
            headers.update(extra)
        return headers

    def _prepare(
        self,
        method: str,
        path: str,
        body: Any,
        query: Mapping[str, Any] | None,
        request_id: str | None,
        expected_revision: int | str | None,
    ) -> tuple[str, str, bytes | None]:
        verb = method.upper()
        if verb not in {*METHODS, "HEAD", "OPTIONS"}:
            raise CliError(
                f"Unsupported HTTP method {method!r}.",
                code="invalid_method",
                exit_code=ExitCode.USAGE,
            )
        target_query = dict(query or {})
        payload = body
        if request_id:
            if isinstance(payload, dict):
                payload = {"request_id": request_id, **payload}
            elif payload is None and verb == "DELETE":
                target_query.setdefault("request_id", request_id)
        target_path = api_path(path)
        if expected_revision is not None and isinstance(payload, dict):
            name = revision_field(target_path)
            params = payload.get("params")
            if "action" in payload and isinstance(params, dict):
                payload = {**payload, "params": {name: expected_revision, **params}}
            else:
                payload = {name: expected_revision, **payload}
        target = _with_query(target_path, target_query)
        encoded = None if payload is None else json.dumps(payload).encode("utf-8")
        return verb, target, encoded

    def _open(
        self,
        verb: str,
        target: str,
        encoded: bytes | None,
        headers: dict[str, str],
        *,
        timeout: float | None,
        repeatable: bool,
    ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        attempt = 0
        while True:
            connection = self._connection(timeout)
            sent = False
            try:
                connection.connect()
                sent = True
                connection.request(verb, target, body=encoded, headers=headers)
                response = connection.getresponse()
            except (ConnectionRefusedError, FileNotFoundError, socket.gaierror) as exc:
                connection.close()
                if attempt < self.retries:
                    attempt += 1
                    time.sleep(self.retry_delay * attempt)
                    continue
                raise self._unreachable(exc) from exc
            except (TimeoutError, OSError, http.client.HTTPException) as exc:
                connection.close()
                if not sent:
                    if attempt < self.retries:
                        attempt += 1
                        time.sleep(self.retry_delay * attempt)
                        continue
                    raise self._unreachable(exc) from exc
                if repeatable and attempt < self.retries:
                    attempt += 1
                    time.sleep(self.retry_delay * attempt)
                    continue
                what = (
                    "The backend did not answer in time"
                    if isinstance(exc, TimeoutError)
                    else f"The connection closed before the backend answered ({exc})"
                )
                if repeatable:
                    advice = "Retrying with the same --request-id is safe."
                else:
                    advice = (
                        "It was not resent, because this endpoint doesn't deduplicate "
                        "request IDs; check whether it happened before trying again."
                    )
                raise CliError(
                    f"{what}. The request may have taken effect. {advice}",
                    code="timeout"
                    if repeatable and verb in IDEMPOTENT_METHODS
                    else "outcome_unknown",
                    exit_code=ExitCode.UNKNOWN,
                ) from exc
            if response.status == 503 and repeatable and attempt < self.retries:
                response.read()
                connection.close()
                attempt += 1
                time.sleep(self.retry_delay * attempt)
                continue
            return connection, response

    def _unreachable(self, exc: BaseException) -> CliError:
        where = self.endpoint.socket_path or self.endpoint.url
        hint = (
            "Start one with `Raticode serve --headless` (see Headless backend in the "
            "Raticode docs), or point RATICODE_URL at a running backend."
        )
        if self.endpoint.pid is not None and not _pid_alive(self.endpoint.pid):
            hint = (
                f"The backend recorded in {self.endpoint.source} (pid {self.endpoint.pid}) "
                "is no longer running."
            )
        return CliError(
            f"No Raticode backend answered at {where}: {exc}. {hint}",
            code="unreachable",
            exit_code=ExitCode.UNREACHABLE,
        )

    def _raise_for_status(self, status: int, data: Any) -> None:
        if status < 400:
            return
        message, code = _error_message(status, data)
        if status == 401:
            if self.endpoint.token:
                message += " The token was rejected; the backend may have restarted."
            else:
                message += f" No token found. Set {ENV_TOKEN_FILE} or pass --token-file" + (
                    " with an agent token; Rem worker sessions don't read the owner token."
                    if in_agent_session()
                    else ", or start a headless backend so it writes <data>/headless/owner-token."
                )
        raise ApiError(status, message, code=code, data=data)

    # Public API -----------------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        expected_revision: int | str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> ApiResponse:
        """Send one request and decode the reply; raise :class:`ApiError` on 4xx/5xx.

        ``request_id`` is added to a JSON object body as ``request_id`` (the field the
        endpoints in :func:`dedupes_request_id` deduplicate on), to a body-less DELETE as a
        query parameter, and to the ``X-Request-Id`` header. ``expected_revision`` is
        added under :func:`revision_field` (inside ``params`` for action-style bodies
        such as the organizations API) and as ``If-Match``.
        """
        verb, target, encoded = self._prepare(
            method, path, body, query, request_id, expected_revision
        )
        request_headers = self._headers(
            accept="application/json",
            has_body=encoded is not None,
            request_id=request_id,
            expected_revision=expected_revision,
            extra=headers,
        )
        connection, response = self._open(
            verb,
            target,
            encoded,
            request_headers,
            timeout=self.timeout if timeout is None else timeout,
            repeatable=safe_to_resend(verb, path, body, request_id),
        )
        try:
            raw = response.read()
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            raise CliError(
                f"The backend's reply was cut off ({exc}).",
                code="outcome_unknown",
                exit_code=ExitCode.UNKNOWN,
            ) from exc
        finally:
            connection.close()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        result = ApiResponse(
            status=response.status,
            headers=response_headers,
            data=_decode_body(
                response_headers.get("content-type", "").split(";", 1)[0].strip().lower(), raw
            ),
            request_id=request_id,
        )
        self._raise_for_status(result.status, result.data)
        return result

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs).data

    def post(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        return self.request("POST", path, body=body, **kwargs).data

    def put(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        return self.request("PUT", path, body=body, **kwargs).data

    def patch(self, path: str, body: Any = None, **kwargs: Any) -> Any:
        return self.request("PATCH", path, body=body, **kwargs).data

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs).data

    def _open_stream(
        self,
        method: str,
        path: str,
        *,
        body: Any,
        query: Mapping[str, Any] | None,
        request_id: str | None,
        expected_revision: int | str | None,
        timeout: float | None,
    ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse, bool]:
        verb, target, encoded = self._prepare(
            method, path, body, query, request_id, expected_revision
        )
        request_headers = self._headers(
            accept="application/x-ndjson, application/json",
            has_body=encoded is not None,
            request_id=request_id,
            expected_revision=expected_revision,
            extra=None,
        )
        connection, response = self._open(
            verb,
            target,
            encoded,
            request_headers,
            timeout=timeout,
            repeatable=safe_to_resend(verb, path, body, request_id),
        )
        content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
        is_stream = response.status < 400 and content_type == "application/x-ndjson"
        return connection, response, is_stream

    def _read_reply(
        self, connection: http.client.HTTPConnection, response: http.client.HTTPResponse
    ) -> Any:
        try:
            content_type = (response.getheader("Content-Type") or "").split(";", 1)[0]
            data = _decode_body(content_type.strip().lower(), response.read())
        finally:
            connection.close()
        self._raise_for_status(response.status, data)
        return data

    def _ndjson_lines(
        self,
        connection: http.client.HTTPConnection,
        response: http.client.HTTPResponse,
        deadline: float | None,
        clock: Callable[[], float],
    ) -> Iterator[Any]:
        try:
            while True:
                if deadline is not None:
                    remaining = deadline - clock()
                    if remaining <= 0:
                        raise CliError(
                            "Stopped following at the timeout.",
                            code="timeout",
                            exit_code=ExitCode.UNKNOWN,
                        )
                    if connection.sock is not None:
                        connection.sock.settimeout(remaining)
                try:
                    line = response.readline()
                except TimeoutError as exc:
                    raise CliError(
                        "Stopped following at the timeout.",
                        code="timeout",
                        exit_code=ExitCode.UNKNOWN,
                    ) from exc
                except (OSError, http.client.HTTPException) as exc:
                    raise CliError(
                        f"The event stream was cut off ({exc}).",
                        code="outcome_unknown",
                        exit_code=ExitCode.UNKNOWN,
                    ) from exc
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    yield json.loads(text)
                except json.JSONDecodeError:
                    yield {"type": "text", "text": text}
        finally:
            connection.close()

    def stream(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        expected_revision: int | str | None = None,
        timeout: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> Iterator[Any]:
        """Yield events from an NDJSON endpoint as they arrive.

        A plain JSON reply is yielded once, so callers can treat every endpoint the
        same way. ``timeout`` bounds the whole stream (``None`` waits until the backend
        closes it); reaching it raises a ``timeout`` error (exit 8).
        """
        deadline = None if timeout is None else clock() + timeout
        connection, response, is_stream = self._open_stream(
            method,
            path,
            body=body,
            query=query,
            request_id=request_id,
            expected_revision=expected_revision,
            timeout=timeout if timeout is not None else None,
        )
        if not is_stream:
            yield self._read_reply(connection, response)
            return
        yield from self._ndjson_lines(connection, response, deadline, clock)

    def follow(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        query: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        expected_revision: int | str | None = None,
        items_key: str = "events",
        cursor_param: str | None = "after",
        cursor_field: str = "seq",
        interval: float = 2.0,
        timeout: float | None = None,
        stop: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Iterator[Any]:
        """Follow any endpoint: NDJSON streams pass through, JSON event lists are polled.

        NDJSON replies (``/api/chat/stream``, ``/api/chat/events``) yield each event
        until the backend ends the stream. A GET that returns a list under
        ``items_key`` (``/api/rem/events``, workflow run events) is polled like
        :meth:`poll`. Anything else is not followable (exit 2).
        """
        deadline = None if timeout is None else clock() + timeout
        connection, response, is_stream = self._open_stream(
            method,
            path,
            body=body,
            query=query,
            request_id=request_id,
            expected_revision=expected_revision,
            timeout=timeout,
        )
        if is_stream:
            yield from self._ndjson_lines(connection, response, deadline, clock)
            return
        data = self._read_reply(connection, response)
        items = data.get(items_key) if isinstance(data, dict) else data
        if method.upper() != "GET" or not isinstance(items, list):
            raise CliError(
                f"{api_path(path)} is not an event stream or a list under {items_key!r}; "
                "it can't be followed.",
                code="not_followable",
                exit_code=ExitCode.USAGE,
                data=data,
            )
        params = dict(query or {})
        cursored = bool(cursor_param) and any(
            isinstance(item, dict) and isinstance(item.get(cursor_field), int) for item in items
        )
        if cursored and cursor_param:
            last = max(
                item[cursor_field]
                for item in items
                if isinstance(item, dict) and isinstance(item.get(cursor_field), int)
            )
            params[cursor_param] = last
        yield from items
        if stop is not None and stop():
            return
        if deadline is not None and clock() >= deadline:
            raise CliError(
                "Stopped following at the timeout.", code="timeout", exit_code=ExitCode.UNKNOWN
            )
        sleep(interval if deadline is None else min(interval, max(0.0, deadline - clock())))
        yield from self.poll(
            path,
            query=params,
            items_key=items_key,
            cursor_param=cursor_param if cursored else None,
            cursor_field=cursor_field,
            interval=interval,
            timeout=None if deadline is None else max(0.0, deadline - clock()),
            stop=stop,
            skip=0 if cursored else len(items),
            clock=clock,
            sleep=sleep,
        )

    def poll(
        self,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        items_key: str = "events",
        cursor_param: str | None = "after",
        cursor_field: str = "seq",
        interval: float = 2.0,
        timeout: float | None = None,
        stop: Callable[[], bool] | None = None,
        skip: int = 0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Iterator[Any]:
        """Follow a JSON endpoint that returns a growing list under ``items_key``.

        With a ``cursor_param`` (``/api/rem/events?after=N``) the highest
        ``cursor_field`` seen is sent back on the next request. Without a cursor, each
        poll yields only items past the number already seen. Ends at ``timeout`` with a
        ``timeout`` error (exit 8) or when ``stop()`` returns true.
        """
        deadline = None if timeout is None else clock() + timeout
        params = dict(query or {})
        seen = max(0, skip)
        cursor = params.get(cursor_param) if cursor_param else None
        while True:
            if cursor_param and cursor is not None:
                params[cursor_param] = cursor
            data = self.get(path, query=params)
            items = data.get(items_key) if isinstance(data, dict) else data
            if not isinstance(items, list):
                raise CliError(
                    f"{api_path(path)} did not return a list under {items_key!r}; "
                    "it can't be followed.",
                    code="not_followable",
                    exit_code=ExitCode.USAGE,
                    data=data,
                )
            cursored = bool(cursor_param) and any(
                isinstance(item, dict) and isinstance(item.get(cursor_field), int) for item in items
            )
            fresh = items if cursored else items[seen:]
            seen = max(seen, len(items))
            for item in fresh:
                if cursored and isinstance(item, dict) and isinstance(item.get(cursor_field), int):
                    cursor = max(int(cursor or 0), item[cursor_field])
                yield item
            if stop is not None and stop():
                return
            if deadline is not None and clock() >= deadline:
                raise CliError(
                    "Stopped following at the timeout.",
                    code="timeout",
                    exit_code=ExitCode.UNKNOWN,
                )
            delay = interval if deadline is None else min(interval, max(0.0, deadline - clock()))
            sleep(delay)


def _user_agent() -> str:
    try:
        from gofer.utils.version import installed_version

        version = installed_version() or "0.0.0+source"
    except Exception:  # noqa: BLE001
        version = "0.0.0+source"
    return f"Raticode-CLI/{version}"


# --- Waiting ---------------------------------------------------------------------


@dataclass
class WaitResult:
    value: Any
    exit_code: ExitCode
    elapsed: float
    attempts: int


def parse_duration(value: str | float | int | None) -> float | None:
    """Parse ``90``, ``90s``, ``5m``, ``1h30m`` or ``2d`` into seconds."""
    if value is None:
        return None
    if isinstance(value, int | float):
        seconds = float(value)
    else:
        text = value.strip().lower()
        if not text:
            return None
        if re.fullmatch(r"\d+(\.\d+)?", text):
            seconds = float(text)
        else:
            parts = re.findall(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)", text)
            if not parts or re.sub(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)", "", text).strip():
                raise CliError(
                    f"Invalid duration {value!r}. Use forms like 30s, 5m or 1h30m.",
                    code="invalid_duration",
                    exit_code=ExitCode.USAGE,
                )
            scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
            seconds = sum(float(n) * scale[unit] for n, unit in parts)
    if seconds < 0:
        raise CliError(
            "Durations can't be negative.", code="invalid_duration", exit_code=ExitCode.USAGE
        )
    return seconds


def _lookup(value: Any, dotted: str) -> Any:
    current = value
    for part in dotted.split(".") if dotted else []:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.lstrip("-").isdigit():
            index = int(part)
            current = current[index] if -len(current) <= index < len(current) else None
        else:
            return None
    return current


def field_classifier(
    field_path: str,
    *,
    success: Sequence[str] = (),
    failure: Sequence[str] = (),
    needs_user: Sequence[str] = (),
) -> Callable[[Any], ExitCode | None]:
    """Classify a JSON value by the string at ``field_path`` (dotted, list indexes OK)."""
    ok = {str(v) for v in success}
    bad = {str(v) for v in failure}
    waiting = {str(v) for v in needs_user}

    def classify(value: Any) -> ExitCode | None:
        found = _lookup(value, field_path)
        state = str(found).lower() if isinstance(found, bool) else found
        if state is None:
            return None
        state = str(state)
        if state in ok:
            return ExitCode.OK
        if state in bad:
            return ExitCode.FAILED
        if state in waiting:
            return ExitCode.NEEDS_USER
        return None

    return classify


def wait_until(
    fetch: Callable[[], T],
    classify: Callable[[T], ExitCode | None],
    *,
    timeout: float | None,
    interval: float = 2.0,
    max_interval: float = 15.0,
    tolerate_unreachable: bool = True,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> WaitResult:
    """Poll ``fetch`` until ``classify`` returns an exit code or the timeout passes.

    The interval backs off by half each poll up to ``max_interval``. A backend that is
    briefly unreachable (restarting) is tolerated until the deadline. On timeout this
    raises a ``timeout`` :class:`CliError` (exit 8) whose ``data`` is the last value.
    """
    start = clock()
    deadline = None if timeout is None else start + timeout
    delay = max(0.05, interval)
    attempts = 0
    last: Any = None
    while True:
        attempts += 1
        try:
            last = fetch()
        except CliError as exc:
            if not (tolerate_unreachable and exc.exit_code == ExitCode.UNREACHABLE):
                raise
        else:
            outcome = classify(last)
            if outcome is not None:
                return WaitResult(last, outcome, clock() - start, attempts)
        now = clock()
        if deadline is not None and now >= deadline:
            raise CliError(
                f"Still waiting after {timeout:g}s.",
                code="timeout",
                exit_code=ExitCode.UNKNOWN,
                data=last,
            )
        pause = delay if deadline is None else min(delay, max(0.0, deadline - now))
        sleep(pause)
        delay = min(max_interval, delay * 1.5)


# --- Output ------------------------------------------------------------------------


class OutputFormat(StrEnum):
    json = "json"
    ndjson = "ndjson"
    text = "text"


def envelope(
    command: str,
    *,
    data: Any = None,
    error: CliError | None = None,
    warnings: Sequence[str] = (),
    request_id: str | None = None,
) -> dict[str, Any]:
    """The stable result object every backend-client command prints."""
    payload: dict[str, Any] = {
        "ok": error is None,
        "command": command,
        "data": data if error is None or data is not None else error.data,
        "error": None if error is None else error.to_error(),
        "warnings": list(warnings),
    }
    if request_id:
        payload["request_id"] = request_id
    return payload


@dataclass
class CommandResult:
    """What a command body returns to :func:`run_command`."""

    data: Any = None
    warnings: list[str] = field(default_factory=list)
    request_id: str | None = None
    text: str | None = None  # Human rendering for --output text; JSON otherwise.


class Output:
    """Writes envelopes and events; data goes to stdout, human text to stderr."""

    def __init__(
        self,
        command: str,
        fmt: OutputFormat | str = OutputFormat.json,
        *,
        stdout: IO[str] | None = None,
        stderr: IO[str] | None = None,
    ) -> None:
        self.command = command
        self.format = OutputFormat(fmt)
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self.streaming = self.format == OutputFormat.ndjson

    def _line(self, payload: Any) -> None:
        self.stdout.write(json.dumps(payload, separators=(",", ":"), default=str) + "\n")
        self.stdout.flush()

    def info(self, message: str) -> None:
        self.stderr.write(message.rstrip("\n") + "\n")
        self.stderr.flush()

    def begin_stream(self) -> None:
        """Switch to NDJSON for the rest of the command (``--follow``)."""
        if self.format != OutputFormat.text:
            self.streaming = True

    def event(self, data: Any) -> None:
        if self.format == OutputFormat.text:
            self.stdout.write(
                (data if isinstance(data, str) else json.dumps(data, default=str)) + "\n"
            )
            self.stdout.flush()
            return
        self._line({"kind": "event", "data": data})

    def _final(self, payload: dict[str, Any]) -> None:
        if self.streaming:
            self._line({"kind": "result", **payload})
        else:
            self._line(payload)

    def result(self, result: CommandResult) -> None:
        for warning in result.warnings:
            self.info(f"warning: {warning}")
        if self.format == OutputFormat.text:
            if result.text is not None:
                text = result.text
            elif isinstance(result.data, str):
                text = result.data
            elif result.data is None:
                text = ""
            else:
                text = json.dumps(result.data, indent=2, default=str)
            if text:
                self.stdout.write(text.rstrip("\n") + "\n")
                self.stdout.flush()
            return
        self._final(
            envelope(
                self.command,
                data=result.data,
                warnings=result.warnings,
                request_id=result.request_id,
            )
        )

    def error(self, error: CliError, *, request_id: str | None = None) -> None:
        self.info(f"Raticode {self.command}: {error.message}")
        if self.format == OutputFormat.text:
            return
        self._final(envelope(self.command, error=error, request_id=request_id))


def run_command(
    command: str,
    output: OutputFormat | str,
    body: Callable[[Output], CommandResult | None],
    *,
    request_id: str | None = None,
) -> None:
    """Run a command body, print its envelope and exit with the right code.

    The body returns a :class:`CommandResult` (or ``None`` after printing its own
    output) and raises :class:`CliError` for failures. Exits through ``typer.Exit``.
    """
    import typer

    out = Output(command, output)
    try:
        result = body(out)
    except CliError as exc:
        out.error(exc, request_id=exc.request_id or request_id)
        raise typer.Exit(int(exc.exit_code)) from None
    except KeyboardInterrupt:
        out.error(
            CliError("Interrupted.", code="interrupted", exit_code=ExitCode.UNKNOWN),
            request_id=request_id,
        )
        raise typer.Exit(int(ExitCode.UNKNOWN)) from None
    if result is not None:
        if result.request_id is None:
            result.request_id = request_id
        out.result(result)
    raise typer.Exit(int(ExitCode.OK))


# --- Interaction -------------------------------------------------------------------


def is_non_interactive(flag: bool = False, env: Mapping[str, str] | None = None) -> bool:
    """True when the CLI must never prompt (flag, env, CI, or no terminal on stdin)."""
    environ = os.environ if env is None else env
    if flag:
        return True
    if environ.get(ENV_NON_INTERACTIVE, "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if environ.get("CI", "").strip().lower() in {"1", "true", "yes"}:
        return True
    try:
        return not sys.stdin.isatty()
    except (AttributeError, ValueError):
        return True


def confirm(message: str, *, yes: bool = False, non_interactive: bool = False) -> None:
    """Ask before a destructive action; in non-interactive mode require ``--yes``."""
    if yes:
        return
    if is_non_interactive(non_interactive):
        raise CliError(
            f"{message} Pass --yes to confirm in non-interactive mode.",
            code="confirmation_required",
            exit_code=ExitCode.USAGE,
        )
    import typer

    if not typer.confirm(message, default=False, err=True):
        raise CliError("Cancelled.", code="cancelled", exit_code=ExitCode.USAGE)


# --- Completion --------------------------------------------------------------------


def complete_from_api(
    path: str,
    *,
    items_key: str | None = None,
    id_field: str = "id",
    label_field: str | None = "title",
    timeout: float = 0.6,
) -> Callable[[str], list[tuple[str, str]]]:
    """Shell-completion callback that offers IDs from a backend list endpoint.

    Uses a short timeout and returns nothing when no backend is reachable, so
    completion never hangs or prints errors.
    """

    def complete(incomplete: str) -> list[tuple[str, str]]:
        try:
            client = ApiClient(discover_endpoint(), timeout=timeout, retries=0)
            data = client.get(path)
        except (CliError, OSError, ValueError):
            return []
        items = _lookup(data, items_key) if items_key else data
        if isinstance(items, dict):
            items = list(items.values())
        if not isinstance(items, list):
            return []
        matches: list[tuple[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            ident = item.get(id_field)
            if not isinstance(ident, str | int) or not str(ident).startswith(incomplete):
                continue
            label = item.get(label_field) if label_field else None
            matches.append((str(ident), str(label or "")[:60]))
        return matches[:100]

    return complete
