"""What Rem remembers: saving, correcting, choosing and undoing memories.

Memories live in Rem's own store and reach every provider as plain prompt text,
so none of this depends on a provider's memory feature.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from gofer.ui.rem_home_models import now

TEXT_LIMIT = 2000
# Prompts carry every memory until there are more than this many, or they get
# this long; then the ones that best match the request, newest first, are sent.
PROMPT_LIMIT = 40
PROMPT_CHARS = 12000
WORKER_LIMIT = 20
WORKER_CHARS = 6000
# on: use and save. paused: use what is saved, save nothing new. off: neither.
MODES = ("on", "paused", "off")
SECRET_REFUSED = (
    "That looks like a password, key or token. Rem doesn't keep those, because every "
    "memory is sent to the model provider."
)

_SECRET = re.compile(
    r"""
    \bsk-[A-Za-z0-9_-]{16,}
    | \bgh[pousr]_[A-Za-z0-9]{20,}
    | \bgithub_pat_[A-Za-z0-9_]{20,}
    | \bxox[abprs]-[A-Za-z0-9-]{10,}
    | \bAKIA[0-9A-Z]{16}\b
    | \bAIza[0-9A-Za-z_-]{35}
    | -----BEGIN\ [A-Z ]*PRIVATE\ KEY-----
    | \beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}
    | \b(?:password|passwd|passphrase|pass\ ?code|api[\ _-]?key|secret|token|pin)
      \s*(?:is|=|:)\s*(?:(?=\S*\d)\S{4,}|\S{12,})
    """,
    re.IGNORECASE | re.VERBOSE,
)
# A long run of mixed-case letters and digits is a key, not prose. Lowercase hex
# such as commit and file hashes stays allowed.
_TOKEN = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
_WORD = re.compile(r"[a-z0-9]{3,}")


def looks_secret(text: str) -> bool:
    if _SECRET.search(text):
        return True
    return any(
        re.search(r"[a-z]", t) and re.search(r"[A-Z]", t) and re.search(r"\d", t)
        for t in _TOKEN.findall(text)
    )


def mode(settings: dict[str, Any]) -> str:
    value = settings.get("mode")
    return value if value in MODES else "on"


def _saved_at(item: dict[str, Any]) -> str:
    return str(item.get("edited_at") or item.get("created_at") or "")


def choose(
    saved: dict[str, Any],
    query: str,
    *,
    limit: int = PROMPT_LIMIT,
    chars: int = PROMPT_CHARS,
) -> tuple[list[dict[str, Any]], int]:
    """The memories for one prompt and how many were left out.

    Everything goes while it fits. Past the limit, memories sharing words with
    the request come first, then the newest.
    """
    newest = sorted(saved.items(), key=lambda pair: _saved_at(pair[1]), reverse=True)
    entries = [
        {"id": identity, "text": str(item.get("text") or ""), "saved": _saved_at(item)[:10]}
        for identity, item in newest
    ]
    if len(entries) <= limit and sum(len(e["text"]) for e in entries) <= chars:
        return entries, 0
    words = set(_WORD.findall(query.lower()))
    ranked = sorted(
        enumerate(entries),
        key=lambda pair: (-len(words & set(_WORD.findall(pair[1]["text"].lower()))), pair[0]),
    )
    picked: list[dict[str, Any]] = []
    used = 0
    for _, entry in ranked:
        if len(picked) >= limit:
            break
        if used + len(entry["text"]) > chars:
            continue
        picked.append(entry)
        used += len(entry["text"])
    return picked, len(entries) - len(picked)


def for_prompt(saved: dict[str, Any], settings: dict[str, Any], query: str) -> dict[str, Any]:
    """Prompt fields for Rem: the chosen memories and the current memory mode."""
    current = mode(settings)
    if current == "off":
        return {"memory_mode": "off"}
    shown, left_out = choose(saved, query)
    fields: dict[str, Any] = {"memories": shown, "memory_mode": current}
    if left_out:
        fields["memories_not_shown"] = left_out
    return fields


def for_worker(saved: dict[str, Any], settings: dict[str, Any], query: str) -> list[str]:
    """Plain memory texts for a worker's background; none while memory is off."""
    if mode(settings) == "off":
        return []
    shown, _ = choose(saved, query, limit=WORKER_LIMIT, chars=WORKER_CHARS)
    return [entry["text"] for entry in shown]


