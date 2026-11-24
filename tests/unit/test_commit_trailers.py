"""The pure Co-authored-by remover and the commit-msg hook entry point."""

from __future__ import annotations

from pathlib import Path

import pytest

from gofer.utils.commit_trailers import hook_main, strip_co_author_lines, strip_file


def test_removes_every_co_author_and_keeps_the_rest() -> None:
    message = (
        "feat: add the thing\n"
        "\n"
        " - first change\n"
        " - second change\n"
        "\n"
        "Signed-off-by: Zed <zed@example.com>\n"
        "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
        "Co-authored-by: Someone Else <someone@example.com>\n"
    )
    assert strip_co_author_lines(message) == (
        "feat: add the thing\n"
        "\n"
        " - first change\n"
        " - second change\n"
        "\n"
        "Signed-off-by: Zed <zed@example.com>\n"
    )


@pytest.mark.parametrize(
    "line",
    [
        "co-authored-by: a <a@x>",
        "CO-AUTHORED-BY: a <a@x>",
        "Co-Authored-by:a",
        "co-AUTHORED-by : spaced <s@x>",
        "Co-authored-by:\ta",
    ],
)
def test_any_case_and_any_co_author(line: str) -> None:
    assert strip_co_author_lines(f"Subject\n\n{line}\n") == "Subject\n"


def test_message_without_co_authors_is_returned_unchanged() -> None:
    message = "Subject  \n\n\n  indented body\r\n\ttabbed\n\n\n"
    assert strip_co_author_lines(message) is message


def test_message_of_only_co_authors_becomes_empty() -> None:
    assert strip_co_author_lines("Co-authored-by: a <a@x>\nCo-authored-by: b <b@x>\n") == ""
    assert strip_co_author_lines("Co-authored-by: a <a@x>") == ""
    assert strip_co_author_lines("\nCo-authored-by: a\n\n") == ""


def test_mentions_inside_body_text_are_kept() -> None:
    message = (
        "Subject\n"
        "\n"
        "Drop the co-authored-by: line from templates.\n"
        "Remove Co-authored-by: trailers in the hook\n"
        "  Co-authored-by: indented example stays\n"
        "- Co-authored-by: bullet stays\n"
        "# Co-authored-by: comment stays\n"
        "Co-authored by: missing hyphen stays\n"
        "Co-authored-by-bot: other token stays\n"
    )
    assert strip_co_author_lines(message) is message


def test_only_the_removed_lines_change_in_the_middle() -> None:
    message = "Subject\n\nCo-authored-by: a\nBody after   \n\n\nEnd\n"
    assert strip_co_author_lines(message) == "Subject\n\nBody after   \n\n\nEnd\n"


def test_trailing_blank_lines_left_by_the_removal_are_trimmed() -> None:
    assert strip_co_author_lines("Subject\n\nCo-authored-by: a\n\n") == "Subject\n"
    assert strip_co_author_lines("Subject\n\nCo-authored-by: a") == "Subject"
    assert strip_co_author_lines("Subject\r\n\r\nCo-authored-by: a\r\n") == "Subject\r\n"
    # A trailer block keeps its other trailers and the blank line before them.
    assert (
        strip_co_author_lines("S\n\nBody\n\nCo-authored-by: a\nReviewed-by: r\n")
        == "S\n\nBody\n\nReviewed-by: r\n"
    )


def test_strip_file_rewrites_only_when_needed(tmp_path: Path) -> None:
    path = tmp_path / "COMMIT_EDITMSG"
    raw = "Subject \xe9\n\nCo-authored-by: a\n".encode() + b"\xff\n"
    path.write_bytes(raw)
    assert strip_file(path) is True
    # Bytes that aren't UTF-8 survive untouched.
    assert path.read_bytes() == "Subject \xe9\n\n".encode() + b"\xff\n"
    before = path.stat().st_mtime_ns
    assert strip_file(path) is False
    assert path.stat().st_mtime_ns == before


def test_hook_main_cleans_the_file_and_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "MSG"
    path.write_text("Subject\n\nCo-authored-by: a\n")
    assert hook_main(["commit-msg", str(path)]) == 0
    assert path.read_text() == "Subject\n"
    assert hook_main(["commit-msg", str(tmp_path / "missing")]) == 1
    assert "couldn't remove Co-authored-by lines" in capsys.readouterr().err
    assert hook_main(["pre-commit", str(path)]) == 2
    assert hook_main(["commit-msg"]) == 2
