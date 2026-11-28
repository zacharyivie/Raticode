"""The mcp-tool node: library lookup, sandbox, preflight, calls and trigger approvals."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from gofer.core import rem_library
from gofer.rattish import agent_sandbox, approved_files, mcp_cache, mcp_tool, trigger_approval
from gofer.rattish.agent_sandbox import workflow_unsandboxed
from gofer.rattish.compiler import CompileContext, RattishCompiler
from gofer.rattish.diagnostics import RattishCompileError
from gofer.rattish.preflight import run_preflight
from gofer.rattish.runtime import execute_node

PROJECT_ROOT = Path(__file__).parents[2]
RATTISH_ROOT = PROJECT_ROOT / "rattish"

# A small MCP server on standard input and output. It answers initialize, lists its
# tools (two pages), and calls them.
FAKE_SERVER = r"""
import json, os, sys

TOOLS = [
    {"name": "echo", "description": "Echo text back",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"},
                     "times": {"type": "integer"}}, "required": ["text"],
                     "additionalProperties": False}},
    {"name": "sum", "description": "Add numbers, answered as JSON text",
     "inputSchema": {"type": "object", "properties": {"values": {"type": "array",
                     "items": {"type": "number"}}}, "required": ["values"]}},
    {"name": "where", "inputSchema": {"type": "object"}},
    {"name": "broken", "inputSchema": {"type": "object"}},
]

def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()

print("server log line that is not JSON-RPC", flush=True)
for line in sys.stdin:
    message = json.loads(line)
    method, ident = message.get("method"), message.get("id")
    if ident is None:
        continue
    params = message.get("params") or {}
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": ident,
              "result": {"protocolVersion": params["protocolVersion"],
                         "capabilities": {"tools": {}},
                         "serverInfo": {"name": "fake", "version": "1"}}})
    elif method == "tools/list":
        if params.get("cursor") == "page-2":
            send({"jsonrpc": "2.0", "id": ident, "result": {"tools": TOOLS[2:]}})
        else:
            send({"jsonrpc": "2.0", "id": ident,
                  "result": {"tools": TOOLS[:2], "nextCursor": "page-2"}})
    elif method == "tools/call":
        name, args = params["name"], params.get("arguments") or {}
        if name == "echo":
            text = args["text"] * args.get("times", 1)
            result = {"content": [{"type": "text", "text": text}],
                      "structuredContent": {"text": text}}
        elif name == "sum":
            total = json.dumps({"total": sum(args["values"])})
            result = {"content": [{"type": "text", "text": total}]}
        elif name == "where":
            text = json.dumps(dict(os.environ)) if args.get("env") else os.getcwd()
            result = {"content": [{"type": "text", "text": text}]}
        else:
            result = {"isError": True, "content": [{"type": "text", "text": "it broke"}]}
        send({"jsonrpc": "2.0", "id": ident, "result": result})
    else:
        send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "nope"}})
"""


def compiler() -> RattishCompiler:
    return RattishCompiler.from_paths(
        schema_root=RATTISH_ROOT / "schemas",
        contract_paths=[RATTISH_ROOT / "contracts" / "mcp-tool.json"],
    )


def compile_source(source: str, project_root: Path) -> dict[str, Any]:
    return compiler().compile(source, CompileContext("mcp-tool", project_root)).ir


@pytest.fixture
def server_script(tmp_path: Path) -> Path:
    path = tmp_path / "fake_mcp.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return path


@pytest.fixture
def data_dir(tmp_path: Path, server_script: Path) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    rem_library.save_mcp_server(
        folder,
        {"name": "fake", "type": "stdio", "command": sys.executable, "args": [str(server_script)]},
    )
    mcp_tool.clear_list_cache()
    return folder


@pytest.fixture
def project(tmp_path: Path) -> Path:
    folder = tmp_path / "project"
    folder.mkdir()
    return folder


UNSANDBOXED = mcp_tool.SandboxSettings(enabled=False)
WORD = {"word": "hi"}


def workflow(node: str) -> str:
    return f"""Rattish: 1
Workflow:
  name: Call a tool
  inputs:
    word:
      schema: {{"type": "string"}}
      default: hi
