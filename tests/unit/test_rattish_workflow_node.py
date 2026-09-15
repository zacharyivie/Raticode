from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from gofer.rattish.artifacts import compile_rattish_file
from gofer.rattish.diagnostics import RattishCompileError
from gofer.rattish.preflight import run_preflight
from gofer.rattish.run_service import run_rattish_file
from gofer.rattish.workflow_runtime import execute_workflow
from gofer.rattish.workspaces import create_registered_workflow


def _write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _child_source(*, command: str = "printf child", output_type: str = "string") -> str:
    return f"""Rattish: 1
Workflow:
  name: Child
  interface-version: 1
  inputs:
    message:
      schema: {{"type": "string"}}
      required: true
  outputs:
    result:
      from: node.run.output.stdout
      schema: {{"type": "{output_type}"}}
Node run:
  type: bash-command
  command: {command}
"""


def _parent_source(*, child: str = "child/workflow.rattish", binding: str = '"hello"') -> str:
    return f"""Rattish: 1
Workflow:
  name: Parent
Node child:
  type: workflow
  workflow-path: {child}
  with:
    message: {binding}
"""


@pytest.mark.anyio
async def test_workflow_node_compiles_interface_and_executes_public_outputs(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "child" / "workflow.rattish", _child_source())
    parent_path = _write(
        tmp_path / "parent.rattish",
        _parent_source().replace('    message: "hello"', '    message: "hello"\n    label: local'),
    )

    compiled = compile_rattish_file(parent_path, data_dir=tmp_path / "data")
    node = compiled.ir["nodes"][0]

    assert node["configuration"]["version"] == 1
    deliveries = {binding["name"]: binding["delivery"]["kind"] for binding in node["bindings"]}
    assert deliveries == {"label": "local_binding", "message": "workflow_input"}
    assert node["output"]["schema"]["required"] == ["result"]
    assert node["resolutions"]["workflow"]["source_kind"] == "project_path"
    assert any(
        item["kind"] == "workflow_interface" for item in compiled.ir["source"]["dependencies"]
    )

    result = await execute_workflow(compiled.ir, data_dir=tmp_path / "data")
    assert result.outcome == "pass"
    assert result.latest_node_outputs["child"] == {"result": "child"}


@pytest.mark.parametrize(
    ("binding", "expected_code"),
    [
        (None, "RATTISH_MISSING_INPUT"),
        ("42", "RATTISH_BINDING_TYPE_MISMATCH"),
    ],
)
def test_workflow_node_validates_child_inputs(
    tmp_path: Path, binding: str | None, expected_code: str
) -> None:
    _write(tmp_path / "child" / "workflow.rattish", _child_source())
    source = _parent_source(binding=binding or '"hello"')
    if binding is None:
        source = source.replace('  with:\n    message: "hello"\n', "")
    parent_path = _write(tmp_path / "parent.rattish", source)

    with pytest.raises(RattishCompileError) as caught:
        compile_rattish_file(parent_path, data_dir=tmp_path / "data")

    assert expected_code in {item.code for item in caught.value.diagnostics}


def test_workflow_node_allows_extra_local_but_still_requires_child_input(tmp_path: Path) -> None:
    _write(tmp_path / "child" / "workflow.rattish", _child_source())
    parent = _parent_source().replace('message: "hello"', 'unknown: "hello"')

    with pytest.raises(RattishCompileError) as caught:
        compile_rattish_file(
            _write(tmp_path / "parent.rattish", parent), data_dir=tmp_path / "data"
        )

    codes = {item.code for item in caught.value.diagnostics}
    assert codes == {"RATTISH_MISSING_INPUT"}


