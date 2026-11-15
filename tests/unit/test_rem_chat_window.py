"""Rem's chat reads its newest window of messages; earlier ones come a page at a time."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

import gofer.ui.rem_home_store as store_module
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_store import CHAT_WINDOW, RemHomeStore, encode
from tests.unit.test_rem_home import home as home  # noqa: F401 - fixture
from tests.unit.test_rem_home import recipe
from tests.unit.test_rem_home_api import api as api  # noqa: F401 - fixture


def seed(store: RemHomeStore, count: int, *, start: int = 0, **extra: Any) -> None:
    """Store ``count`` chat messages m{start}..., alternating user and Rem, oldest first."""
    rows = [
        (
            f"m{index}",
            encode(
                {
                    "id": f"m{index}",
                    "role": "user" if index % 2 == 0 else "rem",
                    "body": f"Message {index}",
                    "created_at": f"2026-11-01T09:{index // 60 % 60:02d}:{index % 60:02d}+00:00",
                    **extra,
                }
            ),
        )
        for index in range(start, start + count)
    ]
    with store.connect() as db:
        db.executemany("INSERT INTO messages(id, body) VALUES(?,?)", rows)


class CountingJson:
    """The store's ``json`` module, counting how many stored records it parses."""

    def __init__(self) -> None:
        self.parsed = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(json, name)

    def loads(self, *args: Any, **kwargs: Any) -> Any:
        self.parsed += 1
        return json.loads(*args, **kwargs)


def ids(messages: list[dict[str, Any]]) -> list[str]:
    return [message["id"] for message in messages]


def test_a_poll_at_10000_messages_reads_only_the_newest_window(api, monkeypatch):
    request, service = api
    seed(service.store, 10_000)
    counter = CountingJson()
    monkeypatch.setattr(store_module, "json", counter)

    polled = request("GET", "/api/rem/conversation").json()

    assert ids(polled["messages"]) == [f"m{i}" for i in range(10_000 - CHAT_WINDOW, 10_000)]
    assert polled["earlier"] == 10_000 - CHAT_WINDOW
    # The window, plus a few settings rows; never the 9,800 messages before it.
    assert counter.parsed <= CHAT_WINDOW + 10
    # A poll that changes nothing reads the same window and answers "unchanged".
    counter.parsed = 0
    again = request("GET", "/api/rem/conversation", headers={"X-Gofer-Known": polled["version"]})
    assert again.json()["unchanged"] is True
    assert counter.parsed <= CHAT_WINDOW + 10


def test_the_version_follows_the_window(api):
    request, service = api
    seed(service.store, 300)
    first = request("GET", "/api/rem/conversation").json()
    assert first["earlier"] == 100

    def poll(version: str) -> dict[str, Any]:
        answer = request("GET", "/api/rem/conversation", headers={"X-Gofer-Known": version})
        return cast(dict[str, Any], answer.json())

    # A message in the window changes: the version changes and the new text is sent.
    with service.store.connect() as db:
        db.execute("UPDATE messages SET body=json_set(body, '$.body', 'Edited') WHERE id='m100'")
    changed = poll(first["version"])
    assert "unchanged" not in changed and changed["version"] != first["version"]
    assert changed["messages"][0]["body"] == "Edited"

    # The version covers only what is sent: a change before the window isn't polled for.
    with service.store.connect() as db:
        db.execute("UPDATE messages SET body=json_set(body, '$.body', 'Edited') WHERE id='m5'")
    assert poll(changed["version"])["unchanged"] is True

    # A new message moves the window on by one.
    seed(service.store, 1, start=300)
    moved = poll(changed["version"])
    assert moved["earlier"] == 101 and ids(moved["messages"])[-1] == "m300"
    assert ids(moved["messages"])[0] == "m101"


def test_history_pages_back_to_the_first_message(api):
    request, service = api
    seed(service.store, 250)
    window = request("GET", "/api/rem/conversation").json()
    assert window["earlier"] == 50 and window["messages"][0]["id"] == "m50"

    def history(query: str) -> dict[str, Any]:
        answer = request("GET", f"/api/rem/history?{query}")
        assert answer.status == 200, answer.json()
        return cast(dict[str, Any], answer.json())

    page = history("before=m50")
    assert ids(page["messages"]) == [f"m{i}" for i in range(10, 50)] and page["earlier"] == 10
    assert page["tickets"] == {}
    last = history("before=m10")
    assert ids(last["messages"]) == [f"m{i}" for i in range(10)] and last["earlier"] == 0
    assert history("before=m0") == {"messages": [], "earlier": 0, "tickets": {}}
    assert ids(history("before=m50&limit=5")["messages"]) == [f"m{i}" for i in range(45, 50)]
    # A search result far back: the page reaches it and the messages that led up to it.
    found = history("before=m50&through=m30&limit=5")
    assert ids(found["messages"])[0] == "m20" and found["earlier"] == 20
    # Never less than a normal page.
    assert ids(history("before=m50&through=m45")["messages"])[0] == "m10"

    assert request("GET", "/api/rem/history?before=gone").status == 404
    assert request("GET", "/api/rem/history?before=m50&through=gone").status == 404
    assert request("GET", "/api/rem/history").status == 400
    assert request("GET", "/api/rem/history?before=m50&limit=x").status == 400
    assert request("GET", "/api/rem/history?before=m50", authenticated=False).status == 401


