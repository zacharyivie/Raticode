"""Starting self-work now versus turning it on.

"Start self-work now" begins work at once, even when self-work is already on, and keeps going
while the user chats until self-work goes off. "Turn on self-work" only flips the switch, so
self-work still waits for 15 minutes without a prompt. Fake providers and isolated data only
(the ``board`` fixture from test_rem_kanban).
"""

from __future__ import annotations

from typing import Any

import pytest

from gofer.ui.headless import agent_request_refusal
from gofer.ui.rem_home import RemHome, self_work_shortcut
from gofer.ui.rem_home_models import SelfWorkRequest
from gofer.ui.rem_kanban import KanbanRefused
from tests.unit.test_cli_rem_api import backend as backend
from tests.unit.test_cli_rem_api import cli
from tests.unit.test_rem_kanban import (
    add,
    api_server,
    at,
    set_options,
    settle,
    tick,
    ticket,
    wait_for,
)
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_pause_resume import reply_to

assert api_server  # a fixture, imported for the HTTP test below


def chat(home: RemHome, request: str, body: str, self_work: dict[str, Any] | None = None) -> str:
    """One chat turn whose model answer has ``self_work`` (none by default); returns the reply.

    The fixture's model comes back afterwards, for the updates finished tasks post.
    """
    before = home.generate
    home.generate = lambda *args: {
        "reply": "Sure.",
        "tasks": [],
        "memories": [],
        "self_work": self_work,
    }
    try:
        return reply_to(home, request, body)
    finally:
        home.generate = before


def starts(home: RemHome, title: str) -> None:
    """Self-work starts the ticket within a few passes of its loop, with the clock held still.

    A thread can take a moment to come free after earlier work ends, so this doesn't count
    on a single pass.
    """
    wait_for(lambda: tick(home) or ticket(home, title)["status"] != "todo")
    assert ticket(home, title)["status"] in {"in_progress", "in_review", "done"}


def notes(home: RemHome) -> list[str]:
    return [n["text"] for n in home.self_work.view()["report"]["notes"]]


@pytest.mark.parametrize(
    "text,action",
    [
        ("Start self work now.", "start_now"),
        ("start self-work", "start_now"),
        ("start your self-work mode", "start_now"),
        ("Begin self-work right away", "start_now"),
        ("please do some self work now", "start_now"),
        ("turn on self-work", "enable"),
        ("enable self-work now", "enable"),
        ("resume self-work", "enable"),
    ],
)
def test_start_and_turn_on_are_read_as_different_requests(text, action):
    assert self_work_shortcut(text) == action


def test_start_self_work_now_while_it_is_already_on_starts_work_without_the_wait(board):
    home, _, _ = board
    add(home, "First")
    at(home, 1)
    reply = chat(home, "go", "Start self work now.")
    # The reply says what happened: it was on, and now it starts without the 15-minute wait.
    assert reply.startswith("Self-work was already on. I'm starting it now"), reply
    assert "starts after 15 minutes" not in reply
    view = home.self_work.view()
    assert view["state"]["started_now_at"] and view["state"]["starts_at"] == ""
    assert view["state"]["detail"].startswith("Starts work on its next check.")
    assert home.self_work.for_prompt()["started_now"] is True
    # One minute after the prompt, far short of 15 quiet minutes, the ticket starts.
    at(home, 2)
    starts(home, "First")
    assert any(n.startswith("Started because you asked") for n in notes(home))
    settle(home, "First")
    # The user keeps chatting: self-work keeps starting work rather than waiting again.
    chat(home, "chat", "Thanks, how is it going?")
    add(home, "Second")
    at(home, 3)
    starts(home, "Second")
    settle(home, "Second")


def test_plain_turn_on_still_waits_for_fifteen_quiet_minutes(board):
    home, _, _ = board
    set_options(home, enabled=False)
    add(home, "Waits")
    at(home, 1)
    reply = chat(home, "on", "turn on self-work")
    assert reply.startswith("Self-work is on now.") and "15 minutes without a prompt" in reply
    assert "start self-work now" in reply
    view = home.self_work.view()
    assert view["settings"]["enabled"] is True and view["state"]["started_now_at"] == ""
    assert view["state"]["starts_at"]
    at(home, 10)
    tick(home)
    assert ticket(home, "Waits")["status"] == "todo"
    at(home, 16)
    starts(home, "Waits")
    settle(home, "Waits")


