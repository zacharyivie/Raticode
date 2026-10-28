from __future__ import annotations

import pytest

from gofer.subscriptions.tool_trace import normalize_tool_trace, successful_receipt


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
