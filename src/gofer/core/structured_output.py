from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError, ValidationError  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from gofer.core.workflow import AgenticWorkflow


class StructuredOutputError(ValueError):
    """An agent response could not be validated as its declared result type."""


class OutputFieldOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"
    NOT_IN = "not_in"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    EXISTS = "exists"
    MATCHES = "matches"


MISSING = object()


def resolve_output_schema(
    declaration: str | dict[str, Any] | None,
    schemas: dict[str, dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    if declaration is None:
        return None, None
    if isinstance(declaration, str):
        schema = schemas.get(declaration)
        if schema is None:
            raise StructuredOutputError(f"Unknown output schema {declaration!r}")
        validate_schema(schema)
        return declaration, schema
    validate_schema(declaration)
    return None, declaration


def validate_schema(schema: dict[str, Any]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise StructuredOutputError(f"Invalid JSON Schema: {exc.message}") from exc
    for reference in _schema_references(schema):
        if not reference.startswith("#"):
            raise StructuredOutputError(
                "Only local JSON Schema $ref values are supported for portable "
                f"structured outputs; got {reference!r}"
            )
        if _resolve_local_json_pointer(schema, reference) is None:
            raise StructuredOutputError(
                f"JSON Schema $ref {reference!r} does not resolve within the schema"
            )


def _schema_references(value: Any) -> list[str]:
    references: list[str] = []
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str):
            references.append(reference)
        for child in value.values():
            references.extend(_schema_references(child))
    elif isinstance(value, list):
        for child in value:
            references.extend(_schema_references(child))
    return references


def structured_output_instruction(schema: dict[str, Any]) -> str:
    return (
        "Return only one JSON value that conforms to this JSON Schema. Do not wrap it "
        "in Markdown fences or add explanatory prose.\n\nJSON Schema:\n"
        + json.dumps(schema, indent=2, sort_keys=True)
    )


def parse_and_validate_output(text: str, schema: dict[str, Any]) -> Any:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(
            f"Structured output is not valid JSON at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path) or "$"
        raise StructuredOutputError(
            f"Structured output violates the schema at {path}: {exc.message}"
        ) from exc
    return value


def get_output_field(value: Any, path: str) -> Any:
    current = value
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return MISSING
    return current


def evaluate_output_field(
    structured_value: Any,
    field: str,
    operator: OutputFieldOperator,
    operand: Any = None,
) -> bool:
    actual = get_output_field(structured_value, field)
    if operator == OutputFieldOperator.EXISTS:
        return actual is not MISSING
    if actual is MISSING:
        return False
    if operator == OutputFieldOperator.EQUALS:
        return bool(actual == operand)
    if operator == OutputFieldOperator.NOT_EQUALS:
        return bool(actual != operand)
    if operator == OutputFieldOperator.IN:
        return isinstance(operand, list) and actual in operand
    if operator == OutputFieldOperator.NOT_IN:
        return isinstance(operand, list) and actual not in operand
    if operator == OutputFieldOperator.MATCHES:
        return isinstance(actual, str) and bool(re.search(str(operand), actual))
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return False
    if isinstance(operand, bool) or not isinstance(operand, (int, float)):
        return False
    if operator == OutputFieldOperator.GREATER_THAN:
        return actual > operand
    if operator == OutputFieldOperator.GREATER_THAN_OR_EQUAL:
        return actual >= operand
    if operator == OutputFieldOperator.LESS_THAN:
        return actual < operand
    if operator == OutputFieldOperator.LESS_THAN_OR_EQUAL:
        return actual <= operand
    return False


def schema_at_path(schema: dict[str, Any], path: str) -> dict[str, Any] | None:
    """Resolve a dotted path through a Draft 2020-12 schema.

    Local JSON Pointer references and the standard composition keywords are followed.
    For ``anyOf``/``oneOf``, a field is considered declared when at least one branch
    declares it; the returned schema retains every matching branch for type checking.
    """
    current: dict[str, Any] | None = schema
    for part in path.split(".") if path else []:
        if current is None:
            return None
        current = _field_schema(current, schema, part, set())
    if current is None:
        return None
    return _attach_root_definitions(current, schema)


def _field_schema(
    schema: dict[str, Any], root: dict[str, Any], part: str, seen_refs: set[str]
) -> dict[str, Any] | None:
    reference = schema.get("$ref")
    if isinstance(reference, str):
        if reference in seen_refs:
            return None
        target = _resolve_local_json_pointer(root, reference)
        if target is None:
            return None
        siblings = {key: value for key, value in schema.items() if key != "$ref"}
        effective = {"allOf": [target, siblings]} if siblings else target
        return _field_schema(effective, root, part, {*seen_refs, reference})

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        constraints: list[dict[str, Any]] = []
        base = {key: value for key, value in schema.items() if key != "allOf"}
        if base:
            found = _field_schema(base, root, part, seen_refs.copy())
            if found is not None:
                constraints.append(found)
        for item in all_of:
            if isinstance(item, dict):
                found = _field_schema(item, root, part, seen_refs.copy())
                if found is not None:
                    constraints.append(found)
        return _combine_constraints("allOf", constraints)

    for keyword in ("anyOf", "oneOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            alternatives: list[dict[str, Any]] = []
            declared_count = 0
            base = {key: value for key, value in schema.items() if key != keyword}
            for item in branches:
                if not isinstance(item, dict):
                    continue
                effective = {"allOf": [base, item]} if base else item
                found = _field_schema(effective, root, part, seen_refs.copy())
                # A branch that does not declare the field leaves its type unconstrained.
                if found is not None:
                    declared_count += 1
                alternatives.append(found if found is not None else {})
            if declared_count == 0:
                return None
            return _combine_constraints(keyword, alternatives)

    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties")
        child = properties.get(part) if isinstance(properties, dict) else None
        return child if isinstance(child, dict) else None
    if schema_type == "array" and part.isdigit():
        items = schema.get("items")
        return items if isinstance(items, dict) else None
    return None


def _resolve_local_json_pointer(root: dict[str, Any], reference: str) -> dict[str, Any] | None:
    if reference == "#":
        return root
    if not reference.startswith("#/"):
        return None
    current: Any = root
    for token in reference[2:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current if isinstance(current, dict) else None


def _dedupe_schemas(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for schema in schemas:
        key = json.dumps(schema, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            unique.append(schema)
    return unique


def _combine_constraints(keyword: str, constraints: list[dict[str, Any]]) -> dict[str, Any] | None:
    constraints = _dedupe_schemas(constraints)
    if not constraints:
        return None
    if len(constraints) == 1:
        return constraints[0]
    return {keyword: constraints}


def _attach_root_definitions(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    if not _schema_references(schema):
        return schema
    definitions: dict[str, Any] = {
        key: root[key] for key in ("$defs", "definitions") if key in root and key not in schema
    }
    return {**schema, **definitions} if definitions else schema


def predicate_type_error(
    field_schema: dict[str, Any], operator: OutputFieldOperator, operand: Any
) -> str | None:
    schema_types = _schema_types(field_schema)
    if operator == OutputFieldOperator.EXISTS:
        return None
    if schema_types is None or not schema_types:
        return f"{operator.value} requires the field schema to declare a usable type"
    if operator == OutputFieldOperator.MATCHES:
        if not schema_types <= {"string"}:
            return "matches requires a string field"
        if not isinstance(operand, str):
            return "matches requires a string operand"
        try:
            re.compile(operand)
        except re.error as exc:
            return f"matches has an invalid regex: {exc}"
        return None
    if operator in {
        OutputFieldOperator.GREATER_THAN,
        OutputFieldOperator.GREATER_THAN_OR_EQUAL,
        OutputFieldOperator.LESS_THAN,
        OutputFieldOperator.LESS_THAN_OR_EQUAL,
    }:
        if not schema_types <= {"integer", "number"}:
            return f"{operator.value} requires a numeric field"
        if isinstance(operand, bool) or not isinstance(operand, (int, float)):
            return f"{operator.value} requires a numeric operand"
    if operator in {OutputFieldOperator.IN, OutputFieldOperator.NOT_IN} and not isinstance(
        operand, list
    ):
        return f"{operator.value} requires an array operand"
    if operator in {OutputFieldOperator.IN, OutputFieldOperator.NOT_IN} and isinstance(
        operand, list
    ):
        if any(not _value_matches_schema(item, field_schema) for item in operand):
            return f"{operator.value} operand items must match the field type"
    if operator in {OutputFieldOperator.EQUALS, OutputFieldOperator.NOT_EQUALS}:
        if not _value_matches_schema(operand, field_schema):
            return f"{operator.value} operand must match the field type"
    return None


def _schema_types(
    schema: dict[str, Any],
    root: dict[str, Any] | None = None,
    seen_refs: set[str] | None = None,
) -> set[str] | None:
    root = root or schema
    seen_refs = seen_refs or set()
    reference = schema.get("$ref")
    if isinstance(reference, str):
        if reference in seen_refs:
            return None
        target = _resolve_local_json_pointer(root, reference)
        if target is None:
            return None
        siblings = {key: value for key, value in schema.items() if key != "$ref"}
        effective = {"allOf": [target, siblings]} if siblings else target
        return _schema_types(effective, root, {*seen_refs, reference})
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return {schema_type}
    if isinstance(schema_type, list):
        return {item for item in schema_type if isinstance(item, str)}
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return {_json_type(value) for value in enum_values}
    if "const" in schema:
        return {_json_type(schema["const"])}
    for keyword in ("anyOf", "oneOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            branch_types = [
                _schema_types(branch, root, seen_refs.copy())
                for branch in branches
                if isinstance(branch, dict)
            ]
            if not branch_types or any(item is None for item in branch_types):
                return None
            return set().union(*(item for item in branch_types if item is not None))
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        known = [
            item
            for branch in all_of
            if isinstance(branch, dict)
            if (item := _schema_types(branch, root, seen_refs.copy())) is not None
        ]
        if not known:
            return None
        result = known[0]
        for item in known[1:]:
            result = _intersect_json_types(result, item)
        return result
    return None


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _intersect_json_types(left: set[str], right: set[str]) -> set[str]:
    intersection = left & right
    if "number" in left and "integer" in right:
        intersection.add("integer")
    if "integer" in left and "number" in right:
        intersection.add("integer")
    return intersection


def _value_matches_schema(value: Any, schema: dict[str, Any]) -> bool:
    return bool(Draft202012Validator(schema).is_valid(value))


def _value_matches_schema_type(value: Any, schema_type: Any) -> bool:
    if isinstance(schema_type, list):
        return any(_value_matches_schema_type(value, item) for item in schema_type)
    if schema_type == "null":
        return value is None
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "object":
        return isinstance(value, dict)
    return True


def predicate_explanation(field: str, operator: OutputFieldOperator, value: Any) -> str:
    if operator == OutputFieldOperator.EXISTS:
        return f"{field} exists"
    return f"{field} {operator.value.replace('_', ' ')} {json.dumps(value, default=str)}"


def structured_workflow_errors(workflow: AgenticWorkflow) -> list[str]:
    """Return configuration errors that must block typed-result execution."""
    from gofer.core.operations import AgentOperation, CommonLlmTaskOperation

    errors: list[str] = []
    resolved_by_node: dict[str, dict[str, Any] | None] = {}
    for node in workflow.graph.nodes_in_order():
        op = node.operation
        if not isinstance(op, (AgentOperation, CommonLlmTaskOperation)):
            resolved_by_node[node.node_id] = None
            continue
        try:
            _, schema = resolve_output_schema(op.output_schema, workflow.config.output_schemas)
        except StructuredOutputError as exc:
            errors.append(f"node {node.node_id!r}: {exc}")
            schema = None
        resolved_by_node[node.node_id] = schema

    for from_node, to_node in workflow.graph._graph.edges():
        edge = workflow.graph.get_edge_config(from_node, to_node)
        if str(edge.condition) != "output_field":
            continue
        schema = resolved_by_node.get(from_node)
        if schema is None:
            errors.append(
                f"edge {from_node!r} -> {to_node!r}: producer must declare an output schema"
            )
            continue
        field_schema = schema_at_path(schema, edge.field or "")
        if field_schema is None:
            errors.append(
                f"edge {from_node!r} -> {to_node!r}: field {edge.field!r} is not declared"
            )
            continue
        if edge.operator is not None:
            issue = predicate_type_error(field_schema, edge.operator, edge.value)
            if issue:
                errors.append(f"edge {from_node!r} -> {to_node!r}: {issue}")
    return errors
