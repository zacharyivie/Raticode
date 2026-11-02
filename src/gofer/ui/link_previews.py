"""Previews for web pages Rem found: title, summary and a small thumbnail.

The backend fetches pages so the renderer never loads arbitrary sites. Each
request pins one validated public address, so a page can't redirect the fetch
to this computer or the local network. No cookies are sent.
"""

from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from collections import OrderedDict
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote, urljoin, urlparse

PAGE_LIMIT = 512 * 1024
IMAGE_LIMIT = 400 * 1024
TIMEOUT_SECONDS = 6
CACHE_SECONDS = 6 * 60 * 60
CACHE_SIZE = 300
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/avif"}
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

Resolver = Any


class PreviewError(ValueError):
    pass


def youtube_id(url: str) -> str | None:
    """The video ID of a YouTube watch, short or embed link."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    candidate = ""
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "music.youtube.com", "youtube-nocookie.com"}:
        if parsed.path == "/watch":
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        else:
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live", "v"}:
                candidate = parts[1]
    return candidate if YOUTUBE_ID.fullmatch(candidate) else None


def _public_address(host: str, port: int, resolver: Resolver) -> str:
    try:
        records = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise PreviewError("The site could not be found") from exc
    addresses = [record[4][0] for record in records if record[4]]
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise PreviewError("Previews only reach public web addresses")
    return str(addresses[0])


def fetch(
    url: str,
    accept: str,
    limit: int,
    *,
    resolver: Resolver = socket.getaddrinfo,
    redirects: int = 0,
) -> tuple[str, str, bytes, bool]:
    """GET a public URL. Returns final URL, MIME type, body and whether it was cut short."""
    if redirects > 4:
        raise PreviewError("Too many redirects")
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise PreviewError("Previews need a public http or https link")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in {80, 443}:
        raise PreviewError("Previews only use standard web ports")
    address = _public_address(parsed.hostname, port, resolver)
    connection = http.client.HTTPConnection(parsed.hostname, port=port, timeout=TIMEOUT_SECONDS)
    sock = socket.create_connection((address, port), timeout=TIMEOUT_SECONDS)
    if parsed.scheme == "https":
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
    connection.sock = sock
    try:
        path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        connection.request(
            "GET",
            path,
            headers={
                # Many sites only serve Open Graph tags to link-preview crawlers.
                "User-Agent": "Mozilla/5.0 (compatible; RaticodeLinkPreview/1.0)",
                "Accept": accept,
                "Accept-Encoding": "identity",
                "Accept-Language": "en;q=0.9, *;q=0.5",
            },
        )
        response = connection.getresponse()
        if response.status in {301, 302, 303, 307, 308}:
            location = response.getheader("Location") or ""
            if not location:
                raise PreviewError("The site redirected without a destination")
            return fetch(
                urljoin(url, location), accept, limit, resolver=resolver, redirects=redirects + 1
            )
        if response.status != 200:
            raise PreviewError(f"The site returned HTTP {response.status}")
        content_type = response.getheader("Content-Type", "")
        body = response.read(limit + 1)
        return url, content_type.split(";")[0].strip().lower(), body[:limit], len(body) > limit
    except (OSError, http.client.HTTPException) as exc:
        raise PreviewError("The site did not answer") from exc
    finally:
        connection.close()


class _MetaParser(HTMLParser):
    """Collects <title>, <meta> and icon links from the head of a page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag == "meta":
            key = (values.get("property") or values.get("name") or "").strip().lower()
            if key and values.get("content") and key not in self.meta:
                self.meta[key] = values["content"].strip()
        elif tag == "title" and not self.title:
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def page_metadata(html: str, base_url: str) -> dict[str, str]:
    parser = _MetaParser()
    # Preview tags live in the head; the body can be most of the download.
    head = re.split(r"</head\s*>", html, maxsplit=1, flags=re.IGNORECASE)[0]
    try:
        parser.feed(head)
    except AssertionError:
        pass
    meta = parser.meta

    def first(*keys: str) -> str:
        return next((meta[key] for key in keys if meta.get(key)), "")

    image = first("og:image:secure_url", "og:image", "og:image:url", "twitter:image")
    return {
        "title": _clean(first("og:title", "twitter:title") or parser.title, 200),
        "description": _clean(first("og:description", "twitter:description", "description"), 300),
        "siteName": _clean(first("og:site_name", "application-name"), 80),
        "image": urljoin(base_url, image) if image else "",
        "type": first("og:type").lower(),
    }


