"""Explain where a binding's reference stopped finding data, without showing any values.

A binding such as ``node.rows.output.name`` fails when a key is missing, a list is too
short or a step lands on text or a number. :func:`describe_miss` walks the compiled
reference again and says which selector missed and what was there instead: the key
names that do exist, the list's length or the value's JSON type. Key names are shown
with invisible characters escaped, so a ``"\\ufeffname"`` key read from a CSV file with
a byte-order mark doesn't look like ``"name"``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Keys listed in a message; the rest are counted.
MAX_LISTED_KEYS = 20
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
# Characters that print as nothing or as plain space: controls, formatting marks such as
# the byte-order mark and zero-width space, separators other than an ordinary space, and
# private or unassigned code points.
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cn", "Cs", "Zl", "Zp", "Zs"})


@dataclass(frozen=True, slots=True)
class BindingMiss:
    """Where a reference walk stopped and what it found there."""

    reference: str
    # The reference up to (not including) the selector that missed.
    location: str
    # The selector that missed: an escaped key name, or ``[n]`` for a list position.
    missing: str
    available_keys: tuple[str, ...] | None = None
    length: int | None = None
    json_type: str | None = None
    # Set when the first name missed: "workflow input" or "node output", for example.
    root_kind: str | None = None

    def message(self, binding_name: str) -> str:
        start = f"Binding {binding_name!r} can't read {self.reference}"
        keys = self.available_keys or ()
        if self.root_kind is not None:
            text = f'{start}: this run has no {self.root_kind} "{self.missing}".'
            if not keys:
                return text
            return f"{text} The {self.root_kind}s it has are {_listed(keys)}."
        if self.length is not None:
            items = "item" if self.length == 1 else "items"
            return (
                f"{start}: the list at {self.location} has {self.length} {items}, "
                f"so {self.missing} is past the end."
            )
        if self.json_type is not None:
            return (
                f"{start}: {self.location} is {_article(self.json_type)} {self.json_type}, "
                f"so it has no {_quoted_selector(self.missing)}."
            )
        if not keys:
            return (
                f"{start}: {self.location} is an empty object, "
                f"so it has no {_quoted_selector(self.missing)}."
            )
        return (
            f"{start}: {self.location} has no {_quoted_selector(self.missing)}. "
            f"Its keys are {_listed(keys)}."
        )

    def details(self) -> dict[str, Any]:
        details: dict[str, Any] = {"reference": self.reference, "missing": self.missing}
        if self.available_keys is not None:
            details["available_keys"] = list(self.available_keys[:MAX_LISTED_KEYS])
            if len(self.available_keys) > MAX_LISTED_KEYS:
                details["available_key_count"] = len(self.available_keys)
        if self.length is not None:
            details["length"] = self.length
        if self.json_type is not None:
            details["type"] = self.json_type
        return details


class BindingSourceMissingError(ValueError):
    """A binding's reference found no value; ``details`` names where and why."""

    def __init__(self, message: str, details: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.details = dict(details)


def visible_key(key: object) -> str:
    """Return ``key`` with invisible characters, quotes and backslashes escaped."""
    escaped: list[str] = []
    for char in str(key):
        if char in '"\\':
            escaped.append("\\" + char)
        elif char != " " and unicodedata.category(char) in _INVISIBLE_CATEGORIES:
            code = ord(char)
            escaped.append(f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}")
        else:
            escaped.append(char)
    return "".join(escaped)


def reference_text(reference: Mapping[str, Any]) -> str:
    """Return a compiled reference as authored, such as ``node.rows.output.name``."""
    return _prefix_text(reference) + "".join(
        _selector_text(selector) for selector in reference["path"]
    )


def describe_miss(
    reference: Mapping[str, Any],
    *,
    workflow_inputs: Mapping[str, Any],
    trigger_events: Sequence[Mapping[str, Any]],
    node_outputs: Mapping[str, Any],
    node_statuses: Mapping[str, str],
    node_errors: Mapping[str, Mapping[str, Any]],
) -> BindingMiss | None:
    """Walk ``reference`` again and return where it stopped, or None if it resolves."""
    authored = reference_text(reference)
    root = reference["root"]
    symbol = reference["symbol"]
    value: Any
    if root == "input":
        if symbol not in workflow_inputs:
            return BindingMiss(
                authored,
                "input",
                visible_key(symbol),
                available_keys=_key_names(workflow_inputs),
                root_kind="workflow input",
            )
        value = workflow_inputs[symbol]
    elif root == "trigger" and symbol == "events":
        value = list(trigger_events)
    elif root == "node" and reference["channel"] in {"output", "status", "error"}:
        channel = reference["channel"]
        found: Mapping[str, Any] = {
            "output": node_outputs,
            "status": node_statuses,
            "error": node_errors,
        }[channel]
        if symbol not in found:
            return BindingMiss(
                authored,
                "node",
                visible_key(symbol),
                available_keys=_key_names(found),
                root_kind=f"node {channel}",
            )
        value = found[symbol]
    else:
        return None
    location = _prefix_text(reference)
    for selector in reference["path"]:
        step = selector["value"]
        missing = f"[{step}]" if selector["kind"] == "index" else visible_key(step)
        try:
            value = value[step]
        except (KeyError, IndexError, TypeError):
            if isinstance(value, Mapping):
                return BindingMiss(authored, location, missing, available_keys=_key_names(value))
            if isinstance(value, Sequence) and not isinstance(value, str | bytes):
                if selector["kind"] == "index":
                    return BindingMiss(authored, location, missing, length=len(value))
            return BindingMiss(authored, location, missing, json_type=_json_type(value))
        location += _selector_text(selector)
    return None


def _prefix_text(reference: Mapping[str, Any]) -> str:
    root = str(reference["root"])
    symbol = reference["symbol"]
    if root == "node":
        return f"node.{symbol}.{reference['channel']}"
    if symbol is None:
        return root
    return root + _member_text(str(symbol))


def _selector_text(selector: Mapping[str, Any]) -> str:
    if selector["kind"] == "index":
        return f"[{selector['value']}]"
    return _member_text(str(selector["value"]))


def _member_text(name: str) -> str:
    if _IDENTIFIER.match(name):
        return f".{name}"
    return f'["{visible_key(name)}"]'


def _listed(keys: Sequence[str]) -> str:
    listed = ", ".join(f'"{key}"' for key in keys[:MAX_LISTED_KEYS])
    more = len(keys) - MAX_LISTED_KEYS
    return f"{listed} and {more} more" if more > 0 else listed


def _quoted_selector(missing: str) -> str:
    return missing if missing.startswith("[") else f'"{missing}"'


def _key_names(mapping: Mapping[Any, Any]) -> tuple[str, ...]:
    return tuple(visible_key(key) for key in mapping)


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence):
        return "array"
    return type(value).__name__


def _article(word: str) -> str:
    return "an" if word[:1] in "aeiou" else "a"
