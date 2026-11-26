"""Find, classify and rewrite file links in notes and reports.

A saved report should keep working when the brain folder moves or syncs to another
computer, so links to files inside the folder are stored relative to the note.
Links to temporary or outside folders are left as written and reported, because
those files are not part of the brain and may disappear.
"""

from __future__ import annotations

import html
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

# Reasons a file link needs attention. Only ABSOLUTE links are rewritten.
ABSOLUTE = "absolute"
TEMPORARY = "temporary"
OUTSIDE = "outside"
MISSING = "missing"

TEMPORARY_ROOTS = tuple(
    dict.fromkeys(os.path.realpath(path) for path in ("/tmp", "/var/tmp", tempfile.gettempdir()))
)

_HTML_LINK = re.compile(
    r"""<[a-zA-Z][^<>]*?\s(?:href|src)\s*=\s*(?P<quote>["'])(?P<target>.*?)(?P=quote)""", re.S
)
# [text](target), ![alt](target) and [text](<target with spaces>), with an optional title.
_MARKDOWN_LINK = re.compile(r"\]\((?:<(?P<angled>[^<>\n]+)>|(?P<target>[^\s()<>]+))")
# [label]: target   (reference-style definitions)
_MARKDOWN_REFERENCE = re.compile(
    r"^[ ]{0,3}\[[^\]\n]+\]:[ \t]*(?:<(?P<angled>[^<>\n]+)>|(?P<target>\S+))", re.M
)
_FENCE = re.compile(r"^[ ]{0,3}(?P<fence>```|~~~).*?^[ ]{0,3}(?P=fence)[^\S\n]*$", re.S | re.M)
_INLINE_CODE = re.compile(r"`[^`\n]+`")


@dataclass(frozen=True)
class LinkCheck:
    """One link and, when it needs attention, why."""

    link: str
    reason: str | None = None  # None when the link is fine as written.
    resolved: str = ""
    relative: str = ""  # The replacement for an ABSOLUTE link.


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def check_link(link: str, root: Path, folder: Path) -> LinkCheck:
    """Classify one link written in a note in ``folder`` of the brain at ``root``.

    Symlinks and ``..`` are resolved before deciding whether a target is inside.
    Web addresses, anchors and other URL schemes are always fine.
    """
    value = link.strip()
    if not value or value.startswith("#"):
        return LinkCheck(link)
    parts = urlsplit(value)
    if parts.scheme.lower() == "file":
        if parts.netloc not in {"", "localhost"}:
            return LinkCheck(link)
        path_text, absolute = unquote(parts.path), True
    elif parts.scheme or value.startswith("//"):
        return LinkCheck(link)
    else:
        path_text = unquote(parts.path)
        absolute = path_text.startswith("/")
    if not path_text:
        return LinkCheck(link)
    suffix = (f"?{parts.query}" if parts.query else "") + (
        f"#{parts.fragment}" if parts.fragment else ""
    )
    real_root = os.path.realpath(root)
    resolved = os.path.realpath(path_text if absolute else os.path.join(folder, path_text))
    if _within(resolved, real_root):
        if not absolute:
            return LinkCheck(link, None if os.path.exists(resolved) else MISSING, resolved)
        relative = os.path.relpath(resolved, os.path.realpath(folder)).replace(os.sep, "/")
        if os.path.isdir(resolved) and not relative.endswith("/"):
            relative += "/"
        return LinkCheck(link, ABSOLUTE, resolved, quote(relative, safe="/") + suffix)
    if any(_within(resolved, item) for item in TEMPORARY_ROOTS):
        return LinkCheck(link, TEMPORARY, resolved)
    return LinkCheck(link, OUTSIDE, resolved)


def _spans(content: str, name: str) -> Iterator[tuple[int, int, str]]:
    """Yield (start, end, link) for every link target, skipping Markdown code."""
    markdown = Path(name).suffix.lower() in {".md", ".markdown"}
    found: list[tuple[int, int, str]] = []
    code = (
        [m.span() for m in _FENCE.finditer(content)]
        + [m.span() for m in _INLINE_CODE.finditer(content)]
        if markdown
        else []
    )

    def in_code(index: int) -> bool:
        return any(start <= index < end for start, end in code)

    for match in _HTML_LINK.finditer(content):
        if not in_code(match.start()):
            found.append(
                (match.start("target"), match.end("target"), html.unescape(match["target"]))
            )
    if markdown:
        for pattern in (_MARKDOWN_LINK, _MARKDOWN_REFERENCE):
            for match in pattern.finditer(content):
                group = "angled" if match["angled"] is not None else "target"
                if not in_code(match.start()):
                    found.append((match.start(group), match.end(group), match[group]))
    yield from sorted(set(found))


def rewrite_links(
    content: str, name: str, root: Path, folder: Path
) -> tuple[str, list[LinkCheck], list[LinkCheck]]:
    """Make absolute links that resolve inside the brain relative to ``folder``.

    Returns the new text, the rewritten links, and the links left as written
    because they point to a temporary folder or outside the brain.
    """
    pieces: list[str] = []
    rewritten: list[LinkCheck] = []
    flagged: list[LinkCheck] = []
    last = 0
    for start, end, link in _spans(content, name):
        check = check_link(link, root, folder)
        if check.reason == ABSOLUTE and start >= last:
            pieces += [content[last:start], check.relative]
            last = end
            rewritten.append(check)
        elif check.reason in {TEMPORARY, OUTSIDE}:
            flagged.append(check)
    pieces.append(content[last:])
    return "".join(pieces), rewritten, flagged


def link_warning(check: LinkCheck) -> str:
    if check.reason == TEMPORARY:
        return (
            f"The link {check.link} points to a temporary folder. The file can disappear at "
            "any time; copy it into the brain and link to the copy with a relative path."
        )
    return (
        f"The link {check.link} points outside the brain. It breaks when the brain moves "
        "or syncs; copy the file into the brain and link to the copy with a relative path."
    )
