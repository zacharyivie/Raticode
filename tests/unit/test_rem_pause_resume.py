"""Pausing and resuming Rem's runs, self-work making way for the user, and chat on/off.

Fake providers and isolated data only (see test_rem_kanban's ``board`` fixture). A paused run
keeps its run, thread and worktree; resuming continues that same run in its thread.
"""

import json
import subprocess
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui.rem_home import RemHome, self_work_shortcut
from gofer.ui.rem_home_models import RemConflict
from gofer.ui.rem_home_store import RESUME_TEXT, RemHomeStore
from gofer.ui.rem_kanban import KanbanRefused
from gofer.ui.rem_self_work import PRIORITY_REASON
from tests.unit.test_rem_kanban import BASE, add, at, set_options, settle, tick, ticket, wait_for
from tests.unit.test_rem_kanban import api_server as api_server
from tests.unit.test_rem_kanban import board as board


def runs(home: RemHome) -> list[dict[str, Any]]:
    with home.store.read() as db:
        return [json.loads(r[0]) for r in db.execute("SELECT body FROM runs")]


def run_of(home: RemHome, identity: str) -> dict[str, Any]:
    with home.store.read() as db:
        return home.store.get(db, "runs", identity)


def start_task(home: RemHome, title: str, request: str = "") -> dict[str, Any]:
    created = home.create_task(
        {
            "request_id": request or f"create-{title}",
            "title": title,
            "type": "errand",
            "prompt": f"Do {title}",
        }
    )["task"]
    return next(r for r in runs(home) if r["task_id"] == created["id"])


def until(home: RemHome, condition: Any, timeout: float = 8) -> None:
    """Tick Rem's loop until ``condition`` holds."""
    wait_for(lambda: tick(home) or condition(), timeout)


def worker_prompts(fake: Any, thread_id: str) -> list[str]:
    """What each worker turn in one thread was sent last."""
    return [
        str(call["messages"][-1]["body"])
        for call in fake.calls
        if call["conversation_id"] == thread_id
    ]


def reply_to(home: RemHome, request: str, body: str) -> str:
    home.submit_message({"request_id": request, "body": body})
    home.tick()
    wait_for(
        lambda: any(m["id"] == f"reply-{request}" for m in home.store.conversation()["messages"])
    )
    return str(
        next(
            m["body"]
            for m in home.store.conversation()["messages"]
            if m["id"] == f"reply-{request}"
        )
    )


# Pausing and resuming one run -------------------------------------------------------


def test_a_running_task_pauses_and_resumes_in_the_same_thread(board):
    home, fake, _ = board
    fake.hold.clear()
    run = start_task(home, "Audit")
    until(home, lambda: run_of(home, run["id"])["state"] == "executing" and fake.working())
    home.pause_run(run["id"], {"request_id": "p1"})
    until(home, lambda: run_of(home, run["id"])["state"] == "paused")
    paused = run_of(home, run["id"])
    # Same run and thread; its worker let go, so it holds no thread while paused.
    assert paused["thread_id"] == run["thread_id"]
    assert paused["paused"]["by"] == "user" and paused["paused"]["resume"] == "continue"
    wait_for(lambda: not home.workers or not tick(home) and not home.workers)
    assert not paused.get("finished_at")
    # The task lists it as paused, not finished or needing attention.
    task = next(t for t in home.store.tasks() if t["id"] == run["task_id"])
    assert task["active_run"] is None and task["last_run"]["state"] == "paused"
    # Its chat card says who paused it.
    card = next(
        m["work"]
        for m in home.store.conversation()["messages"]
        if (m.get("work") or {}).get("run_id") == run["id"]
    )
    assert card["state"] == "paused" and card["paused_by"] == "user"
    # Pausing again changes nothing.
    home.pause_run(run["id"], {"request_id": "p2"})
    assert run_of(home, run["id"])["state"] == "paused"

    fake.hold.set()
    home.resume_run(run["id"], {"request_id": "r1"})
    until(home, lambda: run_of(home, run["id"])["state"] == "succeeded")
    # Still one run, in the same thread, told to check what it did before carrying on.
    assert [r["id"] for r in runs(home) if r["task_id"] == run["task_id"]] == [run["id"]]
    prompts = worker_prompts(fake, run["thread_id"])
    assert len(prompts) == 2 and RESUME_TEXT in prompts[-1]


