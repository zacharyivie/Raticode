"""Terminal output turned into plain text for people to read."""

from __future__ import annotations

import re

# Colour and cursor codes (CSI), window titles and links (OSC, ended by BEL or ST),
# and the two-character escapes a CLI writes for its terminal.
_ESCAPES = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x9b[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|\x1b[ -/]*[0-~]"
)
# Control characters other than tab and line breaks, including a stray escape.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def plain_terminal_text(text: str) -> str:
    """text without terminal escape codes or control characters, lines kept."""
    return _CONTROL.sub("", _ESCAPES.sub("", text))


def one_line(text: str) -> str:
    """plain_terminal_text on one line, with runs of white space as one space."""
    return " ".join(plain_terminal_text(text).split())