Node call:
  type: mcp-tool
  with:
    word: input.word
{node}"""


# Contract ---------------------------------------------------------------------------------


def test_contract_defaults_compile_and_keep_argument_names(project: Path) -> None:
    source = workflow(
        "  server: fake\n  tool: echo\n  arguments:\n    text: hi\n"
        '    "max_results": 2\n    max-hits: 1\n    "camelCase": true\n'
        '    under: {"keepThis": 1}\n    lowerMe: 0\n'
    )
    result = compiler().compile(source, CompileContext("mcp-tool", project))
    node = result.ir["nodes"][0]
    [warning] = [
        item for item in result.diagnostics if item.code == "RATTISH_MCP_ARGUMENT_KEY_CHANGED"
    ]
    assert "'lowerMe'" in warning.message and "'lowerme'" in warning.message
    assert node["configuration"] == {
        "server": "fake",
        "tool": "echo",
        "arguments": {
            "text": "hi",
            "max_results": 2,
            "max-hits": 1,
            "camelCase": True,
            "under": {"keepThis": 1},
            "lowerme": 0,
        },
        "output_schema": None,
        "output_schema_path": None,
        "working_dir": ".",
        "call_timeout": "60s",
        "sandbox": "on",
        "network": [],
    }
    assert node["output"]["schema"] == {"$schema": "https://json-schema.org/draft/2020-12/schema"}


def test_contract_requires_a_tool_and_a_valid_server_name(project: Path) -> None:
    with pytest.raises(RattishCompileError) as missing:
        compile_source(workflow("  server: fake\n"), project)
    assert "RATTISH_MISSING_FIELD" in {item.code for item in missing.value.diagnostics}
    with pytest.raises(RattishCompileError):
        compile_source(workflow('  server: "{{word}}"\n  tool: echo\n'), project)


def test_sandbox_off_warns_with_the_mcp_code(project: Path) -> None:
    result = compiler().compile(
        workflow("  server: fake\n  tool: echo\n  sandbox: off\n"),
        CompileContext("mcp-tool", project),
    )
    codes = {item.code for item in result.diagnostics}
    assert "RATTISH_MCP_TOOL_SANDBOX_OFF" in codes


# Calls ------------------------------------------------------------------------------------


def test_list_tools_follows_pages_and_skips_log_lines(data_dir: Path) -> None:
    server = mcp_tool.library_server(data_dir, "fake")
    tools = mcp_tool.list_tools(server, UNSANDBOXED, data_dir=data_dir)
    assert [tool["name"] for tool in tools] == ["echo", "sum", "where", "broken"]
    assert tools[0]["inputSchema"]["required"] == ["text"]
    # Listing leaves no scratch folder behind.
    assert not list((data_dir / "run-scratch").glob("mcp-list-*"))


def test_call_returns_structured_content_or_text(data_dir: Path, project: Path) -> None:
    server = mcp_tool.library_server(data_dir, "fake")
    settings = mcp_tool.SandboxSettings(enabled=False, working_dir=project)

    def call(tool: str, arguments: dict[str, Any], *, structured: bool = False) -> Any:
        return mcp_tool.call_tool(
            server,
            settings,
            tool,
            arguments,
            data_dir=data_dir,
            run_id="run-1",
            timeout_seconds=20,
            structured=structured,
        )

    assert call("echo", {"text": "ab", "times": 2}) == {"text": "abab"}
    assert call("sum", {"values": [1, 2]}) == '{"total": 3}'
    assert call("sum", {"values": [1, 2]}, structured=True) == {"total": 3}
    assert call("where", {}) == str(project)
    with pytest.raises(mcp_tool.McpToolError) as broken:
        call("broken", {})
    assert (broken.value.kind, broken.value.code) == ("command", "RATTISH_MCP_TOOL_ERROR")
    assert "it broke" in broken.value.message
    with pytest.raises(mcp_tool.McpToolError) as invalid:
        call("echo", {"text": 3})
    assert invalid.value.code == "RATTISH_MCP_ARGUMENTS_INVALID"
    with pytest.raises(mcp_tool.McpToolError) as unknown:
        call("nope", {})
    assert unknown.value.code == "RATTISH_MCP_TOOL_UNKNOWN"
    assert "echo, sum, where, broken" in unknown.value.message


def test_a_local_server_gets_only_the_base_set_and_the_named_variables(
    tmp_path: Path, server_script: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "named-env"
    data.mkdir()
    rem_library.save_mcp_server(
        data,
        {
            "name": "fake",
            "type": "stdio",
            "command": sys.executable,
            "args": [str(server_script)],
            "env": ["NAMED_TOKEN", "NAMED_BUT_UNSET"],
        },
    )
    monkeypatch.setenv("NAMED_TOKEN", "named-value")
    monkeypatch.delenv("NAMED_BUT_UNSET", raising=False)
    monkeypatch.setenv("UNNAMED_SECRET", "must-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-leak")
    monkeypatch.setenv("GOFER_API_TOKEN", "must-not-leak")
    monkeypatch.setenv("LC_TIME", "C")
    monkeypatch.setenv("LANG", "C.UTF-8")
    server = mcp_tool.library_server(data, "fake")
    assert server["env"] == ["NAMED_TOKEN", "NAMED_BUT_UNSET"]
    seen = json.loads(
        mcp_tool.call_tool(
            server,
            mcp_tool.SandboxSettings(enabled=False, working_dir=project),
            "where",
            {"env": True},
            data_dir=data,
            run_id="run-env",
            timeout_seconds=20,
            structured=False,
        )
    )
    assert seen["NAMED_TOKEN"] == "named-value"
    assert seen["PATH"] == os.environ["PATH"]
    assert (seen["LANG"], seen["LC_TIME"]) == ("C.UTF-8", "C")
    for hidden in ("UNNAMED_SECRET", "AWS_SECRET_ACCESS_KEY", "GOFER_API_TOKEN", "NAMED_BUT_UNSET"):
        assert hidden not in seen
    allowed = mcp_tool.BASE_ENV | {"NAMED_TOKEN"}
    # Python itself may add a variable or two to its child's view, such as LC_CTYPE.
    assert {key for key in seen if key not in allowed and not key.startswith("LC_")} <= {
        "__CF_USER_TEXT_ENCODING"
    }


def test_server_environment_uses_the_windows_names_without_case() -> None:
    environ = {
        "Path": "C:\\Windows",
        "SystemRoot": "C:\\Windows",
        "USERPROFILE": "C:\\Users\\me",
        "APPDATA": "C:\\Users\\me\\AppData\\Roaming",
        "ComSpec": "cmd.exe",
        "GITHUB_TOKEN": "secret",
        "Gofer_Api_Token": "secret",
        "OTHER": "secret",
    }
    windows = mcp_tool.server_base_environment(["GITHUB_TOKEN"], platform="win32", environ=environ)
    assert set(windows) == {
        "Path",
        "SystemRoot",
        "USERPROFILE",
        "APPDATA",
        "ComSpec",
        "GITHUB_TOKEN",
    }
    # Windows names aren't part of the base set elsewhere.
    linux = mcp_tool.server_base_environment([], platform="linux", environ=environ)
    assert linux == {}


def test_sandbox_variables_win_over_named_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_tool, "sandbox_problem", lambda *args, **kwargs: None)
    monkeypatch.setenv("TMPDIR", "/host/tmp")
    monkeypatch.setenv("XDG_CACHE_HOME", "/host/cache")
    monkeypatch.setenv("API_TOKEN", "value")
    data = tmp_path / "d"
    data.mkdir()
    server = {
        "name": "s",
        "type": "stdio",
        "command": "srv",
        "env": ["XDG_CACHE_HOME", "TMPDIR", "API_TOKEN"],
    }
    scratch = data / "run-scratch" / "r"
    launch = mcp_tool.prepare_launch(
        server,
        mcp_tool.SandboxSettings(network=[], working_dir=tmp_path),
        data_dir=data,
        scratch=scratch,
        platform="linux",
    )
    assert launch.sandboxed
    assert launch.env["TMPDIR"] == str(scratch / "tmp")
    cache = mcp_cache.cache_dir(data, str(mcp_tool.definition_digest(server)))
    assert launch.env["XDG_CACHE_HOME"] == str(cache / "xdg")
    assert launch.env["API_TOKEN"] == "value"
    # Unsandboxed, the host's own temporary folder is part of the base set.
    off = mcp_tool.prepare_launch(
        server, UNSANDBOXED, data_dir=data, scratch=data / "run-scratch" / "o", platform="linux"
    )
    assert (off.env["TMPDIR"], off.env["XDG_CACHE_HOME"]) == ("/host/tmp", "/host/cache")


def test_a_server_that_exits_reports_its_last_words(tmp_path: Path) -> None:
    script = tmp_path / "dies.py"
    script.write_text("import sys\nsys.stderr.write('missing token\\n')\nsys.exit(3)\n")
    server = {"name": "dies", "type": "stdio", "command": sys.executable, "args": [str(script)]}
    with pytest.raises(mcp_tool.McpToolError) as caught:
        mcp_tool.list_tools(server, UNSANDBOXED, data_dir=tmp_path)
    assert caught.value.code == "RATTISH_MCP_SERVER_EXITED"
    assert "exit code 3" in caught.value.message
    assert "missing token" in caught.value.message


def test_a_missing_program_fails_to_start(tmp_path: Path) -> None:
    server = {"name": "gone", "type": "stdio", "command": str(tmp_path / "no-such-program")}
    with pytest.raises(mcp_tool.McpToolError) as caught:
        mcp_tool.list_tools(server, UNSANDBOXED, data_dir=tmp_path)
    assert caught.value.code == "RATTISH_MCP_SERVER_START_FAILED"


def test_argument_problems_skip_values_known_only_at_run_time() -> None:
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "times": {"type": "integer"}},
        "required": ["text"],
        "additionalProperties": False,
    }
    assert (
        mcp_tool.argument_problems(schema, {"text": "a", "times": "{{input.n}}"}, templates=True)
        == []
    )
    assert mcp_tool.argument_problems(schema, {"text": "a", "times": "{{input.n}}"}) != []
    missing = mcp_tool.argument_problems(schema, {"times": "{{input.n}}"}, templates=True)
    assert missing == ["'text' is a required property"]
    extra = mcp_tool.argument_problems(schema, {"text": "a", "colour": 1}, templates=True)
    assert len(extra) == 1 and "colour" in extra[0]
    assert mcp_tool.argument_problems(schema, {"text": "a", "times": "two"}) == [
        "times: 'two' is not of type 'integer'"
    ]


@pytest.mark.anyio
async def test_node_renders_templated_arguments_and_routes_output(
    data_dir: Path, project: Path
) -> None:
    ir = compile_source(
        workflow(
            "  sandbox: off\n  server: fake\n  tool: echo\n  arguments:\n"
            '    text: "{{word}}"\n    times: 3\n'
        ),
        project,
    )
    result = await execute_node(
        ir, "call", workflow_inputs={"word": "yo"}, data_dir=data_dir, run_id="r1"
    )
    assert result.outcome == "success", result.error
    assert result.output == {"text": "yoyoyo"}


@pytest.mark.anyio
async def test_node_output_schema_parses_text_and_validates(data_dir: Path, project: Path) -> None:
    base = "  sandbox: off\n  server: fake\n  tool: sum\n  arguments:\n    values: [2, 5]\n"
    ok = compile_source(
        workflow(base + '  output-schema:\n    type: object\n    required: ["total"]\n'), project
    )
    result = await execute_node(ok, "call", workflow_inputs=WORD, data_dir=data_dir, run_id="r2")
    assert result.outcome == "success", result.error
    assert result.output == {"total": 7}
    wrong = compile_source(
        workflow(base + '  output-schema:\n    type: object\n    required: ["average"]\n'), project
    )
    as_text = compile_source(workflow(base + '  output-schema: {"type": "string"}\n'), project)
    text = await execute_node(as_text, "call", workflow_inputs=WORD, data_dir=data_dir)
    assert text.outcome == "success", text.error
    assert text.output == '{"total": 7}'
    failed = await execute_node(wrong, "call", workflow_inputs=WORD, data_dir=data_dir, run_id="r3")
    assert failed.outcome == "failure"
    assert failed.error is not None and failed.error.kind == "output_validation"


@pytest.mark.anyio
async def test_node_takes_arguments_from_one_placeholder(data_dir: Path, project: Path) -> None:
    source = """Rattish: 1
