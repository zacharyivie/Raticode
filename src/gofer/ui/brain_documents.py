"""Source extraction and portable archive validation for Second Brain libraries."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import threading
import zipfile
from html import escape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin

import yaml

from gofer.ui.brain_folders import checked_rules
from gofer.ui.brain_pdf import read_pdf
from gofer.ui.brain_summaries import SUMMARY_READER, extracted_summary
from gofer.ui.case_twins import fold_name
from gofer.utils.atomic_output import open_binary_input

MAX_FILE = 32 * 1024 * 1024
MAX_TEXT = 2 * 1024 * 1024
MAX_MANIFEST = 64 * 1024 * 1024
MAX_PACKAGE = 1024 * 1024 * 1024
MAX_ENTRIES = 20000
EXTENSIONS = {
    ".md": "note",
    ".markdown": "note",
    ".txt": "text",
    ".html": "html",
    ".htm": "html",
    ".pdf": "pdf",
}

SETTINGS = {
    "metadata",
    "semantic",
    "embeddings",
    "allowCloud",
    "formats",
    "ocrLanguage",
    "obsidian",
    "zotero",
    "embeddingProfile",
    "embeddingModel",
    "researchProfile",
    "researchProvider",
    "folderRules",
}


def validate_settings(settings: Any) -> None:
    if not isinstance(settings, dict) or set(settings) - SETTINGS:
        raise ValueError("Unknown brain settings")
    for key in ("semantic", "allowCloud", "obsidian", "zotero"):
        if key in settings and not isinstance(settings[key], bool):
            raise ValueError("Brain switches must be booleans")
    if settings.get("metadata", "portable") not in {"portable", "app"} or settings.get(
        "embeddings", "local"
    ) not in {"local", "cloud"}:
        raise ValueError("Choose a supported metadata and embedding mode")
    formats = settings.get("formats", {})
    if (
        not isinstance(formats, dict)
        or set(formats) - {"markdown", "html", "text", "pdf", "ocr"}
        or any(not isinstance(value, bool) for value in formats.values())
    ):
        raise ValueError("Choose supported document formats")
    if not re.fullmatch(r"[a-z]{3}(?:\+[a-z]{3})*", str(settings.get("ocrLanguage", "eng"))):
        raise ValueError("Choose a valid OCR language")
    for key in ("embeddingProfile", "embeddingModel", "researchProfile"):
        if key in settings and (not isinstance(settings[key], str) or len(settings[key]) > 200):
            raise ValueError("Invalid research profile or model name")

    provider = settings.get("researchProvider")
    if provider is not None and (
        not isinstance(provider, dict)
        or set(provider) - {"id", "model", "profile", "effort"}
        or not isinstance(provider.get("id"), str)
        or not provider["id"]
        or any(
            value is not None and (not isinstance(value, str) or len(value) > 200)
            for value in provider.values()
        )
    ):
        raise ValueError("Invalid research provider settings")
    if "folderRules" in settings:
        checked_rules(settings["folderRules"])


def validate_portable(payload: Any) -> None:
    """Validate an untrusted vault manifest before registering any of its records."""
    if (
        not isinstance(payload, dict)
        or payload.get("format") != "raticode-brain"
        or payload.get("version") != 1
    ):
        raise ValueError("Unsupported brain metadata version")
    brain, library = payload.get("brain"), payload.get("library")
    if not isinstance(brain, dict) or not isinstance(library, dict):
        raise ValueError("Invalid brain metadata")
    if (
        not isinstance(brain.get("id"), str)
        or not 1 <= len(brain["id"]) <= 150
        or not isinstance(brain.get("name"), str)
    ):
        raise ValueError("Invalid brain identity")
    validate_settings(brain.get("settings", {}))
    known = {
        "documents",
        "collections",
        "savedSearches",
        "reports",
        "citations",
        "jobs",
        "projects",
        "topics",
    }
    if set(library) - known:
        raise ValueError("Unknown brain metadata records")
    ids: set[str] = set()
    documents: dict[str, Any] = {}
    for kind, items in library.items():
        if not isinstance(items, list) or len(items) > MAX_ENTRIES:
            raise ValueError("Invalid brain record list")
        for item in items:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not 1 <= len(item["id"]) <= 150
            ):
                raise ValueError("Invalid brain record identity")
            if item["id"] in ids or item.get("brainId") != brain["id"]:
                raise ValueError("Duplicate or foreign brain record identity")
            ids.add(item["id"])
            if kind == "documents":
                safe_relative(item.get("path", ""))
                if (
                    not isinstance(item.get("version"), str)
                    or not isinstance(item.get("blocks"), list)
                    or not isinstance(item.get("versions"), list)
                    or not isinstance(item.get("dates"), dict)
                ):
                    raise ValueError("Invalid source version metadata")
                for block in item["blocks"]:
                    if (
                        not isinstance(block, dict)
                        or not isinstance(block.get("id"), str)
                        or not isinstance(block.get("text"), str)
                    ):
                        raise ValueError("Invalid source passage")
                for version in item["versions"]:
                    if (
                        not isinstance(version, dict)
                        or not isinstance(version.get("hash"), str)
                        or not isinstance(version.get("blocks"), dict)
                        or any(not isinstance(text, str) for text in version["blocks"].values())
                    ):
                        raise ValueError("Invalid historical source passages")
                documents[item["id"]] = item
            if kind == "reports":
                safe_relative(item.get("artifactPath", ""))
                if not isinstance(item.get("claims"), list) or not isinstance(
                    item.get("citations"), list
                ):
                    raise ValueError("Invalid report metadata")
            if kind == "collections" and not isinstance(item.get("sections"), list):
                raise ValueError("Invalid collection metadata")
            if kind == "jobs":
                safe_relative(item.get("destination", "Summaries"))
                if not isinstance(item.get("trigger"), dict) or not isinstance(
                    item.get("limits"), dict
                ):
                    raise ValueError("Invalid research job metadata")
    external = payload.get("externalSources", [])
    if not isinstance(external, list):
        raise ValueError("Invalid external source snapshots")
    for doc in external:
        if (
            not isinstance(doc, dict)
            or not isinstance(doc.get("id"), str)
            or not isinstance(doc.get("blocks"), list)
            or not isinstance(doc.get("versions"), list)
        ):
            raise ValueError("Invalid external source snapshot")
        if doc["id"] in documents:
            raise ValueError("Ambiguous external source identity")
        documents[doc["id"]] = doc
    references = list(library.get("citations", []))
    references += [c for report in library.get("reports", []) for c in report["citations"]]
    for collection in library.get("collections", []):
        for section in collection["sections"]:
            if not isinstance(section, dict) or not isinstance(section.get("items"), list):
                raise ValueError("Invalid collection section")
            references += section["items"]
        for field in ("comparison", "comparisonDraft"):
            comparison = collection.get(field)
            if comparison:
                references += [
                    cell
                    for row in comparison.get("rows", [])
                    for cell in row.get("cells", {}).values()
                    if cell
                ]
                references += [
                    side
                    for conflict in comparison.get("conflicts", [])
                    for side in (conflict["left"], conflict["right"])
                ]
    for reference in references:
        if not isinstance(reference, dict) or reference.get("docId") not in documents:
            raise ValueError("Evidence names a source outside this package")
        doc = documents[reference["docId"]]
        version = reference.get("version", doc["version"])
        blocks = (
            {block["id"]: block["text"] for block in doc["blocks"]}
            if version == doc["version"]
            else next((v["blocks"] for v in doc["versions"] if v["hash"] == version), {})
        )
        if reference.get("blockId") and reference["blockId"] not in blocks:
            # An unresolved current citation retains the frozen snapshot for manual review.
            if not reference.get("snapshot") or reference.get("state") not in {
                "unavailable",
                "relocate",
                "changed",
            }:
                raise ValueError("Evidence passage is missing from its source version")
        quote = reference.get("quote")
        if quote is not None and (
            not isinstance(quote, str)
            or not quote
            or quote not in str(blocks.get(reference.get("blockId"), reference.get("snapshot", "")))
        ):
            raise ValueError("Citation text does not match the packaged source")


def safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if not value or "\\" in value or "\x00" in value or path.is_absolute():
        raise ValueError("Expected a relative path inside the brain")
    if any(part in {"..", "."} or ":" in part for part in value.split("/")):
        raise ValueError("Unsafe path in brain")
    return path.as_posix()


def source_path(root: Path, relative: str) -> Path:
    relative = safe_relative(relative)
    target = root
    for part in PurePosixPath(relative).parts:
        target = target / part
        if target.is_symlink():
            raise ValueError("Symbolic links are not followed inside a brain")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path leaves the brain")
    return target


def read_source(root: Path, relative: str, limit: int = MAX_FILE) -> bytes:
    target = source_path(root, relative)
    with open_binary_input(target) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Source is not a regular file within the size limit")
        # read(n) reserves n bytes up front, so asking for the limit would hold 32 MiB
        # for every small note. Ask for the file's size; read on only if it grew.
        data = handle.read(info.st_size + 1)
        if len(data) > info.st_size:
            data += handle.read(limit + 1 - len(data))
        if len(data) > limit:
            raise ValueError("Source exceeds the size limit")
        return data


def preview_html(content: str) -> str:
    """Keep authored HTML styling while removing automatic navigation and active links."""

    class Preview(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self.parts: list[str] = []
            self.script = False

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "script":
                self.script = True
                return
            values = dict(attrs)
            if tag in {"base", "link"} or (
                tag == "meta" and str(values.get("http-equiv", "")).casefold() == "refresh"
            ):
                return
            safe = [
                (key, value)
                for key, value in attrs
                if not key.startswith("on")
                and (key not in {"href", "xlink:href"} or (value or "").startswith("#"))
            ]
            self.parts.append(
                (self.get_starttag_text() or "")
                if safe == attrs
                else "<"
                + tag
                + "".join(
                    " " + key + (f'="{escape(value, quote=True)}"' if value is not None else "")
                    for key, value in safe
                )
                + ">"
            )

        def handle_endtag(self, tag: str) -> None:
            if tag == "script":
                self.script = False
            elif not self.script:
                self.parts.append(f"</{tag}>")

        def handle_data(self, data: str) -> None:
            if not self.script:
                self.parts.append(data)

        def handle_entityref(self, name: str) -> None:
            self.handle_data(f"&{name};")

        def handle_charref(self, name: str) -> None:
            self.handle_data(f"&#{name};")

        def handle_decl(self, decl: str) -> None:
            self.parts.append(f"<!{decl}>")

    parser = Preview()
    parser.feed(content)
    return "".join(parser.parts)


# Bump when HTML passages come out differently, so stored HTML documents are read again
# once (brain_library re-extracts documents whose "reader" is older and re-finds citations
# on their current version by quote). 2: h1-h4 headings and joined short fragments.
# 3: the title skips svg and math <title> captions (brain_library also retitles report
# records whose title came from the file).
HTML_READER = 3
# Passages shorter than this that are not headings join the next passage in their section,
# so KPI numbers, section numbers and status words don't become search hits on their own.
FRAGMENT_CHARS = 25
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4"})
_BREAKS_HEADING = frozenset({"p", "div", "section", "article", "li", "tr", "table", "ul", "ol"})
# Marks the start of a heading paragraph in ReadableHTML's text. It is a private-use
# character, and any copy of it in the page's own text is dropped.
HEADING_MARK = ""


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(HEADING_MARK, "")).strip()


class ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0
        # The page's own <title>: the first one outside svg and math, where <title> is a
        # diagram's caption. Later ones are ignored.
        self.title_parts: list[str] = []
        self.in_title = False
        self.title_seen = False
        self.foreign = 0  # How many svg or math elements are open.
        self.meta: dict[str, str] = {}
        self.links: list[str] = []
        # The h1-h4 currently open (outside hidden content), or "".
        self.heading = ""
        # Where the first h1's text starts in parts while it is open, and its text after.
        self.h1_start: int | None = None
        self.first_h1 = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"script", "style", "noscript", "template", "svg"}:
            self.hidden += 1
        if tag in {"svg", "math"}:
            self.foreign += 1
        if tag == "title" and not self.foreign and not self.title_seen:
            self.in_title = self.title_seen = True
        if tag == "meta":
            self.meta[str(values.get("name") or values.get("property") or "").lower()] = str(
                values.get("content") or ""
            )
        if tag == "a" and values.get("href"):
            self.links.append(str(values["href"]))
        if self.hidden:
            return
        if tag in _HEADING_TAGS:
            # A new heading ends an open one, as it does in a browser.
            self._end_h1()
            self.heading = tag
            if tag == "h1" and not self.first_h1:
                self.h1_start = len(self.parts)
            self.parts.append("\n\n" + HEADING_MARK)
        elif self.heading and tag not in _BREAKS_HEADING:
            # "<span>01</span>Short answer" reads as "01 Short answer" in a heading.
            self.parts.append(" ")
        elif self.heading:
            # A block inside a heading most likely means its end tag is missing: end it
            # here rather than run the rest of the page into one heading.
            self._end_h1()
            self.heading = ""
            self.parts.append("\n\n")
        elif tag in {"p", "div", "section", "article", "li", "br", "tr", "h5", "h6"}:
            # h5 and h6 start a paragraph but not a section.
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template", "svg"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"svg", "math"}:
            self.foreign = max(0, self.foreign - 1)
        if tag == "title":
            self.in_title = False
        if self.hidden or not self.heading:
            return
        if tag in _HEADING_TAGS:
            self._end_h1()
            self.heading = ""
            self.parts.append("\n\n")
        else:
            self.parts.append(" ")

    def _end_h1(self) -> None:
        """Keep the first h1's text when the open heading is that h1 and it ends."""
        if self.heading == "h1" and self.h1_start is not None:
            self.first_h1 = _one_line("".join(self.parts[self.h1_start :]))
            # An h1 with no text (an image, say) leaves the title to the next one.
            self.h1_start = None

    def title(self) -> str:
        """The page's title, else its og:title, else its first h1, else ""."""
        self._end_h1()
        return (
            "".join(self.title_parts).strip()
            or self.meta.get("og:title", "").strip()
            or self.first_h1
        )

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        elif not self.hidden:
            data = data.replace(HEADING_MARK, "")
            # A heading is one paragraph, however its source lines wrap.
            self.parts.append(re.sub(r"\s+", " ", data) if self.heading else data)


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """(offset, paragraph) pairs: runs of lines separated by blank lines, stripped."""
    found = []
    for match in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", text):
        raw = match.group()
        paragraph = raw.strip()
        if paragraph:
            found.append((match.start() + len(raw) - len(raw.lstrip()), paragraph))
    return found


