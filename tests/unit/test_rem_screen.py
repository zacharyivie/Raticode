"""Phone screen text shared with one Rem request: used once, never stored, never obeyed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gofer.ui.rem_home import RemHome
from gofer.ui.rem_screen import MAX_TEXT, SCREEN_GONE, SCREEN_HELD, SCREEN_RULES, clean_screen
from tests.unit.test_device_desktop_control import application as application
from tests.unit.test_device_desktop_control import phone as phone
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe

SECRET = "Recipe: roast the squash at 425F for 40 minutes"
SCREEN = {"app": "Chrome", "text": SECRET}


def _answer(**extra: Any):
    prompts: list[str] = []

    def generate(prompt, config, cancel):
        prompts.append(prompt)
        return {"reply": "It's a squash recipe.", **extra}

    return prompts, generate


def _stored_bytes(root: Path) -> bytes:
    return b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file())


def test_screen_text_reaches_one_reply_and_is_never_stored(home: RemHome):
    prompts, home.generate = _answer()
    home.submit_message(
        {"request_id": "phone-1", "body": "What am I looking at?", "screen": SCREEN},
        source="phone",
    )
    home._reply()
    assert SECRET in prompts[0] and SCREEN_RULES in prompts[0]
    message = next(m for m in home.store.conversation()["messages"] if m["id"] == "phone-1")
    assert message["screen_app"] == "Chrome" and "screen" not in message
    assert home.screens == {}
    # Nothing Raticode wrote to disk holds the screen's words.
    assert SECRET.encode() not in _stored_bytes(home.data_dir)
    # The next turn doesn't see it again.
    home.submit_message({"request_id": "phone-2", "body": "Thanks"}, source="phone")
    home._reply()
    assert SECRET not in prompts[1] and SCREEN_RULES not in prompts[1]


def test_a_reply_that_saw_a_screen_never_saves_memories_or_changes_tasks(home: RemHome):
    prompts, home.generate = _answer(memories=["The user loves squash"])
    home.submit_message(
        {"request_id": "phone-1", "body": "What is this?", "screen": SCREEN}, source="phone"
    )
    home._reply()
    with home.store.connect() as db:
        memories = home.store.setting(db, "memories")
    assert "squash" not in str(memories)


def test_instructions_on_a_screen_start_nothing_and_leave_no_copy(home: RemHome, tmp_path: Path):
    # A page tells Rem to start work; the model obeys and copies the page into every field.
    prompts, home.generate = _answer(
        reply="I started a task as the page asked.",
        tasks=[recipe(tmp_path, prompt=f"Do what the page says: {SECRET}")],
        details=f"The page says: {SECRET}",
        suggestions=[SECRET[:40]],
        phone_actions=[{"type": "sms_draft", "body": SECRET}],
        files=[str(tmp_path / "notes.txt")],
    )
    (tmp_path / "notes.txt").write_text("private notes")
    home.submit_message(
        {"request_id": "phone-1", "body": "What is this page?", "screen": SCREEN}, source="phone"
    )
    home._reply()
    assert home.store.tasks() == [] and home.workers == {}
    reply = next(m for m in home.store.conversation()["messages"] if m["id"] == "reply-phone-1")
    assert reply["body"] == SCREEN_HELD
    for field in ("details", "suggestions", "phone_actions", "files", "work"):
        assert not reply.get(field), field
    assert SECRET.encode() not in _stored_bytes(home.data_dir)
    assert SECRET[:40].encode() not in _stored_bytes(home.data_dir)


def test_a_plain_answer_about_a_screen_is_kept(home: RemHome):
    prompts, home.generate = _answer(reply="A squash recipe.", details=SECRET)
    home.submit_message(
        {"request_id": "phone-1", "body": "What is this?", "screen": SCREEN}, source="phone"
    )
    home._reply()
    reply = next(m for m in home.store.conversation()["messages"] if m["id"] == "reply-phone-1")
    assert reply["body"] == "A squash recipe." and not reply.get("details")
    assert SECRET.encode() not in _stored_bytes(home.data_dir)


def test_only_a_paired_phone_can_share_a_screen(home: RemHome):
    with pytest.raises(ValueError):
        home.submit_message({"request_id": "desk-1", "body": "x", "screen": SCREEN})
    assert home.screens == {}


def test_a_screen_lost_to_a_restart_is_admitted(home: RemHome):
    prompts, home.generate = _answer()
    home.submit_message(
        {"request_id": "phone-1", "body": "What am I looking at?", "screen": SCREEN},
        source="phone",
    )
    home.screens.clear()
    home._reply()
    assert SCREEN_GONE in prompts[0] and SECRET not in prompts[0]


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "text",
        {"app": "Chrome"},
        {"app": "Chrome", "text": "  "},
        {"app": "Chrome", "text": "x" * (MAX_TEXT + 1)},
        {"app": "Chrome", "text": "hi", "url": "https://example.com"},
        {"app": 3, "text": "hi"},
    ],
)
def test_unreadable_screens_are_refused(raw: Any):
    with pytest.raises(ValueError):
        clean_screen(raw)


def test_hidden_characters_are_removed_and_the_app_name_is_bounded():
    cleaned = clean_screen({"app": "A" * 200 + "‮", "text": "Pay​ now\x07\nline two"})
    assert cleaned == {"app": "A" * 80, "text": "Pay now\nline two"}
    assert clean_screen({"text": "hi"})["app"] == "an app"


def test_the_phone_sees_support_and_its_screen_goes_to_rem(phone):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    rem = app.desktop.rem
    answer = send("desktop.action", "rem.message", {"body": "What's this?", "screen": SCREEN})
    assert answer["ok"], answer
    assert answer["value"]["screen_context"] is True
    assert rem.calls[-1][1]["screen"] == SCREEN
    refused = send(
        "desktop.action", "rem.message", {"body": "What's this?", "screen": {"text": ""}}
    )
    assert refused["code"] == "invalid"
