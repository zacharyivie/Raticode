"""The desktop app log, read for a paired phone's Developer log.

The Raticode window writes its application log (Settings > Developer) as JSON lines to
``app.jsonl`` and renames full files to ``app.jsonl.1`` and older. The window passes the
file's path to the backend in ``GOFER_APP_LOG``; a backend started some other way has no
app log, and the phone says so.

A phone first reads the newest entries, then follows the file with a cursor: the file's
identity (device and inode, which a rename keeps) and the byte offset after the last whole
line it was sent. Each entry's ID is that identity and the entry's own offset, so it stays
the same across reads. Entries are redacted again here, since older versions wrote some
without redaction, and long messages are shortened for the phone.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

LEVELS = ("debug", "info", "warn", "error")
_ALIASES = {
    "warning": "warn",
    "critical": "error",
    "fatal": "error",
    "trace": "debug",
    "verbose": "debug",
    "log": "info",
}
# What a first read shows, and how far a follow-up read catches up before it skips ahead.
TAIL_BYTES = 384 * 1024
STEP_BYTES = 512 * 1024
MAX_ENTRIES = 400
# Each message as the phone receives it; the log file keeps the whole text.
MAX_MESSAGE = 8000
# Entries in one answer, measured as escaped JSON, so the result stays well inside the
# 48 parts of 24,000 characters a desktop result may use.
BUDGET = 600_000
UNAVAILABLE = (
    "Raticode keeps this log only while it runs as the desktop app. Start Raticode from "
    "its app on your computer to see the log here."
)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_BEARER = re.compile(r"(bearer\s+)[A-Za-z0-9._~+/-]+=*", re.IGNORECASE)
_BASIC = re.compile(
    r"(authorization[\"']?\s*[:=]\s*[\"']?basic\s+)[A-Za-z0-9._~+/-]+=*", re.IGNORECASE
)
_SECRET = re.compile(
    r"((?:api[_-]?key|token|password|secret|authorization)[\"']?\s*[:=]\s*)"
    r"(?:\"(?:\\.|[^\"\\])*(?:\"|\\?\Z)|'(?:\\.|[^'\\])*(?:'|\\?\Z)|[^\s,\"'}]+)",
    re.IGNORECASE,
)
_USERINFO = re.compile(r"(https?://)[^\s/?#\"'<>]*@", re.IGNORECASE)
_PAIRING = re.compile(r"(raticode://pair\?)[^\s\"'<>]*", re.IGNORECASE)
# Bare tokens with a well-known prefix, wherever they appear: Raticode agent tokens,
# GitHub, OpenAI and Anthropic keys, Slack tokens and AWS access key IDs.
_PREFIXED = re.compile(
    r"\b(?:rtk_agent_|gh[opusr]_|github_pat_|sk-|xox[abprs]-|AKIA)[A-Za-z0-9_-]{12,}"
)


def redact(value: Any) -> str:
    """The same redaction the window applies when it writes the log (app-log.cjs)."""
    text = _ANSI.sub("", str(value))
    text = _BEARER.sub(r"\1[redacted]", text)
    text = _BASIC.sub(r"\1[redacted]", text)
    text = _SECRET.sub(r"\1[redacted]", text)
    text = _USERINFO.sub(r"\1[redacted]@", text)
    text = _PREFIXED.sub("[redacted]", text)
    return _PAIRING.sub(r"\1[redacted]", text)


def level_of(value: Any) -> str:
    level = str(value or "").lower()
    if level in LEVELS:
        return level
    return _ALIASES.get(level, "info")


def configured_path() -> str | None:
    return os.environ.get("GOFER_APP_LOG") or None


def desktop_label() -> str:
    """Which Raticode wrote the log, for the header of an Ask Rem attachment."""
    from gofer.utils.version import installed_version

    version = installed_version() or ""
    return " ".join(part for part in ("Raticode", version, "on", sys.platform) if part)


class _Cursor:
    def __init__(self, file: str, offset: int) -> None:
        self.file = file
        self.offset = offset


def _parse_cursor(value: Any) -> _Cursor | None:
    if not isinstance(value, dict):
        return None
    file = value.get("file")
    offset = value.get("offset")
    if not isinstance(file, str) or not 0 < len(file) <= 64:
        return None
    if type(offset) is not int or not 0 <= offset < 2**53:
        return None
    return _Cursor(file, offset)


def _levels(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set(LEVELS)
    chosen = {item for item in value if isinstance(item, str) and item in LEVELS}
    return chosen or set(LEVELS)


def _identity(stat: os.stat_result) -> str:
    return f"{stat.st_dev:x}-{stat.st_ino:x}"


def _stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except FileNotFoundError:
        return None


def _read(path: Path, start: int, end: int) -> bytes:
    if end <= start:
        return b""
    with path.open("rb") as handle:
        handle.seek(start)
        return handle.read(end - start)


def _entry(identity: str, offset: int, line: bytes) -> dict[str, Any] | None:
    try:
        record = json.loads(line)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("message"), str):
        return None
    message = redact(record["message"])
    entry: dict[str, Any] = {
        "id": f"{identity}:{offset}",
        "time": str(record.get("time") or "")[:40],
        "level": level_of(record.get("level")),
        "source": redact(record.get("source") or "desktop")[:64],
        "message": message[:MAX_MESSAGE],
    }
    component = record.get("component")
    if isinstance(component, str) and component:
        entry["component"] = redact(component)[:128]
    if len(message) > MAX_MESSAGE:
        entry["truncated"] = True
    return entry


def _lines(
    identity: str, data: bytes, base: int, *, partial_start: bool
) -> tuple[list[tuple[int, bytes]], int]:
    """Whole lines in ``data`` read from offset ``base``, and the offset after the last one.

    A read that starts mid-file drops its first, partial line. The writer appends whole
    lines, but a read can still end inside one it is writing, so the cursor stops before it.
    """
    lines: list[tuple[int, bytes]] = []
    position = 0
    if partial_start:
        first = data.find(b"\n")
        if first == -1:
            return [], base
        position = first + 1
    while True:
        end = data.find(b"\n", position)
        if end == -1:
            break
        if end > position:
            lines.append((base + position, data[position:end]))
        position = end + 1
    return lines, base + position


def _entries(
    identity: str, lines: list[tuple[int, bytes]], levels: set[str]
) -> list[dict[str, Any]]:
    found = []
    for offset, line in lines:
        entry = _entry(identity, offset, line)
        if entry is not None and entry["level"] in levels:
            found.append(entry)
    return found


def _size(entry: dict[str, Any]) -> int:
    return len(json.dumps(entry, ensure_ascii=True)) + 1


def _newest(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """The newest entries that fit the count and size limits, oldest first."""
    kept: list[dict[str, Any]] = []
    used = 0
    for entry in reversed(entries):
        size = _size(entry)
        if len(kept) >= MAX_ENTRIES or (kept and used + size > BUDGET):
            break
        kept.append(entry)
        used += size
    kept.reverse()
    return kept, len(kept) < len(entries)


def _tail(path: Path, levels: set[str]) -> dict[str, Any]:
    current = _stat(path)
    if current is None:
        return {"entries": [], "cursor": None, "reset": True, "skipped": False, "more": False}
    identity = _identity(current)
    start = max(0, current.st_size - TAIL_BYTES)
    lines, end = _lines(
        identity, _read(path, start, current.st_size), start, partial_start=start > 0
    )
    entries = _entries(identity, lines, levels)
    older = path.with_name(path.name + ".1")
    previous = _stat(older)
    if start == 0 and previous is not None:
        # The current file is short: the newest part of the previous one comes first.
        old_identity = _identity(previous)
        old_start = max(0, previous.st_size - (TAIL_BYTES - current.st_size))
        old_lines, _ = _lines(
            old_identity,
            _read(older, old_start, previous.st_size),
            old_start,
            partial_start=old_start > 0,
        )
        entries = _entries(old_identity, old_lines, levels) + entries
    kept, _ = _newest(entries)
    return {
        "entries": kept,
        "cursor": {"file": identity, "offset": end},
        "reset": True,
        "skipped": False,
        "more": False,
    }


def _forward(
    identity: str, path: Path, start: int, size: int, levels: set[str], room: int
) -> tuple[list[dict[str, Any]], int, bool]:
    """Entries from ``start``, at most ``room`` bytes of file and the answer's limits.

    Returns the entries, the offset after the last line sent and whether more is waiting.
    """
    end = min(size, start + room)
    lines, after = _lines(identity, _read(path, start, end), start, partial_start=False)
    kept: list[dict[str, Any]] = []
    used = 0
    cursor = start
    for offset, line in lines:
        entry = _entry(identity, offset, line)
        if entry is not None and entry["level"] in levels:
            entry_size = _size(entry)
            if len(kept) >= MAX_ENTRIES or (kept and used + entry_size > BUDGET):
                return kept, cursor, True
            kept.append(entry)
            used += entry_size
        cursor = offset + len(line) + 1
    return kept, after, after < size


def read(path_text: str | None, after: Any = None, levels: Any = None) -> dict[str, Any]:
    """The phone's next part of the log. ``after`` is the cursor the last answer gave it."""
    if not path_text:
        return {"available": False, "detail": UNAVAILABLE}
    path = Path(path_text)
    chosen = _levels(levels)
    cursor = _parse_cursor(after)
    result = _follow(path, cursor, chosen) if cursor else _tail(path, chosen)
    return {"available": True, "log_file": path.name, "desktop": desktop_label(), **result}


