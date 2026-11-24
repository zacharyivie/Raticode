"""Call one tool on an MCP server from Rem's library, without a model.

The ``mcp-tool`` node names a server from the Rem library (``Raticode rem mcp add``),
one of its tools and the arguments. Each call starts a fresh session: a local
(``stdio``) server is started for the call and stopped after it, and an ``http``
server gets one Streamable HTTP session.

Local servers run in the same kind of operating-system sandbox as agent steps
(``agent_sandbox``): they write only to the step's working directory, a scratch
folder for the run and the server's package cache (``mcp_cache``), credential folders
and the rest of Raticode's data folder are hidden, and the network is limited to the
hosts in the step's ``network`` field. On Linux that is bubblewrap with a private
network namespace whose only way out is a filtering proxy for the listed hosts; on
macOS it is ``sandbox-exec``. Native Windows has no sandbox, so servers there run
unsandboxed and preflight says so.

Only list-only starts (preflight, the Studio's tool picker, ``Raticode rem mcp tools``)
may write the package cache: they carry no workflow data. A tool call reads it through
a throwaway layer: on Linux a bubblewrap overlay whose writes go to a tmpfs
(``--overlay-src`` and ``--tmp-overlay``, bubblewrap 0.11 or later), on macOS an APFS
copy-on-write clone in the run's scratch folder. On Linux without overlays the call gets
a copy in its scratch folder (``cp -a --reflink=auto``) of a cache that a list-only
start has filled and that holds at most ``COPY_LIMIT_BYTES``. Where none of these
works, the call gets an empty cache in its scratch folder.

A local server doesn't get Raticode's whole environment. It gets a base set (home
folder, user, ``PATH``, shell, terminal, locale and temporary-folder variables, and the
Windows equivalents), the sandbox's own temporary, cache and proxy variables, and the
variables its library entry names (``Raticode rem mcp add NAME --env VAR``).

A trigger approval covers each server's definition (``definition_text``), so a
changed command, argument list, variable name or URL makes the trigger ask again
before it runs. The definition holds variable names, never their values. A command
or argument that names a regular file inside the project, such as ``tools/server.py``
in ``python3 tools/server.py``, is covered as a script too (``project_files``). It
can't cover what a package runner downloads, so ``mcp_packages`` flags servers whose
package has no exact version.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import queue
import secrets
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from gofer.rattish import agent_sandbox, mcp_cache

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "raticode-workflow", "version": "1"}
# The largest single message read from a server.
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_TOOLS = 1000
MAX_TOOL_PAGES = 20
STDERR_TAIL_BYTES = 4096
DEFAULT_LIST_TIMEOUT_SECONDS = 30.0
# Preflight lists a server's tools at most this often per definition and sandbox.
LIST_CACHE_SECONDS = 300.0
FAILED_LIST_CACHE_SECONDS = 20.0
APPROVAL_PREFIX = "mcp-server:"
APPROVAL_ROLE = "mcp_server"
# Inside the Linux sandbox the proxy is reached on this loopback port.
SANDBOX_PROXY_PORT = 3128
_SANDBOX_PROXY_DIR = "/tmp/.raticode-proxy"
_SANDBOX_PROXY_SOCKET = "proxy.sock"


class McpToolError(Exception):
    """A server or tool call failed: a Rattish error kind, code and message."""

    def __init__(
        self, kind: str, code: str, message: str, details: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.message = message
        self.details = dict(details or {})


# ----------------------------------------------------------------------------
# The server definition


def library_server(data_dir: Path, name: str) -> dict[str, Any]:
    """The library's current definition of server ``name``."""
    from gofer.core import rem_library

    try:
        servers = rem_library.load_library(data_dir).mcpServers
    except ValueError as exc:
        raise McpToolError("configuration", "RATTISH_MCP_LIBRARY_INVALID", str(exc)) from exc
    for server in servers:
        if server.name == name:
            return server.model_dump(include={"name", "type", "url", "command", "args", "env"})
    known = ", ".join(sorted(server.name for server in servers)) or "none yet"
    raise McpToolError(
        "configuration",
        "RATTISH_MCP_SERVER_UNKNOWN",
        f"Rem's MCP server list has no server named {name!r} (servers: {known}).",
        {"server": name},
    )


def definition_text(server: Mapping[str, Any]) -> str:
    """What a trigger approval shows and covers for one server."""
    lines = [f"name: {server.get('name', '')}", f"type: {server.get('type', 'http')}"]
    if server.get("type") == "stdio":
        lines.append(f"command: {json.dumps(str(server.get('command', '')))}")
        lines.append("args:")
        lines.extend(f"  - {json.dumps(str(arg))}" for arg in server.get("args") or [])
        names = env_names(server)
        # Added only when set, so servers without names keep the digests already approved.
        if names:
            lines.append("env:")
            lines.extend(f"  - {name}" for name in names)
    else:
        lines.append(f"url: {server.get('url', '')}")
    return "\n".join(lines) + "\n"


def definition_from_text(text: str | None) -> dict[str, Any] | None:
    """The server ``definition_text`` wrote, read back: name, type, command and args, or URL."""
    if not text:
        return None
    server: dict[str, Any] = {}
    args: list[str] = []
    for line in text.splitlines():
        try:
            if line.startswith("  - "):
                args.append(str(json.loads(line[4:])))
            elif line.startswith("command: "):
                server["command"] = str(json.loads(line[len("command: ") :]))
            elif line.startswith(("name: ", "type: ", "url: ")):
                key, _, value = line.partition(": ")
                server[key] = value
        except json.JSONDecodeError:
            return None
    if server.get("type") == "stdio":
        server["args"] = args
    return server


