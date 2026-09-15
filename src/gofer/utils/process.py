from __future__ import annotations

import os
import signal
import threading
import time
from collections.abc import AsyncIterator
from typing import Any, Literal, TypedDict

import anyio
import anyio.abc


class ProcessStreamEvent(TypedDict):
    type: Literal["chunk", "exit"]
    stream: Literal["stdout", "stderr"] | None
    text: str
    returncode: int | None


class ProcessError(Exception):
    def __init__(self, returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"Process exited with code {returncode}: {stderr[:200]}")


def build_subprocess_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Build an env for user subprocesses without packaged-app library leaks."""
    env = dict(os.environ)
    _sanitize_packaged_runtime_env(env)
    env.update(overrides or {})
    return env


def env_with_executable_on_path(
    executable: str,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return env overrides that put an executable's own directory on PATH.

    Provider CLIs installed as npm globals (``claude``, ``codex``) are Node
    scripts with a ``#!/usr/bin/env node`` shebang.  When such a CLI is resolved
    outside PATH -- the nvm fallback in ``resolve_provider_executable`` -- the
    ``node`` binary sitting beside it is not findable either, so exec fails
    before the CLI can produce any output.  A bare command name is left alone:
    it was found on PATH already, or cannot be located here at all.
    """

    env = dict(overrides or {})
    directory = os.path.dirname(executable)
    if not directory:
        return env
    directory = os.path.abspath(directory)
    current_path = env.get("PATH", os.environ.get("PATH", ""))
    entries = [entry for entry in current_path.split(os.pathsep) if entry]
    if directory in entries:
        return env
    env["PATH"] = os.pathsep.join([directory, *entries])
    return env


def _sanitize_packaged_runtime_env(env: dict[str, str]) -> None:
    """Avoid leaking AppImage/PyInstaller dynamic library paths into user tools."""
    original_library_path = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original_library_path is not None:
        if original_library_path:
            _set_clean_library_path(env, original_library_path)
        else:
            env.pop("LD_LIBRARY_PATH", None)
        return

    if env.get("APPIMAGE") or env.get("APPDIR") or env.get("_MEIPASS"):
        env.pop("LD_LIBRARY_PATH", None)
        return

    library_path = env.get("LD_LIBRARY_PATH")
    if library_path:
        _set_clean_library_path(env, library_path)


def _set_clean_library_path(env: dict[str, str], library_path: str) -> None:
    entries = [
        entry
        for entry in library_path.split(os.pathsep)
        if entry and not _is_packaged_runtime_library_path(entry)
    ]
    if entries:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(entries)
    else:
        env.pop("LD_LIBRARY_PATH", None)


def _is_packaged_runtime_library_path(entry: str) -> bool:
    path = entry.replace("\\", "/")
    return (
        "/.mount_" in path
        or "/app.asar" in path
        or "/resources/app.asar" in path
        or path.endswith("/resources")
        or "/resources/" in path
    )


async def run_subprocess(
    cmd: list[str],
    *,
    cancel_event: threading.Event | None = None,
    cwd: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    stdin: bytes | None = None,
    max_output_bytes: int | None = None,
) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    merged_env = build_subprocess_env(env)

    async def _run() -> tuple[int, str, str]:
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        returncode = 1
        async for event in stream_subprocess(
            cmd,
            cancel_event=cancel_event,
            cwd=cwd,
            env=merged_env,
            stdin=stdin,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
        ):
            if event["type"] == "chunk":
                if event["stream"] == "stdout":
                    stdout_chunks.append(event["text"])
                elif event["stream"] == "stderr":
                    stderr_chunks.append(event["text"])
                continue
            if event["stream"] is None:
                returncode = event["returncode"] if event["returncode"] is not None else 1
        return returncode, "".join(stdout_chunks), "".join(stderr_chunks)

    return await _run()


