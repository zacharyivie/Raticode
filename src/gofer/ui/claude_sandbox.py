"""Operating-system limits for Claude Code turns that nobody is watching.

Rem's self-work and every independent review run Claude Code with
``--permission-mode dontAsk`` and no one at the screen. Without these limits
their shell could read SSH keys and Claude's own login, reach any host, and
write to the project a review is only meant to read.

Claude Code's sandbox (Seatbelt on macOS, bubblewrap on Linux and WSL2) covers
shell commands and their children only. The Read, Edit and Write tools follow
permission rules instead, so the same secrets also get permission deny rules.
Native Windows has no Claude Code sandbox, so these turns can't run there.
A headless backend's secrets folder is hidden from both the same way.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

# Credential stores a shell started by an unattended agent has no reason to
# read. Paths that don't exist on this computer are simply skipped by the
# sandbox, so one list serves Linux and macOS.
SECRET_PATHS = (
    "~/.ssh",
    "~/.gnupg",
    "~/.claude",
    "~/.claude.json",
    "~/.codex/auth.json",
    "~/.aws",
    "~/.azure",
    "~/.config/gcloud",
    "~/.kube",
    "~/.docker/config.json",
    "~/.netrc",
    "~/.git-credentials",
    "~/.config/git/credentials",
    "~/.config/gh",
    "~/.local/share/keyrings",
    "~/.password-store",
    "~/.npmrc",
    "~/.pypirc",
    "~/Library/Keychains",
)
# Raticode's own data folder holds paired-device keys and site grants. The shell
# can't see the folder at all; the Read tool is kept from these parts of it.
DATA_SECRETS = ("devices", "browser-grants")


def secrets_folders() -> list[str]:
    """A headless backend's read-only secrets folder and systemd's credentials folder.

    The configured folder (``--secrets-dir``, or ``$CREDENTIALS_DIRECTORY`` by default)
    holds the Jev key and other keychain secrets, such as Organizations and workflow
    secrets. systemd's folder is listed too in case it differs, from the sealed copy once
    startup moved it out of the environment. Each folder is listed as given and as
    resolved, so a symlinked path can't be read under its other name.
    """
    from gofer.core.secrets_folder import CREDENTIALS_DIRECTORY_ENV, configured_secrets_dir
    from gofer.utils.process import credential_env

    candidates: list[Path] = []
    configured = configured_secrets_dir()
    if configured is not None:
        candidates.append(configured)
    systemd = credential_env(CREDENTIALS_DIRECTORY_ENV).strip()
    if systemd:
        candidates.append(Path(systemd))
    folders: list[str] = []
    for folder in candidates:
        folder = Path(os.path.abspath(folder.expanduser()))
        for path in (folder, folder.resolve()):
            if str(path) not in folders:
                folders.append(str(path))
    return folders


def unavailable_reason(
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    is_file: Callable[[Path], bool] = Path.is_file,
) -> str | None:
    """Why Claude Code's sandbox can't start on this computer, or None when it can."""
    platform = platform or sys.platform
    if platform == "win32":
        return (
            "Claude Code has no sandbox on Windows, and unattended Claude Code runs need one. "
            "Run Raticode inside WSL2, or use Codex for Rem's tasks on Windows."
        )
    if platform == "darwin":
        if not is_file(Path("/usr/bin/sandbox-exec")):
            return "Claude Code's sandbox needs macOS's sandbox-exec, which isn't on this Mac."
        return None
    missing = [tool for tool in ("bwrap", "socat") if which(tool) is None]
    if missing:
        names = " and ".join({"bwrap": "bubblewrap (bwrap)", "socat": "socat"}[m] for m in missing)
        return (
            f"Claude Code's sandbox needs {names}, which {'is' if len(missing) == 1 else 'are'} "
            "not installed. Install it with your package manager, for example "
            "`sudo apt install bubblewrap socat`."
        )
    return None


def git_common_dir(root: Path) -> Path | None:
    """The shared ``.git`` folder of a worktree, when it lives outside ``root``."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    common = Path(completed.stdout.strip()).resolve()
    return None if common.is_relative_to(root.resolve()) else common


