"""Discord: Execute Webhook with ``wait=true``, so a sent message comes back with its id.

``allowed_mentions`` defaults to nothing allowed, so a draft can't ping @everyone, a role
or a person unless the account's settings allow it. A 404 means the webhook was deleted;
Discord asks clients not to keep calling it, so the account is turned off.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

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
    multipart,
    send,
)

LABEL = "Discord"
MAX_FILES = 10
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class DiscordAdapter(NoNativeScheduling):
    platform = "discord"
    capabilities = Capabilities(text_limit=2000, media=("image", "video"), native_scheduling=False)

    def validate(self, request: PublishRequest) -> list[str]:
        problems = []
        text = _content(request)
        if not text.strip() and not request.media:
            problems.append("Discord needs text or a file.")
        if len(text) > self.capabilities.text_limit:
            problems.append(f"Discord allows {self.capabilities.text_limit} characters.")
        if len(request.media) > MAX_FILES:
            problems.append(f"Discord allows {MAX_FILES} files.")
        if sum(item.size for item in request.media) > MAX_UPLOAD_BYTES:
            problems.append("Discord webhooks take at most 10 MB of files.")
        return problems

    async def publish(
        self,
        request: PublishRequest,
        secrets: dict[str, str],
        http: HttpClient,
        save_secrets: SaveSecrets,
    ) -> PublishResult:
        webhook = secrets.get("webhook_url")
        if not webhook:
            raise PublishError("auth", "This Discord account has no webhook saved.")
        query: dict[str, str] = {"wait": "true"}
        thread_id = request.options.get("thread_id") or request.account_options.get("thread_id")
        if isinstance(thread_id, str) and thread_id.isdigit():
            query["thread_id"] = thread_id
        parse = [
            item
            for item in request.account_options.get("allowed_mentions") or []
            if item in {"users", "roles", "everyone"}
        ]
        payload: dict[str, Any] = {
            "content": _content(request),
            "allowed_mentions": {"parse": parse},
        }
        url = f"{webhook}?{urlencode(query)}"
        if request.media:
            parts: list[tuple[str, str, bytes, str | None]] = [
                ("payload_json", "", json.dumps(payload).encode(), "application/json")
            ]
            for index, item in enumerate(request.media):
                parts.append((f"files[{index}]", item.name, item.path.read_bytes(), item.mime))
            body, content_type = multipart(parts)
        else:
            body, content_type = json.dumps(payload).encode(), "application/json"
        response = await send(
            http,
            HttpRequest(
                "POST",
                url,
                headers={"Content-Type": content_type, "User-Agent": "Raticode"},
                body=body,
                timeout_seconds=DEFAULT_TIMEOUT,
            ),
            LABEL,
        )
        if response.status == 404:
            raise PublishError(
                "auth",
                "The Discord webhook was deleted. Add a new one to post again.",
                status=404,
                disable_account=True,
            )
        if response.status >= 300:
            raise classify(response, LABEL)
        data = json_body(response)
        message_id = str(data.get("id") or "")
        channel = str(data.get("channel_id") or "")
        guild = str(data.get("guild_id") or "")
        link = f"https://discord.com/channels/{guild}/{channel}/{message_id}" if guild else ""
        return PublishResult(remote_id=message_id, url=link)

    async def verify(
        self, secrets: dict[str, str], http: HttpClient, save_secrets: SaveSecrets
    ) -> dict[str, Any]:
        webhook = secrets.get("webhook_url")
        if not webhook:
            raise PublishError("auth", "No webhook saved.")
        response = await send(
            http, HttpRequest("GET", webhook, timeout_seconds=DEFAULT_TIMEOUT), LABEL
        )
        if response.status == 404:
            raise PublishError("auth", "The Discord webhook was deleted.", status=404)
        if response.status >= 300:
            raise classify(response, LABEL)
        data = json_body(response)
        return {"name": str(data.get("name") or "Discord webhook")}


def _content(request: PublishRequest) -> str:
    text = request.text
    if request.link and request.link not in text:
        text = f"{text}\n{request.link}" if text else request.link
    return text
