"""X: ``POST /2/tweets`` as the account's own user, signed one of two ways.

API keys (the default): the app's API key and secret plus the access token and secret
that X's developer console generates for the app owner's own account. Every request is
signed with OAuth 1.0a (HMAC-SHA1). Nothing expires and no callback URL is involved. The
JSON body isn't part of the signature, as OAuth 1.0a only signs query and form fields.

OAuth 2.0: a user-context token from X's PKCE sign-in. These expire after about two
hours. When the account has a refresh token and client id, a 401 renews the token once
and the new tokens go back into the keychain.

X charges per post and more for posts with a link, so the dashboard shows the cost and
the account has a monthly cap.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from uuid import uuid4

from gofer.core.http import HttpClient, HttpRequest, HttpResponse
from gofer.social.providers.base import (
    DEFAULT_TIMEOUT,
    Capabilities,
    NoNativeScheduling,
    PublishError,
    PublishRequest,
    PublishResult,
    SaveSecrets,
    classify,
    json_body,
    send,
)

LABEL = "X"
API = "https://api.x.com/2"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
URL_WEIGHT = 23  # X counts every link as 23 characters
COST_USD = 0.015
COST_WITH_LINK_USD = 0.20
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
# The four values from the console's Keys and tokens page that make an API-key login.
API_KEY_FIELDS = ("api_key", "api_secret", "access_token", "access_token_secret")
READ_ONLY_HINT = (
    " If your X app's permission is Read only, set it to Read and write in X's developer "
    "console, then regenerate the access token and secret and paste them again."
)


def uses_api_keys(secrets: dict[str, str]) -> bool:
    """True when the saved login is the console's API keys rather than an OAuth 2.0 token."""
    return all(secrets.get(key) for key in API_KEY_FIELDS)


def _percent(value: str) -> str:
    # RFC 3986 unreserved characters only, as X's signing guide requires.
    return quote(value, safe="-._~")


def oauth1_header(
    method: str,
    url: str,
    *,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
    nonce: str,
    timestamp: int,
    params: Iterable[tuple[str, str]] = (),
) -> str:
    """An OAuth 1.0a ``Authorization`` header (HMAC-SHA1).

    ``params`` are form-encoded body fields; query fields are read from ``url``. A JSON
    body is never signed.
    """
    parts = urlsplit(url)
    base_url = f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path}"
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(timestamp),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    pairs = [*parse_qsl(parts.query, keep_blank_values=True), *params, *oauth.items()]
    encoded = sorted((_percent(key), _percent(value)) for key, value in pairs)
    parameter_string = "&".join(f"{key}={value}" for key, value in encoded)
    base = "&".join((method.upper(), _percent(base_url), _percent(parameter_string)))
    signing_key = f"{_percent(consumer_secret)}&{_percent(token_secret)}"
    digest = hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()
    oauth["oauth_signature"] = base64.b64encode(digest).decode()
    return "OAuth " + ", ".join(
        f'{_percent(key)}="{_percent(value)}"' for key, value in sorted(oauth.items())
    )


def _nonce() -> str:
    return uuid4().hex


def _timestamp() -> int:
    return int(time.time())


def weighted_length(text: str) -> int:
    links = _URL.findall(text)
    rest = _URL.sub("", text)
    return len(rest) + URL_WEIGHT * len(links)


def post_cost(text: str, link: str = "") -> float:
    return COST_WITH_LINK_USD if (link or _URL.search(text)) else COST_USD


