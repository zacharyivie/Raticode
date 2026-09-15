from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gofer.rattish.compiler import CompileContext, ProviderContract, RattishCompiler
from gofer.rattish.diagnostics import RattishCompileError, RattishParseError
from gofer.rattish.parser import parse_rattish_recovering

PROJECT_ROOT = Path(__file__).parents[2]
RATTISH_ROOT = PROJECT_ROOT / "rattish"
SCHEMA_ROOT = RATTISH_ROOT / "schemas"
BASH_CONTRACT = RATTISH_ROOT / "contracts" / "bash-command.json"
AGENT_CONTRACT = RATTISH_ROOT / "contracts" / "agent.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def fixture_compiler(fixture: Path) -> tuple[RattishCompiler, CompileContext]:
    raw = load_json(fixture / "compile-context.json")
    compiler = RattishCompiler.from_paths(
        schema_root=SCHEMA_ROOT,
        contract_paths=[BASH_CONTRACT],
        contract_fingerprints={
            name: contract["fingerprint"] for name, contract in raw["contracts"].items()
        },
    )
    context = CompileContext(
        workflow_id=raw["workflow_id"],
        project_root=Path(raw["project_root"]),
        entrypoint=raw["entrypoint"],
        compiler_version=raw["compiler"]["version"],
    )
    return compiler, context


def default_compiler() -> RattishCompiler:
    return RattishCompiler.from_paths(
        schema_root=SCHEMA_ROOT,
        contract_paths=[BASH_CONTRACT],
    )


def provider_contract() -> ProviderContract:
    return ProviderContract(
        provider_id="codex",
        version=1,
        default_model="gpt-5.6-sol",
        default_effort="high",
        fingerprint="sha256:" + "3" * 64,
    )


def test_parser_and_compiler_match_bash_conformance_fixture() -> None:
    fixture = RATTISH_ROOT / "conformance" / "valid" / "bash-environment"
    compiler, context = fixture_compiler(fixture)

    result = compiler.compile((fixture / "workflow.rattish").read_text(), context)

    assert result.ast == load_json(fixture / "expected-ast.json")
    assert result.ir == load_json(fixture / "expected-ir.json")
    assert [item.to_json() for item in result.diagnostics] == load_json(
        fixture / "expected-diagnostics.json"
    )


def test_unresolved_route_matches_diagnostic_fixture() -> None:
    fixture = RATTISH_ROOT / "conformance" / "invalid" / "unresolved-route"

    with pytest.raises(RattishCompileError) as caught:
        default_compiler().compile(
            (fixture / "workflow.rattish").read_text(),
            CompileContext("broken-route", Path("/fixture/project")),
        )

    assert [item.to_json() for item in caught.value.diagnostics] == load_json(
        fixture / "expected-diagnostics.json"
    )


def test_plaintext_secret_warning_does_not_prevent_ir() -> None:
    fixture = RATTISH_ROOT / "conformance" / "warnings" / "plaintext-secret"

    result = default_compiler().compile(
        (fixture / "workflow.rattish").read_text(),
        CompileContext("plaintext-warning", Path("/fixture/project")),
    )

    assert [item.to_json() for item in result.diagnostics] == load_json(
        fixture / "expected-diagnostics.json"
    )
    assert result.ir["nodes"][0]["configuration"]["env"]["API_TOKEN"] == ("visible-in-source")


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (
            """Node a:
  type: bash-command
  command: \"true\"
  finish: pass
  to: b
Node b:
  type: bash-command
  command: \"true\"
""",
            "RATTISH_INVALID_TERMINAL",
        ),
        (
            """Node a:
  type: bash-command
  command: \"true\"
Node b:
  type: bash-command
  command: \"true\"
  start: true
  needs: a
""",
            "RATTISH_INVALID_START_ASSERTION",
        ),
        (
            """Node a:
  type: bash-command
  command: cat
  with:
    stdin: 42
""",
            "RATTISH_BINDING_TYPE_MISMATCH",
        ),
        (
            """Node a:
  type: bash-command
  command: \"true\"
  to:
    - b
    - b
Node b:
  type: bash-command
  command: \"true\"
""",
            "RATTISH_DUPLICATE_ROUTE",
        ),
    ],
)
def test_common_semantic_rules_reject_invalid_source(body: str, code: str) -> None:
    source = f"Rattish: 1\nWorkflow:\n  name: Invalid semantics\n{body}"

    with pytest.raises(RattishCompileError) as caught:
        default_compiler().compile(source, CompileContext("semantic-probe", Path("/tmp")))

    assert code in {diagnostic.code for diagnostic in caught.value.diagnostics}


