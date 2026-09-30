from __future__ import annotations

import os
from pathlib import Path

import pytest

from gofer.rattish.storage import migrate_legacy_directory


def test_storage_migration_preserves_conflicts_and_moves_nested_records(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    destination = tmp_path / "current"
    (source / "nested").mkdir(parents=True)
    destination.mkdir()
    (source / "conflict.json").write_bytes(b"old")
    (destination / "conflict.json").write_bytes(b"current")
    (source / "nested" / "run.json").write_bytes(b"run")
    original = source / "nested" / "run.json"
    os.utime(original, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
    modified = original.stat().st_mtime_ns
    migrate_legacy_directory(source, destination)
    assert (destination / "nested" / "run.json").read_bytes() == b"run"
    assert (destination / "nested" / "run.json").stat().st_mtime_ns == modified
    assert not (source / "nested").exists()
    assert (destination / "conflict.json").read_bytes() == b"current"
    assert (source / "conflict.json").read_bytes() == b"old"
    (source / "conflict.json").unlink()
    migrate_legacy_directory(source, destination)
    assert not source.exists()


@pytest.mark.parametrize("linked", ["source", "destination", "source-parent", "destination-parent"])
def test_storage_migration_refuses_linked_roots(tmp_path: Path, linked: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "private.json"
    sentinel.write_bytes(b"private")
    source = tmp_path / "source" / "records"
    destination = tmp_path / "destination" / "records"
    for name, root in [("source", source), ("destination", destination)]:
        if linked == name + "-parent":
            root.parent.symlink_to(outside, target_is_directory=True)
        else:
            root.parent.mkdir()
            if linked == name:
                root.symlink_to(outside, target_is_directory=True)
            elif name == "source":
                root.mkdir()
                (root / "run.json").write_bytes(b"run")
    migrate_legacy_directory(source, destination)
    assert sentinel.read_bytes() == b"private"
    assert sorted(p.name for p in outside.iterdir()) == ["private.json"]


def test_storage_migration_leaves_linked_records_in_legacy_directory(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    source.mkdir()
    private = tmp_path / "private.json"
    private.write_bytes(b"private")
    (source / "linked.json").symlink_to(private)
    destination = tmp_path / "current"
    migrate_legacy_directory(source, destination)
    assert private.read_bytes() == b"private"
    assert (source / "linked.json").is_symlink()
    assert list(destination.iterdir()) == []
