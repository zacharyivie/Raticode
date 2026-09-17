"""Raticode-owned JSON-RPC transport for CLI providers.

ACP uses newline framing; Copilot SDK discovery uses Content-Length framing.
This module handles process/protocol lifetime only. Adapters own initialization,
session capabilities, authentication errors and permission decisions. No request
is automatically retried, and unsupported agent callbacks are rejected.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypeVar

from gofer.utils.process import (
    _terminate_process_tree,
    build_subprocess_env,
    env_with_executable_on_path,
)

RequestHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
T = TypeVar("T")


async def _settle(task: asyncio.Task[T]) -> tuple[T, bool]:
    """Wait for owned cleanup despite repeated cancellation of its caller."""
    cancelled = False
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True


class AcpTransportError(RuntimeError):
    """The connection cannot safely continue; never replay its pending work."""


class AcpRpcError(AcpTransportError):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        super().__init__(f"ACP request rejected ({code}): {message[:2000]}")


class AcpTransport:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        max_output_bytes: int | None,
        request_handler: RequestHandler | None,
        content_length_framing: bool = False,
    ) -> None:
        self._content_length_framing = content_length_framing
        self.process = process
        self.stderr = ""
        self._limit = max_output_bytes
        self._size = 0
        self._serial = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._notification_ready = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self._handler = request_handler
        self._failure: AcpTransportError | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._closed = False
        self._cleanup_task: asyncio.Task[None] | None = None

    def _fail(self, error: AcpTransportError) -> None:
        if self._failure is not None:
            return
        self._failure = error
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._notification_ready.set()

    def _check(self) -> None:
        if self._failure is not None:
            raise self._failure
        if self._closed:
            raise AcpTransportError("ACP connection is closed")

    def _count(self, size: int) -> None:
        self._size += size
        if self._limit is not None and self._size > self._limit:
            raise AcpTransportError("ACP output exceeded the configured output limit")

    async def _send(self, message: dict[str, Any]) -> None:
        self._check()
        if self.process.stdin is None:
            raise AcpTransportError("ACP input is closed")
        data = json.dumps({"jsonrpc": "2.0", **message}).encode()
        data = (
            f"Content-Length: {len(data)}\r\n\r\n".encode() + data
            if self._content_length_framing
            else data + b"\n"
        )
        try:
            async with self._write_lock:
                self._check()
                self.process.stdin.write(data)
                await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            error = AcpTransportError("ACP process closed its input")
            self._fail(error)
            raise error from exc

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"method": method, "params": params})

    async def request(
        self, method: str, params: dict[str, Any], *, timeout: float | None = 30
    ) -> dict[str, Any]:
        self._check()
        self._serial += 1
        serial = self._serial
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[serial] = future
        try:
            # Include pipe backpressure in the deadline, not only the response.
            async with asyncio.timeout(timeout):
                await self._send({"id": serial, "method": method, "params": params})
                return await future
        except TimeoutError as exc:
            error = AcpTransportError(f"ACP {method} timed out; delivery is uncertain")
            self._fail(error)
            raise error from exc
        except asyncio.CancelledError:
            self._fail(AcpTransportError(f"ACP {method} cancelled; delivery is uncertain"))
            raise
        finally:
            self._pending.pop(serial, None)
            # A write failure may have failed the future before it was awaited.
            if future.done() and not future.cancelled():
                future.exception()
            elif not future.done():
                future.cancel()

    async def next_notification(self) -> dict[str, Any]:
        while self._notifications.empty():
            self._check()
            await self._notification_ready.wait()
        event = self._notifications.get_nowait()
        if self._notifications.empty() and self._failure is None:
            self._notification_ready.clear()
        return event

    def drain_notifications(self) -> list[dict[str, Any]]:
        """Return buffered updates; call raise_if_failed afterwards for terminal errors."""
        events = []
        while not self._notifications.empty():
            events.append(self._notifications.get_nowait())
        if self._failure is None:
            self._notification_ready.clear()
        return events

    def raise_if_failed(self) -> None:
        """Check terminal state separately, after preserving buffered partial output."""
        self._check()

    async def _callback(self, message: dict[str, Any]) -> None:
        serial, method = message["id"], message["method"]
        if type(serial) not in (str, int):
            raise AcpTransportError("ACP agent request has an invalid ID")
        if self._handler is None:
            await self._send(
                {"id": serial, "error": {"code": -32601, "message": "Client method unavailable"}}
            )
            return
        try:
            result = await asyncio.wait_for(self._handler(method, message.get("params", {})), 10)
            if not isinstance(result, dict):
                raise ValueError("Callback must return an object")
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not leak callback details or echo potentially sensitive request fields.
            await self._send(
                {"id": serial, "error": {"code": -32603, "message": "Client request rejected"}}
            )
        else:
            await self._send({"id": serial, "result": result})

    async def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            while line := await self.process.stdout.readline():
                self._check()
                self._count(len(line))
                if self._content_length_framing:
                    if not line.startswith(b"Content-Length:"):
                        raise AcpTransportError("Invalid JSON-RPC content length header")
                    size = int(line.split(b":", 1)[1].strip())
                    if size < 1:
                        raise AcpTransportError("Invalid JSON-RPC content length")
                    self._count(size + 2)
                    if await self.process.stdout.readexactly(2) != b"\r\n":
                        raise AcpTransportError("Invalid JSON-RPC header terminator")
                    line = await self.process.stdout.readexactly(size)
                message = json.loads(line)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise AcpTransportError("ACP emitted an invalid JSON-RPC object")
                if "method" in message:
                    if not isinstance(message["method"], str) or not isinstance(
                        message.get("params", {}), dict
                    ):
                        raise AcpTransportError("ACP emitted an invalid method or params")
                    if "id" in message:
                        await self._callback(message)
                    else:
                        self._notifications.put_nowait(message)
                        self._notification_ready.set()
                else:
                    serial = message.get("id")
                    if type(serial) is not int or serial not in self._pending:
                        raise AcpTransportError("ACP response has an unknown request ID")
                    future = self._pending[serial]
                    if future.done():
                        raise AcpTransportError("ACP emitted a duplicate response")
                    if ("result" in message) == ("error" in message):
                        raise AcpTransportError("ACP response must contain result or error")
                    if "error" in message:
                        error = message["error"]
                        if not isinstance(error, dict) or type(error.get("code")) is not int:
                            raise AcpTransportError("ACP emitted an invalid RPC error")
                        future.set_exception(
                            AcpRpcError(error["code"], str(error.get("message", "")))
                        )
                    elif isinstance(message["result"], dict):
                        future.set_result(message["result"])
                    else:
                        raise AcpTransportError("ACP response result must be an object")
            self._fail(AcpTransportError("ACP process closed before the connection was released"))
        except (ValueError, OSError, EOFError, RecursionError, AcpTransportError) as exc:
            # JSON decode errors include raw data; deliberately keep framing errors generic.
            error = (
                exc
                if isinstance(exc, AcpTransportError)
                else AcpTransportError("ACP emitted malformed or oversized JSON output")
            )
            self._fail(error)

    async def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        try:
            while chunk := await self.process.stderr.read(4096):
                self._count(len(chunk))
                self.stderr += chunk.decode(errors="replace")
        except (OSError, AcpTransportError) as exc:
            self._fail(AcpTransportError(str(exc)))

    async def _watch(self, cancel: threading.Event | None, timeout: float | None) -> None:
        started = time.monotonic()
        while not self._closed:
            if cancel is not None and cancel.is_set():
                self._fail(AcpTransportError("ACP turn cancelled"))
            elif timeout is not None and time.monotonic() - started >= timeout:
                self._fail(AcpTransportError("ACP turn exceeded its time limit"))
            if self._failure is not None:
                await self._stop_process()
                return
            await asyncio.sleep(0.02)

    async def _stop_process(self) -> None:
        await _terminate_process_tree(self.process)
        # A child can ignore SIGTERM even after its parent exits. The dedicated
        # group must be gone before callers delete per-run MCP configuration.
        if os.name != "nt":
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await self.process.wait()

    async def close(self) -> None:
        if self._cleanup_task is None:
            self._closed = True
            self._cleanup_task = asyncio.create_task(self._cleanup())
        _, cancelled = await _settle(self._cleanup_task)
        if cancelled:
            raise asyncio.CancelledError

    async def _cleanup(self) -> None:
        self._fail(AcpTransportError("ACP connection closed"))
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._stop_process()


@asynccontextmanager
async def open_acp_transport(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    cancel_event: threading.Event | None = None,
    timeout: float | None = None,
    max_output_bytes: int | None = 2_000_000,
    request_handler: RequestHandler | None = None,
    content_length_framing: bool = False,
) -> AsyncIterator[AcpTransport]:
    if (
        not command
        or (max_output_bytes is not None and max_output_bytes < 1)
        or (timeout is not None and timeout <= 0)
    ):
        raise ValueError("ACP needs a command and positive output/time limits")
    if cancel_event is not None and cancel_event.is_set():
        raise AcpTransportError("ACP turn cancelled before launch")
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=build_subprocess_env(env_with_executable_on_path(command[0], env)),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
            limit=max_output_bytes + 1 if max_output_bytes is not None else sys.maxsize,
        )
    )
    try:
        process = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        # Cancellation can arrive after the OS spawned the child but before
        # asyncio returned its handle. Recover the handle and reap that child.
        process, _ = await _settle(spawn)
        abandoned = AcpTransport(process, max_output_bytes=max_output_bytes, request_handler=None)
        await abandoned.close()
        raise
    transport = AcpTransport(
        process,
        max_output_bytes=max_output_bytes,
        request_handler=request_handler,
        content_length_framing=content_length_framing,
    )
    transport._tasks = [
        asyncio.create_task(transport._read()),
        asyncio.create_task(transport._read_stderr()),
        asyncio.create_task(transport._watch(cancel_event, timeout)),
    ]
    try:
        yield transport
    finally:
        await transport.close()
