"""The backend replaces whole files through one wrapper, ``replace_file``.

A hand-written temp-and-rename writer tends to get one of the details wrong: a
predictable temporary name, a link followed at the destination, a file that is
readable by other accounts until a later chmod, or no flush before the rename. The
wrapper does all of them over ``atomic_binary_output``. This test finds the next
hand-written one.
"""

from __future__ import annotations

import ast
import os
import stat
import sys
from pathlib import Path

import pytest

from gofer.core import computers, provider_preferences
from gofer.rattish import workflow_secrets
from gofer.utils.atomic_output import replace_file

SOURCE = Path(__file__).resolve().parents[2] / "src" / "gofer"
# The wrapper and the helpers under it.
OWN_FILE_WRITES = "utils/atomic_output.py"
# "file::function" for renames that aren't one file's new contents, with why.
ALLOWED: dict[str, str] = {
    "ui/rem_db_backups.py::snapshot": (
        "SQLite's backup API writes the copy by path and an integrity check reads it "
        "before the rename, so there are no bytes to hand to replace_file"
    ),
    "ui/rem_db_backups.py::_move_into": "moves the damaged live files aside, not a new write",
    "ui/rem_db_backups.py::_put_back": "puts moved files back after a failed restore",
    "ui/rem_db_backups.py::restore": (
        "swaps a verified SQLite copy, staged on disk, in as the live database"
    ),
    "ui/rem_db_backups.py::_complete": (
        "finishes an interrupted restore: moves files aside and swaps the staged copy in"
    ),
    "social/service.py::import_media": (
        "content-addressed media: the copy is streamed with a size cap and EXIF removed "
        "before its name, the digest, is known, and an existing file is never replaced"
    ),
    "ui/chat.py::_prepare_local_gofer_cli": (
        "copies the CLI helper with shutil.copy2 and must keep its owner exec bit"
    ),
    "ui/chat.py::_capture_chat_project": (
        "streams a project file into a snapshot blob named by its digest, known only after the copy"
    ),
    "ui/chat_media.py::_ensure_offline_model": (
        "swaps in a whole extracted speech model folder, not a file"
    ),
    "rattish/artifacts.py::_move_legacy_file": (
        "moves an old artifact into its workflow workspace; nothing is rewritten"
    ),
    "rattish/formatter.py::_replace_text": (
        "rewrites a workflow file in the user's own project, which must keep its mode "
        "instead of becoming 0600"
    ),
}


def file_renames(tree: ast.AST) -> list[tuple[str, int]]:
    """(enclosing function, line) of each os.replace/os.rename and Path.replace call.

    ``str.replace`` takes two arguments and ``datetime.replace`` takes keywords, so a
    ``.replace`` call with one positional argument and nothing else is a path's.
    """
    found: list[tuple[str, int]] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = (
                child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else function
            )
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                target = child.func
                by_os = (
                    isinstance(target.value, ast.Name)
                    and target.value.id == "os"
                    and target.attr in {"replace", "rename"}
                )
                by_path = target.attr == "replace" and len(child.args) == 1 and not child.keywords
                if by_os or by_path:
                    found.append((name, child.lineno))
            visit(child, name)

    visit(tree, "<module>")
    return found


def backend_renames() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        relative = path.relative_to(SOURCE).as_posix()
        if relative == OWN_FILE_WRITES:
            continue
        for function, line in file_renames(ast.parse(path.read_text(encoding="utf-8"))):
            found.setdefault(f"{relative}::{function}", []).append(line)
    return found


def test_backend_file_writes_all_go_through_replace_file():
    found = backend_renames()
    unexpected = [
        f"{place}:{line}"
        for place, lines in found.items()
        if place not in ALLOWED
        for line in lines
    ]
    assert not unexpected, (
        "Write whole files with gofer.utils.atomic_output.replace_file, or add the place "
        "to ALLOWED with the reason it can't: " + ", ".join(unexpected)
    )


def test_every_allowed_place_still_renames_and_says_why():
    found = backend_renames()
    assert (SOURCE / OWN_FILE_WRITES).is_file()
    assert [place for place in ALLOWED if place not in found] == []
    assert all(isinstance(reason, str) and reason.strip() for reason in ALLOWED.values())


def test_the_scan_finds_renames_but_not_text_or_date_replacements():
    code = (
        "def write():\n"
        "    os.replace(temporary, path)\n"
        "    temporary.replace(path)\n"
        "    os.rename(a, b)\n"
        "def other():\n"
        "    text.replace('a', 'b')\n"
        "    moment.replace(tzinfo=UTC)\n"
        "    dataclasses.replace(files, digests=digests)\n"
    )
    assert file_renames(ast.parse(code)) == [("write", 2), ("write", 3), ("write", 4)]


# The wrapper ---------------------------------------------------------------------------

needs_modes = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, account: str) -> str | None:
        return self.values.get(account)

    def put(self, account: str, value: str) -> None:
        self.values[account] = value

    def delete(self, account: str) -> None:
        self.values.pop(account, None)


@needs_modes
def test_data_files_through_a_linked_data_folder_end_up_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real-data"
    real.mkdir()
    link = tmp_path / "data"
    link.symlink_to(real, target_is_directory=True)
    # An older copy readable by everyone is replaced by a private one.
    (real / "providers.json").write_text("{}\n")
    os.chmod(real / "providers.json", 0o644)
    monkeypatch.setattr(provider_preferences, "get_data_dir", lambda: link)
    keychain = MemoryStore()
    monkeypatch.setattr(workflow_secrets, "_secret_store", lambda: keychain)
    old_umask = os.umask(0o022)
    try:
        provider_preferences.save_speech_preference({"engine": "offline"})
        workflow_secrets.save_workflow_secret("DEPLOY_TOKEN", "value", data_dir=link)
        computer = computers.this_computer_id(link)
    finally:
        os.umask(old_umask)

    written = {
        "providers.json": real / "providers.json",
        "secrets index": real / workflow_secrets.INDEX_FILE,
        "computers.json": real / computers._STORE,
    }
    assert {name: mode(path) for name, path in written.items()} == dict.fromkeys(written, 0o600)
    assert provider_preferences.provider_preference("speech")["engine"] == "offline"
    assert workflow_secrets.workflow_secret_saved("DEPLOY_TOKEN", data_dir=link)
    assert computers.this_computer_id(link) == computer
    assert link.is_symlink() and sorted(p.name for p in real.iterdir() if p.suffix == ".tmp") == []


@needs_modes
def test_replace_file_sets_a_requested_mode_before_the_name_appears(tmp_path: Path) -> None:
    script = tmp_path / "bin" / "wrapper"
    replace_file(script, b"#!/bin/sh\n", mode=0o700)
    assert mode(script) == 0o700 and script.read_bytes() == b"#!/bin/sh\n"
    replace_file(script, b"#!/bin/sh\nexit 0\n")
    assert mode(script) == 0o600


def test_replace_file_never_writes_through_a_link_at_the_destination(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    destination = tmp_path / "data" / "settings.json"
    destination.parent.mkdir()
    destination.symlink_to(outside)

    replace_file(destination, b"{}")

    assert outside.read_text() == "keep"
    assert not destination.is_symlink() and destination.read_bytes() == b"{}"
