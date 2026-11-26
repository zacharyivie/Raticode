"""Second Brain query syntax and the search predicates built on it.

``parse_query`` mirrors ``parseQuery`` in ``frontend/src/lib/secondBrain.js``. Both
run the cases in ``tests/fixtures/brain-query-syntax.json``, so they can't drift.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Any

# Field names in the order the app lists them, and the filter each one sets.
FIELDS = {
    "type": "types",
    "tag": "tags",
    "author": "authors",
    "domain": "domains",
    "project": "projects",
    "folder": "folder",
    "in": "collection",
    "state": "reading",
    "origin": "origins",
    "brain": "brains",
    "modified": "date",
    "added": "date",
    "published": "date",
    "captured": "date",
    "generated": "date",
}
QUERY_FIELDS = list(FIELDS)
# JavaScript's \s, so words split exactly where the app splits them.
_SPACE = (
    "\t\n\v\f\r \u00a0\u1680"
    + "".join(chr(code) for code in range(0x2000, 0x200B))
    + "\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_CLAUSE = re.compile(rf'(-?)(?:([a-zA-Z]+):)?(?:"([^"]*)("?)|([^{_SPACE}]+))')
_DATE = re.compile(r"([0-9]{4})(?:-([0-9]{2}))?(?:-([0-9]{2}))?")
FIELD_LIST = ", ".join(f"{field}:" for field in QUERY_FIELDS)


def date_bound(value: str, end: bool = False) -> str:
    if not value:
        return ""
    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        raise ValueError("Invalid date in saved search")
    parts = [int(part) for part in value.split("-")]
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 12 if end else 1
    day = parts[2] if len(parts) > 2 else calendar.monthrange(year, month)[1] if end else 1
    return date(year, month, day).isoformat()


def parse_date_bound(value: str, end: bool = False) -> str | None:
    """YYYY, YYYY-MM or YYYY-MM-DD as an inclusive ISO day; "" when blank, None when invalid."""
    text = str(value or "").strip(_SPACE)
    if not text:
        return ""
    match = _DATE.fullmatch(text)
    if not match:
        return None
    year, month, day = int(match[1]), match[2], match[3]
    try:
        if day:
            return date(year, int(month), int(day)).isoformat()
        if month:
            number = int(month)
            last = calendar.monthrange(year, number)[1] if 1 <= number <= 12 else 0
            return date(year, number, last if end else 1).isoformat()
        return date(year, 12, 31).isoformat() if end else date(year, 1, 1).isoformat()
    except ValueError:
        return None


def parse_query(text: str = "") -> dict[str, Any]:
    """Words, "phrases", -exclusions, OR and field:value clauses, as the app reads them.

    Invalid clauses are listed in ``errors`` with their position and never applied.
    """
    result: dict[str, Any] = {
        "terms": [],
        "phrases": [],
        "exclude": [],
        "any": False,
        "filters": {},
        "date": None,
        "clauses": [],
        "errors": [],
    }
    for match in _CLAUSE.finditer(str(text)):
        raw = match[0]
        negated, field, quoted, closed, bare = match.groups()
        start, end = match.start(), match.end()
        value = quoted if quoted is not None else bare

        def error(message: str, start: int = start, end: int = end, raw: str = raw) -> None:
            result["errors"].append({"start": start, "end": end, "text": raw, "message": message})

        if quoted is not None and not closed:
            error("Close the quotation mark to search for this phrase.")
            continue
        if not field and not negated and bare == "OR":
            result["any"] = True
            continue
        if field:
            key = FIELDS.get(field.lower())
            if not key:
                error(f"“{field}:” is not a search field. Use one of {FIELD_LIST}")
                continue
            if negated:
                error(f"“-{field}:” can't exclude a field. Remove the minus sign.")
                continue
            if not value:
                error(f"Add a value after “{field}:”.")
                continue
            if key == "date":
                bounds = value.split("..")
                low = bounds[0]
                high = bounds[1] if len(bounds) > 1 else low
                found = {
                    "field": field.lower(),
                    "from": parse_date_bound(low),
                    "to": parse_date_bound(high, True),
                }
                if (
                    found["from"] is None
                    or found["to"] is None
                    or not (found["from"] or found["to"])
                ):
                    error(f"“{value}” is not a date range. Use 2026, 2026-03 or 2025..2026-06.")
                    continue
                result["date"] = found
            elif key in {"folder", "collection"}:
                result["filters"][key] = value
            else:
                result["filters"].setdefault(key, []).append(value.lower())
            result["clauses"].append(
                {"start": start, "end": end, "field": key, "value": value, "negated": False}
            )
            continue
        if not value:
            continue
        if negated:
            result["exclude"].append(value.lower())
        elif quoted is not None:
            result["phrases"].append(value.lower())
        else:
            result["terms"].append(value.lower())
    return result


def query_error(parsed: dict[str, Any]) -> str:
    """One message for every clause the parser refused, or "" when there are none."""
    return " ".join(f"{error['text']}: {error['message']}" for error in parsed.get("errors", []))


class QuerySyntaxError(ValueError):
    """A query clause the parser refused, such as an unknown field or a bad date."""


def checked_query(text: str) -> dict[str, Any]:
    """The parsed query; raises QuerySyntaxError naming each invalid clause."""
    parsed = parse_query(text)
    problem = query_error(parsed)
    if problem:
        raise QuerySyntaxError(f"The search query has clauses that can't be used. {problem}")
    return parsed


def semantic_text(text: str) -> str:
    """Words and phrases as typed, without field clauses, exclusions or OR."""
    return " ".join(
        quoted if quoted is not None else bare
        for negated, field, quoted, closed, bare in (
            match.groups() for match in _CLAUSE.finditer(str(text))
        )
        if not field
        and not negated
        and (quoted is None or closed)
        and (quoted or bare)
        and bare != "OR"
    )


def semantic_query(saved: dict[str, Any]) -> str:
    return semantic_text(str(saved.get("text", "")))


def scope_brains(saved: dict[str, Any], brains: list[dict[str, Any]], default: str) -> list[str]:
    selected = [
        str(value).casefold()
        for value in [
            *saved.get("filters", {}).get("brains", []),
            *parse_query(str(saved.get("text", "")))["filters"].get("brains", []),
        ]
    ]
    if not selected:
        return [default]
    return [
        brain["id"]
        for brain in brains
        if brain["id"].casefold() in selected or brain["name"].casefold() in selected
    ]


def needs_library(parsed: dict[str, Any], saved: dict[str, Any] | None = None) -> set[str]:
    """Which library records (collections, citations, reports) matching will read."""
    filters = {**(saved or {}).get("filters", {}), **parsed["filters"]}
    kinds: set[str] = set()
    if filters.get("collection"):
        kinds.add("collections")
    if {"highlighted", "annotated"} & set(filters.get("reading", [])):
        kinds.add("citations")
    if filters.get("citations") or (saved or {}).get("searchIn") == "citations":
        kinds.update({"citations", "reports"})
    return kinds


def saved_matches(
    doc: dict[str, Any],
    saved: dict[str, Any],
    library: dict[str, Any],
    related_ids: set[str] | None = None,
) -> bool:
    return query_matches(
        doc, checked_query(str(saved.get("text", ""))), saved, library, related_ids
    )


def query_matches(
    doc: dict[str, Any],
    parsed: dict[str, Any],
    saved: dict[str, Any],
    library: dict[str, Any],
    related_ids: set[str] | None = None,
    *,
    words: bool = True,
) -> bool:
    """Whether a document passes a parsed query and a saved search's visual filters.

    ``words=False`` leaves plain words out, for callers whose word index already
    ranked them, and checks only fields, phrases and exclusions.
    """
    from gofer.ui.brain_research import saved_search_matches

    filters = {**saved.get("filters", {})}
    for key in ("from", "to"):
        if filters.get(key):
            filters[key] = date_bound(filters[key], key == "to")
    for key, value in parsed["filters"].items():
        if key in {"folder", "collection"}:
            filters[key] = value
        else:
            filters[key] = [*filters.get(key, []), *value]
    if parsed["date"]:
        found = parsed["date"]
        filters.update(dateField=found["field"], **{"from": found["from"], "to": found["to"]})
    terms = parsed["terms"] if words else []
    excluded, phrases = parsed["exclude"], parsed["phrases"]
    any_words = saved.get("match") == "any" or parsed["any"]
    if doc.get("excluded") or doc.get("missing"):
        return False
    if filters.get("brains"):
        wanted = {str(value).casefold() for value in filters["brains"]}
        names = {brain["id"]: brain["name"].casefold() for brain in library.get("brains", [])}
        if doc["brainId"].casefold() not in wanted and names.get(doc["brainId"]) not in wanted:
            return False
    if not saved_search_matches(doc, {"text": "", "filters": filters}):
        return False
    collection = filters.get("collection")
    if collection:
        selected = next(
            (
                item
                for item in library.get("collections", [])
                if item["id"] == collection or item["name"].casefold() == collection.casefold()
            ),
            None,
        )
        if not selected or doc["id"] not in {
            item["docId"] for section in selected["sections"] for item in section["items"]
        }:
            return False
    citations = [
        citation for citation in library.get("citations", []) if citation["docId"] == doc["id"]
    ]
    reports = [
        report
        for report in library.get("reports", [])
        if any(citation["docId"] == doc["id"] for citation in report["citations"])
    ]
    for label in filters.get("citations", []):
        if label == "has-source" and not any(doc["source"].get(key) for key in ("url", "doi")):
            return False
        if label == "cites" and not doc.get("cites"):
            return False
        if label == "cited" and not reports:
            return False
        if label == "needs-review" and not any(
            citation["state"] in {"changed", "relocate", "unavailable", "unreviewed"}
            for report in reports
            for citation in report["citations"]
            if citation["docId"] == doc["id"]
        ):
            return False
    for state in filters.get("reading", []):
        if state == "highlighted" and not citations:
            return False
        if state == "annotated" and not (citations or doc.get("reading", {}).get("annotations")):
            return False
    if not (terms or excluded or phrases):
        return related_ids is None or doc["id"] in related_ids or saved.get("mode") != "semantic"
    search_in = saved.get("searchIn", "all")
    text = " ".join(
        ([doc["title"], doc["path"]] if search_in in {"all", "title"} else [])
        + ([block["text"] for block in doc["blocks"]] if search_in in {"all", "content"} else [])
        + ([citation["quote"] for citation in citations] if search_in == "citations" else [])
    ).casefold()
    if any(word in text for word in excluded) or any(phrase not in text for phrase in phrases):
        return False
    if related_ids is not None:
        if doc["id"] in related_ids:
            return True
        if saved.get("mode") == "semantic":
            return False
    if saved.get("match") == "phrase" and terms:
        return " ".join(terms) in text
    return (
        (any(term in text for term in terms) if any_words else all(term in text for term in terms))
        if terms
        else True
    )
