"""Browse the files of open projects from a paired phone, read-only.

The phone sees the folders of the desktop projects its catalog names (open,
shared projects), the same scope as source control. It names a project by its
catalog ID and a file by its path relative to that project, written with ``/``.
It never sends or receives a full folder path.

Every lookup walks from the project folder one name at a time without following
symbolic links, as the scoped file snapshot does, so a link can't lead outside
the project. Links are listed but never opened. Files that may hold credentials
(``rem_home.sensitive_path``) are listed, marked, and never read or sent.

``files.text`` sends the start of a text file as text, so the phone can show
code, notes and logs of any size without downloading them. ``files.open`` offers
the whole file (at most 10 MiB) on the phone's Rem chat file channel, through
the normal end-to-end file transfer, for pictures, videos, PDFs and other files.
Nothing here writes, renames or deletes anything.
"""

from __future__ import annotations

import codecs
import json
import os
import stat
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gofer.devices.desktop_control import _HIDDEN, Refused
from gofer.devices.files import MAX_FILE

READS = frozenset({"files.list", "files.text"})
ACTIONS = frozenset({"files.open"})
ENTRY_LIMIT = 1000
PATH_LIMIT = 1024
NAME_LIMIT = 255
PROJECT_LIMIT = 100
# The start of a text file sent as text. Larger files are cut at a line end.
TEXT_LIMIT = 256 * 1024
# Escaped JSON a text answer may use; the desktop result holds about 1.1 million characters.
TEXT_BUDGET = 900_000
# Bytes checked for NUL and other signs of a binary file.
SNIFF = 8000
NOT_OPEN = "This project is no longer open on your computer."
MISSING = "This folder is no longer on your computer."
NO_FILE = "This file is no longer on your computer."
NOT_FOLDER = "This is not a folder."
NOT_FILE = "This is not a regular file, so it can't be opened from the phone."
LINKED = "This is a link to somewhere else, so the phone doesn't follow it."
SENSITIVE = "This file may hold passwords, keys or sign-ins, so it stays on your computer."
TOO_LARGE = "This file is larger than the 10 MB a phone can download. Open it on your computer."
BINARY = "This file isn't text."
UNREADABLE = "Your computer couldn't read this."
UNSUPPORTED = "Browsing files from the phone isn't supported on this computer's system."


def _supported() -> bool:
    return (
        hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and os.open in os.supports_dir_fd
        and os.scandir in os.supports_fd
    )


def parts(value: Any) -> list[str]:
    """A project-relative path as names, or a refusal. ``""`` is the project folder."""
    if value is None or value == "":
        return []
    if not isinstance(value, str) or len(value) > PATH_LIMIT or "\0" in value or "\\" in value:
        raise Refused("invalid", "This path can't be opened from the phone.")
    names = value.split("/")
    for name in names:
        if (
            not name
            or name in {".", ".."}
            or len(name.encode("utf-8", "surrogatepass")) > NAME_LIMIT
            or any(unicodedata.category(c) == "Cc" for c in name)
        ):
            raise Refused("invalid", "This path can't be opened from the phone.")
    return names


def _project(projects: dict[str, dict[str, str]], params: dict[str, Any]) -> tuple[str, str]:
    identifier = str(params.get("project_id") or "")[:128]
    project = projects.get(identifier)
    if project is None:
        raise Refused("not_found", NOT_OPEN)
    return identifier, project["root"]


