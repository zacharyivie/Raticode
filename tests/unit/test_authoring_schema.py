from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast, get_args

import pytest
from jsonschema import (  # type: ignore[import-untyped]
    Draft202012Validator,
    FormatChecker,
    ValidationError,
)
from pydantic import BaseModel
from rich.text import Text
from typer import rich_utils
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.core.agent import AgentConfig
from gofer.core.authoring import (
    AUTHORING_SCHEMA_VERSION,
    CAPABILITY_IDS,
    OPERATION_MODELS,
    REFERENCE_SUPPORT,
    authoring_contract,
)
from gofer.core.executor import WorkflowExecutor
from gofer.core.graph import GraphNode
from gofer.core.operations import CommonLlmTaskOperation, Operation, OperationType
from gofer.core.references import (
    REFERENCE_FIELD_CAPABILITIES,
    REFERENCE_NAMESPACE_CAPABILITIES,
    ReferenceNamespace,
)
from gofer.core.workflow import AgenticWorkflow
from gofer.rattish.artifacts import RattishArtifactError
from gofer.ui.chat import _load_skill_text
from tests.conftest import FakeSubscription

runner = CliRunner()


def test_full_authoring_contract_covers_runtime_operations() -> None:
    payload = authoring_contract()
    published = {item["type"] for item in payload["operations"]}

    assert published == {item.value for item in OperationType}
    assert published == set(OPERATION_MODELS)
    assert set(payload["schemas"]) == set(CAPABILITY_IDS)
    assert payload["metadata"]["schema_version"] == AUTHORING_SCHEMA_VERSION
    assert payload["metadata"]["cli_version"]
    assert payload["metadata"]["workflow_format_versions"]


def test_all_published_schemas_are_valid_draft_2020_12() -> None:
    payload = authoring_contract()

    for schema in payload["schemas"].values():
        Draft202012Validator.check_schema(schema)
    for operation in payload["operations"]:
        Draft202012Validator.check_schema(operation["schema"])


def test_operation_examples_match_schema_and_round_trip_through_toml_parser(
    tmp_path: Path,
) -> None:
    contract = authoring_contract()
    document_schema = contract["schemas"]["document"]

    for item in contract["operations"]:
        node = item["minimal_example"]
        Draft202012Validator(item["schema"]).validate(node)
        document: dict[str, Any] = {
            "workflow": {"id": "schema-round-trip", "name": "Schema round trip"},
            "nodes": [node],
        }
        Draft202012Validator(document_schema).validate(document)
        workflow = AgenticWorkflow.from_dict(document)
        workflow.validate()
        assert workflow.graph._nodes[node["id"]].operation.type.value == item["type"]
        toml_path = tmp_path / f"{item['type']}.toml"
        workflow.to_file(toml_path)
        reparsed = AgenticWorkflow.from_file(toml_path)
        reparsed.validate(toml_path)
        assert reparsed.graph._nodes[node["id"]].operation.type.value == item["type"]


def test_representative_examples_parse_and_validate_as_complete_documents() -> None:
    contract = authoring_contract()
    schema = contract["schemas"]["document"]

    for example in contract["examples"]:
        document = example["workflow"]
        Draft202012Validator(schema).validate(document)
        workflow = AgenticWorkflow.from_dict(document)
        workflow.validate()
        assert workflow.config.id == document["workflow"]["id"]


def test_authoring_schemas_use_flattened_toml_shapes() -> None:
    schemas = authoring_contract()["schemas"]
    node_variant = schemas["node"]["oneOf"][0]

    assert "id" in node_variant["properties"]
    assert "node_id" not in node_variant["properties"]
    assert "operation" not in node_variant["properties"]
    assert set(schemas["edge"]["required"]) == {"from", "to"}
    assert "from_node" not in schemas["edge"]["properties"]
    assert "agent_id" not in schemas["agent"]["properties"]
    assert schemas["agent"]["x-gofer-container-key-field"] == "agent_id"


