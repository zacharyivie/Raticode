"""Use Raticode from a paired phone: Rem, organizations, swarms, workflows, source control,
settings and usage.

Nothing here runs unless the user turns on "Use Raticode from this phone" in
Paired devices. It is off by default and requires thread sync. Every operation
calls the same desktop service the window uses, with that service's validation
and approval policies, so the phone gains no authority the desktop UI lacks.

The phone never sends paths. Projects are named by the IDs in the phone's
catalog; Rem tasks, runs, threads, organizations, swarms and workflows by their
desktop IDs. Workflows are viewed and run, never edited (see ``workflows.py``).
Source control is a read-only status view of open projects (``source_control.py``),
and the file explorer browses their folders and opens files read-only
(``file_explorer.py``). The phone's own browser sees the window's open web tabs and
can send a page to a new background tab there (``phone_browser.py``); Rem's browser
tools stay desktop-only.
Results are projections for the phone screens, without paths, credentials,
grants or provider configuration.

Rem's chat has its own file channel per phone (``files_thread``). The phone
uploads attachments on it, and the desktop offers it files Rem's replies name,
read from an open project or the Second Brain folder. It is not a shared thread.
Tapping a file link in a reply asks for that file directly (``files.link``);
no model is involved, and the link must be in a message Rem or an agent wrote.
Files attached to the user's messages, from the desktop window or any phone, are
offered on the same channel when the phone asks for one by message and
attachment ID (``rem.attachment``), so each device can show every attachment.
Phones that opted in to Rem chat alerts poll ``rem.notices`` for Rem's replies and
task updates to their own requests; the phone decides what an alert shows. A phone
that turned on further alert switches names them in ``include``, and the desktop
decides which tasks count, from Rem's chat, self-work and organizations.

"Let this phone wake this computer" is a second opt-in on top of this one (``wake.py``).
While it is on, ``overview`` adds this computer's network card hardware address as
``wake_mac``, so the phone can send a Wake-on-LAN packet when the desktop is asleep.

``rem.tidy`` tidies a dictation with the model chosen in desktop Settings. It
answers ``pending`` at once, so the request leaves the phone's queue, and the
tidied words follow as a second result for the same request when the model is
done. Neither device stores the words; a failure answers with the heard text.

Excluded on purpose: editing, renaming or deleting files, terminals, driving the
browser (pages, input, Rem's browser tools and its profiles), pairing, provider
credentials, skill and MCP server definitions, organization configuration and
imports, workflow editing and run inputs, and settings that widen what agents
may do.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
import stat
import time
import unicodedata
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

from gofer.devices import app_log, organization_view
from gofer.devices.files import MAX_FILE
from gofer.devices.registry import PairingError
from gofer.devices.tailnet import is_tailnet_address
from gofer.devices.wake import clean_address, hardware_address, private_ipv4
from gofer.ui.file_mentions import file_mentions
from gofer.ui.rem_ticket_activity import visible_comments

READ_OPS = frozenset(
    {
        "overview",
        "browser.tabs",
        "developer.log",
        "files.list",
        "files.text",
        "kanban.board",
        "kanban.timeline",
        "media.image",
        "media.preview",
        "rem.draft",
        "rem.history",
        "rem.home",
        "rem.memories",
        "rem.notices",
        "rem.search",
        "rem.task",
        "rem.thread",
        "organizations.list",
        "organizations.read",
        "organizations.note",
        "selfwork.read",
        "settings.get",
        "source_control.commit",
        "source_control.diff",
        "source_control.history",
        "source_control.list",
        "source_control.read",
        "usage.overview",
        "workflows.list",
        "workflows.read",
    }
)
ACTION_OPS = frozenset(
    {
        "browser.open",
        "files.link",
        "files.open",
        "kanban.participant",
        "kanban.ticket",
        "selfwork.settings",
        "selfwork.report.seen",
        "rem.attachment",
        "rem.message",
        "rem.instruct",
        "rem.tidy",
        "rem.memory.add",
        "rem.memory.edit",
        "rem.memory.forget",
        "rem.memory.mode",
        "rem.memory.undo",
        "rem.task.create",
        "rem.task.run",
        "rem.task.pause",
        "rem.task.resume",
        "rem.task.delete",
        "rem.run.cancel",
        "rem.run.acknowledge",
        "rem.run.retry_review",
        "rem.run.pause",
        "rem.run.resume",
        "rem.run.wake",
        "organizations.control",
        "organizations.task",
        "organizations.knowledge",
        "organizations.worker",
        "organizations.schedule",
        "organizations.backlog_pull",
        "organizations.requests",
        "organizations.auto_mode",
        "swarms.start",
        "settings.update",
        "workflows.run",
        "workflows.stop",
        "workflows.resume",
    }
)
# Result bodies are JSON text split into parts. Escaping can at most double a
# part inside the record, which stays below the 65,536-byte record limit.
CHUNK = 24000
MAX_PARTS = 48
OFF = "Turn on Use Raticode from this phone in desktop Paired devices."


def _comment_author(actor: Any, employees: dict[Any, Any]) -> str:
    """Show organization comment authors as people, never raw actor IDs."""
    actor = str(actor or "")
    if actor == "user":
        return "You"
    if actor == "system":
        return "Raticode"
    if actor == "Rem" or actor.startswith("Rem:"):
        return "Rem"
    eid = actor.removeprefix("employee:")
    return str((employees.get(eid) or {}).get("name") or eid or "Unknown")


def _timestamp(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0


def _organization_usage(runtime: dict[str, Any]) -> dict[str, Any]:
    """This month's company totals: runs and reported tokens and dollars."""
    month = time.strftime("%Y-%m", time.gmtime())
    account = ((runtime.get("usage") or {}).get(month) or {}).get("company") or {}
    turns = int(account.get("turns") or 0)
    unknown = int(account.get("unknownCostTurns") or 0)
    return {
        "month": month,
        "runs": turns,
        "tokens": int(account.get("tokens") or 0),
        "cost_usd": None if turns and unknown >= turns else float(account.get("costUsd") or 0),
    }


OUTSIDE = "Only files in an open desktop project or the Second Brain can go to this phone."
SENSITIVE = (
    "This file may hold passwords, keys or sign-ins, so a link can't send it. "
    "Ask Rem for it by its full path if you want it on this phone."
)
_CANONICAL_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Files on one Rem chat message, in either direction.
REM_FILES = 8
# The longest Rem chat search a phone may send, in characters.
REM_SEARCH_QUERY = 200
# Full file paths found in one Rem message's text, sent so the phone can link them.
MENTIONS = 32
# Phone alerts per read; older ones are counted, not sent.
REM_NOTICES = 8
# The alert switches a phone can name in rem.notices; the desktop decides what counts.
REM_NOTICE_FILTERS = ("needs_you", "finished", "proposals")
# Report position, then organization event position, or "-" with no organizations here.
_REM_NOTICE_MARK = re.compile(r"n1\.([0-9]{1,16})\.([0-9]{1,16}|-)")
# Rem run states that end a task; a cancelled one was stopped by the person being told.
_REM_FINISHED = {"succeeded", "failed", "blocked", "outcome_unknown"}
_LOCAL_LINK = re.compile(r"\[([^\]]*)\]\(<?(?:file:|/|~/|[A-Za-z]:[\\/])[^)]*\)")
_LOCAL_PATH = re.compile(
    r"(?<![\w.:/])(?:~/|/|[A-Za-z]:[\\/])[^\s)\]>\"'`]*[/\\]([^\s)\]>\"'`/\\]+)"
)
# Where a tapped file link came from: Rem's chat, a Rem task thread, a shared thread.
LINK_SOURCES = frozenset({"rem", "rem_thread", "thread"})
LINK_LIMIT = 2048
_LINE_SUFFIX = re.compile(r"(\.[^:/?#]+):[1-9]\d*(?::[1-9]\d*)?$")
_SCHEME = re.compile(r"^[a-z][a-z\d+.-]*:", re.IGNORECASE)
_WINDOWS_DRIVE = re.compile(r"^[a-z]:[\\/]", re.IGNORECASE)
# Message images the phone fetches through the desktop, identified by their bytes.
IMAGE_LIMIT = 768 * 1024
IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
# Characters the phone strips from file names; an offer must already be clean.
_HIDDEN = set("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")

_ACTIVITIES = [
    ("rem", "Rem"),
    ("workflows", "Workflows"),
    ("files", "Files"),
    ("search", "Search"),
    ("source-control", "Source control"),
    ("organizations", "Organizations"),
    ("second-brain", "Second Brain"),
]
# Display and default choices only. Agent tools, browser permissions, swarm
# access, execution mode, folders and keybindings stay desktop-only.
SETTINGS: list[dict[str, Any]] = [
    {
        "key": "appearance.theme",
        "group": "Appearance",
        "label": "Theme",
        "kind": "enum",
        "options": [["system", "System"], ["light", "Light"], ["dark", "Dark"]],
    },
    {
        "key": "appearance.reducedMotion",
        "group": "Appearance",
        "label": "Reduce motion",
        "kind": "enum",
        "options": [["system", "System"], ["on", "On"], ["off", "Off"]],
    },
    {
        "key": "general.initialActivity",
        "group": "General",
        "label": "Open Raticode to",
        "kind": "enum",
        "options": [list(item) for item in _ACTIVITIES],
    },
    {
        "key": "general.autosave",
        "group": "General",
        "label": "Save workflow edits automatically",
        "kind": "bool",
    },
    {
        "key": "general.checkForUpdates",
        "group": "General",
        "label": "Check for updates",
        "kind": "bool",
    },
    {
        "key": "general.archivedThreadRetention",
        "group": "General",
        "label": "Delete archived threads",
        "kind": "enum",
        "options": [
            ["never", "Never"],
            ["7", "After 7 days"],
            ["30", "After 30 days"],
            ["90", "After 90 days"],
        ],
    },
    {"key": "assistant.provider", "group": "Rem", "label": "Default provider", "kind": "provider"},
    {"key": "assistant.model", "group": "Rem", "label": "Default model", "kind": "model"},
    {"key": "assistant.effort", "group": "Rem", "label": "Default effort", "kind": "effort"},
    {
        "key": "assistant.defaultScope",
        "group": "Rem",
        "label": "New thread scope",
        "kind": "enum",
        "options": [["current-directory", "Current project"], ["global", "Global"]],
    },
    {
        "key": "assistant.autoHideThoughts",
        "group": "Rem",
        "label": "Hide thoughts after a reply",
        "kind": "bool",
    },
    {
        "key": "assistant.avatarEnabled",
        "group": "Rem",
        "label": "Show Rem's avatar",
        "kind": "bool",
    },
    {
        "key": "assistant.avatarAnimated",
        "group": "Rem",
        "label": "Animate Rem's avatar",
        "kind": "bool",
    },
    {
        "key": "memory.reportFormat",
        "group": "Reports",
        "label": "Report format",
        "kind": "enum",
        "options": [["md", "Markdown"], ["html", "HTML"], ["slides", "Slides"], ["pdf", "PDF"]],
    },
    {
        # The browser new web tabs and Rem's tabs open in. Rem's site rules, sign-in
        # sharing and approvals are browser settings the phone never sees or changes.
        # Values never change; older phones show whatever labels the desktop sends.
        "key": "browser.engine",
        "group": "Browser",
        "label": "Default browser for new tabs",
        "kind": "enum",
        "options": [
            ["raticode", "Raticode (chromium)"],
            ["firefox", "Firefox"],
            ["chrome", "Chrome"],
            ["edge", "Edge"],
        ],
    },
    {
        "key": "editor.fontSize",
        "group": "Editor",
        "label": "Font size",
        "kind": "number",
        "min": 10,
        "max": 24,
        "step": 1,
    },
    {
        "key": "editor.tabSize",
        "group": "Editor",
        "label": "Tab size",
        "kind": "number",
        "min": 1,
        "max": 8,
        "step": 1,
    },
    {"key": "editor.wordWrap", "group": "Editor", "label": "Word wrap", "kind": "bool"},
    {"key": "editor.minimap", "group": "Editor", "label": "Minimap", "kind": "bool"},
    {
        "key": "editor.markdownDefault",
        "group": "Editor",
        "label": "Open Markdown as",
        "kind": "enum",
        "options": [["preview", "Preview"], ["edit", "Editor"]],
    },
    {
        "key": "terminal.fontSize",
        "group": "Terminal",
        "label": "Font size",
        "kind": "number",
        "min": 8,
        "max": 28,
        "step": 0.5,
    },
    {
        "key": "terminal.cursorBlink",
        "group": "Terminal",
        "label": "Blinking cursor",
        "kind": "bool",
    },
    {
        "key": "terminal.scrollback",
        "group": "Terminal",
        "label": "Scrollback lines",
        "kind": "number",
        "min": 100,
        "max": 100000,
        "step": 100,
    },
]
SETTING_KEYS = {item["key"]: item for item in SETTINGS}
PENDING_SETTINGS = 20
logger = logging.getLogger(__name__)


