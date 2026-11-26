"""Test files import the shared organization and Second Brain helpers instead of copying them."""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
SHARED = [TESTS / "helpers" / "organizations.py", TESTS / "helpers" / "brain_library.py"]


def shared_names() -> set[str]:
    names: set[str] = set()
    for path in SHARED:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names |= {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    return names


def test_test_files_use_the_shared_helpers_or_say_why() -> None:
    names = shared_names()
    assert {"act", "as_worker", "company", "wait_idle", "wait_task"} <= names
    uncommented = []
    for path in sorted((TESTS / "unit").glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.split("\n")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef) or node.name not in names:
                continue
            first = (node.decorator_list[0] if node.decorator_list else node).lineno
            # A local helper with a shared name must say, in the line above it, why it differs.
            if first < 2 or not lines[first - 2].lstrip().startswith("#"):
                uncommented.append(f"{path.name}:{node.lineno} {node.name}")
    assert uncommented == []
