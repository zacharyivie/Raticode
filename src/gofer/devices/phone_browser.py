"""The desktop browser's tabs as a paired phone sees them, and pages a phone sends to it.

The phone has its own browser. Two things connect it to this one:

* ``browser.tabs`` lists the web tabs open in the desktop window, the user's and
  Rem's, so the phone can open one of them itself. Each tab names the browser it
  runs in (``engine``: Raticode (chromium) is ``raticode``, Raticode (gecko) is
  ``gecko``, or ``firefox``, ``chrome``, ``edge``). The view's own ``engine`` is
  the default browser for Rem's tabs and pages a phone sends; phones from before
  per-tab browsers read only that one.
* ``browser.open`` asks the window to open a web address in a new background tab
  of the user's own browser profile. It never reaches Rem's profile, never selects
  the tab and never runs anything else.

The window owns its tabs. It publishes them through the authenticated local API
(``exchange``) on its device-sync loop and collects the pages phones sent, the same
way it exchanges settings. Both stay in this process's memory only: tab addresses
are never written to the registry or logged, and a restart forgets them.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlsplit

MAX_TABS = 40
MAX_URL = 2048
MAX_TITLE = 200
MAX_PENDING = 20
# The window publishes every five seconds while it runs, even hidden in the tray.
STALE_AFTER = 30.0
GONE_AFTER = 120.0
WINDOW_GONE = "Open Raticode on your computer to use its browser from this phone."


class BrowserRefused(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def web_url(value: Any) -> str | None:
    """A plain http or https address with a host and no user name, or None."""
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > MAX_URL or any(ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 - raises on a malformed port
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not host:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    return url


def _title(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text[:MAX_TITLE]


# Browsers that run the window's web tabs without a window of their own.
STREAMED_ENGINES = ("firefox", "chrome", "edge")
# Every browser a desktop web tab can run in.
TAB_ENGINES = ("raticode", "gecko", *STREAMED_ENGINES)


class PhoneBrowser:
    def __init__(self, clock: Any = time.time) -> None:
        self.clock = clock
        self.lock = threading.Lock()
        self.published: float | None = None
        self.tabs: list[dict[str, Any]] = []
        self.search_url = ""
        # The default browser for Rem's tabs and pages phones send: Raticode
        # (chromium), or the windowless Firefox, Chrome or Edge.
        self.engine = "raticode"
        self.pending: list[dict[str, str]] = []

    # Local window ------------------------------------------------------------

    def exchange(
        self, tabs: Any, search_url: Any, opened: Any, engine: Any = None
    ) -> dict[str, Any]:
        """Local window only: publish open tabs, collect pages phones sent.

        ``opened`` names pages the window has opened since the last exchange; they
        leave the queue. A page is offered again until the window says so, and the
        window keeps its own record so a repeated offer never opens a second tab.
        """
        done = {str(item) for item in opened} if isinstance(opened, list) else set()
        listed: list[dict[str, Any]] = []
        for tab in tabs if isinstance(tabs, list) else []:
            if not isinstance(tab, dict):
                continue
            url = web_url(tab.get("url"))
            if url is None:
                # Previews, local files, blank and home tabs stay on the computer.
                continue
            owner = "rem" if tab.get("owner") == "rem" else "you"
            entry = {
                "tab_id": str(tab.get("id") or "")[:120],
                "title": _title(tab.get("title")),
                "url": url,
                "owner": owner,
                "active": tab.get("active") is True,
            }
            if tab.get("engine") in TAB_ENGINES:
                entry["engine"] = tab["engine"]
            if owner == "rem":
                entry["label"] = _title(tab.get("label"))[:60] or "Rem"
            listed.append(entry)
            if len(listed) >= MAX_TABS:
                break
        template = search_url if isinstance(search_url, str) else ""
        with self.lock:
            self.published = self.clock()
            self.tabs = listed
            self.search_url = (
                template
                if "{query}" in template and web_url(template.replace("{query}", "q"))
                else ""
            )
            self.engine = engine if engine in STREAMED_ENGINES else "raticode"
            self.pending = [item for item in self.pending if item["id"] not in done]
            return {"opens": [dict(item) for item in self.pending]}

    # Phone ---------------------------------------------------------------------

    def view(self) -> dict[str, Any]:
        with self.lock:
            published = self.published
            age = None if published is None else max(0.0, self.clock() - published)
            if age is None or age > GONE_AFTER:
                return {
                    "available": False,
                    "detail": WINDOW_GONE,
                    "tabs": [],
                    "search_url": "",
                    "pending": len(self.pending),
                }
            return {
                "available": True,
                # No timestamps, so an unchanged list keeps its digest between polls.
                "stale": age > STALE_AFTER,
                "tabs": [dict(tab) for tab in self.tabs],
                "search_url": self.search_url,
                "engine": self.engine,
                "pending": len(self.pending),
            }

    def open(self, params: dict[str, Any], request: str) -> dict[str, Any]:
        url = web_url(params.get("url"))
        if url is None:
            raise BrowserRefused(
                "invalid", "Only web addresses (http or https) can open on your computer."
            )
        with self.lock:
            published = self.published
            if published is None or self.clock() - published > GONE_AFTER:
                raise BrowserRefused("unavailable", WINDOW_GONE)
            if not any(item["id"] == request for item in self.pending):
                if len(self.pending) >= MAX_PENDING:
                    raise BrowserRefused(
                        "conflict", "Earlier pages are still waiting for the desktop window."
                    )
                self.pending.append({"id": request, "url": url})
        return {
            "queued": True,
            "message": "Opens in a new background tab in the Browser on your computer.",
        }
