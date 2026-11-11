"""Folders Rem opens or creates as projects when the user's request needs one.

Rem acts for the user, so it opens and creates project folders without asking. It
never deletes or overwrites anything: an existing folder is opened as it is. It
refuses places where an agent workspace would expose credentials, Raticode's own
data or live system state, and reports the operating system's own refusals plainly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Opened projects waiting for the desktop window to list them as its own.
MAX_PENDING = 20
# Places no agent workspace may start in or contain, beyond the home credential
# stores. The OS also refuses most writes here; Rem refuses reads too.
SYSTEM_ROOTS: tuple[str, ...] = (
    ("C:/Windows", "C:/Program Files", "C:/Program Files (x86)", "C:/ProgramData")
    if sys.platform == "win32"
    else (
        "/proc",
        "/sys",
        "/dev",
        "/run",
        "/boot",
        "/etc",
        # macOS: the system volume and the real home of /etc. (/private/var also holds
        # the per-user temporary folders, so it isn't refused as a whole.)
        "/System",
        "/private/etc",
        "/Library/Keychains",
    )
)

_create_lock = threading.Lock()


class ProjectOpen(BaseModel):
    """One folder the user's request needs open: ``{path, create?, git_init?}``."""

    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    # Make the folder (and missing parents) when it doesn't exist yet.
    create: bool = Field(default=False, strict=True)
    # Start a git repository, but only in a folder this request created or found empty.
    git_init: bool = Field(default=False, strict=True)

    @field_validator("path")
    @classmethod
    def plain_path(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value or any(ord(c) < 32 for c in value):
            raise ValueError("Use a plain folder path")
        return value


class ProjectOpenError(ValueError):
    """A folder Rem couldn't open, with the reason in words the user can act on."""


def _inside(path: Path, place: Path) -> bool:
    return path == place or path.is_relative_to(place)


def refusal(path: Path, *, data_dir: Path, home: Path | None = None) -> str | None:
    """Why ``path`` (absolute, symlinks resolved) can't be a Rem project, or None."""
    from gofer.ui.rem_home import SENSITIVE_HOME

    home = (home or Path.home()).resolve()
    shown = str(path)
    if path == Path(path.anchor):
        return f"{shown} is the whole filesystem, not a project folder."
    if home == path or home.is_relative_to(path):
        return f"{shown} holds your entire home folder. Name a project folder inside it instead."
    for item in SENSITIVE_HOME:
        place = home / item
        if _inside(path, place) or place.is_relative_to(path):
            return (
                f"{shown} is or contains {place}, where credentials and browser data are kept, "
                "so Rem won't open it as an agent workspace. If you really want that, open it "
                "yourself with File > Open Folder."
            )
    for item in SYSTEM_ROOTS:
        place = Path(item)
        if _inside(path, place):
            return f"{shown} is system state ({item}), not a project folder."
    data = data_dir.resolve()
    # Raticode's data root also holds its trusted tools (.gofer-trusted-bin).
    root = data.parent if (data.parent / ".gofer-trusted-bin").is_dir() else data
    if _inside(path, root) or root.is_relative_to(path):
        return (
            f"{shown} is or contains Raticode's own data folder ({root}). Opening it as a "
            "project would let workers edit Rem's records."
        )
    return None


def _os_error(action: str, path: Path, exc: OSError) -> ProjectOpenError:
    if isinstance(exc, PermissionError):
        return ProjectOpenError(
            f"The operating system refused to {action} {path}: permission denied. Choose a "
            "folder your account can write to, or change that folder's permissions."
        )
    reason = exc.strerror or type(exc).__name__
    return ProjectOpenError(f"Couldn't {action} {path}: {reason}.")


def _empty(path: Path) -> bool:
    with os.scandir(path) as entries:
        return next(entries, None) is None


def _git_init(path: Path) -> None:
    try:
        result = subprocess.run(
            ["git", "init", "--quiet", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise ProjectOpenError(
            f"Created {path}, but git isn't installed, so it has no repository yet."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ProjectOpenError(f"Created {path}, but git init didn't finish in time.") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
        raise ProjectOpenError(f"Created {path}, but git init failed: {detail[0][:300]}")


def prepare_project(
    request: ProjectOpen, *, data_dir: Path, home: Path | None = None
) -> dict[str, Any]:
    """Check, and when asked create, one project folder. Never changes an existing folder.

    Returns ``{root, name, created, git_initialized, existing}``. ``root`` is the
    canonical path that grants and tasks use. Raises ProjectOpenError with the reason.
    """
    raw = Path(os.path.expanduser(request.path))
    if not raw.is_absolute():
        raise ProjectOpenError(f"Use an absolute folder path, not {request.path!r}.")
    # Check the path as written and as the filesystem resolves it, so a symlink
    # can't lead a harmless-looking name into a refused place.
    lexical = Path(os.path.normpath(raw))
    for candidate in (lexical, lexical.resolve()):
        reason = refusal(candidate, data_dir=data_dir, home=home)
        if reason:
            raise ProjectOpenError(reason)
    created = False
    with _create_lock:
        try:
            if os.path.lexists(lexical):
                if not lexical.is_dir():
                    raise ProjectOpenError(
                        f"{lexical} is a file, not a folder. Nothing was changed. Name a "
                        "different path for the project."
                    )
            elif not request.create:
                raise ProjectOpenError(
                    f"{lexical} doesn't exist. Ask Rem to create it as a new project."
                )
            else:
                try:
                    lexical.mkdir(parents=True)
                    created = True
                except FileExistsError:
                    # Something appeared there between the check and mkdir.
                    if not lexical.is_dir():
                        raise ProjectOpenError(
                            f"{lexical} appeared as a file while Rem was creating it. Nothing "
                            "was changed."
                        ) from None
            root = lexical.resolve(strict=True)
        except ProjectOpenError:
            raise
        except OSError as exc:
            raise _os_error("create" if request.create else "open", lexical, exc) from exc
    reason = refusal(root, data_dir=data_dir, home=home)
    if reason:
        raise ProjectOpenError(reason)
    if not os.access(root, os.R_OK | os.X_OK):
        raise ProjectOpenError(
            f"The operating system won't let Raticode read {root}: permission denied."
        )
    try:
        empty = _empty(root)
    except OSError as exc:
        raise _os_error("open", root, exc) from exc
    git_initialized = False
    if request.git_init and (created or empty) and not (root / ".git").exists():
        if not os.access(root, os.W_OK):
            raise ProjectOpenError(
                f"The operating system won't let Raticode write to {root}, so it can't start a "
                "git repository there: permission denied."
            )
        _git_init(root)
        git_initialized = True
    return {
        "root": str(root),
        "name": root.name,
        "created": created,
        "git_initialized": git_initialized,
        "existing": not created and not empty,
    }


def opened_note(opened: list[dict[str, Any]]) -> str:
    """One line per opened folder for Rem's reply, from what actually happened."""
    lines = []
    for item in opened:
        if item["created"]:
            what = "Created and opened"
        elif item.get("already_open"):
            what = "Already open:"
        else:
            what = "Opened"
        extra = (
            " with a new git repository"
            if item["git_initialized"]
            else " as it is; nothing in it was changed"
            if item["existing"] and not item.get("already_open")
            else ""
        )
        lines.append(f"{what} `{item['root']}`{extra}.")
    return "\n".join(lines)
