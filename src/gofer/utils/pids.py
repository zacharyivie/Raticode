"""Ask whether a process is still running, and when it started, without ever signalling it.

``os.kill(pid, 0)`` only checks a process on POSIX. On Windows any signal number other
than the two console events makes ``os.kill`` call ``TerminateProcess``, so the same
check ends the process it meant to ask about, or whichever program now has that ID.

A PID is reused once its process ends, so a saved PID only names the same process
while its start time, read by ``process_start_token``, matches too.
"""

from __future__ import annotations

import functools
import os
import struct
import sys
from pathlib import Path
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
    filetime = ctypes.POINTER(wintypes.FILETIME)
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, filetime, filetime, filetime, filetime]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
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


def windows_process_state(pid: int) -> tuple[bool, str | None]:
    """Whether a Windows process is going, and a token for when it started.

    Only queries the process. A process this user may not query counts as gone,
    unlike in ``pid_alive``: callers use this for processes Raticode started.
    """
    if sys.platform != "win32" or pid <= 0 or pid > 0xFFFFFFFF:
        return False, None
    import ctypes
    from ctypes import wintypes

    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False, None
    try:
        code = wintypes.DWORD()
        # A process that exited with code 259 reads as going while another
        # program still holds a handle to it; Raticode's processes never exit so.
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False, None
        if code.value != _STILL_ACTIVE:
            return False, None
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return True, None
        # Creation time in 100 ns steps since 1601, the same for the life of
        # the process and different for any later process given its PID.
        return True, f"win:{(created.dwHighDateTime << 32) | created.dwLowDateTime}"
    finally:
        kernel32.CloseHandle(handle)


def process_start_token(pid: object) -> str | None:
    """Something that tells this process apart from a later one given the same PID.

    It comes from the process's start time: ``/proc`` on Linux, ``GetProcessTimes``
    on Windows and ``sysctl`` on macOS. None when the process is gone or the time
    can't be read, including on any other system.
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if sys.platform == "win32":
        return windows_process_state(pid)[1]
    if sys.platform == "darwin":
        return _mac_start_token(pid)
    if sys.platform.startswith("linux"):
        return _linux_start_token(pid)
    return None


def _linux_start_token(pid: int) -> str | None:
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Field 22, the start time in clock ticks since boot, comes after the
    # parenthesized command name, which may itself contain spaces or brackets.
    fields = stat[stat.rfind(")") + 2 :].split()
    if len(fields) < 20 or not boot_id:
        return None
    return f"{boot_id}:{fields[19]}"


# sysctl names for one process's kinfo_proc: CTL_KERN, KERN_PROC, KERN_PROC_PID.
_CTL_KERN = 1
_KERN_PROC = 14
_KERN_PROC_PID = 1
# kinfo_proc is 648 bytes on 64-bit macOS; room to spare if it grows.
_KINFO_PROC_BUFFER = 1024
# kinfo_proc starts with kp_proc.p_starttime, a timeval: 64-bit seconds, 32-bit microseconds.
_TIMEVAL = struct.Struct("=qi")


@functools.cache
def _libc() -> Any:
    import ctypes
    import ctypes.util

    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.dylib", use_errno=True)
    libc.sysctl.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    libc.sysctl.restype = ctypes.c_int
    return libc


def _mac_start_token(pid: int) -> str | None:
    if pid > 0x7FFFFFFF:
        return None
    import ctypes

    try:
        libc = _libc()
    except (OSError, AttributeError):
        return None
    mib = (ctypes.c_int * 4)(_CTL_KERN, _KERN_PROC, _KERN_PROC_PID, pid)
    buffer = ctypes.create_string_buffer(_KINFO_PROC_BUFFER)
    size = ctypes.c_size_t(len(buffer))
    if libc.sysctl(mib, 4, buffer, ctypes.byref(size), None, 0) != 0:
        return None
    # No such process returns success with nothing written.
    if size.value < _TIMEVAL.size:
        return None
    seconds, microseconds = _TIMEVAL.unpack_from(buffer.raw, 0)
    if seconds <= 0:
        return None
    return f"mac:{seconds}.{microseconds:06d}"
