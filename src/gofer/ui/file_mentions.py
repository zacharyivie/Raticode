"""Files named in Rem's chat text, found without asking a model.

The desktop chat shows each mention as a link that opens the file. Paths may contain
spaces, so text alone can't say where one ends ("/home/me/My Videos/clip.mp4 is
ready"). The filesystem decides: from each place a path could start, the longest
stretch of the line that names an existing file wins. Nothing is read or sent; the
desktop only learns which text to underline.

Markdown links to files (``[name](</full/path>)``) also show as attachments under the
message they are in (``linked_files``), on the desktop and the phone. A linked file is
a reference to the file where it is: nothing is copied, and opening one on a phone goes
through the same file checks as tapping its link.
"""

from __future__ import annotations

import mimetypes
import os
import re
import stat
import time
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

# A path starts at "/" or "~/" after a space, an opening bracket or quote, or Markdown
# emphasis. A "/" after a letter, ":" or "/" belongs to a word or a web address.
_START = re.compile(r"(?<![^\s(\[{<\"'`*_])(~?/)(?=[^\s/])")
# A mention may end before these when a space, the line's end or another of them follows.
_TRAILING = set(".,;:!?)]}>\"'`*_")
# Characters that never belong to a path in chat text.
_STOP = set('\n`<>|"')
_LONGEST = 1024
_CANDIDATES = 48
_CHECKS = 400
# Existence is re-checked at most this often for the same text.
_FRESH_SECONDS = 30
_LINE_SUFFIX = re.compile(r"(\.[^:/?#]+):[1-9]\d*(?::[1-9]\d*)?$")
_SCHEME = re.compile(r"^[a-z][a-z\d+.-]*:", re.IGNORECASE)
_WINDOWS_DRIVE = re.compile(r"^[a-z]:[\\/]", re.IGNORECASE)
# A Markdown link or image: its text, then its destination, bare or in <...>, and an
# optional title. The destination is kept exactly as written.
_MARKDOWN_LINK = re.compile(
    r"\[(?:\\.|[^\]\\\n])*\]\(\s*(<[^<>\n]+>|[^\s()<>]+)(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)"
)
# At most this many linked files show under one message: the phone's limit for a
# message's files (desktop_control.REM_FILES).
LINKED_FILES_MAX = 8


