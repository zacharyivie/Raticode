"""A finished run never shows, or goes back to, Plan.

Cards and progress steps read a run's state. These cover the ways a run whose
work was done could still say Plan: a requeue that waits to check finished work,
a card whose run the task list no longer has, a stale read writing over a
finished run, and a backend that stopped for good after another took over Rem.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from gofer.devices.desktop_control import DesktopControl
from gofer.ui.rem_home import RemHome, Superseded
from gofer.ui.rem_home_store import progress_step
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe, wait_run


def summary_run(home: RemHome, task_id: str) -> dict[str, Any]:
    """The task's newest run as the desktop's task list and the phone receive it."""
    task = next(t for t in home.store.tasks(summary=True) if t["id"] == task_id)
    return dict(task["last_run"])


def cards(home: RemHome, run_id: str) -> list[dict[str, Any]]:
    return [
        m["work"]
        for m in home.store.conversation()["messages"]
        if (m.get("work") or {}).get("run_id") == run_id
    ]


def test_a_run_that_never_started_is_at_plan_and_one_whose_work_is_done_is_at_check(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    assert summary_run(home, task["id"])["step"] == "plan"
    assert cards(home, run["id"])[-1]["step"] == "plan"

    # The worker finished and the review was running when Raticode restarted.
    home._update(
        run["id"],
        state="verifying",
        started_at="2026-11-10T10:00:00+00:00",
        worker_result="Implemented and tested.",
        active_thread_id=run["thread_id"],
        verification_thread_id="review-thread",
        active_turn_id="review-turn",
        active_role="verify",
    )
    monkeypatch.setattr(home.jobs, "snapshot", lambda *args: [])
    home._recover()

    recovered = summary_run(home, task["id"])
    # Queued again until a worker is free, but the work is done: it is at Check.
    assert recovered["state"] == "queued"
    assert recovered["step"] == "check"
    # The recovery card and the start card both say so, and so does the phone's copy.
    assert {card["step"] for card in cards(home, run["id"])} == {"check"}
    assert DesktopControl._run(recovered)["step"] == "check"  # type: ignore[index]


@pytest.mark.parametrize(
    ("run", "step"),
    [
        ({"state": "queued"}, "plan"),
        ({"state": "queued", "started_at": "x"}, "work"),  # a follow-up or a worker retry
        ({"state": "queued", "started_at": "x", "resume_review": True}, "check"),
        ({"state": "executing"}, "work"),
        ({"state": "verifying"}, "check"),
        ({"state": "repairing"}, "check"),
        ({"state": "blocked"}, "work"),
        ({"state": "blocked", "verification_thread_id": "r"}, "check"),
        ({"state": "succeeded", "resume_review": True}, "report"),
    ],
)
def test_progress_step_follows_how_far_the_run_got(run: dict[str, Any], step: str):
    assert progress_step(run) == step


def test_a_finished_runs_card_says_so_after_its_task_leaves_the_list(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"succeeded"})
    # The task leaves the task list (deleted here; cleanup and a repeating task's
    # older runs do the same). The desktop's card then has only what chat gives it.
    home.edit_task(task["id"], {"request_id": "delete"}, "delete")
    assert all(t["id"] != task["id"] for t in home.store.tasks())

    work = cards(home, run["id"])
    # The "Started" card was written while the run was queued; it used to keep saying so.
    assert work and all(card["state"] == "succeeded" for card in work), work
    assert all(card["step"] == "report" for card in work)


def test_a_stop_decided_from_an_older_read_leaves_a_finished_run_finished(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"succeeded"})

    # The worker thread finished the run but hasn't been pruned when the dispatcher
    # rechecks its access, which a just-revoked project now fails.
    gate = threading.Event()
    lingering = threading.Thread(target=gate.wait, daemon=True)
    lingering.start()
    home.workers[run["id"]] = (lingering, threading.Event())

    def revoked(config: Any, recipe: Any) -> None:
        raise ValueError("Rem no longer has access to this project")

    monkeypatch.setattr(home, "authorize", revoked)
    try:
        home.tick()
    finally:
        gate.set()
        lingering.join(timeout=5)

    with home.store.read() as db:
        after = home.store.get(db, "runs", run["id"])
    assert after["state"] == "succeeded"
    assert after["epoch"] == run["epoch"]
    assert not after.get("stop_reason")


def test_self_work_stopping_a_run_that_just_finished_leaves_it_finished(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"succeeded"})
    # Self-work read the run while it was still working, then the run finished.
    stale = {**run, "state": "executing"}
    monkeypatch.setattr(home.self_work, "_run", lambda db, identity: stale)

    home.self_work._stop_run(run["id"])

    with home.store.read() as db:
        assert home.store.get(db, "runs", run["id"])["state"] == "succeeded"
    with pytest.raises(Superseded):
        home._update(run["id"], state="cancelling")


def test_an_edit_waiting_to_start_never_overrides_a_stop(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    # An edit arrived mid-turn, then the run was stopped before the worker requeued it.
    home._update(run["id"], state="executing", pending_recipe=dict(task))
    home.store.mutate_run(run["id"], {"request_id": "stop"}, "cancel")

    home._requeue_edited(run["id"])

    with home.store.read() as db:
        assert home.store.get(db, "runs", run["id"])["state"] == "cancelling"
    # Without the stop, the edit is queued to run.
    other = home.create_task({"request_id": "other", **recipe(tmp_path, title="Other")})["task"]
    home._update(other["active_run"]["id"], state="executing", pending_recipe=dict(other))
    home._requeue_edited(other["active_run"]["id"])
    with home.store.read() as db:
        assert home.store.get(db, "runs", other["active_run"]["id"])["state"] == "queued"


def test_a_backend_that_lost_rem_takes_it_back_when_the_other_one_goes_away(
    home: RemHome, tmp_path: Path
):
    home.tick()
    assert home.owned
    # Another backend on the same data folder (say `Raticode serve`) takes over
    # after this one's lease lapsed, for example while the computer slept.
    other = RemHome(
        home.data_dir,
        source=home.source,
        jobs=home.jobs,
        steering=home.steering,
        authorize=lambda c, r: None,
        start=False,
    )
    with home.store.connect() as db:
        db.execute("UPDATE lease SET expires=0")
    assert other._lease()
    home.tick()

    # Work queued while the other backend holds Rem, and then it exits.
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    other.close()
    with home.store.connect() as db:
        db.execute("UPDATE lease SET expires=0")

    # This backend used to have stopped for good, leaving the task queued (Plan).
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert home.owned
    deadline = time.monotonic() + 5
    while any(worker.is_alive() for worker, _ in home.workers.values()):
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_cleanup_leaves_a_finished_runs_cards_at_its_final_state(tmp_path: Path):
    from gofer.ui.rem_home_store import RETENTION, RemHomeStore
    from tests.unit.test_rem_home_retention import FINISHED, add

    store = RemHomeStore(tmp_path)
    done = add(store, "Done once")
    started = next(
        m for m in store.conversation()["messages"] if m["id"] == f"queued-{done['run']['id']}"
    )
    assert started["work"]["state"] == "succeeded"

    # Cleanup removes the run 30 days later; its "Started" card was written as queued.
    assert store.cleanup(FINISHED + RETENTION) == [done["run"]["thread_id"]]
    after = [
        m["work"]
        for m in store.conversation()["messages"]
        if (m.get("work") or {}).get("run_id") == done["run"]["id"]
    ]
    assert after and all(w["state"] == "succeeded" and w["step"] == "report" for w in after)
