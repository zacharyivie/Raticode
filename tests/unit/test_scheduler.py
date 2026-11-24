from __future__ import annotations

import gc
import json
import sqlite3
import threading
import warnings
from pathlib import Path

import pytest

from gofer.core.agent import AgentConfig
from gofer.core.scheduler import WorkflowScheduler, _run_workflow
from gofer.core.workflow import AgenticWorkflow, ScheduleConfig, WorkflowConfig
from tests.conftest import FakeSubscription


def _scheduled_workflow(wf_id: str = "daily") -> AgenticWorkflow:
    return AgenticWorkflow(
        WorkflowConfig(
            id=wf_id,
            name="Daily Job",
            schedule=ScheduleConfig(cron_expression="0 9 * * 1-5"),
        )
    )


def _assert_no_unclosed_database_warnings() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        gc.collect()
        gc.collect()
    messages = [str(warning.message) for warning in caught]
    assert not any("unclosed database" in message for message in messages), messages


def _assert_db_reopenable_and_replaceable(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("select 1").fetchone()
    finally:
        conn.close()
    replacement = db_path.with_suffix(".replacement")
    db_path.replace(replacement)
    replacement.replace(db_path)


def test_add_and_list(tmp_path: Path) -> None:
    scheduler = WorkflowScheduler(db_path=tmp_path / "jobs.db")
    wf = _scheduled_workflow()
    wf_path = tmp_path / "wf.toml"
    wf_path.write_text("")  # path just needs to exist for the scheduler record
    scheduler.add_workflow(wf, wf_path)
    listed = scheduler.list_workflows()
    assert any(j["id"] == "daily" for j in listed)
    scheduler.shutdown(wait=False)
    del scheduler
    _assert_no_unclosed_database_warnings()


def test_remove(tmp_path: Path) -> None:
    scheduler = WorkflowScheduler(db_path=tmp_path / "jobs.db")
    wf = _scheduled_workflow()
    wf_path = tmp_path / "wf.toml"
    wf_path.write_text("")
    scheduler.add_workflow(wf, wf_path)
    scheduler.remove_workflow("daily")
    assert not any(j["id"] == "daily" for j in scheduler.list_workflows())
    scheduler.shutdown(wait=False)
    del scheduler
    _assert_no_unclosed_database_warnings()


def test_add_without_schedule_raises(tmp_path: Path) -> None:
    scheduler = WorkflowScheduler()
    wf = AgenticWorkflow(WorkflowConfig(id="no-sched", name="No Schedule"))
    with pytest.raises(ValueError, match="no schedule"):
        scheduler.add_workflow(wf, tmp_path / "wf.toml")
    scheduler.shutdown(wait=False)
    del scheduler
    _assert_no_unclosed_database_warnings()


def test_replace_existing(tmp_path: Path) -> None:
    scheduler = WorkflowScheduler(db_path=tmp_path / "jobs.db")
    wf = _scheduled_workflow()
    wf_path = tmp_path / "wf.toml"
    wf_path.write_text("")
    scheduler.add_workflow(wf, wf_path)
    scheduler.add_workflow(wf, wf_path)  # should not raise
    assert len(scheduler.list_workflows()) == 1
    scheduler.shutdown(wait=False)
    del scheduler
    _assert_no_unclosed_database_warnings()


def test_transient_operations_cleanup_sqlite_resources(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    scheduler = WorkflowScheduler(db_path=db_path)
    wf_path = tmp_path / "wf.toml"
    wf_path.write_text("", encoding="utf-8")

    for index in range(5):
        scheduler.add_workflow(_scheduled_workflow(f"daily-{index}"), wf_path)
        assert len(scheduler.list_workflows()) == index + 1
    for index in range(5):
        scheduler.remove_workflow(f"daily-{index}")
        assert len(scheduler.list_workflows()) == 4 - index

    scheduler.shutdown(wait=False)
    del scheduler
    _assert_no_unclosed_database_warnings()
    _assert_db_reopenable_and_replaceable(db_path)


def test_explicit_start_resume_shutdown_cleanup(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.db"
    scheduler = WorkflowScheduler(db_path=db_path)
    # Other tests in this process may still have scheduler threads; watch only this one's.
    earlier = {thread for thread in threading.enumerate() if thread.name == "APScheduler"}

    scheduler.start(paused=True)
    assert scheduler.is_running()
    ours = {t for t in threading.enumerate() if t.name == "APScheduler"} - earlier
    assert ours
    scheduler.resume()
    scheduler.shutdown(wait=False)
    assert not scheduler.is_running()

    del scheduler
    _assert_no_unclosed_database_warnings()
    _assert_db_reopenable_and_replaceable(db_path)
    # shutdown(wait=False) only wakes the scheduler's thread; give it a moment to exit.
    for thread in ours:
        thread.join(timeout=5)
        assert not thread.is_alive(), "the APScheduler thread kept running"


def test_invalid_cron_timezone_and_missing_path_fail_clearly(tmp_path: Path) -> None:
    scheduler = WorkflowScheduler(db_path=tmp_path / "jobs.db")
    wf_path = tmp_path / "wf.toml"
    wf_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="Wrong number of fields"):
        scheduler.add_workflow(
            AgenticWorkflow(
                WorkflowConfig(
                    id="bad-cron",
                    name="Bad Cron",
                    schedule=ScheduleConfig(cron_expression="* * *"),
                )
            ),
            wf_path,
        )
    with pytest.raises(Exception, match="timezone|zone|Zone|No time zone"):
        scheduler.add_workflow(
            AgenticWorkflow(
                WorkflowConfig(
                    id="bad-tz",
                    name="Bad Timezone",
                    schedule=ScheduleConfig(
                        cron_expression="0 9 * * *",
                        timezone="Not/A_Timezone",
                    ),
                )
            ),
            wf_path,
        )
    with pytest.raises(FileNotFoundError, match="Workflow file not found"):
        scheduler.add_workflow(_scheduled_workflow("missing-path"), tmp_path / "missing.toml")

    scheduler.shutdown(wait=False)


def test_duplicate_workflow_id_replaces_existing_name(tmp_path: Path) -> None:
    scheduler = WorkflowScheduler(db_path=tmp_path / "jobs.db")
    wf_path = tmp_path / "wf.toml"
    wf_path.write_text("", encoding="utf-8")
    first = _scheduled_workflow("duplicate")
    second = AgenticWorkflow(
        WorkflowConfig(
            id="duplicate",
            name="Replacement Job",
            schedule=ScheduleConfig(cron_expression="0 10 * * *"),
        )
    )

    scheduler.add_workflow(first, wf_path)
    scheduler.add_workflow(second, wf_path)

    listed = scheduler.list_workflows()
    assert len(listed) == 1
    assert listed[0]["id"] == "duplicate"
    assert listed[0]["name"] == "Replacement Job"
    scheduler.shutdown(wait=False)


def test_remove_nonexistent_workflow_is_idempotent(tmp_path: Path) -> None:
    scheduler = WorkflowScheduler(db_path=tmp_path / "jobs.db")

    scheduler.remove_workflow("does-not-exist")

    assert scheduler.list_workflows() == []
    scheduler.shutdown(wait=False)


def test_scheduled_execution_logs_external_agent_access(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    work_dir = tmp_path / "work"
    extra_dir = tmp_path / "shared"
    work_dir.mkdir()
    extra_dir.mkdir()
    wf = _scheduled_workflow()
    wf.register_agent(
        AgentConfig(
            agent_id="reviewer",
            subscription="codex",
            working_dir=work_dir,
            extra_paths=[extra_dir],
        )
    )
    wf_path = tmp_path / "wf.toml"
    wf.to_file(wf_path)

    _run_workflow(wf.config.id, str(wf_path), {})

    assert "grants provider filesystem access outside working_dir" in caplog.text
    assert str(extra_dir) in caplog.text


def test_scheduled_execution_persists_masked_usage_sidecar(tmp_path: Path) -> None:
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("Token: {{secret}}", encoding="utf-8")
    wf_path = tmp_path / "wf.toml"
    wf_path.write_text(
        f"""
[workflow]
id = "scheduled-usage"
name = "Scheduled Usage"

[[nodes]]
id = "ask"
type = "agent"
agent_id = "assistant"
working_dir = "."
prompt_path = "{prompt_path}"

[agents.assistant]
subscription = "codex"
working_dir = "."
""".lstrip(),
        encoding="utf-8",
    )

    _run_workflow(
        "scheduled-usage",
        str(wf_path),
        {"codex": FakeSubscription(output="done")},
    )

    sidecars = list((tmp_path / "logs" / "scheduled-usage").glob("*.outputs.json"))
    assert len(sidecars) == 1
    payload = json.loads(sidecars[0].read_text(encoding="utf-8"))
    assert payload["usageSummary"]["totals"]["agent_calls"] == 1
    assert "prompt" not in payload["nodeOutputs"]["ask"]["data"]
    assert "Token:" not in sidecars[0].read_text(encoding="utf-8")
