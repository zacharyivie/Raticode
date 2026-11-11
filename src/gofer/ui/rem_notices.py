"""Cursor reads of Rem's chat and alerts for scripts and the command line.

The phone reads the same alerts through ``rem.notices``, filtered to the requests it
sent. Every request made through the desktop API is the user's own, so this read keeps
the task updates that need the user or finished, plus self-work and organization alerts.
"""

from __future__ import annotations

import re
from typing import Any

from gofer.devices.desktop_control import (
    REM_NOTICE_FILTERS,
    _notice_when,
    _source_notice,
    _task_reason,
)

# Message position, self-work report position, then organization event position or "-".
_CURSOR = re.compile(r"c1\.([0-9]{1,16})\.([0-9]{1,16})\.([0-9]{1,16}|-)")
NOTICES = 200
MESSAGE_PAGE = 300


def _position(query: dict[str, list[str]], name: str, default: int, top: int) -> int:
    raw = query.get(name, [""])[0]
    if not raw:
        return default
    if not raw.isdigit() or int(raw) > top:
        raise ValueError(f"Invalid {name}")
    return int(raw)


def messages_page(store: Any, query: dict[str, list[str]]) -> dict[str, Any]:
    """Chat messages after position ``after``, or the newest ``tail``, oldest first.

    ``last`` is the position to pass as ``after`` next time. ``conversation`` says which
    request Rem is answering and which it has answered, so a caller can tell when the
    reply to its own message is complete.
    """
    if "tail" in query:
        limit = _position(query, "tail", 20, MESSAGE_PAGE)
        with store.read() as db:
            # The position just before the newest ``limit`` messages.
            row = db.execute(
                "SELECT seq FROM messages ORDER BY seq DESC LIMIT 1 OFFSET ?", (limit,)
            ).fetchone()
            newest = db.execute("SELECT COALESCE(MAX(seq), 0) FROM messages").fetchone()
        after = int(newest[0]) if limit == 0 else int(row[0]) if row else 0
    else:
        limit = _position(query, "limit", 100, MESSAGE_PAGE)
        after = _position(query, "after", 0, 2**53)
    stored = store.messages_after(after, limit)
    messages = []
    for message in stored["messages"]:
        # A failed reply's diagnostic stays in the database for tracing, as in chat.
        message.pop("diagnostic", None)
        messages.append(message)
    with store.read() as db:
        conversation = store.setting(db, "conversation")
    return {
        "messages": messages,
        "last": stored["last"],
        "latest": stored["latest"],
        "conversation": {
            "id": conversation.get("id"),
            "turn_state": conversation.get("turn_state"),
            "pending": conversation.get("pending"),
            "processed": conversation.get("processed"),
            "waiting": [item.get("id") for item in conversation.get("waiting") or []],
        },
    }


def parse_include(value: str) -> list[str]:
    """The checked alert switches from a comma-separated list; all of them when empty."""
    include = [part.strip() for part in value.split(",") if part.strip()] or list(
        REM_NOTICE_FILTERS
    )
    if any(item not in REM_NOTICE_FILTERS for item in include) or len(set(include)) != len(include):
        raise ValueError("Choose alerts from needs_you, finished and proposals")
    return include


def read_notices(rem: Any, organizations: Any, cursor: str, include: list[str]) -> dict[str, Any]:
    """Alerts after ``cursor``, oldest first, and the cursor to read from next.

    Without a cursor nothing older is reported: the answer only says where now is, so a
    script that starts watching never gets the whole history as new alerts.
    """
    if cursor:
        match = _CURSOR.fullmatch(cursor)
        if match is None:
            raise ValueError("Unknown alert cursor")
        after: int | None = int(match[1])
        report: int | None = int(match[2])
        organization: int | None = None if match[3] == "-" else int(match[3])
    else:
        after = report = organization = None
    stored = rem.store.messages_after(after or 0, 0 if after is None else 300)
    latest = int(stored["latest"])
    reports = rem.self_work.alerts_after(report)
    manager = organizations if organizations is not None and organizations.enabled else None
    orgs = manager.alerts_after(organization) if manager is not None else None
    found: list[dict[str, Any]] = []
    last = latest if after is None or after > latest else int(stored["last"])
    if after is not None and after <= latest:
        for message in stored["messages"]:
            work = message.get("work") if isinstance(message.get("work"), dict) else {}
            # Self-work's own alerts already cover the work it started.
            if message.get("kind") != "task_update" or work.get("self_work"):
                continue
            reason = _task_reason(work, include)
            if not reason:
                continue
            found.append(
                {
                    "id": str(message.get("id") or ""),
                    "kind": "task_update",
                    "body": str(message.get("body") or ""),
                    "created_at": str(message.get("created_at") or ""),
                    "title": str(work.get("title") or ""),
                    "needs_user": work.get("needs_user") is True,
                    "reason": reason,
                    "task_id": str(work.get("task_id") or ""),
                    "run_id": str(work.get("run_id") or ""),
                    "thread_id": str(work.get("thread_id") or ""),
                }
            )
    alerts = [*reports["alerts"], *(orgs["alerts"] if orgs else [])]
    found.extend(_source_notice(a) for a in alerts if a["reason"] in include)
    found.sort(key=_notice_when)
    org_last = "-" if orgs is None else str(orgs["last"])
    return {
        "cursor": f"c1.{last}.{reports['last']}.{org_last}",
        "include": include,
        "notices": found[-NOTICES:],
        "skipped": max(0, len(found) - NOTICES),
        # More is stored after this cursor; read again at once.
        "more": last < latest or reports["more"] or bool(orgs and orgs["more"]),
    }
