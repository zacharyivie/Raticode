"""Each Rem turn records how big each part of its prompt was: sizes only, never the text."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gofer.core.turn_context import (
    LABELS,
    estimate_tokens,
    measure,
    record_turn_context,
    standing_parts,
    thread_context,
)
from gofer.core.usage_ledger import CONTEXT_SENT, track_chat_usage, usage_db
from gofer.ui.chat import build_chat_prompt, measured_chat_prompt
from gofer.ui.chat_sessions import ChatSession
from tests.unit.test_cli_api_commands import backend as backend  # noqa: F401
from tests.unit.test_cli_api_commands import ok, run
from tests.unit.test_rem_home import home as home  # noqa: F401

SECRET = "private-words-0451"


def _workflow(**extra: Any) -> dict[str, Any]:
    return {
        "projectRoot": "/work/project",
        "chatThreadId": "thread-a",
        "remResources": {"shell": True, "web": False},
        "remReportTheme": {"format": "html"},
        **extra,
    }


def _messages() -> list[dict[str, Any]]:
    return [{"id": "m1", "role": "user", "body": f"Please look at {SECRET}."}]


def _sizes(event: dict[str, Any]) -> dict[str, int]:
    return {part["key"]: part["chars"] for part in event["parts"]}


def test_estimates_are_characters_divided_by_four() -> None:
    assert [estimate_tokens(n) for n in (0, 1, 4, 5, 400)] == [0, 1, 1, 2, 100]


def test_the_prompt_is_unchanged_and_its_parts_add_up() -> None:
    prompt, event = measured_chat_prompt("codex", "gpt", _messages(), _workflow())
    assert prompt == build_chat_prompt("codex", "gpt", _messages(), _workflow())
    assert event["type"] == CONTEXT_SENT and event["resumed"] is False
    sizes = _sizes(event)
    assert {"identity", "commit", "cli_docs", "skills", "resources", "theme", "rules"} <= set(sizes)
    assert sizes["conversation"] == len(f"USER: Please look at {SECRET}.")
    # Every instruction part together is the instructions' length; the editor context and
    # the conversation sit outside them in the envelope.
    instructions = prompt.split("\n\n{\n", 1)[0]
    standing = sum(v for k, v in sizes.items() if k not in {"editor", "conversation"})
    assert standing == len(instructions)
    assert all(part["sent"] for part in event["parts"])
    assert all(part["tokens_estimate"] == estimate_tokens(part["chars"]) for part in event["parts"])
    # Sizes only: nothing in the event carries the prompt's text.
    assert SECRET not in json.dumps(event)


def test_rem_task_background_and_memories_are_split_from_the_identity() -> None:
    task = "You are a delegated Raticode worker. Follow the task contract."
    background = "\nBackground from Rem's chat ... " + "x" * 300
    instructions = task + background
    workflow = _workflow(remContextParts={"background": 200, "memories": 131})
    _, event = measured_chat_prompt(
        "claude_code", "m", _messages(), workflow, agent_instructions=instructions
    )
    sizes = _sizes(event)
    assert sizes["background"] == 200 and sizes["memories"] == 131
    assert sizes["identity"] == len(instructions) - 331


def test_read_only_task_turns_leave_out_the_commit_template() -> None:
    # Research rounds and reviews never commit.
    workflow = _workflow(remManagedTask={"readOnly": True, "runId": "run-a"})
    prompt, event = measured_chat_prompt("claude_code", "m", _messages(), workflow)
    assert "Commit message template" not in prompt
    # A part with no characters is left out of the event: its size is 0.
    sizes = _sizes(event)
    assert sizes.get("commit", 0) == 0
    instructions = prompt.split("\n\n{\n", 1)[0]
    assert sum(v for k, v in sizes.items() if k not in {"editor", "conversation"}) == len(
        instructions
    )
    # An attended writing task still gets it.
    writing = _workflow(remManagedTask={"readOnly": False, "runId": "run-a"})
    prompt, event = measured_chat_prompt("claude_code", "m", _messages(), writing)
    assert "Commit message template" in prompt and _sizes(event)["commit"] > 0


BROWSER_BLOCKER = "Name the missing Browser as a blocker in your result"


def test_unattended_task_turns_name_the_missing_browser_as_a_blocker() -> None:
    # Self-work forces Browser off with nobody at the thread to turn it on.
    workflow = _workflow(remManagedTask={"readOnly": False, "unattended": True})
    prompt, event = measured_chat_prompt("claude_code", "m", _messages(), workflow)
    assert "browser=false" in prompt
    assert "turn on Browser" not in prompt and "Tell them" not in prompt
    assert BROWSER_BLOCKER in prompt
    # Its writing turns still commit.
    assert "Commit message template" in prompt and _sizes(event)["commit"] > 0
    # With Browser on there is nothing to report.
    on = _workflow(
        remResources={"shell": True, "web": False, "browser": True},
        remManagedTask={"readOnly": False, "unattended": True},
    )
    assert BROWSER_BLOCKER not in measured_chat_prompt("claude_code", "m", _messages(), on)[0]


def test_ordinary_chat_threads_keep_the_commit_template_and_browser_advice() -> None:
    prompt, event = measured_chat_prompt("claude_code", "m", _messages(), _workflow())
    assert "Commit message template" in prompt and _sizes(event)["commit"] > 0
    assert "Tell them to turn on Browser in this thread's Tools menu" in prompt
    assert BROWSER_BLOCKER not in prompt


def test_only_turns_with_rem_artifacts_send_the_report_away_from_save_note(
    tmp_path: Path,
) -> None:
    brain = {"remSecondBrain": {"enabled": True, "root": str(tmp_path)}}
    # A plain chat turn with Second Brain keeps today's save_note line.
    chat, _ = measured_chat_prompt("claude_code", "m", _messages(), _workflow(**brain))
    assert "save_note to create a report" in chat
    assert "Save generated notes and reports as HTML" in chat
    assert "rem_artifacts.save_report" not in chat.split("Search spans")[0]
    # A research worker turn is granted rem_artifacts for its report.
    artifacts = {"name": "rem_artifacts", "type": "http", "url": "http://127.0.0.1:1/mcp"}
    research = _workflow(
        **brain,
        remResources={"shell": True, "web": False, "mcpServers": [artifacts]},
        remManagedTask={"readOnly": True, "unattended": True, "runId": "run-a"},
    )
    prompt, _ = measured_chat_prompt("claude_code", "m", _messages(), research)
    assert "save_note to create a report" not in prompt
    assert "Save this task's report with rem_artifacts.save_report, not save_note." in prompt
    assert "Use save_note only for notes, as HTML" in prompt


def test_a_resumed_session_marks_the_instructions_it_did_not_resend(tmp_path: Path) -> None:
    def session(state: dict[str, Any], resumed: bool) -> ChatSession:
        return ChatSession(
            root=tmp_path, state=state, users=[], resumed=resumed, document={"sessions": {}}
        )

    first = session({"provider": "codex"}, resumed=False)
    _, fresh = measured_chat_prompt("codex", "m", _messages(), _workflow(), session=first)
    assert fresh["resumed"] is False and all(p["sent"] for p in fresh["parts"])
    again = session({"provider": "codex", **first.pending}, resumed=True)
    _, resumed = measured_chat_prompt("codex", "m", _messages(), _workflow(), session=again)
    sent = {part["key"]: part["sent"] for part in resumed["parts"]}
    assert resumed["resumed"] is True
    assert sent["conversation"] is True
    assert not any(value for key, value in sent.items() if key != "conversation")


def test_the_ledger_keeps_the_sizes_with_the_turn_and_hides_the_event(tmp_path: Path) -> None:
    @track_chat_usage
    async def stream(
        provider: str,
        model: str = "m",
        workflow: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        data_dir: Path | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        yield {
            "type": CONTEXT_SENT,
            "resumed": False,
            "parts": measure({"identity": 400, "skills": 80, "conversation": 12}),
        }
        yield {"type": "final", "usage": {"input_tokens": 3, "output_tokens": 4}}

    async def consume() -> list[dict[str, Any]]:
        return [
            event
            async for event in stream(
                "claude_code",
                workflow={"remManagedTask": {"runId": "run-a"}, "chatThreadId": "thread-a"},
                data_dir=tmp_path,
            )
        ]

    events = asyncio.run(consume())
    assert [event["type"] for event in events] == ["final"]
    found = thread_context("thread-a", tmp_path)
    assert found["turn_count"] == 1
    turn = found["turns"][0]
    assert turn["run_id"] == "run-a" and turn["provider"] == "claude_code"
    assert [(p["key"], p["label"], p["chars"]) for p in turn["parts"]] == [
        ("identity", LABELS["identity"], 400),
        ("skills", "Skill index", 80),
        ("conversation", "Conversation", 12),
    ]
    assert turn["sent_chars"] == 492 and turn["sent_tokens_estimate"] == 123
    assert "estimate" in found["estimate"]
    # The invocation and its sizes share one id, so a turn's tokens and parts line up.
    with usage_db(tmp_path) as db:
        invocation = db.execute("SELECT id FROM usage_invocations").fetchone()[0]
    assert turn["turn_id"] == invocation


def _store(
    tmp_path: Path, identity: str, thread: str, sizes: dict[str, int], **options: Any
) -> None:
    with usage_db(tmp_path) as db:
        record_turn_context(
            db,
            invocation_id=identity,
            parts=measure(sizes, **options),
            resumed=not options.get("instructions_sent", True),
            provider="codex",
            model="m",
            thread_id=thread,
            run_id=None,
        )


def test_standing_parts_rank_what_was_sent_over_the_last_week(tmp_path: Path) -> None:
    _store(tmp_path, "a", "t1", {"identity": 100, "brain": 900, "conversation": 9000})
    _store(tmp_path, "b", "t2", {"identity": 100, "brain": 900, "skills": 50})
    # A resumed turn kept its instructions: its sizes count, its sends don't.
    _store(tmp_path, "c", "t2", {"identity": 100, "brain": 900}, instructions_sent=False)
    old = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    _store(tmp_path, "d", "t3", {"skills": 99999})
    with usage_db(tmp_path) as db:
        db.execute("UPDATE usage_turn_context SET observed_at=? WHERE id='d'", (old,))
    found = standing_parts(tmp_path)
    assert found["turns"] == 3 and found["days"] == 7
    keys = [part["key"] for part in found["parts"]]
    # The conversation isn't a standing part, and last month's turn is left out.
    assert keys == ["brain", "identity", "skills"]
    brain = found["parts"][0]
    assert (brain["turns"], brain["sent_turns"], brain["sent_chars"]) == (3, 2, 1800)
    assert brain["average_chars"] == 900 and brain["sent_tokens_estimate"] == 450


def test_turns_older_than_the_keep_period_are_removed(tmp_path: Path) -> None:
    _store(tmp_path, "old", "t1", {"identity": 10})
    with usage_db(tmp_path) as db:
        db.execute(
            "UPDATE usage_turn_context SET observed_at=?",
            ((datetime.now(UTC) - timedelta(days=91)).isoformat(),),
        )
    _store(tmp_path, "new", "t1", {"identity": 10})
    assert [turn["turn_id"] for turn in thread_context("t1", tmp_path)["turns"]] == ["new"]


def test_thread_ids_are_checked(tmp_path: Path) -> None:
    for bad in ("", "a/b", "x" * 201, "a b"):
        with pytest.raises(ValueError):
            thread_context(bad, tmp_path)


def test_cli_and_usage_page_read_the_sizes(backend) -> None:  # noqa: F811
    _store(backend.data, "t-1", "thread-cli", {"identity": 4000, "conversation": 120})
    _store(
        backend.data, "t-2", "thread-cli", {"identity": 4000, "brain": 800}, instructions_sent=False
    )
    found = ok("rem", "context", "thread-cli", "--turns", "1")
    assert found["turn_count"] == 2 and len(found["turns"]) == 1
    assert found["turns"][0]["turn_id"] == "t-2" and found["turns"][0]["resumed"] is True
    assert ok("rem", "context", "unknown-thread")["turns"] == []
    code, text, stderr = run("rem", "context", "thread-cli", "--output", "text", json_output=False)
    assert code == 0 and not stderr
    assert "Identity and task instructions" in text and "kept" in text and "~1,000" in text
    assert "Token counts are estimates" in text
    standing = ok("usage", "show")["standing_parts"]
    assert [part["key"] for part in standing["parts"]] == ["identity", "brain"]
    code, text, _ = run("usage", "show", "--output", "text", json_output=False)
    assert code == 0 and "Biggest standing parts, last 7 days (2 turns)" in text
    assert run("rem", "context", "bad id")[0] != 0


def test_worker_turns_measure_background_and_memories(home, tmp_path: Path) -> None:  # noqa: F811
    from gofer.ui.rem_home_store import encode
    from tests.unit.test_rem_home import recipe, wait_run

    with home.store.connect() as db:
        home.store.set_setting(
            db, "memories", {"html": {"text": "Prefers HTML reports.", "created_at": "2026-10-01"}}
        )
    calls: list[dict[str, Any]] = []
    original = home.source

    async def source(**kwargs: Any):
        calls.append(kwargs)
        async for event in original(**kwargs):
            yield event

    home.source = source
    task = home.create_task({"request_id": "sizes", **recipe(tmp_path)})["task"]
    wait_run(home, task["id"], {"succeeded", "failed", "needs_user", "blocked"}, timeout=15)
    worker = next(
        c for c in calls if "independent read-only reviewer" not in c["agent_instructions"]
    )
    parts = worker["workflow"]["remContextParts"]
    memories = len(encode({"user_memories": ["Prefers HTML reports."]})) - len(encode({}))
    assert parts["memories"] == memories
    assert parts["background"] > 0
    assert worker["agent_instructions"].count("Prefers HTML reports.") == 1
    _, event = measured_chat_prompt(
        "codex",
        "m",
        worker["messages"],
        worker["workflow"],
        agent_instructions=worker["agent_instructions"],
    )
    sizes = _sizes(event)
    assert sizes["memories"] == memories and sizes["background"] == parts["background"]
    assert sizes["identity"] > 0


def test_rem_call_prompts_are_unchanged_and_their_parts_add_up() -> None:
    from gofer.core.turn_context import measured_prompt, payload_sizes, rem_call_parts
    from gofer.ui.rem_home_store import encode

    payload = {
        "current_request": {"body": f"Look at {SECRET}"},
        "workspace": {"desktop": {"threads": [{"id": "t1", "title": SECRET}]}, "knowledge": []},
        "tasks": [{"id": "a"}],
        "at": "2026-11-20T12:00:00Z",
    }
    prompt, sizes = measured_prompt("Only for this call.", payload, encode)
    assert prompt == "Only for this call.\n" + encode(payload)
    assert measured_prompt("", payload, encode)[0] == encode(payload)
    assert sum(sizes.values()) == len(prompt)
    assert sizes["turn_rules"] == len("Only for this call.\n")
    # The workspace is measured by its own records, so the thread list stands out.
    assert {"workspace.desktop", "workspace.knowledge", "tasks", "current_request"} <= set(sizes)
    assert "workspace" not in sizes
    assert sizes["workspace.desktop"] == len('"desktop": ') + len(
        encode({"threads": [{"id": "t1", "title": SECRET}]})
    )
    assert sum(payload_sizes(payload, encode).values()) == len(encode(payload))
    # A lookup appended before the next ask is what the call added; the rules are the
    # system prompt. Every part is sent: Rem's calls have no session.
    parts = rem_call_parts(prompt + "\nLookup results: []", "RULES" * 10, sizes)
    by_key = {part["key"]: part for part in parts}
    assert by_key["added"]["chars"] == len("\nLookup results: []")
    assert by_key["rules"]["chars"] == 50
    assert sum(part["chars"] for part in parts) == len(prompt) + len("\nLookup results: []") + 50
    assert all(part["sent"] for part in parts)
    assert [part["chars"] for part in parts] == sorted((p["chars"] for p in parts), reverse=True)
    assert SECRET not in json.dumps(parts)
    # Without measured parts the prompt is one part.
    assert [p["key"] for p in rem_call_parts("abc", "", None)] == ["prompt"]


def _store_call(data: Path, ident: str, call: str, sizes: dict[str, int], run: str = "") -> None:
    from gofer.core.turn_context import rem_call_parts

    with usage_db(data) as db:
        record_turn_context(
            db,
            invocation_id=ident,
            parts=rem_call_parts("x" * sum(sizes.values()), "", sizes),
            resumed=False,
            provider="claude_code",
            model="sonnet",
            thread_id=None,
            run_id=run or None,
            call=call,
        )


def test_rem_calls_rank_with_the_threads_turns_and_stay_out_of_thread_views(
    tmp_path: Path,
) -> None:
    from gofer.core.turn_context import rem_calls

    _store(tmp_path, "a", "t1", {"identity": 100, "brain": 900, "conversation": 9000})
    _store_call(tmp_path, "c1", "chat_reply", {"workspace.desktop": 5000, "history": 7000})
    _store_call(tmp_path, "c2", "chat_reply", {"workspace.desktop": 4000, "history": 7000})
    _store_call(tmp_path, "c3", "task_update", {"goal": 300}, run="run-1")
    found = standing_parts(tmp_path)
    assert (found["turns"], found["calls"]) == (1, 3)
    first = found["parts"][0]
    # The chat history is what a reply answers, so it isn't a standing part.
    assert first["id"] == "chat_reply:workspace.desktop"
    assert first["label"] == "Chat reply: Desktop thread list"
    assert (first["turns"], first["sent_chars"], first["call"]) == (2, 9000, "chat_reply")
    assert "chat_reply:history" not in [part["id"] for part in found["parts"]]
    assert [kind["call"] for kind in found["rem_calls"]] == ["chat_reply", "task_update"]
    chat = found["rem_calls"][0]
    assert (chat["calls"], chat["sent_chars"], chat["max_chars"]) == (2, 23000, 12000)
    assert chat["average_tokens_estimate"] == estimate_tokens(11500)
    # Rem's calls have no thread, and a thread view lists only its turns.
    assert [turn["turn_id"] for turn in thread_context("t1", tmp_path)["turns"]] == ["a"]
    listed = rem_calls(tmp_path)
    assert listed["call_count"] == 3
    assert [call["turn_id"] for call in listed["calls"]] == ["c3", "c2", "c1"]
    assert listed["calls"][0]["call_label"] == "Task update"
    assert listed["calls"][0]["parts"][0]["label"] == "Task goal"
    assert [c["turn_id"] for c in rem_calls(tmp_path, call="chat_reply", limit=1)["calls"]] == [
        "c2"
    ]
    assert [c["turn_id"] for c in rem_calls(tmp_path, run_id="run-1")["calls"]] == ["c3"]
    with pytest.raises(ValueError):
        rem_calls(tmp_path, call="poetry")
    with pytest.raises(ValueError):
        rem_calls(tmp_path, run_id="a b")


def test_an_older_ledger_gains_the_call_column(tmp_path: Path) -> None:
    import sqlite3

    db = sqlite3.connect(tmp_path / "usage.sqlite3")
    db.execute(
        """CREATE TABLE usage_turn_context (
        id TEXT PRIMARY KEY, thread_id TEXT, run_id TEXT, provider TEXT, model TEXT,
        observed_at TEXT NOT NULL, resumed INTEGER NOT NULL DEFAULT 0, parts TEXT NOT NULL)"""
    )
    db.execute(
        "INSERT INTO usage_turn_context VALUES ('old', 't1', NULL, 'codex', NULL, ?, 0, ?)",
        (datetime.now(UTC).isoformat(), json.dumps([{"key": "identity", "chars": 40}])),
    )
    db.execute("PRAGMA user_version=4")
    db.commit()
    db.close()
    assert [turn["turn_id"] for turn in thread_context("t1", tmp_path)["turns"]] == ["old"]
    _store_call(tmp_path, "new", "self_work_plan", {"prompt": 80})
    assert standing_parts(tmp_path)["calls"] == 1


def test_rem_chat_replies_and_task_updates_record_their_sizes(
    home,  # noqa: F811
    tmp_path: Path,
) -> None:
    from gofer.core.turn_context import rem_calls
    from tests.unit.test_rem_home import recipe, wait_run

    prompts: list[str] = []

    def generate(prompt: str, config: dict[str, Any], cancel: Any) -> dict[str, Any]:
        prompts.append(prompt)
        return {"reply": "Hello.", "tasks": [], "memories": []}

    home.generate = generate
    home.context = lambda query: {"desktop": {"threads": [{"id": "d1", "title": SECRET}]}}
    home.submit_message({"request_id": "sizes-chat", "body": f"Hello about {SECRET}"})
    home._reply()
    assert home.store.conversation()["messages"][-1]["body"] == "Hello."
    chat = rem_calls(home.data_dir, call="chat_reply")
    assert chat["call_count"] == len(prompts) == 1
    call = chat["calls"][0]
    sizes = {part["key"]: part["chars"] for part in call["parts"]}
    # The hook reads the fixed rules then the prompt, joined by a blank line.
    assert sum(sizes.values()) == len(prompts[0]) - 2
    assert {"rules", "current_request", "workspace.desktop", "tasks", "board"} <= set(sizes)
    assert sizes["workspace.desktop"] == len('"desktop": ') + len(
        json.dumps({"threads": [{"id": "d1", "title": SECRET}]}, ensure_ascii=False, sort_keys=True)
    )
    with usage_db(home.data_dir) as db:
        stored = [row[0] for row in db.execute("SELECT parts FROM usage_turn_context")]
    assert stored and not any(SECRET in parts for parts in stored)

    task = home.create_task({"request_id": "sizes-update", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "failed", "needs_user", "blocked"})
    updates = rem_calls(home.data_dir, call="task_update", run_id=run["id"])
    assert updates["call_count"] >= 1
    assert "worker_result" in {part["key"] for part in updates["calls"][0]["parts"]}


def test_cli_and_usage_show_rem_calls(backend) -> None:  # noqa: F811
    _store_call(backend.data, "c-1", "chat_reply", {"workspace.desktop": 4000, "history": 800})
    _store_call(backend.data, "c-2", "worker_answer", {"question": 120}, run="run-9")
    found = ok("rem", "context")
    assert found["call_count"] == 2 and found["calls"][0]["call"] == "worker_answer"
    assert ok("rem", "context", "--call", "chat_reply")["calls"][0]["turn_id"] == "c-1"
    assert ok("rem", "context", "--run", "run-9")["call_count"] == 1
    code, text, stderr = run("rem", "context", "--output", "text", json_output=False)
    assert code == 0 and not stderr
    assert "Rem's newest call: Answer to a worker" in text and "Worker's question" in text
    assert "Earlier calls:" in text and "Chat reply" in text
    assert run("rem", "context", "thread-x", "--call", "chat_reply")[0] == 2
    assert run("rem", "context", "--call", "poetry")[0] != 0
    standing = ok("usage", "show")["standing_parts"]
    assert standing["calls"] == 2 and standing["rem_calls"][0]["call"] == "chat_reply"
    code, text, _ = run("usage", "show", "--output", "text", json_output=False)
    assert code == 0 and "(0 turns, 2 Rem calls)" in text
    assert "Chat reply: Desktop thread list" in text and "Rem's own calls:" in text


def _attempts(data: Path, call: str) -> list[tuple[str, int]]:
    with usage_db(data) as db:
        return [
            (row["attempt"], row["failed"])
            for row in db.execute(
                "SELECT attempt, failed FROM usage_turn_context WHERE call=? "
                "ORDER BY observed_at, rowid",
                (call,),
            )
        ]


def test_a_task_update_answered_in_prose_records_a_reformat(
    home,  # noqa: F811
) -> None:
    import threading

    from gofer.core.turn_context import rem_calls
    from gofer.ui.rem_home_provider import parse_decision

    replies = iter(
        [
            f"Sure, here's how the task went: it finished and {SECRET}.",
            json.dumps({"summary": "The task finished.", "needs_user": False}),
        ]
    )
    prompts: list[str] = []

    def generate(prompt: str, config: dict[str, Any], cancel: Any) -> dict[str, Any]:
        # The provider's own parser reads the fake model's raw text.
        prompts.append(prompt)
        return parse_decision(next(replies))

    home.generate = generate
    update = home._task_update(
        "Write the update.", {"provider": "codex"}, threading.Event(), run_id="run-7"
    )
    assert update.summary == "The task finished."
    assert len(prompts) == 2 and "not the requested JSON object" in prompts[1]
    assert _attempts(home.data_dir, "task_update") == [("first", 0), ("reformat", 0)]
    found = rem_calls(home.data_dir, call="task_update")
    assert [(c["attempt"], c["failed"]) for c in found["calls"]] == [
        ("reformat", False),
        ("first", False),
    ]
    assert found["calls"][0]["attempt_label"] == "Asked again for the JSON format"
    [kind] = found["by_kind"]
    assert (kind["call"], kind["calls"], kind["reasked"], kind["failed"]) == (
        "task_update",
        2,
        1,
        0,
    )
    assert kind["attempts"] == {"first": 1, "reformat": 1, "repair": 0, "read": 0}
    with usage_db(home.data_dir) as db:
        stored = [tuple(row) for row in db.execute("SELECT * FROM usage_turn_context")]
    assert not any(SECRET in str(value) for row in stored for value in row)


def test_rem_calls_that_still_cannot_be_used_are_marked_failed(
    home,  # noqa: F811
) -> None:
    import threading

    from pydantic import ValidationError

    from gofer.ui.rem_home_provider import MalformedDecision

    def prose(prompt: str, config: dict[str, Any], cancel: Any) -> dict[str, Any]:
        raise MalformedDecision("Still just talking.")

    home.generate = prose
    with pytest.raises(MalformedDecision):
        home._task_update("Write the update.", {"provider": "codex"}, threading.Event())
    assert _attempts(home.data_dir, "task_update") == [("first", 0), ("reformat", 1)]

    def invalid(prompt: str, config: dict[str, Any], cancel: Any) -> dict[str, Any]:
        return {"summary": "Done.", "needs_user": "perhaps"}

    home.generate = invalid
    with pytest.raises(ValidationError):
        home._task_update("Write the update.", {"provider": "codex"}, threading.Event())
    assert _attempts(home.data_dir, "task_update")[2:] == [("first", 0), ("repair", 1)]


def test_a_chat_reply_repair_and_its_failure_are_recorded(
    home,  # noqa: F811
) -> None:
    replies = iter([{"reply": "Hi.", "tasks": "not a list"}, {"reply": "Hello."}])

    def generate(prompt: str, config: dict[str, Any], cancel: Any) -> dict[str, Any]:
        return next(replies)

    home.generate = generate
    home.submit_message({"request_id": "repair-ok", "body": "Hello"})
    home._reply()
    assert home.store.conversation()["messages"][-1]["body"] == "Hello."
    assert _attempts(home.data_dir, "chat_reply") == [("first", 0), ("repair", 0)]

    home.generate = lambda prompt, config, cancel: {"reply": "Hi.", "tasks": "still not a list"}
    home.submit_message({"request_id": "repair-fails", "body": "Hello again"})
    home._reply()
    assert _attempts(home.data_dir, "chat_reply")[2:] == [("first", 0), ("repair", 1)]


def test_a_chat_reply_after_a_lookup_is_a_read_not_a_reask(
    home,  # noqa: F811
) -> None:
    from gofer.core.turn_context import rem_calls

    replies = iter(
        [{"reply": "", "recall": {"lookups": [{"kind": "tasks"}]}}, {"reply": "Two tasks."}]
    )
    home.generate = lambda prompt, config, cancel: next(replies)
    home.submit_message({"request_id": "lookup", "body": "How many tasks are there?"})
    home._reply()
    assert home.store.conversation()["messages"][-1]["body"] == "Two tasks."
    assert _attempts(home.data_dir, "chat_reply") == [("first", 0), ("read", 0)]
    [kind] = rem_calls(home.data_dir)["by_kind"]
    assert (kind["calls"], kind["reasked"], kind["failed"]) == (2, 0, 0)
    assert kind["attempts"]["read"] == 1


def test_an_older_ledger_gains_the_attempt_columns(tmp_path: Path) -> None:
    import sqlite3

    from gofer.core.turn_context import rem_calls

    db = sqlite3.connect(tmp_path / "usage.sqlite3")
    db.execute(
        """CREATE TABLE usage_turn_context (
        id TEXT PRIMARY KEY, thread_id TEXT, run_id TEXT, provider TEXT, model TEXT,
        observed_at TEXT NOT NULL, resumed INTEGER NOT NULL DEFAULT 0, parts TEXT NOT NULL,
        call TEXT)"""
    )
    db.execute(
        "INSERT INTO usage_turn_context VALUES ('old', NULL, NULL, 'codex', NULL, ?, 0, ?, ?)",
        (datetime.now(UTC).isoformat(), json.dumps([{"key": "prompt", "chars": 40}]), "self_work"),
    )
    db.execute("PRAGMA user_version=5")
    db.commit()
    db.close()
    found = rem_calls(tmp_path)
    # A call saved before attempts were kept counts as a first ask that didn't fail.
    assert (found["calls"][0]["attempt"], found["calls"][0]["failed"]) == ("first", False)
    assert found["by_kind"][0]["attempts"]["first"] == 1


def test_cli_and_usage_count_reasked_and_failed_calls(backend) -> None:  # noqa: F811
    from gofer.core.usage_ledger import mark_rem_call_failed, record_rem_call

    config = {"provider": "claude_code", "model": "sonnet"}
    for attempt in ("first", "reformat"):
        record_rem_call(
            "task_update", "x" * 40, "", None, config=config, data_dir=backend.data, attempt=attempt
        )
    for attempt in ("first", "repair"):
        last = record_rem_call(
            "chat_reply", "y" * 80, "", None, config=config, data_dir=backend.data, attempt=attempt
        )
    mark_rem_call_failed(last, backend.data)
    found = ok("rem", "context", "--format", "json")
    counts = {k["call"]: (k["calls"], k["reasked"], k["failed"]) for k in found["by_kind"]}
    assert counts == {"task_update": (2, 1, 0), "chat_reply": (2, 1, 1)}
    assert found["calls"][0]["attempt"] == "repair" and found["calls"][0]["failed"] is True
    code, text, _ = run("rem", "context", "--format", "text", json_output=False)
    assert code == 0
    assert "(asked again to repair invalid fields, failed):" in text
    assert "Calls by kind" in text
    assert any(
        "Task update" in line and "2 calls" in line and "1 re-asked" in line and "0 failed" in line
        for line in text.splitlines()
    )
    kinds = ok("usage", "show")["standing_parts"]["rem_calls"]
    assert {k["call"]: (k["reasked"], k["failed"]) for k in kinds} == {
        "task_update": (1, 0),
        "chat_reply": (1, 1),
    }
    code, text, _ = run("usage", "show", "--format", "text", json_output=False)
    assert code == 0 and "RE-ASKED" in text and "FAILED" in text


class _CachedProvider:
    """A provider whose usage report has cache reads and writes; its calls are tracked."""

    provider = "codex"

    def __init__(self, reply: str, usage: dict[str, int]) -> None:
        self.reply, self.usage = reply, usage

    async def _answer(self, prompt: str, provider_settings: Any = None) -> Any:
        from gofer.core.agent import AgentResult

        return AgentResult(
            agent_id="Rem",
            success=True,
            output=self.reply,
            exit_code=0,
            duration_seconds=0.1,
            usage_metadata=dict(self.usage),
        )

    def answer(self, prompt: str) -> str:
        from gofer.subscriptions.usage import track_invocation

        # The same usage tracking every real subscription's execute has.
        return asyncio.run(track_invocation(_CachedProvider._answer)(self, prompt)).output


def test_a_rem_chat_reply_counts_its_cache_reads_and_writes(
    home,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gofer.ui.rem_home as rem_home_module
    from gofer.ui.rem_home_provider import parse_decision

    # Provider calls record their tokens in the data folder Raticode runs from.
    monkeypatch.setenv("GOFER_DATA_DIR", str(home.data_dir))
    provider = _CachedProvider(
        json.dumps({"reply": "Done."}),
        {"input_tokens": 50, "cache_read_tokens": 900, "cache_write_tokens": 100},
    )

    def generate_decision(prompt, config, cancel, data_dir, on_reply=None, images=(), **kwargs):
        return parse_decision(provider.answer(str(prompt)))

    monkeypatch.setattr(rem_home_module, "generate_decision", generate_decision)
    home.generate = home._ask_provider
    home.submit_message({"request_id": "cached", "body": f"Remember {SECRET}"})
    home._reply()
    assert home.store.conversation()["messages"][-1]["body"] == "Done."
    [kind] = standing_parts(home.data_dir)["rem_calls"]
    assert kind["call"] == "chat_reply" and kind["calls"] == kind["reported_calls"] == 1
    assert (kind["input_tokens"], kind["cache_read_tokens"], kind["cache_write_tokens"]) == (
        50,
        900,
        100,
    )
    # The size record and the token record are one call under one ID; neither has the text.
    with usage_db(home.data_dir) as db:
        joined = db.execute(
            "SELECT t.call, i.tokens FROM usage_turn_context t "
            "JOIN usage_invocations i ON i.id = t.id"
        ).fetchall()
        stored = json.dumps([list(row) for row in db.execute("SELECT * FROM usage_turn_context")])
    assert [row["call"] for row in joined] == ["chat_reply"]
    assert SECRET not in stored and SECRET not in json.dumps([list(row) for row in joined])


def test_only_the_first_call_takes_a_named_id(tmp_path: Path) -> None:
    from gofer.core.usage_context import named_invocation, take_invocation_id

    with named_invocation("size-record"):
        first, second = take_invocation_id(), take_invocation_id()
    assert first == "size-record" and second != "size-record"
    with named_invocation(None):
        assert take_invocation_id() != "size-record"
    assert take_invocation_id() != "size-record"


def test_a_kind_without_reported_tokens_keeps_its_sums_unknown(tmp_path: Path) -> None:
    from gofer.core.usage_ledger import record_invocation

    _store_call(tmp_path, "r-1", "task_update", {"prompt": 400})
    _store_call(tmp_path, "r-2", "task_update", {"prompt": 400})
    _store_call(tmp_path, "r-3", "worker_answer", {"question": 120})
    # Only one task update reported tokens, and a thread's turn under its own ID is
    # never counted as one of Rem's calls.
    record_invocation(
        invocation_id="r-1",
        provider="codex",
        metadata={"input_tokens": 1200, "cache_read_tokens": 1000},
        data_dir=tmp_path,
    )
    kinds = {kind["call"]: kind for kind in standing_parts(tmp_path)["rem_calls"]}
    update = kinds["task_update"]
    assert (update["calls"], update["reported_calls"]) == (2, 1)
    assert (update["input_tokens"], update["cache_read_tokens"], update["cache_write_tokens"]) == (
        1200,
        1000,
        None,
    )
    answer = kinds["worker_answer"]
    assert answer["reported_calls"] == 0 and answer["cache_read_tokens"] is None


def test_usage_show_prints_each_kinds_reported_cache_tokens(backend) -> None:  # noqa: F811
    from gofer.core.usage_ledger import record_invocation

    _store_call(backend.data, "u-1", "chat_reply", {"workspace.desktop": 4000})
    _store_call(backend.data, "u-2", "worker_answer", {"question": 120})
    record_invocation(
        invocation_id="u-1",
        provider="codex",
        metadata={"input_tokens": 1050, "cache_read_tokens": 900, "cache_write_tokens": 100},
        data_dir=backend.data,
    )
    kinds = {k["call"]: k for k in ok("usage", "show")["standing_parts"]["rem_calls"]}
    assert kinds["chat_reply"]["cache_read_tokens"] == 900
    assert kinds["chat_reply"]["cache_write_tokens"] == 100
    code, text, _ = run("usage", "show", "--output", "text", json_output=False)
    assert code == 0 and "CACHE READ" in text and "CACHE WRITE" in text
    chat = next(line for line in text.splitlines() if line.startswith("Chat reply "))
    assert chat.split()[-3:] == ["1,050", "900", "100"]
    answer = next(line for line in text.splitlines() if line.startswith("Answer to a worker"))
    assert answer.split()[-3:] == ["-", "-", "-"]


def _rules_versions(data: Path) -> dict[str, str | None]:
    with usage_db(data) as db:
        return {
            row["id"]: row["rules_version"]
            for row in db.execute("SELECT id, rules_version FROM usage_turn_context")
        }


def test_each_rem_call_records_the_version_of_its_fixed_rules(tmp_path: Path) -> None:
    import hashlib

    from gofer.core.usage_ledger import record_rem_call

    config = {"provider": "claude_code", "model": "sonnet"}
    old = record_rem_call(
        "chat_reply", "p", "Answer as JSON.", None, config=config, data_dir=tmp_path
    )
    new = record_rem_call(
        "chat_reply", "p", "Answer as JSON!", None, config=config, data_dir=tmp_path
    )
    bare = record_rem_call("chat_reply", "p", "", None, config=config, data_dir=tmp_path)
    assert old and new and bare
    versions = _rules_versions(tmp_path)
    assert versions[old] == hashlib.sha256(b"Answer as JSON.").hexdigest()[:12]
    assert versions[new] == hashlib.sha256(b"Answer as JSON!").hexdigest()[:12]
    assert versions[old] != versions[new]
    # A call with no fixed rules has no version, and the rules' text is never stored.
    assert versions[bare] is None
    with usage_db(tmp_path) as db:
        stored = " ".join(
            str(value) for row in db.execute("SELECT * FROM usage_turn_context") for value in row
        )
    assert "Answer as JSON" not in stored
    with usage_db(tmp_path) as db, pytest.raises(ValueError):
        record_turn_context(
            db,
            invocation_id="bad",
            parts=[],
            resumed=False,
            provider=None,
            model=None,
            thread_id=None,
            run_id=None,
            call="chat_reply",
            rules_version="not-a-version",
        )


def test_cli_splits_each_kind_by_rules_version(backend) -> None:  # noqa: F811
    from gofer.core.turn_context import rules_version
    from gofer.core.usage_ledger import mark_rem_call_failed, record_rem_call

    config = {"provider": "claude_code", "model": "sonnet"}

    def ask(system: str, attempt: str = "first") -> str | None:
        return record_rem_call(
            "chat_reply",
            "x" * 40,
            system,
            None,
            config=config,
            data_dir=backend.data,
            attempt=attempt,
        )

    ask("rules one")
    mark_rem_call_failed(ask("rules one", "reformat"), backend.data)
    ask("rules one", "repair")
    ask("rules two")
    ask("rules two", "read")
    ask("")
    found = ok("rem", "context", "--format", "json")
    (kind,) = found["by_kind"]
    # The kind's totals are unchanged; its versions split them, newest rules first.
    assert (kind["calls"], kind["reasked"], kind["failed"]) == (6, 2, 1)
    assert [
        (v["version"], v["calls"], v["re_asked"], v["failed"], v["current"])
        for v in kind["versions"]
    ] == [
        (None, 1, 0, 0, True),
        (rules_version("rules two"), 2, 0, 0, False),
        (rules_version("rules one"), 3, 2, 1, False),
    ]
    assert all(isinstance(v["first_seen"], str) and v["first_seen"] for v in kind["versions"])
    assert kind["versions"][1]["first_seen"] > kind["versions"][2]["first_seen"]
    assert found["calls"][1]["rules_version"] == rules_version("rules two")
    code, text, _ = run("rem", "context", "--format", "text", json_output=False)
    assert code == 0
    assert any(
        f"rules {rules_version('rules one')}" in line and "3 calls" in line and "2 re-asked" in line
        for line in text.splitlines()
    )
    # The newest call had no fixed rules, so Settings > Usage has no current version line.
    (usage,) = ok("usage", "show")["standing_parts"]["rem_calls"]
    assert usage["current_version"] is None
    ask("rules two", "repair")
    (usage,) = ok("usage", "show")["standing_parts"]["rem_calls"]
    current = usage["current_version"]
    assert (current["version"], current["calls"], current["re_asked"], current["failed"]) == (
        rules_version("rules two"),
        3,
        1,
        0,
    )
    assert current["first_seen"] == kind["versions"][1]["first_seen"]
    assert (usage["calls"], usage["reasked"]) == (7, 3)


def test_an_older_ledger_gains_the_rules_version_column(tmp_path: Path) -> None:
    import sqlite3

    from gofer.core.turn_context import rem_calls, rules_version
    from gofer.core.usage_ledger import record_rem_call

    path = tmp_path / "usage.sqlite3"
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE usage_turn_context (
        id TEXT PRIMARY KEY, thread_id TEXT, run_id TEXT, provider TEXT, model TEXT,
        observed_at TEXT NOT NULL, resumed INTEGER NOT NULL DEFAULT 0, parts TEXT NOT NULL,
        call TEXT, attempt TEXT, failed INTEGER NOT NULL DEFAULT 0)"""
    )
    db.execute(
        "INSERT INTO usage_turn_context VALUES "
        "('old', NULL, NULL, 'codex', NULL, ?, 0, ?, ?, ?, 1)",
        (
            (datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
            json.dumps([{"key": "prompt", "chars": 40}]),
            "chat_reply",
            "reformat",
        ),
    )
    db.execute("PRAGMA user_version=6")
    db.commit()
    db.close()
    record_rem_call(
        "chat_reply", "p", "fixed rules", None, config={"provider": "codex"}, data_dir=tmp_path
    )
    db = sqlite3.connect(path)
    assert "rules_version" in {
        row[1] for row in db.execute("PRAGMA table_info(usage_turn_context)")
    }
    db.close()
    (kind,) = rem_calls(tmp_path)["by_kind"]
    # The call from before versions were kept counts under no version.
    assert [(v["version"], v["calls"], v["re_asked"], v["failed"]) for v in kind["versions"]] == [
        (rules_version("fixed rules"), 1, 0, 0),
        (None, 1, 1, 1),
    ]
