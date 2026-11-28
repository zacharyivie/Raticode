"""The phone's Posts screen: the review list and the user's decisions on it."""

from __future__ import annotations

from typing import Any

import pytest

from gofer.devices.desktop_control import Refused
from gofer.devices.phone_posts import PhonePosts
from gofer.social.service import SocialService
from tests.unit.test_device_application import application as application
from tests.unit.test_device_desktop_control import phone as phone
from tests.unit.test_social_posts import Clock, FakeSecrets, at, discord


@pytest.fixture
def posts(phone, tmp_path):
    app, peer, _context, send = phone
    service = SocialService(tmp_path / "social-data", secrets=FakeSecrets(), clock=Clock())
    app.desktop.posts.service = service
    app.desktop.enable(peer, True)
    return service, send


def _propose(service: SocialService, text: str = "Raticode 0.6 is out", **extra: Any) -> Any:
    channel = discord(service)
    return service.propose(
        "rem",
        {
            "title": "Release",
            "scheduled_at": at(60),
            "variants": [{"account_id": channel["id"], "text": text, **extra}],
        },
    )


def test_the_phone_offers_posts_only_when_the_desktop_has_them(phone):
    app, peer, _context, send = phone
    app.desktop.enable(peer, True)
    assert send("desktop.read", "overview")["value"]["posts"] is False
    assert send("desktop.read", "posts.list")["code"] == "unavailable"


def test_the_phone_lists_posts_with_what_each_takes_and_no_secrets(posts):
    service, send = posts
    post = _propose(service)
    listed = send("desktop.read", "posts.list")["value"]
    assert listed["paused"] is False and listed["accounts"][0]["platform"] == "discord"
    shown = listed["posts"][0]
    assert shown["id"] == post["id"] and shown["lane"] == "review"
    assert shown["content_hash"] == post["content_hash"]
    assert shown["actions"] == ["approve", "approve_now", "request_changes", "decline"]
    assert shown["variants"][0]["text"] == "Raticode 0.6 is out"
    assert "webhook" not in str(listed).lower()


def test_an_approval_from_the_phone_is_the_users_and_pinned_to_what_it_showed(posts):
    service, send = posts
    post = _propose(service)
    stale = send(
        "desktop.action",
        "posts.decide",
        {"decision": "approve", "post_id": post["id"], "content_hash": "0" * 64},
    )
    assert stale["code"] == "conflict"
    done = send(
        "desktop.action",
        "posts.decide",
        {"decision": "approve", "post_id": post["id"], "content_hash": post["content_hash"]},
    )
    assert done["ok"] and done["value"]["posts"][0]["state"] == "scheduled"
    events = service.get(post["id"], events=True)["events"]
    approved = next(e for e in events if e["kind"] == "approved")
    assert approved["actor"] == "user" and approved["detail"]["via"] == "phone"
    # Once approved it can be cancelled from the phone, not approved again.
    assert send("desktop.read", "posts.list")["value"]["posts"][0]["actions"] == ["cancel"]


def test_a_post_the_phone_cant_fully_show_needs_a_second_confirmation(posts):
    service, send = posts
    # Patreon takes long posts; the phone shows the first 4000 characters.
    post = service.propose(
        "rem", {"scheduled_at": at(60), "variants": [{"platform": "patreon", "text": "x" * 5000}]}
    )
    shown = send("desktop.read", "posts.list")["value"]["posts"][0]
    assert shown["unseen"] is True and len(shown["variants"][0]["text"]) == 4000
    params = {"decision": "approve", "post_id": post["id"], "content_hash": post["content_hash"]}
    assert send("desktop.action", "posts.decide", params)["code"] == "conflict"
    assert service.get(post["id"])["approval"] is None
    assert send("desktop.action", "posts.decide", {**params, "unseen_confirmed": True})["ok"]


def test_a_post_with_options_the_phone_doesnt_show_needs_a_second_confirmation(posts):
    service, send = posts
    # The phone shows no platform options, such as a video or link-card title.
    post = service.propose(
        "rem",
        {
            "scheduled_at": at(60),
            "variants": [
                {"platform": "patreon", "text": "Short", "options": {"title": "Big news"}}
            ],
        },
    )
    shown = send("desktop.read", "posts.list")["value"]["posts"][0]
    assert shown["unseen"] is True
    params = {"decision": "approve", "post_id": post["id"], "content_hash": post["content_hash"]}
    assert send("desktop.action", "posts.decide", params)["code"] == "conflict"
    assert send("desktop.action", "posts.decide", {**params, "unseen_confirmed": True})["ok"]
    plain = _propose(service, "No options")
    listed = send("desktop.read", "posts.list")["value"]["posts"]
    assert next(p for p in listed if p["id"] == plain["id"])["unseen"] is False


def test_the_phone_declines_asks_for_changes_and_pauses(posts):
    service, send = posts
    post = _propose(service)
    asked = send(
        "desktop.action",
        "posts.decide",
        {
            "decision": "request_changes",
            "post_id": post["id"],
            "content_hash": post["content_hash"],
            "note": "Shorter, please",
        },
    )
    assert asked["value"]["posts"][0]["state"] == "changes_requested"
    assert asked["value"]["posts"][0]["notes"][-1]["text"] == "Shorter, please"
    paused = send("desktop.action", "posts.decide", {"decision": "pause"})
    assert paused["value"]["paused"] is True and service.settings()["paused"] is True
    assert (
        send("desktop.action", "posts.decide", {"decision": "resume"})["value"]["paused"] is False
    )
    # The schema already refuses unknown decisions; the desktop checks again.
    with pytest.raises(Refused):
        PhonePosts().act({"decision": "pause"})


def test_the_phone_shows_a_copy_that_couldnt_be_deleted_and_takes_its_choices(posts):
    from datetime import timedelta

    from gofer.social.publisher import Publisher
    from tests.unit.test_social_posts import START, FakeHttp, _held_on_facebook

    service, send = posts
    http = FakeHttp()
    publisher = Publisher(service, http=http)
    _account, post = _held_on_facebook(service, publisher, http)
    service.decide("user", post["id"], "cancel")
    http.reply(403, {"error": {"message": "Permissions error", "code": 200}})
    publisher.tick(START + timedelta(minutes=1))
    shown = send("desktop.read", "posts.list")["value"]["posts"][0]
    assert shown["lane"] == "problems"
    (copy,) = shown["copies"]
    assert copy["message"] == "Permissions error" and copy["waiting"] is True
    assert copy["url"] == "https://www.facebook.com/12345_678"
    assert copy["actions"] == ["retry_copy", "copy_deleted"]
    params = {"post_id": post["id"], "content_hash": shown["content_hash"], "copy_id": copy["id"]}
    retried = send("desktop.action", "posts.decide", {"decision": "retry_copy", **params})
    assert retried["ok"]
    events = service.get(post["id"], events=True)["events"]
    retry = next(e for e in events if e["kind"] == "platform_copy_retry")
    assert retry["actor"] == "user" and retry["detail"]["via"] == "phone"
    done = send("desktop.action", "posts.decide", {"decision": "copy_deleted", **params})
    assert done["ok"] and done["value"]["posts"][0]["copies"] == []
    assert done["value"]["posts"][0]["lane"] == "done"
    gone = send("desktop.action", "posts.decide", {"decision": "copy_deleted", **params})
    assert gone["code"] == "not_found"
