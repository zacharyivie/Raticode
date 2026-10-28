from __future__ import annotations

from pathlib import Path

import pytest

from gofer.ui.rem_home_models import Recipe
from gofer.ui.rem_home_recall import Recall, RemRecall, ThreadRead
from gofer.ui.rem_home_store import RemHomeStore, encode


def linked_run(store: RemHomeStore, config=None, project=None):
    with store.connect() as db:
        task = store.add_task(
            db,
            Recipe(
                title="YouTube research",
                type="errand",
                prompt="Investigate transcripts",
                project_dir=project,
            ),
        )
        run = store.add_run(db, task, "test-" + task["id"], config or {})
        task["next_occurrence_at"] = None
        store.put(db, "tasks", task)
        run.update(
            state="succeeded", artifacts=[{"path": "/reports/youtube.html", "sha256": "digest"}]
        )
        store.put(db, "runs", run)
        store.archive_run_threads(db, run)
    return run


def test_recall_finds_older_chat_and_archived_thread_report(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    with store.connect() as db:
        store.message(
            db,
            "Fetch YouTube transcripts from subscribed AI channels daily at 6AM",
            role="user",
            identity="old-goal",
        )
    run = linked_run(store)
    store.append_thread(
        run["thread_id"],
        {
            "id": "report",
            "role": "assistant",
            "kind": "final",
            "body": "Use the transcript API. Report: /reports/youtube.html",
        },
    )
    with store.connect() as db:
        for number in range(80):
            store.message(db, f"Unrelated chat {number}")
    result = RemRecall(store, {}).gather(Recall(), "Develop the YouTube tool we discussed earlier")
    assert any(m["id"] == "old-goal" and "6AM" in m["body"] for m in result["history"])
    assert result["threads"][0]["archived"]
    assert result["threads"][0]["messages"][0]["id"] == "report"
    assert result["threads"][0]["artifacts"][0]["path"] == "/reports/youtube.html"
    assert RemRecall(RemHomeStore(tmp_path), {}).gather(Recall(), "YouTube")["threads"]


def test_thread_pagination_preserves_order_and_omits_thoughts(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    run = linked_run(store)
    for number in range(26):
        store.append_thread(
            run["thread_id"],
            {"id": f"m{number}", "role": "assistant", "body": f"Exchange {number}"},
        )
        store.append_thread(
            run["thread_id"],
            {
                "id": f"t{number}",
                "role": "assistant",
                "kind": "thought",
                "body": "Private reasoning",
            },
        )
    reader = RemRecall(store, {})
    page = reader.read_thread(ThreadRead(thread_id=run["thread_id"]), "")
    assert [m["id"] for m in page["messages"]] == [f"m{i}" for i in range(6, 26)]
    assert page["more_before"] == "m6"
    older = reader.read_thread(ThreadRead(thread_id=run["thread_id"], before_message_id="m6"), "")
    assert [m["id"] for m in older["messages"]] == [f"m{i}" for i in range(6)]
    assert older["more_before"] is None
    assert reader.read_thread(
        ThreadRead(thread_id=run["thread_id"], before_message_id="missing"), ""
    )["unavailable"]


@pytest.mark.parametrize("revoked", ["project", "brain", "deleted", "unlinked"])
def test_recall_excludes_revoked_deleted_and_unlinked_workers(tmp_path: Path, revoked: str):
    store = RemHomeStore(tmp_path)
    source = {
        "projects": [{"root": "/private"}],
        "brain": {"enabled": True, "root": "/brain", "brainIds": ["brain-a"]},
    }
    run = linked_run(store, source, "/private")
    config = {**source}
    with store.connect() as db:
        if revoked == "project":
            config["projects"] = []
        elif revoked == "brain":
            config["brain"] = {"enabled": False}
        elif revoked == "deleted":
            task = store.get(db, "tasks", run["task_id"])
            task["deleted_at"] = "today"
            store.put(db, "tasks", task)
        else:
            db.execute("DELETE FROM messages")
    reader = RemRecall(store, config)
    assert reader.read_thread(ThreadRead(thread_id=run["thread_id"]), "")["unavailable"]
    assert reader.index("YouTube", []) == []
    assert not any(m.get("work") for m in reader.history(Recall(), "YouTube"))
    assert reader.read_thread(ThreadRead(thread_id="arbitrary-path-or-id"), "")["unavailable"]


def test_recall_budget_keeps_relevant_old_goal_over_large_recent_messages(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    with store.connect() as db:
        store.message(
            db, "YouTube subscriptions with AI channels at 6AM", role="user", identity="goal"
        )
        for i in range(80):
            store.message(db, "Unrelated text " * 800, identity=f"filler-{i}")
    result = RemRecall(store, {}).gather(Recall(), "YouTube subscriptions")
    assert any(m["id"] == "goal" for m in result["history"])
    assert sum(len(m["body"]) for m in result["history"]) <= 24000
    assert len(encode(result)) < 30000


def test_recall_search_matches_worker_content_and_can_page_chat(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    run = linked_run(store)
    store.append_thread(
        run["thread_id"],
        {
            "id": "finding",
            "role": "assistant",
            "body": "Use captions-service-v2 for authenticated subscriptions",
        },
    )
    with store.connect() as db:
        for i in range(40):
            store.message(db, f"Context {i}", identity=f"chat-{i}")
    result = RemRecall(store, {}).gather(
        Recall(queries=["captions-service-v2"]), "that earlier thing"
    )
    assert result["threads"][0]["messages"][0]["id"] == "finding"
    history = RemRecall(store, {}).history(Recall(before_message_id="chat-20"), "")
    assert "chat-0" in [m["id"] for m in history]


def test_recall_validates_controller_read_requests():
    with pytest.raises(ValueError):
        Recall.model_validate({"threads": [{"thread_id": "id", "path": "/etc/passwd"}]})
    with pytest.raises(ValueError):
        Recall(queries=["query"] * 4)
