from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from gofer.core.agent import AgentConfig
from gofer.core.executor import NodeOutput, WorkflowExecutor
from gofer.core.graph import EdgeConditionType, EdgeConfig, GraphNode
from gofer.core.operations import (
    AgentOperation,
    BashCommandOperation,
    CommonLlmTaskOperation,
    OperationType,
)
from gofer.core.planner import build_execution_plan
from gofer.core.structured_output import (
    OutputFieldOperator,
    StructuredOutputError,
    parse_and_validate_output,
    predicate_type_error,
    resolve_output_schema,
    schema_at_path,
)
from gofer.core.validation import validate_workflow
from gofer.core.workflow import AgenticWorkflow, WorkflowConfig
from gofer.ui.api import workflow_from_payload, workflow_to_payload
from tests.conftest import FakeSubscription

REVIEW_SCHEMA = {
    "type": "object",
    "required": ["verdict", "score", "findings"],
    "properties": {
        "verdict": {"type": "string", "enum": ["approved", "changes_requested"]},
        "score": {"type": "number"},
        "findings": {"type": "array", "items": {"type": "string"}},
        "note": {"type": ["string", "null"]},
    },
}


def _output(value: object) -> NodeOutput:
    return NodeOutput("review", True, "raw", 0, 0, value=value)


def test_output_field_predicate_operators() -> None:
    output = _output({"verdict": "approved", "score": 8.5, "findings": ["minor"], "note": None})
    cases = [
        ("verdict", "equals", "approved", True),
        ("verdict", "not_equals", "changes_requested", True),
        ("verdict", "in", ["approved", "rejected"], True),
        ("verdict", "not_in", ["rejected"], True),
        ("score", "greater_than", 8, True),
        ("score", "greater_than_or_equal", 8.5, True),
        ("score", "less_than", 9, True),
        ("score", "less_than_or_equal", 8.5, True),
        ("verdict", "matches", "^appro", True),
        ("note", "exists", None, True),
        ("missing", "exists", None, False),
    ]
    for field, operator, operand, expected in cases:
        edge = EdgeConfig(
            from_node="review",
            to_node="next",
            condition=EdgeConditionType.OUTPUT_FIELD,
            field=field,
            operator=OutputFieldOperator(operator),
            value=operand,
        )
        assert edge.evaluate(output) is expected


def test_schema_path_resolves_local_refs_defs_and_composition() -> None:
    schema = {
        "$defs": {
            "base": {
                "type": "object",
                "properties": {"verdict": {"type": "string"}},
            },
            "details": {
                "type": "object",
                "properties": {"score": {"type": "number"}},
            },
        },
        "allOf": [
            {"$ref": "#/$defs/base"},
            {
                "oneOf": [
                    {"$ref": "#/$defs/details"},
                    {
                        "type": "object",
                        "properties": {"score": {"type": "integer"}},
                    },
                ]
            },
        ],
    }

    assert schema_at_path(schema, "verdict") == {"type": "string"}
    score_schema = schema_at_path(schema, "score")
    assert score_schema is not None
    assert "oneOf" in score_schema


def test_all_of_field_constraints_remain_conjunctive_for_predicate_types() -> None:
    field_schema = {
        "allOf": [
            {"type": "string"},
            {"enum": ["a"]},
        ]
    }
    assert (
        predicate_type_error(field_schema, OutputFieldOperator.EQUALS, 1)
        == "equals operand must match the field type"
    )
    assert predicate_type_error(field_schema, OutputFieldOperator.EQUALS, "a") is None
    assert (
        predicate_type_error(field_schema, OutputFieldOperator.EQUALS, "b")
        == "equals operand must match the field type"
    )


def test_all_of_operand_mismatch_is_reported_by_workflow_validation(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    review = workflow.graph._nodes["review"].operation
    assert isinstance(review, AgentOperation)
    review.output_schema = {
        "type": "object",
        "properties": {"verdict": {"allOf": [{"type": "string"}, {"enum": ["approved"]}]}},
    }
    workflow.add_operation(
        GraphNode(
            node_id="next",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="true"),
        )
    )
    workflow.then(
        "review",
        "next",
        EdgeConfig(
            from_node="review",
            to_node="next",
            condition=EdgeConditionType.OUTPUT_FIELD,
            field="verdict",
            operator=OutputFieldOperator.EQUALS,
            value=1,
        ),
    )

    report = validate_workflow(workflow)
    assert "workflow.edge_output_field_type_invalid" in {
        diagnostic.code for diagnostic in report.diagnostics
    }


