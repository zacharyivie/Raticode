from __future__ import annotations

import asyncio
import copy
from typing import Any

import pytest

from gofer.ui.chat_steering import ChatSteering, ChatSteeringConflict


def request(text: str = "Use the new direction", request_id: str = "request-1") -> dict[str, Any]:
    return {
        "conversationId": "conversation-1",
        "turnId": "turn-1",
        "requestId": request_id,
        "text": text,
    }


@pytest.mark.parametrize(
    "provider", ["codex", "claude_code", "cursor", "copilot", "xai", "opencode"]
)
async def test_interrupt_wait_continue_preserves_context_and_orders_messages(tmp_path, provider):
    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin("conversation-1", "turn-1", provider, "custom-model")
    entered = asyncio.Event()
    release = asyncio.Event()
    closed = []
    invocations: list[dict[str, Any]] = []

    async def fake(**kwargs):
        index = len(invocations)
        invocations.append(copy.deepcopy({k: v for k, v in kwargs.items() if k != "cancel_event"}))
        if index == 0:
            try:
                entered.set()
                await release.wait()
                assert kwargs["cancel_event"].is_set()
                yield {
                    "type": "final",
                    "message": {"body": "partial reply"},
                    "changes": {"id": "edit"},
                }
            finally:
                closed.append(index)
        else:
            assert closed == [0]
            yield {"type": "final", "message": {"body": "redirected"}}

    async def consume() -> list[dict[str, Any]]:
        return [
            event
            async for event in coordinator.stream(
                turn,
                fake,
                messages=[{"role": "user", "body": "original"}],
                provider=provider,
                model="custom-model",
                workflow={"remResources": {"skills": ["s"]}},
            )
        ]

    task = asyncio.create_task(consume())
    await entered.wait()
    first = coordinator.steer(request())
    assert first["status"] == "interrupting"
    assert coordinator.steer(request()) == first
    coordinator.steer(request("Also preserve tests", "request-2"))
    release.set()
    events = await task
    assert len(invocations) == 2
    assert invocations[1]["workflow"] == invocations[0]["workflow"]
    assert invocations[1]["provider"] == provider
    assert invocations[1]["model"] == "custom-model"
    messages = invocations[1]["messages"]
    assert [m["body"] for m in messages if m["role"] == "user"] == [
        "original",
        "Use the new direction",
        "Also preserve tests",
    ]
    assert {"role": "assistant", "body": "partial reply"} in messages
    assert [e["generation"] for e in events if e["type"] == "turn"] == [0, 1]
    assert [e["message"]["body"] for e in events if e["type"] == "final"] == ["redirected"]
    assert next(e for e in events if e["type"] == "interrupted")["partial"]["changes"] == {
        "id": "edit"
    }
    assert [r["status"] for r in coordinator.receipts("conversation-1")] == [
        "delivered",
        "delivered",
    ]
    assert coordinator.steer(request())["status"] == "delivered"
    with pytest.raises(ChatSteeringConflict):
        coordinator.steer(request("conflict"))
    with pytest.raises(ChatSteeringConflict):
        coordinator.steer(request("late", "late"))


async def test_completion_wins_rejects_stale_steering(tmp_path):
    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin("conversation-1", "turn-1", "claude_code", "model")

    async def fake(**kwargs):
        yield {"type": "final", "message": {"body": "done"}}

    events = [e async for e in coordinator.stream(turn, fake)]
    assert events[-1]["type"] == "final"
    with pytest.raises(ChatSteeringConflict):
        coordinator.steer(request())
    assert coordinator.receipts("conversation-1") == []


async def test_stop_during_shutdown_prevents_successor(tmp_path):
    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin("conversation-1", "turn-1", "claude_code", "model")
    calls = 0

    async def fake(**kwargs):
        nonlocal calls
        calls += 1
        coordinator.steer(request())
        coordinator.stop("conversation-1", "turn-1")
        yield {"type": "error", "error": "cancelled"}

    events = [e async for e in coordinator.stream(turn, fake)]
    assert calls == 1
    assert events[-1]["type"] == "stopped"
    assert coordinator.receipts("conversation-1")[0]["status"] == "cancelled"


