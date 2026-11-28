"""Social platform adapters on their own: send errors, retry hints, redaction, and each
platform's validate, publish, verify and delete paths.

Every adapter talks to ``FakeHttp``, so no test reaches a real platform, and every login
is a made-up value.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import parse_qs, urlsplit

import pytest

from gofer.core.http import HttpRequest, HttpResponse
from gofer.social.providers.base import (
    Media,
    NoNativeScheduling,
    PublishError,
    PublishRequest,
    classify,
    json_body,
    multipart,
    platform_message,
    redact,
    retry_after,
    send,
)
from gofer.social.providers.discord import DiscordAdapter
from gofer.social.providers.facebook import FacebookAdapter
from gofer.social.providers.linkedin import LinkedInAdapter
from gofer.social.providers.youtube import YouTubeAdapter

T = TypeVar("T")
FAKE_WEBHOOK = "https://discord.com/api/webhooks/123456/fake-webhook-token"
FB_TOKEN = {"page_access_token": "fake-page-token"}
FB_OPTIONS = {"page_id": "1234567890"}
LI_TOKEN = {"access_token": "fake-linkedin-token"}
LI_OPTIONS = {"author_urn": "urn:li:person:abc123"}
YT_LOGIN = {"refresh_token": "fake-refresh", "client_id": "fake-client"}


class FakeHttp:
    """Records requests; answers from a queue of responses or exceptions."""

    def __init__(self) -> None:
        self.requests: list[HttpRequest] = []
        self.replies: list[HttpResponse | BaseException] = []

    def reply(self, status: int, body: Any = None, headers: dict[str, str] | None = None) -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
        self.replies.append(HttpResponse(status, headers or {}, raw))

    def fail(self, error: BaseException) -> None:
        self.replies.append(error)

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError(f"Unexpected request to {urlsplit(request.url).netloc}")
        item = self.replies.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class Saved:
    """Collects what an adapter asks to save, like the account store would."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def __call__(self, secrets: dict[str, str]) -> None:
        self.calls.append(secrets)


def run(coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def response(status: int, body: Any = None, headers: dict[str, str] | None = None) -> HttpResponse:
    raw = body if isinstance(body, bytes) else json.dumps(body or {}).encode()
    return HttpResponse(status, headers or {}, raw)


def raises(coroutine: Coroutine[Any, Any, Any]) -> PublishError:
    with pytest.raises(PublishError) as caught:
        run(coroutine)
    return caught.value


def media(tmp_path: Path, name: str, mime: str, size: int | None = None) -> Media:
    path = tmp_path / name
    path.write_bytes(b"fake-bytes")
    return Media(path=path, mime=mime, name=name, size=len(b"fake-bytes") if size is None else size)


# --- base.send ---------------------------------------------------------------------------


def _send_error(error: BaseException) -> PublishError:
    http = FakeHttp()
    http.fail(error)
    return raises(send(http, HttpRequest("POST", "https://example.test/x"), "Example"))


@pytest.mark.parametrize(
    ("error", "kind", "words"),
    [
        (TimeoutError(), "outcome_unknown", "didn't answer in time"),
        (ConnectionResetError(), "outcome_unknown", "connection to Example dropped"),
        (ConnectionRefusedError(), "transient", "Couldn't reach Example."),
        (OSError("no route"), "transient", "Couldn't reach Example."),
    ],
)
def test_send_sorts_network_failures(error: BaseException, kind: str, words: str) -> None:
    caught = _send_error(error)
    assert caught.kind == kind
    assert words in caught.message


def test_send_timeout_is_outcome_unknown_never_transient() -> None:
    caught = _send_error(TimeoutError("read timed out"))
    assert caught.kind == "outcome_unknown"
    assert caught.message == (
        "Example didn't answer in time, so Raticode can't tell whether it posted."
    )


def test_send_value_error_is_permanent_and_redacted() -> None:
    caught = _send_error(
        ValueError(f"bad url {FAKE_WEBHOOK}?access_token=sekrit with Bearer abc.def-123")
    )
    assert caught.kind == "permanent"
    assert "sekrit" not in caught.message
    assert "fake-webhook-token" not in caught.message
    assert "abc.def-123" not in caught.message
    assert "access_token=[hidden]" in caught.message
    assert "/webhooks/[hidden]" in caught.message
    assert "Bearer [hidden]" in caught.message


def test_send_value_error_text_is_capped() -> None:
    assert len(_send_error(ValueError("x" * 500)).message) == 200


def test_send_returns_the_response() -> None:
    http = FakeHttp()
    http.reply(200, {"ok": True})
    answer = run(send(http, HttpRequest("GET", "https://example.test/"), "Example"))
    assert answer.status == 200
    assert len(http.requests) == 1


# --- base.classify and retry_after ------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "headers", "body", "wait"),
    [
        (429, {"Retry-After": "12"}, {}, 12.0),
        (503, {"X-RateLimit-Reset-After": "2.5"}, {}, 2.5),
        (429, {}, {"retry_after": 7}, 7.0),
        (500, {}, {"retry_after": 1.25}, 1.25),
        (502, {}, {}, None),
    ],
)
def test_classify_rate_limits_and_server_errors_are_transient(
    status: int, headers: dict[str, str], body: Any, wait: float | None
) -> None:
    error = classify(response(status, body, headers), "Example")
    assert error.kind == "transient"
    assert error.status == status
    assert error.retry_after_seconds == wait


