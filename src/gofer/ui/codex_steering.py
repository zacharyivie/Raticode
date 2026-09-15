"""Codex app-server transport for steerable turns, using Rem's prepared command.

Protocol: https://developers.openai.com/codex/app-server
The control and stream must run on the same asyncio loop.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from gofer.utils.process import (
    _terminate_process_tree,
    env_with_executable_on_path,
    stream_subprocess,
)


class SteeringDeliveryUncertain(RuntimeError):
    """A steering request may have been accepted; do not retry automatically."""


class _RequestRejected(RuntimeError):
    def __init__(self, error: Any) -> None:
        super().__init__(str(error))
        self.code = error.get("code") if isinstance(error, dict) else None


class CodexTurnControl:
    def __init__(self) -> None:
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self._transport: _Transport | None = None
        self._steering_available = True

    @property
    def active(self) -> bool:
        return self._steering_available and self._transport is not None and self.turn_id is not None

    async def steer(self, text: str) -> bool:
        transport, turn_id = self._transport, self.turn_id
        if not self.active or transport is None or turn_id is None or not text.strip():
            return False
        try:
            result = await transport.request(
                "turn/steer",
                {
                    "threadId": self.thread_id,
                    "expectedTurnId": turn_id,
                    "input": [{"type": "text", "text": text}],
                },
            )
            if result.get("turnId") != turn_id:
                raise SteeringDeliveryUncertain("Codex returned an unexpected steering turn ID")
            return True
        except _RequestRejected as exc:
            # An explicit RPC error means the server rejected this request.
            if exc.code == -32601:
                self._steering_available = False
            return False
        except (OSError, RuntimeError, TimeoutError) as exc:
            # A missing acknowledgement cannot distinguish a lost request from
            # accepted work. The runtime exposes this delivery for user review.
            self._steering_available = False
            raise SteeringDeliveryUncertain(str(exc)) from exc


class _Transport:
    def __init__(self, process: asyncio.subprocess.Process, limit: int) -> None:
        self.process = process
        self.limit = limit
        self.size = 0
        self.serial = 0
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def send(self, payload: dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise RuntimeError("Codex input is closed")
        self.process.stdin.write((json.dumps(payload) + "\n").encode())
        await self.process.stdin.drain()

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.serial += 1
        serial = self.serial
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[serial] = future
        try:
            await self.send({"id": serial, "method": method, "params": params})
            return await asyncio.wait_for(future, 30)
        finally:
            self.pending.pop(serial, None)

    async def read(self) -> None:
        assert self.process.stdout is not None
        error = "Codex app-server closed before completing the turn"
        try:
            while line := await self.process.stdout.readline():
                self.size += len(line)
                if self.size > self.limit:
                    raise RuntimeError("Codex output exceeded the configured output limit")
                message = json.loads(line)
                if not isinstance(message, dict):
                    continue
                serial = message.get("id")
                if "method" in message and serial is not None:
                    # There is no interactive approval surface in swarm turns.
                    # Fail closed rather than granting a provider request.
                    await self.send(
                        {
                            "id": serial,
                            "error": {
                                "code": -32601,
                                "message": "Interactive requests are unavailable in swarm turns",
                            },
                        }
                    )
                elif isinstance(serial, int) and serial in self.pending:
                    future = self.pending[serial]
                    if not future.done():
                        if "error" in message:
                            future.set_exception(_RequestRejected(message["error"]))
                        else:
                            future.set_result(message.get("result") or {})
                elif "method" in message:
                    await self.events.put(message)
        except (ValueError, OSError, RuntimeError) as exc:
            error = str(exc)
        finally:
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(RuntimeError(error))
            await self.events.put({"method": "transport/error", "params": {"message": error}})


def _app_server_command(command: list[str]) -> list[str]:
    # Reuse all config overrides, including explicit resource isolation and
    # Second Brain tool grants, without forwarding exec-only flags.
    result = [command[0], "app-server"]
    for index, value in enumerate(command[:-1]):
        if value == "-c":
            result.extend([value, command[index + 1]])
    return result


def _event(payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": "chunk", "stream": "stdout", "text": json.dumps(payload) + "\n"}


def _exec_item(item: dict[str, Any]) -> dict[str, Any]:
    converted = dict(item)
    converted["type"] = {
        "agentMessage": "agent_message",
        "commandExecution": "command_execution",
        "fileChange": "file_change",
        "mcpToolCall": "mcp_tool_call",
        "webSearch": "web_search",
    }.get(str(item.get("type")), item.get("type"))
    if "aggregatedOutput" in item:
        converted["aggregated_output"] = item["aggregatedOutput"]
    return converted


async def stream_codex_turn(
    command: list[str],
    *,
    control: CodexTurnControl,
    cwd: Path,
    cancel_event: threading.Event | None,
    max_output_bytes: int,
) -> AsyncIterator[dict[str, Any]]:
    process = await asyncio.create_subprocess_exec(
        *_app_server_command(command),
        cwd=cwd,
        env=env_with_executable_on_path(command[0]),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        limit=max_output_bytes + 1,
    )
    transport = _Transport(process, max_output_bytes)
    reader = asyncio.create_task(transport.read())

    async def drain_stderr() -> None:
        assert process.stderr is not None
        while chunk := await process.stderr.read(4096):
            transport.size += len(chunk)
            if transport.size > max_output_bytes:
                await transport.events.put(
                    {
                        "method": "transport/error",
                        "params": {"message": "Codex output exceeded the configured output limit"},
                    }
                )
                return

    async def watch_cancel() -> None:
        while not (cancel_event and cancel_event.is_set()):
            await asyncio.sleep(0.1)
        if control.active:
            try:
                await asyncio.wait_for(
                    transport.request(
                        "turn/interrupt",
                        {
                            "threadId": control.thread_id,
                            "turnId": control.turn_id,
                        },
                    ),
                    1,
                )
            except (OSError, RuntimeError, TimeoutError):
                pass
        await _terminate_process_tree(process)

    stderr_reader = asyncio.create_task(drain_stderr())
    canceller = asyncio.create_task(watch_cancel())
    turn_requested = False
    fallback = False
    try:
        await transport.request(
            "initialize",
            {"clientInfo": {"name": "raticode", "title": "Raticode swarms", "version": "1.0.0"}},
        )
        await transport.send({"method": "initialized", "params": {}})
        sandbox = command[command.index("--sandbox") + 1]
        params: dict[str, Any] = {"cwd": str(cwd), "approvalPolicy": "never", "sandbox": sandbox}
        if "--model" in command:
            params["model"] = command[command.index("--model") + 1]
        # Same additional writable directories as the ordinary Rem transport.
        roots = [command[i + 1] for i, flag in enumerate(command[:-1]) if flag == "--add-dir"]
        params["config"] = {"sandbox_workspace_write.writable_roots": roots}
        result = await transport.request("thread/start", params)
        control.thread_id = result["thread"]["id"]
        inputs = [{"type": "text", "text": command[-1]}]
        inputs.extend(
            {"type": "localImage", "path": flag.split("=", 1)[1]}
            for flag in command
            if flag.startswith("--image=")
        )
        turn_requested = True
        result = await transport.request(
            "turn/start", {"threadId": control.thread_id, "input": inputs}
        )
        control.turn_id = result["turn"]["id"]
        control._transport = transport
        while True:
            event = await transport.events.get()
            method, payload = event["method"], event.get("params") or {}
            if method in {"item/started", "item/completed"}:
                yield _event(
                    {"type": method.replace("/", "."), "item": _exec_item(payload["item"])}
                )
            elif method == "turn/completed":
                control.turn_id = None
                turn = payload.get("turn") or {}
                status = turn.get("status")
                if status != "completed":
                    yield {
                        "type": "chunk",
                        "stream": "stderr",
                        "text": str(turn.get("error") or status),
                    }
                yield {"type": "exit", "returncode": 0 if status == "completed" else 1}
                return
            elif method in {"transport/error", "error"}:
                raise RuntimeError(str(payload.get("message") or payload))
    except (OSError, RuntimeError, TimeoutError, KeyError) as exc:
        fallback = not turn_requested and not (cancel_event and cancel_event.is_set())
        if not fallback:
            yield {"type": "chunk", "stream": "stderr", "text": str(exc)}
            yield {"type": "exit", "returncode": 1}
    finally:
        control._transport = None
        control.turn_id = None
        for task in (reader, stderr_reader, canceller):
            task.cancel()
        await asyncio.gather(reader, stderr_reader, canceller, return_exceptions=True)
        await _terminate_process_tree(process)

    if fallback:
        # Only retry before turn/start was sent. A missing start response may
        # already represent running work and must never cause duplicate execution.
        async for fallback_event in stream_subprocess(
            command,
            cwd=cwd,
            env=env_with_executable_on_path(command[0]),
            cancel_event=cancel_event,
            timeout=None,
            max_output_bytes=max_output_bytes,
        ):
            yield dict(fallback_event)
