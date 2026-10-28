"""Source extraction and portable archive validation for Second Brain libraries."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tempfile
import zipfile
from html import escape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin

import yaml

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
    known = {"documents", "collections", "savedSearches", "reports", "citations", "jobs"}
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
        data = handle.read(limit + 1)
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


class ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0
        self.title_parts: list[str] = []
        self.in_title = False
        self.meta: dict[str, str] = {}
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"script", "style", "noscript", "template", "svg"}:
            self.hidden += 1
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            self.meta[str(values.get("name") or values.get("property") or "").lower()] = str(
                values.get("content") or ""
            )
        if tag == "a" and values.get("href"):
            self.links.append(str(values["href"]))
        if (
            tag in {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3", "h4", "tr"}
            and not self.hidden
        ):
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template", "svg"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        elif not self.hidden:
            self.parts.append(data)


def blocks_for(text: str) -> list[dict[str, Any]]:
    """Bounded passages, with offsets into normalized text and content-stable IDs."""
    blocks: list[dict[str, Any]] = []
    heading = "Document"
    counts: dict[str, int] = {}
    for match in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", text):
        paragraph = match.group().strip()
        if not paragraph:
            continue
        if re.match(r"^#{1,6}\s", paragraph):
            heading = re.sub(r"^#+\s*", "", paragraph.splitlines()[0])
        for offset in range(0, len(paragraph), 1600):
            passage = paragraph[offset : offset + 1600]
            digest = hashlib.sha256(passage.encode()).hexdigest()[:20]
            counts[digest] = counts.get(digest, 0) + 1
            blocks.append(
                {
                    "id": f"b-{digest}-{counts[digest]}",
                    "heading": heading,
                    "text": passage,
                    "start": match.start() + offset,
                    "end": match.start() + offset + len(passage),
                }
            )
    return blocks


def extract(
    data: bytes, suffix: str, *, encoding: str = "utf-8", obsidian: bool = True
) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("Unlock the encrypted PDF before indexing it")
        if len(reader.pages) > 500:
            raise ValueError("PDF exceeds the 500 page extraction limit")
        blocks = []
        total = 0
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            total += len(text.encode("utf-8"))
            if total > MAX_TEXT:
                raise ValueError("Extracted PDF text exceeds 2 MB")
            for block in blocks_for(text):
                blocks.append(
                    {
                        **block,
                        "id": f"p{index + 1}-{block['id']}",
                        "page": index + 1,
                        "heading": f"Page {index + 1}",
                    }
                )
        metadata: Any = reader.metadata
        return {
            "blocks": blocks,
            "extraction": "ready" if blocks else "ocr",
            "title": str((metadata or {}).get("/Title") or ""),
            "author": str((metadata or {}).get("/Author") or ""),
            "pageCount": len(reader.pages),
            "links": [],
        }
    text = data.decode(encoding, errors="strict")
    if len(text.encode("utf-8")) > MAX_TEXT:
        raise ValueError("Extracted text exceeds 2 MB")
    links: list[str] = []
    if suffix in {".html", ".htm"}:
        parser = ReadableHTML()
        parser.feed(text)
        text = "".join(parser.parts)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        meta = {
            "title": "".join(parser.title_parts).strip(),
            "author": parser.meta.get("author", ""),
            "published": parser.meta.get("article:published_time", ""),
            "url": parser.meta.get("og:url", ""),
        }
        links = parser.links
        if meta["url"]:
            links = [urljoin(meta["url"], link) for link in links]
    elif suffix in {".md", ".markdown"}:
        front = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.S)
        if front:
            loaded = yaml.safe_load(front.group(1))
            if isinstance(loaded, dict):
                meta = loaded
            text = text[front.end() :]
        if obsidian:
            links = re.findall(r"\[\[([^\]|#]+)(?:[^\]]*)\]\]", text)
            meta["tags"] = list(
                set(_tags(meta.get("tags")) + re.findall(r"(?<!\w)#([\w/-]+)", text))
            )
        links += re.findall(r"\[[^\]]*\]\(([^ )]+)", text)
        if not meta.get("title"):
            title = re.search(r"^#\s+(.+)$", text, re.M)
            meta["title"] = title.group(1) if title else ""
    return {
        "blocks": blocks_for(text),
        "extraction": "ready",
        "tags": _tags(meta.get("tags")),
        "title": str(meta.get("title") or ""),
        "author": str(meta.get("author") or ""),
        "published": str(meta.get("published") or meta.get("date") or ""),
        "project": str(meta.get("project") or ""),
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
            if name.casefold() in seen:
                raise ValueError("Package contains duplicate or ambiguous paths")
            seen.add(name.casefold())
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
