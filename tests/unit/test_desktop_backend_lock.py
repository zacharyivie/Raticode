"""The desktop app's backend and a headless one share the data folder's lock.

With the desktop app open, `serve --headless` refuses to start beside it and the CLI's
errors say the app is running instead of suggesting a second backend.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import socket
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli.client import AGENT_SESSION_ENVS, CLI_BACKEND_DOCS_URL, ExitCode
from gofer.cli.main import app
from gofer.ui import headless
from gofer.utils.paths import DATA_DIR_ENV

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean_client_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Tests run inside Rem sessions too; each starts as the user's own terminal.
    for name in (
        "RATICODE_URL",
        "RATICODE_SOCKET",
        "RATICODE_TOKEN",
        "RATICODE_TOKEN_FILE",
        "RATICODE_DATA_DIR",
        *AGENT_SESSION_ENVS,
    ):
        monkeypatch.delenv(name, raising=False)
    # `serve` points this at its --data-dir; keep that from leaking into other tests.
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "unused"))


@pytest.fixture
def desktop_lock(tmp_path: Path):
    """Hold the data folder's lock the way the desktop app's backend does."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    descriptor = headless.hold_backend_lock(data_dir, kind="desktop")
    yield data_dir
    headless.release_backend_lock(descriptor)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _api_error(data_dir: Path, endpoint: str) -> tuple[int, str]:
    result = runner.invoke(
        app, ["api", "GET", "/health", "--endpoint", endpoint, "--data-dir", str(data_dir)]
    )
    return result.exit_code, json.loads(result.stdout)["error"]["message"]


def test_the_lock_record_holds_only_the_kind_and_process(desktop_lock: Path) -> None:
    record = headless.backend_record_path(desktop_lock)
    assert json.loads(record.read_text(encoding="utf-8")) == {
        "kind": "desktop",
        "pid": os.getpid(),
    }
    if sys.platform != "win32":
        assert stat.S_IMODE(record.stat().st_mode) == 0o600
    assert headless.backend_lock_holder(desktop_lock) == "desktop"


def test_headless_serve_refuses_while_the_desktop_app_holds_the_data_folder(
    desktop_lock: Path,
) -> None:
    result = runner.invoke(
        app, ["serve", "--headless", "--port", "0", "--data-dir", str(desktop_lock)]
    )
    assert result.exit_code != 0
    message = " ".join(result.stderr.split())
    assert "The Raticode desktop app is using the data folder" in message
    assert str(desktop_lock.resolve()) in message
    assert "Quit it first, or give this backend its own --data-dir." in message
    # Nothing the desktop app would share was written.
    assert not (desktop_lock / "headless" / "owner-token").exists()
    assert not (desktop_lock / "headless" / "endpoint.json").exists()