def test_multiline_json_is_accepted_and_duplicate_keys_are_rejected() -> None:
    multiline = """Rattish: 1
Workflow:
  name: JSON
Node a:
  type: bash-command
  command: \"true\"
  with:
    payload: {
      \"key\": \"value\"
    }
"""
    result = default_compiler().compile(multiline, CompileContext("multiline-json", Path("/tmp")))
    assert result.ir["nodes"][0]["bindings"][0]["source"]["value"] == {"key": "value"}

    duplicate = multiline.replace('"key": "value"', '"key": "value",\n      "key": "again"')
    with pytest.raises(RattishParseError) as caught:
        default_compiler().compile(duplicate, CompileContext("duplicate-json", Path("/tmp")))
    assert caught.value.diagnostics[0].code == "RATTISH_INVALID_JSON"


def test_empty_expanded_binding_is_a_parse_diagnostic() -> None:
    source = """Rattish: 1
Workflow:
  name: Incomplete binding
Node approve:
  type: approval-gate
  with:
    cat:
  message: What should I do? {{cat}}
"""

    with pytest.raises(RattishParseError) as caught:
        default_compiler().compile(source, CompileContext("incomplete-binding", Path("/tmp")))

    assert caught.value.diagnostics[0].code == "RATTISH_EXPECTED_TOKEN"
    assert "requires a from field" in caught.value.diagnostics[0].message
    recovered = parse_rattish_recovering(source)
    assert [item.code for item in recovered.diagnostics] == ["RATTISH_EXPECTED_TOKEN"]
    assert recovered.ast is not None
    assert recovered.ast["nodes"] == []


def test_recovering_parser_keeps_valid_nodes_and_comment_trivia() -> None:
    source = """Rattish: 1
Workflow:
  name: Editor recovery
# retained comment
Node broken:
  type: bash-command
  command
Node valid:
  type: bash-command
  command: echo valid
"""

    result = parse_rattish_recovering(source)

    assert result.ast is not None
    assert [node["name"]["canonical"] for node in result.ast["nodes"]] == ["valid"]
    assert result.diagnostics[0].code == "RATTISH_EXPECTED_TOKEN"
    assert "Node broken" in result.invalid_regions[0].source
    assert any(token.kind == "comment" for token in result.tokens)


def test_agent_defaults_and_inline_output_schema_are_frozen_in_ir() -> None:
    compiler = RattishCompiler.from_paths(
        schema_root=SCHEMA_ROOT,
        contract_paths=[AGENT_CONTRACT],
    )
    source = """Rattish: 1
Workflow:
  name: Agent
Node review:
  type: agent
  provider: CoDeX
  output-schema: {
    "type": "object",
    "properties": {"approved": {"type": "boolean"}},
    "required": ["approved"],
    "additionalProperties": false
  }
"""
    context = CompileContext(
        "agent-contract",
        Path("/tmp"),
        provider_contracts={"codex": provider_contract()},
    )

    result = compiler.compile(source, context)
    node = result.ir["nodes"][0]

    assert node["configuration"]["provider"] == "codex"
    assert node["configuration"]["model"] == "gpt-5.6-sol"
    assert node["configuration"]["effort"] == "high"
    assert node["output"]["schema"]["properties"]["approved"] == {"type": "boolean"}
    assert node["resolutions"]["provider"]["contract_fingerprint"] == "sha256:" + "3" * 64
    assert {item["kind"] for item in result.ir["source"]["dependencies"]} == {
        "node_contract",
        "provider_contract",
    }


def test_agent_prompt_sources_are_mutually_exclusive() -> None:
    compiler = RattishCompiler.from_paths(
        schema_root=SCHEMA_ROOT,
        contract_paths=[AGENT_CONTRACT],
    )
    source = """Rattish: 1
Workflow:
  name: Agent
Node review:
  type: agent
  provider: codex
  prompt: inline
  prompt-path: prompt.md
"""

    with pytest.raises(RattishCompileError) as caught:
        compiler.compile(
            source,
            CompileContext(
                "agent-conflict",
                Path("/tmp"),
                provider_contracts={"codex": provider_contract()},
            ),
        )

    assert "RATTISH_MUTUALLY_EXCLUSIVE_FIELDS" in {
        diagnostic.code for diagnostic in caught.value.diagnostics
    }


