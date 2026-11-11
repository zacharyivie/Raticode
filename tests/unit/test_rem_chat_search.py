"""Searching Rem's chat history on the desktop and from a paired phone.

Search matches words in the whole saved chat without a model. A phone holds only the
newest messages, so it asks the desktop to search and to page back to a result.
Isolated data and fake providers only.
"""

import json
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.rem_chat_search import plain_text, query_terms
from gofer.ui.rem_home_store import LEAD_IN, RemHomeStore
from tests.unit.test_device_desktop_control import alerting_phone
from tests.unit.test_device_desktop_control import application as application
from tests.unit.test_device_desktop_control import home as home
from tests.unit.test_device_desktop_control import phone as phone
from tests.unit.test_rem_kanban import api_server as api_server


def add(store: RemHomeStore, body: str, *, identity: str | None = None, **extra: Any) -> str:
    with store.connect() as db:
        return str(store.message(db, body, identity=identity, **extra)["id"])


def joined(parts: list[dict[str, Any]]) -> str:
    return "".join(p["text"] for p in parts)


def marked(parts: list[dict[str, Any]]) -> list[str]:
    return [p["text"] for p in parts if p["match"]]


@pytest.fixture
def store(tmp_path: Path) -> RemHomeStore:
    return RemHomeStore(tmp_path / "data")


def test_words_match_in_any_case_and_order_newest_first(store):
    add(store, "Please draft the RELEASE notes for Friday.", role="user")
    add(store, "The notes for the release are saved.")
    add(store, "Unrelated chatter about lunch.")
    found = store.search("release NOTES")
    assert found["total"] == 2
    assert [joined(r["snippet"]) for r in found["results"]] == [
        "The notes for the release are saved.",
        "Please draft the RELEASE notes for Friday.",
    ]
    assert marked(found["results"][1]["snippet"]) == ["RELEASE", "notes"]
    assert found["results"][1]["role"] == "user" and found["results"][0]["role"] == "rem"
    # Every word must appear; one missing word means no match.
    assert store.search("release lunch")["total"] == 0


def test_blank_and_oversized_queries(store):
    add(store, "Anything")
    assert store.search("   ") == {
        "query": "",
        "terms": [],
        "total": 0,
        "results": [],
        "next": None,
    }
    assert store.search(None)["total"] == 0
    assert len(query_terms(" ".join(f"w{i}" for i in range(20)))) == 8
    assert len(query_terms("x" * 500)[0]) == 200


def test_task_titles_details_and_file_names_are_searched(store):
    add(
        store,
        "Done.",
        kind="task_update",
        work={"task_id": "t", "run_id": "r", "thread_id": "h", "title": "Audit the Kanban board"},
    )
    add(store, "Here it is.", details="The kanban column was renamed.")
    add(
        store,
        "See attached",
        role="user",
        attachments=[{"id": "a", "name": "Kanban-export.csv", "path": "/secret/x.csv"}],
    )
    found = store.search("kanban")
    assert found["total"] == 3
    where = {r["where"]: r for r in found["results"]}
    assert set(where) == {"task", "details", "file"}
    task = where["task"]
    assert task["kind"] == "task_update"
    assert marked(task["title"]) == ["Kanban"] and joined(task["title"]) == "Audit the Kanban board"
    assert joined(where["file"]["snippet"]) == "Kanban-export.csv"
    # Paths are never searched or returned.
    assert store.search("secret")["total"] == 0


def test_markdown_is_searched_and_shown_as_plain_text(store):
    add(store, "## Plan\n\nSee [the **deploy** guide](/tmp/x.md) and `make deploy`.")
    found = store.search("deploy guide")["results"][0]
    assert joined(found["snippet"]) == "Plan See the deploy guide and make deploy."
    assert marked(found["snippet"]) == ["deploy", "guide", "deploy"]
    assert plain_text("**a**   _b_\n> c") == "a b c"


def test_long_messages_get_a_snippet_around_the_first_match(store):
    words = " ".join(f"word{i}" for i in range(200))
    add(store, f"{words} needle {words}")
    snippet = store.search("NEEDLE")["results"][0]["snippet"]
    text = joined(snippet)
    assert text.startswith("…") and text.endswith("…") and len(text) <= 182
    assert marked(snippet) == ["needle"]
    # The cut falls between words.
    assert text[1:].split(" ")[0].startswith("word")


def test_non_ascii_quotes_and_backslashes_match_exactly(store):
    add(store, 'Café "quoted" C:\\path ÉCOLE 🎉 done')
    assert store.search("café")["total"] == 1
    assert store.search("école")["total"] == 1
    assert marked(store.search("ÉCOLE")["results"][0]["snippet"]) == ["ÉCOLE"]
    assert store.search('"quoted"')["total"] == 1
    assert store.search("C:\\path")["total"] == 1
    assert store.search("🎉")["total"] == 1
    assert store.search("cafe")["total"] == 0


def test_results_page_back_with_a_count_of_all_matches(store):
    for index in range(7):
        add(store, f"Build {index} passed")
    first = store.search("build", limit=3)
    assert first["total"] == 7 and len(first["results"]) == 3
    assert [joined(r["snippet"]) for r in first["results"]] == [
        "Build 6 passed",
        "Build 5 passed",
        "Build 4 passed",
    ]
    second = store.search("build", limit=3, before=first["next"])
    assert [joined(r["snippet"]) for r in second["results"]][0] == "Build 3 passed"
    last = store.search("build", limit=3, before=second["next"])
    assert len(last["results"]) == 1 and last["next"] is None and last["total"] == 7


