"""Shared search predicates for persisted research-job scopes."""

from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Any

ALIASES = {
    "type": "types",
    "tag": "tags",
    "author": "authors",
    "domain": "domains",
    "project": "projects",
    "origin": "origins",
    "state": "reading",
    "folder": "folder",
    "in": "collection",
    "brain": "brains",
}
DATES = {"added", "modified", "published", "captured", "generated"}


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


def clauses_for(saved: dict[str, Any]) -> list[str]:
    return re.findall(r'(?:[^\s"\']+|"[^"]*")+', str(saved.get("text", "")))


def semantic_query(saved: dict[str, Any]) -> str:
    return " ".join(
        token.strip('"')
        for token in clauses_for(saved)
        if ":" not in token and not token.startswith("-") and token.upper() != "OR"
    )


def scope_brains(saved: dict[str, Any], brains: list[dict[str, Any]], default: str) -> list[str]:
    selected = list(saved.get("filters", {}).get("brains", []))
    selected += [
        token.split(":", 1)[1].strip('"')
        for token in clauses_for(saved)
        if token.casefold().startswith("brain:")
    ]
    if not selected:
        return [default]
    return [
        brain["id"]
        for brain in brains
        if brain["id"] in selected
        or brain["name"].casefold() in [str(value).casefold() for value in selected]
    ]


def saved_matches(
    doc: dict[str, Any],
    saved: dict[str, Any],
    library: dict[str, Any],
    related_ids: set[str] | None = None,
) -> bool:
    from gofer.ui.brain_research import saved_search_matches

    clauses = clauses_for(saved)
    filters = {**saved.get("filters", {})}
    for key in ("from", "to"):
        if filters.get(key):
            filters[key] = date_bound(filters[key], key == "to")
    terms, excluded, phrases = [], [], []
    any_words = saved.get("match") == "any"
    for token in clauses:
        if token.upper() == "OR":
            any_words = True
            continue
        if token.startswith("-"):
            excluded.append(token[1:].strip('"').casefold())
            continue
        if ":" in token:
            field, value = token.split(":", 1)
            field = field.casefold()
            value = value.strip('"')
            if field in DATES:
                bounds = value.split("..", 1)
                filters.update(
                    dateField=field,
                    **{"from": date_bound(bounds[0]), "to": date_bound(bounds[-1], True)},
                )
            elif field in ALIASES:
                key = ALIASES[field]
                if key in {"folder", "collection"}:
                    filters[key] = value
                else:
                    filters[key] = list(set(filters.get(key, []) + [value]))
            else:
                raise ValueError(f"Unknown search field: {field}")
        elif token.startswith('"'):
            phrases.append(token.strip('"').casefold())
        else:
            terms.append(token.casefold())
    if doc.get("excluded") or doc.get("missing"):
        return False
    if filters.get("brains") and doc["brainId"] not in filters["brains"]:
        names = {brain["id"]: brain["name"].casefold() for brain in library.get("brains", [])}
        if names.get(doc["brainId"]) not in [str(value).casefold() for value in filters["brains"]]:
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
