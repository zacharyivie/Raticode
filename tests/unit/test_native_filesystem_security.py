"""Native path-boundary fixtures run on all three release platforms."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from gofer.utils.atomic_output import (
    atomic_binary_output,
    mkdir_without_links,
    open_binary_input,
    scandir_without_links,
    unlink_without_links,
)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows junctions")
def test_windows_junction_ancestors_cannot_redirect_file_operations(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "note").write_bytes(b"private")
    junction = tmp_path / "approved"
    subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        check=True,
        capture_output=True,
    )
    try:
        with pytest.raises(OSError):
            with atomic_binary_output(junction / "written"):
                pytest.fail("Junction must not authorize a write")
        with pytest.raises(OSError):
            with open_binary_input(junction / "note"):
                pytest.fail("Junction must not authorize a read")
        with pytest.raises(OSError):
            with scandir_without_links(junction):
                pytest.fail("Junction must not authorize enumeration")
        with pytest.raises(OSError):
            mkdir_without_links(junction / "created")
        with pytest.raises(OSError):
            unlink_without_links(junction / "note")
        assert sorted(path.name for path in outside.iterdir()) == ["note"]
        assert (outside / "note").read_bytes() == b"private"
    finally:
        junction.rmdir()


@pytest.mark.parametrize("operation", ["read", "scan", "write"])
def test_open_handles_survive_or_block_adversarial_ancestor_replacement(
    tmp_path: Path, operation: str
) -> None:
    approved = tmp_path / "approved"
    parent = approved / "nested"
    parent.mkdir(parents=True)
    (parent / "note").write_bytes(b"approved")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "note").write_bytes(b"outside")

    def replace_ancestor() -> None:
        if os.name == "nt":
            # Windows handles omit delete sharing for every ancestor.
            with pytest.raises(OSError):
                approved.rename(tmp_path / "moved")
        else:
            approved.rename(tmp_path / "moved")
            approved.symlink_to(outside, target_is_directory=True)

    if operation == "read":
        with open_binary_input(parent / "note") as stream:
            replace_ancestor()
            assert stream.read() == b"approved"
    elif operation == "scan":
        with scandir_without_links(parent) as entries:
            replace_ancestor()
            assert [entry.name for entry in entries] == ["note"]
    else:
        with atomic_binary_output(parent / "result") as stream:
            replace_ancestor()
            stream.write(b"result")
        destination = parent if os.name == "nt" else tmp_path / "moved/nested"
        assert (destination / "result").read_bytes() == b"result"
    assert (outside / "note").read_bytes() == b"outside"
    assert not (outside / "result").exists()
