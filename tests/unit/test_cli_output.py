"""The shared CLI output helpers: the unseen-change prompt and the error exit."""

from __future__ import annotations

import json
from typing import Any

import pytest
import typer

from gofer.cli import approval, output
from gofer.cli.commands import rattish_cache, rattish_secrets, rattish_shortcuts, rattish_triggers

CUT = [{"path": "prompts/ask.md", "absolutePath": "/p/prompts/ask.md", "reason": "cut"}]


def test_rattish_commands_share_one_unseen_prompt_and_one_error_helper() -> None:
    assert rattish_triggers.confirm_unseen is output.confirm_unseen
    # The shortcut approve flow asks through the shared approval helper.
    assert approval.confirm_unseen is output.confirm_unseen
    for module in (rattish_shortcuts, rattish_secrets, rattish_cache):
        assert module.fail is output.fail
        assert not hasattr(module, "_fail")
    for module in (rattish_triggers, rattish_shortcuts):
        assert not hasattr(module, "_confirm_unseen")
        assert not hasattr(module, "_unseen_lines")


def _no_prompt(*_args: Any, **_kwargs: Any) -> bool:
    raise AssertionError("asked")


@pytest.mark.parametrize(
    ("files", "yes", "unseen", "fmt", "expected"),
    [
        ([], False, False, "text", False),
        ([], False, True, "text", True),
        (CUT, False, True, "json", True),
        (CUT, True, False, "text", False),
        (CUT, False, False, "json", False),
    ],
)
def test_confirm_unseen_answers_without_asking(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    files: list[dict[str, Any]],
    yes: bool,
    unseen: bool,
    fmt: str,
    expected: bool,
) -> None:
    monkeypatch.setattr(typer, "confirm", _no_prompt)
    assert output.confirm_unseen(files, yes=yes, unseen=unseen, fmt=fmt) is expected
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("answer", [True, False])
def test_confirm_unseen_lists_the_files_and_asks_a_second_time(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], answer: bool
) -> None:
    asked: list[tuple[str, bool]] = []

    def confirm(text: str, default: bool = True) -> bool:
        asked.append((text, default))
        return answer

    monkeypatch.setattr(typer, "confirm", confirm)
    assert output.confirm_unseen(CUT, yes=False, unseen=False, fmt="text") is answer
    assert capsys.readouterr().out == (
        "These files' changes weren't shown in full:\n"
        "  /p/prompts/ask.md (diff cut short)\n"
        "Open them to read them before you approve.\n"
    )
    assert asked == [("Approve them without seeing all of their changes?", False)]


def test_fail_prints_json_on_stdout_or_text_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_json = output.fail("Nope.", "json")
    assert exit_json.exit_code == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"ok": False, "error": "Nope."}
    assert captured.out == '{"error": "Nope.", "ok": false}\n'
    assert captured.err == ""

    exit_text = output.fail("Nope.", "text", 2)
    assert exit_text.exit_code == 2
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "Nope.\n")
