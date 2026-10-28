"""Provider-native evidence fixtures from the official SDK/headless schemas."""

from __future__ import annotations

import json

import pytest

from gofer.core.prompt_envelope import AgentResources
from gofer.subscriptions.acp_session import _session_trace
from gofer.subscriptions.antigravity import stream_antigravity
from gofer.subscriptions.cli_providers import CliOutput, cli_invocation, stream_cli
from gofer.subscriptions.tool_trace import successful_receipt


@pytest.mark.parametrize("width", [1, 11, 10000])
async def test_copilot_json_retains_correlated_checks_and_final_answer(
    monkeypatch, tmp_path, width
):
    records = [
        {"type": "session.start", "data": {"sessionId": "one"}},
        {"type": "assistant.message", "data": {"messageId": "progress", "content": "Checking."}},
        {
            "type": "tool.execution_start",
            "data": {
                "toolCallId": "check",
                "toolName": "bash",
                "arguments": {"command": "pytest"},
            },
        },
        {
            "type": "tool.execution_complete",
            "data": {
                "toolCallId": "check",
                "success": True,
                "result": {"content": "1 passed"},
                "shellExecution": {"exitCode": 0},
            },
        },
        {"type": "assistant.message", "data": {"messageId": "final", "content": "Done."}},
        {"type": "session.idle", "data": {}},
    ]
    wire = "\n".join(json.dumps(r) for r in records)

    async def fake(command, **kwargs):
        assert kwargs["protocol_stdout"] is True
        for index in range(0, len(wire), width):
            yield {"type": "chunk", "stream": "stdout", "text": wire[index : index + width]}
        yield {"type": "exit", "returncode": 0}

    monkeypatch.setattr("gofer.subscriptions.cli_providers.stream_subprocess", fake)
    events = [
        e
        async for e in stream_cli(
            ["copilot", "--output-format", "json"],
            "copilot",
            cwd=tmp_path,
            env={},
        )
    ]
    assert events[-1]["type"] == "final" and events[-1]["message"]["body"] == "Done."
    assert events[-1]["sessionId"] == "one"
    tools = [e["trace"] for e in events if e.get("trace")]
    assert [t["phase"] for t in tools] == ["start", "result"]
    assert tools[-1]["category"] == "shell" and tools[-1]["output"] == "1 passed"
    assert json.loads(tools[-1]["input"]) == {"command": "pytest"}
    assert successful_receipt(tools[-1])


@pytest.mark.parametrize(
    "ending",
    [
        {"type": "session.error", "data": {"message": "Not authenticated"}},
        {"type": "session.idle", "data": {"aborted": True}},
        {"type": "session.shutdown", "data": {"shutdownType": "error", "errorReason": "Failed"}},
        {"type": "tool.execution_start", "data": {"toolCallId": "one", "toolName": "view"}},
    ],
)
def test_copilot_native_error_cancel_and_incomplete_turn_cannot_pass(ending):
    output = CliOutput("copilot", json_output=True)
    output.feed(json.dumps(ending) + "\n")
    output.finish(0, "")
    assert output.error


def test_copilot_denied_tool_and_nonzero_command_are_not_success():
    output = CliOutput("copilot", json_output=True)
    for event in [
        {"type": "tool.execution_start", "data": {"toolCallId": "one", "toolName": "bash"}},
        {
            "type": "tool.execution_complete",
            "data": {
                "toolCallId": "one",
                "success": False,
                "error": {"message": "denied"},
            },
        },
        {"type": "tool.execution_start", "data": {"toolCallId": "two", "toolName": "bash"}},
        {
            "type": "tool.execution_complete",
            "data": {
                "toolCallId": "two",
                "success": True,
                "result": {"content": "1 failed"},
                "shellExecution": {"exitCode": 1},
            },
        },
    ]:
        output.feed(json.dumps(event) + "\n")
    traces = [e["trace"] for e in output.drain_events()]
    assert not any(successful_receipt(t) for t in traces)


