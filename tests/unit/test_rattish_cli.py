from __future__ import annotations

import json
import tomllib
from pathlib import Path

from typer.testing import CliRunner

from gofer.cli.main import app

runner = CliRunner()


def test_distribution_packages_rattish_assistant_resources() -> None:
    repository = Path(__file__).parents[2]
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    included = project["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]

    assert included["rattish/spec"] == "gofer/rattish/assets/docs"
    assert included["skills/gofer-flow-workflow-builder"] == "gofer/rattish/assets/assistant-skill"
    frozen_build = (repository / "gof.spec").read_text(encoding="utf-8")
    assert '("rattish/spec", "gofer/rattish/assets/docs")' in frozen_build
    assert (
        '("skills/gofer-flow-workflow-builder", "gofer/rattish/assets/assistant-skill")'
        in frozen_build
    )


def test_rattish_docs_reports_installed_authoring_resources() -> None:
    result = runner.invoke(app, ["rattish", "docs", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert Path(payload["overview"]).is_file()
    assert Path(payload["grammar"]).is_file()
    assert Path(payload["staticSemantics"]).is_file()
    assert Path(payload["nodeContractsGuide"]).is_file()
    assert (Path(payload["contractsRoot"]) / "agent.json").is_file()
    assert (Path(payload["schemasRoot"]) / "ir.schema.json").is_file()


def _read_file_source(name: str = "CLI slice") -> str:
    return f"""Rattish: 1

Workflow:
  name: {name}

Node read:
  type: read-file
  path: "input.txt"
"""


def _invoke_json(command: str, source: Path, data_dir: Path):
    result = runner.invoke(
        app,
        ["rattish", command, str(source), "--format", "json", "--data-dir", str(data_dir)],
    )
    payload = json.loads(result.stdout)
    return result, payload


def test_rattish_check_compiles_to_internal_cache_and_reuses_valid_artifact(
    tmp_path: Path,
) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_read_file_source(), encoding="utf-8")
    data_dir = tmp_path / "app-data"

    first, first_payload = _invoke_json("check", source, data_dir)
    second, second_payload = _invoke_json("check", source, data_dir)

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert first_payload["ok"] is True
    assert first_payload["cache"]["hit"] is False
    assert second_payload["cache"]["hit"] is True
    assert (
        second_payload["cache"]["compilation_fingerprint"]
        == first_payload["cache"]["compilation_fingerprint"]
    )
    artifacts = list((data_dir / "radish" / "artifacts").glob("*.json"))
    assert len(artifacts) == 1
    cached = json.loads(artifacts[0].read_text(encoding="utf-8"))
    assert cached["source_path"] == str(source)
    assert cached["ir"]["compiler"]["version"] == "0.2.1"
    assert cached["ir"]["workflow"]["name"] == "CLI slice"


def test_rattish_check_invalidates_cache_when_source_changes(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_read_file_source(), encoding="utf-8")
    data_dir = tmp_path / "app-data"
    first, first_payload = _invoke_json("check", source, data_dir)

    source.write_text(_read_file_source("Changed slice"), encoding="utf-8")
    changed, changed_payload = _invoke_json("check", source, data_dir)

    assert first.exit_code == 0
    assert changed.exit_code == 0
    assert changed_payload["cache"]["hit"] is False
    assert (
        changed_payload["cache"]["compilation_fingerprint"]
        != first_payload["cache"]["compilation_fingerprint"]
    )


def test_rattish_check_rebuilds_a_corrupt_internal_artifact(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_read_file_source(), encoding="utf-8")
    data_dir = tmp_path / "app-data"
    first, _ = _invoke_json("check", source, data_dir)
    artifact_path = next((data_dir / "radish" / "artifacts").glob("*.json"))
    artifact_path.write_text("not json", encoding="utf-8")

    rebuilt, payload = _invoke_json("check", source, data_dir)

    assert first.exit_code == 0
    assert rebuilt.exit_code == 0, rebuilt.output
    assert payload["cache"]["hit"] is False
    assert json.loads(artifact_path.read_text(encoding="utf-8"))["ir"]["ir_version"] == 1


def test_failed_rattish_check_preserves_last_valid_artifact(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    valid_source = _read_file_source()
    source.write_text(valid_source, encoding="utf-8")
    data_dir = tmp_path / "app-data"
    first, _ = _invoke_json("check", source, data_dir)
    artifact_path = next((data_dir / "radish" / "artifacts").glob("*.json"))
    published = artifact_path.read_bytes()

    source.write_text("Rattish: 1\n\nWorkflow:\n  name:\n", encoding="utf-8")
    failed, _ = _invoke_json("check", source, data_dir)
    source.write_text(valid_source, encoding="utf-8")
    restored, restored_payload = _invoke_json("check", source, data_dir)

    assert first.exit_code == 0
    assert failed.exit_code == 1
    assert artifact_path.read_bytes() == published
    assert restored.exit_code == 0
    assert restored_payload["cache"]["hit"] is True


def test_rattish_preflight_uses_cached_ir_and_checks_current_environment(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_read_file_source(), encoding="utf-8")
    data_dir = tmp_path / "app-data"

    missing, missing_payload = _invoke_json("preflight", source, data_dir)
    (tmp_path / "input.txt").write_text("ready", encoding="utf-8")
    ready, ready_payload = _invoke_json("preflight", source, data_dir)

    assert missing.exit_code == 1
    assert missing_payload["ok"] is False
    assert [item["code"] for item in missing_payload["diagnostics"]] == [
        "RATTISH_PREFLIGHT_RESOURCE_MISSING"
    ]
    assert ready.exit_code == 0, ready.output
    assert ready_payload["ok"] is True
    assert ready_payload["cache"]["hit"] is True
    assert ready_payload["diagnostics"] == []


def test_rattish_cli_loads_script_contract_and_preflights_cached_ir(tmp_path: Path) -> None:
    script = tmp_path / "job.py"
    script.write_text("print('ready')\n", encoding="utf-8")
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1

Workflow:
  name: Script CLI slice

Node run:
  type: python-script
  script-path: "job.py"
""",
        encoding="utf-8",
    )
    data_dir = tmp_path / "app-data"

    checked, checked_payload = _invoke_json("check", source, data_dir)
    ready, ready_payload = _invoke_json("preflight", source, data_dir)

    assert checked.exit_code == 0, checked.output
    assert checked_payload["cache"]["hit"] is False
    assert ready.exit_code == 0, ready.output
    assert ready_payload["cache"]["hit"] is True
    assert ready_payload["ok"] is True


def test_rattish_check_uses_bundled_provider_contract_without_local_cli(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1

Workflow:
  name: Portable agent

Node review:
  type: agent
  provider: codex
  prompt: Review the change
""",
        encoding="utf-8",
    )
    data_dir = tmp_path / "app-data"

    checked, payload = _invoke_json("check", source, data_dir)

    assert checked.exit_code == 0, checked.output
    assert payload["ok"] is True
    artifact_path = next((data_dir / "radish" / "artifacts").glob("*.json"))
    ir = json.loads(artifact_path.read_text(encoding="utf-8"))["ir"]
    resolution = ir["nodes"][0]["resolutions"]["provider"]
    assert resolution["provider_id"] == "codex"
    assert resolution["model"] == "gpt-5.6-sol"
    assert resolution["effort"] == "high"


def test_rattish_check_returns_machine_readable_language_diagnostics(tmp_path: Path) -> None:
    source = tmp_path / "broken.rattish"
    source.write_text("Rattish: 1\n\nWorkflow:\n  name:\n", encoding="utf-8")

    result, payload = _invoke_json("check", source, tmp_path / "app-data")

    assert result.exit_code == 1
    assert payload["ok"] is False
    assert payload["cache"] is None
    assert payload["diagnostics"]
    diagnostic = payload["diagnostics"][0]
    assert diagnostic["code"].startswith("RATTISH_")
    assert diagnostic["severity"] == "error"
    assert diagnostic["span"]["start"]["line"] >= 1


def test_rattish_check_reports_source_io_failure_with_usage_exit_code(tmp_path: Path) -> None:
    missing = tmp_path / "missing.rattish"

    result, payload = _invoke_json("check", missing, tmp_path / "app-data")

    assert result.exit_code == 2
    assert payload["diagnostics"][0]["code"] == "RATTISH_ARTIFACT_IO_ERROR"
    assert payload["diagnostics"][0]["phase"] == "lowering"


def test_rattish_check_text_output_names_source_and_cache_state(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_read_file_source(), encoding="utf-8")
    data_dir = tmp_path / "app-data"

    first = runner.invoke(app, ["rattish", "check", str(source), "--data-dir", str(data_dir)])
    second = runner.invoke(app, ["rattish", "check", str(source), "--data-dir", str(data_dir)])

    assert first.exit_code == 0
    assert f"{source}: valid Rattish (compiled)" in first.output
    assert second.exit_code == 0
    assert f"{source}: valid Rattish (cached)" in second.output


def test_rattish_compile_publishes_internal_ir_and_inspect_prints_it(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_read_file_source(), encoding="utf-8")
    data_dir = tmp_path / "app-data"

    compiled, payload = _invoke_json("compile", source, data_dir)
    inspected = runner.invoke(
        app,
        ["rattish", "inspect-ir", str(source), "--data-dir", str(data_dir)],
    )

    assert compiled.exit_code == 0, compiled.output
    assert payload["command"] == "compile"
    assert payload["ir"]["version"] == 1
    assert payload["ir"]["workflow_id"] == tmp_path.name.replace("_", "-")
    assert inspected.exit_code == 0, inspected.output
    ir = json.loads(inspected.stdout)
    assert ir["ir_version"] == 1
    assert ir["workflow"]["name"] == "CLI slice"
    assert not list(tmp_path.glob("*.ir.json"))


def test_rattish_format_check_and_write_modes(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_read_file_source().replace("Rattish", "RATTISH"), encoding="utf-8")
    original = source.read_text(encoding="utf-8")

    needed = runner.invoke(app, ["rattish", "format", str(source), "--check"])
    formatted = runner.invoke(app, ["rattish", "format", str(source)])
    clean = runner.invoke(app, ["rattish", "format", str(source), "--check"])

    assert needed.exit_code == 1
    assert source.read_text(encoding="utf-8") != original
    assert formatted.exit_code == 0, formatted.output
    assert clean.exit_code == 0, clean.output
    assert source.read_text(encoding="utf-8").startswith("rattish: 1\n")


def test_rattish_format_stdout_does_not_modify_source(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    original = _read_file_source().replace("Rattish", "RATTISH")
    source.write_text(original, encoding="utf-8")

    result = runner.invoke(app, ["rattish", "format", str(source), "--stdout"])

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("rattish: 1\n")
    assert source.read_text(encoding="utf-8") == original


def test_rattish_create_and_list_use_registered_project_layout(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "app-data"

    created = runner.invoke(
        app,
        [
            "rattish",
            "create",
            "Review PR",
            "--project",
            str(project),
            "--registry-dir",
            str(registry),
            "--format",
            "json",
        ],
    )
    listed = runner.invoke(
        app,
        ["rattish", "list", "--registry-dir", str(registry), "--format", "json"],
    )

    assert created.exit_code == 0, created.output
    created_payload = json.loads(created.stdout)
    assert created_payload["workflow"]["workflowRoot"] == str(project / ".raticode" / "review-pr")
    assert listed.exit_code == 0, listed.output
    listed_payload = json.loads(listed.stdout)
    assert listed_payload["projects"] == [
        {
            "name": "project",
            "root": str(project),
            "workflows": [created_payload["workflow"]],
        }
    ]


def test_rattish_run_executes_persists_and_reports_json_result(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(
        """Rattish: 1
Workflow:
  name: CLI run
  inputs:
    count:
      schema: {"type": "integer"}
      required: true
Node execute:
  type: bash-command
  command: "printf complete"
""",
        encoding="utf-8",
    )
    data_dir = tmp_path / "app-data"

    result = runner.invoke(
        app,
        [
            "rattish",
            "run",
            str(source),
            "--input",
            "count=3",
            "--format",
            "json",
            "--data-dir",
            str(data_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["run"]["status"] == "passed"
    assert payload["run"]["input_names"] == ["count"]
    assert payload["run"]["latest_node_outputs"]["execute"]["stdout"] == "complete"
    assert Path(payload["artifact_path"]).is_file()


def test_rattish_run_uses_stable_failure_and_usage_exit_codes(tmp_path: Path) -> None:
    source = tmp_path / "workflow.rattish"
    source.write_text(_read_file_source(), encoding="utf-8")
    data_dir = tmp_path / "app-data"

    preflight_failure = runner.invoke(
        app,
        [
            "rattish",
            "run",
            str(source),
            "--format",
            "json",
            "--data-dir",
            str(data_dir),
        ],
    )
    invalid_cli_input = runner.invoke(
        app,
        [
            "rattish",
            "run",
            str(source),
            "--input",
            "broken",
            "--format",
            "json",
            "--data-dir",
            str(data_dir),
        ],
    )

    assert preflight_failure.exit_code == 1
    failed_payload = json.loads(preflight_failure.stdout)
    assert failed_payload["run"]["status"] == "preflight_failed"
    assert Path(failed_payload["artifact_path"]).is_file()
    assert invalid_cli_input.exit_code == 2
    invalid_payload = json.loads(invalid_cli_input.stdout)
    assert invalid_payload["diagnostics"][0]["code"] == "RATTISH_CLI_INPUT_INVALID"