def test_cli_errors_name_the_desktop_app_instead_of_serve_headless(
    desktop_lock: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATICODE_TOKEN", "t" * 32)
    code, message = _api_error(desktop_lock, f"http://127.0.0.1:{_free_port()}")
    assert code == ExitCode.UNREACHABLE
    assert "The Raticode desktop app is running with the data folder" in message
    assert str(desktop_lock) in message
    assert "doesn't share its address or token with the CLI yet" in message
    assert CLI_BACKEND_DOCS_URL in message
    assert "serve --headless" not in message

    class Unauthorized(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps({"error": "Unauthorized"}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Unauthorized)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.delenv("RATICODE_TOKEN")
        url = f"http://127.0.0.1:{server.server_address[1]}"
        code, message = _api_error(desktop_lock, url)
    finally:
        server.shutdown()
        server.server_close()
    assert code == ExitCode.AUTH
    assert "The Raticode desktop app is running with the data folder" in message
    assert CLI_BACKEND_DOCS_URL in message
    assert "serve --headless" not in message
    assert "start a headless backend" not in message


def test_cli_errors_stay_as_they_were_without_a_live_desktop_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATICODE_TOKEN", "t" * 32)
    data_dir = tmp_path / "data"
    descriptor = headless.hold_backend_lock(data_dir, kind="desktop")
    headless.release_backend_lock(descriptor)
    # The record stays after the app quits (or crashes) but means nothing without the lock.
    assert headless.backend_record_path(data_dir).exists()
    assert headless.backend_lock_holder(data_dir) is None
    _, message = _api_error(data_dir, f"http://127.0.0.1:{_free_port()}")
    assert "Raticode serve --headless" in message and "desktop app" not in message

    # A held lock whose record names a process that is gone isn't the desktop app.
    descriptor = headless.hold_backend_lock(data_dir, kind="desktop")
    try:
        headless.write_private_file(
            headless.backend_record_path(data_dir),
            json.dumps({"kind": "desktop", "pid": 2**22 + 7}),
        )
        assert headless.backend_lock_holder(data_dir) == "unknown"
        _, message = _api_error(data_dir, f"http://127.0.0.1:{_free_port()}")
        assert "Raticode serve --headless" in message and "desktop app" not in message
    finally:
        headless.release_backend_lock(descriptor)

    # A folder with no lock file isn't created just by asking.
    empty = tmp_path / "empty"
    assert headless.backend_lock_holder(empty) is None
    assert not empty.exists()


def test_the_desktop_backend_takes_the_lock_and_names_a_headless_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gofer.ui import server as ui

    monkeypatch.setattr(ui, "DESKTOP_LOCK_WAIT_SECONDS", 0.2)
    data_dir = tmp_path / "data"
    descriptor = headless.hold_backend_lock(data_dir, kind="headless")
    try:
        with pytest.raises(headless.HeadlessError) as refused:
            ui.serve(host="127.0.0.1", port=0, data_dir=data_dir)
    finally:
        headless.release_backend_lock(descriptor)
    message = str(refused.value)
    assert "A headless Raticode backend is already running with the data folder" in message
    # The desktop app picks its data folder in Settings, not with a flag.
    assert "Settings > General" in message and "--data-dir" not in message


def test_a_restarted_desktop_backend_waits_for_the_old_one_to_free_the_lock(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    old = headless.hold_backend_lock(data_dir, kind="desktop")
    with pytest.raises(headless.HeadlessError, match="desktop app is using"):
        headless.hold_backend_lock(data_dir, kind="desktop")
    releaser = threading.Timer(0.3, headless.release_backend_lock, args=(old,))
    releaser.start()
    started = time.monotonic()
    new = headless.hold_backend_lock(data_dir, kind="desktop", wait=5)
    try:
        assert time.monotonic() - started >= 0.2
        assert headless.backend_lock_holder(data_dir) == "desktop"
    finally:
        releaser.join()
        headless.release_backend_lock(new)


def _flock_fails_with(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    import fcntl

    def flock(descriptor: int, operation: int) -> None:
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(fcntl, "flock", flock)


@pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX; Windows uses msvcrt")
@pytest.mark.parametrize("code", ["ENOLCK", "EOPNOTSUPP"])
def test_a_data_folder_whose_file_system_cant_lock_still_starts_the_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, code: str
) -> None:
    # Some NFS and SMB mounts answer flock this way; nobody else holds the folder.
    _flock_fails_with(monkeypatch, getattr(errno, code))
    data_dir = tmp_path / "data"
    with caplog.at_level(logging.WARNING, logger=headless.__name__):
        descriptor = headless.hold_backend_lock(data_dir, kind="desktop", wait=0.2)
    try:
        assert any("can't be locked" in record.getMessage() for record in caplog.records)
        # The record names this live process, so the CLI can still say the app holds it.
        assert headless.backend_lock_holder(data_dir) == "desktop"
    finally:
        headless.release_backend_lock(descriptor)


@pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX; Windows uses msvcrt")
@pytest.mark.parametrize("code", ["EWOULDBLOCK", "EAGAIN", "EACCES"])
def test_a_lock_another_backend_holds_still_refuses_a_second_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    _flock_fails_with(monkeypatch, getattr(errno, code))
    with pytest.raises(headless.HeadlessError, match="already running"):
        headless.hold_backend_lock(tmp_path / "data", kind="desktop")
