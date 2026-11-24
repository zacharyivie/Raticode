"""A triggered run stopped by a provider's usage limit waits for the reset, then resumes."""

from __future__ import annotations

import json
import shlex
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core import usage_limits
from gofer.devices.workflows import PhoneWorkflows
from gofer.rattish.run_service import RattishRunResult
from gofer.rattish.trigger_service import (
    TriggerRun,
    run_trigger,
    usage_limit_failure,
    usage_wait_message,
)
from gofer.rattish.triggers import TriggerError, TriggerStore, usage_waits_by_run
from gofer.rattish.workspaces import create_registered_workflow
from gofer.ui.api import _rattish_run_directory, recent_rattish_runs_payload
from gofer.ui.workflow_runs import all_workflow_runs_payload
from tests.unit.test_rattish_provider_fallback import ScriptedProvider
from tests.unit.test_rattish_triggers import START, Clock, _engine, _wait_for

runner = CliRunner()
# Once, at 12:01 UTC on the test day, so later ticks start nothing new.
ONCE = {"expression": "1 12 9 11 *", "timezone": "UTC"}
RESET = START + timedelta(hours=1)
LIMITED = f"Claude AI usage limit reached|{int(RESET.timestamp())}"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def _agent_workflow(project: Path) -> Path:
    """A step that counts its runs, then an agent step on Claude Code."""
    counter = shlex.quote(str(project / "prepared.txt"))
    source = project / "workflow.rattish"
    source.write_text(
        "Rattish: 1\n\nWorkflow:\n  name: Usage Limit\n\n"
        f"Node prepare:\n  type: bash-command\n  command: printf x >> {counter}\n  to: ask\n\n"
        "Node ask:\n  type: agent\n  provider: claude-code\n  prompt: Say hi.\n"
        "  needs: prepare\n",
        encoding="utf-8",
    )
    return source


def _bash_workflow(project: Path) -> Path:
    source = project / "workflow.rattish"
    source.write_text(
        "Rattish: 1\n\nWorkflow:\n  name: Fake\n\nNode hello:\n  type: bash-command\n"
        "  command: echo hello\n",
        encoding="utf-8",
    )
    return source


def _once(store: TriggerStore, source: Path, **options: Any) -> str:
    record, _ = store.create(
        kind="cron",
        source_path=source,
        config=ONCE,
        now=START - timedelta(seconds=20),
        **options,
    )
    return record.id


def _history(store: TriggerStore, trigger_id: str) -> list[tuple[str, str]]:
    return [(event.status, event.message) for event in store.events(trigger_id=trigger_id)]


def _real_engine(data_dir: Path, provider: ScriptedProvider) -> tuple[Any, Clock]:
    engine, _, clock = _engine(data_dir)
    engine._runner = lambda run: run_trigger(run, subscriptions={"claude_code": provider})
    return engine, clock


def test_a_run_at_a_usage_limit_waits_for_the_reset_then_resumes_without_redoing_steps(
    data_dir: Path, project: Path
) -> None:
    source = _agent_workflow(project)
    provider = ScriptedProvider("claude_code", [(False, "", LIMITED, 1, {})])
    engine, clock = _real_engine(data_dir, provider)
    trigger_id = _once(engine.store, source)
    engine.become_owner()
    clock.advance(31)
    engine.tick()

    _wait_for(lambda: _history(engine.store, trigger_id)[0][0] == "waiting", timeout=60)
    _wait_for(lambda: not engine._threads, timeout=30)
    reset_clock = RESET.astimezone().strftime("%H:%M")
    [event] = engine.store.events(trigger_id=trigger_id)
    assert event.message == (
        f"Waiting for Claude Code's usage limit to reset at {reset_clock}; resumes then."
    )
    failed_run = event.run_id
    assert failed_run is not None
    [wait] = engine.store.usage_waits()
    assert wait.run_id == failed_run and wait.waits == 1
    assert wait.resume_at == (RESET + usage_limits.RESET_GRACE).isoformat()
    # The workflow's Runs list shows the failed run as waiting for the limit.
    workflow_id = engine.store.get(trigger_id).workflow_id
    [listed] = recent_rattish_runs_payload(workflow_id, data_dir)["runs"]
    assert listed["id"] == failed_run and listed["status"] == "error"
    assert listed["usageLimitWait"]["label"] == "Waiting for usage limit"
    assert listed["usageLimitWait"]["resumeAt"] == wait.resume_at

    # Before the reset plus its grace, nothing resumes.
    clock.moment = RESET + timedelta(minutes=1)
    engine.tick()
    assert not engine._threads and engine.store.usage_waits()

    clock.moment = RESET + usage_limits.RESET_GRACE
    engine.tick()
    _wait_for(lambda: _history(engine.store, trigger_id)[0][0] == "passed", timeout=60)
    _wait_for(lambda: not engine._threads, timeout=30)
    [event] = engine.store.events(trigger_id=trigger_id)
    assert event.message == f"Resumed run {failed_run} after Claude Code's usage limit reset."
    assert event.run_id not in (None, failed_run)
    # The passed step ran once; only the agent step ran again.
    assert (project / "prepared.txt").read_text(encoding="utf-8") == "x"
    assert len(provider.calls) == 2
    assert engine.store.usage_waits() == []
    newest, earlier = recent_rattish_runs_payload(workflow_id, data_dir)["runs"]
    assert (newest["id"], newest["status"], newest["resumedFrom"]) == (
        event.run_id,
        "success",
        failed_run,
    )
    assert newest["usageLimitWait"] is None and earlier["usageLimitWait"] is None


