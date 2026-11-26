"""Daily limits on what triggered runs spend, per trigger and for the whole computer."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core.phone_alerts import PhoneAlertStore
from gofer.core.usage_context import ProviderCall
from gofer.rattish.run_service import RattishRunResult
from gofer.rattish.run_usage import COST_LIMIT_EXCEEDED, RunUsageMeter
from gofer.rattish.trigger_service import TriggerEngine, TriggerRun, run_in_process
from gofer.rattish.triggers import (
    DAILY_LIMIT_SKIP_MESSAGE,
    TriggerError,
    TriggerStore,
    computer_limit_state,
    daily_allowance,
    day_bounds,
    trigger_limit_state,
)
from tests.unit.test_rattish_triggers import START, WORKFLOW, Clock, _settled, _wait_for

runner = CliRunner()
EVERY_MINUTE = {"expression": "* * * * *", "timezone": "UTC"}
AGENT_WORKFLOW = """Rattish: 1

Workflow:
  name: Agent Trigger

Node review:
  type: agent
  provider: codex
  prompt: Review the change.
"""


class SpendingRuns:
    """A runner whose every run reports the same usage."""

    def __init__(self, *, cost: float | None = 0.6, tokens: int | None = 1000) -> None:
        self.cost = cost
        self.tokens = tokens
        self.started: list[TriggerRun] = []
        self.finished = 0

    def __call__(self, run: TriggerRun) -> RattishRunResult:
        self.started.append(run)
        self.finished += 1
        usage = {"calls": 1, "cost_usd": self.cost, "total_tokens": self.tokens}
        return cast(
            RattishRunResult,
            SimpleNamespace(document={"status": "passed", "error": None, "usage": usage}),
        )


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


def _workflow(tmp_path: Path, text: str = WORKFLOW, name: str = "project") -> Path:
    project = tmp_path / name
    project.mkdir()
    path = project / "workflow.rattish"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return _workflow(tmp_path)


def _engine(data_dir: Path, runs: SpendingRuns, clock: Clock) -> TriggerEngine:
    return TriggerEngine(
        data_dir, runner=runs, clock=clock, enable_webhooks=False, enable_watchers=False
    )


def _cron(store: TriggerStore, source: Path, **options: Any) -> str:
    config = options.pop("config", EVERY_MINUTE)
    record, _ = store.create(
        kind="cron",
        source_path=source,
        config=config,
        now=START - timedelta(seconds=20),
        **options,
    )
    return record.id


def _fire(engine: TriggerEngine, runs: SpendingRuns, clock: Clock, expected: int) -> None:
    clock.advance(60)
    engine.tick()
    _settled(engine, cast(Any, runs), expected)


def test_limits_are_checked_and_zero_or_null_removes_them(data_dir: Path, source: Path) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, source, daily_max_cost_usd=2.5, daily_max_tokens=10_000)
    record = store.get(trigger_id)
    assert (record.daily_max_cost_usd, record.daily_max_tokens) == (2.5, 10_000)
    assert record.to_payload()["dailyMaxCostUsd"] == 2.5

    # Leaving them out keeps them; 0 or None removes one.
    assert store.update(trigger_id, label="Nightly").daily_max_cost_usd == 2.5
    cleared = store.update(trigger_id, daily_max_cost_usd=0, daily_max_tokens=None, actor="user")
    assert (cleared.daily_max_cost_usd, cleared.daily_max_tokens) == (None, None)

    for bad, match in (
        ({"daily_max_cost_usd": -1}, "negative"),
        ({"daily_max_cost_usd": "lots"}, "number"),
        ({"daily_max_tokens": 1.5}, "whole number"),
        ({"daily_max_cost_usd": float("nan")}, "negative"),
    ):
        with pytest.raises(TriggerError, match=match):
            store.update(trigger_id, **bad)

    budget = store.set_daily_budget(max_cost_usd=10)
    assert (budget.max_cost_usd, budget.max_tokens) == (10.0, None)
    assert store.set_daily_budget(max_tokens=500).max_cost_usd == 10.0
    assert not store.set_daily_budget(max_cost_usd=None, max_tokens=0).active


def test_a_database_from_before_daily_limits_gains_them(data_dir: Path, source: Path) -> None:
    root = data_dir / "rattish-triggers"
    root.mkdir()
    db = sqlite3.connect(root / "triggers.sqlite3")
    db.executescript(
        """
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE triggers (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_path TEXT NOT NULL,
            workflow_id TEXT NOT NULL, label TEXT NOT NULL, config TEXT NOT NULL,
            overlap TEXT NOT NULL, missed TEXT NOT NULL, inputs TEXT NOT NULL,
            enabled INTEGER NOT NULL, token_hash TEXT, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, baseline_at TEXT NOT NULL
        );
        CREATE TABLE cursors (trigger_id TEXT PRIMARY KEY, cursor TEXT NOT NULL);
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, trigger_id TEXT NOT NULL,
            workflow_id TEXT NOT NULL, source_path TEXT NOT NULL, kind TEXT NOT NULL,
            status TEXT NOT NULL, at TEXT NOT NULL, scheduled_for TEXT, run_id TEXT,
            message TEXT NOT NULL, count INTEGER NOT NULL
        );
        INSERT INTO triggers VALUES ('trg_old', 'cron', 'x', 'wf', '', '{}', 'skip',
            'record', '{}', 1, NULL, '2026-11-01T00:00:00+00:00',
            '2026-11-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00');
        PRAGMA user_version = 1;
        """
    )
    db.close()
    store = TriggerStore(data_dir)
    old = store.get("trg_old")
    assert (old.daily_max_cost_usd, old.daily_max_tokens) == (None, None)
    store.start_spend("run-1", "trg_old", START)
    assert store.spent_since(START - timedelta(hours=1)).runs == 1


def test_each_run_is_capped_at_what_is_left_and_the_day_stops_when_used_up(
    data_dir: Path, source: Path
) -> None:
    runs = SpendingRuns(cost=0.6, tokens=1000)
    clock = Clock()
    engine = _engine(data_dir, runs, clock)
    trigger_id = _cron(engine.store, source, daily_max_cost_usd=1.0)
    engine.become_owner()

    _fire(engine, runs, clock, 1)
    assert runs.started[0].daily_max_cost_usd == pytest.approx(1.0)
    assert runs.started[0].daily_max_tokens is None
    _fire(engine, runs, clock, 2)
    assert runs.started[1].daily_max_cost_usd == pytest.approx(0.4)

    # $1.20 used of $1.00: later starts today are skipped and say why.
    clock.advance(60)
    engine.tick()
    clock.advance(60)
    engine.tick()
    assert len(runs.started) == 2
    events = engine.store.events(trigger_id=trigger_id)
    assert [item.status for item in events[:2]] == ["skipped", "skipped"]
    assert events[0].message.startswith(f"{DAILY_LIMIT_SKIP_MESSAGE}.")
    assert "$1.20 of $1.00 used today" in events[0].message
    assert "midnight (UTC)" in events[0].message

    # One phone message for the day, under Workflow messages.
    alerts = PhoneAlertStore(data_dir).after(0)["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["reason"] == "workflow"
    assert alerts[0]["source"] == "daily-limit"
    assert alerts[0]["title"].startswith("Daily limit reached")

    state = trigger_limit_state(engine.store, engine.store.get(trigger_id), clock())
    assert state is not None and state.reached
    assert state.to_payload()["spentCostUsd"] == pytest.approx(1.2)

    # After midnight the trigger runs again with the full limit.
    clock.moment = datetime(2026, 11, 10, 0, 0, 30, tzinfo=UTC)
    engine.store.set_cursors({trigger_id: clock.moment - timedelta(seconds=5)})
    engine._cursors[trigger_id] = clock.moment - timedelta(seconds=5)
    _fire(engine, runs, clock, 3)
    assert runs.started[2].daily_max_cost_usd == pytest.approx(1.0)
    engine.stop()


def test_the_computer_wide_limit_covers_every_trigger(data_dir: Path, tmp_path: Path) -> None:
    first = _workflow(tmp_path, name="first")
    second = _workflow(tmp_path, name="second")
    runs = SpendingRuns(cost=None, tokens=600)
    clock = Clock()
    engine = _engine(data_dir, runs, clock)
    one = _cron(engine.store, first)
    two = _cron(engine.store, second, daily_max_tokens=5000)
    engine.store.set_daily_budget(max_tokens=1000)
    engine.become_owner()

    def tick_and_settle() -> None:
        clock.advance(60)
        engine.tick()
        _wait_for(
            lambda: (
                not engine._threads
                and all(engine.store.latest_event(item) is not None for item in (one, two))
                and all(engine.store.latest_event(item).status != "running" for item in (one, two))
            )
        )

    # The computer's 1,000 is less than the trigger's 5,000. While one run is running its
    # whole cap counts as used, so a run due beside it is skipped rather than both
    # spending the same 1,000; once it finishes, the next run gets the 400 it left.
    tick_and_settle()
    if len(runs.started) < 2:
        tick_and_settle()
    assert sorted(run.daily_max_tokens or 0 for run in runs.started) == [400, 1000]
    tick_and_settle()
    assert len(runs.started) == 2
    for trigger_id in (one, two):
        latest = engine.store.latest_event(trigger_id)
        assert latest is not None and latest.status == "skipped"
        assert "Triggered runs on this computer reached the daily limit" in latest.message
    alerts = PhoneAlertStore(data_dir).after(0)["alerts"]
    assert [item["title"] for item in alerts] == ["Daily limit reached for all triggers"]

    state = computer_limit_state(engine.store, clock())
    assert state is not None
    assert state.spend.tokens == 1200
    # Cost wasn't reported; it isn't counted as zero in what the user sees.
    assert state.spend.unknown_cost_runs == 2
    engine.stop()


def test_a_running_runs_cap_counts_as_used_so_overlapping_runs_cant_pass_the_limit(
    data_dir: Path, source: Path
) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _cron(store, source, daily_max_cost_usd=5.0)
    record = store.get(trigger_id)
    first = daily_allowance(store, record, START)
    assert first.reached is None and first.max_cost_usd == pytest.approx(5.0)
    store.start_spend("run-1", trigger_id, START, reserved_cost_usd=first.max_cost_usd)
    # A second start while the first runs finds nothing left.
    second = daily_allowance(store, record, START + timedelta(seconds=1))
    assert second.reached is not None
    # What the user sees is what was used, not the reservation.
    shown = trigger_limit_state(store, record, START + timedelta(seconds=1))
    assert shown is not None and shown.spend.cost_usd == 0 and not shown.reached
    # Once the first run reports $1.50, $3.50 is left for the next.
    store.finish_spend("run-1", cost_usd=1.5, tokens=100)
    third = daily_allowance(store, record, START + timedelta(seconds=2))
    assert third.reached is None and third.max_cost_usd == pytest.approx(3.5)


def test_the_day_ends_at_midnight_in_the_triggers_time_zone(data_dir: Path, source: Path) -> None:
    start, end, day = day_bounds("America/New_York", datetime(2026, 11, 10, 3, 0, tzinfo=UTC))
    # 03:00 UTC is still the 9th in New York (UTC-5).
    assert day == "2026-11-09"
    assert start == datetime(2026, 11, 9, 5, 0, tzinfo=UTC)
    assert end == datetime(2026, 11, 10, 5, 0, tzinfo=UTC)

    store = TriggerStore(data_dir)
    trigger_id = _cron(
        store,
        source,
        config={"expression": "0 * * * *", "timezone": "America/New_York"},
        daily_max_cost_usd=1.0,
    )
    store.start_spend("late", trigger_id, datetime(2026, 11, 10, 4, 30, tzinfo=UTC))
    store.finish_spend("late", cost_usd=1.0, tokens=10)
    record = store.get(trigger_id)
    before = trigger_limit_state(store, record, datetime(2026, 11, 10, 4, 59, tzinfo=UTC))
    after = trigger_limit_state(store, record, datetime(2026, 11, 10, 5, 1, tzinfo=UTC))
    assert before is not None and before.reached
    assert after is not None and not after.reached
    assert before.timezone == "America/New_York"


def test_runs_without_any_limit_are_not_capped(data_dir: Path, source: Path) -> None:
    runs = SpendingRuns()
    clock = Clock()
    engine = _engine(data_dir, runs, clock)
    _cron(engine.store, source)
    engine.become_owner()
    _fire(engine, runs, clock, 1)
    assert (runs.started[0].daily_max_cost_usd, runs.started[0].daily_max_tokens) == (None, None)
    # Still counted, so a computer-wide limit set later today sees it.
    assert engine.store.spent_since(START).cost_usd == pytest.approx(0.6)
    engine.stop()


def test_the_run_cap_is_the_smaller_of_the_workflow_limit_and_the_day() -> None:
    ir = {"workflow": {"max_cost_usd": 2.0, "max_tokens": 100}}
    meter = RunUsageMeter.for_ir(ir, "run", daily_max_cost_usd=0.5, daily_max_tokens=500)
    assert (meter.max_cost_usd, meter.max_tokens) == (0.5, 100)
    assert meter.snapshot()["limits"] == {
        "max_tokens": 100,
        "max_cost_usd": 0.5,
        "daily_cost": True,
    }
    meter.add(
        "review",
        1,
        ProviderCall(
            provider="claude_code",
            model=None,
            tokens={"total_tokens": 10},
            cost_usd=0.75,
            partial=False,
            estimated=False,
        ),
    )
    reached = meter.limit_reached()
    assert reached is not None and reached["code"] == COST_LIMIT_EXCEEDED
    assert "remaining daily cost limit of $0.50" in reached["message"]
    assert reached["details"]["daily"] is True

    plain = RunUsageMeter.for_ir(ir, "run")
    assert (plain.max_cost_usd, plain.max_tokens) == (2.0, 100)
    assert "daily_cost" not in plain.snapshot()["limits"]


def test_a_real_triggered_run_records_its_daily_cap(data_dir: Path, source: Path) -> None:
    import threading

    result = run_in_process(
        TriggerRun(
            source=source,
            workflow_id="trigger-test",
            inputs={},
            events=[],
            data_dir=data_dir,
            run_id="20261109T120000Z-cap1",
            cancel_event=threading.Event(),
            daily_max_cost_usd=0.25,
            daily_max_tokens=900,
        )
    )
    assert result.document["status"] == "passed"
    assert result.document["started_by"] == "trigger"
    limits = result.document["usage"]["limits"]
    assert limits["max_cost_usd"] == 0.25
    assert limits["max_tokens"] == 900
    assert limits["daily_cost"] is True and limits["daily_tokens"] is True


def test_api_saves_limits_and_warns_about_providers_without_cost(
    data_dir: Path, tmp_path: Path
) -> None:
    from gofer.rattish.workspaces import discover_project_workflows
    from gofer.ui.rattish_triggers import (
        create_trigger_payload,
        daily_budget_payload,
        set_daily_budget_payload,
        trigger_pause_payload,
        triggers_payload,
        update_trigger_payload,
    )

    source = _workflow(tmp_path, AGENT_WORKFLOW, name="agent")
    (registered,) = discover_project_workflows(source.parent, registry_dir=data_dir).workflows
    workflow_id = registered.workflow_id
    created = create_trigger_payload(
        data_dir,
        {
            "kind": "cron",
            "workflowId": workflow_id,
            "config": EVERY_MINUTE,
            "dailyMaxCostUsd": 3,
        },
    )
    trigger = created["trigger"]
    assert trigger["dailyMaxCostUsd"] == 3.0
    assert trigger["dailyLimit"]["summary"] == "$0 of $3.00 used today"
    assert created["warnings"] and "'review' use codex" in created["warnings"][0]

    tokens_only = update_trigger_payload(
        data_dir, trigger["id"], {"dailyMaxCostUsd": None, "dailyMaxTokens": 20000}
    )
    assert tokens_only["warnings"] == []
    assert tokens_only["trigger"]["dailyLimit"]["maxTokens"] == 20000
    with pytest.raises(TriggerError):
        update_trigger_payload(data_dir, trigger["id"], {"dailyMaxTokens": -5})

    assert daily_budget_payload(data_dir)["today"] is None
    saved = set_daily_budget_payload(data_dir, {"maxCostUsd": 5})
    assert saved["budget"] == {"maxCostUsd": 5.0, "maxTokens": None}
    assert saved["today"]["scope"] == "computer"
    assert saved["warnings"]  # The codex workflow's trigger is on.
    with pytest.raises(TriggerError, match="maxCostUsd"):
        set_daily_budget_payload(data_dir, {})
    assert triggers_payload(data_dir, workflow_id)["dailyBudget"]["budget"]["maxCostUsd"] == 5.0
    assert trigger_pause_payload(data_dir)["dailyBudget"]["today"]["maxCostUsd"] == 5.0
    assert set_daily_budget_payload(data_dir, {"maxCostUsd": None})["today"] is None


def _cli(*arguments: str) -> dict[str, Any]:
    result = runner.invoke(app, ["rattish", "triggers", *arguments, "--format", "json"])
    assert result.exit_code == 0, result.output
    return cast(dict[str, Any], json.loads(result.output))


def test_cli_sets_trigger_and_computer_limits(data_dir: Path, tmp_path: Path) -> None:
    source = _workflow(tmp_path, AGENT_WORKFLOW, name="agent")
    base = ("--data-dir", str(data_dir))
    trigger_id = _cron(TriggerStore(data_dir), source)

    updated = _cli("update", trigger_id, "--daily-max-cost-usd", "1.5", *base)
    assert updated["trigger"]["dailyMaxCostUsd"] == 1.5
    assert "codex" in updated["warnings"][0]
    both = _cli("update", trigger_id, "--daily-max-tokens", "40000", *base)["trigger"]
    assert (both["dailyMaxCostUsd"], both["dailyMaxTokens"]) == (1.5, 40000)
    cleared = _cli("update", trigger_id, "--daily-max-cost-usd", "0", *base)["trigger"]
    assert cleared["dailyMaxCostUsd"] is None

    text = runner.invoke(app, ["rattish", "triggers", "list", *base])
    assert "daily: 0 of 40,000 tokens used today" in text.output

    assert _cli("budget", *base)["today"] is None
    shown = _cli("budget", "--max-cost-usd", "4", "--max-tokens", "100000", *base)
    assert shown["budget"] == {"maxCostUsd": 4.0, "maxTokens": 100000}
    plain = runner.invoke(app, ["rattish", "triggers", "budget", *base])
    assert "Daily limit for all triggered runs: $0 of $4.00 and 0 of 100,000 tokens" in (
        " ".join(plain.output.split())
    )
    assert _cli("budget", "--max-cost-usd", "0", "--max-tokens", "0", *base)["today"] is None

    refused = runner.invoke(
        app, ["rattish", "triggers", "budget", "--max-cost-usd", "-2", *base, "--format", "json"]
    )
    assert refused.exit_code == 1
    assert "negative" in json.loads(refused.output)["error"]