def test_workflow_id_resolves_through_workspace_registry(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "data"
    child = create_registered_workflow(project, "Reusable Child", registry_dir=data_dir)
    child.entrypoint.write_text(_child_source(), encoding="utf-8")
    parent = _parent_source(child="unused").replace(
        "workflow-path: unused", f"workflow-id: {child.workflow_id}"
    )

    compiled = compile_rattish_file(_write(tmp_path / "parent.rattish", parent), data_dir=data_dir)

    resolution = compiled.ir["nodes"][0]["resolutions"]["workflow"]
    assert resolution["source_kind"] == "registry"
    assert resolution["source"] == child.workflow_id


def test_registered_workflow_paths_resolve_from_project_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    data_dir = tmp_path / "data"
    parent = create_registered_workflow(project, "Parent", registry_dir=data_dir)
    _write(project / "workflows" / "child.rattish", _child_source())
    parent.entrypoint.write_text(_parent_source(child="workflows/child.rattish"), encoding="utf-8")

    compiled = compile_rattish_file(parent.entrypoint, data_dir=data_dir)

    assert Path(compiled.ir["source"]["project_root"]) == project
    assert compiled.ir["nodes"][0]["resolutions"]["workflow"]["source"] == (
        "workflows/child.rattish"
    )


def test_workflow_node_rejects_recursive_references(tmp_path: Path) -> None:
    a = _write(tmp_path / "a.rattish", _parent_source(child="b.rattish"))
    _write(tmp_path / "b.rattish", _parent_source(child="a.rattish"))

    with pytest.raises(RattishCompileError) as caught:
        compile_rattish_file(a, data_dir=tmp_path / "data")

    assert caught.value.diagnostics[0].code == "RATTISH_WORKFLOW_RECURSION"


def test_workflow_node_reports_missing_child_at_call_site(tmp_path: Path) -> None:
    parent = _write(tmp_path / "parent.rattish", _parent_source())

    with pytest.raises(RattishCompileError) as caught:
        compile_rattish_file(parent, data_dir=tmp_path / "data")

    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "RATTISH_CHILD_WORKFLOW_UNRESOLVED"
    assert diagnostic.details["source"] == "child/workflow.rattish"


def test_workflow_node_accepts_authored_symlink_outside_project(tmp_path: Path) -> None:
    outside = _write(tmp_path.parent / f"{tmp_path.name}-outside.rattish", _child_source())
    child = tmp_path / "child" / "workflow.rattish"
    child.parent.mkdir(parents=True)
    child.symlink_to(outside)
    parent = _write(tmp_path / "parent.rattish", _parent_source())

    artifact = compile_rattish_file(parent, data_dir=tmp_path / "data")
    assert artifact.ir["nodes"]


def test_workflow_node_preflights_entire_child_tree(tmp_path: Path) -> None:
    child = """Rattish: 1
Workflow:
  name: Child
  interface-version: 1
Node missing:
  type: read-file
  path: missing.txt
"""
    _write(tmp_path / "child" / "workflow.rattish", child)
    parent_source = _parent_source().replace('  with:\n    message: "hello"\n', "")
    parent = compile_rattish_file(
        _write(tmp_path / "parent.rattish", parent_source), data_dir=tmp_path / "data"
    )

    preflight = run_preflight(parent.ir, data_dir=tmp_path / "data")

    assert not preflight.ready
    diagnostic = next(
        item
        for item in preflight.diagnostics
        if item.code == "RATTISH_PREFLIGHT_CHILD_WORKFLOW_NOT_READY"
    )
    assert diagnostic.details["diagnostics"][0]["code"] == "RATTISH_PREFLIGHT_RESOURCE_MISSING"


def test_parent_cache_tracks_child_compilation_changes(tmp_path: Path) -> None:
    child_path = _write(tmp_path / "child" / "workflow.rattish", _child_source())
    parent_path = _write(tmp_path / "parent.rattish", _parent_source())
    data_dir = tmp_path / "data"

    first = compile_rattish_file(parent_path, data_dir=data_dir)
    second = compile_rattish_file(parent_path, data_dir=data_dir)
    child_path.write_text(_child_source(command="printf changed"), encoding="utf-8")
    third = compile_rattish_file(parent_path, data_dir=data_dir)

    assert not first.cache_hit
    assert second.cache_hit
    assert not third.cache_hit
    assert (
        first.ir["source"]["compilation_fingerprint"]
        != third.ir["source"]["compilation_fingerprint"]
    )


@pytest.mark.anyio
async def test_stale_parent_ir_rejects_changed_child_interface(tmp_path: Path) -> None:
    child_path = _write(tmp_path / "child" / "workflow.rattish", _child_source())
    data_dir = tmp_path / "data"
    parent = compile_rattish_file(
        _write(tmp_path / "parent.rattish", _parent_source()), data_dir=data_dir
    )
    child_path.write_text(
        _child_source().replace(
            'schema: {"type": "string"}\n      required: true', 'schema: {"type": "string"}'
        ),
        encoding="utf-8",
    )

    result = await execute_workflow(parent.ir, data_dir=data_dir)

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_WORKFLOW_INTERFACE_CHANGED"


@pytest.mark.anyio
async def test_stale_parent_ir_rejects_changed_child_implementation(tmp_path: Path) -> None:
    child_path = _write(tmp_path / "child" / "workflow.rattish", _child_source())
    data_dir = tmp_path / "data"
    parent = compile_rattish_file(
        _write(tmp_path / "parent.rattish", _parent_source()), data_dir=data_dir
    )
    child_path.write_text(_child_source(command="printf changed"), encoding="utf-8")

    result = await execute_workflow(parent.ir, data_dir=data_dir)

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_WORKFLOW_DEPENDENCY_CHANGED"


@pytest.mark.anyio
async def test_workflow_node_outputs_are_serialized_in_parent_run_artifact(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "child" / "workflow.rattish", _child_source())
    parent_path = _write(tmp_path / "parent.rattish", _parent_source())

    run = await run_rattish_file(parent_path, data_dir=tmp_path / "data")

    assert run.status == "passed"
    assert run.document["runs"][0]["output"] == {"result": "child"}
    assert run.document["latest_node_outputs"] == {"child": {"result": "child"}}


@pytest.mark.anyio
async def test_parent_cancellation_reaches_running_child_workflow(tmp_path: Path) -> None:
    _write(tmp_path / "child" / "workflow.rattish", _child_source(command="sleep 5"))
    parent = compile_rattish_file(
        _write(tmp_path / "parent.rattish", _parent_source()), data_dir=tmp_path / "data"
    )

    with anyio.move_on_after(0.05) as scope:
        await execute_workflow(parent.ir, data_dir=tmp_path / "data")

    assert scope.cancel_called


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("child_workflow_field", "command", "expected_code"),
    [
        ("", "exit 7", "RATTISH_RUNTIME_COMMAND_FAILED"),
        ("  timeout: 1ms\n", "sleep 1", "RATTISH_TIMEOUT"),
    ],
)
async def test_workflow_node_propagates_child_failure_and_timeout(
    tmp_path: Path,
    child_workflow_field: str,
    command: str,
    expected_code: str,
) -> None:
    child = _child_source(command=command).replace(
        "  interface-version: 1\n", f"  interface-version: 1\n{child_workflow_field}"
    )
    _write(tmp_path / "child" / "workflow.rattish", child)
    compiled = compile_rattish_file(
        _write(tmp_path / "parent.rattish", _parent_source()), data_dir=tmp_path / "data"
    )

    result = await execute_workflow(compiled.ir, data_dir=tmp_path / "data")

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.kind == "child_workflow"
    assert result.error.code == expected_code
