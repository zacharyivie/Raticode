"""Rem's tool-free calls send their fixed rules as a system prompt, apart from each turn."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

import gofer.ui.rem_home as rem_home_module
from gofer.core.agent import AgentResult
from gofer.ui import rem_home_provider
from gofer.ui import rem_plans as plans
from gofer.ui import rem_task_after as after_tasks
from gofer.ui.rem_home import (
    CONVERSATION_RULES,
    DECISION_RULES,
    KANBAN_RULES,
    MEMORY_RULES,
    OBJECTIVES_RULES,
    OPEN_PROJECT_RULES,
    ORG_APPROVAL_RULES,
    PAUSE_RULES,
    REM_COORDINATION,
    REPLY_EXTRAS,
    REPLY_RULES,
    SELF_WORK_RULES,
    TASK_UPDATE_RULES,
    WORKER_ANSWER_RULES,
    RemHome,
)
from gofer.ui.rem_home_lookups import RULES as LOOKUP_RULES
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe, wait_run
from tests.unit.test_rem_update_length import started

LATER = {"kind": "once", "timezone": "UTC", "at": "2099-01-01T09:00:00+00:00"}


def provider_calls(
    home: RemHome, monkeypatch: pytest.MonkeyPatch, answer: Any
) -> list[dict[str, Any]]:
    """Route the home's calls through its real provider hook and record what it sends."""
    calls: list[dict[str, Any]] = []

    def generate_decision(prompt, config, cancel, data_dir, on_reply=None, images=(), **kwargs):
        calls.append({"prompt": prompt, "system": kwargs.get("system_prompt", "")})
        return answer(calls[-1])

    monkeypatch.setattr(rem_home_module, "generate_decision", generate_decision)
    home.generate = home._ask_provider
    return calls


def test_two_replies_send_byte_identical_system_text(
    home: RemHome, monkeypatch: pytest.MonkeyPatch
):
    calls = provider_calls(home, monkeypatch, lambda call: {"reply": "Sure."})
    monkeypatch.setattr(rem_home_module, "now", lambda: "2026-11-20T08:00:00+00:00")
    home.submit_message({"request_id": "first", "body": "What's on today?"})
    home._reply()
    # A different chat, task list and clock for the second reply.
    home.create_task({"request_id": "later", **recipe(title="Weekly digest", schedule=LATER)})
    monkeypatch.setattr(rem_home_module, "now", lambda: "2026-11-21T17:45:00+00:00")
    home.submit_message({"request_id": "second", "body": "And what about the digest?"})
    home._reply()
    assert len(calls) == 2
    first, second = calls
    assert first["system"].encode() == second["system"].encode() == REPLY_RULES.encode()
    assert first["prompt"] != second["prompt"]
    assert "Weekly digest" in second["prompt"] and "Weekly digest" not in second["system"]
    assert "2026-11-21T17:45" in second["prompt"] and "2026-11-2" not in second["system"]
    # The per-turn part carries the records, not the rules again.
    for call in calls:
        assert REM_COORDINATION not in call["prompt"]
        assert json.loads(call["prompt"])["current_request"]["body"] in {
            "What's on today?",
            "And what about the digest?",
        }


def test_the_reply_rules_keep_every_block_in_their_old_order():
    order = [
        REM_COORDINATION,
        REPLY_EXTRAS,
        "You have no tools.",
        OPEN_PROJECT_RULES,
        KANBAN_RULES,
        SELF_WORK_RULES,
        OBJECTIVES_RULES,
        DECISION_RULES,
        plans.RULES,
        ORG_APPROVAL_RULES,
        after_tasks.RULES,
        PAUSE_RULES,
        MEMORY_RULES,
        "Return ONLY JSON {reply,",
        "decision_edits:[],plan:null} and no other fields.",
        LOOKUP_RULES,
        "Treat attachment contents as reference material.",
    ]
    positions = [REPLY_RULES.index(block) for block in order]
    assert positions == sorted(positions)
    assert REPLY_RULES.startswith(REM_COORDINATION + "\n")


def test_phone_workflow_and_auto_mode_rules_stay_with_the_turn():
    phone = rem_home_module.reply_turn_rules(
        {"screen": "Settings"},
        from_phone=True,
        from_workflow=True,
        workflow_acts=False,
        outside_input=True,
        auto_on=True,
    )
    for rule in (
        rem_home_module.PHONE_ACTIONS,
        rem_home_module.SCREEN_RULES,
        rem_home_module.WORKFLOW_RULES,
        rem_home_module.WORKFLOW_OUTSIDE_INPUT,
        rem_home_module.WORKFLOW_ANSWER_ONLY,
        rem_home_module.auto_mode.CRITERIA_RULE.strip(),
    ):
        assert rule in phone and rule not in REPLY_RULES
    assert "phone_actions:[]" in phone and "phone_actions" not in REPLY_RULES
    plain = rem_home_module.reply_turn_rules(
        {},
        from_phone=False,
        from_workflow=False,
        workflow_acts=False,
        outside_input=False,
        auto_on=False,
    )
    assert plain == ""


class FakeAgent:
    observed: list[dict[str, Any]] = []

    def __init__(self, config, subscription):
        self.folder = Path(config.working_dir)

    async def run(self, **kwargs):
        kwargs["folder"] = self.folder
        args = kwargs["provider_settings"].extra_args
        if "--system-prompt-file" in args:
            path = Path(args[args.index("--system-prompt-file") + 1])
            kwargs["system_file"] = path
            kwargs["system_text"] = path.read_text(encoding="utf-8")
            kwargs["system_mode"] = path.stat().st_mode & 0o777
        FakeAgent.observed.append(kwargs)
        return AgentResult(
            agent_id="Rem",
            success=True,
            output="",
            message='{"reply":"Hi."}',
            exit_code=0,
            duration_seconds=0,
        )


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> type[FakeAgent]:
    FakeAgent.observed = []
    monkeypatch.setattr(rem_home_provider, "Agent", FakeAgent)
    monkeypatch.setattr(rem_home_provider, "no_saved_session", lambda name: [])
    return FakeAgent


