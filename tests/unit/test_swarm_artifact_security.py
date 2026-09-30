from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

import pytest

from gofer.ui.swarm_workspaces import artifacts


@pytest.mark.parametrize("parent_link", [False, True])
def test_artifacts_reject_links_replaced_after_validation(tmp_path, monkeypatch, parent_link):
    root = tmp_path / "workspace"
    folder = root / "artifacts"
    folder.mkdir(parents=True)
    source = folder / "result.txt"
    source.write_bytes(b"approved artifact")
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / source.name).write_bytes(b"outside the workspace")
    original_is_file = Path.is_file
    swapped = False

    def swap(path, *args, **kwargs):
        nonlocal swapped
        result = original_is_file(path, *args, **kwargs)
        if path == source and not swapped:
            swapped = True
            if parent_link:
                folder.rename(root / "original")
                folder.symlink_to(outside, target_is_directory=True)
            else:
                source.unlink()
                source.symlink_to(outside / source.name)
        return result

    monkeypatch.setattr(Path, "is_file", swap)
    with pytest.raises(ValueError, match="Artifact"):
        artifacts(root, ["artifacts/result.txt"])
    assert swapped


def test_artifacts_hash_regular_files_and_allow_internal_aliases(tmp_path):
    content = b"verified artifact"
    source = tmp_path / "result.txt"
    source.write_bytes(content)
    (tmp_path / "alias.txt").symlink_to(source)
    assert artifacts(tmp_path, ["result.txt", "alias.txt"]) == [
        {"path": name, "sha256": hashlib.sha256(content).hexdigest()}
        for name in ["result.txt", "alias.txt"]
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX named pipes")
def test_artifacts_reject_files_replaced_by_named_pipes_without_blocking(tmp_path, monkeypatch):
    source = tmp_path / "result.txt"
    source.write_bytes(b"approved artifact")
    original_is_file = Path.is_file
    swapped = False

    def swap(path, *args, **kwargs):
        nonlocal swapped
        result = original_is_file(path, *args, **kwargs)
        if path == source and not swapped:
            swapped = True
            source.unlink()
            os.mkfifo(source)
        return result

    monkeypatch.setattr(Path, "is_file", swap)
    finished = threading.Event()
    errors = []

    def read():
        try:
            artifacts(tmp_path, [source.name])
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    completed = finished.wait(timeout=2)
    if not completed:
        # Release an unsafe blocking reader so a regression cannot hang pytest.
        try:
            writer = os.open(source, os.O_WRONLY | os.O_NONBLOCK)
            os.close(writer)
        except OSError:
            pass
    worker.join(timeout=2)
    assert swapped
    assert completed, "Artifact verification waited for a named-pipe writer"
    assert len(errors) == 1 and isinstance(errors[0], ValueError)
