"""A worker's timed sleep for long outside waits (rem_home_sleep): park, free the slot, wake.

Every test uses the fake provider from test_rem_home and isolated temporary data. The fake
worker calls the real per-turn ``rem_wait`` tool server over HTTP, as a provider would.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import rem_home_sleep as sleeping
from gofer.ui import rem_ticket_activity as activity
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_models import RemConflict
from tests.unit.test_rem_home import force_run, recipe, wait_run
from tests.unit.test_rem_home import home as home
from tests.unit.test_swarm_tools import request


def call_sleep(kwargs: dict[str, Any], **arguments: Any) -> dict[str, Any]:
    """What a provider does when its worker calls rem_wait.sleep."""
    servers = kwargs["workflow"]["remResources"]["mcpServers"]
    server = next(s for s in servers if s["name"] == sleeping.SERVER_NAME)
    assert kwargs["trusted_rem_wait_url"] == server["url"]
    reply = request(
        server["url"],
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "sleep", "arguments": arguments},
        },
    )[1]["result"]
    return dict(reply)


def last_prompt(kwargs: dict[str, Any]) -> str:
    return str([m for m in kwargs["messages"] if m["role"] == "user"][-1]["body"])


def until_state(home: RemHome, check: Any, timeout: float = 5) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        home.tick()
        run = home.store.tasks()[0]["last_run"]
        if check(run):
            return dict(run)
        assert time.monotonic() < deadline, run
        time.sleep(0.01)


def sleepy_source(home: RemHome, wakes: list[str], sleeps: int = 1):
    """A worker that sleeps ``sleeps`` times for a CI run, then finishes; the review passes."""
    original = home.source
    calls = {"worker": 0, "review": 0}

    async def source(**kwargs: Any):
        if "independent read-only reviewer" in kwargs["agent_instructions"]:
            calls["review"] += 1
            # Reviews keep their own wait; they never get the sleep tool.
            assert all(
                s["name"] != sleeping.SERVER_NAME
                for s in kwargs["workflow"]["remResources"].get("mcpServers", [])
            )
            async for event in original(**kwargs):
                yield event
            return
        calls["worker"] += 1
        assert "Rem stops a turn after 20 minutes" in kwargs["agent_instructions"]
        assert "rem_wait.sleep" in kwargs["agent_instructions"]
        if calls["worker"] > 1:
            wakes.append(last_prompt(kwargs))
            # The same thread and native session continue; nothing resets them.
            assert kwargs["reset_session"] is False
        if calls["worker"] <= sleeps:
            reply = call_sleep(kwargs, waiting_for="CI run 4294", recheck_minutes=10)
            assert not reply.get("isError"), reply
            yield {"type": "final", "message": {"body": "CI run 4294 is still running."}}
            return
        async for event in original(**kwargs):
            yield event

    home.source = source
    return calls


def test_sleep_parks_the_run_frees_its_worker_and_wakes_in_the_same_thread(
    home: RemHome, tmp_path: Path
):
    wakes: list[str] = []
    calls = sleepy_source(home, wakes)
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    thread = task["active_run"]["thread_id"]
    run = until_state(home, sleeping.asleep)
    # Parked, not reviewed, not finished, with no worker held.
    assert calls == {"worker": 1, "review": 0}
    assert run["state"] == "queued" and run["thread_id"] == thread
    assert run["waiting_for"] == "CI run 4294" and run.get("worker_result") is None
    until = datetime.fromisoformat(run["not_before"])
    assert timedelta(minutes=9) < until - datetime.now(UTC) <= timedelta(minutes=10)
    assert run["sleep"]["minutes"] == 10 and run["sleep"]["wakes"] == 0
    assert run["step"] == "work"
    for _ in range(3):
        home.tick()
        time.sleep(0.02)
    assert run["id"] not in home.workers
    run = home.store.tasks()[0]["last_run"]
    assert run["state"] == "queued" and run["waiting_reason"].startswith("Sleeping until ")
    assert "waiting for CI run 4294" in run["waiting_reason"]
    assert calls["worker"] == 1

    # Time passes; the same thread wakes with Rem's message, finishes and is reviewed once.
    force_run(home, run["id"], not_before=datetime.now(UTC).isoformat())
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    assert calls == {"worker": 2, "review": 1}
    assert run["thread_id"] == thread
    assert "Your 10-minute wait for CI run 4294 is over" in wakes[0]
    assert "Wake 1 of at most 60" in wakes[0]
    assert run["sleep"]["asleep"] is False and run["sleep"]["wakes"] == 1


def test_repeated_sleeps_count_wakes_and_budget(home: RemHome, tmp_path: Path):
    wakes: list[str] = []
    calls = sleepy_source(home, wakes, sleeps=3)
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    for expected in (0, 1, 2):
        run = until_state(home, sleeping.asleep)
        assert run["sleep"]["wakes"] == expected
        # Push the park time back so each sleep counts its full ten minutes.
        record = {**run["sleep"], "parked_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
        force_run(home, run["id"], sleep=record, not_before=datetime.now(UTC).isoformat())
        until_state(home, lambda r: not sleeping.asleep(r))
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded"
    assert calls == {"worker": 4, "review": 1}
    assert run["sleep"]["wakes"] == 3 and run["sleep"]["slept_seconds"] == 30 * 60
    assert "30 minutes of the 12 hours wait budget used" in wakes[-1]


def test_wake_now_stop_and_follow_up(home: RemHome, tmp_path: Path):
    wakes: list[str] = []
    calls = sleepy_source(home, wakes)
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = until_state(home, sleeping.asleep)
    # Wake now is idempotent per request and refuses a run that isn't asleep.
    woken = home.wake_run(run["id"], {"request_id": "wake-1"})["run"]
    assert woken["sleep"]["woken_by"] == "user"
    assert datetime.fromisoformat(woken["not_before"]) <= datetime.now(UTC)
    assert home.wake_run(run["id"], {"request_id": "wake-1"})["run"]["id"] == run["id"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded"
    assert wakes[0].startswith("Rem: The user woke you early from your wait for CI run 4294.")
    with pytest.raises(RemConflict):
        home.wake_run(run["id"], {"request_id": "wake-2"})
    assert calls["worker"] == 2


def test_stop_ends_a_sleeping_run_without_a_worker(home: RemHome, tmp_path: Path):
    sleepy_source(home, [])
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = until_state(home, sleeping.asleep)
    home.store.mutate_run(run["id"], {"request_id": "stop"}, "cancel")
    run = wait_run(home, task["id"], {"cancelled", "blocked", "succeeded"})
    assert run["state"] == "cancelled"


def test_a_follow_up_wakes_a_sleeping_run_with_the_users_message(home: RemHome, tmp_path: Path):
    wakes: list[str] = []
    calls = sleepy_source(home, wakes)
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = until_state(home, sleeping.asleep)
    home.instruct(run["thread_id"], {"request_id": "follow", "body": "CI finished, carry on."})
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded"
    assert calls["worker"] == 2
    # The user's own words are the prompt, not a wake message, and the wait is over.
    assert wakes == ["User: CI finished, carry on."]
    assert run.get("sleep") is None


def test_a_sleep_survives_a_turn_that_then_went_silent(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import gofer.ui.rem_home as rem_home_module

    monkeypatch.setattr(rem_home_module, "TURN_IDLE_SECONDS", 0.3)

    async def source(**kwargs: Any):
        call_sleep(kwargs, waiting_for="CI run 4294", recheck_minutes=5)
        # The worker kept waiting in silence instead of ending its turn.
        while not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "stopped"}

    home.source = source
    home.create_task({"request_id": "create", **recipe(tmp_path)})
    run = until_state(
        home, lambda r: r["state"] != "executing" and r["state"] != "queued" or sleeping.asleep(r)
    )
    assert sleeping.asleep(run), run
    assert run["sleep"]["minutes"] == 5


def test_restart_honors_a_sleep_asked_for_before_the_turn_ended(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    pending, _ = sleeping.request(
        run, {"waiting_for": "CI", "recheck_minutes": 15}, datetime.now(UTC)
    )
    force_run(
        home,
        run["id"],
        state="executing",
        started_at=datetime.now(UTC).isoformat(),
        pending_sleep=pending,
        active_thread_id=run["thread_id"],
        active_turn_id="lost-turn",
    )
    home._recover()
    with home.store.read() as db:
        recovered = home.store.get(db, "runs", run["id"])
    assert sleeping.asleep(recovered)
    assert recovered["sleep"]["recovered"] is True
    assert not recovered.get("recovery_inspection")
    fields, message = sleeping.wake(recovered, datetime.now(UTC))
    assert message.startswith("Raticode restarted after you asked to sleep")


def test_sleep_tool_checks_its_bounds_and_its_epoch(home: RemHome, tmp_path: Path):
    now = datetime.now(UTC)
    run = {"epoch": 0}
    for arguments, message in [
        ({"waiting_for": "", "recheck_minutes": 10}, "Say what you are waiting for"),
        ({"waiting_for": "CI", "recheck_minutes": 4}, "between 5 and 120"),
        ({"waiting_for": "CI", "recheck_minutes": 121}, "between 5 and 120"),
        ({"waiting_for": "CI", "recheck_minutes": 7.5}, "whole number"),
        ({"waiting_for": "CI", "recheck_minutes": True}, "whole number"),
        ({"waiting_for": "CI", "recheck_minutes": 10, "max_total_hours": 0}, "positive"),
        ({"waiting_for": "CI", "recheck_minutes": 10, "max_total_hours": 193}, "at most 192"),
        ({"waiting_for": "CI", "recheck_minutes": 10, "shell": "rm"}, "takes waiting_for"),
    ]:
        with pytest.raises(ValueError, match=message):
            sleeping.request(run, arguments, now)
    pending, answer = sleeping.request(run, {"waiting_for": "CI", "recheck_minutes": 120}, now)
    assert pending["budget_hours"] == 12 and answer["recorded"] is True
    # The budget counts every sleep of the run, and so does the wake limit.
    spent = {"epoch": 0, "sleep": {"slept_seconds": 11.5 * 3600, "wakes": 4}}
    with pytest.raises(ValueError, match="wait budget of 12 hours"):
        sleeping.request(spent, {"waiting_for": "CI", "recheck_minutes": 60}, now)
    sleeping.request(spent, {"waiting_for": "CI", "recheck_minutes": 30}, now)
    with pytest.raises(ValueError, match="slept 60 times"):
        sleeping.request(
            {"epoch": 0, "sleep": {"wakes": 60}}, {"waiting_for": "CI", "recheck_minutes": 5}, now
        )

    # A Stop or edit during the turn voids the grant.
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    live = task["active_run"]
    with sleeping.sleep_tools(home, live, "execute") as server:
        assert server is not None
        kwargs = {
            "workflow": {"remResources": {"mcpServers": [server]}},
            "trusted_rem_wait_url": server["url"],
        }
        force_run(home, live["id"], epoch=live["epoch"] + 1)
        reply = call_sleep(kwargs, waiting_for="CI", recheck_minutes=10)
        assert reply["isError"] is True and "revoked" in reply["content"][0]["text"]
    with sleeping.sleep_tools(home, live, "verify") as server:
        assert server is None
    reminder = {**live, "recipe": {**live["recipe"], "type": "reminder"}}
    assert not sleeping.offered(reminder, "execute")


def test_a_sleep_past_the_budget_blocks_the_run(home: RemHome, tmp_path: Path):
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = task["active_run"]
    pending = {"epoch": run["epoch"], "waiting_for": "CI run 4294", "minutes": 60}
    force_run(
        home,
        run["id"],
        state="executing",
        pending_sleep={**pending, "budget_hours": 1},
        sleep={"slept_seconds": 30 * 60, "wakes": 3, "waiting_for": "CI run 4294"},
    )
    assert home._park_sleeping(run["id"], run["epoch"]) is True
    finished = wait_run(home, task["id"], {"blocked"})
    assert finished["summary_short"].startswith("Waited 30 minutes for CI run 4294")


def test_a_sleeping_ticket_is_blocked_once_and_resumes_in_its_thread():
    at = datetime.now(UTC)
    run: dict[str, Any] = {
        "id": "run-1",
        "epoch": 2,
        "task_id": "task-1",
        "thread_id": "thread-1",
        "state": "queued",
        "not_before": (at + timedelta(minutes=10)).isoformat(),
        "waiting_for": "CI run 4294",
        "sleep": {"asleep": True, "waiting_for": "CI run 4294", "wakes": 1},
    }
    assert activity.run_phase(run, at) == "waiting"
    assert activity.target_status({"status": "in_progress"}, run, at) == "blocked"
    headline, body, _ = activity.block_reason(run, at)
    assert headline == "Sleeping"
    assert "waiting for CI run 4294" in body and "holds no worker" in body
    # Every sleep of one stretch of work shares one sync key, so its entry is written once.
    later = {**run, "not_before": (at + timedelta(minutes=40)).isoformat()}
    assert activity.sync_key(run, at) == activity.sync_key(later, at)
    awake = {**run, "state": "executing", "sleep": {**run["sleep"], "asleep": False}}
    entry = activity.transition_entry(
        {"status": "blocked", "run_id": "run-1"}, "in_progress", awake, at=at
    )
    assert entry["title"] == "Moved to In progress: task resumed"
    assert "woke from its sleep" in entry["body"]
    # A review's proof wait keeps its own wording.
    proof = {**run, "sleep": None, "resume_review": True}
    assert activity.block_reason(proof, at)[0] == "Waiting"


def test_sleep_shows_on_phone_cli_and_rem_context(home: RemHome, tmp_path: Path):
    from gofer.cli.commands.rem_api import _task_line
    from gofer.devices.desktop_control import DesktopControl

    sleepy_source(home, [])
    home.create_task({"request_id": "create", **recipe(tmp_path)})
    run = until_state(home, sleeping.asleep)
    task = home.store.tasks()[0]
    phone = DesktopControl._run(task["active_run"])
    assert phone is not None and phone["sleep"]["waiting_for"] == "CI run 4294"
    assert phone["sleep"]["until"] == run["sleep"]["until"]
    assert phone["sleep"]["max_wakes"] == 60 and phone["sleep"]["budget_minutes"] == 720
    line = _task_line(task)
    assert " sleeping " in line and "waiting for CI run 4294, next check" in line
    # An awake run sends no sleep, so an older phone and a new one both read its state.
    awake = DesktopControl._run({**task["active_run"], "state": "executing"})
    assert awake is not None and awake["sleep"] is None


def test_codex_approves_only_the_turns_own_sleep_server(tmp_path: Path):
    from gofer.core.prompt_envelope import AgentResources
    from gofer.ui.chat import _build_chat_command

    url = "http://127.0.0.1:1234/private-capability"
    resources = AgentResources.model_validate({"mcpServers": [{"name": "rem_wait", "url": url}]})
    options: dict[str, Any] = dict(
        provider="codex",
        model="cli-default",
        prompt="Work",
        data_dir=tmp_path,
        working_dir=tmp_path,
        resources=resources,
        permission_mode="workspace-write",
    )
    trusted = _build_chat_command(**options, trusted_rem_wait_url=url)
    untrusted = _build_chat_command(**options, trusted_rem_wait_url=url + "-other")
    assert any('tools.sleep.approval_mode="approve"' in arg for arg in trusted)
    assert not any('tools.sleep.approval_mode="approve"' in arg for arg in untrusted)


def test_rem_wait_is_a_reserved_connection_name():
    from gofer.core.rem_library import RESERVED_MCP_NAMES

    assert sleeping.SERVER_NAME in RESERVED_MCP_NAMES


def test_pausing_a_sleeping_run_keeps_its_wait(home: RemHome, tmp_path: Path):
    """Pause beats sleep: a paused sleeper keeps its wait, and on resume sleeps out the rest."""
    wakes: list[str] = []
    calls = sleepy_source(home, wakes)
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    run = until_state(home, sleeping.asleep)
    until = run["not_before"]
    home.pause_run(run["id"], {"request_id": "pause"})
    run = until_state(home, lambda r: r["state"] == "paused")
    assert run["sleep"]["asleep"] is True and run["not_before"] == until
    home.resume_run(run["id"], {"request_id": "resume"})
    run = until_state(home, lambda r: r["state"] == "queued")
    # Its time hasn't come, so it is still asleep and holds no worker.
    assert sleeping.asleep(run) and run["not_before"] == until
    home.tick()
    assert run["id"] not in home.workers and calls["worker"] == 1
    force_run(home, run["id"], not_before=datetime.now(UTC).isoformat())
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded" and calls == {"worker": 2, "review": 1}
    assert "wait for CI run 4294 is over" in wakes[0]


def test_a_sleeping_run_holds_no_self_work_thread(home: RemHome, tmp_path: Path):
    from gofer.ui.rem_self_work import _holds_thread

    sleepy_source(home, [])
    home.create_task({"request_id": "create", **recipe(tmp_path)})
    with home.store.read() as db:
        run = home.store.get(db, "runs", until_state(home, sleeping.asleep)["id"])
    assert _holds_thread(run, datetime.now(UTC)) is False
    # Once its check time comes, it needs a worker again.
    assert _holds_thread({**run, "not_before": datetime.now(UTC).isoformat()}, datetime.now(UTC))
