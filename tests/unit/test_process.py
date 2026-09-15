from __future__ import annotations

import os
import sys
import threading

import anyio
import pytest

import gofer.utils.process as process_module
from gofer.utils.process import (
    build_subprocess_env,
    env_with_executable_on_path,
    run_subprocess,
    stream_subprocess,
)


@pytest.mark.anyio
async def test_task_cancellation_settles_process_tree_before_returning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settled = []

    class FakeProcess:
        stdin = None
        stdout = None
        stderr = None
        returncode = None

        async def wait(self):
            await anyio.sleep_forever()

        async def aclose(self):
            settled.append("closed")

    process = FakeProcess()

    async def open_process(*args, **kwargs):
        return process

    async def terminate_tree(target):
        assert target is process
        await anyio.sleep(0.01)
        settled.append("terminated")

    monkeypatch.setattr(process_module.anyio, "open_process", open_process)
    monkeypatch.setattr(process_module, "_terminate_process_tree", terminate_tree)
    with anyio.move_on_after(0.02) as scope:
        await run_subprocess(["fake-process-only"])
    assert scope.cancel_called
    assert settled == ["terminated", "closed"]


def test_env_with_executable_on_path_prepends_missing_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", "/bin"]))

    env = env_with_executable_on_path("/home/user/.nvm/versions/node/v20.20.0/bin/codex")

    assert env["PATH"].split(os.pathsep) == [
        "/home/user/.nvm/versions/node/v20.20.0/bin",
        "/usr/bin",
        "/bin",
    ]


def test_env_with_executable_on_path_keeps_path_when_directory_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/local/bin", "/usr/bin"]))

    env = env_with_executable_on_path("/usr/local/bin/claude")

    assert "PATH" not in env


def test_env_with_executable_on_path_ignores_bare_command_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")

    assert env_with_executable_on_path("codex", {"FOO": "bar"}) == {"FOO": "bar"}


def test_env_with_executable_on_path_extends_overridden_path() -> None:
    env = env_with_executable_on_path(
        "/opt/node/bin/claude",
        {"PATH": "/sandbox/bin", "FOO": "bar"},
    )

    assert env["PATH"].split(os.pathsep) == ["/opt/node/bin", "/sandbox/bin"]
    assert env["FOO"] == "bar"


def test_build_subprocess_env_restores_original_library_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPIMAGE", "/tmp/Gofer.AppImage")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_Gofer/usr/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")

    env = build_subprocess_env()

    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in env


def test_build_subprocess_env_filters_packaged_entries_from_original_library_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPIMAGE", "/tmp/Gofer.AppImage")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_Gofer/usr/lib")
    monkeypatch.setenv(
        "LD_LIBRARY_PATH_ORIG",
        os.pathsep.join(["/usr/local/lib", "/tmp/.mount_Gofer/usr/lib"]),
    )

    env = build_subprocess_env()

    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in env


def test_build_subprocess_env_removes_appimage_library_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPIMAGE", "/tmp/Gofer.AppImage")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_Gofer/usr/lib")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

    env = build_subprocess_env()

    assert "LD_LIBRARY_PATH" not in env


def test_build_subprocess_env_removes_packaged_library_path_without_appimage_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.delenv("APPDIR", raising=False)
    monkeypatch.delenv("_MEIPASS", raising=False)
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setenv(
        "LD_LIBRARY_PATH",
        os.pathsep.join(
            [
                "/tmp/.mount_Gofer-URJELL/usr/lib",
                "/usr/local/lib",
                "/tmp/.mount_Gofer-URJELL/resources",
            ]
        ),
    )

    env = build_subprocess_env()

    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib"


def test_build_subprocess_env_keeps_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPIMAGE", "/tmp/Gofer.AppImage")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_Gofer/usr/lib")

    env = build_subprocess_env({"LD_LIBRARY_PATH": "/workflow/lib"})

    assert env["LD_LIBRARY_PATH"] == "/workflow/lib"


@pytest.mark.asyncio
async def test_stream_subprocess_timeout_yields_nonzero_exit() -> None:
    events = [
        event
        async for event in stream_subprocess(
            [
                sys.executable,
                "-c",
                "import time; print('started', flush=True); time.sleep(5)",
            ],
            timeout=0.1,
        )
    ]

    assert any(event["text"] == "started\n" for event in events)
    assert any("Process timed out after" in event["text"] for event in events)
    assert events[-1]["type"] == "exit"
    assert events[-1]["returncode"] == 124


