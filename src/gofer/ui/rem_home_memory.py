"""What Rem remembers: saving, correcting, choosing and undoing memories.

Memories live in Rem's own store and reach every provider as plain prompt text,
so none of this depends on a provider's memory feature.

A memory may belong to a project (its ``project`` field holds the project root).
Global memories go to every prompt; a project's memories go only to work in that
project. Memories saved before projects existed have no field and stay global.

A memory may also have an end (its ``until`` field: an ISO instant with a
timezone). After it, the memory is no longer sent in prompts or worker
background, but it stays saved, shown as ended, until the user keeps or forgets it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time
from pathlib import PurePath
from typing import Any
from uuid import uuid4

from gofer.ui.rem_home_models import now
from gofer.utils.instants import aware_instant

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


UNTIL_LABEL = "Memory end date"
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_until(value: Any) -> str:
    """A memory's end as it is stored: an ISO instant with its timezone, still ahead.

    A plain date (``YYYY-MM-DD``) means the end of that day in the user's timezone;
    a date and time must name its timezone, the same check organization memories use.
    Raises ValueError for anything else, or for an end that has already passed.
    """
    text = str(value or "").strip()
    if _DAY.fullmatch(text):
        from gofer.ui.rem_self_work_windows import zone

        try:
            day = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{UNTIL_LABEL} must be a real date") from exc
        text = datetime.combine(day, time(23, 59, 59), tzinfo=zone(None)).isoformat()
    return aware_instant(text, UNTIL_LABEL, future=True).isoformat()


def until_of(item: dict[str, Any]) -> datetime | None:
    """When a memory stops applying, or None when it has no end (or an unreadable one)."""
    value = item.get("until")
    if not isinstance(value, str) or not value:
        return None
    try:
        end = datetime.fromisoformat(value)
    except ValueError:
        return None
    return end if end.tzinfo is not None else None


def ended(item: dict[str, Any], at: datetime | None = None) -> bool:
    """Whether a memory's end date has passed. Ended memories are kept, never sent."""
    end = until_of(item)
    return end is not None and end <= (at or datetime.now(UTC))


def project_of(item: dict[str, Any]) -> str | None:
    """The project root a memory belongs to, or None for a global memory."""
    root = item.get("project")
    return root if isinstance(root, str) and root else None


def in_project(root: str, place: str) -> bool:
    """Whether work in folder [place] is in the project at [root]: the root or inside it."""
    project, folder = PurePath(root), PurePath(place)
    return folder == project or project in folder.parents


def project_name(root: str, projects: Iterable[dict[str, Any]] = ()) -> str:
    """The project's name from the window's list, else its folder name."""
    for project in projects:
        if project.get("root") == root and str(project.get("name") or "").strip():
            return str(project["name"]).strip()
    return PurePath(root).name or root


def project_for(text: str, projects: Sequence[dict[str, Any]]) -> str | None:
    """The one project in [projects] that [text] names, by name or root; None otherwise.

    A fact naming no project, or several, stays global. When one named project's
    name holds another's ("Raticode Mobile" and "Raticode"), the longer one counts.
    """
    named: dict[str, str] = {}
    lowered = text.casefold()
    for project in projects:
        root = str(project.get("root") or "")
        if not root:
            continue
        name = project_name(root, [project]).casefold()
        by_name = len(name) >= 3 and re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", lowered)
        if by_name or root.casefold() in lowered:
            named[root] = name
    roots = [
        root
        for root, name in named.items()
        if not any(name != other and name in other for other in named.values())
    ]
    return roots[0] if len(roots) == 1 else None


def _entry(identity: str, item: dict[str, Any]) -> dict[str, Any]:
    entry = {"id": identity, "text": str(item.get("text") or ""), "saved": _saved_at(item)[:10]}
    root = project_of(item)
    if root:
        entry["project"] = root
    if until_of(item):
        entry["until"] = item["until"]
    return entry


def _pick(
    entries: list[dict[str, Any]], words: set[str], limit: int, chars: int
) -> list[dict[str, Any]]:
    """Today's ranking: entries sharing words with the request first, then the newest."""
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
    return picked


