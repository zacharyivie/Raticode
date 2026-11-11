"""Rem tasks that start after other tasks succeed ("after task X, do task Y")."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_models import Recipe
from gofer.ui.rem_home_store import RemHomeStore
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe, wait_run

LATER = {"kind": "once", "timezone": "UTC", "at": "2099-01-01T09:00:00+00:00"}


def later(home: RemHome, title: str) -> dict[str, Any]:
    """A prerequisite that won't run on its own during the test."""
    created = home.create_task(
        {"request_id": f"create-{title}", **recipe(title=title, schedule=LATER)}
    )
    return dict(created["task"])


def after(home: RemHome, title: str, *refs: str, **patch: Any) -> dict[str, Any]:
    created = home.create_task(
        {"request_id": f"create-{title}", **recipe(title=title, after=list(refs), **patch)}
    )
    return dict(created["task"])


def view(home: RemHome, task_id: str) -> dict[str, Any]:
    return next(t for t in home.store.tasks() if t["id"] == task_id)


def runs(store: RemHomeStore, task_id: str) -> list[dict[str, Any]]:
    with store.read() as db:
        return [
            json.loads(r[0]) for r in db.execute("SELECT body FROM runs WHERE task=?", (task_id,))
        ]


def run_with(home: RemHome, task_id: str, state: str) -> dict[str, Any]:
    """Give a prerequisite a run in ``state`` without starting a worker."""
    with home.store.connect() as db:
        task = home.store.get(db, "tasks", task_id)
        run = home.store.add_run(db, task, f"manual:{uuid4()}", {}, announce=False)
        run["state"] = state
        if state not in {"queued", "executing"}:
            run["finished_at"] = datetime.now(UTC).isoformat()
        home.store.put(db, "runs", run)
    return run


def set_state(home: RemHome, run_id: str, state: str) -> None:
    with home.store.connect() as db:
        run = home.store.get(db, "runs", run_id)
        run["state"] = state
        home.store.put(db, "runs", run)


def gate(home: RemHome) -> None:
    home.store.due(datetime.now(UTC))


def finished(home: RemHome, task_id: str, timeout: float = 10) -> dict[str, Any]:
    """Tick until the waiting task has a run, then until that run ends."""
    deadline = time.monotonic() + timeout
    while view(home, task_id)["last_run"] is None:
        assert time.monotonic() < deadline, view(home, task_id)
        home.tick()
        time.sleep(0.01)
    return wait_run(home, task_id, {"succeeded", "blocked", "failed"}, timeout)


def test_a_task_waits_then_starts_once_with_what_its_prerequisite_found(home: RemHome):
    seen: list[str] = []
    original = home.source

    async def source(**kwargs: Any):
        seen.append(kwargs["agent_instructions"])
        async for event in original(**kwargs):
            yield event

    home.source = source
    audit = home.create_task({"request_id": "audit", **recipe(title="Audit")})["task"]
    fix = after(home, "Fix findings", audit["id"])
    assert fix["next_occurrence_at"] is None
    waiting = view(home, fix["id"])
    assert waiting["active_run"] is None and waiting["last_run"] is None
    assert waiting["dependency"]["state"] == "waiting"
    assert waiting["dependency"]["reason"] == "Waiting for “Audit” to finish"
    assert waiting["dependency"]["prerequisites"][0]["task_id"] == audit["id"]

    first = wait_run(home, audit["id"], {"succeeded", "blocked"})
    assert first["state"] == "succeeded", first
    done = finished(home, fix["id"])
    assert done["state"] == "succeeded", done
    assert done["occurrence"] == f"after:{fix['id']}"
    (started,) = runs(home.store, fix["id"])
    assert started["after_results"][0]["run_id"] == first["id"]
    assert started["after_results"][0]["result"] == "Implemented and tested."
    worker = [text for text in seen if "Prerequisite results" in text]
    assert worker and "Implemented and tested." in worker[0]
    assert "dependency" not in view(home, fix["id"])
    assert any(
        m["body"] == "“Audit” finished, so I started “Fix findings”."
        for m in home.store.conversation()["messages"]
    )

    # Restarting, or a second backend sharing the data, never starts it again.
    again = RemHomeStore(home.data_dir)
    again.due(datetime.now(UTC))
    gate(home)
    assert len(runs(home.store, fix["id"])) == 1