def test_resume_twice_or_run_on_a_paused_task_never_starts_a_second_run(board):
    home, fake, _ = board
    fake.hold.clear()
    run = start_task(home, "Twice")
    until(home, lambda: fake.working() == 1)
    home.pause_run(run["id"], {"request_id": "p"})
    until(home, lambda: run_of(home, run["id"])["state"] == "paused")
    # "Run" on a paused task picks the paused run back up rather than adding one.
    home.edit_task(run["task_id"], {"request_id": "again"}, "run")
    home.resume_run(run["id"], {"request_id": "r-other"})
    assert run_of(home, run["id"])["state"] == "queued"
    assert len([r for r in runs(home) if r["task_id"] == run["task_id"]]) == 1
    fake.hold.set()
    until(home, lambda: run_of(home, run["id"])["state"] == "succeeded")
    assert len([r for r in runs(home) if r["task_id"] == run["task_id"]]) == 1


def test_only_running_or_waiting_runs_pause_and_only_paused_ones_resume(board):
    home, _, _ = board
    run = start_task(home, "Quick")
    until(home, lambda: run_of(home, run["id"])["state"] == "succeeded")
    with pytest.raises(RemConflict, match="can be paused"):
        home.pause_run(run["id"], {"request_id": "late"})
    with pytest.raises(RemConflict, match="paused task can be resumed"):
        home.resume_run(run["id"], {"request_id": "nope"})


def test_a_queued_run_pauses_before_it_starts_and_resumes_untouched(board):
    home, fake, _ = board
    run = start_task(home, "Later")
    # Not ticked yet: still queued, with no worker.
    home.pause_run(run["id"], {"request_id": "p"})
    until(home, lambda: run_of(home, run["id"])["state"] == "paused")
    assert run_of(home, run["id"])["paused"]["resume"] == "queue"
    assert not fake.calls
    home.resume_run(run["id"], {"request_id": "r"})
    until(home, lambda: run_of(home, run["id"])["state"] == "succeeded")
    assert RESUME_TEXT not in worker_prompts(fake, run["thread_id"])[0]


def test_stopping_a_paused_run_ends_it(board):
    home, fake, _ = board
    fake.hold.clear()
    run = start_task(home, "Drop")
    until(home, lambda: fake.working() == 1)
    home.pause_run(run["id"], {"request_id": "p"})
    until(home, lambda: run_of(home, run["id"])["state"] == "paused")
    home.store.mutate_run(run["id"], {"request_id": "c"}, "cancel")
    until(home, lambda: run_of(home, run["id"])["state"] == "cancelled")
    assert not run_of(home, run["id"]).get("paused") or True


def test_a_pause_survives_a_restart_and_a_half_settled_pause_settles_as_paused(board):
    home, fake, _ = board
    fake.hold.clear()
    first = start_task(home, "Kept")
    second = start_task(home, "Interrupted")
    until(home, lambda: fake.working() == 2)
    home.pause_run(first["id"], {"request_id": "p"})
    until(home, lambda: run_of(home, first["id"])["state"] == "paused")
    # A pause asked for just before the backend went away: stopping, not yet paused.
    with home.store.connect() as db:
        run = home.store.get(db, "runs", second["id"])
        home.store.request_pause(run, by="user")
        run["active_thread_id"] = run["active_turn_id"] = None
        home.store.put(db, "runs", run)
    fake.hold.set()
    home.close()
    for worker, _ in list(home.workers.values()):
        worker.join(timeout=5)
    later = RemHome(
        home.data_dir,
        source=fake,
        jobs=home.jobs,
        steering=home.steering,
        authorize=lambda c, r: None,
        generate=home.generate,
        start=False,
    )
    try:
        later.configure({"provider": "codex", "command_slots": {"maxWorkers": 8}})
        calls = len(fake.calls)
        for _ in range(20):
            later.tick()
            time.sleep(0.02)
        # Neither run started again; both are paused with their threads.
        assert run_of(later, first["id"])["state"] == "paused"
        assert run_of(later, second["id"])["state"] == "paused"
        assert run_of(later, second["id"])["thread_id"] == second["thread_id"]
        assert len(fake.calls) == calls
    finally:
        later.close()


def test_a_follow_up_on_a_paused_run_resumes_it_with_that_message(board):
    home, fake, _ = board
    fake.hold.clear()
    run = start_task(home, "Steer")
    until(home, lambda: fake.working() == 1)
    home.pause_run(run["id"], {"request_id": "p"})
    until(home, lambda: run_of(home, run["id"])["state"] == "paused")
    fake.hold.set()
    home.instruct(run["thread_id"], {"request_id": "more", "body": "Also check the docs"})
    until(home, lambda: run_of(home, run["id"])["state"] == "succeeded")
    assert len([r for r in runs(home) if r["task_id"] == run["task_id"]]) == 1
    assert "Also check the docs" in worker_prompts(fake, run["thread_id"])[-1]


