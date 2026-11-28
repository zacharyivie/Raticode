"""``/api/social/*`` routing: who may call what, which service call a path reaches, and the
account check.

Everything runs against a real ``SocialService`` in a temporary folder with an in-memory
secret store, and the account check gets ``FakeHttp``, so nothing reaches a platform.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gofer.social import api as social_api
from gofer.social.accounts import SocialAccountError
from gofer.social.service import SocialError, SocialService
from tests.unit.test_social_posts import (
    Clock,
    FakeHttp,
    FakeSecrets,
    at,
    discord,
    propose,
)


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


def call(
    service: SocialService,
    method: str,
    parts: list[str],
    actor: str = "user",
    body: dict[str, Any] | None = None,
    query: dict[str, list[str]] | None = None,
) -> Any:
    return social_api.handle(service, method, parts, actor, body, query)


def stored_accounts(service: SocialService) -> str:
    """The account file as saved, so a refused call can be shown to change nothing."""
    return service.accounts.path.read_text() if service.accounts.path.exists() else ""


def facebook(service: SocialService, **options: Any) -> dict[str, Any]:
    return service.accounts.connect(
        {
            "platform": "facebook",
            "name": "Page",
            "secrets": {"page_access_token": "fake-page-token"},
            "options": options,
        }
    )


def linkedin_login(service: SocialService) -> dict[str, Any]:
    return service.accounts.connect(
        {"platform": "linkedin", "name": "Me", "secrets": {"access_token": "fake-li-token"}}
    )


# Rem's token on account routes ----------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "tail", "body"),
    [
        ("GET", [], None),
        ("POST", [], {"platform": "discord", "secrets": {"webhook_url": "x"}}),
        ("PATCH", ["ACCOUNT"], {"enabled": False, "name": "Changed"}),
        ("DELETE", ["ACCOUNT"], None),
        ("POST", ["ACCOUNT", "check"], None),
        ("POST", ["sign-in"], {"platform": "youtube"}),
    ],
)
def test_rem_is_refused_on_every_account_route_and_nothing_changes(
    service: SocialService, method: str, tail: list[str], body: dict[str, Any] | None
) -> None:
    account = discord(service)
    secrets_before = service.accounts.read_secrets(account["id"])
    saved_before = stored_accounts(service)
    parts = ["accounts", *[account["id"] if item == "ACCOUNT" else item for item in tail]]
    with pytest.raises(social_api.SocialApiError) as refused:
        call(service, method, parts, "rem", body)
    assert refused.value.status == 403
    assert refused.value.code == "forbidden"
    assert stored_accounts(service) == saved_before
    assert service.accounts.read_secrets(account["id"]) == secrets_before


def test_rem_posting_a_new_account_leaves_the_account_list_unchanged(
    service: SocialService,
) -> None:
    discord(service)
    listed_before = call(service, "GET", ["accounts"])["accounts"]
    body = {
        "platform": "discord",
        "name": "Sneaky",
        "secrets": {"webhook_url": "https://discord.com/api/webhooks/999/another-token"},
    }
    with pytest.raises(social_api.SocialApiError) as refused:
        call(service, "POST", ["accounts"], "rem", body)
    assert refused.value.status == 403 and refused.value.code == "forbidden"
    assert call(service, "GET", ["accounts"])["accounts"] == listed_before
    assert len(listed_before) == 1


def test_the_user_lists_connects_changes_and_disconnects_accounts(
    service: SocialService,
) -> None:
    assert call(service, "GET", ["accounts"]) == {"accounts": []}
    body = {
        "platform": "discord",
        "name": "Channel",
        "secrets": {"webhook_url": "https://discord.com/api/webhooks/123456/fake-webhook-token"},
    }
    account = call(service, "POST", ["accounts"], body=body)
    assert account["name"] == "Channel" and "secret_fields" not in account
    changed = call(service, "PATCH", ["accounts", account["id"]], body={"quiet": True})
    assert changed["quiet"] is True
    assert call(service, "DELETE", ["accounts", account["id"]]) == {"ok": True}
    assert call(service, "GET", ["accounts"]) == {"accounts": []}


def test_an_account_store_error_becomes_a_400_invalid(service: SocialService) -> None:
    with pytest.raises(social_api.SocialApiError) as bad:
        call(service, "POST", ["accounts"], body={"platform": "myspace"})
    assert (bad.value.status, bad.value.code) == (400, "invalid")
    assert "Choose Discord" in str(bad.value)
    with pytest.raises(social_api.SocialApiError) as missing:
        call(service, "DELETE", ["accounts", "nope"])
    assert (missing.value.status, missing.value.code) == (400, "invalid")
    assert str(missing.value) == "No such social account."


# Platform copies Raticode couldn't delete -----------------------------------------------


@pytest.mark.parametrize(("action", "choice"), [("retry", "retry"), ("mark-deleted", "deleted")])
def test_copy_routes_reach_platform_copy_with_the_users_choice(
    service: SocialService, monkeypatch: pytest.MonkeyPatch, action: str, choice: str
) -> None:
    calls: list[tuple[Any, ...]] = []

    def spy(actor: str, post_id: str, copy_id: Any, chosen: str) -> dict[str, Any]:
        calls.append((actor, post_id, copy_id, chosen))
        return {"id": post_id}

    monkeypatch.setattr(service, "platform_copy", spy)
    result = call(service, "POST", ["posts", "post-1", "copies", "copy-9", action])
    assert result == {"id": "post-1"}
    assert calls == [("user", "post-1", "copy-9", choice)]


def test_an_unknown_copy_action_is_not_found(
    service: SocialService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "platform_copy", pytest.fail)
    with pytest.raises(social_api.SocialApiError) as missing:
        call(service, "POST", ["posts", "post-1", "copies", "copy-9", "delete"])
    assert (missing.value.status, missing.value.code) == (404, "not_found")


@pytest.mark.parametrize("action", ["retry", "mark-deleted"])
def test_rem_is_refused_on_copy_routes(service: SocialService, action: str) -> None:
    post = propose(service, discord(service))
    with pytest.raises(social_api.SocialApiError) as refused:
        call(service, "POST", ["posts", post["id"], "copies", "copy-9", action], "rem")
    assert (refused.value.status, refused.value.code) == (403, "forbidden")


def test_the_user_on_a_copy_that_is_no_longer_waiting_gets_404(service: SocialService) -> None:
    post = propose(service, discord(service))
    with pytest.raises(social_api.SocialApiError) as gone:
        call(service, "POST", ["posts", post["id"], "copies", "copy-9", "mark-deleted"])
    assert (gone.value.status, gone.value.code) == (404, "not_found")


# Routing ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "parts"),
    [
        ("GET", ["nothing-here"]),
        ("PUT", ["posts"]),
        ("POST", ["posts", "post-1", "explode"]),
        ("POST", ["posts", "post-1", "variants", "v1", "explode"]),
        ("PATCH", ["templates"]),
        ("GET", ["accounts", "a", "b", "c"]),
        ("GET", ["accounts", "sign-in", "a", "b", "c"]),
    ],
)
def test_an_unknown_path_is_not_found(
    service: SocialService, method: str, parts: list[str]
) -> None:
    with pytest.raises(social_api.SocialApiError) as missing:
        call(service, method, parts)
    assert (missing.value.status, missing.value.code) == (404, "not_found")
    assert str(missing.value) == "Not found."


def test_read_routes_answer_rem_and_the_user(service: SocialService) -> None:
    channel = discord(service)
    post = propose(service, channel)
    snapshot = call(service, "GET", [], "rem")
    assert [item["id"] for item in snapshot["accounts"]] == [channel["id"]]
    assert [p["id"] for p in call(service, "GET", ["posts"], "rem")["posts"]] == [post["id"]]
    assert call(service, "GET", ["posts"], query={"state": ["published"]}) == {"posts": []}
    shown = call(service, "GET", ["posts", post["id"]], "rem")
    assert shown["id"] == post["id"] and "events" in shown
    assert call(service, "GET", ["templates"], "rem")["templates"]
    times = call(
        service, "GET", ["times"], "rem", query={"target": [f" {channel['id']} ,"], "tz": ["UTC"]}
    )
    assert times["slots"]
    with pytest.raises(social_api.SocialApiError) as missing:
        call(service, "GET", ["posts", "nope"])
    assert (missing.value.status, missing.value.code) == (404, "not_found")


def test_rules_list_only_accounts_rem_may_target(service: SocialService) -> None:
    channel = discord(service)
    discord(service, name="Quiet channel", quiet=True)
    found = call(service, "GET", ["rules"], "rem")
    assert [item["account_id"] for item in found["accounts"]] == [channel["id"]]
    assert {item["platform"] for item in found["manual_platforms"]} >= {"patreon"}
    assert found["limits"]["open_proposals_per_source"] > 0


def test_post_actions_reach_the_service_and_rem_cannot_decide(service: SocialService) -> None:
    channel = discord(service)
    post = call(
        service,
        "POST",
        ["posts"],
        "rem",
        {
            "title": "Release",
            "scheduled_at": at(60),
            "variants": [{"account_id": channel["id"], "text": "Raticode 0.6 is out"}],
        },
    )
    assert post["state"] == "proposed"
    edited = call(
        service,
        "POST",
        ["posts", post["id"], "edit"],
        "rem",
        {"variants": [{"account_id": channel["id"], "text": "Raticode 0.6.1 is out"}]},
    )
    assert edited["content_hash"] != post["content_hash"]
    for action in ("approve", "decline", "cancel", "request-changes"):
        with pytest.raises(social_api.SocialApiError) as refused:
            call(service, "POST", ["posts", post["id"], action], "rem", {"note": "x"})
        assert refused.value.status == 403, action
    asked = call(
        service, "POST", ["posts", post["id"], "request-changes"], body={"note": "Shorter"}
    )
    assert asked["id"] == post["id"]
    declined = call(service, "POST", ["posts", post["id"], "decline"], body={"note": "No"})
    assert declined["state"] == "declined"


def test_approve_passes_the_hash_and_only_a_true_publish_now(
    service: SocialService, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        service,
        "approve",
        lambda actor, post_id, content_hash, *, publish_now: seen.append(
            (actor, post_id, content_hash, publish_now)
        ),
    )
    call(service, "POST", ["posts", "p", "approve"], body={"content_hash": "h"})
    call(service, "POST", ["posts", "p", "approve"], body={"content_hash": "h", "publish_now": 1})
    call(service, "POST", ["posts", "p", "approve"], body={"publish_now": True})
    assert seen == [
        ("user", "p", "h", False),
        ("user", "p", "h", False),
        ("user", "p", None, True),
    ]


def test_variant_routes_reach_publish_and_mark_posted(
    service: SocialService, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[Any, ...]] = []

    def recorder(name: str) -> Any:
        def record(*args: Any) -> dict[str, Any]:
            seen.append((name, *args))
            return {}

        return record

    monkeypatch.setattr(service, "publish_variant", recorder("publish"))
    monkeypatch.setattr(service, "mark_posted", recorder("posted"))
    call(service, "POST", ["posts", "p", "variants", "v", "publish"], body={"content_hash": "h"})
    call(service, "POST", ["posts", "p", "variants", "v", "mark-posted"], body={"url": "u"})
    assert seen == [("publish", "user", "p", "v", "h"), ("posted", "user", "p", "v", "u")]


def test_templates_pause_and_settings_are_the_users(service: SocialService) -> None:
    body = {"name": "Release", "variants": [{"platform": "discord", "text": "{version} is out"}]}
    for method, parts in (
        ("POST", ["templates"]),
        ("PUT", ["templates", "t1"]),
        ("DELETE", ["templates", "t1"]),
        ("POST", ["pause"]),
        ("PUT", ["settings"]),
    ):
        with pytest.raises(social_api.SocialApiError) as refused:
            call(service, method, parts, "rem", body)
        assert refused.value.status == 403, parts
    saved = call(service, "POST", ["templates"], body=body)
    renamed = call(service, "PUT", ["templates", saved["id"]], body={**body, "name": "Renamed"})
    assert renamed["name"] == "Renamed"
    assert call(service, "DELETE", ["templates", saved["id"]]) == {"ok": True}
    assert call(service, "POST", ["pause"], body={"paused": True})["paused"] is True
    assert call(service, "POST", ["pause"], body={"paused": "yes"})["paused"] is False
    settings = call(service, "PUT", ["settings"], body={"late_window_minutes": 45})
    assert settings["late_window_minutes"] == 45


def test_sign_in_routes_and_errors(service: SocialService, monkeypatch: pytest.MonkeyPatch) -> None:
    from gofer.social.signin import SignInError

    seen: list[tuple[str, ...]] = []

    class Manager:
        def start(self, body: dict[str, Any]) -> dict[str, Any]:
            seen.append(("start", body["platform"]))
            return {"id": "s1"}

        def get(self, sign_in_id: str) -> dict[str, Any]:
            seen.append(("get", sign_in_id))
            return {"id": sign_in_id}

        def cancel(self, sign_in_id: str) -> dict[str, Any]:
            seen.append(("cancel", sign_in_id))
            return {"id": sign_in_id}

        def reopen(self, sign_in_id: str) -> dict[str, Any]:
            seen.append(("open", sign_in_id))
            return {"id": sign_in_id}

        def choose_page(self, sign_in_id: str, page_id: Any) -> dict[str, Any]:
            if page_id == "bad":
                raise SignInError("That Page isn't on this login.")
            seen.append(("page", sign_in_id, page_id))
            return {"id": sign_in_id}

    monkeypatch.setattr(SocialService, "sign_ins", Manager())
    call(service, "POST", ["accounts", "sign-in"], body={"platform": "youtube"})
    call(service, "GET", ["accounts", "sign-in", "s1"])
    call(service, "POST", ["accounts", "sign-in", "s1", "open"])
    call(service, "POST", ["accounts", "sign-in", "s1", "page"], body={"page_id": "42"})
    call(service, "DELETE", ["accounts", "sign-in", "s1"])
    assert seen == [
        ("start", "youtube"),
        ("get", "s1"),
        ("open", "s1"),
        ("page", "s1", "42"),
        ("cancel", "s1"),
    ]
    with pytest.raises(social_api.SocialApiError) as bad:
        call(service, "POST", ["accounts", "sign-in", "s1", "page"], body={"page_id": "bad"})
    assert (bad.value.status, bad.value.code, str(bad.value)) == (
        400,
        "invalid",
        "That Page isn't on this login.",
    )


# Account check ---------------------------------------------------------------------------


def test_an_auth_failure_marks_the_account_for_reconnect(
    service: SocialService, http: FakeHttp
) -> None:
    account = linkedin_login(service)
    http.reply(401, {"message": "Invalid access token"})
    with pytest.raises(SocialError) as failed:
        social_api.check_account(service, account["id"], http)
    stored = service.accounts.get(account["id"])
    assert stored["needs_reconnect"] is True
    assert stored["check_error"] == str(failed.value)
    assert service.accounts.list()[0]["status"] == "needs_reconnect"


def test_a_failure_that_is_not_about_the_login_keeps_the_account_connected(
    service: SocialService, http: FakeHttp
) -> None:
    account = linkedin_login(service)
    http.reply(503, {"message": "Try later"})
    with pytest.raises(SocialError) as failed:
        social_api.check_account(service, account["id"], http)
    stored = service.accounts.get(account["id"])
    assert stored["needs_reconnect"] is False
    assert stored["check_error"] == str(failed.value)


def test_a_good_check_saves_the_name_and_author_and_clears_the_error(
    service: SocialService, http: FakeHttp
) -> None:
    account = linkedin_login(service)
    service.accounts.mark(account["id"], needs_reconnect=True, check_error="Earlier failure")
    http.reply(200, {"sub": "abc123", "name": "Zed Example"})
    checked = social_api.check_account(service, account["id"], http)
    assert checked["id"] == account["id"]
    assert checked["remote_name"] == "Zed Example"
    assert checked["options"]["author_urn"] == "urn:li:person:abc123"
    assert checked["check_error"] is None and checked["needs_reconnect"] is False
    assert checked["status"] == "connected"
    assert "fake-li-token" in http.requests[0].headers["Authorization"]


def test_a_good_check_fills_in_a_missing_page_id_only(
    service: SocialService, http: FakeHttp
) -> None:
    empty = facebook(service)
    http.reply(200, {"id": "777", "name": "My Page"})
    assert social_api.check_account(service, empty["id"], http)["options"]["page_id"] == "777"
    chosen = facebook(service, page_id="12345")
    http.reply(200, {"id": "777", "name": "My Page"})
    checked = social_api.check_account(service, chosen["id"], http)
    assert checked["options"]["page_id"] == "12345"
    assert checked["remote_name"] == "My Page"
    assert "author_urn" not in checked["options"]


def test_the_check_route_is_the_users_and_reaches_the_platform(
    service: SocialService, http: FakeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = linkedin_login(service)
    seen: list[Any] = []
    original = social_api.check_account

    def check(svc: SocialService, account_id: str, client: Any = None) -> dict[str, Any]:
        seen.append(account_id)
        return original(svc, account_id, http)

    monkeypatch.setattr(social_api, "check_account", check)
    http.reply(200, {"sub": "abc123", "name": "Zed"})
    checked = call(service, "POST", ["accounts", account["id"], "check"])
    assert checked["remote_name"] == "Zed" and seen == [account["id"]]
    http.reply(401, {"message": "Invalid access token"})
    with pytest.raises(social_api.SocialApiError) as failed:
        call(service, "POST", ["accounts", account["id"], "check"])
    assert (failed.value.status, failed.value.code) == (400, "invalid")


def test_a_platform_with_no_api_cannot_be_checked(
    service: SocialService, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = discord(service)
    monkeypatch.setattr(social_api, "adapter_for", lambda platform: None)
    with pytest.raises(SocialError, match=r"^This platform has no API\.$"):
        social_api.check_account(service, account["id"], FakeHttp())
    assert "check_error" not in service.accounts.get(account["id"])


def test_checking_an_unknown_account_is_an_account_error(service: SocialService) -> None:
    with pytest.raises(SocialAccountError):
        social_api.check_account(service, "nope", FakeHttp())
    with pytest.raises(social_api.SocialApiError) as bad:
        call(service, "POST", ["accounts", "nope", "check"])
    assert (bad.value.status, bad.value.code) == (400, "invalid")


# Time zone -------------------------------------------------------------------------------


def test_local_zone_reads_tz_then_the_system_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", ":Europe/Paris")
    assert social_api.local_zone() == "Europe/Paris"
    monkeypatch.setenv("TZ", "")
    monkeypatch.setattr(Path, "resolve", lambda self: Path("/usr/share/zoneinfo/America/Denver"))
    assert social_api.local_zone() == "America/Denver"
    monkeypatch.setattr(Path, "resolve", lambda self: Path("/etc/localtime"))
    assert social_api.local_zone() == "UTC"

    def broken(self: Path) -> Path:
        raise OSError("no link")

    monkeypatch.setattr(Path, "resolve", broken)
    assert social_api.local_zone() == "UTC"
