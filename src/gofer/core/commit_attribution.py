"""Settings > Rem > Remove Co-authored-by lines: where the check runs.

Two places, both deterministic Python (gofer.utils.commit_trailers):

- Commits Raticode's own code makes (landing, worktree runs, swarms, the Source
  Control panel) pass their message through ``clean_commit_message`` first.
- Every child process Raticode starts (Rem threads, workers, organization
  employees, workflow agent steps and commands) gets a git commit-msg hook through
  ``GIT_CONFIG_PARAMETERS`` (see ``hook_environment``). Claude Code processes also
  get it as ``GIT_CONFIG_COUNT`` entries (``count_hook_environment``): Claude Code's
  sandbox replaces ``GIT_CONFIG_PARAMETERS`` in its shell with its proxy settings,
  but appends to ``GIT_CONFIG_COUNT``. Codex keeps ``GIT_CONFIG_PARAMETERS`` and may
  drop ``*KEY*`` names (``shell_environment_policy``), which would leave git with a
  count and no keys, so it gets only the first form. A hook named in both runs once.
  It runs
  ``Raticode git-hook commit-msg FILE`` before git creates the commit, next to the
  repository's own hooks, so ``git commit`` and ``git merge`` in an agent's shell
  are cleaned without the model doing anything. Git skips commit-msg hooks for
  ``--no-verify``, ``cherry-pick``, ``rebase`` and ``commit-tree``; those replay or
  write messages that already went through a check.

Config-based hooks (``hook.<name>.command``) need Git 2.54 or later; older versions
ignore them, and ``config_hooks_supported`` says which this computer has.
"""

from __future__ import annotations

import functools
import os
import shlex
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from gofer.utils.commit_trailers import strip_co_author_lines

HOOK_NAME = "raticode-remove-co-authors"
_PARAMETERS = "GIT_CONFIG_PARAMETERS"
_COUNT = "GIT_CONFIG_COUNT"
_TRUSTED_BIN = ".gofer-trusted-bin"
_CLI_NAMES = ("Raticode", "Raticode.exe", "Raticode.cmd", "Raticode.bat")


def enabled() -> bool:
    from gofer.core.provider_preferences import strip_co_authors_enabled

    return strip_co_authors_enabled()


def clean_commit_message(message: str) -> str:
    """``message`` as Raticode should commit it under the current setting."""
    return strip_co_author_lines(message) if enabled() else message


def trusted_cli(data_dir: Path | None = None) -> Path | None:
    """The Raticode CLI copy in the data root's trusted folder, if the app made one."""
    from gofer.utils.paths import get_data_dir

    try:
        folder = (data_dir or get_data_dir()).resolve().parent / _TRUSTED_BIN
    except OSError:
        return None
    for name in _CLI_NAMES:
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def _sq(value: str) -> str:
    """Git's own quoting for GIT_CONFIG_PARAMETERS entries."""
    return "'" + value.replace("'", "'\\''") + "'"


def hook_command(cli: Path) -> str:
    # Git runs the command through sh (Git for Windows ships one) with the message
    # file appended.
    path = cli.as_posix() if os.name == "nt" else str(cli)
    return f"{shlex.quote(path)} git-hook commit-msg"


def _hook_entries(cli: Path) -> tuple[tuple[str, str], ...]:
    return (
        (f"hook.{HOOK_NAME}.event", "commit-msg"),
        (f"hook.{HOOK_NAME}.command", hook_command(cli)),
    )


def hook_parameters(cli: Path) -> str:
    return " ".join(f"{_sq(key)}={_sq(value)}" for key, value in _hook_entries(cli))


def hook_environment(env: dict[str, str], cli: Path | None = None) -> dict[str, str]:
    """GIT_CONFIG_PARAMETERS for ``env`` with the commit-msg hook added, or ``{}``.

    Empty while the setting is off, when the trusted CLI is missing, or when ``env``
    already has the hook. Existing entries (from ``git -c`` or an outer process) stay
    in front.
    """
    current = env.get(_PARAMETERS, "")
    if f"hook.{HOOK_NAME}." in current or not enabled():
        return {}
    cli = cli or trusted_cli()
    if cli is None:
        return {}
    added = hook_parameters(cli)
    return {_PARAMETERS: f"{current} {added}" if current.strip() else added}


def count_hook_environment(env: Mapping[str, str], cli: Path | None = None) -> dict[str, str]:
    """The same hook as GIT_CONFIG_COUNT/KEY/VALUE entries after ``env``'s own, or ``{}``.

    ``env`` must be the child's whole environment (parent included), so the new entries
    go after every existing one. For Claude Code only; see the module docstring.
    """
    try:
        count = max(0, int(env.get(_COUNT, "0") or "0"))
    except ValueError:
        return {}
    keys = {env.get(f"GIT_CONFIG_KEY_{index}") for index in range(count)}
    if f"hook.{HOOK_NAME}.command" in keys or not enabled():
        return {}
    cli = cli or trusted_cli()
    if cli is None:
        return {}
    added: dict[str, str] = {}
    for offset, (key, value) in enumerate(_hook_entries(cli)):
        added[f"GIT_CONFIG_KEY_{count + offset}"] = key
        added[f"GIT_CONFIG_VALUE_{count + offset}"] = value
    added[_COUNT] = str(count + 2)
    return added


@functools.cache
def config_hooks_supported(git: str = "git") -> bool:
    """Whether this computer's git runs hooks named in config (``hook.<name>.command``)."""
    try:
        with tempfile.TemporaryDirectory(prefix="raticode-hook-probe-") as folder:
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            env.pop(_PARAMETERS, None)
            init = subprocess.run(
                [git, "init", "-q", folder],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                env=env,
            )
            if init.returncode != 0:
                return False
            listed = subprocess.run(
                [
                    git,
                    "-C",
                    folder,
                    "-c",
                    "hook.raticode-probe.event=commit-msg",
                    "-c",
                    "hook.raticode-probe.command=true",
                    "hook",
                    "list",
                    "commit-msg",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                env=env,
            )
    except (OSError, subprocess.SubprocessError):
        return False
    return listed.returncode == 0 and "raticode-probe" in listed.stdout


def status() -> dict[str, bool]:
    """What Settings shows next to the checkbox."""
    return {
        "agentCommitsChecked": config_hooks_supported(),
        "helperReady": trusted_cli() is not None,
    }
