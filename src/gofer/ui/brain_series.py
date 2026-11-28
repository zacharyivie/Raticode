"""Dated report series: editions of one report saved under one name with different dates.

Documents in one brain belong to a series when their file names match once a trailing
ISO date (and an optional -vN after it) is removed, the remaining stem has at least two
words, at least two documents share it and at least one of their names carries a date.
An edition's date comes from its file name, or from its modified date when the name has
none. Older editions stay current: they are dated records, not replaced versions.

Search keeps the score order between different documents and lists the matching
editions of a series together at the best-scoring edition's position, newest first.
A query that names a date or filters by date keeps the plain score order.

The user can mark a detected group "Not a series". The mark is stored as ``notSeries``
on the member document records, which the brain's portable metadata carries; a later
edition with the same stem joins the marked group.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import date
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from gofer.ui.brain_library import evidence_references

if TYPE_CHECKING:
    from gofer.ui.brain_library import BrainLibrary

# A trailing ISO date, then an optional version number, at the end of a file stem.
_EDITION = re.compile(
    r"^(?P<stem>.*?)(?:[\s._-]*(?P<date>\d{4}-\d{2}-\d{2}))?(?:[\s._-]*v(?P<version>\d{1,4}))?$",
    re.IGNORECASE,
)
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_TRIM = re.compile(r"[\s._-]+$")
MIN_STEM_WORDS = 2
MIN_EDITIONS = 2

_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_SHORT_MONTHS = "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
# Spellings of a date in a search query. A bare month name counts only when it can't be
# an ordinary word ("may", "march").
_DATE_IN_QUERY = re.compile(
    r"(?<!\w)-?(?:modified|added|published|captured|generated):"
    r"|\b\d{4}-\d{1,2}(?:-\d{1,2})?\b"
    r"|\b\d{1,4}/\d{1,2}(?:/\d{1,4})?\b"
    r"|\b(?:19|20)\d{2}\b"
    rf"|\b(?:{_MONTHS}|{_SHORT_MONTHS})\.?\s+\d{{1,4}}(?:st|nd|rd|th)?\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTHS}|{_SHORT_MONTHS})\b"
    r"|\b(?:january|february|april|june|july|august|september|october|november|december)\b",
    re.IGNORECASE,
)


def names_date(query: str) -> bool:
    """Whether a search query names a date or uses a date filter such as modified:."""
    return bool(_DATE_IN_QUERY.search(query))


def parse_edition(path: str) -> dict[str, Any] | None:
    """The series stem and edition parts of a file name, or None for a one-word stem."""
    name = PurePosixPath(path).name
    stem_text = name.rsplit(".", 1)[0] if "." in name.lstrip(".") else name
    found = _EDITION.match(stem_text)
    if found is None:  # pragma: no cover - the pattern matches any string
        return None
    stem, raw_date, version = found.group("stem"), found.group("date"), found.group("version")
    if raw_date:
        try:
            date.fromisoformat(raw_date)
        except ValueError:
            # Not a real date, so it stays part of the name.
            stem = re.sub(r"[\s._-]*v\d{1,4}$", "", stem_text, flags=re.IGNORECASE)
            raw_date = None
    words = _WORDS.findall(stem.casefold())
    if len(words) < MIN_STEM_WORDS:
        return None
    return {
        "key": " ".join(words),
        "stem": _TRIM.sub("", stem) or stem,
        "nameDate": raw_date or "",
        "version": int(version) if version else 0,
    }


def _eligible(doc: Mapping[str, Any]) -> bool:
    return not doc.get("missing") and not doc.get("excluded")


def detect_series(documents: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every dated series per brain, editions newest first.

    Groups the user marked "Not a series" are listed too, with ``notSeries`` set, so
    Library health can offer to undo the mark. Search ignores them.
    """
    groups: dict[tuple[str, str], list[tuple[Mapping[str, Any], dict[str, Any]]]] = {}
    for doc in documents:
        if not _eligible(doc):
            continue
        edition = parse_edition(str(doc.get("path", "")))
        if edition is None:
            continue
        groups.setdefault((str(doc["brainId"]), edition["key"]), []).append((doc, edition))
    found: list[dict[str, Any]] = []
    for (brain_id, key), members in groups.items():
        if len(members) < MIN_EDITIONS or not any(edition["nameDate"] for _, edition in members):
            continue
        editions = []
        for doc, edition in members:
            modified = str((doc.get("dates") or {}).get("modified", ""))
            editions.append(
                {
                    "docId": doc["id"],
                    "path": doc.get("path", ""),
                    "date": edition["nameDate"] or modified[:10],
                    "dateFrom": "name" if edition["nameDate"] else "modified",
                    "version": edition["version"],
                    "_order": (
                        edition["nameDate"] or modified[:10],
                        edition["version"],
                        modified,
                        str(doc.get("path", "")),
                    ),
                    "_stem": edition["stem"],
                    "_not": bool(doc.get("notSeries")),
                }
            )
        editions.sort(key=lambda item: item["_order"], reverse=True)
        stem = editions[0]["_stem"]
        not_series = any(item["_not"] for item in editions)
        for item in editions:
            for private in ("_order", "_stem", "_not"):
                del item[private]
        digest = hashlib.sha256(f"{brain_id}\0{key}".encode()).hexdigest()[:16]
        found.append(
            {
                "id": f"series-{digest}",
                "brainId": brain_id,
                "key": key,
                "stem": stem,
                "editions": editions,
                "newestId": editions[0]["docId"],
                "notSeries": not_series,
            }
        )
    found.sort(key=lambda series: (series["notSeries"], series["stem"].casefold()))
    return found


