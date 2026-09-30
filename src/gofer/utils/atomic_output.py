"""Write an output without following destination links or predictable temporary names."""

from __future__ import annotations

import os
import secrets
import shutil
import stat
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO


@contextmanager
def _windows_directory_handles(
    directory: Path, *, exclusive: bool = False, create: bool = True
) -> Iterator[None]:
    """Hold ancestors without delete sharing so reparse/rename swaps fail on Windows."""
    import ctypes
    from ctypes import wintypes

    kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]

    class AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    with ExitStack() as held:
        current = Path(directory.anchor)
        for index, part in enumerate(("", *directory.parts[1:])):
            current /= part
            if create:
                current.mkdir(
                    exist_ok=not (exclusive and index == len(directory.parts) - 1), mode=0o700
                )
            handle = kernel.CreateFileW(
                str(current), 0x80, 0x1 | 0x2, None, 3, 0x02000000 | 0x00200000, None
            )
            if handle == ctypes.c_void_p(-1).value:
                raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
            held.callback(kernel.CloseHandle, handle)
            info = AttributeTag()
            if not kernel.GetFileInformationByHandleEx(
                handle, 9, ctypes.byref(info), ctypes.sizeof(info)
            ):
                raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
            if info.attributes & 0x400 or not info.attributes & 0x10:
                raise OSError("Output directory is a reparse point or is not a directory")
        yield


