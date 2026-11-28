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

import logging
import os
import shutil
import stat
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from gofer.core.agent_credentials import (
    HOME_SECRET_PATHS,
    data_credential_names,
)
from gofer.core.agent_credentials import secrets_folders as _secrets_folders
from gofer.utils.process import run_repository_git

log = logging.getLogger(__name__)

# Credential stores a shell started by an unattended agent has no reason to read.
SECRET_PATHS = HOME_SECRET_PATHS


def data_secrets(data_dir: Path) -> list[str]:
    """Every credential folder in the data folder, as names (see agent_credentials).

    Raticode's own data folder holds paired-device keys, site grants and every
    provider's account folders. The shell can't see the folder at all; the Read tool
    is kept from these parts of it.
    """
    return data_credential_names(data_dir)


def secrets_folders() -> list[str]:
    """A headless backend's secrets folder and systemd's credentials folder, as paths."""
    return [str(folder) for folder in _secrets_folders()]


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
    common = repository_git_dir(root)
    if common is None:
        return None
    return None if common.is_relative_to(root.resolve()) else common


def git_control_paths(root: Path, common: Path | None) -> list[Path]:
    """Parts of a worktree's shared ``.git`` folder a turn granted that folder can't change.

    Git runs hooks, and config can name more programs (``core.hooksPath``,
    ``core.fsmonitor``, filters), the next time anyone runs git in the repository,
    outside any sandbox. Claude Code protects these only under its own working folder,
    which for a worktree is the checkout, not the shared folder. The worktree's own
    ``commondir``, ``gitdir`` and ``config.worktree`` say which repository it belongs to,
    and other worktrees' admin folders hold their checkouts' state and config. Commits
    still write objects, refs, logs and the worktree's own index and HEAD.
    """
    if common is None:
        return []
    from gofer.rattish.agent_sandbox import ADMIN_PROTECTED_FILES, _worktree_admin_dir

    own = _worktree_admin_dir(common, root)
    paths = [common / "hooks", common / "config", common / "info"]
    if own is not None:
        paths += [own / name for name in ADMIN_PROTECTED_FILES]
    try:
        admins = sorted((common / "worktrees").iterdir())
    except OSError:
        admins = []
    paths += [path for path in admins if path.is_dir() and path.resolve() != own]
    try:
        modules = sorted((common / "modules").iterdir())
    except OSError:
        modules = []
    # Submodules keep their own hooks and config here.
    paths += [module / name for module in modules for name in ("hooks", "config")]
    return paths


def git_control_deny_rules(paths: Iterable[Path]) -> list[str]:
    """Edit rules for ``git_control_paths``: the sandbox covers only the shell."""
    return [f"Edit({spec})" for path in paths for spec in _rule_paths(str(path))]