@pytest.mark.parametrize(
    "operator,operand",
    [
        (OutputFieldOperator.EQUALS, 1),
        (OutputFieldOperator.MATCHES, ".*"),
        (OutputFieldOperator.GREATER_THAN, 1),
    ],
)
def test_unconstrained_field_rejects_type_specific_predicates(
    operator: OutputFieldOperator, operand: object
) -> None:
    issue = predicate_type_error({}, operator, operand)
    assert issue == f"{operator.value} requires the field schema to declare a usable type"


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef", "$recursiveRef"])
@pytest.mark.parametrize("nested", [False, True])
def test_external_schema_refs_are_rejected_as_unsupported_portable_subset(
    keyword: str, nested: bool
) -> None:
    schema: dict[str, object] = {keyword: "https://example.test/result.schema.json"}
    if nested:
        schema = {"properties": {"result": schema}}
    with pytest.raises(StructuredOutputError, match="Only local JSON Schema.*supported"):
        resolve_output_schema(schema, {})


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef", "$recursiveRef"])
@pytest.mark.parametrize("scheme", ["http", "file"])
def test_output_validation_never_retrieves_external_schemas(monkeypatch, keyword, scheme):
    calls = []

    def retrieve(request):
        calls.append(request)
        raise AssertionError("Schema validation must not retrieve external resources")

    monkeypatch.setattr(urllib.request, "urlopen", retrieve)
    schema = {"properties": {"result": {keyword: f"{scheme}://localhost/private/schema.json"}}}
    with pytest.raises(StructuredOutputError, match="Only local JSON Schema.*supported"):
        parse_and_validate_output('{"result":"value"}', schema)
    assert not calls


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
def test_output_validation_preserves_local_schema_references(keyword):
    schema = {keyword: "#/$defs/result", "$defs": {"result": {"type": "string"}}}
    assert parse_and_validate_output('"value"', schema) == "value"
    with pytest.raises(StructuredOutputError, match="not of type 'string'"):
        parse_and_validate_output("42", schema)


@pytest.mark.parametrize("scheme", ["http", "file"])
def test_local_reference_under_external_schema_id_never_retrieves_resources(monkeypatch, scheme):
    calls = []

    def retrieve(request):
        calls.append(request)
        raise AssertionError("Schema validation must not retrieve external resources")

    monkeypatch.setattr(urllib.request, "urlopen", retrieve)
    schema = {
        "$defs": {"result": {"type": "string"}},
        "properties": {
            "result": {"$id": f"{scheme}://localhost/private/schema.json", "$ref": "#/$defs/result"}
        },
    }
    with pytest.raises(StructuredOutputError, match="cannot be resolved locally"):
        parse_and_validate_output('{"result":"value"}', schema)
    assert not calls


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
def test_structured_output_rejects_nonfinite_numbers(raw):
    with pytest.raises(StructuredOutputError, match="finite"):
        parse_and_validate_output(f'{{"score":{raw}}}', {"type": "object"})


def test_structured_output_preserves_finite_numbers_and_literal_constant_strings():
    value = {"score": 1.5, "count": 123, "label": "NaN Infinity"}
    assert parse_and_validate_output(json.dumps(value), {"type": "object"}) == value


def test_deep_structured_output_returns_a_recoverable_error():
    with pytest.raises(StructuredOutputError, match="nesting"):
        parse_and_validate_output("[" * 2000 + "0" + "]" * 2000, {"type": "array"})


def test_recursive_schema_returns_a_recoverable_error():
    with pytest.raises(StructuredOutputError, match="recursion"):
        parse_and_validate_output('"value"', {"$ref": "#"})


def test_oversized_integer_returns_a_recoverable_error():
    with pytest.raises(StructuredOutputError, match="not valid JSON"):
        parse_and_validate_output("1" * 5000, {"type": "integer"})