def test_file_links_and_ticket_pills_cover_the_window_and_each_page(api, tmp_path):
    request, service = api
    report = str(tmp_path / "old-report.html")
    Path(report).write_text("<p>Report</p>")
    seed(service.store, 1, body=f"Saved {report}", role="rem")
    seed(service.store, 250, start=1)
    window = request("GET", "/api/rem/conversation").json()
    assert all("file_mentions" not in message for message in window["messages"])
    page = request("GET", "/api/rem/history?before=m10").json()
    assert page["messages"][0]["id"] == "m0"
    assert [m["text"] for m in page["messages"][0]["file_mentions"]] == [report]


def test_waiting_messages_and_replies_to_older_messages(api):
    request, service = api
    seed(service.store, 250)
    with service.store.connect() as db:
        db.execute("UPDATE messages SET body=json_set(body, '$.refers_to', 'm0') WHERE id='m249'")
        conversation = service.store.setting(db, "conversation")
        conversation["waiting"] = [
            {"id": "w1", "body": "And this?", "created_at": "2026-11-02T09:00:00+00:00"},
            {"id": "w2", "body": "About that", "refers_to": "m1", "created_at": "t"},
        ]
        service.store.set_setting(db, "conversation", conversation)

    polled = request("GET", "/api/rem/conversation").json()
    # Waiting messages come on top of the window; they don't push stored ones out.
    assert len(polled["messages"]) == CHAT_WINDOW and polled["earlier"] == 50
    assert [(m["id"], m["waiting"]) for m in polled["waiting"]] == [("w1", True), ("w2", True)]
    # A reply quotes a message from before the window, read by its ID.
    assert polled["messages"][-1]["referenced"]["body"] == "Message 0"
    assert polled["waiting"][1]["referenced"]["body"] == "Message 1"

    store = service.store
    assert store.find_message("m0")["body"] == "Message 0"
    assert store.find_message("w1") is None
    assert store.find_message("w1", waiting=True) == {
        "id": "w1",
        "body": "And this?",
        "created_at": "2026-11-02T09:00:00+00:00",
        "role": "user",
        "request_id": "w1",
        "waiting": True,
    }
    assert store.find_message("missing", waiting=True) is None
    assert ids(store.tail(3)) == ["m247", "m248", "m249"]
    assert ids(store.tail(2, roles=("user",))) == ["m246", "m248"]


def test_clear_finds_the_conversation_of_a_long_chat(home: RemHome):
    seed(home.store, 500)
    snapshot = home.store.conversation()
    assert len(snapshot["messages"]) == CHAT_WINDOW
    cleared = home.clear_history(
        {
            "request_id": "clear",
            "conversation_id": snapshot["conversation"]["id"],
            "confirm": True,
        }
    )
    assert cleared["conversation"]["id"] != snapshot["conversation"]["id"]
    assert home.store.conversation() == {
        "conversation": cleared["conversation"],
        "messages": [],
        "earlier": 0,
        "waiting": [],
    }


def test_rems_reply_reads_the_end_of_a_long_chat(home: RemHome, monkeypatch):
    seed(home.store, 10_000)
    prompts: list[str] = []

    def generate(prompt, config, cancel):
        prompts.append(prompt)
        return {"reply": "Here.", "tasks": [], "memories": []}

    home.generate = generate
    home.submit_message({"request_id": "latest", "body": "What did we decide?"})
    counter = CountingJson()
    monkeypatch.setattr(store_module, "json", counter)
    home._reply()

    assert len(prompts) == 1
    assert '"current_request": {"body": "What did we decide?"' in prompts[0]
    assert '"m9999"' in prompts[0] and '"m9000"' not in prompts[0]
    # The request by ID and the last messages; never all 10,000.
    assert counter.parsed < 500
    assert home.store.conversation(2)["messages"][-1]["body"] == "Here."


def test_continue_finds_a_task_whose_card_is_before_the_window(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    home._finish(run["id"], "cancelled", "Stopped the current run.", 0)
    seed(home.store, CHAT_WINDOW + 50, start=0)
    assert task["id"] not in json.dumps(home.store.conversation()["messages"])

    home.generate = lambda prompt, config, cancel: {
        "reply": "New task",
        "tasks": [recipe(tmp_path)],
    }
    home.submit_message({"request_id": "continue", "body": "continue working that."})
    home._reply()
    current = home.store.tasks()
    assert len(current) == 1
    assert current[0]["active_run"]["id"] == run["id"]


@pytest.mark.parametrize("limit", [0, 1, CHAT_WINDOW])
def test_an_empty_chat_has_nothing_earlier(home: RemHome, limit: int):
    snapshot = home.store.conversation(limit)
    assert snapshot["messages"] == [] and snapshot["earlier"] == 0
    assert home.store.tail(limit) == []
    assert home.store.latest_card_task({"anything"}) is None
