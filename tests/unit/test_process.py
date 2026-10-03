from __future__ import annotations

import os
import shutil
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
@pytest.mark.parametrize("parent_exited", [False, True])
async def test_task_cancellation_settles_process_tree_before_returning(
    monkeypatch: pytest.MonkeyPatch,
    parent_exited: bool,
) -> None:
    settled = []

    class OpenPipe:
        async def receive(self):
            await anyio.sleep_forever()

    class FakeProcess:
        stdin = None
        stdout = OpenPipe()
        stderr = None
        returncode = 0 if parent_exited else None

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


@pytest.mark.parametrize("copied_parent", [False, True])
def test_child_environment_excludes_desktop_credentials(
    monkeypatch: pytest.MonkeyPatch, copied_parent: bool
) -> None:
    monkeypatch.setenv("GOFER_UI_API_TOKEN", "desktop-api-token")
    monkeypatch.setenv("GOFER_DESKTOP_GRANT_SECRET", "desktop-grant-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-key")
    monkeypatch.setenv("RATICODE_REPORT_PDF_TOKEN", "report-capability")

    env = build_subprocess_env(dict(os.environ) if copied_parent else None)

    assert "GOFER_UI_API_TOKEN" not in env
    assert "GOFER_DESKTOP_GRANT_SECRET" not in env
    assert env["OPENAI_API_KEY"] == "provider-key"
    assert env["RATICODE_REPORT_PDF_TOKEN"] == "report-capability"
    assert os.environ["GOFER_UI_API_TOKEN"] == "desktop-api-token"
    assert os.environ["GOFER_DESKTOP_GRANT_SECRET"] == "desktop-grant-secret"


@pytest.mark.anyio
async def test_workflow_child_cannot_read_desktop_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOFER_UI_API_TOKEN", "desktop-api-token")
    monkeypatch.setenv("GOFER_DESKTOP_GRANT_SECRET", "desktop-grant-secret")
    code, output, _ = await run_subprocess(
        [
            sys.executable,
            "-c",
            "import os; print(any(name in os.environ for name in "
            "('GOFER_UI_API_TOKEN', 'GOFER_DESKTOP_GRANT_SECRET')))",
        ],
        env=dict(os.environ),
    )
    assert code == 0
    assert output.strip() == "False"


def test_explicit_child_environment_does_not_restore_removed_git_overrides(monkeypatch):
    monkeypatch.setenv("GIT_INDEX_FILE", "/user/index")
    assert build_subprocess_env(
        {"GIT_LITERAL_PATHSPECS": "1", "GOFER_DESKTOP_GRANT_SECRET": "desktop-secret"},
        inherit_parent=False,
    ) == {"GIT_LITERAL_PATHSPECS": "1"}


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


@pytest.mark.parametrize(
    "original", [None, "", os.pathsep.join(["/opt/user/lib", "/tmp/_MEIparent/lib"])]
)
def test_build_subprocess_env_removes_pyinstaller_library_paths(monkeypatch, original):
    for name in ("APPIMAGE", "APPDIR", "_MEIPASS", "LD_LIBRARY_PATH_ORIG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "LD_LIBRARY_PATH",
        os.pathsep.join(["/tmp/_MEIchild", "/opt/user/lib", "/custom/tmp/_MEIchild/lib"]),
    )
    if original is not None:
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", original)

    env = build_subprocess_env()

    assert env.get("LD_LIBRARY_PATH") == (None if original == "" else "/opt/user/lib")
    assert "LD_LIBRARY_PATH_ORIG" not in env
    assert "/tmp/_MEIchild" in os.environ["LD_LIBRARY_PATH"]
    assert build_subprocess_env({"LD_LIBRARY_PATH": "/workflow/lib"})[
        "LD_LIBRARY_PATH"
    ] == "/workflow/lib"