def test_retry_after_clamps_negative_and_skips_bad_headers() -> None:
    assert retry_after(response(429, {}, {"Retry-After": "-5"})) == 0.0
    bad_header = response(
        429, {}, {"Retry-After": "Wed, 21 Oct 2026", "x-ratelimit-reset-after": "3"}
    )
    assert retry_after(bad_header) == 3.0
    assert retry_after(response(429, b"not json")) is None
    assert retry_after(response(429, b"\xff\xfe")) is None
    assert retry_after(response(429, {"retry_after": "soon"})) is None


def test_classify_401_is_auth() -> None:
    error = classify(response(401, {"message": "Invalid token"}), "Example")
    assert (error.kind, error.message, error.status) == ("auth", "Invalid token", 401)


def test_classify_3xx_is_permanent_redirected() -> None:
    error = classify(response(302, {"message": "moved"}, {"Location": "https://x"}), "Example")
    assert error.kind == "permanent"
    assert error.message == "Example redirected the request."
    assert error.status == 302


def test_classify_other_4xx_is_permanent_with_default_message() -> None:
    error = classify(response(400, b""), "Example")
    assert (error.kind, error.message) == ("permanent", "Example answered 400.")
    assert error.retry_after_seconds is None


# --- base.platform_message and redact ---------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"error": {"message": "Token expired"}}, "Token expired"),
        ({"error": {"error_user_msg": "Page is gone"}}, "Page is gone"),
        ({"errors": [{"message": "Duplicate content"}]}, "Duplicate content"),
        ({"errors": [{"detail": "Too long"}]}, "Too long"),
        ({"error": "invalid_grant", "error_description": "Bad grant"}, "invalid_grant"),
        ({"error_description": "Refresh token revoked"}, "Refresh token revoked"),
        ({"detail": "Not allowed"}, "Not allowed"),
        ({"title": "Forbidden"}, "Forbidden"),
        ({"message": "  padded  "}, "padded"),
    ],
)
def test_platform_message_picks_the_platform_text(body: Any, expected: str) -> None:
    assert platform_message(response(400, body), "fallback") == expected


def test_platform_message_prefers_error_message_over_errors_list() -> None:
    body = {"error": {"message": "first"}, "errors": [{"message": "second"}]}
    assert platform_message(response(400, body), "fallback") == "first"


@pytest.mark.parametrize(
    "body",
    [b"<html>oops</html>", b"[1, 2]", {"errors": []}, {"errors": ["text"]}, {"message": "  "}],
)
def test_platform_message_falls_back(body: Any) -> None:
    assert platform_message(response(500, body), "fallback") == "fallback"


def test_platform_message_hides_tokens_and_webhook_paths() -> None:
    body = {
        "error": {
            "message": (
                f"Header Bearer ya29.a0-fake_token was rejected for {FAKE_WEBHOOK} "
                "(client_secret=shh&refresh_token=r1)"
            )
        }
    }
    message = platform_message(response(401, body), "fallback")
    for secret in ("ya29.a0-fake_token", "fake-webhook-token", "123456", "shh", "r1)"):
        assert secret not in message
    assert "Bearer [hidden]" in message
    assert "/webhooks/[hidden]" in message
    assert "client_secret=[hidden]" in message


