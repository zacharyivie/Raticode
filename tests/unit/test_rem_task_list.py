"""Rem's polled task list is built again only when something it shows changed."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.rem_home_store import RemHomeStore
from gofer.ui.rem_self_work_windows import SCHEDULE_KEY
from gofer.ui.rem_task_list import CLOCK_LABELS_FOR, TaskList, next_due
from tests.unit.test_rem_home_api import api  # noqa: F401 - the shared API fixture

TASKS = 300
RUNS_PER_TASK = 3


def _count_builds(store: RemHomeStore, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the ID of every task view the store builds."""
    built: list[str] = []
    view = store.task_view

    def counted(db: Any, task: dict[str, Any], *, summary: bool = False) -> dict[str, Any]:
        built.append(task["id"])
        return view(db, task, summary=summary)

    monkeypatch.setattr(store, "task_view", counted)
    return built


def _fill(store: RemHomeStore, task: dict[str, Any], run: dict[str, Any]) -> None:
    """Copy one real task and its run into a large store, in one transaction."""
    with store.connect() as db:
        for number in range(TASKS):
            copy = {**task, "id": f"bulk-{number}", "prompt": f"Bulk work {number} " + "x" * 2000}
            store.put(db, "tasks", copy)
            for attempt in range(RUNS_PER_TASK):
                store.put(
                    db,
                    "runs",
                    {
                        **run,
                        "id": f"bulk-{number}-run-{attempt}",
                        "task_id": copy["id"],
                        "occurrence": f"bulk-{number}:{attempt}",
                        "state": "succeeded",
                        "worker_traces": [{"type": "tool", "output": "y" * 5000}],
                    },
                )


def test_a_large_task_list_is_built_once_while_nothing_changes(api, monkeypatch):  # noqa: F811
    request, service = api
    created = request(
        "POST",
        "/api/rem/tasks",
        body={
            "request_id": "bulk-seed",
            "title": "Check notes",
            "type": "errand",
            "prompt": "Check the notes folder",
            "schedule": {"kind": "daily", "local_time": "09:00", "timezone": "UTC"},
        },
    ).json()["task"]
    run = request(
        "POST", f"/api/rem/tasks/{created['id']}/run", body={"request_id": "bulk-run"}
    ).json()["run"]
    with service.store.read() as db:
        task_body = service.store.get(db, "tasks", created["id"])
        run_body = service.store.get(db, "runs", run["id"])
    _fill(service.store, task_body, run_body)
    built = _count_builds(service.store, monkeypatch)

    listed = request("GET", "/api/rem/tasks").json()
    assert len(listed["tasks"]) == TASKS + 1
    assert len(built) == TASKS + 1
    # List views leave every prompt out; the task sheet reads one task at a time.
    assert not any("prompt" in task for task in listed["tasks"])
    assert "Bulk work" not in json.dumps(listed)

    # Polls with the version, and without it, answer from the same build.
    for _ in range(20):
        assert request("GET", f"/api/rem/tasks?known={listed['version']}").json() == {
            "unchanged": True,
            "version": listed["version"],
        }
    again = request("GET", "/api/rem/tasks").json()
    assert again == listed
    assert len(built) == TASKS + 1, "no task was built again"

    # One task, with its prompt, reads only that task.
    one = request("GET", "/api/rem/tasks/bulk-7").json()
    assert one["task"]["prompt"].startswith("Bulk work 7 ")
    assert one["task"]["last_run"]["id"] == "bulk-7-run-2"
    assert "worker_traces" not in one["task"]["last_run"]
    assert built[-1] == "bulk-7" and len(built) == TASKS + 2
    assert request("GET", "/api/rem/tasks/no-such-task").status == 404

    # A write to a run is counted by the trigger, so the next poll builds again.
    service._update(run["id"], phase_label="Second pass")
    changed = request("GET", f"/api/rem/tasks?known={listed['version']}").json()
    assert changed["version"] != listed["version"]
    assert len(built) == 2 * (TASKS + 1) + 1
    mine = next(task for task in changed["tasks"] if task["id"] == created["id"])
    assert mine["active_run"]["phase_label"] == "Second pass"

    # A deleted task is gone from both reads.
    deleted = request("DELETE", "/api/rem/tasks/bulk-3?request_id=delete-bulk-3", body=None)
    assert deleted.status == 200, deleted.json()
    assert request("GET", "/api/rem/tasks/bulk-3").status == 404
    assert "bulk-3" not in {task["id"] for task in request("GET", "/api/rem/tasks").json()["tasks"]}