def blocks_for(text: str, headings: dict[int, str] | None = None) -> list[dict[str, Any]]:
    """Bounded passages, with offsets into normalized text and content-stable IDs.

    Without ``headings``, a Markdown ``#`` line starts a section. With it (HTML), the
    paragraph starting at each offset in ``headings`` starts a section named by its value,
    and a short passage that isn't a heading joins the next one in the same section.
    """
    blocks: list[dict[str, Any]] = []
    heading = "Document"
    counts: dict[str, int] = {}
    paragraphs = _paragraphs(text)
    merged: list[tuple[int, str]] = []
    pending: tuple[int, str] | None = None
    for start, paragraph in paragraphs:
        if headings is not None and start in headings:
            if pending:
                merged.append(pending)
                pending = None
            merged.append((start, paragraph))
            continue
        if pending:
            # The passage keeps the text between the two, so offsets still slice it out.
            start, paragraph = pending[0], text[pending[0] : start + len(paragraph)]
            pending = None
        if headings is not None and len(paragraph) < FRAGMENT_CHARS:
            pending = (start, paragraph)
        else:
            merged.append((start, paragraph))
    if pending:
        merged.append(pending)
    for start, paragraph in merged:
        if headings is None:
            if re.match(r"^#{1,6}\s", paragraph):
                heading = re.sub(r"^#+\s*", "", paragraph.splitlines()[0])
        elif start in headings:
            heading = headings[start]
        for offset in range(0, len(paragraph), 1600):
            passage = paragraph[offset : offset + 1600]
            digest = hashlib.sha256(passage.encode()).hexdigest()[:20]
            counts[digest] = counts.get(digest, 0) + 1
            blocks.append(
                {
                    "id": f"b-{digest}-{counts[digest]}",
                    "heading": heading,
                    "text": passage,
                    "start": start + offset,
                    "end": start + offset + len(passage),
                }
            )
    return blocks