def test_runtime_constraints_and_nested_field_ids_are_published() -> None:
    contract = authoring_contract()
    workflow_id = contract["schemas"]["workflow"]["properties"]["id"]
    loop_source = next(item for item in contract["operations"] if item["type"] == "loop")["schema"][
        "properties"
    ]["source"]

    assert workflow_id["pattern"] == "[a-z0-9][a-z0-9-]{0,127}"
    assert loop_source["x-gofer-field-id"] == "operation.loop.source"
    assert all(
        variant["properties"]["type"]["x-gofer-field-id"] == "operation.loop.source.type"
        for variant in loop_source["oneOf"]
    )
    count_variant = next(
        variant for variant in loop_source["oneOf"] if "count" in variant["properties"]
    )
    assert count_variant["properties"]["count"]["x-gofer-field-id"] == "operation.loop.source.count"


def test_runtime_value_schemas_reject_wrong_literals_and_accept_exact_references() -> None:
    operations = {item["type"]: item for item in authoring_contract()["operations"]}
    cases = (
        ("write_file", "create_dirs"),
        ("local_search", "top_k"),
        ("agent", "memory"),
        ("notification", "channel"),
    )

    for operation_type, field_name in cases:
        operation = operations[operation_type]
        invalid = {**operation["minimal_example"], field_name: {"not": "a scalar"}}
        with pytest.raises(ValidationError):
            Draft202012Validator(operation["schema"]).validate(invalid)
        referenced = {
            **operation["minimal_example"],
            field_name: f"{{{{inputs.{field_name}}}}}",
        }
        Draft202012Validator(operation["schema"]).validate(referenced)

    write_file = operations["write_file"]
    runtime_fan_out = {
        **write_file["minimal_example"],
        "for_each": "{{inputs.items}}",
        "max_concurrency": "{{inputs.workers}}",
        "fail_fast": "{{inputs.stop_early}}",
    }
    Draft202012Validator(write_file["schema"]).validate(runtime_fan_out)

    notification = operations["notification"]
    Draft202012Validator(notification["schema"]).validate(
        {**notification["minimal_example"], "headers": "{{inputs.headers}}"}
    )


def test_canvas_and_parameter_regex_constraints_match_runtime_validation() -> None:
    schemas = authoring_contract()["schemas"]
    workflow_schema = schemas["workflow"]
    parameter_schema = schemas["parameter"]
    invalid_canvas = {
        "id": "canvas-constraints",
        "name": "Canvas constraints",
        "metadata": {
            "canvas": {
                "groups": [
                    {
                        "id": "bad id",
                        "label": "Bad",
                        "color": "red",
                        "width": 20,
                        "height": 20,
                        "opacity": 2,
                    }
                ]
            }
        },
    }

    with pytest.raises(ValidationError):
        Draft202012Validator(workflow_schema).validate(invalid_canvas)
    with pytest.raises(ValidationError):
        Draft202012Validator(
            parameter_schema,
            format_checker=FormatChecker(),
        ).validate({"pattern": "[invalid"})
    with pytest.raises(ValueError):
        AgenticWorkflow.from_dict({"workflow": invalid_canvas})


def test_document_schema_allows_parser_default_for_omitted_nodes() -> None:
    document = {"workflow": {"id": "empty-workflow", "name": "Empty workflow"}}
    schema = authoring_contract()["schemas"]["document"]

    Draft202012Validator(schema).validate(document)
    workflow = AgenticWorkflow.from_dict(document)
    workflow.validate()
    assert not workflow.graph.nodes_in_order()


def test_agent_contract_reports_all_executor_deprecations() -> None:
    agent = authoring_contract(operation="agent")["operation"]
    deprecated_fields = {
        item["field_id"]
        for item in agent["deprecations"]
        if item["field_id"].startswith("operation.agent.")
    }

    assert deprecated_fields == {
        "operation.agent.dynamic_count",
        "operation.agent.fan_source",
    }