def repository_git_dir(root: Path) -> Path | None:
    """The shared ``.git`` folder of the repository ``root`` is in, when that repository
    lists ``root``'s checkout back. None outside a repository and in every other case.

    Git finds the shared folder by following the checkout's own ``.git`` file and any
    ``commondir`` file in the folder it names. An earlier turn can write both, so they
    alone could hand this turn another repository's ``.git``. The answer is used only
    when the checkout's ``.git`` is a real folder that is both git's folder and the
    shared one, or when git's folder is ``<shared>/worktrees/<id>`` and its ``gitdir``
    file, which git writes in the repository and a turn can't write in another one,
    names the checkout's ``.git`` file. Otherwise the reason is logged once and the
    turn gets no shared folder, so its commits fail with git's own error.
    """
    try:
        completed = run_repository_git(
            root,
            "rev-parse",
            "--path-format=absolute",
            "--show-toplevel",
            "--absolute-git-dir",
            "--git-common-dir",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = completed.stdout.splitlines()
    if completed.returncode != 0 or len(lines) != 3 or not all(lines):
        return None
    top, git_dir, common = (Path(line).resolve() for line in lines)
    reason = _unlisted_checkout(root.resolve(), top, git_dir, common)
    if reason is not None:
        _log_refused_git_dir(root, common, reason)
        return None
    return common


def _unlisted_checkout(root: Path, top: Path, git_dir: Path, common: Path) -> str | None:
    """Why ``common`` isn't the repository of the checkout at ``top``, or None when it is.

    ``top`` is the checkout folder holding ``root`` (``root`` itself unless the turn
    works in a subfolder), so the ``.git`` checked is the one git found for ``root``.
    """
    if not root.is_relative_to(top):
        return f"git names {top} as the checkout, which doesn't hold the folder"
    dot_git = top / ".git"
    if dot_git.is_symlink():
        return f"{dot_git} is a link"
    if dot_git.is_dir():
        if git_dir == common == dot_git:
            return None
        return f"{dot_git} is a folder, but git reads {git_dir}"
    if not dot_git.is_file():
        return f"{dot_git} is missing"
    if git_dir.parent != common / "worktrees":
        return f"{git_dir} isn't one of {common}'s worktree folders"
    try:
        named = (git_dir / "gitdir").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return f"{git_dir / 'gitdir'} can't be read"
    # Git writes an absolute path; with worktree.useRelativePaths it is relative to git_dir.
    if not named or (git_dir / named).resolve() != dot_git:
        return f"{common} doesn't list {top} as one of its worktrees"
    return None


_refusals_logged: set[tuple[str, str]] = set()
_refusals_lock = threading.Lock()


def _log_refused_git_dir(root: Path, common: Path, reason: str) -> None:
    key = (str(root), reason)
    with _refusals_lock:
        if key in _refusals_logged:
            return
        _refusals_logged.add(key)
    log.warning("A sandboxed turn in %s gets no write access to %s: %s.", root, common, reason)


def _rule_paths(path: str) -> list[str]:
    """Permission rule specifiers for a path and, if it is a folder, everything in it."""
    if path.startswith("/"):
        # Permission rules write absolute paths with two slashes.
        path = "/" + path
    return [path, f"{path}/**"]


def permission_deny_rules(data_dir: Path, secret_folders: Iterable[str] = ()) -> list[str]:
    paths = [
        *SECRET_PATHS,
        *secret_folders,
        *(str(data_dir / name) for name in data_secrets(data_dir)),
    ]
    return [
        f"{tool}({spec})"
        for path in paths
        for spec in _rule_paths(path)
        for tool in ("Read", "Edit")
    ]


def hidden_deny_rules(folders: Iterable[str]) -> list[str]:
    """Read, Edit, Grep and Glob deny rules for folders a turn must not see at all.

    Claude Code 2.1.319 matches Grep and Glob against Read rules; the rules under their
    own names are kept for any version that matches those tools by name.
    """
    return [
        f"{tool}({spec})"
        for path in folders
        for spec in _rule_paths(path)
        for tool in ("Read", "Edit", "Grep", "Glob")
    ]


def credential_deny_rules(data_dir: Path) -> list[str]:
    """Read and Edit deny rules for every Raticode credential folder, for any Claude turn.

    Account folders, paired-device keys, site grants and a headless secrets folder
    (``gofer.core.agent_credentials``), including folders not made yet. Edit rules also
    cover the Write tool. Turns the user watches keep their home folder's own
    credentials; unattended turns hide those too (``permission_deny_rules``).
    """
    from gofer.core.agent_credentials import credential_folders

    return [
        f"{tool}({spec})"
        for folder in credential_folders(data_dir)
        for spec in _rule_paths(str(folder))
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
    hidden: Iterable[Path] = (),
) -> dict[str, Any]:
    """Claude Code settings that sandbox the shell and deny secrets to every tool.

    ``writable`` adds folders the shell may write besides the project (or none
    in a read-only turn) and Claude Code's own temp folder. ``visible`` are
    paths inside Raticode's data folder the shell still needs to read, such as
    the command-slot wrappers. ``protected`` are
    extra folders the turn may read but not change (Claude Code makes every
    ``--add-dir`` writable to the shell otherwise). ``hidden`` are folders, inside
    the turn's projects or anywhere else, that neither the shell nor any file tool may
    read or change, such as a Second Brain that keeps cloud processing off.
    """
    root = root.resolve()
    data_dir = data_dir.resolve()
    private = [str(Path(path).resolve()) for path in hidden]
    secret_folders = secrets_folders()
    writes = [Path(path).resolve() for path in writable if path is not None]
    # A denied folder also denies every folder inside it, so an extra folder
    # that holds one the turn must write, like its scratch folder, stays open.
    # A read-only project is denied regardless.
    kept = [Path(path).resolve() for path in protected if path is not None]
    deny_write = [
        *([root] if read_only else []),
        *(path for path in kept if not any(w.is_relative_to(path) for w in writes)),
        *(Path(path) for path in private),
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
    for folder in map(Path, private):
        # A folder re-opened for reading would show what is inside it again.
        if any(folder.is_relative_to(path) or path.is_relative_to(folder) for path in allow_read):
            raise ValueError(f"The sandbox can't hide {folder}: the turn must read or write in it")
    return {
        "sandbox": {
            "enabled": True,
            # Refuse to start rather than quietly running commands unsandboxed.
            "failIfUnavailable": True,
            # A blocked command can't ask to run again outside the sandbox.
            "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": True,
            "filesystem": {
                "denyRead": [*SECRET_PATHS, *secret_folders, str(data_dir), *private],
                "allowRead": _unique(allow_read),
                "allowWrite": _unique(writes),
                "denyWrite": _unique(deny_write),
            },
            # Shell commands reach no host. WebFetch and WebSearch are separate
            # tools that a turn only has when its web access is on.
            "network": {"allowedDomains": [], "strictAllowlist": True},
        },
        # The sandbox doesn't cover the Read and Edit tools.
        "permissions": {
            "deny": [*permission_deny_rules(data_dir, secret_folders), *hidden_deny_rules(private)]
        },
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


# Claude Code reads a project's own .claude/settings.json and settings.local.json
# unless told otherwise, and the hooks they name run outside the sandbox, so a
# cloned repository could run anything as the user. Unattended turns load only the
# user's settings and Raticode's own --settings (whose hook queues tests). That also
# drops the project's CLAUDE.md files, which project_instruction_args passes back as
# plain text. Measured with Claude Code 2.1.319: project hooks don't run, Raticode's do.
PROJECT_SETTING_SOURCES = ("--setting-sources", "user")
INSTRUCTION_FILES = ("CLAUDE.md", ".claude/CLAUDE.md")
# All instruction files together; anything past this is cut with a note.
MAX_INSTRUCTION_BYTES = 64 * 1024
# Nested CLAUDE.md files listed by path, and how much of the tree is searched for them.
MAX_NESTED_INSTRUCTIONS = 40
MAX_NESTED_DEPTH = 6
MAX_NESTED_FOLDERS = 5000
_SKIPPED_FOLDERS = frozenset(
    {"node_modules", "build", "dist", "out", "target", "venv", "__pycache__", "vendor"}
)


def _read_instruction_file(path: Path, limit: int) -> str | None:
    """The file's text, or None if it's missing, a link or not a regular file."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(max(limit, 0) + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    text = data[:limit].decode("utf-8", errors="replace")
    return (
        text + "\n[Cut here: Raticode passes at most 64 KB of instructions.]"
        if len(data) > limit
        else text
    )


def _instruction_files_above(root: Path) -> list[Path]:
    """CLAUDE.md files in ``root`` and the folders above it, outermost first, below home.

    The walk stops before the home folder. ``--setting-sources user`` already loads
    ``~/.claude/CLAUDE.md``, so passing home's files as well would send them twice.
    """
    home = Path.home().resolve()
    folders: list[Path] = []
    for folder in (root, *root.parents):
        if folder == folder.parent or folder == home:
            break
        folders.append(folder)
    return [
        folder / name
        for folder in reversed(folders)
        for name in INSTRUCTION_FILES
        if (folder / name).is_file()
    ]


def _nested_instruction_files(root: Path) -> list[Path]:
    """CLAUDE.md files in folders below ``root``, skipping hidden and dependency folders."""
    found: list[Path] = []
    visited = 0
    for current, folders, files in os.walk(root):
        visited += 1
        depth = len(Path(current).relative_to(root).parts)
        folders[:] = sorted(
            name
            for name in folders
            if not name.startswith(".")
            and name not in _SKIPPED_FOLDERS
            and depth < MAX_NESTED_DEPTH
        )
        if depth and "CLAUDE.md" in files:
            found.append(Path(current) / "CLAUDE.md")
        if len(found) >= MAX_NESTED_INSTRUCTIONS or visited >= MAX_NESTED_FOLDERS:
            break
    return found


def project_instruction_args(root: Path) -> list[str]:
    """Claude Code arguments for an unattended turn: no project settings, its CLAUDE.md as text.

    ``--setting-sources user`` keeps a project's hooks, permissions and MCP servers out
    of the turn. The CLAUDE.md files Claude Code would have read in ``root`` and the
    folders above it, short of home, come back through ``--append-system-prompt``; links aren't
    followed, so a CLAUDE.md that points at a key file adds nothing. Nested CLAUDE.md
    files are listed so the agent reads the one for a folder it works in.
    """
    root = root.resolve()
    args = list(PROJECT_SETTING_SOURCES)
    blocks: list[str] = []
    remaining = MAX_INSTRUCTION_BYTES
    for path in _instruction_files_above(root):
        text = _read_instruction_file(path, remaining)
        if text is None or not text.strip():
            continue
        remaining -= len(text.encode("utf-8"))
        blocks.append(
            f"Contents of {path} (project instructions, checked into the codebase):\n\n{text}"
        )
        if remaining <= 0:
            break
    nested = _nested_instruction_files(root)
    if nested:
        listing = "\n".join(f"- {path.relative_to(root)}" for path in nested)
        blocks.append(
            "These folders have their own CLAUDE.md. Before working in one of them, read "
            f"its CLAUDE.md and follow it:\n{listing}"
        )
    if blocks:
        header = (
            "Raticode doesn't load this project's Claude Code settings in unattended turns, "
            "so the project's instruction files are given here instead."
        )
        args += ["--append-system-prompt", "\n\n".join([header, *blocks])]
    return args


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
