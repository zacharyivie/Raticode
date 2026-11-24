"""The layout of Rem's chat decision prompt: what rarely changes first, the clock last.

A provider can read a prompt back from cache only while it starts with the same
bytes as an earlier one. Rem's chat prompt is one JSON object of records, written
with its keys in three groups, each sent as its own block (PromptBlocks):

1. records that change only when the user changes them (projects, decisions, memories)
2. the state of the work (questions waiting, the board, tasks, the desktop)
3. this request (recalled context, chat history, self-work's clock, the request, the time)

The turn's own rules follow the object. Consecutive replies then share their leading
bytes while those records stay the same, and a lookup or repair round, which adds
blocks after these, re-sends the earlier prompt unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from gofer.core.turn_context import payload_sizes
from gofer.ui.rem_home_provider import PromptBlocks
from gofer.ui.rem_home_store import encode

# Desktop context that changes from call to call, written after the rest of it.
WORKSPACE_LAST = ("running_threads", "knowledge", "at")


class InOrder(dict[str, Any]):
    """An object whose keys are written in this order; its values are still sorted."""


def _value(value: Any) -> str:
    if isinstance(value, InOrder):
        return "{" + ", ".join(_pair(key, item) for key, item in value.items()) + "}"
    return encode(value)


def _pair(key: str, value: Any) -> str:
    return f"{json.dumps(key, ensure_ascii=False)}: {_value(value)}"


def encode_blocks(*groups: Mapping[str, Any]) -> list[str]:
    """One JSON object holding every group's keys in order, split where a group ends.

    The blocks joined with nothing between them are the object's text, written with
    the same separators and escaping as encode(). Empty groups add no block.
    """
    pairs = [[_pair(key, value) for key, value in group.items()] for group in groups]
    pairs = [group for group in pairs if group]
    if not pairs:
        return ["{}"]
    blocks = [", ".join(group) for group in pairs]
    blocks = [block + ", " for block in blocks[:-1]] + [blocks[-1]]
    blocks[0] = "{" + blocks[0]
    blocks[-1] += "}"
    return blocks


def ordered_workspace(workspace: Mapping[str, Any]) -> InOrder:
    """The desktop context with its steady parts first and the clock last."""
    steady = sorted(key for key in workspace if key not in WORKSPACE_LAST)
    return InOrder((key, workspace[key]) for key in (*steady, *WORKSPACE_LAST) if key in workspace)


def reply_prompt(
    stable: Mapping[str, Any],
    work: Mapping[str, Any],
    turn: Mapping[str, Any],
    rules: str = "",
) -> PromptBlocks:
    """The chat decision prompt: the three record groups, then the turn's own rules."""
    blocks = encode_blocks(stable, work, turn)
    if rules:
        blocks.append("\n" + rules)
    return PromptBlocks(blocks)


def measured_reply_prompt(
    stable: Mapping[str, Any],
    work: Mapping[str, Any],
    turn: Mapping[str, Any],
    rules: str = "",
) -> tuple[PromptBlocks, dict[str, int]]:
    """reply_prompt and each part's size, as turn_context.measured_prompt measures them.

    The records are the same as one sorted object's, only in another order, so their
    sizes still add up to the prompt's length.
    """
    prompt = reply_prompt(stable, work, turn, rules)
    sizes = {
        "turn_rules": len("\n" + rules) if rules else 0,
        **payload_sizes({**stable, **work, **turn}, encode),
    }
    return prompt, sizes
