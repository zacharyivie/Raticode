"""Posts planning helpers: templates, best-time hints, spacing and "see more" folds.

Templates and suggested times only fill in a draft or a proposal. Nothing here approves:
the tests check that a post made from a template still waits for the user.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.social import api as social_api
from gofer.social.mcp import SocialActions, social_tools
from gofer.social.models import fold_at
from gofer.social.service import SocialError, SocialService
from gofer.social.templates import STARTERS, blanks, fill
from gofer.social.timing import suggest_times, timing_hints
from gofer.ui.headless import agent_request_refusal
from tests.unit.test_social_posts import START, Clock, FakeSecrets, at, discord, linkedin


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(tmp_path: Path, clock: Clock) -> SocialService:
    return SocialService(tmp_path / "data", secrets=FakeSecrets(), clock=clock)


# Templates ------------------------------------------------------------------------------


def test_blanks_are_named_in_braces_and_code_braces_are_left_alone() -> None:
    texts = ["{product} {version} is out", "if (x) { return {} }", "{version} again {what's}"]
    assert blanks(texts) == ["product", "version"]
    assert fill("{product} {version} {missing}", {"product": "Raticode", "version": "0.6"}) == (
        "Raticode 0.6 {missing}"
    )


def test_only_the_user_saves_changes_and_deletes_templates(service: SocialService) -> None:
    body = {
        "name": "Release",
        "title": "{version} is out",
        "variants": [{"platform": "discord", "text": "Raticode {version}: {headline}"}],
    }
    for actor in ("rem", "cli", "workflow:release@3"):
        with pytest.raises(SocialError) as refused:
            service.save_template(actor, body)
        assert refused.value.code == "forbidden"
    saved = service.save_template("user", body)
    assert saved["blanks"] == ["version", "headline"] and saved["builtin"] is False
    with pytest.raises(SocialError):
        service.save_template("rem", {**body, "name": "Changed"}, saved["id"])
    with pytest.raises(SocialError):
        service.delete_template("rem", saved["id"])
    changed = service.save_template("user", {**body, "name": "Changed"}, saved["id"])
    assert changed["name"] == "Changed"
    listed = service.templates()
    # The user's own come first, then the starters.
    assert listed[0]["id"] == saved["id"]
    assert [t["id"] for t in listed[1:]] == [s["id"] for s in STARTERS]
    assert service.delete_template("user", saved["id"]) == {"ok": True}
    with pytest.raises(SocialError) as gone:
        service.delete_template("user", saved["id"])
    assert gone.value.code == "not_found"
    # Starters can't be changed.
    with pytest.raises(SocialError):
        service.save_template("user", body, STARTERS[0]["id"])


def test_templates_are_checked(service: SocialService) -> None:
    with pytest.raises(SocialError, match="name"):
        service.save_template("user", {"name": " ", "variants": [{"platform": "x"}]})
    with pytest.raises(SocialError, match="Unknown platform"):
        service.save_template("user", {"name": "A", "variants": [{"platform": "myspace"}]})
    with pytest.raises(SocialError, match="once"):
        service.save_template(
            "user", {"name": "A", "variants": [{"platform": "x"}, {"platform": "x"}]}
        )


def test_a_proposal_from_a_template_still_waits_for_the_user(service: SocialService) -> None:
    channel = discord(service)
    with pytest.raises(SocialError, match="Fill in: product, version, headline"):
        service.propose("rem", {"template": "starter-release", "fields": {}})
    post = service.propose(
        "rem",
        {
            "template": "starter-release",
            "fields": {
                "product": "Raticode",
                "version": "0.6",
                "headline": "Posts got templates.",
                "change one": "Templates",
                "change two": "Best times",
                "link": "https://raticode.dev/",
            },
            "scheduled_at": at(60),
        },
    )
    # Only Discord has an account here, so the X and LinkedIn parts are left out.
    assert [v["account_id"] for v in post["variants"]] == [channel["id"]]
    assert post["title"] == "Raticode 0.6 is out"
    assert post["variants"][0]["text"].startswith("**Raticode 0.6 is out**")
    assert post["state"] == "proposed" and post["approval"] is None
    with pytest.raises(SocialError, match="No such template"):
        service.propose("rem", {"template": "starter-nope", "fields": {}})


def test_a_template_with_no_connected_platform_says_so(service: SocialService) -> None:
    saved = service.save_template(
        "user", {"name": "X only", "variants": [{"platform": "x", "text": "Hello"}]}
    )
    with pytest.raises(SocialError, match="connected account"):
        service.propose("rem", {"template": saved["id"]})


def test_agents_cant_write_templates_through_the_api(service: SocialService) -> None:
    assert agent_request_refusal("POST", "/api/social/templates") is not None
    assert agent_request_refusal("PUT", "/api/social/templates/template-1") is not None
    assert agent_request_refusal("DELETE", "/api/social/templates/template-1") is not None
    with pytest.raises(social_api.SocialApiError) as refused:
        social_api.handle(
            service, "POST", ["templates"], "rem", {"name": "A", "variants": [{"platform": "x"}]}
        )
    assert refused.value.status == 403
    listed = social_api.handle(service, "GET", ["templates"], "rem", None)
    assert {t["id"] for t in listed["templates"]} >= {"starter-release"}


def test_rem_sees_templates_and_best_times_in_its_rules(service: SocialService) -> None:
    discord(service)
    rules = SocialActions(service).call("social_rules", {})
    assert any(t["id"] == "starter-weekly" and t["yours"] is False for t in rules["templates"])
    assert rules["best_times"]["next"] and rules["timezone"]
    assert any("template" in line for line in rules["rules"])
    schema = next(t for t in social_tools() if t["name"] == "propose_post")["inputSchema"]
    assert {"template", "fields"} <= set(schema["properties"])
    names = {tool["name"] for tool in social_tools()}
    assert not names & {"approve_post", "publish_post", "save_template", "delete_template"}


# Best times ------------------------------------------------------------------------------


def test_suggested_times_fall_in_every_platforms_window_one_per_day() -> None:
    now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)  # Monday
    slots = suggest_times(["linkedin", "x"], "America/New_York", now)
    assert len(slots) == 3
    days = set()
    for slot in slots:
        when = datetime.fromisoformat(slot["at"])
        assert when.utcoffset() == timedelta(hours=-5)
        # X wants 9-12 and LinkedIn 10-13 on Tue-Thu, so 10 or 11 suits both.
        assert when.weekday() in (1, 2, 3) and when.hour in (10, 11)
        assert set(slot["fits"]) == {"linkedin", "x"}
        days.add(when.date())
    assert len(days) == 3


def test_suggestions_skip_times_near_other_planned_posts() -> None:
    now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    first = suggest_times(["x"], "UTC", now, count=1)[0]
    taken = datetime.fromisoformat(first["at"])
    later = suggest_times(["x"], "UTC", now, [taken], count=1)[0]
    assert abs(datetime.fromisoformat(later["at"]) - taken) >= timedelta(hours=3)


def test_unknown_time_zones_and_platforms_fall_back() -> None:
    slots = suggest_times(["myspace"], "Mars/Olympus", datetime(2026, 11, 16, tzinfo=UTC))
    assert slots and slots[0]["at"].endswith("+00:00")


def test_the_times_route_uses_accounts_and_planned_posts(
    service: SocialService, clock: Clock
) -> None:
    channel = discord(service)
    found = social_api.handle(
        service,
        "GET",
        ["times"],
        "rem",
        None,
        {"target": [channel["id"]], "tz": ["Europe/Berlin"]},
    )
    assert len(found["slots"]) == 3 and "General guidance" in found["note"]
    first = found["slots"][0]["at"]
    service.propose(
        "user",
        {"scheduled_at": first, "variants": [{"account_id": channel["id"], "text": "Hi"}]},
    )
    again = service.suggest_times([channel["id"]], "Europe/Berlin")
    assert first not in [slot["at"] for slot in again["slots"]]


# Spacing and quiet hours -----------------------------------------------------------------


def test_posts_close_together_on_one_account_get_a_spacing_note(service: SocialService) -> None:
    channel = discord(service)
    me = linkedin(service)
    one = service.propose(
        "user",
        {
            "title": "One",
            "scheduled_at": at(60),
            "variants": [{"account_id": channel["id"], "text": "First"}],
        },
    )
    service.propose(
        "user",
        {
            "title": "Two",
            "scheduled_at": at(120),
            "variants": [{"account_id": channel["id"], "text": "Second"}],
        },
    )
    service.propose(
        "user",
        {
            "title": "Elsewhere",
            "scheduled_at": at(90),
            "variants": [{"account_id": me["id"], "text": "Other account"}],
        },
    )
    posts = {post["title"]: post for post in service.snapshot()["posts"]}
    notes = [hint["message"] for hint in posts["One"]["timing"]]
    assert notes == ["Test channel: “Two” is 60 minutes after this one."]
    assert posts["Elsewhere"]["timing"] == []
    assert service.get(one["id"])  # reading one post still works


def test_a_post_at_night_gets_a_quiet_hours_note() -> None:
    post: dict[str, Any] = {
        "id": "p1",
        "scheduled_at": "2026-11-20T08:00:00+00:00",
        "timezone": "America/Los_Angeles",
        "variants": [],
    }
    hints = timing_hints(post, [])
    assert hints[0]["kind"] == "quiet_hours" and "00:00 America/Los_Angeles" in hints[0]["message"]


# "See more" folds ------------------------------------------------------------------------


def test_folds_follow_each_platforms_cut() -> None:
    assert fold_at("x", "a" * 500) is None
    assert fold_at("discord", "a" * 5000) is None
    assert fold_at("linkedin", "short") is None
    assert fold_at("linkedin", "a" * 300) == 210
    assert fold_at("linkedin", "one\ntwo\nthree\nfour") == len("one\ntwo\nthree")
    assert fold_at("facebook", "a" * 481) == 480


def test_posts_carry_their_fold_and_platform_preview_facts(service: SocialService) -> None:
    me = linkedin(service)
    post = service.propose(
        "user", {"variants": [{"account_id": me["id"], "text": "x" * 400}], "scheduled_at": None}
    )
    assert post["variants"][0]["fold_at"] == 210
    platforms = {item["id"]: item for item in service.snapshot()["platforms"]}
    assert platforms["linkedin"]["fold_chars"] == 210 and platforms["x"]["link_card"] is True
    assert service.snapshot()["best_times"]["windows"]["youtube"]


def test_phone_decisions_are_recorded_as_the_users_with_where_they_came_from(
    service: SocialService, clock: Clock
) -> None:
    channel = discord(service)
    post = service.propose(
        "rem",
        {"scheduled_at": at(60), "variants": [{"account_id": channel["id"], "text": "Hi"}]},
    )
    service.approve("user", post["id"], post["content_hash"], via="phone")
    events = service.get(post["id"], events=True)["events"]
    approved = next(e for e in events if e["kind"] == "approved")
    assert approved["actor"] == "user" and approved["detail"]["via"] == "phone"
    assert clock.now == START
