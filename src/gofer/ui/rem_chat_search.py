"""Search Rem's saved chat history without a model.

The desktop chat and paired phones both search here, so every device finds the same
messages in the full history, including what a phone no longer holds.

A query is split into words, and a message matches when each word appears, ignoring
case, in its text, its folded details, its task card's title or the names of its
files. Matches are listed newest first with a short snippet split into plain and
matched parts, so each app highlights them without counting characters (Python,
JavaScript and Kotlin count characters differently).

The scan is bounded: SQLite first keeps only rows whose stored JSON holds every
plain ASCII word, which skips most of the history without parsing it, and only
those rows are read in Python. Words that JSON escapes or SQLite can't lowercase
skip that first pass and are checked in Python only.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any

MAX_QUERY = 200
MAX_TERMS = 8
MAX_RESULTS = 100
SNIPPET = 180
# How much of a snippet comes before its first match.
LEAD = 60

_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MARKUP = re.compile(r"(?m)^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)|[*_`~]{1,3}")
# Words SQLite can prefilter: printable ASCII that JSON stores as typed.
_PLAIN = re.compile(r"[\x20-\x7e]+")


def plain_text(text: Any) -> str:
    """Markdown as one plain line: link labels kept, markup and extra spaces dropped."""
    if not isinstance(text, str):
        return ""
    return " ".join(_MARKUP.sub("", _LINK.sub(r"\1", text)).split())


def query_terms(query: Any) -> list[str]:
    """The distinct words of a query, at most ``MAX_TERMS``, in typed order."""
    if not isinstance(query, str):
        return []
    seen: dict[str, str] = {}
    for word in query[:MAX_QUERY].split():
        seen.setdefault(word.casefold(), word)
    return list(seen.values())[:MAX_TERMS]


def _pattern(terms: list[str]) -> re.Pattern[str]:
    # Longest first, so "rem chat" highlights "chat" whole when "c" is also a term.
    ordered = sorted(terms, key=len, reverse=True)
    return re.compile("|".join(re.escape(t) for t in ordered), re.IGNORECASE)


def _parts(text: str, pattern: re.Pattern[str]) -> list[dict[str, Any]]:
    """``text`` split into plain and matched parts, in order."""
    parts: list[dict[str, Any]] = []
    at = 0
    for found in pattern.finditer(text):
        if found.start() == found.end():
            continue
        if found.start() > at:
            parts.append({"text": text[at : found.start()], "match": False})
        parts.append({"text": found.group(0), "match": True})
        at = found.end()
    if at < len(text):
        parts.append({"text": text[at:], "match": False})
    return parts


def _snippet(text: str, pattern: re.Pattern[str]) -> list[dict[str, Any]]:
    """About ``SNIPPET`` characters of ``text`` around its first match, cut at spaces."""
    found = pattern.search(text)
    start = max(0, (found.start() if found else 0) - LEAD)
    if start:
        space = text.find(" ", start, start + 20)
        start = space + 1 if space >= 0 else start
    end = min(len(text), start + SNIPPET)
    if end < len(text):
        space = text.rfind(" ", end - 20, end)
        end = space if space > start else end
    piece = text[start:end]
    return _parts(("…" if start else "") + piece + ("…" if end < len(text) else ""), pattern)


def _fields(message: dict[str, Any]) -> list[tuple[str, str]]:
    """What a message is searched by, each with where it shows: text, task title or file."""
    fields = [("message", plain_text(message.get("body")))]
    details = plain_text(message.get("details"))
    if details:
        fields.append(("details", details))
    work = message.get("work")
    if isinstance(work, dict) and isinstance(work.get("title"), str):
        fields.append(("task", work["title"]))
    result = message.get("result")
    if isinstance(result, dict) and isinstance(result.get("title"), str):
        fields.append(("result", result["title"]))
    for key in ("attachments", "files"):
        for item in message.get(key) or []:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                fields.append(("file", item["name"]))
    return [(where, text) for where, text in fields if text]


def _matches(fields: list[tuple[str, str]], terms: list[str]) -> bool:
    folded = "\n".join(text for _, text in fields).casefold()
    return all(term.casefold() in folded for term in terms)


def _duplicate_notice(db: sqlite3.Connection, message: dict[str, Any]) -> bool:
    """Whether the chat leaves this legacy "Started <task>." notice out (``duplicate_notices``)."""
    work = message.get("work") or {}
    run = str(work.get("run_id") or "")
    if not run or message.get("id") != f"queued-{run}":
        return False
    if message.get("body") != f"Started {work.get('title')}.":
        return False
    try:
        written = datetime.fromisoformat(str(message.get("created_at")))
    except ValueError:
        return False
    for (body,) in db.execute(
        "SELECT body FROM messages WHERE json_extract(body, '$.work.run_id') = ?", (run,)
    ):
        other = json.loads(body)
        if other.get("id") == message.get("id") or other.get("kind") == "task_update":
            continue
        try:
            reply = datetime.fromisoformat(str(other.get("created_at")))
        except ValueError:
            continue
        if abs(reply - written) < timedelta(seconds=5):
            return True
    return False


def search_messages(
    db: sqlite3.Connection, query: Any, *, before: int | None = None, limit: int = 50
) -> dict[str, Any]:
    """Messages matching every word of ``query``, newest first.

    ``total`` counts every match in the history. ``results`` holds at most ``limit`` of
    them stored before position ``before`` (all of them without it); ``next`` is the
    position to pass as ``before`` for the next page, or null after the last one.
    """
    terms = query_terms(query)
    limit = max(1, min(int(limit), MAX_RESULTS))
    if not terms:
        return {"query": "", "terms": [], "total": 0, "results": [], "next": None}
    where, args = [], []
    for term in terms:
        if _PLAIN.fullmatch(term) and '"' not in term and "\\" not in term:
            where.append("instr(lower(body), ?) > 0")
            args.append(term.lower())
    sql = "SELECT seq, body FROM messages"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY seq DESC"
    pattern = _pattern(terms)
    total = 0
    results: list[dict[str, Any]] = []
    following: int | None = None
    for seq, body in db.execute(sql, args):
        message = json.loads(body)
        fields = _fields(message)
        if not _matches(fields, terms) or _duplicate_notice(db, message):
            continue
        total += 1
        if before is not None and seq >= before:
            continue
        if len(results) == limit:
            following = following or results[-1]["seq"]
            continue
        where_found, text = next(
            ((w, t) for w, t in fields if pattern.search(t)), fields[0] if fields else ("", "")
        )
        work = message.get("work") if isinstance(message.get("work"), dict) else {}
        title = work.get("title") if isinstance(work.get("title"), str) else ""
        item: dict[str, Any] = {
            "id": str(message.get("id") or ""),
            "seq": int(seq),
            "role": "user" if message.get("role") == "user" else "rem",
            "created_at": str(message.get("created_at") or ""),
            "where": where_found,
            "snippet": _snippet(text, pattern),
        }
        if message.get("kind") == "task_update":
            item["kind"] = "task_update"
        if title:
            item["title"] = _parts(title[:160], pattern)
        results.append(item)
    return {
        "query": " ".join(terms),
        "terms": terms,
        "total": total,
        "results": results,
        "next": following,
    }