def test_legacy_node_on_failure_is_disclosed_as_parser_compatibility() -> None:
    contract = authoring_contract()
    bash = next(item for item in contract["operations"] if item["type"] == "bash_command")
    field = bash["schema"]["properties"]["on_failure"]

    assert field["deprecated"] is True
    assert field["x-gofer-replacement"] == "edges.*.condition"
    assert contract["deprecations"] == [
        {
            "field_id": "node.on_failure",
            "replacement": "edges.*.condition",
            "message": "Use explicit conditional edges instead.",
        }
    ]
    with pytest.warns(DeprecationWarning, match="conditional edges"):
        workflow = AgenticWorkflow.from_dict(
            {
                "workflow": {"id": "legacy-failure", "name": "Legacy failure"},
                "nodes": [
                    {
                        "id": "command",
                        "type": "bash_command",
                        "command": "true",
                        "on_failure": "halt",
                    }
                ],
            }
        )
    workflow.validate()


def test_operation_registry_is_derived_from_discriminated_union() -> None:
    operation_union = get_args(Operation)[0]
    runtime_models = {
        model
        for model in get_args(operation_union)
        if isinstance(model, type) and issubclass(model, BaseModel)
    }

    assert set(OPERATION_MODELS.values()) == runtime_models


def test_reference_contract_is_the_runtime_registry_without_drift() -> None:
    assert REFERENCE_SUPPORT["namespaces"] == REFERENCE_NAMESPACE_CAPABILITIES
    assert {item["name"] for item in REFERENCE_SUPPORT["namespaces"]} >= {
        item.value for item in ReferenceNamespace
    }
    assert {item["field_pattern"] for item in REFERENCE_SUPPORT["field_rules"]} == set(
        REFERENCE_FIELD_CAPABILITIES
    )
    assert "item" in {item["name"] for item in REFERENCE_SUPPORT["namespaces"]}
    assert "items" in {item["name"] for item in REFERENCE_SUPPORT["namespaces"]}
    assert "nodes[type=prompt_file].template" in REFERENCE_FIELD_CAPABILITIES
    assert REFERENCE_FIELD_CAPABILITIES["nodes[type=agent].input_mapping.*"] == (
        "literal",
        "exact_typed_reference",
        "interpolation",
    )
    assert REFERENCE_FIELD_CAPABILITIES["nodes[type=common_llm_task].input_mapping.*"] == (
        "literal",
        "exact_typed_reference",
        "interpolation",
    )
    assert "nodes[type=write_file].create_dirs" in REFERENCE_FIELD_CAPABILITIES
    assert "nodes[type=local_search].top_k" in REFERENCE_FIELD_CAPABILITIES
    assert "nodes[type=notification].headers" in REFERENCE_FIELD_CAPABILITIES
    assert REFERENCE_FIELD_CAPABILITIES["nodes.*.max_concurrency"] == (
        "literal",
        "exact_typed_reference",
    )
    assert REFERENCE_FIELD_CAPABILITIES["nodes.*.fail_fast"] == (
        "literal",
        "exact_typed_reference",
    )
    for whole_container in (
        "nodes[type=bash_command].env",
        "nodes[type=agent].input_mapping",
        "nodes[type=common_llm_task].input_mapping",
        "nodes[type=http_request].json",
        "nodes[type=notification].payload",
        "nodes[type=workflow].input_bindings",
        "nodes[type=subflow].parameter_bindings",
        "nodes[type=subflow].input_bindings",
    ):
        assert REFERENCE_FIELD_CAPABILITIES[whole_container] == (
            "literal",
            "exact_typed_reference",
        )
    recursively_resolved_string_and_path_fields = {
        "nodes[type=pass].message",
        "nodes[type=fail].message",
        "nodes[type=break].message",
        "nodes[type=read_file].path",
        "nodes[type=read_file].encoding",
        "nodes[type=read_file].errors",
        "nodes[type=write_file].path",
        "nodes[type=write_file].content",
        "nodes[type=write_file].encoding",
        "nodes[type=copy_file].source_path",
        "nodes[type=copy_file].destination_path",
        "nodes[type=move_file].source_path",
        "nodes[type=move_file].destination_path",
        "nodes[type=delete_file].path",
        "nodes[type=file].path",
        "nodes[type=folder].path",
        "nodes[type=open_resource].target",
        "nodes[type=prompt_file].encoding",
        "nodes[type=local_vectorize].source_path",
        "nodes[type=local_vectorize].index_path",
        "nodes[type=local_vectorize].glob",
        "nodes[type=local_vectorize].encoding",
        "nodes[type=local_vectorize].embedding_strategy",
        "nodes[type=local_vectorize].search_strategy",
        "nodes[type=local_search].index_path",
        "nodes[type=local_search].query",
        "nodes[type=local_search].embedding_strategy",
        "nodes[type=local_search].search_strategy",
        "nodes[type=workflow].workflow_id",
        "nodes[type=subflow].component_id",
        "nodes[type=subflow].version",
        "nodes[type=subflow].source_path",
        "nodes[type=agent].agent_id",
        "nodes[type=agent].profile",
        "nodes[type=agent].model",
        "nodes[type=agent].effort",
        "nodes[type=agent].skill_name",
        "nodes[type=common_llm_task].agent_id",
        "nodes[type=common_llm_task].profile",
        "nodes[type=common_llm_task].model",
        "nodes[type=common_llm_task].effort",
    }
    assert recursively_resolved_string_and_path_fields <= set(REFERENCE_FIELD_CAPABILITIES)
    assert all(
        "interpolation" in REFERENCE_FIELD_CAPABILITIES[field]
        for field in recursively_resolved_string_and_path_fields
    )
    assert not any(
        item["field_pattern"] == "nodes.*.<string-or-path-field>"
        for item in REFERENCE_SUPPORT["field_rules"]
    )


