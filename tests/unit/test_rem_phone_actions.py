from __future__ import annotations

from typing import Any

import pytest

from gofer.ui.rem_home import RemHome
from gofer.ui.rem_phone_actions import clean_phone_actions, phone_action
from tests.unit.test_rem_home import home as home


def test_allowed_actions_keep_only_their_fields():
    assert phone_action({"type": "timer", "seconds": 1200, "label": " Pasta "}) == {
        "type": "timer",
        "seconds": 1200,
        "label": "Pasta",
    }
    assert phone_action({"type": "alarm", "hour": 7, "minute": 5, "days": ["fri", "mon"]}) == {
        "type": "alarm",
        "hour": 7,
        "minute": 5,
        "days": ["mon", "fri"],
    }
    assert phone_action(
        {"type": "calendar_event", "title": "Trip", "start": "2026-11-06", "end": "2026-11-08"}
    ) == {
        "type": "calendar_event",
        "title": "Trip",
        "start": "2026-11-06",
        "end": "2026-11-08",
        "all_day": True,
    }
    assert phone_action({"type": "map", "query": "hardware store", "directions": True})
    assert phone_action({"type": "dial", "number": "+1 (555) 123-4567"})
    assert phone_action({"type": "sms_draft", "body": "Running late"}) == {
        "type": "sms_draft",
        "to": "",
        "body": "Running late",
    }
    assert phone_action({"type": "email_draft", "to": "sam@example.com", "subject": "Hi"})


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "call", "number": "5551234567"},
        {"type": "sms_send", "to": "5551234567", "body": "hi"},
        {"type": "open_app", "package": "com.bank"},
        {"type": "timer", "seconds": 60, "skip_ui": True},
        {"type": "timer", "seconds": 0},
        {"type": "timer", "seconds": True},
        {"type": "timer", "seconds": 86401},
        {"type": "alarm", "hour": 24, "minute": 0},
        {"type": "alarm", "hour": 7, "minute": 0, "days": "mon"},
        {"type": "alarm", "hour": 7, "minute": 0, "days": ["mon", "mon"]},
        {"type": "calendar_event", "title": "X", "start": "next friday"},
        {"type": "calendar_event", "title": "X", "start": "20261106T190000"},
        {
            "type": "calendar_event",
            "title": "X",
            "start": "2026-11-06T19:00:00Z",
            "end": "2026-11-06T18:00:00Z",
        },
        {
            "type": "calendar_event",
            "title": "X",
            "start": "2026-11-06T19:00:00Z",
            "attendees": ["a@b.c"],
        },
        {"type": "dial", "number": "*#06#"},
        {"type": "dial", "number": "555,1234"},
        {"type": "dial", "number": "1-800-FLOWERS"},
        {"type": "sms_draft", "to": "5551234567,5559876543", "body": "hi"},
        {"type": "sms_draft"},
        {"type": "email_draft", "to": "a@b.com,c@d.com", "body": "x"},
        {"type": "email_draft", "to": "Sam <sam@example.com>", "body": "x"},
        {"type": "map", "query": "Bank‮"},
        {"type": "map", "query": "two\nlines"},
        {"seconds": 60},
        "timer",
    ],
)
def test_unsupported_or_unsafe_actions_are_dropped(raw: Any):
    assert phone_action(raw) is None


def test_at_most_three_actions_survive():
    raw = [{"type": "timer", "seconds": 60 * n} for n in range(1, 5)] + [{"type": "call"}]
    assert [a["seconds"] for a in clean_phone_actions(raw)] == [60, 120, 180]
    assert clean_phone_actions("timer") == []


def _answer(actions: list[Any]):
    prompts: list[str] = []

    def generate(prompt, config, cancel):
        prompts.append(prompt)
        return {"reply": "The timer card is ready on your phone.", "phone_actions": actions}

    return prompts, generate


def test_phone_requests_get_checked_cards_tagged_for_that_request(home: RemHome):
    prompts, home.generate = _answer(
        [{"type": "timer", "seconds": 1200}, {"type": "dial", "number": "*#06#"}]
    )
    home.submit_message({"request_id": "phone-1", "body": "20 minute timer"}, source="phone")
    home._reply()
    messages = {m["id"]: m for m in home.store.conversation()["messages"]}
    assert messages["phone-1"]["source"] == "phone"
    reply = messages["reply-phone-1"]
    assert reply["phone_actions"] == [{"type": "timer", "seconds": 1200}]
    assert reply["reply_to"] == "phone-1"
    assert "paired phone" in prompts[0] and "phone_actions:[]" in prompts[0]


def test_desktop_requests_never_carry_phone_actions(home: RemHome):
    prompts, home.generate = _answer([{"type": "timer", "seconds": 1200}])
    home.submit_message({"request_id": "desk-1", "body": "20 minute timer"})
    home._reply()
    reply = next(m for m in home.store.conversation()["messages"] if m["id"] == "reply-desk-1")
    assert "phone_actions" not in reply and "reply_to" not in reply
    assert "paired phone" not in prompts[0] and "phone_actions" not in prompts[0]
    # The local API can't mark a message as the phone's.
    with pytest.raises(ValueError):
        home.submit_message({"request_id": "desk-2", "body": "x", "source": "phone"})
