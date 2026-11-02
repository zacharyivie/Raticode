from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import rem_home_memory as memory_book
from gofer.ui import rem_home_provider as provider_module
from gofer.ui.rem_home import RemHome
from gofer.ui.rem_home_models import Decision
from tests.unit.test_rem_home import home as home
from tests.unit.test_rem_home import recipe


def remember(home: RemHome, memories: dict[str, Any], mode: str | None = None) -> None:
    with home.store.connect() as db:
        home.store.set_setting(db, "memories", memories)
        if mode:
            home.store.set_setting(db, "memory_settings", {"mode": mode})


def saved(home: RemHome) -> dict[str, Any]:
    with home.store.read() as db:
        return home.store.setting(db, "memories")


def reply(home: RemHome, request: str = "m1") -> dict[str, Any]:
    return next(m for m in home.store.conversation()["messages"] if m["id"] == f"reply-{request}")


def ask(home: RemHome, body: str, request: str = "m1") -> None:
    home.submit_message({"request_id": request, "body": body})
    home._reply()


def test_rem_corrects_and_forgets_memories_by_id_and_the_reply_can_undo_it(home: RemHome):
    remember(
        home,
        {
            "tea": {"text": "Drinks coffee.", "created_at": "2026-10-01T00:00:00+00:00"},
            "oauth": {"text": "Uses Google OAuth.", "created_at": "2026-10-02T00:00:00+00:00"},
        },
    )
    prompts = []

    def generate(prompt, config, cancel):
        prompts.append(prompt)
        return {
            "reply": "Got it.",
            "memories": ["Prefers HTML reports"],
            "memory_changes": [
                {"id": "tea", "action": "update", "text": "Drinks tea, not coffee."},
                {"id": "oauth", "action": "forget"},
            ],
        }

    home.generate = generate
    ask(home, "I switched to tea, forget the OAuth thing, and I like HTML reports")
    # Memories reach the model with IDs, so it can change them instead of duplicating.
    assert '"id": "tea"' in prompts[0] and "memory_changes" in prompts[0]
    after = saved(home)
    assert after["tea"]["text"] == "Drinks tea, not coffee." and after["tea"]["edited_at"]
    assert "oauth" not in after
    added = [m for i, m in after.items() if i != "tea"]
    assert [m["text"] for m in added] == ["Prefers HTML reports"]
    note = reply(home)["memory"]
    assert [e["text"] for e in note["saved"]] == ["Prefers HTML reports"]
    assert note["updated"][0]["previous"] == "Drinks coffee."
    assert note["forgotten"][0]["text"] == "Uses Google OAuth."

    home.undo_memory("reply-m1")
    undone = saved(home)
    assert undone["tea"]["text"] == "Drinks coffee."
    assert undone["oauth"]["text"] == "Uses Google OAuth."
    assert len(undone) == 2
    assert reply(home)["memory"]["undone_at"]
    # Undoing twice changes nothing.
    home.undo_memory("reply-m1")
    assert saved(home) == undone


def test_unknown_memory_ids_get_one_repair_instead_of_failing(home: RemHome):
    remember(home, {"tea": {"text": "Drinks coffee.", "created_at": "2026-10-01T00:00:00+00:00"}})
    prompts = []

    def generate(prompt, config, cancel):
        prompts.append(prompt)
        if len(prompts) == 1:
            return {
                "reply": "Forgotten.",
                "memory_changes": [{"id": "made-up", "action": "forget"}],
            }
        assert "don't exist: made-up" in prompt
        return {"reply": "Forgotten.", "memory_changes": [{"id": "tea", "action": "forget"}]}

    home.generate = generate
    ask(home, "Forget what I drink")
    assert len(prompts) == 2
    assert saved(home) == {}


