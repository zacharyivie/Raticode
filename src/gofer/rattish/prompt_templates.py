"""Strict placeholder parsing and rendering for Rattish prompt templates."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")
_PLACEHOLDER = re.compile(r"\{\{\s*(.*?)\s*\}\}")
_OPEN_SENTINEL = "\0RATTISH_OPEN\0"
_CLOSE_SENTINEL = "\0RATTISH_CLOSE\0"


class PromptTemplateError(ValueError):
    """Raised when a Rattish prompt template is malformed or cannot resolve."""


@dataclass(frozen=True, slots=True)
class PromptPlaceholder:
    source: str
    name: str
    selectors: tuple[str | int, ...]


def parse_prompt_template(template: str) -> tuple[PromptPlaceholder, ...]:
    protected = template.replace("{{{{", _OPEN_SENTINEL).replace("}}}}", _CLOSE_SENTINEL)
    placeholders: list[PromptPlaceholder] = []
    for match in _PLACEHOLDER.finditer(protected):
        expression = match.group(1).strip()
        placeholders.append(_parse_expression(expression))
    remainder = _PLACEHOLDER.sub("", protected)
    if "{{" in remainder or "}}" in remainder:
        raise PromptTemplateError("Prompt contains an unmatched placeholder delimiter.")
    return tuple(placeholders)


def validate_prompt_template(template: str, names: set[str]) -> tuple[PromptPlaceholder, ...]:
    placeholders = parse_prompt_template(template)
    for placeholder in placeholders:
        if placeholder.name.lower() not in names:
            raise PromptTemplateError(
                f"Prompt placeholder {placeholder.name!r} does not name a bound input."
            )
    return placeholders


def render_template_value(template: str, values: Mapping[str, Any]) -> Any:
    """Render a Rattish template, preserving an exact placeholder's native value."""
    canonical_values = {name.lower(): value for name, value in values.items()}
    protected = template.replace("{{{{", _OPEN_SENTINEL).replace("}}}}", _CLOSE_SENTINEL)

    exact = _PLACEHOLDER.fullmatch(protected)
    if exact is not None:
        placeholder = _parse_expression(exact.group(1).strip())
        return _placeholder_value(placeholder, canonical_values)

    def replace(match: re.Match[str]) -> str:
        placeholder = _parse_expression(match.group(1).strip())
        value = _placeholder_value(placeholder, canonical_values)
        if isinstance(value, str):
            return value
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    rendered = _PLACEHOLDER.sub(replace, protected)
    if "{{" in rendered or "}}" in rendered:
        raise PromptTemplateError("Prompt contains an unmatched placeholder delimiter.")
    return rendered.replace(_OPEN_SENTINEL, "{{").replace(_CLOSE_SENTINEL, "}}")


def render_prompt_template(template: str, values: Mapping[str, Any]) -> str:
    """Render a text-only Rattish template."""
    rendered = render_template_value(template, values)
    if isinstance(rendered, str):
        return rendered
    return json.dumps(rendered, sort_keys=True, separators=(",", ":"))


def _placeholder_value(placeholder: PromptPlaceholder, canonical_values: Mapping[str, Any]) -> Any:
    try:
        value = canonical_values[placeholder.name.lower()]
        for selector in placeholder.selectors:
            if isinstance(selector, int):
                if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                    raise TypeError(selector)
                value = value[selector]
            else:
                if not isinstance(value, Mapping):
                    raise TypeError(selector)
                value = value[selector]
    except (KeyError, IndexError, TypeError) as exc:
        raise PromptTemplateError(
            f"Prompt placeholder {placeholder.source!r} could not be resolved."
        ) from exc
    return value


def _parse_expression(expression: str) -> PromptPlaceholder:
    match = _IDENTIFIER.match(expression)
    if match is None:
        raise PromptTemplateError(f"Invalid prompt placeholder {expression!r}.")
    name = match.group(0)
    cursor = match.end()
    selectors: list[str | int] = []
    while cursor < len(expression):
        if expression[cursor] == ".":
            member = _IDENTIFIER.match(expression, cursor + 1)
            if member is None:
                raise PromptTemplateError(f"Invalid prompt placeholder {expression!r}.")
            selectors.append(member.group(0))
            cursor = member.end()
            continue
        if expression[cursor] == "[":
            close = expression.find("]", cursor + 1)
            if close < 0:
                raise PromptTemplateError(f"Invalid prompt placeholder {expression!r}.")
            raw = expression[cursor + 1 : close]
            if raw.isdigit():
                selectors.append(int(raw))
            else:
                try:
                    member = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise PromptTemplateError(
                        f"Invalid prompt placeholder {expression!r}."
                    ) from exc
                if not isinstance(member, str):
                    raise PromptTemplateError(f"Invalid prompt placeholder {expression!r}.")
                selectors.append(member)
            cursor = close + 1
            continue
        raise PromptTemplateError(f"Invalid prompt placeholder {expression!r}.")
    return PromptPlaceholder(expression, name, tuple(selectors))
