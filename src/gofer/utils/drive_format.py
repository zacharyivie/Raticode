"""Name the format of the drive a folder is on, and explain a failed write in those terms.

A raw ``[Errno 1] Operation not permitted`` tells nobody that their backup drive is
FAT32 and can't hold a 5 GB file, or that it is mounted read-only. These helpers turn
an ``OSError`` into a sentence that names the folder, the drive format when it can be
found, and what to do next.
"""

from __future__ import annotations

import errno
import os
import re
import subprocess
import sys
from pathlib import Path

# Kernel and macOS names for drive formats, as people know them.
_FORMAT_NAMES = {
    "vfat": "FAT",
    "msdos": "FAT",
    "fat": "FAT",
    "fat12": "FAT",
    "fat16": "FAT",
    "fat32": "FAT32",
    "exfat": "exFAT",
    "ntfs": "NTFS",
    "ntfs3": "NTFS",
    "fuseblk": "",  # ntfs-3g or exfat-fuse: the kernel doesn't say which.
    "apfs": "APFS",
    "hfs": "HFS+",
    "btrfs": "Btrfs",
    "ext2": "ext2",
    "ext3": "ext3",
    "ext4": "ext4",
    "xfs": "XFS",
    "zfs": "ZFS",
    "f2fs": "F2FS",
    "refs": "ReFS",
    "nfs": "NFS",
    "nfs4": "NFS",
    "cifs": "SMB",
    "smb3": "SMB",
    "smbfs": "SMB",
}
# Formats with no hard links, no Unix permissions and, for FAT, a 4 GB file limit.
FAT_FORMATS = frozenset({"FAT", "FAT32", "exFAT"})
_MAC_MOUNT = re.compile(r"^.+? on (?P<point>.+) \((?P<type>[^,)]+)")


def _unescape_mountinfo(field: str) -> str:
    """mountinfo writes spaces, tabs, newlines and backslashes as octal escapes."""
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), field)


def _best_mount(path: Path, mounts: list[tuple[str, str]]) -> str:
    """The type of the deepest mount point that holds ``path``, or ``""``."""
    best, best_type = -1, ""
    for point, kind in mounts:
        mount = Path(point)
        if (path == mount or path.is_relative_to(mount)) and len(mount.parts) > best:
            best, best_type = len(mount.parts), kind
    return best_type


def parse_mountinfo(text: str) -> list[tuple[str, str]]:
    """(mount point, type) pairs from Linux ``/proc/self/mountinfo``."""
    mounts = []
    for line in text.splitlines():
        left, separator, right = line.partition(" - ")
        fields = left.split()
        if not separator or len(fields) < 5 or not right.split():
            continue
        mounts.append((_unescape_mountinfo(fields[4]), right.split()[0]))
    return mounts


def parse_mac_mount(text: str) -> list[tuple[str, str]]:
    """(mount point, type) pairs from macOS ``/sbin/mount`` output."""
    mounts = []
    for line in text.splitlines():
        match = _MAC_MOUNT.match(line)
        if match:
            mounts.append((match["point"], match["type"].strip()))
    return mounts


def _windows_format(path: Path) -> str:
    import ctypes

    kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    root = ctypes.create_unicode_buffer(1024)
    if not kernel.GetVolumePathNameW(str(path), root, len(root)):
        return ""
    name = ctypes.create_unicode_buffer(64)
    if not kernel.GetVolumeInformationW(root, None, 0, None, None, None, name, len(name)):
        return ""
    return name.value


def _raw_format(path: Path) -> str:
    if sys.platform.startswith("linux"):
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as source:
            return _best_mount(path, parse_mountinfo(source.read()))
    if sys.platform == "darwin":
        listed = subprocess.run(
            ["/sbin/mount"], capture_output=True, text=True, timeout=5, check=False
        )
        return _best_mount(path, parse_mac_mount(listed.stdout))
    if os.name == "nt":
        return _windows_format(path)
    return ""


def drive_format(folder: Path) -> str:
    """The format of the drive holding ``folder``, such as ``exFAT``, or ``""`` if unknown.

    Linux reads ``/proc/self/mountinfo``, macOS lists ``/sbin/mount`` and Windows asks
    the volume.

    A folder that doesn't exist yet is looked up through its nearest existing parent.
    """
    path = Path(os.path.abspath(folder.expanduser()))
    while not path.exists() and path.parent != path:
        path = path.parent
    try:
        raw = _raw_format(path.resolve())
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    # Only formats people would recognize; tmpfs, overlay and the like say nothing useful.
    return _FORMAT_NAMES.get(raw.lower(), "")


def _hint(code: int | None, format_name: str) -> str:
    fat = format_name in FAT_FORMATS
    if code == errno.EFBIG:
        if format_name in {"FAT", "FAT32"}:
            return " FAT32 drives can't hold a file over 4 GB. Choose a folder on another drive."
        return " The file is too large for this drive."
    if code == errno.ENOSPC or code == getattr(errno, "EDQUOT", None):
        return " The drive is full. Free some space or choose another folder."
    if code == errno.EROFS:
        return (
            " The drive is mounted read-only. Remount it with write access"
            " or choose another folder."
        )
    if code in {errno.EACCES, errno.EPERM}:
        if fat:
            return (
                f" {format_name} drives have no per-file permissions, so the drive is"
                " probably mounted for another user. Remount it for your account or"
                " choose another folder."
            )
        return " Check that your account can write to this folder, or choose another one."
    if code == errno.EINVAL and fat:
        return f" {format_name} drives don't allow some characters in file names."
    if code == errno.EEXIST:
        return " A file with that name is already there."
    if code in {errno.ENOENT, errno.ENOTDIR}:
        return " The folder isn't there. Plug the drive in or choose another folder."
    return ""


def write_error_message(exc: OSError, folder: Path) -> str:
    """Explain why writing to ``folder`` failed, naming the drive format when known."""
    format_name = drive_format(folder)
    reason = (exc.strerror or str(exc) or type(exc).__name__).rstrip(".")
    place = f"{folder} on this {format_name} drive" if format_name else str(folder)
    return f"Couldn't write to {place}: {reason.lower()}.{_hint(exc.errno, format_name)}"
