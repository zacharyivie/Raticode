#!/usr/bin/env python3
"""Fail when a commit about to land on dev credits an AI tool in its message.

Commit messages in this repository carry no ``Co-Authored-By`` line for an AI coding
tool and no "Generated with ..." line. Some agent tools add them on their own, so
``scripts/check-dev.py`` runs this before a branch lands. It reads the messages of
the commits in ``<base>..HEAD`` and prints each offending commit and line.

A co-author line for a person is fine; only lines naming an AI tool or an AI
vendor's no-reply address count.

Usage:
    python3 scripts/check-commit-messages.py              # commits not yet on dev
    python3 scripts/check-commit-messages.py --base main

Exit codes: 0 when no commit credits an AI tool, 1 when one does, 2 when git fails.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Tool and vendor names only; a person's name never matches these.
AI_NAMES = r"claude|anthropic|codex|openai|chatgpt|gpt-\d|copilot|gemini|grok"
CO_AUTHOR = re.compile(rf"^\s*co-authored-by:.*\b(?:{AI_NAMES})", re.IGNORECASE)
GENERATED = re.compile(rf"generated (?:with|by)\b.*\b(?:{AI_NAMES})", re.IGNORECASE)
# Commits are separated by a NUL byte, and each starts with its hash on its own line.
LOG_FORMAT = "%H%n%B%x00"


def offending_lines(message: str) -> list[str]:
    return [
        line.strip()
        for line in message.splitlines()
        if CO_AUTHOR.search(line) or GENERATED.search(line)
    ]


def commit_messages(base: str, root: Path | None = None) -> list[tuple[str, str]]:
    done = subprocess.run(
        ["git", "log", f"--format={LOG_FORMAT}", f"{base}..HEAD"],
        cwd=root or ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    commits = []
    for chunk in done.stdout.split("\0"):
        chunk = chunk.strip("\n")
        if chunk:
            commit, _, message = chunk.partition("\n")
            commits.append((commit, message))
    return commits


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="dev", help="branch the commits will land on")
    args = parser.parse_args(argv)
    try:
        commits = commit_messages(args.base)
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or error
        print(f"Couldn't read the commits in {args.base}..HEAD: {detail}".rstrip())
        return 2
    found = [(commit, offending_lines(message)) for commit, message in commits]
    found = [(commit, lines) for commit, lines in found if lines]
    for commit, lines in found:
        for line in lines:
            print(f"{commit[:8]}: {line}")
    if found:
        print(
            f"{len(found)} of {len(commits)} commits credit an AI tool. Remove those lines "
            "(git commit --amend, or reword older commits) before landing."
        )
        return 1
    print(f"{len(commits)} commits checked; none credits an AI tool.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
