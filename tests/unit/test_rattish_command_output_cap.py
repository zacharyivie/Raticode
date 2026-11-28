"""Command and script steps past the 2,000,000-byte output limit.

stdout and stderr have separate budgets, the error text at the end of a long run
survives, ``truncated`` says a stream was cut, and nothing is written into the
command's output that a ``with: stdin:`` binding would pass on as data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from gofer.core.resources import DEFAULT_RESOURCE_LIMITS
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.runtime import command_failure_message, execute_node
from gofer.rattish.workflow_runtime import execute_workflow
from gofer.utils.process import CapturedOutput, run_command_capture, run_subprocess

PROJECT_ROOT = Path(__file__).parents[2]
RATTISH_ROOT = PROJECT_ROOT / "rattish"
LIMIT = DEFAULT_RESOURCE_LIMITS.max_subprocess_output_bytes
OLD_NOTE = "[subprocess output truncated"


def compile_source(source: str, tmp_path: Path) -> dict[str, Any]:
    compiler = RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[
            RATTISH_ROOT / "contracts" / f"{name}.json"
            for name in ("bash-command", "python-script", "shell-script", "open-resource")
        ],
    )
    return compiler.compile(source, CompileContext("output-cap", tmp_path)).ir


def run_event_schema() -> dict[str, Any]:
    schema = json.loads((RATTISH_ROOT / "schemas" / "run.schema.json").read_text())
    return {**schema["properties"]["events"]["items"], "$defs": schema["$defs"]}


async def run_with_events(ir: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    result = await execute_workflow(ir, on_node_event=lambda event, _record: events.append(event))
    return result, events


def test_the_cap_is_two_million_bytes() -> None:
    assert LIMIT == 2_000_000


@pytest.mark.anyio
async def test_failed_command_keeps_its_error_when_stdout_passes_the_cap(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: Loud failure
Node build:
  type: bash-command
  command: |
    head -c 2500000 /dev/zero | tr '\\0' a
    echo 'fatal: bad config' >&2
    exit 3
""",
        tmp_path,
    )

    result, events = await run_with_events(ir)

    (record,) = result.runs
    assert record.result.outcome == "failure"
    assert record.result.error is not None
    assert "fatal: bad config" in record.result.error.message
    output = record.result.output
    assert output["truncated"] is True
    assert output["exit_code"] == 3
    assert output["stderr"] == "fatal: bad config\n"
    assert output["stdout"] == "a" * LIMIT
    (event,) = [event for event in events if event["type"] == "node_output_truncated"]
    assert event["node_id"] == "build"
    assert event["limit_bytes"] == LIMIT
    assert event["streams"] == ["stdout"]
    assert "'build'" in event["message"] and "2,000,000-byte" in event["message"]
    Draft202012Validator(run_event_schema()).validate({**event, "sequence": 1})


@pytest.mark.anyio
async def test_cut_stdout_piped_to_stdin_carries_only_the_command_bytes(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: Big pipe
Node produce:
  type: bash-command
  command: head -c 3000000 /dev/zero | tr '\\0' x
  to: count
Node count:
  type: bash-command
  command: wc -c
  needs: produce
  with:
    stdin: node.produce.output.stdout
""",
        tmp_path,
    )

    result, _events = await run_with_events(ir)

    assert result.outcome == "pass"
    runs = {record.node_id: record.result.output for record in result.runs}
    upstream = runs["produce"]["stdout"]
    assert OLD_NOTE not in upstream
    assert runs["produce"]["truncated"] is True
    assert int(runs["count"]["stdout"].strip()) == len(upstream) == LIMIT
    assert runs["count"]["truncated"] is False


@pytest.mark.anyio
async def test_long_stderr_keeps_its_last_lines(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: Noisy stderr
Node build:
  type: bash-command
  command: |
    head -c 2500000 /dev/zero | tr '\\0' w >&2
    echo 'fatal: the real cause' >&2
    exit 1
""",
        tmp_path,
    )

    result, events = await run_with_events(ir)

    (record,) = result.runs
    output = record.result.output
    assert output["truncated"] is True
    assert output["stderr"].endswith("fatal: the real cause\n")
    assert len(output["stderr"].encode()) <= LIMIT
    assert OLD_NOTE not in output["stderr"]
    assert record.result.error is not None
    assert record.result.error.message.endswith("fatal: the real cause")
    (event,) = [event for event in events if event["type"] == "node_output_truncated"]
    assert event["streams"] == ["stderr"]


