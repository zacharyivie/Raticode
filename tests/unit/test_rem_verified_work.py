"""Usage › Verified work: first-pass acceptance, repairs, blocks and step-ins per period.

The history is seeded the way Raticode writes it: status entries from
``rem_ticket_activity.transition_entry`` and ``note_move``, a failed project check's own
feedback text, runs with ``repair_count`` and ledger calls tagged with their run.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.core.usage_history import resolve_range
from gofer.core.usage_ledger import record_invocation
from gofer.ui import rem_project_checks as project_checks
from gofer.ui import rem_self_work_windows as windows
from gofer.ui import rem_ticket_activity as activity
from gofer.ui import rem_verified_work as verified
from gofer.ui import turn_failures
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_recall import excerpt
from gofer.ui.rem_home_store import RemHomeStore
from tests.unit.test_cli_api_commands import backend as backend
from tests.unit.test_cli_api_commands import clean_environment as clean_environment
from tests.unit.test_cli_api_commands import ok, run
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe, wait_run

LINE = "1 of 3 tickets passed review first time"


def _run(identity: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": identity,
        "task_id": f"task-{identity}",
        "thread_id": f"thread-{identity}",
        "state": "succeeded",
        "summary_short": "Accepted.",
        "review_feedback": "",
        "recipe": {"acceptance_criteria": [{"id": "check", "description": "It works"}]},
        "criteria": [{"id": "check", "result": "pass"}],
        **fields,
    }


class Seeder:
    """Writes a ticket timeline as Raticode does, each entry at a chosen time."""

    def __init__(self, db: sqlite3.Connection, start: datetime) -> None:
        self.db, self.clock = db, start

    def _stamp(self) -> None:
        self.clock += timedelta(minutes=1)
        self.db.execute(
            "UPDATE kanban_comments SET body=json_set(body,'$.at',?) "
            "WHERE seq=(SELECT MAX(seq) FROM kanban_comments)",
            (self.clock.isoformat(),),
        )

    def follow(self, ticket: dict[str, Any], target: str, run: dict[str, Any]) -> None:
        """The ticket follows its run, as self-work's sync writes it."""
        entry = activity.transition_entry(ticket, target, run)
        before = ticket["status"]
        ticket.update(status=target, run_id=run["id"])
        activity.note_move(
            self.db,
            ticket,
            before,
            author="system",
            title=entry["title"],
            body=entry["body"],
            details=entry.get("details", ""),
            run=run,
        )
        self._stamp()

    def user_move(self, ticket: dict[str, Any], target: str, title: str) -> None:
        before = ticket["status"]
        ticket["status"] = target
        activity.note_move(self.db, ticket, before, author="user", title=title, body="By you.")
        self._stamp()

    def request_changes(self, ticket: dict[str, Any]) -> None:
        activity.add_comment(
            self.db, ticket["id"], author="user", title="Requested changes", body="Fix line 3."
        )
        self._stamp()

    def store_run(self, run: dict[str, Any], ticket_id: str, **fields: Any) -> None:
        body = {
            "id": run["id"],
            "task_id": run["task_id"],
            "self_work": {"ticket_id": ticket_id},
            "created_at": (self.clock - timedelta(seconds=30)).isoformat(),
            "repair_count": 0,
            "state": "succeeded",
            "thread_id": run["thread_id"],
            "recipe": {"title": f"Work for {ticket_id}"},
            "finished_at": self.clock.isoformat(),
            **fields,
        }
        self.db.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?)",
            (run["id"], run["task_id"], run["id"], "succeeded", json.dumps(body)),
        )


def _accept(seed: Seeder, ticket: dict[str, Any], run: dict[str, Any], repairs: list[str]) -> None:
    """Work, then one review or check send-back per item in ``repairs``, then Done."""
    seed.follow(ticket, "in_review", {**run, "state": "verifying"})
    for feedback in repairs:
        seed.follow(
            ticket, "in_progress", {**run, "state": "repairing", "review_feedback": feedback}
        )
        seed.follow(ticket, "in_review", {**run, "state": "verifying"})
    seed.follow(ticket, "done", run)


