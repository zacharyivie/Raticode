"""Compatibility checks for the restricted Rattish JSON Schema profile."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

_PROFILE_KEYWORDS = {
    "$schema",
    "additionalProperties",
    "anyOf",
    "const",
    "description",
    "enum",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "items",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "pattern",
    "properties",
    "required",
    "title",
    "type",
}


def schema_accepts_schema(destination: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
    """Return whether every value described by source is accepted by destination."""
    if not _has_constraints(destination):
        return True
    source_any_of = source.get("anyOf")
    if isinstance(source_any_of, list):
        return bool(source_any_of) and all(
            isinstance(branch, Mapping) and schema_accepts_schema(destination, branch)
            for branch in source_any_of
        )
    destination_any_of = destination.get("anyOf")
    if isinstance(destination_any_of, list):
        return any(
            isinstance(branch, Mapping) and schema_accepts_schema(branch, source)
            for branch in destination_any_of
        )
    if "const" in source:
        return bool(Draft202012Validator(destination).is_valid(source["const"]))
    source_enum = source.get("enum")
    if isinstance(source_enum, list):
        return all(Draft202012Validator(destination).is_valid(value) for value in source_enum)

    destination_types = _types(destination)
    source_types = _types(source)
    if destination_types and source_types:
        for source_type in source_types:
            if source_type == "integer" and "number" in destination_types:
                continue
            if source_type not in destination_types:
                return False
    elif destination_types and not source_types:
        return False

    if "enum" in destination:
        return False
    if "const" in destination:
        return False

    if "object" in source_types and "object" in destination_types:
        if not _object_compatible(destination, source):
            return False
    if "array" in source_types and "array" in destination_types:
        destination_items = destination.get("items")
        source_items = source.get("items")
        if isinstance(destination_items, Mapping):
            if not isinstance(source_items, Mapping):
                return False
            if not schema_accepts_schema(destination_items, source_items):
                return False
    return _bounds_compatible(destination, source)


def instance_matches_schema(schema: Mapping[str, Any], value: Any) -> bool:
    return bool(Draft202012Validator(schema).is_valid(value))


def unsupported_profile_paths(
    schema: Mapping[str, Any], path: tuple[str | int, ...] = ()
) -> list[tuple[str | int, ...]]:
    """Return keyword paths outside Rattish Schema Profile 1."""
    unsupported = [path + (key,) for key in schema if key not in _PROFILE_KEYWORDS]
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for name, child in properties.items():
            if isinstance(child, Mapping):
                unsupported.extend(
                    unsupported_profile_paths(child, path + ("properties", str(name)))
                )
    items = schema.get("items")
    if isinstance(items, Mapping):
        unsupported.extend(unsupported_profile_paths(items, path + ("items",)))
    additional = schema.get("additionalProperties")
    if isinstance(additional, Mapping):
        unsupported.extend(unsupported_profile_paths(additional, path + ("additionalProperties",)))
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        for index, child in enumerate(any_of):
            if isinstance(child, Mapping):
                unsupported.extend(unsupported_profile_paths(child, path + ("anyOf", index)))
    return unsupported


def _has_constraints(schema: Mapping[str, Any]) -> bool:
    return any(key != "$schema" for key in schema)


def _types(schema: Mapping[str, Any]) -> set[str]:
    raw = schema.get("type")
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return set(raw)
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        result: set[str] = set()
        for branch in any_of:
            if isinstance(branch, Mapping):
                result.update(_types(branch))
        return result
    return set()


def _object_compatible(destination: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
    destination_properties = destination.get("properties", {})
    source_properties = source.get("properties", {})
    if not isinstance(destination_properties, Mapping) or not isinstance(
        source_properties, Mapping
    ):
        return False
    source_required = set(source.get("required", []))
    for name in destination.get("required", []):
        if name not in source_required:
            return False
    for name, source_property in source_properties.items():
        if name in destination_properties:
            destination_property = destination_properties[name]
            if isinstance(source_property, Mapping) and isinstance(destination_property, Mapping):
                if not schema_accepts_schema(destination_property, source_property):
                    return False
        elif destination.get("additionalProperties") is False:
            return False
        elif isinstance(destination.get("additionalProperties"), Mapping):
            additional = destination["additionalProperties"]
            if isinstance(source_property, Mapping) and not schema_accepts_schema(
                additional, source_property
            ):
                return False
    source_additional = source.get("additionalProperties", True)
    destination_additional = destination.get("additionalProperties", True)
    if source_additional is not False:
        if destination_additional is False:
            return False
        if isinstance(destination_additional, Mapping):
            if source_additional is True or not isinstance(source_additional, Mapping):
                return False
            if not schema_accepts_schema(destination_additional, source_additional):
                return False
        for name, destination_property in destination_properties.items():
            if name in source_properties or not isinstance(destination_property, Mapping):
                continue
            if source_additional is True or not isinstance(source_additional, Mapping):
                return False
            if not schema_accepts_schema(destination_property, source_additional):
                return False
    return True


def _bounds_compatible(destination: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
    destination_lower = _numeric_bound(destination, lower=True)
    source_lower = _numeric_bound(source, lower=True)
    if destination_lower is not None:
        if source_lower is None or source_lower[0] < destination_lower[0]:
            return False
        if source_lower[0] == destination_lower[0] and destination_lower[1] and not source_lower[1]:
            return False
    destination_upper = _numeric_bound(destination, lower=False)
    source_upper = _numeric_bound(source, lower=False)
    if destination_upper is not None:
        if source_upper is None or source_upper[0] > destination_upper[0]:
            return False
        if source_upper[0] == destination_upper[0] and destination_upper[1] and not source_upper[1]:
            return False
    for minimum in ("minLength", "minItems"):
        if minimum in destination and source.get(minimum, -1) < destination[minimum]:
            return False
    for maximum in ("maxLength", "maxItems"):
        if maximum in destination and source.get(maximum, float("inf")) > destination[maximum]:
            return False
    if "pattern" in destination and source.get("pattern") != destination["pattern"]:
        return False
    if "format" in destination and source.get("format") != destination["format"]:
        return False
    return True


def _numeric_bound(schema: Mapping[str, Any], *, lower: bool) -> tuple[float, bool] | None:
    inclusive = "minimum" if lower else "maximum"
    exclusive = "exclusiveMinimum" if lower else "exclusiveMaximum"
    candidates: list[tuple[float, bool]] = []
    if isinstance(schema.get(inclusive), (int, float)):
        candidates.append((float(schema[inclusive]), False))
    if isinstance(schema.get(exclusive), (int, float)):
        candidates.append((float(schema[exclusive]), True))
    if not candidates:
        return None
    if lower:
        return max(candidates, key=lambda item: (item[0], item[1]))
    return min(candidates, key=lambda item: (item[0], not item[1]))
