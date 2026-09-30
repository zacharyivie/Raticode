from __future__ import annotations

import http.client
import io
import json
import socket
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import pytest

from gofer.ui import swarm_tools
from gofer.ui.swarm_tools import MAX_REQUEST_BYTES, SwarmToolServer


@pytest.fixture
def tool_server() -> Any:
    server = SwarmToolServer()
    yield server
    server.close()


def request(url: str, body: Any, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    endpoint = urlsplit(url)
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=2)
    try:
        connection.request("POST", endpoint.path, json.dumps(body), headers or {})
        response = connection.getresponse()
        data = response.read()
        return response.status, json.loads(data) if data else None
    finally:
        connection.close()


@pytest.fixture
def memory_tool_server(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run the HTTP parser and handler without opening an OS socket."""

    def initialize(server: Any, address: Any, handler: Any) -> None:
        server.server_address = address
        server.server_port = 12345
        server.RequestHandlerClass = handler

    monkeypatch.setattr(swarm_tools.ThreadingHTTPServer, "__init__", initialize)
    for method in ("serve_forever", "shutdown", "server_close"):
        monkeypatch.setattr(swarm_tools.ThreadingHTTPServer, method, lambda *_args, **_kw: None)
    server = SwarmToolServer()
    yield server
    server.close()


def memory_request(server: SwarmToolServer, url: str, body: bytes) -> tuple[int, Any]:
    endpoint = urlsplit(url)

    class Connection:
        def __init__(self) -> None:
            self.output = bytearray()

        def settimeout(self, _timeout: float) -> None:
            pass

        def makefile(self, _mode: str, _buffering: int) -> io.BytesIO:
            return io.BytesIO(
                f"POST {endpoint.path} HTTP/1.1\r\nHost: {endpoint.netloc}\r\n"
                f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )

        def sendall(self, data: bytes) -> None:
            self.output.extend(data)

    connection = Connection()
    server._server.RequestHandlerClass(connection, ("127.0.0.1", 12346), server._server)
    headers, raw = bytes(connection.output).split(b"\r\n\r\n", 1)
    return int(headers.split(b" ")[1]), json.loads(raw) if raw else None


def test_turn_mcp_lifecycle_and_scoped_tool_calls(tool_server: SwarmToolServer) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((name, arguments))
        return {"messages": [{"text": "Ready for review"}]}

    definitions = [{"name": "read_board", "inputSchema": {"type": "object"}}]
    url = tool_server.register(call, definitions, "You are the reviewer.")
    status, result = request(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert status == 200
    assert result["result"]["instructions"] == "You are the reviewer."
    assert result["result"]["capabilities"] == {"tools": {}}
    assert request(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}) == (202, None)
    _, result = request(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert result["result"]["tools"] == definitions
    _, result = request(
        url,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "read_board", "arguments": {}},
        },
    )
    assert (
        json.loads(result["result"]["content"][0]["text"])["messages"][0]["text"]
        == "Ready for review"
    )
    assert calls == [("read_board", {})]
    tool_server.revoke(url)
    assert request(url, {"jsonrpc": "2.0", "id": 4, "method": "tools/list"}) == (401, None)


def test_turn_capability_is_not_interchangeable(tool_server: SwarmToolServer) -> None:
    tools = [{"name": "identity", "inputSchema": {"type": "object"}}]
    a = tool_server.register(lambda _name, _args: "a", tools)
    b = tool_server.register(lambda _name, _args: "b", tools)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "identity"}}
    assert json.loads(request(a, body)[1]["result"]["content"][0]["text"]) == "a"
    assert json.loads(request(b, body)[1]["result"]["content"][0]["text"]) == "b"
    tool_server.revoke(a)
    assert request(a, body)[0] == 401
    assert request(b, body)[0] == 200


def test_tools_reject_browser_origin_and_unknown_tools(tool_server: SwarmToolServer) -> None:
    url = tool_server.register(lambda _name, _args: pytest.fail("must not be called"), [])
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "anything"}}
    assert request(url, body, {"Origin": "https://example.com"})[0] == 403
    assert request(url, body, {"Host": "evil.example"})[0] == 403
    assert request(url, body)[1]["error"]["code"] == -32602
    assert request(url, [], {})[0] == 400
    assert request(url, body, {"Content-Length": str(MAX_REQUEST_BYTES + 1)})[0] == 413


@pytest.mark.parametrize("identifier", ["\ud800", "\udfff", "café 🐀"])
def test_mcp_unicode_ids_remain_recoverable(
    memory_tool_server: SwarmToolServer, identifier: str
) -> None:
    tool_server = memory_tool_server
    url = tool_server.register(lambda _name, _args: "Ready", [{"name": "read"}])
    status, reply = memory_request(
        tool_server,
        url,
        json.dumps({"jsonrpc": "2.0", "id": identifier, "method": "ping"}).encode(),
    )
    assert status == 200
    assert reply == {"jsonrpc": "2.0", "id": identifier, "result": {}}
    status, reply = memory_request(
        tool_server,
        url,
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "read"},
            }
        ).encode(),
    )
    assert status == 200
    assert reply["id"] == 2
    assert json.loads(reply["result"]["content"][0]["text"]) == "Ready"


@pytest.mark.parametrize("depth", [65, 2000])
def test_deep_mcp_requests_release_slots_without_dispatch(
    memory_tool_server: SwarmToolServer, depth: int
) -> None:
    server = memory_tool_server
    calls: list[str] = []
    url = server.register(lambda name, _args: calls.append(name), [{"name": "write"}])
    nested = "[" * depth + "0" + "]" * depth
    body = (
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"write",'
        '"arguments":{"nested":' + nested + "}}}"
    ).encode()
    assert memory_request(server, url, body) == (400, None)
    assert not calls
    assert memory_request(server, url, b'{"jsonrpc":"2.0","id":2,"method":"ping"}') == (
        200,
        {"jsonrpc": "2.0", "id": 2, "result": {}},
    )
    for _ in range(8):
        assert server._slots.acquire(blocking=False)
    for _ in range(8):
        server._slots.release()


def test_tool_validation_failure_remains_a_tool_result(tool_server: SwarmToolServer) -> None:
    def denied(_name: str, _args: dict[str, Any]) -> None:
        raise ValueError("Only the orchestrator may accept milestones.")

    url = tool_server.register(denied, [{"name": "accept"}])
    status, result = request(
        url, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "accept"}}
    )
    assert status == 200
    assert result["result"]["isError"] is True
    assert "orchestrator" in result["result"]["content"][0]["text"]


@pytest.mark.parametrize("invalidate", ["revoke", "close"])
def test_pending_request_cannot_outlive_its_grant(
    tool_server: SwarmToolServer, monkeypatch: pytest.MonkeyPatch, invalidate: str
) -> None:
    calls: list[str] = []
    url = tool_server.register(lambda name, _args: calls.append(name), [{"name": "write"}])
    authorized = threading.Event()
    original = tool_server._grant

    def observe_grant(path: str) -> Any:
        grant = original(path)
        authorized.set()
        return grant

    monkeypatch.setattr(tool_server, "_grant", observe_grant)
    endpoint = urlsplit(url)
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "write"}}
    ).encode()
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=2)
    try:
        connection.putrequest("POST", endpoint.path)
        connection.putheader("Content-Length", str(len(body)))
        connection.endheaders()
        assert authorized.wait(timeout=2)
        if invalidate == "revoke":
            tool_server.revoke(url)
        else:
            tool_server.close()
        connection.send(body)
        response = connection.getresponse()
        response.read()
        assert response.status == 401
        assert calls == []
    finally:
        connection.close()


def test_truncated_request_cannot_dispatch_valid_json(tool_server: SwarmToolServer) -> None:
    calls: list[str] = []
    url = tool_server.register(lambda name, _args: calls.append(name), [{"name": "write"}])
    endpoint = urlsplit(url)
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "write"}}
    ).encode()
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=2)
    try:
        connection.request("POST", endpoint.path, body, {"Content-Length": str(len(body) + 1)})
        assert connection.sock is not None
        connection.sock.shutdown(socket.SHUT_WR)
        response = connection.getresponse()
        response.read()
        assert response.status == 400
        assert calls == []
    finally:
        connection.close()


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_trickled_request_releases_connection_and_callback_slots(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    monkeypatch.setattr(swarm_tools, "REQUEST_READ_DEADLINE_SECONDS", 0.2)
    server = SwarmToolServer()
    calls: list[str] = []
    url = server.register(lambda name, _args: calls.append(name), [{"name": "write"}])
    endpoint = urlsplit(url)
    connection = socket.create_connection((str(endpoint.hostname), int(endpoint.port)), timeout=2)
    stop = threading.Event()

    def trickle() -> None:
        while not stop.wait(0.02):
            try:
                connection.sendall(b" ")
            except OSError:
                return

    sender = threading.Thread(target=trickle)
    try:
        prefix = f"POST {endpoint.path} HTTP/1.1\r\nHost: {endpoint.netloc}\r\n"
        if phase == "headers":
            prefix += "X-Slow: "
        else:
            prefix += "Content-Length: 100000\r\n\r\n"
        connection.sendall(prefix.encode())
        sender.start()
        # No inactivity timeout can fire while the sender supplies bytes.
        connection.settimeout(1)
        try:
            while connection.recv(4096):
                pass
        except ConnectionResetError:
            pass
        except TimeoutError:
            pytest.fail("Trickled request outlived its absolute read deadline")
        stop.set()
        sender.join(timeout=2)
        assert calls == []
        # Acquire the complete capacity to prove the stalled reader released it.
        for slots, count in [(server._slots, 8), (server._connection_slots, 16)]:
            acquired = 0
            try:
                for _ in range(count):
                    assert slots.acquire(timeout=1)
                    acquired += 1
            finally:
                for _ in range(acquired):
                    slots.release()
        assert request(url, {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0] == 200
    finally:
        stop.set()
        if sender.ident is not None:
            sender.join(timeout=2)
        connection.close()
        server.close()


def test_read_deadline_does_not_interrupt_a_dispatched_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(swarm_tools, "REQUEST_READ_DEADLINE_SECONDS", 0.1)
    server = SwarmToolServer()

    def call(_name: str, _args: dict[str, Any]) -> str:
        time.sleep(0.3)
        return "completed"

    try:
        url = server.register(call, [{"name": "wait"}])
        status, response = request(
            url,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "wait"}},
        )
        assert status == 200
        assert json.loads(response["result"]["content"][0]["text"]) == "completed"
    finally:
        server.close()