def seed_history(data: Path, start: datetime) -> dict[str, str]:
    """Three tickets accepted after 0, 1 and 2 repairs, and one blocked by its review.

    The ticket with two repairs first failed Raticode's own project check. The clean one's
    check failed once and passed on a rerun of the same commit (flaky). Returns run IDs.
    """
    store = RemHomeStore(data)
    failed_check = project_checks.feedback(
        {
            "folder": "/repo",
            "command": "python3 scripts/check-dev.py",
            "exit_code": 1,
            "output": "FAIL ruff",
        },
        "D-3",
    )
    with store.connect() as db:
        seed = Seeder(db, start)
        tickets = {
            name: {"id": f"ticket-{name}", "status": "todo", "number": n}
            for n, name in enumerate(("clean", "once", "twice", "stuck"), start=1)
        }
        runs = {name: _run(f"run-{name}") for name in tickets}
        seed.follow(tickets["clean"], "in_progress", runs["clean"])
        _accept(seed, tickets["clean"], runs["clean"], [])
        seed.store_run(
            runs["clean"],
            "ticket-clean",
            project_check={"passed": True, "flaky": True, "first_exit_code": 1},
            project_check_flakes=1,
        )
        seed.follow(tickets["once"], "in_progress", runs["once"])
        _accept(seed, tickets["once"], runs["once"], ["The review asked for a test."])
        # This run is gone, as finished runs are after 30 days: only its timeline remains.
        seed.follow(tickets["twice"], "in_progress", runs["twice"])
        _accept(seed, tickets["twice"], runs["twice"], [failed_check, "The review asked for docs."])
        seed.store_run(runs["twice"], "ticket-twice", repair_count=2)
        seed.follow(tickets["stuck"], "in_progress", runs["stuck"])
        seed.follow(tickets["stuck"], "in_review", {**runs["stuck"], "state": "verifying"})
        blocked = {
            **runs["stuck"],
            "state": "blocked",
            "summary_short": "The review still has unresolved checks.",
            "criteria": [{"id": "check", "result": "fail"}],
        }
        seed.follow(tickets["stuck"], "blocked", blocked)
        # The user stepped in once: they moved the blocked ticket back to To do by hand.
        seed.user_move(tickets["stuck"], "todo", "Moved to To do")
    for n, name in enumerate(("clean", "once", "twice")):
        record_invocation(
            invocation_id=f"call-{name}",
            provider="codex",
            data_dir=data,
            run_id=runs[name]["id"],
            metadata={"input_tokens": 1000 * (n + 1), "output_tokens": 0},
        )
    return {name: run["id"] for name, run in runs.items()}


def test_period_numbers_count_first_pass_repairs_blocks_and_step_ins(tmp_path: Path):
    now = datetime.now(UTC)
    seed_history(tmp_path, now - timedelta(days=2))
    numbers = verified.verified_work(tmp_path, resolve_range(period="30d", tz="UTC"))
    assert numbers["accepted"] == 3
    assert numbers["first_pass"] == 1 and numbers["first_pass_percent"] == 33
    assert numbers["line"] == LINE
    # 0 + 1 + 2 repairs over three accepted tickets.
    assert numbers["repairs"] == 3 and numbers["repairs_per_ticket"] == 1.0
    assert numbers["finished"] == 4
    assert numbers["blocked"] == 1 and numbers["blocked_percent"] == 25
    assert numbers["blocked_causes"] == [
        {"cause": "review", "label": "Review checks failed", "tickets": 1}
    ]
    # Two tickets show a check run; the one with two repairs failed it on its first try. The
    # clean ticket's flaky run is counted on its own: it neither failed first nor cost a repair.
    assert numbers["project_check"] == {
        "checked": 2,
        "failed_first": 1,
        "failed_first_percent": 50,
        "flaky": 1,
    }
    assert numbers["step_ins"] == {
        "total": 1,
        "request_changes": 0,
        "manual_moves": 1,
        "tickets": 1,
    }
    # 1000 + 2000 + 3000 tokens over three accepted tickets.
    assert numbers["tokens"] == {
        "total": 6000,
        "tickets": 3,
        "per_accepted": 2000,
        "partial": False,
    }
    assert numbers["scope"] == "rem_tickets" and numbers["range"]["key"] == "30d"
    assert sum(b["accepted"] for b in numbers["trend"]["buckets"]) == 3
    assert numbers["trend"]["bucket"] == "day"


