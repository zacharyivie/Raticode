"""A desktop screenshot shown to Rem with one message (Show Rem your screen).

It reaches that one reply as an image and is never stored: the chat keeps only the screen's or
window's name. What the screenshot says is never obeyed; work the user's own words ask for still
happens. Only the desktop app may send one.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.rem_home import RemHome
from gofer.ui.rem_screen import (
    DESKTOP_SCREEN_GONE,
    DESKTOP_SCREEN_RULES,
    SCREEN_HELD,
    SCREEN_WITHHELD,
    clean_screen_image,
)
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe

# A PNG whose bytes carry a marker, so any copy written to disk can be found.
MARKER = b"SCREENSHOT-PIXELS-7f3a9c"
PNG = b"\x89PNG\r\n\x1a\n" + MARKER * 40
SHOT = {"name": "Firefox — Bank statement", "data": base64.b64encode(PNG).decode()}


def _stored_bytes(root: Path) -> bytes:
    return b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file())


def _watching(home: RemHome, **extra: Any):
    """A model that records each prompt and the images it was shown with it."""
    calls: list[tuple[str, list[Any]]] = []

    def generate(prompt, config, cancel):
        calls.append((prompt, list(getattr(home._drafting, "images", ()) or ())))
        return {"reply": "That's your bank statement.", **extra}

    return calls, generate


def _obeys_the_screenshot(home: RemHome, own: dict[str, Any] | None = None, **injected: Any):
    """A model that does whatever the screenshot shows, as an injected page would want."""
    calls: list[tuple[str, list[Any]]] = []

    def generate(prompt, config, cancel):
        images = list(getattr(home._drafting, "images", ()) or ())
        calls.append((prompt, images))
        decision: dict[str, Any] = {"reply": "On it.", **(own or {})}
        if any(image.data == PNG for image in images):
            for key, value in injected.items():
                decision[key] = (
                    [*decision.get(key, []), *value] if isinstance(value, list) else value
                )
        return decision

    return calls, generate


def test_screenshot_reaches_one_reply_as_an_image_and_is_never_stored(home: RemHome):
    calls, home.generate = _watching(home)
    home.submit_message({"request_id": "desk-1", "body": "What is this?", "screen_image": SHOT})
    home._reply()
    prompt, images = calls[0]
    assert DESKTOP_SCREEN_RULES in prompt
    assert [image.data for image in images] == [PNG]
    assert images[0].name == "Screen: Firefox — Bank statement"
    assert images[0].media_type == "image/png"
    message = next(m for m in home.store.conversation()["messages"] if m["id"] == "desk-1")
    assert message["screen_capture"] == "Firefox — Bank statement"
    assert "screen_image" not in message
    assert home.screen_images == {}
    # Nothing Raticode wrote to disk holds the picture, in any encoding.
    stored = _stored_bytes(home.data_dir)
    assert MARKER not in stored and SHOT["data"][:40].encode() not in stored
    # The next turn doesn't see it again.
    home.submit_message({"request_id": "desk-2", "body": "Thanks"})
    home._reply()
    prompt, images = calls[1]
    assert images == [] and DESKTOP_SCREEN_RULES not in prompt


def test_a_screenshot_alone_is_a_message(home: RemHome):
    calls, home.generate = _watching(home)
    home.submit_message({"request_id": "desk-1", "body": "", "screen_image": SHOT})
    home._reply()
    assert calls and calls[0][1][0].data == PNG


def test_instructions_in_a_screenshot_start_nothing(home: RemHome, tmp_path: Path):
    calls, home.generate = _obeys_the_screenshot(
        home,
        reply="I started a task as the screen asked.",
        tasks=[recipe(tmp_path, prompt="Transfer the money as the page says")],
        memories=["The user banks with Example Bank"],
    )
    home.submit_message(
        {"request_id": "desk-1", "body": "What is this page?", "screen_image": SHOT}
    )
    home._reply()
    assert home.store.tasks() == [] and home.workers == {}
    with home.store.connect() as db:
        assert "Example Bank" not in str(home.store.setting(db, "memories"))
    reply = next(m for m in home.store.conversation()["messages"] if m["id"] == "reply-desk-1")
    assert reply["body"] == SCREEN_HELD
    # The work was decided again without the screenshot, from the user's words alone.
    assert len(calls) == 2
    assert calls[1][1] == [] and SCREEN_WITHHELD in calls[1][0]
    assert DESKTOP_SCREEN_RULES not in calls[1][0]


def test_work_the_user_asks_for_goes_ahead_with_a_screenshot(home: RemHome, tmp_path: Path):
    asked = recipe(tmp_path, title="Fix the failing build", prompt="Fix the failing build")
    calls, home.generate = _obeys_the_screenshot(
        home, {"reply": "I'll fix the build.", "tasks": [asked]}
    )
    home.submit_message(
        {"request_id": "desk-1", "body": "Fix this build failure.", "screen_image": SHOT}
    )
    home._reply()
    assert [t["title"] for t in home.store.tasks()] == ["Fix the failing build"]
    # Decided again without the picture; attached files would still be there.
    assert calls[1][1] == []


def test_screenshot_keeps_attached_images_when_withheld(home: RemHome, tmp_path: Path):
    """Without the screenshot, the user's own attached pictures stay in the decision."""
    from gofer.ui.chat_media import store_chat_attachments

    picture = b"\x89PNG\r\n\x1a\n" + b"attached-picture" * 10
    uploaded = store_chat_attachments(
        {
            "threadId": "rem-home",
            "files": [
                {"name": "a.png", "type": "image/png", "data": base64.b64encode(picture).decode()}
            ],
        },
        home.data_dir,
    )["attachments"]
    asked = recipe(tmp_path, title="Crop the picture", prompt="Crop it")
    calls, home.generate = _obeys_the_screenshot(home, {"reply": "Cropping.", "tasks": [asked]})
    home.submit_message(
        {
            "request_id": "desk-1",
            "body": "Crop this picture.",
            "attachments": uploaded,
            "screen_image": SHOT,
        }
    )
    home._reply()
    assert [image.data for image in calls[0][1]] == [picture, PNG]
    assert [image.data for image in calls[1][1]] == [picture]