def test_platform_message_is_capped() -> None:
    assert len(platform_message(response(400, {"message": "y" * 1000}), "fallback")) == 300


def test_redact_keeps_ordinary_text() -> None:
    assert redact("Nothing secret here.") == "Nothing secret here."
    assert redact("key=abc token=def") == "key=[hidden] token=[hidden]"


# --- base helpers ------------------------------------------------------------------------


def test_json_body_returns_only_objects() -> None:
    assert json_body(response(200, {"id": "1"})) == {"id": "1"}
    assert json_body(response(200, b"[1]")) == {}
    assert json_body(response(200, b"\xff")) == {}
    assert json_body(response(200, b"")) == {}


def test_multipart_encodes_fields_and_files() -> None:
    body, content_type = multipart(
        [("payload_json", "", b"{}", "application/json"), ("files[0]", "a.png", b"PNG", None)]
    )
    boundary = content_type.split("boundary=", 1)[1]
    assert content_type.startswith("multipart/form-data; boundary=raticode-")
    assert b'name="payload_json"\r\nContent-Type: application/json' in body
    assert b'name="files[0]"; filename="a.png"\r\n\r\nPNG' in body
    assert body.endswith(f"--{boundary}--\r\n".encode())


def test_no_native_scheduling_refuses_to_cancel() -> None:
    caught = raises(NoNativeScheduling().cancel_native("1", {}, FakeHttp(), Saved()))
    assert caught.kind == "permanent"
    assert caught.message == "This platform doesn't hold scheduled posts."


# --- Discord -----------------------------------------------------------------------------


def test_discord_validate_refusals(tmp_path: Path) -> None:
    adapter = DiscordAdapter()
    assert adapter.validate(PublishRequest(text="hi")) == []
    assert adapter.validate(PublishRequest(text="  ")) == ["Discord needs text or a file."]
    assert adapter.validate(PublishRequest(text="x" * 2001)) == ["Discord allows 2000 characters."]
    link_over = PublishRequest(text="x" * 1990, link="https://example.com/long")
    assert adapter.validate(link_over) == ["Discord allows 2000 characters."]
    many = tuple(media(tmp_path, f"{i}.png", "image/png") for i in range(11))
    assert adapter.validate(PublishRequest(text="hi", media=many)) == ["Discord allows 10 files."]
    big = (media(tmp_path, "big.mp4", "video/mp4", size=11 * 1024 * 1024),)
    assert adapter.validate(PublishRequest(text="", media=big)) == [
        "Discord webhooks take at most 10 MB of files."
    ]


def test_discord_publish_sends_json_with_no_mentions_by_default() -> None:
    http = FakeHttp()
    http.reply(200, {"id": "99", "channel_id": "7", "guild_id": "5"})
    result = run(
        DiscordAdapter().publish(
            PublishRequest(text="Hello", link="https://example.com"),
            {"webhook_url": FAKE_WEBHOOK},
            http,
            Saved(),
        )
    )
    assert result.remote_id == "99"
    assert result.url == "https://discord.com/channels/5/7/99"
    sent = http.requests[0]
    assert parse_qs(urlsplit(sent.url).query) == {"wait": ["true"]}
    payload = json.loads(sent.body or b"")
    assert payload == {"content": "Hello\nhttps://example.com", "allowed_mentions": {"parse": []}}


def test_discord_publish_with_files_thread_and_allowed_mentions(tmp_path: Path) -> None:
    http = FakeHttp()
    http.reply(200, {"id": "99", "channel_id": "7"})
    request = PublishRequest(
        text="",
        link="https://example.com",
        media=(media(tmp_path, "a.png", "image/png"),),
        options={"thread_id": "555"},
        account_options={"allowed_mentions": ["users", "everyone", "nonsense"]},
    )
    result = run(DiscordAdapter().publish(request, {"webhook_url": FAKE_WEBHOOK}, http, Saved()))
    assert result.url == ""  # no guild id, so no message link
    sent = http.requests[0]
    assert parse_qs(urlsplit(sent.url).query) == {"wait": ["true"], "thread_id": ["555"]}
    assert sent.headers["Content-Type"].startswith("multipart/form-data")
    body = sent.body or b""
    assert b'"parse": ["users", "everyone"]' in body
    assert b'"content": "https://example.com"' in body
    assert b'filename="a.png"' in body and b"fake-bytes" in body