def test_build_subprocess_env_removes_actual_pyinstaller_bundle_root(monkeypatch):
    monkeypatch.setattr(sys, "_MEIPASS", "/opt/raticode/bundle", raising=False)
    for name in ("APPIMAGE", "APPDIR", "_MEIPASS", "LD_LIBRARY_PATH_ORIG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(
        "LD_LIBRARY_PATH",
        os.pathsep.join(
            ["/opt/raticode/bundle", "/opt/raticode/bundle/lib", "/opt/raticode/bundle-other"]
        ),
    )

    assert build_subprocess_env()["LD_LIBRARY_PATH"] == "/opt/raticode/bundle-other"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux dynamic linker regression")
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_bash_does_not_inherit_pyinstaller_libraries(monkeypatch, tmp_path, streaming):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is unavailable")
    bundle = tmp_path / "_MEIbroken"
    bundle.mkdir()
    # If leaked, this invalid library prevents Bash from even running its command.
    (bundle / "libreadline.so.8").write_bytes(b"invalid bundled Readline library")
    for name in ("APPIMAGE", "APPDIR", "_MEIPASS", "LD_LIBRARY_PATH_ORIG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))
    command = [bash, "--noprofile", "--norc", "-c", 'printf "shell-ok:%s" "$LD_LIBRARY_PATH"']

    if streaming:
        events = [event async for event in stream_subprocess(command)]
        code = events[-1]["returncode"]
        stdout = "".join(event["text"] for event in events if event["stream"] == "stdout")
        stderr = "".join(event["text"] for event in events if event["stream"] == "stderr")
    else:
        code, stdout, stderr = await run_subprocess(command)

    assert (code, stdout, stderr) == (0, "shell-ok:", "")


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


@pytest.mark.asyncio
async def test_run_subprocess_drains_output_while_sending_large_input() -> None:
    payload = b"p" * (1024 * 1024)
    code = (
        "import sys\n"
        "sys.stdout.write('o' * 1024 * 1024); sys.stdout.flush()\n"
        "sys.stderr.write('e' * 1024 * 1024); sys.stderr.flush()\n"
        "data = sys.stdin.buffer.read()\n"
        "print(len(data))\n"
    )
    with anyio.fail_after(5):
        returncode, stdout, stderr = await run_subprocess(
            [sys.executable, "-c", code], stdin=payload, timeout=3
        )
    assert returncode == 0
    assert stdout == "o" * (1024 * 1024) + f"{len(payload)}\n"
    assert stderr == "e" * (1024 * 1024)


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", [False, True], ids=["timeout", "stop"])
async def test_run_subprocess_interrupts_blocked_input(stop: bool) -> None:
    cancel = threading.Event()

    async def request_stop() -> None:
        await anyio.sleep(0.2)
        if stop:
            cancel.set()

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(request_stop)
            result = await run_subprocess(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                stdin=b"p" * (1024 * 1024),
                cancel_event=cancel,
                timeout=None if stop else 0.2,
            )
    returncode, _, stderr = result
    assert returncode == (130 if stop else 124)
    assert ("stopped by user" if stop else "timed out") in stderr


@pytest.mark.asyncio
async def test_run_subprocess_keeps_error_when_child_closes_input() -> None:
    with anyio.fail_after(5):
        returncode, stdout, stderr = await run_subprocess(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('login required\\n'); sys.exit(7)",
            ],
            stdin=b"p" * (1024 * 1024),
            timeout=3,
        )
    assert returncode == 7
    assert stdout == ""
    assert stderr == "login required\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("max_output_bytes", [None, 1024])