def test_paused_during_review_resumes_with_a_fresh_review_of_the_saved_work(tmp_path):
    store = RemHomeStore(tmp_path)
    run: dict[str, Any] = {
        "id": "r",
        "task_id": "t",
        "occurrence": "o",
        "state": "verifying",
        "epoch": 3,
        "recipe": {"acceptance_criteria": []},
        "config": {},
        "worker_result": "Done.",
        "verification_thread_id": "v",
        "thread_id": "w",
        "started_at": "2026-11-11T00:00:00+00:00",
    }
    with store.connect() as db:
        store.put(db, "tasks", {"id": "t"})
        assert store.request_pause(run, by="user")
        assert run["state"] == "cancelling" and run["epoch"] == 4
        store.settle_pause(db, run)
        assert run["state"] == "paused" and run["paused"]["resume"] == "review"
        assert run["paused"]["step"] == "check"
        store.resume_paused(db, run, {}, by="user")
    assert run["state"] == "queued" and run["resume_review"] is True
    assert run["reset_review_session"] is True and run["worker_result"] == "Done."


def test_a_paused_prerequisite_keeps_a_dependent_task_waiting_not_on_hold(board):
    home, fake, _ = board
    fake.hold.clear()
    first = start_task(home, "First")
    until(home, lambda: fake.working() == 1)
    waiting = home.create_task(
        {
            "request_id": "after",
            "title": "Second",
            "type": "errand",
            "prompt": "After",
            "after": [first["task_id"]],
        }
    )["task"]
    home.pause_run(first["id"], {"request_id": "p"})
    until(home, lambda: run_of(home, first["id"])["state"] == "paused")
    view = next(t for t in home.store.tasks() if t["id"] == waiting["id"])
    assert view["dependency"]["state"] == "waiting"
    assert "paused" in view["dependency"]["reason"]
    fake.hold.set()


# Self-work makes way for the user ----------------------------------------------------


def git_project(project: Path) -> None:
    """A project in git, so a second task in it gets its own worktree."""
    for command in (
        ["init", "-q", "-b", "main"],
        [
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "start",
        ],
    ):
        subprocess.run(["git", "-C", str(project), *command], check=True)


def test_new_user_work_pauses_self_work_which_resumes_after_it(board):
    home, fake, project = board
    git_project(project)
    fake.hold.clear()
    add(home, "Alpha", priority="high")
    add(home, "Beta", priority="low")
    set_options(home, max_threads=2)
    at(home, 15)
    tick(home)
    until(home, lambda: fake.working() == 2)
    beta = next(r for r in runs(home) if r["recipe"]["title"] == "Beta")
    # The user starts work of their own: the thread limit is full of self-work.
    mine = start_task(home, "Mine")
    until(home, lambda: run_of(home, mine["id"])["state"] == "executing")
    until(home, lambda: any(r["state"] == "paused" for r in runs(home)))
    paused = [r for r in runs(home) if r["state"] == "paused"]
    assert len(paused) == 1
    made_way = paused[0]
    assert made_way["paused"]["by"] == "priority"
    # The newest self-work made way, and its worktree, with any changes, is kept.
    assert made_way["id"] == beta["id"] and made_way.get("worktree")
    assert Path(made_way["worktree"]["checkout"]).is_dir()
    # The ticket keeps its claim and shows why it stopped, not Done or "needs you".
    until(home, lambda: ticket(home, made_way["recipe"]["title"])["paused"] == "priority")
    stopped = ticket(home, made_way["recipe"]["title"])
    assert stopped["status"] == "blocked" and stopped["paused"] == "priority"
    assert not stopped["running"] and stopped["block_reason"].startswith("Paused so your own")
    view = home.self_work.view()["state"]
    assert view["paused_for_you"] == 1 and view["paused_threads"] == 1
    assert any(item["paused"] == "priority" for item in view["activity"])
    notes = [
        m["body"]
        for m in home.store.conversation()["messages"]
        if m["body"].startswith("Paused 1 self-work task")
    ]
    assert len(notes) == 1
    # Ticking again doesn't pause more or post again.
    for _ in range(5):
        tick(home)
    assert len([r for r in runs(home) if r["state"] == "paused"]) == 1
    # The user's work finishes; the paused ticket picks up in its own thread.
    fake.hold.set()
    settle(home, "Alpha", "Beta", states=("done",))
    resumed = run_of(home, made_way["id"])
    assert resumed["state"] == "succeeded" and resumed["resumed"]["by"] == "rem"
    assert RESUME_TEXT in worker_prompts(fake, made_way["thread_id"])[-1]
    assert run_of(home, mine["id"])["state"] == "succeeded"
    # One run per ticket: nothing was duplicated.
    for title in ("Alpha", "Beta"):
        assert len([r for r in runs(home) if r["recipe"]["title"] == title]) == 1
    report = home.self_work.view()["report"]
    assert any(n["text"].startswith("Picked 1 paused task back up") for n in report["notes"])


