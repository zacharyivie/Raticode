"""Fetch one web page and turn it into readable Markdown or plain text for read-web-page.

The fetch pins each hop to an address checked by ``link_previews.public_address``, so a
page or a redirect can't reach this computer or the local network unless the node's
network-allowlist names the host or address. No cookies or credentials are sent. No
JavaScript runs unless ``render`` asks for a headless browser (``web_render``), whose
requests pass the same check. Extraction builds on the Second Brain's stdlib
``ReadableHTML`` parser.
"""

from __future__ import annotations

import http.client
import re
import socket
import ssl
import tempfile
import threading
import time
import zlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from gofer.ui.brain_documents import ReadableHTML
from gofer.ui.link_previews import PreviewError, PrivateAddressError, public_address
from gofer.utils.http_read import read_before

MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_LINKS = 200
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
HTML_TYPES = {"text/html", "application/xhtml+xml"}
TEXT_TYPES = {"text/plain"}
USER_AGENT = "Mozilla/5.0 (compatible; RaticodeReadWebPage/1.0)"
# Below this many readable characters, a page with an app root or a "turn on JavaScript"
# notice probably builds its content in the browser. Below the smaller one, any script does.
JAVASCRIPT_TEXT_THRESHOLD = 200
SCRIPT_ONLY_TEXT_THRESHOLD = 50
APP_ROOT_IDS = {"root", "app", "__next", "__nuxt", "___gatsby", "svelte", "q-app"}

Resolver = Callable[..., Any]


