"""JSON type names and equality for decoded JSON values (schema checks, route conditions)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

NUMBER_TYPES = frozenset({"integer", "number"})


def json_type(value: Any) -> str:
    """Name the JSON Schema type of a decoded JSON value.

    A Boolean is never a number here, though Python's bool is an int.
    """
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
    if isinstance(value, list | tuple):
        return "array"
    return "object"


def json_equal(left: Any, right: Any) -> bool:
    """Compare two JSON values by JSON rules: different types are never equal.

    Integers and numbers compare by value, so 1 equals 1.0, but true never equals 1.
    """
    left_type, right_type = json_type(left), json_type(right)
    if left_type in NUMBER_TYPES and right_type in NUMBER_TYPES:
        return bool(left == right)
    if left_type != right_type:
        return False
    if left_type == "array":
        return len(left) == len(right) and all(
            json_equal(item, other) for item, other in zip(left, right, strict=True)
        )
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(
            json_equal(left[key], right[key]) for key in left
        )
    return bool(left == right)