def test_ref_schema_field_predicate_validates() -> None:
    schema = {
        "$ref": "#/$defs/result",
        "$defs": {
            "result": {
                "type": "object",
                "properties": {"verdict": {"type": "string"}},
                "required": ["verdict"],
            }
        },
    }
    workflow = AgenticWorkflow(
        WorkflowConfig(
            id="refs",
            name="Refs",
            output_schemas={"result": schema},
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="producer",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="missing-agent",
                working_dir=Path("."),
                output_schema="result",
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="next",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="true"),
        )
    )
    workflow.then(
        "producer",
        "next",
        EdgeConfig(
            from_node="producer",
            to_node="next",
            condition=EdgeConditionType.OUTPUT_FIELD,
            field="verdict",
            operator=OutputFieldOperator.EQUALS,
            value="approved",
        ),
    )

    codes = {item.code for item in validate_workflow(workflow).diagnostics}
    assert "workflow.edge_output_field_unknown" not in codes


def test_field_level_ref_is_resolved_for_predicate_type_checking() -> None:
    schema = {
        "type": "object",
        "$defs": {"verdict": {"type": "string", "enum": ["approved"]}},
        "properties": {"verdict": {"$ref": "#/$defs/verdict"}},
    }
    field_schema = schema_at_path(schema, "verdict")

    assert field_schema is not None
    assert predicate_type_error(field_schema, OutputFieldOperator.EQUALS, "approved") is None
    assert predicate_type_error(field_schema, OutputFieldOperator.MATCHES, "^appro") is None
    assert (
        predicate_type_error(field_schema, OutputFieldOperator.EQUALS, 1)
        == "equals operand must match the field type"
    )

    workflow = AgenticWorkflow(WorkflowConfig(id="field-ref", name="Field ref"))
    workflow.add_operation(
        GraphNode(
            node_id="producer",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="missing-agent",
                working_dir=Path("."),
                output_schema=schema,
            ),
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="next",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="true"),
        )
    )
    workflow.then(
        "producer",
        "next",
        EdgeConfig(
            from_node="producer",
            to_node="next",
            condition=EdgeConditionType.OUTPUT_FIELD,
            field="verdict",
            operator=OutputFieldOperator.EQUALS,
            value="approved",
        ),
    )

    codes = {item.code for item in validate_workflow(workflow).diagnostics}
    assert "workflow.edge_output_field_unknown" not in codes
    assert "workflow.edge_output_field_type_invalid" not in codes


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ('{"score":1,"findings":[]}', "required property"),
        ('{"verdict":null,"score":1,"findings":[]}', "not of type 'string'"),
        ('{"verdict":"approved","score":"high","findings":[]}', "not of type 'number'"),
    ],
)
def test_missing_null_and_wrong_type_fail_schema_validation(raw: str, message: str) -> None:
    with pytest.raises(StructuredOutputError, match=message):
        parse_and_validate_output(raw, REVIEW_SCHEMA)


def _workflow(tmp_path: Path, *, repair_attempts: int = 0) -> AgenticWorkflow:
    workflow = AgenticWorkflow(
        WorkflowConfig(
            id="structured",
            name="Structured",
            output_schemas={"review_result": REVIEW_SCHEMA},
        )
    )
    workflow.register_agent(
        AgentConfig(
            agent_id="reviewer",
            subscription="claude_code",
            working_dir=tmp_path,
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="review",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="reviewer",
                working_dir=tmp_path,
                output_schema="review_result",
                repair_attempts=repair_attempts,
            ),
        )
    )
    return workflow


