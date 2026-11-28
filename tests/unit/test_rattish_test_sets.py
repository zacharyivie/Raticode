from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core.agent import AgentResult
from gofer.core.provider_profiles import ResolvedProviderSettings
from gofer.rattish.test_sets import (
    WorkflowTestError,
    compare_results,
    enforce_cost_limit,
    estimate_test_set,
    list_test_results,
    parse_target,
    prepare_test_run,
    resolve_target,
    run_test_set,
    save_test_set,
    validate_test_set,
    with_comparison,
    workflow_tests_file,
)
from gofer.rattish.workspaces import create_registered_workflow
from gofer.subscriptions.base import Subscription
from gofer.ui import workflow_tests

runner = CliRunner()

GREETER = """Rattish: 1

Workflow:
  name: Greeter
  inputs:
    name:
      schema: {"type": "string"}
      required: true
  outputs:
    greeting:
      from: node.greet.output.stdout
      schema: {"type": "string"}

Node greet:
  type: bash-command
  command: printf '%s, %s!' "$GREETING" "$NAME"
  env:
    "GREETING": Hello
  with:
    name: input.name
"""

SUMMARIZER = """Rattish: 1

Workflow:
  name: Summarizer
  inputs:
    topic:
      schema: {"type": "string"}
      required: true
  outputs:
    summary:
      from: node.write.output
      schema: {"type": "string"}

Node write:
  type: agent
  provider: codex
  prompt: Summarize {{topic}}
  with:
    topic: input.topic
"""


def greeter_tests() -> dict[str, Any]:
    return {
        "testSetVersion": 1,
        "cases": [
            {
                "id": "alice",
                "name": "Greets Alice",
                "inputs": {"name": "Alice"},
                "checks": [
                    {"type": "text", "target": "outputs.greeting", "equals": "Hello, Alice!"},
                    {
                        "type": "text",
                        "target": "outputs.greeting",
                        "matches": "^hello",
                        "ignoreCase": True,
                    },
                    {"type": "json", "target": "nodes.greet.exit_code", "equals": 0},
                    {"type": "json", "target": "outputs.missing", "exists": False},
                    {"type": "command", "target": "outputs.greeting", "command": "grep -q Alice"},
                ],
            },
            {
                "id": "bob",
                "inputs": {"name": "Bob"},
                "checks": [{"type": "text", "target": "outputs.greeting", "contains": "Alice"}],
            },
        ],
    }


class ScriptedSubscription(Subscription):
    """Answers each prompt through ``respond`` and reports a fixed cost per call."""

    def __init__(self, respond: Callable[[str], str], cost: float | None = 0.01) -> None:
        self.respond = respond
        self.cost = cost
        self.prompts: list[str] = []

    def _build_command(
        self,
        prompt: str,
        tools: list[str],
        mcp_servers: list[str],
        extra_paths: list[Path] | None = None,
        provider_settings: ResolvedProviderSettings | None = None,
    ) -> list[str]:
        return ["scripted"]

    def is_available(self) -> bool:
        return True

    async def execute(
        self,
        prompt: str,
        working_dir: Path,
        tools: list[str],
        mcp_servers: list[str],
        env: dict[str, str],
        timeout: float | None = None,
        cancel_event: Any | None = None,
        extra_paths: list[Path] | None = None,
        max_output_bytes: int | None = None,
        on_thought: Callable[[str], None] | None = None,
        provider_settings: ResolvedProviderSettings | None = None,
    ) -> AgentResult:
        self.prompts.append(prompt)
        usage: dict[str, object] = {"total_tokens": 100}
        if self.cost is not None:
            usage["cost_usd"] = self.cost
        return AgentResult(
            agent_id="scripted",
            success=True,
            output=self.respond(prompt),
            exit_code=0,
            duration_seconds=0.01,
            usage_metadata=usage,
        )


# Writes a workflow and its test set, unlike the shared write.
def write(tmp_path: Path, source: str, tests: dict[str, Any] | None) -> Path:
    path = tmp_path / "project" / "workflow.rattish"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    if tests is not None:
        save_test_set(workflow_tests_file(path), tests)
    return path


