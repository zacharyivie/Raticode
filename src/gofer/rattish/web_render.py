"""Render a page with an installed Chrome, Chromium or Edge for read-web-page's ``render``.

The browser runs headless with ``--dump-dom`` in a new, empty profile inside the run's
scratch folder, and the profile is deleted when the render ends. Every request the browser
makes, including loopback ones, goes through a local proxy that lives only for this render.
The proxy resolves each host itself, refuses it with the same ``public_address`` check and
network-allowlist the plain fetch uses, and connects to the checked address, so a page's
scripts can't reach this computer or the local network either. The proxy listens on
127.0.0.1 only and admits nothing a run of the node couldn't reach itself.
"""

from __future__ import annotations

import os
import selectors
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlsplit

from gofer.ui.link_previews import PreviewError, PrivateAddressError, public_address

Resolver = Callable[..., Any]

MAX_HEAD_BYTES = 64 * 1024
# All traffic through the proxy in one render, in both directions.
MAX_PROXY_BYTES = 200 * 1024 * 1024
# Chrome's own --timeout ends loading this long before the render's limit, so the page
# that has loaded so far is still printed.
MAX_TIMEOUT_GRACE_SECONDS = 5.0
STDERR_TAIL_BYTES = 8192
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-connection",
    "proxy-authorization",
    "proxy-authenticate",
    "te",
    "trailer",
    "upgrade",
}

