"""Session lifecycle for the Grok ACP adapter.

ACP v1 uses newline JSON-RPC, session/update text chunks and a prompt response.
Provider adapters supply source-verified configuration and permission handling;
this layer never authenticates, retries a prompt, or resumes a foreign session.
"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from gofer.subscriptions.acp_transport import AcpTransport, AcpTransportError


def session_text(event: dict[str, Any], session_id: str) -> str:
    """Preserve text chunks in wire order; never use display text as tool identity."""
    if event.get("method") != "session/update":
        return ""
    params = event.get("params", {})
    if not isinstance(params, dict):
        raise AcpTransportError("ACP session update parameters are malformed")
    if params.get("sessionId") != session_id:
        raise AcpTransportError("ACP update belongs to another session")
    update = params.get("update")
    if not isinstance(update, dict):
        raise AcpTransportError("ACP session update is malformed")
    if update.get("sessionUpdate") != "agent_message_chunk":
        return ""
    content = update.get("content")
    if not isinstance(content, dict):
        raise AcpTransportError("ACP assistant content is malformed")
    if content.get("type") != "text":
        return ""
    text = content.get("text")
    if not isinstance(text, str):
        raise AcpTransportError("ACP assistant text is malformed")
    return text


def _session_trace(
    event: dict[str, Any], tools: dict[str, dict[str, Any]], stream_id: str
) -> dict[str, Any] | None:
    # session_text validates the session and update before this is called.
    if event.get("method") != "session/update":
        return None
    update = event["params"]["update"]
    kind = update.get("sessionUpdate")
    if kind == "agent_thought_chunk":
        content = update.get("content")
        if (
            isinstance(content, dict)
            and content.get("type") == "text"
            and isinstance(content.get("text"), str)
        ):
            return {
                "type": "thought",
                "text": content["text"],
                "deltaStreamId": f"reasoning-{stream_id}",
            }
    if kind not in {"tool_call", "tool_call_update"}:
        return None
    identity = update.get("toolCallId")
    if not isinstance(identity, str) or not identity:
        raise AcpTransportError("ACP tool update has no tool identity")
    previous = tools.get(identity, {})
    current = {
        **previous,
        **{
            key: value
            for key, value in update.items()
            if value is not None and key != "sessionUpdate"
        },
    }
    if current == previous:
        return None
    tools[identity] = current
    title = str(current.get("title") or current.get("name") or "Tool")
    output = current.get("rawOutput")
    if output is None:
        output = "\n".join(
            item["content"]["text"]
            for item in current.get("content", [])
            if isinstance(item, dict)
            and isinstance(item.get("content"), dict)
            and item["content"].get("type") == "text"
            and isinstance(item["content"].get("text"), str)
        )

    def display(value: Any) -> str:
        if value is None:
            return ""
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    return {
        "type": "thought",
        "text": title,
        "trace": {
            "kind": "tool",
            "id": identity,
            "title": title,
            "category": current.get("kind", ""),
            "status": current.get("status", "pending"),
            "input": display(current.get("rawInput")),
            "output": display(output),
        },
    }


async def initialize_session(
    rpc: AcpTransport,
    params: dict[str, Any],
    *,
    require_http: bool = False,
    session_id: str | None = None,
    timeout: float = 30,
) -> str:
    """Create or restore an owned session with current resources and CLI auth."""
    hello = await rpc.request(
        "initialize",
        {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {"name": "raticode", "version": "1"},
        },
        timeout=timeout,
    )
    if type(hello.get("protocolVersion")) is not int or hello["protocolVersion"] != 1:
        raise AcpTransportError("Provider does not support ACP protocol version 1")
    capabilities = hello.get("agentCapabilities", {})
    mcp = capabilities.get("mcpCapabilities", {}) if isinstance(capabilities, dict) else {}
    if require_http and (not isinstance(mcp, dict) or mcp.get("http") is not True):
        raise AcpTransportError("Provider does not support per-session HTTP MCP injection")
    if session_id:
        session_caps = (
            capabilities.get("sessionCapabilities", {}) if isinstance(capabilities, dict) else {}
        )
        if isinstance(session_caps, dict) and isinstance(session_caps.get("resume"), dict):
            method = "session/resume"
        elif isinstance(capabilities, dict) and capabilities.get("loadSession") is True:
            method = "session/load"
        else:
            raise AcpTransportError("Provider cannot load the saved ACP session; update its CLI")
        await rpc.request(method, {**params, "sessionId": session_id}, timeout=timeout)
        # ACP load replays history to the client before replying. It is already
        # in Rem's transcript, and must not become this turn's output.
        rpc.drain_notifications()
        return session_id
    session = await rpc.request("session/new", params, timeout=timeout)
    identity = session.get("sessionId")
    if not isinstance(identity, str) or not identity:
        raise AcpTransportError("Provider returned no ACP session identity")
    return identity


async def prompt_session(
    rpc: AcpTransport,
    session_id: str,
    prompt: str,
    *,
    timeout: float | None = None,
    image_paths: list[Path] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Consume a single prompt, preserving buffered output before terminal errors."""
    text = ""
    # ACP text updates are deltas, not independent timeline thoughts.
    stream_id = uuid4().hex
    tools: dict[str, dict[str, Any]] = {}
    content = [{"type": "text", "text": prompt}]
    for path in image_paths or []:
        content.append(
            {
                "type": "image",
                "mimeType": mimetypes.guess_type(path.name)[0] or "image/png",
                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            }
        )
    request = asyncio.create_task(
        rpc.request(
            "session/prompt",
            {"sessionId": session_id, "prompt": content},
            timeout=timeout,
        )
    )
    notification: asyncio.Task[dict[str, Any]] | None = None
    try:
        try:
            while not request.done():
                notification = asyncio.create_task(rpc.next_notification())
                await asyncio.wait({request, notification}, return_when=asyncio.FIRST_COMPLETED)
                if notification.done():
                    event = notification.result()
                    chunk = session_text(event, session_id)
                    if trace := _session_trace(event, tools, stream_id):
                        yield trace
                    if chunk:
                        text += chunk
                        yield {"type": "thought", "text": chunk, "deltaStreamId": stream_id}
                else:
                    notification.cancel()
                    await asyncio.gather(notification, return_exceptions=True)
                notification = None
            # The response reader enqueues preceding notifications synchronously.
            # Drain before checking EOF/errors or awaiting a failed prompt result.
            buffered_error: AcpTransportError | None = None
            for event in rpc.drain_notifications():
                try:
                    chunk = session_text(event, session_id)
                    trace = _session_trace(event, tools, stream_id)
                except AcpTransportError as exc:
                    if buffered_error is None:
                        buffered_error = exc
                    continue
                if trace:
                    yield trace
                if chunk:
                    text += chunk
                    yield {"type": "thought", "text": chunk, "deltaStreamId": stream_id}
            if buffered_error is not None:
                raise buffered_error
            result = await request
            rpc.raise_if_failed()
            if result.get("stopReason") != "end_turn":
                raise AcpTransportError("ACP prompt did not complete with end_turn")
        except AcpTransportError as exc:
            # Preserve the first failure even if queued updates are also invalid.
            # Valid chunks from this session remain useful; foreign text never is.
            for event in rpc.drain_notifications():
                try:
                    chunk = session_text(event, session_id)
                    trace = _session_trace(event, tools, stream_id)
                except AcpTransportError:
                    continue
                if trace:
                    yield trace
                if chunk:
                    text += chunk
                    yield {"type": "thought", "text": chunk, "deltaStreamId": stream_id}
            yield {
                "type": "error",
                "error": str(exc),
                "message": {"role": "assistant", "body": text},
                "sessionId": session_id,
                "exitCode": 1,
            }
        else:
            yield {
                "type": "final",
                "error": None,
                "message": {"role": "assistant", "body": text},
                "sessionId": session_id,
                "exitCode": 0,
            }
    finally:
        tasks = [request] + ([notification] if notification is not None else [])
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def wait_grok_mcp(
    rpc: AcpTransport,
    session_id: str,
    expected: dict[str, tuple[str, set[str]]],
    *,
    timeout: float = 30,
) -> None:
    """Gate HTTP plugin readiness using Grok 37949780's session-scoped extension.

    Keys are exact generated server aliases; values contain the expected URL and
    required unqualified tool names. Never report readiness from session/new alone.
    """
    if not expected:
        return
    try:
        async with asyncio.timeout(timeout):
            while True:
                # ACP extension methods need the leading underscore on the wire.
                payload = await rpc.request(
                    "_x.ai/mcp/list", {"sessionId": session_id, "cache": True}, timeout=timeout
                )
                result = payload.get("result")
                if payload.get("error") or not isinstance(result, dict):
                    raise AcpTransportError("Grok MCP readiness query failed")
                servers = result.get("servers")
                if not isinstance(servers, list):
                    raise AcpTransportError("Grok MCP readiness response is malformed")
                ready: set[str] = set()
                seen: set[str] = set()
                for server in servers:
                    if not isinstance(server, dict):
                        raise AcpTransportError("Grok MCP server entry is malformed")
                    name = server.get("name")
                    if not isinstance(name, str) or name not in expected:
                        continue
                    if name in seen:
                        raise AcpTransportError("Grok MCP readiness contains duplicate servers")
                    seen.add(name)
                    url, tools = expected[name]
                    # Grok represents session-only plugin transports as an empty
                    # stdio placeholder, even for HTTP. The unique per-run alias
                    # binds that entry to our injected config; check URLs when given.
                    placeholder = (
                        server.get("source") == "local"
                        and server.get("type") == "stdio"
                        and server.get("command") == ""
                        and "url" not in server
                        and not server.get("args")
                        and not server.get("env")
                    )
                    if server.get("url") != url and not placeholder:
                        raise AcpTransportError("Grok MCP endpoint does not match this run")
                    state = server.get("session")
                    if not isinstance(state, dict):
                        raise AcpTransportError("Grok MCP response has no session state")
                    if state.get("blockedReason"):
                        raise AcpTransportError("Grok MCP server is blocked by managed policy")
                    if state.get("authRequired"):
                        raise AcpTransportError("Grok MCP server requires authentication")
                    if state.get("setupRequired") or state.get("status") == "setuprequired":
                        raise AcpTransportError("Grok MCP server requires setup")
                    if state.get("enabled") is not True or state.get("status") == "unavailable":
                        raise AcpTransportError("Grok MCP server is unavailable or disabled")
                    if state.get("status") == "initializing":
                        continue
                    if state.get("status") != "ready":
                        raise AcpTransportError("Grok MCP server returned an unknown status")
                    available = state.get("tools")
                    if not isinstance(available, list) or not tools.issubset(
                        {
                            item["name"]
                            for item in available
                            if isinstance(item, dict)
                            and isinstance(item.get("name"), str)
                            and item.get("enabled") is True
                        }
                    ):
                        raise AcpTransportError(
                            "Grok MCP server is missing a required enabled tool"
                        )
                    ready.add(name)
                if ready == expected.keys():
                    return
                await asyncio.sleep(0.05)
    except TimeoutError as exc:
        raise AcpTransportError(
            "Grok MCP readiness timed out; required server unavailable"
        ) from exc