def test_the_hidden_duplicate_started_notice_is_not_a_result(store):
    work = {"task_id": "t", "run_id": "run9", "thread_id": "h", "title": "Nightly audit"}
    add(store, "Started Nightly audit.", identity="queued-run9", work=work)
    add(store, "I'll run the Nightly audit now.", work=work)
    found = store.search("nightly audit")
    assert found["total"] == 1
    assert found["results"][0]["id"] != "queued-run9"
    assert "queued-run9" not in {m["id"] for m in store.conversation()["messages"]}


def test_search_stays_fast_on_a_long_history(store):
    with store.connect() as db:
        for index in range(6000):
            store.message(db, f"Routine status update number {index} " + "filler " * 40)
        store.message(db, "The rare zebra finding")
    started = time.perf_counter()
    found = store.search("zebra")
    common = store.search("routine", limit=50)
    took = time.perf_counter() - started
    assert found["total"] == 1 and common["total"] == 6000 and len(common["results"]) == 50
    # Both queries over 6,000 messages; a full parse of every row takes several times longer.
    assert took < 2.5, took


def test_a_page_reaches_back_to_a_result_with_what_led_up_to_it(store):
    ids = [add(store, f"Message {i}") for i in range(100)]
    page = store.page(ids[60], through=ids[20])
    assert page["messages"][0]["id"] == ids[20 - LEAD_IN]
    assert page["messages"][-1]["id"] == ids[59]
    assert page["earlier"] == 20 - LEAD_IN
    plain = store.page(ids[60], limit=5)
    assert [m["id"] for m in plain["messages"]] == ids[55:60] and plain["earlier"] == 55
    assert store.page(ids[0])["messages"] == [] and store.page(ids[0])["earlier"] == 0
    with pytest.raises(KeyError):
        store.page("gone")
    with pytest.raises(KeyError):
        store.page(ids[5], through="gone")


def test_a_page_carries_reply_previews_from_outside_it(store):
    first = add(store, "The original question", role="user")
    for i in range(10):
        add(store, f"Filler {i}")
    reply = add(store, "Answering that", refers_to=first)
    last = add(store, "Newest")
    page = store.page(last, limit=1)
    assert [m["id"] for m in page["messages"]] == [reply]
    assert page["messages"][0]["referenced"]["body"] == "The original question"


def test_desktop_chat_searches_through_its_api(api_server):
    request, service = api_server
    with service.store.connect() as db:
        service.store.message(db, "Remember the Zephyr launch", role="user")
        service.store.message(db, "Zephyr is scheduled.")
    found = request("GET", "/api/rem/search?q=zephyr&limit=1").json()
    assert found["total"] == 2 and len(found["results"]) == 1 and found["next"]
    rest = request("GET", f"/api/rem/search?q=zephyr&before={found['next']}").json()
    assert joined(rest["results"][0]["snippet"]) == "Remember the Zephyr launch"
    assert request("GET", "/api/rem/search?q=zephyr&before=x").status == 400


def test_phone_searches_the_whole_history_and_pages_back_to_a_result(phone, home):
    app, peer, context, send = alerting_phone(phone, home)
    store = home.store
    stored = "/home/someone/.local/data/chat-attachments/orchid.csv"
    target = add(
        store,
        "The quarterly Orchid report is attached",
        role="user",
        attachments=[{"id": "f1", "name": "orchid.csv", "path": stored, "size": 3}],
    )
    for i in range(120):
        add(store, f"Later message {i}")
    shown = send("desktop.read", "rem.home")["value"]
    assert shown["search"] is True and shown["earlier"] > 0
    assert target not in {m["id"] for m in shown["messages"]}

    found = send("desktop.read", "rem.search", {"query": "ORCHID report"})["value"]
    assert found["total"] == 1 and found["results"][0]["id"] == target
    assert marked(found["results"][0]["snippet"]) == ["Orchid", "report"]

    oldest = shown["messages"][0]["id"]
    page = send("desktop.read", "rem.history", {"before": oldest, "through": target})["value"]
    ids = [m["id"] for m in page["messages"]]
    assert target in ids and ids[-1] != oldest
    assert page["earlier"] == 0
    # Messages are shaped as rem.home shapes them: file names only, paths stay here.
    opened = next(m for m in page["messages"] if m["id"] == target)
    assert opened["attachments"] == [{"file_id": "f1", "name": "orchid.csv", "size": 3}]
    assert stored not in json.dumps(page) and stored not in json.dumps(found)

    gone = send("desktop.read", "rem.history", {"before": "no-such-message"})
    assert gone["code"] == "not_found"
    assert send("desktop.read", "rem.history", {})["code"] == "invalid"
    assert send("desktop.read", "rem.search", {"query": "x", "before": "1"})["code"] == "invalid"


def test_phone_search_needs_desktop_control(phone, home):
    app, peer, _, send = phone
    app.desktop.rem = home
    add(home.store, "Secret plans")
    assert send("desktop.read", "rem.search", {"query": "secret"})["code"] == "unauthorized"
    app.desktop.enable(peer, True)
    assert send("desktop.read", "rem.search", {"query": "secret"})["value"]["total"] == 1
