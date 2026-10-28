"""Normalize native tool evidence without inventing outcomes or exit codes."""

from __future__ import annotations

import json
from typing import Any

from gofer.utils.protocol import retained_text

SHELL_TOOLS = frozenset(
    {
        "bash",
        "shell",
        "powershell",
        "run_command",
        "run_terminal_cmd",
        "shell_tool_call",
        "bash_tool_call",
        "shelltoolcall",
        "bashtoolcall",
        "terminal",
        "execute",
    }
)
SUCCESS_STATUSES = frozenset({"complete", "completed", "success", "succeeded"})
FINISHED_STATUSES = SUCCESS_STATUSES | {"error", "failed", "cancelled", "canceled"}


def normalize_tool_trace(trace: dict[str, Any]) -> dict[str, Any]:
    result = dict(trace)
    status = str(result.get("status") or "").lower()
    if not result.get("phase"):
        result["phase"] = "result" if status in FINISHED_STATUSES else "start"
    title = str(result.get("title") or "").lower().replace(" ", "_")
    if result.get("category") == "execute" or title in SHELL_TOOLS:
        result["category"] = "shell"
    # Retain only actual structured exit codes, including nonzero failures.
    output = result.get("output")
    if isinstance(output, dict):
        nested = output.get("success")
        native = nested if isinstance(nested, dict) else output
        for key in ("exitCode", "exit_code", "exitcode"):
            if type(native.get(key)) is int:
                result["exitCode"] = native[key]
                break
        if output.get("is_error") is True or output.get("error") or output.get("failure"):
            result["status"] = "failed"
    for key in ("input", "output", "error"):
        value = result.get(key)
        if value is not None:
            result[key] = retained_text(
                value if isinstance(value, str) else json.dumps(value, ensure_ascii=False), 8192
            )
        else:
            result.pop(key, None)
    return result


def successful_receipt(trace: dict[str, Any]) -> bool:
    if (
        trace.get("phase") != "result"
        or trace.get("status") not in SUCCESS_STATUSES
        or trace.get("error")
    ):
        return False
    if "exitCode" in trace:
        return type(trace["exitCode"]) is int and trace["exitCode"] == 0
    return bool(trace.get("output")) and not trace.get("error")
