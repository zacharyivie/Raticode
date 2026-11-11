from __future__ import annotations

from typing import Any

import pytest

from gofer.ui.rem_home import RemHome
from gofer.ui.rem_phone_actions import clean_phone_actions, missing_cards_note, phone_action
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


def test_missing_cards_are_admitted_in_the_reply(home: RemHome):
    _, home.generate = _answer([{"type": "dial", "number": "*#06#"}])
    home.submit_message({"request_id": "phone-2", "body": "Dial the code"}, source="phone")
    home._reply()
    reply = next(m for m in home.store.conversation()["messages"] if m["id"] == "reply-phone-2")
    assert "phone_actions" not in reply
    assert reply["body"].startswith("The timer card is ready on your phone.\n\nI couldn't make")


def test_missing_cards_note_counts_what_was_kept():
    timer = {"type": "timer", "seconds": 60}
    assert missing_cards_note([], []) == ""
    assert missing_cards_note([timer], [timer]) == ""
    assert missing_cards_note(None, []) == ""
    assert "that phone card" in missing_cards_note([{"type": "call"}], [])
    assert "those phone cards" in missing_cards_note([{"type": "call"}, {"type": "x"}], [])
    assert missing_cards_note([timer, {"type": "call"}], [timer]).endswith(
        "only one is on your phone."
    )
    four = [timer] * 4
    assert missing_cards_note(four, four[:3]).endswith("only 3 are on your phone.")


def test_calls_and_texts_can_name_a_person_instead_of_a_number():
    assert phone_action({"type": "dial", "recipient": " my brother "}) == {
        "type": "dial",
        "recipient": "my brother",
    }
    assert phone_action({"type": "sms_draft", "recipient": "Baby Bro", "body": "You alive?"}) == {
        "type": "sms_draft",
        "to": "",
        "body": "You alive?",
        "recipient": "Baby Bro",
    }
    # A name without a message still opens a draft to that person.
    assert phone_action({"type": "sms_draft", "recipient": "Mom"})


@pytest.mark.parametrize(
    "raw",
    [
        # A number and a name at once, or neither, is ambiguous.
        {"type": "dial", "number": "5551234567", "recipient": "brother"},
        {"type": "dial"},
        {"type": "sms_draft", "to": "5551234567", "recipient": "brother", "body": "hi"},
        # Numbers belong in number/to, never smuggled in as a name.
        {"type": "dial", "recipient": "555 123 4567"},
        {"type": "sms_draft", "recipient": "brother 555", "body": "hi"},
        {"type": "dial", "recipient": "x" * 61},
        {"type": "dial", "recipient": "two\nlines"},
        {"type": "dial", "recipient": "Bro‮"},
        {"type": "dial", "recipient": 7},
        {"type": "email_draft", "recipient": "brother", "body": "x"},
    ],
)
def test_person_names_are_checked(raw: Any):
    assert phone_action(raw) is None


def test_phone_prompt_asks_for_the_users_words_not_a_number(home: RemHome):
    prompts, home.generate = _answer(
        [{"type": "sms_draft", "recipient": "my brother", "body": "You alive?"}]
    )
    home.submit_message({"request_id": "phone-3", "body": "Text my brother"}, source="phone")
    home._reply()
    reply = next(m for m in home.store.conversation()["messages"] if m["id"] == "reply-phone-3")
    assert reply["phone_actions"] == [
        {"type": "sms_draft", "to": "", "body": "You alive?", "recipient": "my brother"}
    ]
    assert "put their words for that person in recipient" in prompts[0]
