"""Voice tidying: the default follows the default provider, and nothing heard is ever lost."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.core import provider_preferences as preferences
from gofer.core.agent import AgentResult
from gofer.ui import rem_home_provider
from gofer.ui import voice_tidy as tidying


@pytest.fixture(autouse=True)
def isolated_preferences(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(preferences, "get_data_dir", lambda: tmp_path)


def catalog(
    provider: str, models: list[str], version: str = "1.0.0", efforts: bool = True, **extra: Any
) -> dict[str, Any]:
    return {
        "id": provider,
        "displayName": {"claude_code": "Claude Code", "codex": "Codex"}.get(provider, provider),
        "available": True,
        "enabled": True,
        "version": version,
        "deniedModels": [],
        "models": [
            {"id": m, "efforts": [{"id": e} for e in ("low", "medium", "high") if efforts]}
            for m in models
        ],
        **extra,
    }


CATALOGS = {
    "claude_code": catalog("claude_code", ["claude-opus-5-5", "fable"], "2.1.293 (Claude Code)"),
    "codex": catalog("codex", ["gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna"]),
    "opencode": catalog(
        "opencode",
        ["opencode/big-pickle", "opencode/qwen3.8-max", "opencode/qwen3.8-flash"],
        efforts=False,
    ),
    "cursor": catalog("cursor", ["auto", "claude-haiku-5-5-thinking", "claude-haiku-5-5"]),
}


def resolve(preference: dict[str, Any], default: str) -> dict[str, Any]:
    return tidying.selection(
        {**preferences.voice_tidy_preference(), **preference}, default, CATALOGS.get
    )


@pytest.mark.parametrize(
    "default,model,effort",
    [
        ("claude_code", "claude-haiku-5-5", "low"),
        ("codex", "gpt-6-luna", "low"),
        ("opencode", "opencode/qwen3.8-flash", ""),
        ("cursor", "claude-haiku-5-5", "low"),
    ],
)
def test_the_first_choice_is_the_default_providers_fast_model(default, model, effort):
    chosen = resolve({"enabled": True}, default)
    assert (chosen["provider"], chosen["model"], chosen["effort"]) == (default, model, effort)
    assert chosen["followsDefault"] is True and chosen["available"] is True


def test_changing_the_default_provider_moves_an_untouched_choice_with_it():
    assert resolve({}, "codex")["model"] == "gpt-6-luna"
    assert resolve({}, "claude_code")["model"] == "claude-haiku-5-5"
    # A provider the user picked stays, with that provider's fast model.
    picked = resolve({"provider": "codex"}, "claude_code")
    assert (picked["provider"], picked["model"], picked["followsDefault"]) == (
        "codex",
        "gpt-6-luna",
        False,
    )
    # And a model the user picked stays, with the effort they picked.
    exact = resolve({"provider": "codex", "model": "gpt-6-astra", "effort": "medium"}, "x")
    assert (exact["model"], exact["effort"]) == ("gpt-6-astra", "medium")


def test_haiku_needs_the_claude_code_version_that_supports_it():
    old = dict(CATALOGS["claude_code"], version="2.1.289 (Claude Code)")
    chosen = tidying.selection(
        {"enabled": True}, "claude_code", lambda p: old if p == "claude_code" else None
    )
    assert chosen["available"] is False and chosen["model"] == "claude-haiku-5-5"
    assert "2.1.293" in chosen["reason"] and "claude update" in chosen["reason"]
    # Never falls back to the haiku alias, which means Haiku 4.5 on older versions.
    assert chosen["model"] != "haiku"


def test_unlisted_blocked_or_missing_models_are_unavailable_with_a_reason():
    no_qwen = catalog("opencode", ["opencode/big-pickle"])
    chosen = tidying.selection({}, "opencode", lambda p: no_qwen)
    assert chosen["model"] == "" and "has no Qwen 3.8 model. Choose a model" in chosen["reason"]
    blocked = catalog("codex", ["gpt-6-luna"], deniedModels=["gpt-6-luna"])
    assert "blocked" in tidying.selection({}, "codex", lambda p: blocked)["reason"]
    gone = tidying.selection({"provider": "codex", "model": "gpt-9"}, "", CATALOGS.get)
    assert gone["available"] is False and "model list" in gone["reason"]
    missing = tidying.selection({}, "grok", lambda p: None)
    assert missing["available"] is False


def test_saved_preferences_are_validated():
    preferences.save_voice_tidy_preference({"enabled": True})
    assert preferences.voice_tidy_preference()["enabled"] is True
    preferences.save_voice_tidy_preference({"provider": "codex", "model": "gpt-6-astra"})
    # Choosing another provider starts again from that provider's fast model.
    preferences.save_voice_tidy_preference({"provider": "claude_code"})
    assert preferences.voice_tidy_preference()["model"] == ""
    for bad in ({"enabled": "yes"}, {"provider": "nobody"}, {"other": 1}, {}):
        with pytest.raises(ValueError):
            preferences.save_voice_tidy_preference(bad)
    preferences.save_provider_preference("codex", {"deniedModels": ["gpt-6-luna"]})
    with pytest.raises(ValueError, match="Allow this model"):
        preferences.save_voice_tidy_preference({"provider": "codex", "model": "gpt-6-luna"})
    assert preferences.voice_tidy_preference()["enabled"] is True


HEARD = "okay um so can you check if quad code has a voice thing like the one from report 2"


@pytest.mark.parametrize(
    "answer,expected",
    [
        (
            "Okay, so can you check if Claude Code has a voice thing like the one from report 2?",
            "Okay, so can you check if Claude Code has a voice thing like the one from report 2?",
        ),
        (
            "```\nOkay, so can you check if `Claude Code` has a voice thing like the one from "
            "report 2?\n```",
            "Okay, so can you check if Claude Code has a voice thing like the one from report 2?",
        ),
    ],
)
def test_tidied_text_is_cleaned_of_added_formatting(answer, expected):
    assert tidying.check(HEARD, answer) == (expected, "")


@pytest.mark.parametrize(
    "answer,reason",
    [
        ("", "empty"),
        (
            "Okay, so can you check if Claude Code has a voice thing like the one from report 3?",
            "number",
        ),
        ("Sure! Claude Code has no voice mode, but here is what I found " * 3, "changed too much"),
        (
            "Okay, so can you check if Claude Code has a voice thing like report 2?\n"
            "Uncertain: quad code",
            "notes",
        ),
    ],
)
def test_answers_that_go_beyond_tidying_are_refused(answer, reason):
    text, problem = tidying.check(HEARD, answer)
    assert text is None and reason in problem


ON = {
    "enabled": True,
    "available": True,
    "context": True,
    "provider": "claude_code",
    "model": "claude-haiku-5-5",
    "effort": "low",
    "label": "Claude Haiku 5.5",
}


def test_swearing_stays_as_said_and_masked_words_survive_cleanup():
    # Spoken swearing comes back written out; a tidy that censors it is refused, so the
    # words as heard are used instead.
    heard = "what the fuck is this shitty build doing"
    assert tidying.check(heard, "What the fuck is this shitty build doing?") == (
        "What the fuck is this shitty build doing?",
        "",
    )
    text, problem = tidying.check(heard, "What the f*** is this s***ty build doing?")
    assert text is None and "hid words" in problem
    # The recognizer masked them: restoring is allowed, and so is keeping a mask the model
    # can't resolve. Bold cleanup leaves the asterisks of masked words alone.
    masked = "what the f*** is this s*** doing"
    assert tidying.check(masked, "What the fuck is this s*** doing?") == (
        "What the fuck is this s*** doing?",
        "",
    )
    assert tidying.check(masked, "What the f*** is this s*** doing?") == (
        "What the f*** is this s*** doing?",
        "",
    )
    assert tidying.check("check the build", "Check the **build**.") == ("Check the build.", "")


def test_the_instructions_keep_swearing_and_only_restore_unambiguous_masks():
    rules = tidying.INSTRUCTIONS.lower()
    assert "never censor" in rules
    assert "exactly one word" in rules and "asterisks included" in rules


def test_tidy_sends_the_instructions_and_context_and_returns_the_tidied_words(tmp_path):
    seen: dict[str, Any] = {}

    def run(prompt, config, cancel, data_dir, **options):
        seen.update(prompt=prompt, config=config, **options)
        return "Okay, so can you check if Claude Code has a voice thing like the one from report 2?"

    result = tidying.tidy(HEARD, ON, tmp_path, context="Names: Claude Code", run=run)
    assert result["tidied"] is True and result["text"].startswith("Okay, so")
    assert result["model"] == "Claude Haiku 5.5"
    assert seen["config"] == {
        "provider": "claude_code",
        "model": "claude-haiku-5-5",
        "effort": "low",
    }
    assert seen["system_prompt"] == tidying.INSTRUCTIONS
    assert "<message>\n" + HEARD in seen["prompt"] and "Names: Claude Code" in seen["prompt"]
    assert seen["timeout"] == tidying.TIME_LIMIT
    # Without the context option the chat stays on the desktop.
    tidying.tidy(HEARD, {**ON, "context": False}, tmp_path, context="Names: secret", run=run)
    assert "secret" not in seen["prompt"]


def test_a_continuation_sends_the_text_before_it_but_tidies_only_the_new_words(tmp_path):
    seen: dict[str, Any] = {}

    def run(prompt, config, cancel, data_dir, **options):
        seen["prompt"] = prompt
        return "and invite the whole team."

    before = "x" * 500 + " Book a room for Friday."
    result = tidying.tidy("and invite the whole team", ON, tmp_path, before=before, run=run)
    assert result == {**result, "text": "and invite the whole team.", "tidied": True}
    written = seen["prompt"].split("<written>\n", 1)[1].split("\n</written>", 1)[0]
    # Only the end of the text before goes, enough to read on from.
    assert written.endswith("Book a room for Friday.")
    assert len(written) == tidying.BEFORE_CHARS
    assert seen["prompt"].index("</written>") < seen["prompt"].index("<message>")
    assert "<message>\nand invite the whole team\n</message>" in seen["prompt"]
    # Without it, the prompt is as before.
    tidying.tidy("and invite the whole team", ON, tmp_path, run=run)
    assert "<written>" not in seen["prompt"]


def test_a_dictation_too_long_to_tidy_comes_back_whole(tmp_path):
    heard = " ".join(f"word{index}" for index in range(900))
    assert len(heard) > tidying.MAX_CHARS

    def run(*_args, **_options):
        raise AssertionError("not sent")

    result = tidying.tidy(heard, ON, tmp_path, run=run)
    assert result["tidied"] is False and result["text"] == heard
    assert "too long" in result["reason"]


@pytest.mark.parametrize(
    "chosen,failure,reason",
    [
        ({**ON, "enabled": False}, None, "Tidying is off."),
        ({**ON, "available": False, "reason": "Run claude update."}, None, "Run claude update."),
        (ON, ValueError("Not logged in. Please run /login"), "isn't signed in"),
        (ON, ValueError("Rate limit reached"), "usage limit"),
        (ON, InterruptedError("Rem stopped"), "stopped"),
        (ON, ValueError("boom"), "Tidying failed: boom"),
    ],
)
def test_every_failure_returns_the_heard_words_with_a_reason(tmp_path, chosen, failure, reason):
    def run(*_args, **_options):
        raise failure

    result = tidying.tidy(HEARD, chosen, tmp_path, run=run)
    assert result["text"] == HEARD and result["tidied"] is False
    assert reason in result["reason"]


def test_a_slow_provider_is_reported_as_slow(tmp_path):
    ticks = iter([0.0, tidying.TIME_LIMIT, tidying.TIME_LIMIT])

    def run(*_args, **_options):
        raise ValueError("process exited")

    result = tidying.tidy(HEARD, ON, tmp_path, run=run, clock=lambda: next(ticks))
    assert result["reason"] == "Tidying took too long." and result["text"] == HEARD


def test_chat_context_keeps_names_and_recent_messages_but_not_secrets():
    from tests.unit.test_device_desktop_control import Rem

    rem = Rem("/tmp/project")
    rem.store.config = {"projects": [{"root": "/tmp/project", "name": "Raticode Mobile"}]}
    rem.store.messages.append(
        {
            "id": "m3",
            "role": "user",
            "body": "token = ghp_AbCdEf1234567890abcdefGHIJKL1234",
            "created_at": "t3",
        }
    )
    text = tidying.chat_context(rem)
    assert "Raticode Mobile" in text and "Build check" in text
    assert "User: Check builds" in text and "ghp_" not in text
    assert tidying.chat_context(None) == ""


def test_generate_text_gives_claude_code_the_instructions_as_its_system_prompt(
    tmp_path, monkeypatch
):
    observed: list[dict[str, Any]] = []

    class Agent:
        def __init__(self, config, subscription):
            pass

        async def run(self, **kwargs):
            observed.append(kwargs)
            return AgentResult(
                agent_id="Rem",
                success=True,
                output="",
                message="Tidied.",
                exit_code=0,
                duration_seconds=0,
            )

    monkeypatch.setattr(rem_home_provider, "Agent", Agent)
    monkeypatch.setattr(rem_home_provider, "no_saved_session", lambda name: [])
    cancel = threading.Event()
    text = rem_home_provider.generate_text(
        "the message",
        {"provider": "claude_code", "model": "claude-haiku-5-5"},
        cancel,
        tmp_path,
        system_prompt="Tidy only.",
        timeout=15,
    )
    assert text == "Tidied."
    args = observed[0]["provider_settings"].extra_args
    assert args[args.index("--system-prompt") + 1] == "Tidy only."
    assert observed[0]["prompt_override"] == "the message" and observed[0]["timeout"] == 15
    rem_home_provider.generate_text(
        "the message", {"provider": "codex"}, cancel, tmp_path, system_prompt="Tidy only."
    )
    assert observed[1]["prompt_override"] == "Tidy only.\n\nthe message"
    assert "--system-prompt" not in observed[1]["provider_settings"].extra_args
