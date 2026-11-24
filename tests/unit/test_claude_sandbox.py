"""Claude Code's OS sandbox for unattended runs (self-work and reviews)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from gofer.core import command_slots, secrets_folder
from gofer.ui import chat, claude_sandbox
from gofer.ui.chat import ChatProviderError, stream_workflow_chat
from gofer.utils import process


def test_sandbox_is_unavailable_on_windows_and_without_its_tools() -> None:
    assert "Windows" in str(claude_sandbox.unavailable_reason("win32"))
    assert claude_sandbox.unavailable_reason("darwin", is_file=lambda _path: True) is None
    assert "sandbox-exec" in str(
        claude_sandbox.unavailable_reason("darwin", is_file=lambda _path: False)
    )
    assert claude_sandbox.unavailable_reason("linux", which=lambda tool: f"/usr/bin/{tool}") is None
    missing = claude_sandbox.unavailable_reason(
        "linux", which=lambda tool: None if tool == "bwrap" else "/usr/bin/socat"
    )
    assert missing is not None and "bubblewrap (bwrap)" in missing and "socat," not in missing


def test_settings_lock_down_the_shell_and_deny_secrets_to_every_tool(tmp_path: Path) -> None:
    data = tmp_path / "data"
    project = tmp_path / "project"
    scratch = data / "rem-work" / "turn-scratch" / "run-verify"
    skills = tmp_path / "skills"
    settings = claude_sandbox.sandbox_settings(
        root=project,
        data_dir=data,
        read_only=True,
        writable=[scratch, None],
        visible=[scratch],
        protected=[skills, tmp_path],
    )
    sandbox = settings["sandbox"]
    assert sandbox["enabled"] is True
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["network"] == {"allowedDomains": [], "strictAllowlist": True}
    files = sandbox["filesystem"]
    assert {"~/.ssh", "~/.claude", "~/.claude.json", str(data.resolve())} <= set(files["denyRead"])
    assert files["allowWrite"] == [str(scratch.resolve())]
    # The read-only project and the skills folder are protected. tmp_path holds
    # the scratch folder, and a denied folder would deny everything inside it.
    assert files["denyWrite"] == [str(project.resolve()), str(skills.resolve())]
    assert str(scratch.resolve()) in files["allowRead"]
    deny = settings["permissions"]["deny"]
    for rule in (
        "Read(~/.ssh/**)",
        "Read(~/.claude/**)",
        "Read(~/.claude.json)",
        "Edit(~/.ssh/**)",
    ):
        assert rule in deny
    # Absolute paths in permission rules start with two slashes.
    assert f"Read(/{data.resolve()}/devices/**)" in deny


def test_no_secrets_folder_adds_nothing(tmp_path: Path) -> None:
    assert claude_sandbox.secrets_folders() == []
    settings = claude_sandbox.sandbox_settings(
        root=tmp_path / "project", data_dir=tmp_path / "data", read_only=False
    )
    deny_read = settings["sandbox"]["filesystem"]["denyRead"]
    assert deny_read == [*claude_sandbox.SECRET_PATHS, str((tmp_path / "data").resolve())]


def test_headless_secrets_folders_are_hidden_from_the_shell_and_read_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "secrets"
    configured.mkdir(mode=0o700)
    secrets_folder.configure_secrets_dir(configured)
    # systemd's folder is hidden too, also after startup sealed it out of the environment.
    systemd = tmp_path / "run" / "credentials" / "raticode.service"
    systemd.mkdir(parents=True)
    monkeypatch.setenv(secrets_folder.CREDENTIALS_DIRECTORY_ENV, str(systemd))
    process.seal_credential_env((secrets_folder.CREDENTIALS_DIRECTORY_ENV,))
    assert secrets_folder.CREDENTIALS_DIRECTORY_ENV not in os.environ
    # A symlinked name for the folder is hidden under both names.
    alias = tmp_path / "secrets-link"
    alias.symlink_to(configured, target_is_directory=True)
    secrets_folder.configure_secrets_dir(alias)

    settings = claude_sandbox.sandbox_settings(
        root=tmp_path / "project", data_dir=tmp_path / "data", read_only=True
    )
    deny_read = settings["sandbox"]["filesystem"]["denyRead"]
    deny = settings["permissions"]["deny"]
    for folder in (alias, configured.resolve(), systemd.resolve()):
        assert str(folder) in deny_read
        for tool in ("Read", "Edit"):
            assert f"{tool}(/{folder})" in deny
            assert f"{tool}(/{folder}/**)" in deny
    # The usual credential stores are still there.
    assert "~/.ssh" in deny_read and "Read(~/.ssh/**)" in deny


def test_writer_projects_stay_writable_and_data_folder_work_stays_visible(tmp_path: Path) -> None:
    data = tmp_path / "data"
    root = data / "rem-work" / "task-1"
    settings = claude_sandbox.sandbox_settings(root=root, data_dir=data, read_only=False)
    files = settings["sandbox"]["filesystem"]
    assert files["denyWrite"] == []
    # A projectless task works inside the hidden data folder.
    assert files["allowRead"] == [str(root.resolve())]


def test_a_readable_parent_never_hides_a_writable_folder(tmp_path: Path) -> None:
    data = tmp_path / "data"
    slots = data / "command-slots"
    settings = claude_sandbox.sandbox_settings(
        root=tmp_path / "project",
        data_dir=data,
        read_only=True,
        writable=[slots / "activity"],
        visible=[slots, slots / "bin"],
    )
    readable = settings["sandbox"]["filesystem"]["allowRead"]
    assert readable == [str((slots / "bin").resolve()), str((slots / "activity").resolve())]


def test_sandbox_environment_moves_temp_and_read_only_caches(tmp_path: Path) -> None:
    writer = claude_sandbox.sandbox_environment(tmp_path, read_only=False)
    assert writer == {
        "CLAUDE_CODE_TMPDIR": str(tmp_path),
        "npm_config_cache": str(tmp_path / "npm-cache"),
        command_slots.SANDBOX_ENV: "1",
    }
    reader = claude_sandbox.sandbox_environment(tmp_path, read_only=True)
    assert reader["MYPY_CACHE_DIR"] == str(tmp_path / "mypy-cache")
    assert "no:cacheprovider" in reader["PYTEST_ADDOPTS"]
    # Claude Code picks the sandbox's $TMPDIR itself, and Playwright and other
    # tools still need the user's own cache folder.
    assert not {"TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME"} & set(reader)


def test_git_common_dir_is_reported_only_outside_the_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q", str(repo)], check=True)
    (repo / "a").write_text("a")
    subprocess.run([*git, "-C", str(repo), "add", "a"], check=True)
    subprocess.run([*git, "-C", str(repo), "commit", "-qm", "init"], check=True)
    worktree = tmp_path / "wt"
    subprocess.run([*git, "-C", str(repo), "worktree", "add", "-q", str(worktree)], check=True)
    assert claude_sandbox.git_common_dir(repo) is None
    assert claude_sandbox.git_common_dir(worktree) == (repo / ".git").resolve()
    assert claude_sandbox.git_common_dir(tmp_path / "missing") is None


@pytest.mark.skipif(sys.platform == "win32", reason="command slots are POSIX only")
def test_sandboxed_slot_commands_skip_their_own_systemd_scope(monkeypatch, tmp_path) -> None:
    started: list[dict[str, Any]] = []

    def fake_run_child(argv, env, *, limit=None, notify=None):
        started.append({"argv": argv, "limit": limit})
        return 0

    monkeypatch.setattr(command_slots, "_run_child", fake_run_child)
    monkeypatch.setattr(command_slots, "acquire", lambda *args, **kwargs: None)
    monkeypatch.setenv(command_slots.ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(command_slots.SLICE_ENV, command_slots.SLICE)
    monkeypatch.delenv(command_slots.HELD_ENV, raising=False)
    monkeypatch.delenv(command_slots.ACTIVITY_ENV, raising=False)
    monkeypatch.setattr(command_slots, "command_memory_limit", lambda: 1024)
    assert command_slots.run("test", ["true"]) == 0
    monkeypatch.setenv(command_slots.SANDBOX_ENV, "1")
    assert command_slots.run("test", ["true"]) == 0
    assert [s["limit"] for s in started] == [1024, None]


async def _fake_claude_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    managed: dict[str, Any],
    *,
    scratch: Path | None = None,
    during: str = "",
    cli: Path | None = None,
) -> dict[str, Any]:
    """Run one managed turn through a fake Claude CLI that records argv and env."""
    report = tmp_path / "report.json"
    script = tmp_path / "fake_claude.py"
    script.write_text(
        "import json, os, sys\n" + during + f"report = open({str(report)!r}, 'w')\n"
        "report.write(json.dumps({'argv': sys.argv, 'env': dict(os.environ)}))\n"
        "report.close()\n"
        "print(json.dumps({'type':'result','session_id':'claude-1','result':'done'}))\n"
    )
    gof = tmp_path / "trusted" / "gof"
    gof.parent.mkdir(exist_ok=True)
    gof.write_text("#!/bin/sh\n")
    gof.chmod(0o755)

    async def no_compaction(**options):
        return options["messages"], False

    real_build_command = chat._build_chat_command

    def build_command(**options):
        return [sys.executable, str(script), *real_build_command(**options)[1:]]

    monkeypatch.setattr(chat, "_compact_chat_messages_if_needed", no_compaction)
    monkeypatch.setattr(chat, "resolve_provider_executable", lambda _: sys.executable)
    monkeypatch.setattr(chat, "ensure_local_gofer_cli", lambda _data_dir: cli or gof)
    monkeypatch.setattr(chat, "_build_chat_command", build_command)
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    events = [
        event
        async for event in stream_workflow_chat(
            provider="claude_code",
            model="cli-default",
            messages=[{"role": "user", "body": "review the change"}],
            workflow={
                "projectRoot": str(project),
                "chatThreadId": "thread-1",
                "remResources": {},
                "remManagedTask": managed,
            },
            working_dir=project,
            data_dir=tmp_path / "data",
            permission_mode="dontAsk",
            trusted_scratch_dir=scratch,
        )
    ]
    assert events[-1]["type"] == "final", events[-1]
    seen: dict[str, Any] = json.loads(report.read_text())
    return seen


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Claude Code has no sandbox on Windows")
async def test_unattended_claude_turns_pass_sandbox_settings(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(claude_sandbox, "unavailable_reason", lambda: None)
    scratch = tmp_path / "data" / "rem-work" / "turn-scratch" / "run-verify"
    seen = await _fake_claude_turn(
        monkeypatch,
        tmp_path,
        {"readOnly": True, "claudeSandbox": True, "runId": "run"},
        scratch=scratch,
    )
    argv, env = seen["argv"], seen["env"]
    assert argv.count("--settings") == 1
    settings = json.loads(argv[argv.index("--settings") + 1])
    files = settings["sandbox"]["filesystem"]
    assert settings["sandbox"]["failIfUnavailable"] is True
    assert files["denyWrite"][0] == str((tmp_path / "project").resolve())
    assert str(scratch.resolve()) in files["allowWrite"]
    # The command-slot hook still queues tests, and its progress file stays writable.
    slots = (tmp_path / "data" / "command-slots").resolve()
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    assert str(slots / "activity") in files["allowWrite"]
    assert str(slots / "bin") in files["allowRead"]
    # A read-only parent would hide the writable progress folder inside it.
    assert str(slots) not in files["allowRead"]
    assert str(slots / "activity") in files["allowRead"]
    assert "Read(~/.ssh/**)" in settings["permissions"]["deny"]
    # The data folder isn't an extra directory, which the sandbox would make writable.
    added = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--add-dir"]
    assert str((tmp_path / "data").resolve()) not in added
    assert env["CLAUDE_CODE_TMPDIR"] == str(scratch)
    assert env["MYPY_CACHE_DIR"] == str(scratch / "mypy-cache")
    assert env[command_slots.SANDBOX_ENV] == "1"


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Claude Code has no sandbox on Windows")
async def test_unattended_claude_turns_skip_project_settings_but_keep_claude_md(
    monkeypatch, tmp_path
) -> None:
    """A project's .claude/settings.json hooks would run outside the sandbox."""
    monkeypatch.setattr(claude_sandbox, "unavailable_reason", lambda: None)
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / "CLAUDE.md").write_text("Run the project's own checks.\n")
    (project / ".claude" / "settings.json").write_text('{"hooks": {}}\n')
    seen = await _fake_claude_turn(
        monkeypatch,
        tmp_path,
        {"readOnly": False, "claudeSandbox": True, "runId": "run"},
        scratch=tmp_path / "data" / "rem-work" / "turn-scratch" / "run-x",
    )
    argv = seen["argv"]
    assert argv[argv.index("--setting-sources") + 1] == "user"
    appended = argv[argv.index("--append-system-prompt") + 1]
    assert "Run the project's own checks." in appended
    # Raticode's own settings, with the slot hook, still go in.
    assert "PreToolUse" in json.loads(argv[argv.index("--settings") + 1])["hooks"]


