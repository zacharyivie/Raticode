"""Raticode's data folder holds transcripts and databases that can quote credentials."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gofer.utils import paths
from gofer.utils.paths import restrict_to_owner

posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")


@posix
def test_existing_data_folder_becomes_private_without_touching_its_files(tmp_path: Path):
    data = tmp_path / "Gofer Flow Workflows"
    data.mkdir(mode=0o755)
    data.chmod(0o755)
    journal = data / "chat-jobs" / "turn.jsonl"
    journal.parent.mkdir()
    journal.write_text('{"text": "synthetic"}\n')
    journal.chmod(0o644)

    assert restrict_to_owner(data) is True
    assert data.stat().st_mode & 0o777 == 0o700
    # Files keep their content and mode; the folder alone stops other accounts.
    assert journal.read_text() == '{"text": "synthetic"}\n'
    assert journal.stat().st_mode & 0o777 == 0o644
    # Already private: nothing to change.
    assert restrict_to_owner(data) is False


@posix
def test_home_and_folders_above_it_are_never_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    house = tmp_path / "home" / "user"
    house.mkdir(parents=True)
    house.chmod(0o755)
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: house))
    assert restrict_to_owner(house) is False
    assert restrict_to_owner(house.parent) is False
    assert house.stat().st_mode & 0o777 == 0o755
    assert restrict_to_owner(tmp_path / "missing") is False


@posix
def test_the_desktop_backend_makes_its_data_folder_private(tmp_path: Path):
    from gofer.ui.server import GoferUiServer

    data = tmp_path / "data"
    data.mkdir()
    data.chmod(0o755)
    server = GoferUiServer(("127.0.0.1", 0), data, api_token="permissions-test")
    try:
        assert data.stat().st_mode & 0o777 == 0o700
    finally:
        server.server_close()
