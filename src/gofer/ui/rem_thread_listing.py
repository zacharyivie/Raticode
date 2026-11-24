"""How many threads Rem's prompts list, and the lookup that finds the rest.

The desktop sends every thread it has (``workspace_snapshot``), and Rem's recall ranks
every task thread linked from its chat. Listing them all made each chat reply, worker
answer and conversation decision carry hundreds of threads. The prompts keep the
``DESKTOP_LISTED`` most recently updated desktop threads and the ``LINKED_LISTED`` best
linked threads, with how many there are in all; a threads lookup searches the rest by
title and project. Reading a linked thread by its ID doesn't depend on either list.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DESKTOP_LISTED = 25
LINKED_LISTED = 15
FOUND_THREADS = 20


def stamp(value: Any) -> float:
    """A thread's update time as a sortable number; an unreadable one sorts oldest."""
    if isinstance(value, bool):
        return float("-inf")
    if isinstance(value, int | float):
        # The desktop's Date.now() style milliseconds.
        return float(value) / 1000
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    # A naive time is read as UTC so it compares with aware ones.
    return parsed.timestamp() if parsed.tzinfo else parsed.replace(tzinfo=UTC).timestamp()


def desktop_threads(snapshot: Any) -> list[dict[str, Any]]:
    """The desktop's threads from its snapshot, newest first, ignoring malformed entries."""
    threads = snapshot.get("threads") if isinstance(snapshot, dict) else None
    found = [t for t in threads or [] if isinstance(t, dict) and t.get("id")]
    return sorted(found, key=lambda t: stamp(t.get("updatedAt")), reverse=True)


def desktop_listing(snapshot: Any) -> dict[str, Any]:
    """The workspace's ``desktop`` section: the newest threads and how many there are."""
    listing = dict(snapshot) if isinstance(snapshot, dict) else {}
    threads = desktop_threads(snapshot)
    listing["threads"] = threads[:DESKTOP_LISTED]
    listing["threads_total"] = len(threads)
    return listing


def folder_matches(folder: str, roots: set[str], name: str) -> bool:
    """Whether a thread's project folder is one of ``roots`` (or inside one) or named ``name``."""
    folder = folder.rstrip("/")
    if not folder:
        return False
    if any(folder == root or folder.startswith(root + "/") for root in roots):
        return True
    return bool(name) and Path(folder).name.casefold() == name.casefold()


def find_threads(
    desktop: list[dict[str, Any]],
    linked: list[dict[str, Any]],
    *,
    query: str,
    roots: set[str],
    name: str,
) -> dict[str, Any]:
    """Threads whose title and project hold every query word, newest first, in brief.

    ``desktop`` comes from the desktop's snapshot and ``linked`` from Rem's task threads; a
    task thread the desktop also lists appears once, with its task. ``roots`` and ``name``
    narrow the search to one project when either is given.
    """
    words = query.casefold().split()
    entries: dict[str, dict[str, Any]] = {}
    for thread in desktop:
        entries[str(thread["id"])] = {
            "thread_id": str(thread["id"]),
            "title": str(thread.get("title") or "")[:200],
            "project_dir": thread.get("projectRoot") or None,
            "updated_at": thread.get("updatedAt"),
            **({"archived": True} if thread.get("archived") else {}),
        }
    for thread in linked:
        identity = str(thread["thread_id"])
        previous = entries.get(identity, {})
        entries[identity] = {
            **previous,
            "thread_id": identity,
            "title": str(thread.get("title") or previous.get("title") or "")[:200],
            "project_dir": thread.get("project_dir") or previous.get("project_dir"),
            "updated_at": thread.get("updated_at") or previous.get("updated_at"),
            **({"archived": True} if thread.get("archived") else {}),
            "task_id": thread.get("task_id"),
            "run_id": thread.get("run_id"),
            "state": thread.get("state"),
        }
    found = []
    for entry in entries.values():
        folder = str(entry.get("project_dir") or "")
        if (roots or name) and not folder_matches(folder, roots, name):
            continue
        text = " ".join([entry["title"], folder]).casefold()
        if all(word in text for word in words):
            found.append(entry)
    if not found:
        return {"threads": [], "note": "No thread matched."}
    found.sort(key=lambda entry: stamp(entry.get("updated_at")), reverse=True)
    return {"threads": found[:FOUND_THREADS], "more": max(0, len(found) - FOUND_THREADS)}
