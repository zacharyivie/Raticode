from __future__ import annotations

from pathlib import Path

import pytest

from gofer.ui import rem_objectives
from gofer.ui.rem_home_models import Recipe
from gofer.ui.rem_home_recall import (
    Recall,
    RemRecall,
    ThreadRead,
    merge_recalled,
    remembered_ids,
    send_once,
)
from gofer.ui.rem_home_store import RemHomeStore, encode
from gofer.ui.rem_thread_listing import LINKED_LISTED


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
        thread = store.get(db, "threads", run["thread_id"])
        store.put(db, "threads", {**thread, "archived": True})
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


def test_restart_returns_threads_the_old_completion_policy_archived(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    run = linked_run(store)
    other = linked_run(store)
    with store.connect() as db:
        # The old policy archived finished task threads and stamped archivedAt.
        legacy = store.get(db, "threads", run["thread_id"])
        store.put(db, "threads", {**legacy, "archived": True, "archivedAt": "2026-10-30T00:00:00Z"})
    RemHomeStore(tmp_path)
    with store.read() as db:
        restored = store.get(db, "threads", run["thread_id"])
        untouched = store.get(db, "threads", other["thread_id"])
    assert restored["archived"] is False
    assert "archivedAt" not in restored
    assert untouched["archived"] is True


def test_prompt_lists_the_best_linked_threads_and_the_lookup_and_reads_reach_the_rest(
    tmp_path: Path,
):
    store = RemHomeStore(tmp_path)
    runs = [linked_run(store) for _ in range(LINKED_LISTED + 5)]
    oldest = runs[0]
    with store.connect() as db:
        thread = store.get(db, "threads", oldest["thread_id"])
        store.put(db, "threads", {**thread, "title": "Invoice export audit"})
        # Newer chat, so no task's message is among the latest ones recall always reads.
        for number in range(10):
            store.message(db, f"Unrelated chat {number}")
    reader = RemRecall(store, {})
    result = reader.gather(Recall(), "How did the invoice export go?")
    listed = [t["thread_id"] for t in result["linked_threads"]]
    # The matching thread ranks first however old it is; the rest are the newest.
    assert listed[0] == oldest["thread_id"] and len(listed) == LINKED_LISTED
    assert listed[1:] == [r["thread_id"] for r in reversed(runs[1:])][: LINKED_LISTED - 1]
    assert result["linked_threads_total"] == LINKED_LISTED + 5
    # Every recall read is trimmed the same way, and an unlisted thread reads by ID.
    unlisted = runs[1]["thread_id"]
    assert unlisted not in listed
    paged = reader.gather(Recall(threads=[ThreadRead(thread_id=unlisted)]), "anything")
    assert len(paged["linked_threads"]) == LINKED_LISTED
    assert paged["threads"][0]["thread_id"] == unlisted
    assert "unavailable" not in paged["threads"][0]
    # The threads lookup sees all of them, in brief.
    every = reader.linked_threads()
    assert {t["thread_id"] for t in every} == {r["thread_id"] for r in runs}
    assert set(every[0]) == {
        "thread_id",
        "run_id",
        "task_id",
        "title",
        "project_dir",
        "state",
        "archived",
        "updated_at",
    }
    assert reader.gather(Recall(), "x", include_index=False).keys() == {"history", "threads"}


def self_work_report(store: RemHomeStore, identity: str, titles: list[str]) -> str:
    body = "\n".join(
        [
            "**Self-work report**: 9:00 to 11:00, it stopped.",
            f"{len(titles)} tasks ran.",
            *(f"- Finished: “{title}” (REM-{n}): done" for n, title in enumerate(titles)),
        ]
    )
    with store.connect() as db:
        store.message(
            db, body, identity=identity, kind="self_work_report", self_work_report={"items": []}
        )
    return body


def test_self_work_reports_rank_after_other_matches_and_show_only_their_start(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    titles = [f"Invoice export cleanup step {n} with long detail text" for n in range(60)]
    with store.connect() as db:
        store.message(db, "Make the invoice export faster", role="user", identity="goal")
    # More matching reports than one search keeps, all newer than the real match.
    bodies = {f"report-{n}": self_work_report(store, f"report-{n}", titles) for n in range(10)}
    with store.connect() as db:
        for number in range(10):
            store.message(db, f"Unrelated chat {number}")
    reader = RemRecall(store, {})
    history = reader.history(Recall(), "How is the invoice export going?")
    ids = [m["id"] for m in history]
    assert "goal" in ids
    reports = [m for m in history if m["id"].startswith("report-")]
    # At most two, each cut to its start, and marked so Rem knows to ask for the rest.
    assert 0 < len(reports) <= 2
    for item in reports:
        assert item["self_work_report_cut"] and item["truncated"]
        assert len(item["body"]) == 600
        assert bodies[item["id"]].startswith(item["body"])
    before = sum(len(b) for b in bodies.values())
    assert sum(len(m["body"]) for m in reports) <= 1200 < before
    # The worker's background rereads by ID, so cut reports aren't recorded for it.
    assert "goal" in remembered_ids(history)
    assert not any(i.startswith("report-") for i in remembered_ids(history))
    # Replaying the recorded read, as a delegated worker's background does, stays short.
    replay = reader.history(Recall(message_ids=remembered_ids(history)), "invoice export")
    assert all(m.get("self_work_report_cut") for m in replay if m["id"].startswith("report-"))


def test_self_work_question_and_reads_by_id_or_paging_return_reports_whole(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    titles = [f"Invoice export cleanup step {n} with long detail text" for n in range(60)]
    body = self_work_report(store, "report", titles)
    with store.connect() as db:
        for number in range(10):
            store.message(db, f"Unrelated chat {number}", identity=f"chat-{number}")
    reader = RemRecall(store, {})
    for query, request in [
        ("What did self-work do this morning?", Recall()),
        ("anything", Recall(queries=["selfwork invoice"])),
        ("invoice export", Recall(message_ids=["report"])),
        ("invoice export", Recall(before_message_id="chat-0")),
    ]:
        found = {m["id"]: m for m in reader.history(request, query)}
        assert found["report"]["body"] == body[:6000], (query, request)
        assert "self_work_report_cut" not in found["report"]
    assert reader.referenced("report", "invoice")["body"] == body[:6000]


def test_worker_answer_search_drops_the_objectives_a_self_work_task_carries():
    objectives = {"items": [{"id": "o1", "text": "Reduce token use"}]}
    brief = rem_objectives.brief(objectives)
    assert brief.startswith(rem_objectives.BRIEF_LEAD)
    prompt = f"Speed up the invoice export\n\n{brief}\nWork on one ticket."
    assert rem_objectives.without_brief(prompt) == "Speed up the invoice export"
    assert rem_objectives.without_brief("Plain prompt") == "Plain prompt"


def test_send_once_leaves_out_what_the_prompt_shows_and_keeps_longer_copies():
    shown = {"a": 5, "b": 2}
    threads: set[tuple[str, tuple[str, ...]]] = set()
    page = {"thread_id": "t", "messages": [{"id": "x", "body": "hi"}]}
    recalled = {
        "history": [{"id": "a", "body": "12345"}, {"id": "b", "body": "longer"}],
        "threads": [page],
        "linked_threads": [{"thread_id": "t"}],
    }
    first = send_once(recalled, shown, threads)
    # The history part cut b short, so the whole copy still comes through.
    assert [m["id"] for m in first["history"]] == ["b"]
    assert first["threads"] == [page] and first["linked_threads"] == [{"thread_id": "t"}]
    again = send_once(recalled, shown, threads)
    assert again["history"] == [] and again["threads"] == []
    assert [m["id"] for m in recalled["history"]] == ["a", "b"]


def test_merge_recalled_joins_thread_pages_in_order_and_keeps_longest_messages():
    newer = {
        "thread_id": "t",
        "messages": [{"id": "m3", "body": "c"}, {"id": "m4", "body": "d"}],
        "more_before": "m3",
    }
    older = {
        "thread_id": "t",
        "messages": [{"id": "m1", "body": "a"}, {"id": "m3", "body": "c, longer"}],
        "more_before": None,
    }
    entries = [
        {"history": [{"id": "s", "body": "x"}, {"id": "n", "body": "new"}], "threads": [newer]},
        {"history": [{"id": "n", "body": "newest"}, {"id": "o", "body": "old"}]},
        {"history": [{"id": "n", "body": "new"}], "threads": [older]},
    ]
    merged = merge_recalled(entries, frozenset({"s"}), {"m1": 1, "m3": 3, "m4": 4})
    assert [[m["id"] for m in e["history"]] for e in merged] == [["n"], ["o"]]
    assert merged[0]["history"][0]["body"] == "newest"
    thread = merged[0]["threads"][0]
    assert [m["id"] for m in thread["messages"]] == ["m1", "m3", "m4"]
    assert thread["messages"][1]["body"] == "c, longer" and thread["more_before"] is None
    assert merged[1]["threads"] == []
