"""Settings > Rem > Remove Co-authored-by lines, end to end with real git."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import gofer
from gofer.core import commit_attribution
from gofer.core.provider_preferences import save_commit_message_preference
from gofer.rattish import run_worktrees
from gofer.ui import ticket_patch
from gofer.utils.paths import get_data_dir
from gofer.utils.process import build_subprocess_env

MESSAGE = (
    "Fix the thing\n\nBody text mentions co-authored-by: inline.\n\nCo-Authored-By: Bot <b@x>\n"
)
CLEANED = "Fix the thing\n\nBody text mentions co-authored-by: inline.\n"
SOURCE = Path(gofer.__file__).resolve().parents[1]


def _turn_on(on: bool = True) -> None:
    save_commit_message_preference({"stripCoAuthors": on})


def _install_cli() -> Path:
    """The trusted CLI copy the app makes, as a shim that runs this checkout's code."""
    folder = get_data_dir().resolve().parent / ".gofer-trusted-bin"
    folder.mkdir(parents=True, exist_ok=True)
    cli = folder / "Raticode"
    cli.write_text(
        f"#!/bin/sh\nPYTHONPATH='{SOURCE}' exec '{sys.executable}' -m gofer.cli.main \"$@\"\n"
    )
    cli.chmod(0o700)
    return cli


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        timeout=60,
    ).stdout


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "commit", "-q", "--allow-empty", "-m", "base")
    return path


def _last_message(repo: Path) -> str:
    return _git(repo, "log", "-1", "--format=%B").rstrip("\n")


def test_hook_environment_follows_the_setting(tmp_path: Path) -> None:
    cli = tmp_path / "bin" / "Raticode it's"
    assert commit_attribution.hook_environment({}, cli) == {}
    _turn_on()
    # No trusted CLI yet: nothing to run, so nothing is added.
    assert commit_attribution.hook_environment({}) == {}
    added = commit_attribution.hook_environment({}, cli)["GIT_CONFIG_PARAMETERS"]
    assert added.startswith(f"'hook.{commit_attribution.HOOK_NAME}.event'='commit-msg' ")
    # Git reads back exactly the command, with the path shell-quoted inside it.
    read = subprocess.run(
        ["git", "config", "--get", f"hook.{commit_attribution.HOOK_NAME}.command"],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
        env={**os.environ, "GIT_CONFIG_PARAMETERS": added},
    ).stdout
    assert read == f"'{cli.parent}/Raticode it'\"'\"'s' git-hook commit-msg\n"
    # Entries from `git -c` or an outer process stay first; the hook is never doubled.
    kept = commit_attribution.hook_environment({"GIT_CONFIG_PARAMETERS": "'a.b'='c'"}, cli)
    assert kept["GIT_CONFIG_PARAMETERS"] == f"'a.b'='c' {added}"
    assert commit_attribution.hook_environment(kept, cli) == {}
    _turn_on(False)
    assert commit_attribution.hook_environment({}, cli) == {}


def test_every_child_environment_gets_the_hook_only_while_on() -> None:
    _install_cli()
    assert "GIT_CONFIG_PARAMETERS" not in build_subprocess_env({}, inherit_parent=False)
    _turn_on()
    env = build_subprocess_env({}, inherit_parent=False)
    assert commit_attribution.HOOK_NAME in env["GIT_CONFIG_PARAMETERS"]
    # Building again from a built env (run_subprocess -> stream_subprocess) keeps one copy.
    again = build_subprocess_env(env, inherit_parent=False)
    assert again["GIT_CONFIG_PARAMETERS"] == env["GIT_CONFIG_PARAMETERS"]


def test_clean_commit_message_follows_the_setting() -> None:
    assert commit_attribution.clean_commit_message(MESSAGE) == MESSAGE
    _turn_on()
    assert commit_attribution.clean_commit_message(MESSAGE) == CLEANED


needs_config_hooks = pytest.mark.skipif(
    os.name == "nt" or not commit_attribution.config_hooks_supported(),
    reason="this git doesn't run hooks named in config",
)


