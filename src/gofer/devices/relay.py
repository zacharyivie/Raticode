"""ntfy carries bounded TLS frames, never application plaintext or delivery receipts."""

from __future__ import annotations

import asyncio
import ipaddress
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

from gofer.devices.framing import Frame, FrameError, strict_json

MAX_LINE = 16384
MAX_POLL_BYTES = 1024 * 1024
MAX_MESSAGES = 256
_TOPIC = re.compile(r"[A-Za-z0-9_-]{22,128}")
_CURSOR = re.compile(r"[A-Za-z0-9_-]{1,128}")
_ORIGIN_LOCK = threading.Lock()
_ORIGIN_BACKOFF: dict[tuple[str, str, int], float] = {}


class RelayError(Exception):
    """Sanitized failure; URLs, routing tokens and response bodies stay private."""

    def __init__(self, code: str, retry_after: float = 5.0) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True)
class RelayMessage:
    id: str
    frame: bytes


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def _retry_after(value: str | None) -> float:
    try:
        delay = float(value or "5")
    except ValueError:
        try:
            delay = parsedate_to_datetime(value or "").timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            delay = 5
    # Honour server backoff without allowing a malicious value to overflow sleep.
    return min(86400.0, max(1.0, delay))


class NtfyRelay:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        allow_loopback_http: bool = False,
        timeout: float = 15,
    ) -> None:
        try:
            url = urllib.parse.urlsplit(base_url)
            loopback = bool(url.hostname and ipaddress.ip_address(url.hostname).is_loopback)
        except ValueError:
            loopback = False
            try:
                url = urllib.parse.urlsplit(base_url)
            except ValueError:
                raise RelayError("relay_url") from None
        if (
            not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path not in ("", "/")
            or not (
                url.scheme == "https" or (allow_loopback_http and loopback and url.scheme == "http")
            )
        ):
            raise RelayError("relay_url")
        try:
            url.port
        except ValueError:
            raise RelayError("relay_url") from None
        if token is not None and (
            not token
            or not token.isascii()
            or any(ord(char) < 33 or ord(char) > 126 for char in token)
        ):
            raise RelayError("relay_token")
        if not 0 < timeout <= 60:
            raise RelayError("relay_timeout")
        self.base_url = base_url.rstrip("/")
        self._origin = (
            url.scheme.lower(),
            url.hostname.lower(),
            url.port or (443 if url.scheme == "https" else 80),
        )
        self._token = token
        self._timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect())
        self._gate = asyncio.Semaphore(2)

    def _request(
        self, topic: str, *, frame: bytes | None, since: str = "all"
    ) -> list[RelayMessage]:
        if not _TOPIC.fullmatch(topic):
            raise RelayError("relay_topic")
        if not _CURSOR.fullmatch(since):
            raise RelayError("relay_cursor")
        with _ORIGIN_LOCK:
            now = time.monotonic()
            for origin, until in list(_ORIGIN_BACKOFF.items()):
                if until <= now:
                    _ORIGIN_BACKOFF.pop(origin)
            until = _ORIGIN_BACKOFF.get(self._origin, 0)
            if until > now:
                raise RelayError("relay_rate_limited", until - now)
        headers = {"User-Agent": "Raticode-Device/2", "Accept": "application/x-ndjson"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        url = self.base_url + "/" + topic
        if frame is not None:
            Frame.decode(frame)
            # ntfy otherwise interprets JSON bodies as its JSON publish API.
            headers["Content-Type"] = "text/plain; charset=utf-8"
            headers["Title"] = "Raticode"
            headers["Priority"] = "min"
        else:
            url += "/json?" + urllib.parse.urlencode({"poll": "1", "since": since})
        request = urllib.request.Request(url, data=frame, headers=headers)
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                if response.status != 200:
                    raise RelayError("relay_http")
                if frame is not None:
                    if len(response.read(MAX_LINE + 1)) > MAX_LINE:
                        raise RelayError("relay_response_limit")
                    return []
                total = 0
                messages: list[RelayMessage] = []
                while True:
                    line = response.readline(MAX_LINE + 1)
                    if not line:
                        return messages
                    total += len(line)
                    if len(line) > MAX_LINE or total > MAX_POLL_BYTES:
                        raise RelayError("relay_response_limit")
                    value = strict_json(line, limit=MAX_LINE)
                    if value.get("event") in ("open", "keepalive"):
                        continue
                    if value.get("event") != "message":
                        raise RelayError("relay_event")
                    message_id = value.get("id")
                    body = value.get("message")
                    if (
                        not isinstance(message_id, str)
                        or not _CURSOR.fullmatch(message_id)
                        or not isinstance(body, str)
                        or value.get("topic") != topic
                        or "attachment" in value
                    ):
                        raise RelayError("relay_event")
                    raw = body.encode("utf-8")
                    Frame.decode(raw)
                    messages.append(RelayMessage(message_id, raw))
                    if len(messages) > MAX_MESSAGES:
                        raise RelayError("relay_response_limit")
        except urllib.error.HTTPError as exc:
            delay = _retry_after(exc.headers.get("Retry-After"))
            code = "relay_rate_limited" if exc.code == 429 else "relay_http"
            if exc.code == 429 or (exc.code >= 400 and exc.headers.get("Retry-After")):
                with _ORIGIN_LOCK:
                    _ORIGIN_BACKOFF[self._origin] = max(
                        _ORIGIN_BACKOFF.get(self._origin, 0), time.monotonic() + delay
                    )
            exc.close()
            raise RelayError(code, delay) from None
        except (OSError, ValueError, FrameError):
            raise RelayError("relay_unavailable_or_invalid") from None

    async def publish(self, topic: str, frame: bytes) -> None:
        async with self._gate:
            await asyncio.to_thread(self._request, topic, frame=frame)

    async def poll(self, topic: str, since: str = "all") -> list[RelayMessage]:
        async with self._gate:
            return await asyncio.to_thread(self._request, topic, frame=None, since=since)

    async def subscribe(
        self, topic: str, since: str = "all", *, interval: float = 5
    ) -> AsyncIterator[RelayMessage]:
        """Errors propagate so the owner can discard a broken TLS session and back off."""
        if interval < 1:
            raise RelayError("relay_poll_interval")
        while True:
            for message in await self.poll(topic, since):
                since = message.id
                yield message
            await asyncio.sleep(interval)
