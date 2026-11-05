"""Replies to an earlier Rem chat message: the link is kept, shown and given to Rem."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from gofer.ui.rem_home import REPLYING_RULES, RemHome
from gofer.ui.rem_home_store import PREVIEW_LENGTH, reference_preview
from tests.unit.test_device_desktop_control import application as application
from tests.unit.test_device_desktop_control import phone as phone
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe

LONG = "The deploy failed on step three because the cache key changed. " * 12


def _record(**reply: Any):
    prompts: list[str] = []

    def generate(prompt, config, cancel):
        prompts.append(prompt)
        return {"reply": "Understood.", **reply}

    return prompts, generate


def _request(prompt: str) -> dict[str, Any]:
    """The current_request object Rem was given."""
    start = prompt.index('{"at"')
    context = json.loads(prompt[start:].split("\nAdditional recalled context:")[0])
    return cast(dict[str, Any], context["current_request"])


def _message(home: RemHome, identity: str) -> dict[str, Any]:
    return next(m for m in home.store.conversation()["messages"] if m["id"] == identity)


def _open(home: RemHome, *projects: Path) -> None:
    home.configure(
        {
            "provider": "codex",
            "model": "cli-default",
            "projects": [{"root": str(project)} for project in projects],
        }
    )


def _chatter(home: RemHome, count: int) -> None:
    with home.store.connect() as db:
        for index in range(count):
            home.store.message(db, f"Filler message {index}", identity=f"filler-{index}")


def test_a_reply_keeps_its_link_and_both_apps_get_a_preview(home: RemHome):
    with home.store.connect() as db:
        home.store.message(db, "**Deploy** [report](/tmp/r.html) is ready.", identity="rem-1")
    prompts, home.generate = _record()
    home.submit_message({"request_id": "u-1", "body": "Why?", "refers_to": "rem-1"})
    home._reply()
    sent = _message(home, "u-1")
    assert sent["refers_to"] == "rem-1"
    assert sent["referenced"] == {
        "id": "rem-1",
        "role": "rem",
        "body": "Deploy report is ready.",
        "created_at": _message(home, "rem-1")["created_at"],
    }
    # Only the ID is stored; the preview is built from the chat when it is read.
    with home.store.read() as db:
        row = db.execute("SELECT body FROM messages WHERE id='u-1'").fetchone()
    assert "referenced" not in json.loads(row[0])
    # Rem's own reply carries no link.
    assert "refers_to" not in _message(home, "reply-u-1")


def test_rem_reads_the_whole_message_even_far_outside_recent_history(home: RemHome):
    with home.store.connect() as db:
        home.store.message(db, LONG, identity="old-rem")
    _chatter(home, 90)
    prompts, home.generate = _record()
    home.submit_message({"request_id": "u-1", "body": "Fix that", "refers_to": "old-rem"})
    home._reply()
    request = _request(prompts[0])
    assert request["replying_to"]["id"] == "old-rem"
    assert request["replying_to"]["body"] == LONG
    assert REPLYING_RULES in prompts[0]
    # The preview shown in the chat is short.
    preview = _message(home, "u-1")["referenced"]["body"]
    assert len(preview) == PREVIEW_LENGTH and preview.endswith("…")
    # A message that isn't a reply gets neither.
    home.submit_message({"request_id": "u-2", "body": "Thanks"})
    home._reply()
    assert "replying_to" not in _request(prompts[1]) and REPLYING_RULES not in prompts[1]


def test_a_reply_to_any_kind_of_message_works(home: RemHome, tmp_path: Path):
    _open(home, tmp_path)
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    card = f"queued-{task['active_run']['id']}"
    with home.store.connect() as db:
        home.store.message(
            db,
            "Done.",
            identity="update-1",
            kind="task_update",
            work=home.store.work(task["active_run"]),
            files=[{"path": "/tmp/a"}, {"path": "/tmp/b"}],
        )
        home.store.message(db, "", identity="files-1", role="user", attachments=[{"name": "a.png"}])
    prompts, home.generate = _record()
    for index, target in enumerate([card, "update-1", "files-1", "u-0"]):
        if target == "u-0":
            home.submit_message({"request_id": "u-0", "body": "My own words"})
            home._reply()
        home.submit_message({"request_id": f"r-{index}", "body": "About this", "refers_to": target})
        home._reply()
    update = _message(home, "r-1")["referenced"]
    assert update["kind"] == "task_update" and update["files"] == 2
    assert update["title"] == task["title"]
    assert _message(home, "r-0")["referenced"]["title"] == task["title"]
    assert _message(home, "r-2")["referenced"] == {
        "id": "files-1",
        "role": "user",
        "body": "",
        "created_at": _message(home, "files-1")["created_at"],
        "files": 1,
    }
    assert _message(home, "r-3")["referenced"]["body"] == "My own words"
    # The task card's link reaches Rem, so it can follow up on that task.
    assert _request(prompts[1])["replying_to"]["work"]["task_id"] == task["id"]


def test_a_missing_message_is_shown_and_told_to_rem_as_unavailable(home: RemHome):
    prompts, home.generate = _record()
    home.submit_message({"request_id": "u-1", "body": "And this?", "refers_to": "gone"})
    home._reply()
    assert _message(home, "u-1")["referenced"] == {"id": "gone", "unavailable": True}
    assert _request(prompts[0])["replying_to"] == {"id": "gone", "unavailable": True}
    assert _message(home, "reply-u-1")["body"] == "Understood."


def test_a_message_rem_may_no_longer_read_stays_hidden_from_it(home: RemHome, tmp_path: Path):
    _open(home, tmp_path)
    task = home.create_task({"request_id": "create", **recipe(tmp_path, prompt=LONG)})["task"]
    card = f"queued-{task['active_run']['id']}"
    home._finish(task["active_run"]["id"], "cancelled", "Stopped.", 0)
    prompts, home.generate = _record()

    def ask(request: str) -> dict[str, Any]:
        home.submit_message({"request_id": request, "body": "Redo this", "refers_to": card})
        home._reply()
        return cast(dict[str, Any], _request(prompts[-1])["replying_to"])

    assert ask("u-1")["work"]["task_id"] == task["id"]
    # The project was closed in Raticode: the same limits as recall apply.
    _open(home)
    assert ask("u-2") == {"id": card, "unavailable": True}
    _open(home, tmp_path)
    home.edit_task(task["id"], {"request_id": "delete"}, "delete")
    assert ask("u-3") == {"id": card, "unavailable": True}
    # The chat still shows what the message said.
    assert _message(home, "u-3")["referenced"]["title"] == task["title"]


@pytest.mark.parametrize("bad", ["", " ", "x" * 129, 3, {"id": "rem-1"}, "self"])
def test_unusable_links_are_refused(home: RemHome, bad: Any):
    with pytest.raises(ValueError, match="Reply to a message"):
        home.submit_message({"request_id": "self", "body": "Hi", "refers_to": bad})
    assert home.store.conversation()["messages"] == []


def test_replying_continue_to_a_task_continues_that_task(home: RemHome, tmp_path: Path):
    _open(home, tmp_path)
    older = home.create_task({"request_id": "c1", **recipe(tmp_path, title="Older work")})["task"]
    newer = home.create_task({"request_id": "c2", **recipe(tmp_path, title="Newer work")})["task"]
    for task in (older, newer):
        home._finish(task["active_run"]["id"], "cancelled", "Stopped.", 0)
    # The model would continue the newest task; the reply names the older one.
    home.generate = lambda p, c, e: {"reply": "Continuing."}
    home.submit_message(
        {
            "request_id": "u-1",
            "body": "continue",
            "refers_to": f"queued-{older['active_run']['id']}",
        }
    )
    home._reply()
    tasks = {t["id"]: t for t in home.store.tasks()}
    assert tasks[older["id"]]["active_run"] is not None
    assert tasks[newer["id"]]["active_run"] is None


def test_clearing_history_leaves_no_reply_links(home: RemHome):
    with home.store.connect() as db:
        home.store.message(db, "Hello", identity="rem-1")
    home.submit_message({"request_id": "u-1", "body": "Hi", "refers_to": "rem-1"})
    conversation = home.store.conversation()["conversation"]
    home.conversation_cancel.set()
    home.clear_history(
        {"request_id": "reset", "conversation_id": conversation["id"], "confirm": True}
    )
    assert home.store.conversation()["messages"] == []


def test_preview_reads_only_what_a_reply_needs():
    message = {
        "id": "m",
        "role": "rem",
        "body": "# Title\n\n> quoted\n- `item` one\n\n![pic](/x.png)",
        "created_at": "2026-11-03T10:00:00+00:00",
        "details": "secret details",
        "phone_actions": [{"type": "sms_draft", "body": "x"}],
    }
    assert reference_preview(message, "m") == {
        "id": "m",
        "role": "rem",
        "body": "Title quoted item one pic",
        "created_at": "2026-11-03T10:00:00+00:00",
    }
    assert reference_preview(None, "m") == {"id": "m", "unavailable": True}


def test_a_phone_reply_links_its_message_and_sees_the_preview(phone, home):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    with home.store.connect() as db:
        home.store.message(db, LONG, identity="old-rem")
    _chatter(home, 100)
    prompts, home.generate = _record()
    shown = send("desktop.read", "rem.home")["value"]
    assert shown["replies"] is True
    assert "old-rem" not in {m["id"] for m in shown["messages"]}
    answer = send("desktop.action", "rem.message", {"body": "Fix it", "refers_to": "old-rem"})
    assert answer["ok"], answer
    home._reply()
    assert _request(prompts[0])["replying_to"]["body"] == LONG
    messages = {m["id"]: m for m in send("desktop.read", "rem.home")["value"]["messages"]}
    (sent,) = [m for m in messages.values() if m["role"] == "user"]
    assert sent["refers_to"] == "old-rem"
    referenced = sent["referenced"]
    assert referenced["role"] == "rem" and referenced["body"].startswith("The deploy failed")
    assert len(referenced["body"]) == PREVIEW_LENGTH
    # A redelivered request isn't stored twice.
    request = sent["id"]
    assert sum(1 for m in home.store.conversation()["messages"] if m["id"] == request) == 1

    missing = send("desktop.action", "rem.message", {"body": "This?", "refers_to": "gone"})
    assert missing["ok"], missing
    home._reply()
    latest = [
        m for m in send("desktop.read", "rem.home")["value"]["messages"] if m["role"] == "user"
    ]
    assert latest[-1]["referenced"] == {"id": "gone", "unavailable": True}


@pytest.mark.parametrize("bad", ["", 7, ["old-rem"], "x" * 129])
def test_a_phone_cant_send_a_malformed_link(phone, home, bad: Any):
    app, peer, _, send = phone
    app.desktop.enable(peer, True)
    app.desktop.rem = home
    refused = send("desktop.action", "rem.message", {"body": "Hi", "refers_to": bad})
    assert refused["code"] == "invalid"
    assert home.store.conversation()["messages"] == []