@needs_config_hooks
def test_an_agents_own_git_commit_is_cleaned_before_it_is_made(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    # The repository's own commit-msg hook still runs, after Raticode's.
    seen = tmp_path / "seen"
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.write_text(f"#!/bin/sh\ncat \"$1\" > '{seen}'\n")
    hook.chmod(0o700)
    _install_cli()
    _turn_on()
    env = build_subprocess_env()

    _git(repo, "commit", "-q", "--allow-empty", "-m", MESSAGE, env=env)
    assert _last_message(repo) == CLEANED.rstrip("\n")
    assert "Co-Authored-By" not in seen.read_text()

    # Raticode's own git calls turn repository hooks off; its hook still runs.
    _git(
        repo,
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-q",
        "--amend",
        "--allow-empty",
        "-m",
        MESSAGE,
        env=env,
    )
    assert _last_message(repo) == CLEANED.rstrip("\n")

    _git(repo, "switch", "-q", "-c", "side", env=env)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "side", env=env)
    _git(repo, "switch", "-q", "main", env=env)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "main", env=env)
    _git(repo, "merge", "-q", "--no-ff", "-m", MESSAGE, "side", env=env)
    assert _last_message(repo) == CLEANED.rstrip("\n")

    # Only Co-authored-by lines go: no hook means git's own cleanup alone.
    with_trailer_only = "Subject\n\nCo-authored-by: a <a@x>\nSigned-off-by: Me <m@x>\n"
    _git(repo, "commit", "-q", "--allow-empty", "-m", with_trailer_only, env=env)
    assert _last_message(repo) == "Subject\n\nSigned-off-by: Me <m@x>"

    # A message of nothing but co-authors leaves nothing to commit with.
    failed = subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", "Co-authored-by: a <a@x>"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert failed.returncode != 0


@needs_config_hooks
def test_commits_are_untouched_while_the_setting_is_off(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    _install_cli()
    _git(repo, "commit", "-q", "--allow-empty", "-m", MESSAGE, env=build_subprocess_env())
    assert _last_message(repo) == MESSAGE.rstrip("\n")


@needs_config_hooks
def test_an_outer_environment_with_the_hook_keeps_cleaning(tmp_path: Path) -> None:
    """A turn started while on keeps its hook even if the setting changes mid-turn."""
    repo = _repo(tmp_path / "repo")
    _install_cli()
    _turn_on()
    env = build_subprocess_env()
    _turn_on(False)
    _git(repo, "commit", "-q", "--allow-empty", "-m", MESSAGE, env=env)
    assert _last_message(repo) == CLEANED.rstrip("\n")


def test_backend_commits_that_skip_hooks_are_cleaned_too(tmp_path: Path) -> None:
    """Landing and worktree runs commit with --no-verify, so they clean the message first."""
    _turn_on()
    repo = _repo(tmp_path / "repo")
    (repo / "a.txt").write_text("a\n")
    assert ticket_patch.commit_leftovers(str(repo), MESSAGE, branch="main")
    assert _last_message(repo) == CLEANED.rstrip("\n")
    (repo / "b.txt").write_text("b\n")
    assert run_worktrees.commit_changes(repo, MESSAGE, branch="main")
    assert _last_message(repo) == CLEANED.rstrip("\n")
    _turn_on(False)
    (repo / "c.txt").write_text("c\n")
    assert run_worktrees.commit_changes(repo, MESSAGE, branch="main")
    assert _last_message(repo) == MESSAGE.rstrip("\n")


def test_hook_probe_reports_an_old_git(tmp_path: Path) -> None:
    old_git = tmp_path / "git"
    old_git.write_text('#!/bin/sh\n[ "$1" = init ] && exit 0\nexit 129\n')
    old_git.chmod(0o700)
    assert commit_attribution.config_hooks_supported(str(old_git)) is False
    assert commit_attribution.config_hooks_supported(str(tmp_path / "missing")) is False


def _claude_code_sandbox_shell(env: dict[str, str]) -> dict[str, str]:
    """The environment Claude Code's sandbox gives a Bash command (Claude Code 2.1.319).

    With its network proxy on it assigns GIT_CONFIG_PARAMETERS outright, dropping what
    Raticode put there, then appends a credential entry; GIT_CONFIG_COUNT entries are
    only ever appended to.
    """
    shell = dict(env)
    shell["GIT_CONFIG_PARAMETERS"] = (
        "'http.proxyAuthMethod=basic' 'credential.http://127.0.0.1:3128.helper='"
    )
    count = int(shell.get("GIT_CONFIG_COUNT", "0"))
    shell["GIT_CONFIG_KEY_" + str(count)] = "credential.interactive"
    shell["GIT_CONFIG_VALUE_" + str(count)] = "false"
    shell["GIT_CONFIG_COUNT"] = str(count + 1)
    return shell


def test_count_hook_environment_follows_the_setting_and_appends_once(tmp_path: Path) -> None:
    cli = tmp_path / "Raticode"
    assert commit_attribution.count_hook_environment({}, cli) == {}
    _turn_on()
    existing = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "gc.auto", "GIT_CONFIG_VALUE_0": "0"}
    added = commit_attribution.count_hook_environment(existing, cli)
    assert added == {
        "GIT_CONFIG_KEY_1": f"hook.{commit_attribution.HOOK_NAME}.event",
        "GIT_CONFIG_VALUE_1": "commit-msg",
        "GIT_CONFIG_KEY_2": f"hook.{commit_attribution.HOOK_NAME}.command",
        "GIT_CONFIG_VALUE_2": commit_attribution.hook_command(cli),
        "GIT_CONFIG_COUNT": "3",
    }
    assert commit_attribution.count_hook_environment({**existing, **added}, cli) == {}
    assert commit_attribution.count_hook_environment({"GIT_CONFIG_COUNT": "x"}, cli) == {}