def test_project_instructions_come_from_claude_md_files_but_never_through_links(
    monkeypatch, tmp_path
) -> None:
    home = tmp_path / "home"
    project = home / "code" / "project"
    (project / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".claude").mkdir()
    (home / "CLAUDE.md").write_text("home rules")
    (home / ".claude" / "CLAUDE.md").write_text("user memory")
    (home / "code" / "CLAUDE.md").write_text("code rules")
    (project / "CLAUDE.md").write_text("project rules")
    (project / ".claude" / "CLAUDE.md").write_text("dot rules")
    for nested in ("app/CLAUDE.md", "node_modules/pkg/CLAUDE.md", ".worktrees/w/CLAUDE.md"):
        (project / nested).parent.mkdir(parents=True)
        (project / nested).write_text("nested")
    secret = tmp_path / "id_rsa"
    secret.write_text("PRIVATE KEY")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "CLAUDE.md").symlink_to(secret)

    args = claude_sandbox.project_instruction_args(project)

    assert args[:2] == ["--setting-sources", "user"]
    text = args[args.index("--append-system-prompt") + 1]
    assert text.index("code rules") < text.index("project rules") < text.index("dot rules")
    # Home's own files stop the walk: the "user" setting source already loads them.
    assert "home rules" not in text and "user memory" not in text
    assert "- app/CLAUDE.md" in text
    assert "node_modules" not in text and ".worktrees" not in text
    # A CLAUDE.md that links to a key file adds nothing, so only the setting is left.
    assert claude_sandbox.project_instruction_args(linked) == ["--setting-sources", "user"]