def test_discord_publish_ignores_a_thread_id_that_is_not_a_number() -> None:
    http = FakeHttp()
    http.reply(200, {"id": "1"})
    request = PublishRequest(text="hi", account_options={"thread_id": "../other"})
    run(DiscordAdapter().publish(request, {"webhook_url": FAKE_WEBHOOK}, http, Saved()))
    assert "thread_id" not in http.requests[0].url


def test_discord_publish_without_webhook_is_auth() -> None:
    caught = raises(DiscordAdapter().publish(PublishRequest(text="hi"), {}, FakeHttp(), Saved()))
    assert (caught.kind, caught.message) == ("auth", "This Discord account has no webhook saved.")


def test_discord_publish_404_turns_the_account_off() -> None:
    http = FakeHttp()
    http.reply(404, {"message": "Unknown Webhook"})
    caught = raises(
        DiscordAdapter().publish(
            PublishRequest(text="hi"), {"webhook_url": FAKE_WEBHOOK}, http, Saved()
        )
    )
    assert caught.kind == "auth"
    assert caught.disable_account is True
    assert caught.message == "The Discord webhook was deleted. Add a new one to post again."


def test_discord_publish_rate_limit_carries_the_wait() -> None:
    http = FakeHttp()
    http.reply(429, {"message": "You are being rate limited.", "retry_after": 3.5})
    caught = raises(
        DiscordAdapter().publish(
            PublishRequest(text="hi"), {"webhook_url": FAKE_WEBHOOK}, http, Saved()
        )
    )
    assert (caught.kind, caught.retry_after_seconds) == ("transient", 3.5)


def test_discord_verify_without_webhook_is_auth() -> None:
    caught = raises(DiscordAdapter().verify({}, FakeHttp(), Saved()))
    assert (caught.kind, caught.message) == ("auth", "No webhook saved.")


def test_discord_verify_404_says_the_webhook_was_deleted() -> None:
    http = FakeHttp()
    http.reply(404, {"message": "Unknown Webhook"})
    caught = raises(DiscordAdapter().verify({"webhook_url": FAKE_WEBHOOK}, http, Saved()))
    assert (caught.kind, caught.message, caught.status) == (
        "auth",
        "The Discord webhook was deleted.",
        404,
    )


def test_discord_verify_other_errors_are_classified() -> None:
    http = FakeHttp()
    http.reply(500, {})
    caught = raises(DiscordAdapter().verify({"webhook_url": FAKE_WEBHOOK}, http, Saved()))
    assert caught.kind == "transient"


def test_discord_verify_returns_the_webhook_name() -> None:
    http = FakeHttp()
    http.reply(200, {"name": "Announcements"})
    http.reply(200, {})
    secrets = {"webhook_url": FAKE_WEBHOOK}
    assert run(DiscordAdapter().verify(secrets, http, Saved())) == {"name": "Announcements"}
    assert run(DiscordAdapter().verify(secrets, http, Saved())) == {"name": "Discord webhook"}
    assert http.requests[0].method == "GET"


# --- Facebook ----------------------------------------------------------------------------


def test_facebook_validate_refusals(tmp_path: Path) -> None:
    adapter = FacebookAdapter()
    assert adapter.validate(PublishRequest(text="", link="https://example.com")) == []
    assert adapter.validate(PublishRequest(text=" ")) == ["Facebook needs text or a link."]
    assert adapter.validate(PublishRequest(text="x" * 63207)) == [
        "The text is too long for Facebook."
    ]
    photo = (media(tmp_path, "a.png", "image/png"),)
    assert adapter.validate(PublishRequest(text="hi", media=photo)) == [
        "Photos on Facebook aren't supported yet. Remove the media."
    ]


def test_facebook_publish_puts_the_token_in_the_body_and_schedules() -> None:
    http = FakeHttp()
    http.reply(200, {"id": "123_456"})
    request = PublishRequest(
        text="Hello",
        link="https://example.com",
        account_options=FB_OPTIONS,
        publish_at_unix=1_900_000_000,
    )
    result = run(FacebookAdapter().publish(request, dict(FB_TOKEN), http, Saved()))
    assert result.remote_id == "123_456"
    assert result.url == "https://www.facebook.com/123_456"
    sent = http.requests[0]
    assert sent.url.endswith("/1234567890/feed")
    assert "fake-page-token" not in sent.url
    form = parse_qs((sent.body or b"").decode())
    assert form == {
        "message": ["Hello"],
        "access_token": ["fake-page-token"],
        "link": ["https://example.com"],
        "published": ["false"],
        "scheduled_publish_time": ["1900000000"],
    }