def _rule_paths(path: str) -> list[str]:
    """Permission rule specifiers for a path and, if it is a folder, everything in it."""
    if path.startswith("/"):
        # Permission rules write absolute paths with two slashes.
        path = "/" + path
    return [path, f"{path}/**"]


def permission_deny_rules(data_dir: Path, secret_folders: Iterable[str] = ()) -> list[str]:
    paths = [*SECRET_PATHS, *secret_folders, *(str(data_dir / name) for name in DATA_SECRETS)]
    return [
        f"{tool}({spec})"
        for path in paths
        for spec in _rule_paths(path)
        for tool in ("Read", "Edit")
    ]


def _unique(paths: Iterable[Path | None]) -> list[str]:
    seen: list[str] = []
    for path in paths:
        if path is None:
            continue
        text = str(path)
        if text not in seen:
            seen.append(text)
    return seen


def sandbox_settings(
    *,
    root: Path,
    data_dir: Path,
    read_only: bool,
    writable: Iterable[Path | None] = (),
    visible: Iterable[Path | None] = (),
    protected: Iterable[Path | None] = (),
) -> dict[str, Any]:
    """Claude Code settings that sandbox the shell and deny secrets to every tool.

    ``writable`` adds folders the shell may write besides the project (or none
    in a read-only turn) and Claude Code's own temp folder. ``visible`` are
    paths inside Raticode's data folder the shell still needs to read, such as
    the command-slot wrappers. ``protected`` are
    extra folders the turn may read but not change (Claude Code makes every
    ``--add-dir`` writable to the shell otherwise).
    """
    root = root.resolve()
    data_dir = data_dir.resolve()
    hidden = secrets_folders()
    writes = [Path(path).resolve() for path in writable if path is not None]
    # A denied folder also denies every folder inside it, so an extra folder
    # that holds one the turn must write, like its scratch folder, stays open.
    # A read-only project is denied regardless.
    kept = [Path(path).resolve() for path in protected if path is not None]
    deny_write = [*([root] if read_only else [])] + [
        path for path in kept if not any(w.is_relative_to(path) for w in writes)
    ]
    # Writable folders must be readable too. A folder re-opened for reading is
    # mounted read-only over everything inside it, so a parent of a writable
    # folder is never listed; name the parts the shell reads instead.
    seen = [Path(path).resolve() for path in visible if path is not None]
    allow_read = [
        *(path for path in seen if not any(w != path and w.is_relative_to(path) for w in writes)),
        *writes,
        *([root] if root.is_relative_to(data_dir) else []),
    ]
    return {
        "sandbox": {
            "enabled": True,
            # Refuse to start rather than quietly running commands unsandboxed.
            "failIfUnavailable": True,
            # A blocked command can't ask to run again outside the sandbox.
            "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": True,
            "filesystem": {
                "denyRead": [*SECRET_PATHS, *hidden, str(data_dir)],
                "allowRead": _unique(allow_read),
                "allowWrite": _unique(writes),
                "denyWrite": _unique(deny_write),
            },
            # Shell commands reach no host. WebFetch and WebSearch are separate
            # tools that a turn only has when its web access is on.
            "network": {"allowedDomains": [], "strictAllowlist": True},
        },
        # The sandbox doesn't cover the Read and Edit tools.
        "permissions": {"deny": permission_deny_rules(data_dir, hidden)},
    }


