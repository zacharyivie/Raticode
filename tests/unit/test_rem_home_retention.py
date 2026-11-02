"""Finished one-time Rem tasks leave task lists, then are removed 30 days after their run."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_models import Recipe
from gofer.ui.rem_home_recall import Recall, RemRecall
from gofer.ui.rem_home_store import RETENTION, RemHomeStore, ran_at, retention
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe, wait_run

FINISHED = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def add(
    store: RemHomeStore,
    title: str,
    *,
    schedule: dict[str, Any] | None = None,
    state: str | None = "succeeded",
    finished: datetime | None = FINISHED,
    **run_fields: Any,
) -> dict[str, Any]:
    """A task with one run in ``state``, its thread, transcript and conversation link."""
    with store.connect() as db:
        task = store.add_task(
            db,
            Recipe.model_validate(
                recipe(title=title, schedule=schedule or {"kind": "once", "timezone": "UTC"})
            ),
        )
        if state is None:
            return task
        run = store.add_run(db, task, f"test:{task['id']}", {})
        if (schedule or {}).get("kind", "once") == "once":
            task["next_occurrence_at"] = None
            store.put(db, "tasks", task)
        run.update(
            state=state,
            finished_at=finished.isoformat() if finished and state != "executing" else None,
            **run_fields,
        )
        store.put(db, "runs", run)
        store.message(db, f"{title} finished.", work=store.work(run), kind="task_update")
        db.execute(
            "INSERT INTO receipts VALUES(?,?,?)",
            (f"receipt-{task['id']}", "x", json.dumps({"task": {"id": task["id"]}})),
        )
    store.append_thread(
        run["thread_id"], {"id": f"{run['id']}-m", "role": "assistant", "body": "Did it"}
    )
    return {**task, "run": run}


def view(store: RemHomeStore, task_id: str) -> dict[str, Any]:
    return next(t for t in store.tasks() if t["id"] == task_id)


def rows(store: RemHomeStore, table: str, column: str, value: str) -> int:
    with store.read() as db:
        return int(
            db.execute(f"SELECT count(*) FROM {table} WHERE {column}=?", (value,)).fetchone()[0]
        )


def test_only_finished_one_time_tasks_leave_the_list(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    done = add(store, "Done once")
    stopped = add(store, "Stopped once", state="cancelled")
    blocked = add(store, "Needs you", state="blocked", needs_user=True)
    dismissed = add(store, "Dismissed", state="failed", acknowledged=True)
    unknown = add(store, "Unsure", state="outcome_unknown")
    running = add(store, "Running", state="executing")
    daily = add(store, "Daily", schedule={"kind": "daily", "timezone": "UTC"})
    waiting = add(store, "Not run yet", state=None)
    later = add(
        store,
        "Later",
        schedule={"kind": "once", "timezone": "UTC", "at": "2030-01-01T00:00:00+00:00"},
        state=None,
    )

    listed = {t["title"]: retention(t) for t in store.tasks()}
    hidden = {title for title, kept in listed.items() if not kept["listed"]}
    assert hidden == {"Done once", "Stopped once", "Dismissed"}
    due = (FINISHED + RETENTION).isoformat()
    assert listed["Done once"] == {
        "listed": False,
        "finished_at": FINISHED.isoformat(),
        "cleanup_at": due,
    }
    # Recurring, scheduled, running and attention tasks never get a cleanup time.
    for task in (blocked, unknown, running, daily, waiting, later):
        assert listed[task["title"]]["cleanup_at"] == ""
    assert {done["id"], stopped["id"], dismissed["id"]}.isdisjoint(
        t["id"] for t in store.tasks() if retention(t)["listed"]
    )


def test_ran_at_is_the_latest_start_or_finish():
    created = "2026-08-01T00:00:00+00:00"
    assert ran_at({"created_at": created}) == created
    finished = {
        "created_at": created,
        "last_run": {
            "created_at": "2026-08-02T00:00:00+00:00",
            "finished_at": "2026-08-03T05:00:00Z",
        },
    }
    assert ran_at(finished) == "2026-08-03T05:00:00+00:00"
    active = {**finished, "active_run": {"created_at": "2026-08-04T00:00:00-04:00"}}
    assert ran_at(active) == "2026-08-04T04:00:00+00:00"


def test_cleanup_waits_exactly_thirty_days_and_keeps_conversation_links(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    done = add(store, "Done once")
    keep = [
        add(store, "Daily", schedule={"kind": "daily", "timezone": "UTC"}),
        add(store, "Needs you", state="blocked"),
        add(store, "Running", state="executing"),
        add(store, "Unsure", state="outcome_unknown"),
    ]
    before = store.conversation()["messages"]

    assert store.cleanup(FINISHED + RETENTION - timedelta(seconds=1)) == []
    assert view(store, done["id"])
    removed = store.cleanup(FINISHED + RETENTION)
    assert removed == [done["run"]["thread_id"]]

    # Conversation messages, including their task and thread links, are unchanged.
    assert store.conversation()["messages"] == before
    link = next(m["work"] for m in before if m.get("work", {}).get("task_id") == done["id"])
    assert link["thread_id"] == done["run"]["thread_id"] and link["title"] == "Done once"
    # Everything else about the task is gone.
    assert done["id"] not in {t["id"] for t in store.tasks()}
    assert rows(store, "tasks", "id", done["id"]) == 0
    assert rows(store, "revisions", "task", done["id"]) == 0
    assert rows(store, "runs", "task", done["id"]) == 0
    assert rows(store, "threads", "id", done["run"]["thread_id"]) == 0
    assert rows(store, "thread_messages", "thread", done["run"]["thread_id"]) == 0
    assert rows(store, "receipts", "id", f"receipt-{done['id']}") == 0
    with store.read() as db:
        leftovers = [
            r[0]
            for r in db.execute("SELECT body FROM events")
            if done["run"]["id"] in r[0] or done["run"]["thread_id"] in r[0]
        ]
        cleaned = [r[0] for r in db.execute("SELECT body FROM events WHERE kind='task.cleaned'")]
    assert leftovers == [] and cleaned == [json.dumps({"task_id": done["id"]})]
    with pytest.raises(KeyError):
        store.thread(done["run"]["thread_id"])

    # Recurring, attention, running and uncertain tasks survive any age.
    assert store.cleanup(FINISHED + timedelta(days=3650)) == []
    for task in keep:
        assert view(store, task["id"])
        assert store.thread(task["run"]["thread_id"])["messages"]


def test_cleanup_skips_undelivered_results_and_busy_work(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    pending = add(store, "Undelivered")
    busy = add(store, "Busy thread")
    draining = add(store, "Draining worker")
    with store.connect() as db:
        store.enqueue_outcome(db, pending["run"], body="Done")
    clock = FINISHED + RETENTION
    assert (
        store.cleanup(
            clock,
            busy_runs=frozenset({draining["run"]["id"]}),
            busy_threads=frozenset({busy["run"]["thread_id"]}),
        )
        == []
    )
    with store.connect() as db:
        db.execute("DELETE FROM outbox")
    assert sorted(store.cleanup(clock)) == sorted(
        t["run"]["thread_id"] for t in (pending, busy, draining)
    )


def test_a_rerun_lists_the_task_again_and_restarts_retention(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    done = add(store, "Done once")
    assert not retention(view(store, done["id"]))["listed"]
    store.mutate_task(done["id"], {"request_id": "again"}, "run")
    again = view(store, done["id"])
    assert retention(again)["listed"] and again["active_run"]
    # Conversation follow-ups keep using the same thread while the task exists.
    assert again["active_run"]["thread_id"] == done["run"]["thread_id"]
    later = FINISHED + timedelta(days=20)
    with store.connect() as db:
        run = store.get(db, "runs", again["active_run"]["id"])
        run.update(state="succeeded", finished_at=later.isoformat())
        store.put(db, "runs", run)
    assert retention(view(store, done["id"]))["cleanup_at"] == (later + RETENTION).isoformat()
    assert store.cleanup(FINISHED + RETENTION) == []
    assert store.cleanup(later + RETENTION) == [done["run"]["thread_id"]]


def test_deleted_one_time_tasks_follow_the_same_rule(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    done = add(store, "Deleted later")
    never = add(store, "Deleted unrun", state=None)
    for task in (done, never):
        store.mutate_task(task["id"], {"request_id": f"delete-{task['id']}"}, "delete")
    store.cleanup(FINISHED + RETENTION)
    assert rows(store, "tasks", "id", done["id"]) == 0
    # A task that never ran has no finished run to count from.
    assert rows(store, "tasks", "id", never["id"]) == 1


def test_recall_keeps_messages_of_removed_tasks(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    done = add(store, "Report the weather")
    assert store.cleanup(FINISHED + RETENTION) == [done["run"]["thread_id"]]
    found = RemRecall(store, {}).history(Recall(queries=["weather"]), "weather")
    assert "Report the weather finished." in [m["body"] for m in found]


def test_rem_cleans_up_hourly_and_removes_saved_thread_files(home: RemHome, tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    created = home.create_task({"request_id": "create", **recipe(project)})["task"]
    run = wait_run(home, created["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert not retention(view(home.store, created["id"]))["listed"]
    thread = run["thread_id"]
    session = (
        home.data_dir
        / "chat-provider-sessions"
        / hashlib.sha256(json.dumps(thread).encode()).hexdigest()
    )
    session.mkdir(parents=True, exist_ok=True)
    assert (home.jobs.directory / thread).exists()

    finished = datetime.fromisoformat(run["finished_at"])
    assert home.cleanup(finished + RETENTION - timedelta(minutes=1)) == []
    # The hourly check already ran during the task, so a tick doesn't repeat it.
    home.tick()
    assert view(home.store, created["id"])
    assert home.cleanup(finished + RETENTION) == [thread]
    assert not (home.jobs.directory / thread).exists()
    assert not session.exists()
    links = [m["work"] for m in home.store.conversation()["messages"] if m.get("work")]
    assert links and all(w["task_id"] == created["id"] and w["thread_id"] == thread for w in links)


def test_the_background_loop_removes_old_one_time_tasks(home: RemHome):
    old = add(home.store, "Old errand")
    recent = add(home.store, "Recent errand", finished=datetime.now(UTC) - timedelta(days=29))
    daily = add(home.store, "Old daily", schedule={"kind": "daily", "timezone": "UTC"})
    home.next_cleanup = 0
    home.tick()
    ids = {t["id"] for t in home.store.tasks()}
    assert old["id"] not in ids and {recent["id"], daily["id"]} <= ids
    # The next check is an hour away.
    stale = add(home.store, "Another old errand")
    home.tick()
    assert stale["id"] in {t["id"] for t in home.store.tasks()}