def choose(
    saved: dict[str, Any],
    query: str,
    *,
    limit: int = PROMPT_LIMIT,
    chars: int = PROMPT_CHARS,
    places: Iterable[str] = (),
    at: datetime | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """The memories for one prompt and how many of those that apply were left out.

    A memory whose end date has passed (at [at], default now) never applies.
    Global memories always apply; a project's memories apply only when one of
    [places] (the folders the work is in) is in that project. Everything that
    applies goes while it fits. Past the limit, global memories are chosen first
    and the project's memories fill the space left, each by today's ranking:
    memories sharing words with the request first, then the newest.
    """
    folders = [str(place) for place in places if place]
    newest = sorted(saved.items(), key=lambda pair: _saved_at(pair[1]), reverse=True)
    entries = [
        _entry(identity, item)
        for identity, item in newest
        if not ended(item, at)
        and (
            not project_of(item)
            or any(in_project(str(project_of(item)), folder) for folder in folders)
        )
    ]
    if len(entries) <= limit and sum(len(e["text"]) for e in entries) <= chars:
        return entries, 0
    words = set(_WORD.findall(query.lower()))
    picked = _pick([e for e in entries if "project" not in e], words, limit, chars)
    used = sum(len(e["text"]) for e in picked)
    picked += _pick(
        [e for e in entries if "project" in e], words, limit - len(picked), chars - used
    )
    return picked, len(entries) - len(picked)


def for_prompt(
    saved: dict[str, Any],
    settings: dict[str, Any],
    query: str,
    places: Iterable[str] = (),
) -> dict[str, Any]:
    """Prompt fields for Rem: the chosen memories and the current memory mode.

    [places] are the project folders the conversation is in; their memories join
    the global ones.
    """
    current = mode(settings)
    if current == "off":
        return {"memory_mode": "off"}
    shown, left_out = choose(saved, query, places=places)
    fields: dict[str, Any] = {"memories": shown, "memory_mode": current}
    if left_out:
        fields["memories_not_shown"] = left_out
    return fields


def for_worker(
    saved: dict[str, Any],
    settings: dict[str, Any],
    query: str,
    project_dir: str | None = None,
) -> list[str]:
    """Plain memory texts for a worker's background; none while memory is off.

    A task in a project also gets that project's memories; other projects' never.
    """
    if mode(settings) == "off":
        return []
    shown, _ = choose(
        saved,
        query,
        limit=WORKER_LIMIT,
        chars=WORKER_CHARS,
        places=[project_dir] if project_dir else (),
    )
    return [entry["text"] for entry in shown]


def unknown_ids(changes: list[Any], saved: dict[str, Any]) -> list[str]:
    return [change.id for change in changes if change.id not in saved]


def _proposed(memory: Any) -> tuple[str, str | None]:
    """A new memory's words and the end Rem proposed, from a string or {text, until}.

    An end that isn't usable (no timezone, already past, not a date) is dropped and
    the fact is saved without one; the user can set an end date afterwards.
    """
    if isinstance(memory, str):
        return memory.strip(), None
    fields = memory if isinstance(memory, dict) else vars(memory)
    text = str(fields.get("text") or "").strip()
    try:
        return text, parse_until(fields["until"]) if fields.get("until") else None
    except ValueError:
        return text, None


def apply(
    saved: dict[str, Any],
    added: Sequence[Any],
    changes: list[Any],
    settings: dict[str, Any],
    source_message_id: str,
    projects: Sequence[dict[str, Any]] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply one decision's memory changes and describe them for the reply.

    Returns the new memories and a record for the reply message, which shows
    what changed and lets the user undo it. Nothing is saved while memory is
    paused or off; an explicit correction or forget still applies while paused.
    A new fact that names exactly one of [projects] (the projects the
    conversation has selected) belongs to that project; any other is global.
    [added] holds strings or {text, until}, where until is the end Rem proposes
    for a fact tied to a date or event; the reply's note shows it with Undo.
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
            text, until = _proposed(memory)
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
            entry = {"id": identity, "text": text, "written": text}
            project = project_for(text, projects)
            if project:
                result[identity]["project"] = project
                entry["project"] = project
            if until:
                result[identity]["until"] = until
                entry["until"] = until
            known.add(text.casefold())
            record["saved"].append(entry)
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
                    **({"until": str(e["until"])[:80]} if e.get("until") else {}),
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