async def test_run_subprocess_preserves_utf8_across_pipe_reads(
    monkeypatch: pytest.MonkeyPatch, max_output_bytes: int | None
) -> None:
    class SplitStream:
        def __init__(self, data: bytes) -> None:
            self.chunks = iter(bytes([value]) for value in data)

        async def receive(self) -> bytes:
            await anyio.sleep(0)
            try:
                return next(self.chunks)
            except StopIteration:
                raise anyio.EndOfStream from None

    class FakeProcess:
        stdin = None
        stdout = SplitStream("東京 🐀\n".encode())
        stderr = SplitStream("échec\n".encode() + b"\xe2")
        returncode = 0

        async def wait(self) -> int:
            return 0

        async def aclose(self) -> None:
            pass

    async def open_process(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(process_module.anyio, "open_process", open_process)
    returncode, stdout, stderr = await run_subprocess(
        ["fake-process-only"], max_output_bytes=max_output_bytes
    )
    assert returncode == 0
    assert stdout == "東京 🐀\n"
    assert stderr == "échec\n\ufffd"


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX-specific")
@pytest.mark.asyncio
async def test_stop_kills_child_that_ignores_sigterm_after_parent_exits(tmp_path) -> None:
    marker = tmp_path / "child-survived.txt"
    cancel = threading.Event()
    child = (
        "import pathlib, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready', flush=True)\n"
        "time.sleep(0.7)\n"
        f"pathlib.Path({str(marker)!r}).write_text('survived')\n"
    )
    parent = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "time.sleep(30)\n"
    )
    with anyio.fail_after(5):
        events = []
        async for event in stream_subprocess([sys.executable, "-c", parent], cancel_event=cancel):
            events.append(event)
            if "ready" in event["text"]:
                cancel.set()
    await anyio.sleep(0.8)
    assert events[-1]["returncode"] == 130
    assert not marker.exists()


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX-specific")
@pytest.mark.asyncio
@pytest.mark.parametrize("stop", [False, True], ids=["timeout", "stop"])
async def test_interrupts_child_output_after_parent_already_exited(
    tmp_path, monkeypatch: pytest.MonkeyPatch, stop: bool
) -> None:
    marker = tmp_path / "child-survived.txt"
    cancel = threading.Event()
    opened = []
    real_open = process_module.anyio.open_process

    async def open_process(*args, **kwargs):
        process = await real_open(*args, **kwargs)
        opened.append(process)
        return process

    monkeypatch.setattr(process_module.anyio, "open_process", open_process)
    child = (
        "import pathlib, signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready', flush=True)\n"
        "time.sleep(1.5)\n"
        f"pathlib.Path({str(marker)!r}).write_text('survived')\n"
    )
    parent = f"import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', {child!r}])\n"
    with anyio.fail_after(5):
        events = []
        async for event in stream_subprocess(
            [sys.executable, "-c", parent],
            cancel_event=cancel,
            timeout=None if stop else 0.5,
        ):
            events.append(event)
            if "ready" in event["text"]:
                while opened[0].returncode is None:
                    await anyio.sleep(0.01)
                if stop:
                    cancel.set()
    assert events[-1]["returncode"] == (130 if stop else 124)
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_code", [0, 7])
async def test_keeps_child_output_and_parent_status_after_parent_exits(exit_code: int) -> None:
    child = "import time; time.sleep(0.2); print('child output', flush=True)"
    parent = (
        "import subprocess, sys\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        f"sys.exit({exit_code})\n"
    )
    with anyio.fail_after(5):
        returncode, stdout, stderr = await run_subprocess([sys.executable, "-c", parent], timeout=3)
    assert returncode == exit_code
    assert stdout == "child output\n"
    assert stderr == ""


async def test_stdout_observer_keeps_split_utf8_after_stderr_fills_log_budget() -> None:
    observed: list[str] = []
    script = (
        "import os, time\n"
        "os.write(2, b'x' * 4096)\n"
        "time.sleep(0.05)\n"
        "os.write(1, b'caf\\xc3')\n"
        "time.sleep(0.05)\n"
        "os.write(1, b'\\xa9')\n"
    )
    code, stdout, stderr = await run_subprocess(
        [sys.executable, "-c", script],
        max_output_bytes=1024,
        on_stdout=observed.append,
    )
    assert code == 0
    assert "subprocess output truncated" in stderr
    assert stdout == ""
    assert "".join(observed) == "café"
