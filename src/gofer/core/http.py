from __future__ import annotations

import http.client
import socket
import ssl
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Protocol

import anyio

from gofer.core.network_policy import parse_http_request_url, resolve_http_request_target

HTTP_RESPONSE_MAX_BYTES = 16 * 1024 * 1024


class _RequestControl:
    """Close active I/O on cancellation or expiry, including slow header reads."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.connection: http.client.HTTPConnection | None = None
        self.transport: socket.socket | None = None
        self.closed = False

    def attach(self, connection: http.client.HTTPConnection) -> None:
        with self.lock:
            if self.closed:
                connection.close()
                raise TimeoutError("HTTP request deadline exceeded or request cancelled")
            self.connection = connection

    def close(self) -> None:
        with self.lock:
            self.closed = True
            connection = self.connection
            if connection is not None:
                sock = self.transport or getattr(connection, "sock", None)
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                connection.close()

    def set_transport(self, transport: socket.socket) -> None:
        with self.lock:
            if self.closed:
                transport.close()
                raise TimeoutError("HTTP request deadline exceeded or request cancelled")
            self.transport = transport


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    timeout_seconds: float = 30.0
    network_allowlist: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class HttpClient(Protocol):
    async def send(self, request: HttpRequest) -> HttpResponse:
        """Send an HTTP request and return the response."""


class UrllibHttpClient:
    async def send(self, request: HttpRequest) -> HttpResponse:
        control = _RequestControl()
        try:
            with anyio.fail_after(request.timeout_seconds):
                return await anyio.to_thread.run_sync(
                    self._send_sync, request, control, abandon_on_cancel=True
                )
        finally:
            control.close()

    def _send_sync(
        self, request: HttpRequest, control: _RequestControl | None = None
    ) -> HttpResponse:
        if request.timeout_seconds <= 0:
            raise ValueError("HTTP timeout must be positive")
        control = control or _RequestControl()
        deadline = time.monotonic() + request.timeout_seconds
        target = resolve_http_request_target(
            request.url,
            allowlist=request.network_allowlist,
        )
        parsed = urllib.parse.urlsplit(request.url)
        path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        headers = dict(request.headers)
        if parsed.scheme.lower() == "https":
            conn: _PolicyHttpConnection | _PolicyHttpsConnection = _PolicyHttpsConnection(
                target.host,
                target.connect_port,
                timeout=request.timeout_seconds,
            )
        else:
            conn = _PolicyHttpConnection(
                target.host,
                target.connect_port,
                timeout=request.timeout_seconds,
            )
        conn.set_policy_target(target.connect_host, target.connect_port)
        conn._gofer_control = control
        control.attach(conn)
        timer = threading.Timer(max(0, deadline - time.monotonic()), control.close)
        timer.daemon = True
        timer.start()
        try:
            try:
                conn.request(
                    request.method.upper(),
                    path,
                    body=request.body,
                    headers=headers,
                )
            except http.client.InvalidURL:
                # The stdlib quotes invalid paths in these errors.
                # Paths and headers can contain credentials even without a
                # declared secret reference, and callers persist error messages.
                raise http.client.InvalidURL("Invalid HTTP request URL path") from None
            except ValueError:
                # Also covers invalid methods, header names/values and encoding.
                raise ValueError("Invalid HTTP request method, headers or encoding") from None
            control.transport = getattr(conn, "sock", None)
            response = conn.getresponse()
            response_headers = dict(response.headers.items())
            # The parser accounts for HEAD, bodyless statuses and chunked framing.
            # Content-Length alone may describe a representation we will not read.
            remaining = response.length
            if remaining is not None and remaining > HTTP_RESPONSE_MAX_BYTES:
                raise ValueError("HTTP response exceeds the 16 MiB response limit")
            body = bytearray()
            while True:
                if control.closed or time.monotonic() >= deadline:
                    raise TimeoutError("HTTP request deadline exceeded or request cancelled")
                chunk = response.read1(min(64 * 1024, HTTP_RESPONSE_MAX_BYTES + 1 - len(body)))
                if not chunk:
                    # read1 deliberately tolerates EOF before Content-Length,
                    # unlike read(). Do not pass that partial body to the workflow.
                    if response.length:
                        raise http.client.IncompleteRead(bytes(body), response.length)
                    break
                body.extend(chunk)
                if len(body) > HTTP_RESPONSE_MAX_BYTES:
                    raise ValueError("HTTP response exceeds the 16 MiB response limit")
            if control.closed or time.monotonic() >= deadline:
                raise TimeoutError("HTTP request deadline exceeded or request cancelled")
            return HttpResponse(
                status=response.status,
                headers=response_headers,
                body=bytes(body),
            )
        finally:
            timer.cancel()
            conn.close()


class _PolicyHttpConnection(http.client.HTTPConnection):
    _gofer_connect_host: str
    _gofer_connect_port: int
    _gofer_control: _RequestControl

    def set_policy_target(self, host: str, port: int) -> None:
        self._gofer_connect_host = host
        self._gofer_connect_port = port

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._gofer_connect_host, self._gofer_connect_port),
            self.timeout,
            getattr(self, "source_address", None),
        )
        self._gofer_control.set_transport(self.sock)


class _PolicyHttpsConnection(http.client.HTTPSConnection):
    _gofer_connect_host: str
    _gofer_connect_port: int
    _gofer_control: _RequestControl

    def set_policy_target(self, host: str, port: int) -> None:
        self._gofer_connect_host = host
        self._gofer_connect_port = port

    def connect(self) -> None:
        sock = socket.create_connection(
            (self._gofer_connect_host, self._gofer_connect_port),
            self.timeout,
            getattr(self, "source_address", None),
        )
        self._gofer_control.set_transport(sock)
        context = getattr(self, "_context", None)
        if context is None:
            context = ssl.create_default_context()
        self.sock = context.wrap_socket(
            sock, server_hostname=self.host, do_handshake_on_connect=False
        )
        self._gofer_control.set_transport(self.sock)
        self.sock.do_handshake()


def read_response_bytes(response: http.client.HTTPResponse, max_bytes: int) -> bytes:
    """Read at most limit + 1 bytes, rejecting an incomplete HTTP body.

    Callers retain their own size-limit errors when the extra byte is present.
    Bounded HTTPResponse.read() tolerates early EOF with Content-Length, so
    check the parser's remaining length before accepting an in-limit body.
    """
    try:
        data = response.read(max_bytes + 1)
    except http.client.IncompleteRead:
        # Keep framing errors recoverable by UI/MCP callers without exposing
        # partial response contents, which may contain private account data.
        raise ValueError("Incomplete HTTP response") from None
    if len(data) <= max_bytes and getattr(response, "length", None):
        raise ValueError("Incomplete HTTP response")
    return data


def append_query_params(url: str, params: dict[str, str]) -> str:
    if not params:
        return url
    parsed = parse_http_request_url(url)
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query_pairs.extend((key, value) for key, value in params.items())
    return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query_pairs)))
