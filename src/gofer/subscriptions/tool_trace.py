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


# What a result trace knows better than the start and update traces before it.
OUTCOME_FIELDS = frozenset({"phase", "status", "output", "exitCode", "error"})


def per_call_receipts(traces: list[dict[str, Any]], window: int = 80) -> list[dict[str, Any]]:
    """One entry per tool call seen in the last ``window`` traces, in order of start.

    A call's start, update and result traces share its ID, and the result already
    repeats the start's input, so sending the traces as they are spends most of the
    window on copies. Each entry keeps the call's input, output, exit code and status.
    Later start and update traces refine what the call was (streamed input arrives in
    the update); the result only adds its outcome and anything still missing. Traces
    without an ID stay as they are. An entry drops only exact copies: JSON input loses
    its indentation, and a detail or command already inside the input goes. Checks
    that judge the work read the full traces.
    """
    if window <= 0:
        return []
    recent = traces[-window:]
    seen = {str(t["id"]) for t in recent if t.get("id")}
    calls: dict[str, dict[str, Any]] = {}
    entries: list[dict[str, Any]] = []
    for position, trace in enumerate(traces):
        key = trace.get("id")
        if not key:
            if position >= len(traces) - len(recent):
                entries.append(dict(trace))
            continue
        if str(key) not in seen:
            continue
        merged = calls.get(str(key))
        if merged is None:
            merged = calls[str(key)] = {}
            entries.append(merged)
        result = trace.get("phase") == "result"
        for field, value in trace.items():
            if value is None or value == "":
                continue
            if result and field not in OUTCOME_FIELDS and field in merged:
                continue
            merged[field] = value
    return [_without_copies(entry) for entry in entries]


def _without_copies(entry: dict[str, Any]) -> dict[str, Any]:
    text = entry.get("input")
    if not isinstance(text, str):
        return entry
    try:
        # Input cut to its retained length isn't valid JSON and stays as it is.
        text = entry["input"] = json.dumps(
            json.loads(text), ensure_ascii=False, separators=(",", ":")
        )
    except ValueError:
        pass
    for field in ("detail", "command"):
        value = entry.get(field)
        if isinstance(value, str) and (
            value in text or json.dumps(value, ensure_ascii=False)[1:-1] in text
        ):
            del entry[field]
    return entry


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
