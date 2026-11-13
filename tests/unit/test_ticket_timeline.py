"""Board tickets follow their runs into Blocked and back, and keep a timeline that says why."""

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from gofer.ui import rem_ticket_activity as activity
from gofer.ui.rem_kanban import get_ticket, put_ticket
from tests.unit.test_device_application import application as application
from tests.unit.test_device_desktop_control import phone as phone
from tests.unit.test_rem_kanban import (
    add,
    at,
    set_options,
    settle,
    tick,
    ticket,
)
from tests.unit.test_rem_kanban import (
    board as board,
)
from tests.unit.test_rem_kanban import (
    linked as linked,
)
from tests.unit.test_rem_kanban import (
    org as org,
)

NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)


def run(state: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": "run-1",
        "task_id": "task-1",
        "thread_id": "thread-1",
        "epoch": 1,
        "state": state,
        "recipe": {
            "acceptance_criteria": [
                {"id": "c1", "description": "Tests pass"},
                {"id": "c2", "description": "Docs updated"},
            ]
        },
        **fields,
    }


def stored(**fields: Any) -> dict[str, Any]:
    return {"id": "t", "status": "in_progress", "run_id": "run-1", "blocked_at": None, **fields}


# What a run means for its ticket ---------------------------------------------------------------


def test_each_run_state_maps_to_one_column_and_stopping_waits_for_the_outcome() -> None:
    ticket_ = stored()
    expected = {
        "queued": "in_progress",
        "executing": "in_progress",
        "repairing": "in_progress",
        "verifying": "in_review",
        "succeeded": "done",
        "blocked": "blocked",
        "cancelled": "blocked",
        "outcome_unknown": "blocked",
    }
    for state, column in expected.items():
        assert activity.target_status(ticket_, run(state, finished_at=NOW.isoformat())) == column
    # A run being stopped doesn't move the ticket twice.
    assert activity.target_status(ticket_, run("cancelling")) is None
    # A review resumed after a restart is a review, not new work.
    assert activity.target_status(ticket_, run("queued", resume_review=True)) == "in_review"
    # A review waiting for a future event's proof is waiting on something: Blocked.
    later = (NOW + timedelta(hours=1)).isoformat()
    waiting = run("queued", waiting_for="the nightly build", not_before=later)
    assert activity.target_status(ticket_, waiting, NOW) == "blocked"
    assert "the nightly build" in activity.block_reason(waiting, NOW)[1]


def test_a_blocked_ticket_ignores_an_older_success() -> None:
    blocked = stored(status="blocked", blocked_at=NOW.isoformat())
    older = run("succeeded", finished_at=(NOW - timedelta(minutes=5)).isoformat())
    newer = run("succeeded", finished_at=(NOW + timedelta(minutes=5)).isoformat())
    assert activity.target_status(blocked, older) is None
    assert activity.target_status(blocked, newer) == "done"
    assert activity.target_status(blocked, None) is None


def test_block_reasons_come_from_the_run_records() -> None:
    asks = run("blocked", needs_user=True, user_question="Which account should I deploy to?")
    assert activity.block_reason(asks)[:2] == (
        "Waiting for your answer",
        "Which account should I deploy to?",
    )
    checks = run(
        "blocked",
        summary_short="The review still has unresolved checks.",
        criteria=[{"id": "c1", "result": "pass"}, {"id": "c2", "result": "fail"}],
        review_feedback="README still describes the old flag.",
    )
    entry = activity.transition_entry(stored(), "blocked", checks)
    assert entry["title"] == "Blocked: review checks failed"
    assert entry["body"] == "The review left these checks unresolved: Docs updated"
    assert entry["details"] == "README still describes the old flag."
    assert {"name": "Docs updated", "result": "fail"} in entry["checks"]
    failed = run("blocked", summary_short="The provider kept failing after 3 retries: 503")
    assert activity.transition_entry(stored(), "blocked", failed)["title"] == (
        "Blocked: task failed"
    )
    stopped = activity.transition_entry(stored(), "blocked", run("cancelled"))
    assert stopped["title"] == "Blocked: task stopped"