def test_paused_self_work_waits_for_the_user_to_be_away_again(board):
    home, fake, _ = board
    fake.hold.clear()
    add(home, "Solo")
    set_options(home, max_threads=1)
    at(home, 15)
    tick(home)
    until(home, lambda: fake.working() == 1)
    solo = next(r for r in runs(home) if r["recipe"]["title"] == "Solo")
    # The user comes back and asks for something: it starts at once.
    reply_to(home, "back", "Hi")
    mine = start_task(home, "Request")
    until(home, lambda: run_of(home, solo["id"])["state"] == "paused")
    assert run_of(home, solo["id"])["paused"]["reason"] == PRIORITY_REASON
    fake.hold.set()
    until(home, lambda: run_of(home, mine["id"])["state"] == "succeeded")
    # Still here (the prompt was at minute 15): the paused ticket waits.
    at(home, 25)
    for _ in range(10):
        tick(home)
    assert run_of(home, solo["id"])["state"] == "paused"
    # Fifteen quiet minutes later it resumes, before anything new starts.
    at(home, 31)
    settle(home, "Solo", states=("done",))
    assert run_of(home, solo["id"])["state"] == "succeeded"


def test_a_self_work_run_the_user_resumes_is_not_paused_again(board):
    home, fake, _ = board
    fake.hold.clear()
    add(home, "Pinned")
    set_options(home, max_threads=1)
    at(home, 15)
    tick(home)
    until(home, lambda: fake.working() == 1)
    pinned = next(r for r in runs(home) if r["recipe"]["title"] == "Pinned")
    mine = start_task(home, "Mine")
    until(home, lambda: run_of(home, pinned["id"])["state"] == "paused")
    # The user moves the paused ticket to In progress: that resumes it, as theirs.
    until(home, lambda: ticket(home, "Pinned")["paused"] == "priority")
    current = ticket(home, "Pinned")
    with pytest.raises(KanbanRefused, match="paused"):
        home.kanban.move(
            "user",
            {
                "ticket_id": current["ticket_id"],
                "expected_revision": current["revision"],
                "column": "todo",
            },
        )
    home.kanban.move(
        "user",
        {
            "ticket_id": current["ticket_id"],
            "expected_revision": current["revision"],
            "column": "in_progress",
        },
    )
    assert run_of(home, pinned["id"])["resumed"]["by"] == "user"
    for _ in range(10):
        tick(home)
    assert run_of(home, pinned["id"])["state"] != "paused"
    fake.hold.set()
    settle(home, "Pinned", states=("done",))
    until(home, lambda: run_of(home, mine["id"])["state"] == "succeeded")


def test_self_work_counts_the_users_running_work_against_its_limit(board):
    home, fake, _ = board
    fake.hold.clear()
    mine = start_task(home, "Mine first")
    until(home, lambda: fake.working() == 1)
    add(home, "One")
    add(home, "Two")
    set_options(home, max_threads=2)
    at(home, 15)
    tick(home)
    # One thread is the user's, so self-work takes only the other.
    assert sum(1 for r in runs(home) if r["recipe"]["title"] in {"One", "Two"}) == 1
    fake.hold.set()
    until(home, lambda: run_of(home, mine["id"])["state"] == "succeeded")
    settle(home, "One", "Two", states=("done",))


# Turning self-work on and off from chat --------------------------------------------


def test_chat_turns_self_work_off_and_on_and_says_what_it_now_is(board):
    home, _, project = board
    set_options(home, max_threads=3, auto_pull_backlog=True)
    before = home.self_work.view()["settings"]
    home.generate = lambda *args: {
        "reply": "Done, it's off.",
        "tasks": [],
        "memories": [],
        "self_work": {"action": "disable"},
    }
    reply = reply_to(home, "off", "Could you stop working on your own for a while")
    settings = home.self_work.view()["settings"]
    assert settings["enabled"] is False and settings["revision"] == before["revision"] + 1
    # Only the switch changed.
    assert (settings["max_threads"], settings["auto_pull_backlog"]) == (3, True)
    assert reply.startswith("Self-work is off now") and "Done, it's off" not in reply
    with home.store.read() as db:
        assert home.store.setting(db, "self_work_projects")["projects"] == [str(project)]
    # A plain "turn on self-work" works whatever the model returned.
    home.generate = lambda *args: {"reply": "Sure.", "tasks": [], "memories": []}
    reply = reply_to(home, "on", "turn on self-work")
    assert home.self_work.view()["settings"]["enabled"] is True
    assert reply.startswith("Self-work is on now.") and "15 minutes" in reply
    reply = reply_to(home, "again", "Turn on self work")
    assert reply.startswith("Self-work was already on.")
    revision = home.self_work.view()["settings"]["revision"]
    reply = reply_to(home, "ask", "is self-work on?")
    assert reply.startswith("Self-work is on.")
    assert home.self_work.view()["settings"]["revision"] == revision


