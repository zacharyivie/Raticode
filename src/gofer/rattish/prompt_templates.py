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
    """Render a Rattish template, preserving an exact placeholder's native value.

    Only the author's template is checked for delimiters. Values go in as they are, in one
    pass, and are never rendered again: a value may contain ``{{`` or ``}}``, and a value
    such as ``{{other}}`` stays text instead of pulling in another local.
    """
    parse_prompt_template(template)
    canonical_values = {name.lower(): value for name, value in values.items()}
    protected = template.replace("{{{{", _OPEN_SENTINEL).replace("}}}}", _CLOSE_SENTINEL)

    # Only a template that is one placeholder keeps the native value. fullmatch alone would
    # read "{{a}}: {{b}}" as one placeholder named "a}}: {{b".
    matches = list(_PLACEHOLDER.finditer(protected))
    if len(matches) == 1 and matches[0].span() == (0, len(protected)):
        placeholder = _parse_expression(matches[0].group(1).strip())
        return _placeholder_value(placeholder, canonical_values)

    # Escapes are restored in the author's text only, so a value can't turn into a delimiter.
    parts: list[str] = []
    cursor = 0
    for match in matches:
        parts.append(_unprotect(protected[cursor : match.start()]))
        placeholder = _parse_expression(match.group(1).strip())
        value = _placeholder_value(placeholder, canonical_values)
        if isinstance(value, str):
            parts.append(value)
        else:
            parts.append(json.dumps(value, sort_keys=True, separators=(",", ":")))
        cursor = match.end()
    parts.append(_unprotect(protected[cursor:]))
    return "".join(parts)


def render_prompt_template(template: str, values: Mapping[str, Any]) -> str:
    """Render a text-only Rattish template."""
    rendered = render_template_value(template, values)
    if isinstance(rendered, str):
        return rendered
    return json.dumps(rendered, sort_keys=True, separators=(",", ":"))


def _unprotect(text: str) -> str:
    return text.replace(_OPEN_SENTINEL, "{{").replace(_CLOSE_SENTINEL, "}}")


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