def unknown_ids(changes: list[Any], saved: dict[str, Any]) -> list[str]:
    return [change.id for change in changes if change.id not in saved]


def apply(
    saved: dict[str, Any],
    added: list[str],
    changes: list[Any],
    settings: dict[str, Any],
    source_message_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one decision's memory changes and describe them for the reply.

    Returns the new memories and a record for the reply message, which shows
    what changed and lets the user undo it. Nothing is saved while memory is
    paused or off; an explicit correction or forget still applies while paused.
    """
    current = mode(settings)
    result = dict(saved)
    record: dict[str, list[dict[str, Any]]] = {"saved": [], "updated": [], "forgotten": []}
    refused = 0
    if current != "off":
        for change in changes:
            item = result.get(change.id)
            if item is None:
                continue
            if change.action == "forget":
                del result[change.id]
                record["forgotten"].append({"id": change.id, "text": item["text"], "item": item})
                continue
            text = (change.text or "").strip()
            if not text or text == item["text"]:
                continue
            if looks_secret(text):
                refused += 1
                continue
            result[change.id] = {**item, "text": text, "edited_at": now()}
            # "written" keeps the reply's own words; "text" follows later edits for display.
            record["updated"].append(
                {"id": change.id, "text": text, "written": text, "previous": item["text"]}
            )
    if current == "on":
        known = {str(item.get("text") or "").strip().casefold() for item in result.values()}
        for memory in added:
            text = memory.strip()
            if not text or len(text) > TEXT_LIMIT or text.casefold() in known:
                continue
            if looks_secret(text):
                refused += 1
                continue
            identity = str(uuid4())
            result[identity] = {
                "text": text,
                "source_message_id": source_message_id,
                "created_at": now(),
            }
            known.add(text.casefold())
            record["saved"].append({"id": identity, "text": text, "written": text})
    summary: dict[str, Any] = {key: value for key, value in record.items() if value}
    if refused:
        summary["refused"] = refused
    return result, summary


def undo(saved: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Reverse one reply's memory changes, leaving later edits by the user alone.

    A saved or updated memory whose words no longer match what the reply wrote
    was changed afterwards, so Undo keeps it as it is now.
    """
    result = dict(saved)
    for entry in record.get("saved", []):
        item = result.get(entry["id"])
        if item and item.get("text") == entry.get("written", entry.get("text")):
            del result[entry["id"]]
    for entry in record.get("updated", []):
        item = result.get(entry["id"])
        written = entry.get("written", entry.get("text"))
        if item and item.get("text") == written and entry.get("previous"):
            result[entry["id"]] = {**item, "text": entry["previous"], "edited_at": now()}
    for entry in record.get("forgotten", []):
        if entry["id"] not in result and isinstance(entry.get("item"), dict):
            result[entry["id"]] = entry["item"]
    return result


def scrub(record: dict[str, Any], identity: str, text: str | None) -> bool:
    """Make a reply's record match a memory the user edited (text) or forgot (None).

    A forgotten memory's words don't stay behind in the chat's memory notes.
    Returns whether the record changed.
    """
    changed = False
    for key in ("saved", "updated", "forgotten"):
        for entry in record.get(key, []):
            if entry.get("id") != identity:
                continue
            if text is None:
                for field in ("text", "written", "previous", "item"):
                    if field in entry:
                        entry.pop(field)
                        changed = True
                entry["removed"] = True
            elif entry.get("text") != text:
                if key != "forgotten":
                    # Undo compares against these words to leave the user's edit alone.
                    entry.setdefault("written", entry.get("text"))
                entry["text"] = text
                changed = True
    return changed


def phone_record(record: Any) -> dict[str, Any] | None:
    """The reply's memory note as the phone shows it: texts only, no stored items."""
    if not isinstance(record, dict):
        return None
    shown: dict[str, Any] = {}
    for key in ("saved", "updated", "forgotten"):
        entries = record.get(key)
        if isinstance(entries, list) and entries:
            shown[key] = [
                {
                    "id": str(e.get("id") or "")[:64],
                    "text": str(e.get("text") or "")[:TEXT_LIMIT],
                    "removed": e.get("removed") is True,
                }
                for e in entries[:10]
                if isinstance(e, dict)
            ]
    if not shown and not record.get("refused"):
        return None
    if isinstance(record.get("refused"), int):
        shown["refused"] = record["refused"]
    shown["undone"] = bool(record.get("undone_at"))
    return shown
