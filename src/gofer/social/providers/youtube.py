"""YouTube: a resumable ``videos.insert`` upload with Google OAuth.

The post text becomes the description, the variant's ``title`` option the title. YouTube
can hold a scheduled video itself (``status.publishAt`` on a private video), so it is
handed over early like Facebook. Access tokens last an hour, so every send first trades
the stored refresh token for a fresh one. A timeout during the upload is an unknown
outcome and is never retried on its own.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
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

LABEL = "YouTube"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = (
    "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
)
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
TITLE_LIMIT = 100
DESCRIPTION_BYTES = 5000
MAX_VIDEO_BYTES = 256 * 1024 * 1024
UPLOAD_TIMEOUT = 900.0


class YouTubeAdapter:
    platform = "youtube"
    capabilities = Capabilities(
        text_limit=DESCRIPTION_BYTES,
        media=("video",),
        native_scheduling=True,
        min_native_lead_seconds=15 * 60,
        max_native_lead_seconds=365 * 24 * 60 * 60,
        delete=True,
    )

    def validate(self, request: PublishRequest) -> list[str]:
        problems = []
        title = str(request.options.get("title") or "").strip()
        if not title:
            problems.append("YouTube needs a title.")
        elif len(title) > TITLE_LIMIT or any(char in title for char in "<>"):
            problems.append("YouTube titles are up to 100 characters with no < or >.")
        if len(_description(request).encode()) > DESCRIPTION_BYTES or any(
            char in request.text for char in "<>"
        ):
            problems.append("YouTube descriptions are up to 5,000 bytes with no < or >.")
        videos = [item for item in request.media if item.mime.startswith("video/")]
        if len(videos) != 1 or len(request.media) != 1:
            problems.append("YouTube needs exactly one video file.")
        elif videos[0].size > MAX_VIDEO_BYTES:
            problems.append("Raticode uploads videos up to 256 MB.")
        return problems

    async def _token(
        self, http: HttpClient, secrets: dict[str, str], save_secrets: SaveSecrets
    ) -> str:
        if not secrets.get("refresh_token") or not secrets.get("client_id"):
            if secrets.get("access_token"):
                return secrets["access_token"]
            raise PublishError("auth", "This YouTube account has no sign-in saved.")
        form = {
            "grant_type": "refresh_token",
            "refresh_token": secrets["refresh_token"],
            "client_id": secrets["client_id"],
        }
        if secrets.get("client_secret"):
            form["client_secret"] = secrets["client_secret"]
        response = await send(
            http,
            HttpRequest(
                "POST",
                TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=urlencode(form).encode(),
                timeout_seconds=DEFAULT_TIMEOUT,
            ),
            LABEL,
        )
        data = json_body(response)
        if response.status >= 500:
            raise classify(response, LABEL)
        if response.status >= 300 or not isinstance(data.get("access_token"), str):
            raise PublishError("auth", "Google signed this account out. Reconnect it.")
        token = str(data["access_token"])
        secrets["access_token"] = token
        save_secrets(dict(secrets))
        return token

    async def publish(
        self,
        request: PublishRequest,
        secrets: dict[str, str],
        http: HttpClient,
        save_secrets: SaveSecrets,
    ) -> PublishResult:
        token = await self._token(http, secrets, save_secrets)
        video = request.media[0]
        privacy = str(request.account_options.get("privacy") or "public")
        status: dict[str, Any] = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
        if request.publish_at_unix is not None:
            status = {
                "privacyStatus": "private",
                "publishAt": datetime.fromtimestamp(request.publish_at_unix, UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "selfDeclaredMadeForKids": False,
            }
        metadata = {
            "snippet": {
                "title": str(request.options.get("title") or "")[:TITLE_LIMIT],
                "description": _description(request),
            },
            "status": status,
        }
        start = await send(
            http,
            HttpRequest(
                "POST",
                UPLOAD_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": video.mime,
                    "X-Upload-Content-Length": str(video.size),
                },
                body=json.dumps(metadata).encode(),
                timeout_seconds=DEFAULT_TIMEOUT,
            ),
            LABEL,
        )
        if start.status >= 300:
            raise _error(start)
        headers = {key.lower(): value for key, value in start.headers.items()}
        session = headers.get("location", "")
        if not session.startswith("https://www.googleapis.com/"):
            raise PublishError("transient", "YouTube didn't start the upload.")
        uploaded = await send(
            http,
            HttpRequest(
                "PUT",
                session,
                headers={"Authorization": f"Bearer {token}", "Content-Type": video.mime},
                body=video.path.read_bytes(),
                timeout_seconds=UPLOAD_TIMEOUT,
            ),
            LABEL,
        )
        if uploaded.status >= 500:
            raise PublishError(
                "outcome_unknown",
                "YouTube failed while receiving the video, so it may have been uploaded.",
                status=uploaded.status,
            )
        if uploaded.status >= 300:
            raise _error(uploaded)
        video_id = str(json_body(uploaded).get("id") or "")
        return PublishResult(remote_id=video_id, url=f"https://www.youtube.com/watch?v={video_id}")

    async def cancel_native(
        self, remote_id: str, secrets: dict[str, str], http: HttpClient, save_secrets: SaveSecrets
    ) -> None:
        token = await self._token(http, secrets, save_secrets)
        response = await send(
            http,
            HttpRequest(
                "DELETE",
                f"{VIDEOS_URL}?{urlencode({'id': remote_id})}",
                headers={"Authorization": f"Bearer {token}"},
                timeout_seconds=DEFAULT_TIMEOUT,
            ),
            LABEL,
        )
        if response.status >= 300 and response.status != 404:
            raise _error(response)

    async def verify(
        self, secrets: dict[str, str], http: HttpClient, save_secrets: SaveSecrets
    ) -> dict[str, Any]:
        token = await self._token(http, secrets, save_secrets)
        response = await send(
            http,
            HttpRequest(
                "GET",
                "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                headers={"Authorization": f"Bearer {token}"},
                timeout_seconds=DEFAULT_TIMEOUT,
            ),
            LABEL,
        )
        if response.status >= 300:
            raise _error(response)
        items = json_body(response).get("items") or []
        title = items[0].get("snippet", {}).get("title") if items else None
        return {"name": str(title or "YouTube channel")}


def _description(request: PublishRequest) -> str:
    text = request.text
    if request.link and request.link not in text:
        text = f"{text}\n\n{request.link}" if text else request.link
    return text


def _error(response: HttpResponse) -> PublishError:
    error = classify(response, LABEL)
    detail = json_body(response).get("error")
    reasons = []
    if isinstance(detail, dict):
        reasons = [
            item.get("reason") for item in detail.get("errors") or [] if isinstance(item, dict)
        ]
    if response.status == 403 and any(
        reason in {"quotaExceeded", "uploadLimitExceeded", "rateLimitExceeded"}
        for reason in reasons
    ):
        error.kind = "cap"
    return error
