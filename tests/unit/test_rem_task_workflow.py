"""Make it a workflow: draft a workflow from a repeating Rem task and switch over to it."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from gofer.core.usage_ledger import record_invocation
from gofer.rattish.artifacts import compile_rattish_file
from gofer.rattish.test_sets import TEST_RESULT_VERSION, results_directory
from gofer.rattish.triggers import (
    ApprovalConflict,
    TriggerStore,
    approval_status,
    next_occurrence,
    parse_cron,
)
from gofer.ui import rem_task_workflow as flow
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_models import RemConflict, Schedule
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe

WORKFLOW = """Rattish: 1

Workflow:
  name: Morning digest

Node collect:
  type: bash-command
  command: echo collected
"""
DAILY = {"kind": "daily", "local_time": "07:30", "timezone": "Europe/Berlin"}


def repeating(home: RemHome, project: Path, **patch: Any) -> dict[str, Any]:
    created = home.create_task(
        {
            "request_id": f"create-{uuid4()}",
            **recipe(
                project,
                title="Morning digest",
                type="maintain",
                prompt="Collect yesterday's numbers and write the digest.",
                schedule=DAILY,
                **patch,
            ),
        }
    )
    return dict(created["task"])


def passed_run(home: RemHome, task_id: str, *, cost: float | None = None) -> dict[str, Any]:
    """A finished successful run, with worker traces and ledger calls, without a worker."""
    with home.store.connect() as db:
        task = home.store.get(db, "tasks", task_id)
        run = home.store.add_run(db, task, f"manual:{uuid4()}", {}, announce=False)
        run.update(
            state="succeeded",
            finished_at=datetime.now(UTC).isoformat(),
            summary_short="Wrote the digest.",
            worker_traces=[
                {"id": "a", "phase": "start", "category": "shell", "command": "python3 collect.py"},
                {"id": "a", "phase": "result", "status": "completed", "exitCode": 0},
                {"id": "b", "phase": "start", "category": "shell", "command": "rm -rf build"},
                {"id": "b", "phase": "result", "status": "failed", "exitCode": 1},
                {
                    "id": "c",
                    "phase": "start",
                    "title": "Write",
                    "input": '{"file_path": "digest.md"}',
                },
            ],
        )
        home.store.put(db, "runs", run)
    if cost is not None:
        record_invocation(
            invocation_id=str(uuid4()),
            provider="claude_code",
            metadata={"cost_usd": cost, "input_tokens": 10, "output_tokens": 5},
            data_dir=home.data_dir,
            run_id=run["id"],
        )
    return run


def view(home: RemHome, task_id: str) -> dict[str, Any]:
    return next(t for t in home.store.tasks() if t["id"] == task_id)


def finish_draft(home: RemHome, draft_id: str, answer: str) -> None:
    with home.store.connect() as db:
        row = db.execute("SELECT body FROM runs WHERE task=?", (draft_id,)).fetchone()
        run = json.loads(row[0])
        run.update(
            state="succeeded", worker_result=answer, finished_at=datetime.now(UTC).isoformat()
        )
        home.store.put(db, "runs", run)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


def drafted(home: RemHome, project: Path) -> tuple[dict[str, Any], dict[str, Any], Path]:
    task = repeating(home, project)
    passed_run(home, task["id"], cost=0.40)
    passed_run(home, task["id"], cost=0.60)
    started = home.task_workflow(task["id"], {"action": "draft", "request_id": "draft-1"})
    workflow = project / "workflow.rattish"
    workflow.write_text(WORKFLOW, encoding="utf-8")
    finish_draft(home, started["draft"]["id"], f"Done.\n\nWorkflow draft: {workflow}")
    return task, started["draft"], workflow


# --------------------------------------------------------------------------- schedule


def _schedule_matches(schedule: dict[str, Any], count: int = 12) -> bool:
    """Whether the trigger's next ``count`` times are the task's, from now."""
    expression, timezone = flow.trigger_schedule(schedule)
    trigger = parse_cron(expression, timezone)
    rem = Schedule.model_validate(schedule)
    clock = datetime.now(UTC)
    rem_clock = clock
    for _ in range(count):
        mine = next_occurrence(trigger, clock)
        theirs = rem.next(rem_clock)
        if mine is None or theirs is None:
            return mine is None and theirs is None
        if mine.astimezone(UTC).isoformat() != theirs:
            return False
        clock = mine
        rem_clock = datetime.fromisoformat(theirs)
    return True


def test_trigger_schedule_copies_daily_weekly_and_cron_times() -> None:
    assert flow.trigger_schedule(DAILY) == ("30 7 * * *", "Europe/Berlin")
    weekly = {
        "kind": "weekly",
        "local_time": "18:05",
        "weekdays": ["friday", "monday"],
        "timezone": "UTC",
    }
    assert flow.trigger_schedule(weekly) == ("5 18 * * mon,fri", "UTC")
    # Rem reads cron weekdays the crontab way and stores them as day names, which the
    # trigger reads the same way, so it fires on the same days the task did.
    cron = Schedule.model_validate(
        {"kind": "cron", "cron": "0 9 * * 1-5", "timezone": "America/New_York"}
    ).model_dump()
    assert flow.trigger_schedule(cron) == ("0 9 * * mon-fri", "America/New_York")
    for schedule in (
        DAILY,
        weekly,
        cron,
        {"kind": "cron", "cron": "*/20 6-8 * * *", "timezone": "Asia/Tokyo"},
    ):
        assert _schedule_matches(schedule)
    with pytest.raises(flow.WorkflowSwitchError):
        flow.trigger_schedule({"kind": "interval", "every_minutes": 30})


# --------------------------------------------------------------------------- offer + draft


def test_the_menu_offers_it_only_after_a_repeating_task_passed_twice(
    home: RemHome, project: Path
) -> None:
    task = repeating(home, project)
    assert "workflow_offer" not in view(home, task["id"])
    passed_run(home, task["id"])
    assert "workflow_offer" not in view(home, task["id"])
    passed_run(home, task["id"])
    assert view(home, task["id"])["workflow_offer"] == {"passes": 2}

    once = home.create_task({"request_id": "once", **recipe(project, title="Once")})["task"]
    passed_run(home, once["id"])
    passed_run(home, once["id"])
    assert "workflow_offer" not in view(home, once["id"])
    with pytest.raises(flow.WorkflowSwitchError):
        home.task_workflow(once["id"], {"action": "draft", "request_id": "x"})


def test_make_it_a_workflow_starts_a_task_from_the_runs_commands_and_files(
    home: RemHome, project: Path
) -> None:
    task = repeating(home, project)
    with pytest.raises(flow.WorkflowSwitchError):
        home.task_workflow(task["id"], {"action": "draft", "request_id": "early"})
    passed_run(home, task["id"])
    passed_run(home, task["id"])

    started = home.task_workflow(task["id"], {"action": "draft", "request_id": "draft-1"})
    draft = started["draft"]
    assert draft["type"] == "develop" and draft["project_dir"] == str(project)
    assert draft["schedule"]["kind"] == "once"
    assert draft["workflow_draft_for"]["task_id"] == task["id"]
    assert started["run"]["state"] == "queued"
    prompt = draft["prompt"]
    assert "Collect yesterday's numbers and write the digest." in prompt
    assert "python3 collect.py" in prompt and "digest.md" in prompt
    # A command that failed isn't offered as a step to copy.
    assert "rm -rf build" not in prompt
    assert "raticode-workflow-builder" in prompt and "rattish preflight" in prompt
    assert "Workflow draft: not worth it" in prompt and "exactly one test case" in prompt
    assert "30 7 * * *" in prompt
    criteria = " ".join(c["description"] for c in draft["acceptance_criteria"])
    assert "rattish check" in criteria and "No trigger was added" in criteria

    # The repeating task is otherwise unchanged and stops offering while the draft exists.
    source = view(home, task["id"])
    assert source["revision"] == task["revision"] and source["state"] == "enabled"
    assert source["workflow_draft"]["task_id"] == draft["id"]
    assert "workflow_offer" not in source
    # The same request again answers with the first result; a new one is refused.
    again = home.task_workflow(task["id"], {"action": "draft", "request_id": "draft-1"})
    assert again["draft"]["id"] == draft["id"]
    with pytest.raises(RemConflict):
        home.task_workflow(task["id"], {"action": "draft", "request_id": "draft-2"})
    # Deleting the draft brings the offer back.
    home.edit_task(draft["id"], {"request_id": "del-draft"}, "delete")
    assert view(home, task["id"])["workflow_offer"] == {"passes": 2}


def test_the_finished_card_compares_average_task_cost_with_the_test_run(
    home: RemHome, project: Path
) -> None:
    _, draft, workflow = drafted(home, project)
    compiled = compile_rattish_file(workflow, data_dir=home.data_dir)
    results = results_directory(workflow, str(compiled.ir["workflow"]["id"]), home.data_dir)
    results.mkdir(parents=True)
    (results / "r1.json").write_text(
        json.dumps(
            {
                "testResultVersion": TEST_RESULT_VERSION,
                "id": "r1",
                "status": "passed",
                "finishedAt": "2026-11-16T08:00:00+00:00",
                "cases": [
                    {
                        "id": "today",
                        "status": "passed",
                        "usage": {
                            "modelCalls": 1,
                            "costUsd": 0.05,
                            "judgeCalls": 1,
                            "judgeCostUsd": 0.01,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    card = view(home, draft["id"])["workflow_draft_result"]
    assert card["status"] == "ready" and card["workflow_path"] == str(workflow.resolve())
    assert card["task_cost"] == {"runs": 2, "runs_with_cost": 2, "average_usd": 0.5}
    assert card["test_cost"]["cost_usd"] == 0.05 and card["test_cost"]["model_calls"] == 1
    assert card["source_title"] == "Morning digest" and "switch" not in card


def test_a_task_that_needs_judgment_every_run_gets_no_switch(home: RemHome, project: Path) -> None:
    task = repeating(home, project)
    passed_run(home, task["id"])
    passed_run(home, task["id"])
    draft = home.task_workflow(task["id"], {"action": "draft", "request_id": "d"})["draft"]
    assert view(home, draft["id"])["workflow_draft_result"]["status"] == "drafting"
    finish_draft(
        home,
        draft["id"],
        "Each run decides what to research.\n\n"
        "Workflow draft: not worth it - every run picks new sources and writes new prose.",
    )
    card = view(home, draft["id"])["workflow_draft_result"]
    assert card["status"] == "not_worth_it"
    assert card["reason"] == "every run picks new sources and writes new prose."
    with pytest.raises(flow.WorkflowSwitchError):
        home.task_workflow(draft["id"], {"action": "preview"})


def test_a_named_file_outside_the_project_is_not_a_draft(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.rattish"
    outside.write_text(WORKFLOW, encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    assert flow.parse_marker(f"Workflow draft: {outside}", str(project))["status"] == "missing"
    assert flow.parse_marker("All done.", str(project))["status"] == "missing"
    inside = project / "workflow.rattish"
    inside.write_text(WORKFLOW, encoding="utf-8")
    marker = f"**Workflow draft: `{inside}`**"
    assert flow.parse_marker(marker, str(project)) == {
        "status": "ready",
        "workflow_path": str(inside.resolve()),
    }


# --------------------------------------------------------------------------- switch


def test_switch_over_adds_an_approved_cron_trigger_and_resume_undoes_it(
    home: RemHome, project: Path
) -> None:
    task, draft, workflow = drafted(home, project)
    shown = home.task_workflow(draft["id"], {"action": "preview"})
    assert shown["cron"] == "30 7 * * *" and shown["timezone"] == "Europe/Berlin"
    assert [f["change"] for f in shown["changes"]["files"]] == ["added"]
    assert TriggerStore(home.data_dir).list_triggers() == []  # previewing adds nothing

    switched = home.task_workflow(
        draft["id"], {"action": "switch", "fingerprint": shown["changes"]["fingerprint"]}
    )
    store = TriggerStore(home.data_dir)
    record = store.get(switched["trigger_id"])
    assert record.kind == "cron" and record.enabled
    assert record.config["expression"] == "30 7 * * *"
    assert record.config["timezone"] == "Europe/Berlin"
    assert record.source_path == str(workflow.resolve())
    approval = approval_status(store, record)
    assert approval.state == "approved" and approval.approval.approved_by == "desktop"
    source = view(home, task["id"])
    assert source["state"] == "paused" and source["enabled"] is False
    assert source["workflow_switch"]["active"] is True
    assert view(home, draft["id"])["workflow_draft_result"]["switch"]["active"] is True

    # Resume schedule undoes it: the task runs again and the trigger turns off.
    home.edit_task(
        task["id"],
        {"request_id": "resume", "expected_revision": source["revision"], "enabled": True},
        "edit",
    )
    assert store.get(record.id).enabled is False
    source = view(home, task["id"])
    assert source["state"] == "enabled" and source["workflow_switch"]["active"] is False

    # Switching again reuses that trigger.
    shown = home.task_workflow(task["id"], {"action": "preview"})
    again = home.task_workflow(
        task["id"], {"action": "switch", "fingerprint": shown["changes"]["fingerprint"]}
    )
    assert again["trigger_id"] == record.id and store.get(record.id).enabled
    assert len(store.list_triggers()) == 1


def test_a_workflow_changed_after_review_switches_nothing(home: RemHome, project: Path) -> None:
    task, draft, workflow = drafted(home, project)
    shown = home.task_workflow(draft["id"], {"action": "preview"})
    workflow.write_text(WORKFLOW.replace("echo collected", "echo changed"), encoding="utf-8")
    with pytest.raises(ApprovalConflict):
        home.task_workflow(
            draft["id"], {"action": "switch", "fingerprint": shown["changes"]["fingerprint"]}
        )
    assert TriggerStore(home.data_dir).list_triggers() == []
    source = view(home, task["id"])
    assert source["state"] == "enabled" and "workflow_switch" not in source
    with pytest.raises(flow.WorkflowSwitchError):
        home.task_workflow(draft["id"], {"action": "switch"})


def switched_over(home: RemHome, project: Path) -> tuple[dict[str, Any], str]:
    task, draft, _ = drafted(home, project)
    shown = home.task_workflow(draft["id"], {"action": "preview"})
    switched = home.task_workflow(
        draft["id"], {"action": "switch", "fingerprint": shown["changes"]["fingerprint"]}
    )
    assert view(home, task["id"])["state"] == "paused"
    return task, str(switched["trigger_id"])


def test_resuming_through_rems_chat_undoes_switch_over(home: RemHome, project: Path) -> None:
    task, trigger_id = switched_over(home, project)
    home.generate = lambda p, c, e: {
        "reply": "Resumed.",
        "controls": [{"task_id": task["id"], "action": "resume"}],
    }
    home.submit_message({"request_id": "resume-message", "body": "Resume the morning digest"})
    home._reply()

    source = view(home, task["id"])
    assert source["state"] == "enabled" and source["enabled"] is True
    assert source["workflow_switch"]["active"] is False
    assert source["workflow_switch"]["trigger_off"] is True
    assert TriggerStore(home.data_dir).get(trigger_id).enabled is False


def test_an_undo_recorded_in_a_transaction_waits_for_its_commit(
    home: RemHome, project: Path
) -> None:
    task, trigger_id = switched_over(home, project)
    store = TriggerStore(home.data_dir)
    # A chat reply that fails after resuming rolls back: the task stays paused and the
    # workflow keeps its trigger, so the job still runs exactly once per scheduled time.
    with pytest.raises(RuntimeError), home.store.connect() as db:
        current = home.store.get(db, "tasks", task["id"])
        home.edit_task(
            task["id"],
            {"request_id": "r1", "expected_revision": current["revision"], "enabled": True},
            "edit",
            db=db,
        )
        raise RuntimeError("reply failed")
    home.tick()
    assert view(home, task["id"])["state"] == "paused"
    assert view(home, task["id"])["workflow_switch"]["active"] is True
    assert store.get(trigger_id).enabled is True

    # Committed, the trigger turns off on the next settle (after the reply, or a tick).
    with home.store.connect() as db:
        current = home.store.get(db, "tasks", task["id"])
        home.edit_task(
            task["id"],
            {"request_id": "r2", "expected_revision": current["revision"], "enabled": True},
            "edit",
            db=db,
        )
    assert store.get(trigger_id).enabled is True
    home.tick()
    assert store.get(trigger_id).enabled is False
    assert view(home, task["id"])["workflow_switch"]["trigger_off"] is True
