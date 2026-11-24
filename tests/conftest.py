from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import pytest

from gofer.core import command_slots, model_lifecycle, secrets_folder
from gofer.core.agent import AgentResult
from gofer.core.provider_profiles import ResolvedProviderSettings
from gofer.subscriptions.base import Subscription
from gofer.utils import process
from gofer.utils.paths import get_data_dir

# Lets tests marked ``serial`` run alone under ``pytest -n``; see the module.
pytest_plugins = ["tests.helpers.serial_tests"]

TESTS = Path(__file__).resolve().parent


def checkouts(here: Path = TESTS.parent) -> list[Path]:
    """This checkout and, from a worktree, the main checkout that holds it."""
    roots = [here]
    try:
        common = subprocess.run(
            ["git", "-C", str(here), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        common = ""
    if common and Path(common).name == ".git":
        roots.append(Path(common).resolve().parent)
    return roots


def temp_inside_checkout(temp: Path, roots: list[Path]) -> Path | None:
    temp = temp.resolve()
    return next((root for root in roots if temp.is_relative_to(root)), None)


def pytest_configure(config: pytest.Config) -> None:
    """Refuse a temp folder inside the repository.

    pytest's temp folders there show up as changes in the checkout the user's
    app runs from, and organization and kanban tests fail because their
    projects land inside a Raticode worktree. Use a folder outside instead.
    """
    temp = Path(config.option.basetemp or tempfile.gettempdir())
    root = temp_inside_checkout(temp, checkouts())
    if root is not None:
        raise pytest.UsageError(
            f"Test temp folder {temp} is inside the Raticode checkout {root}. "
            "Set TMPDIR (or --basetemp) to a folder outside the repository, "
            "such as ~/.cache/<task>-tmp."
        )


@pytest.fixture(autouse=True)
def fake_cli_arguments() -> Iterator[None]:
    """Fake provider CLIs see the arguments their tests expect.

    Codex's credential profile and the bubblewrap mask for unsandboxed providers rewrite
    the command; test_agent_credentials.py turns both back on with ``credential_guards``.
    Set and restored directly, so it doesn't change when ``monkeypatch`` is set up.
    """
    from gofer.core import codex_sandbox, credential_mask

    codex_sandbox.ENABLED = credential_mask.ENABLED = False
    try:
        yield
    finally:
        codex_sandbox.ENABLED = credential_mask.ENABLED = True


@pytest.fixture(autouse=True)
def host_independent_worker_room() -> Iterator[None]:
    """New workers see a computer with free memory and no CPU or memory pressure.

    Every start waits while the host is short of memory (command_slots.memory_hold),
    and swarm turns and incoming work claim against a limit lowered by the host's
    pressure (computers.local_room_limit), so a busy test machine would hold workers
    back. Tests of either set their own readings. Set and restored directly, like
    ``fake_cli_arguments``.
    """
    from gofer.core import command_slots, computers

    hold, pressure = command_slots.memory_hold, computers.local_pressure
    command_slots.memory_hold = lambda: None
    computers.local_pressure = lambda: (None, None)
    try:
        yield
    finally:
        command_slots.memory_hold, computers.local_pressure = hold, pressure


@pytest.fixture
def credential_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    from gofer.core import codex_sandbox, credential_mask

    monkeypatch.setattr(codex_sandbox, "ENABLED", True)
    monkeypatch.setattr(credential_mask, "ENABLED", True)


@pytest.fixture(autouse=True)
def isolated_gofer_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Keep tests from touching the developer's real Raticode data directory."""
    env_root = tmp_path / "gofer-env"
    home = env_root / "home"
    xdg_data = env_root / "xdg-data"
    appdata = env_root / "appdata"
    local_appdata = env_root / "local-appdata"
    for path in (home, xdg_data, appdata, local_appdata):
        path.mkdir(parents=True, exist_ok=True)

    # Agents run tests from inside Raticode, which exports its own settings such as
    # the phone network opt-in, command slots and UI tokens. Tests set what they need.
    # Raticode's "remove Co-authored-by lines" setting also arrives as GIT_CONFIG_* git
    # config, which would rewrite every commit a test makes.
    for name in list(os.environ):
        if name.startswith(("RATICODE_", "GOFER_", "GIT_CONFIG_")) or name == "GIT_CONFIG":
            monkeypatch.delenv(name)
    monkeypatch.setenv("NVM_DIR", str(home / ".nvm"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    # Fake providers must not start inside the user's real systemd session.
    monkeypatch.setattr(command_slots, "_systemd_ready", False)
    # Nothing a test starts may open a window, notification, tray icon or wallet
    # prompt on the developer's desktop. Tests that need these set them explicitly.
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_PID"):
        monkeypatch.delenv(name, raising=False)
    # A systemd service's credentials folder must not become a test's secrets folder.
    monkeypatch.delenv(secrets_folder.CREDENTIALS_DIRECTORY_ENV, raising=False)
    # Credentials one test's startup sealed out of the environment stay with that test.
    monkeypatch.setattr(process, "_SEALED_CREDENTIALS", {})
    monkeypatch.setenv(
        "DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent/raticode-test-session-bus"
    )

    # Preflight asks Anthropic and OpenAI for their model lists when a key is set. Tests
    # are offline unless they replace this, and start with nothing remembered.
    def _offline(request: object) -> object:
        raise OSError("Tests don't reach provider APIs")

    monkeypatch.setattr(model_lifecycle, "_send", _offline)
    model_lifecycle.clear_cache()
    # Preflight checks an account profile's sign-in with the provider's own CLI. Tests
    # never start a real one: the state is unknown, which passes, unless a test sets it.
    from gofer.rattish import preflight

    monkeypatch.setattr(preflight, "account_sign_in_state", lambda name, data_dir: None)
    # Run records name the provider app's version. A real CLI on PATH must not add one,
    # so tests that check versions turn this on with a fake CLI of their own.
    from gofer.core import provider_capabilities

    monkeypatch.setattr(provider_capabilities, "APP_VERSION_READS", False)
    monkeypatch.setattr(provider_capabilities, "_app_versions", {})

    # A backend whose data folder is tmp_path puts its trusted tools folder in the
    # base folder that the test's neighbours share (one per pytest-xdist worker).
    # That marks the whole base folder as Raticode's own data, so remove it again
    # rather than let it change how later tests on the same worker see their paths.
    shared_trusted_bin = tmp_path.parent / ".gofer-trusted-bin"
    already_there = shared_trusted_bin.exists()
    yield get_data_dir()
    secrets_folder.configure_secrets_dir(None)
    if not already_there and shared_trusted_bin.is_dir():
        shutil.rmtree(shared_trusted_bin, ignore_errors=True)


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
def secrets_dir(tmp_path: Path) -> Path:
    """A private headless secrets folder, configured for this test only."""
    folder = tmp_path / "secrets"
    folder.mkdir(mode=0o700)
    secrets_folder.configure_secrets_dir(folder)
    return folder


@pytest.fixture
def fake_subscription() -> FakeSubscription:
    return FakeSubscription()


def envelope_request(prompt: object) -> str:
    """Extract task text for test doubles that branch on workflow input values."""
    text = str(prompt)
    if text.startswith("Execute the agent node request."):
        return str(json.loads(text.split("\n\n", 1)[1])["request"])
    return text