def test_workflow_runs_and_the_phone_show_a_waiting_run(data_dir: Path, project: Path) -> None:
    registered = create_registered_workflow(project, "Fake", registry_dir=data_dir)
    runs = LimitedRuns(project / "run.json")
    clock = Clock()
    engine = _fake_engine(data_dir, project, runs, clock)
    trigger_id = _once(engine.store, registered.entrypoint)
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: _history(engine.store, trigger_id)[0][0] == "waiting")
    _wait_for(lambda: not engine._threads)
    run_id = engine.store.events(trigger_id=trigger_id)[0].run_id
    directory = _rattish_run_directory(data_dir, registered.workflow_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{run_id}.json").write_text(
        json.dumps(
            {
                "run_artifact_version": 1,
                "run_id": run_id,
                "status": "failed",
                "workflow": {"id": registered.workflow_id, "name": "Fake"},
                "started_at": "2026-11-09T12:01:01+00:00",
                "finished_at": "2026-11-09T12:01:02+00:00",
                "duration_ms": 1000,
                "started_by": "trigger",
                "error": {"message": LIMITED},
            }
        ),
        encoding="utf-8",
    )

    [row] = all_workflow_runs_payload(data_dir)["runs"]
    assert row["id"] == run_id and row["status"] == "error"
    assert row["usageLimitWait"]["label"] == "Waiting for usage limit"
    shown = PhoneWorkflows._run(row)
    assert (shown["status"], shown["summary"]) == ("failed", "Waiting for usage limit")


@pytest.mark.parametrize(
    "error",
    [
        "Credit balance is too low",
        '{"type":"error","error":{"type":"enforced_spend_limit_reached","message":'
        '"You have reached your specified API usage limits."}}',
    ],
)
def test_credit_and_spend_cap_errors_fail_without_waiting(
    data_dir: Path, project: Path, error: str
) -> None:
    source = _agent_workflow(project)
    provider = ScriptedProvider("claude_code", [(False, "", error, 1, {})])
    engine, clock = _real_engine(data_dir, provider)
    trigger_id = _once(engine.store, source)
    engine.become_owner()
    clock.advance(31)
    engine.tick()

    _wait_for(lambda: _history(engine.store, trigger_id)[0][0] == "failed", timeout=60)
    _wait_for(lambda: not engine._threads, timeout=30)
    assert [status for status, _ in _history(engine.store, trigger_id)] == ["failed"]
    assert engine.store.usage_waits() == []
    assert not usage_limits.usage_limited_text(error)


class LimitedRuns:
    """Stands in for the runner: every run fails at Claude Code's usage limit."""

    def __init__(self, record: Path, message: str = LIMITED, passes_after: int = 99) -> None:
        self.record = record
        self.message = message
        self.passes_after = passes_after
        self.started: list[TriggerRun] = []

    def __call__(self, run: TriggerRun) -> RattishRunResult:
        self.started.append(run)
        if len(self.started) > self.passes_after:
            document: dict[str, Any] = {"status": "passed", "error": None}
        else:
            error = {
                "kind": "provider",
                "code": "RATTISH_RUNTIME_PROVIDER_ERROR",
                "message": self.message,
                "details": {"provider": "claude-code", "availability": "rate_limit"},
            }
            document = {
                "status": "failed",
                "error": error,
                "runs": [{"node_id": "ask", "outcome": "failure", "error": error}],
            }
        return cast(RattishRunResult, SimpleNamespace(document=document, path=self.record))


def _fake_engine(
    data_dir: Path, project: Path, runs: LimitedRuns, clock: Clock | None = None
) -> Any:
    engine, _, _ = _engine(data_dir, clock=clock or Clock())
    engine._runner = runs
    return engine