def test_start_is_exactly_once_across_restarts(home: RemHome):
    audit = later(home, "Audit")
    fix = after(home, "Fix", audit["id"])
    prerequisite = run_with(home, audit["id"], "succeeded")
    stores = [home.store, RemHomeStore(home.data_dir), RemHomeStore(home.data_dir)]
    for store in stores:
        store.due(datetime.now(UTC))
    (started,) = runs(home.store, fix["id"])
    assert started["state"] == "queued"
    assert started["after_results"][0]["run_id"] == prerequisite["id"]
    task = view(home, fix["id"])
    assert task["after_started"]["how"] == "prerequisites"
    assert task["after_started"]["run_id"] == started["id"]


@pytest.mark.parametrize(
    ("state", "words"),
    [
        ("failed", "didn't finish"),
        ("blocked", "was blocked"),
        ("cancelled", "was stopped"),
        ("outcome_unknown", "ended with an unclear result"),
    ],
)
def test_an_unsuccessful_prerequisite_holds_the_task_until_retried(
    home: RemHome, state: str, words: str
):
    audit = later(home, "Audit")
    fix = after(home, "Fix", audit["id"])
    failed = run_with(home, audit["id"], state)
    for _ in range(3):
        gate(home)
    held = view(home, fix["id"])
    assert held["dependency"]["state"] == "held"
    assert f"“Audit” {words}" in held["dependency"]["reason"]
    assert held["dependency"]["prerequisites"][0]["retryable"] is True
    assert held["active_run"] is None
    notices = [
        m
        for m in home.store.conversation()["messages"]
        if m["id"].startswith(f"after-held-{fix['id']}")
    ]
    # Said once in the chat, as a card that needs the user (phone alerts read this).
    assert len(notices) == 1
    assert notices[0]["kind"] == "task_update"
    assert notices[0]["work"]["state"] == "held" and notices[0]["work"]["needs_user"] is True
    assert notices[0]["work"]["task_id"] == fix["id"]

    # A follow-up requeues that same run; the task waits again, then starts on success.
    set_state(home, failed["id"], "queued")
    gate(home)
    assert view(home, fix["id"])["dependency"]["state"] == "waiting"
    set_state(home, failed["id"], "succeeded")
    gate(home)
    assert len(runs(home.store, fix["id"])) == 1


def test_a_retry_with_a_new_run_counts(home: RemHome):
    audit = later(home, "Audit")
    fix = after(home, "Fix", audit["id"])
    run_with(home, audit["id"], "failed")
    gate(home)
    assert view(home, fix["id"])["dependency"]["state"] == "held"
    run_with(home, audit["id"], "succeeded")
    gate(home)
    assert len(runs(home.store, fix["id"])) == 1


def test_a_deleted_or_paused_prerequisite_holds_with_a_reason(home: RemHome):
    audit = later(home, "Audit")
    report = later(home, "Report")
    fix = after(home, "Fix", audit["id"], report["id"])
    home.edit_task(audit["id"], {"request_id": "delete-audit"}, "delete")
    home.edit_task(report["id"], {"request_id": "pause-report"}, "pause")
    gate(home)
    dependency = view(home, fix["id"])["dependency"]
    assert dependency["state"] == "held"
    assert "“Audit” was deleted" in dependency["reason"]
    assert "“Report” is paused" in dependency["reason"]
    retry = {item["title"]: item["retryable"] for item in dependency["prerequisites"]}
    assert retry == {"Audit": False, "Report": True}


def test_starting_it_by_hand_releases_the_condition(home: RemHome):
    audit = later(home, "Audit")
    fix = after(home, "Fix", audit["id"])
    run_with(home, audit["id"], "failed")
    gate(home)
    started = home.edit_task(fix["id"], {"request_id": "start-anyway"}, "run")
    assert started["task"]["after_started"]["how"] == "started_by_user"
    assert "dependency" not in started["task"]
    # The prerequisite succeeding later never starts it a second time.
    run_with(home, audit["id"], "succeeded")
    gate(home)
    assert [r["id"] for r in runs(home.store, fix["id"])] == [started["run"]["id"]]


