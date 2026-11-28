from __future__ import annotations

import json
import math
import re
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError, ValidationError  # type: ignore[import-untyped]
from referencing import Registry
from referencing.exceptions import Unresolvable

from gofer.core.json_types import json_type


class StructuredOutputError(ValueError):
    """An agent response could not be validated as its declared result type."""


MISSING = object()
STRUCTURED_OUTPUT_MAX_DEPTH = 64
# Models sometimes wrap an otherwise valid reply in one Markdown fence despite instructions.
_JSON_FENCE = re.compile(r"\A\s*```[ \t]*(?:json)?[ \t]*\r?\n(.*?)\r?\n?```\s*\Z", re.I | re.S)


def validate_schema(schema: dict[str, Any]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise StructuredOutputError(f"Invalid JSON Schema: {exc.message}") from exc
    except RecursionError:
        raise StructuredOutputError("JSON Schema nesting is too deep") from None
    for keyword, reference in _schema_references(schema):
        if not reference.startswith("#"):
            raise StructuredOutputError(
                f"Only local JSON Schema {keyword} values are supported for portable "
                f"structured outputs; got {reference!r}"
            )
        if _resolve_local_json_pointer(schema, reference) is None:
            raise StructuredOutputError(
                f"JSON Schema {keyword} {reference!r} does not resolve within the schema"
            )


def _schema_references(value: Any) -> list[tuple[str, str]]:
    references: list[tuple[str, str]] = []
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for keyword in ("$ref", "$dynamicRef", "$recursiveRef"):
                reference = item.get(keyword)
                if isinstance(reference, str):
                    references.append((keyword, reference))
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return references


def structured_output_instruction(schema: dict[str, Any]) -> str:
    return (
        "Return only one JSON value that conforms to this JSON Schema. Do not wrap it "
        "in Markdown fences or add explanatory prose.\n\nJSON Schema:\n"
        + json.dumps(schema, indent=2, sort_keys=True)
    )


def parse_and_validate_output(text: str, schema: dict[str, Any]) -> Any:
    # Recheck at the runtime boundary, including callers with directly supplied IR.
    validate_schema(schema)

    def finite_number(raw: str) -> float:
        number = float(raw)
        if not math.isfinite(number):
            raise StructuredOutputError("Structured output numbers must be finite")
        return number

    fenced = _JSON_FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text, parse_float=finite_number, parse_constant=finite_number)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(
            f"Structured output is not valid JSON at line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc
    except StructuredOutputError:
        raise
    except ValueError as exc:
        raise StructuredOutputError(f"Structured output is not valid JSON: {exc}") from exc
    except RecursionError:
        raise StructuredOutputError("Structured output nesting is too deep") from None
    pending: list[tuple[Any, int]] = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > STRUCTURED_OUTPUT_MAX_DEPTH:
            raise StructuredOutputError("Structured output nesting is too deep")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    try:
        # An empty registry has no retrieval callback. Schema validation must
        # never make HTTP requests or read file URLs, even after dialect changes.
        Draft202012Validator(schema, registry=Registry()).validate(value)
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path) or "$"
        raise StructuredOutputError(
            f"Structured output violates the schema at {path}: {exc.message}"
        ) from exc
    except Unresolvable:
        raise StructuredOutputError("JSON Schema reference cannot be resolved locally") from None
    except RecursionError:
        raise StructuredOutputError("JSON Schema validation exceeded its recursion limit") from None
    return value


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
        return {json_type(value) for value in enum_values}
    if "const" in schema:
        return {json_type(schema["const"])}
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


def _intersect_json_types(left: set[str], right: set[str]) -> set[str]:
    intersection = left & right
    if "number" in left and "integer" in right:
        intersection.add("integer")
    if "integer" in left and "number" in right:
        intersection.add("integer")
    return intersection
