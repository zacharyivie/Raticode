"""Remove Co-authored-by lines from a commit message, and nothing else.

Standard library only: git runs ``Raticode git-hook commit-msg FILE`` for every
commit while Settings > Rem > Remove Co-authored-by lines is on, so this module
has to load fast.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# A git trailer starts at the first column: the token, optional spaces, then a colon.
# Indented lines and lines that only mention the words later on are body text.
_CO_AUTHOR_LINE = re.compile(r"co-authored-by[ \t]*:", re.IGNORECASE)


def is_co_author_line(line: str) -> bool:
    return _CO_AUTHOR_LINE.match(line) is not None


def strip_co_author_lines(message: str) -> str:
    """``message`` without its Co-authored-by lines; every other character is kept.

    Lines are split on ``\\n`` only, so ``\\r\\n`` endings and other characters
    survive as they were. When a removed line was among the message's last lines,
    the blank lines left at the end are trimmed too, keeping the final newline if
    the message had one. A message with nothing but those lines becomes empty.
    """
    lines = message.split("\n")
    kept = [line for line in lines if not is_co_author_line(line)]
    if len(kept) == len(lines):
        return message
    # The last line that is neither blank nor removed; everything after it was blank
    # lines and Co-authored-by lines.
    last = max(
        (i for i, line in enumerate(lines) if line.strip() and not is_co_author_line(line)),
        default=-1,
    )
    if last < 0:
        return ""
    if not any(is_co_author_line(line) for line in lines[last + 1 :]):
        return "\n".join(kept)
    body = [line for line in lines[: last + 1] if not is_co_author_line(line)]
    return "\n".join(body) + ("\n" if message.endswith("\n") else "")


def strip_file(path: Path) -> bool:
    """Rewrite the message file without Co-authored-by lines. True when it changed."""
    raw = path.read_bytes()
    # surrogateescape keeps bytes that aren't UTF-8 exactly as they were.
    text = raw.decode("utf-8", errors="surrogateescape")
    cleaned = strip_co_author_lines(text)
    if cleaned == text:
        return False
    path.write_bytes(cleaned.encode("utf-8", errors="surrogateescape"))
    return True


def hook_main(argv: list[str]) -> int:
    """``Raticode git-hook commit-msg FILE``: git's commit-msg hook.

    A message file it can't read or write stops the commit (exit 1), so a commit
    never goes through unchecked while the setting is on.
    """
    if len(argv) != 2 or argv[0] != "commit-msg":
        print("usage: Raticode git-hook commit-msg MESSAGE_FILE", file=sys.stderr)
        return 2
    try:
        strip_file(Path(argv[1]))
    except OSError as exc:
        print(
            f"Raticode couldn't remove Co-authored-by lines from {argv[1]}: {exc}",
            file=sys.stderr,
        )
        return 1
    return 0
