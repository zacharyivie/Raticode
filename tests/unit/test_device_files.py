"""Whole-file and local scope boundaries, without network or user files."""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from gofer.devices.files import DeviceFiles
from gofer.devices.registry import DeviceRegistry, PairingError


class Secrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def put(self, key: str, value: str) -> None:
        self.values[key] = value


@pytest.fixture
def files(tmp_path):
    registry = DeviceRegistry(tmp_path / "state", Secrets(), initialize=True)
    yield DeviceFiles(registry)
    registry.close()


def offer(files: DeviceFiles, data: bytes) -> dict[str, Any]:
    return {
        "file_id": str(uuid4()),
        "name": "private.txt",
        "mime": "text/plain",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "expires_at": int(files.registry.clock()) + 300,
    }


def chunk(value: dict[str, Any], data: bytes, offset: int = 0, eof: bool = True) -> dict[str, Any]:
    return {
        "file_id": value["file_id"],
        "offset": offset,
        "data": base64.urlsafe_b64encode(data).rstrip(b"=").decode(),
        "eof": eof,
    }


def test_whole_file_before_expose_and_encrypted_at_rest(files):
    value = offer(files, b"private content")
    with files.registry.transaction():
        files.offer("peer", "thread", value)
        files.receive("peer", "thread", chunk(value, b"private ", eof=False))
    with pytest.raises(PairingError, match="unavailable"):
        files.content("peer", "thread", value["file_id"])
    with files.registry.transaction():
        result = files.receive("peer", "thread", chunk(value, b"content", offset=8))
    assert result["state"] == "available"
    assert files.content("peer", "thread", value["file_id"])[1] == b"private content"
    for row in files.registry.db.execute(
        "SELECT data FROM device_files UNION ALL SELECT data FROM device_file_chunks"
    ):
        assert b"private" not in row[0]
    with pytest.raises(PairingError, match="unavailable"):
        files.content("other", "thread", value["file_id"])
    with pytest.raises(PairingError, match="unavailable"):
        files.content("peer", "other", value["file_id"])


def test_duplicate_chunk_and_conflict(files):
    value = offer(files, b"abcdef")
    with files.registry.transaction():
        files.offer("peer", "thread", value)
        files.receive("peer", "thread", chunk(value, b"abc", eof=False))
        assert (
            files.receive("peer", "thread", chunk(value, b"abc", eof=False))["received_size"] == 3
        )
    with pytest.raises(PairingError, match="conflict"), files.registry.transaction():
        files.receive("peer", "thread", chunk(value, b"xyz", eof=False))
    with pytest.raises(PairingError, match="offset"), files.registry.transaction():
        files.receive("peer", "thread", chunk(value, b"def", offset=4))


def test_truncation_and_bad_digest_never_expose(files):
    value = offer(files, b"abcdef")
    with files.registry.transaction():
        files.offer("peer", "thread", value)
    with pytest.raises(PairingError, match="integrity"), files.registry.transaction():
        files.receive("peer", "thread", chunk(value, b"abc"))
    with pytest.raises(PairingError, match="integrity"), files.registry.transaction():
        files.receive("peer", "thread", chunk(value, b"123456"))
    with pytest.raises(PairingError, match="unavailable"):
        files.content("peer", "thread", value["file_id"])


def test_scope_snapshot_rejects_parent_and_leaf_symlinks(files, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (root / "link").symlink_to(outside)
    (root / "parent").symlink_to(tmp_path, target_is_directory=True)
    for path in (outside, root / "link", root / "parent" / "outside.txt"):
        with pytest.raises(PairingError, match="scoped"), files.registry.transaction():
            files.snapshot("peer", "thread", root, path)
    good = root / "inside.txt"
    good.write_text("inside")
    with files.registry.transaction():
        value = files.snapshot("peer", "thread", root, good)
    good.write_text("changed")
    assert files.content("peer", "thread", value["file_id"])[1] == b"inside"


def test_expiry_and_cancel_remove_protected_content(files):
    value = offer(files, b"abc")
    with files.registry.transaction():
        files.offer("peer", "thread", value)
        files.receive("peer", "thread", chunk(value, b"abc"))
        files.cancel("peer", "thread", value["file_id"])
    with pytest.raises(PairingError, match="unavailable"):
        files.content("peer", "thread", value["file_id"])


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX named pipes")
def test_snapshot_rejects_fifo_without_waiting_for_writer(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    # Isolate the open so a regression times out instead of hanging the test suite.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path\n"
            "import sys\n"
            "from gofer.devices.files import DeviceFiles\n"
            "from gofer.devices.registry import PairingError\n"
            "root = Path(sys.argv[1])\n"
            "try:\n"
            "    object.__new__(DeviceFiles).snapshot('peer', 'thread', root, root / 'pipe')\n"
            "except PairingError as exc:\n"
            "    assert str(exc) == 'scoped_file_unavailable'\n"
            "else:\n"
            "    raise AssertionError('FIFO was accepted')\n",
            str(tmp_path),
        ],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
