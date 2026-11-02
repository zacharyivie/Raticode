"""Use Raticode from a paired phone: Rem, organizations, swarms, workflows, settings and usage.

Nothing here runs unless the user turns on "Use Raticode from this phone" in
Paired devices. It is off by default and requires thread sync. Every operation
calls the same desktop service the window uses, with that service's validation
and approval policies, so the phone gains no authority the desktop UI lacks.

The phone never sends paths. Projects are named by the IDs in the phone's
catalog; Rem tasks, runs, threads, organizations, swarms and workflows by their
desktop IDs. Workflows are viewed and run, never edited (see ``workflows.py``).
Results are projections for the phone screens, without paths, credentials,
grants or provider configuration.

Rem's chat has its own file channel per phone (``files_thread``). The phone
uploads attachments on it, and the desktop offers it files Rem's replies name,
read from an open project or the Second Brain folder. It is not a shared thread.
Tapping a file link in a reply asks for that file directly (``files.link``);
no model is involved, and the link must be in a message Rem or an agent wrote.

Excluded on purpose: browsing files and the code editor, terminals, the browser,
pairing, provider credentials, skill and MCP server definitions, organization
configuration and imports, workflow editing and run inputs, and settings that
widen what agents may do.
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
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

from gofer.devices.files import MAX_FILE
from gofer.devices.registry import PairingError

READ_OPS = frozenset(
    {
        "overview",
        "media.image",
        "media.preview",
        "rem.draft",
        "rem.home",
        "rem.memories",
        "rem.task",
        "rem.thread",
        "organizations.list",
        "organizations.read",
        "settings.get",
        "usage.overview",
        "workflows.list",
        "workflows.read",
    }
)
ACTION_OPS = frozenset(
    {
        "files.link",
        "rem.message",
        "rem.instruct",
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
        "organizations.control",
        "organizations.task",
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


def _draft_delta(conversation: dict[str, Any], since: Any) -> dict[str, Any]:
    """Rem's reply text written since the phone's last read, while Rem is replying.

    `reset` means the draft started over (a new attempt), so `text` is all of it.
    """
    replying = conversation.get("turn_state") == "replying"
    draft = _text(conversation.get("draft_reply"), 4000) if replying else ""
    offset = since if isinstance(since, int) and not isinstance(since, bool) else 0
    reset = not 0 <= offset <= len(draft)
    start = 0 if reset else offset
    return {
        "replying": replying,
        "turn_label": _text(conversation.get("turn_label"), 120) if replying else "",
        "start": start,
        "text": draft[start:],
        "length": len(draft),
        "reset": reset,
    }


def _checks(criteria: Any) -> dict[str, int]:
    items = [c for c in criteria or [] if isinstance(c, dict)]
    return {
        "passed": sum(1 for c in items if c.get("result") == "pass"),
        "failed": sum(1 for c in items if c.get("result") == "fail"),
        "total": len(items),
    }


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
        entries.append(
            {
                "id": _text(message.get("id"), 128),
                "role": _text(message.get("role"), 20),
                "body": _text(message.get("body"), 16000),
                "kind": _text(message.get("kind"), 40),
            }
        )
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


def _authored(messages: list[Any], identity: str, roles: set[str]) -> str | None:
    """The body of a message Rem or an agent wrote; the user's own text doesn't count."""
    for message in messages:
        if isinstance(message, dict) and message.get("id") == identity:
            return str(message.get("body") or "") if message.get("role") in roles else None
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
    if name in {"RemConflict", "OrganizationConflict"}:
        return Refused("conflict", str(exc)[:1000] or "This changed on desktop. Refresh first.")
    if isinstance(exc, KeyError):
        return Refused("not_found", "This item no longer exists on desktop.")
    return Refused("invalid", str(exc)[:1000] or "The desktop could not apply this action.")


class DesktopControl:
    def __init__(self, application: Any) -> None:
        self.app = application
        self.db = application.registry.db
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS device_desktop_control (peer TEXT PRIMARY KEY);"
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
        from gofer.devices.workflows import PhoneWorkflows

        self.workflows = PhoneWorkflows(self)

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

    def gather(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Read the desktop services a phone view needs, without the device lock.

        Rem, organizations and usage keep their own locks and can be slow. Phones
        poll their open screen, so holding the device lock here would stall the
        desktop window's own device calls. The caller checks authorization before
        and again after, under the lock.
        """
        if event["type"] != "desktop.read":
            return None
        payload = event["payload"]
        try:
            return {"value": self._services(payload["op"], payload["params"])}
        except Exception as exc:  # noqa: BLE001
            # Answered by handle() exactly as if the read had failed there.
            return {"error": exc}

    def _services(self, op: str, params: dict[str, Any]) -> dict[str, Any]:
        if op == "media.image":
            return {"image": self.fetch_image(str(params.get("url") or ""))}
        if op == "media.preview":
            return {"preview": self.fetch_preview(str(params.get("url") or ""))}
        if op == "rem.draft":
            store = self._need(self.rem, "Rem").store
            with store.read() as db:
                return {"conversation": store.setting(db, "conversation")}
        if op == "rem.home":
            store = self._need(self.rem, "Rem").store
            with store.read() as db:
                config = store.setting(db, "config")
            # Folders Rem may send files from: its open projects and the Second Brain.
            roots = [p["root"] for p in config.get("projects") or [] if p.get("root")]
            if (config.get("brain") or {}).get("root"):
                roots.append(config["brain"]["root"])
            return {
                "conversation": store.conversation(),
                "tasks": store.tasks(),
                "threads": store.thread_ids(),
                "roots": roots,
            }
        if op == "rem.task":
            return {"tasks": self._need(self.rem, "Rem").store.tasks()}
        if op == "rem.memories":
            return {"memories": self._need(self.rem, "Rem").memories()}
        if op == "rem.thread":
            store = self._need(self.rem, "Rem").store
            return {"thread": store.thread(_text(params.get("thread_id"), 128))}
        if op == "organizations.list":
            manager = self._organizations_manager()
            return {"listed": manager.call("", "user", {"action": "list", "params": {}})}
        if op == "organizations.read":
            identifier = _text(params.get("organization_id"), 100)
            manager = self._organizations_manager()
            return {
                "org": manager.call(
                    "", "user", {"action": "read", "organizationId": identifier, "params": {}}
                )
            }
        if op == "usage.overview":
            return {"overview": self._need(self.usage, "Usage")()}
        if op.startswith("workflows."):
            from gofer.devices.workflows import gather

            return gather(op, params, self.workflows.base)
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
        body = json.dumps(result, separators=(",", ":"), ensure_ascii=True)
        # Phones poll the open screen. An unchanged view is answered with its digest only.
        digest = hashlib.sha256(body.encode()).hexdigest()[:32]
        if kind == "desktop.read" and payload["params"].get("known") == digest:
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

    def _need(self, service: Any, label: str) -> Any:
        if service is None:
            raise Refused("unavailable", f"{label} is not available on this desktop.")
        return service

    # Reads -----------------------------------------------------------------

    def _read(
        self, peer: str, op: str, params: dict[str, Any], services: dict[str, Any] | None = None
    ) -> Any:
        data = self._services(op, params) if services is None else services
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
                "task_retention": True,
                "rich_results": True,
                "draft_deltas": True,
                "review_retry": True,
                "memories": True,
                # Phones send thread.manage read/unread only to desktops that say so here.
                "thread_read": True,
                "workflows": self.workflows.available(),
            }
        if op == "media.image":
            return data["image"]
        if op == "media.preview":
            return data["preview"]
        if op == "rem.draft":
            return _draft_delta(data["conversation"], params.get("since"))
        if op == "rem.home":
            return self._rem_home(peer, data)
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
        if op == "settings.get":
            return self._settings_view()
        if op == "usage.overview":
            return self._usage(data)
        if op in {"workflows.list", "workflows.read"}:
            return self.workflows.read(peer, op, params, data)
        raise Refused("invalid", "Unknown desktop request.")

    def _rem_home(self, peer: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        from gofer.ui.rem_home_store import ran_at, retention

        data = data or self._services("rem.home", {})
        snapshot = data["conversation"]
        conversation = snapshot.get("conversation") or {}
        messages = snapshot.get("messages") or []
        # Decrypt the project catalog once, not once per task.
        catalog = self.projects(peer)
        roots = [p["root"] for p in catalog.values()] + list(data.get("roots") or [])
        # Links in the conversation outlive a finished one-time task; say which still open.
        tasks = {task["id"] for task in data["tasks"]}
        threads = set(data.get("threads") or [])
        items = []
        for message in messages[-80:]:
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
            items.append(item)
        shown = _newest(items)
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
            "tasks": [
                self._task_summary(peer, task, catalog)
                for task in sorted(
                    (task for task in data["tasks"] if retention(task)["listed"]),
                    key=lambda task: (ran_at(task), task.get("created_at") or ""),
                    reverse=True,
                )
            ][:100],
            "files_thread": self.files_thread(peer),
            "media": True,
            "file_links": True,
            "task_retention": True,
            "rich_results": True,
            "draft_deltas": True,
            "review_retry": True,
            "memories": True,
        }

    def _reply_files(
        self, peer: str, message: dict[str, Any], roots: list[str]
    ) -> list[dict[str, Any]]:
        """Offer the files on Rem's reply to the phone that asked for them, once each."""
        files = message.get("files")
        request = message.get("reply_to")
        if not isinstance(files, list) or not files or not isinstance(request, str):
            return []
        if (
            self.db.execute(
                "SELECT 1 FROM device_rem_requests WHERE peer=? AND request=?", (peer, request)
            ).fetchone()
            is None
        ):
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

        ``own_folder`` is for files already on one of Rem's replies, which Rem checked
        when it wrote the reply: one outside the open folders is read from its own
        folder. Tapped links never use it, so a link can't reach outside those folders.
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
        if href not in body:
            raise Refused("not_found", "That link isn't in this message on the desktop.")
        path = _linked_file(link_paths(href), base)
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
            offer = self._offer_path(peer, path, self._rem_roots(peer))
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
        if isinstance(message.get("state"), str):
            item["state"] = _text(message["state"], 40)
        if message.get("kind") == "task_update":
            item["kind"] = "task_update"
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
        return {
            "run_id": _text(run.get("id"), 128),
            "state": _text(run.get("state"), 40),
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
        }

    def _rem_task(
        self, peer: str, identifier: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = data or self._services("rem.task", {})
        task = next((t for t in data["tasks"] if t.get("id") == identifier), None)
        if task is None:
            raise Refused("not_found", "This Rem task no longer exists.")
        return {
            **self._task_summary(peer, task),
            "prompt": _text(task.get("prompt"), 8000),
            "provider": _text(task.get("provider"), 80),
            "model": _text(task.get("model"), 160),
            "effort": _text(task.get("effort"), 80),
            "criteria": [
                _text(c.get("description"), 600) for c in task.get("acceptance_criteria") or []
            ][:40],
            "runs": [self._run(run) for run in (task.get("recent_runs") or [])[:20]],
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
        employees = {e.get("id"): e for e in config.get("employees") or []}
        attention = set((org.get("diagnostics") or {}).get("attention") or [])
        tasks = []
        for task in (runtime.get("tasks") or [])[-200:]:
            tasks.append(
                {
                    "task_id": _text(task.get("id"), 100),
                    "title": _text(task.get("title"), 500),
                    "description": _text(task.get("description"), 1500),
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
                    "comments": [
                        {
                            "actor": _text(_comment_author(c.get("actor"), employees), 150),
                            "at": _text(c.get("at"), 80),
                            "body": _text(c.get("body"), 1500),
                            "decision": _text(c.get("decision"), 20),
                        }
                        for c in (task.get("comments") or [])[-5:]
                    ],
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
                r.get("status") == "pending"
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
        if op == "files.link":
            return self._file_link(peer, params)
        if op.startswith("rem."):
            return self._rem_action(peer, op, params, request)
        if op.startswith("workflows."):
            return self.workflows.act(peer, op, params, request)
        if op == "organizations.control":
            manager = self._organizations_manager()
            if params.get("state") not in {"running", "paused"}:
                raise Refused("invalid", "Choose running or paused.")
            identifier = _text(params.get("organization_id"), 100)
            if params["state"] == "running":
                from gofer.ui.organization_store import DANGEROUS_PERMISSION_MODES

                config = manager.store.get("", identifier)["config"]
                if any(
                    not e.get("paused") and e.get("permissionMode") in DANGEROUS_PERMISSION_MODES
                    for e in config.get("employees", [])
                ):
                    # The phone can't show the roster for review, so it can't confirm this.
                    raise Refused(
                        "unauthorized",
                        "This organization has employees that run without a sandbox. Start it "
                        "from the desktop, where you can review and confirm them.",
                    )
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
        if op == "organizations.task":
            return self._organization_task(peer, params, request)
        if op == "swarms.start":
            remote = self.app.remote
            self._need(remote.swarms, "Swarms")
            task = _text(params.get("task"), 4000).strip()
            if not task:
                raise Refused("invalid", "Describe the task for the swarm.")
            root, identifier = remote._swarm(peer, _text(params.get("swarm_id"), 36))
            from gofer.ui.swarms import full_access_agents

            team = remote.swarms.get(root, identifier, include_history=False)
            if full_access_agents(team.get("agents", [])):
                # The phone can't show the team for review, so it can't confirm this.
                raise Refused(
                    "unauthorized",
                    "This swarm has agents that run without a sandbox. Start it from the "
                    "desktop, where you can review and confirm them.",
                )
            _, grants = remote.projects(peer)
            remote.swarms.start(root, identifier, task, workspace_grants=grants)
            return {"started": True}
        if op == "settings.update":
            return self._update_settings(params, request)
        raise Refused("invalid", "Unknown desktop action.")

    def _rem_action(self, peer: str, op: str, params: dict[str, Any], request: str) -> Any:
        rem = self._need(self.rem, "Rem")
        if op == "rem.message":
            self.db.execute(
                "INSERT OR IGNORE INTO device_rem_requests VALUES (?,?)", (peer, request)
            )
            message: dict[str, Any] = {
                "request_id": request,
                "body": _text(params.get("body"), 20000),
            }
            identifiers = params.get("attachment_ids") or []
            if (
                not isinstance(identifiers, list)
                or len(identifiers) > REM_FILES
                or len(set(map(str, identifiers))) != len(identifiers)
            ):
                raise Refused("invalid", f"Attach at most {REM_FILES} different files.")
            sent = any(m.get("id") == request for m in rem.store.conversation()["messages"])
            if identifiers and not sent:
                message["attachments"] = self._rem_attachments(peer, rem, identifiers)
            try:
                if not sent:
                    rem.submit_message(message)
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
        if op == "rem.run.retry_review":
            # The same desktop control as the chat's "retry the review"; it refuses runs
            # without an interrupted review or whose task changed since.
            rem.retry_review(_text(params.get("run_id"), 128), {"request_id": request})
            return self._rem_home(peer)
        raise Refused("invalid", "Unknown Rem action.")

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

    def _organization_task(self, peer: str, params: dict[str, Any], request: str) -> Any:
        manager = self._organizations_manager()
        identifier = _text(params.get("organization_id"), 100)
        action = params.get("action")
        task = _text(params.get("task_id"), 100)
        revision = params.get("expected_revision")
        if action == "create":
            arguments: dict[str, Any] = {
                "action": "task_create",
                "params": {
                    "title": _text(params.get("title"), 500),
                    "description": _text(params.get("description"), 100000),
                    "priority": params.get("priority", "medium"),
                    "idempotencyKey": request,
                    **({"assignee": params["assignee"]} if params.get("assignee") else {}),
                },
            }
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