class XAdapter(NoNativeScheduling):
    platform = "x"
    capabilities = Capabilities(text_limit=280, media=(), native_scheduling=False, lookup=False)

    def validate(self, request: PublishRequest) -> list[str]:
        text = _content(request)
        problems = []
        if not text.strip():
            problems.append("X needs text.")
        if weighted_length(text) > 280:
            problems.append("X allows 280 characters (each link counts as 23).")
        if request.media:
            problems.append("Images on X aren't supported yet. Remove the media.")
        return problems

    async def publish(
        self,
        request: PublishRequest,
        secrets: dict[str, str],
        http: HttpClient,
        save_secrets: SaveSecrets,
    ) -> PublishResult:
        body = json.dumps({"text": _content(request)}).encode()
        response = await self._call(http, secrets, save_secrets, "POST", f"{API}/tweets", body)
        if response.status >= 300:
            error = classify(response, LABEL)
            if response.status == 403:
                error.kind = "permanent"
                if uses_api_keys(secrets):
                    error.message += READ_ONLY_HINT
            raise error
        data = json_body(response).get("data") or {}
        tweet_id = str(data.get("id") or "") if isinstance(data, dict) else ""
        return PublishResult(remote_id=tweet_id, url=f"https://x.com/i/web/status/{tweet_id}")

    async def verify(
        self, secrets: dict[str, str], http: HttpClient, save_secrets: SaveSecrets
    ) -> dict[str, Any]:
        response = await self._call(http, secrets, save_secrets, "GET", f"{API}/users/me", None)
        if response.status >= 300:
            raise classify(response, LABEL)
        data = json_body(response).get("data") or {}
        return {"name": f"@{data.get('username')}" if isinstance(data, dict) else "X"}

    async def _call(
        self,
        http: HttpClient,
        secrets: dict[str, str],
        save_secrets: SaveSecrets,
        method: str,
        url: str,
        body: bytes | None,
    ) -> HttpResponse:
        if uses_api_keys(secrets):
            header = oauth1_header(
                method,
                url,
                consumer_key=secrets["api_key"],
                consumer_secret=secrets["api_secret"],
                token=secrets["access_token"],
                token_secret=secrets["access_token_secret"],
                nonce=_nonce(),
                timestamp=_timestamp(),
            )
            headers = {"Authorization": header}
            if body is not None:
                headers["Content-Type"] = "application/json"
            request = HttpRequest(
                method, url, headers=headers, body=body, timeout_seconds=DEFAULT_TIMEOUT
            )
            # Console keys don't expire, so a 401 means they were regenerated or revoked.
            return await send(http, request, LABEL)
        for attempt in range(2):
            token = secrets.get("access_token")
            if not token:
                raise PublishError("auth", "This X account has no access token saved.")
            response = await send(
                http,
                HttpRequest(
                    method,
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    body=body,
                    timeout_seconds=DEFAULT_TIMEOUT,
                ),
                LABEL,
            )
            if response.status != 401 or attempt or not secrets.get("refresh_token"):
                return response
            await self._refresh(http, secrets, save_secrets)
        return response

    async def _refresh(
        self, http: HttpClient, secrets: dict[str, str], save_secrets: SaveSecrets
    ) -> None:
        client_id = secrets.get("client_id")
        if not client_id:
            raise PublishError("auth", "X signed this account out. Reconnect it.")
        form = {
            "grant_type": "refresh_token",
            "refresh_token": secrets["refresh_token"],
            "client_id": client_id,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if secrets.get("client_secret"):
            pair = f"{client_id}:{secrets['client_secret']}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
        response = await send(
            http,
            HttpRequest(
                "POST",
                TOKEN_URL,
                headers=headers,
                body=urlencode(form).encode(),
                timeout_seconds=DEFAULT_TIMEOUT,
            ),
            LABEL,
        )
        data = json_body(response)
        if response.status >= 300 or not isinstance(data.get("access_token"), str):
            raise PublishError("auth", "X signed this account out. Reconnect it.")
        secrets["access_token"] = data["access_token"]
        if isinstance(data.get("refresh_token"), str):
            secrets["refresh_token"] = data["refresh_token"]
        save_secrets(dict(secrets))


def _content(request: PublishRequest) -> str:
    text = request.text
    if request.link and request.link not in text:
        text = f"{text}\n{request.link}" if text else request.link
    return text
