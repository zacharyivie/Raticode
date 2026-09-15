from __future__ import annotations

import os
import sys
from pathlib import Path


def get_data_dir() -> Path:
    """Return the OS-appropriate user data directory for Raticode."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "gofer"
