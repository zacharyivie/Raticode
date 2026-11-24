"""Short document summaries: written into files by the savers, read back at index time.

A summary is plain text of at most ``SUMMARY_MAX_CHARS`` characters. Savers store it in
the file itself, so it travels with the file, packages and backups: a
``<meta name="description">`` for HTML (and the HTML source of a PDF) and a ``summary:``
front-matter field for Markdown. Indexing takes it, with no model call, from front
matter ``summary`` or ``description``, then the HTML meta description, then the first
paragraph under a heading named Short answer, Summary, TL;DR, Abstract or Key findings.

A summary describes a document; it is never a passage. It is stored beside the
passages, never among them, so it can't be searched as a passage or cited.
"""

from __future__ import annotations

import json
import re
import unicodedata
from html import escape
from pathlib import PurePosixPath
from typing import Any

SUMMARY_MAX_CHARS = 600
# Bump when summaries come out differently, so text documents are read again once.
SUMMARY_READER = 1
SUMMARY_HEADINGS = frozenset(
    {"short answer", "summary", "tl;dr", "tldr", "abstract", "key findings"}
)
# Where a document's summary came from: the file's own field, the first paragraph of a
# summary section, or Edit metadata in the Second Brain.
SOURCE_FILE = "file"
SOURCE_SECTION = "section"
SOURCE_METADATA = "metadata"
SUMMARY_SUFFIXES = frozenset({".md", ".markdown", ".html", ".htm", ".txt"})
# Told to every saver and to Rem with search results.
SUMMARY_RULE = (
    "Pass summary: two or three plain-text sentences (at most 600 characters) saying what "
    "the document finds or answers. Raticode stores it in the file (a meta description in "
    "HTML, a summary: front-matter field in Markdown) and Rem sees it first in search. "
    "A summary describes the document; it is not a passage and can't be cited."
)
_DROPPED_CATEGORIES = frozenset({"Cc", "Cs", "Co", "Cn"})
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def clean_summary(value: Any) -> str:
    """Summary text as one line: whitespace collapsed, control and private characters gone."""
    if not isinstance(value, str):
        return ""
    text = "".join(
        " " if char.isspace() else char
        for char in value
        if char.isspace() or unicodedata.category(char) not in _DROPPED_CATEGORIES
    )
    return re.sub(r" {2,}", " ", text).strip()


def capped_summary(value: Any) -> str:
    """A summary read from a file, cut at a word to at most SUMMARY_MAX_CHARS."""
    text = clean_summary(value)
    if len(text) <= SUMMARY_MAX_CHARS:
        return text
    cut = text[: SUMMARY_MAX_CHARS - 1]
    space = cut.rfind(" ")
    if space > SUMMARY_MAX_CHARS // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:-") + "…"


def checked_summary(value: Any) -> str:
    """A summary argument from a saver or an edit: "" for none; refuses anything too long."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"summary must be plain text of at most {SUMMARY_MAX_CHARS} characters.")
    text = clean_summary(value)
    if len(text) > SUMMARY_MAX_CHARS:
        raise ValueError(
            f"Keep summary to at most {SUMMARY_MAX_CHARS} characters; this one has "
            f"{len(text)}. Shorten it and save again."
        )
    return text


def _heading_name(label: str) -> str:
    """'01 · Short answer:' and '**TL;DR**' compare as 'short answer' and 'tl;dr'."""
    name = re.sub(r"[*_`]", "", label.casefold())
    name = re.sub(r"^[^a-z]+", "", name)
    return re.sub(r"[\s:.#\-–—]+$", "", name)


def _paragraph(text: str) -> str:
    """A passage as summary text: list markers dropped, lines joined."""
    lines = [re.sub(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+", "", line) for line in text.splitlines()]
    return " ".join(line.strip() for line in lines if line.strip())


def section_summary(blocks: list[dict[str, Any]]) -> str:
    """The first paragraph under the first summary heading that has one, or ""."""
    from gofer.ui.brain_documents import _heading_passage

    for index, block in enumerate(blocks):
        rendered = _heading_passage(block)
        if rendered is None or _heading_name(str(block.get("heading") or "")) not in (
            SUMMARY_HEADINGS
        ):
            continue
        # "## Summary\nText" with no blank line between is one Markdown passage.
        rest = rendered.split("\n", 1)[1] if rendered.startswith("#") and "\n" in rendered else ""
        if rest.strip():
            return capped_summary(_paragraph(rest))
        for following in blocks[index + 1 :]:
            if following.get("heading") != block.get("heading"):
                break
            if _heading_passage(following) is None and str(following["text"]).strip():
                return capped_summary(_paragraph(str(following["text"])))
    return ""


def extracted_summary(meta: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, str]:
    """Summary fields for an extracted text document: summary and summarySource, or none.

    ``meta`` holds front matter (Markdown) or the meta description (HTML).
    """
    for key in ("summary", "description"):
        value = meta.get(key)
        text = capped_summary(value) if isinstance(value, str) else ""
        if text:
            return {"summary": text, "summarySource": SOURCE_FILE}
    text = section_summary(blocks)
    return {"summary": text, "summarySource": SOURCE_SECTION} if text else {}


def summary_stale(doc: dict[str, Any]) -> bool:
    """A text document indexed before summaries were read (read again once)."""
    return (
        PurePosixPath(str(doc.get("path", ""))).suffix.lower() in SUMMARY_SUFFIXES
        and doc.get("extraction") == "ready"
        and not doc.get("missing")
        and int(doc.get("summaryReader") or 0) < SUMMARY_READER
    )


def file_summary(doc: dict[str, Any]) -> tuple[str, str]:
    """The summary the document's file gives, under any summary set in Edit metadata."""
    if doc.get("summarySource") == SOURCE_METADATA:
        return str(doc.get("fileSummary") or ""), str(doc.get("fileSummarySource") or "")
    return str(doc.get("summary") or ""), str(doc.get("summarySource") or "")


