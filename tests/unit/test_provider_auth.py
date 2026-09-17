from __future__ import annotations

import subprocess
import threading
from unittest.mock import MagicMock

import pytest

from gofer.core import provider_auth as auth


@pytest.fixture
def login(monkeypatch):
    monkeypatch.setattr(auth, "resolve_provider_executable", lambda provider: "/test/provider")
    process = MagicMock()
    process.poll.return_value = None
    process.wait.return_value = 0
    spawn = MagicMock(return_value=process)
    monkeypatch.setattr(auth.subprocess, "Popen", spawn)
    # Exercise completion deterministically, without invoking real provider CLIs.
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    return auth.ProviderAuthSessions(), process, spawn


@pytest.mark.parametrize("provider", ["cursor", "codex", "claude_code", "copilot"])
def test_browser_login_uses_provider_cli_and_same_config(login, monkeypatch, provider):
    sessions, process, spawn = login
    monkeypatch.setenv("XDG_CONFIG_HOME", "/isolated/config")
    monkeypatch.setenv("NO_OPEN_BROWSER", "1")
    assert sessions.start(provider) == {"status": "pending"}
    assert spawn.call_args.args[0] == ["/test/provider", *auth.BROWSER_LOGIN_COMMANDS[provider]]
    assert spawn.call_args.kwargs["env"]["XDG_CONFIG_HOME"] == "/isolated/config"
    assert "NO_OPEN_BROWSER" not in spawn.call_args.kwargs["env"]
    assert spawn.call_args.kwargs["stdout"] == subprocess.DEVNULL
    sessions.start(provider)
    assert spawn.call_count == 1
    sessions._wait(provider, process)
    assert sessions.status(provider) == {"status": "complete"}


def test_failed_login_can_retry(login):
    sessions, process, spawn = login
    process.wait.return_value = 1
    sessions.start("cursor")
    sessions._wait("cursor", process)
    assert sessions.status("cursor")["status"] == "error"
    sessions.start("cursor")
    assert spawn.call_count == 2


def test_timeout_and_cancel_stop_process_without_stale_completion(login, monkeypatch):
    sessions, process, _ = login
    stopped = []
    monkeypatch.setattr(sessions, "_stop", lambda child: stopped.append(child))
    sessions.start("cursor")
    process.wait.side_effect = subprocess.TimeoutExpired("login", 300)
    sessions._wait("cursor", process)
    assert "timed out" in sessions.status("cursor")["error"]
    sessions.start("cursor")
    assert sessions.cancel("cursor") == {"status": "idle"}
    sessions._wait("cursor", process)
    assert sessions.status("cursor") == {"status": "idle"}
    assert stopped
    sessions.start("codex")
    sessions.close()
    assert sessions.status("codex") == {"status": "idle"}


def test_unknown_and_missing_providers_never_spawn(login, monkeypatch):
    sessions, _, spawn = login
    with pytest.raises(ValueError):
        sessions.start("cursor; echo injection")
    monkeypatch.setattr(auth, "resolve_provider_executable", lambda provider: None)
    with pytest.raises(ValueError):
        sessions.start("cursor")
    spawn.assert_not_called()
