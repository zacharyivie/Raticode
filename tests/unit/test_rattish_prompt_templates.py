"""Values bound into a Rattish template are inserted as they are."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.prompt_templates import (
    PromptTemplateError,
    render_prompt_template,
    render_template_value,
)
from gofer.rattish.workflow_runtime import execute_workflow

RATTISH_ROOT = Path(__file__).parents[2] / "rattish"


def test_structured_value_with_braces_renders_as_compact_json() -> None:
    assert render_prompt_template("Item: {{row}}", {"row": {"a": {"b": 1}}}) == (
        'Item: {"a":{"b":1}}'
    )


def test_value_containing_placeholder_text_comes_through_unchanged() -> None:
    assert render_prompt_template("Page: {{html}}", {"html": "<p>{{ name }}</p>"}) == (
        "Page: <p>{{ name }}</p>"
    )
    assert render_template_value("{{html}}", {"html": "<p>{{ name }}</p>"}) == ("<p>{{ name }}</p>")


def test_inserted_value_is_never_rendered_again() -> None:
    values = {"token": "{{other}}", "other": "secret-ish"}

    assert render_prompt_template("Got {{token}} and {{token}}", values) == (
        "Got {{other}} and {{other}}"
    )


def test_unbalanced_value_delimiters_are_kept() -> None:
    values = {"a": "}}", "b": "{{"}

    assert render_prompt_template("{{a}} then {{b}}", values) == "}} then {{"


def test_escapes_still_emit_literal_delimiters() -> None:
    assert render_prompt_template("{{{{name}}}} is {{name}}", {"name": "Ada"}) == (
        "{{name}} is Ada"
    )


def test_value_with_escape_sentinel_text_is_not_turned_into_delimiters() -> None:
    value = "\0RATTISH_OPEN\0x\0RATTISH_CLOSE\0"

    assert render_prompt_template("v={{v}}", {"v": value}) == f"v={value}"


@pytest.mark.parametrize("template", ["Hi {{name", "Hi name}}", "{{name}} }}"])
def test_authored_unmatched_delimiter_still_fails(template: str) -> None:
    with pytest.raises(PromptTemplateError, match="unmatched placeholder delimiter"):
        render_prompt_template(template, {"name": "Ada"})


@pytest.mark.anyio
async def test_prompt_file_writes_read_file_content_with_braces(tmp_path: Path) -> None:
    (tmp_path / "page.html").write_text("Hello {{ user.name }}!\n", encoding="utf-8")
    source = """Rattish: 1
Workflow:
  name: Template data with braces
Node page:
  type: read-file
  path: page.html
  to: render
Node render:
  type: prompt-file
  output-path: generated/out.md
  needs: page
  template: "Page: {{content}}"
  with:
    content: node.page.output.content
"""
    compiler = RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[
            RATTISH_ROOT / "contracts" / f"{name}.json" for name in ("read-file", "prompt-file")
        ],
    )
    ir: dict[str, Any] = compiler.compile(source, CompileContext("braces", tmp_path)).ir

    result = await execute_workflow(ir)

    assert result.outcome == "pass"
    assert (tmp_path / "generated" / "out.md").read_text(encoding="utf-8") == (
        "Page: Hello {{ user.name }}!\n"
    )