def sandbox_environment(scratch: Path, *, read_only: bool) -> dict[str, str]:
    """Environment for a sandboxed turn: temp files and caches in its scratch folder.

    Claude Code keeps its own temp files in ``CLAUDE_CODE_TMPDIR``, so they go
    away with the scratch folder instead of piling up in a RAM-backed /tmp. It
    still gives sandboxed commands a short ``$TMPDIR`` of its own choosing. A
    read-only turn can't write caches into the project, so they move here too.
    """
    from gofer.core.command_slots import SANDBOX_ENV
    from gofer.ui.chat import scratch_environment

    env = {
        "CLAUDE_CODE_TMPDIR": str(scratch),
        "npm_config_cache": str(scratch / "npm-cache"),
        SANDBOX_ENV: "1",
    }
    if read_only:
        cached = scratch_environment(scratch)
        # Sandboxed commands keep the user's cache folder readable, so tools
        # such as Playwright still find what is installed there.
        cached.pop("XDG_CACHE_HOME", None)
        for name in ("TMPDIR", "TMP", "TEMP"):
            cached.pop(name, None)
        env.update(cached)
    return env


def sandbox_instructions(*, read_only: bool, scratch: Path | None) -> str:
    """What the agent should know so sandbox refusals don't look like bugs."""
    project = (
        "The project is read-only to the shell. "
        if read_only
        else "The shell can change only this task's project. "
    )
    caches = f"Test and tool caches go to a private scratch folder, {scratch}. " if scratch else ""
    return (
        "\nNobody is watching this run, so Claude Code runs your shell commands in an "
        "operating-system sandbox. Shell commands have no network access, and credential "
        "folders such as ~/.ssh and ~/.claude are hidden. "
        + project
        + caches
        + "If a command fails only because of the sandbox, say so in your result instead of "
        "working around it."
    )


# While a sandboxed command runs, Claude Code write-protects shell and tool
# settings in the folder it started in (.bashrc, .gitconfig, .mcp.json,
# .claude/settings.json and so on). bubblewrap can only mount over a path that
# exists, so for each one that is missing it leaves an empty read-only file.
# Claude Code removes them when the command ends, but not when the turn is
# stopped mid-command, and they show in the project as untracked files the
# whole time a long command runs. Measured with Claude Code 2.1.319 on Linux.
STUB_FOLDERS = ("", ".claude")
STUB_MODE = 0o444

_stub_lock = threading.Lock()
# Sandboxed turns running in each folder, and what was there before the first.
_stub_turns: dict[Path, tuple[int, frozenset[str]]] = {}


def _stub_entries(root: Path) -> frozenset[str]:
    names: set[str] = set()
    for folder in STUB_FOLDERS:
        try:
            with os.scandir(root / folder) as entries:
                names.update(os.path.join(folder, entry.name) for entry in entries)
        except OSError:
            continue
    return frozenset(names)


def _is_mount_stub(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(info.st_mode) and info.st_size == 0 and stat.S_IMODE(info.st_mode) == STUB_MODE
    )


def remove_mount_stubs(root: Path, before: frozenset[str]) -> list[str]:
    """Delete the empty read-only files the sandbox left in ``root`` since ``before``.

    Only entries that are new, empty, regular and exactly read-only for everyone
    go, so nothing a person or agent wrote is touched.
    """
    removed: list[str] = []
    for name in sorted(_stub_entries(root) - before):
        path = root / name
        if not _is_mount_stub(path):
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(name)
    return removed


def track_mount_stubs(root: Path) -> Path:
    """Note what is in ``root`` before a sandboxed turn starts there."""
    root = root.resolve()
    with _stub_lock:
        count, before = _stub_turns.get(root, (0, frozenset()))
        if count == 0:
            before = _stub_entries(root)
        _stub_turns[root] = (count + 1, before)
    return root


def release_mount_stubs(root: Path) -> list[str]:
    """Clean up after the last sandboxed turn in ``root`` ends.

    While another of Raticode's sandboxed turns still runs there its commands
    may be using the files, so they stay until that turn ends too.
    """
    with _stub_lock:
        entry = _stub_turns.pop(root, None)
        if entry is None:
            return []
        count, before = entry
        if count > 1:
            _stub_turns[root] = (count - 1, before)
            return []
        return remove_mount_stubs(root, before)