def test_copilot_duplicate_completion_cannot_change_another_tools_receipt():
    output = CliOutput("copilot", json_output=True)
    first = {
        "type": "tool.execution_complete",
        "data": {
            "toolCallId": "one",
            "success": True,
            "result": {"content": "Done"},
            "shellExecution": {"exitCode": 0},
        },
    }
    second = {
        "type": "tool.execution_complete",
        "data": {
            "toolCallId": "two",
            "success": True,
            "result": {"content": "Failed checks"},
            "shellExecution": {"exitCode": 1},
        },
    }
    for record in [first, second, first]:
        output.feed(json.dumps(record) + "\n")
    traces = [event["trace"] for event in output.drain_events()]
    assert [(t["id"], t["exitCode"]) for t in traces] == [("one", 0), ("two", 1)]


@pytest.mark.parametrize("code", [0, 1])
def test_grok_native_exit_code_survives_acp_output_formatting(code):
    event = _session_trace(
        {
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "check",
                    "kind": "execute",
                    "status": "completed",
                    "title": "pytest",
                    "rawOutput": {"exitCode": code},
                }
            },
        },
        {},
        "turn",
    )
    assert event is not None
    trace = event["trace"]
    assert trace["exitCode"] == code and trace["category"] == "shell"
    assert successful_receipt(trace) is (code == 0)


@pytest.mark.parametrize("code", [0, 1])
def test_opencode_native_exit_code_survives_tool_metadata(code):
    output = CliOutput("opencode")
    output.feed(
        json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "callID": "check",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "pytest"},
                        "output": "Check result",
                        "metadata": {"exit": code},
                    },
                },
            }
        )
        + "\n"
    )
    trace = output.drain_events()[0]["trace"]
    assert trace["exitCode"] == code
    assert successful_receipt(trace) is (code == 0)


def test_managed_copilot_requests_json_and_keeps_readonly_permissions():
    with cli_invocation(
        "copilot",
        ["copilot", "--output-format", "text", "-p", "Review"],
        AgentResources(shell=True),
        read_only=True,
        structured_output=True,
    ) as (command, _):
        assert command[command.index("--output-format") + 1] == "json"
        denied = [command[i + 1] for i, value in enumerate(command) if value == "--deny-tool"]
        assert "write" in denied and "shell" in denied
        assert "--allow-all-tools" not in command


@pytest.mark.parametrize("failed", [False, True])
async def test_antigravity_tool_info_is_real_evidence(monkeypatch, tmp_path, failed):
    info = {"parameters": {"CommandLine": "pytest"}, "output": "1 passed"}
    if failed:
        info["error"] = {"type": "permission", "message": "denied"}

    async def fake(command, **kwargs):
        for record in [
            {
                "event": "step_update",
                "step_update": {
                    "step_type": "tool",
                    "step_index": 1,
                    "state": "DONE",
                    "tool_name": "run_command",
                    "tool_info": info,
                },
            },
            {"event": "result", "result": {"status": "SUCCESS", "response": "Done"}},
        ]:
            yield {"type": "chunk", "stream": "stdout", "text": json.dumps(record) + "\n"}
        yield {"type": "exit", "returncode": 0}

    monkeypatch.setattr(
        "gofer.subscriptions.antigravity.resolve_provider_executable", lambda p: "/fake/agy"
    )
    monkeypatch.setattr("gofer.subscriptions.antigravity.stream_subprocess", fake)
    events = [
        e async for e in stream_antigravity("Check", cwd=tmp_path, permission_mode="cli-managed")
    ]
    trace = next(e["trace"] for e in events if e.get("trace"))
    assert trace["category"] == "shell" and trace["phase"] == "result"
    assert trace["output"] == "1 passed"
    assert json.loads(trace["input"]) == {"CommandLine": "pytest"}
    assert successful_receipt(trace) is not failed
