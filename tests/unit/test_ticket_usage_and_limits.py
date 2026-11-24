"""Usage per model call, run and thread; ticket totals; and self-work's token and rate limits."""

import json
import sqlite3
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import gofer.ui.rem_home as rem_home_module
from gofer.core.usage_ledger import (
    empty_usage,
    record_invocation,
    run_usage,
    track_chat_usage,
    usage_db,
    usage_label,
    usage_scope,
)
from gofer.core.usage_service import stored_windows, window_used_percent
from gofer.ui.chat_jobs import ChatJobs
from gofer.ui.chat_steering import ChatSteering
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_kanban import KanbanRefused, organization_ticket_usage
from tests.unit.test_rem_kanban import (
    BASE,
    Fake,
    add,
    at,
    set_options,
    settle,
    start_new_session,
    tick,
    ticket,
    wait_for,
)
from tests.unit.test_rem_kanban import board as board
from tests.unit.test_rem_kanban import org as org

# The ledger ------------------------------------------------------------------------


def test_each_call_keeps_its_run_and_thread(tmp_path: Path):
    record_invocation(
        invocation_id="one",
        provider="codex",
        metadata={"input_tokens": 10, "output_tokens": 5},
        data_dir=tmp_path,
        run_id="run-1",
        thread_id="thread-1",
    )
    # A replay without the scope keeps the scope it had.
    record_invocation(
        invocation_id="one",
        provider="codex",
        metadata={"input_tokens": 10, "output_tokens": 6},
        data_dir=tmp_path,
    )
    with usage_db(tmp_path) as db:
        row = db.execute("SELECT run_id, thread_id, tokens FROM usage_invocations").fetchone()
    assert (row["run_id"], row["thread_id"]) == ("run-1", "thread-1")
    assert json.loads(row["tokens"])["total_tokens"] == 16


def test_an_older_ledger_gains_the_run_and_thread_columns(tmp_path: Path):
    db = sqlite3.connect(tmp_path / "usage.sqlite3")
    db.executescript("""
        CREATE TABLE usage_invocations (
            id TEXT PRIMARY KEY, provider TEXT NOT NULL, profile TEXT,
            model TEXT, observed_at TEXT NOT NULL, status TEXT NOT NULL,
            tokens TEXT NOT NULL, estimated INTEGER NOT NULL DEFAULT 0,
            partial INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO usage_invocations VALUES
            ('old', 'codex', NULL, NULL, '2026-11-01T00:00:00+00:00', 'completed', '{}', 0, 0);
    """)
    db.commit()
    db.close()
    record_invocation(invocation_id="new", provider="codex", data_dir=tmp_path, run_id="r")
    with usage_db(tmp_path) as db:
        rows = {r["id"]: r["run_id"] for r in db.execute("SELECT * FROM usage_invocations")}
    assert rows == {"old": None, "new": "r"}


def test_scope_comes_from_the_organization_run_or_rem_task():
    org = {"organizationRun": {"parentRunId": "turn-7"}, "chatThreadId": "organization:x"}
    assert usage_scope(org) == {"run_id": "turn-7", "thread_id": "organization:x"}
    rem = {"remManagedTask": {"runId": "run-3"}, "chatThreadId": "thread-3"}
    assert usage_scope(rem, "thread-3-review") == {"run_id": "run-3", "thread_id": "thread-3"}
    assert usage_scope(None, "chat-9") == {"run_id": None, "thread_id": "chat-9"}
    assert usage_scope({}, None) == {"run_id": None, "thread_id": None}


