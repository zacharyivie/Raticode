"""The owner-only file rule shared by ``--token-file`` and the headless secrets folder."""

from __future__ import annotations

import os
import stat
import sys


def private_file_problem(info: os.stat_result, *, secret: bool) -> str | None:
    """Why a file fails the owner-only rule, or None when it passes.

    The file must be a regular file that belongs to this account. Other accounts may not
    change it, and for a ``secret`` they may not read it either (mode 600 or 400).
    Windows has no such modes, so nothing is checked there.
    """
    if sys.platform == "win32":
        return None
    if not stat.S_ISREG(info.st_mode):
        return "must be a regular file"
    if info.st_uid != os.getuid():
        return "belongs to another account"
    if info.st_mode & (0o077 if secret else 0o022):
        needed = "600" if secret else "go-w"
        return f"is open to other accounts; run chmod {needed} on it"
    return None
