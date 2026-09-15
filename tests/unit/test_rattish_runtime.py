from __future__ import annotations

import json
from pathlib import Path

import pytest

from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.preflight import PreflightRegistry, run_preflight
from gofer.rattish.runtime import (
    HandlerResult,
    InvalidRattishIrError,
    NodeHandlerRegistry,
    execute_bash_node,
    execute_node,
    load_ir,
)

PROJECT_ROOT = Path(__file__).parents[2]
RATTISH_ROOT = PROJECT_ROOT / "rattish"


def compiler() -> RattishCompiler:
    return RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[RATTISH_ROOT / "contracts" / "bash-command.json"],
    )


def read_file_compiler() -> RattishCompiler:
    return RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[RATTISH_ROOT / "contracts" / "read-file.json"],
    )


def script_compiler() -> RattishCompiler:
    return RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[
            RATTISH_ROOT / "contracts" / "python-script.json",
            RATTISH_ROOT / "contracts" / "shell-script.json",
        ],
    )


def filesystem_compiler() -> RattishCompiler:
    return RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[
            RATTISH_ROOT / "contracts" / f"{name}.json"
            for name in ("write-file", "copy-file", "move-file", "delete-file")
        ],
    )


@pytest.mark.anyio
async def test_source_to_ir_to_bash_execution_honors_binding_precedence(
    tmp_path: Path,
) -> None:
    source = """Rattish: 1

Workflow:
  name: Runtime slice

Node show-mode:
  type: bash-command
  command: |
    printf '%s' \"$BUILD_MODE\"
  env:
    \"BUILD_MODE\": local
  with:
    build-mode: release
"""
    rattish_compiler = compiler()
    result = rattish_compiler.compile(
        source,
        CompileContext("runtime-slice", tmp_path),
    )
    schema = json.loads((RATTISH_ROOT / "schemas" / "ir.schema.json").read_text(encoding="utf-8"))

    ir = load_ir(
        result.ir,
        schema,
        {contract.node_type: contract.document for contract in rattish_compiler.contracts},
    )
    execution = await execute_bash_node(ir, "show-mode")

    assert execution.outcome == "success"
    assert execution.output == {"stdout": "release", "stderr": "", "exit_code": 0}


@pytest.mark.anyio
async def test_compiled_route_need_and_output_reference_cross_runtime_boundary(
    tmp_path: Path,
) -> None:
    source = """Rattish: 1

Workflow:
  name: Two node slice

Node produce:
  type: bash-command
  command: printf ready
  to: consume

Node consume:
  type: bash-command
  command: cat
  needs: produce
  with:
    stdin: node.produce.output.stdout
"""
    compiled = compiler().compile(source, CompileContext("two-node-slice", tmp_path))

    produce = await execute_bash_node(compiled.ir, "produce")
    consume = await execute_bash_node(
        compiled.ir,
        "consume",
        node_outputs={"produce": produce.output},
    )

    nodes = {node["id"]: node for node in compiled.ir["nodes"]}
    assert nodes["produce"]["routes"][0]["target"] == "consume"
    assert nodes["consume"]["readiness"]["needs"] == ["produce"]
    assert produce.outcome == "success"
    assert consume.outcome == "success"
    assert consume.output["stdout"] == "ready"


@pytest.mark.anyio
async def test_declared_workflow_input_crosses_compiler_and_runtime_boundary(
    tmp_path: Path,
) -> None:
    source = """Rattish: 1

Workflow:
  name: Workflow input slice
  inputs:
    message:
      schema: {"type": "string"}
      required: true

Node consume:
  type: bash-command
  command: cat
  with:
    stdin: input.message
"""
    compiled = compiler().compile(source, CompileContext("workflow-input-slice", tmp_path))

    execution = await execute_bash_node(
        compiled.ir,
        "consume",
        workflow_inputs={"message": "hello from input"},
    )

    assert compiled.ir["workflow"]["inputs"][0]["name"] == "message"
    assert execution.outcome == "success"
    assert execution.output["stdout"] == "hello from input"