@needs_config_hooks
def test_a_claude_code_sandbox_shell_still_runs_the_hook(tmp_path: Path) -> None:
    """Claude Code's sandbox replaces GIT_CONFIG_PARAMETERS; the COUNT copy survives it."""
    repo = _repo(tmp_path / "repo")
    _install_cli()
    _turn_on()
    built = build_subprocess_env()
    # Without the COUNT copy the sandbox loses the hook: the line stays.
    _git(
        repo, "commit", "-q", "--allow-empty", "-m", MESSAGE, env=_claude_code_sandbox_shell(built)
    )
    assert _last_message(repo) == MESSAGE.rstrip("\n")
    claude = {**built, **commit_attribution.count_hook_environment(built)}
    shell = _claude_code_sandbox_shell(claude)
    _git(repo, "commit", "-q", "--allow-empty", "-m", MESSAGE, env=shell)
    assert _last_message(repo) == CLEANED.rstrip("\n")
    # Outside the sandbox both copies are present, and the hook still runs once.
    marker = tmp_path / "runs"
    counting = tmp_path / "counting-cli"
    counting.write_text(f"#!/bin/sh\necho run >> '{marker}'\nexec '{_install_cli()}' \"$@\"\n")
    counting.chmod(0o700)
    both = {
        **built,
        **commit_attribution.hook_environment({}, counting),
        **commit_attribution.count_hook_environment(built, counting),
    }
    _git(repo, "commit", "-q", "--allow-empty", "-m", MESSAGE, env=both)
    assert _last_message(repo) == CLEANED.rstrip("\n")
    assert marker.read_text() == "run\n"


async def _provider_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, subscription: object
) -> dict[str, str]:
    """The environment a workflow agent step's provider process starts with."""
    import json

    report = tmp_path / f"{type(subscription).__name__}.json"
    script = tmp_path / "fake_provider.py"
    script.write_text(
        "import json, os, sys\n"
        f"open({str(report)!r}, 'w').write(json.dumps(dict(os.environ)))\n"
        "print(json.dumps({'type': 'result', 'result': 'done'}))\n"
    )
    monkeypatch.setattr(subscription, "_build_command", lambda *a: [sys.executable, str(script)])
    await subscription.execute("prompt", tmp_path, [], [], {})  # type: ignore[attr-defined]
    env: dict[str, str] = json.loads(report.read_text())
    return env


@pytest.mark.asyncio
async def test_workflow_steps_give_only_claude_code_the_count_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gofer.subscriptions.claude_code import ClaudeCodeSubscription
    from gofer.subscriptions.codex import CodexSubscription

    _install_cli()
    _turn_on()
    hook_key = f"hook.{commit_attribution.HOOK_NAME}.command"
    claude = await _provider_env(monkeypatch, tmp_path, ClaudeCodeSubscription())
    assert commit_attribution.HOOK_NAME in claude["GIT_CONFIG_PARAMETERS"]
    count = int(claude["GIT_CONFIG_COUNT"])
    assert hook_key in {claude[f"GIT_CONFIG_KEY_{i}"] for i in range(count)}
    # Codex may drop *KEY* names from its shell, which would leave git a count with no
    # keys and break every git command, so it only gets GIT_CONFIG_PARAMETERS.
    codex = await _provider_env(monkeypatch, tmp_path, CodexSubscription())
    assert commit_attribution.HOOK_NAME in codex["GIT_CONFIG_PARAMETERS"]
    assert "GIT_CONFIG_COUNT" not in codex
