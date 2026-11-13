"""A chat model that writes too much never costs the user their task update or reply."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from gofer.ui.rem_home import RemHome, result_details
from gofer.ui.rem_home_models import Decision, TaskUpdate, fit_lengths, split_lead
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe
from tests.unit.test_rem_task_files import ask, latest_run

SENTENCE = "The phone-trust work landed on local dev and every targeted check passed. "
# Like the dropped updates: a summary well over TaskUpdate's 700 characters.
LONG_SUMMARY = SENTENCE * 14


def started(home: RemHome) -> dict[str, Any]:
    ask(home, "start", "Do the follow-up", {"reply": "Starting.", "tasks": [recipe(None)]})
    return latest_run(home, "Check the feature")


def finish(
    home: RemHome, run: dict[str, Any], worker_result: str, replies: list[Any]
) -> tuple[dict[str, Any], list[str]]:
    """Finish [run]; the chat model answers each update request with the next reply."""
    with home.store.connect() as db:
        current = home.store.get(db, "runs", run["id"])
        current["worker_result"] = worker_result
        current["review_verdict"] = {"criteria": [], "feedback": "No repair needed."}
        home.store.put(db, "runs", current)
    prompts: list[str] = []

    def generate(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        prompts.append(prompt)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        assert isinstance(reply, dict)
        return reply

    home.generate = generate
    assert home._finish(run["id"], "succeeded", worker_result, epoch=current["epoch"])
    home.deliver_outcomes()
    message = next(
        m
        for m in home.store.conversation()["messages"]
        if m["id"].startswith(f"finished-{run['id']}")
    )
    return message, prompts


def test_split_lead_stops_after_a_sentence_and_keeps_the_rest():
    lead, rest = split_lead(LONG_SUMMARY, 700)
    assert len(lead) <= 700 and lead.endswith("passed.")
    assert lead + " " + rest == LONG_SUMMARY.strip()


def test_split_lead_without_a_sentence_end_cuts_after_a_word_with_an_ellipsis():
    text = "word " * 300
    lead, rest = split_lead(text, 700)
    assert len(lead) <= 700 and lead.endswith("word…") and rest.startswith("…word")
    assert split_lead("short", 700) == ("short", "")


def test_an_over_long_summary_keeps_its_lead_and_the_rest_opens_details(
    home: RemHome, caplog: pytest.LogCaptureFixture
):
    with caplog.at_level(logging.WARNING, logger="gofer.ui.rem_home"):
        update, prompts = finish(
            home,
            started(home),
            "Done.\n\nEverything landed.",
            [
                {
                    "summary": LONG_SUMMARY,
                    "details": "- Commit `c7892f8b`",
                    "suggestions": ["Update test from dev"],
                }
            ],
        )
    assert len(prompts) == 1, "shortening needs no second model call"
    assert "summary must stay under 700 characters" in prompts[0]
    assert len(update["body"]) <= 700 and update["body"].endswith("passed.")
    assert update["details"].endswith("- Commit `c7892f8b`")
    assert (update["body"] + " " + update["details"]).startswith(LONG_SUMMARY.strip())
    assert update["suggestions"] == ["Update test from dev"]
    assert "couldn't write the update" not in caplog.text


def test_over_long_details_and_suggestions_are_trimmed_not_fatal(home: RemHome):
    update, _ = finish(
        home,
        started(home),
        "Done.",
        [
            {
                "summary": "The fix landed.",
                "details": "- A finding that matters.\n" * 400,
                "suggestions": ["x" * 301, "Show me the diff"],
            }
        ],
    )
    assert update["body"] == "The fix landed."
    assert 0 < len(update["details"]) <= 6000
    assert update["suggestions"] == ["Show me the diff"]


def test_an_otherwise_invalid_update_is_asked_for_once_more(home: RemHome):
    update, prompts = finish(
        home,
        started(home),
        "Done.",
        [
            {"summary": "Landed.", "needs_user": "no"},
            {"summary": "The fix landed on dev.", "details": "- One commit"},
        ],
    )
    assert len(prompts) == 2 and "couldn't be used" in prompts[1]
    assert update["body"] == "The fix landed on dev." and update["details"] == "- One commit"


def test_when_the_model_fails_twice_the_update_reports_the_whole_result(
    home: RemHome, caplog: pytest.LogCaptureFixture
):
    result = (
        "The phone-trust work landed on local dev at c7892f8b.\n\n"
        "**Checks**\n- ruff: passed\n- 762 targeted tests passed\n\n"
        "I'll watch the next release for you."
    )
    with caplog.at_level(logging.WARNING, logger="gofer.ui.rem_home"):
        update, prompts = finish(
            home,
            started(home),
            result,
            [{"summary": "Landed.", "needs_user": "no"}, {"oops": True}],
        )
    assert len(prompts) == 2
    assert update["body"] == "The phone-trust work landed on local dev at c7892f8b."
    # The rest of the worker's report, without the promise nothing will keep.
    assert update["details"] == "**Checks**\n- ruff: passed\n- 762 targeted tests passed"
    assert "couldn't write the update" in caplog.text


def test_a_provider_failure_still_posts_the_plain_update(home: RemHome):
    update, _ = finish(home, started(home), "Done and landed.", [RuntimeError("down")])
    assert update["body"] == "Done and landed." and "details" not in update


def test_result_details_skip_the_shown_opening_even_when_it_was_cut():
    opening = "word " * 100
    result = opening + "\n\nMore."
    body = split_lead(opening, 320)[0]
    details = result_details(result, body)
    assert details.startswith("…") and details.endswith("More.") and len(details) < 200
    assert result_details("Only line.", "Only line.") == ""


def test_an_over_long_chat_reply_keeps_its_lead_and_the_rest_opens_details(home: RemHome):
    reply = "Here is what changed in the release gates and why it matters. " * 80
    ask(home, "long", "Explain the gates", {"reply": reply, "details": "| a | b |"})
    message = next(m for m in home.store.conversation()["messages"] if m["id"] == "reply-long")
    assert len(message["body"]) <= 4000 and message["body"].endswith("matters.")
    assert message["details"].endswith("| a | b |")
    assert "couldn't" not in message["body"].lower()


def test_fit_lengths_leaves_valid_replies_and_non_prose_fields_alone():
    valid = {"reply": "Hi.", "details": "", "files": ["/x"] * 9}
    fitted = fit_lengths(Decision, valid, lead="reply", prose=("details",))
    assert fitted == valid and fitted is not valid
    # Lists of paths aren't prose, so a reply with too many still fails validation.
    with pytest.raises(ValueError):
        Decision.model_validate(fitted)
    assert fit_lengths(TaskUpdate, "not json", lead="summary") == "not json"
    # Details that can't also hold the cut text stay as the model wrote them.
    full = {"summary": LONG_SUMMARY, "details": "d" * 5990}
    fitted = fit_lengths(TaskUpdate, full, lead="summary", prose=("details",))
    assert fitted["details"] == "d" * 5990
    TaskUpdate.model_validate(fitted)
