"""Ask whether a process is still running, without ever signalling it.

``os.kill(pid, 0)`` only checks a process on POSIX. On Windows any signal number other
than the two console events makes ``os.kill`` call ``TerminateProcess``, so the same
check ends the process it meant to ask about, or whichever program now has that ID.
"""

from __future__ import annotations

import functools
import os
import sys
from typing import Any

# Enough to read a process's exit code, and granted for most processes this user runs.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def pid_alive(pid: object) -> bool:
    """Whether a process with this ID is running. Never sends it a signal on Windows.

    A process this user may not signal (``PermissionError``) counts as running.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@functools.cache
def _kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined, unused-ignore]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _windows_pid_alive(pid: int) -> bool:
    if pid > 0xFFFFFFFF:
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Access denied means a process exists that this user can't query.
        return bool(ctypes.get_last_error() == 5)  # type: ignore[attr-defined, unused-ignore]
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return bool(code.value == _STILL_ACTIVE)
    finally:
        kernel32.CloseHandle(handle)
