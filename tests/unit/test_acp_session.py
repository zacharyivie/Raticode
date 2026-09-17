"""Synthetic ACP v1 and pinned Grok 37949780 session fixtures, no live models."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from gofer.subscriptions.acp_session import (
    initialize_session,
    prompt_session,
    session_text,
    wait_grok_mcp,
)
from gofer.subscriptions.acp_transport import AcpTransportError, open_acp_transport


def fake_agent(tmp_path: Path, body: str) -> list[str]:
    path = tmp_path / "session-agent.py"
    path.write_text(
        "import json, sys, time\n"
        "def read(): return json.loads(sys.stdin.readline())\n"
        "def send(message):\n"
        " print(json.dumps({'jsonrpc': '2.0', **message}), flush=True)\n" + body
    )
    return [sys.executable, str(path)]


def update(text: str, session: str = "s") -> dict[str, Any]:
    return {
        "method": "session/update",
        "params": {
            "sessionId": session,
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text},
            },
        },
    }


@pytest.mark.parametrize("exit_after_response", [False, True])
async def test_prompt_drains_final_text_and_retains_eof_error(
    tmp_path, exit_after_response, monkeypatch
):
    body = (
        "request = read()\n"
        "assert request['params']['prompt'] == [{'type': 'text', 'text': 'exact \\n prompt'}]\n"
        + "send("
        + repr(update("first "))
        + ")\n"
        + "send("
        + repr(update("last"))
        + ")\n"
        + "send({'id': request['id'], 'result': {'stopReason': 'end_turn'}})\n"
    )
    if not exit_after_response:
        body += "time.sleep(30)\n"
    command = fake_agent(tmp_path, body)
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        if exit_after_response:
            original_request = rpc.request

            async def delayed_response(*args, **kwargs):
                result = await original_request(*args, **kwargs)
                # Force the reader to observe EOF before releasing the result.
                await rpc.process.wait()
                await asyncio.sleep(0.01)
                return result

            monkeypatch.setattr(rpc, "request", delayed_response)
        events = [event async for event in prompt_session(rpc, "s", "exact \n prompt")]
    assert "".join(e["text"] for e in events if e["type"] == "thought") == "first last"
    assert events[-1]["message"]["body"] == "first last"
    assert events[-1]["type"] == ("error" if exit_after_response else "final")


@pytest.mark.parametrize("ending", ["malformed", "rpc_error", "cancelled", "timeout"])
async def test_prompt_failure_preserves_partial_output(tmp_path, ending):
    end = {
        "malformed": "print('{bad json', flush=True)\n",
        "rpc_error": "send({'id': request['id'], 'error': {'code': -32000, 'message': 'auth'}})\n",
        "cancelled": "send({'id': request['id'], 'result': {'stopReason': 'cancelled'}})\n",
        "timeout": "",
    }[ending]
    command = fake_agent(
        tmp_path,
        "request = read()\nsend(" + repr(update("partial")) + ")\n" + end + "time.sleep(30)\n",
    )
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        events = [e async for e in prompt_session(rpc, "s", "hello", timeout=0.3)]
    assert events[-1]["type"] == "error"
    assert events[-1]["message"]["body"] == "partial"
    assert events[-1]["exitCode"] == 1


@pytest.mark.parametrize("queued", ["malformed", "foreign"])
@pytest.mark.parametrize("response_pending", [True, False])
async def test_prompt_recovery_keeps_first_error_despite_invalid_queued_updates(
    tmp_path, monkeypatch, queued, response_pending
):
    malformed = update("placeholder")
    malformed["params"]["update"]["content"]["text"] = None
    invalid = {
        "malformed": malformed,
        "foreign": update("foreign text", session="other"),
    }[queued]
    command = fake_agent(
        tmp_path,
        "request = read()\n"
        + "".join(
            "send(" + repr(event) + ")\n"
            for event in [update("first"), update(" last"), malformed, invalid, update(" accepted")]
        )
        + "send({'id': request['id'], 'result': {'stopReason': 'end_turn'}})\n"
        + "time.sleep(30)\n",
    )
    response_received = asyncio.Event()
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        original_request = rpc.request

        async def pending_response(*args, **kwargs):
            result = await original_request(*args, **kwargs)
            response_received.set()
            if response_pending:
                await asyncio.Event().wait()
            return result

        monkeypatch.setattr(rpc, "request", pending_response)
        events = []
        async with asyncio.timeout(3):
            async for event in prompt_session(rpc, "s", "hello"):
                events.append(event)
                if event["type"] == "thought":
                    await response_received.wait()
                    # Let the request finish before consuming the buffered batch.
                    if not response_pending:
                        await asyncio.sleep(0)
    assert rpc.process.returncode is not None
    terminal = [event for event in events if event["type"] in {"error", "final"}]
    assert len(terminal) == 1
    assert terminal[0]["type"] == "error"
    assert terminal[0]["error"] == "ACP assistant text is malformed"
    assert terminal[0]["message"]["body"] == "first last accepted"
    assert terminal[0]["exitCode"] == 1
    assert (
        "".join(event["text"] for event in events if event["type"] == "thought")
        == "first last accepted"
    )


def test_session_text_rejects_malformed_parameters():
    with pytest.raises(AcpTransportError, match="parameters are malformed"):
        session_text({"method": "session/update", "params": None}, "s")


def test_session_identity_cannot_cross_provider_turns():
    with pytest.raises(AcpTransportError, match="another session"):
        session_text(update("foreign", session="other"), "s")


@pytest.mark.parametrize("http", [True, False])
async def test_initialize_checks_http_before_session_and_preserves_auth(tmp_path, http):
    params = {"cwd": str(tmp_path), "mcpServers": [{"type": "http", "name": "private"}]}
    command = fake_agent(
        tmp_path,
        "request = read()\n"
        "assert request['method'] == 'initialize'\n"
        "assert request['params']['clientCapabilities'] == {}\n"
        + "send({'id': request['id'], 'result': "
        + repr({"protocolVersion": 1, "agentCapabilities": {"mcpCapabilities": {"http": http}}})
        + "})\n"
        + "request = read()\n"
        + "assert request['method'] == 'session/new'\n"
        + "assert request['params'] == "
        + repr(params)
        + "\n"
        + "send({'id': request['id'], 'result': {'sessionId': 'fresh'}})\n"
        + "time.sleep(30)\n",
    )
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        if http:
            assert await initialize_session(rpc, params, require_http=True) == "fresh"
        else:
            with pytest.raises(AcpTransportError, match="HTTP MCP"):
                await initialize_session(rpc, params, require_http=True)


def server(**state: Any) -> dict[str, Any]:
    return {
        "name": "raticode-run-swarm",
        "type": "http",
        "url": "http://127.0.0.1:12345/mcp",
        "session": {
            "enabled": True,
            "status": "ready",
            "tools": [{"name": "swarm_action", "enabled": True}],
            **state,
        },
    }


@pytest.mark.parametrize(
    ("entry", "match"),
    [
        (server(blockedReason="private token must not leak"), "managed policy"),
        (server(authRequired=True), "authentication"),
        (server(setupRequired=True), "setup"),
        (server(enabled=False), "disabled"),
        (server(tools=[]), "required enabled tool"),
        ({**server(), "url": "http://wrong/mcp"}, "endpoint"),
        (
            {
                **server(),
                "url": "http://wrong/mcp",
                "source": "local",
                "type": "stdio",
                "command": "",
            },
            "endpoint",
        ),
        ({**server(), "url": None, "source": "local", "type": "stdio", "command": ""}, "endpoint"),
        ({**server(), "session": None}, "session state"),
        (server(status="unknown"), "unknown status"),
    ],
)
async def test_grok_readiness_failures_prevent_prompt(tmp_path, entry, match):
    command = fake_agent(
        tmp_path,
        "request = read()\n"
        "assert request['method'] == '_x.ai/mcp/list'\n"
        "assert request['params'] == {'sessionId': 's', 'cache': True}\n"
        + "send({'id': request['id'], 'result': "
        + repr({"result": {"servers": [entry]}})
        + "})\n"
        + "time.sleep(30)\n",
    )
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        with pytest.raises(AcpTransportError, match=match) as error:
            await wait_grok_mcp(rpc, "s", {entry["name"]: (server()["url"], {"swarm_action"})})
        assert "private token" not in str(error.value)


@pytest.mark.parametrize("placeholder", [False, True])
async def test_grok_waits_for_actual_session_tools(tmp_path, placeholder):
    entries = [server(status="initializing"), server()]
    if placeholder:
        for entry in entries:
            entry.pop("url")
            entry.update(source="local", type="stdio", command="")
    command = fake_agent(
        tmp_path,
        "entries = json.loads(" + repr(json.dumps(entries)) + ")\n"
        "for entry in entries:\n"
        " request = read()\n"
        " assert request['method'] == '_x.ai/mcp/list'\n"
        " send({'id': request['id'], 'result': {'result': {'servers': [entry]}}})\n"
        "time.sleep(30)\n",
    )
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        await wait_grok_mcp(rpc, "s", {server()["name"]: (server()["url"], {"swarm_action"})})


@pytest.mark.parametrize(
    ("response", "match"),
    [
        ({"error": {"code": -32601, "message": "Method not found"}}, "-32601"),
        ({"result": {"result": None, "error": {"message": "secret"}}}, "query failed"),
        ({"result": {"result": {"servers": None}}}, "malformed"),
        ({"result": {"result": {"servers": [server(), server()]}}}, "duplicate"),
        ({"result": {"result": {"servers": []}}}, "timed out"),
    ],
)
async def test_grok_readiness_rejects_missing_and_invalid_results(tmp_path, response, match):
    command = fake_agent(
        tmp_path,
        "while True:\n"
        " request = read()\n" + " send({'id': request['id'], **" + repr(response) + "})\n",
    )
    async with open_acp_transport(command, cwd=tmp_path) as rpc:
        with pytest.raises(AcpTransportError, match=match):
            await wait_grok_mcp(
                rpc, "s", {server()["name"]: (server()["url"], {"swarm_action"})}, timeout=0.2
            )
