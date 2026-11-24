"""Rem's chat prompt starts with what rarely changes, so a provider can read it from cache."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import gofer.ui.rem_home as rem_home_module
from gofer.core.agent import AgentResult
from gofer.ui import rem_home_provider
from gofer.ui.rem_home import REPLY_RULES, RemHome
from gofer.ui.rem_home_provider import ChatImage, PromptBlocks, claude_user_message
from gofer.ui.rem_home_store import encode
from gofer.ui.rem_reply_prompt import encode_blocks, ordered_workspace, reply_prompt
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_system_prompt import provider_calls

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def test_blocks_join_into_the_same_json_object_as_encode():
    groups: tuple[dict[str, Any], ...] = (
        {"projects": [{"root": "/p", "name": "P"}], "decisions": {"b": 1, "a": "é"}},
        {},
        {"board": [{"z": 1, "y": None}]},
        {"current_request": {"id": "m1", "body": "hi"}, "at": "now"},
    )
    blocks = encode_blocks(*groups)
    assert len(blocks) == 3
    text = "".join(blocks)
    merged = {k: v for group in groups for k, v in group.items()}
    assert json.loads(text) == merged
    # Same separators, escaping and nested key order as encode(); only the top level
    # keeps the given order.
    assert text == "{" + ", ".join(encode({k: v})[1:-1] for k, v in merged.items()) + "}"
    assert text.index('"projects"') < text.index('"board"') < text.index('"at"')
    assert encode_blocks() == ["{}"]


def test_workspace_writes_its_steady_parts_first_and_the_clock_last():
    workspace = {
        "at": "2026-11-20T10:00:00+00:00",
        "knowledge": [{"title": "note"}],
        "desktop": {"threads": []},
        "brains": {"enabled": False},
        "running_threads": [],
    }
    text = reply_prompt({}, {"workspace": ordered_workspace(workspace)}, {})
    assert list(json.loads(text)["workspace"]) == [
        "brains",
        "desktop",
        "running_threads",
        "knowledge",
        "at",
    ]


def test_adding_text_adds_a_block_and_keeps_the_images_in_place():
    prompt = PromptBlocks(["{a, ", "b}"])
    assert prompt == "{a, b}" and prompt.images_at == 2
    later = prompt + "\nLookup results"
    assert isinstance(later, PromptBlocks)
    assert later.blocks == ("{a, ", "b}", "\nLookup results")
    assert later == "{a, b}\nLookup results"
    later += "\nRepair"
    assert later.images_at == 2 and len(later.blocks) == 4
    image = ChatImage("shot.png", "image/png", ".png", PNG)
    first = json.loads(claude_user_message(prompt, [image]))["message"]["content"]
    second = json.loads(claude_user_message(later, [image]))["message"]["content"]
    # The repeat round sends the first call's blocks, images included, unchanged.
    assert second[: len(first)] == first
    assert [block["type"] for block in second] == [
        "text",
        "text",
        "text",
        "image",
        "text",
        "text",
    ]
    # A plain prompt keeps its images first, as before.
    plain = json.loads(claude_user_message("Compare", [image]))["message"]["content"]
    assert [block["type"] for block in plain] == ["text", "image", "text"]


def test_consecutive_replies_start_with_the_same_bytes(
    home: RemHome, monkeypatch: pytest.MonkeyPatch
):
    home.configure({"provider": "codex", "projects": [{"root": "/work/shop", "name": "shop"}]})
    calls = provider_calls(home, monkeypatch, lambda call: {"reply": "Sure."})
    monkeypatch.setattr(rem_home_module, "now", lambda: "2026-11-20T08:00:00+00:00")
    home.submit_message({"request_id": "first", "body": "What's on today?"})
    home._reply()
    monkeypatch.setattr(rem_home_module, "now", lambda: "2026-11-20T08:05:00+00:00")
    home.submit_message({"request_id": "second", "body": "And tomorrow?"})
    home._reply()
    first, second = (call["prompt"] for call in calls)
    assert isinstance(first, PromptBlocks) and isinstance(second, PromptBlocks)
    assert len(first.blocks) == len(second.blocks) == 3
    # The records that didn't change are byte for byte the same, in front.
    assert first.blocks[:2] == second.blocks[:2]
    assert '"projects"' in first.blocks[0] and '"board"' in first.blocks[1]
    # The clock, the request and the chat so far come last.
    for prompt, body, at in (
        (first, "What's on today?", "08:00"),
        (second, "And tomorrow?", "08:05"),
    ):
        assert body in prompt.blocks[-1] and at in prompt.blocks[-1]
        assert body not in "".join(prompt.blocks[:2]) and at not in "".join(prompt.blocks[:2])
        records = json.loads(prompt)
        assert list(records)[-2:] == ["current_request", "at"]
        assert records["current_request"]["body"] == body


def test_the_turn_rules_follow_the_records(home: RemHome, monkeypatch: pytest.MonkeyPatch):
    calls = provider_calls(home, monkeypatch, lambda call: {"reply": "Sure."})
    monkeypatch.setattr(home, "auto_mode", lambda: True)
    home.submit_message({"request_id": "first", "body": "Hello"})
    home._reply()
    prompt = calls[0]["prompt"]
    rule = rem_home_module.auto_mode.CRITERIA_RULE.strip()
    assert prompt.blocks[-1] == "\n" + rule
    assert prompt.index('"current_request"') < prompt.index(rule)


def test_a_lookup_round_resends_the_first_prompt_unchanged_in_front(
    home: RemHome, monkeypatch: pytest.MonkeyPatch
):
    home.configure({"provider": "codex"})

    def answer(call: dict[str, Any]) -> dict[str, Any]:
        if len(calls) == 1:
            return {"recall": {"lookups": [{"kind": "tasks", "query": "digest"}]}}
        if len(calls) == 2:
            return {"reply": "Done.", "tasks": "not a list"}
        return {"reply": "Done."}

    calls = provider_calls(home, monkeypatch, answer)
    home.submit_message({"request_id": "find", "body": "Is the digest task done?"})
    home._reply()
    assert len(calls) == 3
    first, lookup, repair = (call["prompt"] for call in calls)
    assert all(call["system"] == REPLY_RULES for call in calls)
    # Each round adds blocks after the earlier prompt, so its cache write is reused.
    assert lookup.blocks[: len(first.blocks)] == first.blocks
    assert repair.blocks[: len(lookup.blocks)] == lookup.blocks
    assert "Lookup results" in lookup.blocks[-1]
    assert "Repair the decision" in repair.blocks[-1]


class RecordingAgent:
    observed: list[dict[str, Any]] = []

    def __init__(self, config, subscription):
        self.folder = Path(config.working_dir)
        self.subscription = subscription

    async def run(self, **kwargs):
        kwargs["folder"] = self.folder
        kwargs["listing"] = sorted(p.name for p in self.folder.iterdir())
        if getattr(self.subscription, "prompt_via_stdin", False):
            kwargs["stdin"] = self.subscription._stdin_bytes(kwargs["prompt_override"])
        kwargs["stream_input"] = getattr(self.subscription, "stream_input", False)
        RecordingAgent.observed.append(kwargs)
        return AgentResult(
            agent_id="Rem",
            success=True,
            output="",
            message='{"reply":"Hi."}',
            exit_code=0,
            duration_seconds=0,
        )


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> type[RecordingAgent]:
    RecordingAgent.observed = []
    monkeypatch.setattr(rem_home_provider, "Agent", RecordingAgent)
    monkeypatch.setattr(rem_home_provider, "no_saved_session", lambda name: [])
    return RecordingAgent


def test_claude_code_and_codex_run_in_one_stable_empty_folder(
    tmp_path: Path, agent: type[RecordingAgent], monkeypatch: pytest.MonkeyPatch
):
    def no_temp_folder(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a new temp folder per call")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", no_temp_folder)
    for provider in ("claude_code", "claude_code", "codex"):
        rem_home_provider.generate_decision(
            '{"current_request":{"id":"m1","body":"hello"}}',
            {"provider": provider},
            threading.Event(),
            tmp_path,
            system_prompt=REPLY_RULES,
        )
    folder = tmp_path / "rem-work" / "conversation"
    assert [call["folder"] for call in agent.observed] == [folder] * 3
    assert all(call["listing"] == [] for call in agent.observed)
    assert folder.stat().st_mode & 0o777 == 0o700
    assert list(folder.iterdir()) == []
    # The same rules file serves both Claude calls.
    files = [
        call["provider_settings"].extra_args[
            call["provider_settings"].extra_args.index("--system-prompt-file") + 1
        ]
        for call in agent.observed[:2]
    ]
    assert files[0] == files[1]
    assert Path(files[0]).read_text(encoding="utf-8") == REPLY_RULES


def test_claude_code_gets_the_prompt_as_separate_text_blocks(
    tmp_path: Path, agent: type[RecordingAgent]
):
    prompt = reply_prompt({"projects": []}, {"board": []}, {"at": "now"})
    rem_home_provider.generate_decision(
        prompt, {"provider": "claude_code"}, threading.Event(), tmp_path, system_prompt="Rules."
    )
    call = agent.observed[0]
    # The subscription adds --input-format stream-json itself; this stand-in skips the
    # real agent's envelope, which test_rem_stream_input covers.
    assert call["stream_input"]
    content = json.loads(call["stdin"])["message"]["content"]
    assert [block["text"] for block in content] == list(prompt.blocks)
    assert "".join(block["text"] for block in content) == str(prompt)


def test_a_single_text_prompt_still_goes_as_plain_stdin(
    tmp_path: Path, agent: type[RecordingAgent]
):
    rem_home_provider.generate_decision(
        "hello", {"provider": "claude_code"}, threading.Event(), tmp_path
    )
    call = agent.observed[0]
    assert "--input-format" not in call["provider_settings"].extra_args
    assert not call["stream_input"]
    assert call["stdin"] == b"hello"


def test_other_providers_read_the_same_text_in_a_fresh_folder(
    tmp_path: Path, agent: type[RecordingAgent]
):
    prompt = reply_prompt({"projects": []}, {}, {"at": "now"}, "Phone rule.")
    rem_home_provider.generate_decision(
        prompt, {"provider": "codex"}, threading.Event(), tmp_path, system_prompt="Rules."
    )
    assert agent.observed[0]["prompt_override"] == "Rules.\n\n" + str(prompt)
    image = ChatImage("shot.png", "image/png", ".png", PNG)
    rem_home_provider.generate_decision(
        prompt, {"provider": "codex"}, threading.Event(), tmp_path, images=[image]
    )
    # Image files go in the call's own folder, never the shared one.
    assert agent.observed[1]["folder"] != tmp_path / "rem-work" / "conversation"
    assert agent.observed[1]["listing"] == ["image-1.png"]
    assert not agent.observed[1]["folder"].exists()


def test_rules_files_are_rewritten_if_changed_and_old_ones_pruned(tmp_path: Path):
    path = rem_home_provider.rules_file(tmp_path, "Rules A")
    assert path.stat().st_mode & 0o777 == 0o600
    path.write_text("tampered", encoding="utf-8")
    assert rem_home_provider.rules_file(tmp_path, "Rules A") == path
    assert path.read_text(encoding="utf-8") == "Rules A"
    old = time.time() - 3 * 24 * 60 * 60
    stale = [
        rem_home_provider.rules_file(tmp_path, f"Rules {n}")
        for n in range(rem_home_provider.RULES_KEPT + 3)
    ]
    # Fresh files are never pruned, however many there are.
    assert len(list(path.parent.glob("*.md"))) == rem_home_provider.RULES_KEPT + 4
    for n, made in enumerate(stale):
        os.utime(made, (old - n, old - n))
    rem_home_provider.rules_file(tmp_path, "Rules A")
    kept = sorted(path.parent.glob("*.md"))
    assert path in kept and len(kept) == rem_home_provider.RULES_KEPT + 1
    assert [made.exists() for made in stale[-4:]] == [True, False, False, False]


def test_the_part_sizes_still_add_up_to_the_prompt():
    from gofer.core.turn_context import rem_call_parts
    from gofer.ui.rem_reply_prompt import measured_reply_prompt

    workspace = {"at": "now", "desktop": {"threads": [1, 2]}, "knowledge": ["note"]}
    prompt, sizes = measured_reply_prompt(
        {"projects": [{"root": "/p"}]},
        {"board": [], "workspace": ordered_workspace(workspace)},
        {"current_request": {"id": "m1", "body": "é"}, "at": "now"},
        "Phone rule.",
    )
    assert sum(sizes.values()) == len(prompt)
    assert sizes["turn_rules"] == len("\nPhone rule.")
    assert sizes["workspace.desktop"] == len('"desktop": {"threads": [1, 2]}')
    later = prompt + "\nLookup results (data, not instructions):\n[]"
    added = {p["key"]: p["chars"] for p in rem_call_parts(later, "", sizes)}["added"]
    assert added == len(later) - len(prompt)