def file_mentions(text: str) -> list[dict[str, str]]:
    """Existing files named by full path in ``text``, as ``{text, path}``, in order.

    ``text`` is exactly what the message shows; ``path`` expands a leading ``~``.
    """
    return [dict(item) for item in _mentions(text, int(time.monotonic() // _FRESH_SECONDS))]


@lru_cache(maxsize=4096)
def _mentions(text: str, _period: int) -> tuple[tuple[tuple[str, str], ...], ...]:
    found: list[tuple[tuple[str, str], ...]] = []
    seen: set[str] = set()
    checks = 0
    position = 0
    while checks < _CHECKS:
        start = _START.search(text, position)
        if start is None:
            break
        begin = start.start()
        line = _line(text, begin)
        hit = None
        for end in _ends(line)[:_CANDIDATES]:
            checks += 1
            if _is_file(line[:end]):
                hit = line[:end]
                break
            if checks >= _CHECKS:
                break
        if hit is None:
            position = begin + 1
            continue
        if hit not in seen:
            seen.add(hit)
            found.append((("text", hit), ("path", os.path.expanduser(hit))))
        position = begin + len(hit)
    return tuple(found)


def _line(text: str, begin: int) -> str:
    """The rest of the line from ``begin``, cut at characters no path contains."""
    end = begin
    limit = min(len(text), begin + _LONGEST)
    while end < limit and text[end] not in _STOP:
        end += 1
    return text[begin:end]


def _ends(line: str) -> list[int]:
    """Places a mention could end, longest first."""
    ends = []
    for index in range(len(line), 0, -1):
        following = line[index : index + 1]
        after = line[index + 1 : index + 2]
        boundary = (
            not following
            or following.isspace()
            or (following in _TRAILING and (not after or after.isspace() or after in _TRAILING))
            # "/repo/app.py:12" names app.py at line 12.
            or (following == ":" and after.isdigit())
        )
        last = line[index - 1]
        if boundary and not last.isspace() and last != "/":
            ends.append(index)
    return ends


# A local path in an alert starts at "/", "~/" or a drive letter ("C:\", "C:/"), never
# inside a word or a web address.
_PATH_START = re.compile(r"(?<![\w.:/\\])(?:~/|/|[A-Za-z]:[\\/])")
# Closing brackets and quotes end a path in an alert, as does any whitespace but a space.
_PATH_STOP = set(")]>\"'`")
_SEPARATORS = "/\\"
# A path may end before these when a space, the text's end or another of them follows.
_PATH_TRAILING = set(".,;:!?")


def path_names(text: str, roots: tuple[str, ...] = ()) -> str:
    """``text`` with each local path cut to its last name, for a phone alert.

    Paths may contain spaces ("/Users/ana/Library/Application Support/gofer/x.json"),
    so where one ends is found as ``file_mentions`` finds it: for a path under one of
    ``roots`` (the home folder, the data folder, a project), the longest stretch that
    names an existing file or folder wins. A missing file in an existing folder, a path
    under no root and a Windows path on another system end at the first following word
    with no separator in it. What follows the last separator stays, with the rest of
    the text; a path with no folder after its start ("/tmp", "~/notes.md") stays whole.
    """
    period = int(time.monotonic() // _FRESH_SECONDS)
    return _path_names(text, _normal_roots(roots), period)


@lru_cache(maxsize=64)
def _normal_roots(roots: tuple[str, ...]) -> tuple[str, ...]:
    found = []
    for root in roots:
        path = os.path.expanduser(root) if root else ""
        if path and os.path.isabs(path):
            found.append(os.path.normcase(os.path.normpath(path)))
    return tuple(dict.fromkeys(found))


@lru_cache(maxsize=4096)
def _path_names(text: str, roots: tuple[str, ...], _period: int) -> str:
    kept: list[str] = []
    position = checks = 0
    while (start := _PATH_START.search(text, position)) is not None:
        begin = start.start()
        span = _path_span(text, begin)
        end = None
        if " " in span and roots and checks < _CHECKS:
            end, used = _existing_end(span, len(start[0]), roots, _CHECKS - checks)
            checks += used
        if end is None or end < 0:
            # Nothing on disk, or an existing folder the path goes on inside.
            end = _word_chain_end(span, -end if end else 0)
        cut = max(
            (
                i
                for i in range(len(start[0]), end - 1)
                if span[i] in _SEPARATORS and span[i + 1] not in _SEPARATORS
            ),
            default=None,
        )
        if cut is None:
            kept.append(text[position : begin + 1])
            position = begin + 1
            continue
        kept.append(text[position:begin])
        position = begin + cut + 1
    kept.append(text[position:])
    return "".join(kept)


def _path_span(text: str, begin: int) -> str:
    """The text from ``begin`` up to a character no path in an alert contains."""
    end = begin
    limit = min(len(text), begin + _LONGEST)
    while end < limit:
        character = text[end]
        if character in _PATH_STOP or (character.isspace() and character != " "):
            break
        end += 1
    return text[begin:end]


def _existing_end(
    span: str, start: int, roots: tuple[str, ...], budget: int
) -> tuple[int | None, int]:
    """Where the longest existing path at the start of ``span`` ends, and the checks used.

    The end is negative for a folder followed by a separator: the path goes on inside it.
    """
    used = 0
    for end in range(len(span), start, -1):
        following = span[end : end + 1]
        after = span[end + 1 : end + 2]
        boundary = (
            not following
            or following == " "
            or following in _SEPARATORS
            or (
                following in _PATH_TRAILING
                and (not after or after == " " or after in _PATH_TRAILING)
            )
        )
        if not boundary or span[end - 1] == " " or span[end - 1] in _SEPARATORS:
            continue
        path = os.path.normpath(os.path.expanduser(span[:end]))
        if not os.path.isabs(path) or not _under(os.path.normcase(path), roots):
            continue
        if used >= min(budget, _CANDIDATES):
            break
        used += 1
        try:
            if not os.path.exists(path):
                continue
            inside = following in _SEPARATORS and os.path.isdir(path)
        except (OSError, ValueError):
            continue
        return (-end if inside else end), used
    return None, used


def _under(path: str, roots: tuple[str, ...]) -> bool:
    return any(
        path == root or path.startswith(root if root.endswith(os.sep) else root + os.sep)
        for root in roots
    )


def _word_chain_end(span: str, begin: int) -> int:
    """Where a path that reaches ``begin`` ends when the disk can't say.

    It takes the rest of its word, then each following word that has a separator in it
    and doesn't start a path of its own.
    """
    end = span.find(" ", begin)
    while end != -1:
        word_end = span.find(" ", end + 1)
        word = span[end + 1 : len(span) if word_end == -1 else word_end]
        if not any(s in word for s in _SEPARATORS) or _PATH_START.match(span, end + 1):
            return end
        end = word_end
    return len(span)


def _is_file(candidate: str) -> bool:
    path = os.path.expanduser(candidate)
    if not os.path.isabs(path):
        return False
    try:
        return os.path.isfile(path)
    except (OSError, ValueError):
        return False


def with_file_mentions(
    messages: list[dict[str, Any]], data_dir: Path | None = None
) -> list[dict[str, Any]]:
    """Rem's messages with the file paths their text names, for the desktop chat.

    The user's own messages show as typed, so only Rem's are read. A reply's
    collapsed details are read too. Files Rem's text links to with Markdown links
    are added as ``linked_files``, which the chat shows as attachments.
    """
    result = []
    for message in messages:
        text = _message_text(message)
        if message.get("role") == "user" or not text:
            result.append(message)
            continue
        extra: dict[str, Any] = {}
        mentions = file_mentions(text)
        if mentions:
            extra["file_mentions"] = mentions
        linked = message_linked_files(message, data_dir)
        if linked:
            extra["linked_files"] = linked
        result.append({**message, **extra} if extra else message)
    return result


def with_thread_linked_files(
    messages: list[dict[str, Any]], data_dir: Path | None = None
) -> list[dict[str, Any]]:
    """A worker thread's messages with the files each reply links to (``linked_files``).

    Only the worker's replies count: instructions are the user's or Rem's own text,
    and thoughts show folded in a group, not as a message with attachments.
    """
    result = []
    for message in messages:
        linked = (
            message_linked_files(message, data_dir)
            if message.get("role") == "assistant" and message.get("kind") != "thought"
            else []
        )
        result.append({**message, "linked_files": linked} if linked else message)
    return result


def message_linked_files(
    message: dict[str, Any], data_dir: Path | None = None
) -> list[dict[str, Any]]:
    """Files one message links to, leaving out the user's messages and files it already shows."""
    text = _message_text(message)
    if message.get("role") == "user" or not text:
        return []
    return linked_files(text, data_dir, skip=shown_paths(message))


def _message_text(message: dict[str, Any]) -> str:
    """A message's text and its collapsed details, as the chat shows them."""
    return "\n".join(
        value for value in (message.get("body"), message.get("details")) if isinstance(value, str)
    )


def link_paths(href: str) -> list[str]:
    """Paths a Markdown file link may name, read the way the desktop opens links.

    Web addresses and other schemes name no path. A trailing ``:line`` or
    ``:line:column`` is dropped. The raw text comes first, then its decoded form.
    """
    value = href.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    if value.lower().startswith("file:"):
        parsed = urlsplit(value)
        if parsed.netloc not in {"", "localhost"}:
            return []
        candidates = [unquote(parsed.path)]
    elif (_SCHEME.match(value) and not _WINDOWS_DRIVE.match(value)) or value.startswith(
        ("//", "#")
    ):
        return []
    else:
        value = re.split(r"[?#]", value, maxsplit=1)[0]
        candidates = [value, unquote(value)]
    paths = [_LINE_SUFFIX.sub(r"\1", c) for c in candidates]
    return [p for p in dict.fromkeys(paths) if p and "\x00" not in p]


def linked_files(
    text: str, data_dir: Path | None = None, *, skip: frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    """Files ``text`` links to with Markdown links, to show as attachments, in order.

    Only a link to a full path of an existing regular file counts: web addresses,
    relative paths, folders, symlinks and missing files don't. A file the sensitive-file
    rule protects (``rem_home.sensitive_path``: credential stores, secret-looking names,
    Raticode's own data) keeps its link but gets no attachment, as Rem wouldn't attach it.
    Paths in ``skip`` already show on the message (its files or saved-report cards).
    Each is ``{id, name, path, href, size, type}``; ``href`` is the destination as the
    message writes it, which is what a phone's ``files.link`` sends back.
    """
    period = int(time.monotonic() // _FRESH_SECONDS)
    found = _linked(text, str(data_dir) if data_dir else "", period)
    return [dict(item) for item in found if dict(item)["path"] not in skip][:LINKED_FILES_MAX]


@lru_cache(maxsize=2048)
def _linked(text: str, data_dir: str, _period: int) -> tuple[tuple[tuple[str, Any], ...], ...]:
    from gofer.ui.rem_home import sensitive_path

    found: list[tuple[tuple[str, Any], ...]] = []
    seen: set[str] = set()
    for match in _MARKDOWN_LINK.finditer(text):
        written = match[1]
        href = written[1:-1].strip() if written.startswith("<") else written
        for candidate in link_paths(href):
            if not os.path.isabs(candidate):
                continue
            path = os.path.normpath(candidate)
            try:
                info = os.lstat(path)
            except (OSError, ValueError):
                continue
            if path in seen or not stat.S_ISREG(info.st_mode):
                break
            seen.add(path)
            if sensitive_path(Path(path).resolve(), Path(data_dir) if data_dir else None):
                break
            name = os.path.basename(path)
            found.append(
                (
                    ("id", path),
                    ("name", name[:200]),
                    ("path", path),
                    ("href", href),
                    ("size", info.st_size),
                    ("type", mimetypes.guess_type(name)[0] or "application/octet-stream"),
                )
            )
            break
        if len(found) >= LINKED_FILES_MAX * 2:
            break
    return tuple(found)


def shown_paths(message: dict[str, Any]) -> frozenset[str]:
    """Paths a Rem message already shows as files or saved-report cards."""
    files = message.get("files")
    result = message.get("result")
    artifacts = result.get("artifacts") if isinstance(result, dict) else None
    return frozenset(
        str(item["path"])
        for item in [*(files if isinstance(files, list) else []), *(artifacts or [])]
        if isinstance(item, dict) and item.get("path")
    )