def test_waits_are_bounded_then_the_run_fails_with_the_last_error(
    data_dir: Path, project: Path
) -> None:
    source = _bash_workflow(project)
    runs = LimitedRuns(project / "run.json")
    clock = Clock()
    engine = _fake_engine(data_dir, project, runs, clock)
    trigger_id = _once(engine.store, source)
    engine.become_owner()
    clock.advance(31)
    engine.tick()

    for number in range(1, usage_limits.MAX_WAITS + 1):
        _wait_for(lambda: _history(engine.store, trigger_id)[0][0] == "waiting")
        _wait_for(lambda: not engine._threads)
        [wait] = engine.store.usage_waits()
        assert wait.waits == number
        message = _history(engine.store, trigger_id)[0][1]
        if number == 1:
            assert message.endswith("resumes then.")
        else:
            # The reset each run names has passed, so the scheduler checks every half hour.
            assert message.startswith("Claude Code's usage limit was reached; trying again at")
            assert message.endswith(f" (wait {number} of {usage_limits.MAX_WAITS}).")
        assert runs.started[-1].resume_record == (None if number == 1 else str(runs.record))
        clock.moment = datetime.fromisoformat(wait.resume_at)
        engine.tick()

    _wait_for(lambda: _history(engine.store, trigger_id)[0][0] == "failed")
    _wait_for(lambda: not engine._threads)
    assert len(runs.started) == usage_limits.MAX_WAITS + 1
    assert _history(engine.store, trigger_id) == [
        (
            "failed",
            f"Still at Claude Code's usage limit after {usage_limits.MAX_WAITS} waits for it "
            f"to reset. {LIMITED}",
        )
    ]
    assert engine.store.usage_waits() == []


def test_a_wait_survives_a_scheduler_restart(data_dir: Path, project: Path) -> None:
    source = _bash_workflow(project)
    runs = LimitedRuns(project / "run.json", passes_after=1)
    clock = Clock()
    engine = _fake_engine(data_dir, project, runs, clock)
    trigger_id = _once(engine.store, source)
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: _history(engine.store, trigger_id)[0][0] == "waiting")
    _wait_for(lambda: not engine._threads)
    engine._resign(wait_seconds=1)

    later = _fake_engine(data_dir, project, runs, Clock(RESET + timedelta(minutes=3)))
    later.become_owner()
    assert _history(later.store, trigger_id)[0][0] == "waiting"
    later.tick()
    _wait_for(lambda: _history(later.store, trigger_id)[0][0] == "passed")
    _wait_for(lambda: not later._threads)
    assert runs.started[-1].resume_record == str(runs.record)


def test_an_unnamed_reset_is_checked_again_in_half_an_hour(data_dir: Path, project: Path) -> None:
    source = _bash_workflow(project)
    runs = LimitedRuns(project / "run.json", message="You've hit your usage limit.")
    clock = Clock()
    engine = _fake_engine(data_dir, project, runs, clock)
    trigger_id = _once(engine.store, source)
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: _history(engine.store, trigger_id)[0][0] == "waiting")
    _wait_for(lambda: not engine._threads)
    [wait] = engine.store.usage_waits()
    check = clock.moment + usage_limits.UNKNOWN_RESET_WAIT
    assert wait.resume_at == check.isoformat()
    assert _history(engine.store, trigger_id)[0][1] == (
        f"Claude Code's usage limit was reached; trying again at "
        f"{check.astimezone().strftime('%H:%M')}."
    )


def test_a_waiting_run_counts_as_live_for_the_overlap_policy(data_dir: Path, project: Path) -> None:
    source = _bash_workflow(project)
    runs = LimitedRuns(project / "run.json")
    clock = Clock()
    engine = _fake_engine(data_dir, project, runs, clock)
    trigger_id = _once(engine.store, source)
    every_minute, _ = engine.store.create(
        kind="cron",
        source_path=source,
        config={"expression": "* * * * *", "timezone": "UTC"},
        now=clock.moment,
    )
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: _history(engine.store, trigger_id)[0][0] == "waiting")
    _wait_for(lambda: not engine._threads)
    waiting_run = engine.store.events(trigger_id=trigger_id)[0].run_id
    # The next minute's start finds no run going, only the one waiting for the reset.
    clock.advance(60)
    engine.tick()
    assert _history(engine.store, every_minute.id)[0] == (
        "skipped",
        f"Skipped because run {waiting_run} is waiting for Claude Code's usage limit to reset.",
    )


def test_turning_the_switch_off_fails_the_run_as_before(data_dir: Path, project: Path) -> None:
    source = _bash_workflow(project)
    runs = LimitedRuns(project / "run.json")
    clock = Clock()
    engine = _fake_engine(data_dir, project, runs, clock)
    trigger_id = _once(engine.store, source, wait_usage_limit=False)
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: _history(engine.store, trigger_id)[0][0] == "failed")
    assert _history(engine.store, trigger_id) == [("failed", LIMITED)]
    assert engine.store.usage_waits() == []