def test_validation_reports_duplicate_ids_operators_regexes_and_targets() -> None:
    document = {
        "testSetVersion": 1,
        "cases": [
            {
                "id": "one",
                "checks": [
                    {"type": "text", "equals": "a", "contains": "b"},
                    {"type": "text", "matches": "("},
                    {"type": "json", "target": "output.x", "exists": True},
                ],
            },
            {"id": "one"},
        ],
    }
    errors = validate_test_set(document)
    assert any("used more than once" in error for error in errors)
    assert any("exactly one of equals, contains" in error for error in errors)
    assert any("not a valid regular expression" in error for error in errors)
    assert any("must start with one of status, error, outputs, nodes" in error for error in errors)
    schema_errors = validate_test_set(
        {"testSetVersion": 1, "cases": [{"id": "x", "checks": [{"type": "command"}]}]}
    )
    assert any("'command' is a required property" in error for error in schema_errors)
    assert validate_test_set(greeter_tests()) == []


def test_targets_read_outputs_nodes_and_list_items() -> None:
    view = {"status": "passed", "outputs": {"items": [{"name": "a"}]}, "nodes": {}, "error": None}
    assert parse_target("outputs.items[0].name") == ["outputs", "items", 0, "name"]
    assert resolve_target(view, "outputs.items[0].name") == "a"
    assert resolve_target(view, "status") == "passed"
    for bad in ("", ".outputs", "outputs..x", "result.x"):
        with pytest.raises(ValueError):
            parse_target(bad)