@contextmanager
def _folder(root: str, names: list[str], missing: str = MISSING) -> Iterator[int]:
    """An open descriptor of the folder ``names`` below ``root``, reached without links."""
    if not _supported():
        raise Refused("unavailable", UNSUPPORTED)
    try:
        resolved = Path(root).resolve(strict=True)
        descriptor = os.open(resolved, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise Refused("not_found", "This project's folder is missing on your computer.") from None
    try:
        for name in names:
            try:
                child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            except OSError:
                raise Refused("not_found", missing) from None
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def when(seconds: float) -> str:
    try:
        return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return ""


def _shown(name: str) -> bool:
    return (
        bool(name)
        and not any(unicodedata.category(c) == "Cc" or c in _HIDDEN for c in name)
        and len(name) <= NAME_LIMIT
    )


def _sensitive(path: Path, root: str, data_dir: Path | None) -> bool:
    from gofer.ui.rem_home import sensitive_path

    return sensitive_path(path, data_dir, opened=Path(root).resolve())


def listing(root: str, names: list[str], data_dir: Path | None = None) -> dict[str, Any]:
    """One folder: folders first, then files, by name, at most ``ENTRY_LIMIT``."""
    entries: list[dict[str, Any]] = []
    # Names with control or text-direction characters could pass for other names on the
    # phone, so they are left out and only counted.
    hidden = 0
    base = Path(root).resolve().joinpath(*names)
    with _folder(root, names) as descriptor:
        try:
            scanned = list(os.scandir(descriptor))
        except OSError:
            raise Refused("unavailable", UNREADABLE) from None
        for item in scanned:
            if not _shown(item.name):
                hidden += 1
                continue
            try:
                info = item.stat(follow_symlinks=False)
            except OSError:
                continue
            mode = info.st_mode
            kind = (
                "folder"
                if stat.S_ISDIR(mode)
                else "file"
                if stat.S_ISREG(mode)
                else "link"
                if stat.S_ISLNK(mode)
                else "other"
            )
            entry: dict[str, Any] = {
                "name": item.name,
                "type": kind,
                "modified": when(info.st_mtime),
            }
            if kind == "file":
                entry["size"] = info.st_size
                if _sensitive(base / item.name, root, data_dir):
                    entry["locked"] = True
            entries.append(entry)
    entries.sort(key=lambda e: (e["type"] != "folder", e["name"].casefold(), e["name"]))
    return {
        "path": "/".join(names),
        "entries": entries[:ENTRY_LIMIT],
        "more": max(0, len(entries) - ENTRY_LIMIT),
        "hidden": hidden,
    }


@contextmanager
def _file(
    root: str, names: list[str], data_dir: Path | None
) -> Iterator[tuple[int, os.stat_result]]:
    """An open descriptor of a regular file, checked for links and secret-looking names."""
    if not names:
        raise Refused("invalid", NOT_FILE)
    if _sensitive(Path(root).resolve().joinpath(*names), root, data_dir):
        raise Refused("unauthorized", SENSITIVE)
    with _folder(root, names[:-1], NO_FILE) as folder:
        try:
            info = os.stat(names[-1], dir_fd=folder, follow_symlinks=False)
        except OSError:
            raise Refused("not_found", NO_FILE) from None
        if stat.S_ISLNK(info.st_mode):
            raise Refused("unauthorized", LINKED)
        if not stat.S_ISREG(info.st_mode):
            raise Refused("invalid", NOT_FILE)
        try:
            # A FIFO swapped in must not block before fstat rejects it.
            source = os.open(names[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
        except OSError:
            raise Refused("not_found", NO_FILE) from None
        try:
            info = os.fstat(source)
            if not stat.S_ISREG(info.st_mode):
                raise Refused("invalid", NOT_FILE)
            yield source, info
        finally:
            os.close(source)


def _decode(data: bytes, complete: bool) -> str | None:
    """Text from the start of a file, or None when it looks binary."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            text = data.decode("utf-16")
        except UnicodeDecodeError:
            # A cut in the middle of a character; drop the last unit.
            try:
                text = data[: len(data) - (len(data) % 2) - 2].decode("utf-16")
            except UnicodeDecodeError:
                return None
        return text
    if b"\0" in data[:SNIFF]:
        return None
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        text = decoder.decode(data, final=complete)
    except UnicodeDecodeError as exc:
        # Mostly UTF-8 with a few stray bytes still reads as text, as editors show it.
        if exc.start < min(len(data), SNIFF) // 2:
            return None
        text = data.decode("utf-8", "replace")
    text = text.removeprefix("﻿")
    sample = text[:SNIFF]
    controls = sum(
        1 for c in sample if unicodedata.category(c) == "Cc" and c not in "\n\r\t\f\v\x1b"
    )
    if sample and controls / len(sample) > 0.05:
        return None
    return text


def text(root: str, names: list[str], data_dir: Path | None = None) -> dict[str, Any]:
    """The start of a text file, cut at a line end when it is longer than ``TEXT_LIMIT``."""
    with _file(root, names, data_dir) as (source, info):
        chunks = []
        remaining = TEXT_LIMIT + 1
        while remaining > 0:
            try:
                chunk = os.read(source, min(remaining, 65536))
            except OSError:
                raise Refused("unavailable", UNREADABLE) from None
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    data = b"".join(chunks)
    truncated = len(data) > TEXT_LIMIT
    data = data[:TEXT_LIMIT]
    value = _decode(data, not truncated)
    result: dict[str, Any] = {
        "path": "/".join(names),
        "name": names[-1],
        "size": info.st_size,
        "modified": when(info.st_mtime),
    }
    if value is None:
        return {**result, "binary": True, "text": "", "truncated": False}
    if truncated and "\n" in value:
        value = value[: value.rindex("\n") + 1]
    while len(json.dumps(value, ensure_ascii=True)) > TEXT_BUDGET:
        value = value[: len(value) // 2]
        truncated = True
    return {**result, "binary": False, "text": value, "truncated": truncated}


def gather(
    op: str,
    params: dict[str, Any],
    projects: dict[str, dict[str, str]],
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Read a folder or file for a phone view. Runs outside the device lock."""
    if op == "files.list" and not params.get("project_id"):
        return {
            "projects": [
                {"project_id": key, "root": value["root"]}
                for key, value in list(projects.items())[:PROJECT_LIMIT]
            ],
            "more": max(0, len(projects) - PROJECT_LIMIT),
        }
    identifier, root = _project(projects, params)
    names = parts(params.get("path"))
    value = listing(root, names, data_dir) if op == "files.list" else text(root, names, data_dir)
    return {"project_id": identifier, "root": root, "value": value}


def read(op: str, data: dict[str, Any], projects: dict[str, dict[str, str]]) -> dict[str, Any]:
    """The phone's answer, keeping only projects still open under the same folder."""

    def still_open(entry: dict[str, Any]) -> bool:
        project = projects.get(entry["project_id"])
        return project is not None and project["root"] == entry["root"]

    def name(entry: dict[str, Any]) -> str:
        return projects[entry["project_id"]]["name"] or Path(entry["root"]).name[:160]

    if "projects" in data:
        return {
            "projects": [
                {"project_id": entry["project_id"], "name": name(entry)}
                for entry in data["projects"]
                if still_open(entry)
            ],
            "more": data["more"],
        }
    if not still_open(data):
        raise Refused("not_found", NOT_OPEN)
    return {"project_id": data["project_id"], "project": name(data), **data["value"]}


def offer_target(
    projects: dict[str, dict[str, str]], params: dict[str, Any], data_dir: Path | None = None
) -> tuple[str, Path, Path, os.stat_result]:
    """The project folder and file a ``files.open`` names, checked before the snapshot."""
    identifier, root = _project(projects, params)
    names = parts(params.get("path"))
    with _file(root, names, data_dir) as (_, info):
        if info.st_size > MAX_FILE:
            raise Refused("unavailable", TOO_LARGE)
    resolved = Path(root).resolve()
    return identifier, resolved, resolved.joinpath(*names), info