async def test_agent_structured_output_is_validated_and_routes(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    workflow.add_operation(
        GraphNode(
            node_id="approved",
            operation=BashCommandOperation(
                type=OperationType.BASH_COMMAND, command="printf approved"
            ),
        )
    )
    workflow.then(
        "review",
        "approved",
        EdgeConfig(
            from_node="review",
            to_node="approved",
            condition=EdgeConditionType.OUTPUT_FIELD,
            field="verdict",
            operator=OutputFieldOperator.EQUALS,
            value="approved",
        ),
    )
    raw = '{"verdict":"approved","score":9,"findings":[]}'
    subscription = FakeSubscription(output=raw)

    result = await WorkflowExecutor(workflow, {"claude_code": subscription}).run()

    review = result.node_outputs["review"]
    assert result.success
    assert review.output == raw
    assert review.value == {"verdict": "approved", "score": 9, "findings": []}
    assert review.data["rawResponse"] == raw
    assert result.node_outputs["approved"].success
    assert "Return only one JSON value" in str(subscription.calls[0]["prompt"])


async def test_unstructured_agent_preserves_legacy_text_value(tmp_path: Path) -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="legacy-agent", name="Legacy agent"))
    workflow.register_agent(
        AgentConfig(
            agent_id="legacy",
            subscription="claude_code",
            working_dir=tmp_path,
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="legacy",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="legacy",
                working_dir=tmp_path,
            ),
        )
    )

    result = await WorkflowExecutor(
        workflow, {"claude_code": FakeSubscription(output="plain text")}
    ).run()

    output = result.node_outputs["legacy"]
    assert result.success
    assert output.output == "plain text"
    assert output.text == "plain text"
    assert output.value == "plain text"


async def test_unstructured_common_llm_task_preserves_legacy_text_value(
    tmp_path: Path,
) -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="legacy-common", name="Legacy common"))
    workflow.register_agent(
        AgentConfig(
            agent_id="legacy",
            subscription="claude_code",
            working_dir=tmp_path,
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="legacy",
            operation=CommonLlmTaskOperation(
                type=OperationType.COMMON_LLM_TASK,
                agent_id="legacy",
                target="text",
                working_dir=tmp_path,
            ),
        )
    )

    result = await WorkflowExecutor(
        workflow, {"claude_code": FakeSubscription(output="plain common text")}
    ).run()

    assert result.node_outputs["legacy"].value == "plain common text"


async def test_invalid_structured_output_fails_without_prose_routing(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    subscription = FakeSubscription(output="The verdict is approved")

    result = await WorkflowExecutor(workflow, {"claude_code": subscription}).run()

    review = result.node_outputs["review"]
    assert not result.success
    assert not review.success
    assert "not valid JSON" in review.output
    assert review.data["rawResponse"] == "The verdict is approved"


async def test_common_llm_task_supports_inline_output_schema(tmp_path: Path) -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="common", name="Common"))
    workflow.register_agent(
        AgentConfig(
            agent_id="extractor",
            subscription="claude_code",
            working_dir=tmp_path,
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="extract",
            operation=CommonLlmTaskOperation(
                type=OperationType.COMMON_LLM_TASK,
                agent_id="extractor",
                task="extract",
                target="release status",
                working_dir=tmp_path,
                output_schema={
                    "type": "object",
                    "required": ["ready"],
                    "properties": {"ready": {"type": "boolean"}},
                },
            ),
        )
    )

    result = await WorkflowExecutor(
        workflow, {"claude_code": FakeSubscription(output='{"ready":true}')}
    ).run()

    assert result.success
    assert result.node_outputs["extract"].value == {"ready": True}


async def test_top_level_json_null_is_preserved_as_native_none(tmp_path: Path) -> None:
    workflow = AgenticWorkflow(WorkflowConfig(id="null-result", name="Null result"))
    workflow.register_agent(
        AgentConfig(
            agent_id="nullable",
            subscription="claude_code",
            working_dir=tmp_path,
        )
    )
    workflow.add_operation(
        GraphNode(
            node_id="nullable",
            operation=AgentOperation(
                type=OperationType.AGENT,
                agent_id="nullable",
                working_dir=tmp_path,
                output_schema={"type": "null"},
            ),
        )
    )

    result = await WorkflowExecutor(
        workflow, {"claude_code": FakeSubscription(output="null")}
    ).run()

    output = result.node_outputs["nullable"]
    assert result.success
    assert output.output == "null"
    assert output.text == "null"
    assert output.value is None
    assert output.data["structuredValue"] is None


async def test_bounded_repair_is_accounted_and_can_succeed(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path, repair_attempts=1)
    subscription = FakeSubscription()
    responses = iter(
        [
            "not json",
            '{"verdict":"changes_requested","score":2,"findings":["bug"]}',
        ]
    )

    def set_response(_working_dir: Path) -> None:
        subscription._output = next(responses)

    subscription._on_execute = set_response
    result = await WorkflowExecutor(workflow, {"claude_code": subscription}).run()

    review = result.node_outputs["review"]
    assert result.success
    assert len(subscription.calls) == 2
    assert review.value == {
        "verdict": "changes_requested",
        "score": 2,
        "findings": ["bug"],
    }
    repairs = review.data["structuredOutputRepairs"]
    assert isinstance(repairs, list)
    assert repairs[-1]["validated"] is True
    assert "usage" in repairs[-1]