def test_agent_prompt_path_without_inline_prompt_is_valid() -> None:
    compiler = RattishCompiler.from_paths(
        schema_root=SCHEMA_ROOT,
        contract_paths=[AGENT_CONTRACT],
    )
    source = """Rattish: 1
Workflow:
  name: Agent
Node review:
  type: agent
  provider: codex
  prompt-path: prompt.md
"""

    result = compiler.compile(
        source,
        CompileContext(
            "agent-prompt-path",
            Path("/tmp"),
            provider_contracts={"codex": provider_contract()},
        ),
    )

    assert not result.diagnostics
    assert result.ir["nodes"][0]["configuration"]["prompt"] == ""
    assert result.ir["nodes"][0]["configuration"]["prompt_path"] == "prompt.md"


def test_agent_external_output_schema_is_embedded_and_fingerprinted(tmp_path: Path) -> None:
    schema_path = tmp_path / "result.schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "array",
                "items": {"type": "string"},
            }
        ),
        encoding="utf-8",
    )
    compiler = RattishCompiler.from_paths(
        schema_root=SCHEMA_ROOT,
        contract_paths=[AGENT_CONTRACT],
    )
    source = """Rattish: 1
Workflow:
  name: Agent schema file
Node review:
  type: agent
  provider: codex
  output-schema-path: result.schema.json
"""

    result = compiler.compile(
        source,
        CompileContext(
            "agent-schema-file",
            tmp_path,
            provider_contracts={"codex": provider_contract()},
        ),
    )

    assert result.ir["nodes"][0]["output"]["schema"]["type"] == "array"
    schema_dependency = next(
        item for item in result.ir["source"]["dependencies"] if item["kind"] == "schema"
    )
    assert schema_dependency["path"] == "result.schema.json"


def test_missing_provider_contract_blocks_agent_compilation() -> None:
    compiler = RattishCompiler.from_paths(
        schema_root=SCHEMA_ROOT,
        contract_paths=[AGENT_CONTRACT],
    )
    source = """Rattish: 1
Workflow:
  name: Agent
Node review:
  type: agent
  provider: unknown-provider
"""

    with pytest.raises(RattishCompileError) as caught:
        compiler.compile(source, CompileContext("missing-provider", Path("/tmp")))

    assert "RATTISH_PROVIDER_CONTRACT_UNRESOLVED" in {
        diagnostic.code for diagnostic in caught.value.diagnostics
    }


def test_predicate_types_and_regular_expressions_are_checked() -> None:
    source = """Rattish: 1
Workflow:
  name: Predicate
Node inspect:
  type: bash-command
  command: echo inspect
  to: finish when node.inspect.output["exit_code"] matches "["
Node finish:
  type: bash-command
  command: echo finish
"""

    with pytest.raises(RattishCompileError) as caught:
        default_compiler().compile(source, CompileContext("predicate-types", Path("/tmp")))

    codes = {diagnostic.code for diagnostic in caught.value.diagnostics}
    assert "RATTISH_PREDICATE_TYPE_MISMATCH" in codes
    assert "RATTISH_INVALID_REGEX" in codes


def test_negated_status_predicates_preserve_failure_meaning() -> None:
    valid = """Rattish: 1
Workflow:
  name: Success condition
Node inspect:
  type: bash-command
  command: echo inspect
  to: finish when not failed
Node finish:
  type: bash-command
  command: echo finish
"""
    invalid = valid.replace("not failed", "not succeeded")

    result = default_compiler().compile(valid, CompileContext("negated-success", Path("/tmp")))
    inspect = next(node for node in result.ir["nodes"] if node["id"] == "inspect")
    assert inspect["routes"][0]["predicate"]["kind"] == "not"

    with pytest.raises(RattishCompileError) as caught:
        default_compiler().compile(invalid, CompileContext("negated-failure", Path("/tmp")))
    assert "RATTISH_UNREACHABLE_FAILURE_ROUTE" in {
        diagnostic.code for diagnostic in caught.value.diagnostics
    }


