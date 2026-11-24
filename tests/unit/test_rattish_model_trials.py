"""Try another model on a test set: overrides, the side-by-side result and Use this model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.rattish.artifacts import compile_rattish_file
from gofer.rattish.model_overrides import (
    ModelOverride,
    ModelOverrideError,
    parse_model_override,
    use_model,
)
from gofer.rattish.run_service import run_rattish_file
from gofer.rattish.test_sets import (
    WorkflowTestError,
    list_test_results,
    prepare_test_run,
    previous_revision_result,
    run_test_set,
    with_comparison,
)
from tests.unit.test_rattish_test_sets import ScriptedSubscription, write

runner = CliRunner()

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


def case_set() -> dict[str, Any]:
    return {
        "testSetVersion": 1,
        "judge": {"provider": "codex"},
        "cases": [
            {
                "id": "cats",
                "inputs": {"topic": "cats"},
                "checks": [
                    {"type": "text", "target": "outputs.summary", "contains": "Cats"},
                    {"type": "judge", "target": "outputs.summary", "criteria": "About cats."},
                ],
            },
            {
                "id": "dogs",
                "inputs": {"topic": "dogs"},
                "checks": [{"type": "text", "target": "outputs.summary", "contains": "Dogs"}],
            },
        ],
    }


def judge_or(answer: str):
    def respond(prompt: str) -> str:
        if "grading one output" in prompt:
            return json.dumps({"pass": True, "reason": "Fine."})
        topic = "cats" if "cats" in prompt else "dogs"
        return answer.format(topic=topic.capitalize())

    return respond


def providers() -> tuple[ScriptedSubscription, ScriptedSubscription]:
    # The current model (codex) only gets cats right; the candidate gets both.
    current = ScriptedSubscription(
        lambda prompt: (
            json.dumps({"pass": True, "reason": "Fine."})
            if "grading one output" in prompt
            else ("Cats purr." if "cats" in prompt else "Birds sing.")
        ),
        cost=0.02,
    )
    candidate = ScriptedSubscription(judge_or("{topic} are great."), cost=0.005)
    return current, candidate


def test_parse_accepts_colons_in_the_model_and_refuses_bad_text() -> None:
    assert parse_model_override("write=OpenCode:llama3:8b") == ModelOverride(
        "write", "opencode", "llama3:8b"
    )
    for text in ("write", "write=codex", "=codex:x", "write=codex:", "write=Co dex:x"):
        with pytest.raises(ModelOverrideError):
            parse_model_override(text)


def test_overrides_are_checked_against_the_workflow(tmp_path: Path) -> None:
    source = write(tmp_path, SUMMARIZER, case_set())
    data_dir = tmp_path / "data"
    for override, message in (
        ("nope=codex:x", "has no node 'nope'"),
        ("write=nosuchprovider:x", "can't run with that model"),
    ):
        with pytest.raises(WorkflowTestError, match=message):
            prepare_test_run(source, data_dir=data_dir, models=[parse_model_override(override)])
    current = compile_rattish_file(source, data_dir=data_dir).ir
    model = next(node for node in current["nodes"] if node["id"] == "write")["resolutions"][
        "provider"
    ]["model"]
    with pytest.raises(WorkflowTestError, match="already uses"):
        prepare_test_run(source, data_dir=data_dir, models=[ModelOverride("write", "codex", model)])


async def test_try_runs_both_models_side_by_side_and_keeps_the_source(tmp_path: Path) -> None:
    source = write(tmp_path, SUMMARIZER, case_set())
    original = source.read_text(encoding="utf-8")
    data_dir = tmp_path / "data"
    current, candidate = providers()
    subscriptions = {"codex": current, "claude_code": candidate}
    override = parse_model_override("write=claude-code:claude-sonnet-5-5")

    prepared = prepare_test_run(source, data_dir=data_dir, models=[override])
    assert prepared.baseline is None
    estimate = prepared.cost_estimate
    assert estimate["current"]["reused"] is False
    assert prepared.variant is not None
    assert estimate["candidate"]["variant"]["models"] == [
        {
            "node": "write",
            "provider": "claude-code",
            "model": "claude-sonnet-5-5",
            "fromProvider": "codex",
            "fromModel": prepared.variant["models"][0]["fromModel"],
        }
    ]
    assert "Current model:" in estimate["summary"] and "Candidate (write:" in estimate["summary"]

    seen: list[dict[str, Any]] = []
    result = await run_test_set(prepared, subscriptions=subscriptions, on_progress=seen.append)

    assert source.read_text(encoding="utf-8") == original
    comparison = result["modelComparison"]
    assert comparison["baselineReused"] is False
    rows = {row["id"]: row for row in comparison["cases"]}
    assert rows["cats"]["current"]["status"] == "passed"
    assert rows["cats"]["candidate"]["status"] == "passed", rows["cats"]
    assert rows["dogs"]["current"]["status"] == "failed"
    assert rows["dogs"]["candidate"]["status"] == "passed"
    assert rows["dogs"]["change"] == "fixed"
    assert rows["dogs"]["current"]["costUsd"] == pytest.approx(0.02)
    assert rows["dogs"]["candidate"]["costUsd"] == pytest.approx(0.005)
    assert comparison["totals"]["current"]["passed"] == 1
    assert comparison["totals"]["candidate"]["passed"] == 2
    assert "1 fixed" in comparison["sentence"]
    assert any("modelComparison" in item for item in seen)

    # Judge checks keep their own model: every grading prompt went to codex.
    assert not any("grading one output" in prompt for prompt in candidate.prompts)
    assert sum("grading one output" in prompt for prompt in current.prompts) == 2

    # Each case's run record names the tried model; the source fingerprint is the variant's.
    run_file = Path(result["cases"][0]["runFile"])
    record = json.loads(run_file.read_text(encoding="utf-8"))
    assert record["model_overrides"][0]["provider"] == "claude-code"
    assert record["source"]["source_fingerprint"] != result["revision"]

    history = list_test_results(prepared.directory)
    assert len(history) == 2
    baseline = next(item for item in history if item["id"] == result["baselineId"])
    assert baseline.get("variant") is None and baseline["trialId"] == result["id"]
    assert baseline["status"] == "failed"
    shown = with_comparison(next(item for item in history if item["id"] == result["id"]), history)
    assert shown["modelComparison"]["baselineId"] == baseline["id"]

    # A second try reuses that baseline, prices it at $0 and runs only the candidate.
    current.prompts.clear()
    again = prepare_test_run(source, data_dir=data_dir, models=[override])
    assert again.baseline is not None and again.baseline["id"] == baseline["id"]
    assert again.cost_estimate["current"]["totals"]["costUsd"] == 0.0
    # Two candidate calls at $0.005 plus the judge check, which runs again on codex at $0.02.
    assert again.cost_estimate["candidate"]["totals"]["costUsd"] == pytest.approx(0.03)
    second = await run_test_set(again, subscriptions=subscriptions)
    assert second["modelComparison"]["baselineReused"] is True
    assert not any("Summarize" in prompt for prompt in current.prompts)

    # The workflow's own estimate and revision comparison ignore the tries.
    own = prepare_test_run(source, data_dir=data_dir)
    assert own.estimate["totals"]["costUsd"] == pytest.approx(0.06)
    newest = list_test_results(prepared.directory)
    assert previous_revision_result(newest, newest[0]) is None


async def test_a_bad_override_is_refused_by_the_run_service(tmp_path: Path) -> None:
    source = write(tmp_path, SUMMARIZER, None)
    with pytest.raises(ModelOverrideError, match="doesn't call a model|has no node"):
        await run_rattish_file(
            source,
            workflow_inputs={"topic": "x"},
            data_dir=tmp_path / "data",
            subscriptions={},
            model_overrides=[ModelOverride("missing", "codex", "x")],
        )


def test_use_model_writes_the_node_through_the_editor(tmp_path: Path) -> None:
    from gofer.rattish.workspaces import create_registered_workflow

    data_dir = tmp_path / "data"
    project = tmp_path / "project"
    project.mkdir()
    workflow = create_registered_workflow(project, "Summarizer", registry_dir=data_dir)
    workflow.entrypoint.write_text(SUMMARIZER, encoding="utf-8")
    revision = compile_rattish_file(workflow.entrypoint, data_dir=data_dir).ir["source"][
        "source_fingerprint"
    ]
    with pytest.raises(ModelOverrideError, match="changed since"):
        use_model(
            workflow.entrypoint,
            [ModelOverride("write", "claude-code", "claude-sonnet-5-5")],
            data_dir=data_dir,
            expected_revision="sha256:old",
        )
    result = use_model(
        workflow.entrypoint,
        [ModelOverride("write", "claude-code", "claude-sonnet-5-5")],
        data_dir=data_dir,
        expected_revision=revision,
    )
    text = workflow.entrypoint.read_text(encoding="utf-8")
    assert "provider: claude-code" in text
    assert 'model: "claude-sonnet-5-5"' in text
    assert result["revision"] != revision
    node = next(
        node
        for node in compile_rattish_file(workflow.entrypoint, data_dir=data_dir).ir["nodes"]
        if node["id"] == "write"
    )
    assert node["resolutions"]["provider"]["model"] == "claude-sonnet-5-5"


def test_cli_try_prints_both_estimates_and_the_side_by_side_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write(tmp_path, SUMMARIZER, case_set())
    data_dir = tmp_path / "data"
    current, candidate = providers()
    monkeypatch.setattr(
        "gofer.rattish.provider_runtime.default_provider_subscriptions",
        lambda: {"codex": current, "claude_code": candidate},
    )
    common = ["--data-dir", str(data_dir)]
    model = ["--model", "write=claude-code:claude-sonnet-5-5"]

    estimate = runner.invoke(
        app, ["rattish", "test", str(source), "--estimate", *model, "--format", "json", *common]
    )
    assert estimate.exit_code == 0, estimate.output
    payload = json.loads(estimate.stdout)
    assert payload["estimate"]["current"]["reused"] is False
    assert payload["estimate"]["candidate"]["variant"]["key"] == (
        "write=claude-code:claude-sonnet-5-5"
    )

    tried = runner.invoke(app, ["rattish", "test", str(source), *model, *common])
    assert tried.exit_code == 0, tried.output
    out = " ".join(tried.output.split())
    assert "Current model:" in out and "Candidate (write: claude-code claude-sonnet-5-5):" in out
    assert "dogs FAIL" in out and "PASS" in out and "fixed" in out
    assert "Use it with --use-model write=claude-code:claude-sonnet-5-5" in out

    history = runner.invoke(app, ["rattish", "test", str(source), "--history", *common])
    assert history.exit_code == 0, history.output
    assert "[tried write: claude-code claude-sonnet-5-5]" in " ".join(history.output.split())

    bad = runner.invoke(app, ["rattish", "test", str(source), "--model", "write", *common])
    assert bad.exit_code == 2 and "NODE=PROVIDER:MODEL" in bad.output

    use = runner.invoke(
        app,
        ["rattish", "test", str(source), "--use-model", "write=claude-code:claude-sonnet-5-5"]
        + common,
    )
    assert use.exit_code == 0, use.output
    assert "provider: claude-code" in source.read_text(encoding="utf-8")