def test_paused_memory_saves_nothing_new_but_still_forgets(home: RemHome):
    remember(
        home,
        {"tea": {"text": "Drinks coffee.", "created_at": "2026-10-01T00:00:00+00:00"}},
        mode="paused",
    )
    prompts = []

    def generate(prompt, config, cancel):
        prompts.append(prompt)
        return {
            "reply": "Okay.",
            "memories": ["Lives in Ohio"],
            "memory_changes": [{"id": "tea", "action": "forget"}],
        }

    home.generate = generate
    ask(home, "I live in Ohio. Forget the coffee.")
    assert '"memory_mode": "paused"' in prompts[0]
    assert saved(home) == {}
    assert "saved" not in reply(home)["memory"]


def test_memory_off_hides_memories_and_changes_nothing(home: RemHome):
    remember(
        home,
        {"tea": {"text": "Drinks coffee.", "created_at": "2026-10-01T00:00:00+00:00"}},
        mode="off",
    )
    prompts = []

    def generate(prompt, config, cancel):
        prompts.append(prompt)
        return {"reply": "Okay.", "memories": ["Lives in Ohio"]}

    home.generate = generate
    ask(home, "I live in Ohio")
    assert "Drinks coffee." not in prompts[0]
    assert saved(home) == {
        "tea": {"text": "Drinks coffee.", "created_at": "2026-10-01T00:00:00+00:00"}
    }
    assert "memory" not in reply(home)


def test_secret_like_memories_are_never_saved(home: RemHome):
    home.generate = lambda p, c, e: {
        "reply": "Okay.",
        "memories": ["My API key is sk-ant-abcdefghijklmnop1234", "Prefers short replies"],
    }
    ask(home, "Remember my key sk-ant-abcdefghijklmnop1234 and keep replies short")
    assert [m["text"] for m in saved(home).values()] == ["Prefers short replies"]
    assert reply(home)["memory"]["refused"] == 1
    with pytest.raises(ValueError, match="password, key or token"):
        home.add_memory({"text": "password: hunter22"})


@pytest.mark.parametrize(
    "text",
    [
        "sk-proj-abcdefghij0123456789",
        "ghp_abcdefghijklmnopqrstuvwxyz0123",
        "AKIAABCDEFGHIJKLMNOP",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "token = abc12345",
        "Zx9Qw8Er7Ty6Ui5Op4As3Df2Gh1Jk0Lm9Nb",
    ],
)
def test_secret_detector_catches_keys(text: str):
    assert memory_book.looks_secret(text)


@pytest.mark.parametrize(
    "text",
    [
        "Prefers HTML reports in the Neon Horizon theme",
        "The APK hash is cc53542b64edc8aa5719d9f7f49f1e47ed202b32a8733866be9a9eaf14b49b07",
        "Never use Google OAuth for the YouTube digest",
        "Keeps secrets out of chat",
        "The secret is to keep replies short",
    ],
)
def test_secret_detector_allows_ordinary_preferences(text: str):
    assert not memory_book.looks_secret(text)


def test_duplicate_memories_are_not_saved_twice(home: RemHome):
    remember(home, {"tea": {"text": "Drinks tea.", "created_at": "2026-10-01T00:00:00+00:00"}})
    home.generate = lambda p, c, e: {"reply": "Okay.", "memories": ["drinks tea.", "Drinks tea."]}
    ask(home, "I drink tea")
    assert list(saved(home)) == ["tea"]
    assert "memory" not in reply(home)


def test_prompts_carry_every_memory_until_the_cap_then_the_most_relevant():
    few = {str(n): {"text": f"fact {n}", "created_at": f"2026-10-{n + 1:02d}"} for n in range(5)}
    shown, left = memory_book.choose(few, "anything")
    assert left == 0 and [e["id"] for e in shown] == ["4", "3", "2", "1", "0"]

    many = {
        str(n): {"text": f"ordinary note number {n}", "created_at": f"2026-10-01T00:00:{n:02d}"}
        for n in range(60)
    }
    many["old"] = {"text": "The YouTube digest runs at 06:00", "created_at": "2026-01-01"}
    shown, left = memory_book.choose(many, "When does the youtube digest run?")
    assert len(shown) == memory_book.PROMPT_LIMIT and left == 61 - memory_book.PROMPT_LIMIT
    assert shown[0]["id"] == "old"
    assert "59" in [e["id"] for e in shown]
    fields = memory_book.for_prompt(many, {}, "youtube")
    assert fields["memories_not_shown"] == left


