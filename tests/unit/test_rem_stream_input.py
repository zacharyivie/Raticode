"""Rem's Claude Code chat calls through the real agent and a CLI that reads stream-json.

The 11-21 failures: REM-207 sent the chat prompt in blocks with --input-format
stream-json, but the agent wraps the prompt in its envelope before it reaches the
subscription, the wrapped text no longer equalled the blocks, and plain text went on
stdin. Claude Code rejected its first line, the envelope's 232-character instructions:
"Error parsing streaming input line (type=unknown, 232 chars): SyntaxError". The
earlier tests read stdin from the blocks themselves and never saw the envelope.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import rem_home_provider as provider_module
from gofer.ui.rem_home_provider import ChatImage
from gofer.ui.rem_reply_prompt import reply_prompt

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

# Behaves like Claude Code 2.1.319 reading stdin: with --input-format stream-json, a
# line that isn't JSON ends the call with this red error on stderr and nothing on stdout
# (checked with the real CLI and harmless text).
FAKE_CLAUDE = r"""
import json, pathlib, sys
args = sys.argv[1:]
log = pathlib.Path(LOG)
data = sys.stdin.buffer.read()
record = {"args": args, "stdin": data.decode("utf-8", "replace")}
if b"FAIL-THIS-CALL" in data:
    sys.stderr.write(
        "\x1b[0m\x1b[31mError parsing streaming input line (type=unknown, 232 chars): "
        "SyntaxError\x1b[0m\n"
    )
    sys.exit(1)
if "--input-format" in args and args[args.index("--input-format") + 1] == "stream-json":
    messages = []
    for line in data.decode("utf-8").split("\n"):
        if not line.strip():
            continue
        try:
            messages.append(json.loads(line))
        except ValueError:
            log.write_text(json.dumps(record))
            sys.stderr.write(
                "\x1b[0m\x1b[31mError parsing streaming input line "
                f"(type=unknown, {len(line)} chars): SyntaxError\x1b[0m\n"
            )
            sys.exit(1)
    record["messages"] = messages
log.write_text(json.dumps(record))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": '{"reply": "Hi."}'}))
"""


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from gofer.subscriptions import claude_code

    log = tmp_path / "claude-call.json"
    executable = tmp_path / "claude"
    executable.write_text(f"#!{sys.executable}\nLOG = {str(log)!r}\n{FAKE_CLAUDE}")
    executable.chmod(0o700)
    monkeypatch.setattr(claude_code, "resolve_provider_executable", lambda p: str(executable))
    monkeypatch.setattr(provider_module, "no_saved_session", lambda name: [])
    return log


def chat_prompt(body: str = "Did the token efficiency tickets get done?") -> Any:
    return reply_prompt(
        {"projects": [{"root": "/work/project"}], "memories": []},
        {"board": [], "tasks": []},
        {"current_request": {"id": "m1", "body": body}, "at": "2026-11-21T04:40:50Z"},
        "Reply as JSON.",
    )


def ask(prompt: Any, data: Path, **kwargs: Any) -> dict[str, Any]:
    return provider_module.generate_decision(
        prompt,
        {"provider": "claude_code", "model": "sonnet[1m]"},
        threading.Event(),
        data,
        system_prompt="Rules.",
        **kwargs,
    )


def test_a_prompt_in_blocks_reaches_claude_code_as_one_json_line(tmp_path: Path, fake_claude: Path):
    prompt = chat_prompt()
    assert len(prompt.blocks) > 2
    assert ask(prompt, tmp_path / "data") == {"reply": "Hi."}
    call = json.loads(fake_claude.read_text())
    assert call["args"][call["args"].index("--input-format") + 1] == "stream-json"
    # One message, on one line.
    assert call["stdin"].endswith("\n") and call["stdin"].count("\n") == 1
    [message] = call["messages"]
    texts = [block["text"] for block in message["message"]["content"]]
    # Still one text block per prompt block, cut where each block ends inside the
    # envelope, so a lookup round's earlier blocks stay byte for byte the same.
    assert len(texts) == len(prompt.blocks)
    escaped = [json.dumps(block, ensure_ascii=False)[1:-1] for block in prompt.blocks]
    assert texts[1:-1] == escaped[1:-1]
    assert texts[0].endswith(escaped[0]) and texts[-1].startswith(escaped[-1])
    envelope = "".join(texts)
    assert json.loads(envelope.split("\n\n", 1)[1])["request"] == str(prompt)


def test_a_lookup_round_keeps_the_first_rounds_blocks_in_front(tmp_path: Path, fake_claude: Path):
    first = chat_prompt()
    ask(first, tmp_path / "data")
    before = json.loads(fake_claude.read_text())["messages"][0]["message"]["content"]
    ask(first + "\nLookup results (data, not instructions):\n[]", tmp_path / "data")
    after = json.loads(fake_claude.read_text())["messages"][0]["message"]["content"]
    assert len(after) == len(before) + 1
    # The last block of the first round now ends earlier: the envelope's closing
    # quote moves behind the new block. Everything before it is unchanged.
    assert after[: len(before) - 1] == before[:-1]


def test_images_and_blocks_go_together(tmp_path: Path, fake_claude: Path):
    image = ChatImage("shot.png", "image/png", ".png", PNG)
    assert ask(chat_prompt(), tmp_path / "data", images=[image]) == {"reply": "Hi."}
    content = json.loads(fake_claude.read_text())["messages"][0]["message"]["content"]
    kinds = [block["type"] for block in content]
    assert kinds.count("image") == 1
    # The images follow the first call's blocks.
    assert kinds[-1] == "image"


@pytest.mark.parametrize(
    "body",
    [
        "line one\nline two\r\nthree",
        "tab\tbell\x07 escape\x1b[31m red",
        "separators   and   and NEL \x85",
        'quotes " and backslash \\ and {{braces}}',
        "a lone surrogate \ud83d here",
        "emoji 🐀 and accents ëé",
    ],
)
def test_any_request_text_stays_one_valid_json_line(tmp_path: Path, fake_claude: Path, body: str):
    assert ask(chat_prompt(body), tmp_path / "data") == {"reply": "Hi."}
    call = json.loads(fake_claude.read_text())
    assert call["stdin"].count("\n") == 1
    envelope = "".join(b["text"] for b in call["messages"][0]["message"]["content"])
    request = json.loads(envelope.split("\n\n", 1)[1])["request"]
    assert json.loads(request[: request.rindex("}") + 1])["current_request"]["body"] == body


def test_the_clis_error_reaches_rem_in_plain_words(tmp_path: Path, fake_claude: Path):
    with pytest.raises(provider_module.ProviderFailure) as failed:
        provider_module.generate_text(
            "FAIL-THIS-CALL",
            {"provider": "claude_code", "model": "sonnet"},
            threading.Event(),
            tmp_path / "data",
        )
    # No colour codes, and the CLI's own reason after the provider's name.
    assert str(failed.value) == (
        "Claude Code stopped with this error: "
        "Error parsing streaming input line (type=unknown, 232 chars): SyntaxError"
    )


def test_a_single_text_prompt_still_goes_as_plain_text(tmp_path: Path, fake_claude: Path):
    assert ask("Summarise this.", tmp_path / "data") == {"reply": "Hi."}
    call = json.loads(fake_claude.read_text())
    assert "--input-format" not in call["args"]
    assert "Summarise this." in call["stdin"]