def test_statically_dead_requirement_cycle_is_rejected() -> None:
    source = """Rattish: 1
Workflow:
  name: Dead cycle
Node a:
  type: bash-command
  command: echo a
  needs: b
  to: b
Node b:
  type: bash-command
  command: echo b
  needs: a
  to: a
"""

    with pytest.raises(RattishCompileError) as caught:
        default_compiler().compile(source, CompileContext("dead-cycle", Path("/tmp")))

    assert "RATTISH_UNREACHABLE_REQUIREMENT" in {
        diagnostic.code for diagnostic in caught.value.diagnostics
    }


def test_workflow_public_outputs_lower_references_and_declared_schemas() -> None:
    source = """Rattish: 1
Workflow:
  name: Public interface
  interface-version: 1
  inputs:
    optional-message:
      schema: {"type": "string"}
    request:
      schema: {
        "type": "object",
        "properties": {"ID": {"type": "integer"}},
        "required": ["ID"],
        "additionalProperties": false
      }
      required: true
  outputs:
    optional-message:
      from: input.optional-message
      schema: {"type": "string"}
    request-id:
      from: input.request.ID
      schema: {"type": "integer"}
    stdout:
      from: node.build.output.stdout
      schema: {"type": "string"}
Node build:
  type: bash-command
  command: echo ready
"""

    result = default_compiler().compile(source, CompileContext("public-interface", Path("/tmp")))

    assert result.ir["workflow"]["interface_version"] == 1
    assert [output["name"] for output in result.ir["workflow"]["outputs"]] == [
        "optional-message",
        "request-id",
        "stdout",
    ]
    optional_message = result.ir["workflow"]["outputs"][0]
    assert optional_message["source"]["optional"] is True
    request = result.ir["workflow"]["outputs"][1]
    assert request["source"]["root"] == "input"
    assert request["source"]["symbol"] == "request"
    assert request["source"]["optional"] is False
    assert request["source"]["path"] == [{"kind": "member", "value": "ID", "case_sensitive": True}]
    stdout = result.ir["workflow"]["outputs"][2]
    assert stdout["source"]["symbol"] == "build"
    assert stdout["source"]["channel"] == "output"
    assert stdout["source"]["schema"]["type"] == "string"


@pytest.mark.parametrize(
    ("output_definition", "code"),
    [
        ('schema: {"type": "string"}', "RATTISH_MISSING_FIELD"),
        ("from: node.build.output.stdout", "RATTISH_MISSING_FIELD"),
        (
            'from: "not a reference"\n      schema: {"type": "string"}',
            "RATTISH_WRONG_REFERENCE_KIND",
        ),
        (
            'from: node.missing.output.stdout\n      schema: {"type": "string"}',
            "RATTISH_UNRESOLVED_REFERENCE",
        ),
        (
            'from: node.build.output.missing\n      schema: {"type": "string"}',
            "RATTISH_INVALID_JSON_SELECTOR",
        ),
        (
            'from: secret.token\n      schema: {"type": "string"}',
            "RATTISH_WRONG_REFERENCE_KIND",
        ),
        (
            'from: node.build.output.stdout\n      schema: {"type": "integer"}',
            "RATTISH_OUTPUT_TYPE_MISMATCH",
        ),
    ],
)
def test_workflow_public_output_contract_errors_are_compile_time_diagnostics(
    output_definition: str, code: str
) -> None:
    source = f"""Rattish: 1
Workflow:
  name: Invalid public interface
  outputs:
    result:
      {output_definition}
Node build:
  type: bash-command
  command: echo ready
"""

    with pytest.raises(RattishCompileError) as caught:
        default_compiler().compile(source, CompileContext("invalid-public-output", Path("/tmp")))

    assert code in {diagnostic.code for diagnostic in caught.value.diagnostics}


def test_invalid_common_field_never_publishes_ir() -> None:
    source = """Rattish: 1
Workflow:
  name: Invalid common field
Node a:
  type: bash-command
  command: echo a
  finish: maybe
"""

    with pytest.raises(RattishCompileError) as caught:
        default_compiler().compile(source, CompileContext("invalid-common", Path("/tmp")))

    assert "RATTISH_INVALID_FIELD_VALUE" in {
        diagnostic.code for diagnostic in caught.value.diagnostics
    }
