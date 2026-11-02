"""Whole-file and local scope boundaries, without network or user files."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from gofer.devices import files as files_module
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
    stored = list(files.directory.iterdir())
    assert len(stored) == 1 and b"private" not in stored[0].read_bytes()
    assert stored[0].stat().st_mode & 0o777 == 0o600
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


def test_small_chunks_never_multiply_storage(files):
    data = bytes(range(256)) * 80  # 20,480 bytes: one full record and one partial
    value = offer(files, data)
    with files.registry.transaction():
        files.offer("peer", "thread", value)
        for offset in range(len(data)):
            last = offset == len(data) - 1
            files.receive("peer", "thread", chunk(value, data[offset : offset + 1], offset, last))
        # Retransmissions are compared with what was committed.
        assert files.receive("peer", "thread", chunk(value, data[:3], eof=False))["state"] == (
            "available"
        )
    (stored,) = files.directory.iterdir()
    assert stored.stat().st_size == files_module.stored_size(len(data))
    assert files.content("peer", "thread", value["file_id"])[1] == data


def test_byte_quota_and_full_disk_refuse_offers(files, monkeypatch):
    monkeypatch.setattr(files_module, "MAX_TOTAL", 10)
    with files.registry.transaction():
        files.offer("peer", "thread", offer(files, b"123456"))
    with pytest.raises(PairingError, match="file_quota"), files.registry.transaction():
        files.offer("peer", "thread", offer(files, b"12345"))
    monkeypatch.setattr(files_module, "MAX_TOTAL", 10**12)
    monkeypatch.setattr(files, "_free", lambda: files_module.FREE_SPACE_MARGIN + 100)
    with pytest.raises(PairingError, match="file_disk_full"), files.registry.transaction():
        files.offer("peer", "thread", offer(files, b"x" * 100))
    with files.registry.transaction():
        files.offer("peer", "thread", offer(files, b"x" * 50))


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


def sized(files: DeviceFiles, size: int) -> dict[str, Any]:
    return {**offer(files, b""), "size": size, "sha256": "0" * 64}


@pytest.mark.parametrize(
    ("limit", "size", "accepted"),
    [
        (files_module.MAX_FILE, files_module.MAX_FILE, True),
        (files_module.MAX_FILE, files_module.MAX_FILE + 1, False),
        (64 * 1024 * 1024, 64 * 1024 * 1024 - 1, True),
        (64 * 1024 * 1024, 64 * 1024 * 1024 + 1, False),
        (files_module.MAX_FILE_LARGE, files_module.MAX_FILE_LARGE - 1, True),
        (files_module.MAX_FILE_LARGE, files_module.MAX_FILE_LARGE, True),
        (files_module.MAX_FILE_LARGE, files_module.MAX_FILE_LARGE + 1, False),
    ],
)
def test_offer_size_boundaries(files, monkeypatch, limit, size, accepted):
    monkeypatch.setattr(files, "_free", lambda: 10**13)
    value = sized(files, size)
    if accepted:
        with files.registry.transaction():
            files.offer("peer", "thread", value, limit=limit)
    else:
        with pytest.raises(PairingError, match="bounds"), files.registry.transaction():
            files.offer("peer", "thread", value, limit=limit)


def test_legacy_limit_applies_without_an_explicit_one(files, monkeypatch):
    monkeypatch.setattr(files, "_free", lambda: 10**13)
    with pytest.raises(PairingError, match="bounds"), files.registry.transaction():
        files.offer("peer", "thread", sized(files, files_module.MAX_FILE + 1))


def test_snapshot_size_boundaries_use_sparse_files(files, tmp_path, monkeypatch):
    monkeypatch.setattr(files, "_free", lambda: 10**13)
    root = tmp_path / "project"
    root.mkdir()
    for name, size in (
        ("over-legacy", files_module.MAX_FILE + 1),
        ("over-max", files_module.MAX_FILE_LARGE + 1),
    ):
        with open(root / name, "wb") as handle:
            handle.truncate(size)
    with pytest.raises(PairingError, match="scoped"), files.registry.transaction():
        files.snapshot("peer", "thread", root, root / "over-legacy")
    with pytest.raises(PairingError, match="scoped"), files.registry.transaction():
        files.snapshot("peer", "thread", root, root / "over-max", limit=files_module.MAX_FILE_LARGE)
    assert list(files.directory.iterdir()) == []
    with files.registry.transaction():
        value = files.snapshot(
            "peer", "thread", root, root / "over-legacy", limit=files_module.MAX_FILE_LARGE
        )
    assert value["size"] == files_module.MAX_FILE + 1
    assert value["sha256"] == hashlib.sha256(bytes(files_module.MAX_FILE + 1)).hexdigest()


def test_large_snapshot_and_chunks_keep_memory_bounded(files, tmp_path, monkeypatch):
    import tracemalloc

    monkeypatch.setattr(files, "_free", lambda: 10**13)
    root = tmp_path / "project"
    root.mkdir()
    size = 64 * 1024 * 1024 + 1
    pattern = os.urandom(files_module.CHUNK)
    digest = hashlib.sha256()
    with open(root / "big.bin", "wb") as handle:
        for _ in range(size // len(pattern)):
            handle.write(pattern)
            digest.update(pattern)
        handle.write(b"!")
        digest.update(b"!")
    tracemalloc.start()
    with files.registry.transaction():
        value = files.snapshot(
            "peer", "thread", root, root / "big.bin", limit=files_module.MAX_FILE_LARGE
        )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    assert value["sha256"] == digest.hexdigest()
    _, last = files.chunk("peer", "thread", value["file_id"], size - 1)
    assert last == b"!"
    _, middle = files.chunk("peer", "thread", value["file_id"], 1000 * files_module.CHUNK)
    assert middle == pattern
    _, chunk_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 2 * 1024 * 1024 and chunk_peak < 1024 * 1024
    with pytest.raises(PairingError, match="too_large"):
        files.content("peer", "thread", value["file_id"])


def test_migrates_files_stored_as_blobs_or_chunk_rows(tmp_path):
    registry = DeviceRegistry(tmp_path / "state", Secrets(), initialize=True)
    try:
        files = DeviceFiles(registry)
        done, partial = offer(files, b"finished file"), offer(files, b"abcdefgh")
        db = registry.db
        with registry.transaction():
            for value, state, received, blob in (
                (done, "available", 13, files._seal("peer", done["file_id"], b"finished file")),
                (partial, "receiving", 6, None),
            ):
                raw = json.dumps(value, sort_keys=True).encode()
                db.execute(
                    "INSERT INTO device_files VALUES (?,?,?,?,?,?,?,?,?,0)",
                    (
                        "peer",
                        value["file_id"],
                        "thread",
                        files._seal("peer", value["file_id"], raw),
                        value["size"],
                        value["expires_at"],
                        state,
                        received,
                        blob,
                    ),
                )
            for offset, piece in ((0, b"abc"), (3, b"def")):
                db.execute(
                    "INSERT INTO device_file_chunks VALUES (?,?,?,?)",
                    (
                        "peer",
                        partial["file_id"],
                        offset,
                        files._seal("peer", partial["file_id"], piece),
                    ),
                )
        migrated = DeviceFiles(registry)
        assert migrated.content("peer", "thread", done["file_id"])[1] == b"finished file"
        assert db.execute("SELECT COUNT(*) FROM device_file_chunks").fetchone()[0] == 0
        assert (
            db.execute("SELECT COUNT(*) FROM device_files WHERE data IS NOT NULL").fetchone()[0]
            == 0
        )
        with registry.transaction():
            status = migrated.receive("peer", "thread", chunk(partial, b"gh", offset=6))
        assert status["state"] == "available"
        assert migrated.content("peer", "thread", partial["file_id"])[1] == b"abcdefgh"
    finally:
        registry.close()


def test_removed_rows_lose_their_record_files(files):
    value = offer(files, b"abc")
    with files.registry.transaction():
        files.offer("peer", "thread", value)
        files.receive("peer", "thread", chunk(value, b"abc"))
        files.registry.db.execute("DELETE FROM device_files")
        files._expire()
    assert list(files.directory.iterdir()) == []


def test_rolled_back_chunk_does_not_break_the_retry(files):
    value = offer(files, b"abcdef")
    with files.registry.transaction():
        files.offer("peer", "thread", value)
        files.receive("peer", "thread", chunk(value, b"ab", eof=False))
    with pytest.raises(RuntimeError), files.registry.transaction():
        files.receive("peer", "thread", chunk(value, b"cd", offset=2, eof=False))
        raise RuntimeError("a later step of the same event failed")
    with files.registry.transaction():
        assert files.receive("peer", "thread", chunk(value, b"ab", eof=False))["received_size"] == 2
        files.receive("peer", "thread", chunk(value, b"cd", offset=2, eof=False))
        status = files.receive("peer", "thread", chunk(value, b"ef", offset=4))
    assert status["state"] == "available"
    assert files.content("peer", "thread", value["file_id"])[1] == b"abcdef"