@contextmanager
def open_binary_input(source: Path) -> Iterator[BinaryIO]:
    """Open an ordinary file without following links in any path component."""
    source = source.expanduser().absolute()
    with ExitStack() as held:
        if os.open in os.supports_dir_fd:
            parent = os.open(source.anchor, os.O_RDONLY | os.O_DIRECTORY)
            held.callback(os.close, parent)
            for part in source.parent.parts[1:]:
                parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                held.callback(os.close, parent)
            descriptor = os.open(
                source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
        else:
            if os.name == "nt":
                held.enter_context(_windows_directory_handles(source.parent, create=False))
            if source.is_symlink() or source.resolve(strict=True) != source:
                raise OSError("Input contains a symbolic link")
            descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        stream = held.enter_context(os.fdopen(descriptor, "rb"))
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OSError("Input is not an ordinary file")
        yield stream


@contextmanager
def scandir_without_links(directory: Path) -> Iterator[Iterator[os.DirEntry[str]]]:
    """List a pinned ordinary directory without traversing replaced parent links."""
    directory = directory.expanduser().absolute()
    with ExitStack() as held:
        if os.open in os.supports_dir_fd and os.scandir in os.supports_fd:
            descriptor = os.open(directory.anchor, os.O_RDONLY | os.O_DIRECTORY)
            held.callback(os.close, descriptor)
            for part in directory.parts[1:]:
                descriptor = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
                held.callback(os.close, descriptor)
            entries = held.enter_context(os.scandir(descriptor))
        else:
            if os.name == "nt":
                held.enter_context(_windows_directory_handles(directory, create=False))
            elif directory.resolve(strict=True) != directory:
                raise OSError("Input directory contains a symbolic link")
            entries = held.enter_context(os.scandir(directory))
        yield entries


def mkdir_without_links(directory: Path, *, exclusive: bool = False) -> None:
    """Create directories without following a replaced or dangling parent link."""
    directory = directory.expanduser().absolute()
    if os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd:
        descriptor = os.open(directory.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for index, part in enumerate(directory.parts[1:], 1):
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    if exclusive and index == len(directory.parts) - 1:
                        raise
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
                os.close(descriptor)
                descriptor = child
        finally:
            os.close(descriptor)
        return
    if os.name == "nt":
        with _windows_directory_handles(directory, exclusive=exclusive):
            return
    current = Path(directory.anchor)
    for index, part in enumerate(directory.parts[1:], 1):
        current /= part
        current.mkdir(exist_ok=not (exclusive and index == len(directory.parts) - 1), mode=0o700)
        if current.is_symlink() or current.resolve(strict=True) != current:
            raise OSError("Directory contains a symbolic link or reparse point")


def unlink_without_links(destination: Path, *, directory: bool = False) -> None:
    """Remove one entry without following parent links, including during rollback."""
    destination = destination.expanduser().absolute()
    with ExitStack() as held:
        if os.open in os.supports_dir_fd and os.unlink in os.supports_dir_fd:
            parent = os.open(destination.anchor, os.O_RDONLY | os.O_DIRECTORY)
            held.callback(os.close, parent)
            for part in destination.parent.parts[1:]:
                parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                held.callback(os.close, parent)
            if directory:
                os.rmdir(destination.name, dir_fd=parent)
            else:
                os.unlink(destination.name, dir_fd=parent)
        else:
            if os.name == "nt":
                held.enter_context(_windows_directory_handles(destination.parent, create=False))
            elif destination.parent.resolve(strict=True) != destination.parent:
                raise OSError("Refusing to remove a file through a symbolic link")
            if directory:
                destination.rmdir()
            else:
                destination.unlink()


def remove_tree_without_links(
    directory: Path, *, onerror: Callable[..., None] | None = None
) -> None:
    """Remove a tree while refusing replaced parents, including during rollback."""
    directory = directory.expanduser().absolute()
    if os.open in os.supports_dir_fd:
        descriptor = os.open(directory.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in directory.parent.parts[1:]:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
                os.close(descriptor)
                descriptor = child
            shutil.rmtree(directory.name, dir_fd=descriptor)
        finally:
            os.close(descriptor)
    elif os.name == "nt":
        with _windows_directory_handles(directory.parent):
            if directory.is_symlink():
                raise OSError("Refusing to remove a symbolic link")
            shutil.rmtree(directory, onerror=onerror)
    else:
        if directory.parent.resolve(strict=True) != directory.parent or directory.is_symlink():
            raise OSError("Refusing to remove a directory through a symbolic link")
        shutil.rmtree(directory)


@contextmanager
def atomic_binary_output(
    destination: Path,
    *,
    exclusive: bool = False,
    timestamps_ns: tuple[int, int] | None = None,
) -> Iterator[BinaryIO]:
    """Pin the parent directory before creating/replacing an output on POSIX.

    Callers authorize the canonical destination before using this helper. Opening
    each directory without following links also prevents a swapped parent from
    redirecting the write after authorization. Atomic replacement never edits an
    existing hardlink's inode. Exclusive publication fails if any destination
    already exists, including a dangling symlink or a concurrent creation.
    """
    destination = destination.expanduser().absolute()
    parent = destination.parent
    temporary = f".{destination.name}.{secrets.token_hex(16)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    parent_fd: int | None = None
    windows_handles = ExitStack()
    if os.open in os.supports_dir_fd and os.rename in os.supports_dir_fd:
        parent_fd = os.open(parent.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parent.parts[1:]:
                try:
                    next_fd = os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
                    )
                except FileNotFoundError:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=parent_fd)
                    except FileExistsError:
                        # Another writer may have created this parent. The open
                        # below still rejects files and links without following them.
                        pass
                    next_fd = os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
                    )
                os.close(parent_fd)
                parent_fd = next_fd
        except BaseException:
            os.close(parent_fd)
            raise
    else:
        if os.name == "nt":
            windows_handles.enter_context(_windows_directory_handles(parent))
        else:
            mkdir_without_links(parent)
    parent_identity = os.fstat(parent_fd) if parent_fd is not None else parent.stat()
    temporary_path = parent / temporary
    try:
        fd = (
            os.open(temporary, flags, 0o600, dir_fd=parent_fd)
            if parent_fd is not None
            else os.open(temporary_path, flags, 0o600)
        )
        with os.fdopen(fd, "wb") as output:
            yield output
            output.flush()
            if timestamps_ns is not None:
                if os.utime in os.supports_fd:
                    os.utime(output.fileno(), ns=timestamps_ns)
                else:
                    # The parent is pinned on Windows and the temporary file
                    # remains open without delete sharing until publication.
                    os.utime(temporary_path, ns=timestamps_ns, follow_symlinks=False)
            os.fsync(output.fileno())
        if parent_fd is not None:
            if exclusive:
                # linkat publishes atomically without replacing an existing name.
                os.link(
                    temporary,
                    destination.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            else:
                os.replace(temporary, destination.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        else:
            current = parent.stat()
            if (current.st_dev, current.st_ino) != (parent_identity.st_dev, parent_identity.st_ino):
                raise OSError("Output parent changed during write")
            if exclusive:
                if os.name == "nt":
                    # Windows rename has no-replace semantics, including on FAT.
                    os.rename(temporary_path, destination)
                else:
                    os.link(temporary_path, destination, follow_symlinks=False)
            else:
                os.replace(temporary_path, destination)
    finally:
        if parent_fd is not None:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            finally:
                os.close(parent_fd)
        else:
            try:
                temporary_path.unlink(missing_ok=True)
            finally:
                windows_handles.close()
