"""Opened brains are private to this account, and Library health says when they are not."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from gofer.ui import brain_library
from gofer.ui.brain_library import BrainLibrary, open_to_others

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")


@pytest.fixture
def library(tmp_path: Path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def brain_folder(tmp_path: Path, name: str = "brain") -> Path:
    root = tmp_path / name
    (root / ".git").mkdir(parents=True)
    (root / "notes.md").write_text("# Notes\n", encoding="utf-8")
    root.chmod(0o755)
    (root / ".git").chmod(0o755)
    return root


def access_issues(library: BrainLibrary) -> list[dict[str, Any]]:
    return [item for item in library.snapshot()["health"] if item["kind"] == "permissions"]


def test_opening_a_brain_makes_its_folder_owner_only(library, tmp_path):
    root = brain_folder(tmp_path)
    brain = library.open({"root": str(root)})
    assert mode(root) == 0o700
    # The folder and its exposed Git folder change; files keep their modes.
    assert mode(root / ".git") == 0o700
    assert mode(root / "notes.md") == 0o644
    assert access_issues(library) == []

    # Reopening an already registered brain applies the rule again.
    root.chmod(0o755)
    assert library.open({"root": str(root)})["id"] == brain["id"]
    assert mode(root) == 0o700


def test_library_health_warns_when_folder_and_git_are_readable(library, tmp_path):
    root = brain_folder(tmp_path)
    brain = library.open({"root": str(root)})
    root.chmod(0o755)
    (root / ".git").chmod(0o755)

    issues = access_issues(library)
    assert {item["path"] for item in issues} == {str(root), str(root / ".git")}
    for item in issues:
        assert item["brainId"] == brain["id"]
        assert item["fix"] == "Make private"
        assert "Other accounts on this computer can read" in item["detail"]
        assert "755" in item["detail"]
        assert "docId" not in item

    # The fix applies the owner-only rule; a private folder also hides its nested .git.
    assert library.request("restrict_access", {"brainId": brain["id"]}) == {"remaining": []}
    assert mode(root) == 0o700
    assert access_issues(library) == []


def test_separate_git_folder_is_checked_and_restricted(library, tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    shared = tmp_path / "elsewhere" / "brain.git"
    (shared / "objects").mkdir(parents=True)
    (shared / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    shared.chmod(0o755)
    (root / ".git").write_text(f"gitdir: {shared}\n", encoding="utf-8")
    brain = library.open({"root": str(root)})
    assert mode(root) == 0o700
    # Opening made both private, because the Git folder sits outside the brain folder.
    assert mode(shared) == 0o700

    shared.chmod(0o750)
    issues = access_issues(library)
    assert [item["path"] for item in issues] == [str(shared)]
    assert "Git history" in issues[0]["detail"]
    library.request("restrict_access", {"brainId": brain["id"]})
    assert mode(shared) == 0o700


def test_git_file_pointing_at_an_ordinary_folder_never_changes_it(library, tmp_path):
    root = tmp_path / "brain"
    root.mkdir()
    shared = tmp_path / "public_html"
    shared.mkdir()
    (shared / "index.html").write_text("<p>Hi</p>", encoding="utf-8")
    shared.chmod(0o755)
    (root / ".git").write_text(f"gitdir: {shared}\n", encoding="utf-8")
    brain = library.open({"root": str(root)})
    assert mode(root) == 0o700
    # Not a Git folder, so opening the brain, health and the fix all leave it alone.
    assert mode(shared) == 0o755
    assert access_issues(library) == []
    library.request("restrict_access", {"brainId": brain["id"]})
    assert mode(shared) == 0o755


def test_ignored_access_warning_returns_when_permissions_change(library, tmp_path):
    root = brain_folder(tmp_path)
    library.open({"root": str(root)})
    root.chmod(0o755)
    first = access_issues(library)
    assert [item["path"] for item in first] == [str(root)]
    library.request("ignore_health", {"ids": [item["id"] for item in first]})
    assert access_issues(library) == []

    # A different mode is a new warning, found when health checks the folders again:
    # at most a minute later, or right away when Library health opens.
    root.chmod(0o775)
    library.recheck_health()
    assert [item["path"] for item in access_issues(library)] == [str(root)]


def test_folder_the_rule_cannot_change_still_warns(library, tmp_path, monkeypatch):
    root = brain_folder(tmp_path)
    monkeypatch.setattr(brain_library, "restrict_to_owner", lambda folder: False)
    brain = library.open({"root": str(root)})
    assert mode(root) == 0o755
    assert library.request("restrict_access", {"brainId": brain["id"]})["remaining"] == [
        str(root),
        str(root / ".git"),
    ]
    assert len(access_issues(library)) == 2


def test_brains_opened_earlier_become_private_on_the_background_pass(library, tmp_path):
    root = brain_folder(tmp_path)
    library.open({"root": str(root)})
    root.chmod(0o755)
    library._monitor_pass()
    assert mode(root) == 0o700
    # Once per session: a later loosening is reported, not silently reverted.
    root.chmod(0o755)
    library._monitor_pass()
    assert mode(root) == 0o755
    assert open_to_others(root)