def _follow(path: Path, cursor: _Cursor, levels: set[str]) -> dict[str, Any]:
    current = _stat(path)
    if current is None:
        # Between a rename and the next write, or the log folder was removed.
        return {
            "entries": [],
            "cursor": {"file": cursor.file, "offset": cursor.offset},
            "reset": False,
            "skipped": False,
            "more": False,
        }
    identity = _identity(current)
    older = path.with_name(path.name + ".1")
    previous = _stat(older)
    if cursor.file == identity and cursor.offset <= current.st_size:
        if current.st_size - cursor.offset > STEP_BYTES:
            # Too far behind to catch up usefully: show the newest part instead.
            return {**_tail(path, levels), "reset": False, "skipped": True}
        entries, offset, more = _forward(
            identity, path, cursor.offset, current.st_size, levels, STEP_BYTES
        )
        return {
            "entries": entries,
            "cursor": {"file": identity, "offset": offset},
            "reset": False,
            "skipped": False,
            "more": more,
        }
    if (
        previous is not None
        and cursor.file == _identity(previous)
        and cursor.offset <= previous.st_size
    ):
        # The file the phone was following was renamed: finish it, then start the new one.
        remaining = previous.st_size - cursor.offset
        if remaining + current.st_size > STEP_BYTES * 2:
            return {**_tail(path, levels), "reset": False, "skipped": True}
        old_identity = _identity(previous)
        entries, offset, more = _forward(
            old_identity, older, cursor.offset, previous.st_size, levels, STEP_BYTES
        )
        if more:
            return {
                "entries": entries,
                "cursor": {"file": old_identity, "offset": offset},
                "reset": False,
                "skipped": False,
                "more": True,
            }
        newer, offset, more = _forward(identity, path, 0, current.st_size, levels, STEP_BYTES)
        return {
            "entries": (entries + newer)[-MAX_ENTRIES:],
            "cursor": {"file": identity, "offset": offset},
            "reset": False,
            "skipped": False,
            "more": more,
        }
    # The phone's file is gone (renamed twice, or replaced): entries in between are missing.
    return {**_tail(path, levels), "reset": False, "skipped": True}