def test_ir_loader_rejects_invalid_document() -> None:
    schema = json.loads((RATTISH_ROOT / "schemas" / "ir.schema.json").read_text(encoding="utf-8"))

    with pytest.raises(InvalidRattishIrError, match="Invalid Rattish IR"):
        load_ir({"ir_version": 1}, schema)


def test_ir_loader_rejects_unsupported_version_before_execution(tmp_path: Path) -> None:
    compiled = compiler().compile(
        """Rattish: 1
Workflow:
  name: Version gate
Node run:
  type: bash-command
  command: "true"
""",
        CompileContext("version-gate", tmp_path),
    )
    schema = json.loads((RATTISH_ROOT / "schemas" / "ir.schema.json").read_text(encoding="utf-8"))
    document = json.loads(json.dumps(compiled.ir))
    document["ir_version"] = 2

    with pytest.raises(InvalidRattishIrError, match="Unsupported Rattish IR version 2"):
        load_ir(document, schema)


def test_ir_loader_rejects_semantically_invalid_route_graph(tmp_path: Path) -> None:
    compiled = compiler().compile(
        """Rattish: 1
Workflow:
  name: Invariant gate
Node first:
  type: bash-command
  command: "true"
  to: second
Node second:
  type: bash-command
  command: "true"
  needs: first
""",
        CompileContext("invariant-gate", tmp_path),
    )
    schema = json.loads((RATTISH_ROOT / "schemas" / "ir.schema.json").read_text(encoding="utf-8"))
    document = json.loads(json.dumps(compiled.ir))
    document["nodes"][0]["routes"][0]["target"] = "missing"

    with pytest.raises(InvalidRattishIrError, match="references unknown node"):
        load_ir(document, schema)


def test_ir_loader_rejects_incompatible_public_output_source_schema(tmp_path: Path) -> None:
    compiled = compiler().compile(
        """Rattish: 1
Workflow:
  name: Output invariant
  outputs:
    text:
      from: node.run.output.stdout
      schema: {"type": "string"}
Node run:
  type: bash-command
  command: "true"
""",
        CompileContext("output-invariant", tmp_path),
    )
    schema = json.loads((RATTISH_ROOT / "schemas" / "ir.schema.json").read_text(encoding="utf-8"))
    document = json.loads(json.dumps(compiled.ir))
    document["workflow"]["outputs"][0]["source"]["schema"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "integer",
    }

    with pytest.raises(InvalidRattishIrError, match="source violates its public schema"):
        load_ir(document, schema)


def read_file_source(path: str = "input.txt") -> str:
    return f"""Rattish: 1

Workflow:
  name: Read file slice

Node read:
  type: read-file
  path: {json.dumps(path)}
"""


def test_missing_read_file_compiles_but_fails_deployment_preflight(tmp_path: Path) -> None:
    compiled = read_file_compiler().compile(
        read_file_source(), CompileContext("read-file-missing", tmp_path)
    )

    preflight = run_preflight(compiled.ir)

    node = compiled.ir["nodes"][0]
    assert node["configuration"] == {
        "path": "input.txt",
        "encoding": "utf-8",
        "errors": "strict",
    }
    assert [item["id"] for item in node["preflight_checks"]] == [
        "encoding-supported",
        "path-readable",
    ]
    assert not preflight.ready
    assert [item.code for item in preflight.diagnostics] == ["RATTISH_PREFLIGHT_RESOURCE_MISSING"]


