"""A chat reply that changes many tasks: bad task IDs, big batches and self-work's limit.

On 2026-11-27 the user asked Rem to resume three blocked research tasks. The model kept the
first 28 characters of one task's ID and invented the rest, its one repair had only "Rem
proposed a change to an unknown task" to go on, and the whole reply failed. An hour earlier
a request to resume eight tasks resumed five, the most one reply could hold. These tests run
the real RemHome with a scripted model; no provider is called.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gofer.ui.rem_home import RemHome, queue_user_message
from gofer.ui.rem_self_work import yields_worker
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe


def blocked_tasks(
    home: RemHome, tmp_path: Path, count: int, *, self_work: bool, group: str = "task"
) -> list[str]:
    """[count] tasks whose runs stopped blocked, started by self-work or not; their IDs.

    Each call in one test needs its own [group]: it names the request IDs, which never
    hold the temp path, since a long one would pass the 128 characters a request ID allows.
    """
    ids: list[str] = []
    for index in range(count):
        project = tmp_path / f"project-{index}"
        task = home.create_task(
            {"request_id": f"create-{group}-{index}", **recipe(project, title=f"Research {index}")}
        )["task"]
        with home.store.connect() as db:
            run = home.store.get(db, "runs", task["active_run"]["id"])
            run.update(state="blocked", blocked_phase="executing")
            if self_work:
                run["self_work"] = {"kind": "research", "target": str(project)}
            home.store.put(db, "runs", run)
        ids.append(task["id"])
    return ids


def follow_up(task_id: str) -> dict[str, Any]:
    return {"task_id": task_id, "action": "follow_up", "message": "Resume and finish it."}


def run_of_task(home: RemHome, task_id: str) -> dict[str, Any]:
    task = next(t for t in home.store.tasks() if t["id"] == task_id)
    return dict(task.get("active_run") or task["last_run"])


def reply_body(home: RemHome, request_id: str) -> str:
    return str(
        next(m for m in home.store.conversation()["messages"] if m["id"] == f"reply-{request_id}")[
            "body"
        ]
    )


def test_a_mistyped_task_id_is_retried_with_real_ids_then_only_that_change_is_left_out(
    home: RemHome, tmp_path: Path
):
    first, second, third = blocked_tasks(home, tmp_path, 3, self_work=True)
    # The 2026-11-27 mistake: the ID's start is right, its last 12 characters invented.
    mistyped = third[:-12] + "cbdafa02beef"
    decisions = [
        {
            "reply": "Resuming the three blocked research tasks now.",
            "controls": [follow_up(first), follow_up(second), follow_up(mistyped)],
        }
    ] * 2
    prompts: list[str] = []

    def generate(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        prompts.append(prompt)
        return decisions[len(prompts) - 1]

    home.generate = generate
    home.submit_message({"request_id": "resume", "body": "Resume the other three"})
    home._reply()

    # One retry, told which control was wrong, the real ID it most likely meant and every
    # ID it may use.
    assert len(prompts) == 2
    repair = prompts[1][len(prompts[0]) :]
    assert f"task_id {mistyped} is not the id of any task" in repair
    details = json.loads(repair.split("Validation details:\n", 1)[1].split("\n", 1)[0])
    assert details[0]["loc"] == ["controls", 2]
    assert details[0]["did_you_mean"][0] == {"id": third, "title": "Research 2"}
    assert "Task IDs you can use in controls" in repair and first in repair
    # The retry made the same mistake: that one change is left out, the rest goes ahead.
    body = reply_body(home, "resume")
    assert not body.startswith("I couldn’t finish that request")
    assert body == (
        "Resuming the three blocked research tasks now.\n\nI couldn't apply one task "
        f"change, so I left it out: the follow-up for task {mistyped} named a task that "
        "isn't in your list. Everything else in this reply went ahead."
    )
    assert run_of_task(home, first)["state"] == "queued"
    assert run_of_task(home, second)["state"] == "queued"
    assert run_of_task(home, third)["state"] == "blocked"


def test_a_retry_that_fixes_the_task_id_applies_every_change(home: RemHome, tmp_path: Path):
    first, second = blocked_tasks(home, tmp_path, 2, self_work=False)
    answers = iter(
        [
            {"reply": "Resuming both.", "controls": [follow_up(first), follow_up("made-up")]},
            {"reply": "Resuming both.", "controls": [follow_up(first), follow_up(second)]},
        ]
    )
    home.generate = lambda *args: next(answers)
    home.submit_message({"request_id": "both", "body": "Resume both"})
    home._reply()

    assert reply_body(home, "both") == "Resuming both."
    assert {run_of_task(home, t)["state"] for t in (first, second)} == {"queued"}


def test_a_malformed_change_after_the_retry_is_left_out_not_the_whole_reply(
    home: RemHome, tmp_path: Path
):
    first, second = blocked_tasks(home, tmp_path, 2, self_work=False)
    bad = {"task_id": second, "action": "follow_up", "message": ""}
    home.generate = lambda *args: {
        "reply": "Resuming both.",
        "controls": [follow_up(first), bad],
    }
    home.submit_message({"request_id": "both", "body": "Resume both"})
    home._reply()

    body = reply_body(home, "both")
    assert body.startswith("Resuming both.\n\nI couldn't apply one task change")
    assert f"a follow-up for task {second} wasn't complete" in body
    assert run_of_task(home, first)["state"] == "queued"
    assert run_of_task(home, second)["state"] == "blocked"


def test_the_user_resuming_eight_self_work_tasks_starts_all_eight_past_its_thread_limit(
    home: RemHome, tmp_path: Path
):
    resumed = blocked_tasks(home, tmp_path, 8, self_work=True)
    # Self-work's own running work, which its thread limit still governs.
    own = blocked_tasks(home, tmp_path / "own", 2, self_work=True, group="own")
    with home.store.connect() as db:
        for task_id in own:
            run = home.store.get(db, "runs", run_of_task(home, task_id)["id"])
            run["state"] = "executing"
            home.store.put(db, "runs", run)
    home.generate = lambda *args: {
        "reply": "Resuming all eight.",
        "controls": [follow_up(t) for t in resumed],
    }
    home.submit_message({"request_id": "eight", "body": "Resume all eight blocked tasks"})
    home._reply()

    # One reply resumes every task the user asked for, not five of them.
    assert reply_body(home, "eight") == "Resuming all eight."
    runs = [run_of_task(home, t) for t in resumed]
    assert {r["state"] for r in runs} == {"queued"}
    assert {r["resumed"]["by"] for r in runs} == {"user"}
    # They are the user's work now: they don't wait behind the user's other tasks for a
    # worker, and self-work's thread limit doesn't pause them.
    assert not any(yields_worker(r) for r in runs)
    with home.store.read() as db:
        current = home.self_work._current_runs(db)
    mine, theirs = home.self_work._occupancy(current)
    assert {r["task_id"] for r in mine} == set(own)
    assert set(resumed) <= {r["task_id"] for r in theirs}
    paused = home.self_work._yield_to_user(mine, theirs, limit=4)
    # Over the limit of four, only self-work's own runs make way.
    assert {r["task_id"] for r in paused} == set(own)


def test_a_resume_a_workflow_message_asks_for_is_not_the_users(home: RemHome, tmp_path: Path):
    [task_id] = blocked_tasks(home, tmp_path, 1, self_work=True)
    with home.store.connect() as db:
        run = home.store.get(db, "runs", run_of_task(home, task_id)["id"])
        run.update(state="paused", paused={"at": run["created_at"], "by": "user"})
        home.store.put(db, "runs", run)
    home.generate = lambda *args: {
        "reply": "Resumed it.",
        "controls": [{"task_id": task_id, "action": "resume"}],
    }
    entry = {
        "id": "nightly",
        "body": "Resume the paused research task",
        "created_at": run["created_at"],
        "source": "workflow",
        "workflow": {"name": "nightly", "allow_actions": True},
    }
    with home.store.connect() as db:
        queue_user_message(home.store, db, entry, home.owner)
    home._reply()

    assert reply_body(home, "nightly") == "Resumed it."
    again = run_of_task(home, task_id)
    assert again["state"] != "paused" and again["resumed"]["by"] == "rem"
    # Nobody here asked for it, so self-work's own run still yields its worker.
    assert yields_worker(again)


def test_rem_resuming_its_own_blocked_work_keeps_it_self_work(home: RemHome, tmp_path: Path):
    [task_id] = blocked_tasks(home, tmp_path, 1, self_work=True)
    run = run_of_task(home, task_id)
    home.instruct(
        run["thread_id"],
        {"request_id": "unblock", "body": "Pick this back up."},
        notify=False,
        context={"source_message_id": "", "self_work": "unblock"},
    )
    again = run_of_task(home, task_id)
    assert again["state"] == "queued" and again["resumed"]["by"] == "rem"
    assert yields_worker(again)
    with home.store.read() as db:
        mine, _ = home.self_work._occupancy(home.self_work._current_runs(db))
    assert [r["task_id"] for r in mine] == [task_id]
