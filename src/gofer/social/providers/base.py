"""What every platform adapter shares: the request it gets, its result and its errors.

Adapters send what they are given. They never decide whether something should publish,
never retry on their own and never put a login into an error message.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from gofer.core.http import HttpClient, HttpRequest, HttpResponse

ErrorKind = Literal["transient", "auth", "permanent", "outcome_unknown", "cap"]
DEFAULT_TIMEOUT = 30.0
_SECRETISH = re.compile(
    r"(access_token|refresh_token|client_secret|token|key)=[^&\s\"']+", re.IGNORECASE
)
_WEBHOOK = re.compile(r"/webhooks/\d+/[\w-]+")
_BEARER = re.compile(r"Bearer\s+[\w\-.~+/=]+", re.IGNORECASE)


class PublishError(Exception):
    """A failed send, classified for the publisher. ``message`` is safe to show."""

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        status: int | None = None,
        retry_after_seconds: float | None = None,
        disable_account: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.message = message
        self.status = status
        self.retry_after_seconds = retry_after_seconds
        self.disable_account = disable_account


@dataclass(frozen=True)
class Media:
    path: Path
    mime: str
    name: str
    size: int


@dataclass(frozen=True)
class PublishRequest:
    text: str
    link: str = ""
    media: tuple[Media, ...] = ()
    options: Mapping[str, Any] = field(default_factory=dict)
    account_options: Mapping[str, Any] = field(default_factory=dict)
    publish_at_unix: int | None = None  # set only for platform-held scheduling


@dataclass(frozen=True)
class PublishResult:
    remote_id: str
    url: str = ""


@dataclass(frozen=True)
class Capabilities:
    text_limit: int
    media: tuple[str, ...]
    native_scheduling: bool
    min_native_lead_seconds: int = 0
    max_native_lead_seconds: int = 0
    lookup: bool = False
    idempotency: bool = False
    delete: bool = False


SaveSecrets = Callable[[dict[str, str]], None]


class Adapter(Protocol):
    platform: str
    capabilities: Capabilities

    def validate(self, request: PublishRequest) -> list[str]: ...

    async def publish(
        self,
        request: PublishRequest,
        secrets: dict[str, str],
        http: HttpClient,
        save_secrets: SaveSecrets,
    ) -> PublishResult: ...

    async def cancel_native(
        self, remote_id: str, secrets: dict[str, str], http: HttpClient, save_secrets: SaveSecrets
    ) -> None: ...

    async def verify(
        self, secrets: dict[str, str], http: HttpClient, save_secrets: SaveSecrets
    ) -> dict[str, Any]: ...


def redact(text: str) -> str:
    text = _SECRETISH.sub(lambda m: f"{m.group(1)}=[hidden]", text)
    text = _WEBHOOK.sub("/webhooks/[hidden]", text)
    return _BEARER.sub("Bearer [hidden]", text)


def platform_message(response: HttpResponse, default: str) -> str:
    """A short, redacted message from a platform's error body."""
    try:
        body = json.loads(response.body.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError:
        body = {}
    candidates: list[Any] = []
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            candidates += [error.get("message"), error.get("error_user_msg")]
        candidates += [
            body.get("message"),
            body.get("detail"),
            body.get("title"),
            error if isinstance(error, str) else None,
            body.get("error_description"),
        ]
        errors = body.get("errors")
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            candidates.append(errors[0].get("message") or errors[0].get("detail"))
    for item in candidates:
        if isinstance(item, str) and item.strip():
            return redact(item.strip())[:300]
    return default


def retry_after(response: HttpResponse) -> float | None:
    headers = {key.lower(): value for key, value in response.headers.items()}
    for key in ("retry-after", "x-ratelimit-reset-after"):
        try:
            return max(0.0, float(headers[key]))
        except (KeyError, ValueError):
            continue
    try:
        body = json.loads(response.body or b"{}")
        if isinstance(body, dict) and isinstance(body.get("retry_after"), int | float):
            return float(body["retry_after"])
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return None


def classify(response: HttpResponse, label: str) -> PublishError:
    """The default mapping from an HTTP error to the publisher's error classes."""
    status = response.status
    message = platform_message(response, f"{label} answered {status}.")
    if status == 429:
        return PublishError(
            "transient", message, status=status, retry_after_seconds=retry_after(response)
        )
    if status >= 500:
        return PublishError(
            "transient", message, status=status, retry_after_seconds=retry_after(response)
        )
    if status == 401:
        return PublishError("auth", message, status=status)
    if 300 <= status < 400:
        return PublishError("permanent", f"{label} redirected the request.", status=status)
    return PublishError("permanent", message, status=status)


async def send(http: HttpClient, request: HttpRequest, label: str) -> HttpResponse:
    """Send one request. A timeout after sending is an unknown outcome, never retried."""
    try:
        return await http.send(request)
    except TimeoutError:
        raise PublishError(
            "outcome_unknown",
            f"{label} didn't answer in time, so Raticode can't tell whether it posted.",
        ) from None
    except (ConnectionRefusedError, ConnectionResetError) as exc:
        if isinstance(exc, ConnectionResetError):
            raise PublishError(
                "outcome_unknown",
                f"The connection to {label} dropped, so Raticode can't tell whether it posted.",
            ) from None
        raise PublishError("transient", f"Couldn't reach {label}.") from None
    except OSError:
        raise PublishError("transient", f"Couldn't reach {label}.") from None
    except ValueError as exc:
        raise PublishError("permanent", redact(str(exc))[:200]) from None


def json_body(response: HttpResponse) -> dict[str, Any]:
    try:
        value = json.loads(response.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def multipart(fields: list[tuple[str, str, bytes, str | None]]) -> tuple[bytes, str]:
    """Encode (name, filename, data, content type) parts. Filename "" for plain fields."""
    from uuid import uuid4

    boundary = f"raticode-{uuid4().hex}"
    chunks: list[bytes] = []
    for name, filename, data, content_type in fields:
        disposition = f'form-data; name="{name}"'
        if filename:
            disposition += f'; filename="{filename}"'
        chunks.append(f"--{boundary}\r\nContent-Disposition: {disposition}\r\n".encode())
        if content_type:
            chunks.append(f"Content-Type: {content_type}\r\n".encode())
        chunks.append(b"\r\n" + data + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class NoNativeScheduling:
    async def cancel_native(
        self, remote_id: str, secrets: dict[str, str], http: HttpClient, save_secrets: SaveSecrets
    ) -> None:
        raise PublishError("permanent", "This platform doesn't hold scheduled posts.")
