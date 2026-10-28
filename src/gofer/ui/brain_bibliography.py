"""Portable bibliography import and export without accessing external attachments."""

from __future__ import annotations

import json
import re
import threading
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gofer.ui.brain_documents import MAX_TEXT, safe_relative, source_path
from gofer.utils.atomic_output import atomic_binary_output, open_binary_input

if TYPE_CHECKING:
    from gofer.ui.brain_library import BrainLibrary


def date_parts(value: str) -> list[int]:
    match = re.match(r"^(\d{4})(?:[-/](\d{1,2}))?(?:[-/](\d{1,2}))?", value)
    return [int(part) for part in match.groups() if part] if match else []


def issued_date(item: dict[str, Any]) -> str:
    parts = item.get("issued", {}).get("date-parts", [[]])[0]
    return "-".join(str(part).zfill(2) if index else str(part) for index, part in enumerate(parts))


def records(data: str, suffix: str) -> list[dict[str, Any]]:
    if suffix == ".json":
        value = json.loads(data)
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError("Choose a CSL JSON array of bibliography records")
        return value
    if suffix == ".bib":
        import bibtexparser

        database = bibtexparser.loads(data)
        return [
            {
                "id": entry.get("ID"),
                "title": entry.get("title", ""),
                "author": [
                    {"literal": author.strip()}
                    for author in entry.get("author", "").split(" and ")
                    if author.strip()
                ],
                "URL": entry.get("url", ""),
                "DOI": entry.get("doi", ""),
                "issued": {"date-parts": [date_parts(entry.get("date") or entry.get("year", ""))]},
            }
            for entry in database.entries
        ]
    if suffix == ".ris":
        result = []
        item: dict[str, Any] = {}
        for line in data.splitlines():
            match = re.match(r"^([A-Z0-9]{2})\s*-\s?(.*)$", line)
            if not match:
                continue
            tag, text = match.groups()
            if tag == "ER":
                result.append(item)
                item = {}
            elif tag in {"TI", "T1"}:
                item["title"] = text
            elif tag in {"AU", "A1"}:
                item.setdefault("author", []).append({"literal": text})
            elif tag == "DO":
                item["DOI"] = text
            elif tag == "UR":
                item["URL"] = text
            elif tag in {"PY", "Y1"} and date_parts(text):
                item["issued"] = {"date-parts": [date_parts(text)]}
        return result
    raise ValueError("Choose CSL JSON, BibTeX or RIS")


def bibliography_operation(library: BrainLibrary, params: dict[str, Any]) -> dict[str, Any]:
    brain = library.brain(params["brainId"])
    if params.get("export"):
        target = Path(params["path"])
        if not target.is_absolute() or target.exists():
            raise ValueError("Choose a new absolute bibliography filename")
        items = []
        for doc in library.entities("documents", brain["id"]):
            item = doc.get("metadata", {}).get("bibliography") or {
                "id": doc["id"],
                "type": "article",
                "title": doc["title"],
                "author": [{"literal": doc["author"]}] if doc["author"] else [],
                "URL": doc["source"].get("url", ""),
                "DOI": doc["source"].get("doi", ""),
                "issued": {"date-parts": [date_parts(doc["dates"].get("published", ""))]},
            }
            items.append(item)
        suffix = target.suffix.lower()
        if suffix == ".json":
            content = json.dumps(items, indent=2, ensure_ascii=False)
        elif suffix == ".ris":
            content = "\n".join(
                "TY  - GEN\nTI  - "
                + item.get("title", "").replace("\n", " ")
                + "\n"
                + "\n".join(
                    "AU  - "
                    + author.get(
                        "literal", " ".join((author.get("given", ""), author.get("family", "")))
                    )
                    for author in item.get("author", [])
                )
                + "\nDO  - "
                + item.get("DOI", "")
                + "\nUR  - "
                + item.get("URL", "")
                + "\nPY  - "
                + issued_date(item)
                + "\nER  -\n"
                for item in items
            )
        elif suffix == ".bib":
            import bibtexparser

            database = bibtexparser.bibdatabase.BibDatabase()
            database.entries = [
                {
                    "ENTRYTYPE": "misc",
                    "ID": str(item.get("id") or f"ref{index}"),
                    "title": item.get("title", ""),
                    "author": " and ".join(
                        author.get(
                            "literal", " ".join((author.get("given", ""), author.get("family", "")))
                        )
                        for author in item.get("author", [])
                    ),
                    "doi": item.get("DOI", ""),
                    "url": item.get("URL", ""),
                    "year": issued_date(item)[:4],
                    "date": issued_date(item),
                }
                for index, item in enumerate(items)
            ]
            content = bibtexparser.dumps(database)
        else:
            raise ValueError("Use a .json, .bib or .ris export filename")
        target.parent.mkdir(parents=True, exist_ok=True)
        with atomic_binary_output(target, exclusive=True) as handle:
            handle.write(content.encode("utf-8"))
        return {"path": str(target), "records": len(items)}
    path = Path(params["path"])
    with open_binary_input(path) as handle:
        data = handle.read(MAX_TEXT + 1)
    if len(data) > MAX_TEXT:
        raise ValueError("Bibliography exceeds 2 MB")
    items = records(data.decode("utf-8-sig"), path.suffix.lower())
    root = Path(brain["root"])
    imported = []
    for index, item in enumerate(items):
        title = str(item.get("title") or f"Reference {index + 1}")
        # Imported citation records become searchable reference notes; original CSL is retained.
        relative = safe_relative(
            f"References/reference-{index + 1}-{__import__('uuid').uuid4().hex[:8]}.html"
        )
        target = source_path(root, relative)
        target.parent.mkdir(exist_ok=True)
        authors = "; ".join(
            author.get("literal", " ".join((author.get("given", ""), author.get("family", ""))))
            for author in item.get("author", [])
        )
        url = str(item.get("URL", ""))
        content = (
            f'<!doctype html><html><head><meta charset="utf-8"><meta '
            f'name="author" content="{escape(authors, quote=True)}"><meta '
            f'name="og:url" content="{escape(url, quote=True)}"><title>'
            f"{escape(title)}</title></head><body><h1>{escape(title)}</h1><p>"
            f"{escape(str(item.get('abstract', '')))}</p><p>DOI: "
            f"{escape(str(item.get('DOI', '')))}</p></body></html>"
        )
        with atomic_binary_output(target, exclusive=True) as handle:
            handle.write(content.encode("utf-8"))
        imported.append((relative, item))
    library.scan(brain["id"], threading.Event())
    docs = {doc["path"]: doc for doc in library.entities("documents", brain["id"])}
    for relative, item in imported:
        doc = docs[relative]
        doc["metadata"]["bibliography"] = item
        doc["source"]["doi"] = str(item.get("DOI", ""))
        doc["metadata"]["doi"] = doc["source"]["doi"]
        date = item.get("issued", {}).get("date-parts", [[]])[0]
        if date:
            doc["dates"]["published"] = "-".join(
                str(value).zfill(2) if index else str(value) for index, value in enumerate(date)
            )
            doc["metadata"]["published"] = doc["dates"]["published"]
        doc["origin"] = "imported"
        library.put("documents", doc)
    library._portable(brain["id"])
    return {"records": len(items)}