def test_older_history_is_the_before_picture(tmp_path: Path):
    seed_history(tmp_path, datetime.now(UTC) - timedelta(days=60))
    recent = verified.verified_work(tmp_path, resolve_range(period="30d", tz="UTC"))
    assert recent["accepted"] == 0 and recent["line"] == "No tickets were accepted in this period."
    everything = verified.verified_work(tmp_path, resolve_range(period="all", tz="UTC"))
    assert everything["accepted"] == 3 and everything["line"] == LINE
    assert everything["history_since"] is not None
    assert everything["range"]["start"] == everything["history_since"]


def test_a_store_without_rem_history_shows_zeros(tmp_path: Path):
    numbers = verified.verified_work(tmp_path, resolve_range(period="7d", tz="UTC"))
    assert numbers["accepted"] == 0 and numbers["blocked_causes"] == []
    assert not (tmp_path / verified.STORE_FILE).exists()


def test_requested_changes_and_marking_done_by_hand_are_not_first_pass(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    with store.connect() as db:
        seed = Seeder(db, datetime.now(UTC) - timedelta(hours=5))
        landed = {"id": "t-landed", "status": "todo"}
        run = _run("r-landed")
        seed.follow(landed, "in_progress", run)
        seed.follow(landed, "in_review", {**run, "state": "verifying"})
        # Landing accepted work is the user's usual decision, not stepping in.
        seed.user_move(landed, "done", "Accepted and landed")
        by_hand = {"id": "t-hand", "status": "in_progress"}
        seed.user_move(by_hand, "done", "Marked done by hand")
        changed = {"id": "t-changed", "status": "todo"}
        other = _run("r-changed")
        seed.follow(changed, "in_progress", other)
        seed.request_changes(changed)
        _accept(seed, changed, other, [])
    numbers = verified.verified_work(tmp_path, resolve_range(period="24h", tz="UTC"))
    assert numbers["accepted"] == 3 and numbers["first_pass"] == 1
    assert numbers["accepted_by_hand"] == 1
    assert numbers["step_ins"]["request_changes"] == 1
    assert numbers["step_ins"]["manual_moves"] == 1


def test_blocks_after_two_failed_checks_or_decision_breaks_get_their_own_cause(tmp_path: Path):
    store = RemHomeStore(tmp_path)
    with store.connect() as db:
        seed = Seeder(db, datetime.now(UTC) - timedelta(hours=3))
        texts = {
            "check": "The project's check still fails after two repairs. FAIL pytest",
            "decision": "The work still breaks the project's decisions after two repairs.",
            "sleep": "",
        }
        for name, summary in texts.items():
            ticket = {"id": f"t-{name}", "status": "in_progress"}
            run = _run(f"r-{name}", state="blocked", summary_short=summary, criteria=[])
            if name == "sleep":
                # A worker that sleeps until a CI run ends waits; it isn't stuck.
                run.update(
                    state="queued",
                    waiting_for="CI run 7",
                    not_before="2999-01-01T00:00:00+00:00",
                    sleep={"until": "2999-01-01T00:00:00+00:00"},
                )
            seed.follow(ticket, "blocked", run)
    numbers = verified.verified_work(tmp_path, resolve_range(period="24h", tz="UTC"))
    assert {c["cause"]: c["tickets"] for c in numbers["blocked_causes"]} == {
        "project_check": 1,
        "decisions": 1,
    }


# Failed tasks' messages as rem_home writes them, oldest wording first where it changed.
FAILURES = {
    "no_activity": "The worker showed no activity for 20 minutes and was stopped. Its progress "
    "is saved in the thread; ask me to continue.",
    "review_idle": "The review showed no activity for 20 minutes and was stopped. Its progress "
    "is saved in the thread; ask me to continue.",
    "turn_limit": "The worker reached Rem's 120-minute turn limit and was stopped. Its progress "
    "is saved in the thread; ask me to continue.",
    "usage_limit": "The provider's usage limit was still reached after 6 waits for it to reset: "
    "You've hit your usage limit. Your limit resets 5am.",
    "transient": "The provider kept failing after 3 retries: API Error: 529 Overloaded",
    "provider_error": "The provider app stopped with an error: API Error: 400 invalid_request",
    "signal": "The provider was stopped by the system (SIGKILL). This usually means the "
    "computer ran out of memory, often because a test or build used too much.",
    "not_started": "Could not start 'claude' CLI: [Errno 2] No such file or directory",
    "prompt": "The worker couldn't start because its prompt exceeded the launch limit.",
    # An older entry, or a failure none of the phrases name: it stays an other failure.
    "failed": "The task couldn't finish.",
}


def _seed_failures(data: Path, start: datetime) -> None:
    store = RemHomeStore(data)
    with store.connect() as db:
        seed = Seeder(db, start)
        for name, summary in FAILURES.items():
            ticket = {"id": f"t-{name}", "status": "in_progress"}
            run = _run(f"r-{name}", state="blocked", summary_short=summary, criteria=[])
            seed.follow(ticket, "blocked", run)


def test_a_failed_task_is_counted_by_what_its_status_entry_says(tmp_path: Path):
    _seed_failures(tmp_path, datetime.now(UTC) - timedelta(hours=3))
    numbers = verified.verified_work(tmp_path, resolve_range(period="24h", tz="UTC"))
    causes = {c["cause"]: (c["label"], c["tickets"]) for c in numbers["blocked_causes"]}
    assert causes == {
        "no_activity": ("No activity for 20 minutes", 2),
        "turn_limit": ("Reached the 120-minute turn limit", 1),
        "usage_limit": ("Usage limit waits used up", 1),
        "provider_error": ("Provider error", 3),
        "not_started": ("Couldn't start", 2),
        "failed": ("Other failure", 1),
    }
    assert numbers["blocked"] == len(FAILURES)


def _blocked_cause(run: dict[str, Any]) -> str:
    """The cause Verified work reads from the status entry this blocked run gets."""
    entry = activity.transition_entry({"status": "in_progress"}, "blocked", run)
    assert entry["title"] == "Blocked: task failed", entry
    return verified._cause(
        verified._Entry(
            "t",
            datetime.now(UTC),
            "status",
            "system",
            "in_progress",
            "blocked",
            entry["title"],
            str(run["id"]),
            entry["details"][:80],
            entry["body"][:300],
        )
    )


def test_rem_homes_own_failure_messages_get_their_cause(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A real idle stop, turn limit, provider error and failed launch, as rem_home writes them."""
    import gofer.ui.rem_home as rem_home_module

    async def silent(**kwargs: Any) -> Any:
        while not kwargs["cancel_event"].is_set():
            await asyncio.sleep(0.01)
        yield {"type": "stopped"}

    def blocked(name: str, source: Any) -> dict[str, Any]:
        home.source = source
        task = home.create_task(
            {"request_id": name, **recipe(tmp_path / name, title=f"Task {name}")}
        )["task"]
        run = wait_run(home, task["id"], {"blocked", "succeeded", "outcome_unknown"})
        assert run["state"] == "blocked", run
        return run

    # The windows are shortened on both sides, so the message and the match agree.
    with monkeypatch.context() as patch:
        patch.setattr(rem_home_module, "TURN_IDLE_SECONDS", 0)
        patch.setattr(turn_failures, "TURN_IDLE_SECONDS", 0)
        assert _blocked_cause(blocked("idle", silent)) == "no_activity"
    with monkeypatch.context() as patch:
        patch.setattr(rem_home_module, "TURN_LIMIT_SECONDS", 0)
        patch.setattr(turn_failures, "TURN_LIMIT_SECONDS", 0)
        assert _blocked_cause(blocked("limit", silent)) == "turn_limit"

    async def provider_error(**kwargs: Any) -> Any:
        yield {"type": "error", "error": "API Error: 400 invalid_request", "errorKind": "provider"}

    run = blocked("provider", provider_error)
    assert run["summary_short"].startswith(turn_failures.PROVIDER_FAILED)
    assert _blocked_cause(run) == "provider_error"

    async def not_started(**kwargs: Any) -> Any:
        yield {"type": "error", "error": "Could not start 'claude' CLI: missing"}

    assert _blocked_cause(blocked("start", not_started)) == "not_started"


def test_cli_lists_each_failure_cause_in_json_and_text(backend):
    _seed_failures(backend.data, datetime.now(UTC) - timedelta(days=1))
    shown = ok("usage", "verified", "--range", "30d")
    causes = {c["cause"]: c["tickets"] for c in shown["blocked_causes"]}
    for cause in ("no_activity", "turn_limit", "usage_limit", "provider_error"):
        assert causes[cause] >= 1, cause
    code, text, _ = run(
        "usage", "verified", "--range", "30d", "--output", "text", json_output=False
    )
    assert code == 0
    assert "No activity for 20 minutes 2" in text
    assert "Usage limit waits used up 1" in text
    assert "Other failure 1" in text


def test_the_self_work_report_card_gets_the_line_but_its_chat_text_does_not(tmp_path: Path):
    start = datetime.now(UTC) - timedelta(hours=1)
    seed_history(tmp_path, start)
    store = RemHomeStore(tmp_path)
    with store.connect() as db:
        report = windows.build_report(
            db, {"id": "p1", "started_at": start.isoformat()}, datetime.now(UTC), "UTC"
        )
    assert report["verified_work"]["line"] == LINE
    assert report["verified_work"]["accepted"] == 3
    text = windows.report_text(report, datetime.now(UTC), "user")
    assert "first time" not in text
    message = {
        "id": "m",
        "role": "assistant",
        "kind": "self_work_report",
        "body": text,
        "self_work_report": report,
    }
    assert "first time" not in json.dumps(excerpt(message))


def test_verified_numbers_never_reach_rems_or_a_workers_prompt(home: RemHome, tmp_path: Path):
    """Rem's chat reply and a task's worker and reviewer see no verified-work numbers."""
    start = datetime.now(UTC) - timedelta(hours=1)
    seed_history(home.data_dir, start)
    with home.store.connect() as db:
        report = windows.build_report(
            db, {"id": "p1", "started_at": start.isoformat()}, datetime.now(UTC), "UTC"
        )
        assert report["verified_work"]["line"] == LINE
        home.store.message(
            db,
            windows.report_text(report, datetime.now(UTC), "user"),
            identity="self-work-period-p1",
            kind="self_work_report",
            self_work_report={**report, "ended_by": "user"},
        )
    prompts: list[str] = []

    def generate(prompt: str, config: Any, cancel: Any) -> dict[str, Any]:
        prompts.append(prompt)
        return {"reply": "Self-work finished three tickets."}

    home.generate = generate
    home.submit_message({"request_id": "ask", "body": "How did self-work do? Search self-work."})
    home._reply()
    source = home.source

    def recording(**kwargs: Any) -> Any:
        prompts.append(kwargs["agent_instructions"])
        prompts.append(json.dumps(kwargs.get("messages") or [], default=str))
        return source(**kwargs)

    home.source = recording
    task = home.create_task({"request_id": "create", **recipe(tmp_path)})["task"]
    wait_run(home, task["id"], {"succeeded"})
    assert len(prompts) >= 3
    # The report itself is in Rem's history; its verified line and numbers are not.
    assert any("Self-work report" in prompt for prompt in prompts[:1])
    for prompt in prompts:
        assert "first time" not in prompt
        assert "verified_work" not in prompt
        assert "first_pass" not in prompt


def test_cli_and_usage_overview_return_the_same_numbers(backend):
    """`Raticode usage verified` reads its own route; `usage show` carries the same section."""
    seed_history(backend.data, datetime.now(UTC) - timedelta(days=1))
    shown = ok("usage", "verified", "--range", "30d")
    assert shown["line"] == LINE and shown["accepted"] == 3 and shown["blocked"] == 1
    assert shown["repairs_per_ticket"] == 1.0
    overview = ok("usage", "show", "--range", "30d")["verified_work"]
    for key in ("accepted", "first_pass", "repairs", "blocked_causes", "step_ins", "tokens"):
        assert overview[key] == shown[key]
    custom = ok("usage", "verified", "--from", "2020-01-01", "--to", "2020-01-31")
    assert custom["accepted"] == 0 and custom["range"]["key"] == "custom"
    assert run("usage", "verified", "--range", "2y")[0] == 2
    code, text, _ = run("usage", "verified", "--output", "text", json_output=False)
    assert code == 0
    assert f"Verified work, Last 30 days: {LINE}" in text
    assert "Blocked: 1 of 4 finished tickets (Review checks failed 1)" in text
    assert "Project check failed on the first try: 1 of 2 checked tickets" in text
    assert "Flaky check runs (failed, then passed on a rerun of the same commit): 1" in text
    assert "Tokens per accepted ticket: 2,000" in text
