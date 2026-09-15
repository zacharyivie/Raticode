from __future__ import annotations

import http.client
import json
from typing import Any
from urllib.parse import urlsplit

import pytest

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
