"""Check MCP tools/call arguments against a tool's own inputSchema.

The stdio tool servers (Second Brain, reports, browser) refuse a mismatch as a
tool error the model can act on, instead of letting a wrong type reach the tool.
Values are never coerced: a JSON string where an array belongs is refused, not
parsed.
"""

from __future__ import annotations

import json
from typing import Any

MAX_PROBLEMS = 5

_TYPE_NOUNS = {
    "string": ("a string", "strings"),
    "integer": ("a whole number", "whole numbers"),
    "number": ("a number", "numbers"),
    "boolean": ("true or false", "true/false values"),
    "null": ("null", "nulls"),
    "array": ("an array", "arrays"),
    "object": ("an object", "objects"),
}


def argument_problems(schema: dict[str, Any], arguments: Any) -> list[str]:
    """Readable problems with `arguments`, or an empty list when they fit `schema`."""
    problems: list[str] = []
    _check(schema, arguments, "", problems)
    if not problems:
        problems.extend(_fallback_problems(schema, arguments))
    return problems[:MAX_PROBLEMS]


def _fallback_problems(schema: dict[str, Any], arguments: Any) -> list[str]:
    """Keywords the readable checks don't cover still get checked by jsonschema."""
    from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
    from jsonschema.exceptions import best_match  # type: ignore[import-untyped]

    error = best_match(Draft202012Validator(schema).iter_errors(arguments))
    if error is None:
        return []
    label = _label(list(error.absolute_path))
    return [f"{label}: {error.message}."]


def _check(schema: Any, value: Any, path: str, problems: list[str]) -> None:
    if not isinstance(schema, dict) or len(problems) >= MAX_PROBLEMS:
        return
    label = path or "the arguments"
    if not _type_matches(schema, value):
        message = f"{label} must be {describe(schema)}; got {_kind(value)}."
        if isinstance(value, str) and set(_types(schema)) & {"array", "object"}:
            message += " Send the value itself, not JSON text."
        elif _types(schema) == ["array"] and isinstance(schema.get("items"), dict):
            if _type_matches(schema["items"], value):
                message += " Wrap a single item in an array."
        problems.append(message)
        return
    if "enum" in schema and not any(_same(value, option) for option in schema["enum"]):
        options = ", ".join(_short(option) for option in schema["enum"])
        problems.append(f"{label} must be one of {options}; got {_short(value)}.")
        return
    if _is_number(value):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{label} must be at least {schema['minimum']}; got {value}.")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{label} must be at most {schema['maximum']}; got {value}.")
    if isinstance(value, list):
        _check_array(schema, value, path, problems)
    if isinstance(value, dict):
        _check_object(schema, value, path, problems)


def _check_array(schema: dict[str, Any], value: list[Any], path: str, problems: list[str]) -> None:
    label = path or "the arguments"
    items = schema.get("items")
    if not isinstance(items, dict):
        return
    for index, item in enumerate(value):
        if not _type_matches(items, item):
            # One message for the whole array reads better than one per item.
            problems.append(
                f"{label} must be {describe(schema)}; item {index + 1} is {_kind(item)}."
            )
            return
    for index, item in enumerate(value):
        _check(items, item, f"{label}[{index}]", problems)


def _check_object(
    schema: dict[str, Any], value: dict[str, Any], path: str, problems: list[str]
) -> None:
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    for key in schema.get("required") or []:
        if key not in value:
            where = f"{path}.{key}" if path else key
            problems.append(f"{where} is required: pass {describe(properties.get(key, {}))}.")
    extra = [key for key in value if key not in properties]
    additional = schema.get("additionalProperties", True)
    if extra and additional is False:
        names = ", ".join(sorted(extra))
        allowed = ", ".join(properties) or "nothing"
        if path:
            problems.append(f"{path} has unknown keys {names}; it takes {allowed}.")
        else:
            problems.append(f"Unknown arguments {names}; this tool takes {allowed}.")
    for key, item in value.items():
        child = properties.get(key, additional if isinstance(additional, dict) else None)
        _check(child, item, f"{path}.{key}" if path else key, problems)


def describe(schema: Any, plural: bool = False) -> str:
    """A short noun phrase for the values `schema` accepts."""
    if not isinstance(schema, dict):
        return "values" if plural else "a value"
    if "enum" in schema and not plural:
        return "one of " + ", ".join(_short(option) for option in schema["enum"])
    types = _types(schema)
    if not types:
        return "values" if plural else "a value"
    phrases = [_describe_type(schema, kind, plural) for kind in types]
    return " or ".join(phrases)


def _describe_type(schema: dict[str, Any], kind: str, plural: bool) -> str:
    singular, many = _TYPE_NOUNS.get(kind, (f"a {kind}", f"{kind} values"))
    noun = many if plural else singular
    if kind == "array" and isinstance(schema.get("items"), dict):
        return f"{noun} of {describe(schema['items'], plural=True)}"
    if kind == "object":
        properties = schema.get("properties")
        if isinstance(properties, dict) and properties:
            phrase = f"{noun} with {_join(list(properties))}"
            required = [key for key in schema.get("required") or [] if key in properties]
            if required and len(required) < len(properties):
                phrase += f" ({_join(required)} required)"
            return phrase
    if kind in {"integer", "number"}:
        low, high = schema.get("minimum"), schema.get("maximum")
        if low is not None and high is not None:
            return f"{noun} from {low} to {high}"
        if low is not None:
            return f"{noun} of at least {low}"
        if high is not None:
            return f"{noun} of at most {high}"
    return noun


def _types(schema: dict[str, Any]) -> list[str]:
    kind = schema.get("type")
    if isinstance(kind, str):
        return [kind]
    if isinstance(kind, list):
        return [item for item in kind if isinstance(item, str)]
    return []


def _type_matches(schema: dict[str, Any], value: Any) -> bool:
    types = _types(schema)
    return not types or any(_is_type(value, kind) for kind in types)


def _is_type(value: Any, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return _is_number(value)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "null":
        return value is None
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    return True


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _same(value: Any, option: Any) -> bool:
    # JSON true is not 1, even though Python says True == 1.
    return value == option and isinstance(value, bool) == isinstance(option, bool)


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int):
        return "a whole number"
    if isinstance(value, float):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 60 else text[:57] + "..."


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _label(path: list[Any]) -> str:
    label = ""
    for part in path:
        if isinstance(part, int):
            label += f"[{part}]"
        else:
            label += f".{part}" if label else str(part)
    return label or "the arguments"