async def test_bounded_repair_exhaustion_has_actionable_failure(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path, repair_attempts=1)
    result = await WorkflowExecutor(
        workflow, {"claude_code": FakeSubscription(output="still not json")}
    ).run()

    review = result.node_outputs["review"]
    assert not result.success
    assert "repair exhausted after 1 attempt" in review.output
    repairs = review.data["structuredOutputRepairs"]
    assert isinstance(repairs, list)
    assert len(repairs) == 2


def test_schema_and_predicate_round_trip_and_plan(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    workflow.add_operation(
        GraphNode(
            node_id="next",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="printf next"),
        )
    )
    workflow.then(
        "review",
        "next",
        EdgeConfig(
            from_node="review",
            to_node="next",
            condition=EdgeConditionType.OUTPUT_FIELD,
            field="score",
            operator=OutputFieldOperator.GREATER_THAN_OR_EQUAL,
            value=7,
        ),
    )
    path = tmp_path / "workflow.toml"
    workflow.to_file(path)
    loaded = AgenticWorkflow.from_file(path)

    edge = loaded.graph.get_edge_config("review", "next")
    assert loaded.config.output_schemas["review_result"] == REVIEW_SCHEMA
    assert edge.operator == OutputFieldOperator.GREATER_THAN_OR_EQUAL
    assert edge.value == 7
    assert validate_workflow(loaded).ok
    plan = build_execution_plan(loaded)
    assert plan["generations"][0]["nodes"][0]["outputSchema"] == REVIEW_SCHEMA
    assert plan["edges"][0]["explanation"] == "score greater than or equal 7"

    payload = workflow_to_payload(loaded)
    assert payload["outputSchemas"]["review_result"] == REVIEW_SCHEMA
    assert payload["edges"][0]["field"] == "score"
    ui_round_trip = workflow_from_payload(payload)
    ui_edge = ui_round_trip.graph.get_edge_config("review", "next")
    assert ui_edge.operator == OutputFieldOperator.GREATER_THAN_OR_EQUAL


def test_predicate_path_and_operand_are_checked_before_execution(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    for node_id in ("bad-path", "bad-type"):
        workflow.add_operation(
            GraphNode(
                node_id=node_id,
                operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="true"),
            )
        )
    workflow.then(
        "review",
        "bad-path",
        EdgeConfig(
            from_node="review",
            to_node="bad-path",
            condition=EdgeConditionType.OUTPUT_FIELD,
            field="unknown",
            operator=OutputFieldOperator.EQUALS,
            value=True,
        ),
    )
    workflow.then(
        "review",
        "bad-type",
        EdgeConfig(
            from_node="review",
            to_node="bad-type",
            condition=EdgeConditionType.OUTPUT_FIELD,
            field="verdict",
            operator=OutputFieldOperator.GREATER_THAN,
            value=1,
        ),
    )

    report = validate_workflow(workflow)
    assert not report.ok
    assert {item.code for item in report.diagnostics} >= {
        "workflow.edge_output_field_unknown",
        "workflow.edge_output_field_type_invalid",
    }


async def test_invalid_predicate_configuration_blocks_provider_call(tmp_path: Path) -> None:
    workflow = _workflow(tmp_path)
    workflow.add_operation(
        GraphNode(
            node_id="next",
            operation=BashCommandOperation(type=OperationType.BASH_COMMAND, command="true"),
        )
    )
    workflow.then(
        "review",
        "next",
        EdgeConfig(
            from_node="review",
            to_node="next",
            condition=EdgeConditionType.OUTPUT_FIELD,
            field="missing",
            operator=OutputFieldOperator.EQUALS,
            value=True,
        ),
    )
    subscription = FakeSubscription(output="{}")

    with pytest.raises(ValueError, match="Structured output configuration is invalid"):
        await WorkflowExecutor(workflow, {"claude_code": subscription}).run()

    assert subscription.calls == []
