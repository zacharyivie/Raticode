from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import anyio
import pytest

import gofer.ui.api as api
from gofer.rattish.editor import source_revision
from gofer.rattish.run_service import (
    effective_run_status,
    has_live_run,
    process_start_token,
    request_run_stop,
    run_rattish_file,
    run_stop_marker,
)
from gofer.rattish.runtime import (
    DEFAULT_NODE_HANDLERS,
    HandlerResult,
    NodeHandlerRegistry,
    RuntimeErrorInfo,
)
from gofer.rattish.workspaces import create_registered_workflow


@pytest.mark.anyio
async def test_synchronous_run_stays_live_with_matching_source_revision(tmp_path: Path) -> None:
    base, workflow = setup_workflow(tmp_path)
    release = anyio.Event()
    started = anyio.Event()
    live = {}
    final = {}

    async def handler(node, context, bindings):
        await release.wait()
        return HandlerResult(True, {"stdout": "done", "stderr": "", "exit_code": 0})

    def on_started(run):
        live.update(run.document)
        started.set()

    async def execute():
        run = await run_rattish_file(
            workflow.entrypoint,
            data_dir=base,
            handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
            on_started=on_started,
            expected_revision=source_revision(workflow.entrypoint.read_text()),
        )
        final.update(run.document)

    async with anyio.create_task_group() as group:
        group.start_soon(execute)
        await started.wait()
        try:
            payload = api.workflow_run_log_payload(workflow.workflow_id, live["run_id"], base)
            assert payload["status"] == "running"
            assert payload["success"] is None
            assert payload["workflowId"] == workflow.workflow_id
        finally:
            release.set()
    assert final["status"] == "passed"


