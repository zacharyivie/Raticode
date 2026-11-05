"""Full file paths written in Rem's chat text, found without asking a model.

The desktop chat shows each mention as a link that opens the file. Paths may contain
spaces, so text alone can't say where one ends ("/home/me/My Videos/clip.mp4 is
ready"). The filesystem decides: from each place a path could start, the longest
stretch of the line that names an existing file wins. Nothing is read or sent; the
desktop only learns which text to underline.
"""

from __future__ import annotations

import os
import re
import time
from functools import lru_cache
from typing import Any

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


def _is_file(candidate: str) -> bool:
    path = os.path.expanduser(candidate)
    if not os.path.isabs(path):
        return False
    try:
        return os.path.isfile(path)
    except (OSError, ValueError):
        return False


def with_file_mentions(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rem's messages with the file paths their text names, for the desktop chat.

    The user's own messages show as typed, so only Rem's are read. A reply's
    collapsed details are read too.
    """
    result = []
    for message in messages:
        text = "\n".join(
            value
            for value in (message.get("body"), message.get("details"))
            if isinstance(value, str)
        )
        mentions = file_mentions(text) if message.get("role") != "user" and text else []
        result.append({**message, "file_mentions": mentions} if mentions else message)
    return result