def test_triggers_count_every_write_task_lists_read(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    start = store.change_counts()
    with store.connect() as db:
        store.put(db, "tasks", {"id": "t1", "title": "One"})
    with store.connect() as db:
        # Writes that skip put() are counted too.
        db.execute("UPDATE tasks SET body=json_set(body,'$.title','Two') WHERE id='t1'")
        db.execute(
            "INSERT INTO runs VALUES('r1','t1','o1','queued',?)",
            (json.dumps({"id": "r1", "task_id": "t1"}),),
        )
        db.execute("DELETE FROM runs WHERE id='r1'")
    after_tasks = store.change_counts()
    assert after_tasks["tasks"] == start["tasks"] + 4
    assert after_tasks["tickets"] == start["tickets"]

    # Chat state lives in settings too and changes all the time; it isn't counted.
    with store.connect() as db:
        store.set_setting(db, "conversation", {"id": "c", "turn_state": "replying"})
    assert store.change_counts() == after_tasks
    # The self-work window schedule decides "after self-work ends" waits, so it is.
    with store.connect() as db:
        store.set_setting(db, SCHEDULE_KEY, {"windows": [], "history": []})
    assert store.change_counts()["tasks"] == after_tasks["tasks"] + 1

    with store.connect() as db:
        db.execute("INSERT INTO kanban_tickets VALUES('k1','todo','{}')")
    assert store.change_counts()["tickets"] == after_tasks["tickets"] + 1

    # Opening the store again keeps its count and adds no second set of triggers.
    counted = store.change_counts()
    reopened = RemHomeStore(tmp_path)
    assert reopened.change_counts() == counted
    with reopened.connect() as db:
        reopened.put(db, "tasks", {"id": "t2", "title": "Three"})
    assert reopened.change_counts()["tasks"] == counted["tasks"] + 1


class _Board:
    """A board whose ticket revision and lookups the test controls."""

    def __init__(self) -> None:
        self.revision = (0, 0)
        self.resolved = 0

    def ticket_revision(self, *, organizations: bool) -> tuple[int, int]:
        return self.revision

    def resolve(self, keys: list[str]) -> dict[str, Any]:
        self.resolved += 1
        return {"REM-1": {"key": "REM-1", "status": f"todo-{self.revision[0]}"}}


def test_tickets_and_objectives_change_the_version_without_building_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = RemHomeStore(tmp_path)
    with store.connect() as db:
        store.put(db, "tasks", {"id": "t1", "title": "One", "schedule": {"kind": "once"}})
    built = _count_builds(store, monkeypatch)
    board = _Board()
    objectives = {"revision": 1, "items": []}
    listed = TaskList(store, board, lambda: dict(objectives))

    first, version = listed.view()
    assert len(built) == 1 and board.resolved == 1
    assert listed.view()[1] == version and len(built) == 1 and board.resolved == 1

    board.revision = (1, 0)
    second, changed = listed.view()
    assert changed != version and second["tickets"]["REM-1"]["status"] == "todo-1"
    assert len(built) == 1 and board.resolved == 2

    objectives.update(revision=2, items=[{"id": "o1", "text": "Faster lists"}])
    third, newest = listed.view()
    assert newest != changed and third["objectives"]["items"][0]["text"] == "Faster lists"
    assert len(built) == 1


def test_a_time_based_wait_coming_due_builds_the_list_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = RemHomeStore(tmp_path)
    clock = datetime(2026, 11, 17, 12, tzinfo=UTC)
    with store.connect() as db:
        store.put(
            db,
            "tasks",
            {
                "id": "daily",
                "title": "Daily",
                "enabled": True,
                "schedule": {"kind": "daily"},
                "next_occurrence_at": (clock + timedelta(minutes=10)).isoformat(),
            },
        )
    built = _count_builds(store, monkeypatch)
    now = [clock]
    listed = TaskList(store, _Board(), lambda: {"revision": 0}, clock=lambda: now[0])
    listed.tasks()
    now[0] = clock + timedelta(minutes=9)
    listed.tasks()
    assert len(built) == 1, "nothing came due yet"
    now[0] = clock + timedelta(minutes=10)
    listed.tasks()
    assert len(built) == 2, "the scheduled occurrence came due"


def test_a_queued_run_past_its_wait_is_built_once_until_a_write(tmp_path: Path):
    """A retry, usage-limit wait or woken sleeper can sit queued past its not_before while
    every worker is busy. That moment is behind the build, so polls reuse it."""
    store = RemHomeStore(tmp_path)
    clock = datetime(2026, 11, 17, 12, tzinfo=UTC)
    with store.connect() as db:
        store.put(db, "tasks", {"id": "t1", "title": "Retry", "schedule": {"kind": "once"}})
        store.put(
            db,
            "runs",
            {
                "id": "r1",
                "task_id": "t1",
                "occurrence": "t1:once",
                "state": "queued",
                "not_before": (clock - timedelta(minutes=5)).isoformat(),
                "sleep": {"until": (clock - timedelta(minutes=5)).isoformat()},
            },
        )
    now = [clock]
    listed = TaskList(store, _Board(), lambda: {"revision": 0}, clock=lambda: now[0])
    first = listed.tasks()
    assert first.tasks[0]["active_run"]["id"] == "r1", "the queued run is shown"
    assert first.due is None
    for minutes in range(1, 6):
        now[0] = clock + timedelta(minutes=minutes)
        assert listed.tasks() is first
    assert listed.builds == 1, "a wait already past doesn't build the list on every poll"

    # The write that starts the run is counted, so the next poll builds again.
    with store.connect() as db:
        run = store.get(db, "runs", "r1")
        store.put(db, "runs", {**run, "state": "executing"})
    again = listed.tasks()
    assert listed.builds == 2
    assert again.tasks[0]["active_run"]["state"] == "executing"


def test_next_due_names_the_first_moment_a_list_reads_differently():
    clock = datetime(2026, 11, 17, 12, tzinfo=UTC)
    soon, later = clock + timedelta(seconds=30), clock + timedelta(hours=2)
    assert next_due([{"id": "plain", "schedule": {"kind": "once"}}], clock) is None
    # A paused task's next occurrence doesn't start anything.
    assert next_due([{"enabled": False, "next_occurrence_at": soon.isoformat()}], clock) is None
    assert next_due([{"active_run": {"not_before": soon.isoformat()}}], clock) == soon
    assert next_due([{"active_run": {"sleep": {"until": later.isoformat()}}}], clock) == later
    # Moments at or before the build's clock are behind it: only a write changes the list.
    past = clock - timedelta(minutes=1)
    assert next_due([{"active_run": {"not_before": past.isoformat()}}], clock) is None
    assert next_due([{"active_run": {"not_before": clock.isoformat()}}], clock) is None
    assert (
        next_due(
            [
                {"next_occurrence_at": past.isoformat()},
                {"active_run": {"not_before": later.isoformat()}},
            ],
            clock,
        )
        == later
    )

    def waiting(at: datetime, kind: str = "time") -> dict[str, Any]:
        return {
            "schedule": {"kind": "once", "at": at.isoformat()},
            "dependency": {"prerequisites": [{"kind": kind, "state": "waiting"}]},
        }

    # A wait for a time comes due then, and its "today" label is read again each minute.
    assert next_due([waiting(soon)], clock) == soon
    assert next_due([waiting(later)], clock) == clock + CLOCK_LABELS_FOR
    assert next_due([waiting(later, "self_work")], clock) == clock + CLOCK_LABELS_FOR
    # A finished workflow draft's costs come from files no trigger counts.
    drafted = {"workflow_draft_result": {"status": "ready", "task_cost": {}}}
    assert next_due([drafted], clock) == clock + CLOCK_LABELS_FOR