def test_facebook_publish_needs_a_token_and_a_page_id() -> None:
    request = PublishRequest(text="hi", account_options=FB_OPTIONS)
    caught = raises(FacebookAdapter().publish(request, {}, FakeHttp(), Saved()))
    assert (caught.kind, caught.message) == (
        "auth",
        "This Facebook Page has no access token saved.",
    )
    bad_page = PublishRequest(text="hi", account_options={"page_id": "me/../x"})
    caught = raises(FacebookAdapter().publish(bad_page, dict(FB_TOKEN), FakeHttp(), Saved()))
    assert (caught.kind, caught.message) == ("auth", "Add this Facebook Page's ID in Settings.")


@pytest.mark.parametrize(
    ("status", "code", "kind"),
    [
        (400, 190, "auth"),
        (400, 102, "auth"),
        (400, 4, "transient"),
        (403, 32, "transient"),
        (400, 100, "permanent"),
        (500, None, "transient"),
    ],
)
def test_facebook_error_codes(status: int, code: int | None, kind: str) -> None:
    http = FakeHttp()
    http.reply(status, {"error": {"message": "Graph said no", "code": code}})
    request = PublishRequest(text="hi", account_options=FB_OPTIONS)
    caught = raises(FacebookAdapter().publish(request, dict(FB_TOKEN), http, Saved()))
    assert caught.kind == kind
    assert caught.message == "Graph said no"


def test_facebook_cancel_native_deletes_and_treats_404_as_already_gone() -> None:
    http = FakeHttp()
    http.reply(200, {"success": True})
    http.reply(404, {"error": {"message": "Unsupported delete request", "code": 100}})
    adapter = FacebookAdapter()
    run(adapter.cancel_native("123_456", dict(FB_TOKEN), http, Saved()))
    run(adapter.cancel_native("123_456", dict(FB_TOKEN), http, Saved()))
    assert [r.method for r in http.requests] == ["DELETE", "DELETE"]
    assert http.requests[0].url.endswith("/123_456")
    assert parse_qs((http.requests[0].body or b"").decode()) == {
        "access_token": ["fake-page-token"]
    }


def test_facebook_cancel_native_raises_on_server_error() -> None:
    http = FakeHttp()
    http.reply(500, {"error": {"message": "Try again later"}})
    caught = raises(FacebookAdapter().cancel_native("1", dict(FB_TOKEN), http, Saved()))
    assert (caught.kind, caught.status) == ("transient", 500)


def test_facebook_cancel_native_without_token_is_auth() -> None:
    caught = raises(FacebookAdapter().cancel_native("1", {}, FakeHttp(), Saved()))
    assert caught.kind == "auth"


def test_facebook_verify_returns_the_page_name() -> None:
    http = FakeHttp()
    http.reply(200, {"id": "1234567890", "name": "Raticode"})
    info = run(FacebookAdapter().verify(dict(FB_TOKEN), http, Saved()))
    assert info == {"name": "Raticode", "page_id": "1234567890"}
    assert http.requests[0].headers["Authorization"] == "Bearer fake-page-token"
    assert "fake-page-token" not in http.requests[0].url


def test_facebook_verify_errors() -> None:
    caught = raises(FacebookAdapter().verify({}, FakeHttp(), Saved()))
    assert (caught.kind, caught.message) == ("auth", "No Page access token saved.")
    http = FakeHttp()
    http.reply(400, {"error": {"message": "Session expired", "code": 190}})
    caught = raises(FacebookAdapter().verify(dict(FB_TOKEN), http, Saved()))
    assert (caught.kind, caught.message) == ("auth", "Session expired")


# --- LinkedIn ----------------------------------------------------------------------------


def test_linkedin_validate_refusals(tmp_path: Path) -> None:
    adapter = LinkedInAdapter()
    assert adapter.validate(PublishRequest(text="hi")) == []
    assert adapter.validate(PublishRequest(text=" ")) == ["LinkedIn needs text."]
    assert adapter.validate(PublishRequest(text="x" * 3001)) == [
        "LinkedIn allows about 3000 characters."
    ]
    photo = (media(tmp_path, "a.png", "image/png"),)
    assert adapter.validate(PublishRequest(text="hi", media=photo)) == [
        "Images on LinkedIn aren't supported yet. Remove the media."
    ]
    assert adapter.validate(PublishRequest(text="hi", options={"visibility": "LOGGED_IN"})) == [
        "LinkedIn visibility is PUBLIC or CONNECTIONS."
    ]


