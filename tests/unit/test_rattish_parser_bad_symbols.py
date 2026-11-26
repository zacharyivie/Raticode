"""A lone '=' or '!' in a route condition and an empty '-' list item get a diagnostic.

These once hung the condition tokenizer forever or tripped an internal assert,
so the editor's analysis returned a 500 instead of showing a problem.
"""

from __future__ import annotations

import pytest

from gofer.rattish.diagnostics import RattishParseError
from gofer.rattish.formatter import format_rattish
from gofer.rattish.parser import parse_rattish, parse_rattish_recovering
from tests.helpers.time_limit import finishes_within

_ROUTED = """Rattish: 1
Workflow:
  name: demo
Node t:
  type: bash-command
  command: echo hi
  to:
    - u when {condition}
Node u:
  type: bash-command
  command: echo u
  needs: t
"""

_HTTP = """Rattish: 1
Workflow:
  name: demo
Node fetch:
  type: http-request
  url: https://example.com
  expected-statuses:
    -
"""

# (source, 1-based line, 1-based column, message fragment, suggestions)
BAD_SOURCES: dict[str, tuple[str, int, int, str, tuple[str, ...]]] = {
    "lone-equals": (
        _ROUTED.format(condition="node.t.status = ok"),
        8,
        28,
        "use '==' to compare",
        ("==",),
    ),
    "lone-bang": (
        _ROUTED.format(condition="!node.t.ok"),
        8,
        14,
        "use 'not' to negate or '!=' to compare",
        ("not", "!="),
    ),
    "empty-tags-item": (
        _ROUTED.format(condition="node.t.ok").replace(
            "  name: demo\n", "  name: demo\n  tags:\n    -\n"
        ),
        5,
        5,
        "List item '-' needs a value.",
        (),
    ),
    "empty-expected-statuses-item": (_HTTP, 8, 5, "List item '-' needs a value.", ()),
}


@pytest.mark.parametrize("case", sorted(BAD_SOURCES))
def test_parse_rattish_reports_lone_symbols_and_empty_list_items(case: str) -> None:
    source, line, column, message, suggestions = BAD_SOURCES[case]

    with finishes_within(2), pytest.raises(RattishParseError) as raised:
        parse_rattish(source)

    [diagnostic] = raised.value.diagnostics
    assert message in diagnostic.message
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (line, column)
    assert diagnostic.span.end.column == column + 1
    assert diagnostic.suggestions == suggestions


@pytest.mark.parametrize("case", sorted(BAD_SOURCES))
def test_parse_rattish_recovering_reports_lone_symbols_and_empty_list_items(case: str) -> None:
    source, line, column, message, _suggestions = BAD_SOURCES[case]

    with finishes_within(2):
        result = parse_rattish_recovering(source)

    assert [
        (item.span.start.line, item.span.start.column)
        for item in result.diagnostics
        if message in item.message
    ] == [(line, column)]


@pytest.mark.parametrize("case", sorted(BAD_SOURCES))
def test_format_rattish_rejects_lone_symbols_and_empty_list_items(case: str) -> None:
    source, _line, _column, message, _suggestions = BAD_SOURCES[case]

    with finishes_within(2), pytest.raises(RattishParseError) as raised:
        format_rattish(source)

    assert any(message in item.message for item in raised.value.diagnostics)


@pytest.mark.parametrize(
    "condition",
    [
        'node.t.status == "success"',
        'node.t.status != "success"',
        "not node.t.ok",
        "node.t.count >= 2",
    ],
)
def test_two_character_operators_and_not_still_parse(condition: str) -> None:
    with finishes_within(2):
        parse_rattish(_ROUTED.format(condition=condition))
