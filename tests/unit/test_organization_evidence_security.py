from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from gofer.ui.organization_operations import file_evidence


@pytest.mark.parametrize("parent_link", [False, True])
def test_evidence_rejects_links_swapped_after_validation(tmp_path, monkeypatch, parent_link):
    root = tmp_path / "workspace"
    folder = root / "artifacts"
    folder.mkdir(parents=True)
    source = folder / "result.txt"
    source.write_text("approved evidence")
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / source.name).write_text("outside the workspace")
    original_stat = Path.stat
    swapped = False

    def swap(path, *args, **kwargs):
        nonlocal swapped
        result = original_stat(path, *args, **kwargs)
        if path == source and not swapped:
            swapped = True
            if parent_link:
                folder.rename(root / "original")
                folder.symlink_to(outside, target_is_directory=True)
            else:
                source.unlink()
                source.symlink_to(outside / source.name)
        return result

    monkeypatch.setattr(Path, "stat", swap)
    with pytest.raises(ValueError):
        file_evidence({"config": {"projectRoots": [str(root)]}}, str(source))
    assert swapped


def test_evidence_rejects_growth_after_size_check(tmp_path, monkeypatch):
    source = tmp_path / "result.txt"
    source.write_bytes(b"small")
    original_stat = Path.stat
    grew = False

    def grow(path, *args, **kwargs):
        nonlocal grew
        result = original_stat(path, *args, **kwargs)
        if path == source and not grew:
            grew = True
            source.write_bytes(b"x" * 10_000_001)
        return result

    monkeypatch.setattr(Path, "stat", grow)
    with pytest.raises(ValueError, match="10 MB"):
        file_evidence({"config": {"projectRoots": [str(tmp_path)]}}, str(source))
    assert grew


def test_evidence_hashes_regular_file_in_employee_workspace(tmp_path):
    source = tmp_path / "result.txt"
    source.write_bytes(b"reviewed content")
    org = {
        "config": {
            "projectRoots": [str(tmp_path)],
            "employees": [
                {"id": "reviewer", "workspacePaths": [str(tmp_path)]},
            ],
        }
    }
    assert file_evidence(org, str(source), "reviewer") == {
        "path": str(source),
        "sha256": hashlib.sha256(b"reviewed content").hexdigest(),
    }
