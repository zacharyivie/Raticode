from __future__ import annotations

from typing import Any

import pytest

from gofer.subscriptions.tool_trace import (
    normalize_tool_trace,
    per_call_receipts,
    successful_receipt,
)


@pytest.mark.parametrize(
    "tool", ["Bash", "bash", "Shell", "shellToolCall", "run_command", "run_terminal_cmd"]
)
def test_native_command_names_are_normalized(tool):
    trace = normalize_tool_trace({"title": tool, "status": "completed", "output": "1 passed"})
    assert trace["category"] == "shell"
    assert successful_receipt(trace)
    assert "exitCode" not in trace


@pytest.mark.parametrize(
    "trace",
    [
        {"title": "shell", "status": "running", "output": "working"},
        {"title": "shell", "status": "failed", "output": "failed"},
        {"title": "shell", "status": "completed", "output": "1 failed", "exitCode": 1},
        {"title": "shell", "status": "completed", "output": "", "exitCode": False},
        {"title": "shell", "status": "completed", "output": {"exit_code": 3}},
        {"title": "shell", "status": "completed", "output": {"error": "denied"}},
        {"title": "shell", "status": "completed", "output": {"success": {"exitCode": 1}}},
        {"title": "shell", "status": "completed", "exitCode": 0, "error": "denied"},
        {"title": "shell", "status": "pending", "output": "1 passed"},
        {"title": "shell", "status": "completed"},
    ],
)
def test_failed_running_or_empty_evidence_is_never_success(trace):
    assert not successful_receipt(normalize_tool_trace(trace))


def test_acp_execute_category_preserved_and_zero_is_real_evidence():
    trace = normalize_tool_trace(
        {"category": "execute", "status": "completed", "output": {"exitCode": 0}}
    )
    assert trace["category"] == "shell" and trace["phase"] == "result"
    assert trace["exitCode"] == 0
    assert successful_receipt(trace)


def test_per_call_receipts_send_each_call_once_with_its_outcome():
    pretty = '{\n  "command": "pytest -k \\"one\\"",\n  "description": "Run one test"\n}'
    traces: list[dict[str, Any]] = [
        {"id": "a", "kind": "tool", "title": "Bash", "input": "{}", "phase": "start"},
        {
            "id": "a",
            "kind": "tool",
            "title": "Bash",
            "input": pretty,
            "detail": "Run one test",
            "command": 'pytest -k "one"',
            "category": "shell",
            "phase": "update",
            "status": "running",
        },
        {
            "id": "a",
            "kind": "tool",
            "title": "Tool result",
            "input": "{}",
            "category": "shell",
            "phase": "result",
            "status": "completed",
            "exitCode": 1,
            "output": "1 failed",
        },
        {"kind": "summary", "title": "Summary", "body": "Looking at the failure"},
        {"id": "b", "title": "Read", "input": "a.py", "detail": "src", "phase": "start"},
    ]
    entries = per_call_receipts(traces)
    assert entries == [
        {
            "id": "a",
            "kind": "tool",
            "title": "Bash",
            "input": '{"command":"pytest -k \\"one\\"","description":"Run one test"}',
            "category": "shell",
            "phase": "result",
            "status": "completed",
            "exitCode": 1,
            "output": "1 failed",
        },
        {"kind": "summary", "title": "Summary", "body": "Looking at the failure"},
        # A detail that isn't a copy of the input stays, and so does an unfinished call.
        {"id": "b", "title": "Read", "input": "a.py", "detail": "src", "phase": "start"},
    ]
    # The prompt copy never changes the traces the acceptance gate reads.
    assert len(traces) == 5 and traces[1]["input"] == pretty
    assert traces[2]["title"] == "Tool result"
    assert not successful_receipt(entries[0])


def test_per_call_receipts_leave_cut_input_as_it_is():
    cut = '{"content": "' + "x" * 50 + "…"
    entries = per_call_receipts([{"id": "w", "title": "Write", "input": cut, "phase": "start"}])
    assert entries[0]["input"] == cut


def test_per_call_receipts_keep_the_calls_of_the_same_trace_window():
    traces = []
    for n in range(60):
        traces += [
            {"id": f"c{n}", "title": "Bash", "input": f"cmd {n}", "phase": "start"},
            {
                "id": f"c{n}",
                "title": "Tool result",
                "input": f"cmd {n}",
                "phase": "result",
                "status": "completed",
                "exitCode": 0,
                "output": f"out {n}",
            },
        ]
    traces.append({"title": "Summary", "body": "old"})
    entries = per_call_receipts(traces[:-1] + [{"title": "Summary", "body": "new"}], window=6)
    # The window's first trace is c57's result: its start, before the window, still names it.
    assert [e.get("id") for e in entries] == ["c57", "c58", "c59", None]
    assert entries[0] == {
        "id": "c57",
        "title": "Bash",
        "input": "cmd 57",
        "phase": "result",
        "status": "completed",
        "exitCode": 0,
        "output": "out 57",
    }
    assert entries[-1]["body"] == "new"
    assert per_call_receipts(traces, window=0) == []
    assert len(per_call_receipts(traces)) == 41
