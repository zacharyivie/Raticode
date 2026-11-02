"""Structured extras for Rem's chat: result cards, web sources and follow-ups.

A message's text stays complete on its own. These fields only add to it, so a
desktop or phone that ignores them still shows the whole answer and its links.
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from gofer.utils.atomic_output import open_binary_input

# The same rules as frontend/src/lib/chatSources.js, so a task's sources match
# the cards its worker thread shows.
SEARCH_LINK = re.compile(r'\{"title":"((?:[^"\\]|\\.)*)","url":"((?:[^"\\]|\\.)*)"')
BARE_URL = re.compile(r"https?://[^\s\"'<>`)\]\\]+")
MARKDOWN_LINK = re.compile(r"\[([^\]]{1,300})\]\((https?://[^\s)]+)\)")
WEB_TOOL = re.compile(r"search|fetch|browser_(?:navigate|open)|web", re.IGNORECASE)
RANK = {"cited": 0, "read": 1, "found": 2}
TITLE = re.compile(r"<title[^>]*>(.*?)</title>|<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
SOURCE_LIMIT = 8
ARTIFACT_LIMIT = 4


def public_url(value: str) -> str:
    """A web address that previews may fetch, or an empty string."""
    url = re.sub(r"[.,;:!?'\"]+$", "", str(value or "").strip())
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
        return ""
    if port not in {None, 80, 443} or host == "localhost" or host.endswith(".local"):
        return ""
    try:
        if not ipaddress.ip_address(host).is_global:
            return ""
    except ValueError:
        pass
    return url if len(url) <= 2048 else ""


def _source_key(url: str) -> str:
    return url.split("#", 1)[0].rstrip("/").replace("http://", "https://", 1)


def _unescape(value: str) -> str:
    try:
        return str(json.loads(f'"{value}"'))
    except ValueError:
        return value


def turn_sources(
    transcript: list[dict[str, Any]], final_body: str = "", limit: int = SOURCE_LIMIT
) -> list[dict[str, str]]:
    """Web pages a worker cited, read or found, cited first. Empty without web use."""
    found: dict[str, dict[str, Any]] = {}

    def add(url: str, title: str, kind: str) -> None:
        clean = public_url(url)
        if not clean:
            return
        key = _source_key(clean)
        current = found.get(key)
        label = re.sub(r"\s+", " ", title or "").strip()[:200]
        if current is None:
            found[key] = {"url": clean, "title": label, "kind": kind, "order": len(found)}
            return
        if not current["title"] and label:
            current["title"] = label
        if RANK[kind] < RANK[current["kind"]]:
            current["kind"] = kind

    used_web = False
    for entry in transcript:
        trace = entry.get("trace") if isinstance(entry, dict) else None
        if not isinstance(trace, dict) or trace.get("kind") != "tool":
            continue
        if entry.get("worker_role") not in {None, "execute"}:
            continue
        if not WEB_TOOL.search(str(trace.get("title") or "")):
            continue
        used_web = True
        try:
            arguments = json.loads(str(trace.get("input") or ""))
        except ValueError:
            arguments = None
        if isinstance(arguments, dict) and isinstance(arguments.get("url"), str):
            add(arguments["url"], "", "read")
        for match in SEARCH_LINK.finditer(str(trace.get("output") or "")):
            add(_unescape(match[2]), _unescape(match[1]), "found")
    if not used_web:
        return []
    for match in MARKDOWN_LINK.finditer(final_body or ""):
        add(match[2], match[1], "cited")
    for match in BARE_URL.finditer(final_body or ""):
        add(match[0], "", "cited")
    ranked = sorted(found.values(), key=lambda s: (RANK[s["kind"]], s["order"]))
    return [{"url": s["url"], "title": s["title"], "kind": s["kind"]} for s in ranked[:limit]]


def report_title(path: Path) -> str:
    """The <title> or first heading of a saved HTML report, without its markup."""
    if path.suffix.lower() not in {".html", ".htm"}:
        return ""
    try:
        with open_binary_input(path) as handle:
            head = handle.read(64 * 1024).decode("utf-8", errors="replace")
    except OSError:
        return ""
    match = TITLE.search(head)
    if not match:
        return ""
    text = re.sub(r"<[^>]+>", " ", match[1] or match[2] or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()[:160]


def artifact_cards(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Saved reports for a result card: title, file name, kind and size."""
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for artifact in run.get("artifacts", []):
        raw = artifact.get("path") if isinstance(artifact, dict) else None
        if not raw or artifact.get("superseded") or raw in seen:
            continue
        seen.add(raw)
        path = Path(raw)
        try:
            info = path.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        cards.append(
            {
                "name": path.name[:160],
                "title": report_title(path) or path.stem[:160],
                "path": str(path),
                "link": str(artifact.get("link") or "")[:4600],
                "kind": path.suffix.lstrip(".").lower()[:12] or "file",
                "size": info.st_size,
            }
        )
    return cards[-ARTIFACT_LIMIT:]


def result_card(
    run: dict[str, Any], state: str, transcript: list[dict[str, Any]]
) -> dict[str, Any]:
    """What a finished run produced: its checks, saved reports and web sources."""
    criteria = [c for c in run.get("criteria") or [] if isinstance(c, dict)]
    return {
        "title": str(run["recipe"].get("title") or "Task")[:160],
        "type": str(run["recipe"].get("type") or "")[:40],
        "state": state,
        "checks": {
            "passed": sum(1 for c in criteria if c.get("result") == "pass"),
            "failed": sum(1 for c in criteria if c.get("result") == "fail"),
            "total": len(criteria),
        },
        "artifacts": artifact_cards(run),
        "sources": turn_sources(transcript, str(run.get("worker_result") or "")),
        "thread_id": str(run.get("thread_id") or ""),
    }


def clean_suggestions(values: list[str]) -> list[str]:
    """At most three short, distinct follow-up prompts, one line each."""
    kept: list[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if text and len(text) <= 120 and text.lower() not in {k.lower() for k in kept}:
            kept.append(text)
    return kept[:3]