def setup_workflow(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    base = tmp_path / "data"
    workflow = create_registered_workflow(project, "Live run", registry_dir=base)
    workflow.entrypoint.write_text(
        "Rattish: 1\nWorkflow:\n  name: Live run\n"
        "Node work:\n  type: bash-command\n  command: fake-only\n",
        encoding="utf-8",
    )
    return base, workflow


@pytest.mark.anyio
@pytest.mark.parametrize("ending", ["passed", "failed", "stopped"])
async def test_live_progress_survives_completion_failure_and_stop(
    tmp_path: Path, ending: str
) -> None:
    base, workflow = setup_workflow(tmp_path)
    workflow.entrypoint.write_text(
        "Rattish: 1\nWorkflow:\n  name: Live progress\n"
        "Node first:\n  type: bash-command\n  command: fake\n  to: work\n"
        "Node work:\n  type: bash-command\n  command: fake\n  needs: first\n"
    )
    release, working = anyio.Event(), anyio.Event()
    cancel = threading.Event()
    final = {}
    run_id = "live-progress"

    async def handler(node, context, bindings):
        if node["id"] == "work":
            working.set()
            await release.wait()
            if ending == "failed":
                return HandlerResult(
                    False,
                    {"stdout": "", "stderr": "broken command", "exit_code": 7},
                    RuntimeErrorInfo("command", "RATTISH_TEST_FAILURE", "broken command"),
                )
        return HandlerResult(True, {"stdout": node["id"], "stderr": "", "exit_code": 0})

    async def execute():
        result = await run_rattish_file(
            workflow.entrypoint,
            data_dir=base,
            run_id=run_id,
            cancel_event=cancel,
            handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
        )
        final.update(result.document)

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(execute)
            await working.wait()
            try:
                latest = api.latest_workflow_log_payload(workflow.workflow_id, base)
                selected = api.workflow_run_log_payload(
                    workflow.workflow_id, run_id, base, include_details=False
                )
                events = api.workflow_run_events_payload(workflow.workflow_id, run_id, base)
                assert latest["runEvents"] == selected["runEvents"] == events["runEvents"]
                assert latest["runNodes"] == selected["runNodes"] == events["runNodes"]
                assert latest["runNodes"]["first"]["status"] == "success"
                assert latest["nodeOutputs"]["first"]["data"]["stdout"] == "first"
                assert latest["runNodes"]["work"]["status"] == "started"
                assert latest["runNodes"]["work"]["finishedAt"] is None
                assert latest["runEvents"][-1]["message"] == "Activation 1 started."
                assert latest["runEvents"][-1]["activationLineageId"] == "root"
                if ending == "stopped":
                    cancel.set()
            finally:
                if ending != "stopped":
                    release.set()

    assert final["status"] == ending
    assert final["latest_node_outputs"]["first"]["stdout"] == "first"
    payload = api.workflow_run_log_payload(workflow.workflow_id, run_id, base)
    assert payload["runNodes"]["first"]["status"] == "success"
    assert (
        payload["runNodes"]["work"]["status"]
        == {"passed": "success", "failed": "error", "stopped": "stopped"}[ending]
    )
    assert [event["sequence"] for event in final["events"]] == list(
        range(1, len(final["events"]) + 1)
    )
    if ending == "failed":
        assert payload["runEvents"][-2]["message"] == "broken command"
    if ending == "stopped":
        assert payload["runEvents"][-1]["status"] == "stopped"


@pytest.mark.anyio
async def test_concurrent_loop_progress_keeps_running_activation_and_retry_attempts(
    tmp_path: Path,
) -> None:
    base, workflow = setup_workflow(tmp_path)
    workflow.entrypoint.write_text("""Rattish: 1
Workflow:
  name: Concurrent progress
Node loop:
  type: loop
  source: {"type": "count", "count": 2, "max-concurrency": 2}
  to: work
Node work:
  type: bash-command
  command: fake
  max-concurrency: 2
  retry-count: 1
  retry-delay: 1ms
  needs: loop
""")
    release = anyio.Event()
    attempts: dict[int, int] = {}
    final = {}
    run_id = "loop-progress"

    async def handler(node, context, bindings):
        index = context.node_outputs["loop"]["index"]
        attempts[index] = attempts.get(index, 0) + 1
        if index == 0:
            await release.wait()
        elif attempts[index] == 1:
            return HandlerResult(
                False, {}, RuntimeErrorInfo("command", "RATTISH_TEST_RETRY", "try again")
            )
        return HandlerResult(True, {"stdout": str(index), "stderr": "", "exit_code": 0})

    handlers = NodeHandlerRegistry(
        {
            "raticode.loop": DEFAULT_NODE_HANDLERS.require("raticode.loop"),
            "raticode.bash_command": handler,
        }
    )

    async def execute():
        result = await run_rattish_file(
            workflow.entrypoint, data_dir=base, run_id=run_id, handlers=handlers
        )
        final.update(result.document)

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(execute)
            try:
                while True:
                    await anyio.sleep(0.01)
                    payload = api.latest_workflow_log_payload(workflow.workflow_id, base)
                    if any(
                        e["nodeId"] == "work" and e["status"] == "completed"
                        for e in payload["runEvents"]
                    ):
                        break
                assert payload["runNodes"]["work"]["status"] == "started"
                work_events = [e for e in payload["runEvents"] if e["nodeId"] == "work"]
                assert work_events[-1]["status"] == "completed"
                assert work_events[-1]["fanOutItem"] == {"index": 1}
                assert work_events[-1]["attempt"] == 2
                assert work_events[-1]["runNumber"] == 2
                assert any(e["status"] == "retried" for e in work_events)
            finally:
                release.set()
    assert final["status"] == "passed"
    payload = api.workflow_run_log_payload(workflow.workflow_id, run_id, base)
    assert payload["runNodes"]["work"]["status"] == "success"
    assert len(payload["runNodes"]["work"]["attempts"]) == 2
    finished_attempts = payload["runNodes"]["work"]["attempts"]
    assert finished_attempts[0]["attempt"] == 2
    assert finished_attempts[0]["fanOutItem"] == {"index": 1}


@pytest.mark.anyio
async def test_background_runs_have_exact_ids_and_stop_only_one_after_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, workflow = setup_workflow(tmp_path)
    release = threading.Event()
    cleaned: set[str] = set()

    async def handler(node, context, bindings):
        try:
            while not release.is_set():
                await anyio.sleep(0.01)
            return HandlerResult(True, {"stdout": "done", "stderr": "", "exit_code": 0})
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.sleep(0.02)
                cleaned.add(context.run_id)

    async def fake_run(*args, **kwargs):
        return await run_rattish_file(
            *args, **kwargs, handlers=NodeHandlerRegistry({"raticode.bash_command": handler})
        )

    monkeypatch.setattr(api, "run_rattish_file", fake_run)
    first = await api.run_workflow_payload(workflow.workflow_id, base, background=True)
    second = await api.run_workflow_payload(workflow.workflow_id, base, background=True)
    try:
        assert first["workflowId"] == second["workflowId"] == workflow.workflow_id
        assert first["runId"] != second["runId"]
        assert first["status"] == second["status"] == "running"
        assert first["success"] is None
        assert first["graphSnapshot"]["nodes"][0]["id"] == "work"
        assert (
            len(
                api.list_workflow_run_logs_payload(workflow.workflow_id, base, status="running")[
                    "runs"
                ]
            )
            == 2
        )
        acknowledgement = api.stop_workflow_run_payload(workflow.workflow_id, base, first["runId"])
        assert acknowledgement["stopped"] is True
        with anyio.fail_after(5):
            while (
                api.workflow_run_log_payload(workflow.workflow_id, first["runId"], base)["status"]
                == "running"
            ):
                await anyio.sleep(0.01)
        stopped = api.workflow_run_log_payload(workflow.workflow_id, first["runId"], base)
        assert stopped["status"] == "stopped"
        assert first["runId"] in cleaned
        assert (
            api.workflow_run_log_payload(workflow.workflow_id, second["runId"], base)["status"]
            == "running"
        )
        assert second["runId"] not in cleaned
        assert (
            api.stop_workflow_run_payload(workflow.workflow_id, base, "wrong-id")["stopped"]
            is False
        )
    finally:
        release.set()
        with anyio.fail_after(5):
            while (
                api.workflow_run_log_payload(workflow.workflow_id, second["runId"], base)["status"]
                == "running"
            ):
                await anyio.sleep(0.01)
    assert (
        api.workflow_run_log_payload(workflow.workflow_id, second["runId"], base)["status"]
        == "success"
    )


@pytest.mark.anyio
async def test_background_run_rejects_changed_source_and_reports_preflight_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, workflow = setup_workflow(tmp_path)
    invoked = []

    async def handler(node, context, bindings):
        invoked.append(node["id"])
        return HandlerResult(True, {"stdout": "", "stderr": "", "exit_code": 0})

    async def fake_run(*args, **kwargs):
        return await run_rattish_file(
            *args, **kwargs, handlers=NodeHandlerRegistry({"raticode.bash_command": handler})
        )

    monkeypatch.setattr(api, "run_rattish_file", fake_run)
    old_revision = source_revision(workflow.entrypoint.read_text())
    workflow.entrypoint.write_text(
        "Rattish: 1\nWorkflow:\n  name: Changed\n"
        "Node missing:\n  type: read-file\n  path: missing.txt\n"
    )
    with pytest.raises(api.WorkflowRunError, match="source changed"):
        await api.run_workflow_payload(
            workflow.workflow_id, base, background=True, expected_revision=old_revision
        )
    assert invoked == []
    result = await api.run_workflow_payload(workflow.workflow_id, base, background=True)
    assert result["status"] == "error"
    assert result["rattishRun"]["status"] == "preflight_failed"
    assert invoked == []


@pytest.mark.anyio
async def test_orphaned_live_artifact_is_disconnected_never_succeeded(
    tmp_path: Path,
) -> None:
    base, workflow = setup_workflow(tmp_path)

    async def handler(node, context, bindings):
        return HandlerResult(True, {"stdout": "", "stderr": "", "exit_code": 0})

    run = await run_rattish_file(
        workflow.entrypoint,
        data_dir=base,
        handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
    )
    orphan = {**run.document, "status": "running", "runner_pid": 2147483647}
    run.path.write_text(json.dumps(orphan))
    result = api.workflow_run_log_payload(workflow.workflow_id, run.document["run_id"], base)
    assert result["status"] == "disconnected"
    assert result["success"] is None
    assert (
        api.stop_workflow_run_payload(workflow.workflow_id, base, run.document["run_id"])["stopped"]
        is False
    )


@pytest.mark.anyio
async def test_rattish_runs_refuse_resume_options_instead_of_rerunning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gofer.core.executor import ResumeOptions

    base, workflow = setup_workflow(tmp_path)
    started: list[object] = []

    async def fake_run(*args, **kwargs):
        started.append(args)
        raise AssertionError("Resume must not start a fresh Rattish run")

    monkeypatch.setattr(api, "run_rattish_file", fake_run)
    with pytest.raises(api.WorkflowRunError, match="Rattish runs resume where they stopped"):
        await api.run_workflow_payload(
            workflow.workflow_id,
            base,
            resume_options=ResumeOptions(run_id="earlier", from_node="step"),
        )
    assert not started


@pytest.mark.anyio
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux exposes process start")
async def test_a_run_whose_pid_another_program_reused_reads_as_interrupted(
    tmp_path: Path,
) -> None:
    """After a restart, a dead runner's PID can belong to an unrelated live program."""
    base, workflow = setup_workflow(tmp_path)
    seen: dict[str, object] = {}

    async def handler(node, context, bindings):
        return HandlerResult(True, {"stdout": "", "stderr": "", "exit_code": 0})

    def on_started(result) -> None:
        seen.update(result.document)

    run = await run_rattish_file(
        workflow.entrypoint,
        data_dir=base,
        handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
        on_started=on_started,
    )
    assert seen["runner_start"] == process_start_token(os.getpid())

    # The parent process is alive but didn't start this run.
    live_stranger = os.getppid()
    reused = {
        **run.document,
        "status": "running",
        "runner_pid": live_stranger,
        "runner_start": "an-earlier-boot:12345",
    }
    assert effective_run_status(reused) == "interrupted"
    run.path.write_text(json.dumps(reused))
    result = api.workflow_run_log_payload(workflow.workflow_id, run.document["run_id"], base)
    assert result["status"] == "disconnected"
    assert not has_live_run(workflow.entrypoint, data_dir=base)

    # The same program, still going, keeps counting as live.
    same = {**reused, "runner_start": process_start_token(live_stranger)}
    assert effective_run_status(same) == "running"


@pytest.mark.anyio
async def test_a_stop_marker_ends_a_run_that_has_no_cancel_event(tmp_path: Path) -> None:
    """Another process asks through a file beside the record; the run ends as stopped."""
    base, workflow = setup_workflow(tmp_path)
    working = anyio.Event()
    cleaned: list[str] = []
    started: dict[str, Path] = {}
    final = {}

    async def handler(node, context, bindings):
        working.set()
        try:
            await anyio.sleep(60)
        finally:
            cleaned.append(node["id"])
        return HandlerResult(True, {"stdout": "", "stderr": "", "exit_code": 0})

    async def execute():
        result = await run_rattish_file(
            workflow.entrypoint,
            data_dir=base,
            handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
            on_started=lambda run: started.setdefault("path", run.path),
        )
        final.update(result.document)

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(execute)
            await working.wait()
            record = started["path"]
            assert await anyio.to_thread.run_sync(request_run_stop, record) is True
            assert run_stop_marker(record).is_file()
    assert final["status"] == "stopped"
    assert final["events"][-1] == {**final["events"][-1], "status": "stopped"}
    assert cleaned == ["work"]
    # The request ends with the run, and a finished run can't be asked again.
    assert not run_stop_marker(record).exists()
    assert request_run_stop(record) is False
    assert not run_stop_marker(record).exists()


@pytest.mark.anyio
async def test_the_app_stop_reaches_a_run_it_did_not_start_and_runs_shows_the_request(
    tmp_path: Path,
) -> None:
    """A run this backend doesn't track, such as a trigger scheduler's, is asked by marker.

    The node keeps going for a moment after the request, as a provider call or a
    command's cleanup can, so Runs must say "Stop requested" until it really ends.
    """
    base, workflow = setup_workflow(tmp_path)
    working, release = anyio.Event(), threading.Event()
    started: dict[str, Path] = {}
    final = {}

    async def handler(node, context, bindings):
        working.set()
        with anyio.CancelScope(shield=True):
            while not release.is_set():
                await anyio.sleep(0.01)
        return HandlerResult(True, {"stdout": "", "stderr": "", "exit_code": 0})

    async def execute():
        result = await run_rattish_file(
            workflow.entrypoint,
            data_dir=base,
            handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
            on_started=lambda run: started.setdefault("path", run.path),
        )
        final.update(result.document)

    def runs_row(run_id: str) -> dict[str, object]:
        runs = api.list_workflow_run_logs_payload(workflow.workflow_id, base)["runs"]
        return next(run for run in runs if run["id"] == run_id)

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(execute)
            await working.wait()
            run_id = started["path"].stem
            assert runs_row(run_id)["stopRequested"] is False
            answer = api.stop_workflow_run_payload(workflow.workflow_id, base, run_id)
            assert answer == {
                "workflowId": workflow.workflow_id,
                "runId": run_id,
                "stopped": True,
                "message": "Stop requested",
            }
            await anyio.sleep(0.5)  # Past the runner's marker check; the node is shielded.
            row = runs_row(run_id)
            assert (row["status"], row["stopRequested"]) == ("running", True)
            detail = api.workflow_run_log_payload(workflow.workflow_id, run_id, base)
            assert (detail["status"], detail["stopRequested"]) == ("running", True)
            release.set()
    assert final["status"] == "stopped"
    row = runs_row(run_id)
    assert (row["status"], row["stopRequested"]) == ("stopped", False)
    again = api.stop_workflow_run_payload(workflow.workflow_id, base, run_id)
    assert again["stopped"] is False and again["message"] == "This run is no longer running."
    assert not run_stop_marker(started["path"]).exists()


@pytest.mark.anyio
async def test_stopping_a_workflow_without_a_run_id_asks_every_running_run(
    tmp_path: Path,
) -> None:
    base, workflow = setup_workflow(tmp_path)
    working = anyio.Event()
    count = {"n": 0}
    finals: list[str] = []

    async def handler(node, context, bindings):
        count["n"] += 1
        if count["n"] == 2:
            working.set()
        await anyio.sleep(60)
        return HandlerResult(True, {"stdout": "", "stderr": "", "exit_code": 0})

    async def execute():
        result = await run_rattish_file(
            workflow.entrypoint,
            data_dir=base,
            handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
        )
        finals.append(result.status)

    assert api.stop_workflow_run_payload(workflow.workflow_id, base)["stopped"] is False
    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(execute)
            group.start_soon(execute)
            await working.wait()
            answer = api.stop_workflow_run_payload(workflow.workflow_id, base)
            assert answer["stopped"] is True and len(answer["runIds"]) == 2
    assert finals == ["stopped", "stopped"]
