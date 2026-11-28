"""Seeded fuzz test: every Rattish parse, recovery and format call ends in a result or a diagnostic.

The corpus is the repository's tracked ``*.rattish`` files. Each mutant gets one to eight
edits (delete a span, insert an operator or punctuation token, copy a span elsewhere) from
a fixed seed, so a failure names a mutant that reproduces on every machine.
"""

from __future__ import annotations

import random
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from gofer.rattish.diagnostics import RattishError
from gofer.rattish.formatter import format_rattish
from gofer.rattish.parser import parse_rattish, parse_rattish_recovering

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED = 20261126
MUTANT_COUNT = 2400
PER_INPUT_LIMIT_SECONDS = 2.0
TOTAL_LIMIT_SECONDS = 30.0

# Tokens the Rattish lexer and parser give meaning to, plus whitespace that changes layout.
INSERT_TOKENS = (
    "#",
    "# ",
    ":",
    ": ",
    "-",
    "- ",
    "|",
    "{",
    "}",
    "[",
    "]",
    "(",
    ")",
    '"',
    "'",
    "\\",
    ",",
    ".",
    "==",
    "!=",
    ">=",
    "<=",
    "<",
    ">",
    " matches ",
    " when ",
    " otherwise",
    " and ",
    " or ",
    "not ",
    " is null",
    " is not null",
    "exists ",
    "\n",
    "\r\n",
    "\r",
    "  ",
    "\t",
    "node ",
    "to:",
    "with:",
    "from:",
    "needs:",
    "default:",
    "workflow:",
    "rattish: 1",
    "﻿",
    "é",
    "\x00",
    "1e999",
    "-0",
    "5m",
)

# A plain '#' glued to a value used to jump to the line above when formatted.
GLUED_COMMENT_SOURCE = """rattish: 1

workflow:
  start: first

node first:
  type: bash-command
  command: echo first
  needs: second
  to: c#
  json: {"a": 1}#

node second:
  type: bash-command
  command: echo second

node c:
  type: bash-command
  command: echo c
"""

# Comments around a bare '-' list item used to jump onto the '-' line or disappear.
DASH_COMMENT_SOURCE = """rattish: 1

workflow:
  fallback:
    -  # dash note
      provider: claude-code  # provider note
    -
      # standalone note
      provider: c#laude-code
"""

# A tab crossing a block string's trim column used to lose a column and break the output.
TAB_BLOCK_SOURCE = "rattish: 1\n\nworkflow:\n  command: |\n   \t {\n    echo\n"

_NUMBER_WORKFLOW = """rattish: 1

workflow:
  start: first
  VALUE

node first:
  type: bash-command
  command: echo first
  to:
    - first when node.first.output.exit-code > PREDICATE
    - first otherwise
"""

# Inputs the parser once crashed on, or that the spec says must be diagnostics.
REJECTED_SOURCES = {
    "version-only": "rattish: 1\n",
    "version-only-no-newline": "rattish: 1",
    "version-and-comment": "rattish: 1\n# nothing else\n",
    "empty": "",
    "infinite-number": _NUMBER_WORKFLOW.replace("VALUE", "max-cost-usd: 0.1e99950").replace(
        "PREDICATE", "0"
    ),
    "infinite-predicate-number": _NUMBER_WORKFLOW.replace("VALUE", "name: x").replace(
        "PREDICATE", "1e999"
    ),
    "infinite-json-number": _NUMBER_WORKFLOW.replace("VALUE", "tags: [1e999]").replace(
        "PREDICATE", "0"
    ),
    "json-nan": _NUMBER_WORKFLOW.replace("VALUE", 'tags: {"a": NaN}').replace("PREDICATE", "0"),
    "json-infinity": _NUMBER_WORKFLOW.replace("VALUE", "tags: [-Infinity]").replace(
        "PREDICATE", "0"
    ),
    "integer-past-digit-limit": _NUMBER_WORKFLOW.replace(
        "VALUE", "max-cost-usd: 1" + "0" * 5000
    ).replace("PREDICATE", "0"),
    "json-integer-past-digit-limit": _NUMBER_WORKFLOW.replace(
        "VALUE", "tags: [1" + "0" * 5000 + "]"
    ).replace("PREDICATE", "0"),
}