def test_work_complete_keeps_long_results_folded() -> None:
    result = "All 42 tests passed.\n\n" + "Details line.\n" * 400
    entry = activity.transition_entry(stored(), "in_review", run("verifying", worker_result=result))
    assert entry["title"] == "Work complete, moved to In review"
    assert entry["body"] == "All 42 tests passed."
    assert entry["details"].startswith("All 42 tests passed.")
    assert len(entry["details"]) <= activity.DETAIL_CHARS
    done = activity.transition_entry(
        stored(status="in_review"),
        "done",
        run(
            "succeeded",
            summary_short="Fixed and verified.",
            criteria=[{"id": "c1", "result": "pass"}, {"id": "c2", "result": "pass"}],
        ),
    )
    assert done["title"] == "Verified complete, moved to Done"
    assert done["checks"] == [
        {"name": "Tests pass", "result": "pass"},
        {"name": "Docs updated", "result": "pass"},
    ]
    resumed = activity.transition_entry(stored(status="blocked"), "in_progress", run("executing"))
    assert resumed["title"] == "Moved to In progress: task resumed"
    changes = activity.transition_entry(
        stored(status="in_review"),
        "in_progress",
        run("repairing", criteria=[{"id": "c1", "result": "fail"}]),
    )
    assert changes["title"] == "Moved back to In progress: changes requested"
    assert "Tests pass" in changes["body"]


# Rem's tickets on the board ---------------------------------------------------------------------


def timeline(home: Any, title: str) -> Any:
    found = ticket(home, title)
    return home.kanban.timeline({"ticket_id": found["ticket_id"], "scope": "rem"})["comments"]


def latest_run(home: Any, title: str) -> Any:
    found = ticket(home, title)
    with home.store.read() as db:
        return json.loads(
            db.execute("SELECT body FROM runs WHERE id=?", (found["run_id"],)).fetchone()[0]
        )


def put_run(home: Any, changed: dict[str, Any]) -> None:
    with home.store.connect() as db:
        home.store.put(db, "runs", changed)


def test_a_finished_ticket_says_it_was_verified_and_starting_it_says_nothing(board):
    home, _, _ = board
    add(home, "Ship it")
    at(home, 15)
    tick(home)
    settle(home, "Ship it")
    assert ticket(home, "Ship it")["status"] == "done"
    entries = timeline(home, "Ship it")
    # Leaving To do isn't explained; the verified result is, with its checks.
    assert all(e["from"] not in {"todo", "backlog"} for e in entries)
    done = entries[-1]
    assert done["title"] == "Verified complete, moved to Done"
    assert done["author"] == "system" and done["run_id"] and done["thread_id"]
    assert done["checks"] and all(c["result"] == "pass" for c in done["checks"])


def begin(home: Any, title: str) -> Any:
    """Start a ticket's run without a worker picking it up, so a test sets its states."""
    created = add(home, title)
    with home.store.read() as db:
        config = home.store.setting(db, "config")
    assert home.self_work._begin_ticket(created["id"], config)
    return latest_run(home, title)