def definition_digest(server: Mapping[str, Any] | None) -> str | None:
    if server is None:
        return None
    digest = hashlib.sha256(definition_text(server).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def env_names(server: Mapping[str, Any]) -> list[str]:
    """The environment variables a local server's library entry names."""
    if not is_local(server):
        return []
    return [str(name) for name in server.get("env") or []]


def missing_env(server: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> list[str]:
    """Named variables that aren't set in Raticode's environment."""
    source = os.environ if environ is None else environ
    return [name for name in env_names(server) if source.get(name) is None]


def approval_key(name: str) -> str:
    return f"{APPROVAL_PREFIX}{name}"


def project_files(
    server: Mapping[str, Any], working_dir: Path | None, project_root: Path
) -> list[Path]:
    """Regular files inside the project that a local server's command or arguments name.

    A server like ``python3 tools/server.py`` runs the project's own code, so a trigger
    approval covers each such file as a script the workflow runs. A relative value
    resolves from ``working_dir``, where the server starts; without that folder the server
    starts somewhere else, so only absolute values count. Each path is resolved, and one
    that leads outside ``project_root`` doesn't count.
    """
    if not is_local(server):
        return []
    try:
        root = project_root.resolve()
        base = working_dir if working_dir is not None and working_dir.is_dir() else None
    except (OSError, RuntimeError):
        return []
    found: list[Path] = []
    for value in [server.get("command"), *(server.get("args") or [])]:
        text = str(value or "")
        if not text:
            continue
        try:
            candidate = Path(text)
            if not candidate.is_absolute():
                if base is None:
                    continue
                candidate = base / candidate
            resolved = candidate.resolve()
            if resolved.is_relative_to(root) and resolved.is_file() and resolved not in found:
                found.append(resolved)
        except (OSError, RuntimeError, ValueError):
            # Too long, a null byte, or a symbolic-link loop: not a file it can run.
            continue
    return found


# ----------------------------------------------------------------------------
# The sandbox


@dataclass(frozen=True, slots=True)
class SandboxSettings:
    """Where a local server may write and which hosts it may reach."""

    enabled: bool = True
    network: agent_sandbox.Network = field(default_factory=list)
    working_dir: Path | None = None

    def signature(self) -> tuple[Any, ...]:
        network = self.network if self.network == agent_sandbox.ANY_HOST else tuple(self.network)
        return (self.enabled, network, str(self.working_dir or ""))


def step_settings(configuration: Mapping[str, Any], project_root: Path) -> SandboxSettings:
    from gofer.rattish.project_paths import project_path

    working = configuration.get("working_dir") or "."
    return SandboxSettings(
        enabled=configuration.get("sandbox") != agent_sandbox.SANDBOX_OFF,
        network=agent_sandbox.step_network(configuration),
        working_dir=project_path(project_root, str(working)),
    )


def is_local(server: Mapping[str, Any]) -> bool:
    return server.get("type") == "stdio"


def unsandboxed_reason(
    server: Mapping[str, Any], settings: SandboxSettings, *, platform: str | None = None
) -> str | None:
    """Why this local server would run without a sandbox, or None."""
    if not is_local(server):
        return None
    if not settings.enabled:
        return "the step sets sandbox: off"
    if (platform or sys.platform) == "win32":
        return "Raticode has no sandbox for MCP servers on native Windows"
    return None


_probe_lock = threading.Lock()
_probe_result: dict[str, str | None] = {}


def _bwrap_probe() -> str | None:
    """Whether bubblewrap can make the namespaces a server needs here, cached per process."""
    with _probe_lock:
        if "bwrap" in _probe_result:
            return _probe_result["bwrap"]
        try:
            completed = subprocess.run(
                [
                    "bwrap",
                    "--ro-bind",
                    "/",
                    "/",
                    "--unshare-net",
                    "--unshare-pid",
                    "--dev",
                    "/dev",
                    "--proc",
                    "/proc",
                    "true",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            reason: str | None = f"bubblewrap couldn't start: {exc}"
        else:
            reason = None
            if completed.returncode != 0:
                detail = (completed.stderr or "").strip().splitlines()
                reason = "bubblewrap can't create a sandbox here" + (
                    f": {detail[-1]}" if detail else "."
                )
        _probe_result["bwrap"] = reason
        return reason


# Overlay mounts (--overlay-src, --tmp-overlay) arrived in this bubblewrap release.
OVERLAY_MIN_BWRAP = (0, 11)


def _bwrap_version() -> tuple[int, ...] | None:
    try:
        completed = subprocess.run(
            ["bwrap", "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_bwrap_version(completed.stdout)


def parse_bwrap_version(text: str) -> tuple[int, ...] | None:
    """``(0, 13, 1)`` from ``bubblewrap 0.13.1``; None when there's no version."""
    for word in text.split():
        parts = word.strip().split(".")
        if len(parts) >= 2 and all(part.isdigit() for part in parts):
            return tuple(int(part) for part in parts)
    return None


def _overlay_probe(root: Path) -> str | None:
    """Why tool calls can't read the package cache through a bubblewrap overlay, or None.

    ``root`` is the folder that holds every server's cache. The probe mounts a folder
    there the way a tool call does, writes into it and checks that nothing reached the
    real folder. Cached per process.
    """
    with _probe_lock:
        if "overlay" in _probe_result:
            return _probe_result["overlay"]
    version = _bwrap_version()
    reason: str | None
    if version is None:
        reason = "bubblewrap didn't report its version"
    elif version < OVERLAY_MIN_BWRAP:
        shown = ".".join(str(part) for part in version)
        reason = f"bubblewrap {shown} is older than 0.11, which added overlay mounts"
    else:
        reason = _overlay_mount_problem(root)
    with _probe_lock:
        _probe_result["overlay"] = reason
    return reason


def _overlay_mount_problem(root: Path) -> str | None:
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix=".overlay-probe-", dir=root))
    except OSError as exc:
        return f"no folder for the overlay check: {exc}"
    try:
        target = str(folder.resolve())
        completed = subprocess.run(
            [
                "bwrap",
                "--die-with-parent",
                "--unshare-pid",
                "--unshare-ipc",
                "--unshare-net",
                "--ro-bind",
                "/",
                "/",
                "--dev",
                "/dev",
                "--proc",
                "/proc",
                "--tmpfs",
                str(root.resolve()),
                "--overlay-src",
                target,
                "--tmp-overlay",
                target,
                "--",
                "/bin/sh",
                "-c",
                ': > "$1/probe"',
                "sh",
                target,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"bubblewrap couldn't start: {exc}"
    else:
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()
            return "bubblewrap couldn't mount an overlay" + (f": {detail[-1]}" if detail else "")
        if (folder / "probe").exists():
            return "a write through bubblewrap's overlay reached the real folder"
        return None
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def sandbox_problem(
    server: Mapping[str, Any],
    settings: SandboxSettings,
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] | None = None,
    probe: Callable[[], str | None] | None = None,
) -> str | None:
    """Why a local server can't start in its sandbox here, or None.

    ``which`` defaults to ``shutil.which`` and ``probe`` to the module's ``_bwrap_probe``,
    both looked up at call time.
    """
    platform = platform or sys.platform
    which = which or shutil.which
    if unsandboxed_reason(server, settings, platform=platform) or not is_local(server):
        return None
    if platform == "darwin":
        if not Path("/usr/bin/sandbox-exec").is_file():
            return "The MCP server sandbox needs macOS's sandbox-exec, which isn't on this Mac."
        return None
    needed = ["bwrap"]
    if isinstance(settings.network, list) and settings.network:
        needed.append("socat")
    missing = [tool for tool in needed if which(tool) is None]
    if missing:
        names = " and ".join({"bwrap": "bubblewrap (bwrap)", "socat": "socat"}[m] for m in missing)
        return (
            f"The MCP server sandbox needs {names}, which {'is' if len(missing) == 1 else 'are'} "
            "not installed. Install it with your package manager, for example "
            "`sudo apt install bubblewrap socat`."
        )
    return (probe or _bwrap_probe)()


# The system bus answers any program that can reach its socket: systemd-resolved's
# ResolveHostname there looks up names (carrying data out of a sandbox without network),
# and NetworkManager or systemd may act on the computer for a local session's programs.
SYSTEM_BUS_FOLDERS = ("/run/dbus",)
# Sockets that look names up for a program: systemd-resolved, Avahi (mDNS on the local
# network) and nscd. Hidden unless the sandbox allows every host.
RESOLVER_FOLDERS = ("/run/systemd/resolve", "/run/avahi-daemon", "/run/nscd")


def _hidden_paths(data_dir: Path) -> list[Path]:
    from gofer.ui import claude_sandbox

    paths = [Path(os.path.expanduser(item)) for item in claude_sandbox.SECRET_PATHS]
    paths.extend(Path(item) for item in claude_sandbox.secrets_folders())
    paths.append(data_dir)
    found: list[Path] = []
    for path in paths:
        if path.exists() and path not in found:
            found.append(path)
    return found


def bwrap_command(
    command: list[str],
    *,
    settings: SandboxSettings,
    data_dir: Path,
    scratch: Path,
    proxy_dir: Path | None,
    cache: Path | None = None,
    cache_overlay: bool = False,
    read_only: list[Path] | None = None,
) -> list[str]:
    """The bubblewrap command line that runs ``command`` in the server's sandbox.

    ``cache`` is bound writable, or with ``cache_overlay`` mounted as an overlay whose
    writes go to a tmpfs that disappears when the server exits. ``read_only`` folders in
    the hidden data folder are shown again for reading only.
    """
    argv = ["bwrap", "--die-with-parent", "--unshare-pid", "--unshare-ipc"]
    if settings.network != agent_sandbox.ANY_HOST:
        argv.append("--unshare-net")
    argv += ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    # A read-only mount still lets a program connect to the sockets in it. The user's
    # runtime folder holds the session bus (systemd can start a unit outside the sandbox
    # through it, and the keyring answers on it), the SSH agent and the display; the
    # resolver's socket answers name lookups, which carry data out of a sandbox with no
    # network or only some hosts.
    runtime = [Path(f"/run/user/{os.getuid()}")] if hasattr(os, "getuid") else []
    runtime.extend(Path(item) for item in SYSTEM_BUS_FOLDERS)
    if settings.network != agent_sandbox.ANY_HOST:
        runtime.extend(Path(item) for item in RESOLVER_FOLDERS)
    for path in runtime:
        if path.is_dir():
            argv += ["--tmpfs", str(path)]
    hidden = _hidden_paths(data_dir.resolve())

    def hide(paths: list[Path]) -> None:
        nonlocal argv
        for path in paths:
            if path.is_dir():
                argv += ["--tmpfs", str(path)]
            else:
                argv += ["--ro-bind", "/dev/null", str(path)]

    hide(hidden)
    writable = [scratch.resolve()]
    if cache is not None and not cache_overlay:
        writable.append(cache.resolve())
    if settings.working_dir is not None:
        writable.insert(0, settings.working_dir.resolve())
    for path in writable:
        argv += ["--bind", str(path), str(path)]
    # A writable folder that holds a hidden one (a working folder of the home folder, say)
    # would show it again: hide those once more, then show the writable folders inside
    # them, such as the scratch folder in the data folder.
    rehidden = [h for h in hidden if any(h != w and h.is_relative_to(w) for w in writable)]
    if rehidden:
        hide(rehidden)
        for path in writable:
            if any(path != h and path.is_relative_to(h) for h in rehidden):
                argv += ["--bind", str(path), str(path)]
    if cache is not None and cache_overlay:
        lower = str(cache.resolve())
        argv += ["--overlay-src", lower, "--tmp-overlay", lower]
    for path in read_only or []:
        shown = str(path.resolve())
        argv += ["--ro-bind", shown, shown]
    if proxy_dir is not None:
        argv += ["--bind", str(proxy_dir), _SANDBOX_PROXY_DIR]
    if settings.working_dir is not None:
        argv += ["--chdir", str(settings.working_dir.resolve())]
    argv.append("--")
    if proxy_dir is not None:
        # socat carries the server's loopback proxy port to the filtering proxy outside.
        bridge = (
            f"socat TCP-LISTEN:{SANDBOX_PROXY_PORT},bind=127.0.0.1,fork,reuseaddr "
            f"UNIX-CONNECT:{_SANDBOX_PROXY_DIR}/{_SANDBOX_PROXY_SOCKET} >/dev/null 2>&1 & "
            'sleep 0.1; exec "$@"'
        )
        argv += ["/bin/sh", "-c", bridge, "sh"]
    return argv + command


def _seatbelt_string(path: Path | str) -> str:
    return json.dumps(str(path))


def seatbelt_profile(
    *,
    settings: SandboxSettings,
    data_dir: Path,
    scratch: Path,
    proxy_port: int | None,
    cache: Path | None = None,
    read_only: list[Path] | None = None,
) -> str:
    """A macOS sandbox profile with the same limits as the Linux sandbox.

    ``cache`` is readable and writable; ``read_only`` folders in the hidden data folder
    are shown again for reading only.
    """
    shown = [scratch.resolve()] + ([cache.resolve()] if cache is not None else [])
    readable = [path.resolve() for path in read_only or []]
    writable = [*shown, Path(tempfile.gettempdir()).resolve()]
    if settings.working_dir is not None:
        writable.insert(0, settings.working_dir.resolve())
    hidden = _hidden_paths(data_dir.resolve())
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny file-write*)",
        "(allow file-write* "
        + " ".join(f"(subpath {_seatbelt_string(path)})" for path in writable)
        + ' (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr")'
        + ' (literal "/dev/tty") (subpath "/dev/fd"))',
    ]
    if hidden:
        lines.append(
            "(deny file-read* file-write* "
            + " ".join(f"(subpath {_seatbelt_string(path)})" for path in hidden)
            + ")"
        )
        # The run's scratch and the server's cache sit in the hidden data folder.
        lines.append(
            "(allow file-read* file-write* "
            + " ".join(f"(subpath {_seatbelt_string(path)})" for path in shown)
            + ")"
        )
        if readable:
            lines.append(
                "(allow file-read* "
                + " ".join(f"(subpath {_seatbelt_string(path)})" for path in readable)
                + ")"
            )
    if settings.network != agent_sandbox.ANY_HOST:
        lines.append("(deny network-outbound)")
        lines.append("(deny network-bind)")
        if proxy_port is not None:
            lines.append(f'(allow network-outbound (remote ip "localhost:{proxy_port}"))')
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------
# The proxy for listed hosts


class HostProxy:
    """An HTTP proxy that only opens connections to the hosts a step lists.

    It takes ``CONNECT host:port`` for HTTPS and absolute-URL requests for plain HTTP.
    On Linux it listens on a Unix socket bound into the sandbox; on macOS on a loopback
    port the sandbox profile allows.
    """

    def __init__(self, network: list[str], *, unix_dir: Path | None) -> None:
        self.network = network
        self.unix_dir = unix_dir
        self._closed = threading.Event()
        if unix_dir is not None:
            self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._server.bind(str(unix_dir / _SANDBOX_PROXY_SOCKET))
            self.port = None
        else:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.bind(("127.0.0.1", 0))
            self.port = int(self._server.getsockname()[1])
        self._server.listen(32)
        self._thread = threading.Thread(target=self._serve, name="mcp-host-proxy", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._closed.is_set():
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        try:
            client.settimeout(30)
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = client.recv(4096)
                if not chunk:
                    return
                head += chunk
                if len(head) > 64 * 1024:
                    return
            request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
            parts = request_line.split()
            if len(parts) != 3:
                client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                return
            method, target, _ = parts
            if method.upper() == "CONNECT":
                host, _, port_text = target.rpartition(":")
                port = int(port_text) if port_text.isdigit() else 443
                rest = head.split(b"\r\n\r\n", 1)[1]
            else:
                url = urlsplit(target)
                if url.scheme != "http" or not url.hostname:
                    client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                    return
                host, port = url.hostname, url.port or 80
                rest = head
            host = host.strip("[]")
            if not agent_sandbox.host_allowed(host, self.network):
                message = f"Raticode's sandbox doesn't allow {host}. Add it to network."
                body = message.encode("utf-8")
                client.sendall(
                    b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                    + body
                )
                return
            upstream = socket.create_connection((host, port), timeout=30)
            if method.upper() == "CONNECT":
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                upstream.sendall(rest)
            client.settimeout(None)
            upstream.settimeout(None)
            self._pipe(client, upstream)
        except (OSError, ValueError):
            return
        finally:
            with contextlib.suppress(OSError):
                client.close()
            if upstream is not None:
                with contextlib.suppress(OSError):
                    upstream.close()

    def _pipe(self, left: socket.socket, right: socket.socket) -> None:
        sockets = [left, right]
        while not self._closed.is_set():
            readable, _, _ = select.select(sockets, [], [], 0.5)
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                (right if source is left else left).sendall(data)

    def close(self) -> None:
        self._closed.set()
        with contextlib.suppress(OSError):
            self._server.close()


def _short_socket_dir(scratch: Path) -> Path:
    """A private folder whose socket path fits the 104-byte Unix socket limit."""
    for base in (scratch, Path(tempfile.gettempdir()), Path("/tmp")):
        if len(str(base)) + 40 < 100 and base.is_dir():
            return Path(tempfile.mkdtemp(prefix="rt-mcp-", dir=base))
    raise McpToolError(
        "configuration",
        "RATTISH_MCP_SANDBOX_UNAVAILABLE",
        "No folder with a short enough path for the sandbox's network proxy.",
    )


# ----------------------------------------------------------------------------
# Starting a local server


@dataclass(slots=True)
class Launch:
    argv: list[str]
    env: dict[str, str]
    cwd: Path | None
    sandboxed: bool
    cleanup: list[Callable[[], None]] = field(default_factory=list)

    def close(self) -> None:
        for step in reversed(self.cleanup):
            with contextlib.suppress(Exception):
                step()
        self.cleanup.clear()


# Every local server gets these from Raticode's environment, plus LC_* locale variables.
BASE_ENV = frozenset(
    {
        "HOME",
        "LOGNAME",
        "PATH",
        "SHELL",
        "TERM",
        "USER",
        "LANG",
        "LANGUAGE",
        "TMPDIR",
        "TMP",
        "TEMP",
    }
)
# On native Windows, also these (matched without case, as Windows does).
WINDOWS_BASE_ENV = frozenset(
    {
        "APPDATA",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "COMMONPROGRAMW6432",
        "COMPUTERNAME",
        "COMSPEC",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "PUBLIC",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "USERDOMAIN",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    }
)


def server_base_environment(
    names: list[str] | tuple[str, ...] = (),
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The base set and the named variables, taken from Raticode's environment."""
    source = os.environ if environ is None else environ
    windows = (platform or sys.platform) == "win32"
    wanted = BASE_ENV | WINDOWS_BASE_ENV if windows else BASE_ENV
    env: dict[str, str] = {}
    for key, value in source.items():
        name = key.upper() if windows else key
        if name.startswith("GOFER_"):
            continue
        if name in wanted or name.startswith("LC_"):
            env[key] = value
    for name in names:
        named = source.get(name)
        if named is not None and not name.upper().startswith("GOFER_"):
            env[name] = named
    return env


def _server_environment(
    scratch: Path,
    sandboxed: bool,
    names: list[str] | tuple[str, ...] = (),
    *,
    platform: str | None = None,
    cache: Path | None = None,
) -> dict[str, str]:
    """A server's environment. In the sandbox, temporary files go to the run's scratch
    folder and package caches to ``cache``, the server's lasting ``mcp_cache`` folder."""
    env = server_base_environment(names, platform=platform)
    # The sandbox's own variables come last, so a named variable can't replace them.
    if sandboxed:
        temp = scratch / "tmp"
        temp.mkdir(parents=True, exist_ok=True)
        caches = cache if cache is not None else scratch / "cache"
        env.update(
            {
                "TMPDIR": str(temp),
                "TMP": str(temp),
                "TEMP": str(temp),
                "XDG_CACHE_HOME": str(caches / "xdg"),
                "npm_config_cache": str(caches / "npm"),
                "UV_CACHE_DIR": str(caches / "xdg" / "uv"),
            }
        )
    return env


def package_cache(server: Mapping[str, Any], data_dir: Path) -> Path:
    """The server's lasting package cache folder, made ready for a start.

    Caches unused for ``mcp_cache.UNUSED_DAYS`` days are pruned first, at most daily.
    """
    digest = definition_digest(server)
    assert digest is not None
    with contextlib.suppress(OSError):
        mcp_cache.prune_if_due(data_dir)
    try:
        return mcp_cache.prepare(data_dir, digest)
    except OSError as exc:
        raise McpToolError(
            "configuration",
            "RATTISH_MCP_SANDBOX_UNAVAILABLE",
            f"Couldn't create the MCP server's package cache folder: {exc}",
        ) from exc


# How a start uses the server's lasting package cache.
CACHE_WRITABLE = "writable"  # list-only starts: bound writable
CACHE_OVERLAY = "overlay"  # Linux tool calls: read through a throwaway overlay
CACHE_CLONE = "clone"  # macOS tool calls: a copy-on-write clone in the scratch folder
CACHE_COPY = "copy"  # Linux tool calls without overlays: a copy in the scratch folder
CACHE_SCRATCH = "scratch"  # when the others fail: an empty cache in the scratch folder

# The largest package cache a tool call copies when bubblewrap has no overlays.
COPY_LIMIT_BYTES = 500 * 1024 * 1024


def call_cache_mode(data_dir: Path, platform: str | None = None) -> str:
    """How a sandboxed tool call reads the package cache on this computer."""
    platform = platform or sys.platform
    if platform == "darwin":
        return CACHE_CLONE
    if _overlay_probe(mcp_cache.cache_root(data_dir)) is None:
        return CACHE_OVERLAY
    return CACHE_COPY


def _megabytes(size: int) -> str:
    return f"{size / (1024 * 1024):.0f} MB"


def call_cache_problem(
    server: Mapping[str, Any], data_dir: Path, platform: str | None = None
) -> str | None:
    """Why this computer's tool calls get an empty package cache for ``server``, or None.

    Only a Linux computer without bubblewrap overlays whose filled cache is over
    ``COPY_LIMIT_BYTES`` is known to: a copy or clone that fails can't be foreseen.
    """
    platform = platform or sys.platform
    if platform in ("darwin", "win32"):
        return None
    digest = definition_digest(server)
    if not digest:
        return None
    folder = mcp_cache.cache_dir(data_dir, digest)
    if not mcp_cache.listed(folder):
        return None
    size = mcp_cache.folder_size(folder, stop_after=COPY_LIMIT_BYTES)
    if size <= COPY_LIMIT_BYTES:
        return None
    overlay = _overlay_probe(mcp_cache.cache_root(data_dir))
    if overlay is None:
        return None
    return (
        f"{overlay}, and the cache holds more than the {_megabytes(COPY_LIMIT_BYTES)} "
        "a tool call copies instead"
    )


def _scratch_cache(scratch: Path) -> Path:
    folder = Path(tempfile.mkdtemp(prefix="mcp-cache-", dir=scratch))
    for name in ("npm", "xdg"):
        (folder / name).mkdir(mode=0o700)
    return folder


def _clone_cache(cache: Path, scratch: Path) -> Path | None:
    """An APFS copy-on-write clone of ``cache`` in ``scratch``; None when cloning fails."""
    target = scratch / f"mcp-cache-{secrets.token_hex(6)}"
    try:
        completed = subprocess.run(
            ["cp", "-c", "-R", str(cache), str(target)],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        completed = None
    if completed is None or completed.returncode != 0 or not target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
        return None
    return target


def _copy_cache(cache: Path, scratch: Path) -> Path | None:
    """A copy of ``cache`` in ``scratch`` for one tool call, or None.

    Only a cache that a list-only start has filled and that holds at most
    ``COPY_LIMIT_BYTES`` is copied. ``--reflink=auto`` makes the copy free on file systems
    that share blocks (btrfs, XFS) and a plain copy elsewhere.
    """
    if not mcp_cache.listed(cache):
        return None
    if mcp_cache.folder_size(cache, stop_after=COPY_LIMIT_BYTES) > COPY_LIMIT_BYTES:
        return None
    target = scratch / f"mcp-cache-{secrets.token_hex(6)}"
    try:
        completed = subprocess.run(
            ["cp", "-a", "--reflink=auto", "--", str(cache), str(target)],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        completed = None
    if completed is None or completed.returncode != 0 or not target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
        return None
    return target


def _proxy_environment(url: str) -> dict[str, str]:
    return {
        "HTTP_PROXY": url,
        "HTTPS_PROXY": url,
        "http_proxy": url,
        "https_proxy": url,
        "ALL_PROXY": url,
        "all_proxy": url,
        # Nothing skips the proxy: inside the sandbox loopback is its own, empty network,
        # so a listed localhost is reached through the proxy like any other host.
        "NO_PROXY": "",
        "no_proxy": "",
        # Node's own fetch reads the proxy variables only when asked to.
        "NODE_USE_ENV_PROXY": "1",
    }


def prepare_launch(
    server: Mapping[str, Any],
    settings: SandboxSettings,
    *,
    data_dir: Path,
    scratch: Path,
    platform: str | None = None,
    writable_cache: bool = True,
) -> Launch:
    """The command line, environment and folder that start a local server.

    Only list-only starts get ``writable_cache``. A tool call gets the package cache
    through a throwaway layer (``call_cache_mode``), so its writes never reach the
    lasting cache.
    """
    platform = platform or sys.platform
    command = [str(server.get("command", "")), *[str(arg) for arg in server.get("args") or []]]
    cwd = settings.working_dir if settings.working_dir and settings.working_dir.is_dir() else None
    names = env_names(server)
    if unsandboxed_reason(server, settings, platform=platform):
        return Launch(
            command, _server_environment(scratch, False, names, platform=platform), cwd, False
        )
    problem = sandbox_problem(server, settings, platform=platform)
    if problem:
        raise McpToolError(
            "configuration",
            "RATTISH_MCP_SANDBOX_UNAVAILABLE",
            problem,
            {"suggestion": "Fix that, or add `sandbox: off` to the step to run it unsandboxed."},
        )
    scratch.mkdir(parents=True, exist_ok=True)
    lasting = package_cache(server, data_dir)
    mode = CACHE_WRITABLE if writable_cache else call_cache_mode(data_dir, platform)
    cleanup: list[Callable[[], None]] = []
    cache = lasting
    if mode in (CACHE_CLONE, CACHE_COPY):
        copied = (_clone_cache if mode == CACHE_CLONE else _copy_cache)(lasting, scratch)
        mode = mode if copied is not None else CACHE_SCRATCH
        cache = copied if copied is not None else cache
    if mode == CACHE_SCRATCH:
        cache = _scratch_cache(scratch)
    if cache != lasting:
        throwaway = cache
        cleanup.append(lambda: shutil.rmtree(throwaway, ignore_errors=True))
    env = _server_environment(scratch, True, names, platform=platform, cache=cache)
    launch = Launch(command, env, cwd, True, cleanup)
    network = settings.network
    proxy: HostProxy | None = None
    if isinstance(network, list) and network:
        unix_dir = _short_socket_dir(scratch) if platform != "darwin" else None
        if unix_dir is not None:
            launch.cleanup.append(lambda: shutil.rmtree(unix_dir, ignore_errors=True))
        proxy = HostProxy(network, unix_dir=unix_dir)
        launch.cleanup.append(proxy.close)
        port = proxy.port if proxy.port is not None else SANDBOX_PROXY_PORT
        env.update(_proxy_environment(f"http://127.0.0.1:{port}"))
    if platform == "darwin":
        profile = seatbelt_profile(
            settings=settings,
            data_dir=data_dir,
            scratch=scratch,
            proxy_port=proxy.port if proxy is not None else None,
            cache=lasting if mode == CACHE_WRITABLE else None,
            # A clone's files may name the lasting folder (a uv environment's scripts do).
            read_only=[lasting] if mode == CACHE_CLONE else None,
        )
        launch.argv = ["/usr/bin/sandbox-exec", "-p", profile, *command]
    else:
        launch.argv = bwrap_command(
            command,
            settings=settings,
            data_dir=data_dir,
            scratch=scratch,
            proxy_dir=proxy.unix_dir if proxy is not None else None,
            # A copy or scratch cache sits in the scratch folder, which is bound already.
            cache=lasting if mode in (CACHE_WRITABLE, CACHE_OVERLAY) else None,
            cache_overlay=mode == CACHE_OVERLAY,
            # A copy's files may name the lasting folder (a uv environment's scripts do).
            read_only=[lasting] if mode == CACHE_COPY else None,
        )
    return launch


# ----------------------------------------------------------------------------
# Sessions


class _Session:
    def request(self, method: str, params: Mapping[str, Any] | None, deadline: float) -> Any:
        raise NotImplementedError

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise McpToolError("timeout", "RATTISH_MCP_TIMEOUT", "The MCP server took too long.")
    return left


def _rpc_result(message: Mapping[str, Any], method: str) -> Any:
    error = message.get("error")
    if isinstance(error, Mapping):
        text = str(error.get("message") or "error")
        raise McpToolError(
            "command",
            "RATTISH_MCP_PROTOCOL_ERROR",
            f"The MCP server refused {method}: {text}",
            {"rpc_code": error.get("code")},
        )
    return message.get("result")


class StdioSession(_Session):
    """JSON-RPC over a local server's standard input and output, one message per line."""

    def __init__(self, launch: Launch, cancel: threading.Event | None = None) -> None:
        self._launch = launch
        self._cancel = cancel or threading.Event()
        self._messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._stderr = b""
        self._next_id = 0
        self._write_lock = threading.Lock()
        try:
            self._process = subprocess.Popen(
                launch.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=launch.env,
                cwd=str(launch.cwd) if launch.cwd is not None else None,
                start_new_session=sys.platform != "win32",
            )
        except OSError as exc:
            launch.close()
            raise McpToolError(
                "command",
                "RATTISH_MCP_SERVER_START_FAILED",
                f"The MCP server couldn't start: {exc}",
            ) from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        stream = self._process.stdout
        assert stream is not None
        try:
            while True:
                line = stream.readline(MAX_MESSAGE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_MESSAGE_BYTES:
                    self._messages.put({"__too_large__": True})
                    break
                text = line.strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    continue  # Servers sometimes log to stdout; skip what isn't JSON-RPC.
                if isinstance(message, dict):
                    self._messages.put(message)
        except (OSError, ValueError):
            pass
        self._messages.put(None)

    def _read_stderr(self) -> None:
        stream = self._process.stderr
        assert stream is not None
        try:
            while chunk := stream.read1(4096):  # type: ignore[attr-defined]
                self._stderr = (self._stderr + chunk)[-STDERR_TAIL_BYTES:]
        except (OSError, ValueError):
            pass

    def stderr_tail(self) -> str:
        return self._stderr.decode("utf-8", "replace").strip()

    def _send(self, message: Mapping[str, Any]) -> None:
        data = json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n"
        stdin = self._process.stdin
        assert stdin is not None
        with self._write_lock:
            try:
                stdin.write(data.encode("utf-8"))
                stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise self._exited() from exc

    def _exited(self) -> McpToolError:
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._process.wait(timeout=1)
        code = self._process.poll()
        tail = self.stderr_tail()
        message = "The MCP server stopped before it answered"
        message += f" (exit code {code})." if code is not None else "."
        if tail:
            message += f" It said: {tail.splitlines()[-1][:500]}"
        return McpToolError(
            "command",
            "RATTISH_MCP_SERVER_EXITED",
            message,
            {"exit_code": code, "stderr": tail[-2000:]},
        )

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        self._send(message)

    def request(self, method: str, params: Mapping[str, Any] | None, deadline: float) -> Any:
        self._next_id += 1
        request_id = self._next_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = dict(params)
        self._send(message)
        while True:
            if self._cancel.is_set():
                raise McpToolError("cancelled", "RATTISH_MCP_CANCELLED", "The call was stopped.")
            try:
                reply = self._messages.get(timeout=min(0.2, _remaining(deadline)))
            except queue.Empty:
                continue
            if reply is None:
                self._messages.put(None)
                raise self._exited()
            if reply.get("__too_large__"):
                raise McpToolError(
                    "output_validation",
                    "RATTISH_MCP_MESSAGE_TOO_LARGE",
                    f"The MCP server sent a message over {MAX_MESSAGE_BYTES} bytes.",
                )
            if "method" in reply:
                self._answer_server(reply)
                continue
            if reply.get("id") == request_id:
                return _rpc_result(reply, method)

    def _answer_server(self, message: Mapping[str, Any]) -> None:
        """Reply to a server's own request: ping works, nothing else is offered."""
        if "id" not in message:
            return
        if message.get("method") == "ping":
            self._send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
            return
        self._send(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "error": {"code": -32601, "message": "Raticode workflows don't offer this."},
            }
        )

    def close(self) -> None:
        try:
            if self._process.stdin is not None:
                with contextlib.suppress(OSError):
                    self._process.stdin.close()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._signal_group()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._signal_group(kill=True)
                    self._process.wait(timeout=5)
        finally:
            self._launch.close()

    def _signal_group(self, *, kill: bool = False) -> None:
        if sys.platform == "win32":
            (self._process.kill if kill else self._process.terminate)()
            return
        import signal

        with contextlib.suppress(OSError):
            os.killpg(self._process.pid, signal.SIGKILL if kill else signal.SIGTERM)


class HttpSession(_Session):
    """MCP Streamable HTTP: each message is a POST, answered with JSON or an event stream."""

    def __init__(self, url: str, cancel: threading.Event | None = None) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise McpToolError(
                "configuration", "RATTISH_MCP_SERVER_INVALID", f"Invalid MCP server URL {url!r}."
            )
        self._parts = parts
        self._path = parts.path or "/"
        if parts.query:
            self._path += f"?{parts.query}"
        self._cancel = cancel or threading.Event()
        self._session_id: str | None = None
        self._protocol: str | None = None
        self._next_id = 0

    def _connection(self, timeout: float) -> http.client.HTTPConnection:
        host = self._parts.hostname or ""
        if self._parts.scheme == "https":
            return http.client.HTTPSConnection(host, self._parts.port, timeout=timeout)
        return http.client.HTTPConnection(host, self._parts.port, timeout=timeout)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "Raticode-workflow",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol:
            headers["MCP-Protocol-Version"] = self._protocol
        return headers

    def _post(self, message: Mapping[str, Any], deadline: float) -> Any:
        if self._cancel.is_set():
            raise McpToolError("cancelled", "RATTISH_MCP_CANCELLED", "The call was stopped.")
        body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        connection = self._connection(_remaining(deadline))
        try:
            connection.request("POST", self._path, body=body, headers=self._headers())
            response = connection.getresponse()
            session = response.getheader("Mcp-Session-Id")
            if session:
                self._session_id = session
            if "id" not in message:
                response.read(MAX_MESSAGE_BYTES)
                return None
            if response.status >= 400:
                text = response.read(2000).decode("utf-8", "replace").strip()
                raise McpToolError(
                    "network",
                    "RATTISH_MCP_HTTP_STATUS",
                    f"The MCP server answered HTTP {response.status}"
                    + (f": {text.splitlines()[0][:300]}" if text else "."),
                    {"status": response.status},
                )
            kind = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if kind == "text/event-stream":
                return self._read_events(response, message["id"], deadline)
            data = response.read(MAX_MESSAGE_BYTES + 1)
            if len(data) > MAX_MESSAGE_BYTES:
                raise McpToolError(
                    "output_validation",
                    "RATTISH_MCP_MESSAGE_TOO_LARGE",
                    f"The MCP server sent a message over {MAX_MESSAGE_BYTES} bytes.",
                )
            replies = json.loads(data.decode("utf-8"))
            for reply in replies if isinstance(replies, list) else [replies]:
                if isinstance(reply, dict) and reply.get("id") == message["id"]:
                    return reply
            raise McpToolError(
                "command", "RATTISH_MCP_PROTOCOL_ERROR", "The MCP server sent no answer."
            )
        except (OSError, http.client.HTTPException) as exc:
            if isinstance(exc, TimeoutError) or "timed out" in str(exc):
                raise McpToolError(
                    "timeout", "RATTISH_MCP_TIMEOUT", "The MCP server took too long."
                ) from exc
            raise McpToolError(
                "network",
                "RATTISH_MCP_CONNECTION_FAILED",
                f"Couldn't reach the MCP server: {exc}",
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise McpToolError(
                "command", "RATTISH_MCP_PROTOCOL_ERROR", "The MCP server sent invalid JSON."
            ) from exc
        finally:
            connection.close()

    def _read_events(
        self, response: http.client.HTTPResponse, request_id: Any, deadline: float
    ) -> Any:
        total = 0
        data_lines: list[str] = []
        while True:
            _remaining(deadline)
            if self._cancel.is_set():
                raise McpToolError("cancelled", "RATTISH_MCP_CANCELLED", "The call was stopped.")
            line = response.readline(MAX_MESSAGE_BYTES + 1)
            total += len(line)
            if not line or total > MAX_MESSAGE_BYTES:
                raise McpToolError(
                    "command", "RATTISH_MCP_PROTOCOL_ERROR", "The MCP server's stream ended early."
                )
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            if text.startswith("data:"):
                data_lines.append(text[5:].lstrip(" "))
                continue
            if text or not data_lines:
                continue
            payload, data_lines = "\n".join(data_lines), []
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("id") == request_id and "method" not in event:
                return event

    def request(self, method: str, params: Mapping[str, Any] | None, deadline: float) -> Any:
        self._next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            message["params"] = dict(params)
        reply = self._post(message, deadline)
        result = _rpc_result(reply, method)
        if method == "initialize" and isinstance(result, Mapping):
            version = result.get("protocolVersion")
            self._protocol = str(version) if isinstance(version, str) else PROTOCOL_VERSION
        return result

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        with contextlib.suppress(McpToolError):
            self._post(message, time.monotonic() + 10)

    def close(self) -> None:
        if not self._session_id:
            return
        with contextlib.suppress(OSError, http.client.HTTPException):
            connection = self._connection(5)
            try:
                connection.request("DELETE", self._path, headers=self._headers())
                connection.getresponse().read(1024)
            finally:
                connection.close()


@contextlib.contextmanager
def open_session(
    server: Mapping[str, Any],
    settings: SandboxSettings,
    *,
    data_dir: Path,
    scratch: Path,
    deadline: float,
    cancel: threading.Event | None = None,
    writable_cache: bool = True,
) -> Iterator[_Session]:
    """An initialized session with ``server``, closed (and its process stopped) on exit.

    Pass ``writable_cache=False`` for any start that carries workflow data.
    """
    session: _Session
    if is_local(server):
        launch = prepare_launch(
            server, settings, data_dir=data_dir, scratch=scratch, writable_cache=writable_cache
        )
        session = StdioSession(launch, cancel)
    else:
        session = HttpSession(str(server.get("url", "")), cancel)
    try:
        result = session.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            deadline,
        )
        if not isinstance(result, Mapping):
            raise McpToolError(
                "command", "RATTISH_MCP_PROTOCOL_ERROR", "The MCP server didn't initialize."
            )
        session.notify("notifications/initialized")
        yield session
    finally:
        session.close()


def list_session_tools(session: _Session, deadline: float) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(MAX_TOOL_PAGES):
        result = session.request("tools/list", {"cursor": cursor} if cursor else {}, deadline)
        if not isinstance(result, Mapping) or not isinstance(result.get("tools"), list):
            raise McpToolError(
                "command", "RATTISH_MCP_PROTOCOL_ERROR", "The MCP server sent no tool list."
            )
        for tool in result["tools"]:
            if isinstance(tool, Mapping) and isinstance(tool.get("name"), str):
                tools.append(
                    {
                        "name": tool["name"],
                        "title": str(tool.get("title") or ""),
                        "description": str(tool.get("description") or ""),
                        "inputSchema": dict(tool.get("inputSchema") or {"type": "object"}),
                        **(
                            {"outputSchema": dict(tool["outputSchema"])}
                            if isinstance(tool.get("outputSchema"), Mapping)
                            else {}
                        ),
                    }
                )
        if len(tools) >= MAX_TOOLS:
            return tools[:MAX_TOOLS]
        next_cursor = result.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            return tools
        cursor = next_cursor
    return tools


# ----------------------------------------------------------------------------
# Arguments


def _has_template(value: Any) -> bool:
    if isinstance(value, str):
        return "{{" in value or "}}" in value
    if isinstance(value, Mapping):
        return any(_has_template(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_template(item) for item in value)
    return False


# Errors about which keys are present, which a {{...}} value can't change.
_KEY_VALIDATORS = frozenset({"required", "additionalProperties", "dependentRequired"})


def argument_problems(
    input_schema: Mapping[str, Any], arguments: Any, *, templates: bool = False
) -> list[str]:
    """Why ``arguments`` don't fit a tool's input schema; empty when they do.

    With ``templates``, values that still hold ``{{...}}`` placeholders are only known
    at run time, so only problems they can't change are reported.
    """
    from jsonschema import Draft202012Validator, SchemaError  # type: ignore[import-untyped]
    from jsonschema.exceptions import best_match  # type: ignore[import-untyped]
    from referencing import Registry

    if not isinstance(arguments, Mapping):
        return ["Arguments must be a JSON object."]
    schema = dict(input_schema) if isinstance(input_schema, Mapping) else {}
    try:
        validator = Draft202012Validator(schema, registry=Registry())
        errors = list(validator.iter_errors(dict(arguments)))
    except (SchemaError, RecursionError, Exception):  # noqa: BLE001 - the server's own schema
        # A schema the validator can't use is the server's problem; the server checks.
        return []
    problems: list[str] = []
    for error in errors:
        if templates and error.validator not in _KEY_VALIDATORS and _has_template(error.instance):
            continue
        shown = best_match([error]) if error.context else error
        where = "/".join(str(part) for part in shown.absolute_path)
        problems.append(f"{where}: {shown.message}" if where else shown.message)
    return problems


# ----------------------------------------------------------------------------
# Listing and calling


@dataclass(frozen=True, slots=True)
class _Cached:
    at: float
    tools: list[dict[str, Any]] | None
    error: McpToolError | None


_list_cache: dict[tuple[Any, ...], _Cached] = {}
_list_lock = threading.Lock()


def _scratch(data_dir: Path, run_id: str | None) -> tuple[Path, bool]:
    if run_id:
        return agent_sandbox.run_scratch_dir(data_dir.resolve(), run_id), False
    name = f"mcp-list-{secrets.token_hex(6)}"
    return agent_sandbox.run_scratch_dir(data_dir.resolve(), name), True


def list_tools(
    server: Mapping[str, Any],
    settings: SandboxSettings,
    *,
    data_dir: Path,
    timeout_seconds: float = DEFAULT_LIST_TIMEOUT_SECONDS,
    use_cache: bool = False,
) -> list[dict[str, Any]]:
    """Start a session with ``server`` and return its tools, with their input schemas."""
    key = (definition_digest(server), settings.signature(), str(data_dir))
    if use_cache:
        with _list_lock:
            cached = _list_cache.get(key)
        if cached is not None:
            age = time.monotonic() - cached.at
            limit = LIST_CACHE_SECONDS if cached.error is None else FAILED_LIST_CACHE_SECONDS
            if age < limit:
                if cached.error is not None:
                    raise cached.error
                return [dict(tool) for tool in cached.tools or []]
    deadline = time.monotonic() + timeout_seconds
    try:
        tools = _list_only_start(server, settings, data_dir=data_dir, deadline=deadline)
    except McpToolError as exc:
        with _list_lock:
            _list_cache[key] = _Cached(time.monotonic(), None, exc)
        raise
    with _list_lock:
        _list_cache[key] = _Cached(time.monotonic(), tools, None)
    return tools


def _list_only_start(
    server: Mapping[str, Any],
    settings: SandboxSettings,
    *,
    data_dir: Path,
    deadline: float,
    cancel: threading.Event | None = None,
) -> list[dict[str, Any]]:
    """Start ``server`` with a writable package cache, list its tools and stop it.

    It carries no workflow data, so it may fill the lasting cache. Afterwards the cache
    is marked as filled for tool calls (``mcp_cache.listed``).
    """
    scratch, temporary = _scratch(data_dir, None)
    try:
        with open_session(
            server, settings, data_dir=data_dir, scratch=scratch, deadline=deadline, cancel=cancel
        ) as session:
            tools = list_session_tools(session, deadline)
    finally:
        if temporary:
            shutil.rmtree(scratch, ignore_errors=True)
    if is_local(server) and unsandboxed_reason(server, settings) is None:
        digest = definition_digest(server)
        if digest:
            mcp_cache.mark_listed(mcp_cache.cache_dir(data_dir, digest))
    return tools


def _fill_cache_for_call(
    server: Mapping[str, Any],
    settings: SandboxSettings,
    *,
    data_dir: Path,
    deadline: float,
    cancel: threading.Event | None,
) -> None:
    """Before a tool call reads an empty lasting cache, fill it with one list-only start.

    A failure here is left to the call itself to report.
    """
    if not is_local(server) or unsandboxed_reason(server, settings) is not None:
        return
    digest = definition_digest(server)
    if not digest or mcp_cache.listed(mcp_cache.cache_dir(data_dir, digest)):
        return
    with contextlib.suppress(McpToolError):
        _list_only_start(server, settings, data_dir=data_dir, deadline=deadline, cancel=cancel)


def clear_list_cache() -> None:
    with _list_lock:
        _list_cache.clear()


def find_tool(tools: list[dict[str, Any]], name: str, server_name: str) -> dict[str, Any]:
    for tool in tools:
        if tool["name"] == name:
            return tool
    names = ", ".join(tool["name"] for tool in tools[:40]) or "none"
    raise McpToolError(
        "configuration",
        "RATTISH_MCP_TOOL_UNKNOWN",
        f"MCP server {server_name!r} has no tool named {name!r}. Its tools: {names}.",
        {"server": server_name, "tool": name, "tools": [tool["name"] for tool in tools]},
    )


def tool_output(result: Any, *, structured: bool) -> Any:
    """A tool result as node output: its structured content, or its text.

    With ``structured`` (the step sets an output schema) text that holds JSON is parsed.
    """
    if not isinstance(result, Mapping):
        raise McpToolError(
            "command", "RATTISH_MCP_PROTOCOL_ERROR", "The MCP server sent no tool result."
        )
    raw_content = result.get("content")
    content: list[Any] = raw_content if isinstance(raw_content, list) else []
    text = "\n".join(
        str(item.get("text", ""))
        for item in content
        if isinstance(item, Mapping) and item.get("type") == "text"
    )
    if result.get("isError") is True:
        raise McpToolError(
            "command",
            "RATTISH_MCP_TOOL_ERROR",
            f"The tool reported an error: {text[:2000]}" if text else "The tool reported an error.",
            {"text": text[:20000]},
        )
    if "structuredContent" in result and result["structuredContent"] is not None:
        return result["structuredContent"]
    if structured:
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpToolError(
                "output_validation",
                "RATTISH_MCP_OUTPUT_NOT_JSON",
                "The step has an output schema, but the tool sent text that isn't JSON.",
                {"text": text[:2000]},
            ) from exc
    return text


def call_tool(
    server: Mapping[str, Any],
    settings: SandboxSettings,
    tool: str,
    arguments: Mapping[str, Any],
    *,
    data_dir: Path,
    run_id: str,
    timeout_seconds: float,
    structured: bool,
    cancel: threading.Event | None = None,
) -> Any:
    """Start a session, check the arguments against the tool's input schema and call it.

    The server reads its package cache but can't change it (``prepare_launch``).
    """
    scratch, _ = _scratch(data_dir, run_id)
    deadline = time.monotonic() + timeout_seconds
    _fill_cache_for_call(server, settings, data_dir=data_dir, deadline=deadline, cancel=cancel)
    with open_session(
        server,
        settings,
        data_dir=data_dir,
        scratch=scratch,
        deadline=deadline,
        cancel=cancel,
        writable_cache=False,
    ) as session:
        found = find_tool(list_session_tools(session, deadline), tool, str(server.get("name")))
        problems = argument_problems(found["inputSchema"], arguments)
        if problems:
            raise McpToolError(
                "configuration",
                "RATTISH_MCP_ARGUMENTS_INVALID",
                f"The arguments don't fit tool {tool!r}: " + "; ".join(problems[:5]),
                {"problems": problems[:20]},
            )
        result = session.request(
            "tools/call", {"name": tool, "arguments": dict(arguments)}, deadline
        )
    return tool_output(result, structured=structured)


# ----------------------------------------------------------------------------
# Reports for preflight and the Triggers section


def node_unsandboxed(
    node: Mapping[str, Any], data_dir: Path, *, platform: str | None = None
) -> list[dict[str, str]]:
    """The step's local server, when it would run unsandboxed, and why."""
    if node.get("runtime_handler") != "raticode.mcp_tool":
        return []
    configuration = node.get("configuration") or {}
    name = str(configuration.get("server") or "")
    try:
        server = library_server(data_dir, name)
    except McpToolError:
        return []
    settings = SandboxSettings(
        enabled=configuration.get("sandbox") != agent_sandbox.SANDBOX_OFF,
        network=agent_sandbox.step_network(configuration),
    )
    reason = unsandboxed_reason(server, settings, platform=platform)
    if reason is None:
        return []
    return [{"node": str(node["id"]), "provider": f"MCP server {name}", "reason": reason}]


def parse_network(values: list[str] | None) -> agent_sandbox.Network:
    """Hosts from a command line or query string: ``any``, or a list of host patterns."""
    hosts = [item.strip().lower().rstrip(".") for item in values or [] if item.strip()]
    if agent_sandbox.ANY_HOST in hosts:
        return agent_sandbox.ANY_HOST
    return hosts


def server_tools(
    data_dir: Path,
    name: str,
    *,
    network: agent_sandbox.Network | None = None,
    sandbox: bool = True,
    working_dir: Path | None = None,
    timeout_seconds: float = DEFAULT_LIST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """A server's tools as the Studio and ``Raticode rem mcp tools`` show them.

    The server starts as an ``mcp-tool`` step with these sandbox settings would start it.
    """
    server = library_server(data_dir, name)
    settings = SandboxSettings(
        enabled=sandbox, network=network if network is not None else [], working_dir=working_dir
    )
    tools = list_tools(server, settings, data_dir=data_dir, timeout_seconds=timeout_seconds)
    return {
        "server": name,
        "type": server["type"],
        "sandboxed": is_local(server) and unsandboxed_reason(server, settings) is None,
        "tools": tools,
    }
