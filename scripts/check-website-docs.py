#!/usr/bin/env python3
"""Check that a feature branch updates the website docs before it lands.

The website (``website/``) is the single source of truth for Raticode documentation.
A branch that adds a feature (a ``feat:`` commit) and changes app code under
``frontend/``, ``src/gofer/`` or ``mobile/android/`` must also change ``website/``.

To skip the check for one branch, add a ``Docs-not-needed: <reason>`` line to any
commit message in the range, or to the pull request title or description
(``--pr-text-file``). Other commit types that change app code only get a reminder.

Usage:
    python3 scripts/check-website-docs.py               # compares dev...HEAD
    python3 scripts/check-website-docs.py --base origin/main --pr-text-file pr.txt

Exit codes: 0 when the check passes or is skipped, 1 when website docs are missing,
2 when git fails.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

APP_PREFIXES = (
    "frontend/src/",
    "frontend/electron/",
    "src/gofer/",
    "mobile/android/app/src/main/",
)
# Test-only and generated files never need docs.
TEST_PATTERNS = (
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)__tests__/"),
    re.compile(r"(^|/)src/(test|androidTest)/"),
    re.compile(r"\.test\.[cm]?[jt]sx?$"),
    re.compile(r"\.browser\.cjs$"),
    re.compile(r"(^|/)test_[^/]*\.py$"),
    re.compile(r"_test\.py$"),
)
DOCS_PREFIX = "website/"
MARKER = re.compile(r"^\s*docs-not-needed\s*:\s*\S", re.IGNORECASE | re.MULTILINE)
FEATURE_SUBJECT = re.compile(r"^\s*feat(\([^)]*\))?!?\s*:", re.IGNORECASE)


def is_app_change(path: str) -> bool:
    if not path.startswith(APP_PREFIXES):
        return False
    return not any(pattern.search(path) for pattern in TEST_PATTERNS)


def _git(args: Sequence[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def changed_files(base: str, head: str, cwd: Path) -> list[str]:
    out = _git(["diff", "--name-only", f"{base}...{head}"], cwd)
    return [line for line in out.splitlines() if line]


def commit_messages(base: str, head: str, cwd: Path) -> list[str]:
    out = _git(["log", "--format=%B%x00", f"{base}..{head}"], cwd)
    return [message.strip() for message in out.split("\x00") if message.strip()]


def evaluate(files: Iterable[str], messages: Sequence[str], pr_text: str = "") -> tuple[bool, str]:
    """Return (passed, explanation) for one branch."""
    files = list(files)
    app = [path for path in files if is_app_change(path)]
    docs = [path for path in files if path.startswith(DOCS_PREFIX)]
    texts = [*messages, pr_text]
    if not app:
        return True, "No app code changed; website docs are not required."
    if docs:
        return True, f"App code and website docs both changed ({len(docs)} website file(s))."
    if any(MARKER.search(text) for text in texts if text):
        return True, "Website docs skipped by a Docs-not-needed line."
    subjects = [text.splitlines()[0] for text in texts if text.strip()]
    if any(FEATURE_SUBJECT.match(subject) for subject in subjects):
        sample = "\n".join(f"  {path}" for path in app[:10])
        more = f"\n  … and {len(app) - 10} more" if len(app) > 10 else ""
        return False, (
            "This branch adds a feature and changes app code, but nothing under website/.\n"
            "The website is the single source of truth for Raticode documentation.\n"
            "Update the matching pages in website/docs/ before merging, or add a line\n"
            "'Docs-not-needed: <reason>' to a commit message or the pull request.\n"
            f"App files changed:\n{sample}{more}"
        )
    return True, (
        "Reminder: app code changed without website docs. If this changes how a\n"
        "feature works in a way users would notice, update website/docs/ too."
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="dev", help="branch the change merges into")
    parser.add_argument("--head", default="HEAD", help="tip of the change")
    parser.add_argument(
        "--pr-text-file", type=Path, help="file holding the pull request title and body"
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository path")
    args = parser.parse_args(argv)

    pr_text = ""
    if args.pr_text_file and args.pr_text_file.exists():
        pr_text = args.pr_text_file.read_text(encoding="utf-8", errors="replace")
    try:
        files = changed_files(args.base, args.head, args.repo)
        messages = commit_messages(args.base, args.head, args.repo)
    except RuntimeError as error:
        print(f"check-website-docs: {error}", file=sys.stderr)
        return 2
    passed, explanation = evaluate(files, messages, pr_text)
    print(explanation, file=sys.stdout if passed else sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