def test_self_waits_cycles_unknown_ids_and_repeating_tasks_are_refused(home: RemHome):
    first = later(home, "First")
    second = after(home, "Second", first["id"])
    with pytest.raises(ValueError, match="itself"):
        home.edit_task(
            second["id"],
            {
                "request_id": "self",
                "expected_revision": second["revision"],
                "after": [second["id"]],
            },
            "edit",
        )
    third = after(home, "Third", second["id"])
    # First (scheduled) may not start waiting for Third, which waits for it in turn.
    with pytest.raises(ValueError, match="already ran|wait for each other"):
        home.edit_task(
            first["id"],
            {"request_id": "cycle", "expected_revision": first["revision"], "after": [third["id"]]},
            "edit",
        )
    with pytest.raises(ValueError, match="wait for each other"):
        home.edit_task(
            second["id"],
            {"request_id": "loop", "expected_revision": second["revision"], "after": [third["id"]]},
            "edit",
        )
    with pytest.raises(ValueError, match="no task or run"):
        after(home, "Ghost", "not-a-task")
    with pytest.raises(ValidationError, match="one-time"):
        Recipe.model_validate(
            recipe(after=[first["id"]], schedule={"kind": "daily", "timezone": "UTC"})
        )
    with pytest.raises(ValidationError, match="ask_rem"):
        Recipe.model_validate(recipe(ask_rem=True, after=[first["id"]]))
    assert len(home.store.tasks()) == 3


def test_a_run_id_names_its_task_from_that_run_on(home: RemHome):
    audit = later(home, "Audit")
    old = run_with(home, audit["id"], "succeeded")
    current = run_with(home, audit["id"], "executing")
    fix = after(home, "Fix", current["id"])
    (entry,) = view(home, fix["id"])["prerequisites"]
    assert entry == {"task_id": audit["id"], "title": "Audit", "since": current["created_at"]}
    assert old["id"] != current["id"]
    gate(home)
    assert view(home, fix["id"])["dependency"]["state"] == "waiting"
    set_state(home, current["id"], "succeeded")
    gate(home)
    assert len(runs(home.store, fix["id"])) == 1


def test_a_prerequisite_that_already_succeeded_counts_as_done(home: RemHome):
    audit = later(home, "Audit")
    run_with(home, audit["id"], "succeeded")
    # Its next run is far off, so it waits for that run, not the finished one.
    fix = after(home, "Fix", audit["id"])
    gate(home)
    assert view(home, fix["id"])["dependency"]["state"] == "waiting"
    home.edit_task(audit["id"], {"request_id": "pause"}, "pause")
    finished = after(home, "Report", audit["id"])
    gate(home)
    assert len(runs(home.store, finished["id"])) == 1


def test_clearing_the_condition_starts_the_task_and_started_tasks_stay_fixed(home: RemHome):
    audit = later(home, "Audit")
    fix = after(home, "Fix", audit["id"])
    cleared = home.edit_task(
        fix["id"],
        {"request_id": "clear", "expected_revision": fix["revision"], "after": []},
        "edit",
    )["task"]
    assert cleared["next_occurrence_at"]
    gate(home)
    assert len(runs(home.store, fix["id"])) == 1
    current = view(home, fix["id"])
    with pytest.raises(ValueError, match="already ran"):
        home.edit_task(
            fix["id"],
            {
                "request_id": "again",
                "expected_revision": current["revision"],
                "after": [audit["id"]],
            },
            "edit",
        )


def test_a_chain_waits_and_a_hold_travels_down_it(home: RemHome):
    audit = later(home, "Audit")
    fix = after(home, "Fix", audit["id"])
    ship = after(home, "Ship", fix["id"])
    gate(home)
    assert view(home, ship["id"])["dependency"]["reason"] == "Waiting for “Fix”, which waits too"
    run_with(home, audit["id"], "failed")
    gate(home)
    assert "“Fix” is on hold" in view(home, ship["id"])["dependency"]["reason"]


def test_a_paused_waiting_task_does_not_start(home: RemHome):
    audit = later(home, "Audit")
    fix = after(home, "Fix", audit["id"])
    home.edit_task(fix["id"], {"request_id": "pause"}, "pause")
    run_with(home, audit["id"], "succeeded")
    gate(home)
    assert runs(home.store, fix["id"]) == []
    paused = view(home, fix["id"])
    home.edit_task(
        fix["id"],
        {"request_id": "resume", "expected_revision": paused["revision"], "enabled": True},
        "edit",
    )
    assert view(home, fix["id"])["next_occurrence_at"] is None
    gate(home)
    assert len(runs(home.store, fix["id"])) == 1


def test_a_requested_time_still_delays_the_start(home: RemHome):
    audit = later(home, "Audit")
    soon = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    fix = after(home, "Fix", audit["id"], schedule={"kind": "once", "timezone": "UTC", "at": soon})
    run_with(home, audit["id"], "succeeded")
    gate(home)
    assert runs(home.store, fix["id"]) == []
    home.store.due(datetime.now(UTC) + timedelta(hours=2))
    assert len(runs(home.store, fix["id"])) == 1


