from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from gofer.utils.atomic_output import atomic_binary_output


def test_atomic_output_replaces_links_without_modifying_their_targets(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_bytes(b"original")
    for kind in ("hard", "symbolic", "dangling"):
        destination = tmp_path / kind
        if kind == "hard":
            os.link(outside, destination)
        else:
            destination.symlink_to(outside if kind == "symbolic" else tmp_path / "absent")
        with atomic_binary_output(destination) as output:
            output.write(b"new")
        assert destination.read_bytes() == b"new"
        assert not destination.is_symlink()
        assert outside.read_bytes() == b"original"
        assert not (tmp_path / "absent").exists()


@pytest.mark.skipif(os.open not in os.supports_dir_fd, reason="POSIX directory descriptors")
def test_atomic_output_pins_parent_against_replacement(tmp_path: Path) -> None:
    parent = tmp_path / "approved"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with atomic_binary_output(parent / "result.zip") as output:
        parent.rename(tmp_path / "original")
        parent.symlink_to(outside, target_is_directory=True)
        output.write(b"fixture")
    assert not (outside / "result.zip").exists()
    assert (tmp_path / "original/result.zip").read_bytes() == b"fixture"


@pytest.mark.skipif(os.open not in os.supports_dir_fd, reason="POSIX directory descriptors")
def test_atomic_output_rejects_swapped_parent_before_open(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "approved").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        with atomic_binary_output(tmp_path / "approved/result.zip"):
            pytest.fail("must reject the replaced parent")
    assert not list(outside.iterdir())


def test_atomic_output_preserves_original_after_error(tmp_path: Path) -> None:
    destination = tmp_path / "output"
    destination.write_bytes(b"original")
    with pytest.raises(RuntimeError):
        with atomic_binary_output(destination) as output:
            output.write(b"incomplete")
            raise RuntimeError("cancelled")
    assert destination.read_bytes() == b"original"
    assert not list(tmp_path.glob(".output.*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows directory handle sharing")
def test_windows_atomic_output_holds_parent_against_rename(tmp_path: Path) -> None:
    parent = tmp_path / "approved"
    parent.mkdir()
    with atomic_binary_output(parent / "result") as output:
        with pytest.raises(OSError):
            parent.rename(tmp_path / "moved")
        output.write(b"complete")
    parent.rename(tmp_path / "moved")
    assert (tmp_path / "moved/result").read_bytes() == b"complete"


def test_mkdir_and_cleanup_refuse_parent_links(tmp_path: Path) -> None:
    from gofer.utils.atomic_output import mkdir_without_links, remove_tree_without_links

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").mkdir()
    parent = tmp_path / "approved"
    parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        mkdir_without_links(parent / "created", exclusive=True)
    with pytest.raises(OSError):
        remove_tree_without_links(parent / "keep")
    assert list(outside.iterdir()) == [outside / "keep"]


@pytest.mark.parametrize("kind", ["plain", "hardlink", "symlink", "dangling"])
def test_exclusive_atomic_output_preserves_existing_destinations(tmp_path: Path, kind: str) -> None:
    existing = tmp_path / "existing"
    existing.write_bytes(b"original")
    destination = tmp_path / "note"
    if kind == "plain":
        destination.write_bytes(b"original")
    elif kind == "hardlink":
        os.link(existing, destination)
    else:
        destination.symlink_to(existing if kind == "symlink" else tmp_path / "absent")
    before = destination.lstat()
    with pytest.raises(FileExistsError):
        with atomic_binary_output(destination, exclusive=True) as output:
            output.write(b"replacement")
    assert destination.lstat().st_ino == before.st_ino
    assert existing.read_bytes() == b"original"
    assert not list(tmp_path.glob(".note.*.tmp"))


def test_exclusive_atomic_output_preserves_concurrent_creator(tmp_path: Path) -> None:
    destination = tmp_path / "note"
    with pytest.raises(FileExistsError):
        with atomic_binary_output(destination, exclusive=True) as output:
            output.write(b"our note")
            destination.write_bytes(b"concurrent note")
    assert destination.read_bytes() == b"concurrent note"
    assert not list(tmp_path.glob(".note.*.tmp"))


def test_scandir_refuses_parent_symlinks(tmp_path: Path) -> None:
    from gofer.utils.atomic_output import scandir_without_links

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "nested").mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        with scandir_without_links(tmp_path / "linked/nested"):
            pytest.fail("must not enumerate through a symlink")


@pytest.mark.skipif(os.open not in os.supports_dir_fd, reason="POSIX directory descriptors")
def test_atomic_outputs_can_create_the_same_parent_concurrently(tmp_path, monkeypatch):
    mkdir = os.mkdir
    ready = Barrier(2)

    def concurrent_mkdir(path, mode=0o777, *, dir_fd=None):
        if path == "shared":
            # Both writers observed a missing parent before either creates it.
            ready.wait(timeout=5)
        return mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", concurrent_mkdir)

    def write(name):
        with atomic_binary_output(tmp_path / "shared" / name, exclusive=True) as output:
            output.write(name.encode())

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write, "first")
        second = pool.submit(write, "second")
        first.result(timeout=10)
        second.result(timeout=10)
    assert {file.name: file.read_bytes() for file in (tmp_path / "shared").iterdir()} == {
        "first": b"first",
        "second": b"second",
    }


@pytest.mark.skipif(os.open not in os.supports_dir_fd, reason="POSIX directory descriptors")
@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_atomic_output_rejects_non_directory_created_during_mkdir(tmp_path, monkeypatch, kind):
    mkdir = os.mkdir
    outside = tmp_path / "outside"
    outside.mkdir()

    def replaced_mkdir(path, mode=0o777, *, dir_fd=None):
        if path == "shared":
            if kind == "symlink":
                os.symlink(outside, path, dir_fd=dir_fd)
            else:
                (tmp_path / "shared").write_bytes(b"concurrent file")
        return mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", replaced_mkdir)
    with pytest.raises(OSError):
        with atomic_binary_output(tmp_path / "shared" / "note"):
            pytest.fail("must reject a concurrently created file or symlink")
    assert not list(outside.iterdir())
    if kind == "file":
        assert (tmp_path / "shared").read_bytes() == b"concurrent file"