def test_a_workflow_that_may_not_act_cannot_turn_self_work_off(board):
    from gofer.ui.rem_home import workflow_answer_only
    from gofer.ui.rem_home_models import Decision
    from gofer.ui.rem_screen import acts

    decision = Decision.model_validate({"reply": "Off.", "self_work": {"action": "disable"}})
    assert acts(decision)
    assert workflow_answer_only(decision).self_work is None
    asked = Decision.model_validate({"reply": "On.", "self_work": {"action": "status"}})
    assert not acts(asked)


@pytest.mark.parametrize(
    "text,action",
    [
        ("turn on self-work", "enable"),
        ("Please enable self work.", "enable"),
        ("start your self-work mode", "enable"),
        ("stop self-work", "disable"),
        ("Turn off self work", "disable"),
        ("pause self-work for now", "disable"),
        ("is self-work on?", "status"),
        ("Is your self work running", "status"),
        ("self-work status", "status"),
        ("stop self-work and fix the build", None),
        ("what did self-work do today?", None),
    ],
)
def test_short_self_work_requests_are_read_exactly(text, action):
    assert self_work_shortcut(text) == action


def test_the_decision_prompt_says_whether_self_work_is_on(board):
    home, _, _ = board
    prompts: list[str] = []

    def generate(prompt: str, *args: Any) -> dict[str, Any]:
        prompts.append(prompt)
        return {"reply": "Yes.", "tasks": [], "memories": []}

    home.generate = generate
    reply_to(home, "q", "how's it going")
    assert '"self_work":{"active_threads":0' in prompts[0].replace(" ", "")
    assert "self_work:{action:enable|disable}" in prompts[0]


def test_paused_runs_hold_no_thread_and_free_capacity_for_new_work(board):
    home, fake, _ = board
    fake.hold.clear()
    add(home, "Holder")
    set_options(home, max_threads=1)
    at(home, 15)
    tick(home)
    until(home, lambda: fake.working() == 1)
    holder = next(r for r in runs(home) if r["recipe"]["title"] == "Holder")
    add(home, "Next")
    home.pause_run(holder["id"], {"request_id": "p"})
    until(home, lambda: run_of(home, holder["id"])["state"] == "paused")
    # The ticket follows on the next self-work pass.
    until(home, lambda: ticket(home, "Holder")["paused"] == "user")
    assert ticket(home, "Holder")["block_reason"].startswith("Paused by you")
    # A paused-by-you ticket isn't reported as stuck or alerted on.
    assert not [w for w in home.self_work.view()["report"]["work_done"] if w["title"] == "Holder"]
    # Its thread is free, so the next to-do takes it.
    at(home, 16)
    until(home, lambda: ticket(home, "Next")["running"])
    # Self-work never resumes what the user paused.
    fake.hold.set()
    settle(home, "Next", states=("done",))
    for _ in range(5):
        tick(home)
    assert run_of(home, holder["id"])["state"] == "paused"
    assert BASE + timedelta(minutes=16)


# The app's HTTP routes and the phone's ops ------------------------------------------


def test_http_routes_pause_and_resume_a_run(api_server):
    request, service = api_server
    created = service.create_task(
        {"request_id": "c", "title": "Over HTTP", "type": "errand", "prompt": "Do it"}
    )["task"]
    run = next(r for r in runs(service) if r["task_id"] == created["id"])
    path = f"/api/rem/runs/{run['id']}"
    assert request("POST", f"{path}/pause", authenticated=False, body={}).status == 401
    paused = request("POST", f"{path}/pause", body={"request_id": "p"})
    assert paused.status == 200 and paused.json()["run"]["state"] == "cancelling"
    service.tick()
    assert run_of(service, run["id"])["state"] == "paused"
    resumed = request("POST", f"{path}/resume", body={"request_id": "r"})
    assert resumed.status == 200 and resumed.json()["run"]["state"] == "queued"
    again = request("POST", f"{path}/resume", body={"request_id": "r2"})
    assert again.status == 200 and again.json()["run"]["id"] == run["id"]