def test_rem_queues_follow_up_tasks_from_a_chat_request(home: RemHome):
    prompts: list[str] = []

    def generate(prompt: str, config: dict[str, Any], cancel: Any) -> dict[str, Any]:
        prompts.append(prompt)
        return {
            "reply": "I’ll run the audit, then the fixes.",
            "tasks": [
                recipe(title="Audit the CLI", type="research", prompt="Audit the CLI"),
                recipe(title="Fix group 1", after=["new:0"]),
                recipe(title="Fix group 2", after=["new:0"]),
            ],
        }

    home.generate = generate
    home.submit_message({"request_id": "ask", "body": "Audit, then start two fix threads."})
    home._reply()
    assert "after:[IDs]" in prompts[0] and "new:N" in prompts[0]
    tasks = {t["title"]: t for t in home.store.tasks()}
    audit = tasks["Audit the CLI"]
    assert audit["active_run"]["state"] == "queued"
    for title in ("Fix group 1", "Fix group 2"):
        assert tasks[title]["active_run"] is None
        assert tasks[title]["prerequisites"][0]["task_id"] == audit["id"]
        assert tasks[title]["dependency"]["state"] == "waiting"
    bodies = [m["body"] for m in home.store.conversation()["messages"]]
    assert "“Fix group 2” will start after “Audit the CLI” finishes." in bodies


def test_rem_refuses_a_task_that_waits_for_one_listed_after_it(home: RemHome):
    home.generate = lambda p, c, e: {
        "reply": "Queued.",
        "tasks": [recipe(title="Fix", after=["new:1"]), recipe(title="Audit")],
    }
    home.submit_message({"request_id": "ask", "body": "Fix after the audit."})
    home._reply()
    assert home.store.tasks() == []
    assert home.store.conversation()["conversation"]["turn_state"] == "idle"


def test_a_queued_request_goes_back_to_rem_with_the_results(home: RemHome):
    audit = later(home, "CLI audit")
    plan = home.create_task(
        {
            "request_id": "plan",
            **recipe(
                title="Fix the audit findings",
                type="reminder",
                prompt="Start up to 4 threads to address all the findings.",
                after=[audit["id"]],
                ask_rem=True,
            ),
        }
    )["task"]
    prerequisite = run_with(home, audit["id"], "succeeded")
    with home.store.connect() as db:
        done = home.store.get(db, "runs", prerequisite["id"])
        done.update(summary_short="Found 6 drift areas.", worker_result="Six areas drift.")
        home.store.put(db, "runs", done)
    asked: list[str] = []

    def generate(prompt: str, config: dict[str, Any], cancel: Any) -> dict[str, Any]:
        asked.append(prompt)
        return {"reply": "Starting the fixes.", "tasks": []}

    home.generate = generate
    run = finished(home, plan["id"])
    assert run["state"] == "succeeded"
    conversation = home.store.conversation()
    queued = next(m for m in conversation["messages"] if m["id"] == f"after-{run['id']}")
    assert queued["role"] == "user" and queued["source"] == "after"
    assert queued["body"] == "Start up to 4 threads to address all the findings."
    assert queued["queued_after"][0]["summary"] == "Found 6 drift areas."
    assert queued["queued_after"][0]["title"] == "CLI audit"
    # No separate update repeats the request; Rem's reply answers it.
    home.deliver_outcomes()
    assert not any(
        m.get("kind") == "task_update" and m.get("work", {}).get("run_id") == run["id"]
        for m in home.store.conversation()["messages"]
    )
    # Rem answers it like any message, with the prerequisite's results in front of it.
    deadline = time.monotonic() + 10
    while not asked or (home.conversation_worker and home.conversation_worker.is_alive()):
        assert time.monotonic() < deadline
        home.tick()
        time.sleep(0.01)
    assert '"queued_after"' in asked[0] and "Found 6 drift areas." in asked[0]
    replies = [
        m for m in home.store.conversation()["messages"] if m["id"] == f"reply-{queued['id']}"
    ]
    assert replies and replies[0]["body"].startswith("Starting the fixes.")


def test_retry_in_chat_after_a_hold_retries_the_task_it_waits_for(home: RemHome):
    audit = later(home, "Audit")
    fix = after(home, "Fix", audit["id"])
    blocked = run_with(home, audit["id"], "blocked")
    gate(home)
    home.generate = lambda p, c, e: {"reply": "Sure.", "tasks": [], "memories": []}
    home.submit_message({"request_id": "retry", "body": "retry"})
    home._reply()
    # The blocked prerequisite run is followed up, so the held task waits again.
    with home.store.read() as db:
        assert home.store.get(db, "runs", blocked["id"])["state"] == "queued"
    assert view(home, fix["id"])["dependency"]["state"] == "waiting"