@pytest.mark.anyio
async def test_runtime_interpolation_is_gated_by_reference_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = AgenticWorkflow.from_dict(
        {
            "workflow": {"id": "reference-gate", "name": "Reference gate"},
            "nodes": [
                {
                    "id": "prompt",
                    "type": "prompt_file",
                    "output_path": str(tmp_path / "prompt.md"),
                    "template": "Hello {{name}}",
                    "variables": {"name": "Gofer"},
                }
            ],
        }
    )
    monkeypatch.delitem(
        REFERENCE_FIELD_CAPABILITIES,
        "nodes[type=prompt_file].template",
    )

    result = await WorkflowExecutor(workflow, {}, log_base_dir=tmp_path / "logs").run()

    assert not result.success
    assert "Missing reference capability" in result.node_outputs["prompt"].output


@pytest.mark.anyio
async def test_recursive_operation_fields_are_gated_by_reference_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("resolved", encoding="utf-8")
    workflow = AgenticWorkflow.from_dict(
        {
            "workflow": {
                "id": "recursive-reference-gate",
                "name": "Recursive reference gate",
                "inputs": {"path": {"type": "path", "required": True}},
            },
            "nodes": [
                {
                    "id": "read",
                    "type": "read_file",
                    "path": "{{inputs.path}}",
                }
            ],
        }
    )
    monkeypatch.delitem(
        REFERENCE_FIELD_CAPABILITIES,
        "nodes[type=read_file].path",
    )

    result = await (
        WorkflowExecutor(workflow, {}, log_base_dir=tmp_path / "logs")
        .with_parameters({"path": str(source)})
        .run()
    )

    assert not result.success
    assert "Missing reference capability" in result.node_outputs["read"].output
    assert "nodes[type=read_file].path" in result.node_outputs["read"].output