def test_linkedin_publish_escapes_text_and_reads_the_post_id_header() -> None:
    http = FakeHttp()
    http.reply(201, b"", {"X-RestLi-Id": "urn:li:share:42"})
    request = PublishRequest(
        text="Hi @someone #tag",
        link="https://example.com",
        options={"article_title": "Example", "visibility": "CONNECTIONS"},
        account_options={**LI_OPTIONS, "api_version": "202701"},
    )
    result = run(LinkedInAdapter().publish(request, dict(LI_TOKEN), http, Saved()))
    assert result.remote_id == "urn:li:share:42"
    assert result.url == "https://www.linkedin.com/feed/update/urn:li:share:42/"
    sent = http.requests[0]
    assert sent.headers["Linkedin-Version"] == "202701"
    body = json.loads(sent.body or b"")
    assert body["commentary"] == r"Hi \@someone \#tag"
    assert body["visibility"] == "CONNECTIONS"
    assert body["content"] == {"article": {"source": "https://example.com", "title": "Example"}}


def test_linkedin_publish_without_an_id_has_no_url() -> None:
    http = FakeHttp()
    http.reply(201, b"")
    request = PublishRequest(text="hi", account_options=LI_OPTIONS)
    result = run(LinkedInAdapter().publish(request, dict(LI_TOKEN), http, Saved()))
    assert (result.remote_id, result.url) == ("", "")
    assert "content" not in json.loads(http.requests[0].body or b"")


def test_linkedin_publish_needs_a_token_and_an_author() -> None:
    request = PublishRequest(text="hi", account_options=LI_OPTIONS)
    caught = raises(LinkedInAdapter().publish(request, {}, FakeHttp(), Saved()))
    assert caught.message == "This LinkedIn account has no access token saved."
    no_author = PublishRequest(text="hi", account_options={"author_urn": "urn:li:org:1"})
    caught = raises(LinkedInAdapter().publish(no_author, dict(LI_TOKEN), FakeHttp(), Saved()))
    assert caught.kind == "auth"
    assert caught.message == "Check this LinkedIn account in Settings so Raticode knows who posts."


@pytest.mark.parametrize(
    ("status", "kind", "message"),
    [
        (409, "transient", "Conflict"),
        (
            429,
            "cap",
            "LinkedIn's daily limit for this account is used up. It resets at midnight UTC.",
        ),
        (403, "permanent", "Conflict"),
        (401, "auth", "Conflict"),
        (503, "transient", "Conflict"),
    ],
)
def test_linkedin_publish_errors(status: int, kind: str, message: str) -> None:
    http = FakeHttp()
    http.reply(status, {"message": "Conflict"})
    request = PublishRequest(text="hi", account_options=LI_OPTIONS)
    caught = raises(LinkedInAdapter().publish(request, dict(LI_TOKEN), http, Saved()))
    assert (caught.kind, caught.message, caught.status) == (kind, message, status)


def test_linkedin_verify_returns_name_and_author() -> None:
    http = FakeHttp()
    http.reply(200, {"sub": "abc123", "name": "Ada Example"})
    info = run(LinkedInAdapter().verify(dict(LI_TOKEN), http, Saved()))
    assert info == {"name": "Ada Example", "author_urn": "urn:li:person:abc123"}
    assert http.requests[0].url.endswith("/v2/userinfo")


def test_linkedin_verify_errors() -> None:
    caught = raises(LinkedInAdapter().verify({}, FakeHttp(), Saved()))
    assert (caught.kind, caught.message) == ("auth", "No access token saved.")
    http = FakeHttp()
    http.reply(200, {"name": "No sub"})
    caught = raises(LinkedInAdapter().verify(dict(LI_TOKEN), http, Saved()))
    assert (caught.kind, caught.message) == (
        "permanent",
        "LinkedIn didn't say which member this is.",
    )
    http.reply(401, {"message": "Expired"})
    caught = raises(LinkedInAdapter().verify(dict(LI_TOKEN), http, Saved()))
    assert caught.kind == "auth"


# --- YouTube -----------------------------------------------------------------------------