def test_workers_receive_saved_memories_on_every_provider(home: RemHome, monkeypatch):
    monkeypatch.setattr(home, "_delegation_context", lambda run, config: {})
    remember(home, {"html": {"text": "Prefers HTML reports.", "created_at": "2026-10-01"}})
    run = {"id": "run-1", "epoch": 1, "recipe": recipe()}
    assert home._worker_background(run, {}) == {"user_memories": ["Prefers HTML reports."]}
    with home.store.connect() as db:
        home.store.set_setting(db, "memory_settings", {"mode": "off"})
    assert home._worker_background({**run, "epoch": 2}, {}) == {}


def test_forgetting_a_memory_removes_its_words_from_reply_notes(home: RemHome):
    home.generate = lambda p, c, e: {"reply": "Okay.", "memories": ["Lives in Dayton"]}
    ask(home, "I live in Dayton")
    (identity,) = saved(home)
    home.edit_memory(identity, {"text": "Lives in Columbus"})
    assert reply(home)["memory"]["saved"][0]["text"] == "Lives in Columbus"
    home.forget_memory(identity)
    note = reply(home)["memory"]
    assert note["saved"] == [{"id": identity, "removed": True}]
    with sqlite3.connect(home.store.path) as db:
        rows = [row[0] for row in db.execute("SELECT body FROM messages")]
    assert not any("Columbus" in row for row in rows)
    # Undo can't bring back what the user removed by hand.
    home.undo_memory("reply-m1")
    assert saved(home) == {}


def test_undo_keeps_memories_the_user_edited_after_the_reply(home: RemHome):
    remember(home, {"a": {"text": "Drinks tea.", "created_at": "2026-10-01T00:00:00+00:00"}})
    home.generate = lambda p, c, e: {
        "reply": "Noted.",
        "memories": ["Likes jazz."],
        "memory_changes": [{"id": "a", "action": "update", "text": "Drinks coffee."}],
    }
    ask(home, "I drink coffee now and I like jazz")
    jazz = next(i for i, m in saved(home).items() if m["text"] == "Likes jazz.")
    home.edit_memory("a", {"text": "Drinks green tea."})
    home.edit_memory(jazz, {"text": "Likes blues."})
    # The note shows the current words but remembers what the reply wrote.
    note = reply(home)["memory"]
    assert note["updated"][0]["text"] == "Drinks green tea."
    assert note["updated"][0]["written"] == "Drinks coffee."

    home.undo_memory("reply-m1")
    after = {i: m["text"] for i, m in saved(home).items()}
    assert after == {"a": "Drinks green tea.", jazz: "Likes blues."}
    assert reply(home)["memory"]["undone_at"]


def test_undo_reverses_unedited_changes_next_to_edited_ones(home: RemHome):
    remember(home, {"a": {"text": "Drinks tea.", "created_at": "2026-10-01T00:00:00+00:00"}})
    home.generate = lambda p, c, e: {
        "reply": "Noted.",
        "memories": ["Likes jazz."],
        "memory_changes": [{"id": "a", "action": "update", "text": "Drinks coffee."}],
    }
    ask(home, "I drink coffee now and I like jazz")
    jazz = next(i for i, m in saved(home).items() if m["text"] == "Likes jazz.")
    home.edit_memory(jazz, {"text": "Likes blues."})
    home.undo_memory("reply-m1")
    assert {i: m["text"] for i, m in saved(home).items()} == {
        "a": "Drinks tea.",
        jazz: "Likes blues.",
    }