def test_review_and_repair_move_the_ticket_without_ping_pong(board):
    home, _, _ = board
    current = begin(home, "Hold")
    reconcile = home.self_work.reconcile
    # The run moves to review: the ticket follows, once, however often Rem polls.
    put_run(home, {**current, "state": "verifying", "worker_result": "All tests pass."})
    for _ in range(4):
        reconcile()
    held = ticket(home, "Hold")
    assert held["status"] == "in_review" and held["running"]
    revision = held["revision"]
    for _ in range(3):
        reconcile()
    assert ticket(home, "Hold")["revision"] == revision
    entries = timeline(home, "Hold")
    assert [e["title"] for e in entries] == ["Work complete, moved to In review"]
    assert entries[0]["body"] == "All tests pass."
    # Changes requested: back to In progress, saying which checks failed.
    put_run(home, {**current, "state": "repairing", "criteria": [{"id": "x", "result": "fail"}]})
    reconcile()
    reconcile()
    assert ticket(home, "Hold")["status"] == "in_progress"
    assert timeline(home, "Hold")[-1]["title"] == "Moved back to In progress: changes requested"
    # Stopping: nothing moves until the run has stopped, then it's Blocked once.
    put_run(home, {**current, "state": "cancelling"})
    reconcile()
    assert ticket(home, "Hold")["status"] == "in_progress"
    put_run(
        home,
        {**current, "state": "cancelled", "finished_at": datetime.now(UTC).isoformat()},
    )
    for _ in range(3):
        reconcile()
    stopped = ticket(home, "Hold")
    assert stopped["status"] == "blocked" and not stopped["running"]
    titles = [e["title"] for e in timeline(home, "Hold")]
    assert titles.count("Blocked: task stopped") == 1
    # Picked up again in its thread: In progress, saying so.
    put_run(home, {**current, "state": "executing", "epoch": current["epoch"] + 1})
    reconcile()
    assert ticket(home, "Hold")["status"] == "in_progress"
    assert timeline(home, "Hold")[-1]["title"] == "Moved to In progress: task resumed"


def test_a_ticket_waiting_for_the_user_stays_blocked_with_one_comment(board):
    home, fake, _ = board
    current = begin(home, "Ask")
    put_run(
        home,
        {
            **current,
            "state": "blocked",
            "needs_user": True,
            "user_question": "Which region should the bucket live in?",
            "finished_at": datetime.now(UTC).isoformat(),
        },
    )
    at(home, 15)
    runs = len(fake.calls)
    for _ in range(5):
        home.self_work.tick()
    blocked = ticket(home, "Ask")
    assert blocked["status"] == "blocked"
    assert blocked["block_reason"] == "Which region should the bucket live in?"
    entries = timeline(home, "Ask")
    assert [e["title"] for e in entries] == ["Blocked: waiting for your answer", "Still blocked"]
    assert "Which region" in entries[-1]["body"] and entries[-1]["author"] == "rem"
    # Rem doesn't try to answer for the user or restart it.
    assert latest_run(home, "Ask")["state"] == "blocked" and len(fake.calls) == runs
    report = home.self_work.view()["report"]
    assert any(w["state"] == "still_blocked" for w in report["work_done"])


def test_blocked_tickets_come_before_todo_and_respect_the_thread_limit(board):
    home, fake, _ = board
    set_options(home, max_threads=1)
    fake.failing.add("Broken")
    add(home, "Broken", priority="low")
    at(home, 15)
    tick(home)
    settle(home, "Broken")
    assert ticket(home, "Broken")["status"] == "blocked"
    fake.failing.discard("Broken")
    fake.hold.clear()
    add(home, "Next", priority="critical")
    tick(home)
    # The blocked ticket takes the only thread, ahead of a more urgent To do.
    deadline = time.monotonic() + 5
    while ticket(home, "Broken")["status"] == "blocked" and time.monotonic() < deadline:
        tick(home)
        time.sleep(0.01)
    assert ticket(home, "Broken")["status"] in {"in_progress", "in_review"}
    assert ticket(home, "Next")["status"] == "todo"
    resumed = [e for e in timeline(home, "Broken") if e["to"] == "in_progress"]
    assert resumed and resumed[-1]["body"].startswith("Rem followed up in its thread")
    fake.hold.set()
    settle(home, "Broken", "Next")
    assert ticket(home, "Broken")["status"] == "done"


