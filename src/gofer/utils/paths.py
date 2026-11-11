from __future__ import annotations

import os
import sys
from pathlib import Path

DATA_DIR_ENV = "GOFER_DATA_DIR"


def get_data_dir() -> Path:
    """Return Raticode's data directory.

    ``GOFER_DATA_DIR`` names the data folder itself. The desktop app sets it when the
    user chose a custom folder, so the CLI and the app agree on where data lives.
    Otherwise the folder is ``gofer`` inside the OS-appropriate user data directory.
    """
    configured = os.environ.get(DATA_DIR_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "gofer"


def restrict_to_owner(directory: Path) -> bool:
    """Make Raticode's data folder private to this account (mode 0700 on POSIX).

    Conversations, job logs and databases inside it can quote files and credentials an
    agent read. Removing group and other access on the folder protects everything below
    it without rewriting each file. The home folder or a folder above it is never
    changed. Returns whether the mode changed.
    """
    if sys.platform == "win32":
        return False
    try:
        folder = directory.resolve()
        if Path.home().resolve().is_relative_to(folder):
            return False
        info = folder.stat()
        if info.st_uid != os.getuid() or not info.st_mode & 0o077:
            return False
        folder.chmod(info.st_mode & 0o700)
        return True
    except OSError:
        return False