def passages_text(
    blocks: list[dict[str, Any]], start: str | None = None, limit: int | None = None
) -> str:
    """Readable text of a document's passages, from the passage ``start`` names (or the first).

    Each section opens with its heading: a Markdown ``#`` line stays as written, and any
    other heading (an HTML h1-h4, a PDF page) becomes a ``## `` line. Text that starts
    inside a section opens with that section's heading. Passages cut from one long
    paragraph join back without a break, so Markdown reads as the note did, without
    front matter.
    """
    index = next((n for n, block in enumerate(blocks) if block["id"] == start), 0)
    chosen = blocks[index:]
    if chosen and _heading_passage(chosen[0]) is None:
        # Starting inside a section: its heading comes first.
        for prior in reversed(blocks[:index]):
            if prior.get("heading") != chosen[0].get("heading"):
                break
            if _heading_passage(prior) is not None:
                chosen = [prior, *chosen]
                break
    pieces: list[str] = []
    heading = "Document"
    end: int | None = None
    for block in chosen:
        text = str(block["text"])
        label = str(block.get("heading") or heading)
        rendered = _heading_passage(block)
        if rendered is not None:
            heading, text = label, rendered
        elif label != heading:
            heading = label
            pieces.append(("\n\n" if pieces else "") + "## " + heading)
            end = None
        joined = end is not None and block.get("start") == end
        pieces.append(("" if joined or not pieces else "\n\n") + text)
        end = block.get("end")
        if limit is not None and sum(map(len, pieces)) >= limit:
            break
    text = "".join(pieces)
    return text if limit is None else text[:limit]