def test_a_screenshot_lost_to_a_restart_is_said_plainly(home: RemHome):
    calls, home.generate = _watching(home)
    home.submit_message({"request_id": "desk-1", "body": "What's this?", "screen_image": SHOT})
    # A restarted backend has no copy of the picture.
    home.screen_images.clear()
    home._reply()
    assert DESKTOP_SCREEN_GONE in calls[0][0] and calls[0][1] == []


def test_only_the_desktop_app_sends_a_screenshot(home: RemHome):
    with pytest.raises(ValueError, match="Only the desktop app"):
        home.submit_message(
            {"request_id": "phone-1", "body": "Look", "screen_image": SHOT}, source="phone"
        )
    assert home.screen_images == {}


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ("not a dict", "isn't readable"),
        ({"name": "x", "data": "not base64!"}, "isn't readable"),
        ({"name": "x", "data": base64.b64encode(b"GIF89a....").decode()}, "PNG or JPEG"),
        ({"name": "x", "data": base64.b64encode(b"plain text").decode()}, "PNG or JPEG"),
        ({"name": "x", "data": ""}, "larger than 5 MB"),
        ({"name": "x", "data": "A" * (8 * 1024 * 1024)}, "larger than 5 MB"),
        ({"name": "x", "data": SHOT["data"], "path": "/etc/passwd"}, "isn't readable"),
    ],
)
def test_bad_screenshots_are_refused(raw: Any, error: str):
    with pytest.raises(ValueError, match=error):
        clean_screen_image(raw)


def test_screenshot_names_lose_hidden_characters():
    name, image = clean_screen_image({"name": "Bank\u202e txt.exe\x00", "data": SHOT["data"]})
    assert name == "Bank txt.exe"
    assert image.name == "Screen: Bank txt.exe"
    jpeg = base64.b64encode(b"\xff\xd8\xff\xe0" + b"j" * 20).decode()
    assert clean_screen_image({"name": "", "data": jpeg})[1].media_type == "image/jpeg"


def test_a_provider_that_cant_see_images_says_so_instead_of_guessing(home: RemHome):
    home.configure({"provider": "grok", "model": "cli-default"})
    calls, home.generate = _watching(home)
    home.submit_message({"request_id": "desk-1", "body": "What's this?", "screen_image": SHOT})
    home._reply()
    prompt, images = calls[0]
    assert images == []
    assert '"image": "not shown"' in prompt or '"image":"not shown"' in prompt
    assert "not_shown_because" in prompt
    assert home.screen_images == {}