@pytest.mark.anyio
async def test_failure_reason_uses_the_real_end_of_a_cut_stdout(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: Quiet stderr
Node build:
  type: bash-command
  command: |
    head -c 2500000 /dev/zero | tr '\\0' a
    echo 'error: disk full'
    exit 2
""",
        tmp_path,
    )

    execution = await execute_node(ir, "build")

    assert execution.outcome == "failure"
    assert execution.output["stderr"] == ""
    assert "error: disk full" not in execution.output["stdout"]
    assert execution.error is not None
    assert execution.error.message.startswith("Command exited with status 2.")
    assert execution.error.message.endswith("error: disk full")


@pytest.mark.anyio
@pytest.mark.parametrize("node_type", ["python-script", "shell-script"])
async def test_script_steps_report_truncated(tmp_path: Path, node_type: str) -> None:
    if node_type == "python-script":
        (tmp_path / "loud.py").write_text(
            "import sys\nsys.stdout.write('p' * 2_100_000)\n", encoding="utf-8"
        )
        script = "loud.py"
    else:
        (tmp_path / "loud.sh").write_text(
            "head -c 2100000 /dev/zero | tr '\\0' s\n", encoding="utf-8"
        )
        script = "loud.sh"
    ir = compile_source(
        f"""Rattish: 1
Workflow:
  name: Script cap
Node run:
  type: {node_type}
  script-path: {script}
""",
        tmp_path,
    )

    execution = await execute_node(ir, "run")

    assert execution.outcome == "success"
    assert execution.output["truncated"] is True
    assert len(execution.output["stdout"]) == LIMIT
    assert OLD_NOTE not in execution.output["stdout"]
    assert execution.notices[0]["type"] == "node_output_truncated"


@pytest.mark.anyio
async def test_quiet_command_is_not_truncated(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: Quiet
Node hello:
  type: bash-command
  command: printf hi; printf oops >&2
""",
        tmp_path,
    )

    result, events = await run_with_events(ir)

    assert result.runs[0].result.output == {
        "stdout": "hi",
        "stderr": "oops",
        "exit_code": 0,
        "truncated": False,
    }
    assert not [event for event in events if event["type"] == "node_output_truncated"]


@pytest.mark.anyio
async def test_open_resource_failure_names_the_opener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing_opener(command: list[str], **kwargs: Any) -> CapturedOutput:
        _ = command, kwargs
        return CapturedOutput(4, "no handler for this link\n", "")

    monkeypatch.setattr("gofer.rattish.runtime.run_command_capture", failing_opener)
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: Open
Node docs:
  type: open-resource
  target: https://example.com/docs
""",
        tmp_path,
    )

    execution = await execute_node(ir, "docs")

    assert execution.outcome == "failure"
    assert execution.error is not None
    assert execution.error.message == (
        "Resource opener exited with status 4. no handler for this link"
    )


def test_failure_message_quotes_the_end_of_stdout_when_stderr_is_empty() -> None:
    stdout = "progress line\n" * 2000 + "error: the last line"

    message = command_failure_message("Command", 1, stdout, "")

    assert message.startswith("Command exited with status 1.")
    assert message.endswith("error: the last line")
    assert len(message) <= len("Command exited with status 1. ") + 8192


def test_failure_message_still_reads_json_errors() -> None:
    message = command_failure_message("Script", 1, json.dumps({"error": "bad token"}), "")

    assert message == "Script exited with status 1. bad token"


@pytest.mark.anyio
async def test_separate_budgets_cut_at_a_character_boundary() -> None:
    captured = await run_command_capture(
        ["python3", "-c", "import sys; sys.stdout.write('é' * 100)"],
        max_output_bytes=51,
    )

    assert captured.stdout == "é" * 25
    assert captured.stdout_truncated is True
    assert captured.stderr_truncated is False


@pytest.mark.anyio
async def test_shared_budget_callers_keep_the_in_band_note() -> None:
    returncode, stdout, stderr = await run_subprocess(
        ["python3", "-c", "import sys; sys.stdout.write('x' * 500); sys.stderr.write('late')"],
        max_output_bytes=100,
    )

    assert returncode == 0
    assert "[subprocess output truncated at 100 bytes]" in stdout
    assert len(stdout.encode()) + len(stderr.encode()) <= 100
