from __future__ import annotations

import os
from pathlib import Path

import pytest

from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.codex import CodexSubscription


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,binary", [("codex", "codex"), ("claude_code", "claude")])
async def test_workflow_launch_adds_nvm_node_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider, binary
) -> None:
    executable = tmp_path / "versions/node/v22.12.0/bin" / binary
    executable.parent.mkdir(parents=True)
    executable.touch()
    executable.chmod(0o755)
    monkeypatch.setenv("NVM_DIR", str(tmp_path))
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr("gofer.core.provider_capabilities.shutil.which", lambda _: None)
    calls = []

    async def stream(command, **kwargs):
        calls.append((command, kwargs))
        yield {"type": "exit", "stream": None, "text": "", "returncode": 0}

    monkeypatch.setattr("gofer.subscriptions.base.stream_subprocess", stream)
    subscription = CodexSubscription() if provider == "codex" else ClaudeCodeSubscription()
    await subscription.execute("hello", tmp_path, [], [], {})
    assert calls[0][0][0] == str(executable)
    assert calls[0][1]["env"]["PATH"].split(os.pathsep)[0] == str(executable.parent)