async def test_successor_start_failure_retains_instruction_without_replay(tmp_path):
    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin("conversation-1", "turn-1", "claude_code", "model")
    calls = 0

    async def fake(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            coordinator.steer(request())
            yield {"type": "error", "error": "interrupted"}
        else:
            raise OSError("missing executable")

    with pytest.raises(OSError, match="missing executable"):
        async for _ in coordinator.stream(turn, fake):
            pass
    assert calls == 2
    receipt = coordinator.receipts("conversation-1")[0]
    assert receipt["status"] == "failed"
    assert receipt["text"] == request()["text"]
    assert ChatSteering(tmp_path).receipts("conversation-1") == [receipt]


def test_restart_exposes_unconfirmed_delivery_without_starting_work(tmp_path):
    coordinator = ChatSteering(tmp_path)
    coordinator.begin("conversation-1", "turn-1", "codex", "model")
    coordinator.steer(request())
    recovered = ChatSteering(tmp_path)
    receipt = recovered.receipts("conversation-1")[0]
    assert receipt["status"] == "failed"
    assert "review before retry" in receipt["error"]
    assert recovered.steer(request()) == receipt


def test_persistence_failure_does_not_accept_or_interrupt(tmp_path, monkeypatch):
    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin("conversation-1", "turn-1", "codex", "model")

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(coordinator, "_save", fail)
    with pytest.raises(OSError, match="disk full"):
        coordinator.steer(request())
    assert not turn.cancel.is_set()
    assert not turn.pending
    assert coordinator.receipts("conversation-1") == []


@pytest.mark.parametrize(
    "field,value",
    [("conversationId", "../escape"), ("turnId", None), ("requestId", ""), ("text", " ")],
)
def test_invalid_steering_rejected(tmp_path, field, value):
    body = request()
    body[field] = value
    with pytest.raises(ValueError):
        ChatSteering(tmp_path).steer(body)


async def test_second_interruption_does_not_duplicate_first_instruction(tmp_path):
    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin("conversation-1", "turn-1", "codex", "model")
    calls: list[list[dict[str, str]]] = []

    async def fake(**kwargs):
        calls.append(copy.deepcopy(kwargs["messages"]))
        if len(calls) < 3:
            coordinator.steer(request(f"direction {len(calls)}", f"r{len(calls)}"))
            yield {"type": "error", "error": "interrupted"}
        else:
            yield {"type": "final", "message": {"body": "done"}}

    events = [event async for event in coordinator.stream(turn, fake, messages=[])]
    assert [m["body"] for m in calls[-1] if m["role"] == "user"] == ["direction 1", "direction 2"]
    receipts = coordinator.receipts("conversation-1")
    assert [r["status"] for r in receipts] == ["delivered", "delivered"]
    assert [r["generation"] for r in receipts] == [2, 2]
    assert sum(e["type"] == "final" for e in events) == 1


async def test_reused_turn_id_cannot_target_new_turn_after_restart(tmp_path):
    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin("conversation-1", "turn-1", "codex", "model")

    async def fake(**kwargs):
        yield {"type": "final", "message": {"body": "done"}}

    async for _ in coordinator.stream(turn, fake):
        pass
    recovered = ChatSteering(tmp_path)
    with pytest.raises(ChatSteeringConflict, match="already been used"):
        recovered.begin("conversation-1", "turn-1", "claude_code", "other-model")
    next_turn = recovered.begin("conversation-1", "turn-2", "claude_code", "other-model")
    assert next_turn.provider == "claude_code"
    with pytest.raises(ChatSteeringConflict):
        recovered.steer(request())


async def test_disconnect_closes_source_and_does_not_resume(tmp_path):
    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin("conversation-1", "turn-1", "codex", "model")
    closed = asyncio.Event()

    async def fake(**kwargs):
        try:
            yield {"type": "thought", "text": "partial"}
            pytest.fail("Disconnected consumer must not request another provider event")
        finally:
            closed.set()

    stream = coordinator.stream(turn, fake)
    assert (await anext(stream))["type"] == "turn"
    assert (await anext(stream))["type"] == "thought"
    coordinator.steer(request())
    await stream.aclose()
    assert closed.is_set()
    assert turn.cancel.is_set()
    assert coordinator.receipts("conversation-1")[0]["status"] == "failed"


async def test_stop_before_first_provider_spawn(tmp_path):
    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin("conversation-1", "turn-1", "codex", "model")

    async def fake(**kwargs):
        pytest.fail("Stopped turn must not spawn a provider")
        yield {}

    stream = coordinator.stream(turn, fake)
    await anext(stream)
    coordinator.steer(request())
    coordinator.stop("conversation-1", "turn-1")
    assert (await anext(stream))["type"] == "stopped"
    await stream.aclose()
    assert coordinator.receipts("conversation-1")[0]["status"] == "cancelled"


@pytest.mark.parametrize("intervention", ["steer", "stop"])
async def test_receipt_yield_rechecks_cancellation_before_provider_content(tmp_path, intervention):
    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin("conversation-1", "turn-1", "codex", "model")
    calls = 0

    async def fake(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            coordinator.steer(request())
            yield {"type": "error", "error": "interrupted"}
        else:
            yield {"type": "thought", "text": "old thought"}
            yield {"type": "final", "message": {"body": "done"}}

    source = coordinator.stream(turn, fake)
    events = []
    async for event in source:
        events.append(event)
        if event["type"] == "steering" and event["receipt"]["requestId"] == "request-1":
            if intervention == "stop":
                coordinator.stop("conversation-1", "turn-1")
            else:
                coordinator.steer(request("second instruction", "request-2"))
    assert not [
        event for event in events if event["type"] == "thought" and event["generation"] == 1
    ]
    assert events[-1]["type"] == ("stopped" if intervention == "stop" else "final")


@pytest.mark.parametrize("intervention", ["delivery", "stop"])
async def test_terminal_receipt_recovers_after_disk_failure(tmp_path, monkeypatch, intervention):
    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin("conversation-1", "turn-1", "codex", "model")
    save = coordinator._save
    calls = 0

    def fail(*args):
        raise OSError("disk unavailable")

    async def fake(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            coordinator.steer(request())
            if intervention == "stop":
                monkeypatch.setattr(coordinator, "_save", fail)
                coordinator.stop("conversation-1", "turn-1")
            yield {"type": "error", "error": "interrupted"}
        else:
            monkeypatch.setattr(coordinator, "_save", fail)
            yield {"type": "final", "message": {"body": "done"}}

    with pytest.raises(OSError, match="disk unavailable"):
        async for _ in coordinator.stream(turn, fake):
            pass
    assert turn.cancel.is_set()
    monkeypatch.setattr(coordinator, "_save", save)
    receipt = coordinator.receipts("conversation-1")[0]
    assert receipt["status"] == ("cancelled" if intervention == "stop" else "failed")
    assert receipt["text"] == request()["text"]
    assert coordinator.steer(request()) == receipt
    assert ChatSteering(tmp_path).receipts("conversation-1") == [receipt]
    assert calls == (1 if intervention == "stop" else 2)


@pytest.mark.parametrize("intervention", ["steer", "stop"])
async def test_cancel_during_compaction_drains_before_successor(
    tmp_path, monkeypatch, intervention
):
    from gofer.ui import chat

    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin("conversation-1", "turn-1", "codex", "cli-default")
    entered = asyncio.Event()
    drained = asyncio.Event()
    summary_calls = 0
    answer_calls = 0
    monkeypatch.setattr(chat, "resolve_provider_executable", lambda _: "/fake/codex")
    monkeypatch.setattr(chat, "ensure_local_gofer_cli", lambda _: "/fake/gof")
    monkeypatch.setattr(chat, "CHAT_COMPACT_CHAR_LIMIT", 20)

    async def summarize(*args, **kwargs):
        nonlocal summary_calls
        summary_calls += 1
        if summary_calls == 1:
            assert kwargs["cancel_event"] is turn.cancel
            entered.set()
            while not kwargs["cancel_event"].is_set():
                await asyncio.sleep(0)
            drained.set()
            return -15, "", "cancelled"
        assert drained.is_set()
        return 0, "summary", ""

    async def answer(*args, **kwargs):
        nonlocal answer_calls
        answer_calls += 1
        assert drained.is_set()
        assert not kwargs["cancel_event"].is_set()
        yield {"type": "chunk", "stream": "stdout", "text": "answer\n", "returncode": None}
        yield {"type": "exit", "stream": None, "text": "", "returncode": 0}

    monkeypatch.setattr(chat, "run_subprocess", summarize)
    monkeypatch.setattr(chat, "stream_subprocess", answer)

    async def consume() -> list[dict[str, Any]]:
        return [
            event
            async for event in coordinator.stream(
                turn,
                chat.stream_workflow_chat,
                provider="codex",
                model="cli-default",
                messages=[{"role": "user", "body": "original " * 20}],
                workflow=None,
                working_dir=tmp_path,
                data_dir=tmp_path,
            )
        ]

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        coordinator.steer(request())
        if intervention == "stop":
            coordinator.stop("conversation-1", "turn-1")
        events = await asyncio.wait_for(task, 2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert drained.is_set()
    assert answer_calls == (0 if intervention == "stop" else 1)
    assert not [e for e in events if e["type"] == "final" and e["generation"] == 0]
    assert events[-1]["type"] == ("stopped" if intervention == "stop" else "final")


@pytest.mark.parametrize("text", ["Read this", ""])
async def test_steering_attachments_survive_restart_and_retry(tmp_path, text):
    import base64

    from gofer.ui.chat import _messages_with_attachment_paths
    from gofer.ui.chat_media import store_chat_attachments

    attachments = store_chat_attachments(
        {
            "threadId": "conversation-1",
            "files": [
                {
                    "name": "evidence.txt",
                    "type": "text/plain",
                    "data": base64.b64encode(b"evidence").decode(),
                }
            ],
        },
        tmp_path,
    )["attachments"]
    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin("conversation-1", "turn-1", "codex", "model")
    body = {**request(text), "attachments": attachments}
    receipt = coordinator.steer(body)
    assert coordinator.steer(body) == receipt
    with pytest.raises(ChatSteeringConflict):
        coordinator.steer({**body, "attachments": [{**attachments[0], "name": "different.txt"}]})
    calls = []

    async def fake(**kwargs):
        calls.append(copy.deepcopy(kwargs["messages"]))
        yield {"type": "final", "message": {"body": "done"}}

    events = [event async for event in coordinator.stream(turn, fake, messages=[])]
    assert calls[-1][-1]["attachments"] == attachments
    prepared, images = _messages_with_attachment_paths(
        calls[-1], workflow={"chatThreadId": "conversation-1"}, data_dir=tmp_path
    )
    assert "evidence.txt" in prepared[-1]["body"]
    assert not images
    assert any(event.get("receipt", {}).get("status") == "delivered" for event in events)
    assert ChatSteering(tmp_path).receipts("conversation-1")[0]["attachments"] == attachments


def test_invalid_steering_attachment_does_not_interrupt_turn(tmp_path):
    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin("conversation-1", "turn-1", "codex", "model")
    with pytest.raises(ValueError, match="reference is invalid"):
        coordinator.steer({**request(), "attachments": [{"storageName": "../secret"}]})
    assert not turn.cancel.is_set()
    assert not coordinator.receipts("conversation-1")


def test_steering_uses_turn_data_directory_and_checks_image_support(tmp_path):
    import base64

    from gofer.ui.chat_media import store_chat_attachments

    attachment_dir = tmp_path / "selected-data"
    attachments = store_chat_attachments(
        {
            "threadId": "conversation-1",
            "files": [
                {
                    "name": "diagram.png",
                    "type": "image/png",
                    "data": base64.b64encode(b"image").decode(),
                }
            ],
        },
        attachment_dir,
    )["attachments"]
    coordinator = ChatSteering(tmp_path)
    turn = coordinator.begin(
        "conversation-1", "turn-1", "grok", "grok-4.6", data_dir=attachment_dir
    )
    with pytest.raises(ValueError, match="image attachments are not supported"):
        coordinator.steer({**request(), "attachments": attachments})
    assert not turn.cancel.is_set()
    codex = ChatSteering(tmp_path / "other-server")
    codex.begin("conversation-1", "turn-1", "codex", "model", data_dir=attachment_dir)
    assert codex.steer({**request(""), "attachments": attachments})["attachments"] == attachments