def test_a_ticket_that_couldnt_start_is_started_again_once_it_can(board, monkeypatch):
    home, _, _ = board
    refusals = {"on": True}

    def authorize(config: Any, recipe: Any) -> None:
        if refusals["on"] and "Gated" in str(recipe.get("title", "")):
            raise ValueError("The project isn't trusted yet.")

    monkeypatch.setattr(home, "authorize", authorize)
    add(home, "Gated")
    at(home, 15)
    tick(home)
    gated = ticket(home, "Gated")
    assert gated["status"] == "blocked" and "isn't trusted" in gated["block_reason"]
    tick(home)
    # Still refused: one comment, no loop.
    assert [e["title"] for e in timeline(home, "Gated")] == [
        "Blocked: couldn't start",
        "Still blocked",
    ]
    # Fixed by the user, then a later block review finds it can start now.
    refusals["on"] = False
    with home.store.connect() as db:
        stuck = get_ticket(db, gated["ticket_id"])
        stuck["blocked_at"] = datetime.now(UTC).isoformat()
        put_ticket(db, stuck)
    # It waits for a free thread (research holds both at first), then starts.
    deadline = time.monotonic() + 8
    while ticket(home, "Gated")["status"] == "blocked" and time.monotonic() < deadline:
        tick(home)
        time.sleep(0.01)
    settle(home, "Gated")
    assert ticket(home, "Gated")["status"] == "done"
    titles = [e["title"] for e in timeline(home, "Gated")]
    assert "Moved to In progress: task retried" in titles


def test_the_user_moving_or_commenting_is_recorded_and_ends_following_the_run(board):
    home, fake, _ = board
    fake.failing.add("Moved")
    add(home, "Moved")
    set_options(home, enabled=False)
    with home.store.read() as db:
        config = home.store.setting(db, "config")
    assert home.self_work._begin_ticket(ticket(home, "Moved")["ticket_id"], config)
    settle(home, "Moved")
    blocked = ticket(home, "Moved")
    home.kanban.act(
        "user",
        {"action": "comment", "ticket_id": blocked["ticket_id"], "body": "I'll sign in tonight."},
        "c1",
    )
    # The same request again adds nothing.
    home.kanban.act(
        "user",
        {"action": "comment", "ticket_id": blocked["ticket_id"], "body": "I'll sign in tonight."},
        "c1",
    )
    home.kanban.act(
        "user",
        {
            "action": "move",
            "ticket_id": blocked["ticket_id"],
            "expected_revision": blocked["revision"],
            "column": "todo",
        },
        "m1",
    )
    entries = timeline(home, "Moved")
    assert [(e["author"], e["title"]) for e in entries[-2:]] == [
        ("user", ""),
        ("user", "Moved to To do"),
    ]
    assert sum(e["body"] == "I'll sign in tonight." for e in entries) == 1
    tick(home)
    assert ticket(home, "Moved")["status"] == "todo"


def test_a_comment_needs_text_and_a_real_ticket(board):
    home, _, _ = board
    created = add(home, "Quiet")
    for payload, code in (
        ({"ticket_id": created["id"], "body": "  "}, "invalid"),
        ({"ticket_id": "missing", "body": "Hi"}, "not_found"),
    ):
        try:
            home.kanban.act("user", {"action": "comment", **payload}, "x")
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "code", "") == code
        else:
            raise AssertionError(payload)


def test_status_entries_are_written_once_per_event(board):
    home, _, _ = board
    created = add(home, "Once")
    with home.store.connect() as db:
        for _ in range(3):
            activity.add_comment(
                db,
                created["id"],
                author="system",
                kind="status",
                title="Blocked: task failed",
                body="Same",
                status_to="blocked",
                dedupe="run:1:failed",
            )
        # The same words for a new event are still one entry; different words are a new one.
        activity.add_comment(
            db,
            created["id"],
            author="system",
            kind="status",
            title="Blocked: task failed",
            body="Same",
            status_to="blocked",
            dedupe="run:2:failed",
        )
        activity.add_comment(
            db,
            created["id"],
            author="system",
            kind="status",
            title="Blocked: task failed",
            body="Different",
            status_to="blocked",
            dedupe="run:3:failed",
        )
    assert [e["body"] for e in timeline(home, "Once")] == ["Same", "Different"]


# Organization tickets ---------------------------------------------------------------------------


def org_task(**fields: Any) -> dict[str, Any]:
    return {
        "id": "o1",
        "title": "Port the importer",
        "status": "in_progress",
        "comments": [],
        "dependsOn": [],
        "blockReason": "",
        **fields,
    }


def company(*tasks: dict[str, Any]) -> dict[str, Any]:
    return {
        "config": {"employees": [{"id": "mira", "name": "Mira"}]},
        "runtime": {"tasks": list(tasks)},
    }