@pytest.mark.anyio
async def test_read_file_passes_preflight_and_dispatches_by_runtime_handler(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "input.txt"
    source_path.write_text("hello from Rattish", encoding="utf-8")
    compiled = read_file_compiler().compile(
        read_file_source(), CompileContext("read-file-runtime", tmp_path)
    )

    preflight = run_preflight(compiled.ir)
    execution = await execute_node(compiled.ir, "read")

    assert preflight.ready
    assert not preflight.diagnostics
    assert execution.outcome == "success"
    assert execution.error is None
    assert execution.output == {
        "content": "hello from Rattish",
        "path": str(source_path),
        "file_name": "input.txt",
        "file_stem": "input",
        "file_extension": ".txt",
        "directory": str(tmp_path),
    }


@pytest.mark.anyio
async def test_dispatcher_rejects_handler_output_that_violates_contract(tmp_path: Path) -> None:
    source_path = tmp_path / "input.txt"
    source_path.write_text("hello", encoding="utf-8")
    compiled = read_file_compiler().compile(
        read_file_source(), CompileContext("read-file-bad-handler", tmp_path)
    )

    async def bad_handler(node, context, bindings):
        _ = node, context, bindings
        return HandlerResult(True, {"content": 42})

    handlers = NodeHandlerRegistry({"raticode.read_file": bad_handler})
    execution = await execute_node(compiled.ir, "read", handlers=handlers)

    assert execution.outcome == "failure"
    assert execution.output == {}
    assert execution.error is not None
    assert execution.error.kind == "output_validation"
    assert execution.error.code == "RATTISH_RUNTIME_OUTPUT_INVALID"


def test_preflight_reports_uninstalled_check_implementation(tmp_path: Path) -> None:
    compiled = read_file_compiler().compile(
        read_file_source(), CompileContext("read-file-check-missing", tmp_path)
    )

    preflight = run_preflight(compiled.ir, registry=PreflightRegistry())

    assert not preflight.ready
    assert {item.code for item in preflight.diagnostics} == {"RATTISH_PREFLIGHT_CHECK_UNAVAILABLE"}


def test_preflight_rejects_an_uninstalled_runtime_handler(tmp_path: Path) -> None:
    compiled = compiler().compile(
        """Rattish: 1
Workflow:
  name: Missing handler
Node run:
  type: bash-command
  command: "true"
""",
        CompileContext("missing-handler", tmp_path),
    )
    document = json.loads(json.dumps(compiled.ir))
    document["nodes"][0]["runtime_handler"] = "plugin.example.missing"

    preflight = run_preflight(document)

    assert [item.code for item in preflight.diagnostics] == [
        "RATTISH_PREFLIGHT_HANDLER_UNAVAILABLE"
    ]
    assert preflight.diagnostics[0].details["handler"] == "plugin.example.missing"


def script_source(node_type: str, script_path: str) -> str:
    return f"""Rattish: 1

Workflow:
  name: Script slice

Node run:
  type: {node_type}
  script-path: {json.dumps(script_path)}
  args:
    - explicit-argument
  env:
    "BUILD_MODE": configured
  with:
    build-mode: bound
    stdin: input-text
"""


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("node_type", "script_name", "script_body"),
    [
        (
            "python-script",
            "inspect.py",
            "import os, sys\n"
            "print(f'{sys.argv[1]}|{os.environ[\"BUILD_MODE\"]}|{sys.stdin.read()}', end='')\n",
        ),
        (
            "shell-script",
            "inspect.sh",
            'printf \'%s|%s|\' "$1" "$BUILD_MODE"\ncat\n',
        ),
    ],
)
async def test_script_contracts_compile_preflight_and_execute_with_explicit_delivery(
    tmp_path: Path,
    node_type: str,
    script_name: str,
    script_body: str,
) -> None:
    script = tmp_path / script_name
    script.write_text(script_body, encoding="utf-8")
    compiled = script_compiler().compile(
        script_source(node_type, script_name),
        CompileContext(f"{node_type}-runtime", tmp_path),
    )

    preflight = run_preflight(compiled.ir)
    execution = await execute_node(compiled.ir, "run")

    node = compiled.ir["nodes"][0]
    assert preflight.ready
    assert node["configuration"] == {
        "script_path": script_name,
        "args": ["explicit-argument"],
        "env": {"BUILD_MODE": "configured"},
    }
    assert [item["id"] for item in node["preflight_checks"]] == [
        "interpreter-available",
        "script-readable",
    ]
    assert execution.outcome == "success"
    assert execution.error is None
    assert execution.output == {
        "stdout": "explicit-argument|bound|input-text",
        "stderr": "",
        "exit_code": 0,
        "script_path": str(script),
    }


