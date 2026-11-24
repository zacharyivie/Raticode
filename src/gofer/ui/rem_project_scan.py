"""Find a project's answers before anyone asks, from the files and history it already has.

A scan reads the project's CI files, agent and contributor docs, check scripts, package
and build files and git history, and ranks candidates for three fixed topics:

- ``checks.fast``: the command that shows work in the project is sound before it lands;
- ``branch.integration``: the branch finished work lands on;
- ``decisions.folder``: a folder of decision records the project already keeps.

There is no model call and nothing runs: every candidate comes with the exact line (or git
fact) it was found in, and only what the project itself names can be a candidate. Raticode
ships no default command, branch or folder, so a project that names none gets no request.

``requests`` turns the top candidates into decision requests (``rem_decisions``), the one
store every answer goes through: with self-work's auto mode on Rem records them as
provisional and goes on; otherwise they wait for the user to confirm.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gofer.ui.rem_decisions import Alternative, DecisionRequest, Evidence, Recommendation
from gofer.utils.process import (
    NO_REPOSITORY_PROGRAMS,
    build_subprocess_env,
    untrusted_repository_git_env,
)

CHECK_TOPIC = "checks.fast"
BRANCH_TOPIC = "branch.integration"
FOLDER_TOPIC = "decisions.folder"
TOPICS = (CHECK_TOPIC, BRANCH_TOPIC, FOLDER_TOPIC)

QUESTIONS = {
    CHECK_TOPIC: "Which command shows that work in {name} is sound before it lands?",
    BRANCH_TOPIC: "Which branch does finished work in {name} land on?",
    FOLDER_TOPIC: "Where does {name} keep its decision records?",
}

FILE_BYTES = 256 * 1024
GIT_TIMEOUT_SECONDS = 15
REFLOG_LINES = 400
MAX_BRANCHES = 400
MAX_CANDIDATES = 4
# Below this a candidate rests on too little to bring to anyone.
MIN_SCORE = {CHECK_TOPIC: 20, BRANCH_TOPIC: 25, FOLDER_TOPIC: 1}

# Where projects write instructions for people and agents. Read when present.
AGENT_DOCS = (
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "CONTRIBUTING.md",
    ".github/CONTRIBUTING.md",
    "docs/CONTRIBUTING.md",
    ".github/copilot-instructions.md",
    "README.md",
)
CI_FILES = (
    ".gitlab-ci.yml",
    ".circleci/config.yml",
    "azure-pipelines.yml",
    "bitbucket-pipelines.yml",
    ".buildkite/pipeline.yml",
    ".woodpecker.yml",
)
CI_FOLDERS = (".github/workflows", ".woodpecker", ".forgejo/workflows", ".gitea/workflows")
SCRIPT_FOLDERS = ("scripts", "script", "bin", "tools")

# Weights. A doc that says to run a command before work lands outranks everything; CI
# commands next; then what docs and manifests merely name.
GATE = 100
CI_COMMAND = 40
CI_BLOCK_LINE = 25
DOC_RUN = 30
DOC_MENTION = 10
DOC_REPEAT = 10
SCRIPT = 20
MANIFEST = 25
MANIFEST_STRONG = 30
TOOL_FILE = 20
BRANCH_DOC = 40
BRANCH_HINT = 40
BRANCH_LANDINGS = 60
BRANCH_REMOTE_HEAD = 25
BRANCH_CI = 10

# A line that says something must happen before work lands, or must pass.
_GATE = re.compile(
    r"\bbefore\b[^.\n]{0,80}?\b(?:land\w*|merg\w*|commit\w*|push\w*|fast-forward\w*|"
    r"submit\w*|integrat\w*|open\w*\s+(?:a\s+)?(?:pull|merge)\s+request|PRs?|pull request)"
    r"|\bmust\s+(?:all\s+)?pass\b|\bshould\s+(?:all\s+)?pass\b",
    re.IGNORECASE,
)
_RUN = re.compile(r"\brun\b", re.IGNORECASE)
_CHECK_WORD = re.compile(r"test|check|lint|verif|valid", re.IGNORECASE)
# Commands that change, install or publish rather than check. A token with "check" in it
# ("format --check") keeps the command.
_NOT_A_CHECK = frozenset(
    {
        "install",
        "add",
        "fix",
        "--fix",
        "format",
        "fmt",
        "--write",
        "deploy",
        "publish",
        "release",
        "push",
        "upload",
        "login",
        "serve",
        "start",
        "watch",
        "clean",
        "bump",
        "sync",
        "update",
        "upgrade",
        "init",
        "echo",
        "cd",
        "export",
    }
)
_INLINE = re.compile(r"`([^`\n]{2,200})`")
_FENCE = re.compile(r"^\s*(```|~~~)\s*([\w+-]*)")
_SHELL_FENCES = frozenset({"", "sh", "bash", "shell", "console", "zsh", "fish", "powershell"})
_LANDING_WORDS = re.compile(
    r"\b(?:fast-forward\w*|merg\w*|land\w*|rebas\w*|integrat\w*|target\w*|"
    r"base branch|pull requests?\s+(?:to|into|against)|onto|into)\b",
    re.IGNORECASE,
)
_RECORD_NAME = re.compile(r"^\d{3,4}[-_].+\.md$", re.IGNORECASE)
_STATUS_HEADING = re.compile(r"^#{1,3}\s*status\b", re.IGNORECASE | re.MULTILINE)
_STATUS_FIELD = re.compile(r"\A---\s*\n(?:.*\n){0,30}?status\s*:", re.IGNORECASE)
_SCRIPT_NAME = re.compile(r"^(?:check|verify|lint|test|ci)(?:[-_.].*)?$", re.IGNORECASE)
_TARGET_NAME = re.compile(
    r"^(?:test|tests|check|checks|lint|verify|validate|typecheck|type-check|ci)$"
)
_STRONG_TARGET = frozenset({"check", "checks", "ci", "verify"})
_LANDED = re.compile(
    r"^(?:merge\s+(?P<merged>\S+?):\s+(?:fast-forward|merge made)"
    r"|fetch\s+\.\s+(?P<fetched>[^:\s]+):\S+:\s+fast-forward)",
    re.IGNORECASE,
)


@dataclass
class Source:
    """Where a candidate was found: the evidence a decision request carries."""

    kind: str  # quote | file | commit
    ref: str
    excerpt: str
    weight: int
    label: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ref": self.ref,
            "excerpt": self.excerpt,
            "weight": self.weight,
            "label": self.label,
        }


@dataclass
class Candidate:
    answer: str
    sources: list[Source] = field(default_factory=list)

    @property
    def score(self) -> int:
        return sum(s.weight for s in self.sources)

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "score": self.score,
            "sources": [s.as_dict() for s in self.ranked_sources()],
        }

    def ranked_sources(self) -> list[Source]:
        return sorted(self.sources, key=lambda s: -s.weight)


class _Pool:
    """Candidates for one topic, keyed by their answer with spaces collapsed."""

    def __init__(self) -> None:
        self.items: dict[str, Candidate] = {}

    def add(self, answer: str, source: Source) -> None:
        key = " ".join(answer.split())
        if not key:
            return
        candidate = self.items.setdefault(key, Candidate(key))
        same_file = [
            s for s in candidate.sources if s.ref.split(":", 1)[0] == source.ref.split(":", 1)[0]
        ]
        if same_file and source.kind == "quote":
            # One file saying it again is support, not a second source.
            source.weight = min(source.weight, DOC_REPEAT)
        candidate.sources.append(source)

    def ranked(self, minimum: int) -> list[Candidate]:
        found = [c for c in self.items.values() if c.score >= minimum]
        return sorted(found, key=lambda c: (-c.score, c.answer))[:MAX_CANDIDATES]


# Reading ----------------------------------------------------------------------------


def _read(root: Path, relative: str) -> str | None:
    path = root / relative
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
        if not resolved.is_file() or resolved.stat().st_size > FILE_BYTES:
            return None
        return resolved.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError, RuntimeError):
        return None


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *NO_REPOSITORY_PROGRAMS, "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env=untrusted_repository_git_env(root, build_subprocess_env()),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _line(text: str) -> str:
    return text.strip()[:500]


# Check commands ---------------------------------------------------------------------


def _is_check(command: str) -> bool:
    tokens = command.split()
    if not tokens or ("<" in command and ">" in command) or "..." in command:
        return False
    if any(t.casefold() in _NOT_A_CHECK for t in tokens) and not any(
        "check" in t.casefold() for t in tokens
    ):
        return False
    return bool(_CHECK_WORD.search(command))


def _looks_like_command(span: str, before: str) -> bool:
    span = span.strip()
    if not span or span.startswith(("-", "=", "#")) or span.endswith(":"):
        return False
    if " " in span or "/" in span:
        return True
    # A single word counts only when the text says to run it ("run `tox`").
    return bool(re.search(r"\brun\s*$", before, re.IGNORECASE))


def _doc_commands(relative: str, text: str, pool: _Pool) -> None:
    fence: str | None = None
    fence_lang = ""
    for number, raw in enumerate(text.splitlines(), 1):
        opened = _FENCE.match(raw)
        if opened:
            if fence is None:
                fence, fence_lang = opened.group(1), opened.group(2).casefold()
            elif raw.strip().startswith(fence):
                fence = None
            continue
        ref = f"{relative}:{number}"
        if fence is not None:
            if fence_lang not in _SHELL_FENCES:
                continue
            command = raw.strip()
            command = re.sub(r"^[$>]\s+", "", command)
            if not command or command.startswith("#") or not _is_check(command):
                continue
            pool.add(
                command,
                Source("quote", ref, _line(raw), DOC_MENTION, f"{relative} lists it"),
            )
            continue
        for match in _INLINE.finditer(raw):
            span = match.group(1).strip()
            before = raw[: match.start()]
            if not _looks_like_command(span, before) or not _is_check(span):
                continue
            if _GATE.search(raw):
                weight, label = GATE, f"{relative} says to run it before work lands"
            elif _RUN.search(raw):
                weight, label = DOC_RUN, f"{relative} says to run it"
            else:
                weight, label = DOC_MENTION, f"{relative} names it"
            pool.add(span, Source("quote", ref, _line(raw), weight, label))


def _ci_files(root: Path) -> list[str]:
    found = [f for f in CI_FILES if (root / f).is_file()]
    for folder in CI_FOLDERS:
        base = root / folder
        if base.is_dir():
            try:
                found += sorted(
                    f"{folder}/{p.name}"
                    for p in base.iterdir()
                    if p.is_file() and p.suffix in {".yml", ".yaml"}
                )
            except OSError:
                continue
    return found[:60]


def _ci_commands(relative: str, text: str, pool: _Pool, branches: _Pool, local: set[str]) -> None:
    lines = text.splitlines()
    block_indent: int | None = None
    for number, raw in enumerate(lines, 1):
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if block_indent is not None:
            if stripped and indent <= block_indent:
                block_indent = None
            else:
                command = stripped
                if command and not command.startswith("#") and _usable_ci(command):
                    pool.add(
                        command,
                        Source(
                            "quote",
                            f"{relative}:{number}",
                            _line(raw),
                            CI_BLOCK_LINE,
                            f"CI runs it ({relative})",
                        ),
                    )
                continue
        match = re.match(r"^\s*-?\s*(?:run|script|command)\s*:\s*(.*)$", raw)
        if match:
            value = match.group(1).strip()
            if value in {"|", ">", "|-", ">-", "|+", ">+"}:
                block_indent = indent
                continue
            value = value.strip("'\"")
            if value and _usable_ci(value):
                pool.add(
                    value,
                    Source(
                        "quote",
                        f"{relative}:{number}",
                        _line(raw),
                        CI_COMMAND,
                        f"CI runs it ({relative})",
                    ),
                )
            continue
        listed = re.match(r"^\s*branches\s*:\s*\[([^\]]*)\]", raw)
        names: list[str] = []
        if listed:
            names = [n.strip(" '\"") for n in listed.group(1).split(",")]
        else:
            item = re.match(r"^\s*-\s*['\"]?([\w./-]+)['\"]?\s*$", raw)
            previous = lines[number - 2].strip() if number >= 2 else ""
            if item and previous.startswith(("branches:", "- ")) and _under_branches(lines, number):
                names = [item.group(1)]
        for name in names:
            if name in local:
                branches.add(
                    name,
                    Source(
                        "quote",
                        f"{relative}:{number}",
                        _line(raw),
                        BRANCH_CI,
                        f"CI runs on {name} ({relative})",
                    ),
                )


def _under_branches(lines: list[str], number: int) -> bool:
    """Whether line ``number`` (1-based) is an item of a ``branches:`` list."""
    for back in range(number - 2, max(-1, number - 12), -1):
        text = lines[back].strip()
        if text.startswith("branches:"):
            return True
        if not text.startswith("- "):
            return False
    return False


def _usable_ci(command: str) -> bool:
    if any(s in command for s in ("${{", "$GITHUB_", "$RUNNER_", ">>", "$CI_")):
        return False
    return _is_check(command)


def _interpreter(text: str) -> str | None:
    first = text.split("\n", 1)[0]
    if not first.startswith("#!"):
        return None
    parts = first[2:].strip().split()
    if not parts:
        return None
    if Path(parts[0]).name == "env":
        rest = [p for p in parts[1:] if not p.startswith("-")]
        return rest[0] if rest else None
    return Path(parts[0]).name


def _scripts(root: Path, pool: _Pool) -> None:
    for folder in SCRIPT_FOLDERS:
        base = root / folder
        if not base.is_dir():
            continue
        try:
            entries = sorted(base.iterdir())
        except OSError:
            continue
        for path in entries[:300]:
            if not path.is_file() or not _SCRIPT_NAME.match(path.name):
                continue
            relative = f"{folder}/{path.name}"
            text = _read(root, relative)
            if text is None:
                continue
            interpreter = _interpreter(text)
            if interpreter:
                command = f"{interpreter} {relative}"
            elif os.access(path, os.X_OK):
                command = f"./{relative}"
            else:
                continue
            pool.add(
                command,
                Source(
                    "quote",
                    f"{relative}:1",
                    _line(text.split("\n", 1)[0]),
                    SCRIPT,
                    f"{relative} is a check script",
                ),
            )


def _quote_line(text: str, pattern: str) -> tuple[int, str] | None:
    compiled = re.compile(pattern, re.MULTILINE)
    match = compiled.search(text)
    if not match:
        return None
    number = text.count("\n", 0, match.start()) + 1
    return number, text.splitlines()[number - 1]


def _package_json(root: Path, pool: _Pool) -> None:
    text = _read(root, "package.json")
    if text is None:
        return
    try:
        scripts = (json.loads(text) or {}).get("scripts") or {}
    except (ValueError, AttributeError):
        return
    if not isinstance(scripts, dict):
        return
    runner = next(
        (
            name
            for lock, name in (
                ("pnpm-lock.yaml", "pnpm"),
                ("yarn.lock", "yarn"),
                ("bun.lockb", "bun"),
                ("bun.lock", "bun"),
            )
            if (root / lock).is_file()
        ),
        "npm",
    )
    for name in scripts:
        if not isinstance(name, str) or not _TARGET_NAME.match(name):
            continue
        found = _quote_line(text, rf'^\s*"{re.escape(name)}"\s*:')
        if found is None:
            continue
        number, raw = found
        command = f"{runner} test" if name == "test" and runner == "npm" else f"{runner} run {name}"
        pool.add(
            command,
            Source(
                "quote",
                f"package.json:{number}",
                _line(raw),
                MANIFEST_STRONG if name in _STRONG_TARGET else MANIFEST,
                f"package.json has a {name} script",
            ),
        )


def _targets(root: Path, pool: _Pool) -> None:
    for relative, tool, pattern in (
        ("Makefile", "make", r"^([A-Za-z0-9_.-]+)\s*:(?!=)"),
        ("makefile", "make", r"^([A-Za-z0-9_.-]+)\s*:(?!=)"),
        ("GNUmakefile", "make", r"^([A-Za-z0-9_.-]+)\s*:(?!=)"),
        ("justfile", "just", r"^@?([A-Za-z0-9_-]+)(?:\s[^:\n]*)?:(?!=)"),
        ("Justfile", "just", r"^@?([A-Za-z0-9_-]+)(?:\s[^:\n]*)?:(?!=)"),
    ):
        text = _read(root, relative)
        if text is None:
            continue
        seen: set[str] = set()
        for number, raw in enumerate(text.splitlines(), 1):
            match = re.match(pattern, raw)
            if not match or match.group(1) in seen or not _TARGET_NAME.match(match.group(1)):
                continue
            name = match.group(1)
            seen.add(name)
            pool.add(
                f"{tool} {name}",
                Source(
                    "quote",
                    f"{relative}:{number}",
                    _line(raw),
                    MANIFEST_STRONG if name in _STRONG_TARGET else MANIFEST,
                    f"{relative} has a {name} target",
                ),
            )


def _tool_files(root: Path, pool: _Pool) -> None:
    """Build files whose own test command is part of the tool the project uses."""
    pyproject = _read(root, "pyproject.toml")
    if pyproject is not None:
        for section, tool in (
            ("tool.poe.tasks", "poe"),
            ("tool.pdm.scripts", "pdm run"),
        ):
            header = _quote_line(pyproject, rf"^\[{re.escape(section)}\]")
            if header is None:
                continue
            body = pyproject.split("\n")[header[0] :]
            for offset, raw in enumerate(body):
                if raw.startswith("["):
                    break
                match = re.match(r"^([A-Za-z0-9_-]+)\s*=", raw)
                if match and _TARGET_NAME.match(match.group(1)):
                    pool.add(
                        f"{tool} {match.group(1)}",
                        Source(
                            "quote",
                            f"pyproject.toml:{header[0] + offset + 1}",
                            _line(raw),
                            MANIFEST,
                            f"pyproject.toml has a {match.group(1)} task",
                        ),
                    )
        found = _quote_line(pyproject, r"^\[tool\.pytest(?:\.ini_options)?\]")
        if found:
            pool.add(
                "pytest",
                Source(
                    "quote",
                    f"pyproject.toml:{found[0]}",
                    _line(found[1]),
                    TOOL_FILE,
                    "pyproject.toml configures pytest",
                ),
            )
    for relative, command, pattern in (
        ("tox.ini", "tox", r"^\[tox\]"),
        ("noxfile.py", "nox", r"^\s*import nox|^\s*from nox"),
        ("Cargo.toml", "cargo test", r"^\[(?:package|workspace)\]"),
        ("go.mod", "go test ./...", r"^module\s"),
    ):
        text = _read(root, relative)
        if text is None:
            continue
        found = _quote_line(text, pattern)
        if found:
            pool.add(
                command,
                Source(
                    "quote",
                    f"{relative}:{found[0]}",
                    _line(found[1]),
                    TOOL_FILE,
                    f"the project uses {relative}",
                ),
            )
    if (root / "gradlew").is_file():
        for relative in ("build.gradle.kts", "build.gradle"):
            text = _read(root, relative)
            if text is not None and text.strip():
                pool.add(
                    "./gradlew check",
                    Source(
                        "quote",
                        f"{relative}:1",
                        _line(text.splitlines()[0]),
                        TOOL_FILE,
                        f"the project builds with Gradle ({relative})",
                    ),
                )
                break


# Landing branch ---------------------------------------------------------------------


def _local_branches(root: Path) -> dict[str, str]:
    out = _git(root, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads")
    if out is None:
        return {}
    pairs = [line.split(" ", 1) for line in out.splitlines() if " " in line]
    return dict(pairs[:MAX_BRANCHES])


def _mentions(text: str, name: str) -> bool:
    return bool(re.search(rf"(?<![\w./-])`?{re.escape(name)}`?(?![\w/-])", text))


def _branch_lines(
    relative: str, text: str, local: dict[str, str], pool: _Pool, *, weight: int, label: str
) -> None:
    for number, raw in enumerate(text.splitlines(), 1):
        words = _LANDING_WORDS.search(raw)
        if not words:
            continue
        tail = raw[words.start() : words.end() + 80]
        for name in local:
            if _mentions(tail, name):
                pool.add(
                    name,
                    Source("quote", f"{relative}:{number}", _line(raw), weight, label),
                )


def _hint_branches(hints: Sequence[str], local: dict[str, str], pool: _Pool) -> None:
    for hint in hints:
        for raw in hint.splitlines():
            words = _LANDING_WORDS.search(raw)
            if not words:
                continue
            tail = raw[words.start() : words.end() + 80]
            for name, tip in local.items():
                if _mentions(tail, name):
                    pool.add(
                        name,
                        Source(
                            "commit",
                            tip[:12],
                            _line(raw)[:300],
                            BRANCH_HINT,
                            "the project's enabled worktree skill lands work there",
                        ),
                    )


def _landings(root: Path, local: dict[str, str], pool: _Pool) -> None:
    """Count the merges and fast-forwards of other branches each local branch received."""
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return
    logs = Path(common.strip()) / "logs" / "refs" / "heads"
    remotes = set((_git(root, "remote") or "").split()) | {"refs"}
    for name, tip in local.items():
        path = logs / name
        try:
            if not path.is_file() or path.stat().st_size > 8 * FILE_BYTES:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        merged: list[str] = []
        for entry in lines[-REFLOG_LINES:]:
            message = entry.split("\t", 1)[1] if "\t" in entry else ""
            match = _LANDED.match(message)
            if not match:
                continue
            other = match.group("merged") or match.group("fetched") or ""
            # A pull from a remote is the same branch catching up, not work landing.
            if other and other != name and other not in remotes and "/" not in other[:1]:
                if other.split("/", 1)[0] in remotes:
                    continue
                merged.append(other)
        if len(merged) < 2:
            continue
        pool.add(
            name,
            Source(
                "commit",
                tip[:12],
                f"{len(merged)} other branches were merged or fast-forwarded into {name} "
                f"here, most recently {merged[-1]}",
                min(BRANCH_LANDINGS, 6 * len(merged)),
                f"{len(merged)} branches landed on {name} in this repository",
            ),
        )


def _remote_heads(root: Path, local: dict[str, str], pool: _Pool) -> None:
    out = _git(root, "for-each-ref", "--format=%(refname:short) %(symref:short)", "refs/remotes")
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[0].endswith("/HEAD"):
            continue
        remote, target = parts[0].rsplit("/", 1)[0], parts[1]
        name = target.split("/", 1)[1] if "/" in target else target
        if name in local:
            pool.add(
                name,
                Source(
                    "commit",
                    local[name][:12],
                    f"{remote}'s default branch is {name}",
                    BRANCH_REMOTE_HEAD,
                    f"{remote}'s default branch",
                ),
            )


# Decision records -------------------------------------------------------------------


def _tracked_markdown(root: Path) -> list[str] | None:
    out = _git(root, "ls-files", "-z", "--", "*.md", "*.MD")
    if out is None:
        return None
    return [p for p in out.split("\0") if p][:20000]


def _walk_markdown(root: Path) -> list[str]:
    found: list[str] = []
    skip = {"node_modules", "vendor", "dist", "build", "target", "__pycache__"}
    for current, folders, files in os.walk(root):
        depth = len(Path(current).relative_to(root).parts)
        folders[:] = [
            f
            for f in folders
            if depth < 4 and f not in skip and (not f.startswith(".") or f == ".raticode")
        ]
        found += [
            str(Path(current, f).relative_to(root)) for f in files if f.lower().endswith(".md")
        ]
        if len(found) > 20000:
            break
    return found


def _record_folders(root: Path, pool: _Pool) -> None:
    files = _tracked_markdown(root)
    if files is None:
        files = _walk_markdown(root)
    groups: dict[str, list[str]] = {}
    for relative in files:
        path = Path(relative)
        if _RECORD_NAME.match(path.name):
            groups.setdefault(str(path.parent), []).append(relative)
    for folder, records in groups.items():
        records.sort()
        text = _read(root, records[0]) or ""
        if _STATUS_FIELD.search(text):
            style = "records with a status field in their front matter"
        elif _STATUS_HEADING.search(text):
            style = "records with a Status section"
        elif len(records) >= 3:
            style = "numbered records"
        else:
            continue
        pool.add(
            folder.replace(os.sep, "/"),
            Source(
                "file",
                records[0],
                f"{len(records)} numbered {style} in {folder}",
                10 * min(len(records), 10),
                f"{folder} holds {len(records)} {style}",
            ),
        )


# Scan ---------------------------------------------------------------------------------


def scan(project: str | Path, *, hints: Iterable[str] = ()) -> dict[str, Any]:
    """Rank candidates for each topic from the project's own files and git history.

    ``hints`` are texts the user enabled for this project, such as a worktree skill's
    description; a branch they say work lands on counts as support. Returns
    ``{"project", "name", "read": [files], "topics": {topic: [candidate, ...]}}`` with the
    best candidate first. Nothing is run and no model is asked.
    """
    root = Path(project).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Project folder not found: {project}")
    checks, branches, folders = _Pool(), _Pool(), _Pool()
    read: list[str] = []
    local = _local_branches(root)
    for relative in AGENT_DOCS:
        text = _read(root, relative)
        if text is None:
            continue
        read.append(relative)
        _doc_commands(relative, text, checks)
        if local:
            _branch_lines(
                relative,
                text,
                local,
                branches,
                weight=BRANCH_DOC,
                label=f"{relative} says work lands there",
            )
    for relative in _ci_files(root):
        text = _read(root, relative)
        if text is None:
            continue
        read.append(relative)
        _ci_commands(relative, text, checks, branches, set(local))
    _scripts(root, checks)
    _package_json(root, checks)
    _targets(root, checks)
    _tool_files(root, checks)
    if local:
        _hint_branches(list(hints), local, branches)
        _landings(root, local, branches)
        _remote_heads(root, local, branches)
    _record_folders(root, folders)
    return {
        "project": str(root),
        "name": root.name,
        "read": read,
        "topics": {
            CHECK_TOPIC: [c.as_dict() for c in checks.ranked(MIN_SCORE[CHECK_TOPIC])],
            BRANCH_TOPIC: [c.as_dict() for c in branches.ranked(MIN_SCORE[BRANCH_TOPIC])],
            FOLDER_TOPIC: [c.as_dict() for c in folders.ranked(MIN_SCORE[FOLDER_TOPIC])],
        },
    }


def _why(labels: list[str]) -> str:
    unique = list(dict.fromkeys(labels))
    text = "; ".join(unique[:3])
    return (text[:1].upper() + text[1:])[:299] + ("." if text and not text.endswith(".") else "")


def requests(result: dict[str, Any], topics: Iterable[str] = TOPICS) -> list[DecisionRequest]:
    """Decision requests for the scanned topics that found something, best answer first.

    Each recommends the top candidate with its source lines as evidence, and lists the
    next ones as alternatives. Every request is reversible and stored as a project fact.
    """
    made: list[DecisionRequest] = []
    for topic in topics:
        candidates = result["topics"].get(topic) or []
        if not candidates:
            continue
        top, others = candidates[0], candidates[1:]
        sources = top["sources"][:5]
        made.append(
            DecisionRequest(
                topic=topic,
                question=QUESTIONS[topic].format(name=result["name"]),
                recommendation=Recommendation(
                    answer=top["answer"][:1000],
                    why=_why([s["label"] for s in sources]),
                    evidence=[
                        Evidence.model_validate(
                            {
                                "kind": s["kind"],
                                "ref": s["ref"][:500],
                                "excerpt": s["excerpt"][:500],
                            }
                        )
                        for s in sources
                    ],
                ),
                alternatives=[
                    Alternative(
                        answer=o["answer"][:1000],
                        why_not=(
                            "Less support: "
                            + "; ".join(dict.fromkeys(s["label"] for s in o["sources"][:2]))
                        )[:300],
                    )
                    for o in others[:3]
                ],
                remember_as="fact",
                reversible=True,
            )
        )
    return made


def fresh_request(
    project: str | Path, topic: str, *, hints: Iterable[str] = ()
) -> DecisionRequest | None:
    """A fresh scan's checked request for one topic, or None when it finds nothing usable.

    Used when the user asks again about ``checks.fast``, ``branch.integration`` or
    ``decisions.folder``: the new card recommends what the project's files say now. Nothing
    is filed here; the caller hands it to ``rem_decisions.change`` as the offer.
    """
    from gofer.ui import rem_decisions as decisions

    if topic not in TOPICS:
        return None
    result = scan(project, hints=hints)
    found = requests(result, topics=(topic,))
    if not found:
        return None
    try:
        checked, _ = decisions.check_evidence(found[0], result["project"])
    except ValueError:
        return None
    return checked if checked.recommendation is not None else None


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def file_results(
    db: Any,
    store: Any,
    result: dict[str, Any],
    *,
    decides_alone: bool,
    only_missing: bool,
    source: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Send a scan's requests through the decision store; one outcome per topic found.

    ``only_missing`` (the scan Raticode starts on its own) skips topics that already have
    any entry, so it never brings back a question the user answered, turned down or
    forgot. A scan the user asks for files every topic: a decision in force is kept and,
    when the scan finds the same answer in changed files, only its evidence is refreshed;
    a different answer to a decision whose evidence changed becomes a proposal that waits
    for the user (``rem_decisions.file_request`` never replaces a decision itself). An
    answer the user turned down is never brought back by a scan.
    """
    from gofer.ui import rem_decisions as decisions

    project = result["project"]
    outcomes: list[dict[str, Any]] = []
    for request in requests(result):
        assert request.recommendation is not None
        answer = request.recommendation.answer
        book = decisions.read(db, store)
        entries = decisions.topic_items(book, project, request.topic)
        outcome: dict[str, Any] = {"topic": request.topic, "answer": answer}
        if only_missing and entries:
            outcomes.append({**outcome, "outcome": "skipped"})
            continue
        try:
            checked, _ = decisions.check_evidence(request, project)
        except ValueError:
            outcomes.append({**outcome, "outcome": "unchecked"})
            continue
        assert checked.recommendation is not None
        current = decisions.in_force(book, project, request.topic)
        # The answer in force comes first: a turned-down card that offered the same answer
        # (Keep current answer on an Ask again) mustn't stop its evidence being refreshed.
        if current is not None and _same(current.get("answer") or "", answer):
            if decisions.stale_reason(current) and not int(current.get("failures") or 0):
                decisions.recheck(
                    db,
                    store,
                    current["id"],
                    [e.model_dump() for e in checked.recommendation.evidence],
                )
                outcomes.append({**outcome, "outcome": "rechecked", "id": current["id"]})
            else:
                outcomes.append({**outcome, "outcome": "answered", "id": current["id"]})
            continue
        if any(
            i["status"] == "rejected"
            and _same(i.get("answer") or i.get("recommended") or "", answer)
            for i in entries
        ):
            outcomes.append({**outcome, "outcome": "turned down before"})
            continue
        filed, item = decisions.file_request(
            db,
            store,
            checked,
            project=project,
            decides_alone=decides_alone,
            source={"via": "scan", **(source or {})},
        )
        outcomes.append({**outcome, "outcome": filed, "id": item["id"]})
    return outcomes