class WebPageError(Exception):
    """A failure with a Rattish error kind and code, and a message for the run log."""

    def __init__(
        self, kind: str, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class FetchedPage:
    url: str
    mime: str
    charset: str | None
    body: bytes
    truncated: bool


@dataclass
class PageText:
    title: str = ""
    description: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    likely_needs_javascript: bool = False


def _safe_origin(url: str) -> str:
    """Scheme, host and port only: paths and queries can carry tokens."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return "<invalid-url>"
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme, netloc, "/", "", ""))


def _parse_target(url: str) -> tuple[str, str, int, str]:
    try:
        parsed = urlsplit(url.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise WebPageError(
            "configuration", "RATTISH_WEB_PAGE_INVALID_URL", "The URL is not valid."
        ) from None
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not host:
        raise WebPageError(
            "configuration",
            "RATTISH_WEB_PAGE_INVALID_URL",
            "read-web-page needs an http or https URL with a host.",
            {"url": _safe_origin(url)},
        )
    if parsed.username or parsed.password:
        raise WebPageError(
            "configuration",
            "RATTISH_WEB_PAGE_INVALID_URL",
            "The URL contains a user name or password. read-web-page doesn't send credentials.",
            {"url": _safe_origin(url)},
        )
    path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    return scheme, host, port or (443 if scheme == "https" else 80), path


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise WebPageError(
            "timeout", "RATTISH_WEB_PAGE_TIMEOUT", "The site took too long to send the page."
        )
    return left


def _content_type(value: str) -> tuple[str, str | None]:
    parts = [part.strip() for part in value.split(";")]
    mime = parts[0].lower()
    charset = None
    for part in parts[1:]:
        key, _, raw = part.partition("=")
        if key.strip().lower() == "charset" and raw.strip():
            charset = raw.strip().strip("\"'")
    return mime, charset


def _sniff_mime(body: bytes) -> str:
    start = body[:1024].lstrip().lower()
    if start.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return "text/html"
    if b"\x00" not in body[:4096]:
        return "text/plain"
    return "application/octet-stream"


def _decompress(body: bytes, encoding: str, limit: int) -> tuple[bytes, bool]:
    """Undo gzip or deflate, stopping at ``limit`` bytes."""
    if encoding in {"", "identity"}:
        return body, False
    if encoding not in {"gzip", "x-gzip", "deflate"}:
        raise WebPageError(
            "network",
            "RATTISH_WEB_PAGE_UNSUPPORTED_ENCODING",
            f"The site sent the page with {encoding} compression, which read-web-page can't "
            "unpack.",
        )
    wbits = 16 + zlib.MAX_WBITS if encoding != "deflate" else zlib.MAX_WBITS
    try:
        decoder = zlib.decompressobj(wbits)
        data = decoder.decompress(body, limit + 1)
    except zlib.error:
        raise WebPageError(
            "network", "RATTISH_WEB_PAGE_BAD_ENCODING", "The compressed page is damaged."
        ) from None
    return data[:limit], len(data) > limit


def fetch_page(
    url: str,
    *,
    allowlist: Iterable[str] = (),
    timeout_seconds: float = 30.0,
    resolver: Resolver = socket.getaddrinfo,
    max_bytes: int = MAX_BODY_BYTES,
) -> FetchedPage:
    """GET ``url``, following up to MAX_REDIRECTS redirects within one overall deadline."""
    entries = tuple(str(entry) for entry in allowlist)
    deadline = time.monotonic() + timeout_seconds
    current = url.strip()
    for hop in range(MAX_REDIRECTS + 1):
        scheme, host, port, path = _parse_target(current)
        try:
            address = public_address(host, port, resolver, allowlist=entries)
        except PrivateAddressError as exc:
            raise WebPageError(
                "network",
                "RATTISH_WEB_PAGE_ADDRESS_REFUSED",
                f"Refused {host}: it resolves to the private or loopback address "
                f"{exc.address}. Add the host or address to network-allowlist to read it.",
                {"url": _safe_origin(current), "address": exc.address},
            ) from None
        except PreviewError:
            raise WebPageError(
                "network",
                "RATTISH_WEB_PAGE_HOST_NOT_FOUND",
                f"The host {host} could not be found.",
                {"url": _safe_origin(current)},
            ) from None
        # The connection gets a socket pinned to the checked address; it never resolves the
        # host itself. The class only sets the Host header's default port.
        connection_class = (
            http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        )
        connection = connection_class(host, port=port, timeout=_remaining(deadline))
        try:
            try:
                sock = socket.create_connection((address, port), timeout=_remaining(deadline))
                if scheme == "https":
                    sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
                connection.sock = sock
                connection.request(
                    "GET",
                    path,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
                        "Accept-Encoding": "gzip, deflate",
                        "Accept-Language": "en;q=0.9, *;q=0.5",
                    },
                )
                sock.settimeout(_remaining(deadline))
                response = connection.getresponse()
                if response.status in REDIRECT_STATUSES:
                    location = (response.getheader("Location") or "").strip()
                    if not location:
                        raise WebPageError(
                            "network",
                            "RATTISH_WEB_PAGE_HTTP_STATUS",
                            f"The site answered HTTP {response.status} without a Location.",
                            {"status": response.status},
                        )
                    if hop == MAX_REDIRECTS:
                        raise WebPageError(
                            "network",
                            "RATTISH_WEB_PAGE_TOO_MANY_REDIRECTS",
                            f"The page redirected more than {MAX_REDIRECTS} times.",
                            {"url": _safe_origin(url)},
                        )
                    current = urljoin(current, location)
                    continue
                if not 200 <= response.status < 300:
                    raise WebPageError(
                        "network",
                        "RATTISH_WEB_PAGE_HTTP_STATUS",
                        f"The site answered HTTP {response.status}.",
                        {"status": response.status, "url": _safe_origin(current)},
                    )
                declared = response.getheader("Content-Type") or ""
                mime, charset = _content_type(declared)
                if mime and mime not in HTML_TYPES | TEXT_TYPES:
                    raise WebPageError(
                        "network",
                        "RATTISH_WEB_PAGE_UNSUPPORTED_TYPE",
                        f"The address returned {mime}, not an HTML page or plain text. "
                        "read-web-page reads only HTML and plain text.",
                        {"content_type": mime},
                    )
                encoding = (response.getheader("Content-Encoding") or "").strip().lower()
                # http.client closes its socket object after a Connection: close response,
                # and the response keeps reading through its own file under the timeout set
                # above. The deadline is still checked between chunks.
                live_sock = None if response.will_close else sock
                raw = read_before(response, max_bytes + 1, deadline, live_sock)
            except TimeoutError:
                raise WebPageError(
                    "timeout",
                    "RATTISH_WEB_PAGE_TIMEOUT",
                    "The site took too long to send the page.",
                ) from None
            except ssl.SSLError as exc:
                raise WebPageError(
                    "network",
                    "RATTISH_WEB_PAGE_TLS",
                    f"The secure connection to {host} failed: {exc.reason or 'TLS error'}.",
                ) from None
            except (OSError, http.client.HTTPException) as exc:
                raise WebPageError(
                    "network",
                    "RATTISH_WEB_PAGE_UNREACHABLE",
                    f"{host} did not answer: {type(exc).__name__}.",
                ) from None
        finally:
            connection.close()
        truncated = len(raw) > max_bytes
        body, cut = _decompress(raw[:max_bytes], encoding, max_bytes)
        truncated = truncated or cut
        if not mime:
            mime = _sniff_mime(body)
            if mime not in HTML_TYPES | TEXT_TYPES:
                raise WebPageError(
                    "network",
                    "RATTISH_WEB_PAGE_UNSUPPORTED_TYPE",
                    "The address returned something that isn't an HTML page or plain text.",
                    {"content_type": ""},
                )
        return FetchedPage(current, mime, charset, body, truncated)
    raise AssertionError("unreachable")  # pragma: no cover


def decode_body(body: bytes, charset: str | None) -> str:
    candidates: list[str] = []
    if charset:
        candidates.append(charset)
    if body.startswith(b"\xef\xbb\xbf"):
        candidates.append("utf-8-sig")
    meta = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.IGNORECASE)
    if meta:
        candidates.append(meta.group(1).decode("ascii", "ignore"))
    candidates.append("utf-8")
    for candidate in candidates:
        try:
            return body.decode(candidate, errors="replace")
        except LookupError:
            continue
    return body.decode("utf-8", errors="replace")  # pragma: no cover


_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr",
}  # fmt: skip
_BLOCK = {
    "address", "article", "details", "dd", "div", "dl", "dt", "fieldset", "figcaption",
    "figure", "form", "main", "p", "section", "summary", "table",
}  # fmt: skip
# ReadableHTML already hides script, style, noscript, template and svg.
_ALWAYS_SKIPPED = {"nav", "aside", "button", "select", "dialog", "iframe", "object", "canvas"}
_SKIPPED_ROLES = {"navigation", "banner", "contentinfo", "complementary", "search"}
_HEADINGS = {f"h{level}": level for level in range(1, 7)}


@dataclass
class _Open:
    tag: str
    effects: tuple[str, ...]
    start: int = 0
    href: str = ""


class _PageReader(ReadableHTML):
    """ReadableHTML that also writes Markdown and remembers article and main regions.

    Each emitted piece of text carries whether it sat in an <article> and in <main>, so the
    reader can keep only the article (or main) once parsing ends. nav and aside are always
    left out, as are footers and the page's header; an article's own header stays so its
    title isn't lost.
    """

    def __init__(self, markdown: bool) -> None:
        super().__init__()
        self.markdown = markdown
        self.pieces: list[tuple[str, bool, bool]] = []
        self.pre_blocks: list[str] = []
        self.stack: list[_Open] = []
        self.skip = 0
        self.article = 0
        self.main = 0
        self.pre = 0
        self.lists: list[list[int]] = []
        self.quote_starts: list[int] = []
        self.script_count = 0
        self.noscript_text: list[str] = []
        self.base_href = ""
        self.title_done = False
        self.app_root = False
        self.base_url = ""
        self.li_depth = 0
        self._noscript = 0

    # Output helpers -------------------------------------------------------------------

    def _last_char(self) -> str:
        for text, _a, _m in reversed(self.pieces):
            if text:
                return text[-1]
        return "\n"

    def _emit(self, text: str) -> None:
        if text and not self.skip and not self.hidden:
            self.pieces.append((text, self.article > 0, self.main > 0))

    def _break(self, count: int = 2) -> None:
        # Paragraphs inside a list item stay on the item's line.
        if self.li_depth and count == 2:
            if self._last_char() not in {" ", "\n"}:
                self._emit(" ")
            return
        self._emit("\n" * count)

    # Parser callbacks -------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        hidden_before = self.hidden
        super().handle_starttag(tag, attrs)
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag == "title" and (hidden_before or self.title_done):
            self.in_title = False
        if tag == "base" and values.get("href") and not self.base_href:
            self.base_href = values["href"]
        if tag == "script":
            self.script_count += 1
        if tag == "noscript":
            self._noscript += 1
        if values.get("id", "").strip().lower() in APP_ROOT_IDS or tag == "app-root":
            self.app_root = True
        if tag in _VOID:
            self._void(tag, values)
            return
        effects: list[str] = []
        role = values.get("role", "").strip().lower()
        if (
            tag in _ALWAYS_SKIPPED
            or tag == "footer"
            or (tag == "header" and not self.article)
            or role in _SKIPPED_ROLES
            or "hidden" in values
            or values.get("aria-hidden", "").strip().lower() == "true"
        ):
            effects.append("skip")
            self.skip += 1
        if tag == "article":
            effects.append("article")
            self.article += 1
        if tag == "main" or role == "main":
            effects.append("main")
            self.main += 1
        entry = _Open(tag, ())
        if tag in _HEADINGS:
            self._break()
            if self.markdown:
                self._emit("#" * _HEADINGS[tag] + " ")
        elif tag in {"ul", "ol"}:
            self.lists.append([1 if tag == "ol" else 0])
            effects.append("list")
            if len(self.lists) == 1:
                self._break()
        elif tag == "li":
            depth = max(len(self.lists) - 1, 0)
            counter = self.lists[-1] if self.lists else [0]
            marker = "- "
            if counter[0]:
                marker = f"{counter[0]}. "
                counter[0] += 1
            self._emit("\n" + "  " * depth + marker)
            effects.append("li")
            self.li_depth += 1
        elif tag == "pre":
            self.pre += 1
            effects.append("pre")
            entry.start = len(self.pieces)
            self._break()
        elif tag == "blockquote":
            self._break()
            effects.append("quote")
            entry.start = len(self.pieces)
        elif tag == "tr":
            self._emit("\n|" if self.markdown else "\n")
        elif tag in {"td", "th"} and self.markdown:
            self._emit(" ")
        elif tag == "a":
            effects.append("link")
            entry.start = len(self.pieces)
            entry.href = values.get("href", "").strip()
        elif self.markdown and tag in {"strong", "b"}:
            self._emit("**")
        elif self.markdown and tag in {"em", "i"}:
            self._emit("*")
        elif self.markdown and tag == "code" and not self.pre:
            self._emit("`")
        elif tag in _BLOCK or tag in {"header", "footer"}:
            self._break()
        entry.effects = tuple(effects)
        self.stack.append(entry)

    def _void(self, tag: str, values: dict[str, str]) -> None:
        if tag == "br":
            self._emit("\n")
        elif tag == "hr":
            self._emit("\n\n---\n\n" if self.markdown else "\n\n")

    def handle_endtag(self, tag: str) -> None:
        super().handle_endtag(tag)
        if tag == "title" and self.title_parts:
            self.title_done = True
        if tag == "noscript":
            self._noscript = max(0, self._noscript - 1)
        if tag in _VOID or not any(entry.tag == tag for entry in self.stack):
            return
        while self.stack:
            entry = self.stack.pop()
            self._close(entry)
            if entry.tag == tag:
                break

    def _close(self, entry: _Open) -> None:
        tag = entry.tag
        if tag in _HEADINGS or tag in _BLOCK or tag in {"header", "footer"}:
            self._break()
        elif tag in {"td", "th"}:
            self._emit(" |" if self.markdown else "\t")
        elif tag == "a":
            self._close_link(entry)
        elif self.markdown and tag in {"strong", "b"}:
            self._emit("**")
        elif self.markdown and tag in {"em", "i"}:
            self._emit("*")
        elif self.markdown and tag == "code" and not self.pre:
            self._emit("`")
        for effect in reversed(entry.effects):
            if effect == "skip":
                self.skip = max(0, self.skip - 1)
            elif effect == "article":
                self.article = max(0, self.article - 1)
            elif effect == "main":
                self.main = max(0, self.main - 1)
            elif effect == "li":
                self.li_depth = max(0, self.li_depth - 1)
            elif effect == "list":
                if self.lists:
                    self.lists.pop()
                if not self.lists:
                    self._break()
            elif effect == "pre":
                self.pre = max(0, self.pre - 1)
                self._close_pre(entry.start)
            elif effect == "quote":
                self._close_quote(entry.start)

    def _close_link(self, entry: _Open) -> None:
        if not self.markdown or not entry.href:
            return
        target = urljoin(self.base_url, entry.href)
        if urlsplit(target).scheme.lower() not in {"http", "https"}:
            return
        inner = self.pieces[entry.start :]
        label = "".join(text for text, _a, _m in inner).strip()
        if not label or "\n" in label:
            return
        _text, in_article, in_main = inner[0]
        label = label.replace("[", "\\[").replace("]", "\\]")
        target = target.replace(" ", "%20").replace(")", "%29")
        self.pieces[entry.start :] = [(f"[{label}]({target})", in_article, in_main)]

    def _close_pre(self, start: int) -> None:
        inner = self.pieces[start:]
        if not inner:
            return
        code = "".join(text for text, _a, _m in inner).strip("\n")
        _text, in_article, in_main = inner[0]
        block = f"```\n{code}\n```" if self.markdown else code
        self.pre_blocks.append(block)
        token = f"\x00{len(self.pre_blocks) - 1}\x00"
        self.pieces[start:] = [(f"\n\n{token}\n\n", in_article, in_main)]

    def _close_quote(self, start: int) -> None:
        inner = self.pieces[start:]
        if not self.markdown or not inner:
            self._break()
            return
        text = _normalize("".join(piece for piece, _a, _m in inner))
        if not text:
            return
        quoted = "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))
        _text, in_article, in_main = inner[0]
        self.pieces[start:] = [(f"\n\n{quoted}\n\n", in_article, in_main)]

    def handle_data(self, data: str) -> None:
        was_title = self.in_title
        super().handle_data(data)
        if self._noscript:
            self.noscript_text.append(data)
        if was_title or self.hidden or self.skip:
            return
        if self.pre:
            self._emit(data)
            return
        text = re.sub(r"\s+", " ", data)
        if self._last_char() in {"\n", " "}:
            text = text.lstrip(" ")
        self._emit(text)

    # Results ----------------------------------------------------------------------------

    def body_text(self) -> str:
        if any(text.strip() for text, in_article, _m in self.pieces if in_article):
            chosen = [text for text, in_article, _m in self.pieces if in_article]
        elif any(text.strip() for text, _a, in_main in self.pieces if in_main):
            chosen = [text for text, _a, in_main in self.pieces if in_main]
        else:
            chosen = [text for text, _a, _m in self.pieces]
        text = _normalize("".join(chosen))
        return re.sub(r"\x00(\d+)\x00", lambda match: self.pre_blocks[int(match[1])], text)


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n").strip()


def _absolute_links(hrefs: Iterable[str], base: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for href in hrefs:
        try:
            absolute = urljoin(base, href.strip())
            parsed = urlsplit(absolute)
        except ValueError:
            continue
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            continue
        absolute = urlunsplit(parsed._replace(fragment=""))
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(absolute)
        if len(links) == MAX_LINKS:
            break
    return links


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def read_html(html: str, url: str, *, markdown: bool = True) -> PageText:
    reader = _PageReader(markdown)
    # A first pass finds <base href> so links resolve against it.
    base = re.search(r"<base\s[^>]*href=[\"']?([^\"'\s>]+)", html[:65536], re.IGNORECASE)
    reader.base_url = urljoin(url, base.group(1)) if base else url
    try:
        reader.feed(html)
        reader.close()
    except AssertionError:
        pass
    text = reader.body_text()
    meta = reader.meta
    title = _clean("".join(reader.title_parts)) or _clean(meta.get("og:title", ""))
    description = next(
        (
            _clean(meta[key])
            for key in ("description", "og:description", "twitter:description")
            if meta.get(key, "").strip()
        ),
        "",
    )
    noscript = " ".join(reader.noscript_text).lower()
    readable = len(re.sub(r"\s+", " ", text).strip())
    needs_js = (
        readable < JAVASCRIPT_TEXT_THRESHOLD and ("javascript" in noscript or reader.app_root)
    ) or (readable < SCRIPT_ONLY_TEXT_THRESHOLD and reader.script_count > 0)
    return PageText(
        title=title,
        description=description,
        text=text,
        links=_absolute_links(reader.links, reader.base_url),
        likely_needs_javascript=needs_js,
    )


def read_plain_text(text: str) -> PageText:
    links = re.findall(r"https?://[^\s<>\"'()\[\]]+", text)
    return PageText(
        text=_normalize(text),
        links=_absolute_links((link.rstrip(".,;:!?") for link in links), ""),
    )


def cut_text(text: str, max_chars: int) -> tuple[str, bool]:
    """At most ``max_chars`` characters, ending at a space or line break when one is near."""
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    boundary = max(cut.rfind("\n"), cut.rfind(" "))
    if boundary >= max_chars - 200 and boundary > 0:
        cut = cut[:boundary]
    return cut.rstrip(), True


RENDER_MODES = ("never", "auto", "always")


def read_web_page(
    url: str,
    *,
    max_chars: int = 20_000,
    markdown: bool = True,
    allowlist: Iterable[str] = (),
    timeout_seconds: float = 30.0,
    resolver: Resolver = socket.getaddrinfo,
    render: str = "never",
    scratch_dir: Path | None = None,
    find_browser: Callable[[], str | None] | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Fetch and extract a page. Returns the read-web-page node's output object.

    ``render`` is ``never``, ``auto`` (render only when the fetched page likely needs
    JavaScript) or ``always``. The plain fetch runs first either way: it follows redirects,
    rejects non-HTML content and gives the browser the final address. Rendering then gets
    its own ``timeout_seconds``. With no browser installed, ``auto`` keeps the plain result
    and ``always`` fails. ``auto`` also keeps it when the browser fails.
    """
    from gofer.rattish import web_render

    if render not in RENDER_MODES:
        raise WebPageError(
            "configuration",
            "RATTISH_WEB_PAGE_INVALID_RENDER",
            f"render must be one of {', '.join(RENDER_MODES)}.",
        )
    page = fetch_page(url, allowlist=allowlist, timeout_seconds=timeout_seconds, resolver=resolver)
    decoded = decode_body(page.body, page.charset)
    truncated = page.truncated
    rendered = False
    if page.mime in HTML_TYPES:
        result = read_html(decoded, page.url, markdown=markdown)
        wanted = render == "always" or (render == "auto" and result.likely_needs_javascript)
        browser = (find_browser or web_render.find_browser)() if wanted else None
        if wanted and browser is None and render == "always":
            raise WebPageError(
                "configuration",
                "RATTISH_WEB_PAGE_NO_BROWSER",
                "render: always needs Chrome, Chromium or Edge, and none is installed.",
            )
        if browser is not None:
            if scratch_dir is None:
                scratch_dir = Path(tempfile.gettempdir())
            try:
                html, cut, _refused = web_render.render_page(
                    page.url,
                    browser=browser,
                    scratch_dir=scratch_dir,
                    allowlist=allowlist,
                    timeout_seconds=timeout_seconds,
                    resolver=resolver,
                    cancel=cancel,
                )
            except web_render.RenderError as exc:
                if render == "always":
                    raise WebPageError(exc.kind, exc.code, exc.message) from None
            else:
                result = read_html(html, page.url, markdown=markdown)
                truncated = cut
                rendered = True
    else:
        result = read_plain_text(decoded)
    text, cut = cut_text(result.text, max_chars)
    return {
        "title": result.title,
        "description": result.description,
        "text": text,
        "links": result.links,
        "final_url": page.url,
        "truncated": truncated or cut,
        "likely_needs_javascript": result.likely_needs_javascript,
        "rendered": rendered,
    }