def test_start_now_when_off_turns_it_on_and_turning_it_off_brings_the_wait_back(board):
    home, _, _ = board
    set_options(home, enabled=False)
    add(home, "Now")
    at(home, 1)
    # The model reads a free-form request as start_now; the backend applies it.
    reply = chat(home, "go", "Get going on your own work please", {"action": "start_now"})
    assert reply.startswith("Self-work is on now and starts work on its next check"), reply
    assert home.self_work.view()["settings"]["enabled"] is True
    at(home, 2)
    starts(home, "Now")
    settle(home, "Now")
    # Turned off, starting now ends with it: turned on again, self-work waits as usual.
    chat(home, "off", "turn off self-work")
    assert home.self_work.view()["state"]["started_now_at"] == ""
    chat(home, "on", "turn on self-work")
    add(home, "Later")
    at(home, 3)
    tick(home)
    assert ticket(home, "Later")["status"] == "todo"


def test_start_now_keeps_the_thread_limit(board):
    home, fake, _ = board
    fake.hold.clear()
    set_options(home, max_threads=1)
    add(home, "One")
    add(home, "Two")
    at(home, 1)
    reply = chat(home, "go", "start self-work now")
    assert "up to 1 thread in 1 assigned project" in reply
    at(home, 2)
    wait_for(
        lambda: tick(home) or "in_progress" in {ticket(home, t)["status"] for t in ("One", "Two")}
    )
    for _ in range(3):
        tick(home)
    statuses = sorted(ticket(home, t)["status"] for t in ("One", "Two"))
    assert statuses == ["in_progress", "todo"]
    fake.hold.set()
    settle(home, "One", "Two")


def test_start_now_says_when_it_cannot_start_and_never_claims_it_did(board):
    home, _, _ = board
    add(home, "Nowhere")
    home.kanban.set_participant_projects(
        "user", {"participant_id": "rem", "projects": [], "expected_revision": 1}
    )
    at(home, 1)
    reply = chat(home, "go", "Start self work now.")
    assert "won't start until you choose my projects" in reply
    assert "starts work on its next check" not in reply
    at(home, 2)
    tick(home)
    assert ticket(home, "Nowhere")["status"] == "todo"


def test_side_pane_and_cli_start_now_through_settings(board):
    home, _, _ = board
    set_options(home, enabled=False)
    add(home, "Button")
    at(home, 1)
    revision = home.self_work.view()["settings"]["revision"]
    with pytest.raises(KanbanRefused, match="start_now takes true"):
        home.self_work.update_settings({"start_now": False, "expected_revision": revision})
    with pytest.raises(KanbanRefused, match="start_now takes true"):
        home.self_work.update_settings(
            {"start_now": True, "enabled": False, "expected_revision": revision}
        )
    view = home.self_work.update_settings({"start_now": True, "expected_revision": revision})
    assert view["settings"]["enabled"] is True and view["settings"]["revision"] == revision + 1
    assert view["state"]["started_now_at"]
    # Already on and started: asking again changes nothing and needs no new revision.
    again = home.self_work.update_settings({"start_now": True, "expected_revision": revision + 1})
    assert again["settings"]["revision"] == revision + 1
    at(home, 2)
    starts(home, "Button")
    settle(home, "Button")


def test_start_now_takes_a_stop_condition_like_enable():
    request = SelfWorkRequest.model_validate({"action": "start_now", "done_when": "REM-12 is done"})
    assert request.done_when == "REM-12 is done"


def test_selfwork_start_from_the_cli_starts_now_where_on_only_turns_it_on(backend):
    cli("selfwork", "off")
    on = cli("selfwork", "on")["data"]
    assert on["settings"]["enabled"] is True and on["state"]["started_now_at"] == ""
    started = cli("selfwork", "start")["data"]
    assert started["settings"]["enabled"] is True and started["state"]["started_now_at"]
    assert started["state"]["starts_at"] == ""
    cli("selfwork", "off")
    assert cli("selfwork", "status")["data"]["state"]["started_now_at"] == ""


def test_start_now_over_http_is_the_users_alone(api_server):
    request, service = api_server
    revision = service.self_work.view()["settings"]["revision"]
    service.self_work.update_settings({"enabled": False, "expected_revision": revision})
    revision = service.self_work.view()["settings"]["revision"]
    started = request(
        "PUT", "/api/rem/kanban/settings", body={"start_now": True, "expected_revision": revision}
    )
    assert started.status == 200, started.json()
    assert started.json()["self_work"]["state"]["started_now_at"]
    assert agent_request_refusal("PUT", "/api/rem/kanban/settings")
