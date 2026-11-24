"""Social posts: approval rules, the publisher and every adapter, with fake platforms only.

No test here reaches a real platform: every adapter gets ``FakeHttp``, and logins live in
an in-memory secret store with made-up values.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from gofer.core.http import HttpRequest, HttpResponse
from gofer.social import api as social_api
from gofer.social.accounts import KEYCHAIN_SERVICE, SocialAccountError
from gofer.social.mcp import SocialActions, social_tools
from gofer.social.models import derive_post_state
from gofer.social.policy import clean_text, content_hash, flags_for
from gofer.social.providers import x as x_provider
from gofer.social.providers.base import PublishError, PublishRequest
from gofer.social.providers.linkedin import API_VERSION_SUNSET, escape_little
from gofer.social.publisher import Publisher
from gofer.social.service import SocialError, SocialService
from gofer.ui.headless import agent_request_refusal

FAKE_WEBHOOK = "https://discord.com/api/webhooks/123456/fake-webhook-token"
START = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


class FakeSecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, account: str) -> str | None:
        return self.values.get(account)

    def put(self, account: str, value: str) -> None:
        self.values[account] = value

    def delete(self, account: str) -> None:
        self.values.pop(account, None)


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


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(tmp_path: Path, clock: Clock) -> SocialService:
    return SocialService(
        tmp_path / "data", secrets=FakeSecrets(), clock=clock, media_roots=lambda: [tmp_path]
    )


@pytest.fixture
def http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture
def publisher(service: SocialService, http: FakeHttp) -> Publisher:
    return Publisher(service, http=http)


def discord(service: SocialService, **extra: Any) -> dict[str, Any]:
    return service.accounts.connect(
        {
            "platform": "discord",
            "name": "Test channel",
            "secrets": {"webhook_url": FAKE_WEBHOOK},
            **extra,
        }
    )


def linkedin(service: SocialService) -> dict[str, Any]:
    account = service.accounts.connect(
        {"platform": "linkedin", "name": "Me", "secrets": {"access_token": "fake-li-token"}}
    )
    return service.accounts.update(account["id"], {"options": {"author_urn": "urn:li:person:abc"}})


def at(minutes: float) -> str:
    return (START + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def propose(
    service: SocialService, account: dict[str, Any], *, actor: str = "rem", **fields: Any
) -> dict[str, Any]:
    body = {
        "title": "Release",
        "scheduled_at": at(60),
        "variants": [{"account_id": account["id"], "text": "Raticode 0.6 is out @everyone"}],
        **fields,
    }
    return service.propose(actor, body)


def approve(service: SocialService, post: dict[str, Any]) -> dict[str, Any]:
    return service.approve("user", post["id"], post["content_hash"])


# Approval rules -------------------------------------------------------------------------


def test_only_the_user_approves_and_only_the_version_they_saw(service: SocialService) -> None:
    post = propose(service, discord(service))
    assert post["state"] == "proposed"
    assert post["filled_in"] is True
    for actor in ("rem", "cli", "task:1", "workflow:release@3"):
        with pytest.raises(SocialError) as refused:
            service.approve(actor, post["id"], post["content_hash"])
        assert refused.value.code == "forbidden"
    with pytest.raises(SocialError) as stale:
        service.approve("user", post["id"], "0" * 64)
    assert stale.value.code == "conflict"
    approved = approve(service, post)
    assert approved["state"] == "scheduled"
    assert approved["approval"]["content_hash"] == post["content_hash"]


def test_any_edit_after_approval_sends_the_post_back_to_review(service: SocialService) -> None:
    post = approve(service, propose(service, discord(service)))
    edited = service.edit(
        "user", post["id"], {"variants": [{**post["variants"][0], "text": "Changed"}]}
    )
    assert edited["state"] == "proposed"
    assert edited["approval"] is None
    assert edited["previous"]["variants"][0]["text"].startswith("Raticode 0.6")
    moved = approve(service, edited)
    retimed = service.edit("user", moved["id"], {"scheduled_at": at(90)})
    assert retimed["approval"] is None and retimed["state"] == "proposed"


def test_proposers_revise_only_their_own_undecided_posts(service: SocialService) -> None:
    account = discord(service)
    mine = propose(
        service, account, actor="user", variants=[{"account_id": account["id"], "text": "Mine"}]
    )
    with pytest.raises(SocialError):
        service.edit("rem", mine["id"], {"title": "Hijacked"})
    rems = propose(service, account)
    assert service.edit("rem", rems["id"], {"title": "Better"})["title"] == "Better"
    approve(service, service.get(rems["id"]))
    with pytest.raises(SocialError):
        service.edit("rem", rems["id"], {"title": "After approval"})
    for decision in ("decline", "request_changes", "cancel"):
        with pytest.raises(SocialError):
            service.decide("rem", rems["id"], decision)


def test_proposer_limits_duplicates_and_quiet_accounts(service: SocialService) -> None:
    account = discord(service)
    propose(service, account)
    with pytest.raises(SocialError) as duplicate:
        propose(service, account)
    assert duplicate.value.code == "duplicate"
    for index in range(9):
        propose(service, account, variants=[{"account_id": account["id"], "text": f"Post {index}"}])
    with pytest.raises(SocialError) as limit:
        propose(service, account, variants=[{"account_id": account["id"], "text": "One more"}])
    assert limit.value.code == "limit"
    quiet = discord(service, quiet=True)
    with pytest.raises(SocialError) as refused:
        propose(
            service,
            quiet,
            actor="workflow:x@1",
            variants=[{"account_id": quiet["id"], "text": "Hi"}],
        )
    assert refused.value.code == "forbidden"
    assert propose(
        service, quiet, actor="user", variants=[{"account_id": quiet["id"], "text": "Hi"}]
    )


def test_unknown_platforms_are_refused(service: SocialService) -> None:
    for platform in ("bluesky", "mastodon"):
        with pytest.raises(SocialError):
            service.propose("rem", {"variants": [{"platform": platform, "text": "Hi"}]})


def test_rem_tools_can_only_propose_and_read(service: SocialService) -> None:
    names = {tool["name"] for tool in social_tools()}
    assert names == {"social_rules", "propose_post", "revise_post", "list_posts", "read_post"}
    account = discord(service)
    tools = SocialActions(service)
    rules = tools.call("social_rules", {})
    assert [item["account_id"] for item in rules["accounts"]] == [account["id"]]
    assert "fake-webhook-token" not in json.dumps(rules)
    created = tools.call(
        "propose_post",
        {"title": "Hi", "variants": [{"account_id": account["id"], "text": "Hello"}]},
    )
    assert created["state"] == "proposed" and created["approved"] is False
    assert service.get(created["id"])["created_by"] == "rem"


def test_api_refuses_agents_everything_but_proposing(service: SocialService) -> None:
    for method, path in (
        ("POST", "/api/social/posts/post-1/approve"),
        ("POST", "/api/social/posts/post-1/variants/v-1/publish"),
        ("POST", "/api/social/posts/post-1/variants/v-1/mark-posted"),
        ("POST", "/api/social/posts/post-1/cancel"),
        ("POST", "/api/social/accounts"),
        ("PATCH", "/api/social/accounts/a"),
        ("DELETE", "/api/social/accounts/a"),
        ("POST", "/api/social/pause"),
        ("PUT", "/api/social/settings"),
    ):
        assert agent_request_refusal(method, path) is not None, path
    assert agent_request_refusal("POST", "/api/social/posts") is None
    assert agent_request_refusal("POST", "/api/social/posts/post-1/edit") is None
    assert agent_request_refusal("GET", "/api/social") is None
    post = propose(service, discord(service))
    with pytest.raises(social_api.SocialApiError) as refused:
        social_api.handle(
            service,
            "POST",
            ["posts", post["id"], "approve"],
            "rem",
            {"content_hash": post["content_hash"]},
        )
    assert refused.value.status == 403
    with pytest.raises(social_api.SocialApiError):
        social_api.handle(service, "GET", ["accounts"], "rem", {})


def test_logins_stay_in_their_own_keychain_entry(service: SocialService, tmp_path: Path) -> None:
    from gofer.rattish import workflow_secrets

    assert KEYCHAIN_SERVICE == "Raticode social accounts"
    assert workflow_secrets.KEYCHAIN_SERVICE != KEYCHAIN_SERVICE
    account = discord(service)
    index = (service.root / "accounts.json").read_text()
    snapshot = json.dumps(service.snapshot())
    assert "fake-webhook-token" not in index and "fake-webhook-token" not in snapshot
    assert service.accounts.read_secrets(account["id"]) == {"webhook_url": FAKE_WEBHOOK}
    with pytest.raises(SocialAccountError):
        service.accounts.connect(
            {"platform": "discord", "secrets": {"webhook_url": "https://evil.example/hook"}}
        )
    service.accounts.disconnect(account["id"])
    assert service.accounts.list() == []


def test_audit_log_is_hash_linked(service: SocialService) -> None:
    approve(service, propose(service, discord(service)))
    assert service.verify_log()
    with service.connect() as db:
        db.execute("UPDATE events SET actor = 'user' WHERE kind = 'proposed'")
    assert not service.verify_log()


def test_flags_and_invisible_characters() -> None:
    text, stripped = clean_text("Hello‮evil​ world")
    assert stripped and text == "Helloevil world"
    flags = flags_for(
        ["Try https://rat1code.dev and https://xn--raticde-xya.dev #brandnew @someone"],
        [],
        allowlist=["raticode.dev", "github.com"],
    )
    kinds = [flag["kind"] for flag in flags]
    assert "lookalike" in kinds and "link" in kinds and "mention" in kinds and "hashtag" in kinds
    assert flags_for(["See https://raticode.dev/docs"], [], allowlist=["raticode.dev"]) == []


def test_content_hash_covers_text_time_account_and_media() -> None:
    base: dict[str, Any] = {
        "scheduled_at": "2026-11-20T16:00:00Z",
        "late_window_minutes": 120,
        "variants": [{"account_id": "a", "platform": "x", "text": "Hi", "media": []}],
    }
    first = content_hash(base)
    assert content_hash({**base, "scheduled_at": "2026-11-20T17:00:00Z"}) != first
    changed = {**base, "variants": [{**base["variants"][0], "media": [{"sha256": "f" * 64}]}]}
    assert content_hash(changed) != first


def test_post_state_comes_from_its_variants() -> None:
    assert derive_post_state("proposed", ["pending"]) == "proposed"
    assert derive_post_state("approved", ["published", "due"]) == "due"
    assert derive_post_state("approved", ["published", "failed"]) == "partly_published"
    assert derive_post_state("approved", ["published", "posted_by_you"]) == "published"
    assert derive_post_state("approved", ["missed"]) == "missed"


# The publisher ---------------------------------------------------------------------------


def test_discord_post_goes_out_on_time_with_no_mentions(
    service: SocialService, publisher: Publisher, http: FakeHttp, clock: Clock
) -> None:
    post = approve(service, propose(service, discord(service)))
    publisher.tick(START + timedelta(minutes=30))
    assert http.requests == []
    http.reply(200, {"id": "999", "channel_id": "5", "guild_id": "7"})
    publisher.tick(START + timedelta(minutes=60))
    request = http.requests[0]
    assert parse_qs(urlsplit(request.url).query)["wait"] == ["true"]
    body = json.loads(request.body or b"{}")
    assert body["allowed_mentions"] == {"parse": []}
    assert body["content"] == "Raticode 0.6 is out @everyone"
    done = service.get(post["id"], events=True)
    assert done["state"] == "published"
    assert done["variants"][0]["url"] == "https://discord.com/channels/7/5/999"
    assert [a["trigger"] for a in done["attempts"]] == ["schedule"]


def test_late_posts_are_missed_and_unapproved_ones_expire(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    account = discord(service)
    late = approve(service, propose(service, account))
    waiting = propose(
        service, account, variants=[{"account_id": account["id"], "text": "Never seen"}]
    )
    publisher.tick(START + timedelta(minutes=60 + 121))
    assert http.requests == []
    assert service.get(late["id"])["state"] == "missed"
    assert service.get(waiting["id"])["state"] == "expired"


def test_outcome_unknown_is_never_resent(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    post = approve(service, propose(service, discord(service)))
    http.fail(TimeoutError())
    publisher.tick(START + timedelta(minutes=60))
    publisher.tick(START + timedelta(minutes=70))
    publisher.tick(START + timedelta(minutes=90))
    assert len(http.requests) == 1
    variant = service.get(post["id"])["variants"][0]
    assert variant["state"] == "outcome_unknown"
    # Only the user's Send again sends it again.
    current = service.get(post["id"])
    service.publish_variant("user", post["id"], variant["id"], current["content_hash"])
    http.reply(200, {"id": "1"})
    publisher.tick(START + timedelta(minutes=91))
    assert len(http.requests) == 2
    assert service.get(post["id"], events=True)["attempts"][-1]["trigger"] == "user_tap"


def test_a_send_cut_off_by_a_crash_becomes_outcome_unknown(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    post = approve(service, propose(service, discord(service)))

    def mark(p: dict[str, Any]) -> None:
        p["variants"][0]["state"] = "publishing"

    service.update_post(post["id"], mark, "publisher", "test", {})
    publisher.tick(START + timedelta(minutes=61))
    assert http.requests == []
    assert service.get(post["id"])["variants"][0]["state"] == "outcome_unknown"


def test_transient_errors_retry_inside_the_late_window(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    post = approve(service, propose(service, discord(service)))
    http.reply(503, {"message": "busy"})
    publisher.tick(START + timedelta(minutes=60))
    variant = service.get(post["id"])["variants"][0]
    assert variant["state"] == "scheduled" and variant["next_retry_at"]
    publisher.tick(START + timedelta(minutes=60, seconds=30))
    assert len(http.requests) == 1
    http.reply(200, {"id": "2"})
    publisher.tick(START + timedelta(minutes=62))
    assert service.get(post["id"])["state"] == "published"


def test_auth_errors_need_a_reconnect_and_deleted_webhooks_turn_off(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    account = discord(service)
    post = approve(service, propose(service, account))
    http.reply(404, {"message": "Unknown Webhook"})
    publisher.tick(START + timedelta(minutes=60))
    assert service.get(post["id"])["variants"][0]["state"] == "needs_reconnect"
    assert service.accounts.list()[0]["status"] == "disabled"
    assert "fake-webhook-token" not in json.dumps(service.get(post["id"], events=True))


def test_pause_skips_and_never_catches_up(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    post = approve(service, propose(service, discord(service)))
    service.set_paused("user", True)
    with pytest.raises(SocialError):
        service.set_paused("rem", False)
    publisher.tick(START + timedelta(minutes=60))
    service.set_paused("user", False)
    publisher.tick(START + timedelta(minutes=61))
    assert http.requests == []
    assert service.get(post["id"])["state"] == "missed"


def test_daily_cap_holds_the_post(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    account = discord(service, daily_cap=0)
    post = approve(service, propose(service, account))
    publisher.tick(START + timedelta(minutes=60))
    assert http.requests == []
    assert service.get(post["id"])["variants"][0]["state"] == "held"


def test_linkedin_waits_for_the_users_publish_tap(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    account = linkedin(service)
    post = approve(
        service,
        propose(
            service,
            account,
            variants=[
                {
                    "account_id": account["id"],
                    "text": "Shipped (finally) @[Bob](urn:li:person:x) #rats",
                    "link": "https://raticode.dev/blog",
                    "options": {"article_title": "Release notes"},
                }
            ],
        ),
    )
    assert post["variants"][0]["delivery"] == "tap_to_publish"
    publisher.tick(START + timedelta(minutes=60))
    assert http.requests == []
    current = service.get(post["id"])
    assert current["state"] == "due"
    with pytest.raises(SocialError):
        service.publish_variant(
            "rem", post["id"], current["variants"][0]["id"], current["content_hash"]
        )
    service.publish_variant(
        "user", post["id"], current["variants"][0]["id"], current["content_hash"]
    )
    http.reply(201, {}, {"x-restli-id": "urn:li:share:42"})
    publisher.tick(START + timedelta(minutes=61))
    request = http.requests[0]
    assert request.url == "https://api.linkedin.com/rest/posts"
    assert request.headers["Linkedin-Version"].isdigit()
    sent = json.loads(request.body or b"{}")
    assert sent["author"] == "urn:li:person:abc"
    assert sent["commentary"] == r"Shipped \(finally\) \@\[Bob\]\(urn:li:person:x\) \#rats"
    assert sent["content"]["article"]["title"] == "Release notes"
    done = service.get(post["id"])
    assert done["state"] == "published"
    assert done["variants"][0]["url"] == "https://www.linkedin.com/feed/update/urn:li:share:42/"


def test_linkedin_sends_only_the_link_card_text_the_review_card_shows(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    account = linkedin(service)
    hidden = "Hidden text the review card never showed"
    post = propose(
        service,
        account,
        variants=[
            {
                "account_id": account["id"],
                "text": "Release notes are up",
                "link": "https://raticode.dev/blog",
                "options": {"article_title": "Release notes", "article_description": hidden},
            }
        ],
    )
    # A proposer can't set a field no review screen shows.
    assert post["variants"][0]["options"] == {"article_title": "Release notes"}

    # A post saved before this change may still carry one; it's never sent.
    def plant(p: dict[str, Any]) -> None:
        p["variants"][0]["options"]["article_description"] = hidden

    service.update_post(post["id"], plant, "test", "planted", {})
    current = approve(service, service.get(post["id"]))
    variant_id = current["variants"][0]["id"]
    publisher.tick(START + timedelta(minutes=60))
    service.publish_variant("user", post["id"], variant_id, current["content_hash"])
    http.reply(201, {}, {"x-restli-id": "urn:li:share:43"})
    publisher.tick(START + timedelta(minutes=61))
    sent = json.loads(http.requests[0].body or b"{}")
    assert sent["content"]["article"] == {
        "source": "https://raticode.dev/blog",
        "title": "Release notes",
    }
    assert hidden not in (http.requests[0].body or b"").decode()


@pytest.mark.parametrize("change", ["edit", "cancel"])
def test_an_edit_or_cancel_during_a_send_stops_it(
    service: SocialService, publisher: Publisher, http: FakeHttp, change: str
) -> None:
    account = discord(service)
    post = approve(service, propose(service, account))
    read_secrets = service.accounts.read_secrets

    def change_mid_send(account_id: str) -> dict[str, str]:
        # The publisher has read the approved post and is about to send it.
        current = service.get(post["id"])
        if change == "edit":
            service.edit(
                "user", post["id"], {"variants": [{**current["variants"][0], "text": "Changed"}]}
            )
        else:
            service.decide("user", post["id"], "cancel")
        return read_secrets(account_id)

    service.accounts.read_secrets = change_mid_send  # type: ignore[method-assign]
    publisher.tick(START + timedelta(minutes=60))
    assert http.requests == []
    after = service.get(post["id"])
    assert after["variants"][0]["state"] == ("pending" if change == "edit" else "cancelled")
    assert after["variants"][0].get("attempts", 0) == 0


def test_linkedin_due_post_with_no_tap_is_missed(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    account = linkedin(service)
    post = approve(service, propose(service, account))
    publisher.tick(START + timedelta(minutes=60))
    publisher.tick(START + timedelta(minutes=60 + 121))
    assert http.requests == []
    assert service.get(post["id"])["state"] == "missed"


def test_linkedin_timeout_is_outcome_unknown_and_429_holds(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    account = service.accounts.update(
        linkedin(service)["id"], {"options": {"send_without_tap": True}}
    )
    first = approve(service, propose(service, account))
    assert first["variants"][0]["delivery"] == "api"
    http.fail(TimeoutError())
    publisher.tick(START + timedelta(minutes=60))
    assert service.get(first["id"])["variants"][0]["state"] == "outcome_unknown"
    second = approve(
        service,
        propose(
            service,
            account,
            scheduled_at=at(70),
            variants=[{"account_id": account["id"], "text": "Two"}],
        ),
    )
    http.reply(429, {"message": "Too many"})
    publisher.tick(START + timedelta(minutes=70))
    assert service.get(second["id"])["variants"][0]["state"] == "held"


def test_linkedin_pinned_version_is_not_near_its_sunset() -> None:
    # Bump API_VERSION and API_VERSION_SUNSET in providers/linkedin.py when this fails.
    assert date.today() < API_VERSION_SUNSET - timedelta(days=60)


def test_little_format_escaping() -> None:
    assert escape_little("a_b*c~d|e") == r"a\_b\*c\~d\|e"


def test_facebook_hands_far_posts_to_facebook_and_cancels_them(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    account = service.accounts.connect(
        {
            "platform": "facebook",
            "secrets": {"page_access_token": "fake-page-token"},
            "options": {"page_id": "12345"},
        }
    )
    post = approve(
        service,
        propose(
            service,
            account,
            scheduled_at=at(2 * 24 * 60),
            variants=[{"account_id": account["id"], "text": "Hello Page"}],
        ),
    )
    http.reply(200, {"id": "12345_678"})
    publisher.tick(START)
    request = http.requests[0]
    assert "fake-page-token" not in request.url
    form = parse_qs((request.body or b"").decode())
    assert form["published"] == ["false"]
    assert int(form["scheduled_publish_time"][0]) == int((START + timedelta(days=2)).timestamp())
    assert service.get(post["id"])["variants"][0]["state"] == "held_by_platform"
    service.decide("user", post["id"], "cancel")
    http.reply(200, {"success": True})
    publisher.tick(START + timedelta(minutes=1))
    assert http.requests[1].method == "DELETE"
    assert http.requests[1].url.endswith("/12345_678")


def test_facebook_token_error_needs_reconnect(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    account = service.accounts.connect(
        {"platform": "facebook", "secrets": {"page_access_token": "t"}, "options": {"page_id": "1"}}
    )
    post = approve(service, propose(service, account, scheduled_at=at(5)))
    http.reply(400, {"error": {"message": "Session expired", "code": 190}})
    publisher.tick(START + timedelta(minutes=5))
    assert service.get(post["id"])["variants"][0]["state"] == "needs_reconnect"
    assert service.accounts.list()[0]["status"] == "needs_reconnect"


def test_x_refreshes_its_token_once_and_saves_it(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    account = service.accounts.connect(
        {
            "platform": "x",
            "secrets": {"access_token": "old", "refresh_token": "r1", "client_id": "cid"},
        }
    )
    post = approve(
        service,
        propose(service, account, variants=[{"account_id": account["id"], "text": "Hi X"}]),
    )
    assert service.get(post["id"])["variants"][0]["cost_usd"] == 0.015
    http.reply(401, {"title": "Unauthorized"})
    http.reply(200, {"access_token": "new", "refresh_token": "r2"})
    http.reply(201, {"data": {"id": "77"}})
    publisher.tick(START + timedelta(minutes=60))
    assert [r.url for r in http.requests] == [
        "https://api.x.com/2/tweets",
        "https://api.x.com/2/oauth2/token",
        "https://api.x.com/2/tweets",
    ]
    assert http.requests[2].headers["Authorization"] == "Bearer new"
    assert service.accounts.read_secrets(account["id"])["refresh_token"] == "r2"
    assert service.get(post["id"])["variants"][0]["url"] == "https://x.com/i/web/status/77"


X_KEYS = {
    "api_key": "ck",
    "api_secret": "cs",
    "access_token": "123-at",
    "access_token_secret": "ats",
}


def test_x_signature_matches_xs_published_example() -> None:
    # The worked example on docs.x.com "Creating a signature" (OAuth 1.0a, HMAC-SHA1).
    header = x_provider.oauth1_header(
        "POST",
        "https://api.x.com/1.1/statuses/update.json?include_entities=true",
        consumer_key="xvz1evFS4wEEPTGEFPHBog",
        consumer_secret="kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
        token="370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
        token_secret="LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
        nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg",
        timestamp=1318622958,
        params=[("status", "Hello Ladies + Gentlemen, a signed OAuth request!")],
    )
    assert header.startswith('OAuth oauth_consumer_key="xvz1evFS4wEEPTGEFPHBog", oauth_nonce=')
    assert 'oauth_signature="Ls93hJiZbQ3akF3HF3x1Bz8%2FzU4%3D"' in header
    assert 'oauth_token="370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb"' in header
    assert header.endswith('oauth_version="1.0"')


def test_x_posts_with_api_keys_signed_and_never_refreshes(
    service: SocialService, publisher: Publisher, http: FakeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(x_provider, "_nonce", lambda: "n0nce")
    monkeypatch.setattr(x_provider, "_timestamp", lambda: 1_800_000_000)
    account = service.accounts.connect({"platform": "x", "auth": "api_keys", "secrets": X_KEYS})
    assert account["auth"] == "api_keys"
    post = approve(
        service,
        propose(service, account, variants=[{"account_id": account["id"], "text": "Hi X"}]),
    )
    http.reply(201, {"data": {"id": "88"}})
    publisher.tick(START + timedelta(minutes=60))
    sent = http.requests[0]
    assert sent.url == "https://api.x.com/2/tweets"
    expected = x_provider.oauth1_header(
        "POST",
        sent.url,
        consumer_key="ck",
        consumer_secret="cs",
        token="123-at",
        token_secret="ats",
        nonce="n0nce",
        timestamp=1_800_000_000,
    )
    assert sent.headers["Authorization"] == expected
    assert json.loads(sent.body or b"{}") == {"text": "Hi X"}
    assert service.get(post["id"])["variants"][0]["url"] == "https://x.com/i/web/status/88"

    # A 401 means the keys were regenerated: no OAuth 2.0 refresh, straight to reconnect.
    second = approve(
        service,
        propose(service, account, variants=[{"account_id": account["id"], "text": "Again"}]),
    )
    http.reply(401, {"title": "Unauthorized"})
    publisher.tick(START + timedelta(minutes=120))
    assert len(http.requests) == 2
    assert service.get(second["id"])["variants"][0]["state"] == "needs_reconnect"


def test_x_api_keys_on_a_read_only_app_say_how_to_fix_it() -> None:
    http = FakeHttp()
    http.reply(
        403, {"title": "Forbidden", "detail": "You are not permitted to perform this action."}
    )
    request = PublishRequest(text="Hi")
    with pytest.raises(PublishError) as caught:
        asyncio.run(x_provider.XAdapter().publish(request, dict(X_KEYS), http, lambda _: None))
    assert caught.value.kind == "permanent"
    assert "Read and write" in caught.value.message


def test_x_api_keys_need_all_four_values_and_one_method_at_a_time(
    service: SocialService,
) -> None:
    with pytest.raises(SocialAccountError, match="API key, API secret"):
        service.accounts.connect(
            {"platform": "x", "auth": "api_keys", "secrets": {"api_key": "k", "access_token": "t"}}
        )
    with pytest.raises(SocialAccountError, match="other way"):
        service.accounts.connect(
            {"platform": "x", "auth": "oauth2", "secrets": {**X_KEYS, "refresh_token": "r"}}
        )
    with pytest.raises(SocialAccountError, match="API keys or an OAuth 2.0"):
        service.accounts.connect({"platform": "x", "auth": "pin", "secrets": X_KEYS})
    # Without auth the fields decide, so older callers that send one token still work.
    keys = service.accounts.connect({"platform": "x", "secrets": X_KEYS})
    token = service.accounts.connect({"platform": "x", "secrets": {"access_token": "t"}})
    assert (keys["auth"], token["auth"]) == ("api_keys", "oauth2")


def test_x_replacing_a_login_keeps_its_method_or_starts_fresh(service: SocialService) -> None:
    account = service.accounts.connect(
        {
            "platform": "x",
            "secrets": {"access_token": "old", "refresh_token": "r", "client_id": "c"},
        }
    )
    # Same method: empty fields keep their saved values.
    service.accounts.connect(
        {"id": account["id"], "platform": "x", "auth": "oauth2", "secrets": {"access_token": "new"}}
    )
    assert service.accounts.read_secrets(account["id"]) == {
        "access_token": "new",
        "refresh_token": "r",
        "client_id": "c",
    }
    # Switching to API keys drops the OAuth 2.0 fields and needs all four keys.
    with pytest.raises(SocialAccountError, match="API key, API secret"):
        service.accounts.connect(
            {"id": account["id"], "platform": "x", "auth": "api_keys", "secrets": {"api_key": "k"}}
        )
    switched = service.accounts.connect(
        {"id": account["id"], "platform": "x", "auth": "api_keys", "secrets": X_KEYS}
    )
    assert switched["auth"] == "api_keys"
    assert service.accounts.read_secrets(account["id"]) == X_KEYS
    assert "api_secret" not in json.dumps(service.accounts.list())  # field names stay private
    # A new token alone keeps an API-key account on API keys.
    service.accounts.connect(
        {"id": account["id"], "platform": "x", "secrets": {"access_token": "123-new"}}
    )
    assert service.accounts.read_secrets(account["id"])["access_token_secret"] == "ats"


def test_x_validation_counts_links_as_23(service: SocialService) -> None:
    account = service.accounts.connect({"platform": "x", "secrets": {"access_token": "t"}})
    post = propose(
        service,
        account,
        variants=[
            {"account_id": account["id"], "text": "a" * 260, "link": "https://raticode.dev/x"}
        ],
    )
    with pytest.raises(SocialError, match="280"):
        approve(service, post)


def test_youtube_uploads_with_a_fresh_token(
    service: SocialService, publisher: Publisher, http: FakeHttp, tmp_path: Path
) -> None:
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64)
    account = service.accounts.connect(
        {
            "platform": "youtube",
            "secrets": {"refresh_token": "r", "client_id": "c", "client_secret": "s"},
        }
    )
    post = approve(
        service,
        propose(
            service,
            account,
            scheduled_at=at(5),
            variants=[
                {
                    "account_id": account["id"],
                    "text": "A 90 second demo",
                    "options": {"title": "Raticode demo"},
                    "media": [{"path": str(video)}],
                }
            ],
        ),
    )
    http.reply(200, {"access_token": "fresh"})
    http.reply(
        200, {}, {"Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=1"}
    )
    http.reply(200, {"id": "vid1"})
    publisher.tick(START + timedelta(minutes=5))
    token, start, upload = http.requests
    assert token.url == "https://oauth2.googleapis.com/token"
    metadata = json.loads(start.body or b"{}")
    assert metadata["snippet"]["title"] == "Raticode demo"
    assert metadata["status"]["privacyStatus"] == "public"
    assert upload.method == "PUT" and upload.body == video.read_bytes()
    assert service.get(post["id"])["variants"][0]["url"] == "https://www.youtube.com/watch?v=vid1"


def test_youtube_needs_a_video(service: SocialService) -> None:
    account = service.accounts.connect(
        {"platform": "youtube", "secrets": {"refresh_token": "r", "client_id": "c"}}
    )
    post = propose(service, account, variants=[{"account_id": account["id"], "text": "No video"}])
    with pytest.raises(SocialError, match="video"):
        approve(service, post)


def test_manual_platforms_are_drafts_you_paste(
    service: SocialService, publisher: Publisher, http: FakeHttp
) -> None:
    post = service.propose(
        "rem",
        {
            "title": "Supporters",
            "variants": [
                {"platform": "patreon", "text": "Thanks!", "options": {"title": "November"}},
                {"platform": "buymeacoffee", "text": "Thanks!"},
                {"platform": "tiktok", "text": "Demo clip caption"},
            ],
        },
    )
    approved = approve(service, post)
    assert {v["state"] for v in approved["variants"]} == {"ready_to_post"}
    publisher.tick(START + timedelta(minutes=5))
    assert http.requests == []
    variant = approved["variants"][0]
    with pytest.raises(SocialError):
        service.mark_posted("rem", post["id"], variant["id"], "https://www.patreon.com/posts/1")
    for item in approved["variants"]:
        service.mark_posted("user", post["id"], item["id"], "https://example.com/p")
    assert service.get(post["id"])["state"] == "published"


def test_media_is_pinned_by_hash_and_jpeg_location_is_removed(
    service: SocialService, tmp_path: Path
) -> None:
    from PIL import Image

    photo = tmp_path / "photo.jpg"
    image = Image.new("RGB", (4, 4), "red")
    exif = Image.Exif()
    exif[0x8825] = {2: (1.0, 2.0, 3.0)}
    image.save(photo, exif=exif)
    record = service.import_media(photo)
    stored = service.media_path(record["sha256"])
    assert stored is not None and stored.parent == service.root / "media"
    with Image.open(stored) as reopened:
        assert not reopened.getexif()
    photo.write_bytes(b"changed")
    assert service.media_path(record["sha256"]) == stored
    bad = tmp_path / "notes.txt"
    bad.write_text("secret")
    with pytest.raises(SocialError):
        service.import_media(bad)


def test_proposers_attach_media_only_from_open_projects(tmp_path: Path, clock: Clock) -> None:
    from PIL import Image

    project = tmp_path / "project"
    private = tmp_path / "Pictures"
    project.mkdir()
    private.mkdir()
    service = SocialService(
        tmp_path / "data", secrets=FakeSecrets(), clock=clock, media_roots=lambda: [project]
    )
    account = discord(service)
    for folder in (project, private, project / ".hidden"):
        folder.mkdir(exist_ok=True)
        Image.new("RGB", (2, 2), "blue").save(folder / "shot.png")

    def media(path: Path) -> list[dict[str, Any]]:
        # A distinct text each time, so no proposal is refused as a duplicate.
        text = f"Shot {uuid4().hex[:8]}"
        return [{"account_id": account["id"], "text": text, "media": [{"path": str(path)}]}]

    post = propose(service, account, variants=media(project / "shot.png"))
    assert post["variants"][0]["media"][0]["name"] == "shot.png"
    for refused in (private / "shot.png", project / ".hidden" / "shot.png", private / "none.png"):
        with pytest.raises(SocialError, match="only from a project") as caught:
            propose(service, account, variants=media(refused))
        assert caught.value.code == "forbidden"
    with pytest.raises(SocialError, match="only from a project"):
        propose(service, account, variants=media(project / ".." / "Pictures" / "shot.png"))
    # The user still attaches any file they choose.
    mine = propose(service, account, actor="user", variants=media(private / "shot.png"))
    stored = mine["variants"][0]["media"][0]
    # A proposer can reuse a stored file only under its stored name, never a new label.
    reuse = [
        {
            "account_id": account["id"],
            "text": "Reused picture",
            "media": [{"sha256": stored["sha256"], "name": "launch-banner.png"}],
        }
    ]
    relabelled = propose(service, account, variants=reuse)
    assert relabelled["variants"][0]["media"][0]["name"] != "launch-banner.png"
    with pytest.raises(SocialError):
        propose(
            service,
            account,
            variants=[
                {"account_id": account["id"], "text": "Bad ref", "media": [{"sha256": ["x"]}]}
            ],
        )


def test_media_type_comes_from_the_bytes_and_png_location_is_removed(
    service: SocialService, tmp_path: Path
) -> None:
    from PIL import Image

    fake = tmp_path / "notes.png"
    fake.write_text("not a picture")
    with pytest.raises(SocialError, match="Media can be"):
        service.import_media(fake)
    photo = tmp_path / "photo.png"
    image = Image.new("RGBA", (4, 4), (255, 0, 0, 128))
    exif = Image.Exif()
    exif[0x8825] = {2: (1.0, 2.0, 3.0)}
    image.save(photo, exif=exif)
    with Image.open(photo) as before:
        assert before.getexif()
    record = service.import_media(photo)
    stored = service.media_path(record["sha256"])
    assert stored is not None
    with Image.open(stored) as reopened:
        assert not reopened.getexif()
        assert reopened.mode == "RGBA"


def test_the_cli_has_no_approve_or_publish_command() -> None:
    from gofer.cli.commands.social import app

    names = {command.name for command in app.registered_commands}
    assert names == {"propose", "list", "show", "accounts", "templates", "times"}