def test_script_preflight_reports_missing_script_without_rejecting_compilation(
    tmp_path: Path,
) -> None:
    compiled = script_compiler().compile(
        script_source("python-script", "missing.py"),
        CompileContext("missing-script", tmp_path),
    )

    preflight = run_preflight(compiled.ir)

    assert not preflight.ready
    assert [item.code for item in preflight.diagnostics] == ["RATTISH_PREFLIGHT_RESOURCE_MISSING"]
    assert preflight.diagnostics[0].details["check"] == "script-readable"


@pytest.mark.anyio
async def test_write_file_creates_parents_and_stdin_overrides_content(tmp_path: Path) -> None:
    source = """Rattish: 1

Workflow:
  name: Write slice

Node write:
  type: write-file
  path: generated/result.txt
  content: authored
  with:
    stdin: bound
"""
    compiled = filesystem_compiler().compile(source, CompileContext("write-slice", tmp_path))

    assert run_preflight(compiled.ir).ready
    execution = await execute_node(compiled.ir, "write")

    assert execution.outcome == "success"
    assert (tmp_path / "generated" / "result.txt").read_text(encoding="utf-8") == "bound"
    assert execution.output["action"] == "created"
    assert execution.output["bytes_written"] == 5


@pytest.mark.anyio
async def test_write_file_append_ignores_overwrite_and_preserves_existing_text(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "result.txt"
    destination.write_text("first", encoding="utf-8")
    source = """Rattish: 1

Workflow:
  name: Append slice

Node write:
  type: write-file
  path: result.txt
  content: second
  append: true
  overwrite: false
"""
    compiled = filesystem_compiler().compile(source, CompileContext("append-slice", tmp_path))

    assert run_preflight(compiled.ir).ready
    execution = await execute_node(compiled.ir, "write")

    assert execution.output["action"] == "appended"
    assert destination.read_text(encoding="utf-8") == "firstsecond"


@pytest.mark.anyio
async def test_copy_move_and_delete_support_directories(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "nested.txt").write_text("payload", encoding="utf-8")
    source = """Rattish: 1

Workflow:
  name: Folder operations

Node copy:
  type: copy-file
  source-path: source
  destination-path: copied

Node move:
  type: move-file
  source-path: copied
  destination-path: moved

Node delete:
  type: delete-file
  path: moved
  use-trash: false
  recursive: true
"""
    compiled = filesystem_compiler().compile(source, CompileContext("folder-ops", tmp_path))

    copied = await execute_node(compiled.ir, "copy")
    moved = await execute_node(compiled.ir, "move")
    deleted = await execute_node(compiled.ir, "delete")

    assert copied.output["path_kind"] == "directory"
    assert moved.output["path_kind"] == "directory"
    assert deleted.output["path_kind"] == "directory"
    assert deleted.output["disposition"] == "deleted"
    assert not (tmp_path / "moved").exists()
    assert (source_dir / "nested.txt").read_text(encoding="utf-8") == "payload"


@pytest.mark.anyio
async def test_symlinks_are_copied_and_deleted_as_objects(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("keep", encoding="utf-8")
    (tmp_path / "link").symlink_to(target.name)
    source = """Rattish: 1

Workflow:
  name: Symlink operations

Node copy:
  type: copy-file
  source-path: link
  destination-path: copied-link

Node delete:
  type: delete-file
  path: copied-link
  use-trash: false
"""
    compiled = filesystem_compiler().compile(source, CompileContext("symlink-ops", tmp_path))

    copied = await execute_node(compiled.ir, "copy")
    deleted = await execute_node(compiled.ir, "delete")

    assert copied.output["path_kind"] == "symlink"
    assert deleted.output["path_kind"] == "symlink"
    assert target.read_text(encoding="utf-8") == "keep"
    assert (tmp_path / "link").is_symlink()


@pytest.mark.parametrize("authored_path", ["../outside.txt", "/tmp/outside.txt"])
def test_compiler_accepts_authored_filesystem_paths_outside_project(
    tmp_path: Path, authored_path: str
) -> None:
    source = f"""Rattish: 1

Workflow:
  name: Unsafe path

Node write:
  type: write-file
  path: {json.dumps(authored_path)}
"""

    result = filesystem_compiler().compile(source, CompileContext("authored-path", tmp_path))
    assert result.ir["nodes"][0]["configuration"]["path"] == authored_path
