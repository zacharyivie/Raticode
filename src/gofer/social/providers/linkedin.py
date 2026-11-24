"""LinkedIn personal profile: ``POST /rest/posts`` with Share on LinkedIn (w_member_social).

LinkedIn's API can't schedule and its API Terms bar using the APIs "to automate posting",
so LinkedIn variants default to tap to publish: at the scheduled time the post becomes
due and goes out when the user taps Publish. Post text uses LinkedIn's "little" format,
so every reserved character is escaped and pasted mention syntax stays literal text.
There is no idempotency key and a self-serve token can't list the member's posts, so a
timeout after sending is an unknown outcome straight away.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from gofer.core.http import HttpClient, HttpRequest
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
    platform_message,
    send,
)

LABEL = "LinkedIn"
API = "https://api.linkedin.com"
# The pinned Linkedin-Version and LinkedIn's sunset date for it. LinkedIn retires each
# version about a year after release; tests fail 60 days before this date so it is bumped
# in time. An account can override the version in its settings.
API_VERSION = "202610"
API_VERSION_SUNSET = date(2027, 10, 15)
TEXT_LIMIT = 3000
_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")


def escape_little(text: str) -> str:
    """Escape every reserved character of LinkedIn's little text format."""
    return _RESERVED.sub(r"\\\1", text)


class LinkedInAdapter(NoNativeScheduling):
    platform = "linkedin"
    capabilities = Capabilities(text_limit=TEXT_LIMIT, media=(), native_scheduling=False)

    def validate(self, request: PublishRequest) -> list[str]:
        problems = []
        if not request.text.strip():
            problems.append("LinkedIn needs text.")
        if len(request.text) > TEXT_LIMIT:
            problems.append(f"LinkedIn allows about {TEXT_LIMIT} characters.")
        if request.media:
            problems.append("Images on LinkedIn aren't supported yet. Remove the media.")
        if request.options.get("visibility", "PUBLIC") not in {"PUBLIC", "CONNECTIONS"}:
            problems.append("LinkedIn visibility is PUBLIC or CONNECTIONS.")
        return problems

    async def publish(
        self,
        request: PublishRequest,
        secrets: dict[str, str],
        http: HttpClient,
        save_secrets: SaveSecrets,
    ) -> PublishResult:
        token = secrets.get("access_token")
        if not token:
            raise PublishError("auth", "This LinkedIn account has no access token saved.")
        author = str(request.account_options.get("author_urn") or "")
        if not author.startswith("urn:li:person:"):
            raise PublishError(
                "auth", "Check this LinkedIn account in Settings so Raticode knows who posts."
            )
        body: dict[str, Any] = {
            "author": author,
            "commentary": escape_little(request.text),
            "visibility": request.options.get("visibility") or "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if request.link:
            # The Posts API doesn't scrape links, so the post supplies its own card title.
            # Only the title the review card shows is sent: no description, even if an
            # older post stored one, so nothing reaches LinkedIn that the user didn't see.
            article = {
                "source": request.link,
                "title": str(request.options.get("article_title") or request.link)[:400],
            }
            body["content"] = {"article": article}
        version = str(request.account_options.get("api_version") or API_VERSION)
        response = await send(
            http,
            HttpRequest(
                "POST",
                f"{API}/rest/posts",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Linkedin-Version": version,
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                body=json.dumps(body).encode(),
                timeout_seconds=DEFAULT_TIMEOUT,
            ),
            LABEL,
        )
        if response.status == 409:
            raise PublishError(
                "transient", platform_message(response, "LinkedIn asked to retry."), status=409
            )
        if response.status == 429:
            # Usually the member's daily limit, which resets at midnight UTC.
            raise PublishError(
                "cap",
                "LinkedIn's daily limit for this account is used up. It resets at midnight UTC.",
                status=429,
            )
        if response.status == 403:
            raise PublishError(
                "permanent", platform_message(response, "LinkedIn refused the post."), status=403
            )
        if response.status >= 300:
            raise classify(response, LABEL)
        headers = {key.lower(): value for key, value in response.headers.items()}
        urn = headers.get("x-restli-id") or str(json_body(response).get("id") or "")
        url = f"https://www.linkedin.com/feed/update/{urn}/" if urn else ""
        return PublishResult(remote_id=urn, url=url)

    async def verify(
        self, secrets: dict[str, str], http: HttpClient, save_secrets: SaveSecrets
    ) -> dict[str, Any]:
        """Reads ``sub`` from OpenID Connect userinfo and returns the author URN."""
        token = secrets.get("access_token")
        if not token:
            raise PublishError("auth", "No access token saved.")
        response = await send(
            http,
            HttpRequest(
                "GET",
                f"{API}/v2/userinfo",
                headers={"Authorization": f"Bearer {token}"},
                timeout_seconds=DEFAULT_TIMEOUT,
            ),
            LABEL,
        )
        if response.status >= 300:
            raise classify(response, LABEL)
        data = json_body(response)
        sub = data.get("sub")
        if not isinstance(sub, str) or not sub:
            raise PublishError("permanent", "LinkedIn didn't say which member this is.")
        return {"name": str(data.get("name") or "LinkedIn"), "author_urn": f"urn:li:person:{sub}"}