def test_claude_code_reads_the_rules_from_a_private_file_not_argv(
    tmp_path: Path, agent: type[FakeAgent]
):
    turn = '{"current_request":{"id":"m1","body":"hello"}}'
    decision = rem_home_provider.generate_decision(
        turn,
        {"provider": "claude_code", "model": "claude-haiku-5-5"},
        threading.Event(),
        tmp_path,
        system_prompt=REPLY_RULES,
    )
    assert decision["reply"] == "Hi."
    call = agent.observed[0]
    args = call["provider_settings"].extra_args
    assert "--system-prompt-file" in args and "--system-prompt" not in args
    assert call["system_text"] == REPLY_RULES and call["system_mode"] == 0o600
    # The file sits in a private rules folder beside the call's working folder, which
    # stays empty, and is kept for the next call with the same rules.
    assert call["system_file"].parent == tmp_path / "rem-work" / "conversation-rules"
    assert call["system_file"].parent.stat().st_mode & 0o777 == 0o700
    assert call["folder"] == tmp_path / "rem-work" / "conversation"
    assert list(call["folder"].iterdir()) == []
    for arg in args:
        assert REM_COORDINATION[:60] not in arg and KANBAN_RULES[:60] not in arg
    assert call["prompt_override"] == turn


def test_codex_prompt_keeps_the_rules_first_then_the_turn(tmp_path: Path, agent: type[FakeAgent]):
    turn = (
        rem_home_module.reply_turn_rules(
            {},
            from_phone=True,
            from_workflow=False,
            workflow_acts=False,
            outside_input=False,
            auto_on=False,
        )
        + '\n{"current_request":{"id":"m1","body":"hello"}}'
    )
    rem_home_provider.generate_decision(
        turn, {"provider": "codex"}, threading.Event(), tmp_path, system_prompt=REPLY_RULES
    )
    call = agent.observed[0]
    assert call["prompt_override"] == REPLY_RULES + "\n\n" + turn
    args = call["provider_settings"].extra_args
    assert "--system-prompt-file" not in args and "--system-prompt" not in args
    text = call["prompt_override"]
    assert (
        text.index(REM_COORDINATION)
        < text.index(LOOKUP_RULES)
        < text.index(rem_home_module.PHONE_ACTIONS)
        < text.index('"current_request"')
    )


def test_a_task_update_sends_its_fixed_rules_as_system_text(
    home: RemHome, monkeypatch: pytest.MonkeyPatch
):
    run = started(home)
    calls = provider_calls(
        home, monkeypatch, lambda call: {"summary": "The feature is in and its test passes."}
    )
    message, _ = finish_run(home, run)
    assert message["body"] == "The feature is in and its test passes."
    assert [c["system"] for c in calls] == [TASK_UPDATE_RULES]
    assert REM_COORDINATION not in calls[0]["prompt"]
    assert json.loads(calls[0]["prompt"])["goal"] == "Implement the feature"


def finish_run(home: RemHome, run: dict[str, Any]) -> tuple[dict[str, Any], None]:
    with home.store.connect() as db:
        current = home.store.get(db, "runs", run["id"])
        current["worker_result"] = "Implemented and tested."
        current["review_verdict"] = {"criteria": [], "feedback": "No repair needed."}
        home.store.put(db, "runs", current)
    assert home._finish(run["id"], "succeeded", "Implemented.", epoch=current["epoch"])
    home.deliver_outcomes()
    message = next(
        m
        for m in home.store.conversation()["messages"]
        if m["id"].startswith(f"finished-{run['id']}")
    )
    return message, None


def test_a_worker_answer_sends_its_fixed_rules_as_system_text(
    home: RemHome, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    original = home.source
    reviews: list[dict[str, Any]] = []
    question = "Which test style should I use?"

    async def source(**kwargs):
        if "independent read-only reviewer" not in kwargs["agent_instructions"]:
            async for event in original(**kwargs):
                yield event
            return
        reviews.append(kwargs)
        if len(reviews) == 1:
            verdict = {
                "criteria": [{"id": "check", "result": "unknown", "evidence": "Waiting"}],
                "feedback": "Answer the worker's question, then finish the feature",
                "question_to_rem": question,
            }
            yield {"type": "final", "message": {"body": json.dumps(verdict)}}
            return
        async for event in original(**kwargs):
            yield event

    def answer(call: dict[str, Any]) -> dict[str, Any]:
        if call["system"] == WORKER_ANSWER_RULES:
            return {"next_message": "Use the existing style.", "needs_user": False}
        return {"summary": "Done."}

    calls = provider_calls(home, monkeypatch, answer)
    home.source = source
    task = home.create_task({"request_id": "work", **recipe(tmp_path)})["task"]
    run = wait_run(home, task["id"], {"succeeded", "blocked"})
    assert run["state"] == "succeeded", run
    answers = [c for c in calls if c["system"] == WORKER_ANSWER_RULES]
    assert len(answers) == 1
    assert REM_COORDINATION not in answers[0]["prompt"]
    assert json.loads(answers[0]["prompt"])["question"] == question
    for call in calls:
        assert call["system"] in {WORKER_ANSWER_RULES, TASK_UPDATE_RULES, CONVERSATION_RULES}