def _video_request(tmp_path: Path, **changes: Any) -> PublishRequest:
    fields: dict[str, Any] = {
        "text": "A description",
        "options": {"title": "My video"},
        "media": (media(tmp_path, "clip.mp4", "video/mp4"),),
    }
    fields.update(changes)
    return PublishRequest(**fields)


def test_youtube_validate_refusals(tmp_path: Path) -> None:
    adapter = YouTubeAdapter()
    video = (media(tmp_path, "clip.mp4", "video/mp4"),)
    assert adapter.validate(_video_request(tmp_path)) == []
    assert adapter.validate(_video_request(tmp_path, options={})) == ["YouTube needs a title."]
    title_rule = "YouTube titles are up to 100 characters with no < or >."
    assert adapter.validate(_video_request(tmp_path, options={"title": "x" * 101})) == [title_rule]
    assert adapter.validate(_video_request(tmp_path, options={"title": "a <b>"})) == [title_rule]
    description_rule = "YouTube descriptions are up to 5,000 bytes with no < or >."
    assert adapter.validate(_video_request(tmp_path, text="é" * 2501)) == [description_rule]
    assert adapter.validate(_video_request(tmp_path, text="a > b")) == [description_rule]
    one_video = "YouTube needs exactly one video file."
    assert adapter.validate(_video_request(tmp_path, media=())) == [one_video]
    assert adapter.validate(_video_request(tmp_path, media=video * 2)) == [one_video]
    image = (media(tmp_path, "a.png", "image/png"),)
    assert adapter.validate(_video_request(tmp_path, media=image)) == [one_video]
    big = (media(tmp_path, "big.mp4", "video/mp4", size=257 * 1024 * 1024),)
    assert adapter.validate(_video_request(tmp_path, media=big)) == [
        "Raticode uploads videos up to 256 MB."
    ]


def test_youtube_publish_refreshes_the_token_then_uploads(tmp_path: Path) -> None:
    http = FakeHttp()
    http.reply(200, {"access_token": "fresh-token"})
    http.reply(200, {}, {"Location": "https://www.googleapis.com/upload/session/1"})
    http.reply(200, {"id": "vid123"})
    saved = Saved()
    secrets = {**YT_LOGIN, "client_secret": "fake-secret"}
    request = _video_request(tmp_path, link="https://example.com", publish_at_unix=1_900_000_000)
    result = run(YouTubeAdapter().publish(request, secrets, http, saved))
    assert (result.remote_id, result.url) == ("vid123", "https://www.youtube.com/watch?v=vid123")
    assert saved.calls[-1]["access_token"] == "fresh-token"
    token_form = parse_qs((http.requests[0].body or b"").decode())
    assert token_form["client_secret"] == ["fake-secret"]
    metadata = json.loads(http.requests[1].body or b"")
    assert metadata["snippet"] == {
        "title": "My video",
        "description": "A description\n\nhttps://example.com",
    }
    assert metadata["status"]["privacyStatus"] == "private"
    assert metadata["status"]["publishAt"].endswith("Z")
    upload = http.requests[2]
    assert (upload.method, upload.body) == ("PUT", b"fake-bytes")
    assert upload.headers["Authorization"] == "Bearer fresh-token"


def test_youtube_uses_a_saved_access_token_without_refresh(tmp_path: Path) -> None:
    http = FakeHttp()
    http.reply(200, {}, {"Location": "https://www.googleapis.com/upload/session/1"})
    http.reply(200, {"id": "v"})
    request = _video_request(tmp_path, account_options={"privacy": "unlisted"})
    run(YouTubeAdapter().publish(request, {"access_token": "kept"}, http, Saved()))
    assert http.requests[0].headers["Authorization"] == "Bearer kept"
    assert json.loads(http.requests[0].body or b"")["status"]["privacyStatus"] == "unlisted"


def test_youtube_token_failures(tmp_path: Path) -> None:
    adapter = YouTubeAdapter()
    caught = raises(adapter.verify({}, FakeHttp(), Saved()))
    assert (caught.kind, caught.message) == ("auth", "This YouTube account has no sign-in saved.")
    http = FakeHttp()
    http.reply(400, {"error": "invalid_grant"})
    caught = raises(adapter.verify(dict(YT_LOGIN), http, Saved()))
    assert (caught.kind, caught.message) == (
        "auth",
        "Google signed this account out. Reconnect it.",
    )
    http.reply(503, {"error": {"message": "Backend error"}})
    caught = raises(adapter.verify(dict(YT_LOGIN), http, Saved()))
    assert (caught.kind, caught.message) == ("transient", "Backend error")