class Refused(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _draft_delta(
    conversation: dict[str, Any], since: Any, *, same_attempt: bool = True
) -> dict[str, Any]:
    """Rem's reply text written since the phone's last read, while Rem is replying.

    `reset` means the draft started over (a new attempt), so `text` is all of it.
    ``same_attempt`` is false when the reply started over since this phone's last read:
    the phone's text belongs to the earlier attempt even if the new one is longer.
    """
    replying = conversation.get("turn_state") == "replying"
    draft = _text(conversation.get("draft_reply"), 4000) if replying else ""
    offset = since if isinstance(since, int) and not isinstance(since, bool) else 0
    reset = not 0 <= offset <= len(draft) or (offset > 0 and not same_attempt)
    start = 0 if reset else offset
    return {
        "replying": replying,
        "turn_label": _text(conversation.get("turn_label"), 120) if replying else "",
        "start": start,
        "text": draft[start:],
        "length": len(draft),
        "reset": reset,
        # The reply attempt this text belongs to; the phone sends it back with its next read.
        "attempt": conversation.get("draft_attempt") if replying else None,
    }


_TICKET_ROLES = {"created", "worked", "updated"}
_TICKET_SCOPES = {"rem", "organization"}


def _ticket_key(value: Any) -> str:
    from gofer.ui.rem_kanban import TICKET_KEY

    return value if isinstance(value, str) and TICKET_KEY.fullmatch(value) else ""


def _ticket_refs(value: Any) -> list[dict[str, str]]:
    """The board tickets a task card lists: what a run created, worked on or changed."""
    refs = []
    for ref in value if isinstance(value, list) else []:
        if not isinstance(ref, dict) or not _ticket_key(ref.get("key")):
            continue
        refs.append(
            {
                "ticket_id": _text(ref.get("ticket_id"), 128),
                "key": ref["key"],
                "title": _text(ref.get("title"), 200),
                "scope": ref.get("scope") if ref.get("scope") in _TICKET_SCOPES else "rem",
                "organization_id": _text(ref.get("organization_id"), 100),
                "role": ref.get("role") if ref.get("role") in _TICKET_ROLES else "updated",
            }
        )
    return refs[:40]


def _ticket_index(index: Any, keys: set[str]) -> dict[str, dict[str, Any]]:
    """Board tickets by key, as they are now, for the ``keys`` this answer shows."""
    found: dict[str, dict[str, Any]] = {}
    for key, ticket in (index if isinstance(index, dict) else {}).items():
        if key not in keys or not _ticket_key(key) or not isinstance(ticket, dict):
            continue
        found[key] = {
            "ticket_id": _text(ticket.get("ticket_id"), 128),
            "key": key,
            "title": _text(ticket.get("title"), 200),
            "status": _text(ticket.get("status"), 20),
            "scope": ticket.get("scope") if ticket.get("scope") in _TICKET_SCOPES else "rem",
            "organization_id": _text(ticket.get("organization_id"), 100),
            "proposed": ticket.get("proposed") is True,
        }
    return found


def _search_parts(parts: Any, limit: int) -> list[dict[str, Any]]:
    """Plain and matched parts of a search snippet, at most ``limit`` characters in all."""
    kept: list[dict[str, Any]] = []
    for part in parts if isinstance(parts, list) else []:
        if not isinstance(part, dict) or limit <= 0:
            continue
        text = _text(part.get("text"), limit)
        limit -= len(text)
        kept.append({"text": text, "match": bool(part.get("match"))})
    return kept


def _search_view(found: dict[str, Any]) -> dict[str, Any]:
    """Rem chat search results for a phone: where each match is and a short snippet."""
    results = []
    for item in found.get("results") or []:
        result: dict[str, Any] = {
            "id": _text(item.get("id"), 128),
            "seq": int(item.get("seq") or 0),
            "role": "user" if item.get("role") == "user" else "rem",
            "created_at": _text(item.get("created_at"), 80),
            "where": _text(item.get("where"), 20),
            "snippet": _search_parts(item.get("snippet"), 400),
        }
        if item.get("kind") == "task_update":
            result["kind"] = "task_update"
        if item.get("title"):
            result["title"] = _search_parts(item["title"], 160)
        results.append(result)
    return {
        "query": _text(found.get("query"), REM_SEARCH_QUERY),
        "total": int(found.get("total") or 0),
        "results": results,
        "next": found.get("next") if type(found.get("next")) is int else None,
    }


def _shown_keys(messages: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> set[str]:
    """Ticket keys in the messages and task summaries an answer carries."""
    from gofer.ui.rem_kanban import ticket_keys

    keys: set[str] = set()
    for message in messages:
        keys.update(ticket_keys(message.get("body")))
        keys.update(ticket_keys(message.get("details")))
        keys.update(r["key"] for r in (message.get("work") or {}).get("tickets") or [])
    for task in tasks:
        for run in (task.get("active_run"), task.get("last_run"), *(task.get("runs") or [])):
            keys.update(r["key"] for r in (run or {}).get("tickets") or [])
    return keys


def _checks(criteria: Any) -> dict[str, int]:
    items = [c for c in criteria or [] if isinstance(c, dict)]
    return {
        "passed": sum(1 for c in items if c.get("result") == "pass"),
        "failed": sum(1 for c in items if c.get("result") == "fail"),
        "total": len(items),
    }


CHECK_LIMIT = 40
CHECK_TEXT = 600
CHECK_EVIDENCE = 1500


def _check_details(criteria: Any) -> list[dict[str, str]]:
    """Each check of one run with its outcome and the reviewer's reason, bounded for phones.

    ``result`` is pass, fail or unknown. An unknown check with a reason was reviewed
    but left unresolved; one without a reason hasn't been checked yet.
    """
    details = []
    for item in [c for c in criteria or [] if isinstance(c, dict)][:CHECK_LIMIT]:
        result = item.get("result")
        details.append(
            {
                "id": _text(item.get("id"), 128),
                "description": _text(item.get("description"), CHECK_TEXT),
                "result": result if result in {"pass", "fail"} else "unknown",
                "evidence": _text(item.get("evidence"), CHECK_EVIDENCE),
            }
        )
    return details


def _rich_message(message: dict[str, Any]) -> dict[str, Any]:
    """A Rem message's details, follow-ups, timing and result card, bounded for phones.

    Every field is optional. The body still carries the whole answer and its links,
    so a phone that ignores these loses nothing it had before.
    """
    item: dict[str, Any] = {}
    if isinstance(message.get("details"), str) and message["details"].strip():
        item["details"] = _text(message["details"], 6000)
    suggestions = message.get("suggestions")
    if isinstance(suggestions, list):
        kept = [_text(s, 120) for s in suggestions if isinstance(s, str) and s.strip()][:3]
        if kept:
            item["suggestions"] = kept
    meta = message.get("meta")
    if isinstance(meta, dict):
        seconds = meta.get("seconds")
        item["meta"] = {
            "provider": _text(meta.get("provider"), 40),
            "model": _text(meta.get("model"), 120),
            "seconds": float(seconds) if isinstance(seconds, (int, float)) else None,
        }
    result = message.get("result")
    if isinstance(result, dict):
        item["result"] = {
            "title": _text(result.get("title"), 160),
            "type": _text(result.get("type"), 40),
            "state": _text(result.get("state"), 40),
            "checks": {
                key: int((result.get("checks") or {}).get(key) or 0)
                for key in ("passed", "failed", "total")
            },
            # href is the path as it appears in the message's own link, which is what
            # files.link accepts; the phone never learns a path that isn't in the body.
            "artifacts": [
                {
                    "name": _text(a.get("name"), 160),
                    "title": _text(a.get("title"), 160),
                    "kind": _text(a.get("kind"), 12),
                    "size": int(a.get("size") or 0),
                    "href": _text(a.get("path"), 4096),
                }
                for a in (result.get("artifacts") or [])[:4]
                if isinstance(a, dict)
                and a.get("path")
                and str(a["path"]) in str(message.get("body") or "")
            ],
            "sources": [
                {
                    "url": _text(src.get("url"), 2048),
                    "title": _text(src.get("title"), 200),
                    "kind": _text(src.get("kind"), 12),
                }
                for src in (result.get("sources") or [])[:8]
                if isinstance(src, dict) and src.get("url")
            ],
            "thread_id": _text(result.get("thread_id"), 128),
        }
    return item


THREAD_STEPS = 60
TOKENS_USED = re.compile(r"^(?:tokens? used\s*:?\s*[\d,]+|[\d,]+\s+tokens? used)$", re.I)


def _thought_steps(thoughts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge a run of thought records into steps, as the desktop's thought list does.

    A tool or thinking block writes start, update and result records under one
    trace ID. Inputs and outputs stay on the desktop; a shell step without a
    description keeps a short command instead.
    """
    steps: list[dict[str, str]] = []
    index: dict[str, int] = {}
    for thought in thoughts:
        trace = thought.get("trace")
        raw = (
            trace if isinstance(trace, dict) else {"title": "Thought", "body": thought.get("body")}
        )
        kind = "tool" if raw.get("kind") == "tool" else "summary"
        entry = {
            "kind": kind,
            "title": str(raw.get("title") or ("Tool" if kind == "tool" else "")),
            "detail": str(raw.get("detail") or ""),
            "body": str(raw.get("body") or ""),
            "command": str(raw.get("command") or ""),
            "status": str(raw.get("status") or ""),
        }
        if kind == "summary" and TOKENS_USED.match(" ".join(entry["body"].split())):
            continue
        key = str(raw.get("id") or "")
        if not key or key not in index:
            if key:
                index[key] = len(steps)
            steps.append(entry)
            continue
        old = steps[index[key]]
        title = old["title"]
        if kind == "summary":
            title = entry["title"] or title
        elif title == "Tool result":
            title = entry["title"]
        merged = {f: entry[f] or old[f] for f in ("detail", "body", "command", "status")}
        steps[index[key]] = {"kind": old["kind"], "title": title, **merged}
    return [
        {
            "kind": s["kind"],
            "title": _text(s["title"], 120),
            "detail": _text(s["detail"], 300),
            "body": _text(s["body"], 1200),
            "command": "" if s["detail"] else _text(s["command"], 200),
            "status": _text(s["status"], 20),
        }
        for s in steps
    ]


def _thread_entries(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A work thread as the phone shows it: messages, with each run of thoughts as one group.

    Worker transcripts are mostly tool and thinking records. Sent one by one,
    they pushed the instruction and the final report out of the phone's window.
    """
    entries: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []

    def close() -> None:
        steps = _thought_steps(run)
        if steps:
            count = len(steps)
            entries.append(
                {
                    "id": _text(run[0].get("id"), 128),
                    "role": "assistant",
                    "kind": "thoughts",
                    # Older phones show only the body.
                    "body": f"Worked through {count} {'step' if count == 1 else 'steps'}.",
                    "count": count,
                    "steps": steps[-THREAD_STEPS:],
                }
            )
        run.clear()

    for message in messages:
        if message.get("kind") == "thought":
            run.append(message)
            continue
        close()
        entry = {
            "id": _text(message.get("id"), 128),
            "role": _text(message.get("role"), 20),
            "body": _text(message.get("body"), 16000),
            "kind": _text(message.get("kind"), 40),
        }
        if message.get("role") == "user":
            # Who wrote an instruction: Rem's prompts say "rem", the human's "user".
            # Older phones ignore the field.
            entry["sender"] = "rem" if message.get("sender") == "rem" else "user"
        entries.append(entry)
    close()
    return entries


def _newest(items: list[dict[str, Any]], budget: int = 600_000) -> list[dict[str, Any]]:
    """Keep the newest items whose encoded size fits within one result."""
    kept: list[dict[str, Any]] = []
    for item in reversed(items):
        budget -= len(json.dumps(item, ensure_ascii=True))
        if budget < 0:
            break
        kept.append(item)
    return kept[::-1]


def image_type(data: bytes) -> str | None:
    for signature, mime in IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def fetch_image(url: str) -> dict[str, Any]:
    """A public web image for a phone message, fetched like a link preview.

    The fetcher pins one validated public address and sends no cookies or
    referrer, so a reply can't point the desktop at this computer or the LAN.
    """
    from gofer.ui.link_previews import fetch

    if not 0 < len(url) <= 2048:
        raise Refused("invalid", "Images need a web address of at most 2,048 characters.")
    _, _, data, truncated = fetch(
        url, "image/webp,image/png,image/jpeg,image/gif;q=0.9,*/*;q=0.1", IMAGE_LIMIT
    )
    if truncated:
        raise Refused("invalid", "This image is too large to show on the phone.")
    mime = image_type(data)
    if mime is None:
        raise Refused("invalid", "This address isn't a JPEG, PNG, GIF or WebP image.")
    return {"url": url, "mime": mime, "data": base64.b64encode(data).decode("ascii")}


# A preview thumbnail larger than this is left out of the phone's answer.
PREVIEW_IMAGE_LIMIT = 280_000
_previews: Any = None


def fetch_preview(url: str) -> dict[str, Any]:
    """A web page's title, summary and small thumbnail for a phone source card.

    It uses the same fetcher, address rules and cache as the desktop's cards.
    """
    global _previews
    from gofer.ui.link_previews import LinkPreviews, PreviewError

    if not 0 < len(url) <= 2048:
        raise Refused("invalid", "Previews need a web address of at most 2,048 characters.")
    if _previews is None:
        _previews = LinkPreviews()
    try:
        preview = dict(_previews.get(url))
    except PreviewError as exc:
        raise Refused("invalid", str(exc)) from exc
    if len(str(preview.get("image") or "")) > PREVIEW_IMAGE_LIMIT:
        preview["image"] = ""
    return {
        "url": _text(preview.get("url") or url, 2048),
        "kind": _text(preview.get("kind"), 20),
        "title": _text(preview.get("title"), 200),
        "description": _text(preview.get("description"), 300),
        "site_name": _text(preview.get("siteName"), 80),
        "image": str(preview.get("image") or ""),
    }


def _whole_link(body: str, href: str) -> bool:
    """Whether [href] is a whole link or path in [body], not part of a longer one.

    The phone sends a link exactly as the message has it: a Markdown link's target, a
    path Rem mentioned or a bare path. Any of those starts and ends where a path does,
    so a piece cut from a longer path or word (``/home/me`` out of
    ``/home/me/notes.txt``) is refused.
    """
    for found in re.finditer(re.escape(href), body):
        start, end = found.start(), found.end()
        if start and body[start - 1] not in _LINK_BEFORE:
            continue
        if end < len(body) and body[end] not in _LINK_AFTER:
            continue
        return True
    return False


_LINK_BEFORE = frozenset(" \t\r\n([{<\"'`*_")
_LINK_AFTER = frozenset(" \t\r\n)]}>\"'`*_,;:!?.")


def link_paths(href: str) -> list[str]:
    """Paths a Markdown file link may name, read the way the desktop opens links.

    Web addresses and other schemes name no path. A trailing ``:line`` or
    ``:line:column`` is dropped. The raw text comes first, then its decoded form.
    """
    value = href.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    if value.lower().startswith("file:"):
        parsed = urlsplit(value)
        if parsed.netloc not in {"", "localhost"}:
            return []
        candidates = [unquote(parsed.path)]
    elif (_SCHEME.match(value) and not _WINDOWS_DRIVE.match(value)) or value.startswith(
        ("//", "#")
    ):
        return []
    else:
        value = re.split(r"[?#]", value, maxsplit=1)[0]
        candidates = [value, unquote(value)]
    paths = [_LINE_SUFFIX.sub(r"\1", c) for c in candidates]
    return [p for p in dict.fromkeys(paths) if p and "\x00" not in p]


def _file_mentions(message: dict[str, Any]) -> list[str]:
    """Full paths of existing files in a Rem message's text and Details, as written.

    The desktop chat links the same paths (``with_file_mentions``). The phone gets only
    the text to underline; a tap sends it back through ``files.link``, which applies
    every file check again, so a path here grants nothing.
    """
    if message.get("role") == "user":
        return []
    text = "\n".join(
        value for value in (message.get("body"), message.get("details")) if isinstance(value, str)
    )
    if not text:
        return []
    return [m["text"] for m in file_mentions(text) if len(m["text"]) <= 1024][:MENTIONS]


def _authored(messages: list[Any], identity: str, roles: set[str]) -> str | None:
    """The text of a message Rem or an agent wrote, with its Details section; the
    user's own text doesn't count. Phones show Details as Markdown with links too."""
    for message in messages:
        if isinstance(message, dict) and message.get("id") == identity:
            if message.get("role") not in roles:
                return None
            details = message.get("details")
            return "\n".join(
                [str(message.get("body") or "")] + ([details] if isinstance(details, str) else [])
            )
    return None


def _linked_file(candidates: list[str], base: str | None) -> Path:
    """The existing regular file a link names, checked before the scoped snapshot."""
    if not candidates:
        raise Refused("invalid", "This link doesn't name a file on the desktop.")
    found: Path | None = None
    for candidate in candidates:
        path = Path(candidate)
        if not path.is_absolute():
            if base is None:
                raise Refused("invalid", "This link doesn't give the file's full desktop path.")
            path = Path(base) / path
        # Fold ".." by name. The snapshot still opens every folder without following links.
        path = Path(os.path.normpath(path))
        try:
            info = path.lstat()
        except (OSError, ValueError):
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise Refused("unauthorized", "Only regular files, not links or folders, can be sent.")
        if info.st_size > MAX_FILE:
            raise Refused("invalid", "This file is larger than the 10 MB phone limit.")
        found = path
        break
    if found is None:
        raise Refused("not_found", "That file no longer exists on the desktop.")
    return found


def _snapshot_refused(exc: PairingError) -> Refused:
    code = str(exc)
    if code in {"file_quota", "file_disk_full"}:
        return Refused(
            "unavailable", "The desktop's space for phone files is full. Try again later."
        )
    if code in {"file_not_in_open_desktop_project", "scoped_file_unavailable"}:
        return Refused("unauthorized", OUTSIDE)
    return Refused("unauthorized", "The desktop couldn't send this file safely.")


def _notice_filter(params: dict[str, Any]) -> tuple[list[str], tuple[int, int | None] | None]:
    """The checked ``include`` and ``mark`` of a ``rem.notices`` read that names a filter."""
    include = params["include"]
    if set(params) - {"after", "include", "mark"}:
        raise Refused("invalid", "Unknown alert read field.")
    if (
        type(include) is not list
        or not include
        or any(type(item) is not str or item not in REM_NOTICE_FILTERS for item in include)
        or len(set(include)) != len(include)
    ):
        raise Refused("invalid", "Unknown alert switches.")
    if "mark" not in params:
        return include, None
    mark = params["mark"]
    match = _REM_NOTICE_MARK.fullmatch(mark) if type(mark) is str and len(mark) <= 256 else None
    if match is None:
        raise Refused("invalid", "Unknown alert position.")
    return include, (int(match[1]), None if match[2] == "-" else int(match[2]))


def _task_reason(work: dict[str, Any], include: list[str]) -> str:
    """The alert switch a Rem task update counts under, or an empty string."""
    if work.get("needs_user") is True:
        return "needs_you" if "needs_you" in include else ""
    finished = "finished" in include and work.get("state") in _REM_FINISHED
    return "finished" if finished else ""


def _dependency(value: Any) -> dict[str, Any]:
    """A waiting task's start condition: waiting, held or met, why, and each prerequisite."""
    value = value if isinstance(value, dict) else {}
    return {
        "state": _text(value.get("state"), 20),
        "reason": _text(value.get("reason"), 600),
        "prerequisites": [
            {
                "task_id": _text(item.get("task_id"), 128),
                "title": _text(item.get("title"), 160),
                "state": _text(item.get("state"), 20),
                "reason": _text(item.get("reason"), 300),
                "run_id": _text(item.get("run_id"), 128),
                "run_state": _text(item.get("run_state"), 40),
                # task (a named task), run (one running when asked), idle, time or self_work.
                "kind": _text(item.get("kind") or "task", 20),
                # Whether "Retry" can run it again: false once it is deleted or itself waits.
                "retryable": item.get("retryable") is True,
            }
            for item in value.get("prerequisites") or []
            if isinstance(item, dict)
        ][:10],
    }


def _alert_text(value: Any, limit: int) -> str:
    """Text for an alert about work this phone didn't ask for, with no local paths in it."""
    if not isinstance(value, str):
        return ""
    without_links = _LOCAL_LINK.sub(r"\1", value)
    return _text(_LOCAL_PATH.sub(r"\1", without_links), limit)


def _source_notice(alert: dict[str, Any]) -> dict[str, Any]:
    """A self-work or organization alert as a notice. Neither kind is in Rem's chat."""
    reason = alert["reason"]
    if reason == "proposals":
        count = int(alert["count"])
        body = f"{count} proposal{'' if count == 1 else 's'} waiting for your approval."
        title = "Self-work proposals"
    else:
        name = _alert_text(alert.get("title"), 160)
        where = _alert_text(alert.get("organization"), 160)
        subject = f"“{name}”" if name else "A change"
        state = "needs you" if reason == "needs_you" else "finished"
        body = f"{where + ': ' if where else ''}{subject} {state}."
        title = name or where
    return {
        "id": _text(alert["id"], 128),
        "kind": "proposals" if reason == "proposals" else "organization",
        "body": body,
        "created_at": _text(alert.get("at"), 80),
        "title": title,
        "needs_user": reason == "needs_you",
        "reason": reason,
    }


def _notice_when(notice: dict[str, Any]) -> float:
    try:
        return datetime.fromisoformat(notice["created_at"]).timestamp()
    except ValueError:
        return 0.0


def _notice_answer(
    found: list[dict[str, Any]], cursor: int, latest: int, sources: dict[str, Any] | None
) -> dict[str, Any]:
    """The answer to a ``rem.notices`` read, newest ``REM_NOTICES`` kept, oldest first."""
    more = cursor < latest
    mark: dict[str, str] = {}
    if sources is not None:
        found = sorted([*found, *map(_source_notice, sources["alerts"])], key=_notice_when)
        more = more or sources["more"]
        mark["mark"] = sources["mark"]
    return {
        "cursor": cursor,
        "notices": found[-REM_NOTICES:],
        "skipped": max(0, len(found) - REM_NOTICES),
        # More is stored after these positions; the phone reads again at once.
        "more": more,
        **mark,
    }


def _project_scoped(op: str) -> bool:
    """Reads that need the phone's open projects, read under the device lock."""
    return op.startswith("source_control.") or op in {"files.list", "files.text"}


def _phone_name(name: str) -> bool:
    return (
        bool(name)
        and name == name.strip()
        and name not in {".", ".."}
        and "\\" not in name
        and not any(unicodedata.category(c) == "Cc" or c in _HIDDEN for c in name)
    )


def _path_value(settings: dict[str, Any], key: str) -> Any:
    value: Any = settings
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _service_error(exc: BaseException) -> Refused:
    """Desktop service messages are written for the user and carry no secrets."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            first = errors()[0]
            where = ".".join(str(part) for part in first.get("loc", ()))
            message = str(first.get("msg", "")).removeprefix("Value error, ")
            return Refused("invalid", f"{where}: {message}"[:1000] if where else message[:1000])
        except (IndexError, TypeError, AttributeError):
            pass
    name = type(exc).__name__
    if name == "KanbanRefused":
        return Refused(getattr(exc, "code", "invalid"), str(exc)[:1000])
    if name in {"RemConflict", "OrganizationConflict"}:
        return Refused("conflict", str(exc)[:1000] or "This changed on desktop. Refresh first.")
    if isinstance(exc, KeyError):
        return Refused("not_found", "This item no longer exists on desktop.")
    return Refused("invalid", str(exc)[:1000] or "The desktop could not apply this action.")


class WakeSetting:
    """Per-phone choice to send the hardware address. Off unless a row exists."""

    def __init__(self, desktop: DesktopControl) -> None:
        self.desktop = desktop
        self.db = desktop.db

    # Local authenticated settings API only.
    def enable(self, peer: str, enabled: bool) -> None:
        app = self.desktop.app
        with app.registry.transaction():
            app._trusted(peer)
            if app.registry._row(peer)["role"] != "controller":
                raise PairingError("phone_required")
            if enabled:
                self.db.execute("INSERT OR IGNORE INTO device_wake VALUES (?)", (peer,))
            else:
                self.db.execute("DELETE FROM device_wake WHERE peer=?", (peer,))
        logger.info("Wake this computer turned %s for %s", "on" if enabled else "off", peer)

    def enabled(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT peer FROM device_wake")]

    def allowed(self, peer: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM device_wake WHERE peer=?", (peer,)
        ).fetchone() is not None and self.desktop.allowed(peer)

    def remember_answer(self, peer: str, events: list[dict[str, Any]]) -> None:
        """Note the outbox rows holding the address, so they go once the phone has them."""
        self.db.executemany(
            "INSERT OR IGNORE INTO device_wake_answers VALUES (?,?)",
            [(peer, event["sequence"]) for event in events],
        )

    def forget_delivered(self, peer: str, cursor: int) -> None:
        """Drop answers holding the address as soon as the phone has acknowledged them."""
        self.db.execute(
            "DELETE FROM device_outbox WHERE peer=? AND sequence<=? AND sequence IN "
            "(SELECT sequence FROM device_wake_answers WHERE peer=?)",
            (peer, cursor, peer),
        )
        self.db.execute(
            "DELETE FROM device_wake_answers WHERE peer=? AND sequence<=?", (peer, cursor)
        )


class DesktopControl:
    def __init__(self, application: Any) -> None:
        self.app = application
        self.db = application.registry.db
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS device_desktop_control (peer TEXT PRIMARY KEY);"
            "CREATE TABLE IF NOT EXISTS device_wake (peer TEXT PRIMARY KEY);"
            # Outbox positions of answers holding the address, never the address itself.
            "CREATE TABLE IF NOT EXISTS device_wake_answers "
            "(peer TEXT NOT NULL, sequence INTEGER NOT NULL, PRIMARY KEY(peer,sequence));"
            "CREATE TABLE IF NOT EXISTS device_desktop_settings "
            "(key TEXT PRIMARY KEY, value BLOB NOT NULL);"
            # Rem chat messages a phone sent; only their replies' files go to it.
            "CREATE TABLE IF NOT EXISTS device_rem_requests "
            "(peer TEXT NOT NULL, request TEXT NOT NULL, PRIMARY KEY(peer,request));"
            # One offer per reply file. A NULL file is a file the phone may not have.
            "CREATE TABLE IF NOT EXISTS device_rem_files (peer TEXT NOT NULL, "
            "message TEXT NOT NULL, position INTEGER NOT NULL, file TEXT, name TEXT, "
            "size INTEGER, PRIMARY KEY(peer,message,position));"
            # The latest offer for each tapped link, keyed by a hash of its path.
            "CREATE TABLE IF NOT EXISTS device_link_files (peer TEXT NOT NULL, "
            "thread TEXT NOT NULL, path TEXT NOT NULL, file TEXT NOT NULL, "
            "PRIMARY KEY(peer,thread,path));"
        )
        self.rem: Any = None
        self.organizations: Any = None
        self.usage: Callable[[], dict[str, Any]] | None = None
        self.fetch_image: Callable[[str], dict[str, Any]] = fetch_image
        self.fetch_preview: Callable[[str], dict[str, Any]] = fetch_preview
        self.wake = WakeSetting(self)
        self.hardware_address: Callable[[str], str | None] = hardware_address
        # The window's app log file (Settings > Developer), when the window started this backend.
        self.app_log_path: str | None = app_log.configured_path()
        # The reply attempt each phone last read Rem's draft from (_draft_delta).
        self.draft_attempts: dict[str, Any] = {}
        from gofer.devices.workflows import PhoneWorkflows

        self.workflows = PhoneWorkflows(self)
        from gofer.devices.phone_browser import PhoneBrowser

        # The window's browser tabs and pages phones sent to it, in memory only.
        self.browser = PhoneBrowser()

    # Local authenticated settings API only.
    def enable(self, peer: str, enabled: bool) -> None:
        with self.app.registry.transaction():
            self.app._trusted(peer)
            if self.app.registry._row(peer)["role"] != "controller":
                raise PairingError("phone_required")
            if enabled:
                self.db.execute("INSERT OR IGNORE INTO device_desktop_control VALUES (?)", (peer,))
            else:
                self.db.execute("DELETE FROM device_desktop_control WHERE peer=?", (peer,))

    def enabled(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT peer FROM device_desktop_control")]

    def allowed(self, peer: str) -> bool:
        return (
            self.db.execute("SELECT 1 FROM device_desktop_control WHERE peer=?", (peer,)).fetchone()
            is not None
            and peer in self.app.workspace.enabled()
        )

    def files_thread(self, peer: str) -> str:
        """The phone's private Rem chat file channel. It is never a shared thread."""
        desktop = self.app.registry.identity.device_id
        return str(uuid5(NAMESPACE_URL, f"raticode-rem-files:{desktop}:{peer}"))

    def projects(self, peer: str) -> dict[str, dict[str, str]]:
        """Open desktop projects by catalog ID, as the window last shared them."""
        result: dict[str, dict[str, str]] = {}
        for row in self.db.execute("SELECT context FROM device_grants WHERE peer=?", (peer,)):
            context = self.app._decode(peer, row[0])
            for project in context.get("projects", []):
                if project.get("id") and project.get("root"):
                    result.setdefault(
                        project["id"],
                        {"root": project["root"], "name": _text(project.get("name"), 160)},
                    )
        return result

    def _project_name(
        self, peer: str, root: str | None, catalog: dict[str, dict[str, str]] | None = None
    ) -> str:
        if not root:
            return ""
        for project in (self.projects(peer) if catalog is None else catalog).values():
            if project["root"] == root:
                return project["name"]
        return Path(root).name[:160]

    def gather(
        self, event: dict[str, Any], projects: dict[str, dict[str, str]] | None = None
    ) -> dict[str, Any] | None:
        """Read the desktop services a phone view needs, without the device lock.

        Rem, organizations, usage and Git keep their own locks and can be slow. Phones
        poll their open screen, so holding the device lock here would stall the
        desktop window's own device calls. The caller checks authorization before
        and again after, under the lock. ``projects`` is the phone's open projects,
        read under the lock, for reads scoped to them.
        """
        if event["type"] != "desktop.read":
            return None
        payload = event["payload"]
        try:
            return {"value": self._services(payload["op"], payload["params"], projects)}
        except Exception as exc:  # noqa: BLE001
            # Answered by handle() exactly as if the read had failed there.
            return {"error": exc}

    def _services(
        self,
        op: str,
        params: dict[str, Any],
        projects: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if op == "media.image":
            return {"image": self.fetch_image(str(params.get("url") or ""))}
        if op == "media.preview":
            return {"preview": self.fetch_preview(str(params.get("url") or ""))}
        if op == "kanban.board":
            return {"board": self._need(self.rem, "Rem").kanban.board()}
        if op == "kanban.timeline":
            scope = params.get("scope")
            if scope not in {"rem", "organization"}:
                raise Refused("invalid", "Choose a ticket.")
            return {
                "timeline": self._need(self.rem, "Rem").kanban.timeline(
                    {
                        "ticket_id": _text(params.get("ticket_id"), 128),
                        "scope": scope,
                        "organization_id": _text(params.get("organization_id"), 100),
                    }
                )
            }
        if op == "selfwork.read":
            return {"view": self._need(self.rem, "Rem").self_work.view()}
        if op == "rem.draft":
            store = self._need(self.rem, "Rem").store
            with store.read() as db:
                return {"conversation": store.setting(db, "conversation")}
        if op == "rem.search":
            before = params.get("before")
            if before is not None and (type(before) is not int or not 0 < before < 2**53):
                raise Refused("invalid", "Unknown search position.")
            # Words only, matched in the desktop's whole saved chat; no model reads them.
            return {
                "search": self._need(self.rem, "Rem").store.search(
                    _text(params.get("query"), REM_SEARCH_QUERY), before=before, limit=50
                )
            }
        if op == "rem.history":
            from gofer.ui.rem_kanban import message_ticket_keys

            store = self._need(self.rem, "Rem").store
            before = _text(params.get("before"), 128)
            through = _text(params.get("through"), 128)
            if not before:
                raise Refused("invalid", "Choose where the earlier messages end.")
            try:
                page = store.page(before, through=through or None)
            except KeyError:
                raise Refused(
                    "not_found", "That part of Rem's chat is no longer on your desktop."
                ) from None
            kanban = getattr(self.rem, "kanban", None)
            return {
                "page": page,
                "tasks": store.tasks(summary=True),
                "threads": store.thread_ids(),
                "roots": self._rem_config_roots(store),
                "tickets": kanban.resolve(message_ticket_keys(page["messages"]))
                if kanban is not None
                else {},
            }
        if op == "rem.home":
            store = self._need(self.rem, "Rem").store
            roots = self._rem_config_roots(store)
            from gofer.ui.rem_kanban import message_ticket_keys, task_ticket_keys

            conversation = store.conversation()
            # The phone reads run summaries only, never traces or worker config.
            tasks = store.tasks(summary=True)
            kanban = getattr(self.rem, "kanban", None)
            return {
                "conversation": conversation,
                "tasks": tasks,
                "threads": store.thread_ids(),
                "roots": roots,
                # Board tickets named in chat and on task cards, as they are now. The
                # phone shows these keys as pills; any other key stays plain text.
                "tickets": kanban.resolve(
                    [
                        *message_ticket_keys(conversation["messages"]),
                        *task_ticket_keys(tasks),
                    ]
                )
                if kanban is not None
                else {},
            }
        if op == "rem.notices":
            rem = self._need(self.rem, "Rem")
            store = rem.store
            wanted = _notice_filter(params) if "include" in params else None
            after = params.get("after")
            if after is None:
                return {
                    "after": None,
                    "stored": store.messages_after(0, 0),
                    # No message position yet, so this read only sets the other positions.
                    "sources": wanted and self._notice_sources(rem, wanted[0], None),
                }
            if type(after) is not int or not 0 <= after < 2**53:
                raise Refused("invalid", "Unknown message position.")
            return {
                "after": after,
                "stored": store.messages_after(after),
                "sources": wanted and self._notice_sources(rem, *wanted),
            }
        if op == "rem.task":
            from gofer.ui.rem_kanban import task_ticket_keys

            rem = self._need(self.rem, "Rem")
            tasks = rem.store.tasks(summary=True)
            kanban = getattr(rem, "kanban", None)
            return {
                "tasks": tasks,
                "tickets": kanban.resolve(task_ticket_keys(tasks)) if kanban is not None else {},
            }
        if op == "rem.memories":
            return {"memories": self._need(self.rem, "Rem").memories()}
        if op == "rem.thread":
            store = self._need(self.rem, "Rem").store
            return {"thread": store.thread(_text(params.get("thread_id"), 128))}
        if op == "organizations.list":
            manager = self._organizations_manager()
            return {"listed": manager.call("", "user", {"action": "list", "params": {}})}
        if op == "developer.log":
            return {
                "log": app_log.read(self.app_log_path, params.get("after"), params.get("levels"))
            }
        if op == "organizations.read":
            identifier = _text(params.get("organization_id"), 100)
            manager = self._organizations_manager()
            org = manager.call(
                "", "user", {"action": "read", "organizationId": identifier, "params": {}}
            )
            repository = None
            if (org.get("config") or {}).get("sharedRepository"):
                try:
                    repository = manager.call(
                        "",
                        "user",
                        {"action": "repository", "organizationId": identifier, "params": {}},
                    )
                except Exception as exc:  # noqa: BLE001
                    # The phone shows the organization without its repository summary.
                    logger.info(
                        "Phone organization view left out the shared repository: %s",
                        type(exc).__name__,
                    )
            return {"org": org, "repository": repository}
        if op == "organizations.note":
            identifier = _text(params.get("organization_id"), 100)
            note_id = _text(params.get("note_id"), 100)
            manager = self._organizations_manager()
            org = manager.call(
                "", "user", {"action": "read", "organizationId": identifier, "params": {}}
            )
            entry = manager.call(
                "",
                "user",
                {
                    "action": "knowledge_read",
                    "organizationId": identifier,
                    "params": {"id": note_id},
                },
            )
            return {"org": org, "entry": entry}
        if op == "usage.overview":
            return {"overview": self._need(self.usage, "Usage")()}
        if op.startswith("workflows."):
            from gofer.devices.workflows import gather

            return gather(op, params, self.workflows.base)
        if op.startswith("source_control."):
            from gofer.devices import source_control

            data_dir = getattr(self.rem, "data_dir", None)
            return source_control.gather(
                op, params, projects or {}, Path(data_dir) if data_dir else None
            )
        if op in {"files.list", "files.text"}:
            from gofer.devices import file_explorer

            data_dir = getattr(self.rem, "data_dir", None)
            return file_explorer.gather(
                op, params, projects or {}, Path(data_dir) if data_dir else None
            )
        return {}

    def handle(
        self, peer: str, event: dict[str, Any], prepared: dict[str, Any] | None = None
    ) -> list[dict[str, Any]] | None:
        kind = event["type"]
        if kind not in {"desktop.read", "desktop.action"}:
            return None
        payload = event["payload"]
        op = payload["op"]
        try:
            if not self.allowed(peer):
                raise Refused("unauthorized", OFF)
            if kind == "desktop.read":
                if prepared and "error" in prepared:
                    raise prepared["error"]
                services = prepared["value"] if prepared else None
                result = self._read(peer, op, payload["params"], services)
            else:
                result = self._act(peer, op, payload["params"], event["request_id"])
        except Refused as exc:
            return [self._result(peer, event, op, ok=False, code=exc.code, detail=str(exc))]
        except (ValueError, KeyError, OSError) as exc:
            refused = _service_error(exc)
            return [self._result(peer, event, op, ok=False, code=refused.code, detail=str(refused))]
        except Exception as exc:  # noqa: BLE001
            # A phone retries the same event bytes. An unexpected failure must
            # answer it, or that request would block the phone's queue.
            logger.warning("Phone desktop request %s failed: %s", op, type(exc).__name__)
            return [
                self._result(
                    peer,
                    event,
                    op,
                    ok=False,
                    code="invalid",
                    detail="The desktop could not complete this request.",
                )
            ]
        known = payload["params"].get("known") if kind == "desktop.read" else None
        answered = self._answer(peer, event, op, result, known)
        if isinstance(result, dict) and "wake_mac" in result:
            self.wake.remember_answer(peer, answered)
        return answered

    def _answer(
        self, peer: str, event: dict[str, Any], op: str, result: Any, known: Any = None
    ) -> list[dict[str, Any]]:
        body = json.dumps(result, separators=(",", ":"), ensure_ascii=True)
        # Phones poll the open screen. An unchanged view is answered with its digest only.
        digest = hashlib.sha256(body.encode()).hexdigest()[:32]
        if known is not None and known == digest:
            body = json.dumps({"unchanged": True, "digest": digest})
        else:
            body = json.dumps(
                {**result, "digest": digest}, separators=(",", ":"), ensure_ascii=True
            )
        parts = [body[i : i + CHUNK] for i in range(0, len(body), CHUNK)] or [""]
        if len(parts) > MAX_PARTS:
            return [
                self._result(
                    peer, event, op, ok=False, code="invalid", detail="This result is too large."
                )
            ]
        return [
            self._result(peer, event, op, ok=True, part=index, parts=len(parts), body=part)
            for index, part in enumerate(parts)
        ]

    def _result(
        self,
        peer: str,
        event: dict[str, Any],
        op: str,
        *,
        ok: bool,
        part: int = 0,
        parts: int = 1,
        body: str = "",
        code: str | None = None,
        detail: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"op": op, "ok": ok, "part": part, "parts": parts, "body": body}
        if code:
            payload.update(code=code, detail=detail[:1000])
        result: dict[str, Any] = self.app._emit(
            peer, {**event, "thread_id": None}, "desktop.result", payload
        )
        return result

    def _wake_field(self, peer: str) -> dict[str, str]:
        """``wake_mac`` for this phone's ``overview``, or nothing at all.

        Read fresh for every answer so it follows network changes. The exception text
        is dropped on purpose: it could hold the address, and ``overview`` must answer.
        """
        endpoint = self.app.lan_endpoint
        if not self.wake.allowed(peer) or endpoint is None:
            return {}
        host = endpoint["host"]
        if not private_ipv4(host) or is_tailnet_address(host):
            return {}
        try:
            address = clean_address(self.hardware_address(host))
        except Exception:  # noqa: BLE001
            return {}
        return {"wake_mac": address} if address else {}

    def _need(self, service: Any, label: str) -> Any:
        if service is None:
            raise Refused("unavailable", f"{label} is not available on this desktop.")
        return service

    # Reads -----------------------------------------------------------------

    def _read(
        self, peer: str, op: str, params: dict[str, Any], services: dict[str, Any] | None = None
    ) -> Any:
        if services is None:
            scope = self.projects(peer) if _project_scoped(op) else None
            data = self._services(op, params, scope)
        else:
            data = services
        if op == "overview":
            return {
                "projects": [
                    {"project_id": key, "name": value["name"]}
                    for key, value in self.projects(peer).items()
                ][:100],
                "rem": self.rem is not None,
                "organizations": bool(self.organizations and self.organizations.enabled),
                "swarms": self.app.remote.swarms is not None,
                "settings": self._settings_state()[0] is not None,
                "usage": self.usage is not None,
                "media": True,
                "file_links": True,
                "attachment_files": True,
                "task_retention": True,
                "rich_results": True,
                "draft_deltas": True,
                "review_retry": True,
                "memories": True,
                # Phones send thread.manage read/unread only to desktops that say so here.
                "thread_read": True,
                # Phones read rem.notices for Rem chat alerts only from desktops that say so.
                "rem_notices": self.rem is not None,
                # Phones send rem.notices include and mark only to desktops that say so.
                "rem_notices_filter": self.rem is not None,
                "workflows": self.workflows.available(),
                # Phones read source_control.* only from desktops that say so here;
                # history, commit and diff came later, with their own flag.
                "source_control": True,
                # Phones browse project files (files.list, files.text, files.open) only
                # on desktops that say so here.
                "file_explorer": True,
                "source_control_history": True,
                # Phones send rem.tidy only while this is on; see _tidy.
                "voice_tidy": self._voice_tidy_on(),
                # Phones read developer.log only from desktops that say so here.
                "developer_log": True,
                # Phones send organizations.note, .knowledge and .worker, and the task
                # actions move and backlog create, only to desktops that say so here.
                "organizations_v2": True,
                # organizations.read carries the work schedule, and phones send
                # organizations.schedule, only with desktops that say so here.
                "organization_schedule": True,
                # organizations.read says whether workers pull Backlog tickets, and phones
                # send organizations.backlog_pull, only with desktops that say so here.
                "organization_backlog_pull": True,
                # organizations.read lists Rem's requests with their IDs, and phones send
                # organizations.requests (approve, decline, approve all) only with desktops
                # that say so here.
                "organization_requests": True,
                # organizations.read carries Auto mode, its stop reason and Rem's decisions,
                # and phones send organizations.auto_mode, only with desktops that say so.
                "organization_auto_mode": True,
                # Phones read browser.tabs and send browser.open only to desktops that
                # say so here.
                "browser": True,
                # Phones send kanban.* and selfwork.* only to desktops that say so here.
                "kanban": self.rem is not None,
                # kanban.board carries participants and kanban.participant changes them.
                "kanban_participants": self.rem is not None,
                # Phones read kanban.timeline and send kanban.ticket's comment action only
                # to desktops that say so here.
                "kanban_comments": self.rem is not None,
                # Phones send rem.run.pause and rem.run.resume, and show a run's paused
                # state, only with desktops that say so here.
                "run_pause": self.rem is not None,
                **self._wake_field(peer),
            }
        if op == "kanban.board":
            return self._kanban_board(data["board"], self.projects(peer))
        if op == "kanban.timeline":
            return self._kanban_timeline(data["timeline"], self.projects(peer))
        if op == "selfwork.read":
            return data["view"]
        if op == "browser.tabs":
            return self.browser.view()
        if op == "media.image":
            return data["image"]
        if op == "media.preview":
            return data["preview"]
        if op == "rem.draft":
            attempt = data["conversation"].get("draft_attempt")
            said = params.get("attempt")
            if type(said) is int:
                # The phone names the attempt its text came from, which stays right when
                # it resends a read whose answer was lost.
                same = said == attempt
            else:
                # An older phone: compare with what this phone was last told. One this
                # desktop hasn't answered yet, say after a restart, starts over.
                same = peer in self.draft_attempts and self.draft_attempts[peer] == attempt
            self.draft_attempts[peer] = attempt
            return _draft_delta(data["conversation"], params.get("since"), same_attempt=same)
        if op == "rem.home":
            return self._rem_home(peer, data)
        if op == "rem.search":
            return _search_view(data["search"])
        if op == "rem.history":
            return self._rem_history(peer, data)
        if op == "rem.notices":
            return self._rem_notices(peer, data["after"], data["stored"], data["sources"])
        if op == "rem.task":
            return self._rem_task(peer, _text(params.get("task_id"), 128), data)
        if op == "rem.memories":
            return self._rem_memories(data)
        if op == "rem.thread":
            return self._rem_thread(_text(params.get("thread_id"), 128), data)
        if op == "organizations.list":
            return self._organizations(peer, data)
        if op == "organizations.read":
            return self._organization(peer, _text(params.get("organization_id"), 100), data)
        if op == "organizations.note":
            if data["entry"].get("archived"):
                raise Refused("not_found", "This note was archived on the desktop.")
            return organization_view.note(data["org"], data["entry"])
        if op == "developer.log":
            log = data["log"]
            if not log.pop("available"):
                raise Refused("unavailable", log["detail"])
            return log
        if op == "settings.get":
            return self._settings_view()
        if op == "usage.overview":
            return self._usage(data)
        if op in {"workflows.list", "workflows.read"}:
            return self.workflows.read(peer, op, params, data)
        if op.startswith("source_control."):
            from gofer.devices import source_control

            return source_control.read(op, data, self.projects(peer))
        if op in {"files.list", "files.text"}:
            from gofer.devices import file_explorer

            return file_explorer.read(op, data, self.projects(peer))
        raise Refused("invalid", "Unknown desktop request.")

    @staticmethod
    def _rem_config_roots(store: Any) -> list[str]:
        """Folders Rem may send files from: its open projects and the Second Brain."""
        with store.read() as db:
            config = store.setting(db, "config")
        roots = [p["root"] for p in config.get("projects") or [] if p.get("root")]
        if (config.get("brain") or {}).get("root"):
            roots.append(config["brain"]["root"])
        return roots

    def _rem_home(self, peer: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        from gofer.ui.rem_home_store import ran_at, retention

        data = data or self._services("rem.home", {})
        snapshot = data["conversation"]
        conversation = snapshot.get("conversation") or {}
        # Messages sent while Rem was replying come last, marked waiting.
        messages = [
            *(snapshot.get("messages") or []),
            *({**m, "state": "waiting"} for m in snapshot.get("waiting") or []),
        ]
        # Decrypt the project catalog once, not once per task.
        catalog = self.projects(peer)
        shown = _newest(self._phone_messages(peer, messages[-80:], data, catalog))
        listed = [
            self._task_summary(peer, task, catalog)
            for task in sorted(
                (task for task in data["tasks"] if retention(task)["listed"]),
                key=lambda task: (ran_at(task), task.get("created_at") or ""),
                reverse=True,
            )
        ][:100]
        return {
            "turn_state": _text(conversation.get("turn_state") or "idle", 40),
            # What Rem is doing while it replies, such as "Reading earlier messages".
            "turn_label": _text(conversation.get("turn_label"), 120)
            if conversation.get("turn_state") == "replying"
            else "",
            # Rem's reply as it is being written; the finished message replaces it.
            "draft_reply": _text(conversation.get("draft_reply"), 4000)
            if conversation.get("turn_state") == "replying"
            else "",
            "provider": _text(conversation.get("provider"), 80),
            "model": _text(conversation.get("model"), 160),
            "earlier": len(messages) - len(shown),
            "messages": shown,
            # Finished one-time tasks leave the list; the most recently run comes first.
            "tasks": listed,
            # Board tickets named in these messages and on these tasks, by key, as they
            # are now. Only these keys show as ticket pills; any other stays plain text.
            "tickets": _ticket_index(data.get("tickets"), _shown_keys(shown, listed)),
            "files_thread": self.files_thread(peer),
            "media": True,
            "file_links": True,
            "attachment_files": True,
            "task_retention": True,
            "rich_results": True,
            "draft_deltas": True,
            "review_retry": True,
            # rem.run.pause and rem.run.resume work, and runs can be "paused".
            "run_pause": True,
            "memories": True,
            "phone_actions": True,
            "screen_context": True,
            # rem.message accepts refers_to, and messages carry what they reply to.
            "replies": True,
            "voice_tidy": self._voice_tidy_on(),
            # rem.search finds words in the whole saved chat, and rem.history pages back
            # through what the phone doesn't hold. Phones send them only when this is on.
            "search": True,
        }

    def _phone_messages(
        self,
        peer: str,
        messages: list[dict[str, Any]],
        data: dict[str, Any],
        catalog: dict[str, dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Chat messages shaped for a phone: links, files and actions it may use."""
        roots = [p["root"] for p in catalog.values()] + list(data.get("roots") or [])
        # Links in the conversation outlive a finished one-time task; say which still open.
        tasks = {task["id"] for task in data["tasks"]}
        threads = set(data.get("threads") or [])
        items = []
        for message in messages:
            item = self._rem_message(message)
            if "work" in item:
                item["work"].update(
                    task_available=item["work"]["task_id"] in tasks,
                    thread_available=item["work"]["thread_id"] in threads,
                )
            attached = message.get("attachments") or []
            if message.get("role") == "user" and isinstance(attached, list) and attached:
                item["attachments"] = [
                    {
                        "file_id": _text(a.get("id"), 128),
                        "name": _text(a.get("name"), 160),
                        "size": int(a.get("size") or 0),
                    }
                    for a in attached[:REM_FILES]
                    if isinstance(a, dict)
                ]
            files = self._reply_files(peer, message, roots)
            if files:
                item["files"] = files
            actions = self._phone_actions(peer, message)
            if actions:
                item["phone_actions"] = actions
            mentions = _file_mentions(message)
            if mentions:
                item["file_mentions"] = mentions
            items.append(item)
        return items

    def _rem_history(self, peer: str, data: dict[str, Any]) -> dict[str, Any]:
        """A page of Rem's chat from before what the phone holds, oldest first.

        Each message is shaped as in ``rem.home``. When the page would be too long for one
        reply, the messages nearest the phone's are kept and the rest counted in ``earlier``.
        """
        page = data["page"]
        items = self._phone_messages(peer, page["messages"], data, self.projects(peer))
        shown = _newest(items)
        return {
            "messages": shown,
            "earlier": int(page.get("earlier") or 0) + len(items) - len(shown),
            "tickets": _ticket_index(data.get("tickets"), _shown_keys(shown, [])),
        }

    def _asked_by(self, peer: str, message: dict[str, Any]) -> bool:
        """Whether this Rem message answers a request this phone sent."""
        request = message.get("reply_to")
        return isinstance(request, str) and (
            self.db.execute(
                "SELECT 1 FROM device_rem_requests WHERE peer=? AND request=?", (peer, request)
            ).fetchone()
            is not None
        )

    def _phone_actions(self, peer: str, message: dict[str, Any]) -> list[dict[str, Any]]:
        """Rem's suggested phone actions, only for the phone that asked, checked again."""
        from gofer.ui.rem_phone_actions import clean_phone_actions

        raw = message.get("phone_actions")
        if message.get("role") == "user" or not raw or not self._asked_by(peer, message):
            return []
        return clean_phone_actions(raw)

    def _notice_sources(
        self, rem: Any, include: list[str], positions: tuple[int, int | None] | None
    ) -> dict[str, Any]:
        """Self-work and organization alerts after ``positions``, and the position after them.

        Every source's position moves on whichever switches are on, so a switch turned on
        later starts from now instead of from when the first one went on. A phone with no
        saved ``mark`` passes ``None`` and only learns the current positions.
        """
        report, organization = positions or (None, None)
        reports = rem.self_work.alerts_after(report)
        manager = self.organizations
        organizations = (
            None
            if manager is None
            else manager.alerts_after(None if not manager.enabled else organization)
        )
        last = "-" if organizations is None else str(organizations["last"])
        alerts = [*reports["alerts"], *(organizations["alerts"] if organizations else [])]
        return {
            "include": include,
            "mark": f"n1.{reports['last']}.{last}",
            "more": reports["more"] or bool(organizations and organizations["more"]),
            "alerts": [a for a in alerts if a["reason"] in include],
        }

    def _rem_notices(
        self,
        peer: str,
        after: int | None,
        stored: dict[str, Any],
        sources: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Rem's new messages for this phone's alerts.

        A phone hears about replies and updates for its own requests. Answers to questions
        typed on the desktop, and updates for tasks started there, stay on the desktop
        unless ``sources`` is set, when the phone also hears about the tasks it asked for.
        ``cursor`` is the last message position read; with no ``after``, or one past the
        end of a reset conversation, it only returns the current position.
        """
        latest = int(stored["latest"])
        if after is None or after > latest:
            return _notice_answer([], latest, latest, sources)
        messages = stored["messages"]
        candidates = {str(m.get("id")) for m in messages if m.get("role") == "user"}
        candidates.update(str(v) for v in stored["sources"].values() if v)
        if stored["asked"]:
            candidates.add(str(stored["asked"]))
        mine = self._phone_requests(peer, candidates)
        asked = str(stored["asked"]) in mine if stored["asked"] else False
        found = []
        for message in messages:
            if message.get("role") == "user":
                asked = str(message.get("id")) in mine
                continue
            if message.get("role") != "rem":
                continue
            work = message.get("work") if isinstance(message.get("work"), dict) else {}
            update = message.get("kind") == "task_update"
            reason = ""
            if not (stored["sources"].get(work.get("task_id")) in mine if update else asked):
                # Self-work's own alerts (``sources``) already cover the work it started,
                # so its updates in chat don't alert a second time.
                reason = (
                    _task_reason(work, sources["include"])
                    if sources and update and not work.get("self_work")
                    else ""
                )
                if not reason:
                    continue
            clean = _alert_text if reason else _text
            found.append(
                {
                    "id": _text(message.get("id"), 128),
                    "kind": "task_update" if update else "reply",
                    "body": clean(message.get("body"), 1500),
                    "created_at": _text(message.get("created_at"), 80),
                    "title": clean(work.get("title"), 160),
                    "needs_user": work.get("needs_user") is True,
                    **({"reason": reason} if reason else {}),
                }
            )
        return _notice_answer(found, int(stored["last"]), latest, sources)

    def _phone_requests(self, peer: str, candidates: set[str]) -> set[str]:
        """The Rem chat messages among ``candidates`` that this phone sent."""
        found: set[str] = set()
        listed = sorted(candidates)
        for start in range(0, len(listed), 400):
            chunk = listed[start : start + 400]
            marks = ",".join("?" * len(chunk))
            found.update(
                row[0]
                for row in self.db.execute(
                    "SELECT request FROM device_rem_requests WHERE peer=? "
                    f"AND request IN ({marks})",
                    (peer, *chunk),
                )
            )
        return found

    def _reply_files(
        self, peer: str, message: dict[str, Any], roots: list[str]
    ) -> list[dict[str, Any]]:
        """Offer the files on Rem's reply to the phone that asked for them, once each."""
        files = message.get("files")
        if not isinstance(files, list) or not files or not self._asked_by(peer, message):
            return []
        identity = _text(message.get("id"), 128)
        result = []
        for position, item in enumerate(files[:REM_FILES]):
            row = self.db.execute(
                "SELECT file,name,size FROM device_rem_files WHERE peer=? AND message=? "
                "AND position=?",
                (peer, identity, position),
            ).fetchone()
            if row is None:
                # Rem approved these files when it wrote the reply, including ones outside
                # its open folders that the user asked for by path (rem_home.reply_files).
                offer = self._offer(peer, item, roots, own_folder=True)
                row = (offer["file_id"], offer["name"], offer["size"]) if offer else (None, "", 0)
                self.db.execute(
                    "INSERT INTO device_rem_files VALUES (?,?,?,?,?,?)",
                    (peer, identity, position, *row),
                )
            if row[0]:
                result.append({"file_id": row[0], "name": row[1], "size": row[2]})
        return result

    def _offer(
        self, peer: str, item: Any, roots: list[str], *, own_folder: bool = False
    ) -> dict[str, Any] | None:
        path = Path(str((item or {}).get("path") or "")) if isinstance(item, dict) else None
        if path is None:
            return None
        try:
            return self._offer_path(peer, path, roots, own_folder=own_folder)
        except Refused as exc:
            logger.info("Rem reply file not offered to phone: %s", exc.code)
            return None

    def _offer_path(
        self, peer: str, path: Path, roots: list[str], *, own_folder: bool = False
    ) -> dict[str, Any]:
        """Offer one file from Rem's folders on the phone's Rem chat channel.

        ``own_folder`` is for files Rem chose for its chat: files already on one of its
        replies, and links in its chat replies that the user taps. One outside the open
        folders is read from its own folder, after the sensitive-file check. Links in
        task threads and shared threads never use it, so they stay inside the folders.
        """
        if not path.is_absolute() or not _phone_name(path.name[:160]):
            raise Refused("unauthorized", OUTSIDE)
        # A link may name a project through the symlinked folder it was opened from.
        # Only that root is followed; the snapshot opens everything below it unfollowed.
        for root in map(Path, roots):
            if path.is_relative_to(root) and not path.is_relative_to(root.resolve()):
                path = root.resolve() / path.relative_to(root)
                break
        # Rem stores resolved paths; a project may be open through a symlinked folder.
        inside = [Path(r).resolve() for r in roots if path.is_relative_to(Path(r).resolve())]
        if not inside and own_folder and path.parent.resolve() == path.parent:
            inside = [path.parent]
        if not inside:
            raise Refused("unauthorized", OUTSIDE)
        thread = self.files_thread(peer)
        try:
            # The scoped snapshot refuses symlinks and anything but a regular file.
            offer: dict[str, Any] = self.app.files.snapshot(
                peer, thread, max(inside, key=lambda r: len(r.parts)), path
            )
        except PairingError as exc:
            raise _snapshot_refused(exc) from None
        self.app._emit(peer, {"thread_id": thread, "request_id": str(uuid4())}, "file.offer", offer)
        return offer

    def _rem_roots(self, peer: str) -> list[str]:
        """Folders Rem may send files from, as ``rem.home`` offers them."""
        store = self._need(self.rem, "Rem").store
        with store.read() as db:
            config = store.setting(db, "config")
        roots = [p["root"] for p in self.projects(peer).values()]
        roots += [p["root"] for p in config.get("projects") or [] if p.get("root")]
        if (config.get("brain") or {}).get("root"):
            roots.append(config["brain"]["root"])
        return roots

    def _refuse_sensitive_link(self, peer: str, path: Path, roots: list[str] | None) -> None:
        """A tapped link never sends a credential store, a secret-looking file or
        Raticode's own data, even inside an open folder. The user can still ask Rem for
        it by its full path, which rem_home.reply_files checks against their message."""
        from gofer.ui.rem_home import sensitive_path

        resolved = path.resolve()
        folders = [Path(root).resolve() for root in roots or self._rem_roots(peer) if root]
        opened = max(
            (root for root in folders if resolved.is_relative_to(root)),
            key=lambda root: len(root.parts),
            default=None,
        )
        data_dir = getattr(self.rem, "data_dir", None)
        if sensitive_path(resolved, Path(data_dir) if data_dir else None, opened=opened):
            raise Refused("unauthorized", SENSITIVE)

    def _file_link(self, peer: str, params: dict[str, Any]) -> dict[str, Any]:
        """Offer the file a tapped link names, without asking Rem or any model.

        The link must be in a message Rem or an agent wrote, so the phone can't
        name paths of its own. The scoped snapshot then decides, exactly as for
        files Rem attaches, whether the file may leave the desktop.
        """
        source = params.get("source")
        href = params.get("href")
        message_id = params.get("message_id")
        thread = _text(params.get("thread_id"), 128)
        if (
            source not in LINK_SOURCES
            or not isinstance(href, str)
            or not 0 < len(href.strip()) <= LINK_LIMIT
            or not isinstance(message_id, str)
            or not 0 < len(message_id) <= 200
            or (source != "rem" and not _CANONICAL_ID.fullmatch(thread))
        ):
            raise Refused("invalid", "This link can't be opened from the phone.")
        base: str | None = None
        context: dict[str, Any] | None = None
        if source == "thread":
            context, body = self._shared_link_message(peer, thread, message_id)
            base = context["project_path"]
        elif source == "rem_thread":
            data = self._need(self.rem, "Rem").store.thread(thread)
            body = _authored(data.get("messages") or [], message_id, {"assistant"})
            base = (data.get("metadata") or {}).get("projectRoot") or None
        else:
            messages = self._need(self.rem, "Rem").store.conversation()["messages"]
            body = _authored(messages, message_id, {"rem"})
        if body is None:
            raise Refused("not_found", "This message is no longer on the desktop.")
        if not _whole_link(body, href):
            raise Refused("not_found", "That link isn't in this message on the desktop.")
        # A full path Rem wrote as plain text is exactly the text the phone linked; it is
        # read as written, so spaces, "#", "?" and "~/" name the file the desktop found.
        mentioned = source == "rem" and href in {m["text"] for m in file_mentions(body)}
        path = _linked_file([os.path.expanduser(href)] if mentioned else link_paths(href), base)
        self._refuse_sensitive_link(
            peer,
            path,
            None
            if context is None
            else [p["root"] for p in context.get("projects") or [] if p.get("root")] + [base],
        )
        channel = thread if context is not None else self.files_thread(peer)
        key = hashlib.sha256(str(path).encode("utf-8", "surrogatepass")).hexdigest()
        previous = self.db.execute(
            "SELECT file FROM device_link_files WHERE peer=? AND thread=? AND path=?",
            (peer, channel, key),
        ).fetchone()
        if context is not None:
            try:
                offer = self.app._thread_snapshot(peer, thread, context, path)
            except PairingError as exc:
                raise _snapshot_refused(exc) from None
            self.app._emit(
                peer, {"thread_id": thread, "request_id": str(uuid4())}, "file.offer", offer
            )
        else:
            # Rem's chat may send any file the desktop account can read, as its reply
            # files do; a link in a task thread stays within the open folders.
            offer = self._offer_path(peer, path, self._rem_roots(peer), own_folder=source == "rem")
        # A repeated tap replaces the earlier copy unless the phone is downloading it.
        if (
            previous is not None
            and not self.db.execute(
                "SELECT 1 FROM device_file_sends WHERE peer=? AND id=?", (peer, previous[0])
            ).fetchone()
        ):
            self.app.files.cancel(peer, channel, previous[0])
        self.db.execute(
            "INSERT OR REPLACE INTO device_link_files VALUES (?,?,?,?)",
            (peer, channel, key, offer["file_id"]),
        )
        return {
            "file_id": offer["file_id"],
            "name": offer["name"],
            "size": offer["size"],
            "thread_id": channel,
        }

    def _file_open(self, peer: str, params: dict[str, Any]) -> dict[str, Any]:
        """Offer a file the phone's file explorer opened, from an open project only.

        The phone names the project by catalog ID and the file by its path in it. The
        explorer's checks (no links, no secret-looking files, at most 10 MiB) run first,
        then the scoped snapshot reads the file without following links, as for every
        file the desktop sends. Opening the same file again replaces the earlier copy
        unless the phone is downloading it.
        """
        from gofer.devices import file_explorer

        data_dir = getattr(self.rem, "data_dir", None)
        _, root, path, info = file_explorer.offer_target(
            self.projects(peer), params, Path(data_dir) if data_dir else None
        )
        channel = self.files_thread(peer)
        key = hashlib.sha256(str(path).encode("utf-8", "surrogatepass")).hexdigest()
        previous = self.db.execute(
            "SELECT file FROM device_link_files WHERE peer=? AND thread=? AND path=?",
            (peer, channel, key),
        ).fetchone()
        try:
            offer: dict[str, Any] = self.app.files.snapshot(peer, channel, root, path)
        except PairingError as exc:
            raise _snapshot_refused(exc) from None
        self.app._emit(
            peer, {"thread_id": channel, "request_id": str(uuid4())}, "file.offer", offer
        )
        if (
            previous is not None
            and not self.db.execute(
                "SELECT 1 FROM device_file_sends WHERE peer=? AND id=?", (peer, previous[0])
            ).fetchone()
        ):
            self.app.files.cancel(peer, channel, previous[0])
        self.db.execute(
            "INSERT OR REPLACE INTO device_link_files VALUES (?,?,?,?)",
            (peer, channel, key, offer["file_id"]),
        )
        return {
            "file_id": offer["file_id"],
            "name": offer["name"],
            "size": offer["size"],
            "thread_id": channel,
            "path": str(params.get("path") or ""),
            "modified": file_explorer.when(info.st_mtime),
        }

    def _shared_link_message(
        self, peer: str, thread: str, message_id: str
    ) -> tuple[dict[str, Any], str | None]:
        """The shared thread's file context and the agent message holding a link."""
        from gofer.ui.device_chat import validate_context

        row = self.db.execute(
            "SELECT context FROM device_grants WHERE peer=? AND thread=?", (peer, thread)
        ).fetchone()
        if row is None:
            raise Refused("unauthorized", "This thread is no longer shared with this phone.")
        context = validate_context(self.app._decode(peer, row[0]))
        for message in self.app.workspace.desktop_messages(peer, thread):
            body = str(message.get("body") or "")
            identity = str(message.get("id") or "")
            # History splits long messages; each fragment after the first has its own ID.
            fragments = {identity} | {
                str(uuid5(NAMESPACE_URL, f"raticode-message:{identity}:{offset}"))
                for offset in range(0, max(1, len(body)), 4000)
            }
            if message_id in fragments:
                return context, body if message.get("role") == "assistant" else None
        return context, None

    @staticmethod
    def _rem_message(message: dict[str, Any]) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": _text(message.get("id"), 128),
            "role": _text(message.get("role"), 20),
            "body": _text(message.get("body"), 6000),
            "created_at": _text(message.get("created_at"), 80),
        }
        work = message.get("work")
        if isinstance(work, dict):
            item["work"] = {
                key: _text(work.get(key), 160)
                for key in ("task_id", "run_id", "thread_id", "title", "type", "state")
            }
            tickets = _ticket_refs(work.get("tickets"))
            if tickets:
                item["work"]["tickets"] = tickets
            if work.get("self_work"):
                item["work"]["self_work"] = _text(work["self_work"], 20)
            if work.get("paused_by") in {"user", "priority"}:
                # A paused card: by the user, or making way for the user's own work.
                item["work"]["paused_by"] = work["paused_by"]
        started = message.get("self_work")
        if isinstance(started, dict):
            # Cards one self-work pass started share a batch; the phone shows them together.
            item["self_work"] = {
                "kind": _text(started.get("kind"), 20),
                "batch": _text(started.get("batch"), 64),
            }
        if isinstance(message.get("state"), str):
            item["state"] = _text(message["state"], 40)
        if message.get("kind") == "task_update":
            item["kind"] = "task_update"
        report = message.get("self_work_report")
        if message.get("kind") == "self_work_report" and isinstance(report, dict):
            # What self-work did in one period; each item opens its task like a card.
            item["kind"] = "self_work_report"
            counts = report.get("counts")
            if not isinstance(counts, dict):
                counts = {}
            item["self_work_report"] = {
                "period_id": _text(report.get("period_id"), 64),
                "started_at": _text(report.get("started_at"), 80),
                "ended_at": _text(report.get("ended_at"), 80),
                "ended_by": _text(report.get("ended_by"), 20),
                "counts": {
                    str(key)[:30]: value
                    for key, value in counts.items()
                    if isinstance(value, int) and not isinstance(value, bool)
                },
                "items": [
                    {
                        **{
                            key: _text(entry.get(key), 160)
                            for key in (
                                "task_id",
                                "run_id",
                                "thread_id",
                                "title",
                                "kind",
                                "outcome",
                            )
                        },
                        "tickets": _ticket_refs(entry.get("tickets")),
                    }
                    for entry in report.get("items") or []
                    if isinstance(entry, dict)
                ][:30],
            }
        queued = message.get("queued_after")
        if message.get("role") == "user" and isinstance(queued, list):
            # A request the user queued behind these tasks; Rem got it once they succeeded.
            item["queued_after"] = [
                _text(entry.get("title"), 160) for entry in queued if isinstance(entry, dict)
            ][:10]
        referenced = message.get("referenced")
        if isinstance(referenced, dict) and isinstance(message.get("refers_to"), str):
            # Only a preview of the message replied to: its files and cards stay with it.
            item["refers_to"] = _text(message["refers_to"], 128)
            item["referenced"] = (
                {"id": item["refers_to"], "unavailable": True}
                if referenced.get("unavailable")
                else {
                    "id": item["refers_to"],
                    "role": _text(referenced.get("role"), 20),
                    "body": _text(referenced.get("body"), 300),
                    "created_at": _text(referenced.get("created_at"), 80),
                    "kind": _text(referenced.get("kind"), 40),
                    "title": _text(referenced.get("title"), 160),
                    "files": max(0, min(int(referenced.get("files") or 0), 99)),
                }
            )
        item.update(_rich_message(message))
        from gofer.ui.rem_home_memory import phone_record

        note = phone_record(message.get("memory"))
        if note:
            item["memory"] = note
        return item

    @staticmethod
    def _run(run: dict[str, Any] | None) -> dict[str, Any] | None:
        if not run:
            return None
        from gofer.ui.rem_home_sleep import public as public_sleep
        from gofer.ui.rem_home_store import paused_by, progress_step

        return {
            "run_id": _text(run.get("id"), 128),
            "state": _text(run.get("state"), 40),
            # Plan, Work, Check or Report: a queued run whose work is done is at Check.
            "step": progress_step(run),
            "created_at": _text(run.get("created_at"), 80),
            "finished_at": _text(run.get("finished_at"), 80),
            "summary": _text(run.get("summary_short") or run.get("summary"), 700),
            "needs_user": run.get("needs_user") is True,
            "acknowledged": run.get("acknowledged") is True,
            "thread_id": _text(run.get("thread_id"), 128),
            "verification_thread_id": _text(run.get("verification_thread_id"), 128),
            # Live progress: the step label, why it is waiting, and its checks so far.
            "phase_label": _text(run.get("phase_label"), 160),
            "waiting_reason": _text(run.get("waiting_reason"), 240),
            "checks": _checks(run.get("criteria")),
            # Whether Retry review would be accepted for this run right now.
            "review_retryable": run.get("review_retryable") is True,
            # Board tickets the run created, worked on or changed.
            "tickets": _ticket_refs(run.get("tickets")),
            # Who paused a paused run: the user, or self-work making way for the user's work.
            "paused_by": paused_by(run) if run.get("state") == "paused" else "",
            # A worker's sleep while it waits for something outside Raticode, or null. Older
            # phones ignore it and show waiting_reason; it also tells a phone Wake now works.
            "sleep": public_sleep(run),
        }

    def _task_summary(
        self,
        peer: str,
        task: dict[str, Any],
        catalog: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        from gofer.ui.rem_home_store import ran_at, retention

        schedule = task.get("schedule") or {}
        # listed, finished_at and cleanup_at: whether a finished one-time task is still listed.
        kept = retention(task)
        return {
            "task_id": _text(task.get("id"), 128),
            "title": _text(task.get("title"), 160),
            "type": _text(task.get("type"), 20),
            "state": _text(task.get("state"), 20),
            "revision": int(task.get("revision") or 1),
            "schedule": {
                "kind": _text(schedule.get("kind") or "once", 20),
                "at": _text(schedule.get("at"), 80),
                "local_time": _text(schedule.get("local_time"), 10),
                "weekdays": [_text(day, 12) for day in (schedule.get("weekdays") or [])][:7],
                "every_minutes": int(schedule.get("every_minutes") or 0),
                "timezone": _text(schedule.get("timezone"), 80),
            },
            "next_occurrence_at": _text(task.get("next_occurrence_at"), 80),
            "project_name": self._project_name(peer, task.get("project_dir"), catalog),
            "active_run": self._run(task.get("active_run")),
            "last_run": self._run(task.get("last_run")),
            "ran_at": _text(ran_at(task), 80),
            **{key: value if key == "listed" else _text(value, 80) for key, value in kept.items()},
            # Only while the task waits for other tasks to succeed; older desktops never send it.
            **({"dependency": _dependency(task["dependency"])} if task.get("dependency") else {}),
        }

    def _rem_task(
        self, peer: str, identifier: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = data or self._services("rem.task", {})
        task = next((t for t in data["tasks"] if t.get("id") == identifier), None)
        if task is None:
            raise Refused("not_found", "This Rem task no longer exists.")
        summary = self._task_summary(peer, task)
        # The newest run's checks one by one, with each reason. Only on this read:
        # task lists and older runs keep their counts.
        for key in ("active_run", "last_run"):
            if summary[key] is not None:
                summary[key]["criteria"] = _check_details(task[key].get("criteria"))
        runs = [self._run(run) for run in (task.get("recent_runs") or [])[:20]]
        return {
            **summary,
            "tickets": _ticket_index(
                data.get("tickets"),
                _shown_keys([], [{**summary, "runs": runs}]),
            ),
            "prompt": _text(task.get("prompt"), 8000),
            "provider": _text(task.get("provider"), 80),
            "model": _text(task.get("model"), 160),
            "effort": _text(task.get("effort"), 80),
            "criteria": [
                _text(c.get("description"), 600) for c in task.get("acceptance_criteria") or []
            ][:40],
            "runs": runs,
        }

    def _rem_thread(self, identifier: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        data = data or self._services("rem.thread", {"thread_id": identifier})
        thread = data["thread"]
        metadata = thread.get("metadata") or {}
        # Hidden context must not split a run of thoughts, as on the desktop.
        entries = _thread_entries(
            [
                m
                for m in thread.get("messages") or []
                if m.get("role") in {"user", "assistant"}
                and m.get("kind") not in {"memory", "continuation-context"}
            ]
        )
        shown = _newest(entries[-120:])
        return {
            "thread_id": identifier,
            "title": _text(metadata.get("title") or "Task thread", 160),
            "running": thread.get("running") is True,
            "earlier": len(entries) - len(shown),
            "messages": shown,
        }

    def _organizations(self, peer: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        listed = (data or self._services("organizations.list", {}))["listed"]
        catalog = self.projects(peer)
        return {
            "organizations": [
                {
                    "organization_id": _text(item.get("id"), 100),
                    "name": _text(item.get("name"), 200),
                    "state": _text(item.get("state"), 20),
                    "pause_reason": _text(item.get("pauseReason"), 300),
                    "employees": int(item.get("employees") or 0),
                    "open_tasks": int(item.get("openTasks") or 0),
                    "projects": [
                        self._project_name(peer, root, catalog)
                        for root in item.get("projectRoots") or []
                    ][:20],
                }
                for item in listed
            ][:100]
        }

    def _organizations_manager(self) -> Any:
        manager = self._need(self.organizations, "Organizations")
        if not manager.enabled:
            raise Refused(
                "unavailable",
                "Organizations is experimental and turned off on this desktop.",
            )
        return manager

    def _organization(
        self, peer: str, identifier: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = data or self._services("organizations.read", {"organization_id": identifier})
        org = data["org"]
        config = org.get("config") or {}
        runtime = org.get("runtime") or {}
        diagnostics = org.get("diagnostics") or {}
        employees = {e.get("id"): e for e in config.get("employees") or []}
        attention = set(diagnostics.get("attention") or [])
        claims = organization_view.claims_by_task(runtime)
        reads = organization_view.note_reads(runtime)
        catalog = self.projects(peer)
        tasks = []
        for task in (runtime.get("tasks") or [])[-200:]:
            conversation = visible_comments(task.get("comments") or [])
            tasks.append(
                {
                    **organization_view.task_extras(task, diagnostics, claims, reads),
                    "task_id": _text(task.get("id"), 100),
                    "title": _text(task.get("title"), 500),
                    "description": _text(task.get("description"), 4000),
                    "status": _text(task.get("status"), 20),
                    "priority": _text(task.get("priority"), 20),
                    "assignee": _text(
                        (employees.get(task.get("assignee")) or {}).get("name")
                        or task.get("assignee"),
                        150,
                    ),
                    "revision": int(task.get("revision") or 1),
                    "running": bool(task.get("claim")),
                    "needs_approval": bool(task.get("approvalRequired"))
                    and task.get("approvedRevision") is None,
                    "needs_review": task.get("status") == "in_review",
                    "attention": task.get("id") in attention,
                    "block_reason": _text(task.get("blockReason"), 600),
                    "evidence": _text(task.get("evidence"), 1500),
                    "due_at": _text(task.get("dueAt"), 80),
                    # The conversation as people read it: a status entry is marked as one, and
                    # a repeat of the comment above it is left out (rem_ticket_activity).
                    "comments": [
                        {
                            "actor": _text(_comment_author(c.get("actor"), employees), 150),
                            "at": _text(c.get("at"), 80),
                            "body": _text(c.get("body"), 1500),
                            "decision": _text(c.get("decision"), 20),
                            **(
                                {
                                    "kind": "status",
                                    "title": _text(c.get("title"), 200),
                                    "from": _text(c.get("from"), 20),
                                    "to": _text(c.get("to"), 20),
                                }
                                if c.get("kind") == "status"
                                else {}
                            ),
                        }
                        for c in conversation[-5:]
                    ],
                    "comment_count": len(conversation),
                }
            )
        return {
            "organization_id": identifier,
            "name": _text(config.get("name"), 200),
            "description": _text(config.get("description"), 1500),
            "state": _text(runtime.get("state"), 20),
            "pause_reason": _text(runtime.get("pauseReason"), 300),
            "usage": _organization_usage(runtime),
            "pending_requests": sum(
                r.get("status") == "pending" and time.time() - _timestamp(r.get("at")) < 24 * 3600
                for r in (runtime.get("consentRequests") or {}).values()
            ),
            "projects": [
                self._project_name(peer, root) for root in config.get("projectRoots") or []
            ][:20],
            "employees": [
                {
                    "employee_id": _text(e.get("id"), 100),
                    "name": _text(e.get("name"), 150),
                    "title": _text(e.get("title") or e.get("role"), 150),
                    "reports_to": _text(e.get("reportsTo"), 100),
                    "provider": _text(e.get("provider"), 80),
                    "model": _text(e.get("model"), 160),
                    "paused": e.get("paused") is True,
                }
                for e in employees.values()
            ][:100],
            "tasks": _newest(tasks),
            "revision": int(org.get("revision") or 0),
            **organization_view.extend(
                org,
                lambda root: self._project_name(peer, root, catalog),
                data.get("repository"),
            ),
        }

    def _usage(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        overview = (data or self._services("usage.overview", {}))["overview"]
        activity = overview.get("activity") or {}
        return {
            "days": int(overview.get("days") or 30),
            "observed_at": _text(overview.get("observed_at"), 80),
            "accounts": [
                {
                    "provider": _text(a.get("provider"), 80),
                    "label": _text(a.get("label") or a.get("provider"), 160),
                    "status": _text(a.get("status"), 40),
                    "reason": _text(a.get("reason"), 600),
                    "observed_at": _text(a.get("observed_at"), 80),
                    "windows": [
                        {
                            "label": _text(w.get("label") or w.get("id"), 160),
                            "used_percent": w.get("used_percent")
                            if isinstance(w.get("used_percent"), int | float)
                            else (
                                100 - w["remaining_percent"]
                                if isinstance(w.get("remaining_percent"), int | float)
                                else None
                            ),
                            "resets_at": _text(w.get("resets_at"), 80),
                        }
                        for w in (a.get("windows") or [])[:12]
                    ],
                }
                for a in (overview.get("accounts") or [])[:40]
            ],
            "activity": {
                "calls": int(activity.get("calls") or 0),
                "input_tokens": activity.get("input_tokens"),
                "output_tokens": activity.get("output_tokens"),
                "providers": [
                    {
                        "provider": _text(p.get("provider"), 80),
                        "calls": int(p.get("calls") or 0),
                        "input_tokens": p.get("input_tokens"),
                        "output_tokens": p.get("output_tokens"),
                    }
                    for p in (activity.get("providers") or [])[:40]
                ],
            },
        }

    # Actions ---------------------------------------------------------------

    def _act(self, peer: str, op: str, params: dict[str, Any], request: str) -> Any:
        if op == "browser.open":
            from gofer.devices.phone_browser import BrowserRefused

            try:
                return self.browser.open(params, request)
            except BrowserRefused as exc:
                raise Refused(exc.code, str(exc)) from None
        if op == "files.link":
            return self._file_link(peer, params)
        if op == "files.open":
            return self._file_open(peer, params)
        if op == "rem.attachment":
            return self._rem_attachment(peer, params)
        if op.startswith("rem."):
            return self._rem_action(peer, op, params, request)
        if op.startswith("workflows."):
            return self.workflows.act(peer, op, params, request)
        if op == "organizations.control":
            manager = self._organizations_manager()
            if params.get("state") not in {"running", "paused"}:
                raise Refused("invalid", "Choose running or paused.")
            identifier = _text(params.get("organization_id"), 100)
            # A phone with Use Raticode from this phone on is the user, so it may start an
            # organization whose workers run without a sandbox, as the desktop can.
            manager.call(
                "",
                "user",
                {
                    "action": "control",
                    "organizationId": identifier,
                    "params": {"state": params["state"]},
                },
            )
            return self._organization(peer, identifier)
        if op == "kanban.participant":
            return self._kanban_participant(peer, params)
        if op == "kanban.ticket":
            return self._kanban_ticket(peer, params, request)
        if op == "selfwork.settings":
            return self._need(self.rem, "Rem").self_work.update_settings(dict(params))
        if op == "selfwork.report.seen":
            return self._need(self.rem, "Rem").self_work.mark_seen(params.get("updated_at"))
        if op == "organizations.task":
            return self._organization_task(peer, params, request)
        if op == "organizations.knowledge":
            return self._organization_note(peer, params)
        if op == "organizations.worker":
            return self._organization_worker(peer, params)
        if op == "organizations.schedule":
            return self._organization_schedule(peer, params)
        if op == "organizations.backlog_pull":
            return self._organization_backlog_pull(peer, params)
        if op == "organizations.requests":
            return self._organization_requests(peer, params)
        if op == "organizations.auto_mode":
            return self._organization_auto_mode(peer, params)
        if op == "swarms.start":
            remote = self.app.remote
            self._need(remote.swarms, "Swarms")
            task = _text(params.get("task"), 4000).strip()
            if not task:
                raise Refused("invalid", "Describe the task for the swarm.")
            root, identifier = remote._swarm(peer, _text(params.get("swarm_id"), 36))
            # Starting from the phone is the user's own confirmation, as Start is on the
            # desktop: a phone with Use Raticode from this phone on is the user.
            _, grants = remote.projects(peer)
            remote.swarms.start(
                root,
                identifier,
                task,
                workspace_grants=grants,
                confirm_full_access=True,
                confirmed_by="user",
            )
            return {"started": True}
        if op == "settings.update":
            return self._update_settings(params, request)
        raise Refused("invalid", "Unknown desktop action.")

    def _rem_action(self, peer: str, op: str, params: dict[str, Any], request: str) -> Any:
        if op == "rem.tidy":
            return self._tidy(peer, params, request)
        rem = self._need(self.rem, "Rem")
        if op == "rem.message":
            self.db.execute(
                "INSERT OR IGNORE INTO device_rem_requests VALUES (?,?)", (peer, request)
            )
            message: dict[str, Any] = {
                "request_id": request,
                "body": _text(params.get("body"), 20000),
            }
            refers_to = params.get("refers_to")
            if refers_to is not None:
                # The chat message this one replies to; the desktop looks it up itself.
                if not isinstance(refers_to, str) or not 0 < len(refers_to) <= 128:
                    raise Refused("invalid", "Reply to a message from this chat.")
                message["refers_to"] = refers_to
            if params.get("screen") is not None:
                # Text from the phone's screen for this reply only (rem_screen); never stored.
                from gofer.ui.rem_screen import clean_screen

                try:
                    message["screen"] = clean_screen(params["screen"])
                except ValueError as exc:
                    raise Refused("invalid", str(exc)) from None
            identifiers = params.get("attachment_ids") or []
            if (
                not isinstance(identifiers, list)
                or len(identifiers) > REM_FILES
                or len(set(map(str, identifiers))) != len(identifiers)
            ):
                raise Refused("invalid", f"Attach at most {REM_FILES} different files.")
            snapshot = rem.store.conversation()
            # A resent request Rem already holds, answered or still waiting, isn't stored twice.
            sent = any(
                m.get("id") == request
                for m in [*snapshot["messages"], *(snapshot.get("waiting") or [])]
            )
            if identifiers and not sent:
                message["attachments"] = self._rem_attachments(peer, rem, identifiers)
            try:
                if not sent:
                    rem.submit_message(message, source="phone")
            except Exception:
                self._discard(rem, message.get("attachments") or [])
                raise
            # The desktop keeps its own copy; free the phone channel's quota.
            for identifier in identifiers:
                self.app.files.cancel(peer, self.files_thread(peer), str(identifier))
            return self._rem_home(peer)
        if op == "rem.instruct":
            thread = _text(params.get("thread_id"), 128)
            rem.instruct(thread, {"request_id": request, "body": _text(params.get("body"), 20000)})
            return self._rem_thread(thread)
        if op.startswith("rem.memory."):
            return self._rem_memory_action(rem, op, params)
        if op == "rem.task.create":
            fields: dict[str, Any] = {
                key: params[key]
                for key in ("title", "type", "prompt", "schedule", "execution_mode")
                if key in params
            }
            project = params.get("project_id")
            if project:
                known = self.projects(peer).get(str(project))
                if known is None:
                    raise Refused("invalid", "That project is no longer open on desktop.")
                fields["project_dir"] = known["root"]
            rem.create_task({"request_id": request, **fields})
            return self._rem_home(peer)
        task = _text(params.get("task_id"), 128)
        if op in {"rem.task.run", "rem.task.pause", "rem.task.delete"}:
            body: dict[str, Any] = {"request_id": request}
            if op == "rem.task.pause":
                body["cancel_current"] = params.get("cancel_current") is True
            rem.edit_task(task, body, op.rsplit(".", 1)[1])
            return self._rem_home(peer)
        if op == "rem.task.resume":
            revision = params.get("expected_revision")
            if not isinstance(revision, int):
                raise Refused("invalid", "Refresh Rem before resuming this task.")
            rem.edit_task(
                task,
                {"request_id": request, "expected_revision": revision, "enabled": True},
                "edit",
            )
            return self._rem_home(peer)
        if op in {"rem.run.cancel", "rem.run.acknowledge"}:
            rem.store.mutate_run(
                _text(params.get("run_id"), 128), {"request_id": request}, op.rsplit(".", 1)[1]
            )
            return self._rem_home(peer)
        if op in {"rem.run.pause", "rem.run.resume"}:
            # Pausing keeps the run, its thread and worktree; resuming continues that thread.
            run_id = _text(params.get("run_id"), 128)
            if op == "rem.run.pause":
                rem.pause_run(run_id, {"request_id": request})
            else:
                rem.resume_run(run_id, {"request_id": request})
            return self._rem_home(peer)
        if op == "rem.run.retry_review":
            # The same desktop control as the chat's "retry the review"; it refuses runs
            # without an interrupted review or whose task changed since.
            rem.retry_review(_text(params.get("run_id"), 128), {"request_id": request})
            return self._rem_home(peer)
        if op == "rem.run.wake":
            # Wake now: a sleeping worker resumes in its thread without waiting out its time.
            rem.wake_run(_text(params.get("run_id"), 128), {"request_id": request})
            return self._rem_home(peer)
        raise Refused("invalid", "Unknown Rem action.")

    @staticmethod
    def _voice_tidy_on() -> bool:
        from gofer.core.provider_preferences import voice_tidy_preference

        return voice_tidy_preference().get("enabled") is True

    def _tidy(self, peer: str, params: dict[str, Any], request: str) -> dict[str, Any]:
        """Start tidying a dictation; the answer follows as a second result.

        The provider call takes seconds and runs off the device lock and the
        connection's loop. Every outcome answers, so the phone never waits on
        a request the desktop forgot.
        """
        rem = self._need(self.rem, "Rem")
        heard = params.get("text")
        if not isinstance(heard, str) or not heard.strip():
            raise Refused("invalid", "Nothing was heard to tidy.")
        if len(heard) > 4000:
            raise Refused("invalid", "This message is too long to tidy.")
        if not self._voice_tidy_on():
            return {"text": heard, "tidied": False, "reason": "Tidying is off on your desktop."}
        source = {"request_id": request, "thread_id": None}
        self.tidy_started(peer, source, heard, rem)
        return {"pending": True}

    def tidy_started(self, peer: str, source: dict[str, Any], heard: str, rem: Any) -> None:
        import threading

        from gofer.ui import voice_tidy

        data_dir = Path(getattr(rem, "data_dir", None) or Path.home())

        def work() -> None:
            try:
                chosen = voice_tidy.current(None, rem)
                context = (
                    voice_tidy.chat_context(rem)
                    if chosen.get("available") and chosen.get("context")
                    else ""
                )
                result = voice_tidy.tidy(heard, chosen, data_dir, context=context)
            except Exception as exc:  # noqa: BLE001 - the phone still gets its words back
                logger.warning("Phone voice tidy failed: %s", type(exc).__name__)
                result = {"text": heard, "tidied": False, "reason": "Tidying failed."}
            self.tidy_finished(peer, source, result)

        threading.Thread(target=work, name="phone-voice-tidy", daemon=True).start()

    def tidy_finished(self, peer: str, source: dict[str, Any], result: dict[str, Any]) -> None:
        try:
            with self.app.registry.transaction():
                self.app._trusted(peer)
                if not self.allowed(peer):
                    return
                self._answer(peer, source, "rem.tidy", result)
        except Exception as exc:  # noqa: BLE001 - a revoked or full phone just misses it
            logger.warning("Phone voice tidy answer not sent: %s", type(exc).__name__)

    def _rem_memories(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """What Rem remembers, as the phone's memory screen shows it."""
        listed = (data or self._services("rem.memories", {}))["memories"]
        return {
            "mode": _text(listed.get("mode") or "on", 20),
            "memories": [
                {
                    "id": _text(item.get("id"), 64),
                    "text": _text(item.get("text"), 2000),
                    "created_at": _text(item.get("created_at"), 80),
                    "edited_at": _text(item.get("edited_at"), 80),
                    "added_by_user": item.get("added_by") == "user",
                }
                for item in listed.get("memories") or []
                if isinstance(item, dict)
            ][:500],
        }

    def _rem_memory_action(self, rem: Any, op: str, params: dict[str, Any]) -> dict[str, Any]:
        """The phone changes memories the same way the desktop panel does.

        Repeating an action is harmless: adding the same words, forgetting
        twice or undoing twice changes nothing.
        """
        identity = _text(params.get("memory_id"), 64)
        if op == "rem.memory.add":
            rem.add_memory({"text": _text(params.get("text"), 2000)})
        elif op == "rem.memory.edit":
            rem.edit_memory(identity, {"text": _text(params.get("text"), 2000)})
        elif op == "rem.memory.forget":
            try:
                rem.forget_memory(identity)
            except KeyError:
                pass  # Already forgotten, perhaps by this same request's first delivery.
        elif op == "rem.memory.mode":
            rem.memory_mode({"mode": _text(params.get("mode"), 20)})
        elif op == "rem.memory.undo":
            rem.undo_memory(_text(params.get("message_id"), 128))
        else:
            raise Refused("invalid", "Unknown Rem memory action.")
        return self._rem_memories()

    def _rem_attachment(self, peer: str, params: dict[str, Any]) -> dict[str, Any]:
        """Offer one file attached to the user's message in Rem's chat to the phone.

        The phone names the message and the attachment, never a path. The file is read
        from Rem's chat attachment folder, where the desktop window and every phone's
        uploads are stored, through the same scoped snapshot as any other phone file.
        """
        from gofer.ui.chat_media import attachment_thread_dir
        from gofer.ui.rem_home import REM_ATTACHMENT_THREAD

        message_id = params.get("message_id")
        identifier = params.get("file_id")
        if (
            not isinstance(message_id, str)
            or not 0 < len(message_id) <= 200
            or not isinstance(identifier, str)
            or not 0 < len(identifier) <= 128
        ):
            raise Refused("invalid", "This file can't be opened from the phone.")
        rem = self._need(self.rem, "Rem")
        snapshot = rem.store.conversation()
        messages = [*(snapshot.get("messages") or []), *(snapshot.get("waiting") or [])]
        message = next(
            (m for m in messages if m.get("id") == message_id and m.get("role") == "user"), None
        )
        if message is None:
            raise Refused("not_found", "This message is no longer in Rem's chat.")
        attachment = next(
            (
                a
                for a in message.get("attachments") or []
                if isinstance(a, dict) and str(a.get("id") or "") == identifier
            ),
            None,
        )
        storage = attachment.get("storageName") if attachment else None
        if (
            attachment is None
            or not isinstance(storage, str)
            or storage in {"", ".", ".."}
            or Path(storage).name != storage
        ):
            raise Refused("not_found", "This file is no longer in Rem's chat.")
        folder = attachment_thread_dir(Path(rem.data_dir).resolve(), REM_ATTACHMENT_THREAD)
        path = folder / storage
        if not path.is_file():
            raise Refused("not_found", "This file is no longer on the desktop.")
        if path.stat().st_size > MAX_FILE:
            raise Refused(
                "unavailable",
                "This file is larger than the 10 MB the phone can hold. Open it on the desktop.",
            )
        # The stored name has a unique prefix; the phone gets the name it was attached with.
        name = _text(attachment.get("name"), 160)
        thread = self.files_thread(peer)
        try:
            offer: dict[str, Any] = self.app.files.snapshot(
                peer, thread, folder, path, name=name if _phone_name(name) else None
            )
        except PairingError as exc:
            if str(exc) == "scoped_file_unavailable":
                raise Refused("not_found", "This file is no longer on the desktop.") from None
            raise _snapshot_refused(exc) from None
        self.app._emit(peer, {"thread_id": thread, "request_id": str(uuid4())}, "file.offer", offer)
        return {"file_id": offer["file_id"], "name": offer["name"], "size": offer["size"]}

    def _rem_attachments(self, peer: str, rem: Any, identifiers: list[Any]) -> list[dict[str, Any]]:
        """Copy files the phone uploaded on its channel into Rem's chat attachments."""
        from gofer.ui.chat_media import store_chat_attachments
        from gofer.ui.rem_home import REM_ATTACHMENT_THREAD

        thread = self.files_thread(peer)
        saved: list[dict[str, Any]] = []
        try:
            for identifier in identifiers:
                try:
                    offer, data = self.app.files.content(peer, thread, str(identifier))
                except PairingError:
                    raise Refused(
                        "invalid", "An attachment is no longer on the desktop. Attach it again."
                    ) from None
                mime = mimetypes.guess_type(offer["name"])[0] or offer["mime"]
                stored = store_chat_attachments(
                    {
                        "threadId": REM_ATTACHMENT_THREAD,
                        "files": [
                            {
                                "name": offer["name"],
                                "type": mime,
                                "data": base64.b64encode(data).decode("ascii"),
                            }
                        ],
                    },
                    rem.data_dir,
                )["attachments"][0]
                # The phone's file ID lets it match the message's files to its own.
                saved.append({**stored, "id": str(identifier)})
        except Exception:
            self._discard(rem, saved)
            raise
        return saved

    @staticmethod
    def _discard(rem: Any, attachments: list[dict[str, Any]]) -> None:
        from gofer.ui.chat_media import attachment_thread_dir
        from gofer.ui.rem_home import REM_ATTACHMENT_THREAD
        from gofer.utils.atomic_output import unlink_without_links

        folder = attachment_thread_dir(Path(rem.data_dir).resolve(), REM_ATTACHMENT_THREAD)
        for attachment in attachments:
            try:
                unlink_without_links(folder / attachment["storageName"])
            except OSError:
                pass

    @staticmethod
    def _kanban_board(board: dict[str, Any], catalog: dict[str, dict[str, str]]) -> dict[str, Any]:
        """The global board for a phone: project IDs instead of folder paths."""
        known = {project["root"]: key for key, project in catalog.items()}

        def ids(roots: list[str]) -> list[str]:
            return [known[root] for root in roots if root in known]

        return {
            **board,
            "tickets": [
                {
                    **{k: v for k, v in ticket.items() if k != "project_dir"},
                    "priority": "urgent"
                    if ticket["priority"] == "critical"
                    else ticket["priority"],
                    "project_id": known.get(ticket.get("project_dir") or "", ""),
                }
                for ticket in board["tickets"]
            ],
            "participants": [
                {
                    **participant,
                    "projects": ids(participant["projects"]),
                    "assignable": ids(participant["assignable"]),
                    "unavailable": ids(participant["unavailable"]),
                    "hidden_projects": sum(r not in known for r in participant["projects"]),
                }
                for participant in board["participants"]
            ],
            "projects": [
                {
                    "project_id": known[project["root"]],
                    "name": project["name"],
                    "available": project["available"],
                    "organization_name": project["organization_name"],
                }
                for project in board["projects"]
                if project["root"] in known
            ],
        }

    @classmethod
    def _kanban_timeline(
        cls, timeline: dict[str, Any], catalog: dict[str, dict[str, str]]
    ) -> dict[str, Any]:
        """One ticket and its timeline for a phone, small enough for one reply.

        The newest entries come first to go when the reply would be too long; how many
        were left out is counted in ``earlier``.
        """
        ticket = cls._kanban_board(
            {"tickets": [timeline["ticket"]], "participants": [], "projects": []}, catalog
        )["tickets"][0]
        comments = list(timeline["comments"])
        earlier = int(timeline.get("earlier") or 0)
        budget = 400_000
        kept: list[dict[str, Any]] = []
        for entry in reversed(comments):
            budget -= len(json.dumps(entry))
            if budget < 0:
                break
            kept.append(entry)
        earlier += len(comments) - len(kept)
        return {
            "ticket": ticket,
            "comments": list(reversed(kept)),
            "earlier": earlier,
            "can_comment": bool(timeline.get("can_comment")),
        }

    def _kanban_participant(self, peer: str, params: dict[str, Any]) -> dict[str, Any]:
        """Set a participant's projects from a phone, keeping ones this phone can't see."""
        rem = self._need(self.rem, "Rem")
        catalog = self.projects(peer)
        wanted = params.get("projects")
        if not isinstance(wanted, list) or not all(isinstance(p, str) for p in wanted):
            raise Refused("invalid", "Choose projects.")
        if any(p not in catalog for p in wanted):
            raise Refused("invalid", "That project is no longer open on desktop.")
        identity = _text(params.get("participant_id"), 300)
        participant = next(
            (p for p in rem.kanban.board()["participants"] if p["participant_id"] == identity),
            None,
        )
        if participant is None:
            raise Refused("not_found", "That participant isn't available.")
        visible = {project["root"] for project in catalog.values()}
        rem.kanban.set_participant_projects(
            "user",
            {
                "participant_id": identity,
                "projects": [
                    *(catalog[p]["root"] for p in wanted),
                    *(r for r in participant["projects"] if r not in visible),
                ],
                "expected_revision": params.get("expected_revision"),
            },
        )
        return self._kanban_board(rem.kanban.board(), catalog)

    def _kanban_ticket(self, peer: str, params: dict[str, Any], request: str) -> dict[str, Any]:
        """One board change from a phone; the desktop checks and applies it as the user's."""
        rem = self._need(self.rem, "Rem")
        payload = {k: v for k, v in params.items() if k != "project_dir"}
        project = payload.pop("project_id", None)
        if project:
            known = self.projects(peer).get(str(project))
            if known is None:
                raise Refused("invalid", "That project is no longer open on desktop.")
            payload["project_dir"] = known["root"]
        rem.kanban.act("user", payload, request)
        return self._kanban_board(rem.kanban.board(), self.projects(peer))

    def _organization_task(self, peer: str, params: dict[str, Any], request: str) -> Any:
        manager = self._organizations_manager()
        identifier = _text(params.get("organization_id"), 100)
        action = params.get("action")
        task = _text(params.get("task_id"), 100)
        revision = params.get("expected_revision")
        if action == "create":
            status = params.get("status", "todo")
            if status not in {"backlog", "todo"}:
                raise Refused("invalid", "New tickets start in Backlog or To do.")
            arguments: dict[str, Any] = {
                "action": "task_create",
                "params": {
                    "title": _text(params.get("title"), 500),
                    "description": _text(params.get("description"), 100000),
                    "priority": params.get("priority", "medium"),
                    "idempotencyKey": request,
                    **({"status": status} if status != "todo" else {}),
                    **({"assignee": params["assignee"]} if params.get("assignee") else {}),
                    **({"epicId": _text(params["epic_id"], 100)} if params.get("epic_id") else {}),
                    **(
                        {"project": _text(params["project_id"], 100)}
                        if params.get("project_id")
                        else {}
                    ),
                },
            }
        elif action == "move":
            return self._move_ticket(
                peer, manager, identifier, task, revision, params.get("column")
            )
        elif action == "comment":
            arguments = {
                "action": "comment",
                "params": {
                    "taskId": task,
                    "body": _text(params.get("body"), 100000),
                    "idempotencyKey": request,
                },
            }
        elif action in {"approve", "reopen"}:
            arguments = {"action": action, "params": {"taskId": task, "expectedRevision": revision}}
        elif action == "review":
            arguments = {
                "action": "review",
                "params": {
                    "taskId": task,
                    "expectedRevision": revision,
                    "decision": params.get("decision"),
                    "body": _text(params.get("body"), 100000),
                },
            }
        elif action == "cancel":
            arguments = {
                "action": "task_update",
                "params": {
                    "taskId": task,
                    "expectedRevision": revision,
                    "changes": {"status": "cancelled"},
                },
            }
        elif action == "wake":
            arguments = {
                "action": "wake",
                "params": {
                    "employeeId": _text(params.get("employee_id"), 100),
                    "message": _text(params.get("body"), 4000) or "Check in",
                    "idempotencyKey": request,
                },
            }
        else:
            raise Refused("invalid", "Unknown organization task action.")
        manager.call("", "user", {**arguments, "organizationId": identifier})
        return self._organization(peer, identifier)

    def _move_ticket(
        self, peer: str, manager: Any, identifier: str, task_id: str, revision: Any, column: Any
    ) -> Any:
        """Moves a ticket between Backlog, To do and In progress, as the desktop board does."""
        from gofer.ui.rem_kanban import move_organization_ticket

        def call(organization: str, action: str, arguments: dict[str, Any]) -> Any:
            return manager.call(
                "", "user", {"action": action, "organizationId": organization, "params": arguments}
            )

        move_organization_ticket(identifier, task_id, revision, column, call)
        return self._organization(peer, identifier)

    def _organization_note(self, peer: str, params: dict[str, Any]) -> Any:
        """A new note in the organization's knowledge base. Edits stay on the desktop."""
        manager = self._organizations_manager()
        identifier = _text(params.get("organization_id"), 100)
        if params.get("action") != "create":
            raise Refused("invalid", "Unknown knowledge action.")
        kind = params.get("kind", "note")
        if kind not in organization_view.KINDS:
            raise Refused("invalid", "Choose a decision, note, report, spec or finding.")
        title = _text(params.get("title"), 300).strip()
        if not title:
            raise Refused("invalid", "Give the note a title.")
        entry: dict[str, Any] = {
            "kind": kind,
            "title": title,
            "topic": _text(params.get("topic"), 120).strip() or "General",
            "summary": _text(params.get("summary"), 1000),
            "body": _text(params.get("body"), 50000),
        }
        if params.get("project_id"):
            entry["projectId"] = _text(params.get("project_id"), 100)
        # A resent request is answered from the device inbox, so this runs once per request.
        manager.call(
            "",
            "user",
            {"action": "knowledge_save", "organizationId": identifier, "params": {"entry": entry}},
        )
        return self._organization(peer, identifier)

    def _organization_worker(self, peer: str, params: dict[str, Any]) -> Any:
        """Holds a worker or releases its hold, whatever its permissions, as the desktop can."""
        manager = self._organizations_manager()
        identifier = _text(params.get("organization_id"), 100)
        employee = _text(params.get("employee_id"), 100)
        paused = params.get("paused")
        if type(paused) is not bool:
            raise Refused("invalid", "Choose hold or release.")

        current = manager.store.get("", identifier)
        config = current["config"]
        employees = config.get("employees") or []
        found = next((e for e in employees if e.get("id") == employee), None)
        if found is None:
            raise Refused("not_found", "This worker is no longer in the organization.")
        if found.get("paused") is paused:
            return self._organization(peer, identifier)
        name = found.get("name") or employee
        manager.call(
            "",
            "user",
            {
                "action": "configure",
                "organizationId": identifier,
                "params": {
                    "config": {
                        **config,
                        "employees": [
                            {**e, "paused": paused} if e.get("id") == employee else e
                            for e in employees
                        ],
                    },
                    "expectedRevision": current["revision"],
                    "reason": f"{'Held' if paused else 'Released'} {name} from the phone",
                },
            },
        )
        return self._organization(peer, identifier)

    def _organization_schedule(self, peer: str, params: dict[str, Any]) -> Any:
        """Saves the organization's work schedule, and its time zone, from the phone."""
        manager = self._organizations_manager()
        identifier = _text(params.get("organization_id"), 100)
        revision = params.get("expected_revision")
        blocks = params.get("blocks")
        if type(revision) is not int:
            raise Refused("invalid", "Refresh the organization, then save again.")
        if not isinstance(blocks, list) or len(blocks) > 40:
            raise Refused("invalid", "A work schedule has at most 40 blocks.")
        fields = {
            "block_id": "id",
            "label": "label",
            "focus": "focus",
            "cadence": "cadence",
            "start": "start",
            "end": "end",
            "days": "days",
            "month_day": "monthDay",
            "nth_week": "nthWeek",
            "weekday": "weekday",
            "quarter_month": "quarterMonth",
            "keywords": "keywords",
        }
        converted = []
        for block in blocks:
            if not isinstance(block, dict):
                raise Refused("invalid", "Each block needs a name, times and how often it repeats.")
            converted.append(
                {
                    name: block[key]
                    for key, name in fields.items()
                    if key in block and block[key] is not None
                }
            )
        current = manager.store.get("", identifier)
        item: dict[str, Any] = {"workSchedule": {"blocks": converted}}
        hours = current["config"].get("operatingHours") or {"mode": "always"}
        zone = params.get("timezone")
        if isinstance(zone, str) and zone and zone != hours.get("timezone", "UTC"):
            # One time zone serves working hours and the schedule.
            item["operatingHours"] = {**hours, "timezone": _text(zone, 80)}
        manager.call(
            "",
            "user",
            {
                "action": "plan_save",
                "organizationId": identifier,
                "params": {
                    "kind": "operations",
                    "item": item,
                    "expectedRevision": revision,
                    "reason": "Changed the work schedule from the phone",
                },
            },
        )
        return self._organization(peer, identifier)

    def _organization_requests(self, peer: str, params: dict[str, Any]) -> Any:
        """Approves or declines Rem's requests the user confirmed on the phone.

        ``request_ids`` is the exact list the phone showed; requests that arrived later
        are never in it. Approving uses the desktop's Approve all order and checks
        (organization_consent), and each result comes back in ``request_results``.
        """
        from gofer.ui.organization_consent import MAX_BATCH, approve_all
        from gofer.ui.organizations import CONSENT_TTL

        manager = self._organizations_manager()
        identifier = _text(params.get("organization_id"), 100)
        decision = params.get("decision")
        ids = params.get("request_ids")
        if decision not in {"approve", "decline"}:
            raise Refused("invalid", "Choose approve or decline.")
        if (
            not isinstance(ids, list)
            or not 0 < len(ids) <= MAX_BATCH
            or not all(isinstance(i, str) and 0 < len(i) <= 100 for i in ids)
            or len(set(ids)) != len(ids)
        ):
            raise Refused("invalid", "Refresh the organization, then choose the requests again.")
        if decision == "decline":
            results = []
            for rid in ids:
                try:
                    manager.call(
                        "",
                        "user",
                        {
                            "action": "request_decide",
                            "organizationId": identifier,
                            "params": {"requestId": rid, "decision": "decline"},
                        },
                    )
                    results.append({"requestId": rid, "status": "declined", "message": ""})
                except ValueError as exc:
                    results.append({"requestId": rid, "status": "failed", "message": str(exc)})
        else:
            results = approve_all(
                manager,
                "",
                identifier,
                ids,
                ttl_seconds=CONSENT_TTL.total_seconds(),
            )["results"]
        return {
            **self._organization(peer, identifier),
            "request_results": [
                {
                    "request_id": _text(r.get("requestId"), 100),
                    "summary": _text(r.get("summary"), 400),
                    "status": _text(r.get("status"), 20),
                    "message": _text(r.get("message"), 600),
                }
                for r in results
            ],
        }

    def _organization_auto_mode(self, peer: str, params: dict[str, Any]) -> Any:
        """Turns Auto mode on or off from the phone: the user's own switch.

        Turning it on needs ``confirm`` true, which the phone sends only after showing what
        Auto mode allows.
        """
        manager = self._organizations_manager()
        identifier = _text(params.get("organization_id"), 100)
        revision = params.get("expected_revision")
        enabled = params.get("enabled")
        if type(revision) is not int:
            raise Refused("invalid", "Refresh the organization, then try again.")
        if not isinstance(enabled, bool):
            raise Refused("invalid", "Say whether Auto mode is on.")
        if enabled and params.get("confirm") is not True:
            raise Refused("invalid", "Confirm what Auto mode allows before turning it on.")
        manager.call(
            "",
            "user",
            {
                "action": "auto_mode",
                "organizationId": identifier,
                "params": {"enabled": enabled, "confirm": enabled, "expectedRevision": revision},
            },
        )
        return self._organization(peer, identifier)

    def _organization_backlog_pull(self, peer: str, params: dict[str, Any]) -> Any:
        """Turns workers pulling Backlog tickets into To do on or off, from the phone."""
        manager = self._organizations_manager()
        identifier = _text(params.get("organization_id"), 100)
        revision = params.get("expected_revision")
        enabled = params.get("enabled")
        if type(revision) is not int:
            raise Refused("invalid", "Refresh the organization, then save again.")
        if not isinstance(enabled, bool):
            raise Refused("invalid", "Say whether workers pull Backlog tickets.")
        manager.call(
            "",
            "user",
            {
                "action": "plan_save",
                "organizationId": identifier,
                "params": {
                    "kind": "operations",
                    "item": {"pullBacklog": enabled},
                    "expectedRevision": revision,
                    "reason": (
                        "Workers pull Backlog tickets when they need work, set from the phone"
                        if enabled
                        else "Workers leave Backlog tickets for you to move, set from the phone"
                    ),
                },
            },
        )
        return self._organization(peer, identifier)

    # Settings --------------------------------------------------------------
    # The window owns its settings. It publishes the allowed values here and
    # applies queued phone changes through its normal settings code.

    def _load(self, key: str) -> Any:
        row = self.db.execute(
            "SELECT value FROM device_desktop_settings WHERE key=?", (key,)
        ).fetchone()
        return self.app._decode("desktop-settings", row[0])["value"] if row else None

    def _store(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO device_desktop_settings VALUES (?,?)",
            (key, self.app._encode("desktop-settings", {"value": value})),
        )

    def _settings_state(self) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        return self._load("snapshot"), self._load("pending") or []

    def _providers(self) -> list[dict[str, Any]]:
        return list(self.app.workspace.providers or [])

    def _settings_view(self) -> dict[str, Any]:
        snapshot, pending = self._settings_state()
        values = dict((snapshot or {}).get("values") or {})
        for change in pending:
            values.update(change["values"])
        providers = self._providers()
        items = []
        for item in SETTINGS:
            entry = {k: v for k, v in item.items()}
            if item["kind"] in {"provider", "model", "effort"}:
                entry["kind"] = "enum"
                provider = next(
                    (p for p in providers if p.get("id") == values.get("assistant.provider")), {}
                )
                if item["kind"] == "provider":
                    choices = [str(p["id"]) for p in providers if p.get("id")]
                elif item["kind"] == "model":
                    choices = [""] + [str(m) for m in provider.get("models") or []]
                else:
                    efforts = (provider.get("efforts") or {}).get(values.get("assistant.model"))
                    choices = [""] + [str(e) for e in efforts or []]
                current = values.get(item["key"])
                if isinstance(current, str) and current not in choices:
                    choices.append(current)
                entry["options"] = [[c, c or "Default"] for c in choices][:100]
            items.append(entry)
        return {
            "available": snapshot is not None,
            "updated_at": (snapshot or {}).get("updated_at"),
            "pending": len(pending),
            "values": {key: values.get(key) for key in SETTING_KEYS},
            "settings": items,
        }

    def _valid_setting(self, key: str, value: Any) -> bool:
        item = SETTING_KEYS.get(key)
        if item is None:
            return False
        kind = item["kind"]
        if kind == "bool":
            return isinstance(value, bool)
        if kind == "enum":
            return value in {option[0] for option in item["options"]}
        if kind == "number":
            return (
                isinstance(value, int | float)
                and not isinstance(value, bool)
                and item["min"] <= value <= item["max"]
            )
        providers = self._providers()
        if kind == "provider":
            return value in {str(p.get("id")) for p in providers}
        if kind == "model":
            return value == "" or value in {
                str(m) for p in providers for m in p.get("models") or []
            }
        return value == "" or value in {
            str(e)
            for p in providers
            for efforts in (p.get("efforts") or {}).values()
            for e in efforts or []
        }

    def _update_settings(self, params: dict[str, Any], request: str) -> dict[str, Any]:
        changes = params.get("changes")
        if not isinstance(changes, dict) or not 1 <= len(changes) <= len(SETTINGS):
            raise Refused("invalid", "Choose at least one setting to change.")
        for key, value in changes.items():
            if not self._valid_setting(key, value):
                raise Refused(
                    "invalid",
                    f"{SETTING_KEYS.get(key, {}).get('label', key)} has an unsupported value.",
                )
        snapshot, pending = self._settings_state()
        if snapshot is None:
            raise Refused(
                "unavailable", "Open Raticode on your computer once to load its settings."
            )
        if len(pending) >= PENDING_SETTINGS:
            raise Refused("conflict", "Earlier changes are still waiting for the desktop window.")
        pending.append({"id": request, "values": changes})
        self._store("pending", pending)
        return self._settings_view()

    def exchange_browser(
        self, tabs: Any, search_url: Any, opened: Any, engine: Any = None
    ) -> dict[str, Any]:
        """Local window only: publish browser tabs, collect pages phones sent."""
        return self.browser.exchange(tabs, search_url, opened, engine)

    def exchange_settings(self, settings: Any, applied: Any) -> dict[str, Any]:
        """Local window only: publish allowed values, receive queued phone changes."""
        with self.app.registry.transaction():
            _, pending = self._settings_state()
            done = {str(item) for item in applied or []} if isinstance(applied, list) else set()
            pending = [change for change in pending if change["id"] not in done]
            if isinstance(settings, dict):
                values = {}
                for key in SETTING_KEYS:
                    value = _path_value(settings, key)
                    if isinstance(value, bool | int | float | str) and len(str(value)) <= 160:
                        values[key] = value
                self._store("snapshot", {"values": values, "updated_at": time.time()})
            self._store("pending", pending)
            return {"changes": pending}