def _heading_passage(block: dict[str, Any]) -> str | None:
    """A passage that is its section's heading, as text; None for any other passage."""
    text, label = str(block["text"]), str(block.get("heading") or "")
    if re.match(r"#{1,6}\s", text):
        return text  # A Markdown heading line, kept as written.
    if text == label and label != "Document":
        return "## " + text  # An HTML heading.
    return None


def html_reader_stale(doc: dict[str, Any]) -> bool:
    """An HTML document whose passages an older reader made (re-extracted once)."""
    return (
        PurePosixPath(str(doc.get("path", ""))).suffix.lower() in {".html", ".htm"}
        and doc.get("extraction") == "ready"
        and not doc.get("missing")
        and int(doc.get("reader") or 0) < HTML_READER
    )


def quote_selector(text: str, quote: str) -> dict[str, Any]:
    """Where a quote sits in its passage, with the text around it."""
    start = text.index(quote)
    return {
        "exact": quote,
        "prefix": text[max(0, start - 80) : start],
        "suffix": text[start + len(quote) : start + len(quote) + 80],
        "start": start,
        "end": start + len(quote),
    }


def reanchor(
    evidence: dict[str, Any], old_blocks: dict[str, str], new_blocks: list[dict[str, Any]]
) -> bool:
    """Point evidence on a re-extracted version at the passage that now holds its quote.

    Passage IDs hash passage text, so reading the same file differently changes them.
    The quote is found again by exact text; when the same quote appears in several
    passages, the one that contains the whole old passage wins. Evidence whose quote is
    gone keeps its old passage text as a snapshot and is marked for relocation.
    Returns whether the evidence changed.
    """
    block_id = str(evidence.get("blockId") or "")
    if not block_id or any(block["id"] == block_id for block in new_blocks):
        return False
    old_text = old_blocks.get(block_id) or str(evidence.get("snapshot") or "")
    quote = str(evidence.get("quote") or old_text)
    matches = [block for block in new_blocks if quote and quote in block["text"]]
    if not matches:
        evidence["state"] = "relocate"
        evidence["snapshot"] = evidence.get("snapshot") or old_text
        return True
    best = next((block for block in matches if old_text and old_text in block["text"]), matches[0])
    evidence["blockId"] = best["id"]
    if "selector" in evidence or "snapshot" in evidence:
        evidence["selector"] = quote_selector(best["text"], quote)
        evidence["snapshot"] = best["text"]
    return True