@pytest.mark.parametrize("reason", ["quotaExceeded", "uploadLimitExceeded", "rateLimitExceeded"])
def test_youtube_403_limit_reasons_are_cap(tmp_path: Path, reason: str) -> None:
    http = FakeHttp()
    http.reply(403, {"error": {"message": "Limit", "errors": [{"reason": reason}]}})
    caught = raises(
        YouTubeAdapter().publish(_video_request(tmp_path), {"access_token": "t"}, http, Saved())
    )
    assert (caught.kind, caught.status) == ("cap", 403)


def test_youtube_other_403_stays_permanent(tmp_path: Path) -> None:
    http = FakeHttp()
    http.reply(403, {"error": {"message": "Forbidden", "errors": [{"reason": "forbidden"}, "x"]}})
    caught = raises(
        YouTubeAdapter().publish(_video_request(tmp_path), {"access_token": "t"}, http, Saved())
    )
    assert caught.kind == "permanent"


def test_youtube_refuses_an_upload_session_off_google(tmp_path: Path) -> None:
    http = FakeHttp()
    http.reply(200, {}, {"Location": "https://evil.example/upload"})
    caught = raises(
        YouTubeAdapter().publish(_video_request(tmp_path), {"access_token": "t"}, http, Saved())
    )
    assert (caught.kind, caught.message) == ("transient", "YouTube didn't start the upload.")
    assert len(http.requests) == 1  # the video never went to the other host


def test_youtube_upload_errors(tmp_path: Path) -> None:
    session = {"Location": "https://www.googleapis.com/upload/session/1"}
    http = FakeHttp()
    http.reply(200, {}, session)
    http.reply(502, {})
    caught = raises(
        YouTubeAdapter().publish(_video_request(tmp_path), {"access_token": "t"}, http, Saved())
    )
    assert (caught.kind, caught.status) == ("outcome_unknown", 502)
    http.reply(200, {}, session)
    http.reply(400, {"error": {"message": "Bad video"}})
    caught = raises(
        YouTubeAdapter().publish(_video_request(tmp_path), {"access_token": "t"}, http, Saved())
    )
    assert (caught.kind, caught.message) == ("permanent", "Bad video")
    http.reply(200, {}, session)
    http.fail(TimeoutError())
    caught = raises(
        YouTubeAdapter().publish(_video_request(tmp_path), {"access_token": "t"}, http, Saved())
    )
    assert caught.kind == "outcome_unknown"


def test_youtube_cancel_native_treats_404_as_already_deleted() -> None:
    http = FakeHttp()
    http.reply(204, b"")
    http.reply(404, {"error": {"message": "Video not found"}})
    adapter = YouTubeAdapter()
    run(adapter.cancel_native("vid 1", {"access_token": "t"}, http, Saved()))
    run(adapter.cancel_native("vid 1", {"access_token": "t"}, http, Saved()))
    assert http.requests[0].method == "DELETE"
    assert parse_qs(urlsplit(http.requests[0].url).query) == {"id": ["vid 1"]}


def test_youtube_cancel_native_raises_on_server_error() -> None:
    http = FakeHttp()
    http.reply(500, {"error": {"message": "Backend error"}})
    caught = raises(YouTubeAdapter().cancel_native("v", {"access_token": "t"}, http, Saved()))
    assert (caught.kind, caught.status) == ("transient", 500)


def test_youtube_verify_returns_the_channel_name() -> None:
    http = FakeHttp()
    http.reply(200, {"items": [{"snippet": {"title": "Raticode"}}]})
    http.reply(200, {"items": []})
    adapter = YouTubeAdapter()
    assert run(adapter.verify({"access_token": "t"}, http, Saved())) == {"name": "Raticode"}
    assert run(adapter.verify({"access_token": "t"}, http, Saved())) == {"name": "YouTube channel"}


def test_youtube_verify_error_is_classified() -> None:
    http = FakeHttp()
    http.reply(401, {"error": {"message": "Invalid Credentials"}})
    caught = raises(YouTubeAdapter().verify({"access_token": "t"}, http, Saved()))
    assert (caught.kind, caught.message) == ("auth", "Invalid Credentials")