Workflow:
  name: Call with whole arguments
  inputs:
    payload:
      schema: {"type": "object"}
      required: true
    raw:
      schema: {"type": "string"}
      required: true
Node object:
  type: mcp-tool
  sandbox: off
  server: fake
  tool: echo
  with:
    payload: input.payload
  arguments: "{{payload}}"
Node text:
  type: mcp-tool
  sandbox: off
  server: fake
  tool: echo
  with:
    raw: input.raw
  arguments: "{{raw}}"
"""
    ir = compile_source(source, project)
    inputs = {"payload": {"text": "obj"}, "raw": '{"text": "json text"}'}
    native = await execute_node(ir, "object", workflow_inputs=inputs, data_dir=data_dir)
    assert native.outcome == "success", native.error
    assert native.output == {"text": "obj"}
    text = await execute_node(ir, "text", workflow_inputs=inputs, data_dir=data_dir)
    assert text.outcome == "success", text.error
    assert text.output == {"text": "json text"}
    broken = await execute_node(
        ir, "text", workflow_inputs={**inputs, "raw": "{not json"}, data_dir=data_dir
    )
    assert broken.outcome == "failure"
    assert broken.error is not None and broken.error.code == "RATTISH_MCP_ARGUMENTS_INVALID"


@pytest.mark.anyio
async def test_node_fails_for_a_server_that_is_not_in_the_list(
    data_dir: Path, project: Path
) -> None:
    ir = compile_source(workflow("  server: other\n  tool: echo\n"), project)
    result = await execute_node(ir, "call", workflow_inputs=WORD, data_dir=data_dir)
    assert result.outcome == "failure"
    assert result.error is not None
    assert (result.error.kind, result.error.code) == (
        "configuration",
        "RATTISH_MCP_SERVER_UNKNOWN",
    )
    assert "fake" in result.error.message


# Preflight --------------------------------------------------------------------------------


def preflight_codes(ir: dict[str, Any], data_dir: Path) -> dict[str, str]:
    result = run_preflight(ir, data_dir=data_dir, subscriptions={})
    return {item.code: item.message for item in result.diagnostics}


class _Platform:
    """``mcp_tool``'s view of ``sys`` with another platform, leaving the real one alone."""

    def __init__(self, platform: str) -> None:
        self.platform = platform

    def __getattr__(self, name: str) -> Any:
        return getattr(sys, name)


