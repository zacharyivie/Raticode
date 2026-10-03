"""Interrupted responses and complete records, without real provider CLIs."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.subscriptions import cli_providers
from gofer.subscriptions.claude_code import ClaudeCodeSubscription
from gofer.subscriptions.cli_providers import stream_cli
from gofer.subscriptions.codex import CodexSubscription
from gofer.ui import chat_sessions
from gofer.ui.chat_jobs import ChatJobs
from gofer.ui.chat_sessions import with_chat_session
from gofer.utils.protocol import ProtocolLines


def child(tmp_path: Path, records: list[dict[str, Any]], stderr: str = "") -> list[str]:
    path = tmp_path / "provider.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return [
        sys.executable,
        "-c",
        "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes());"
        "sys.stdout.flush();sys.stderr.write(sys.argv[2])",
        str(path),
        stderr,
    ]


@pytest.mark.parametrize("provider", ["cursor", "opencode"])
async def test_real_protocol_survives_oversized_tools_and_stderr(tmp_path, provider):
    native = "native-id"
    if provider == "cursor":
        records = [
            {"type": "system", "session_id": native},
            {"type": "tool_call", "tool_call": {"readToolCall": {"result": "x" * 2_100_000}}},
            {"type": "result", "subtype": "success", "result": "Finished ✓"},
        ]
    else:
        records = [
            {"type": "step_start", "sessionID": native},
            {"type": "tool_use", "part": {"tool": "read", "state": {"output": "x" * 2_100_000}}},
            {"type": "text", "part": {"id": "answer", "text": "Finished ✓"}},
        ]
    events = [
        event
        async for event in stream_cli(
            child(tmp_path, records, "diagnostic\n" * 1000),
            provider,
            cwd=tmp_path,
            env={},
            max_output_bytes=125,
        )
    ]
    assert events[-1]["type"] == "final"
    assert events[-1]["message"]["body"] == "Finished ✓"
    assert events[-1]["sessionId"] == native
    assert events[-1]["processExitCode"] == 0
    tools = [event["trace"] for event in events if "trace" in event]
    assert tools and all(len(tool["output"]) <= 8192 for tool in tools)


@pytest.mark.parametrize("subscription_type", [CodexSubscription, ClaudeCodeSubscription])
async def test_workflow_subscription_preserves_answer_after_large_record(
    tmp_path, monkeypatch, subscription_type
):
    command = child(
        tmp_path,
        [
            {"type": "tool_use", "output": "x" * 2_100_000},
            {
                "type": "result",
                "result": "Finished",
                "usage": {"input_tokens": 7, "output_tokens": 2},
            },
        ],
    )
    subscription = subscription_type()
    monkeypatch.setattr(subscription, "_build_command", lambda *args: command)
    result = await subscription.execute("prompt", tmp_path, [], [], {}, max_output_bytes=125)
    assert result.success
    assert result.output == "Finished"
    assert result.usage_metadata["input_tokens"] == 7


async def test_provider_stderr_takes_priority_over_parse_failure(tmp_path):
    output = cli_providers.CliOutput("cursor")
    output.feed('{"cut"\n')
    output.finish(1, "Please sign in")
    assert output.error == "Please sign in"
    assert output.error_kind == "provider"
    assert output.parse_error == "Provider emitted malformed JSON output"


def test_oversized_record_has_explicit_limit_and_discards_buffer():
    lines = ProtocolLines(limit=12)
    lines.feed('{"long":')
    with pytest.raises(ValueError, match="protocol record exceeded"):
        lines.feed("x" * 20)
    assert lines.buffer == ""


def recovery_source(
    tmp_path: Path,
    failures: int = 1,
    error: dict[str, Any] | None = None,
    native: str | None = "native-id",
) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    closed: list[bool] = []

    @with_chat_session
    async def source(
        provider,
        model,
        messages,
        workflow,
        *,
        data_dir,
        cancel_event=None,
        permission_mode=None,
        _session=None,
    ):
        assert _session
        assert len(closed) == len(calls)
        calls.append(
            {
                "provider": provider,
                "model": model,
                "messages": messages,
                "workflow": workflow,
                "permission": permission_mode,
                "native": _session.native_id,
                "generation": _session.state["generation"],
            }
        )
        try:
            if native:
                yield {"type": "session", "sessionId": native}
            if len(calls) <= failures:
                yield {
                    "type": "error",
                    "error": "Provider emitted malformed JSON content",
                    "errorKind": "provider",
                    "message": {"body": "Work completed so far"},
                    **(error or {}),
                }
            else:
                yield {"type": "final", "message": {"body": "Finished"}}
        finally:
            closed.append(True)

    kwargs = {
        "provider": "cursor",
        "model": "selected-model",
        "messages": [{"role": "user", "body": "Fix the original issue"}],
        "workflow": {
            "chatThreadId": "thread",
            "projectRoot": str(tmp_path),
            "remResources": {"shell": False},
        },
        "data_dir": tmp_path / "data",
        "permission_mode": "cli-managed",
    }
    return source, kwargs, calls


async def test_recovery_reuses_exact_session_and_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_sessions, "RECOVERY_DELAYS", (0, 0))
    source, kwargs, calls = recovery_source(tmp_path)
    events = [event async for event in source(**kwargs)]
    assert [event["type"] for event in events] == ["session", "recovery", "session", "final"]
    assert events[1]["partial"]["body"] == "Work completed so far"
    assert events[-1]["recoveryAttempts"] == 1
    assert calls[1]["native"] == "native-id"
    assert calls[1]["generation"] == calls[0]["generation"]
    for key in ("provider", "model", "workflow", "permission"):
        assert calls[0][key] == calls[1][key]
    assert "Inspect" in calls[1]["messages"][0]["body"]
    assert calls[0]["messages"] == kwargs["messages"]


async def test_recovery_exhausts_after_two_continuations(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_sessions, "RECOVERY_DELAYS", (0, 0))
    source, kwargs, calls = recovery_source(tmp_path, failures=99)
    events = [event async for event in source(**kwargs)]
    assert len(calls) == 3
    assert sum(event["type"] == "error" for event in events) == 1
    assert events[-1]["recoveryAttempts"] == 2
    assert events[-1]["resumeAvailable"]


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "opencode"},
        {"native": None},
        {"error": {"error": "Please sign in"}},
        {"error": {"errorKind": "protocol"}},
        {"error": {"parseError": "Malformed wire record"}},
        {"error": {"errorKind": "output_limit"}},
        {"error": {"errorKind": "cancelled"}},
    ],
)
async def test_nonrecoverable_failures_do_not_repeat_task(tmp_path, changes):
    changes = dict(changes)
    provider = changes.pop("provider", "cursor")
    source, kwargs, calls = recovery_source(tmp_path, **changes)
    kwargs["provider"] = provider
    events = [event async for event in source(**kwargs)]
    assert len(calls) == 1
    assert events[-1]["type"] == "error"
    assert not any(event["type"] == "recovery" for event in events)


async def test_stop_during_backoff_releases_session_lease(tmp_path):
    source, kwargs, calls = recovery_source(tmp_path, failures=99)
    cancel = threading.Event()
    events = []
    async for event in source(**kwargs, cancel_event=cancel):
        events.append(event)
        if event["type"] == "recovery":
            cancel.set()
    assert events[-1]["type"] == "stopped"
    assert len(calls) == 1
    kwargs["messages"] += [{"role": "user", "body": "A follow-up"}]
    followup = [event async for event in source(**kwargs, cancel_event=cancel)]
    assert followup[-1]["type"] == "stopped"


async def test_session_lease_remains_held_during_recovery_and_is_cleared_on_close(tmp_path):
    source, kwargs, calls = recovery_source(tmp_path, failures=99)
    initial = source(**kwargs)
    try:
        assert (await anext(initial))["type"] == "session"
        assert (await anext(initial))["type"] == "recovery"
        competing = source(**kwargs)
        try:
            with pytest.raises(ValueError, match="active provider session"):
                await anext(competing)
        finally:
            await competing.aclose()
    finally:
        await initial.aclose()
    assert len(calls) == 1
    saved = next((tmp_path / "data" / "chat-provider-sessions").glob("*/session.json"))
    assert "recoveryAttempt" not in json.loads(saved.read_text())["sessions"]["cursor"]


def test_backend_recovery_survives_disconnect_without_relaunch(monkeypatch, tmp_path):
    monkeypatch.setattr(chat_sessions, "RECOVERY_DELAYS", (0, 0))
    source, kwargs, calls = recovery_source(tmp_path)
    jobs = ChatJobs(tmp_path / "data")

    def run(emit):
        async def consume() -> None:
            async for event in source(**kwargs):
                emit(event)

        asyncio.run(consume())

    jobs.start("thread", "turn", run)
    disconnected = jobs.events("thread", "turn")
    try:
        assert next(disconnected)["type"] == "session"
        checkpoint = next(disconnected)
        assert checkpoint["type"] == "recovery"
    finally:
        disconnected.close()
    replay = list(jobs.events("thread", "turn", after=checkpoint["sequence"]))
    assert replay[-1]["type"] == "final"
    assert len(calls) == 2
    journal = jobs.snapshot("thread", "turn")
    assert sum(event["type"] == "recovery" for event in journal) == 1
    assert sum(event["type"] == "final" for event in journal) == 1
    jobs.close()


async def test_copilot_truncation_is_an_explicit_error(tmp_path):
    events = [
        event
        async for event in stream_cli(
            child(tmp_path, [{"text": "x" * 5000}]),
            "copilot",
            cwd=tmp_path,
            env={},
            max_output_bytes=125,
        )
    ]
    assert events[-1]["type"] == "error"
    assert events[-1]["errorKind"] == "output_limit"
    assert events[-1]["processExitCode"] == 0