def _corpus_files() -> list[Path]:
    """Tracked *.rattish files, so every checkout fuzzes the same corpus."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.rattish"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\0")
    except (OSError, subprocess.CalledProcessError):
        return sorted((REPO_ROOT / "rattish").rglob("*.rattish"))
    return sorted(REPO_ROOT / name for name in listed if name and (REPO_ROOT / name).is_file())


def _corpus() -> list[tuple[str, str]]:
    files = _corpus_files()
    return [
        (path.relative_to(REPO_ROOT).as_posix(), path.read_text(encoding="utf-8")) for path in files
    ]


def _mutate(source: str, rng: random.Random) -> str:
    text = source
    for _ in range(rng.choice((1, 1, 2, 3, 8))):
        operation = rng.randrange(3)
        position = rng.randint(0, len(text))
        if operation == 0 and text:
            length = rng.choice((1, 1, 2, 3, rng.randint(1, 40)))
            text = text[:position] + text[position + length :]
        elif operation == 1:
            text = text[:position] + rng.choice(INSERT_TOKENS) + text[position:]
        elif text:
            start = rng.randint(0, len(text) - 1)
            span = text[start : start + rng.randint(1, 120)]
            text = text[:position] + span + text[position:]
    return text


def _mutants() -> Iterator[tuple[str, str]]:
    corpus = _corpus()
    assert len(corpus) >= 20, "the fuzz corpus lost the repository's Rattish files"
    yield "glued-comment", GLUED_COMMENT_SOURCE
    yield "dash-comments", DASH_COMMENT_SOURCE
    yield "tab-block", TAB_BLOCK_SOURCE
    yield from REJECTED_SOURCES.items()
    for name, source in corpus:
        yield name, source
    rng = random.Random(SEED)
    for index in range(MUTANT_COUNT):
        name, source = corpus[rng.randrange(len(corpus))]
        yield f"{name}#mutant-{index}", _mutate(source, rng)


class _InputTimedOut(Exception):
    pass


@contextmanager
def _alarm(seconds: float) -> Iterator[None]:
    def expire(_signum: int, _frame: Any) -> None:
        raise _InputTimedOut

    previous = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _can_use_alarm() -> bool:
    return hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread()


def _run_limited(check: Callable[[], None], seconds: float) -> None:
    """Run ``check`` and raise _InputTimedOut if it takes longer than ``seconds``."""
    if _can_use_alarm():
        with _alarm(seconds):
            check()
        return
    errors: list[BaseException] = []

    def target() -> None:
        try:
            check()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the calling thread
            errors.append(exc)

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise _InputTimedOut
    if errors:
        raise errors[0]


def _check_one(source: str) -> list[str]:
    """Run every entry point on one input and return what went wrong."""
    problems: list[str] = []
    parsed = False
    try:
        parse_rattish(source)
        parsed = True
    except RattishError:
        pass
    except Exception as exc:
        problems.append(f"parse_rattish raised {type(exc).__name__}: {exc}")
    try:
        parse_rattish_recovering(source)
    except RattishError:
        pass
    except Exception as exc:
        problems.append(f"parse_rattish_recovering raised {type(exc).__name__}: {exc}")
    try:
        formatted = format_rattish(source)
    except RattishError:
        if parsed:
            problems.append("format_rattish rejected a document parse_rattish accepted")
        return problems
    except Exception as exc:
        problems.append(f"format_rattish raised {type(exc).__name__}: {exc}")
        return problems
    try:
        again = format_rattish(formatted)
    except Exception as exc:
        problems.append(f"format_rattish rejected its own output: {type(exc).__name__}: {exc}")
        return problems
    if again != formatted:
        problems.append("format_rattish is not idempotent")
    return problems


def test_seeded_rattish_mutants_end_in_a_result_or_a_diagnostic() -> None:
    started = time.perf_counter()
    failures: list[str] = []
    count = 0
    for name, source in _mutants():
        count += 1
        problems: list[str] = []
        input_started = time.perf_counter()
        try:
            _run_limited(lambda: problems.extend(_check_one(source)), PER_INPUT_LIMIT_SECONDS)
        except _InputTimedOut:
            problems.append(f"took over {PER_INPUT_LIMIT_SECONDS:g} s")
        elapsed = time.perf_counter() - input_started
        if elapsed > PER_INPUT_LIMIT_SECONDS and not any("took over" in p for p in problems):
            problems.append(f"took {elapsed:.2f} s")
        failures.extend(f"{name}: {problem}\n{source!r}" for problem in problems)
        if len(failures) >= 10:
            break
    total = time.perf_counter() - started

    assert not failures, "\n\n".join(failures)
    assert count >= MUTANT_COUNT
    assert total < TOTAL_LIMIT_SECONDS, f"fuzz run took {total:.1f} s"


def test_glued_empty_comment_stays_on_its_line_and_formats_stably() -> None:
    formatted = format_rattish(GLUED_COMMENT_SOURCE)

    assert "  needs: second\n  to: c  #\n" in formatted
    assert '  json: {"a": 1}  #\n' in formatted
    assert format_rattish(formatted) == formatted


def test_comments_around_bare_list_items_stay_on_their_lines() -> None:
    formatted = format_rattish(DASH_COMMENT_SOURCE)

    assert formatted == (
        "rattish: 1\n\nworkflow:\n  fallback:\n"
        "    -  # dash note\n      provider: claude-code  # provider note\n"
        "    -\n      # standalone note\n      provider: c  #laude-code\n"
    )
    assert format_rattish(formatted) == formatted


def test_block_string_tab_across_the_trim_column_keeps_its_width() -> None:
    formatted = format_rattish(TAB_BLOCK_SOURCE)

    assert parse_rattish(TAB_BLOCK_SOURCE)["workflow"]["entries"][0]["value"]["value"] == (
        "  {\necho\n"
    )
    assert formatted == "rattish: 1\n\nworkflow:\n  command: |\n      {\n    echo\n"
    assert format_rattish(formatted) == formatted


@pytest.mark.parametrize("name", sorted(REJECTED_SOURCES))
def test_incomplete_documents_and_out_of_range_numbers_are_diagnostics(name: str) -> None:
    source = REJECTED_SOURCES[name]

    with pytest.raises(RattishError):
        parse_rattish(source)
    assert parse_rattish_recovering(source).diagnostics
