from __future__ import annotations

import errno
from pathlib import Path

import pytest

from gofer.utils import drive_format as module
from gofer.utils.drive_format import parse_mac_mount, parse_mountinfo, write_error_message

MOUNTINFO = """\
22 1 0:21 / / rw,relatime shared:1 - btrfs /dev/nvme0n1p2 rw,ssd
30 22 0:26 / /tmp rw,nosuid shared:5 - tmpfs tmpfs rw
41 22 8:17 / /run/media/someone/BACKUP\\040DRIVE rw,nosuid shared:40 - exfat /dev/sdb1 rw,uid=1000
42 41 8:33 / /run/media/someone/BACKUP\\040DRIVE/inner rw shared:41 - vfat /dev/sdc1 rw
"""

MAC_MOUNT = """\
/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)
/dev/disk4s1 on /Volumes/Back Up (exfat, local, nodev, nosuid, noowners)
/dev/disk5s1 on /Volumes/OLD (msdos, local, nodev, nosuid, noowners)
"""


def test_linux_mount_table_finds_the_deepest_mount_and_unescapes_spaces():
    mounts = parse_mountinfo(MOUNTINFO)
    assert ("/run/media/someone/BACKUP DRIVE", "exfat") in mounts
    root = Path("/run/media/someone/BACKUP DRIVE")
    assert module._best_mount(root / "Raticode", mounts) == "exfat"
    assert module._best_mount(root / "inner" / "brains", mounts) == "vfat"
    assert module._best_mount(Path("/run/media/someone/BACKUP"), mounts) == "btrfs"
    assert module._best_mount(Path("/home/someone"), mounts) == "btrfs"


@pytest.mark.parametrize(
    ("raw", "named"),
    [
        ("exfat", "exFAT"),
        ("vfat", "FAT"),
        ("msdos", "FAT"),
        ("FAT32", "FAT32"),
        ("ntfs3", "NTFS"),
        # Names nobody would recognize as a drive, or that hide the real format, aren't used.
        ("tmpfs", ""),
        ("fuseblk", ""),
        ("", ""),
    ],
)
def test_drive_formats_are_named_as_people_know_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str, named: str
) -> None:
    seen: list[Path] = []

    def raw_format(path: Path) -> str:
        seen.append(path)
        return raw

    monkeypatch.setattr(module, "_raw_format", raw_format)
    assert module.drive_format(tmp_path / "not" / "there" / "yet") == named
    # A folder not made yet is looked up through the nearest folder that exists.
    assert seen == [tmp_path.resolve()]


def test_mac_mount_list_names_exfat_and_fat_volumes():
    mounts = parse_mac_mount(MAC_MOUNT)
    assert module._best_mount(Path("/Volumes/Back Up/Raticode"), mounts) == "exfat"
    assert module._best_mount(Path("/Volumes/OLD"), mounts) == "msdos"
    assert module._best_mount(Path("/Users/someone"), mounts) == "apfs"


def test_this_computers_drive_format_never_raises(tmp_path):
    assert isinstance(module.drive_format(tmp_path / "not" / "there"), str)


@pytest.mark.parametrize(
    ("code", "format_name", "expected"),
    [
        (errno.EFBIG, "FAT32", "FAT32 drives can't hold a file over 4 GB"),
        (errno.ENOSPC, "exFAT", "The drive is full"),
        (errno.EROFS, "NTFS", "mounted read-only"),
        (errno.EACCES, "exFAT", "exFAT drives have no per-file permissions"),
        (errno.EACCES, "ext4", "Check that your account can write"),
        (errno.ENOENT, "", "Plug the drive in"),
    ],
)
def test_write_errors_name_the_drive_format_and_what_to_do(
    monkeypatch, code, format_name, expected
):
    monkeypatch.setattr(module, "drive_format", lambda _: format_name)
    exc = OSError(code, __import__("os").strerror(code), "/media/usb/backup.zip")
    message = write_error_message(exc, Path("/media/usb"))
    assert "Errno" not in message and "backup.zip" not in message
    assert expected in message
    if format_name:
        assert f"/media/usb on this {format_name} drive" in message
    else:
        assert message.startswith("Couldn't write to /media/usb: ")