def series_index(series: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Document ID to its place in a series, for series the user didn't mark."""
    index: dict[str, dict[str, Any]] = {}
    for group in series:
        if group["notSeries"]:
            continue
        for rank, edition in enumerate(group["editions"]):
            index[edition["docId"]] = {
                "seriesId": group["id"],
                "rank": rank,
                "stem": group["stem"],
                "editionDate": edition["date"],
                "editions": len(group["editions"]),
                "newestDocumentId": group["newestId"],
            }
    return index


def series_field(place: Mapping[str, Any]) -> dict[str, Any]:
    """The ``series`` field on a search hit."""
    return {
        "stem": place["stem"],
        "editionDate": place["editionDate"],
        "editions": place["editions"],
        "newestDocumentId": place["newestDocumentId"],
    }


def group_series(
    hits: Sequence[dict[str, Any]], index: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Move matching editions of a series to the best-scoring edition's position.

    Hits are passages in score order. Hits of other documents keep their order. When
    two or more editions of one series match, all their hits move to where the first
    of them was, newest edition first; each edition's hits keep their own order.
    """
    anchors: dict[str, int] = {}
    matched: dict[str, set[str]] = {}
    for position, hit in enumerate(hits):
        place = index.get(hit["docId"])
        if place:
            anchors.setdefault(place["seriesId"], position)
            matched.setdefault(place["seriesId"], set()).add(hit["docId"])
    moving = {series_id for series_id, docs in matched.items() if len(docs) > 1}
    if not moving:
        return list(hits)

    def order(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
        position, hit = item
        place = index.get(hit["docId"])
        if place and place["seriesId"] in moving:
            return (anchors[place["seriesId"]], int(place["rank"]), position)
        return (position, 0, position)

    return [hit for _, hit in sorted(enumerate(hits), key=order)]


def library_series_index(
    library: BrainLibrary, brain_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Each document's place in a dated report series, remembered for one revision.

    Series come from file names and modified dates in the document headers, and the
    user's "Not a series" marks, which are all written through the library.
    """
    key = tuple(brain_ids)
    revision = library.revision()
    cached = library._series.get(key)
    if cached and cached[0] == revision:
        return cached[1]
    index = series_index(detect_series(library.document_headers(brain_ids).values()))
    library._series[key] = (revision, index)
    return index


def mark_series(library: BrainLibrary, series_id: str, not_series: bool) -> dict[str, Any]:
    """Mark a detected series "Not a series", or undo that mark.

    The mark is stored on every member's document record and written to the brain's
    portable metadata. Files are not changed.
    """

    def mark(doc: dict[str, Any]) -> None:
        if not_series:
            doc["notSeries"] = True
        else:
            doc.pop("notSeries", None)

    with library.lock, library.write_transaction():
        found = detect_series(library.entities("documents"))
        group = next((item for item in found if item["id"] == series_id), None)
        if group is None:
            raise ValueError("That report series is no longer in the library")
        brain = library.brain(group["brainId"])
        if not Path(brain["root"]).is_dir():
            raise ValueError("Brain folder is unavailable")
        members = library.document_ids(
            (edition["docId"] for edition in group["editions"]), [group["brainId"]]
        )
        for doc_id in members:
            library.edit_document(doc_id, mark)
        library._portable(group["brainId"])
        return {**group, "notSeries": not_series}


def unused_archive(library: BrainLibrary, brain_ids: Sequence[str]) -> set[str]:
    """``unused_archive_documents`` for every document, remembered for one revision.

    Document headers are read only when a selected brain has a conversation archive.
    Archive files reach the index only through a scan, which moves the revision.
    """
    key = tuple(brain_ids)
    revision = library.revision()
    cached = library._unused_archive.get(key)
    if cached and cached[0] == revision:
        return set(cached[1])

    def headers() -> Iterator[dict[str, Any]]:
        yield from library.document_headers(brain_ids).values()

    archived = library.unused_archive_documents(list(brain_ids), headers())
    library._unused_archive[key] = (revision, frozenset(archived))
    return archived


def unused_archive_documents(
    library: BrainLibrary, brain_ids: list[str], documents: Iterable[dict[str, Any]]
) -> set[str]:
    """Files in Rem's conversation archive that no report, citation or collection uses.

    Search ranks these after every other hit. Nothing leaves the index, so a reference
    this check misses only moves a cited file down; it never hides or breaks it.
    """
    from gofer.ui.brain_graph import conversation_archives

    folders: dict[str, set[str]] = {}
    for brain_id in brain_ids:
        found = {path.name for path in conversation_archives(Path(library.brain(brain_id)["root"]))}
        if found:
            folders[brain_id] = found
    if not folders:
        return set()
    archived = {
        doc["id"]
        for doc in documents
        if doc["path"].split("/", 1)[0] in folders.get(doc["brainId"], ())
    }
    if not archived:
        return set()
    evidence = {kind: library.entities(kind) for kind in ("citations", "reports", "collections")}
    used = {ref["docId"] for ref in evidence_references(evidence)}
    used |= {
        item["docId"] for report in evidence["reports"] for item in report.get("sourceVersions", [])
    }
    return archived - used
