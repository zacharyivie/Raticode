"""Hide Raticode's credential folders from agent CLIs that run without a sandbox.

Cursor, GitHub Copilot, OpenCode, Antigravity and Grok always run their shell and file
tools with the user's full access, and so do Claude Code turns the user watches (no
Claude Code sandbox), Claude Code steps with ``sandbox: off``, and Codex with full access
or with its older sandbox settings (``codex_sandbox.legacy_reason``). ``needs_mask``
says which. On Linux Raticode starts them in a mount namespace
(bubblewrap) where every folder in ``gofer.core.agent_credentials`` is an empty,
read-only folder: the CLI and everything it runs can't read the account folders,
paired-device keys, site grants or a headless secrets folder, nor plant a file in them.
Everything else stays as it is, including the CLI's own sign-in, the network and the
project. A fresh ``/proc`` keeps ``/proc/<pid>/root`` from leading back to the real
folders through another process.

The whole CLI runs inside, so only Raticode's folders are covered, never the provider's
own credentials. A Claude Code, Codex or Grok turn that uses one of Raticode's accounts
gets that one account folder back (``account_folders``), since the CLI keeps its sign-in
and sessions there; the files in it that can make the CLI run a program (its settings,
``config.toml``, hooks and plugins) stay read-only, so the turn can't plant a hook in its
own account either. The other accounts' folders stay covered. Without bubblewrap, and
on macOS and Windows, the command runs as before; ``unavailable_reason`` says why for
docs and preflight.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from gofer.core.agent_credentials import credential_folders

logger = logging.getLogger(__name__)

# Turned off only by the test suite, whose fake CLIs report process IDs and arguments
# that a PID namespace would change; tests of the mask itself turn it back on.
ENABLED = True
# Starts every masked command, so a command is never masked twice.
_MARKER = ("--die-with-parent", "--unshare-pid", "--dev-bind", "/", "/")


def unavailable_reason(
    platform: str | None = None, which: Callable[[str], str | None] | None = None
) -> str | None:
    platform = platform or sys.platform
    which = which or shutil.which
    if not platform.startswith("linux"):
        return "Raticode can only hide its credential folders from this provider on Linux"
    if which("bwrap") is None:
        return "bubblewrap (bwrap) isn't installed"
    return None


def is_masked(command: list[str]) -> bool:
    return tuple(command[1 : 1 + len(_MARKER)]) == _MARKER


def inner_command(command: list[str]) -> list[str]:
    """The command a masked command starts (after bubblewrap's ``--``), else ``command``."""
    if not is_masked(command) or "--" not in command:
        return command
    return command[command.index("--") + 1 :]


def files_masked(
    command: list[str],
    files: Iterable[Path],
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] | None = None,
) -> list[str]:
    """``command`` started with each of ``files`` covered by an empty, read-only file.

    For a sandboxed Codex turn, whose own sandbox can deny only one file
    (``codex_sandbox.PROFILE_FILE_LIMIT``). Without bubblewrap the files stay readable
    and the command runs as it was.
    """
    paths = [Path(path) for path in files if os.path.lexists(path) and not Path(path).is_dir()]
    if not ENABLED or not paths or not command or is_masked(command):
        return command
    which = which or shutil.which
    reason = unavailable_reason(platform, which)
    bwrap = which("bwrap")
    if reason or bwrap is None:
        logger.warning(
            "%d credential file(s) stay readable to %s: %s", len(paths), command[0], reason
        )
        return command
    mounts: list[str] = ["--proc", "/proc"]
    for path in paths:
        mounts += ["--ro-bind", os.devnull, str(path)]
    return [bwrap, *_MARKER, *mounts, "--", *command]


# Files and folders in a kept account folder that name programs the CLI starts: Claude
# Code's settings (hooks) and plugins, Codex's config.toml (notify, MCP servers) and hooks.
# Codex's rules folder holds command rules, whose allow decisions loosen what later runs
# ask before doing. Claude Code's .claude.json can't be read-only (the CLI rewrites it on
# every run), so its MCP servers are ignored instead: see strict_mcp_args.
PROGRAM_FILES = (
    "settings.json",
    "settings.local.json",
    "plugins",
    "config.toml",
    "hooks.json",
    "rules",
    # Codex loads $CODEX_HOME/.env into its own environment, which reaches the MCP
    # servers and notify programs it starts outside its sandbox.
    ".env",
)
# What else in a kept Grok account folder starts programs or loosens what Grok asks
# before running one: hooks and their path list, policy and sandbox files, folder
# trust (which turns on a project's hooks), plugins, workflows (JavaScript), language
# servers, the pager and status line commands, and the agent binaries in bin.
GROK_PROGRAM_FILES = (
    "hooks",
    "hooks-paths",
    "disabled-hooks",
    "managed_config.toml",
    "requirements.toml",
    "sandbox.toml",
    "trusted_folders.toml",
    "installed-plugins",
    "plugin-data",
    "bundled",
    "bundles",
    "workflows",
    "lsp.json",
    "pager.toml",
    "statusline.sh",
    "bin",
)
# Environment variables naming the account folder a CLI uses.
ACCOUNT_FOLDER_ENV = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GROK_HOME")


def account_folders(env: dict[str, str] | None) -> list[Path]:
    """The account folders a CLI's environment names, which its own process needs."""
    return [Path(env[name]) for name in ACCOUNT_FOLDER_ENV if env and env.get(name)]


STRICT_MCP = "--strict-mcp-config"