@pytest.fixture
def sandbox_stand_in(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """A sandbox that is available here; servers start outside it so tests run anywhere.

    Returns whether each listing preflight asked for was sandboxed.
    """
    monkeypatch.setattr(mcp_tool, "sys", _Platform("linux"))
    # sandbox_problem holds the probe as a default argument, so set its cached answer.
    monkeypatch.setitem(mcp_tool._probe_result, "bwrap", None)
    monkeypatch.setattr(mcp_tool.shutil, "which", lambda name: f"/usr/bin/{name}")
    real = mcp_tool.list_tools
    asked: list[bool] = []

    def list_tools(server: Any, settings: Any, **kwargs: Any) -> list[dict[str, Any]]:
        asked.append(settings.enabled)
        return real(server, UNSANDBOXED, **kwargs)

    monkeypatch.setattr(mcp_tool, "list_tools", list_tools)
    return asked


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every attempt to list tools or start a server; each attempt fails the step loudly."""
    attempts: list[str] = []

    def refuse(what: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> Any:
            attempts.append(what)
            raise AssertionError(f"{what} was called")

        return record

    monkeypatch.setattr(mcp_tool, "list_tools", refuse("list_tools"))
    monkeypatch.setattr(mcp_tool, "prepare_launch", refuse("prepare_launch"))
    monkeypatch.setattr(mcp_tool, "StdioSession", refuse("StdioSession"))
    return attempts


def test_preflight_lists_tools_and_checks_arguments(
    data_dir: Path, project: Path, sandbox_stand_in: list[bool]
) -> None:
    good = compile_source(
        workflow('  server: fake\n  tool: echo\n  arguments:\n    text: "{{word}}"\n'),
        project,
    )
    assert preflight_codes(good, data_dir) == {}
    assert sandbox_stand_in == [True]

    unknown_tool = compile_source(workflow("  server: fake\n  tool: nope\n"), project)
    codes = preflight_codes(unknown_tool, data_dir)
    assert "RATTISH_PREFLIGHT_MCP_TOOL_UNKNOWN" in codes
    assert "echo, sum, where, broken" in codes["RATTISH_PREFLIGHT_MCP_TOOL_UNKNOWN"]

    bad_arguments = compile_source(
        workflow("  server: fake\n  tool: echo\n  arguments:\n    times: 2\n"), project
    )
    codes = preflight_codes(bad_arguments, data_dir)
    assert "'text' is a required property" in codes["RATTISH_PREFLIGHT_MCP_ARGUMENTS_INVALID"]

    unknown_server = compile_source(workflow("  server: other\n  tool: echo\n"), project)
    codes = preflight_codes(unknown_server, data_dir)
    assert "RATTISH_PREFLIGHT_MCP_SERVER_UNKNOWN" in codes


SKIPPED = (
    "Tool check skipped: this server runs unsandboxed. Check it with Raticode rem mcp tools fake."
)


def skipped_note(diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    notes = [d for d in diagnostics if d["code"] == "RATTISH_PREFLIGHT_MCP_TOOL_CHECK_SKIPPED"]
    assert len(notes) == 1
    return notes[0]


def test_preflight_warns_when_a_named_variable_is_not_set(
    data_dir: Path, project: Path, server_script: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ir = compile_source(
        workflow(
            '  server: fake\n  tool: echo\n  arguments:\n    text: "{{word}}"\n  sandbox: off\n'
        ),
        project,
    )
    assert "RATTISH_PREFLIGHT_MCP_ENV_MISSING" not in preflight_codes(ir, data_dir)
    rem_library.save_mcp_server(
        data_dir,
        {
            "name": "fake",
            "type": "stdio",
            "command": sys.executable,
            "args": [str(server_script)],
            "env": ["MCP_TEST_SET", "MCP_TEST_UNSET"],
        },
        replace="fake",
    )
    monkeypatch.setenv("MCP_TEST_SET", "x")
    monkeypatch.delenv("MCP_TEST_UNSET", raising=False)
    result = run_preflight(ir, data_dir=data_dir, subscriptions={})
    [warning] = [d for d in result.diagnostics if d.code == "RATTISH_PREFLIGHT_MCP_ENV_MISSING"]
    assert warning.severity == "warning"
    assert warning.message == (
        "MCP server 'fake' names environment variable MCP_TEST_UNSET, which is not set "
        "where Raticode runs."
    )
    assert warning.details["missing"] == ["MCP_TEST_UNSET"]
    # Only a warning: the server starts without the variable.
    assert result.ready
    monkeypatch.setenv("MCP_TEST_UNSET", "")
    assert "RATTISH_PREFLIGHT_MCP_ENV_MISSING" not in preflight_codes(ir, data_dir)


def test_preflight_never_starts_a_server_with_sandbox_off(
    data_dir: Path, project: Path, started: list[str]
) -> None:
    # Neither an unknown tool nor arguments that don't fit start the server to find out.
    ir = compile_source(
        workflow("  sandbox: off\n  server: fake\n  tool: nope\n  arguments:\n    times: 2\n"),
        project,
    )
    result = run_preflight(ir, data_dir=data_dir, subscriptions={})
    diagnostics = [item.to_json() for item in result.diagnostics]
    assert {item["code"] for item in diagnostics} == {
        "RATTISH_PREFLIGHT_MCP_TOOL_CHECK_SKIPPED",
        "RATTISH_PREFLIGHT_RUNS_UNSANDBOXED",
    }
    note = skipped_note(diagnostics)
    assert (note["severity"], note["message"]) == ("info", SKIPPED)
    assert note["details"]["reason"] == "the step sets sandbox: off"
    assert result.ready
    assert started == []


def test_preflight_never_starts_a_server_on_native_windows(
    data_dir: Path, project: Path, started: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_tool, "sys", _Platform("win32"))
    ir = compile_source(workflow("  server: fake\n  tool: echo\n"), project)
    result = run_preflight(ir, data_dir=data_dir, subscriptions={})
    note = skipped_note([item.to_json() for item in result.diagnostics])
    assert (note["severity"], note["message"]) == ("info", SKIPPED)
    assert "native Windows" in note["details"]["reason"]
    assert "RATTISH_PREFLIGHT_SANDBOX_UNAVAILABLE" not in {d.code for d in result.diagnostics}
    assert result.ready
    assert started == []


@pytest.mark.parametrize(
    ("platform", "step"),
    [
        ("linux", "  sandbox: off\n  server: fake\n  tool: echo\n"),
        ("win32", "  server: fake\n  tool: echo\n"),
    ],
    ids=["sandbox-off", "windows"],
)
def test_opening_a_document_in_the_studio_starts_no_unsandboxed_server(
    tmp_path: Path,
    data_dir: Path,
    started: list[str],
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    step: str,
) -> None:
    from gofer.rattish.editor import RattishEditorService
    from gofer.rattish.workspaces import create_registered_workflow

    monkeypatch.setattr(mcp_tool, "sys", _Platform(platform))
    studio = tmp_path / "studio"
    studio.mkdir()
    registered = create_registered_workflow(studio, "Call a tool", registry_dir=data_dir)
    source = workflow(step)
    registered.entrypoint.write_text(source, encoding="utf-8")

    service = RattishEditorService()
    document = service.open_document(registered.workflow_id, data_dir=data_dir)
    assert document["compilation"]["state"] == "valid"
    note = skipped_note(document["preflight"]["diagnostics"])
    assert (note["severity"], note["message"]) == ("info", SKIPPED)
    assert document["preflight"]["ready"] is True
    # Editing the buffer analyzes it again, and still starts nothing.
    edited = service.analyze_document(
        registered.workflow_id, source.replace("tool: echo", "tool: sum"), data_dir=data_dir
    )
    skipped_note(edited["preflight"]["diagnostics"])
    assert started == []


def test_preflight_reports_a_sandbox_that_cannot_start(
    data_dir: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_tool.sys, "platform", "linux")
    monkeypatch.setattr(mcp_tool, "_bwrap_probe", lambda: "bubblewrap can't create a sandbox here.")
    monkeypatch.setattr(mcp_tool.shutil, "which", lambda name: f"/usr/bin/{name}")
    ir = compile_source(workflow("  server: fake\n  tool: echo\n"), project)
    codes = preflight_codes(ir, data_dir)
    assert "RATTISH_PREFLIGHT_SANDBOX_UNAVAILABLE" in codes
    assert "RATTISH_PREFLIGHT_MCP_SERVER_UNAVAILABLE" not in codes


# Sandbox ----------------------------------------------------------------------------------


def test_bwrap_command_hides_secrets_and_limits_writes(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    scratch = data / "run-scratch" / "r1"
    scratch.mkdir(parents=True)
    proxy_dir = tmp_path / "proxy"
    proxy_dir.mkdir()
    settings = mcp_tool.SandboxSettings(network=["api.example.com"], working_dir=work)
    argv = mcp_tool.bwrap_command(
        ["npx", "-y", "server"],
        settings=settings,
        data_dir=data,
        scratch=scratch,
        proxy_dir=proxy_dir,
    )
    joined = " ".join(argv)
    assert argv[:2] == ["bwrap", "--die-with-parent"]
    assert "--unshare-net" in argv
    assert "--ro-bind / /" in joined
    assert f"--tmpfs {data.resolve()}" in joined
    # Writable binds come after the data folder is hidden, so the scratch folder shows.
    assert joined.index(f"--tmpfs {data.resolve()}") < joined.index(f"--bind {scratch.resolve()}")
    assert f"--bind {work.resolve()} {work.resolve()}" in joined
    assert f"--chdir {work.resolve()}" in joined
    assert "socat TCP-LISTEN:3128" in joined
    assert argv[-3:] == ["npx", "-y", "server"]

    open_net = mcp_tool.bwrap_command(
        ["srv"],
        settings=mcp_tool.SandboxSettings(network="any", working_dir=work),
        data_dir=data,
        scratch=scratch,
        proxy_dir=None,
    )
    assert "--unshare-net" not in open_net
    assert "socat" not in " ".join(open_net)


def test_bwrap_command_keeps_hidden_folders_hidden_under_a_writable_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    ssh = home / ".ssh"
    data = home / "data"
    scratch = data / "run-scratch" / "r1"
    for folder in (ssh, scratch):
        folder.mkdir(parents=True)
    monkeypatch.setattr(mcp_tool, "_hidden_paths", lambda data_dir: [ssh, data])
    argv = mcp_tool.bwrap_command(
        ["sh"],
        settings=mcp_tool.SandboxSettings(network=[], working_dir=home),
        data_dir=data,
        scratch=scratch,
        proxy_dir=None,
    )
    joined = " ".join(argv)
    # A working folder that holds the SSH keys and the data folder (one a worker's forged
    # worktree record pointed at, say) doesn't show them again.
    shown = joined.index(f"--bind {home} {home}")
    assert joined.index(f"--tmpfs {ssh}", shown) > shown
    assert joined.index(f"--tmpfs {data}", shown) > shown
    # The scratch folder inside the data folder still shows, after the data folder.
    assert joined.rindex(f"--bind {scratch} {scratch}") > joined.rindex(f"--tmpfs {data}")
    # The user's runtime folder (session bus, agents) and the resolver are covered too.
    runtime = Path(f"/run/user/{os.getuid()}")
    if runtime.is_dir():
        assert f"--tmpfs {runtime}" in joined
    if Path("/run/systemd/resolve").is_dir():
        assert "--tmpfs /run/systemd/resolve" in joined


@pytest.mark.parametrize("network", [[], ["example.com"], "any"])
def test_bwrap_hides_the_system_bus_and_name_lookup_sockets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, network: list[str] | str
) -> None:
    """The system bus (resolved's ResolveHostname, NetworkManager) is always hidden, and
    Avahi and nscd are hidden whenever the sandbox doesn't allow every host."""
    bus, avahi, nscd = tmp_path / "dbus", tmp_path / "avahi", tmp_path / "nscd"
    for folder in (bus, avahi, nscd):
        folder.mkdir()
    monkeypatch.setattr(mcp_tool, "SYSTEM_BUS_FOLDERS", (str(bus),))
    monkeypatch.setattr(mcp_tool, "RESOLVER_FOLDERS", (str(avahi), str(nscd)))
    monkeypatch.setattr(mcp_tool, "_hidden_paths", lambda data_dir: [])
    data = tmp_path / "data"
    scratch = data / "run-scratch" / "r1"
    scratch.mkdir(parents=True)
    setting = agent_sandbox.ANY_HOST if network == "any" else network
    argv = mcp_tool.bwrap_command(
        ["sh"],
        settings=mcp_tool.SandboxSettings(network=setting, working_dir=None),
        data_dir=data,
        scratch=scratch,
        proxy_dir=None,
    )
    joined = " ".join(argv)
    assert f"--tmpfs {bus}" in joined
    for folder in (avahi, nscd):
        assert (f"--tmpfs {folder}" in joined) is (network != "any")


def test_prepare_launch_sets_proxy_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mcp_tool, "sandbox_problem", lambda *args, **kwargs: None)
    if not unix_sockets_allowed(tmp_path):
        pytest.skip("This sandbox refuses Unix sockets.")
    data = tmp_path / "d"
    data.mkdir()
    server = {"name": "s", "type": "stdio", "command": "srv", "args": ["--x"]}
    launch = mcp_tool.prepare_launch(
        server,
        mcp_tool.SandboxSettings(network=["example.com"], working_dir=tmp_path),
        data_dir=data,
        scratch=data / "run-scratch" / "r",
        platform="linux",
    )
    try:
        assert launch.sandboxed
        assert launch.argv[0] == "bwrap"
        assert launch.env["HTTPS_PROXY"] == "http://127.0.0.1:3128"
        assert launch.env["TMPDIR"].startswith(str(data / "run-scratch" / "r"))
    finally:
        launch.close()
    darwin = mcp_tool.prepare_launch(
        server,
        mcp_tool.SandboxSettings(network=[], working_dir=tmp_path),
        data_dir=data,
        scratch=data / "run-scratch" / "m",
        platform="darwin",
    )
    assert darwin.argv[:2] == ["/usr/bin/sandbox-exec", "-p"]
    assert "(deny network-outbound)" in darwin.argv[2]
    assert darwin.argv[3:] == ["srv", "--x"]


def test_unsandboxed_reasons(tmp_path: Path) -> None:
    local = {"name": "s", "type": "stdio", "command": "srv"}
    remote = {"name": "r", "type": "http", "url": "https://mcp.example.com/mcp"}
    on = mcp_tool.SandboxSettings()
    assert mcp_tool.unsandboxed_reason(local, on, platform="linux") is None
    assert "Windows" in str(mcp_tool.unsandboxed_reason(local, on, platform="win32"))
    assert mcp_tool.unsandboxed_reason(local, UNSANDBOXED, platform="linux") == (
        "the step sets sandbox: off"
    )
    assert mcp_tool.unsandboxed_reason(remote, UNSANDBOXED, platform="win32") is None
    missing = mcp_tool.sandbox_problem(
        local, on, platform="linux", which=lambda _name: None, probe=lambda: None
    )
    assert missing is not None and "bubblewrap" in missing


def unix_sockets_allowed(folder: Path) -> bool:
    """Some sandboxes, such as Claude Code's, refuse Unix sockets to the tests they run."""
    try:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError:
        return False
    try:
        probe.bind(str(folder / "probe.sock"))
    except OSError:
        return False
    finally:
        probe.close()
        (folder / "probe.sock").unlink(missing_ok=True)
    return True


def proxy_request(proxy: mcp_tool.HostProxy, request: bytes) -> bytes:
    if proxy.unix_dir is not None:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(10)
        client.connect(str(proxy.unix_dir / "proxy.sock"))
    else:
        client = socket.create_connection(("127.0.0.1", int(proxy.port or 0)), timeout=10)
    client.sendall(request)
    data = b""
    try:
        while chunk := client.recv(4096):
            data += chunk
    except TimeoutError:
        pass
    client.close()
    return data


def test_proxy_opens_only_listed_hosts(tmp_path: Path) -> None:
    class Hello(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b"hello from upstream"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Hello)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    port = upstream.server_address[1]
    folder = tmp_path / "p"
    folder.mkdir()
    # Linux listens on a Unix socket bound into the sandbox; macOS on a loopback port.
    proxy = mcp_tool.HostProxy(
        ["localhost"], unix_dir=folder if unix_sockets_allowed(folder) else None
    )
    try:
        allowed = proxy_request(
            proxy, f"GET http://localhost:{port}/ HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
        )
        assert b"hello from upstream" in allowed
        tunnel = proxy_request(proxy, f"CONNECT localhost:{port} HTTP/1.1\r\n\r\n".encode())
        assert tunnel.startswith(b"HTTP/1.1 200")
        refused = proxy_request(
            proxy, f"GET http://127.0.0.1:{port}/ HTTP/1.1\r\nHost: x\r\n\r\n".encode()
        )
        assert refused.startswith(b"HTTP/1.1 403")
        assert b"doesn't allow 127.0.0.1" in refused
    finally:
        proxy.close()
        upstream.shutdown()
        upstream.server_close()


# HTTP servers -----------------------------------------------------------------------------


@pytest.fixture
def http_server() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    seen: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            message = json.loads(self.rfile.read(length))
            seen.append({"message": message, "session": self.headers.get("Mcp-Session-Id")})
            if "id" not in message:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            method = message["method"]
            if method == "initialize":
                result: Any = {"protocolVersion": "2025-06-18", "capabilities": {}}
            elif method == "tools/list":
                result = {"tools": [{"name": "time", "inputSchema": {"type": "object"}}]}
            else:
                result = {"content": [{"type": "text", "text": "noon"}]}
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
            if method == "tools/call":
                # Answer the call as an event stream with a notification first.
                body = (
                    'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
                    f"event: message\ndata: {json.dumps(reply)}\n\n"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
            else:
                body = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", "session-1")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_DELETE(self) -> None:  # noqa: N802
            seen.append({"message": "DELETE", "session": self.headers.get("Mcp-Session-Id")})
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/mcp", seen
    finally:
        server.shutdown()
        server.server_close()


def test_http_server_session_and_event_stream(
    tmp_path: Path, http_server: tuple[str, list[dict[str, Any]]]
) -> None:
    url, seen = http_server
    server = {"name": "remote", "type": "http", "url": url}
    output = mcp_tool.call_tool(
        server,
        mcp_tool.SandboxSettings(),
        "time",
        {},
        data_dir=tmp_path,
        run_id="h1",
        timeout_seconds=10,
        structured=False,
    )
    assert output == "noon"
    methods = [
        item["message"] if item["message"] == "DELETE" else item["message"]["method"]
        for item in seen
    ]
    assert methods == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
        "DELETE",
    ]
    assert seen[0]["session"] is None
    assert all(item["session"] == "session-1" for item in seen[1:])


# Trigger approvals ------------------------------------------------------------------------


def test_trigger_approval_covers_the_server_definition(
    data_dir: Path, project: Path, server_script: Path
) -> None:
    source = project / "workflow.rattish"
    source.write_text(workflow("  server: fake\n  tool: echo\n"), encoding="utf-8")
    before = trigger_approval.take_snapshot(source, data_dir)
    entry = next(item for item in before.files if item.role == "mcp_server")
    assert entry.path == "mcp-server:fake"
    definition = mcp_tool.definition_from_text(entry.text)
    assert definition is not None and str(server_script) in definition["args"]

    rem_library.save_mcp_server(
        data_dir,
        {
            "name": "fake",
            "type": "stdio",
            "command": sys.executable,
            "args": [str(server_script), "--extra"],
        },
        replace="fake",
    )
    after = trigger_approval.take_snapshot(source, data_dir)
    assert after.fingerprint != before.fingerprint
    changes = trigger_approval.describe_changes(before, after)
    [changed] = changes["files"]
    assert changed["roleLabel"] == "MCP server"
    assert changed["absolutePath"] == ""
    assert changed["change"] == "changed"
    assert '+  - "--extra"' in changed["diff"]

    # Named variables are part of the definition: their names, never their values.
    with_names = {
        "name": "fake",
        "type": "stdio",
        "command": sys.executable,
        "args": [str(server_script), "--extra"],
        "env": ["FAKE_TOKEN"],
    }
    rem_library.save_mcp_server(data_dir, with_names, replace="fake")
    named = trigger_approval.take_snapshot(source, data_dir)
    assert named.fingerprint != after.fingerprint
    [changed] = trigger_approval.describe_changes(after, named)["files"]
    assert "+env:" in changed["diff"] and "+  - FAKE_TOKEN" in changed["diff"]
    entry = next(item for item in named.files if item.role == "mcp_server")
    assert entry.text is not None and "FAKE_TOKEN" in entry.text

    # A server removed from the list still counts as a change.
    rem_library.remove_mcp_server(data_dir, "fake")
    removed = trigger_approval.take_snapshot(source, data_dir)
    assert next(item for item in removed.files if item.role == "mcp_server").sha256 is None


@pytest.mark.anyio
async def test_a_triggered_run_refuses_a_changed_server(
    data_dir: Path, project: Path, server_script: Path
) -> None:
    source = project / "workflow.rattish"
    source.write_text(
        workflow("  sandbox: off\n  server: fake\n  tool: echo\n  arguments:\n    text: a\n"),
        encoding="utf-8",
    )
    approved = trigger_approval.take_snapshot(source, data_dir)
    files = approved_files.ApprovedFiles.from_snapshot(approved, data_dir=data_dir, run_id="t1")
    ir = compile_source(source.read_text(), project)
    with approved_files.guarding(files):
        ok = await execute_node(ir, "call", workflow_inputs=WORD, data_dir=data_dir, run_id="t1")
    assert ok.outcome == "success", ok.error

    rem_library.save_mcp_server(
        data_dir,
        {"name": "fake", "type": "stdio", "command": "/bin/sh", "args": ["-c", "evil"]},
        replace="fake",
    )
    with approved_files.guarding(files):
        refused = await execute_node(
            ir, "call", workflow_inputs=WORD, data_dir=data_dir, run_id="t1"
        )
    assert refused.outcome == "failure"
    assert refused.error is not None
    assert refused.error.code == approved_files.CHANGED_CODE
    assert "MCP server" in refused.error.message


def project_server(data_dir: Path, project: Path, script: str | None) -> tuple[Path, Path]:
    """A server ``python tools/server.py`` from the project, and a workflow that calls it."""
    path = project / "tools" / "server.py"
    path.parent.mkdir(exist_ok=True)
    if script is not None:
        path.write_text(script, encoding="utf-8")
    rem_library.save_mcp_server(
        data_dir,
        {"name": "local", "type": "stdio", "command": sys.executable, "args": ["tools/server.py"]},
    )
    source = project / "workflow.rattish"
    source.write_text(
        workflow("  sandbox: off\n  server: local\n  tool: echo\n  arguments:\n    text: a\n"),
        encoding="utf-8",
    )
    return path, source


def test_project_files_are_regular_files_inside_the_project(tmp_path: Path, project: Path) -> None:
    (project / "tools").mkdir()
    script = project / "tools" / "server.py"
    script.write_text("pass\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")
    (project / "escape.py").symlink_to(outside)
    (project / "loop").symlink_to(project / "loop")
    server = {
        "name": "s",
        "type": "stdio",
        "command": sys.executable,
        "args": [
            "tools/server.py",
            "--port",
            "tools",  # a folder, not a file
            str(script),  # the same file again, by absolute path
            str(outside),
            "escape.py",
            "loop",
            "../outside.py",
            "bad\0name",
            "x" * 5000,
        ],
    }
    assert mcp_tool.project_files(server, project, project) == [script.resolve()]
    # Relative values resolve from where the server starts.
    assert mcp_tool.project_files(server, project / "tools", project) == [script.resolve()]
    nested = {**server, "args": ["server.py"]}
    assert mcp_tool.project_files(nested, project / "tools", project) == [script.resolve()]
    assert mcp_tool.project_files(nested, project, project) == []
    # Without its working folder the server starts elsewhere, so only absolute paths count.
    assert mcp_tool.project_files(server, project / "gone", project) == [script.resolve()]
    direct = {**server, "command": str(script), "args": []}
    assert mcp_tool.project_files(direct, None, project) == [script.resolve()]
    assert mcp_tool.project_files({"type": "http", "url": str(script)}, project, project) == []


def test_trigger_approval_covers_a_project_script_the_server_runs(
    data_dir: Path, project: Path, server_script: Path
) -> None:
    script, source = project_server(data_dir, project, FAKE_SERVER)
    before = trigger_approval.take_snapshot(source, data_dir)
    roles = {item.path: item.role for item in before.files}
    assert roles[str(script.resolve())] == "script"
    assert roles["mcp-server:local"] == "mcp_server"
    # The interpreter lives outside the project, so it isn't covered.
    assert sys.executable not in roles

    script.write_text(FAKE_SERVER + "\nimport os\n", encoding="utf-8")
    after = trigger_approval.take_snapshot(source, data_dir)
    assert after.fingerprint != before.fingerprint
    [changed] = trigger_approval.describe_changes(before, after, project_root=project)["files"]
    assert (changed["path"], changed["roleLabel"], changed["change"]) == (
        "tools/server.py",
        "Script",
        "changed",
    )
    assert changed["absolutePath"] == str(script.resolve())
    assert "+import os" in changed["diff"]

    # A workflow that no longer compiles still reads the server's script again.
    source.write_text(source.read_text() + "\nNode broken:\n", encoding="utf-8")
    script.write_text(FAKE_SERVER, encoding="utf-8")
    broken = trigger_approval.take_snapshot(source, data_dir, known=after.files)
    assert not broken.complete
    paths = {
        item["path"]
        for item in trigger_approval.describe_changes(after, broken, project_root=project)["files"]
    }
    assert "tools/server.py" in paths


STARTED_MARKER = "open('started', 'w').close()\n"


@pytest.mark.anyio
async def test_a_triggered_run_refuses_a_server_script_changed_during_the_run(
    data_dir: Path, project: Path
) -> None:
    script, source = project_server(data_dir, project, FAKE_SERVER)
    approved = trigger_approval.take_snapshot(source, data_dir)
    changed_paths: list[tuple[str, str]] = []
    files = approved_files.ApprovedFiles.from_snapshot(
        approved,
        data_dir=data_dir,
        run_id="t1",
        on_changed=lambda path, role: changed_paths.append((path, role)),
    )
    ir = compile_source(source.read_text(), project)
    with approved_files.guarding(files):
        ok = await execute_node(ir, "call", workflow_inputs=WORD, data_dir=data_dir, run_id="t1")
        assert ok.outcome == "success", ok.error

        # Another step, or another program, rewrites the script while the run goes on.
        script.write_text(STARTED_MARKER + FAKE_SERVER, encoding="utf-8")
        refused = await execute_node(
            ir, "call", workflow_inputs=WORD, data_dir=data_dir, run_id="t1"
        )
    assert refused.outcome == "failure"
    assert refused.error is not None
    assert refused.error.code == approved_files.CHANGED_CODE
    assert refused.error.message == approved_files.CHANGED_MESSAGE
    assert refused.error.details["path"] == str(script.resolve())
    assert changed_paths == [(str(script.resolve()), "script")]
    assert not (project / "started").exists()

    # A manual run checks nothing, so the same step starts the edited server.
    manual = await execute_node(ir, "call", workflow_inputs=WORD, data_dir=data_dir, run_id="m1")
    assert manual.outcome == "success", manual.error
    assert (project / "started").exists()


@pytest.mark.anyio
async def test_a_triggered_run_refuses_a_server_script_that_appeared_after_approval(
    data_dir: Path, project: Path
) -> None:
    script, source = project_server(data_dir, project, None)
    approved = trigger_approval.take_snapshot(source, data_dir)
    assert str(script.resolve()) not in {item.path for item in approved.files}
    files = approved_files.ApprovedFiles.from_snapshot(approved, data_dir=data_dir, run_id="t2")
    ir = compile_source(source.read_text(), project)
    script.write_text(STARTED_MARKER + FAKE_SERVER, encoding="utf-8")
    with approved_files.guarding(files):
        refused = await execute_node(
            ir, "call", workflow_inputs=WORD, data_dir=data_dir, run_id="t2"
        )
    assert refused.outcome == "failure"
    assert refused.error is not None and refused.error.code == approved_files.CHANGED_CODE
    assert not (project / "started").exists()


def test_definition_text_holds_names_only_and_keeps_old_digests() -> None:
    plain = {"name": "s", "type": "stdio", "command": "srv", "args": ["--x"]}
    assert mcp_tool.definition_text(plain) == (
        'name: s\ntype: stdio\ncommand: "srv"\nargs:\n  - "--x"\n'
    )
    assert mcp_tool.definition_text({**plain, "env": []}) == mcp_tool.definition_text(plain)
    named = mcp_tool.definition_text({**plain, "env": ["API_TOKEN"]})
    assert named.endswith("env:\n  - API_TOKEN\n")
    assert mcp_tool.definition_digest({**plain, "env": ["API_TOKEN"]}) != (
        mcp_tool.definition_digest(plain)
    )


def test_triggers_section_lists_unsandboxed_servers(data_dir: Path, project: Path) -> None:
    ir = compile_source(workflow("  sandbox: off\n  server: fake\n  tool: echo\n"), project)
    assert workflow_unsandboxed(ir, data_dir) == [
        {"node": "call", "provider": "MCP server fake", "reason": "the step sets sandbox: off"}
    ]
    sandboxed = compile_source(workflow("  server: fake\n  tool: echo\n"), project)
    assert workflow_unsandboxed(sandboxed, data_dir, platform="linux") == []


# The real sandbox -------------------------------------------------------------------------
#
# These start a local server inside bubblewrap and check what it can and can't do. They
# need a computer where bubblewrap can make namespaces, so they skip inside an agent's
# own sandbox, in many containers and where AppArmor limits user namespaces. Run them on
# the desktop with:
#   PYTHONPATH=src .venv/bin/python -m pytest -rs -k real_sandbox \
#       tests/unit/test_rattish_mcp_tool.py

# A server whose tools try to write, read and reach the network, and report what happened.
PROBE_SERVER = r"""
import json, os, socket, sys, urllib.request

def attempt(name, args):
    try:
        if name == "write":
            with open(args["path"], "w") as handle:
                handle.write("x")
            return "ok"
        if name == "read":
            with open(args["path"]) as handle:
                return "ok:" + handle.read()
        if name == "connect":
            socket.create_connection((args["host"], args["port"]), timeout=3).close()
            return "ok"
        if name == "fetch":
            proxy = os.environ.get("HTTP_PROXY", "")
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy} if proxy else {})
            )
            with opener.open(args["url"], timeout=5) as response:
                return "ok:" + response.read().decode()
    except urllib.error.HTTPError as exc:
        return f"error:{exc.code}"
    except Exception as exc:
        return "error:" + type(exc).__name__
    return "error:unknown"

TOOLS = [{"name": name, "inputSchema": {"type": "object"}}
         for name in ("write", "read", "connect", "fetch")]
for line in sys.stdin:
    message = json.loads(line)
    ident, method = message.get("id"), message.get("method")
    if ident is None:
        continue
    params = message.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                  "serverInfo": {"name": "probe", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    else:
        text = attempt(params["name"], params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": text}]}
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}) + "\n")
    sys.stdout.flush()
"""


def real_sandbox_problem(folder: Path) -> str | None:
    if sys.platform != "linux":
        return "The bubblewrap sandbox is Linux only."
    import shutil

    for tool in ("bwrap", "socat"):
        if shutil.which(tool) is None:
            return f"{tool} isn't installed."
    if not unix_sockets_allowed(folder):
        return "This environment refuses Unix sockets."
    return mcp_tool._bwrap_probe()


@pytest.fixture
def real_sandbox(tmp_path: Path) -> Iterator[dict[str, Any]]:
    problem = real_sandbox_problem(tmp_path)
    if problem:
        pytest.skip(f"Needs a working bubblewrap sandbox: {problem}")

    class Hello(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b"hello"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Hello)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    work = tmp_path / "work"
    work.mkdir()
    # The script sits in the working folder: the sandbox's /tmp is its own.
    script = work / "probe_mcp.py"
    script.write_text(PROBE_SERVER, encoding="utf-8")
    data = tmp_path / "data"
    (data / "devices").mkdir(parents=True)
    (data / "devices" / "key").write_text("secret", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    rem_library.save_mcp_server(
        data, {"name": "probe", "type": "stdio", "command": sys.executable, "args": [str(script)]}
    )
    mcp_tool.clear_list_cache()
    try:
        yield {
            "work": work,
            "data": data,
            "outside": outside,
            "port": upstream.server_address[1],
            "server": mcp_tool.library_server(data, "probe"),
        }
    finally:
        upstream.shutdown()
        upstream.server_close()


def test_real_sandbox_limits_files_and_network(real_sandbox: dict[str, Any]) -> None:
    work, data, outside = real_sandbox["work"], real_sandbox["data"], real_sandbox["outside"]
    port, server = real_sandbox["port"], real_sandbox["server"]

    def call(tool: str, arguments: dict[str, Any], network: Any) -> Any:
        settings = mcp_tool.SandboxSettings(network=network, working_dir=work)
        return mcp_tool.call_tool(
            server,
            settings,
            tool,
            arguments,
            data_dir=data,
            run_id="real-sandbox",
            timeout_seconds=30,
            structured=False,
        )

    # Writes stay in the working folder.
    assert call("write", {"path": str(work / "made.txt")}, []) == "ok"
    assert (work / "made.txt").read_text() == "x"
    assert call("write", {"path": str(outside / "escaped.txt")}, []).startswith("error:")
    assert not (outside / "escaped.txt").exists()
    # Raticode's data folder is hidden.
    assert call("read", {"path": str(data / "devices" / "key")}, []).startswith("error:")
    # No network at all by default, not even this computer's loopback.
    assert call("connect", {"host": "127.0.0.1", "port": port}, []).startswith("error:")
    # A listed host is reached through the proxy; an unlisted one is refused there.
    url = f"http://localhost:{port}/"
    assert call("fetch", {"url": url}, ["localhost"]) == "ok:hello"
    assert call("fetch", {"url": f"http://127.0.0.1:{port}/"}, ["localhost"]) == "error:403"
    assert call("connect", {"host": "127.0.0.1", "port": port}, ["localhost"]).startswith("error:")
    # network: any shares this computer's network.
    assert call("connect", {"host": "127.0.0.1", "port": port}, "any") == "ok"


@pytest.mark.anyio
async def test_real_sandbox_step_with_the_default_sandbox(real_sandbox: dict[str, Any]) -> None:
    work, data, outside = real_sandbox["work"], real_sandbox["data"], real_sandbox["outside"]
    source = f"""Rattish: 1
Workflow:
  name: Sandboxed call
Node inside:
  type: mcp-tool
  server: probe
  tool: write
  arguments: {{"path": "{work / "step.txt"}"}}
Node outside:
  type: mcp-tool
  server: probe
  tool: write
  arguments: {{"path": "{outside / "step.txt"}"}}
Node reach:
  type: mcp-tool
  server: probe
  tool: fetch
  arguments: {{"url": "http://localhost:{real_sandbox["port"]}/"}}
"""
    ir = compile_source(source, work)
    assert run_preflight(ir, data_dir=data, subscriptions={}).ready
    inside = await execute_node(ir, "inside", data_dir=data, run_id="step-1")
    assert (inside.outcome, inside.output) == ("success", "ok")
    escaped = await execute_node(ir, "outside", data_dir=data, run_id="step-1")
    assert escaped.outcome == "success" and str(escaped.output).startswith("error:")
    assert not (outside / "step.txt").exists()
    reach = await execute_node(ir, "reach", data_dir=data, run_id="step-1")
    assert reach.outcome == "success" and str(reach.output).startswith("error:")


def test_real_sandbox_cli_lists_tools_with_and_without_network(
    real_sandbox: dict[str, Any],
) -> None:
    from typer.testing import CliRunner

    from gofer.cli.main import app

    if real_sandbox["work"].resolve().is_relative_to("/tmp"):
        # Listing tools binds no working folder, and the sandbox's /tmp is its own empty
        # one, so a server script kept under /tmp isn't there.
        pytest.skip("The test's server script is under /tmp, which the sandbox replaces.")
    data = str(real_sandbox["data"])
    for extra in ([], ["--network", "localhost"], ["--network", "any"]):
        result = CliRunner().invoke(
            app, ["rem", "mcp", "tools", "probe", "--data-dir", data, "--format", "json", *extra]
        )
        assert result.exit_code == 0, result.output
        listing = json.loads(result.output)
        assert listing["sandboxed"] is True
        assert [tool["name"] for tool in listing["tools"]] == ["write", "read", "connect", "fetch"]