# Chrome, then Chromium, then Edge. Linux and other Unix names are looked up on PATH.
_PATH_NAMES = (
    "google-chrome-stable",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "microsoft-edge-stable",
    "microsoft-edge",
)
_MAC_APPS = (
    "Google Chrome.app/Contents/MacOS/Google Chrome",
    "Chromium.app/Contents/MacOS/Chromium",
    "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
_WINDOWS_PATHS = (
    r"Google\Chrome\Application\chrome.exe",
    r"Chromium\Application\chrome.exe",
    r"Microsoft\Edge\Application\msedge.exe",
)

# Quiet, private headless flags. The browser's own sandbox stays on.
_BROWSER_FLAGS = (
    "--headless=new",
    "--dump-dom",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-sync",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-breakpad",
    "--disable-crash-reporter",
    "--disable-gpu",
    "--disable-quic",
    "--dns-prefetch-disable",
    "--deny-permission-prompts",
    "--password-store=basic",
    "--use-mock-keychain",
    "--mute-audio",
    "--hide-scrollbars",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
)


class RenderError(Exception):
    """The browser couldn't render the page. ``kind`` and ``code`` are Rattish's."""

    def __init__(self, kind: str, code: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.message = message


def find_browser() -> str | None:
    """The path of an installed Chrome, Chromium or Edge, or None."""
    if sys.platform == "darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            for app in _MAC_APPS:
                candidate = root / app
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
    elif sys.platform == "win32":
        roots = [
            os.environ.get(name, "")
            for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
        ]
        for relative in _WINDOWS_PATHS:
            for root in roots:
                if root and (Path(root) / relative).is_file():
                    return str(Path(root) / relative)
        for name in ("chrome", "msedge"):
            found = shutil.which(name)
            if found:
                return found
        return None
    for name in _PATH_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


@dataclass
class _ProxyState:
    allowlist: tuple[str, ...]
    resolver: Resolver
    deadline: float
    used_bytes: int = 0
    refused: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class GuardProxy:
    """A local HTTP proxy for one render that checks every host before connecting.

    It handles ``CONNECT`` tunnels (HTTPS and secure WebSockets) and plain ``http://``
    requests, which it sends on with ``Connection: close``. Refused hosts get 403 and are
    listed in ``refused``.
    """

    def __init__(self, *, allowlist: Iterable[str], resolver: Resolver, deadline: float) -> None:
        self.state = _ProxyState(tuple(allowlist), resolver, deadline)
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._listener.settimeout(0.2)
        self.port: int = self._listener.getsockname()[1]
        self._closed = threading.Event()
        self._sockets: set[socket.socket] = set()
        self._sockets_lock = threading.Lock()
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="read-web-page-proxy", daemon=True
        )

    @property
    def refused(self) -> list[str]:
        with self.state.lock:
            return list(self.state.refused)

    def start(self) -> GuardProxy:
        self._accept_thread.start()
        return self

    def close(self) -> None:
        self._closed.set()
        try:
            self._listener.close()
        except OSError:
            pass
        with self._sockets_lock:
            open_sockets = list(self._sockets)
            self._sockets.clear()
        for sock in open_sockets:
            _close_quietly(sock)
        self._accept_thread.join(timeout=2)

    def __enter__(self) -> GuardProxy:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # Connections ---------------------------------------------------------------------

    def _track(self, sock: socket.socket) -> bool:
        with self._sockets_lock:
            if self._closed.is_set():
                return False
            self._sockets.add(sock)
            return True

    def _untrack(self, sock: socket.socket) -> None:
        with self._sockets_lock:
            self._sockets.discard(sock)
        _close_quietly(sock)

    def _accept_loop(self) -> None:
        while not self._closed.is_set():
            try:
                client, _address = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            if not self._track(client):
                _close_quietly(client)
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _remaining(self) -> float:
        return max(self.state.deadline - time.monotonic(), 0.0)

    def _serve(self, client: socket.socket) -> None:
        upstream: socket.socket | None = None
        try:
            client.settimeout(self._remaining() or 0.01)
            head, rest = _read_head(client)
            if head is None:
                return
            request = _parse_head(head)
            if request is None:
                _reply(client, 400, "The proxy couldn't read the request.")
                return
            method, target, headers = request
            if method == "CONNECT":
                host, port = _split_authority(target, 443)
                path = ""
            else:
                try:
                    parsed = urlsplit(target)
                    host, port = parsed.hostname or "", parsed.port or 80
                except ValueError:
                    _reply(client, 400, "The proxy couldn't read the address.")
                    return
                if parsed.scheme.lower() != "http" or not host:
                    _reply(client, 400, "The proxy only forwards http:// requests.")
                    return
                path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
            if not host or not 0 < port < 65536:
                _reply(client, 400, "The proxy couldn't read the address.")
                return
            address = self._check(host, port)
            if address is None:
                _reply(client, 403, f"read-web-page refused {host}.")
                return
            try:
                upstream = socket.create_connection(
                    (address, port), timeout=self._remaining() or 0.01
                )
            except OSError:
                _reply(client, 502, f"{host} did not answer.")
                return
            if not self._track(upstream):
                _close_quietly(upstream)
                upstream = None
                return
            if method == "CONNECT":
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if rest:
                    upstream.sendall(rest)
            else:
                upstream.sendall(_origin_request(method, path, host, port, headers) + rest)
            self._pipe(client, upstream)
        except OSError:
            return
        finally:
            if upstream is not None:
                self._untrack(upstream)
            self._untrack(client)

    def _check(self, host: str, port: int) -> str | None:
        try:
            return public_address(host, port, self.state.resolver, allowlist=self.state.allowlist)
        except PrivateAddressError:
            with self.state.lock:
                if host not in self.state.refused:
                    self.state.refused.append(host)
            return None
        except (PreviewError, OSError, ValueError):
            return None

    def _pipe(self, client: socket.socket, upstream: socket.socket) -> None:
        """Copy bytes both ways until either side closes, the budget runs out or time's up."""
        peers = {client: upstream, upstream: client}
        with selectors.DefaultSelector() as selector:
            for sock in peers:
                sock.setblocking(False)
                selector.register(sock, selectors.EVENT_READ)
            while not self._closed.is_set():
                remaining = self._remaining()
                if remaining <= 0:
                    return
                for key, _events in selector.select(timeout=min(remaining, 0.5)):
                    source = key.fileobj
                    assert isinstance(source, socket.socket)
                    try:
                        data = source.recv(65536)
                    except (BlockingIOError, InterruptedError):
                        continue
                    if not data:
                        return
                    with self.state.lock:
                        self.state.used_bytes += len(data)
                        if self.state.used_bytes > MAX_PROXY_BYTES:
                            return
                    target = peers[source]
                    target.setblocking(True)
                    target.settimeout(remaining)
                    try:
                        target.sendall(data)
                    finally:
                        target.setblocking(False)


def _close_quietly(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass


def _read_head(sock: socket.socket) -> tuple[bytes | None, bytes]:
    data = b""
    while b"\r\n\r\n" not in data:
        if len(data) > MAX_HEAD_BYTES:
            return None, b""
        try:
            chunk = sock.recv(8192)
        except (TimeoutError, OSError):
            return None, b""
        if not chunk:
            return None, b""
        data += chunk
    head, _, rest = data.partition(b"\r\n\r\n")
    return head, rest


def _parse_head(head: bytes) -> tuple[str, str, list[tuple[str, str]]] | None:
    try:
        lines = head.decode("latin-1").split("\r\n")
        method, target, _version = lines[0].split(" ", 2)
    except ValueError:
        return None
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if not sep or not name.strip():
            return None
        headers.append((name.strip(), value.strip()))
    return method.upper(), target, headers


def _split_authority(target: str, default_port: int) -> tuple[str, int]:
    host, port_text = target, ""
    if target.startswith("["):
        end = target.find("]")
        host = target[1:end] if end > 0 else ""
        port_text = target[end + 2 :] if end > 0 and target[end + 1 : end + 2] == ":" else ""
    elif target.count(":") == 1:
        host, _, port_text = target.partition(":")
    try:
        port = int(port_text) if port_text else default_port
    except ValueError:
        return "", 0
    return host, port


def _origin_request(
    method: str, path: str, host: str, port: int, headers: list[tuple[str, str]]
) -> bytes:
    named = {
        token.strip().lower()
        for name, value in headers
        if name.lower() == "connection"
        for token in value.split(",")
    }
    lines = [f"{method} {path} HTTP/1.1"]
    has_host = False
    for name, value in headers:
        lowered = name.lower()
        if lowered in _HOP_BY_HOP or lowered in named:
            continue
        has_host = has_host or lowered == "host"
        lines.append(f"{name}: {value}")
    if not has_host:
        lines.append(f"Host: {host}" if port == 80 else f"Host: {host}:{port}")
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def _reply(sock: socket.socket, status: int, message: str) -> None:
    reasons = {400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway"}
    body = message.encode("utf-8")
    head = (
        f"HTTP/1.1 {status} {reasons.get(status, 'Error')}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    )
    try:
        sock.sendall(head.encode("latin-1") + body)
    except OSError:
        pass


# Browser process -------------------------------------------------------------------------


def _read_capped(stream: IO[bytes], limit: int, sink: list[bytes], tail: bool = False) -> None:
    """Keep the first (or, with ``tail``, last) ``limit`` bytes and drain the rest."""
    kept = b""
    try:
        while chunk := stream.read(65536):
            kept = (kept + chunk)[-limit:] if tail else kept + chunk[: max(limit - len(kept), 0)]
    except (OSError, ValueError):
        pass
    sink.append(kept)


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.kill()
    except OSError:
        pass


def _last_problem(stderr: bytes) -> str:
    lines = [line.strip() for line in stderr.decode("utf-8", "replace").splitlines()]
    for marker in ("FATAL", "ERROR"):
        for line in reversed(lines):
            if f":{marker}:" in line or line.startswith(marker):
                return line.rsplit("] ", 1)[-1][:300]
    return ""


def browser_timeout_ms(timeout_seconds: float) -> int:
    grace = min(MAX_TIMEOUT_GRACE_SECONDS, timeout_seconds / 4)
    return max(int((timeout_seconds - grace) * 1000), 1000)


def render_page(
    url: str,
    *,
    browser: str,
    scratch_dir: Path,
    allowlist: Iterable[str] = (),
    timeout_seconds: float = 30.0,
    resolver: Resolver = socket.getaddrinfo,
    max_bytes: int = 5 * 1024 * 1024,
    cancel: threading.Event | None = None,
) -> tuple[str, bool, list[str]]:
    """The page's DOM after scripts ran, whether it was cut at ``max_bytes``, refused hosts.

    Setting ``cancel`` stops the browser early and fails the render.
    """
    deadline = time.monotonic() + timeout_seconds
    scratch_dir.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="read-web-page-", dir=scratch_dir))
    try:
        with GuardProxy(allowlist=allowlist, resolver=resolver, deadline=deadline) as proxy:
            profile = workspace / "profile"
            profile.mkdir()
            args = [
                browser,
                *_BROWSER_FLAGS,
                f"--timeout={browser_timeout_ms(timeout_seconds)}",
                f"--user-data-dir={profile}",
                f"--proxy-server=http://127.0.0.1:{proxy.port}",
                # Without this the browser skips the proxy for localhost and 127.0.0.1.
                "--proxy-bypass-list=<-loopback>",
                url,
            ]
            env = dict(os.environ)
            # Crash reports and caches stay in the profile that is deleted afterwards.
            env["XDG_CONFIG_HOME"] = str(workspace / "config")
            env["XDG_CACHE_HOME"] = str(workspace / "cache")
            for name in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
                env.pop(name, None)
                env.pop(name.upper(), None)
            popen_options: dict[str, Any] = {}
            if sys.platform == "win32":
                popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_options["start_new_session"] = True
            try:
                process = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=workspace,
                    env=env,
                    **popen_options,
                )
            except OSError as exc:
                raise RenderError(
                    "configuration",
                    "RATTISH_WEB_PAGE_BROWSER_FAILED",
                    f"The browser at {browser} could not start: {exc.strerror or exc}.",
                ) from None
            assert process.stdout is not None and process.stderr is not None
            stdout: list[bytes] = []
            stderr: list[bytes] = []
            readers = [
                threading.Thread(
                    target=_read_capped, args=(process.stdout, max_bytes + 1, stdout), daemon=True
                ),
                threading.Thread(
                    target=_read_capped,
                    args=(process.stderr, STDERR_TAIL_BYTES, stderr, True),
                    daemon=True,
                ),
            ]
            for reader in readers:
                reader.start()
            timed_out = cancelled = False
            try:
                while process.poll() is None:
                    if cancel is not None and cancel.is_set():
                        cancelled = True
                        break
                    left = deadline - time.monotonic()
                    if left <= 0:
                        timed_out = True
                        break
                    try:
                        process.wait(timeout=min(left, 0.2))
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                # Helper processes can outlive the main one; none may keep the profile open.
                _kill_tree(process)
                process.wait()
                for reader in readers:
                    reader.join(timeout=5)
            refused = proxy.refused
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    if cancelled:
        raise RenderError("cancelled", "RATTISH_WEB_PAGE_CANCELLED", "The render was stopped.")
    if timed_out:
        raise RenderError(
            "timeout", "RATTISH_WEB_PAGE_TIMEOUT", "The browser took too long to render the page."
        )
    output = stdout[0] if stdout else b""
    if not output.strip():
        problem = _last_problem(stderr[0] if stderr else b"")
        detail = f": {problem}" if problem else "."
        raise RenderError(
            "network",
            "RATTISH_WEB_PAGE_BROWSER_FAILED",
            f"The browser returned no page (exit {process.returncode}){detail}",
        )
    truncated = len(output) > max_bytes
    return output[:max_bytes].decode("utf-8", errors="replace"), truncated, refused