def test_tracked_chat_records_the_run_and_thread(tmp_path: Path):
    import asyncio

    @track_chat_usage
    async def stream(
        provider: str,
        workflow: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        data_dir: Path | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        yield {"type": "final", "usage": {"input_tokens": 3, "output_tokens": 4}}

    async def consume() -> None:
        async for _ in stream(
            "claude_code",
            workflow={"remManagedTask": {"runId": "run-a"}, "chatThreadId": "thread-a"},
            conversation_id="thread-a",
            data_dir=tmp_path,
        ):
            pass

    asyncio.run(consume())
    assert run_usage(["run-a"], tmp_path)["run-a"]["total_tokens"] == 7
    with usage_db(tmp_path) as db:
        assert db.execute("SELECT thread_id FROM usage_invocations").fetchone()[0] == "thread-a"


def test_run_totals_say_which_calls_were_not_reported(tmp_path: Path):
    record_invocation(
        invocation_id="a",
        provider="codex",
        metadata={"input_tokens": 100, "output_tokens": 20},
        data_dir=tmp_path,
        run_id="r",
    )
    record_invocation(invocation_id="b", provider="copilot", data_dir=tmp_path, run_id="r")
    record_invocation(
        invocation_id="c",
        provider="opencode",
        metadata={"total_tokens": 50, "estimated": True},
        data_dir=tmp_path,
        run_id="r",
    )
    totals = run_usage(["r", "missing"], tmp_path)
    assert set(totals) == {"r"}
    assert totals["r"]["calls"] == 3
    assert totals["r"]["total_tokens"] == 120
    # An estimate isn't a report: it counts as unknown, not as tokens.
    assert totals["r"]["unknown_calls"] == 2
    assert usage_label(totals["r"]) == (
        "120 tokens over 3 calls, 2 of them with unknown or partial usage"
    )
    assert usage_label(empty_usage()) == "No model calls yet"
    unknown = {**empty_usage(), "calls": 2, "unknown_calls": 2}
    assert usage_label(unknown).startswith("Tokens unknown")


def test_organization_tickets_add_up_their_runs(tmp_path: Path):
    record_invocation(
        invocation_id="employee",
        provider="claude_code",
        metadata={"input_tokens": 400, "output_tokens": 100},
        data_dir=tmp_path,
        run_id="turn-1",
    )
    record_invocation(
        invocation_id="swarm-agent",
        provider="codex",
        metadata={"total_tokens": 250},
        data_dir=tmp_path,
        run_id="turn-1",
    )
    org = {
        "runtime": {
            "attempts": {
                "turn-1": {"id": "turn-1", "taskId": "t1", "finishedAt": "x", "usage": {}},
                # Ran elsewhere: no ledger rows, so the run's own report counts once.
                "turn-2": {
                    "id": "turn-2",
                    "taskId": "t1",
                    "finishedAt": "x",
                    "usage": {"total_tokens": 50},
                },
                "turn-3": {"id": "turn-3", "taskId": "t2", "finishedAt": "x", "usage": {}},
                # Still running and nothing recorded yet.
                "turn-4": {"id": "turn-4", "taskId": "t3", "usage": {}},
            }
        }
    }
    totals = organization_ticket_usage(org, tmp_path)
    assert totals["t1"]["total_tokens"] == 800 and totals["t1"]["calls"] == 3
    assert totals["t2"]["total_tokens"] is None and totals["t2"]["unknown_calls"] == 1
    assert "t3" not in totals


def test_board_shows_usage_on_organization_tickets(board, org):
    home, _, _ = board
    manager, identity = org
    manager.call(
        "",
        "user",
        {"action": "task_create", "organizationId": identity, "params": {"title": "Org work"}},
    )
    shown = next(t for t in home.kanban.board()["tickets"] if t["title"] == "Org work")
    assert shown["scope"] == "organization" and shown["usage"] == empty_usage()


def test_stored_windows_skip_past_resets_and_report_the_fullest(tmp_path: Path):
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    with usage_db(tmp_path) as db:
        db.execute(
            "INSERT INTO usage_snapshots VALUES (?, ?, ?)",
            (
                "codex",
                json.dumps(
                    {
                        "windows": [
                            {
                                "id": "5h",
                                "label": "5-hour",
                                "unit": "percent",
                                "used": 91.0,
                                "resets_at": past,
                            },
                            {
                                "id": "week",
                                "label": "Weekly",
                                "unit": "percent",
                                "used": 40.0,
                                "resets_at": future,
                            },
                            {
                                "id": "credits",
                                "label": "Credits",
                                "unit": "tokens",
                                "remaining_percent": 15.0,
                                "resets_at": future,
                            },
                        ]
                    }
                ),
                datetime.now(UTC).isoformat(),
            ),
        )
    assert [(w["id"], round(w["percent"])) for w in stored_windows("codex", tmp_path)] == [
        ("credits", 85),
        ("week", 40),
    ]
    assert window_used_percent({"unit": "requests", "used": 30, "limit": 120}) == 25.0
    assert window_used_percent({"status": "rejected"}) == 100.0
    assert window_used_percent({"unlimited": True, "used": 99, "unit": "percent"}) is None


# Rem tickets and self-work -----------------------------------------------------------


class MeteredFake(Fake):
    """Records a model call in the ledger the way the tracked chat does, when it starts."""

    def __init__(self) -> None:
        super().__init__()
        self.tokens: int | None = 1000

    async def __call__(self, **kwargs: Any) -> Any:
        reviewer = "independent read-only reviewer" in kwargs["agent_instructions"]
        record_invocation(
            invocation_id=uuid4().hex,
            provider="codex",
            # The reviewer's provider reports nothing, so its call is unknown.
            metadata={} if reviewer or self.tokens is None else {"total_tokens": self.tokens},
            data_dir=kwargs["data_dir"],
            **usage_scope(kwargs["workflow"], kwargs["conversation_id"]),
        )
        async for event in super().__call__(**kwargs):
            yield event


@pytest.fixture
def metered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # New workers wait while this computer is short of memory; keep tests host-independent.
    monkeypatch.setattr("gofer.core.command_slots.memory_hold", lambda: None)
    monkeypatch.setattr(rem_home_module, "validate_artifacts", lambda run: None)
    fake = MeteredFake()
    data = tmp_path / "rem-data"
    project = tmp_path / "project"
    project.mkdir()
    home = RemHome(
        data,
        source=fake,
        jobs=ChatJobs(data),
        steering=ChatSteering(data),
        authorize=lambda c, r: None,
        generate=lambda p, c, e: {"reply": "Noted.", "tasks": [], "memories": []},
        start=False,
    )
    fake.home = home
    home.configure(
        {
            "provider": "codex",
            "model": "cli-default",
            "command_slots": {"maxWorkers": 8},
            "projects": [{"root": str(project), "name": "project"}],
        }
    )
    home.kanban.set_participant_projects(
        "user", {"participant_id": "rem", "projects": [str(project)], "expected_revision": 0}
    )
    home.self_work.started_at = BASE
    home.self_work.clock = lambda: BASE
    yield home, fake, data
    fake.hold.set()
    home.close()
    for worker, _ in home.workers.values():
        worker.join(timeout=5)
    home.jobs.close()
    home.steering.close()


def notes(home: RemHome) -> list[str]:
    return [n["text"] for n in home.self_work.view()["report"]["notes"]]


def test_a_rem_ticket_shows_the_usage_of_its_runs(metered):
    home, _, _ = metered
    add(home, "Metered")
    assert ticket(home, "Metered")["usage"]["calls"] == 0
    at(home, 15)
    tick(home)
    settle(home, "Metered")
    shown = ticket(home, "Metered")
    assert shown["status"] == "done"
    # The calls carry the ticket's run and thread; research self-work runs carry their own.
    with usage_db(home.data_dir) as db:
        rows = db.execute("SELECT run_id, thread_id, tokens FROM usage_invocations").fetchall()
    assert all(r["run_id"] and r["thread_id"] for r in rows)
    mine = [r for r in rows if r["run_id"] == shown["run_id"]]
    assert {r["thread_id"] for r in mine} == {shown["thread_id"]}
    reported = sum(1 for r in mine if json.loads(r["tokens"]))
    # Each worker call reported 1000 tokens; the reviewer's provider reported nothing.
    assert reported >= 1 and len(mine) - reported >= 1
    assert shown["usage"]["calls"] == len(mine)
    assert shown["usage"]["total_tokens"] == 1000 * reported
    assert shown["usage"]["unknown_calls"] == len(mine) - reported
    timeline = home.kanban.timeline({"ticket_id": shown["ticket_id"], "scope": "rem"})
    assert timeline["ticket"]["usage"] == shown["usage"]


def test_a_session_token_limit_stops_new_work_and_lets_running_work_finish(metered):
    home, fake, _ = metered
    fake.hold.clear()
    for title in ("First", "Second", "Third"):
        add(home, title)
    set_options(home, max_threads=2, session_token_limit=1000)
    at(home, 15)
    tick(home)
    # Two tickets are claimed; in one project their runs take turns, so one is working.
    assert ticket(home, "Second")["status"] == "in_progress"
    wait_for(lambda: tick(home) or fake.working() == 1)
    # 1000 tokens recorded so far: at the limit, so the third ticket waits.
    tick(home)
    view = home.self_work.view()
    assert ticket(home, "Third")["status"] == "todo"
    assert "this session" in view["state"]["limit_reason"]
    assert "Running work finishes normally." in view["state"]["detail"]
    assert view["state"]["mode"] == "working"
    # The note is written once, however many passes find the limit.
    tick(home)
    assert sum("this session" in n for n in notes(home)) == 1
    # The two running tickets finish normally.
    fake.hold.set()
    settle(home, "First", "Second")
    tick(home)
    assert ticket(home, "Third")["status"] == "todo"
    view = home.self_work.view()
    assert view["state"]["mode"] == "waiting"
    assert view["state"]["detail"].startswith("Stopped starting new work")
    # A new session starts counting again. The ledger keeps real time and this test's clock
    # doesn't move, so date the first session's calls before the new session starts.
    with usage_db(home.data_dir) as db:
        db.execute("UPDATE usage_invocations SET observed_at=?", (BASE.isoformat(),))
    start_new_session(home, 60)
    settle(home, "Third")


def test_a_daily_token_limit_counts_every_session_that_day(metered):
    home, _, _ = metered
    add(home, "Morning")
    set_options(home, max_threads=1, daily_token_limit=1000)
    at(home, 15)
    tick(home)
    settle(home, "Morning")
    add(home, "Afternoon")
    start_new_session(home, 60)
    assert ticket(home, "Afternoon")["status"] == "todo"
    assert any("today" in n and "daily limit of 1k" in n for n in notes(home))
    # The user's own Start now is not self-work, so the limit doesn't hold it back.
    home.kanban.act(
        "user",
        {
            "action": "move",
            "ticket_id": ticket(home, "Afternoon")["ticket_id"],
            "scope": "rem",
            "column": "in_progress",
            "expected_revision": ticket(home, "Afternoon")["revision"],
        },
        "start-afternoon",
    )
    settle(home, "Afternoon")


def test_unreported_tokens_do_not_trip_a_token_limit(metered):
    home, fake, _ = metered
    fake.tokens = None
    add(home, "Silent one")
    add(home, "Silent two")
    set_options(home, max_threads=1, session_token_limit=1)
    at(home, 15)
    tick(home)
    settle(home, "Silent one")
    settle(home, "Silent two")
    assert ticket(home, "Silent one")["usage"]["total_tokens"] is None
    assert not home.self_work.view()["state"]["limit_reason"]


def test_a_full_rate_limit_window_stops_new_work(metered):
    home, _, data = metered
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with usage_db(data) as db:
        db.execute(
            "INSERT INTO usage_snapshots VALUES (?, ?, ?)",
            (
                "codex",
                json.dumps(
                    {
                        "windows": [
                            {
                                "id": "5h",
                                "label": "5-hour",
                                "unit": "percent",
                                "used": 85.0,
                                "resets_at": future,
                            }
                        ]
                    }
                ),
                datetime.now(UTC).isoformat(),
            ),
        )
    add(home, "Waits for allowance")
    set_options(home, rate_limit_percent=90)
    at(home, 15)
    tick(home)
    # 85% is under 90%: work starts.
    settle(home, "Waits for allowance")
    add(home, "Held")
    set_options(home, rate_limit_percent=80)
    tick(home)
    assert ticket(home, "Held")["status"] == "todo"
    reason = home.self_work.view()["state"]["limit_reason"]
    assert "Codex's 5-hour limit is 85% used" in reason and "80%" in reason
    assert reason in notes(home)
    # Turning the limit off lets work start again.
    set_options(home, rate_limit_percent=0)
    tick(home)
    settle(home, "Held")
    assert not home.self_work.view()["state"]["limit_reason"]


@pytest.mark.parametrize(
    "change",
    [
        {"session_token_limit": -1},
        {"daily_token_limit": 1.5},
        {"daily_token_limit": True},
        {"rate_limit_percent": 101},
        {"rate_limit_percent": "80"},
    ],
)
def test_limit_settings_are_checked(metered, change):
    home, _, _ = metered
    with pytest.raises(KanbanRefused):
        set_options(home, **change)


def test_limit_settings_are_shown_and_saved(metered):
    home, _, _ = metered
    view = home.self_work.view()
    assert {k: view["settings"][k] for k in ("session_token_limit", "daily_token_limit")} == {
        "session_token_limit": 0,
        "daily_token_limit": 0,
    }
    set_options(home, session_token_limit=250_000, daily_token_limit=0, rate_limit_percent=75)
    settings = home.self_work.view()["settings"]
    assert (settings["session_token_limit"], settings["rate_limit_percent"]) == (250_000, 75)
    assert home.self_work.view()["limits"]["rate_limit_percent_max"] == 100