def test_organization_moves_get_entries_from_the_ticket_and_skip_routine_moves() -> None:
    final = {"actor": "employee:mira", "at": "x", "body": "Ported it; 12 tests pass."}
    reviewed = org_task(status="in_review", comments=[final])
    org = company(reviewed)
    activity.note_organization_transitions(org, {"o1": "in_progress"}, "employee:mira")
    entry = reviewed["comments"][-1]
    assert entry["kind"] == "status" and entry["title"] == "Work complete, moved to In review"
    # The worker's report is the comment just above; the entry doesn't repeat it.
    assert entry["body"] == ""
    # Polling the same state again adds nothing.
    activity.note_organization_transitions(org, {"o1": "in_review"}, "system")
    activity.note_organization_transitions(org, {"o1": "in_progress"}, "employee:mira")
    assert sum(c.get("kind") == "status" for c in reviewed["comments"]) == 1
    waiting = org_task(status="blocked", blockReason="dependencies", dependsOn=["o2"])
    other = org_task(id="o2", title="Schema first", status="todo")
    activity.note_organization_transitions(
        company(waiting, other), {"o1": "todo", "o2": "backlog"}, "system"
    )
    assert waiting["comments"][-1]["title"] == "Blocked: waiting for other tickets"
    assert "Schema first" in waiting["comments"][-1]["body"]
    # Leaving Backlog or To do and repeating tickets coming round get no entry.
    assert other["comments"] == []
    started = org_task(status="in_progress")
    activity.note_organization_transitions(company(started), {"o1": "todo"}, "system")
    assert started["comments"] == []
    accepted = org_task(
        status="done",
        comments=[{"actor": "user", "at": "y", "body": "Looks right.", "decision": "accept"}],
    )
    activity.note_organization_transitions(company(accepted), {"o1": "in_review"}, "user")
    assert accepted["comments"][-1]["title"] == "Verified complete, moved to Done"
    shown = activity.organization_comment(accepted["comments"][0], company())
    assert shown["title"] == "Review: accepted" and shown["author_name"] == "You"
    worker = activity.organization_comment(final, company())
    assert worker["author"] == "worker" and worker["author_name"] == "Mira"


def test_board_timeline_reads_an_organization_ticket(board, org):
    home, _, _ = board
    manager, oid = org
    created = manager.call(
        "", "user", {"action": "task_create", "organizationId": oid, "params": {"title": "Org"}}
    )
    home.kanban.act(
        "user",
        {
            "action": "comment",
            "scope": "organization",
            "organization_id": oid,
            "ticket_id": created["id"],
            "body": "Check the CSV path too.",
        },
        "oc1",
    )
    view = home.kanban.timeline(
        {"ticket_id": created["id"], "scope": "organization", "organization_id": oid}
    )
    assert view["ticket"]["title"] == "Org"
    assert [(c["author"], c["body"]) for c in view["comments"]] == [
        ("user", "Check the CSV path too.")
    ]


# The phone ---------------------------------------------------------------------------------------


def test_phone_reads_a_timeline_without_folder_paths(linked):
    _, _, _, send, home, _, _, _ = linked
    overview = send("desktop.read", "overview")["value"]
    assert overview["kanban_comments"] is True
    created = add(home, "Phone", project_dir="/home/someone/secret-project")
    send(
        "desktop.action",
        "kanban.ticket",
        {"action": "comment", "scope": "rem", "ticket_id": created["id"], "body": "From my phone"},
    )
    value = send(
        "desktop.read",
        "kanban.timeline",
        {"ticket_id": created["id"], "scope": "rem", "organization_id": ""},
    )["value"]
    assert "/home/someone" not in json.dumps(value)
    assert value["ticket"]["title"] == "Phone" and value["can_comment"] is True
    assert [(c["author"], c["body"]) for c in value["comments"]] == [("user", "From my phone")]
    refused = send("desktop.read", "kanban.timeline", {"ticket_id": created["id"], "scope": "x"})
    assert refused["code"] == "invalid"
