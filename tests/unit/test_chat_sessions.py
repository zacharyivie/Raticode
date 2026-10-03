"""Native Rem continuation with provider doubles, never authenticated inference."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.subscriptions import antigravity, cli_providers
from gofer.ui import chat
from gofer.ui.chat_sessions import with_chat_session
from gofer.utils.process import stream_subprocess as real_stream_subprocess


@pytest.fixture
def native_cli(monkeypatch, tmp_path):
    calls = []
    ids: dict[str, int] = {}

    async def validate(*args):
        pass

    async def subprocess(command, **kwargs):
        provider = command[0]
        flag = "-p" if provider in {"claude_code", "cursor", "copilot"} else None
        prompt = command[command.index(flag) + 1] if flag else command[-1]
        if provider == "antigravity":
            prompt = json.loads(kwargs["stdin"])["message"]["content"]
        calls.append((provider, command, prompt, kwargs))
        resume_flag = {
            "codex": "resume",
            "claude_code": "--resume",
            "cursor": "--resume",
            "opencode": "--session",
            "antigravity": "--conversation",
            "copilot": "--session-id",
        }[provider]
        if resume_flag in command:
            native = command[command.index(resume_flag) + 1]
        else:
            ids[provider] = ids.get(provider, 0) + 1
            native = f"{provider}-{ids[provider]}"
        records = []
        if provider == "codex":
            records = [
                {"type": "thread.started", "thread_id": native},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "Answer"}},
            ]
        elif provider == "claude_code":
            records = [{"type": "result", "result": "Answer", "session_id": native}]
        elif provider == "cursor":
            records = [
                {"type": "result", "subtype": "success", "result": "Answer", "session_id": native}
            ]
        elif provider == "opencode":
            records = [{"type": "text", "part": {"text": "Answer"}, "sessionID": native}]
        elif provider == "antigravity":
            records = [
                {"event": "init", "conversation_id": native},
                {
                    "event": "result",
                    "result": {
                        "response": "Answer",
                        "status": "SUCCESS",
                        "conversation_id": native,
                    },
                },
            ]
        output = (
            "Answer" if provider == "copilot" else "".join(json.dumps(r) + "\n" for r in records)
        )
        yield {"type": "chunk", "stream": "stdout", "text": output}
        yield {"type": "exit", "returncode": 0}

    monkeypatch.setattr(chat, "validate_provider_selection_async", validate)
    monkeypatch.setattr(chat, "provider_preference", lambda _: {})
    monkeypatch.setattr(chat, "resolve_provider_executable", lambda p: p)
    monkeypatch.setattr(chat, "ensure_local_gofer_cli", lambda _: None)
    monkeypatch.setattr(chat, "with_second_brain", lambda w, *a: w)
    monkeypatch.setattr(chat, "with_report_outputs", lambda w, *a: w)
    monkeypatch.setattr(chat, "stream_subprocess", subprocess)
    monkeypatch.setattr(cli_providers, "stream_subprocess", subprocess)
    monkeypatch.setattr(antigravity, "stream_subprocess", subprocess)
    monkeypatch.setattr(chat, "check_cursor_plugins", validate)
    (tmp_path / "project").mkdir()
    return calls


async def turn(
    tmp_path: Path, provider: str, messages: list[dict[str, Any]], **kwargs: Any
) -> list[dict[str, Any]]:
    return [
        e
        async for e in chat.stream_workflow_chat(
            provider,
            kwargs.pop("model", "cli-default"),
            messages,
            kwargs.pop("workflow", {"projectRoot": str(tmp_path / "project")}),
            conversation_id=kwargs.pop("conversation_id", "thread-a"),
            permission_mode="cli-managed" if provider == "antigravity" else None,
            data_dir=tmp_path / "data",
            **kwargs,
        )
    ]


@pytest.mark.parametrize(
    "provider", ["codex", "claude_code", "cursor", "copilot", "opencode", "antigravity"]
)
async def test_followup_resumes_native_context_and_omits_seen_messages(
    provider, native_cli, tmp_path
):
    first = [{"role": "user", "body": "Read secret-project-plan.txt"}]
    initial = await turn(tmp_path, provider, first)
    second = await turn(
        tmp_path,
        provider,
        first
        + [
            {"role": "assistant", "body": "File contents and tool output already seen"},
            {"role": "user", "body": "Continue using what you read"},
        ],
    )
    assert initial[-1]["type"] == second[-1]["type"] == "final"
    assert initial[-1]["sessionId"] == second[-1]["sessionId"]
    assert second[-1]["sessionResumed"] is True
    assert "Read secret-project-plan.txt" not in native_cli[-1][2]
    assert "File contents and tool output already seen" not in native_cli[-1][2]
    assert "Continue using what you read" in native_cli[-1][2]
    assert "You are Rem" in native_cli[0][2] and "You are Rem" not in native_cli[1][2]
    assert "Project root:" not in native_cli[1][2]
    states = list((tmp_path / "data/chat-provider-sessions").glob("*/session.json"))
    assert len(states) == 1 and "secret-project-plan" not in states[0].read_text()
    if provider == "cursor":
        configs = [c[3]["env"]["CURSOR_CONFIG_DIR"] for c in native_cli]
        assert configs[0] == configs[1] and Path(configs[0]).exists()


async def test_model_changes_keep_session_and_refresh_changed_context(native_cli, tmp_path):
    first = [{"role": "user", "body": "First goal"}]
    initial = await turn(tmp_path, "codex", first)
    followup = await turn(
        tmp_path,
        "codex",
        first + [{"role": "user", "body": "Next"}],
        model="custom-model",
        workflow={"projectRoot": str(tmp_path / "project"), "openFiles": ["new.py"]},
    )
    assert followup[-1]["sessionId"] == initial[-1]["sessionId"]
    assert "First goal" not in native_cli[-1][2]
    assert "Requested model: custom-model" in native_cli[-1][2] and "new.py" in native_cli[-1][2]


@pytest.mark.parametrize(
    "provider", ["codex", "claude_code", "cursor", "copilot", "opencode", "antigravity"]
)
async def test_three_turns_two_foreign_turns_then_three_turns_resume_with_only_missing_history(
    provider, native_cli, tmp_path
):
    foreign = "cursor" if provider == "codex" else "codex"
    messages = []
    originals = []
    for index in range(3):
        messages.append({"role": "user", "body": f"Original request {index}"})
        originals.append((await turn(tmp_path, provider, messages))[-1])
        messages.append({"role": "assistant", "body": f"Original reply {index}"})
    original_config = native_cli[-1][3].get("env", {}).get("CURSOR_CONFIG_DIR")
    for index in range(2):
        messages.append({"role": "user", "body": f"Foreign request {index}"})
        switched = (await turn(tmp_path, foreign, messages))[-1]
        if index == 0:
            assert switched["sessionResumed"] is False
            assert all(f"Original request {i}" in native_cli[-1][2] for i in range(3))
            assert all(f"Original reply {i}" in native_cli[-1][2] for i in range(3))
        messages.append({"role": "assistant", "body": f"Foreign tool output {index}"})
        messages.append({"role": "assistant", "body": f"Foreign reply {index}"})
    for index in range(3):
        messages.append({"role": "user", "body": f"Returned request {index}"})
        before = json.loads(json.dumps(messages))
        returned = (await turn(tmp_path, provider, messages))[-1]
        assert messages == before  # Provider selection never prunes Rem's transcript.
        assert returned["sessionId"] == originals[0]["sessionId"]
        assert returned["sessionResumed"] is True
        prompt = native_cli[-1][2]
        assert "Original request" not in prompt and "Original reply" not in prompt
        assert f"Returned request {index}" in prompt
        if index == 0:
            for i in range(2):
                assert f"Foreign request {i}" in prompt
                assert f"Foreign reply {i}" in prompt
                assert f"Foreign tool output {i}" in prompt
        else:
            assert "Foreign" not in prompt
            assert f"Returned request {index - 1}" not in prompt
        if provider == "cursor":
            assert native_cli[-1][3]["env"]["CURSOR_CONFIG_DIR"] == original_config
        messages.append({"role": "assistant", "body": f"Returned reply {index}"})
    # Switching back to the other provider also catches up only once.
    messages.append({"role": "user", "body": "Foreign provider returns"})
    returned_foreign = (await turn(tmp_path, foreign, messages))[-1]
    assert returned_foreign["sessionId"] == switched["sessionId"]
    assert "Original" not in native_cli[-1][2] and "Foreign request" not in native_cli[-1][2]
    assert all(f"Returned reply {i}" in native_cli[-1][2] for i in range(3))


async def test_edited_old_message_starts_new_session(native_cli, tmp_path):
    first = await turn(tmp_path, "codex", [{"role": "user", "body": "Wrong premise"}])
    edited = await turn(tmp_path, "codex", [{"role": "user", "body": "Correct premise"}])
    assert edited[-1]["sessionId"] != first[-1]["sessionId"]
    assert "Wrong premise" not in native_cli[-1][2]


@pytest.mark.parametrize("explicit_reset", [False, True])
async def test_edits_invalidate_dormant_provider_sessions(native_cli, tmp_path, explicit_reset):
    messages = [{"role": "user", "body": "Old premise"}]
    codex = (await turn(tmp_path, "codex", messages))[-1]
    messages.append({"role": "user", "body": "Cursor turn"})
    cursor = (await turn(tmp_path, "cursor", messages))[-1]
    if not explicit_reset:
        messages[0] = {"role": "user", "body": "New premise"}
    edited = (await turn(tmp_path, "cursor", messages, reset_session=explicit_reset))[-1]
    assert edited["sessionId"] != cursor["sessionId"]
    messages.append({"role": "user", "body": "Codex returns"})
    returned = (await turn(tmp_path, "codex", messages))[-1]
    assert returned["sessionId"] != codex["sessionId"]
    assert returned["sessionResumed"] is False


async def test_legacy_reference_migrates_without_losing_native_session(native_cli, tmp_path):
    messages = [{"role": "user", "body": "Keep my native tools"}]
    first = (await turn(tmp_path, "codex", messages))[-1]
    path = next((tmp_path / "data/chat-provider-sessions").glob("*/session.json"))
    legacy = json.loads(path.read_text())["sessions"]["codex"]
    path.write_text(json.dumps(legacy))
    messages.append({"role": "user", "body": "Now use Cursor"})
    await turn(tmp_path, "cursor", messages)
    messages.append({"role": "user", "body": "Back to Codex"})
    returned = (await turn(tmp_path, "codex", messages))[-1]
    saved = json.loads(path.read_text())
    assert saved["version"] == 2 and set(saved["sessions"]) == {"codex", "cursor"}
    assert saved["sessions"]["codex"]["generation"] == legacy["generation"]
    assert returned["sessionId"] == first["sessionId"]
    assert "Keep my native tools" not in native_cli[-1][2]


async def test_failed_foreign_provider_does_not_discard_original_session(
    native_cli, monkeypatch, tmp_path
):
    messages = [{"role": "user", "body": "Original goal"}]
    original = (await turn(tmp_path, "codex", messages))[-1]

    async def unidentified(command, **kwargs):
        yield {"type": "exit", "returncode": 1}

    monkeypatch.setattr(cli_providers, "stream_subprocess", unidentified)
    messages.append({"role": "user", "body": "Failed Cursor request"})
    assert (await turn(tmp_path, "cursor", messages))[-1]["type"] == "error"
    messages.append({"role": "user", "body": "Resume Codex"})
    returned = (await turn(tmp_path, "codex", messages))[-1]
    assert returned["sessionId"] == original["sessionId"]
    assert "Failed Cursor request" in native_cli[-1][2]
    assert "Original goal" not in native_cli[-1][2]


async def test_threads_are_isolated_and_duplicate_requests_do_not_run(native_cli, tmp_path):
    messages = [{"role": "user", "body": "Goal"}]
    first = await turn(tmp_path, "codex", messages)
    other = await turn(tmp_path, "codex", messages, conversation_id="thread-b")
    assert other[-1]["sessionId"] != first[-1]["sessionId"]
    with pytest.raises(ValueError, match="already sent"):
        await turn(tmp_path, "codex", messages)
    assert len(native_cli) == 2


@pytest.mark.parametrize("provider", ["codex", "claude_code"])
async def test_native_thread_skips_raticode_summary_calls(
    native_cli, monkeypatch, tmp_path, provider
):
    async def compact(**kwargs):
        pytest.fail("Native context must use provider compaction")

    monkeypatch.setattr(chat, "_compact_chat_messages_if_needed", compact)
    messages = [{"role": "user", "body": "x" * 4_000_000}]
    await turn(tmp_path, provider, messages)
    await turn(tmp_path, provider, messages + [{"role": "user", "body": "next"}])
    assert len(native_cli[-1][2]) < 200


@pytest.mark.parametrize(
    ("source", "destination", "compacts"),
    [("claude_code", "codex", True), ("codex", "claude_code", False)],
)
async def test_half_million_token_handoff_uses_destination_budget(
    native_cli, monkeypatch, tmp_path, source, destination, compacts
):
    summaries = []

    async def summarize(**kwargs):
        summaries.append(kwargs["messages"])
        return "Imported history summary"

    monkeypatch.setattr(chat, "_summarize_chat_messages", summarize)
    messages = [{"role": "user", "body": "Original goal"}]
    await turn(tmp_path, source, messages)
    evidence = "x" * 2_000_000  # 500k estimated tokens, not 500k characters.
    messages += [
        {"role": "assistant", "body": evidence},
        {"role": "user", "body": "Continue with the new provider"},
    ]
    switched = await turn(tmp_path, destination, messages)
    assert any(e["type"] == "compaction" for e in switched) is compacts
    assert bool(summaries) is compacts
    assert (evidence in native_cli[-1][2]) is not compacts
    assert messages[1]["body"] == evidence
    assert "Continue with the new provider" in native_cli[-1][2]
    if compacts:
        assert 1 < len(summaries) < 10
        assert evidence in next((tmp_path / "data/chat-context").glob("*.txt")).read_text()


@pytest.mark.parametrize(
    "provider", ["codex", "claude_code", "cursor", "copilot", "opencode", "antigravity", "grok"]
)
@pytest.mark.parametrize("over_budget", [-1, 0, 1])
async def test_handoff_compacts_strictly_above_eighty_percent(
    monkeypatch, tmp_path, provider, over_budget
):
    from gofer.core.provider_context import estimate_tokens, handoff_token_budget
    from gofer.core.resources import DEFAULT_RESOURCE_LIMITS

    async def summarize(**kwargs):
        return "Summary"

    monkeypatch.setattr(chat, "_summarize_chat_messages", summarize)
    messages = [{"role": "assistant", "body": "Imported evidence"}]
    budget = handoff_token_budget(provider, "cli-default")
    history_tokens = estimate_tokens(chat._messages_transcript(messages))
    _, compacted = await chat._compact_chat_messages_if_needed(
        provider=provider,
        model="cli-default",
        effort=None,
        messages=messages,
        binary_path=provider,
        data_dir=tmp_path,
        working_dir=tmp_path,
        limits=DEFAULT_RESOURCE_LIMITS,
        token_budget=budget,
        additional_tokens=budget - history_tokens + over_budget,
    )
    assert compacted is (over_budget > 0)


async def test_handoff_counts_current_prompt_but_keeps_it_verbatim(
    native_cli, monkeypatch, tmp_path
):
    summaries = []

    async def summarize(**kwargs):
        summaries.append(kwargs["messages"])
        return "Imported history summary"

    monkeypatch.setattr(chat, "_summarize_chat_messages", summarize)
    messages = [{"role": "user", "body": "Original goal"}]
    await turn(tmp_path, "claude_code", messages)
    request = "z" * 320_000  # History fits alone; adding the 80k-token request does not.
    messages += [
        {"role": "assistant", "body": "x" * 600_000},
        {"role": "user", "body": request},
    ]
    switched = await turn(tmp_path, "codex", messages)
    assert switched[0]["type"] == "compaction"
    assert request in native_cli[-1][2]
    assert all(request not in part[0]["body"] for part in summaries)


@pytest.fixture
def small_handoff_budgets(monkeypatch):
    from gofer.core.provider_context import PROVIDER_CONTEXT_TOKENS

    # Exercise summaries/cancellation cheaply; production limits have separate
    # boundary and 500k-token handoff regressions below.
    monkeypatch.setitem(PROVIDER_CONTEXT_TOKENS, "codex", 10_000)
    monkeypatch.setitem(PROVIDER_CONTEXT_TOKENS, "cursor", 10_000)


async def test_large_provider_handoffs_compact_only_imported_history(
    native_cli, monkeypatch, tmp_path, small_handoff_budgets
):
    summaries: list[str] = []

    async def summarize(**kwargs):
        summaries.extend(m["body"] for m in kwargs["messages"])
        return "Summary of the imported provider history."

    monkeypatch.setattr(chat, "_summarize_chat_messages", summarize)
    messages = [{"role": "user", "body": "Original goal"}]
    original = (await turn(tmp_path, "codex", messages))[-1]
    messages.append({"role": "assistant", "body": "Original evidence " * 2500})
    messages.append({"role": "user", "body": "Cursor request verbatim " * 2000})
    before = json.loads(json.dumps(messages))
    switched = await turn(tmp_path, "cursor", messages)
    assert switched[0]["type"] == "compaction"
    assert switched[0]["scope"] == "provider-handoff"
    assert "messages" not in switched[0]  # Never replace the thread's shared context.
    assert messages == before
    assert "Summary of the imported provider history" in native_cli[-1][2]
    assert messages[-1]["body"] in native_cli[-1][2]
    assert "Original evidence" not in native_cli[-1][2]
    assert "Cursor request verbatim" not in "".join(summaries)
    assert "Original evidence" in next((tmp_path / "data/chat-context").glob("*.txt")).read_text()
    count = len(summaries)
    messages.append({"role": "assistant", "body": "Cursor evidence " * 2500})
    messages.append({"role": "user", "body": "Continue Cursor"})
    continued = await turn(tmp_path, "cursor", messages)
    assert continued[-1]["sessionId"] == switched[-1]["sessionId"]
    assert len(summaries) == count
    assert not any(e["type"] == "compaction" for e in continued)
    messages.append({"role": "assistant", "body": "Cursor final reply"})
    messages.append({"role": "user", "body": "Return to Codex"})
    returned = await turn(tmp_path, "codex", messages)
    assert returned[0]["type"] == "compaction"
    assert returned[-1]["sessionId"] == original["sessionId"]
    assert returned[-1]["sessionResumed"] is True
    assert len(summaries) > count
    assert "Original evidence" not in "".join(summaries[count:])
    assert "Cursor evidence" in "".join(summaries[count:])
    assert "Return to Codex" in native_cli[-1][2]
    count = len(summaries)
    messages.append({"role": "user", "body": "Codex follow-up"})
    followed = await turn(tmp_path, "codex", messages)
    assert followed[-1]["sessionId"] == original["sessionId"]
    assert len(summaries) == count
    assert not any(e["type"] == "compaction" for e in followed)


async def test_fork_and_edit_start_fresh_without_raticode_compaction(
    native_cli, monkeypatch, tmp_path
):
    async def compact(**kwargs):
        pytest.fail("Forks and edits leave compaction to their new provider session")

    monkeypatch.setattr(chat, "_compact_chat_messages_if_needed", compact)
    first = [{"role": "user", "body": "Original premise"}]
    original = (await turn(tmp_path, "codex", first))[-1]
    messages = first + [
        {"role": "assistant", "body": "Long original evidence " * 2500},
        {"role": "user", "body": "Explore another approach"},
    ]
    path = next((tmp_path / "data/chat-provider-sessions").glob("*/session.json"))
    parent_state = path.read_text()
    forked = (await turn(tmp_path, "codex", messages, conversation_id="forked-thread"))[-1]
    assert forked["sessionId"] != original["sessionId"]
    assert forked["sessionResumed"] is False
    assert path.read_text() == parent_state
    assert "Long original evidence" in native_cli[-1][2]
    resumed = (await turn(tmp_path, "codex", messages))[-1]
    assert resumed["sessionId"] == original["sessionId"]
    edited = (await turn(tmp_path, "codex", messages, reset_session=True))[-1]
    assert edited["sessionId"] not in {original["sessionId"], forked["sessionId"]}
    assert edited["sessionResumed"] is False


async def test_provider_handoff_detection_migrates_existing_references(
    native_cli, monkeypatch, tmp_path, small_handoff_budgets
):
    messages = [{"role": "user", "body": "First goal"}]
    original = (await turn(tmp_path, "codex", messages))[-1]
    path = next((tmp_path / "data/chat-provider-sessions").glob("*/session.json"))
    document = json.loads(path.read_text())
    document.pop("lastProvider")
    path.write_text(json.dumps(document))

    async def summarize(**kwargs):
        return "Migrated handoff summary"

    monkeypatch.setattr(chat, "_summarize_chat_messages", summarize)
    messages += [
        {"role": "assistant", "body": "Earlier evidence " * 2500},
        {"role": "user", "body": "Now use Cursor"},
    ]
    switched = await turn(tmp_path, "cursor", messages)
    assert switched[0]["type"] == "compaction"
    document = json.loads(path.read_text())
    assert document["lastProvider"] == "cursor"
    assert document["sessions"]["codex"]["nativeId"] == original["sessionId"]


async def test_cancelled_handoff_does_not_consume_request_or_replace_parent_session(
    native_cli, monkeypatch, tmp_path, small_handoff_budgets
):
    messages = [{"role": "user", "body": "Original goal"}]
    original = (await turn(tmp_path, "codex", messages))[-1]
    messages += [
        {"role": "assistant", "body": "Earlier evidence " * 2500},
        {"role": "user", "body": "Switch to Cursor"},
    ]
    cancel = threading.Event()

    async def summarize(**kwargs):
        if kwargs["cancel_event"] is not None:
            kwargs["cancel_event"].set()
        return "Handoff summary"

    monkeypatch.setattr(chat, "_summarize_chat_messages", summarize)
    assert await turn(tmp_path, "cursor", messages, cancel_event=cancel) == []
    assert cancel.is_set() and len(native_cli) == 1
    path = next((tmp_path / "data/chat-provider-sessions").glob("*/session.json"))
    document = json.loads(path.read_text())
    assert document["lastProvider"] == "codex"
    assert document["sessions"]["codex"]["nativeId"] == original["sessionId"]
    assert not document["sessions"]["cursor"].get("deliveryUncertain")
    retried = await turn(tmp_path, "cursor", messages)
    assert retried[0]["type"] == "compaction"
    assert retried[-1]["type"] == "final"
    assert "Switch to Cursor" in native_cli[-1][2]


async def test_interrupted_provider_id_is_kept_without_replaying_partial_response(
    native_cli, monkeypatch, tmp_path
):
    async def interrupted(command, **kwargs):
        yield {
            "type": "chunk",
            "stream": "stdout",
            "text": json.dumps({"type": "thread.started", "thread_id": "kept-id"}) + "\n",
        }
        yield {"type": "exit", "returncode": 1}

    monkeypatch.setattr(chat, "stream_subprocess", interrupted)
    first = [{"role": "user", "body": "Start work"}]
    error = await turn(tmp_path, "codex", first)
    assert error[-1]["type"] == "error"
    monkeypatch.setattr(chat, "stream_subprocess", cli_providers.stream_subprocess)
    followup = await turn(
        tmp_path,
        "codex",
        first
        + [
            {"role": "assistant", "body": "Partial native output"},
            {"role": "system", "body": "Inspect completed effects before repeating"},
            {"role": "user", "body": "Steering request"},
        ],
    )
    assert followup[-1]["sessionId"] == "kept-id"
    assert (
        "Start work" not in native_cli[-1][2] and "Partial native output" not in native_cli[-1][2]
    )
    assert "Inspect completed effects" in native_cli[-1][2]


def test_session_references_survive_new_event_loops(native_cli, tmp_path):
    messages = [{"role": "user", "body": "Persist this"}]
    first = asyncio.run(turn(tmp_path, "codex", messages))
    next_turn = asyncio.run(turn(tmp_path, "codex", messages + [{"role": "user", "body": "Later"}]))
    assert first[-1]["sessionId"] == next_turn[-1]["sessionId"]


async def test_live_sessions_reject_concurrent_turns_and_release_after_close(tmp_path):
    @with_chat_session
    async def source(provider, messages, workflow, data_dir, conversation_id, _session=None):
        yield {"type": "thought", "text": "Working"}

    kwargs = dict(
        provider="codex",
        messages=[{"role": "user", "body": "Goal"}],
        workflow=None,
        data_dir=tmp_path,
        conversation_id="same",
    )
    first = source(**kwargs)
    assert (await anext(first))["type"] == "thought"
    with pytest.raises(ValueError, match="active provider session"):
        _ = [e async for e in source(**kwargs)]
    await first.aclose()
    assert len([e async for e in source(**kwargs)]) == 1


async def test_cancelled_before_start_does_not_drop_the_new_request(native_cli, tmp_path):
    cancel = threading.Event()
    cancel.set()
    messages = [{"role": "user", "body": "Goal"}]
    assert await turn(tmp_path, "codex", messages, cancel_event=cancel) == []
    assert (await turn(tmp_path, "codex", messages))[-1]["type"] == "final"
    assert len(native_cli) == 1


async def test_explicit_resend_restarts_native_session(native_cli, tmp_path):
    messages = [{"role": "user", "body": "Same text, intentional repeat"}]
    first = await turn(tmp_path, "codex", messages)
    resent = await turn(tmp_path, "codex", messages, reset_session=True)
    assert first[-1]["sessionId"] != resent[-1]["sessionId"]
    assert resent[-1]["sessionResumed"] is False


async def test_missing_identity_does_not_silently_replay_history(native_cli, monkeypatch, tmp_path):
    async def unidentified(command, **kwargs):
        yield {"type": "exit", "returncode": 1}

    monkeypatch.setattr(chat, "stream_subprocess", unidentified)
    first = [{"role": "user", "body": "May have changed files"}]
    assert (await turn(tmp_path, "codex", first))[-1]["type"] == "error"
    monkeypatch.setattr(chat, "stream_subprocess", cli_providers.stream_subprocess)
    with pytest.raises(ValueError, match="without a native session ID"):
        await turn(tmp_path, "codex", first + [{"role": "user", "body": "Next"}])
    assert not native_cli
    assert (await turn(tmp_path, "codex", first, reset_session=True))[-1]["type"] == "final"


async def test_resource_changes_keep_context_and_apply_current_permissions(native_cli, tmp_path):
    messages = [{"role": "user", "body": "Start"}]
    root = str(tmp_path / "project")
    first = await turn(
        tmp_path,
        "cursor",
        messages,
        workflow={"projectRoot": root, "remResources": {"shell": True, "web": True}},
    )
    config_path = Path(native_cli[-1][3]["env"]["CURSOR_CONFIG_DIR"]) / "cli-config.json"
    assert "Shell(*)" in json.loads(config_path.read_text())["permissions"]["allow"]
    second = await turn(
        tmp_path,
        "cursor",
        messages + [{"role": "user", "body": "Read only"}],
        workflow={"projectRoot": root, "remResources": {"shell": False, "web": False}},
    )
    config = json.loads(config_path.read_text())
    assert "Shell(*)" in config["permissions"]["deny"]
    assert "WebFetch(*)" in config["permissions"]["deny"]
    assert second[-1]["sessionId"] == first[-1]["sessionId"]


async def test_consumed_attachment_is_not_reopened_on_followup(native_cli, tmp_path):
    data = tmp_path / "data/chat-attachments/thread-a"
    data.mkdir(parents=True)
    attachment = data / ("a" * 32 + "-note.txt")
    attachment.write_text("Original contents")
    messages = [
        {
            "role": "user",
            "body": "Read the attachment",
            "attachments": [
                {"name": "note.txt", "type": "text/plain", "storageName": attachment.name}
            ],
        }
    ]
    workflow = {"projectRoot": str(tmp_path / "project"), "chatThreadId": "thread-a"}
    await turn(tmp_path, "codex", messages, workflow=workflow)
    attachment.unlink()
    followup = await turn(
        tmp_path,
        "codex",
        messages + [{"role": "user", "body": "Use those contents"}],
        workflow=workflow,
    )
    assert followup[-1]["sessionResumed"] is True
    assert "raticode_attachment" not in native_cli[-1][2]


async def test_failed_resume_does_not_fall_back_to_a_fresh_prompt(
    native_cli, monkeypatch, tmp_path
):
    messages = [{"role": "user", "body": "Original goal"}]
    await turn(tmp_path, "codex", messages)
    calls = []

    async def failed(command, **kwargs):
        calls.append(command)
        yield {"type": "chunk", "stream": "stderr", "text": "Session is unavailable"}
        yield {"type": "exit", "returncode": 1}

    monkeypatch.setattr(chat, "stream_subprocess", failed)
    error = await turn(tmp_path, "codex", messages + [{"role": "user", "body": "Next"}])
    assert error[-1]["type"] == "error" and "Session is unavailable" in error[-1]["error"]
    assert len(calls) == 1 and "resume" in calls[0]
    assert "Original goal" not in calls[0][-1]


async def test_corrupt_reference_does_not_bootstrap_silently(native_cli, tmp_path):
    messages = [{"role": "user", "body": "Original"}]
    await turn(tmp_path, "codex", messages)
    state = next((tmp_path / "data/chat-provider-sessions").glob("*/session.json"))
    state.write_text("[]")
    with pytest.raises(ValueError, match="cannot be read"):
        await turn(tmp_path, "codex", messages + [{"role": "user", "body": "Next"}])
    assert len(native_cli) == 1


async def test_explicit_resend_can_replace_a_corrupt_reference(native_cli, tmp_path):
    messages = [{"role": "user", "body": "Original"}]
    first = await turn(tmp_path, "codex", messages)
    state = next((tmp_path / "data/chat-provider-sessions").glob("*/session.json"))
    state.write_text("[]")
    resent = await turn(tmp_path, "codex", messages, reset_session=True)
    assert first[-1]["sessionId"] != resent[-1]["sessionId"]


async def test_project_changes_start_a_new_native_session_in_the_new_directory(
    native_cli, tmp_path
):
    messages = [{"role": "user", "body": "Project goal"}]
    first = await turn(tmp_path, "codex", messages)
    other = tmp_path / "other-project"
    other.mkdir()
    switched = await turn(
        tmp_path,
        "codex",
        messages + [{"role": "user", "body": "Continue here"}],
        workflow={"projectRoot": str(other)},
    )
    assert switched[-1]["sessionId"] != first[-1]["sessionId"]
    assert native_cli[-1][3]["cwd"] == other
    assert "Project goal" in native_cli[-1][2]


@pytest.mark.parametrize("provider", ["codex", "claude_code", "cursor", "opencode", "antigravity"])
async def test_native_protocol_survives_real_oversized_tool_output(
    native_cli, monkeypatch, tmp_path, provider
):
    native = "oversized-session"
    large = "x" * 2_100_000
    if provider == "codex":
        records = [
            {"type": "thread.started", "thread_id": native},
            {
                "type": "item.completed",
                "item": {"type": "command_execution", "aggregated_output": large},
            },
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Done ✓"}},
            {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 3}},
        ]
    elif provider == "claude_code":
        records = [
            {"type": "system", "session_id": native},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": large}]}},
            {
                "type": "result",
                "result": "Done ✓",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            },
        ]
    elif provider == "cursor":
        records = [
            {"type": "system", "session_id": native},
            {"type": "tool_call", "tool_call": {"readToolCall": {"result": large}}},
            {"type": "result", "subtype": "success", "result": "Done ✓"},
        ]
    elif provider == "opencode":
        records = [
            {"type": "step_start", "sessionID": native},
            {"type": "tool_use", "part": {"tool": "read", "state": {"output": large}}},
            {"type": "text", "part": {"text": "Done ✓"}},
        ]
    else:
        records = [
            {"event": "init", "conversation_id": native},
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 1,
                    "step_type": "tool",
                    "state": "DONE",
                    "tool_name": "read",
                    "output": large,
                },
            },
            {
                "event": "result",
                "result": {"status": "SUCCESS", "response": "Done ✓", "conversation_id": native},
            },
        ]
    wire_path = tmp_path / "wire.jsonl"
    wire_path.write_text("".join(json.dumps(record) + "\n" for record in records))

    async def subprocess(command, **kwargs):
        async for event in real_stream_subprocess(
            [
                sys.executable,
                "-c",
                "import pathlib,sys;"
                "sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",
                str(wire_path),
            ],
            **kwargs,
        ):
            yield event

    monkeypatch.setattr(chat, "stream_subprocess", subprocess)
    monkeypatch.setattr(cli_providers, "stream_subprocess", subprocess)
    monkeypatch.setattr(antigravity, "stream_subprocess", subprocess)
    events = await turn(tmp_path, provider, [{"role": "user", "body": "Read the large file"}])
    assert events[-1]["type"] == "final"
    assert events[-1]["message"]["body"] == "Done ✓"
    assert events[-1]["sessionId"] == native
    if provider in {"codex", "claude_code"}:
        assert events[-1]["usage"]["input_tokens"] == 5
        assert events[-1]["usage"]["output_tokens"] == 3


async def test_cursor_chat_recovery_invokes_native_resume(native_cli, monkeypatch, tmp_path):
    from gofer.ui import chat_sessions

    monkeypatch.setattr(chat_sessions, "RECOVERY_DELAYS", (0, 0))
    original = cli_providers.stream_subprocess
    commands = []

    async def subprocess(command, **kwargs):
        commands.append(command)
        if len(commands) == 1:
            yield {
                "type": "chunk",
                "stream": "stdout",
                "text": '{"type":"system","session_id":"cursor-original"}\n',
            }
            yield {
                "type": "chunk",
                "stream": "stderr",
                "text": "Provider emitted malformed JSON content",
            }
            yield {"type": "exit", "returncode": 1}
        else:
            async for event in original(command, **kwargs):
                yield event

    monkeypatch.setattr(cli_providers, "stream_subprocess", subprocess)
    events = await turn(tmp_path, "cursor", [{"role": "user", "body": "Finish the task"}])
    assert events[-1]["type"] == "final"
    assert events[-1]["recoveryAttempts"] == 1
    assert commands[1][commands[1].index("--resume") + 1] == "cursor-original"
    assert any(event["type"] == "recovery" for event in events)
