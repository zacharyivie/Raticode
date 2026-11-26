"""Every folder Raticode keeps in its data folder is on the one managed list.

Workflow discovery prunes these folders and bundle import refuses to write into them.
A folder added in code without updating the list would show its project files as fake
workflows, so this test reads the source for ``data_dir / "<name>"`` literals.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gofer.utils.data_folders import (
    MANAGED_DATA_FOLDER_PREFIXES,
    MANAGED_DATA_FOLDERS,
    is_managed_data_folder,
)

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "gofer"

# Data-folder entries that are not Raticode's own managed folders. Keep this short.
NOT_MANAGED: set[str] = set()

# data_dir / "x", self._data_dir / "x", get_data_dir() / "x", _data_dir(base) / "x",
# (data_dir or get_data_dir()) / "x", data_dir.resolve() / "x" and data_root / "x".
DATA_FOLDER_LITERAL = re.compile(
    r"""\b[\w.]*data_(?:dir|root)\w*(?:\([^()]*\))?(?:\.resolve\(\))?\)?\s*/\s*"([^"]+)\""""
)
# A name with a file extension (schedules.db, providers.json) is a file, not a folder.
FILE_NAME = re.compile(r"[^.]\.[A-Za-z0-9]+$")


def _data_folder_literals() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in DATA_FOLDER_LITERAL.finditer(text):
            name = match.group(1)
            if FILE_NAME.search(name):
                continue
            line = text.count("\n", 0, match.start()) + 1
            found.setdefault(name, []).append(f"{path.relative_to(SOURCE_ROOT)}:{line}")
    return found


def test_scan_finds_the_folders_code_names() -> None:
    found = _data_folder_literals()
    # A broken pattern would find nothing and pass the next test vacuously.
    for name in ("rem-work", "trash", "second-brains", "command-slots", "chat-attachments"):
        assert name in found, name


def test_every_data_folder_named_in_code_is_managed_or_allowed() -> None:
    missing = {
        name: places
        for name, places in _data_folder_literals().items()
        if not is_managed_data_folder(name) and name not in NOT_MANAGED
    }
    assert missing == {}, (
        "Add these data folders to MANAGED_DATA_FOLDERS in src/gofer/utils/data_folders.py "
        "(or to NOT_MANAGED here if workflow discovery should still scan them): "
        f"{missing}"
    )


def test_allow_list_has_no_stale_entries() -> None:
    found = _data_folder_literals()
    assert {name for name in NOT_MANAGED if name not in found} == set()
    assert not NOT_MANAGED & MANAGED_DATA_FOLDERS


def test_folders_named_through_constants_are_managed() -> None:
    """Folders built from a constant or a default argument rather than a literal."""
    import inspect

    from gofer.cli.client import API_DIR_NAME
    from gofer.core.agent_credentials import DATA_CREDENTIAL_FOLDERS
    from gofer.core.provider_accounts import account_folder_names
    from gofer.core.rem_library import SKILLS_FOLDER
    from gofer.ui import organization_repository
    from gofer.ui.browser_tools import GRANTS_DIR
    from gofer.ui.headless import HEADLESS_DIRNAME
    from gofer.ui.rem_home_worktree import plan_worktree

    names = {
        API_DIR_NAME,
        SKILLS_FOLDER,
        organization_repository.FOLDER,
        organization_repository.WORKTREES,
        GRANTS_DIR,
        HEADLESS_DIRNAME,
        *DATA_CREDENTIAL_FOLDERS,
        *account_folder_names(),
        inspect.signature(plan_worktree).parameters["folder"].default,
    }
    worktree_folders = set(
        re.findall(r'plan_worktree\([^)]*?folder="([^"]+)"', _source_text(), re.DOTALL)
    )
    assert "organization-worktrees" in worktree_folders
    assert {name for name in names | worktree_folders if not is_managed_data_folder(name)} == set()


def _source_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(SOURCE_ROOT.rglob("*.py")))


@pytest.mark.parametrize(
    ("name", "managed"),
    [
        ("rem-worktrees", True),
        ("organization-shared-worktrees", True),
        ("chat-anything-new", True),
        ("Trash", True),
        ("workspaces", True),
        ("my-flows", False),
        ("prompts", False),
        ("chatter", False),
    ],
)
def test_is_managed_data_folder(name: str, managed: bool) -> None:
    assert is_managed_data_folder(name) is managed


def test_prefixes_and_names_are_lowercase() -> None:
    assert all(name == name.lower() for name in MANAGED_DATA_FOLDERS)
    assert all(prefix == prefix.lower() for prefix in MANAGED_DATA_FOLDER_PREFIXES)
