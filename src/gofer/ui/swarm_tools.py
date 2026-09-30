"""Turn-scoped, loopback MCP tools for app-managed swarm members."""

from __future__ import annotations

import hmac
import json
import secrets
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MAX_REQUEST_BYTES = 1024 * 1024
MCP_JSON_MAX_DEPTH = 64
REQUEST_READ_DEADLINE_SECONDS = 5.0


@dataclass(frozen=True)
class _Grant:
    callback: Callable[[str, dict[str, Any]], Any]
    tools: list[dict[str, Any]]
    instructions: str


class SwarmToolServer:
    """Stateless Streamable HTTP MCP, with a fresh capability for each agent turn.

    URLs are invocation-only resources, never part of saved swarm configuration.
    The callback owns authorization for the bound agent, run, and current state.
    """

    def __init__(self, *, max_request_bytes: int = MAX_REQUEST_BYTES) -> None:
        self._lock = threading.Lock()
        self._grants: dict[str, _Grant] = {}
        self._slots = threading.BoundedSemaphore(8)
        self._closed = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                self.request.settimeout(5)
                super().setup()

            def handle_one_request(self) -> None:
                # Socket timeouts only bound inactivity. A fixed deadline also
                # releases connection slots when headers or bodies trickle in.
                def expire() -> None:
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

                self._read_timer = threading.Timer(REQUEST_READ_DEADLINE_SECONDS, expire)
                self._read_timer.daemon = True
                self._read_timer.start()
                try:
                    super().handle_one_request()
                except (
                    BrokenPipeError,
                    ConnectionAbortedError,
                    ConnectionResetError,
                    TimeoutError,
                ):
                    return
                finally:
                    self._read_timer.cancel()

            def log_message(self, format: str, *args: Any) -> None:
                # Request paths contain short-lived capabilities.
                return

            def respond(self, status: int, value: dict[str, Any] | None = None) -> None:
                # Preserve all JSON strings, including escaped lone surrogates,
                # without attempting to encode those code points as UTF-8.
                data = json.dumps(value).encode() if value is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                if data:
                    self.wfile.write(data)

            def do_GET(self) -> None:
                self.respond(405)

            def do_DELETE(self) -> None:
                self.respond(405)

            def do_POST(self) -> None:
                host = self.headers.get("Host", "")
                if host != owner._host or self.headers.get("Origin"):
                    self.respond(403)
                    return
                grant = owner._grant(self.path)
                if grant is None:
                    self.respond(401)
                    return
                if not owner._slots.acquire(blocking=False):
                    self.respond(503)
                    return
                try:
                    if self.headers.get("Transfer-Encoding"):
                        self.respond(400)
                        return
                    lengths = self.headers.get_all("Content-Length", [])
                    if len(lengths) != 1:
                        self.respond(411)
                        return
                    try:
                        length = int(lengths[0])
                        if length < 0 or length > max_request_bytes:
                            self.respond(413)
                            return
                        body = self.rfile.read(length)
                        # Tool callbacks may legitimately take longer than the
                        # request read budget. Their execution is not timed here.
                        self._read_timer.cancel()
                        if len(body) != length:
                            self.respond(400)
                            return
                        request = json.loads(body)
                        pending: list[tuple[Any, int]] = [(request, 0)]
                        while pending:
                            item, depth = pending.pop()
                            if depth > MCP_JSON_MAX_DEPTH:
                                raise ValueError("MCP request nesting is too deep")
                            if isinstance(item, dict):
                                pending.extend((value, depth + 1) for value in item.values())
                            elif isinstance(item, list):
                                pending.extend((value, depth + 1) for value in item)
                    except (ValueError, UnicodeError, RecursionError):
                        self.respond(400)
                        return
                    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                        self.respond(400)
                        return
                    # Reading a slow body may outlive the turn or server. Admit
                    # the complete request only while its original grant is live.
                    # Already-dispatched callbacks retain their own state checks.
                    if owner._grant(self.path) is not grant:
                        self.respond(401)
                        return
                    if "id" not in request:
                        self.respond(202)
                        return
                    result = owner._dispatch(grant, request)
                    self.respond(200, {"jsonrpc": "2.0", "id": request["id"], **result})
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    return
                finally:
                    owner._slots.release()

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

            def process_request(self, request: Any, client_address: Any) -> None:
                # Bound threads as well as active callbacks, including slow body readers.
                if not owner._connection_slots.acquire(blocking=False):
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, client_address)
                except BaseException:
                    owner._connection_slots.release()
                    raise

            def process_request_thread(self, request: Any, client_address: Any) -> None:
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    owner._connection_slots.release()

        self._connection_slots = threading.BoundedSemaphore(16)
        self._server = Server(("127.0.0.1", 0), Handler)
        self._host = f"127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="raticode-swarm-tools",
            daemon=True,
        )
        self._thread.start()

    def register(
        self,
        callback: Callable[[str, dict[str, Any]], Any],
        tools: list[dict[str, Any]],
        instructions: str = "",
    ) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            if self._closed:
                raise ValueError("Swarm tools are closed.")
            if len(self._grants) >= 128:
                raise ValueError("Too many active swarm tool sessions.")
            self._grants[token] = _Grant(callback, tools, instructions)
        return f"http://{self._host}/{token}"

    def revoke(self, url: str) -> None:
        with self._lock:
            self._grants.pop(url.rsplit("/", 1)[-1], None)

    def _grant(self, path: str) -> _Grant | None:
        token = path.removeprefix("/")
        if not token.isascii():
            return None
        with self._lock:
            for candidate, grant in self._grants.items():
                if hmac.compare_digest(candidate, token):
                    return grant
        return None

    @staticmethod
    def _dispatch(grant: _Grant, request: dict[str, Any]) -> dict[str, Any]:
        method = request.get("method")
        if method == "initialize":
            return {
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "raticode-swarm", "version": "1.0.0"},
                    "instructions": grant.instructions,
                }
            }
        if method == "ping":
            return {"result": {}}
        if method == "tools/list":
            return {"result": {"tools": grant.tools}}
        if method != "tools/call":
            return {"error": {"code": -32601, "message": "Method not found"}}
        params = request.get("params")
        if not isinstance(params, dict):
            return {"error": {"code": -32602, "message": "Expected tool parameters"}}
        name, arguments = params.get("name"), params.get("arguments", {})
        if (
            not isinstance(name, str)
            or name not in {tool["name"] for tool in grant.tools}
            or not isinstance(arguments, dict)
        ):
            return {"error": {"code": -32602, "message": "Unknown tool or invalid arguments"}}
        try:
            value = grant.callback(str(name), arguments)
            result: dict[str, Any] = {
                "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]
            }
        except (ValueError, KeyError) as exc:
            result = {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
        except Exception:
            result = {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": "Swarm tool failed. Retry or ask the orchestrator to inspect it.",
                    }
                ],
            }
        return {"result": result}

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._grants.clear()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