@pytest.mark.asyncio
async def test_stream_subprocess_cancel_event_terminates_process() -> None:
    cancel_event = threading.Event()

    async def set_cancel() -> None:
        await anyio.sleep(0.2)
        cancel_event.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(set_cancel)
        events = [
            event
            async for event in stream_subprocess(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    ("import time\nprint('ready', flush=True)\ntime.sleep(5)\n"),
                ],
                cancel_event=cancel_event,
            )
        ]

    assert any(event["text"] == "ready\n" for event in events)
    assert any("Process stopped by user" in event["text"] for event in events)
    assert events[-1]["type"] == "exit"
    assert events[-1]["returncode"] == 130


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX-specific")
@pytest.mark.asyncio
async def test_stream_subprocess_cancel_event_terminates_process_group(
    tmp_path,
) -> None:
    marker = tmp_path / "child-survived.txt"
    cancel_event = threading.Event()
    child_code = (
        f"import pathlib, time\ntime.sleep(1)\npathlib.Path({str(marker)!r}).write_text('alive')\n"
    )
    parent_code = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "print('ready', flush=True)\n"
        "time.sleep(10)\n"
    )

    async def set_cancel() -> None:
        await anyio.sleep(0.2)
        cancel_event.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(set_cancel)
        events = [
            event
            async for event in stream_subprocess(
                [sys.executable, "-u", "-c", parent_code],
                cancel_event=cancel_event,
            )
        ]

    await anyio.sleep(1.2)

    assert any(event["text"] == "ready\n" for event in events)
    assert events[-1]["returncode"] == 130
    assert not marker.exists()


@pytest.mark.asyncio
async def test_run_subprocess_returns_timeout_stderr() -> None:
    returncode, stdout, stderr = await run_subprocess(
        [
            sys.executable,
            "-u",
            "-c",
            "import time; print('ready', flush=True); time.sleep(5)",
        ],
        timeout=0.1,
    )

    assert returncode == 124
    assert stdout == "ready\n"
    assert "Process timed out after" in stderr


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX-specific")
@pytest.mark.asyncio
async def test_run_subprocess_timeout_terminates_process_group(tmp_path) -> None:
    marker = tmp_path / "timeout-child-survived.txt"
    child_code = (
        f"import pathlib, time\ntime.sleep(1)\npathlib.Path({str(marker)!r}).write_text('alive')\n"
    )
    parent_code = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "print('ready', flush=True)\n"
        "time.sleep(10)\n"
    )

    returncode, stdout, stderr = await run_subprocess(
        [sys.executable, "-u", "-c", parent_code],
        timeout=0.2,
    )

    await anyio.sleep(1.2)

    assert returncode == 124
    assert stdout == "ready\n"
    assert "Process timed out after" in stderr
    assert not marker.exists()


@pytest.mark.asyncio
async def test_terminate_process_tree_uses_taskkill_for_windows_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    class FakeProcess:
        pid = 123
        returncode = None
        terminated = False
        killed = False

        async def wait(self) -> None:
            return

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    async def fake_run_process(command: list[str], *, check: bool) -> object:
        commands.append(command)
        assert check is False
        return object()

    fake_process = FakeProcess()
    monkeypatch.setattr(process_module.os, "name", "nt")
    monkeypatch.setattr(process_module.anyio, "run_process", fake_run_process)

    await process_module._terminate_process_tree(fake_process)

    assert commands == [
        ["taskkill", "/PID", "123", "/T"],
        ["taskkill", "/PID", "123", "/T", "/F"],
    ]
    assert fake_process.terminated is False
    assert fake_process.killed is False


@pytest.mark.asyncio
async def test_run_subprocess_cancel_event_returns_stopped_status() -> None:
    cancel_event = threading.Event()

    async def set_cancel() -> None:
        await anyio.sleep(0.2)
        cancel_event.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(set_cancel)
        returncode, stdout, stderr = await run_subprocess(
            [
                sys.executable,
                "-u",
                "-c",
                ("import time\nprint('ready', flush=True)\ntime.sleep(5)\n"),
            ],
            cancel_event=cancel_event,
        )

    assert returncode == 130
    assert stdout == "ready\n"
    assert stderr == "Process stopped by user\n"


@pytest.mark.asyncio
async def test_run_subprocess_bounds_output_while_streaming() -> None:
    returncode, stdout, stderr = await run_subprocess(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000)"],
        max_output_bytes=80,
    )

    assert returncode == 0
    assert stderr == ""
    assert len(stdout.encode()) <= 80
    assert "subprocess output truncated at 80 bytes" in stdout


@pytest.mark.asyncio
async def test_run_subprocess_bounds_many_small_chunks() -> None:
    returncode, stdout, stderr = await run_subprocess(
        [
            sys.executable,
            "-u",
            "-c",
            ("import sys\nfor _ in range(100):\n sys.stdout.write('x'); sys.stdout.flush()"),
        ],
        max_output_bytes=80,
    )

    assert returncode == 0
    assert stderr == ""
    assert len(stdout.encode()) <= 80
    assert "subprocess output truncated at 80 bytes" in stdout


@pytest.mark.asyncio
async def test_run_subprocess_bounds_stdout_and_stderr_combined() -> None:
    returncode, stdout, stderr = await run_subprocess(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('x' * 1000); sys.stderr.write('y' * 1000)",
        ],
        max_output_bytes=80,
    )

    assert returncode == 0
    assert len((stdout + stderr).encode()) <= 80
    assert "subprocess output truncated at 80 bytes" in stdout + stderr
