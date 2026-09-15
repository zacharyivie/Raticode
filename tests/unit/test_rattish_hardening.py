from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from gofer.rattish import runtime as rattish_runtime
from gofer.rattish.artifacts import compile_rattish_file
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.diagnostics import RattishCompileError
from gofer.rattish.ir_validation import InvalidRattishIrError, ValidatedRattishIR
from gofer.rattish.preflight import run_preflight
from gofer.rattish.provider_contracts import load_provider_contracts
from gofer.rattish.runtime import (
    HandlerResult,
    NodeHandlerRegistry,
    RuntimeErrorInfo,
    execute_node,
    load_ir,
)
from gofer.rattish.schema_compat import instance_matches_schema, schema_accepts_schema
from gofer.rattish.workflow_runtime import execute_workflow

PROJECT_ROOT = Path(__file__).parents[2]
RATTISH_ROOT = PROJECT_ROOT / "rattish"


def compile_source(source: str, project_root: Path) -> dict[str, Any]:
    compiler = RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=sorted((RATTISH_ROOT / "contracts").glob("*.json")),
    )
    providers = load_provider_contracts(
        RATTISH_ROOT / "schemas" / "provider-contract.schema.json",
        sorted((RATTISH_ROOT / "providers").glob("*.json")),
    )
    return compiler.compile(
        source,
        CompileContext("hardening", project_root, provider_contracts=providers),
    ).ir


def diagnostic_codes(source: str, project_root: Path) -> set[str]:
    with pytest.raises(RattishCompileError) as caught:
        compile_source(source, project_root)
    return {item.code for item in caught.value.diagnostics}


@pytest.mark.anyio
async def test_with_defines_locals_for_configuration_templates_and_expressions(
    tmp_path: Path,
) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: Local bindings
Node writer:
  type: agent
  provider: codex
  output-schema: {
    "type": "object",
    "properties": {
      "content": {"type": "string"},
      "coverage": {"type": "integer"},
      "people": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["content", "coverage", "people"],
    "additionalProperties": false
  }
  prompt: Write a review
  to: approve
Node approve:
  type: approval-gate
  with:
    cat: node.writer.output.content
    all-good: node.writer.output.coverage > 80
    people: node.writer.output.people
  message: What should I do? {{cat}} Approved={{all-good}}
  approvers: "{{people}}"
  needs: writer
""",
        tmp_path,
    )
    captured: dict[str, Any] = {}

    async def handler(node, context, bindings):
        _ = context
        captured["configuration"] = node["configuration"]
        captured["bindings"] = bindings.local
        return HandlerResult(
            True,
            {
                "decision": "approved",
                "approved": True,
                "decided_by": "test",
                "notes": "",
                "message": node["configuration"]["message"],
                "subject": None,
            },
        )

    result = await execute_node(
        ir,
        "approve",
        node_outputs={"writer": {"content": "cat", "coverage": 81, "people": ["owner", "release"]}},
        handlers=NodeHandlerRegistry({"raticode.approval_gate": handler}),
    )

    assert result.outcome == "success"
    assert captured["configuration"]["message"] == "What should I do? cat Approved=true"
    assert captured["configuration"]["approvers"] == ["owner", "release"]
    assert captured["bindings"] == {
        "all-good": True,
        "cat": "cat",
        "people": ["owner", "release"],
    }


@pytest.mark.anyio
async def test_matching_with_local_satisfies_required_node_configuration(
    tmp_path: Path,
) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: Dynamic input
Node writer:
  type: agent
  provider: codex
  prompt: Write a question
  to: approve
Node approve:
  type: approval-gate
  with:
    message: node.writer.output
  needs: writer
""",
        tmp_path,
    )

    async def handler(node, context, bindings):
        _ = node, context
        message = bindings.local["message"]
        return HandlerResult(
            True,
            {
                "decision": "approved",
                "approved": True,
                "decided_by": "test",
                "notes": "",
                "message": message,
                "subject": None,
            },
        )

    result = await execute_node(
        ir,
        "approve",
        node_outputs={"writer": "Ship it?"},
        handlers=NodeHandlerRegistry({"raticode.approval_gate": handler}),
    )

    assert result.outcome == "success"
    assert result.output["message"] == "Ship it?"