def test_a_wait_due_while_triggers_are_paused_is_skipped(data_dir: Path, project: Path) -> None:
    source = _bash_workflow(project)
    runs = LimitedRuns(project / "run.json")
    clock = Clock()
    engine = _fake_engine(data_dir, project, runs, clock)
    trigger_id = _once(engine.store, source)
    engine.become_owner()
    clock.advance(31)
    engine.tick()
    _wait_for(lambda: _history(engine.store, trigger_id)[0][0] == "waiting")
    _wait_for(lambda: not engine._threads)
    engine.store.set_paused(True, now=clock.moment)
    clock.moment = RESET + timedelta(minutes=3)
    engine.tick()
    assert _history(engine.store, trigger_id)[0] == (
        "skipped",
        "Skipped because all triggers were paused.",
    )
    assert engine.store.usage_waits() == [] and len(runs.started) == 1


def test_only_provider_errors_count_as_a_usage_limit() -> None:
    error = {"kind": "provider", "message": LIMITED, "details": {"provider": "codex"}}
    hit = usage_limit_failure({"status": "failed", "error": error})
    assert hit is not None and hit.provider == "Codex"
    budget = {"kind": "budget", "message": "The run reached its usage limit."}
    assert usage_limit_failure({"status": "failed", "error": budget}) is None
    assert usage_limit_failure({"status": "passed", "error": error}) is None
    assert usage_limit_failure({"status": "stopped", "error": error}) is None
    when = datetime(2026, 11, 9, 12, 58, tzinfo=UTC)
    resume = datetime(2026, 11, 9, 13, 2, tzinfo=UTC)
    reset = usage_limits.clock_text(resume - usage_limits.RESET_GRACE, when)
    assert usage_wait_message("Claude Code", resume, True, when) == (
        f"Waiting for Claude Code's usage limit to reset at {reset}; resumes then."
    )
    tomorrow = when + timedelta(days=1)
    assert usage_limits.clock_text(tomorrow, when) == tomorrow.astimezone().strftime("%H:%M on %a")


def test_the_switch_is_on_by_default_and_set_from_the_cli(data_dir: Path, project: Path) -> None:
    source = _bash_workflow(project)
    store = TriggerStore(data_dir)
    trigger_id = _once(store, source)
    assert store.get(trigger_id).wait_usage_limit is True
    assert store.get(trigger_id).to_payload()["waitForUsageLimit"] is True
    with pytest.raises(TriggerError, match="true or false"):
        store.update(trigger_id, wait_usage_limit=cast(Any, "no"))

    result = runner.invoke(
        app,
        [
            "rattish",
            "triggers",
            "update",
            trigger_id,
            "--usage-limit",
            "fail",
            "--format",
            "json",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["trigger"]["waitForUsageLimit"] is False
    assert store.get(trigger_id).wait_usage_limit is False

    added = runner.invoke(
        app,
        [
            "rattish",
            "triggers",
            "add",
            "cron",
            str(source),
            "--cron",
            "0 9 * * *",
            "--usage-limit",
            "fail",
            "--format",
            "json",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert added.exit_code == 0, added.output
    assert json.loads(added.output)["trigger"]["waitForUsageLimit"] is False
    listed = runner.invoke(app, ["rattish", "triggers", "list", "--data-dir", str(data_dir)])
    assert "usage-limit=fail" in listed.output and "usage-limit=wait" not in listed.output
    back = runner.invoke(
        app,
        [
            "rattish",
            "triggers",
            "update",
            trigger_id,
            "--usage-limit",
            "wait",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert back.exit_code == 0, back.output
    assert store.get(trigger_id).wait_usage_limit is True
    refused = runner.invoke(
        app,
        [
            "rattish",
            "triggers",
            "update",
            trigger_id,
            "--usage-limit",
            "maybe",
            "--data-dir",
            str(data_dir),
        ],
    )
    assert refused.exit_code != 0


def test_triggers_saved_before_the_switch_wait_for_usage_limits(
    data_dir: Path, project: Path
) -> None:
    store = TriggerStore(data_dir)
    trigger_id = _once(store, _bash_workflow(project))
    db = sqlite3.connect(store.path)
    db.execute("ALTER TABLE triggers DROP COLUMN wait_usage_limit")
    db.execute("DROP TABLE usage_waits")
    db.execute("PRAGMA user_version = 6")
    db.commit()
    db.close()

    upgraded = TriggerStore(data_dir)
    assert upgraded.get(trigger_id).wait_usage_limit is True
    assert upgraded.usage_waits() == []
    assert usage_waits_by_run(data_dir) == {}
    assert usage_waits_by_run(data_dir / "nowhere") == {}