@pytest.mark.anyio
async def test_common_llm_input_mapping_uses_registered_typed_references(
    tmp_path: Path,
) -> None:
    workflow = AgenticWorkflow.from_dict(
        {"workflow": {"id": "typed-mapping", "name": "Typed mapping"}}
    )
    workflow.register_agent(
        AgentConfig(
            agent_id="bot",
            subscription="claude_code",
            working_dir=tmp_path,
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="summarize",
            operation=CommonLlmTaskOperation(
                type=OperationType.COMMON_LLM_TASK,
                agent_id="bot",
                working_dir=tmp_path,
                target="{{payload}}",
                input_mapping={"payload": "trigger.payload"},
            ),
        )
    )
    subscription = FakeSubscription(output="done")

    result = (
        await WorkflowExecutor(
            workflow,
            {"claude_code": subscription},
            log_base_dir=tmp_path / "logs",
        )
        .with_trigger_context({"payload": {"items": [1, 2]}})
        .run()
    )

    assert result.success
    assert "{'items': [1, 2]}" in cast(str, subscription.calls[0]["prompt"])


def test_schema_cli_is_deterministic_valid_json_and_focusable() -> None:
    first = runner.invoke(app, ["schema", "--operation", "bash_command"])
    second = runner.invoke(app, ["schema", "--operation", "bash_command"])

    assert first.exit_code == 0
    assert first.output == second.output
    payload = json.loads(first.output)
    assert payload["operation"]["type"] == "bash_command"
    assert payload["operation"]["schema"]["required"] == ["id", "type", "command"]


def test_schema_cli_exposes_mutation_enums_and_numeric_constraints() -> None:
    edge_result = runner.invoke(app, ["schema", "--command", "workflow.add-edge"])
    node_result = runner.invoke(app, ["schema", "--command", "workflow.add-node"])

    assert edge_result.exit_code == 0
    assert node_result.exit_code == 0
    edge_parameters = {
        item["name"]: item
        for item in json.loads(edge_result.output)["mutation_command"]["parameters"]
    }
    node_parameters = {
        item["name"]: item
        for item in json.loads(node_result.output)["mutation_command"]["parameters"]
    }
    assert "output_matches" in edge_parameters["condition"]["enum"]
    assert set(node_parameters["node_type"]["enum"]) == set(OPERATION_MODELS)
    assert node_parameters["retry_count"]["min"] == 0
    assert node_parameters["http_timeout"]["min"] == 0.1


def test_schema_cli_rejects_unsupported_requests() -> None:
    assert runner.invoke(app, ["schema", "--format", "yaml"]).exit_code != 0
    assert runner.invoke(app, ["schema", "--operation", "missing"]).exit_code != 0
    assert runner.invoke(app, ["schema", "--capability", "missing"]).exit_code != 0
    assert runner.invoke(app, ["schema", "--command", "workflow.missing"]).exit_code != 0


def test_installed_entrypoint_discovers_schema_without_repository_cwd(tmp_path: Path) -> None:
    executable = os.path.dirname(sys.executable) + "/gof"
    result = subprocess.run(
        [executable, "schema", "--operation", "pass"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["operation"]["type"] == "pass"


@pytest.mark.parametrize("width", [60, 80, 120])
@pytest.mark.parametrize("color", [False, True])
def test_human_help_and_missing_skill_recovery_point_to_authoring_contract(
    monkeypatch, width: int, color: bool
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(rich_utils, "MAX_WIDTH", width)
    monkeypatch.setattr(rich_utils, "FORCE_TERMINAL", color)
    monkeypatch.setattr(rich_utils, "COLOR_SYSTEM", "standard" if color else None)
    root_help = runner.invoke(app, ["--help"], color=color)
    node_help = runner.invoke(app, ["workflow", "add-node", "--help"], color=color)
    monkeypatch.setattr(
        "gofer.ui.chat.rattish_assistant_skill_path",
        lambda: (_ for _ in ()).throw(RattishArtifactError("missing")),
    )

    assert root_help.exit_code == 0
    assert node_help.exit_code == 0
    if color:
        assert "\x1b[" in root_help.output
    # Rich styles options and wraps help at the terminal width, including in CI.
    root_text = " ".join(Text.from_ansi(root_help.output).plain.split())
    node_text = " ".join(Text.from_ansi(node_help.output).plain.split())
    assert "gof schema --format json" in root_text
    assert "gof schema --operation TYPE" in node_text
    assert "gof rattish docs --format json" in _load_skill_text()