def test_project_instructions_are_capped(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("x" * (claude_sandbox.MAX_INSTRUCTION_BYTES + 100))
    text = claude_sandbox.project_instruction_args(project)[-1]
    assert "at most 64 KB" in text
    assert text.count("x") <= claude_sandbox.MAX_INSTRUCTION_BYTES


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Claude Code has no sandbox on Windows")
async def test_a_sandboxed_claude_turns_own_commit_loses_its_co_author_line(
    monkeypatch, tmp_path
) -> None:
    """Settings > Rem > Remove Co-authored-by lines reaches the sandboxed shell.

    The fake Claude Code does what its sandbox does to a Bash command's environment
    (2.1.319): it assigns GIT_CONFIG_PARAMETERS its proxy settings, dropping what
    Raticode put there, and appends to GIT_CONFIG_COUNT. Then it runs `git commit`.
    """
    from gofer.core import commit_attribution
    from gofer.core.provider_preferences import save_commit_message_preference

    if not commit_attribution.config_hooks_supported():
        pytest.skip("this git doesn't run hooks named in config")
    monkeypatch.setattr(claude_sandbox, "unavailable_reason", lambda: None)
    save_commit_message_preference({"stripCoAuthors": True})
    source = Path(chat.__file__).resolve().parents[2]
    cli = tmp_path / "trusted" / "Raticode"
    cli.parent.mkdir()
    cli.write_text(
        f"#!/bin/sh\nPYTHONPATH='{source}' exec '{sys.executable}' -m gofer.cli.main \"$@\"\n"
    )
    cli.chmod(0o700)
    repo = tmp_path / "project"
    repo.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.name", "T"],
        ["config", "user.email", "t@example.com"],
        ["config", "commit.gpgsign", "false"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    message = "Fix it\n\nCo-Authored-By: Claude <noreply@example.com>\n"
    during = (
        "import subprocess\n"
        "env = dict(os.environ)\n"
        "env['GIT_CONFIG_PARAMETERS'] = "
        "\"'http.proxyAuthMethod=basic' 'credential.http://127.0.0.1:3128.helper='\"\n"
        "n = int(env.get('GIT_CONFIG_COUNT', '0'))\n"
        "env[f'GIT_CONFIG_KEY_{n}'] = 'credential.interactive'\n"
        "env[f'GIT_CONFIG_VALUE_{n}'] = 'false'\n"
        "env['GIT_CONFIG_COUNT'] = str(n + 1)\n"
        f"subprocess.run(['git', 'commit', '-q', '--allow-empty', '-m', {message!r}], env=env,"
        " check=True)\n"
    )
    await _fake_claude_turn(
        monkeypatch,
        tmp_path,
        {"readOnly": False, "claudeSandbox": True, "runId": "run"},
        scratch=tmp_path / "data" / "rem-work" / "turn-scratch" / "run-commit",
        during=during,
        cli=cli,
    )
    log = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%B"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert log.rstrip("\n") == "Fix it"


MOUNT_POINTS = (
    ".bashrc",
    ".gitconfig",
    ".gitmodules",
    ".mcp.json",
    ".vscode",
    ".claude/settings.json",
    ".claude/agents",
)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Claude Code has no sandbox on Windows")
async def test_a_sandboxed_turn_removes_the_mount_points_it_left_in_the_project(
    monkeypatch, tmp_path
) -> None:
    """A turn stopped mid-command leaves bubblewrap's empty read-only mount points."""
    monkeypatch.setattr(claude_sandbox, "unavailable_reason", lambda: None)
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    # Already there before the turn, so not this turn's to remove.
    (project / ".profile").touch(mode=0o444)
    (project / ".profile").chmod(0o444)
    # The agent's own work, including an empty file it made read-only.
    leave = {"notes.md": "kept", ".zshrc": "", ".ripgreprc": "--hidden"}
    during = (
        "from pathlib import Path\n"
        "for name in " + repr(MOUNT_POINTS) + ":\n"
        "    Path(name).touch()\n"
        "    Path(name).chmod(0o444)\n"
        "for name, text in " + repr(leave) + ".items():\n"
        "    Path(name).write_text(text)\n"
        "Path('.zshrc').chmod(0o400)\n"
    )
    await _fake_claude_turn(
        monkeypatch,
        tmp_path,
        {"readOnly": False, "claudeSandbox": True, "runId": "run"},
        scratch=tmp_path / "data" / "rem-work" / "turn-scratch" / "run",
        during=during,
    )
    for name in MOUNT_POINTS:
        assert not (project / name).exists(), name
    for name in [*leave, ".profile", ".claude"]:
        assert (project / name).exists(), name
    assert (project / "notes.md").read_text() == "kept"


def test_mount_points_stay_while_another_sandboxed_turn_runs_in_the_folder(tmp_path) -> None:
    first = claude_sandbox.track_mount_stubs(tmp_path)
    second = claude_sandbox.track_mount_stubs(tmp_path)
    stub = tmp_path / ".bashrc"
    stub.touch()
    stub.chmod(0o444)
    # The other turn's commands may still be mounted over it.
    assert claude_sandbox.release_mount_stubs(first) == []
    assert stub.exists()
    assert claude_sandbox.release_mount_stubs(second) == [".bashrc"]
    assert not stub.exists()
    # Releasing again is harmless.
    assert claude_sandbox.release_mount_stubs(second) == []


@pytest.mark.asyncio
async def test_other_claude_turns_keep_running_without_the_sandbox(monkeypatch, tmp_path) -> None:
    # Run inside a Claude Code sandbox, the suite inherits this; the turn must not add it.
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR", raising=False)
    monkeypatch.setattr(claude_sandbox, "unavailable_reason", lambda: "should not be checked")
    stub = tmp_path / "project" / ".bashrc"
    seen = await _fake_claude_turn(
        monkeypatch,
        tmp_path,
        {"readOnly": False, "runId": "run"},
        during="import pathlib\npathlib.Path('.bashrc').touch(mode=0o444)\n",
    )
    # Only a sandboxed turn's mount points are cleaned up.
    assert stub.exists()
    argv = seen["argv"]
    if "--settings" in argv:
        assert "sandbox" not in json.loads(argv[argv.index("--settings") + 1])
    assert "CLAUDE_CODE_TMPDIR" not in seen["env"]


@pytest.mark.asyncio
async def test_a_sandbox_that_cannot_start_blocks_the_turn_with_its_reason(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(claude_sandbox, "unavailable_reason", lambda: "bwrap is missing.")
    started: list[Any] = []

    async def no_compaction(**options):
        started.append(options)
        return options["messages"], False

    monkeypatch.setattr(chat, "_compact_chat_messages_if_needed", no_compaction)
    monkeypatch.setattr(chat, "resolve_provider_executable", lambda _: sys.executable)
    with pytest.raises(ChatProviderError) as raised:
        async for _event in stream_workflow_chat(
            provider="claude_code",
            model="cli-default",
            messages=[{"role": "user", "body": "work"}],
            workflow={"remManagedTask": {"claudeSandbox": True}},
            working_dir=tmp_path,
            data_dir=tmp_path / "data",
            permission_mode="dontAsk",
        ):
            pass
    assert str(raised.value) == f"{chat.CLAUDE_SANDBOX_BLOCKED} bwrap is missing."
    # Nothing ran, so no tokens were spent on a turn that couldn't start.
    assert started == []