def test_user_can_add_memories_and_switch_modes(home: RemHome):
    listed = home.add_memory({"text": "  Works in Eastern time  "})
    assert [m["text"] for m in listed["memories"]] == ["Works in Eastern time"]
    assert listed["mode"] == "on"
    assert len(home.add_memory({"text": "works in eastern time"})["memories"]) == 1
    assert home.memory_mode({"mode": "paused"})["mode"] == "paused"
    with pytest.raises(ValueError):
        home.memory_mode({"mode": "sometimes"})
    with pytest.raises(ValueError):
        home.add_memory({"text": "x", "pinned": True})


def test_clear_history_erases_memories_but_keeps_the_memory_mode(home: RemHome):
    home.add_memory({"text": "Works in Eastern time"})
    home.memory_mode({"mode": "paused"})
    conversation = home.store.conversation()["conversation"]
    home.clear_history(
        {"request_id": "clear", "conversation_id": conversation["id"], "confirm": True}
    )
    assert home.memories() == {"memories": [], "mode": "paused"}


def test_memory_change_validation():
    with pytest.raises(ValueError):
        Decision.model_validate(
            {"reply": "Hi", "memory_changes": [{"id": "a", "action": "update"}]}
        )
    with pytest.raises(ValueError):
        Decision.model_validate({"reply": "Hi", "memory_changes": [{"id": "a", "action": "pin"}]})


def test_task_replies_carry_the_memory_note(home: RemHome, tmp_path: Path):
    home.generate = lambda p, c, e: {
        "reply": "I’ll handle it.",
        "tasks": [recipe(tmp_path)],
        "memories": ["Prefers concise updates"],
    }
    ask(home, "Implement this feature, and keep updates concise")
    queued = next(m for m in home.store.conversation()["messages"] if m["id"].startswith("queued-"))
    assert queued["memory"]["saved"][0]["text"] == "Prefers concise updates"


def write_cli(path: Path, help_text: str, log: Path) -> None:
    path.write_text(
        f"#!{sys.executable}\nimport sys, pathlib\n"
        f"pathlib.Path({str(log)!r}).open('a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"print({help_text!r})\n"
    )
    path.chmod(0o755)


def test_rem_chat_calls_ask_provider_clis_not_to_save_sessions(tmp_path: Path, monkeypatch):
    log = tmp_path / "help.log"
    claude = tmp_path / "claude"
    old_codex = tmp_path / "codex"
    write_cli(claude, "  --no-session-persistence   Disable session persistence", log)
    write_cli(old_codex, "  --json   Print events", log)
    paths = {"claude_code": str(claude), "codex": str(old_codex)}
    monkeypatch.setattr(provider_module, "resolve_provider_executable", lambda p: paths.get(p))
    provider_module._lists_option.cache_clear()
    assert provider_module.no_saved_session("claude_code") == ["--no-session-persistence"]
    # An older CLI without the option keeps working without it.
    assert provider_module.no_saved_session("codex") == []
    assert provider_module.no_saved_session("cursor") == []
    provider_module.no_saved_session("claude_code")
    assert log.read_text().splitlines() == ["--help", "exec --help"]
    provider_module._lists_option.cache_clear()


def test_phone_record_shows_texts_only():
    record = {
        "saved": [{"id": "a", "text": "Prefers tea"}],
        "forgotten": [{"id": "b", "text": "Old", "item": {"text": "Old", "source": "x"}}],
        "refused": 1,
    }
    shown = memory_book.phone_record(record)
    assert shown == {
        "saved": [{"id": "a", "text": "Prefers tea", "removed": False}],
        "forgotten": [{"id": "b", "text": "Old", "removed": False}],
        "refused": 1,
        "undone": False,
    }
    assert memory_book.phone_record({}) is None
    assert json.dumps(shown)
