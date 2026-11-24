"""No kind of agent run can read or write the folders Raticode keeps credentials in.

For each run kind this builds the real command or settings Raticode would start, works
out every place the run's commands may write and what they may read, and checks each
credential folder: Rem and worker turns on Codex, Claude Code (watched and unattended)
and the providers with no sandbox; workflow agent steps on Codex, Claude Code and the
others; commit-message and compaction calls. Where Codex and bubblewrap are installed,
the rules are also run for real.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gofer.core import agent_credentials, codex_sandbox, credential_mask, secrets_folder
from gofer.core.provider_profiles import ResolvedProviderSettings
from gofer.rattish import agent_sandbox
from gofer.subscriptions.codex import CodexSubscription
from gofer.ui import chat, claude_sandbox

pytestmark = pytest.mark.usefixtures("credential_guards")


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("GOFER_DATA_DIR", str(data))
    # A Claude Code and a Codex account, each with its sign-in files.
    for provider, name in (
        ("claude-accounts", ".credentials.json"),
        ("codex-accounts", "auth.json"),
    ):
        account = data / provider / "0123456789abcdef"
        account.mkdir(parents=True)
        (account / name).write_text("secret", encoding="utf-8")
    return data


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Iterator[Path]:
    folder = tmp_path / "secrets"
    folder.mkdir(mode=0o700)
    (folder / "jev-key").write_text("key", encoding="utf-8")
    (folder / "jev-key").chmod(0o600)
    secrets_folder.configure_secrets_dir(folder)
    try:
        yield folder.resolve()
    finally:
        secrets_folder.configure_secrets_dir(None)


def expected_folders(data: Path, secrets: Path) -> list[Path]:
    return [
        data / "devices",
        data / "browser-grants",
        data / "claude-accounts",
        data / "codex-accounts",
        secrets,
    ]


def test_every_credential_folder_is_listed(data_dir: Path, secrets_dir: Path) -> None:
    folders = agent_credentials.credential_folders(data_dir)
    for folder in expected_folders(data_dir, secrets_dir):
        assert folder in folders
    assert agent_credentials.inside_credential_folder(data_dir / "codex-accounts" / "x")
    assert agent_credentials.inside_credential_folder(data_dir) is not None  # holds them
    assert agent_credentials.inside_credential_folder(data_dir / "chat-jobs") is None


# --- Codex ------------------------------------------------------------------------------


def codex_profile(command: list[str]) -> tuple[str, dict[str, Any], bool]:
    """The working folder, filesystem rules and network setting of a guarded command."""
    config = codex_sandbox.app_server_config(command)
    assert config is not None, f"Codex runs without the credential profile: {command}"
    assert config["default_permissions"] == codex_sandbox.PROFILE
    assert "--sandbox" not in command and "--add-dir" not in command
    cwd = command[command.index("--cd") + 1] if "--cd" in command else os.getcwd()
    rules = config[f"permissions.{codex_sandbox.PROFILE}.filesystem"]
    return cwd, rules, config[f"permissions.{codex_sandbox.PROFILE}.network.enabled"]


def codex_writes(cwd: str, rules: dict[str, Any]) -> list[Path]:
    writes = [Path(path) for path, access in rules.items() if access == "write"]
    if (rules.get(":workspace_roots") or {}).get(".") == "write":
        writes.append(Path(cwd))
    return writes


def assert_codex_hides(command: list[str], folders: list[Path]) -> None:
    cwd, rules, _network = codex_profile(command)
    denied = {Path(path) for path, access in rules.items() if access == "deny"}
    for folder in folders:
        # Denied outright, which Codex applies to reading and writing alike and which
        # wins over the broader writable folder it sits in.
        assert folder in denied, f"{folder} isn't denied in {rules}"
        for root in codex_writes(cwd, rules):
            assert not root.is_relative_to(folder), f"{root} is writable inside {folder}"


async def codex_chat_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data: Path, **options: Any
) -> list[str]:
    monkeypatch.setattr(chat.shutil, "which", lambda _binary: "/usr/bin/codex")
    observed: dict[str, Any] = {}

    async def fake_stream_subprocess(command: list[str], **kwargs: Any):
        observed["command"] = command
        yield {"type": "chunk", "stream": "stdout", "text": "done\n", "returncode": None}
        yield {"type": "exit", "stream": None, "text": "", "returncode": 0}

    monkeypatch.setattr(chat, "stream_subprocess", fake_stream_subprocess)
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    events = [
        event
        async for event in chat.stream_workflow_chat(
            provider="codex",
            model="cli-default",
            messages=[{"role": "user", "body": "hi"}],
            workflow=options.pop("workflow", None),
            working_dir=options.pop("working_dir", project),
            data_dir=data,
            **options,
        )
    ]
    assert events[-1]["type"] == "final"
    return list(observed["command"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        # A Rem thread or worker: the project plus the data folder are writable.
        {"permission_mode": "workspace-write"},
        # A turn with no project works in the data folder itself.
        {"permission_mode": "workspace-write", "use_data_dir": True},
        {"permission_mode": "workspace-write", "network": True},
        # A reviewer: read-only, moved to a scratch folder.
        {"permission_mode": "read-only", "scratch": True},
        # Swarm agents leave the data folder out.
        {"permission_mode": "workspace-write", "trust_data_dir": False},
    ],
    ids=["worker", "no-project", "network", "reviewer", "swarm"],
)
async def test_codex_turns_cannot_reach_credential_folders(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    data_dir: Path,
    secrets_dir: Path,
    options: dict[str, Any],
) -> None:
    options = dict(options)
    if options.pop("use_data_dir", False):
        options["working_dir"] = data_dir
    if options.pop("network", False):
        options["workflow"] = {"remManagedTask": {"networkAccess": True}}
    if options.pop("scratch", False):
        options["trusted_scratch_dir"] = tmp_path / "scratch"
    command = await codex_chat_command(monkeypatch, tmp_path, data_dir, **options)
    assert_codex_hides(command, expected_folders(data_dir, secrets_dir))
    cwd, rules, network = codex_profile(command)
    if options.get("trust_data_dir", True) and options["permission_mode"] == "workspace-write":
        # The rest of the data folder stays writable, as before.
        assert data_dir in codex_writes(cwd, rules)
    assert network is ("workflow" in options)


def codex_access(cwd: str, rules: dict[str, Any], path: Path) -> str:
    """What Codex lets a command do at ``path``: the most specific entry wins, and on a
    tie deny beats write beats read (Codex's permissions documentation).

    Temp folders are left out: every test path is in one.
    """
    entries = [(Path(key), access) for key, access in rules.items() if not key.startswith(":")]
    if (rules.get(":workspace_roots") or {}).get(".") == "write":
        entries.append((Path(cwd), "write"))
    rank = {"read": 0, "write": 1, "deny": 2}
    best: tuple[tuple[int, int], str] = ((-1, -1), "read")
    for root, access in entries:
        if path.is_relative_to(root) and (len(root.parts), rank[access]) > best[0]:
            best = ((len(root.parts), rank[access]), access)
    return best[1]


def fake_slot_environment(monkeypatch: pytest.MonkeyPatch, data: Path) -> tuple[Path, Path]:
    """Queued tests report progress in activity/ and wait in queue/, as on a real computer."""
    from gofer.core import command_slots

    root = command_slots.slots_root(data)
    for name in ("bin", "slots", "queue", "activity"):
        (root / name).mkdir(parents=True, exist_ok=True)
    activity = command_slots.activity_path(root, "thread")

    def environment(*_args: Any, **_kwargs: Any) -> dict[str, str]:
        return {command_slots.ROOT_ENV: str(root), command_slots.ACTIVITY_ENV: str(activity)}

    monkeypatch.setattr(chat, "_command_slot_environment", environment)
    return root / "activity", root / "queue"


@pytest.mark.asyncio
@pytest.mark.parametrize("projectless", [False, True], ids=["project", "no-project"])
async def test_unwatched_codex_turns_write_the_data_folder_only_where_sandboxed_claude_can(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    data_dir: Path,
    secrets_dir: Path,
    projectless: bool,
) -> None:
    activity, queue = fake_slot_environment(monkeypatch, data_dir)
    scratch = data_dir / "rem-work" / "turn-scratch" / "run-execute"
    # A Rem task with no project works in its own folder inside the data folder.
    work = data_dir / "rem-work" / "task" if projectless else tmp_path / "project"
    work.mkdir(parents=True, exist_ok=True)
    # A folder the task's tools granted inside the data folder stays read-only too.
    granted = data_dir / "library" / "notes"
    granted.mkdir(parents=True)
    command = await codex_chat_command(
        monkeypatch,
        tmp_path,
        data_dir,
        permission_mode="workspace-write",
        working_dir=work,
        trusted_scratch_dir=scratch,
        workflow={
            "projectRoot": str(work),
            "remManagedTask": {"unattended": True, "limitDataFolder": True, "runId": "run"},
            "filesystemAccess": [{"path": str(granted)}],
        },
    )
    assert_codex_hides(command, expected_folders(data_dir, secrets_dir))
    cwd, rules, _network = codex_profile(command)
    assert rules[str(data_dir)] == "read"
    for path in (
        data_dir,
        data_dir / "usage.sqlite3",
        data_dir / "rem" / "rem-home.sqlite3",
        data_dir / "command-slots" / "slots",
        data_dir / "command-slots" / "bin" / "pytest",
        data_dir / "rem-work" / "turn-scratch" / "another-run",
        granted,
    ):
        assert codex_access(cwd, rules, path) == "read", f"{path} is writable: {rules}"
    for path in (scratch / "tmp" / "x", activity / "digest", queue / "test" / "ticket", work / "f"):
        assert codex_access(cwd, rules, path) == "write", f"{path} isn't writable: {rules}"
    for folder in expected_folders(data_dir, secrets_dir):
        assert codex_access(cwd, rules, folder / "x") == "deny"


@pytest.mark.asyncio
async def test_codex_reviews_write_only_their_scratch_folder_in_the_data_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data_dir: Path, secrets_dir: Path
) -> None:
    activity, queue = fake_slot_environment(monkeypatch, data_dir)
    scratch = data_dir / "rem-work" / "review-scratch" / "turn"
    command = await codex_chat_command(
        monkeypatch,
        tmp_path,
        data_dir,
        permission_mode="read-only",
        trusted_scratch_dir=scratch,
        workflow={"remManagedTask": {"readOnly": True, "limitDataFolder": True, "runId": "run"}},
    )
    assert_codex_hides(command, expected_folders(data_dir, secrets_dir))
    cwd, rules, _network = codex_profile(command)
    assert Path(cwd) == scratch
    assert codex_access(cwd, rules, data_dir / "usage.sqlite3") == "read"
    assert codex_access(cwd, rules, tmp_path / "project" / "f") == "read"
    for path in (scratch / "x", activity / "digest", queue / "test" / "ticket"):
        assert codex_access(cwd, rules, path) == "write"


def test_codex_keeps_the_data_folder_writable_where_its_profile_cant_apply(
    data_dir: Path,
) -> None:
    command = ["codex", "exec", "--sandbox", "workspace-write", "--add-dir", str(data_dir), "hi"]
    # Native Windows keeps Codex's older settings, data folder and all.
    assert (
        codex_sandbox.credential_guard(
            command, data_dir=data_dir, data_writable=[], platform="win32"
        )
        == command
    )
    # A read-only turn without a scratch folder writes nowhere, as before.
    read_only = ["codex", "exec", "--sandbox", "read-only", "hi"]
    guarded = codex_sandbox.credential_guard(read_only, data_dir=data_dir, data_writable=[])
    _cwd, rules, _network = codex_profile(guarded)
    assert [access for access in rules.values() if access == "write"] == []


def test_codex_steering_starts_the_app_server_with_the_same_profile(
    data_dir: Path, secrets_dir: Path
) -> None:
    command = codex_sandbox.credential_guard(
        ["codex", "exec", "--sandbox", "workspace-write", "--add-dir", str(data_dir), "hi"],
        data_dir=data_dir,
    )
    config = codex_sandbox.app_server_config(command)
    assert config is not None and config["default_permissions"] == "raticode"
    denied = config["permissions.raticode.filesystem"]
    assert denied[str(data_dir / "claude-accounts")] == "deny"
    assert denied[str(secrets_dir)] == "deny"


def test_codex_workflow_steps_hide_credentials_and_home_secrets(
    data_dir: Path, secrets_dir: Path, tmp_path: Path
) -> None:
    home_ssh = Path.home() / ".ssh"
    home_ssh.mkdir(exist_ok=True)
    extra = tmp_path / "extra"
    extra.mkdir()
    command = CodexSubscription()._build_command(
        "prompt", [], [], [extra, data_dir], ResolvedProviderSettings(subscription="codex")
    )
    assert_codex_hides(command, [*expected_folders(data_dir, secrets_dir), home_ssh])
    assert extra in codex_writes(*codex_profile(command)[:2])


def test_codex_commit_messages_and_full_access(data_dir: Path, secrets_dir: Path) -> None:
    from gofer.ui import commit_message

    command = commit_message.commit_message_command("codex", "m", None, "p", "codex", data_dir)
    assert_codex_hides(command, expected_folders(data_dir, secrets_dir))
    _, rules, _ = codex_profile(command)
    assert codex_writes(command[command.index("--cd") + 1], rules) == []

    # Full access has no sandbox to change; preflight and the profile report that.
    for unsandboxed in (
        ["codex", "exec", "--sandbox", "danger-full-access", "hi"],
        ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "hi"],
    ):
        assert codex_sandbox.credential_guard(unsandboxed, data_dir=data_dir) == unsandboxed


def test_codex_keeps_its_older_settings_when_a_config_sets_sandbox_mode(
    tmp_path: Path, data_dir: Path
) -> None:
    home = tmp_path / "codex-home"
    home.mkdir()
    (home / "config.toml").write_text('sandbox_mode = "workspace-write"\n', encoding="utf-8")
    command = ["codex", "exec", "--sandbox", "workspace-write", "hi"]
    assert codex_sandbox.credential_guard(command, data_dir=data_dir, codex_home=home) == command
    assert codex_sandbox.legacy_reason(command, codex_home=home) is not None
    assert codex_sandbox.credential_guard(command, data_dir=data_dir, platform="win32") == command


def test_codex_guard_creates_missing_account_folders_so_they_can_be_denied(
    tmp_path: Path,
) -> None:
    data = tmp_path / "fresh-data"
    data.mkdir()
    command = codex_sandbox.credential_guard(
        ["codex", "exec", "--sandbox", "workspace-write", "--add-dir", str(data), "hi"],
        data_dir=data,
    )
    # A turn can't create claude-accounts first and leave a settings.json in it.
    for name in ("claude-accounts", "codex-accounts", "devices", "browser-grants"):
        assert (data / name).is_dir()
        assert (data / name).stat().st_mode & 0o077 == 0
    assert_codex_hides(command, [data / "claude-accounts", data / "codex-accounts"])


def test_codex_guard_keeps_the_prompt_and_resume_arguments(data_dir: Path) -> None:
    command = [
        "codex", "exec", "--sandbox", "workspace-write", "--json", "--cd", "/p",
        "-c", codex_sandbox.NETWORK_ON, "resume", "abc", "--add-dir=/x", "the prompt",
    ]  # fmt: skip
    guarded = codex_sandbox.credential_guard(command, data_dir=data_dir)
    assert guarded[-1] == "the prompt"
    assert guarded[guarded.index("resume") + 1] == "abc"
    _, rules, network = codex_profile(guarded)
    assert network is True and rules["/x"] == "write"
    assert codex_sandbox.credential_guard(guarded, data_dir=data_dir) == guarded


# --- Claude Code ------------------------------------------------------------------------


def claude_settings(argv: list[str]) -> dict[str, Any]:
    settings: dict[str, Any] = json.loads(argv[argv.index("--settings") + 1])
    return settings


def assert_claude_tools_denied(settings: dict[str, Any], folders: list[Path]) -> None:
    deny = settings["permissions"]["deny"]
    for folder in folders:
        spec = "/" + str(folder)
        for tool in ("Read", "Edit"):
            assert f"{tool}({spec})" in deny and f"{tool}({spec}/**)" in deny, (tool, folder)


@pytest.mark.asyncio
@pytest.mark.parametrize("unattended", [False, True], ids=["watched", "unattended"])
async def test_claude_turns_cannot_reach_credential_folders(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    data_dir: Path,
    secrets_dir: Path,
    unattended: bool,
) -> None:
    monkeypatch.setattr(chat.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(credential_mask.sys, "platform", "linux")
    monkeypatch.setattr(claude_sandbox, "unavailable_reason", lambda *a, **k: None)
    observed: dict[str, Any] = {}

    async def fake_stream_subprocess(command: list[str], **kwargs: Any):
        observed["command"] = command
        yield {"type": "chunk", "stream": "stdout", "text": "done\n", "returncode": None}
        yield {"type": "exit", "stream": None, "text": "", "returncode": 0}

    monkeypatch.setattr(chat, "stream_subprocess", fake_stream_subprocess)
    project = tmp_path / "project"
    project.mkdir()
    workflow = {"remManagedTask": {"claudeSandbox": True}} if unattended else None
    events = [
        event
        async for event in chat.stream_workflow_chat(
            provider="claude_code",
            model="cli-default",
            messages=[{"role": "user", "body": "hi"}],
            workflow=workflow,
            working_dir=project,
            data_dir=data_dir,
            trusted_scratch_dir=tmp_path / "scratch",
        )
    ]
    assert events[-1]["type"] == "final"
    argv = observed["command"]
    settings = claude_settings(argv)
    folders = expected_folders(data_dir, secrets_dir)
    # Read, Edit and Write tools: denied in every turn.
    assert_claude_tools_denied(settings, folders)
    if not unattended:
        # No Claude Code sandbox: the whole CLI, its shell included, starts in the mask,
        # where each credential folder is an empty read-only folder.
        assert_masked(argv, folders)
        assert argv[argv.index("--") + 1] == "/usr/bin/claude"
        return
    # Claude Code's own sandbox hides them; no second layer.
    assert not credential_mask.is_masked(argv)
    # The shell: the data folder and the secrets folder are hidden, and nothing it may
    # write is inside a credential folder.
    filesystem = settings["sandbox"]["filesystem"]
    assert str(data_dir.resolve()) in filesystem["denyRead"]
    assert str(secrets_dir) in filesystem["denyRead"]
    for root in [*filesystem["allowWrite"], str(project)]:
        assert all(not Path(root).is_relative_to(folder) for folder in folders), root
    assert str(data_dir) not in argv[argv.index("--settings") :]  # no --add-dir after it
    assert "--add-dir" not in argv or str(data_dir) not in [
        argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"
    ]


def test_claude_workflow_steps_hide_credentials(
    data_dir: Path, secrets_dir: Path, tmp_path: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    settings = agent_sandbox.claude_step_settings(
        working_dir=work,
        data_dir=data_dir,
        scratch=tmp_path / "scratch",
        extra_paths=[tmp_path / "out"],
        network=[],
    )
    assert_claude_tools_denied(settings, expected_folders(data_dir, secrets_dir))
    filesystem = settings["sandbox"]["filesystem"]
    assert str(secrets_dir) in filesystem["denyRead"]
    assert str(data_dir.resolve()) in filesystem["denyRead"]


# --- Providers with no sandbox ----------------------------------------------------------


def assert_masked(command: list[str], folders: list[Path]) -> None:
    assert credential_mask.is_masked(command), command
    separator = command.index("--")
    mounts = command[:separator]
    for folder in folders:
        index = mounts.index(str(folder))
        assert mounts[index - 1] == "--tmpfs" and mounts[index + 1] == "--remount-ro"
    assert mounts[mounts.index("--proc") + 1] == "/proc"


@pytest.mark.asyncio
async def test_unsandboxed_providers_start_inside_the_mask(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path, secrets_dir: Path, tmp_path: Path
) -> None:
    from gofer.subscriptions import antigravity, cli_providers
    from gofer.subscriptions.base import UNSANDBOXED_CLI_PROVIDERS

    monkeypatch.setattr(credential_mask.shutil, "which", lambda name: f"/usr/bin/{name}")
    seen: list[list[str]] = []

    async def fake_stream_subprocess(command: list[str], **kwargs: Any):
        seen.append(command)
        yield {"type": "exit", "stream": None, "text": "", "returncode": 0}

    # Rem and worker turns on Cursor, Copilot and OpenCode.
    monkeypatch.setattr(cli_providers, "stream_subprocess", fake_stream_subprocess)
    async for _ in cli_providers.stream_cli(
        ["opencode", "run", "hi"], "opencode", cwd=tmp_path, env={}
    ):
        pass
    # Antigravity.
    monkeypatch.setattr(antigravity, "stream_subprocess", fake_stream_subprocess)
    async for _ in antigravity.stream_antigravity(
        "hi", cwd=tmp_path, executable="/usr/bin/agy", permission_mode="cli-managed"
    ):
        pass
    assert len(seen) == 2
    for command in seen:
        assert_masked(command, expected_folders(data_dir, secrets_dir))
    assert {"cursor", "copilot", "opencode", "antigravity", "grok"} <= UNSANDBOXED_CLI_PROVIDERS
    # Grok and other ACP providers start through the ACP transport, which masks too.
    source = Path(sys.modules["gofer.subscriptions.acp_transport"].__file__ or "")
    # It gives back only the account folder the child runs as (test_grok_accounts.py).
    assert "masked_command(command, keep=account_folders(child_env))" in source.read_text(
        encoding="utf-8"
    )


def test_mask_is_skipped_where_it_cant_run() -> None:
    command = ["opencode", "run"]
    assert credential_mask.masked_command(command, platform="darwin") == command
    assert credential_mask.masked_command(command, which=lambda _name: None) == command
    masked = credential_mask.masked_command(command, which=lambda name: f"/usr/bin/{name}")
    assert credential_mask.masked_command(masked) == masked


# --- Workflow files can't hand a step a credential folder -------------------------------


@pytest.mark.parametrize(
    "path",
    ["{data}/claude-accounts", "{data}/codex-accounts/0123456789abcdef", "{data}", "~/.ssh", "~"],
)
def test_steps_cant_be_given_a_credential_folder(data_dir: Path, tmp_path: Path, path: str) -> None:
    target = Path(os.path.expanduser(path.format(data=data_dir)))
    assert agent_sandbox.credential_extra_path(target, data_dir)
    with pytest.raises(agent_sandbox.SandboxError) as caught:
        agent_sandbox.prepare_step(
            "codex",
            {},
            ResolvedProviderSettings(subscription="codex"),
            working_dir=tmp_path,
            extra_paths=[target],
            data_dir=data_dir,
            run_id="run",
        )
    assert caught.value.code == "RATTISH_SANDBOX_CREDENTIAL_PATH"
    assert agent_sandbox.credential_extra_path(tmp_path / "notes", data_dir) is None


# --- The rules, enforced for real -------------------------------------------------------


def _codex_supports_profiles() -> bool:
    if sys.platform != "linux" or shutil.which("codex") is None:
        return False
    try:
        out = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    try:
        major, minor = (int(p) for p in out.stdout.split()[-1].split(".")[:2])
    except (IndexError, ValueError):
        return False
    return (major, minor) >= (0, 133)


def _skip_without_namespaces(result: subprocess.CompletedProcess[str], marker: str) -> None:
    """Skip when bubblewrap can't make a namespace here, as inside another sandbox."""
    error = result.stderr.lower()
    if marker not in result.stdout and any(
        sign in error for sign in ("bwrap", "namespace", "operation not permitted", "panicked")
    ):
        pytest.skip(f"bubblewrap can't start here: {result.stderr.strip()[:300]}")


@pytest.mark.skipif(not _codex_supports_profiles(), reason="needs Codex 0.133+ on Linux")
def test_codex_sandbox_really_keeps_commands_out(tmp_path: Path, data_dir: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    home = tmp_path / "codex-home"
    home.mkdir()
    guarded = codex_sandbox.credential_guard(
        ["codex", "exec", "--sandbox", "workspace-write", "--add-dir", str(data_dir), "x"],
        data_dir=data_dir,
        environ={"CODEX_HOME": str(home)},
    )
    config_args = [arg for i, arg in enumerate(guarded) if i and guarded[i - 1] == "-c"]
    settings = data_dir / "claude-accounts" / "0123456789abcdef" / "settings.json"
    auth = data_dir / "codex-accounts" / "0123456789abcdef" / "auth.json"
    script = (
        f"cat {auth} && echo READ-AUTH; "
        f"echo '{{\"hooks\":{{}}}}' > {settings} && echo PLANTED; "
        f"touch {data_dir}/chat-jobs-probe && echo DATA-OK"
    )
    result = subprocess.run(
        [
            "codex",
            "sandbox",
            *[x for arg in config_args for x in ("-c", arg)],
            "--",
            "sh",
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        cwd=work,
        env={**os.environ, "CODEX_HOME": str(home)},
        timeout=60,
    )
    _skip_without_namespaces(result, "DATA-OK")
    assert "DATA-OK" in result.stdout, result.stderr
    assert "READ-AUTH" not in result.stdout and "PLANTED" not in result.stdout
    assert not settings.exists()


@pytest.mark.skipif(not _codex_supports_profiles(), reason="needs Codex 0.133+ on Linux")
def test_codex_sandbox_really_keeps_unwatched_turns_out_of_the_data_folder(
    tmp_path: Path, data_dir: Path
) -> None:
    work = tmp_path / "work"
    work.mkdir()
    home = tmp_path / "codex-home"
    home.mkdir()
    scratch = data_dir / "rem-work" / "turn-scratch" / "run-execute"
    queue = data_dir / "command-slots" / "queue"
    for folder in (scratch, queue):
        folder.mkdir(parents=True)
    ledger = data_dir / "usage.sqlite3"
    ledger.write_text("ledger", encoding="utf-8")
    guarded = codex_sandbox.credential_guard(
        [
            "codex",
            "exec",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(work),
            "--add-dir",
            str(data_dir),
            "x",
        ],
        data_dir=data_dir,
        data_writable=[scratch, queue],
        environ={"CODEX_HOME": str(home)},
    )
    config_args = [arg for i, arg in enumerate(guarded) if i and guarded[i - 1] == "-c"]
    script = (
        f"cat {ledger} && echo READ-LEDGER; "
        f"echo planted >> {ledger} && echo WROTE-LEDGER; "
        f"touch {data_dir}/new-file && echo WROTE-DATA; "
        f"touch {scratch}/cache && echo SCRATCH-OK; "
        f"touch {queue}/ticket && echo QUEUE-OK; "
        f"touch {work}/edit && echo WORK-OK"
    )
    result = subprocess.run(
        [
            "codex",
            "sandbox",
            *[x for arg in config_args for x in ("-c", arg)],
            "--",
            "sh",
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        cwd=work,
        env={**os.environ, "CODEX_HOME": str(home)},
        timeout=60,
    )
    _skip_without_namespaces(result, "SCRATCH-OK")
    for allowed in ("READ-LEDGER", "SCRATCH-OK", "QUEUE-OK", "WORK-OK"):
        assert allowed in result.stdout, (allowed, result.stdout, result.stderr)
    assert "WROTE-LEDGER" not in result.stdout and "WROTE-DATA" not in result.stdout
    assert ledger.read_text(encoding="utf-8") == "ledger"
    assert not (data_dir / "new-file").exists()


@pytest.mark.skipif(
    credential_mask.unavailable_reason() is not None, reason="needs bubblewrap on Linux"
)
def test_bubblewrap_mask_really_hides_the_folders(data_dir: Path, secrets_dir: Path) -> None:
    settings = data_dir / "claude-accounts" / "0123456789abcdef" / "settings.json"
    script = (
        f"ls {data_dir}/codex-accounts; cat {secrets_dir}/jev-key && echo READ-KEY; "
        f"echo x > {settings} && echo PLANTED; touch {data_dir}/ok && echo DATA-OK"
    )
    command = credential_mask.masked_command(["sh", "-c", script], data_dir=data_dir)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except OSError as exc:  # pragma: no cover - user namespaces off
        pytest.skip(f"bubblewrap can't start here: {exc}")
    _skip_without_namespaces(result, "DATA-OK")
    assert "DATA-OK" in result.stdout
    assert "0123456789abcdef" not in result.stdout
    assert "READ-KEY" not in result.stdout and "PLANTED" not in result.stdout
    assert not settings.exists()
    assert (data_dir / "codex-accounts" / "0123456789abcdef" / "auth.json").exists()


@pytest.mark.skipif(
    credential_mask.unavailable_reason() is not None, reason="needs bubblewrap on Linux"
)
def test_a_turn_keeps_its_own_account_but_cant_plant_a_hook_in_it(data_dir: Path) -> None:
    own = data_dir / "claude-accounts" / "0123456789abcdef"
    other = data_dir / "claude-accounts" / "fedcba9876543210"
    other.mkdir()
    (other / ".credentials.json").write_text("other", encoding="utf-8")
    script = (
        f"cat {own}/.credentials.json && echo OWN-READ; "
        f"touch {own}/session.jsonl && echo OWN-WRITE; "
        f"echo '{{\"hooks\":{{}}}}' > {own}/settings.json && echo PLANTED; "
        f"cat {other}/.credentials.json && echo OTHER-READ"
    )
    command = credential_mask.mask_unsandboxed(
        "claude_code",
        ["sh", "-c", script],
        env={"CLAUDE_CONFIG_DIR": str(own)},
        data_dir=data_dir,
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    _skip_without_namespaces(result, "OWN-READ")
    # The CLI keeps its own sign-in and sessions...
    assert "OWN-READ" in result.stdout and "OWN-WRITE" in result.stdout, result.stderr
    # ...but not its settings, where a hook would run outside every sandbox, nor any
    # other account.
    assert "PLANTED" not in result.stdout and "OTHER-READ" not in result.stdout
    assert json.loads((own / "settings.json").read_text(encoding="utf-8")) == {}


def test_a_codex_accounts_missing_program_files_are_made_read_only_placeholders(
    data_dir: Path,
) -> None:
    """A full-access turn can't create config.toml, .env or rules for later runs."""
    own = data_dir / "codex-accounts" / "0123456789abcdef"
    for name in ("config.toml", ".env", "rules"):
        assert not (own / name).exists()
    args = credential_mask.mask_arguments([data_dir / "codex-accounts"], keep=[own])
    pairs = {(args[i], args[i + 1]) for i in range(len(args) - 1)}
    for name in ("config.toml", ".env", "rules"):
        assert ("--ro-bind", str(own / name)) in pairs, name
    assert (own / "config.toml").read_text(encoding="utf-8") == ""
    assert (own / ".env").read_text(encoding="utf-8") == ""
    assert (own / "rules").is_dir()
    assert (own / ".env").stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(
    credential_mask.unavailable_reason() is not None, reason="needs bubblewrap on Linux"
)
def test_a_full_access_codex_turn_cant_plant_config_in_its_own_account(data_dir: Path) -> None:
    own = data_dir / "codex-accounts" / "0123456789abcdef"
    script = (
        f"cat {own}/auth.json >/dev/null && echo OWN-READ; "
        f"echo 'notify=[\"sh\"]' > {own}/config.toml && echo PLANTED-CONFIG; "
        f"echo 'LD_PRELOAD=/x.so' > {own}/.env && echo PLANTED-ENV; "
        f"touch {own}/rules/allow.rules && echo PLANTED-RULES"
    )
    command = credential_mask.mask_unsandboxed(
        "codex", ["sh", "-c", script], env={"CODEX_HOME": str(own)}, data_dir=data_dir
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    _skip_without_namespaces(result, "OWN-READ")
    assert "OWN-READ" in result.stdout, result.stderr
    assert "PLANTED" not in result.stdout
    assert (own / "config.toml").read_text(encoding="utf-8") == ""
    assert (own / ".env").read_text(encoding="utf-8") == ""
    assert list((own / "rules").iterdir()) == []


@pytest.mark.asyncio
async def test_codex_turns_with_full_access_start_inside_the_mask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data_dir: Path, secrets_dir: Path
) -> None:
    monkeypatch.setattr(credential_mask.sys, "platform", "linux")
    command = await codex_chat_command(
        monkeypatch, tmp_path, data_dir, permission_mode="danger-full-access"
    )
    assert_masked(command, expected_folders(data_dir, secrets_dir))
    assert codex_sandbox.app_server_config(command) is None


@pytest.mark.asyncio
async def test_the_codex_app_server_starts_inside_the_mask_without_the_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data_dir: Path, secrets_dir: Path
) -> None:
    from gofer.ui import codex_steering

    monkeypatch.setattr(credential_mask.sys, "platform", "linux")
    monkeypatch.setattr(credential_mask.shutil, "which", lambda name: f"/usr/bin/{name}")
    started: list[list[str]] = []

    async def refuse(*args: str, **kwargs: Any) -> None:
        started.append(list(args))
        raise OSError("not started in tests")

    monkeypatch.setattr(codex_steering.asyncio, "create_subprocess_exec", refuse)
    for command in (
        ["codex", "exec", "--sandbox", "danger-full-access", "hi"],
        codex_sandbox.credential_guard(
            ["codex", "exec", "--sandbox", "workspace-write", "hi"], data_dir=data_dir
        ),
    ):
        with pytest.raises(OSError):
            async for _ in codex_steering.stream_codex_turn(
                command,
                control=codex_steering.CodexTurnControl(),
                cwd=tmp_path,
                cancel_event=None,
                max_output_bytes=None,
            ):
                pass
    full_access, profiled = started
    assert any(credential_mask.is_masked(full_access[i:]) for i in range(len(full_access)))
    assert not any(credential_mask.is_masked(profiled[i:]) for i in range(len(profiled)))


def test_a_codex_profile_cant_replace_the_sandbox_or_its_denials(data_dir: Path) -> None:
    for override in (
        'sandbox_mode="danger-full-access"',
        'default_permissions=":danger-full-access"',
        'permissions.raticode.filesystem={":root"="write"}',
    ):
        settings = ResolvedProviderSettings(subscription="codex", extra_args=["-c", override])
        problem = agent_sandbox.sandbox_problem("codex", {}, settings, triggered=False)
        assert problem is not None and override in problem.message
    # A command someone else chose a profile for isn't treated as guarded: it is masked.
    foreign = ["codex", "exec", "-c", 'default_permissions=":danger-full-access"', "hi"]
    assert codex_sandbox.credential_guard(foreign, data_dir=data_dir) == foreign
    assert credential_mask.needs_mask("codex", foreign)
    guarded = codex_sandbox.credential_guard(
        ["codex", "exec", "--sandbox", "workspace-write", "hi"], data_dir=data_dir
    )
    assert not credential_mask.needs_mask("codex", guarded)
    assert credential_mask.needs_mask("codex", [*guarded[:-1], "-c", 'sandbox_mode="x"', "hi"])


@pytest.mark.asyncio
@pytest.mark.parametrize("unattended", [False, True], ids=["watched", "unattended"])
async def test_claude_account_turns_start_only_raticodes_mcp_servers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data_dir: Path, unattended: bool
) -> None:
    account = data_dir / "claude-accounts" / "0123456789abcdef"
    monkeypatch.setattr(chat.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(claude_sandbox, "unavailable_reason", lambda *a, **k: None)
    monkeypatch.setattr(chat, "_account_env", lambda *a: {"CLAUDE_CONFIG_DIR": str(account)})
    observed: dict[str, Any] = {}

    async def fake_stream_subprocess(command: list[str], **kwargs: Any):
        observed["command"] = command
        yield {"type": "exit", "stream": None, "text": "", "returncode": 0}

    monkeypatch.setattr(chat, "stream_subprocess", fake_stream_subprocess)
    project = tmp_path / "project"
    project.mkdir()
    async for _ in chat.stream_workflow_chat(
        provider="claude_code",
        model="cli-default",
        messages=[{"role": "user", "body": "hi"}],
        workflow={"remManagedTask": {"claudeSandbox": True}} if unattended else None,
        working_dir=project,
        data_dir=data_dir,
        trusted_scratch_dir=tmp_path / "scratch",
        provider_profile="work",
    ):
        pass
    # A server a turn wrote into the account's .claude.json never starts.
    assert credential_mask.STRICT_MCP in observed["command"]


@pytest.mark.asyncio
async def test_claude_workflow_steps_on_an_account_ignore_its_mcp_servers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data_dir: Path
) -> None:
    from gofer.subscriptions import base
    from gofer.subscriptions.claude_code import ClaudeCodeSubscription

    seen: list[list[str]] = []

    async def fake_stream_subprocess(command: list[str], **kwargs: Any):
        seen.append(command)
        yield {"type": "exit", "stream": None, "text": "", "returncode": 0}

    monkeypatch.setattr(base, "stream_subprocess", fake_stream_subprocess)
    account = data_dir / "claude-accounts" / "0123456789abcdef"
    for env in ({"CLAUDE_CONFIG_DIR": str(account)}, {}):
        await ClaudeCodeSubscription().execute(
            "hi", working_dir=tmp_path, tools=[], mcp_servers=[], env=env
        )
    on_account, default = seen
    assert credential_mask.STRICT_MCP in on_account
    # The default sign-in (~/.claude) keeps the user's own servers.
    assert credential_mask.STRICT_MCP not in default


@pytest.mark.skipif(shutil.which("claude") is None, reason="needs the claude CLI")
def test_a_server_planted_in_an_accounts_claude_json_doesnt_start(tmp_path: Path) -> None:
    account = tmp_path / "claude-accounts" / "0123456789abcdef"
    account.mkdir(parents=True)
    script = tmp_path / "planted.sh"
    script.write_text(f"#!/bin/sh\ntouch {tmp_path}/STARTED\nexec cat\n", encoding="utf-8")
    script.chmod(0o700)
    # What `claude mcp add -s user` from a turn's shell writes.
    (account / ".claude.json").write_text(
        json.dumps({"mcpServers": {"probe": {"type": "stdio", "command": str(script)}}}),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "CLAUDE_CONFIG_DIR": str(account),
        "ANTHROPIC_API_KEY": "sk-ant-dummy",
        # Nothing listens here: no request leaves the computer.
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
    }
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)

    def run(command: list[str]) -> bool:
        """Whether the planted server starts within 20 seconds; the CLI is then stopped."""
        import signal
        import time

        marker = tmp_path / "STARTED"
        marker.unlink(missing_ok=True)
        process = subprocess.Popen(
            command, env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )  # fmt: skip
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not marker.exists():
                if process.poll() is not None:
                    break
                time.sleep(0.2)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)
        return marker.exists()

    command = ["claude", "-p", "hi", "--output-format", "json"]
    if not run(command):
        pytest.skip("this claude CLI didn't start the server even without the flag")
    assert not run(credential_mask.strict_mcp_args(command, env))


@pytest.mark.skipif(
    credential_mask.unavailable_reason() is not None, reason="needs bubblewrap on Linux"
)
def test_a_codex_turn_cant_loosen_its_accounts_rules(data_dir: Path) -> None:
    home = data_dir / "codex-accounts" / "0123456789abcdef"
    (home / "rules").mkdir()
    (home / "config.toml").write_text("", encoding="utf-8")
    script = (
        f"cat {home}/auth.json && echo OWN-READ; "
        f'echo \'prefix_rule(pattern=["curl"], decision="allow")\' > '
        f"{home}/rules/default.rules && echo RULE-PLANTED; "
        f"echo 'notify=[\"sh\"]' >> {home}/config.toml && echo CONFIG-PLANTED"
    )
    command = credential_mask.mask_unsandboxed(
        "codex",
        ["codex", "exec", "--sandbox", "danger-full-access", "x"],
        env={"CODEX_HOME": str(home)},
        data_dir=data_dir,
    )
    shell = [*command[: command.index("--") + 1], "sh", "-c", script]
    result = subprocess.run(shell, capture_output=True, text=True, timeout=60)
    _skip_without_namespaces(result, "OWN-READ")
    assert "OWN-READ" in result.stdout, result.stderr
    assert "RULE-PLANTED" not in result.stdout and "CONFIG-PLANTED" not in result.stdout
    assert not (home / "rules" / "default.rules").exists()


def test_profile_rules_are_valid_toml(data_dir: Path) -> None:
    command = codex_sandbox.credential_guard(
        ["codex", "exec", "--sandbox", "workspace-write", "x"], data_dir=data_dir
    )
    for index, arg in enumerate(command):
        if index and command[index - 1] == "-c":
            key, value = arg.split("=", 1)
            tomllib.loads(f"v = {value}")


# --- Codex denies one file at most ------------------------------------------------------


def _home_secret_files() -> list[Path]:
    home = Path.home()
    files = [home / ".claude.json", home / ".codex" / "auth.json", home / ".grok" / "auth.json"]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("secret", encoding="utf-8")
    return files


def _profile_files(command: list[str]) -> list[Path]:
    _, rules, _ = codex_profile(command)
    return [Path(p) for p, access in rules.items() if access == "deny" and Path(p).is_file()]


def test_codex_profile_denies_one_file_and_masks_the_rest(data_dir: Path) -> None:
    """Codex 0.156's bubblewrap can cover only one denied file (one data descriptor)."""
    claude_json, codex_auth, grok_auth = _home_secret_files()
    which = {"bwrap": "/usr/bin/bwrap"}.get
    command = CodexSubscription()._build_command(
        "prompt", [], [], [], ResolvedProviderSettings(subscription="codex")
    )
    # Codex signs in with its own auth.json, so only its profile can hide that file.
    assert _profile_files(command) == [codex_auth]
    if credential_mask.unavailable_reason() is None:
        assert credential_mask.is_masked(command)
        mounts = command[: command.index("--")]
        for hidden in (claude_json, grok_auth):
            assert ["--ro-bind", os.devnull, str(hidden)] == mounts[
                mounts.index(str(hidden)) - 2 : mounts.index(str(hidden)) + 1
            ]
        assert credential_mask.inner_command(command)[1] == "exec"
    masked = credential_mask.files_masked(
        ["codex", "exec", "-"], [claude_json, grok_auth], platform="linux", which=which
    )
    assert masked[:2] == ["/usr/bin/bwrap", "--die-with-parent"]
    assert masked[masked.index("--") + 1 :] == ["codex", "exec", "-"]
    assert credential_mask.inner_command(masked) == ["codex", "exec", "-"]


def test_codex_with_an_account_masks_the_default_sign_in_too(data_dir: Path) -> None:
    claude_json, codex_auth, grok_auth = _home_secret_files()
    account = data_dir / "codex-accounts" / "0123456789abcdef"
    (data_dir / "devices").mkdir()
    kept, outside = codex_sandbox.split_denied(
        [claude_json, codex_auth, grok_auth, data_dir / "devices"],
        codex_home=account,
        platform="linux",
    )
    # The account signs in from its own folder; ~/.codex/auth.json isn't Codex's own.
    assert data_dir / "devices" in kept
    assert len([path for path in kept if path.is_file()]) == 1
    assert len(outside) == 2
    # Codex's sandbox elsewhere covers files another way; all stay in the profile.
    kept, outside = codex_sandbox.split_denied(
        [claude_json, codex_auth, grok_auth], codex_home=account, platform="darwin"
    )
    assert outside == [] and len(kept) == 3


@pytest.mark.skipif(not _codex_supports_profiles(), reason="needs Codex 0.133+ on Linux")
def test_codex_really_starts_with_several_secret_files_and_cant_read_them(
    tmp_path: Path, data_dir: Path
) -> None:
    secrets = []
    for name in ("one.json", "two.json", "three.json"):
        path = tmp_path / "home-secrets" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"SECRET-{name}", encoding="utf-8")
        secrets.append(path)
    work = tmp_path / "work"
    work.mkdir()
    home = tmp_path / "codex-home"
    home.mkdir()
    guarded = codex_sandbox.credential_guard(
        ["codex", "exec", "--sandbox", "workspace-write", "x"],
        data_dir=data_dir,
        extra_denied=secrets,
        environ={"CODEX_HOME": str(home)},
    )
    inner = credential_mask.inner_command(guarded)
    prefix = guarded[: len(guarded) - len(inner)]
    config_args = [arg for i, arg in enumerate(inner) if i and inner[i - 1] == "-c"]
    script = "; ".join(f"cat {path}" for path in secrets) + "; echo SANDBOX-OK"
    result = subprocess.run(
        [*prefix, "codex", "sandbox", *[x for a in config_args for x in ("-c", a)], "--"]
        + ["sh", "-c", script],
        capture_output=True,
        text=True,
        cwd=work,
        env={**os.environ, "CODEX_HOME": str(home)},
        timeout=60,
    )
    # Before: "bwrap: Can't write data to file ...: Bad file descriptor", nothing ran.
    # That is this bug, not a computer without namespaces, so it fails rather than skips.
    assert "Can't write data to file" not in result.stderr, result.stderr
    _skip_without_namespaces(result, "SANDBOX-OK")
    assert "SANDBOX-OK" in result.stdout, result.stderr
    assert "SECRET-" not in result.stdout
