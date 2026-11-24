"""Facebook Page: a Page feed post with a long-lived Page access token.

Facebook can hold a scheduled post itself (``scheduled_publish_time``, 10 minutes to 30
days ahead), so an approved post far enough out is handed over early and goes out with
the computer off. Cancelling deletes the held post. The token goes in the form body,
never the URL. Error code 190 means the token is no longer valid.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from gofer.core.http import HttpClient, HttpRequest, HttpResponse
from gofer.social.providers.base import (
    DEFAULT_TIMEOUT,
    Capabilities,
    PublishError,
    PublishRequest,
    PublishResult,
    SaveSecrets,
    classify,
    json_body,
    send,
)

LABEL = "Facebook"
GRAPH_VERSION = "v24.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
MIN_LEAD = 10 * 60
MAX_LEAD = 30 * 24 * 60 * 60


class FacebookAdapter:
    platform = "facebook"
    capabilities = Capabilities(
        text_limit=63206,
        media=(),
        native_scheduling=True,
        min_native_lead_seconds=MIN_LEAD + 60,
        max_native_lead_seconds=MAX_LEAD - 60,
        delete=True,
    )

    def validate(self, request: PublishRequest) -> list[str]:
        problems = []
        if not request.text.strip() and not request.link:
            problems.append("Facebook needs text or a link.")
        if len(request.text) > self.capabilities.text_limit:
            problems.append("The text is too long for Facebook.")
        if request.media:
            problems.append("Photos on Facebook aren't supported yet. Remove the media.")
        return problems

    def _page(self, request_options: Any) -> str:
        page = str(request_options.get("page_id") or "")
        if not page.isdigit():
            raise PublishError("auth", "Add this Facebook Page's ID in Settings.")
        return page

    async def publish(
        self,
        request: PublishRequest,
        secrets: dict[str, str],
        http: HttpClient,
        save_secrets: SaveSecrets,
    ) -> PublishResult:
        token = secrets.get("page_access_token")
        if not token:
            raise PublishError("auth", "This Facebook Page has no access token saved.")
        page = self._page(request.account_options)
        form: dict[str, str] = {"message": request.text, "access_token": token}
        if request.link:
            form["link"] = request.link
        if request.publish_at_unix is not None:
            form["published"] = "false"
            form["scheduled_publish_time"] = str(request.publish_at_unix)
        response = await send(
            http,
            HttpRequest(
                "POST",
                f"{GRAPH}/{page}/feed",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=urlencode(form).encode(),
                timeout_seconds=DEFAULT_TIMEOUT,
            ),
            LABEL,
        )
        if response.status >= 300:
            raise _error(response)
        post_id = str(json_body(response).get("id") or "")
        return PublishResult(remote_id=post_id, url=f"https://www.facebook.com/{post_id}")

    async def cancel_native(
        self, remote_id: str, secrets: dict[str, str], http: HttpClient, save_secrets: SaveSecrets
    ) -> None:
        token = secrets.get("page_access_token")
        if not token:
            raise PublishError("auth", "This Facebook Page has no access token saved.")
        response = await send(
            http,
            HttpRequest(
                "DELETE",
                f"{GRAPH}/{remote_id}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=urlencode({"access_token": token}).encode(),
                timeout_seconds=DEFAULT_TIMEOUT,
            ),
            LABEL,
        )
        if response.status >= 300 and response.status != 404:
            raise _error(response)

    async def verify(
        self, secrets: dict[str, str], http: HttpClient, save_secrets: SaveSecrets
    ) -> dict[str, Any]:
        token = secrets.get("page_access_token")
        if not token:
            raise PublishError("auth", "No Page access token saved.")
        response = await send(
            http,
            HttpRequest(
                "GET",
                f"{GRAPH}/me?fields=id,name",
                headers={"Authorization": f"Bearer {token}"},
                timeout_seconds=DEFAULT_TIMEOUT,
            ),
            LABEL,
        )
        if response.status >= 300:
            raise _error(response)
        data = json_body(response)
        return {"name": str(data.get("name") or "Facebook Page"), "page_id": str(data.get("id"))}


def _error(response: HttpResponse) -> PublishError:
    error = classify(response, LABEL)
    detail = json_body(response).get("error")
    if isinstance(detail, dict) and detail.get("code") in {190, 102}:
        error.kind = "auth"
    elif isinstance(detail, dict) and detail.get("code") in {4, 17, 32, 613}:
        error.kind = "transient"
    return error