def raticode_claude_account(env: dict[str, str] | None) -> Path | None:
    """The Raticode Claude Code account folder ``env`` runs as, or None."""
    from gofer.core.provider_accounts import ACCOUNTS_FOLDER_SUFFIX

    value = (env or {}).get("CLAUDE_CONFIG_DIR")
    if not value:
        return None
    folder = Path(value)
    return folder if folder.parent.name == "claude" + ACCOUNTS_FOLDER_SUFFIX else None


def strict_mcp_args(command: list[str], env: dict[str, str] | None) -> list[str]:
    """``command`` with ``--strict-mcp-config`` when it runs as a Raticode Claude account.

    The account's ``.claude.json`` stays writable to the turn, since Claude Code rewrites
    it on every run, and ``claude mcp add`` from a turn's shell writes a server there that
    a later run of the account would start outside Claude Code's sandbox. With this flag
    Claude Code starts only the servers Raticode passes in ``--mcp-config``, and none from
    the account's settings, ``.claude.json`` or a project's ``.mcp.json``.
    """
    if raticode_claude_account(env) is None or STRICT_MCP in command:
        return command
    return [*command, STRICT_MCP]


def _create_empty(path: Path, text: str) -> None:
    """Create the owner-only file ``path`` holding ``text``, unless something is there."""
    if os.path.lexists(path):
        return
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        pass


def _program_files(folder: Path) -> list[Path]:
    """The kept account's files that name programs, creating the main ones first.

    A missing program file is one the turn's own shell could create, and a later run of
    the account would load it. So a Claude Code account gets an empty settings.json, a
    Grok account an empty config.toml, and a Codex account an empty config.toml, an
    empty .env and an empty rules folder before they are bound read-only. Plugins come
    only from settings.
    """
    from gofer.core.provider_accounts import ACCOUNTS_FOLDER_SUFFIX

    claude = folder.parent.name == "claude" + ACCOUNTS_FOLDER_SUFFIX
    grok = folder.parent.name == "grok" + ACCOUNTS_FOLDER_SUFFIX
    codex = folder.parent.name == "codex" + ACCOUNTS_FOLDER_SUFFIX
    if claude:
        _create_empty(folder / "settings.json", "{}\n")
    elif grok:
        _create_empty(folder / "config.toml", "")
    elif codex:
        _create_empty(folder / "config.toml", "")
        _create_empty(folder / ".env", "")
        if not os.path.lexists(folder / "rules"):
            try:
                os.mkdir(folder / "rules", 0o700)
            except OSError:
                pass
    names: tuple[str, ...] = PROGRAM_FILES
    if grok:
        names = (*PROGRAM_FILES, *GROK_PROGRAM_FILES)
    return [folder / name for name in names if os.path.lexists(folder / name)]


def mask_arguments(folders: Iterable[Path], keep: Iterable[Path] = ()) -> list[str]:
    """bubblewrap arguments that cover each existing folder with an empty read-only one.

    Each ``keep`` folder inside a covered folder is bound back, with its program files
    read-only.
    """
    from gofer.core.codex_sandbox import existing_denied

    kept = [Path(os.path.abspath(path)) for path in keep if Path(path).is_dir()]
    args: list[str] = ["--proc", "/proc"]
    for path in existing_denied(folders):
        if not path.is_dir():
            args += ["--ro-bind", os.devnull, str(path)]
            continue
        args += ["--tmpfs", str(path)]
        for folder in kept:
            for variant in dict.fromkeys((folder, folder.resolve())):
                if variant != path and variant.is_relative_to(path):
                    args += ["--bind", str(variant), str(variant)]
                    for item in _program_files(variant):
                        args += ["--ro-bind", str(item), str(item)]
        args += ["--remount-ro", str(path)]
    return args


def _claude_sandboxed(command: list[str]) -> bool:
    """Whether a Claude Code command turns on Claude Code's own sandbox (``--settings``)."""
    import json

    for index, arg in enumerate(command[:-1]):
        if arg != "--settings":
            continue
        try:
            settings = json.loads(command[index + 1])
        except ValueError:
            continue
        if isinstance(settings, dict) and (settings.get("sandbox") or {}).get("enabled") is True:
            return True
    return False


def needs_mask(provider: str, command: list[str]) -> bool:
    """Whether this provider command runs its tools without a sandbox that hides the folders.

    Codex with Raticode's permission profile and Claude Code with its sandbox on already
    hide them, and a second bubblewrap layer around them isn't needed.
    """
    if provider == "codex":
        from gofer.core.codex_sandbox import is_guarded

        return not is_guarded(command)
    if provider == "claude_code":
        return not _claude_sandboxed(command)
    return provider in UNSANDBOXED_PROVIDERS


UNSANDBOXED_PROVIDERS = frozenset({"cursor", "copilot", "opencode", "antigravity", "grok"})


def mask_unsandboxed(
    provider: str,
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    data_dir: Path | None = None,
    check: list[str] | None = None,
) -> list[str]:
    """``command`` masked when ``needs_mask`` says so for ``check`` (default ``command``)."""
    if not needs_mask(provider, check if check is not None else command):
        return command
    return masked_command(command, data_dir=data_dir, keep=account_folders(env))


def masked_command(
    command: list[str],
    *,
    data_dir: Path | None = None,
    keep: Iterable[Path] = (),
    platform: str | None = None,
    which: Callable[[str], str | None] | None = None,
) -> list[str]:
    """``command`` started so it can't see Raticode's credential folders, where possible."""
    if not ENABLED or not command or is_masked(command):
        return command
    which = which or shutil.which
    reason = unavailable_reason(platform, which)
    bwrap = which("bwrap")
    if reason or bwrap is None:
        logger.warning("Credential folders stay visible to %s: %s", command[0], reason)
        return command
    mounts = mask_arguments(credential_folders(data_dir), keep)
    return [bwrap, *_MARKER, *mounts, "--", *command]
