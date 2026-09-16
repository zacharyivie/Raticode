from __future__ import annotations

import json
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from gofer.core.agent import AgentResult
from gofer.core.provider_profiles import ResolvedProviderSettings
from gofer.subscriptions.base import Subscription
from gofer.utils.paths import get_data_dir


@pytest.fixture(autouse=True)
def isolated_gofer_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Keep tests from touching the developer's real Raticode data directory."""
    env_root = tmp_path / "gofer-env"
    home = env_root / "home"
    xdg_data = env_root / "xdg-data"
    appdata = env_root / "appdata"
    local_appdata = env_root / "local-appdata"
    for path in (home, xdg_data, appdata, local_appdata):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("NVM_DIR", str(home / ".nvm"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))

    return get_data_dir()


class FakeSubscription(Subscription):
    def __init__(
        self,
        output: str = "ok",
        exit_code: int = 0,
        thoughts: list[str] | None = None,
        message: str | None = None,
        on_execute: Callable[[Path], Awaitable[None] | None] | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self._output = output
        self._exit_code = exit_code
        self._thoughts = thoughts or []
        self._message = message
        self._on_execute = on_execute

    def _build_command(
        self,
        prompt: str,
        tools: list[str],
        mcp_servers: list[str],
        extra_paths: list[Path] | None = None,
        provider_settings: ResolvedProviderSettings | None = None,
    ) -> list[str]:
        return ["fake"]

    def is_available(self) -> bool:
        return True

    async def execute(
        self,
        prompt: str,
        working_dir: Path,
        tools: list[str],
        mcp_servers: list[str],
        env: dict[str, str],
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
        extra_paths: list[Path] | None = None,
        max_output_bytes: int | None = None,
        on_thought: Callable[[str], None] | None = None,
        provider_settings: ResolvedProviderSettings | None = None,
    ) -> AgentResult:
        if self._on_execute is not None:
            callback_result = self._on_execute(working_dir)
            if callback_result is not None:
                await callback_result
        self.calls.append(
            {
                "prompt": prompt,
                "working_dir": working_dir,
                "tools": tools,
                "mcp_servers": mcp_servers,
                "env": env,
                "extra_paths": extra_paths or [],
                "max_output_bytes": max_output_bytes,
                "provider_settings": provider_settings,
            }
        )
        return AgentResult(
            agent_id="",
            success=self._exit_code == 0,
            output=self._output,
            exit_code=self._exit_code,
            duration_seconds=0.0,
            thoughts=self._thoughts,
            message=self._message,
        )


@pytest.fixture
def fake_subscription() -> FakeSubscription:
    return FakeSubscription()


def envelope_request(prompt: object) -> str:
    """Extract task text for test doubles that branch on workflow input values."""
    text = str(prompt)
    if text.startswith("Execute the agent node request."):
        return str(json.loads(text.split("\n\n", 1)[1])["request"])
    return text
