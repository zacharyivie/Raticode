from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gofer.rattish.binding_misses import BindingSourceMissingError
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.runtime import _resolve_bindings, execute_node

RATTISH_ROOT = Path(__file__).parents[2] / "rattish"


def _node_with_binding(reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "bindings": [
            {
                "name": "stdin",
                "source": {"kind": "reference", "reference": reference},
                "default": {"present": False},
                "delivery": {"kind": "stdin"},
            }
        ]
    }


def _output_reference(node: str, *path: dict[str, Any]) -> dict[str, Any]:
    return {
        "root": "node",
        "symbol": node,
        "channel": "output",
        "path": list(path),
        "optional": False,
        "schema": {},
    }


def _member(name: str) -> dict[str, Any]:
    return {"kind": "member", "value": name, "case_sensitive": True}


def _resolve(node: dict[str, Any], **node_outputs: Any) -> BindingSourceMissingError:
    context = SimpleNamespace(
        data_dir=None,
        workflow_inputs={},
        trigger_events=(),
        node_outputs=node_outputs,
        node_statuses={},
        node_errors={},
    )
    with pytest.raises(BindingSourceMissingError) as raised:
        _resolve_bindings(node, context)  # type: ignore[arg-type]
    return raised.value


def test_missing_key_names_reference_missing_key_and_escaped_existing_keys() -> None:
    node = _node_with_binding(_output_reference("rows", _member("name")))

    error = _resolve(node, rows={"﻿name": "Ana", "email": "a@x"})

    message = str(error)
    assert "node.rows.output.name" in message
    assert '"name"' in message
    assert '"\\ufeffname"' in message
    assert '"email"' in message
    assert "﻿" not in message
    assert "Ana" not in message
    assert "a@x" not in message
    assert error.details == {
        "reference": "node.rows.output.name",
        "missing": "name",
        "available_keys": ["\\ufeffname", "email"],
    }


def test_missing_key_lists_at_most_twenty_keys() -> None:
    node = _node_with_binding(_output_reference("rows", _member("name")))
    row = {f"column_{index:02d}": index for index in range(25)}

    error = _resolve(node, rows=row)

    assert '"column_19" and 5 more' in str(error)
    assert "column_20" not in str(error)
    assert len(error.details["available_keys"]) == 20
    assert error.details["available_key_count"] == 25


def test_step_into_text_gives_its_json_type_not_its_value() -> None:
    node = _node_with_binding(_output_reference("rows", _member("stdout"), _member("name")))

    error = _resolve(node, rows={"stdout": "secret-ish text"})

    assert "node.rows.output.stdout is a string" in str(error)
    assert "secret-ish" not in str(error)
    assert error.details == {
        "reference": "node.rows.output.stdout.name",
        "missing": "name",
        "type": "string",
    }


def test_node_without_output_names_the_nodes_that_have_one() -> None:
    node = _node_with_binding(_output_reference("rows", _member("name")))

    error = _resolve(node, fetch={"body": "hidden"})

    assert 'this run has no node output "rows"' in str(error)
    assert '"fetch"' in str(error)
    assert "hidden" not in str(error)
    assert error.details["available_keys"] == ["fetch"]


@pytest.mark.anyio
async def test_index_past_the_end_states_the_list_length(tmp_path: Path) -> None:
    source = """Rattish: 1

Workflow:
  name: Index slice
  inputs:
    lines:
      schema: {"type": "array", "items": {"type": "string"}}
      required: true

Node consume:
  type: bash-command
  command: cat
  with:
    stdin: input.lines[3]
"""
    rattish_compiler = RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[RATTISH_ROOT / "contracts" / "bash-command.json"],
    )
    compiled = rattish_compiler.compile(source, CompileContext("index-slice", tmp_path))

    execution = await execute_node(
        compiled.ir, "consume", workflow_inputs={"lines": ["first", "second"]}
    )

    assert execution.outcome == "failure"
    assert execution.error is not None
    assert execution.error.message == (
        "Binding 'stdin' can't read input.lines[3]: the list at input.lines "
        "has 2 items, so [3] is past the end."
    )
    assert "first" not in execution.error.message
    assert execution.error.details == {
        "exception": "ValueError",
        "reference": "input.lines[3]",
        "missing": "[3]",
        "length": 2,
    }