async def stream_subprocess(
    cmd: list[str],
    *,
    cancel_event: threading.Event | None = None,
    cwd: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    stdin: bytes | None = None,
    max_output_bytes: int | None = None,
) -> AsyncIterator[ProcessStreamEvent]:
    """Run a subprocess and yield stdout/stderr chunks as they arrive."""
    merged_env = build_subprocess_env(env)

    async def _stream(process: anyio.abc.Process) -> AsyncIterator[ProcessStreamEvent]:
        deadline = time.monotonic() + timeout if timeout is not None else None

        if process.stdin is not None:
            if stdin:
                await process.stdin.send(stdin)
            await process.stdin.aclose()

        send, receive = anyio.create_memory_object_stream[ProcessStreamEvent](100)
        stream_done_count = 0
        exited = False
        returncode: int | None = None

        output_lock = anyio.Lock()
        emitted_output_bytes = 0
        output_truncated = False
        suffix = (
            f"\n[subprocess output truncated at {max_output_bytes} bytes]".encode()
            if max_output_bytes is not None
            else b""
        )
        content_limit = (
            max(0, max_output_bytes - len(suffix)) if max_output_bytes is not None else None
        )

        async def bounded_chunk(chunk: bytes) -> bytes | None:
            nonlocal emitted_output_bytes, output_truncated
            if max_output_bytes is None:
                return chunk
            async with output_lock:
                if output_truncated:
                    return None
                assert content_limit is not None
                remaining_content = content_limit - emitted_output_bytes
                if len(chunk) > remaining_content:
                    output_truncated = True
                    emitted = (chunk[: max(0, remaining_content)] + suffix)[:max_output_bytes]
                    emitted_output_bytes += len(emitted)
                    return emitted
                emitted_output_bytes += len(chunk)
                return chunk

        async def send_stderr_text(text: str) -> None:
            chunk = await bounded_chunk(text.encode())
            if chunk is None:
                return
            await send.send(
                {
                    "type": "chunk",
                    "stream": "stderr",
                    "text": chunk.decode(errors="replace"),
                    "returncode": None,
                }
            )

        async def read_stream(
            stream_name: Literal["stdout", "stderr"],
            stream: anyio.abc.ByteReceiveStream | None,
        ) -> None:
            if stream is None:
                await send.send(
                    {
                        "type": "exit",
                        "stream": stream_name,
                        "text": "",
                        "returncode": None,
                    }
                )
                return
            while True:
                try:
                    chunk = await stream.receive()
                except anyio.EndOfStream:
                    break
                if not chunk:
                    break
                bounded = await bounded_chunk(chunk)
                if bounded is None:
                    continue
                await send.send(
                    {
                        "type": "chunk",
                        "stream": stream_name,
                        "text": bounded.decode(errors="replace"),
                        "returncode": None,
                    }
                )
            await send.send(
                {
                    "type": "exit",
                    "stream": stream_name,
                    "text": "",
                    "returncode": None,
                }
            )

        async def wait_for_process() -> None:
            nonlocal returncode
            stopped = False
            timed_out = False
            while process.returncode is None:
                if cancel_event is not None and cancel_event.is_set():
                    stopped = True
                    await _terminate_process_tree(process)
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    timed_out = True
                    await _terminate_process_tree(process)
                    break
                with anyio.move_on_after(0.1):
                    await process.wait()
            await process.wait()
            returncode = process.returncode if process.returncode is not None else 130
            if timed_out:
                returncode = 124
            if stopped:
                returncode = 130
            if stopped:
                await send_stderr_text("Process stopped by user\n")
            if timed_out:
                await send_stderr_text(f"Process timed out after {timeout:g} seconds\n")
            await send.send(
                {
                    "type": "exit",
                    "stream": None,
                    "text": "",
                    "returncode": returncode,
                }
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(read_stream, "stdout", process.stdout)
            tg.start_soon(read_stream, "stderr", process.stderr)
            tg.start_soon(wait_for_process)
            async with receive:
                async for event in receive:
                    if event["type"] == "exit" and event["stream"] in {"stdout", "stderr"}:
                        stream_done_count += 1
                    elif event["type"] == "exit" and event["stream"] is None:
                        exited = True
                    else:
                        yield event

                    if exited and stream_done_count >= 2:
                        break
            tg.cancel_scope.cancel()

        yield {
            "type": "exit",
            "stream": None,
            "text": "",
            "returncode": returncode if returncode is not None else 130,
        }

    process = await anyio.open_process(
        cmd,
        cwd=cwd,
        env=merged_env,
        start_new_session=True,
    )
    try:
        async for event in _stream(process):
            yield event
    finally:
        # Cancellation must settle the whole process group before the owning
        # workflow can report Stopped. Cleanup must survive its cancelled scope.
        with anyio.CancelScope(shield=True):
            if process.returncode is None:
                await _terminate_process_tree(process)
            await process.aclose()


async def _terminate_process_tree(process: Any) -> None:
    if os.name != "nt":
        pid = getattr(process, "pid", None)
        if pid is not None:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError:
                process.terminate()
        else:
            process.terminate()
    else:
        await _terminate_windows_process_tree(process, force=False)

    with anyio.move_on_after(2):
        await process.wait()
    if process.returncode is not None:
        return

    if os.name != "nt":
        pid = getattr(process, "pid", None)
        if pid is not None:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                process.kill()
        else:
            process.kill()
    else:
        await _terminate_windows_process_tree(process, force=True)


async def _terminate_windows_process_tree(process: Any, *, force: bool) -> None:
    pid = getattr(process, "pid", None)
    if pid is None:
        if force:
            process.kill()
        else:
            process.terminate()
        return

    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    try:
        await anyio.run_process(command, check=False)
    except OSError:
        if force:
            process.kill()
        else:
            process.terminate()