def test_exact_template_value_must_match_configuration_schema(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Typed template
Node writer:
  type: agent
  provider: codex
  prompt: Name one person
  to: approve
Node approve:
  type: approval-gate
  with:
    person: node.writer.output
  message: Continue?
  approvers: "{{person}}"
  needs: writer
"""

    assert "RATTISH_TEMPLATE_TYPE_MISMATCH" in diagnostic_codes(source, tmp_path)


def test_agent_default_string_output_rejects_member_selector(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Invalid agent selector
Node writer:
  type: agent
  provider: codex
  prompt: Say cat
  to: approve
Node approve:
  type: approval-gate
  with:
    cat: node.writer.output.content
  message: What should I do? {{cat}}
  needs: writer
"""

    assert "RATTISH_INVALID_JSON_SELECTOR" in diagnostic_codes(source, tmp_path)


@pytest.mark.anyio
async def test_binding_resolution_failure_returns_node_failure(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: Defensive binding failure
Node writer:
  type: agent
  provider: codex
  output-schema: {
    "type": "object",
    "properties": {"content": {"type": "string"}},
    "required": ["content"],
    "additionalProperties": false
  }
  prompt: Say cat
  to: approve
Node approve:
  type: approval-gate
  with:
    cat: node.writer.output.content
  message: What should I do? {{cat}}
  needs: writer
""",
        tmp_path,
    )

    result = await execute_node(ir, "approve", node_outputs={"writer": "cat"})

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_RUNTIME_CONFIGURATION_ERROR"


@pytest.mark.anyio
async def test_dynamic_project_path_defers_preflight_and_resolves_at_runtime(
    tmp_path: Path,
) -> None:
    (tmp_path / "input.txt").write_text("dynamic", encoding="utf-8")
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: Dynamic path
  inputs:
    source-path:
      schema: {"type": "string", "minLength": 1}
      required: true
Node read:
  type: read-file
  with:
    selected-path: input.source-path
  path: "{{selected-path}}"
""",
        tmp_path,
    )

    preflight = run_preflight(ir)
    result = await execute_node(ir, "read", workflow_inputs={"source-path": "input.txt"})

    assert preflight.ready
    assert result.outcome == "success"
    assert result.output["content"] == "dynamic"


def test_non_direct_dominating_output_does_not_require_default(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: ancestor
Node a:
  type: bash-command
  command: echo a
  to: b
Node b:
  type: bash-command
  command: echo b
  needs: a
  to: c
Node c:
  type: bash-command
  command: echo "$UPSTREAM"
  needs: b
  with:
    upstream: node.a.output.stdout
""",
        tmp_path,
    )

    binding = next(node for node in ir["nodes"] if node["id"] == "c")["bindings"][0]
    assert binding["source"]["reference"]["optional"] is False


def test_status_and_error_references_follow_channel_availability(tmp_path: Path) -> None:
    guaranteed_status = compile_source(
        """Rattish: 1
Workflow:
  name: guaranteed status
Node producer:
  type: bash-command
  command: echo producer
  to: consumer
Node consumer:
  type: bash-command
  command: echo "$STATE"
  needs: producer
  with:
    state: node.producer.status
""",
        tmp_path,
    )
    binding = next(node for node in guaranteed_status["nodes"] if node["id"] == "consumer")[
        "bindings"
    ][0]
    assert binding["source"]["reference"]["optional"] is False

    optional_status = """Rattish: 1
Workflow:
  name: optional status
Node producer:
  type: bash-command
  command: echo producer
Node consumer:
  type: bash-command
  command: echo "$STATE"
  with:
    state: node.producer.status
"""
    assert "RATTISH_BINDING_DEFAULT_REQUIRED" in diagnostic_codes(optional_status, tmp_path)

    optional_error = """Rattish: 1
Workflow:
  name: optional error
Node producer:
  type: bash-command
  command: echo producer
  to: consumer
Node consumer:
  type: bash-command
  command: echo "$FAILURE"
  needs: producer
  with:
    failure: node.producer.error
"""
    assert "RATTISH_BINDING_DEFAULT_REQUIRED" in diagnostic_codes(optional_error, tmp_path)


def test_compiler_rejects_needs_across_independent_loop_lineages(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: disjoint loops
Node loop-a:
  type: loop
  source: {"type":"count","count":1}
  to: a
Node loop-b:
  type: loop
  source: {"type":"count","count":1}
  to: b
Node a:
  type: bash-command
  command: echo a
  needs:
    - loop-a
    - b
Node b:
  type: bash-command
  command: echo b
  needs: loop-b
"""

    assert "RATTISH_INCOMPATIBLE_ACTIVATION_LINEAGE" in diagnostic_codes(source, tmp_path)


@pytest.mark.anyio
async def test_runtime_does_not_read_outputs_from_another_loop_lineage(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: isolated values
Node loop-a:
  type: loop
  source: {"type":"count","count":1}
  to: delay
Node loop-b:
  type: loop
  source: {"type":"count","count":1}
  to: producer
Node delay:
  type: bash-command
  command: sleep 0.1
  needs: loop-a
  to: consumer
Node producer:
  type: bash-command
  command: echo wrong-lineage
  needs: loop-b
Node consumer:
  type: bash-command
  command: printf %s "$EXTERNAL"
  needs: delay
  with:
    external:
      from: node.producer.output.stdout
      default: default-value
"""

    result = await execute_workflow(compile_source(source, tmp_path))

    assert result.outcome == "pass"
    assert result.latest_node_outputs["consumer"]["stdout"] == "default-value"


@pytest.mark.anyio
async def test_concurrent_loop_iterations_keep_separate_values(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: concurrent iteration values
Node repeat:
  type: loop
  source: {"type":"count","count":2,"max-concurrency":2}
  to: producer
Node producer:
  type: bash-command
  command: printf %s "$INDEX"
  needs: repeat
  to: consumer
  with:
    index: node.repeat.output.index
Node consumer:
  type: bash-command
  command: printf %s "$VALUE"
  needs: producer
  with:
    value: node.producer.output.stdout
""",
        tmp_path,
    )

    result = await execute_workflow(ir)

    consumer_values = {
        record.result.output["stdout"] for record in result.runs if record.node_id == "consumer"
    }
    assert result.outcome == "pass"
    assert consumer_values == {"0", "1"}


@pytest.mark.anyio
async def test_nested_loop_iterations_inherit_ordered_parent_values(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: nested iteration values
Node outer:
  type: loop
  source: {"type":"count","count":2,"max-concurrency":2}
  to: inner
Node inner:
  type: loop
  source: {"type":"count","count":2,"max-concurrency":2}
  needs: outer
  to: capture
Node capture:
  type: bash-command
  command: printf '%s:%s' "$OUTER_INDEX" "$INNER_INDEX"
  needs: inner
  with:
    outer-index: node.outer.output.index
    inner-index: node.inner.output.index
""",
        tmp_path,
    )

    result = await execute_workflow(ir)

    captured = {
        record.result.output["stdout"] for record in result.runs if record.node_id == "capture"
    }
    assert result.outcome == "pass"
    assert captured == {"0:0", "0:1", "1:0", "1:1"}


@pytest.mark.anyio
async def test_cycle_routes_after_an_allowed_failure(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: allowed failure cycle
Node retry:
  type: bash-command
  command: retry
  allow-fail: true
  to: inspect
Node inspect:
  type: bash-command
  command: inspect
  needs: retry
  to:
    - retry when node.retry.status == "failure"
""",
        tmp_path,
    )
    calls = 0

    async def handler(node, context, bindings):
        nonlocal calls
        _ = context, bindings
        if node["id"] == "retry":
            calls += 1
            if calls == 1:
                return HandlerResult(
                    False,
                    {"stdout": "", "stderr": "", "exit_code": 1},
                    RuntimeErrorInfo("command", "TEST_RETRY", "retry once"),
                )
        return HandlerResult(
            True,
            {"stdout": node["id"], "stderr": "", "exit_code": 0},
        )

    result = await execute_workflow(
        ir,
        handlers=NodeHandlerRegistry({"raticode.bash_command": handler}),
    )

    assert result.outcome == "pass"
    assert calls == 2


@pytest.mark.anyio
async def test_trigger_and_environment_secret_references(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DEPLOY_TOKEN", "private")
    source = """Rattish: 1
Workflow:
  name: runtime context
Node inspect:
  type: bash-command
  command: printf '%s|%s' "$TOKEN" "$EVENTS"
  with:
    token: secret["DEPLOY_TOKEN"]
    events: trigger.events
"""
    ir = compile_source(source, tmp_path)

    assert run_preflight(ir, data_dir=tmp_path / "data").ready
    result = await execute_workflow(ir, trigger_events=[{"kind": "modified"}])

    assert result.outcome == "pass"
    assert result.latest_node_outputs["inspect"]["stdout"] == ('[REDACTED]|[{"kind":"modified"}]')
    assert "private" not in json.dumps(ir)


def test_removed_reference_aliases_report_a_semantic_error(tmp_path: Path) -> None:
    for reference in ("workflow.value", "loop.item"):
        source = f"""Rattish: 1
Workflow:
  name: removed root
Node inspect:
  type: bash-command
  command: echo value
  with:
    value:
      from: {reference}
      default: none
"""
        assert "RATTISH_REFERENCE_ROOT_REMOVED" in diagnostic_codes(source, tmp_path)


def test_nested_contract_defaults_are_materialized(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: retry defaults
Node request:
  type: http-request
  url: https://example.test
  retry: {"attempts": 2}
""",
        tmp_path,
    )

    assert ir["nodes"][0]["configuration"]["retry"] == {
        "attempts": 2,
        "backoff": "0s",
        "retry_on_statuses": [],
    }


def test_schema_containment_checks_additional_property_schemas() -> None:
    assert not schema_accepts_schema(
        {"type": "object", "additionalProperties": {"type": "string"}},
        {"type": "object", "additionalProperties": {"type": "integer"}},
    )


@pytest.mark.parametrize(
    ("destination", "source"),
    [
        ({"type": "number"}, {"type": "integer", "minimum": -2, "maximum": 4}),
        (
            {"type": "number", "minimum": 0, "maximum": 10},
            {"type": "integer", "minimum": 1, "maximum": 9},
        ),
        (
            {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 10},
            {"type": "integer", "minimum": 1, "maximum": 9},
        ),
        (
            {"type": "array", "items": {"type": "number"}, "maxItems": 3},
            {"type": "array", "items": {"type": "integer"}, "maxItems": 2},
        ),
        (
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"name": {"type": "string", "minLength": 1}},
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        (
            {"anyOf": [{"type": "string"}, {"type": "null"}]},
            {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
        ),
        ({"enum": ["a", "b"]}, {"const": "a"}),
    ],
)
def test_schema_containment_proofs_accept_generated_source_values(
    destination: dict[str, Any], source: dict[str, Any]
) -> None:
    assert schema_accepts_schema(destination, source)
    values = _generated_schema_values(source)
    assert values
    for value in values:
        assert instance_matches_schema(source, value)
        assert Draft202012Validator(destination).is_valid(value)


def _generated_schema_values(schema: dict[str, Any]) -> list[Any]:
    if "const" in schema:
        return [schema["const"]]
    if isinstance(schema.get("enum"), list):
        return list(schema["enum"])
    if isinstance(schema.get("anyOf"), list):
        return [
            value
            for branch in schema["anyOf"]
            if isinstance(branch, dict)
            for value in _generated_schema_values(branch)
        ]
    schema_type = schema.get("type")
    if schema_type == "null":
        return [None]
    if schema_type == "boolean":
        return [False, True]
    if schema_type in {"integer", "number"}:
        lower = int(schema.get("minimum", -1))
        upper = int(schema.get("maximum", lower + 2))
        midpoint = lower + (upper - lower) // 2
        return sorted({lower, midpoint, upper})
    if schema_type == "string":
        minimum = int(schema.get("minLength", 0))
        maximum = int(schema.get("maxLength", max(minimum, 3)))
        return ["x" * length for length in sorted({minimum, maximum})]
    if schema_type == "array":
        item_schema = schema.get("items", {})
        item_values = _generated_schema_values(item_schema) if isinstance(item_schema, dict) else []
        minimum = int(schema.get("minItems", 0))
        maximum = min(int(schema.get("maxItems", max(minimum, 2))), 2)
        item = item_values[0] if item_values else None
        return [[item for _ in range(length)] for length in sorted({minimum, maximum})]
    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        value = {
            name: _generated_schema_values(properties[name])[0]
            for name in required
            if isinstance(properties.get(name), dict)
        }
        return [value]
    return []


@pytest.mark.parametrize(
    ("destination", "source"),
    [
        ({"type": "integer"}, {"type": "number"}),
        ({"type": "number", "exclusiveMinimum": 0}, {"type": "number", "minimum": 0}),
        ({"type": "string", "minLength": 2}, {"type": "string", "minLength": 1}),
        (
            {"type": "object", "additionalProperties": False},
            {"type": "object", "additionalProperties": True},
        ),
        (
            {"type": "array", "items": {"type": "string"}},
            {"type": "array", "items": {"type": "integer"}},
        ),
    ],
)
def test_schema_containment_rejects_unproven_or_broader_sources(
    destination: dict[str, Any], source: dict[str, Any]
) -> None:
    assert not schema_accepts_schema(destination, source)


@pytest.mark.anyio
async def test_finish_fail_applies_to_loop_nodes(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: terminal loop
Node repeat:
  type: loop
  source: {"type":"count","count":0}
  finish: fail
""",
        tmp_path,
    )

    result = await execute_workflow(ir)

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_FINISH_FAIL"


def test_prompt_placeholders_must_name_bound_inputs(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: prompt validation
Node review:
  type: agent
  provider: codex
  prompt: Review {{node.other.output}}
"""

    assert "RATTISH_PROMPT_TEMPLATE_INVALID" in diagnostic_codes(source, tmp_path)


def test_prompt_selector_must_exist_in_known_binding_schema(tmp_path: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: prompt selector
Node review:
  type: prompt-file
  output-path: rendered.md
  template: "Review {{ data.missing }}"
  with:
    data: {"known":"value"}
"""

    assert "RATTISH_PROMPT_TEMPLATE_INVALID" in diagnostic_codes(source, tmp_path)


def test_prompt_path_placeholders_are_checked_during_preflight(tmp_path: Path) -> None:
    (tmp_path / "prompt.md").write_text("Review {{missing}}", encoding="utf-8")
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: prompt path
Node review:
  type: agent
  provider: codex
  prompt-path: prompt.md
""",
        tmp_path,
    )

    result = run_preflight(ir, data_dir=tmp_path / "data", subscriptions={})

    assert "RATTISH_PREFLIGHT_PROMPT_TEMPLATE_INVALID" in {item.code for item in result.diagnostics}


@pytest.mark.anyio
async def test_prompt_file_renders_structured_selectors_and_literal_delimiters(
    tmp_path: Path,
) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: prompt rendering
Node render:
  type: prompt-file
  output-path: rendered.md
  template: |
    {{ data["CaseKey"] }} {{{{literal}}}}
  with:
    data: {"CaseKey":"value"}
""",
        tmp_path,
    )

    result = await execute_workflow(ir)

    assert result.outcome == "pass"
    assert (tmp_path / "rendered.md").read_text(encoding="utf-8") == "value {{literal}}\n"


@pytest.mark.anyio
async def test_break_applies_control_before_finish_fail(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: terminal break
  max-runs: 5
Node repeat:
  type: loop
  source: {"type":"infinite"}
  to: stop
Node stop:
  type: break
  loop: repeat
  needs: repeat
  finish: fail
""",
        tmp_path,
    )

    result = await execute_workflow(ir)

    assert result.outcome == "failure"
    assert result.error is not None
    assert result.error.code == "RATTISH_FINISH_FAIL"


def test_paths_and_registered_entrypoints_are_portable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    workflow_dir = project / ".raticode" / "portable"
    workflow_dir.mkdir(parents=True)
    source_path = workflow_dir / "workflow.rattish"
    source_path.write_text(
        """Rattish: 1
Workflow:
  name: portable
Node read:
  type: read-file
  path: ./input.txt
"""
    )

    artifact = compile_rattish_file(
        source_path,
        data_dir=tmp_path / "data",
        project_root=project,
    )

    assert artifact.ir["source"]["entrypoint"] == ".raticode/portable/workflow.rattish"
    assert artifact.ir["nodes"][0]["configuration"]["path"] == "input.txt"
    assert artifact.source_path == source_path


@pytest.mark.parametrize("authored_path", ["../outside.txt", "/tmp/outside.txt", "~/x", "a\\b"])
def test_compiler_accepts_authored_filesystem_paths(authored_path: str, tmp_path: Path) -> None:
    source = f"""Rattish: 1
Workflow:
  name: invalid path
Node read:
  type: read-file
  path: {json.dumps(authored_path)}
"""

    assert (
        compile_source(source, tmp_path)["nodes"][0]["configuration"]["path"]
        == Path(authored_path).as_posix()
    )


@pytest.mark.anyio
async def test_runtime_rejects_unvalidated_ir(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: boundary
Node run:
  type: bash-command
  command: echo ok
""",
        tmp_path,
    )

    with pytest.raises(InvalidRattishIrError):
        await execute_workflow(copy.deepcopy(dict(ir)))


def test_validated_ir_cannot_be_constructed_or_mutated(tmp_path: Path) -> None:
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: immutable ir
Node run:
  type: bash-command
  command: echo ok
""",
        tmp_path,
    )

    with pytest.raises(InvalidRattishIrError):
        ValidatedRattishIR(dict(ir))
    with pytest.raises(TypeError, match="immutable"):
        ir["workflow"]["name"] = "changed"


def test_ir_loader_requires_complete_contract_configuration(tmp_path: Path) -> None:
    compiler = RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=sorted((RATTISH_ROOT / "contracts").glob("*.json")),
    )
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: complete configuration
Node request:
  type: http-request
  url: https://example.test
""",
        tmp_path,
    )
    document = json.loads(json.dumps(ir))
    document["nodes"][0]["configuration"]["retry"].pop("backoff")
    schema = json.loads((RATTISH_ROOT / "schemas" / "ir.schema.json").read_text())

    with pytest.raises(InvalidRattishIrError, match="nested materialized field 'backoff'"):
        load_ir(
            document,
            schema,
            {contract.node_type: contract.document for contract in compiler.contracts},
        )


def test_ir_loader_requires_matching_provider_contracts(tmp_path: Path) -> None:
    compiler = RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=sorted((RATTISH_ROOT / "contracts").glob("*.json")),
    )
    providers = load_provider_contracts(
        RATTISH_ROOT / "schemas" / "provider-contract.schema.json",
        sorted((RATTISH_ROOT / "providers").glob("*.json")),
    )
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: provider dependency
Node review:
  type: agent
  provider: codex
""",
        tmp_path,
    )
    document = json.loads(json.dumps(ir))
    schema = json.loads((RATTISH_ROOT / "schemas" / "ir.schema.json").read_text())
    contracts = {contract.node_type: contract.document for contract in compiler.contracts}

    with pytest.raises(InvalidRattishIrError, match="installed provider contracts"):
        load_ir(document, schema, contracts)

    loaded = load_ir(
        document,
        schema,
        contracts,
        providers,
    )
    assert loaded["nodes"][0]["resolutions"]["provider"]["provider_id"] == "codex"


@pytest.mark.anyio
async def test_incremental_vectorization_reuses_unchanged_file_chunks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    source_file = docs / "guide.txt"
    source_file.write_text("Rattish indexing", encoding="utf-8")
    ir = compile_source(
        """Rattish: 1
Workflow:
  name: incremental vectors
Node index:
  type: local-vectorize
  source-path: docs
  index-path: cache/index.json
""",
        tmp_path,
    )
    original_read = rattish_runtime.read_text_limited
    source_reads: list[Path] = []

    def recording_read(path: Path, **kwargs: Any) -> str:
        if path == source_file:
            source_reads.append(path)
        return original_read(path, **kwargs)

    monkeypatch.setattr(rattish_runtime, "read_text_limited", recording_read)

    first = await execute_node(ir, "index", data_dir=tmp_path / "data")
    source_reads.clear()
    second = await execute_node(ir, "index", data_dir=tmp_path / "data")

    assert first.outcome == "success"
    assert second.outcome == "success"
    assert second.output["status"] == "current"
    assert source_reads == []


@pytest.mark.anyio
async def test_vector_modes_update_remove_validate_and_rebuild(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    first_path = docs / "first.txt"
    second_path = docs / "second.txt"
    first_path.write_text("alpha", encoding="utf-8")

    def vector_ir(mode: str) -> dict[str, Any]:
        return compile_source(
            f"""Rattish: 1
Workflow:
  name: vector modes
Node index:
  type: local-vectorize
  source-path: docs
  index-path: cache/index.json
  mode: {mode}
""",
            tmp_path,
        )

    index_path = tmp_path / "cache" / "index.json"
    initial = await execute_node(vector_ir("incremental"), "index")
    assert initial.output["status"] == "updated"

    first_path.write_text("alpha changed", encoding="utf-8")
    second_path.write_text("beta", encoding="utf-8")
    updated = await execute_node(vector_ir("incremental"), "index")
    updated_document = json.loads(index_path.read_text(encoding="utf-8"))
    assert updated.output["file_count"] == 2
    assert {entry["text"] for entry in updated_document["entries"]} == {
        "alpha changed",
        "beta",
    }

    first_path.unlink()
    removed = await execute_node(vector_ir("incremental"), "index")
    removed_document = json.loads(index_path.read_text(encoding="utf-8"))
    assert removed.output["file_count"] == 1
    assert {Path(entry["path"]).name for entry in removed_document["entries"]} == {"second.txt"}

    second_path.write_text("beta changed", encoding="utf-8")
    before_validate = index_path.read_bytes()
    validated = await execute_node(vector_ir("validate"), "index")
    assert validated.output["status"] == "stale"
    assert index_path.read_bytes() == before_validate

    original_read = rattish_runtime.read_text_limited
    full_reads: list[Path] = []

    def recording_read(path: Path, **kwargs: Any) -> str:
        if path == second_path:
            full_reads.append(path)
        return original_read(path, **kwargs)

    monkeypatch.setattr(rattish_runtime, "read_text_limited", recording_read)
    rebuilt = await execute_node(vector_ir("full"), "index")
    assert rebuilt.output["status"] == "updated"
    assert full_reads == [second_path]
    assert "beta changed" in index_path.read_text(encoding="utf-8")
