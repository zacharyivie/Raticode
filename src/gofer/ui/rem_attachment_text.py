"""The readable text of files attached in Rem's chat, one bounded part at a time.

Rem's chat model has no file tools, so the backend reads an attached text file for it.
HTML becomes plain text: styles, scripts and drawings are dropped, and headings, list
items and table rows stay readable (tables as ``| a | b |`` rows). The request a file
came with carries its first part (``first_part``); an ``attachment`` lookup reads any
later part, or the part where some words first appear (``read_part``). Both say how long
the whole text is and where the next part starts, so Rem knows what it hasn't seen yet.
"""

from __future__ import annotations

import codecs
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from gofer.utils.atomic_output import open_binary_input

# Files attached in Rem's chat are uploaded under this chat attachment thread.
ATTACHMENT_THREAD = "rem-home"
# The most bytes of one file turned into text. Chat attachments can be up to 20 MB; a
# bigger text file is read up to here and says so.
MAX_READ_BYTES = 4 * 1024 * 1024
# A part ends at a line break when one is this close to its limit, so the next part
# starts at the beginning of a line.
_LINE_SLACK = 2000
# A part found by words starts this far before them, at a line start.
_LEAD = 300

TEXT_NAME = re.compile(
    r"\.(txt|md|markdown|csv|tsv|json|jsonl|ya?ml|toml|ini|log|xml|html?|xhtml|css|"
    r"[jt]sx?|mjs|cjs|py|rb|go|rs|java|kt|swift|c|cc|cpp|h|hpp|cs|sh|bash|fish|sql|"
    r"rattish|diff|patch)$",
    re.IGNORECASE,
)
_TEXT_TYPES = {
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/javascript",
    "application/x-yaml",
    "application/yaml",
    "application/toml",
    "application/sql",
}
_HTML_NAME = re.compile(r"\.(html?|xhtml)$", re.IGNORECASE)
# Each leading comment ends at its first "-->": a lazy ``.*?`` there could stretch across
# comment boundaries, and a text file starting with a few dozen comments took hours.
_HTML_START = re.compile(
    rb"^\s*(?:<\?xml[^>]*>\s*)?(?:<!--(?:(?!-->).)*-->\s*)*<(?:!doctype\s+html|html)\b",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class AttachmentText:
    """A file's readable text; ``complete`` is False when only its start was read."""

    text: str
    html: bool
    complete: bool


def stored_name(name: str) -> str:
    """The name the user gave a file, without the random ID Raticode stores it under."""
    return re.sub(r"^[0-9a-f]{32}-", "", name)


def textual(name: str, media_type: str) -> bool:
    """Whether a file's name or declared type says it holds text."""
    kind = media_type.split(";", 1)[0].strip().lower()
    return kind.startswith("text/") or kind in _TEXT_TYPES or bool(TEXT_NAME.search(name))


def _looks_like_text(head: bytes) -> bool:
    """UTF-8 with no NUL bytes, allowing a character cut at the end of the sample."""
    if b"\x00" in head:
        return False
    try:
        codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
    except UnicodeDecodeError:
        return False
    return True


def read_attachment(path: Path, *, name: str, media_type: str) -> AttachmentText | None:
    """The readable text of an attached file, or None when it isn't a text file.

    The file is opened without following links. A file whose name and type don't say
    text still counts when its first bytes are UTF-8 without NUL bytes, so a log or
    config file with an unusual name is read too.
    """
    with open_binary_input(path) as source:
        info = os.fstat(source.fileno())
    return _cached(str(path), info.st_mtime_ns, info.st_size, name, media_type)


@lru_cache(maxsize=8)
def _cached(path: str, _mtime: int, size: int, name: str, media_type: str) -> AttachmentText | None:
    with open_binary_input(Path(path)) as source:
        data = source.read(min(8192, MAX_READ_BYTES))
        # An image or other binary file is never read past its first bytes.
        if not textual(name, media_type) and not _looks_like_text(data):
            return None
        data += source.read(MAX_READ_BYTES - len(data))
    kind = media_type.split(";", 1)[0].strip().lower()
    is_html = (
        kind in {"text/html", "application/xhtml+xml"}
        or bool(_HTML_NAME.search(name))
        or bool(_HTML_START.match(data[:4096]))
    )
    raw = data.decode("utf-8", errors="replace")
    text = html_to_text(raw) if is_html else raw
    return AttachmentText(text=text, html=is_html, complete=size <= MAX_READ_BYTES)


def _cut(text: str, start: int, limit: int) -> int:
    """Where a part that starts at [start] ends: at a line break near [limit] if one is."""
    end = min(len(text), start + max(limit, 0))
    if end >= len(text):
        return len(text)
    newline = text.rfind("\n", max(start, end - _LINE_SLACK), end)
    return newline + 1 if newline > start else end


def _part(found: AttachmentText, start: int, limit: int) -> dict[str, Any]:
    text = found.text
    end = _cut(text, start, limit)
    part: dict[str, Any] = {"text": text[start:end], "text_chars": len(text)}
    if found.html:
        part["converted_from"] = "html"
    if start or end < len(text):
        part["shown"] = [start, end]
    if end < len(text):
        part["truncated"] = True
        part["next_offset"] = end
    if not found.complete:
        part["read_limit"] = (
            f"Only the first {MAX_READ_BYTES // (1024 * 1024)} MB of this file can be read "
            "in chat; start a task to read the rest."
        )
    return part


def first_part(found: AttachmentText, limit: int) -> dict[str, Any]:
    """The start of a file's text, up to about [limit] characters, and where the rest starts."""
    return _part(found, 0, limit)


def read_part(
    found: AttachmentText, *, offset: int | None, query: str | None, limit: int
) -> dict[str, Any]:
    """One part of a file's text: from [offset], or from where [query] first appears.

    With both, the words are looked for from [offset] on. Words that don't appear give
    the part at [offset] (or the start) with ``query_not_found``.
    """
    text = found.text
    start = min(max(offset or 0, 0), len(text))
    missing = False
    if query and query.strip():
        # A pattern match, not casefold(), so offsets stay those of the text itself.
        match = re.compile(re.escape(query.strip()), re.IGNORECASE).search(text, start)
        if match is None:
            missing = True
        else:
            at = match.start()
            # The start of the line the words are on, or a little before them in a long one.
            start = max(text.rfind("\n", start, at) + 1, at - _LEAD, start)
    part = _part(found, start, limit)
    if missing:
        part["query_not_found"] = True
    return part


# HTML ------------------------------------------------------------------------------

_HIDDEN = {"script", "style", "noscript", "template", "svg", "head", "math", "object"}
_BLOCKS = {
    "p", "div", "section", "article", "header", "footer", "main", "aside", "nav",
    "blockquote", "figure", "figcaption", "details", "summary", "dl", "dt", "dd",
    "form", "fieldset", "address", "hr", "caption",
}  # fmt: skip
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_LABELS = {"span", "b", "strong", "em", "i", "small", "label", "time", "code", "kbd", "mark"}


class _Readable(HTMLParser):
    """HTML as plain text that keeps headings, list items and table rows apart."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.hidden = 0
        self.pre = 0
        self.in_title = False
        # Only the page's own <title>; a drawing's <title> elements are captions.
        self.title_done = False
        self.title: list[str] = []
        self.row: list[str] | None = None
        self.row_header = True
        self.cell: list[str] | None = None
        # Rows written so far in each open table, innermost last.
        self.tables: list[int] = []
        self.links: list[str | None] = []
        # An inline label just opened or closed: ``<b>Ledger</b>13 Nov`` reads
        # "Ledger 13 Nov" when the next text runs straight on from a word.
        self.label_edge = False

    # Writing -------------------------------------------------------------------------

    def _write(self, value: str) -> None:
        if self.cell is not None:
            self.cell.append(value)
        else:
            self.out.append(value)

    def _at_line_start(self) -> bool:
        target = self.cell if self.cell is not None else self.out
        for piece in reversed(target):
            if piece:
                return piece.endswith("\n")
        return True

    def _last_char(self) -> str:
        target = self.cell if self.cell is not None else self.out
        last = next((piece for piece in reversed(target) if piece), "")
        return last[-1:] if last else ""

    def _break(self, value: str = "\n\n") -> None:
        self._write(" " if self.cell is not None else value)

    def _end_cell(self) -> None:
        if self.cell is None:
            return
        value = re.sub(r"\s+", " ", "".join(self.cell)).strip().replace("|", "\\|")
        self.cell = None
        if self.row is None:
            self.row = []
        self.row.append(value)

    def _end_row(self) -> None:
        self._end_cell()
        if self.row is None:
            return
        cells, header, self.row = self.row, self.row_header, None
        self.row_header = True
        if not any(cells):
            return
        if not self._at_line_start():
            self.out.append("\n")
        self.out.append("| " + " | ".join(cells) + " |\n")
        if self.tables:
            if self.tables[-1] == 0 and header:
                self.out.append("|" + " --- |" * len(cells) + "\n")
            self.tables[-1] += 1

    # Parsing -------------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title" and not self.title_done:
            self.in_title = True
        if tag in _HIDDEN:
            self.hidden += 1
            return
        if self.hidden:
            return
        if tag in _HEADINGS:
            self._break()
            if self.cell is None:
                self._write("#" * int(tag[1]) + " ")
        elif tag in _BLOCKS:
            self._break()
        elif tag == "pre":
            self.pre += 1
            self._break()
        elif tag in {"ul", "ol"}:
            self._break("\n")
        elif tag == "li":
            self._write(" " if self.cell is not None else "\n- ")
        elif tag == "br":
            self._break("\n")
        elif tag == "table":
            self._end_row() if self.cell is None else self._end_cell()
            self.tables.append(0)
            self.out.append("\n\n")
        elif tag == "tr":
            self._end_row()
            self.row = []
        elif tag in {"td", "th"}:
            self._end_cell()
            if self.row is None:
                self.row = []
            if tag == "td":
                self.row_header = False
            self.cell = []
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            keep = href.startswith(("http://", "https://", "file:", "/", "./", "../"))
            self.links.append(href if keep else None)
        elif tag in _LABELS:
            self.label_edge = True
        elif tag == "img":
            alt = (dict(attrs).get("alt") or "").strip()
            if alt:
                self._write(f"[image: {alt}]")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self.in_title:
            self.in_title = False
            self.title_done = True
        if tag in _HIDDEN:
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden:
            return
        if tag in _HEADINGS or tag in _BLOCKS:
            self._break()
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            self._break()
        elif tag in {"ul", "ol"}:
            self._break("\n")
        elif tag in {"td", "th"}:
            self._end_cell()
        elif tag == "tr":
            self._end_row()
        elif tag == "table":
            self._end_row()
            if self.tables:
                self.tables.pop()
            self.out.append("\n\n")
        elif tag in _LABELS:
            self.label_edge = True
        elif tag == "a" and self.links:
            href = self.links.pop()
            if href:
                self._write(f" ({href})")

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title.append(data)
        if self.hidden:
            return
        if self.pre and self.cell is None:
            self._write(data)
            return
        value = re.sub(r"\s+", " ", data)
        if self.label_edge and value[:1].isalnum() and self._last_char().isalnum():
            value = " " + value
        if value.strip():
            self.label_edge = False
        if value.startswith(" ") and self._at_line_start():
            value = value[1:]
        if value:
            self._write(value)

    def text(self) -> str:
        self._end_row()
        body = "".join(self.out)
        body = re.sub(r"[ \t]+\n", "\n", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        title = re.sub(r"\s+", " ", "".join(self.title)).strip()
        if title and not body.startswith(f"# {title}"):
            return f"Title: {title}\n\n{body}"
        return body


def html_to_text(content: str) -> str:
    """Readable text of an HTML page: headings as ``#`` lines, tables as ``|`` rows."""
    reader = _Readable()
    reader.feed(content)
    reader.close()
    return reader.text()
