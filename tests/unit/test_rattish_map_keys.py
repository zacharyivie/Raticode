"""Block-map keys: case-sensitive names that Rattish would lowercase, and repeated keys."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from gofer.cli.main import app
from gofer.rattish.compiler import CompileContext, CompileResult, ProviderContract, RattishCompiler
from gofer.rattish.diagnostics import RattishCompileError
from gofer.rattish.parser import parse_rattish_recovering

RATTISH_ROOT = Path(__file__).parents[2] / "rattish"
NODE_TYPES = ("agent", "bash-command", "shell-script", "python-script", "http-request")
runner = CliRunner()

# The fields each node needs besides the case-sensitive map.
REQUIRED = {
    "agent": "  provider: codex\n  prompt: Say hi.\n",
    "bash-command": '  command: "echo hi"\n',
    "shell-script": '  script-path: "run.sh"\n',
    "python-script": '  script-path: "run.py"\n',
    "http-request": '  url: "https://example.com/items"\n',
}
MAP_FIELD = {node_type: "env" for node_type in NODE_TYPES} | {"http-request": "params"}


def compiler() -> RattishCompiler:
    return RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[RATTISH_ROOT / "contracts" / f"{name}.json" for name in NODE_TYPES],
    )


def context(project: Path) -> CompileContext:
    codex = ProviderContract(
        provider_id="codex",
        version=1,
        default_model="gpt-5.6-sol",
        default_effort="high",
        fingerprint="sha256:" + "3" * 64,
    )
    return CompileContext("map-keys", project, provider_contracts={"codex": codex})


def node_source(node_type: str, fields: str) -> str:
    return (
        f"Rattish: 1\nWorkflow:\n  name: Map keys\nNode step:\n  type: {node_type}\n"
        f"{REQUIRED[node_type]}{fields}"
    )


def line_of(node_type: str, fields: str, text: str) -> int:
    """The 1-based line of the first source line that starts with text, after indent."""
    lines = node_source(node_type, fields).splitlines()
    return next(index for index, line in enumerate(lines, 1) if line.strip().startswith(text))


def compile_node(node_type: str, fields: str, project: Path) -> CompileResult:
    (project / "run.sh").write_text("echo hi\n", encoding="utf-8")
    (project / "run.py").write_text("print('hi')\n", encoding="utf-8")
    return compiler().compile(node_source(node_type, fields), context(project))


def duplicate_errors(node_type: str, fields: str, project: Path) -> list[dict[str, Any]]:
    with pytest.raises(RattishCompileError) as caught:
        compile_node(node_type, fields, project)
    return [
        item.to_json()
        for item in caught.value.diagnostics
        if item.code == "RATTISH_DUPLICATE_MAP_KEY"
    ]


@pytest.mark.parametrize("node_type", NODE_TYPES)
def test_unquoted_mixed_case_key_warns_with_both_spellings(node_type: str, tmp_path: Path) -> None:
    field = MAP_FIELD[node_type]
    fields = f'  {field}:\n    userId: "7"\n    plain: "1"\n'
    result = compile_node(node_type, fields, tmp_path)

    [warning] = [item for item in result.diagnostics if item.code == "RATTISH_MAP_KEY_CHANGED"]
    assert warning.severity == "warning"
    assert "'userId'" in warning.message and "'userid'" in warning.message
    assert warning.details == {
        "node": "step",
        "field": field,
        "key": "userId",
        "read_as": "userid",
    }
    assert warning.span.start.line == line_of(node_type, fields, "userId")
    assert warning.suggestions == (
        f'Quote the key ("userId": "7") or write {field} as a JSON object.',
    )
    # The lowercasing rule itself is unchanged, so compiled workflows keep their fingerprint.
    assert result.ir["nodes"][0]["configuration"][field] == {"userid": "7", "plain": "1"}


@pytest.mark.parametrize("node_type", NODE_TYPES)
def test_quoted_key_or_json_keeps_case_without_a_warning(node_type: str, tmp_path: Path) -> None:
    field = MAP_FIELD[node_type]
    quoted = compile_node(node_type, f'  {field}:\n    "DEBUG": "1"\n', tmp_path)
    as_json = compile_node(node_type, f'  {field}: {{"DEBUG": "1"}}\n', tmp_path)

    for result in (quoted, as_json):
        assert not [item for item in result.diagnostics if item.code.endswith("KEY_CHANGED")]
        assert result.ir["nodes"][0]["configuration"][field] == {"DEBUG": "1"}


def test_http_header_names_are_left_alone(tmp_path: Path) -> None:
    result = compile_node("http-request", '  headers:\n    Accept: "text/plain"\n', tmp_path)

    assert not [item for item in result.diagnostics if item.code.endswith("KEY_CHANGED")]


@pytest.mark.parametrize("node_type", NODE_TYPES)
def test_keys_equal_once_lowercased_are_an_error_on_the_second_entry(
    node_type: str, tmp_path: Path
) -> None:
    field = MAP_FIELD[node_type]
    fields = f'  {field}:\n    Path: "/a"\n    other: "x"\n    path: "/b"\n'
    first = line_of(node_type, fields, "Path")
    [error] = duplicate_errors(node_type, fields, tmp_path)

    assert error["severity"] == "error"
    assert error["span"]["start"]["line"] == line_of(node_type, fields, "path")
    assert error["details"] == {"key": "path", "first_line": first}
    assert f"line {first}" in error["message"] and "Path" in error["message"]
    assert error["related"][0]["span"]["start"]["line"] == first


def test_http_headers_that_differ_only_by_case_are_an_error(tmp_path: Path) -> None:
    fields = '  headers:\n    Accept: "a"\n    accept: "b"\n'
    [error] = duplicate_errors("http-request", fields, tmp_path)

    assert error["span"]["start"]["line"] == line_of("http-request", fields, "accept")
    assert error["details"]["first_line"] == line_of("http-request", fields, "Accept")


def test_quoted_keys_compare_exactly(tmp_path: Path) -> None:
    [repeated] = duplicate_errors(
        "bash-command", '  env:\n    "Path": "/a"\n    "Path": "/b"\n', tmp_path
    )
    assert repeated["details"] == {"key": "Path", "first_line": 8}
    [mixed] = duplicate_errors(
        "bash-command", '  env:\n    "path": "/a"\n    PATH: "/b"\n', tmp_path
    )
    assert mixed["details"] == {"key": "path", "first_line": 8}

    distinct = compile_node("bash-command", '  env:\n    "Path": "/a"\n    path: "/b"\n', tmp_path)
    assert distinct.ir["nodes"][0]["configuration"]["env"] == {"Path": "/a", "path": "/b"}


def test_nested_map_repeats_are_found(tmp_path: Path) -> None:
    source = (
        "Rattish: 1\nWorkflow:\n  name: Map keys\n  inputs:\n    topic:\n"
        "      schema:\n        type: string\n        TYPE: integer\n"
    )
    with pytest.raises(RattishCompileError) as caught:
        compiler().compile(source, context(tmp_path))

    [error] = [
        item for item in caught.value.diagnostics if item.code == "RATTISH_DUPLICATE_MAP_KEY"
    ]
    assert error.span.start.line == 8


def test_workflow_outputs_keep_their_own_duplicate_code(tmp_path: Path) -> None:
    source = (
        "Rattish: 1\nWorkflow:\n  name: Map keys\n  outputs:\n"
        "    result:\n      from: node.step.output.stdout\n"
        "    Result:\n      from: node.step.output.stdout\n"
        'Node step:\n  type: bash-command\n  command: "echo hi"\n'
    )
    with pytest.raises(RattishCompileError) as caught:
        compiler().compile(source, context(tmp_path))

    codes = [item.code for item in caught.value.diagnostics]
    assert "RATTISH_DUPLICATE_IDENTIFIER" in codes
    assert "RATTISH_DUPLICATE_MAP_KEY" not in codes


def test_recovering_parser_still_returns_a_document_with_repeated_keys() -> None:
    source = node_source("bash-command", '  env:\n    Path: "/a"\n    path: "/b"\n')

    parsed = parse_rattish_recovering(source)

    assert parsed.ast is not None
    assert parsed.diagnostics == ()


def test_check_command_reports_both_codes(tmp_path: Path) -> None:
    warned = tmp_path / "warned" / "workflow.rattish"
    warned.parent.mkdir()
    warned.write_text(node_source("bash-command", '  env:\n    DEBUG: "1"\n'), encoding="utf-8")
    repeated = tmp_path / "repeated" / "workflow.rattish"
    repeated.parent.mkdir()
    repeated.write_text(
        node_source("bash-command", '  env:\n    Path: "/a"\n    path: "/b"\n'), encoding="utf-8"
    )

    def check(source: Path) -> tuple[int, list[str]]:
        result = runner.invoke(
            app,
            [
                "rattish",
                "check",
                str(source),
                "--format",
                "json",
                "--data-dir",
                str(tmp_path / "app-data"),
            ],
        )
        payload = json.loads(result.stdout)
        return result.exit_code, [item["code"] for item in payload["diagnostics"]]

    assert check(warned) == (0, ["RATTISH_MAP_KEY_CHANGED"])
    exit_code, codes = check(repeated)
    assert exit_code == 1
    assert "RATTISH_DUPLICATE_MAP_KEY" in codes