def apply_summary(doc: dict[str, Any], from_file: tuple[str, str] | None = None) -> None:
    """Set summary and summarySource: Edit metadata's summary wins over the file's.

    ``from_file`` is a fresh extraction's (summary, summarySource); without it the
    document keeps the file summary it has. The file's summary is kept as fileSummary
    only while Edit metadata hides it, so clearing the edit brings it back.
    """
    text, source = from_file if from_file is not None else file_summary(doc)
    for key in ("summary", "summarySource", "fileSummary", "fileSummarySource"):
        doc.pop(key, None)
    edited = clean_summary((doc.get("metadata") or {}).get("summary"))
    if edited:
        doc.update(summary=edited, summarySource=SOURCE_METADATA)
        if text:
            doc.update(fileSummary=text, fileSummarySource=source)
    elif text:
        doc.update(summary=text, summarySource=source)


def summary_field(doc: dict[str, Any]) -> dict[str, str]:
    """{"summary": ...} for a tool result, capped whatever the record holds; {} for none."""
    text = capped_summary(doc.get("summary"))
    return {"summary": text} if text else {}


# A <meta> tag whose name is description, however its attributes are quoted or ordered.
_META_TAG = re.compile(r"<meta\b[^>]*>", re.I)
_DESCRIPTION_NAME = re.compile(r"""\bname\s*=\s*(["']?)description\1(?=[\s/>]|$)""", re.I)


def _html_with_summary(content: str, summary: str) -> str:
    content = _META_TAG.sub(
        lambda match: "" if _DESCRIPTION_NAME.search(match.group()) else match.group(), content
    )
    tag = f'<meta name="description" content="{escape(summary, quote=True)}">'
    head = re.search(r"<head(?:\s[^>]*)?>", content, re.I)
    if head:
        return content[: head.end()] + tag + content[head.end() :]
    html = re.search(r"<html(?:\s[^>]*)?>", content, re.I)
    if html:
        return content[: html.end()] + f"<head>{tag}</head>" + content[html.end() :]
    doctype = re.match(r"\s*<!doctype[^>]*>", content, re.I)
    if doctype:
        return content[: doctype.end()] + tag + content[doctype.end() :]
    return tag + "\n" + content


def _markdown_with_summary(content: str, summary: str) -> str:
    from gofer.ui.brain_documents import _front_matter

    line = "summary: " + json.dumps(summary, ensure_ascii=False)
    front = _FRONT_MATTER.match(content)
    if not front:
        return f"---\n{line}\n---\n\n{content}"
    kept: list[str] = []
    skipping = False
    for header_line in front.group(1).split("\n"):
        if re.match(r"summary\s*:", header_line):
            skipping = True
            continue
        if skipping and (not header_line.strip() or header_line[:1] in {" ", "\t"}):
            continue
        skipping = False
        kept.append(header_line)
    header = "\n".join([line, *kept])
    loaded = _front_matter(header)
    if not isinstance(loaded, dict) or loaded.get("summary") != summary:
        raise ValueError(
            "The note's front matter couldn't take the summary. Fix its front matter, or "
            "leave out the summary argument and put summary: in the front matter yourself."
        )
    return f"---\n{header}\n---\n" + content[front.end() :]


def with_summary(content: str, summary: str, markdown: bool) -> str:
    """Content with the summary written in: front matter for Markdown, else a meta tag.

    The summary replaces any summary (or meta description) the content already has.
    """
    if not summary:
        return content
    return (
        _markdown_with_summary(content, summary)
        if markdown
        else _html_with_summary(content, summary)
    )