def _clean(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _decode(body: bytes) -> str:
    charset = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.IGNORECASE)
    encoding = charset.group(1).decode("ascii", "ignore") if charset else "utf-8"
    try:
        return body.decode(encoding, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def thumbnail(url: str, *, resolver: Resolver = socket.getaddrinfo) -> str:
    """A small image as a data URL, or an empty string when it can't be shown."""
    try:
        _final, mime, body, truncated = fetch(
            url,
            "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
            IMAGE_LIMIT,
            resolver=resolver,
        )
    except PreviewError:
        return ""
    if truncated or mime not in IMAGE_TYPES or not body:
        return ""
    return f"data:{mime};base64,{base64.b64encode(body).decode('ascii')}"


def build_preview(url: str, *, resolver: Resolver = socket.getaddrinfo) -> dict[str, Any]:
    video = youtube_id(url)
    if video:
        canonical = f"https://www.youtube.com/watch?v={video}"
        title = ""
        author = ""
        try:
            _final, _mime, body, _cut = fetch(
                "https://www.youtube.com/oembed?format=json&url=" + quote(canonical, safe=""),
                "application/json",
                64 * 1024,
                resolver=resolver,
            )
            data = json.loads(body.decode("utf-8", errors="replace"))
            title = _clean(str(data.get("title") or ""), 200)
            author = _clean(str(data.get("author_name") or ""), 80)
        except (PreviewError, ValueError):
            pass
        return {
            "url": canonical,
            "kind": "video",
            "title": title,
            "description": author,
            "siteName": "YouTube",
            "image": thumbnail(f"https://i.ytimg.com/vi/{video}/hqdefault.jpg", resolver=resolver),
        }
    final, mime, body, _truncated = fetch(
        url, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5", PAGE_LIMIT, resolver=resolver
    )
    if mime in IMAGE_TYPES:
        return {
            "url": final,
            "kind": "image",
            "title": "",
            "description": "",
            "siteName": "",
            "image": thumbnail(final, resolver=resolver),
        }
    if mime not in {"text/html", "application/xhtml+xml"}:
        raise PreviewError("Only web pages have previews")
    meta = page_metadata(_decode(body), final)
    return {
        "url": final,
        "kind": "video" if meta["type"].startswith("video") else "page",
        "title": meta["title"],
        "description": meta["description"],
        "siteName": meta["siteName"],
        "image": thumbnail(meta["image"], resolver=resolver) if meta["image"] else "",
    }


# Inline chat images, for the phone and for the desktop renderer alike.
CHAT_IMAGE_LIMIT = 768 * 1024
IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def image_mime(data: bytes) -> str | None:
    """The image type named by the file's own bytes, never by the server."""
    for signature, mime in IMAGE_SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def chat_image(url: str, *, resolver: Resolver = socket.getaddrinfo) -> dict[str, Any]:
    """A public web image from a chat message, as base64 with its sniffed type.

    The fetcher pins one validated public address and sends no cookies or
    referrer, so a reply can't point it at this computer or the local network,
    and the site sees the desktop's request instead of the renderer's.
    """
    if not isinstance(url, str) or not 0 < len(url) <= 2048:
        raise PreviewError("Images need a web address of at most 2,048 characters.")
    _, _, data, truncated = fetch(
        url,
        "image/webp,image/png,image/jpeg,image/gif;q=0.9,*/*;q=0.1",
        CHAT_IMAGE_LIMIT,
        resolver=resolver,
    )
    if truncated:
        raise PreviewError("This image is too large to show here.")
    mime = image_mime(data)
    if mime is None:
        raise PreviewError("This address isn't a JPEG, PNG, GIF or WebP image.")
    return {"url": url, "mime": mime, "data": base64.b64encode(data).decode("ascii")}


class LinkPreviews:
    """Recent previews, kept in memory so a reopened thread shows them at once."""

    def __init__(self, resolver: Resolver = socket.getaddrinfo) -> None:
        self.resolver = resolver
        self._cache: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, url: str) -> dict[str, Any]:
        if not isinstance(url, str) or len(url) > 2048:
            raise PreviewError("Previews need a public http or https link")
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(url)
            if cached and now - cached[0] < CACHE_SECONDS:
                self._cache.move_to_end(url)
                return cached[1]
        preview = build_preview(url, resolver=self.resolver)
        with self._lock:
            self._cache[url] = (now, preview)
            self._cache.move_to_end(url)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return preview
