"""Reading a self-work worker's JSON answer block one entry at a time.

Research proposals, backlog rankings and guardrail retro proposals all end a worker's
answer with one fenced JSON block holding a list of entries. A slightly-off block keeps
what is usable: over-long text is cut to its limit, and an entry whose required fields
can't be used is dropped, with the validation reason so the caller can list it.

Nothing else is changed quietly. A bad optional field falls back to its default, and a
list keeps only its usable items, but each such repair is returned with the entry so the
caller records it beside the dropped ones. A list a guard relies on, such as the decisions
a proposal contradicts, is ``required``: one that can't be read whole loses the entry
rather than reaching the board emptied. ``answer_block`` names the parser's own error when
no block can be read, so a repair request can say exactly what to fix.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

FENCED_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)

Entry = TypeVar("Entry", bound=BaseModel)


def answer_block(text: str, keys: Sequence[str], what: str) -> tuple[dict[str, Any] | None, str]:
    """The answer's block, or None and why it couldn't be read.

    The last fenced JSON object with any of ``keys`` counts; each of those present must be
    a list. ``keys[0]`` is the list the errors name and ``what`` says what the block holds.
    """
    blocks = FENCED_BLOCK.findall(text or "")
    if not blocks:
        return None, f"The answer has no fenced ```json block with the {what}."
    error = ""
    for raw in reversed(blocks):
        try:
            data = json.loads(raw)
        except ValueError as exc:
            error = error or f"The last ```json block isn't valid JSON: {exc}."
            continue
        if not isinstance(data, dict) or not set(keys) & data.keys():
            error = error or f'The last ```json block has no "{keys[0]}" list.'
            continue
        for key in keys:
            if not isinstance(data.get(key, []), list):
                return None, f'"{key}" in the ```json block is not a list.'
        return data, ""
    return None, error


def cut_text(value: Any, limit: int) -> Any:
    """Text cut to ``limit`` characters; any other value is left for validation to judge."""
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        value = "\n".join(v for v in value if v.strip())
    return value.strip()[:limit] if isinstance(value, str) else value


def _plural(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def cut_list(
    value: Any, entries: int, limit: int, *, field: str = "list", whole: bool = False
) -> tuple[Any, list[str]]:
    """A list of text with at most ``entries`` items, each within ``limit`` characters,
    and what was left out or shortened to get there.

    A single string counts as one item per line. An item that isn't text is left out, a
    long one is cut after its last sentence that fits (split_lead), and items past
    ``entries`` are left out. With ``whole`` nothing is left out or cut: an item that isn't
    text, an over-long one or one too many is kept for validation to refuse, because the
    caller can't use the list in part.
    """
    from gofer.ui.rem_home_models import split_lead

    if isinstance(value, str):
        value = [line for line in value.splitlines() if line.strip()]
    if not isinstance(value, list):
        return value, []
    items = [
        " ".join(str(v).split())
        if isinstance(v, str | int | float) and not isinstance(v, bool)
        else v
        for v in value
    ]
    items = [v for v in items if v != ""]
    if whole:
        return items, []
    notes: list[str] = []
    text = [v for v in items if isinstance(v, str)]
    if len(text) < len(items):
        notes.append(f"{field}: left out {_plural(len(items) - len(text), 'item')} that isn't text")
    long = sum(1 for v in text if len(v) > limit)
    if long:
        notes.append(f"{field}: cut {_plural(long, 'item')} to {limit} characters")
    if len(text) > entries:
        notes.append(f"{field}: kept the first {entries} of {len(text)} items")
    return [split_lead(v, limit)[0] for v in text[:entries]], notes


def error_text(error: Mapping[str, Any], noun: str = "entry") -> str:
    field = ".".join(str(part) for part in error.get("loc") or ()) or noun
    return f"{field}: {error.get('msg', 'invalid')}"


def read_entry(
    raw: Any,
    model: type[Entry],
    *,
    required: Sequence[str],
    text: Mapping[str, int] | None = None,
    lists: Mapping[str, tuple[int, int]] | None = None,
    noun: str = "entry",
) -> tuple[Entry | None, str, list[str]]:
    """One entry with over-long text cut to its limits, or None and why it was dropped,
    with what was repaired in a kept entry.

    ``text`` maps a field to its character limit and ``lists`` a list field to its entry
    count and per-entry limit. A ``required`` field that doesn't validate loses the entry,
    and a required list is read whole or not at all. Any other field that doesn't validate
    is left out and its default used, and a list keeps its usable items; the third value
    names each of those repairs ("objective: Input should be a valid string; used the
    default"), so a caller can record them. Text cut to its stated limit isn't listed.
    """
    if not isinstance(raw, dict):
        return None, f"{noun}: not a JSON object", []
    data = dict(raw)
    repairs: list[str] = []
    for field, limit in (text or {}).items():
        if field in data:
            data[field] = cut_text(data[field], limit)
    for field, (entries, limit) in (lists or {}).items():
        if field in data:
            data[field], notes = cut_list(
                data[field], entries, limit, field=field, whole=field in required
            )
            repairs.extend(notes)
    for _ in range(len(model.model_fields) + 1):
        try:
            return model.model_validate(data), "", repairs
        except ValidationError as exc:
            errors = exc.errors()
        fatal = [e for e in errors if (e.get("loc") or ("",))[0] in required]
        if fatal:
            return None, "; ".join(error_text(e, noun) for e in fatal), []
        for field in dict.fromkeys(str((e.get("loc") or ("",))[0]) for e in errors):
            if data.pop(field, None) is not None:
                first = next(e for e in errors if str((e.get("loc") or ("",))[0]) == field)
                repairs.append(f"{error_text(first, noun)}; used the default")
    return None, f"{noun}: could not be read", []


def label(raw: Any, field: str, limit: int = 160) -> str:
    """The text of ``field`` in a dropped entry, flattened and cut, for naming it."""
    value = raw.get(field) if isinstance(raw, dict) else None
    if isinstance(value, int | float) and not isinstance(value, bool):
        value = str(value)
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""
