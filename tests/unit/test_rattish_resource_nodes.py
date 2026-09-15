from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.preflight import run_preflight
from gofer.rattish.runtime import execute_node
from gofer.rattish.workflow_runtime import execute_workflow

PROJECT_ROOT = Path(__file__).parents[2]
RATTISH_ROOT = PROJECT_ROOT / "rattish"
CONTRACT_NAMES = ("file", "folder", "open-resource", "prompt-file")


def compiler() -> RattishCompiler:
    return RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[RATTISH_ROOT / "contracts" / f"{name}.json" for name in CONTRACT_NAMES],
    )


def compile_source(source: str, project_root: Path) -> dict[str, Any]:
    return compiler().compile(source, CompileContext("resource-nodes", project_root)).ir


@pytest.mark.anyio
async def test_file_and_folder_nodes_publish_resolved_path_metadata(tmp_path: Path) -> None:
    file_path = tmp_path / "artifacts" / "report.txt"
    file_path.parent.mkdir()
    file_path.write_text("report", encoding="utf-8")
    source = """Rattish: 1
Workflow:
  name: Resource metadata
Node report:
  type: file
  path: artifacts/report.txt
Node artifacts:
  type: folder
  path: artifacts
"""

    result = await execute_workflow(compile_source(source, tmp_path))

    assert result.outcome == "pass"
    assert result.latest_node_outputs["report"] == {
        "path": str(file_path),
        "file_path": str(file_path),
        "file_name": "report.txt",
        "file_stem": "report",
        "file_extension": ".txt",
        "parent_path": str(file_path.parent),
        "directory": str(file_path.parent),
    }
    assert result.latest_node_outputs["artifacts"]["folder_name"] == "artifacts"


@pytest.mark.anyio
async def test_prompt_file_renders_static_and_bound_variables_atomically(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Render prompt
  inputs:
    subject:
      schema: {"type": "string"}
      required: true
Node render:
  type: prompt-file
  output-path: generated/review.md
  template: |
    Review {{subject}} for {{audience}}.
  variables: {"audience": "maintainers"}
  with:
    subject: input.subject
"""

    result = await execute_workflow(
        compile_source(source, tmp_path), workflow_inputs={"subject": "the patch"}
    )

    output_path = tmp_path / "generated" / "review.md"
    assert result.outcome == "pass"
    assert output_path.read_text(encoding="utf-8") == ("Review the patch for maintainers.\n")
    assert result.latest_node_outputs["render"]["inputs"] == {
        "audience": "maintainers",
        "subject": "the patch",
    }


def test_prompt_file_preflight_checks_template_and_destination(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Missing template
Node render:
  type: prompt-file
  output-path: generated/review.md
  template-path: prompts/missing.md
"""

    result = run_preflight(compile_source(source, tmp_path), data_dir=tmp_path / "data")

    assert not result.ready
    assert "RATTISH_PREFLIGHT_RESOURCE_MISSING" in {item.code for item in result.diagnostics}


@pytest.mark.anyio
async def test_open_resource_normalizes_type_and_dispatches_platform_opener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    async def fake_run_subprocess(command, **kwargs):
        _ = kwargs
        calls.append(command)
        return 0, "", ""

    monkeypatch.setattr("gofer.rattish.runtime.run_subprocess", fake_run_subprocess)
    source = """Rattish: 1
Workflow:
  name: Open URL
Node docs:
  type: open-resource
  target: https://example.com/docs
"""

    result = await execute_node(compile_source(source, tmp_path), "docs")

    assert result.outcome == "success"
    assert result.output == {
        "target": "https://example.com/docs",
        "resource_type": "url",
    }
    assert calls and calls[0][-1] == "https://example.com/docs"


def test_file_and_folder_preflight_require_the_declared_path_kind(tmp_path: Path) -> None:
    (tmp_path / "actual-file").write_text("x", encoding="utf-8")
    source = """Rattish: 1
Workflow:
  name: Wrong kinds
Node expected-file:
  type: file
  path: missing.txt
Node expected-folder:
  type: folder
  path: actual-file
"""

    result = run_preflight(compile_source(source, tmp_path), data_dir=tmp_path / "data")

    assert not result.ready
    assert [item.code for item in result.diagnostics] == [
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
        "RATTISH_PREFLIGHT_RESOURCE_MISSING",
    ]
