"""How often a running run's record is saved and how much of it each save checks."""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import anyio
import pytest

from gofer.core.approvals import ApprovalStore
from gofer.rattish import run_service
from gofer.rattish.run_journal import journal_path
from gofer.rattish.run_retention import read_run_record
from gofer.rattish.run_service import resume_rattish_run, run_rattish_file
from gofer.rattish.run_values import delete_run_files
from gofer.rattish.runtime import (
    DEFAULT_NODE_HANDLERS,
    HandlerResult,
    NodeHandlerRegistry,
    RuntimeErrorInfo,
)

_RUN_SCHEMA_ID = "urn:raticode:rattish:schema:run-1.json"


class _CountedValidator:
    def __init__(self, validator: Any, checks: list[Any]) -> None:
        self._validator = validator
        self._checks = checks

    def validate(self, instance: Any) -> None:
        self._checks.append(instance)
        self._validator.validate(instance)


@pytest.fixture
def whole_record_checks(monkeypatch: pytest.MonkeyPatch):
    """Every check of a whole run record against run.schema.json, by any means."""
    checks: list[Any] = []
    real = run_service.Draft202012Validator

    def counting(schema: Any, *args: Any, **kwargs: Any) -> Any:
        validator = real(schema, *args, **kwargs)
        if isinstance(schema, dict) and schema.get("$id") == _RUN_SCHEMA_ID:
            return _CountedValidator(validator, checks)
        return validator

    cached = getattr(run_service, "_run_validators", None)
    if cached is not None:
        cached.cache_clear()
    monkeypatch.setattr(run_service, "Draft202012Validator", counting)
    yield checks
    if cached is not None:
        cached.cache_clear()


def _loop_source(count: int) -> str:
    return f"""Rattish: 1
Workflow:
  name: Long loop
Node loop:
  type: loop
  source: {{"type": "count", "count": {count}}}
  to: say
Node say:
  type: bash-command
  command: echo hi
  needs: loop
"""


@pytest.mark.anyio
async def test_long_loop_checks_the_whole_record_only_on_its_first_and_final_save(
    tmp_path: Path, whole_record_checks: list[Any]
) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_loop_source(300), encoding="utf-8")

    async def say(node, context, bindings):
        return HandlerResult(
            True, {"stdout": "hi\n", "stderr": "", "exit_code": 0, "truncated": False}
        )

    handlers = NodeHandlerRegistry(
        {
            "raticode.loop": DEFAULT_NODE_HANDLERS.require("raticode.loop"),
            "raticode.bash_command": say,
        }
    )
    result = await run_rattish_file(source, data_dir=tmp_path / "data", handlers=handlers)

    assert result.status == "passed", result.document["error"]
    assert len(whole_record_checks) <= 2
    assert [check["status"] for check in whole_record_checks] == ["running", "passed"]
    saved = json.loads(result.path.read_text(encoding="utf-8"))
    assert len(saved["runs"]) == 301
    assert [run["node_id"] for run in saved["runs"]].count("say") == 300
    assert [event["sequence"] for event in saved["events"]] == list(
        range(1, len(saved["events"]) + 1)
    )
    # The validators are built once, not once per save.
    assert run_service._run_validators.cache_info().misses == 1


@pytest.mark.anyio
async def test_failed_step_is_saved_before_the_next_step_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only a save the failure itself asked for can put it on disk in time.
    monkeypatch.setattr(run_service, "_LIVE_SAVE_SECONDS", 3600.0, raising=False)
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: Failure saved
Node first:
  type: bash-command
  command: exit 3
  allow-fail: true
  to:
    - recover when failed
Node recover:
  type: bash-command
  command: echo recovered
  needs: first
""",
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    run_id = "failure-saved"
    record_path = run_service.run_artifact_path(source, run_id, data_dir=data_dir)
    seen: dict[str, Any] = {}

    async def handler(node, context, bindings):
        if node["id"] == "first":
            return HandlerResult(
                False,
                {"stdout": "", "stderr": "broken", "exit_code": 3},
                RuntimeErrorInfo("command", "RATTISH_TEST_FAILURE", "broken"),
            )
        seen["record"] = json.loads(record_path.read_text(encoding="utf-8"))
        return HandlerResult(
            True, {"stdout": "recovered", "stderr": "", "exit_code": 0, "truncated": False}
        )

    result = await run_rattish_file(
        source,
        data_dir=data_dir,
        run_id=run_id,
        handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
    )

    assert result.status == "passed", result.document["error"]
    record = seen["record"]
    assert record["status"] == "running"
    [failed] = [run for run in record["runs"] if run["node_id"] == "first"]
    assert failed["outcome"] == "allowed_failure"
    assert failed["error"]["message"] == "broken"
    assert any(
        event["type"] == "node_completed" and event.get("node_id") == "first"
        for event in record["events"]
    )


@pytest.mark.anyio
async def test_waiting_approval_is_saved_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_service, "_LIVE_SAVE_SECONDS", 3600.0, raising=False)
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: Approval saved
Node gate:
  type: approval-gate
  message: Ship it?
""",
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    store = ApprovalStore(data_dir)
    run_id = "approval-saved"
    holder: dict[str, Any] = {}

    async def run() -> None:
        holder["result"] = await run_rattish_file(source, data_dir=data_dir, run_id=run_id)

    with anyio.fail_after(10):
        async with anyio.create_task_group() as group:
            group.start_soon(run)
            while not store.list_pending():
                await anyio.sleep(0.01)
            path = run_service.run_artifact_path(source, run_id, data_dir=data_dir)
            record = json.loads(path.read_text(encoding="utf-8"))
            pending = store.list_pending()[0]
            store.decide(
                pending.workflow_id, pending.run_id, "gate", "approved", decided_by="owner"
            )

    assert record["status"] == "running"
    assert [event["type"] for event in record["events"] if event.get("node_id") == "gate"] == [
        "node_started"
    ]
    assert holder["result"].status == "passed"