def html_text(parts: list[str]) -> tuple[str, dict[int, str]]:
    """ReadableHTML's text, normalized, and the heading that starts at each offset."""
    text = "".join(parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    headings: dict[int, str] = {}
    pieces: list[str] = []
    length = 0
    for index, piece in enumerate(text.split(HEADING_MARK)):
        if index:
            # The mark sits right after a paragraph break; the heading starts at its
            # first visible character.
            stripped = piece.lstrip(" ")
            if stripped and not stripped.startswith("\n"):
                label = stripped.split("\n", 1)[0].strip()[:200]
                if label:
                    headings[length] = label
            piece = stripped
        pieces.append(piece)
        length += len(piece)
    text = "".join(pieces).strip()
    # Stripping the start moves every offset; the heading map moves with it.
    shift = len("".join(pieces)) - len("".join(pieces).lstrip())
    return text, {offset - shift: label for offset, label in headings.items()}


MAX_FRONT_MATTER_CHARS = 64 * 1024


def _front_matter(header: str) -> Any:
    """Parse a note's YAML front matter, or None when it is too large or uses aliases.

    Anchors and aliases let a few hundred bytes expand into gigabytes once the values are
    turned into text, and any agent or synced folder can add a note.
    """
    if len(header) > MAX_FRONT_MATTER_CHARS:
        return None
    try:
        if any(
            isinstance(token, (yaml.tokens.AnchorToken, yaml.tokens.AliasToken))
            for token in yaml.scan(header)
        ):
            return None
        return yaml.safe_load(header)
    except yaml.YAMLError:
        return None


def extract(
    data: bytes,
    suffix: str,
    *,
    encoding: str = "utf-8",
    obsidian: bool = True,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """A file's passages and metadata.

    PDFs are read in a child process with time and memory limits (gofer.ui.brain_pdf);
    one that can't finish raises PdfReadStopped, a ValueError. ``cancel`` stops it.
    """
    meta: dict[str, Any] = {}
    if suffix == ".pdf":
        read = read_pdf(data, max_text=MAX_TEXT, cancel=cancel)
        blocks = []
        for index, text in enumerate(read["pages"]):
            for block in blocks_for(text):
                blocks.append(
                    {
                        **block,
                        "id": f"p{index + 1}-{block['id']}",
                        "page": index + 1,
                        "heading": f"Page {index + 1}",
                    }
                )
        return {
            "blocks": blocks,
            "extraction": "ready" if blocks else "ocr",
            "title": str(read.get("title") or ""),
            "author": str(read.get("author") or ""),
            "pageCount": len(read["pages"]),
            "links": [],
        }
    text = data.decode(encoding, errors="strict")
    if len(text.encode("utf-8")) > MAX_TEXT:
        raise ValueError("Extracted text exceeds 2 MB")
    links: list[str] = []
    headings: dict[int, str] | None = None
    if suffix in {".html", ".htm"}:
        parser = ReadableHTML()
        parser.feed(text)
        text, headings = html_text(parser.parts)
        meta = {
            "title": parser.title(),
            "author": parser.meta.get("author", ""),
            "published": parser.meta.get("article:published_time", ""),
            "url": parser.meta.get("og:url", ""),
            # <meta name="project"> and <meta name="keywords"> let HTML reports say
            # which project they belong to and what they cover, like front matter.
            "project": parser.meta.get("project", "")[:200],
            "tags": [
                word.strip()[:60]
                for word in parser.meta.get("keywords", "").split(",")
                if word.strip()
            ][:30],
            "description": parser.meta.get("description", ""),
        }
        links = parser.links
        if meta["url"]:
            links = [urljoin(meta["url"], link) for link in links]
    elif suffix in {".md", ".markdown"}:
        front = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.S)
        if front:
            loaded = _front_matter(front.group(1))
            if isinstance(loaded, dict):
                meta = loaded
            text = text[front.end() :]
        if obsidian:
            links = re.findall(r"\[\[([^\]|#]+)(?:[^\]]*)\]\]", text)
            meta["tags"] = list(
                set(_tags(meta.get("tags")) + re.findall(r"(?<!\w)#([\w/-]+)", text))
            )
        # [text](target) and [text](<target with spaces>), which reports save for
        # paths with spaces.
        links += [
            angled or plain
            for angled, plain in re.findall(r"\[[^\]]*\]\((?:<([^<>\n]+)>|([^ )]+))", text)
        ]
        if not meta.get("title"):
            title = re.search(r"^#\s+(.+)$", text, re.M)
            meta["title"] = title.group(1) if title else ""
    blocks = blocks_for(text, headings)
    return {
        "blocks": blocks,
        **({"reader": HTML_READER} if headings is not None else {}),
        **extracted_summary(meta, blocks),
        "summaryReader": SUMMARY_READER,
        "extraction": "ready",
        "tags": _tags(meta.get("tags")),
        "title": str(meta.get("title") or ""),
        "author": str(meta.get("author") or ""),
        "published": str(meta.get("published") or meta.get("date") or ""),
        "project": str(meta.get("project") or ""),
        "organization": str(meta.get("organization") or "")[:200],
        "url": str(meta.get("url") or meta.get("source") or ""),
        "doi": str(meta.get("doi") or ""),
        "links": links,
    }


def _tags(value: Any) -> list[str]:
    return [
        str(item).lstrip("#")
        for item in (value if isinstance(value, list) else re.split(r"[,\s]+", str(value or "")))
        if item
    ]


def ocr_pdf(data: bytes, language: str, cancel: Any) -> list[dict[str, Any]]:
    if not re.fullmatch(r"[a-z]{3}(?:\+[a-z]{3})*", language):
        raise ValueError("Invalid OCR language")
    with tempfile.TemporaryDirectory(prefix="raticode-ocr-") as name:
        folder = Path(name)
        (folder / "source.pdf").write_bytes(data)
        subprocess.run(
            [
                "pdftoppm",
                "-f",
                "1",
                "-l",
                "500",
                "-r",
                "150",
                "-png",
                str(folder / "source.pdf"),
                str(folder / "page"),
            ],
            check=True,
            capture_output=True,
            timeout=180,
        )
        blocks: list[dict[str, Any]] = []
        for file in sorted(folder.glob("page-*.png")):
            if cancel.is_set():
                raise InterruptedError("OCR cancelled")
            result = subprocess.run(
                ["tesseract", str(file), "stdout", "-l", language],
                check=True,
                capture_output=True,
                timeout=90,
            )
            page = int(file.stem.split("-")[-1])
            blocks += [
                {**block, "id": f"p{page}-{block['id']}", "page": page, "heading": f"Page {page}"}
                for block in blocks_for(result.stdout.decode("utf-8"))
            ]
        if not blocks:
            raise ValueError("OCR found no readable text")
        return blocks


def checked_archive(path: Path) -> tuple[dict[str, Any], list[zipfile.ZipInfo]]:
    if path.stat().st_size > MAX_PACKAGE:
        raise ValueError("Package exceeds 1 GB")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            raise ValueError("Package has too many entries")
        expanded = 0
        seen: set[str] = set()
        for entry in entries:
            name = safe_relative(entry.filename.rstrip("/"))
            if fold_name(name) in seen:
                raise ValueError("Package contains duplicate or ambiguous paths")
            seen.add(fold_name(name))
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise ValueError("Package contains links or special files")
            if entry.flag_bits & 1:
                raise ValueError("Encrypted packages are not supported")
            expanded += entry.file_size
            if expanded > MAX_PACKAGE or entry.file_size > (
                MAX_MANIFEST if name == "manifest.json" else MAX_FILE
            ):
                raise ValueError("Expanded package exceeds the size limits")
        manifest = archive.getinfo("manifest.json")
        if manifest.file_size > MAX_MANIFEST:
            raise ValueError("Package manifest is too large")
        payload = json.loads(archive.read(manifest))
        if (
            not isinstance(payload, dict)
            or payload.get("format") != "raticode-brain"
            or payload.get("version") != 1
        ):
            raise ValueError("Unsupported brain package format")
        if not isinstance(payload.get("library"), dict) or not isinstance(
            payload.get("files"), dict
        ):
            raise ValueError("Invalid package manifest")
        validate_portable(payload)
        for name, digest in payload["files"].items():
            safe_relative(name)
            if name not in {e.filename for e in entries} or not re.fullmatch(
                r"[a-f0-9]{64}", str(digest)
            ):
                raise ValueError("Invalid package file inventory")
        allowed = {"manifest.json", *payload["files"]}
        if any(e.filename not in allowed and not e.is_dir() for e in entries):
            raise ValueError("Package contains files outside its inventory")
        return payload, entries