@pytest.mark.anyio
async def test_cases_run_checks_save_results_and_compare_revisions(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    source = write(tmp_path, GREETER, greeter_tests())
    prepared = prepare_test_run(source, data_dir=data_dir)
    assert prepared.estimate["totals"]["modelCalls"] == 0
    assert prepared.estimate["totals"]["costUsd"] == 0.0
    assert "expected cost $0" in prepared.estimate["summary"]

    first = await run_test_set(prepared)

    assert first["status"] == "failed"
    alice, bob = first["cases"]
    assert alice["status"] == "passed", alice
    assert [check["status"] for check in alice["checks"]] == ["passed"] * 6
    assert alice["checks"][0]["type"] == "status"
    assert alice["checks"][-1]["detail"]["exitCode"] == 0
    assert alice["runId"] and Path(alice["runFile"]).is_file()
    assert bob["status"] == "failed"
    assert bob["message"] == 'outputs.greeting contains "Alice": Got "Hello, Bob!".'
    assert first["totals"] == {
        "cases": 2,
        "passed": 1,
        "failed": 1,
        "errors": 0,
        "skipped": 0,
        "modelCalls": 0,
        "judgeCalls": 0,
        "costUsd": 0.0,
        "costComplete": True,
    }
    saved = list_test_results(prepared.directory)
    assert [item["id"] for item in saved] == [first["id"]]
    assert saved[0]["revision"] == prepared.ir["source"]["source_fingerprint"]
    assert with_comparison(first, saved)["comparison"] is None

    # A changed source is a new revision; the next result is compared with the last one.
    source.write_text(GREETER.replace("Hello", "Hello Alice"), encoding="utf-8")
    second = await run_test_set(prepare_test_run(source, data_dir=data_dir))
    comparison = with_comparison(second, list_test_results(prepared.directory))["comparison"]
    assert comparison["previousId"] == first["id"]
    assert comparison["summary"]["broke"] == 1 and comparison["summary"]["fixed"] == 1
    assert {item["id"]: item["change"] for item in comparison["cases"]} == {
        "alice": "broke",
        "bob": "fixed",
    }
    assert '-    "GREETING": Hello' in comparison["sourceDiff"]
    assert '+    "GREETING": Hello Alice' in comparison["sourceDiff"]


@pytest.mark.anyio
async def test_expected_failures_bad_inputs_and_case_filters(tmp_path: Path) -> None:
    tests = {
        "testSetVersion": 1,
        "cases": [
            {"id": "missing-input", "inputs": {}},
            {"id": "fails", "inputs": {"name": "x"}, "expectStatus": "failed"},
            {"id": "any", "inputs": {"name": "y"}, "expectStatus": "any"},
        ],
    }
    source = write(tmp_path, GREETER.replace("printf", "false && printf"), tests)
    with pytest.raises(WorkflowTestError, match="Unknown test case 'nope'"):
        prepare_test_run(source, data_dir=tmp_path / "data", case_ids=["nope"])
    result = await run_test_set(prepare_test_run(source, data_dir=tmp_path / "data"))
    statuses = {case["id"]: case["status"] for case in result["cases"]}
    assert statuses == {"missing-input": "error", "fails": "passed", "any": "passed"}
    assert result["totals"]["errors"] == 1
    only = await run_test_set(
        prepare_test_run(source, data_dir=tmp_path / "data", case_ids=["fails"])
    )
    assert [case["id"] for case in only["cases"]] == ["fails"]


@pytest.mark.anyio
async def test_model_calls_are_metered_judged_and_used_for_the_next_estimate(
    tmp_path: Path,
) -> None:
    tests = {
        "testSetVersion": 1,
        "cases": [
            {
                "id": "cats",
                "inputs": {"topic": "cats"},
                "checks": [
                    {"type": "text", "target": "outputs.summary", "contains": "Cats"},
                    {"type": "judge", "target": "outputs.summary", "criteria": "Mentions cats."},
                ],
            },
            {
                "id": "dogs",
                "inputs": {"topic": "dogs"},
                "checks": [{"type": "judge", "criteria": "Mentions dogs.", "label": "About dogs"}],
            },
        ],
    }
    source = write(tmp_path, SUMMARIZER, tests)

    def respond(prompt: str) -> str:
        if "grading one output" in prompt:
            passed = "Cats" in prompt.split("<<<OUTPUT", 1)[1]
            return json.dumps({"pass": passed, "reason": "Checked the topic."})
        return "Cats are great." if "cats" in prompt else "Birds sing."

    provider = ScriptedSubscription(respond, cost=0.01)
    prepared = prepare_test_run(source, data_dir=tmp_path / "data")
    assert prepared.estimate["totals"]["modelCalls"] == 4
    assert prepared.estimate["totals"]["costUsd"] is None
    assert "unknown until a test run reports it" in prepared.estimate["summary"]
    with pytest.raises(WorkflowTestError, match="unknown"):
        enforce_cost_limit(prepared.estimate, 1.0)

    result = await run_test_set(prepared, subscriptions={"codex": provider})

    cats, dogs = result["cases"]
    assert cats["status"] == "passed", cats
    assert dogs["status"] == "failed"
    assert dogs["checks"][-1]["label"] == "About dogs"
    assert dogs["checks"][-1]["message"] == "Checked the topic."
    assert cats["usage"] == {
        "modelCalls": 1,
        "costUsd": 0.01,
        "totalTokens": 100,
        "judgeCalls": 1,
        "judgeCostUsd": 0.01,
    }
    assert result["totals"]["costUsd"] == pytest.approx(0.04)
    judge_prompt = next(prompt for prompt in provider.prompts if "grading one output" in prompt)
    assert "Mentions cats." in judge_prompt and "Return only one JSON value" in judge_prompt

    estimate = prepare_test_run(source, data_dir=tmp_path / "data").estimate
    assert estimate["totals"]["costUsd"] == pytest.approx(0.04)
    assert estimate["totals"]["costComplete"] is True
    assert {case["basis"] for case in estimate["cases"]} == {"measured"}
    assert estimate["summary"].startswith("About $0.04 for 2 cases (4 model calls)")
    enforce_cost_limit(estimate, 0.05)
    with pytest.raises(WorkflowTestError, match="over the limit"):
        enforce_cost_limit(estimate, 0.03)

    skipped = await run_test_set(
        prepare_test_run(source, data_dir=tmp_path / "data", skip_judge=True),
        subscriptions={"codex": provider},
    )
    assert [check["status"] for check in skipped["cases"][1]["checks"]] == ["passed", "skipped"]
    assert skipped["totals"]["judgeCalls"] == 0


def test_estimate_uses_average_call_cost_for_new_cases() -> None:
    ir = {"nodes": [{"type": "agent", "resolutions": {"provider": {"provider_id": "codex"}}}]}
    history = [
        {
            "status": "passed",
            "cases": [{"id": "old", "usage": {"modelCalls": 2, "costUsd": 0.2, "judgeCalls": 0}}],
        }
    ]
    tests = {"testSetVersion": 1, "cases": [{"id": "old"}, {"id": "new"}]}
    estimate = estimate_test_set(ir, tests, history)
    by_case = {case["id"]: case for case in estimate["cases"]}
    assert by_case["old"] == {**by_case["old"], "basis": "measured", "costUsd": 0.2}
    assert by_case["new"]["basis"] == "average"
    assert by_case["new"]["costUsd"] == pytest.approx(0.1)


def test_compare_marks_new_removed_and_changed_cases() -> None:
    previous = {
        "id": "1",
        "revision": "sha256:a",
        "sourceText": "a",
        "cases": [
            {"id": "gone", "status": "passed"},
            {"id": "err", "status": "failed"},
        ],
    }
    current = {
        "id": "2",
        "revision": "sha256:b",
        "sourceText": "b",
        "cases": [
            {"id": "err", "status": "error"},
            {"id": "added", "status": "passed"},
        ],
    }
    changes = {item["id"]: item["change"] for item in compare_results(current, previous)["cases"]}
    assert changes == {"err": "changed", "added": "new", "gone": "removed"}


def test_cli_runs_estimates_and_lists_history(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    source = write(tmp_path, GREETER, greeter_tests())
    common = ["--data-dir", str(data_dir)]

    estimate = runner.invoke(
        app, ["rattish", "test", str(source), "--estimate", "--format", "json", *common]
    )
    assert estimate.exit_code == 0, estimate.output
    assert json.loads(estimate.stdout)["estimate"]["totals"]["cases"] == 2

    failing = runner.invoke(app, ["rattish", "test", str(source), *common])
    assert failing.exit_code == 1, failing.output
    assert "Expected cost: 2 cases with no model calls" in failing.output
    assert "PASS  alice  Greets Alice" in failing.output
    assert 'x outputs.greeting contains "Alice": Got "Hello, Bob!".' in failing.output

    passing = runner.invoke(
        app, ["rattish", "test", str(source), "--case", "alice", "--format", "json", *common]
    )
    assert passing.exit_code == 0, passing.output
    payload = json.loads(passing.stdout)
    assert payload["ok"] is True and Path(payload["resultPath"]).is_file()

    history = runner.invoke(app, ["rattish", "test", str(source), "--history", *common])
    assert history.exit_code == 0, history.output
    assert history.output.count("(current)") == 2

    tests_path = workflow_tests_file(source)
    tests_path.write_text('{"testSetVersion": 1, "cases": [{"id": "x", "checks": [{}]}]}')
    invalid = runner.invoke(app, ["rattish", "test", str(source), *common])
    assert invalid.exit_code == 2
    assert "is invalid" in invalid.output
    tests_path.unlink()
    missing = runner.invoke(app, ["rattish", "test", str(source), *common])
    assert missing.exit_code == 2 and "No test set" in missing.output


def test_studio_payloads_save_estimate_run_poll_and_stop(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    workflow = create_registered_workflow(project, "Greeter", registry_dir=data_dir)
    workflow.entrypoint.write_text(GREETER, encoding="utf-8")

    empty = workflow_tests.workflow_tests_payload(workflow.workflow_id, data_dir)
    assert empty["exists"] is False
    assert empty["testSet"] == {"testSetVersion": 1, "cases": []}
    assert empty["revision"].startswith("sha256:")

    with pytest.raises(WorkflowTestError, match="invalid"):
        workflow_tests.save_workflow_tests_payload(
            workflow.workflow_id, data_dir, {"testSet": {"testSetVersion": 1, "cases": [{}]}}
        )
    saved = workflow_tests.save_workflow_tests_payload(
        workflow.workflow_id, data_dir, {"testSet": greeter_tests()}
    )
    assert saved["exists"] is True
    assert (workflow.workflow_root / "workflow.tests.json").is_file()
    assert saved["estimate"]["totals"]["cases"] == 2

    with pytest.raises(WorkflowTestError, match="changed since the panel loaded"):
        workflow_tests.start_workflow_tests_payload(
            workflow.workflow_id, data_dir, {"expectedRevision": "sha256:old"}
        )
    started = workflow_tests.start_workflow_tests_payload(
        workflow.workflow_id, data_dir, {"expectedRevision": saved["revision"]}
    )
    result_id = started["result"]["id"]
    assert started["result"]["status"] == "running"
    deadline = time.monotonic() + 30
    while True:
        result = workflow_tests.workflow_test_result_payload(
            workflow.workflow_id, data_dir, result_id
        )["result"]
        if result["status"] != "running" or time.monotonic() > deadline:
            break
        time.sleep(0.05)
    assert result["status"] == "failed"
    assert (workflow.workflow_root / "logs" / "tests" / f"{result_id}.json").is_file()
    after = workflow_tests.workflow_tests_payload(workflow.workflow_id, data_dir)
    assert after["latest"]["id"] == result_id
    assert after["results"][0]["cases"] == {"alice": "passed", "bob": "failed"}
    assert after["activeResultId"] is None
    with pytest.raises(WorkflowTestError, match="not going"):
        workflow_tests.stop_workflow_tests_payload(workflow.workflow_id, data_dir, result_id)
    with pytest.raises(WorkflowTestError, match="Invalid test result ID"):
        workflow_tests.workflow_test_result_payload(workflow.workflow_id, data_dir, "../x")