# Runs one workflow in a process of its own, saving the whole record only at its start,
# so only the journal can hold the steps it finished.
_RUNNER = """
import sys
from pathlib import Path

import anyio

from gofer.rattish import run_service

run_service._LIVE_SAVE_SECONDS = 3600.0
source, data_dir, run_id = sys.argv[1:4]


async def main():
    await run_service.run_rattish_file(Path(source), data_dir=Path(data_dir), run_id=run_id)


anyio.run(main)
"""


def _line_count(path: Path) -> int:
    try:
        return path.read_bytes().count(b"\n")
    except FileNotFoundError:
        return 0


@pytest.mark.anyio
async def test_killed_run_resumes_after_its_last_finished_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Inside a Rem turn every step would be wrapped for a user session the sandbox lacks.
    monkeypatch.delenv("RATICODE_AGENT_SLICE", raising=False)
    lines = tmp_path / "lines.txt"
    source = tmp_path / "workflow.rattish"
    source.write_text(
        f"""Rattish: 1
Workflow:
  name: Killed loop
Node loop:
  type: loop
  source: {{"type": "count", "count": 1000}}
  to: say
Node say:
  type: bash-command
  command: echo step >> '{lines}'
  needs: loop
""",
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    run_id = "killed-loop"
    record = run_service.run_artifact_path(source, run_id, data_dir=data_dir)
    src = Path(run_service.__file__).resolve().parents[2]
    env = {k: v for k, v in os.environ.items() if k != "RATICODE_AGENT_SLICE"}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(src), env.get("PYTHONPATH")]))
    runner = subprocess.Popen(
        [sys.executable, "-c", _RUNNER, str(source), str(data_dir), run_id],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 120
        while _line_count(lines) < 600:
            assert runner.poll() is None, runner.stderr.read().decode() if runner.stderr else ""
            assert time.monotonic() < deadline, "the loop never reached 600 steps"
            time.sleep(0.005)
        os.kill(runner.pid, signal.SIGKILL)
    finally:
        runner.kill()
        runner.wait()
        if runner.stderr is not None:
            runner.stderr.close()
    journal = journal_path(record)
    assert stat.S_IMODE(journal.stat().st_mode) == 0o600
    # A runner killed while writing leaves a torn last line.
    with journal.open("ab") as output:
        output.write(b'{"event": {"sequence": ')
    killed_at = _line_count(lines)
    assert killed_at < 1000
    stale = json.loads(record.read_text(encoding="utf-8"))
    assert stale["status"] == "running"
    assert len(stale["runs"]) <= 1

    document = read_run_record(record).document

    assert document["status"] == "interrupted"
    assert not journal.exists()
    assert [run["node_id"] for run in document["runs"]].count("say") >= killed_at - 1
    assert [event["sequence"] for event in document["events"]] == list(
        range(1, len(document["events"]) + 1)
    )
    result = await resume_rattish_run(source, document, data_dir=data_dir)
    assert result.status == "passed", result.document["error"]
    assert 1000 <= _line_count(lines) <= 1001
    assert not journal_path(result.path).exists()


@pytest.mark.anyio
async def test_journal_holds_each_step_while_running_and_goes_with_the_run(
    tmp_path: Path,
) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_loop_source(3), encoding="utf-8")
    data_dir = tmp_path / "data"
    run_id = "journaled"
    record = run_service.run_artifact_path(source, run_id, data_dir=data_dir)
    seen: list[list[dict[str, Any]]] = []

    async def say(node, context, bindings):
        entries = journal_path(record).read_text(encoding="utf-8").splitlines()
        seen.append([json.loads(entry) for entry in entries])
        return HandlerResult(
            True, {"stdout": "hi\n", "stderr": "", "exit_code": 0, "truncated": False}
        )

    handlers = NodeHandlerRegistry(
        {
            "raticode.loop": DEFAULT_NODE_HANDLERS.require("raticode.loop"),
            "raticode.bash_command": say,
        }
    )
    result = await run_rattish_file(source, data_dir=data_dir, run_id=run_id, handlers=handlers)

    assert result.status == "passed", result.document["error"]
    # Each step finds the one before it already journaled: its run and completion.
    finished = [
        sum(1 for entry in entries if entry["node_run"] and entry["node_run"]["node_id"] == "say")
        for entries in seen
    ]
    assert finished == [0, 1, 2]
    assert not journal_path(record).exists()

    leftover = journal_path(record)
    leftover.write_text("{}\n", encoding="utf-8")
    delete_run_files(record)
    assert not leftover.exists()
    assert not record.exists()
