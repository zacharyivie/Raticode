"""Report families: documents that repeat most of each other's long passages.

A family is usually one report saved several times as it was revised. Raticode
suggests marking the older copy as replaced by the newest one. The user confirms
the suggestion, and the link is stored on the document record in the brain's
portable metadata. Report files are never edited.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Set
from datetime import date
from typing import Any

from gofer.ui.brain_origin import ORIGIN_IMPORTED

# A passage counts as long at this many words. Headings, labels and table cells
# are shorter and repeat across unrelated reports.
LONG_PASSAGE_WORDS = 25
# Two documents are related when they share more than this fraction of the long
# passages in the larger of the two, and at least MIN_SHARED of them.
FAMILY_SHARE = 0.5
MIN_SHARED = 2
# Passages found in more documents than this are boilerplate (a shared notice or
# template text), not evidence that two reports are versions of each other.
BOILERPLATE_DOCUMENTS = 6
CHECK_BY = re.compile(r"\d{4}-\d{2}-\d{2}")
# Search hits from Rem's conversation archive that nothing cites carry this source.
ARCHIVE_SOURCE = "conversation archive"


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold()))


def long_passages(doc: dict[str, Any]) -> set[str]:
    found = set()
    for block in doc.get("blocks", []):
        text = _normalize(str(block.get("text", "")))
        if len(text.split()) >= LONG_PASSAGE_WORDS:
            found.add(hashlib.blake2b(text.encode(), digest_size=12).hexdigest())
    return found


def eligible(doc: dict[str, Any]) -> bool:
    return not doc.get("missing") and not doc.get("excluded") and doc.get("extraction") == "ready"


def recency(doc: dict[str, Any]) -> tuple[str, str, str]:
    dates = doc.get("dates", {})
    return (str(dates.get("modified", "")), str(dates.get("added", "")), str(doc.get("path", "")))


def report_families(
    documents: list[dict[str, Any]], cache: dict[tuple[str, str], set[str]] | None = None
) -> list[dict[str, Any]]:
    """Group documents that share most of their long passages, per brain.

    Each family lists members newest first and carries at most one suggestion:
    mark the older member that shares most with the current (newest unreplaced)
    member as replaced by it.
    """
    passages: dict[str, set[str]] = {}
    for doc in documents:
        if not eligible(doc):
            continue
        key = (doc["id"], str(doc.get("version", "")))
        if cache is None or key not in cache:
            found = long_passages(doc)
            if cache is None:
                passages[doc["id"]] = found
                continue
            cache[key] = found
        passages[doc["id"]] = cache[key]
    if cache is not None and len(cache) > 4 * max(1, len(passages)) + 1000:
        live = {(doc["id"], str(doc.get("version", ""))) for doc in documents}
        for key in [key for key in cache if key not in live]:
            del cache[key]
    by_id = {doc["id"]: doc for doc in documents}
    holders: dict[tuple[str, str], list[str]] = {}
    for doc_id, hashes in passages.items():
        for digest in hashes:
            holders.setdefault((by_id[doc_id]["brainId"], digest), []).append(doc_id)
    shared: dict[tuple[str, str], int] = {}
    for ids in holders.values():
        if len(ids) < 2 or len(ids) > BOILERPLATE_DOCUMENTS:
            continue
        ids = sorted(ids)
        for index, left in enumerate(ids):
            for right in ids[index + 1 :]:
                shared[(left, right)] = shared.get((left, right), 0) + 1
    edges: dict[tuple[str, str], float] = {}
    for (left, right), count in shared.items():
        if by_id[left].get("version") == by_id[right].get("version"):
            continue  # Exact copies are reported as duplicates instead.
        fraction = count / max(len(passages[left]), len(passages[right]))
        if count >= MIN_SHARED and fraction > FAMILY_SHARE:
            edges[(left, right)] = fraction
    parent: dict[str, str] = {}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for left, right in edges:
        parent[find(left)] = find(right)
    groups: dict[str, list[str]] = {}
    for item in list(parent):
        groups.setdefault(find(item), []).append(item)
    families = []
    for members in groups.values():
        ordered = sorted(members, key=lambda doc_id: recency(by_id[doc_id]), reverse=True)

        def overlap(a: str, b: str) -> float:
            return edges.get((min(a, b), max(a, b)), 0.0)

        current = next((doc_id for doc_id in ordered if not by_id[doc_id].get("replacedBy")), None)
        suggestion = None
        if current:
            candidates = [
                doc_id
                for doc_id in ordered
                if doc_id != current
                and not by_id[doc_id].get("replacedBy")
                and overlap(doc_id, current)
                and recency(by_id[doc_id]) < recency(by_id[current])
            ]
            if candidates:
                older = max(candidates, key=lambda doc_id: overlap(doc_id, current))
                key = (min(older, current), max(older, current))
                suggestion = {
                    "olderId": older,
                    "newerId": current,
                    "share": round(overlap(older, current), 3),
                    "shared": shared[key],
                    "olderPassages": len(passages[older]),
                    "newerPassages": len(passages[current]),
                }
        family_id = hashlib.sha256("|".join(sorted(members)).encode()).hexdigest()[:16]
        families.append(
            {
                "id": f"family-{family_id}",
                "brainId": by_id[ordered[0]]["brainId"],
                "members": [
                    {
                        "docId": doc_id,
                        "share": round(
                            max(overlap(doc_id, other) for other in members if other != doc_id),
                            3,
                        ),
                        "replacedBy": (by_id[doc_id].get("replacedBy") or {}).get("docId", ""),
                    }
                    for doc_id in ordered
                ],
                "currentId": current or "",
                "suggestion": suggestion,
            }
        )
    families.sort(key=lambda family: recency(by_id[family["members"][0]["docId"]]), reverse=True)
    return families


def newest_replacement(doc: dict[str, Any], by_id: Mapping[str, dict[str, Any]]) -> list[str]:
    """The chain of newer reports after doc, ending at the one still current."""
    chain: list[str] = []
    seen = {doc["id"]}
    link = (doc.get("replacedBy") or {}).get("docId")
    while link and link in by_id and link not in seen:
        chain.append(link)
        seen.add(link)
        link = (by_id[link].get("replacedBy") or {}).get("docId")
    return chain


def shown_name(doc: dict[str, Any]) -> str:
    """A linked document's title as Rem sees it: its path when the document is imported.

    replacedBy and latest sit outside the attachment wrapper, so an imported document's
    title, which came from outside, isn't shown there.
    """
    return str(doc["path"] if doc.get("origin") == ORIGIN_IMPORTED else doc["title"])


def replacement_status(doc: dict[str, Any], by_id: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    """Status fields shared by Rem's search and read_note results."""
    status: dict[str, Any] = {"status": "current"}
    chain = newest_replacement(doc, by_id)
    if (doc.get("replacedBy") or {}).get("docId"):
        status["status"] = "replaced"
        if chain:
            newer = by_id[chain[0]]
            status["replacedBy"] = {
                "documentId": newer["id"],
                "title": shown_name(newer),
                "path": newer["path"],
            }
            if len(chain) > 1:
                latest = by_id[chain[-1]]
                status["latest"] = {
                    "documentId": latest["id"],
                    "title": shown_name(latest),
                    "path": latest["path"],
                }
        else:
            status["replacedBy"] = {
                "documentId": doc["replacedBy"]["docId"],
                "title": "",
                "path": "",
                "note": "The newer report is no longer in this brain.",
            }
    if doc.get("checkBy"):
        status["checkBy"] = doc["checkBy"]
    return status


def demote_replaced(
    hits: list[dict[str, Any]],
    replaced: Set[str],
    archived: Set[str] = frozenset(),
    lowered: Set[str] | Mapping[str, str] = frozenset(),
) -> list[dict[str, Any]]:
    """Keep the order of current reports and move replaced ones after them.

    Documents in a folder listed after other results (``lowered``) go after current
    ones and before replaced reports. Unused conversation archive files go after
    every other hit.
    """
    return sorted(
        hits,
        key=lambda hit: (
            hit["docId"] in archived,
            hit["docId"] in replaced,
            hit["docId"] in lowered,
        ),
    )


def check_by(value: Any) -> str:
    """Validate an optional check-by date (YYYY-MM-DD). An empty value clears it."""
    text = str(value or "").strip()
    if not text:
        return ""
    if not CHECK_BY.fullmatch(text):
        raise ValueError("Use YYYY-MM-DD for the check-by date")
    date.fromisoformat(text)
    return text


def check_due(doc: dict[str, Any], today: date) -> bool:
    value = doc.get("checkBy")
    if not value or doc.get("replacedBy") or doc.get("missing"):
        return False
    try:
        return date.fromisoformat(str(value)) <= today
    except ValueError:
        return False
