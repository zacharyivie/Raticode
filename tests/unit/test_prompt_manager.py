from __future__ import annotations

from pathlib import Path

from gofer.prompts.manager import PromptManager


def test_interpolation_simple(tmp_path: Path) -> None:
    p = tmp_path / "prompt.md"
    p.write_text("Hello {{name}}!")
    mgr = PromptManager(search_dirs=[tmp_path])
    result = mgr.load(p, {"name": "World"})
    assert result == "Hello World!"


def test_interpolation_nested(tmp_path: Path) -> None:
    p = tmp_path / "p.md"
    p.write_text("Count: {{prev.output.count}}")
    mgr = PromptManager()
    result = mgr.load(p, {"prev": {"output": {"count": 42}}})
    assert result == "Count: 42"


def test_missing_key_left_unchanged(tmp_path: Path) -> None:
    p = tmp_path / "p.md"
    p.write_text("{{missing}}")
    mgr = PromptManager()
    result = mgr.load(p, {})
    assert result == "{{missing}}"


def test_no_placeholders(tmp_path: Path) -> None:
    p = tmp_path / "p.md"
    p.write_text("Plain text.")
    mgr = PromptManager()
    assert mgr.load(p, {}) == "Plain text."


def test_search_dir_resolution(tmp_path: Path) -> None:
    (tmp_path / "greet.md").write_text("Hi {{user}}!")
    mgr = PromptManager(search_dirs=[tmp_path])
    from pathlib import Path

    result = mgr.load(Path("greet.md"), {"user": "Alice"})
    assert result == "Hi Alice!"
