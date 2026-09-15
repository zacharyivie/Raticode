"""Turn-scoped, loopback MCP tools for app-managed swarm members."""

from __future__ import annotations

import hmac
import json
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MAX_REQUEST_BYTES = 1024 * 1024


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

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._grants: dict[str, _Grant] = {}
        self._slots = threading.BoundedSemaphore(8)
        self._closed = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                self.request.settimeout(5)
                super().setup()

            def log_message(self, format: str, *args: Any) -> None:
                # Request paths contain short-lived capabilities.
                return

            def respond(self, status: int, value: dict[str, Any] | None = None) -> None:
                data = json.dumps(value, ensure_ascii=False).encode() if value is not None else b""
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
                        if length < 0 or length > MAX_REQUEST_BYTES:
                            self.respond(413)
                            return
                        request = json.loads(self.rfile.read(length))
                    except (ValueError, UnicodeError):
                        self.respond(400)
                        return
                    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                        self.respond(400)
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
