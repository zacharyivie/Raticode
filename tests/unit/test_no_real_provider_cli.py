"""Unit tests start no real provider CLI and download no embedding model.

tests/conftest.py makes both the default for every test. A real ``claude`` on the
developer's PATH used to answer sign-in checks for new accounts, so results depended on
the computer, and the first Second Brain related-meaning search tried to download its
model.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from gofer.core import provider_accounts as accounts
from gofer.ui.brain_library import BrainLibrary


@pytest.fixture
def recording_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake claude first on PATH that writes down every start. Returns its log."""
    folder = tmp_path / "bin"
    folder.mkdir()
    log = tmp_path / "claude-started.log"
    fake = folder / "claude"
    fake.write_text(f'#!/bin/sh\necho "$*" >> "{log}"\necho \'{{"loggedIn": true}}\'\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ.get('PATH', '')}")
    return log


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX stand-in CLI")
def test_a_new_claude_code_accounts_sign_in_check_never_starts_claude(
    recording_claude: Path, tmp_path: Path
) -> None:
    data_dir = tmp_path / "app"
    profile = accounts.create_account("work", provider="claude_code", data_dir=data_dir)

    status = accounts.check_status(profile, data_dir=data_dir, refresh=True)
    assert status["state"] == accounts.UNAVAILABLE
    accounts.account_status(profile, data_dir=data_dir, refresh=True)
    accounts.accounts_overview(data_dir)

    assert not recording_claude.exists(), recording_claude.read_text()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX stand-in CLI")
def test_a_test_with_its_own_run_function_still_gets_the_cli(
    recording_claude: Path, tmp_path: Path
) -> None:
    data_dir = tmp_path / "app"
    profile = accounts.create_account("work", provider="claude_code", data_dir=data_dir)
    commands: list[list[str]] = []

    def run(command: list[str], **_options: Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, '{"loggedIn": true}', "")

    status = accounts.check_status(profile, data_dir=data_dir, refresh=True, run=run)
    assert status["state"] == accounts.SIGNED_IN
    assert commands and Path(commands[0][0]).name == "claude"
    assert not recording_claude.exists()


def test_the_local_embedding_model_is_never_downloaded(tmp_path: Path) -> None:
    library = BrainLibrary(tmp_path / "app", start=False)
    try:
        with pytest.raises(RuntimeError, match="Tests don't download embedding models"):
            library._local_model()
        assert not (library.folder / "models").exists()
        stand_in = object()
        library.model = stand_in
        assert library._local_model() is stand_in
    finally:
        library.close()
