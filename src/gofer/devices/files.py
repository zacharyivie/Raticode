"""Scoped immutable file snapshots carried inside the authenticated TLS stream.

File bytes never live in memory or in SQLite as a whole. Each file is stored on disk
as fixed 16 KiB plaintext records, each sealed with the registry storage key and bound
to its peer, file ID and record index. The offer's size and SHA-256 authenticate the
whole stream before a file becomes available.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from gofer.devices.registry import DeviceRegistry, PairingError

# Peers that have not announced a larger limit keep the original 10 MiB contract.
MAX_FILE = 10 * 1024 * 1024
# The largest single file this store accepts once both peers announce it.
MAX_FILE_LARGE = 2_000_000_000
MAX_TOTAL = 4_000_000_000
MAX_FILES = 128
CHUNK = 16 * 1024
# Keep room for the database, logs and other work after a file is stored.
FREE_SPACE_MARGIN = 256 * 1024 * 1024
_NONCE = 12
_TAG = 16
RECORD = _NONCE + CHUNK + _TAG


def stored_size(size: int) -> int:
    """Bytes a file of ``size`` plaintext bytes occupies on disk."""
    count = -(-size // CHUNK)
    return count * (_NONCE + _TAG) + size


class DeviceFiles:
    def __init__(self, registry: DeviceRegistry) -> None:
        self.registry = registry
        self.directory = registry.directory / "device-files"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        registry.db.executescript("""
            CREATE TABLE IF NOT EXISTS device_files (
              peer TEXT NOT NULL,id TEXT NOT NULL,thread TEXT NOT NULL,offer BLOB NOT NULL,
              size INTEGER NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL,
              received INTEGER NOT NULL,data BLOB,PRIMARY KEY(peer,id));
            CREATE TABLE IF NOT EXISTS device_file_chunks (
              peer TEXT NOT NULL,id TEXT NOT NULL,offset INTEGER NOT NULL,data BLOB NOT NULL,
              PRIMARY KEY(peer,id,offset));
        """)
        columns = {r[1] for r in registry.db.execute("PRAGMA table_info(device_files)")}
        if "chunked" not in columns:
            registry.db.execute(
                "ALTER TABLE device_files ADD COLUMN chunked INTEGER NOT NULL DEFAULT 0"
            )
        with registry.transaction():
            self._migrate()

    # Records ------------------------------------------------------------------

    def _path(self, peer: str, identifier: str) -> Path:
        # Peer and file IDs never become path components; the name is an opaque digest.
        name = hashlib.sha256(f"{peer}\0{identifier}".encode()).hexdigest()
        return self.directory / f"{name}.bin"

    def _aad(self, peer: str, identifier: str, index: int) -> bytes:
        return f"{peer}:file:{identifier}:record:{index}".encode()

    def _open_records(self, peer: str, identifier: str, *, create: bool) -> int:
        flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        if create:
            flags |= os.O_CREAT
        return os.open(self._path(peer, identifier), flags, 0o600)

    def _write_record(self, fd: int, peer: str, identifier: str, index: int, raw: bytes) -> None:
        nonce = secrets.token_bytes(_NONCE)
        sealed = nonce + self.registry._cipher.encrypt(
            nonce, raw, self._aad(peer, identifier, index)
        )
        os.pwrite(fd, sealed, index * RECORD)
        os.ftruncate(fd, index * RECORD + len(sealed))

    def _read_record(self, fd: int, peer: str, identifier: str, index: int, length: int) -> bytes:
        # Only the last record on disk can be shorter than RECORD. After a rolled-back
        # transaction it can also hold bytes past the committed length; appends only
        # extend a record, so the committed bytes are its authenticated prefix.
        sealed = os.pread(fd, RECORD, index * RECORD)
        try:
            raw = self.registry._cipher.decrypt(
                sealed[:_NONCE], sealed[_NONCE:], self._aad(peer, identifier, index)
            )
        except Exception:
            raise PairingError("file_integrity") from None
        if len(raw) < length:
            raise PairingError("file_integrity")
        return raw[:length]

    def _records(self, peer: str, identifier: str, size: int) -> Iterator[bytes]:
        fd = self._open_records(peer, identifier, create=False)
        try:
            for index in range(-(-size // CHUNK)):
                yield self._read_record(
                    fd, peer, identifier, index, min(CHUNK, size - index * CHUNK)
                )
        finally:
            os.close(fd)

    def _digest(self, peer: str, identifier: str, size: int) -> str:
        digest = hashlib.sha256()
        for record in self._records(peer, identifier, size):
            digest.update(record)
        return digest.hexdigest()

    def _unlink(self, peer: str, identifier: str) -> None:
        try:
            self._path(peer, identifier).unlink()
        except FileNotFoundError:
            pass

    def _free(self) -> int:
        return shutil.disk_usage(self.directory).free

    # Offer metadata ------------------------------------------------------------

    def _seal(self, peer: str, identifier: str, raw: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self.registry._cipher.encrypt(
            nonce, raw, (peer + ":file:" + identifier).encode()
        )

    def _open(self, peer: str, identifier: str, blob: bytes) -> bytes:
        return self.registry._cipher.decrypt(
            blob[:12], blob[12:], (peer + ":file:" + identifier).encode()
        )

    def _expire(self) -> None:
        db = self.registry.db
        for row in db.execute(
            "SELECT peer,id FROM device_files WHERE expires<=?", (self.registry.clock(),)
        ).fetchall():
            self._unlink(row["peer"], row["id"])
        db.execute("DELETE FROM device_files WHERE expires<=?", (self.registry.clock(),))
        db.execute(
            "DELETE FROM device_file_chunks WHERE NOT EXISTS "
            "(SELECT 1 FROM device_files f WHERE f.peer=device_file_chunks.peer "
            "AND f.id=device_file_chunks.id)"
        )
        self._sweep()

    def _sweep(self) -> None:
        """Remove record files whose row is gone, for example after a peer was removed."""
        keep = {
            self._path(r["peer"], r["id"]).name
            for r in self.registry.db.execute("SELECT peer,id FROM device_files")
        }
        for entry in os.scandir(self.directory):
            if entry.name.endswith(".bin") and entry.name not in keep:
                try:
                    os.unlink(entry.path)
                except FileNotFoundError:
                    pass

    def _migrate(self) -> None:
        """Move files stored by earlier versions (one sealed blob or SQLite chunks) to records."""
        db = self.registry.db
        for row in db.execute("SELECT * FROM device_files WHERE chunked=0").fetchall():
            peer, identifier = row["peer"], row["id"]
            fd = self._open_records(peer, identifier, create=True)
            try:
                os.ftruncate(fd, 0)
                if row["data"] is not None:
                    data = self._open(peer, identifier, row["data"])
                    received = len(data)
                else:
                    pieces = [
                        self._open(peer, identifier, r[0])
                        for r in db.execute(
                            "SELECT data FROM device_file_chunks WHERE peer=? AND id=? "
                            "ORDER BY offset",
                            (peer, identifier),
                        )
                    ]
                    data = b"".join(pieces)
                    if len(data) != row["received"]:
                        # A gap means the old rows cannot rebuild this transfer; drop it.
                        data, received = b"", 0
                        db.execute(
                            "UPDATE device_files SET state='failed' WHERE peer=? AND id=?",
                            (peer, identifier),
                        )
                    received = len(data)
                for index in range(-(-len(data) // CHUNK)):
                    self._write_record(
                        fd, peer, identifier, index, data[index * CHUNK : (index + 1) * CHUNK]
                    )
                os.fsync(fd)
            finally:
                os.close(fd)
            db.execute(
                "UPDATE device_files SET data=NULL,chunked=1,received=? WHERE peer=? AND id=?",
                (received, peer, identifier),
            )
            db.execute("DELETE FROM device_file_chunks WHERE peer=? AND id=?", (peer, identifier))

    # Transfers -----------------------------------------------------------------

    def _reserve(self, size: int) -> None:
        db = self.registry.db
        total = db.execute("SELECT COALESCE(SUM(size),0) FROM device_files").fetchone()[0]
        count = db.execute("SELECT COUNT(*) FROM device_files").fetchone()[0]
        if count >= MAX_FILES or total + size > MAX_TOTAL:
            raise PairingError("file_quota")
        if self._free() < stored_size(size) + FREE_SPACE_MARGIN:
            raise PairingError("file_disk_full")

    def offer(
        self, peer: str, thread: str, offer: dict[str, Any], *, limit: int = MAX_FILE
    ) -> None:
        self._expire()
        identifier = offer["file_id"]
        raw = json.dumps(offer, sort_keys=True).encode()
        existing = self.registry.db.execute(
            "SELECT * FROM device_files WHERE peer=? AND id=?", (peer, identifier)
        ).fetchone()
        if existing:
            if (
                existing["thread"] != thread
                or self._open(peer, identifier, existing["offer"]) != raw
            ):
                raise PairingError("file_offer_conflict")
            return
        now = self.registry.clock()
        if not now < offer["expires_at"] <= now + 86400 or not 0 <= offer["size"] <= limit:
            raise PairingError("file_offer_bounds")
        self._reserve(offer["size"])
        self.registry.db.execute(
            "INSERT INTO device_files VALUES (?,?,?,?,?,?,?,0,NULL,1)",
            (
                peer,
                identifier,
                thread,
                self._seal(peer, identifier, raw),
                offer["size"],
                offer["expires_at"],
                "receiving",
            ),
        )
        fd = self._open_records(peer, identifier, create=True)
        os.ftruncate(fd, 0)
        os.close(fd)

    def receive(self, peer: str, thread: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._expire()
        identifier, offset = payload["file_id"], payload["offset"]
        row = self.registry.db.execute(
            "SELECT * FROM device_files WHERE peer=? AND id=? AND thread=?",
            (peer, identifier, thread),
        ).fetchone()
        if row is None:
            raise PairingError("file_unavailable")
        try:
            text = payload["data"]
            raw = base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
            if len(raw) > CHUNK or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != text:
                raise ValueError()
        except ValueError:
            raise PairingError("file_chunk_encoding") from None
        size, received = row["size"], row["received"]
        if row["state"] == "available" or offset < received:
            # A retransmission must match what was already committed.
            if not 0 <= offset <= received or offset + len(raw) > received:
                raise PairingError("file_chunk_conflict")
            if self._slice(peer, identifier, received, offset, len(raw)) != raw:
                raise PairingError("file_chunk_conflict")
            return {"file_id": identifier, "state": row["state"], "received_size": received}
        if (
            row["state"] != "receiving"
            or offset != received
            or offset + len(raw) > size
            or (not raw and not payload["eof"])
        ):
            raise PairingError("file_chunk_offset")
        fd = self._open_records(peer, identifier, create=False)
        try:
            # The last partial record is rewritten as later bytes arrive, so tiny
            # chunks never multiply records or storage overhead.
            index, start = divmod(received, CHUNK)
            tail = self._read_record(fd, peer, identifier, index, start) if start else b""
            pending = tail + raw
            for position in range(0, len(pending), CHUNK):
                self._write_record(
                    fd,
                    peer,
                    identifier,
                    index + position // CHUNK,
                    pending[position : position + CHUNK],
                )
            received = offset + len(raw)
            state = "receiving"
            if payload["eof"]:
                offer = json.loads(self._open(peer, identifier, row["offer"]))
                if received != size or self._digest(peer, identifier, size) != offer["sha256"]:
                    raise PairingError("file_integrity")
                os.fsync(fd)
                state = "available"
        finally:
            os.close(fd)
        self.registry.db.execute(
            "UPDATE device_files SET state=?,received=? WHERE peer=? AND id=?",
            (state, received, peer, identifier),
        )
        return {"file_id": identifier, "state": state, "received_size": received}

    def _slice(self, peer: str, identifier: str, committed: int, offset: int, length: int) -> bytes:
        """Bytes from offset; committed is how many bytes the records hold."""
        if length == 0:
            return b""
        fd = self._open_records(peer, identifier, create=False)
        try:
            parts = []
            for index in range(offset // CHUNK, (offset + length - 1) // CHUNK + 1):
                parts.append(
                    self._read_record(
                        fd, peer, identifier, index, min(CHUNK, committed - index * CHUNK)
                    )
                )
        finally:
            os.close(fd)
        start = offset - (offset // CHUNK) * CHUNK
        return b"".join(parts)[start : start + length]

    def _available(self, peer: str, thread: str, identifier: str) -> Any:
        self._expire()
        row = self.registry.db.execute(
            "SELECT * FROM device_files WHERE peer=? AND id=? AND thread=? AND state='available'",
            (peer, identifier, thread),
        ).fetchone()
        if row is None:
            raise PairingError("file_unavailable")
        return row

    def metadata(self, peer: str, thread: str, identifier: str) -> dict[str, Any]:
        """The authenticated offer of a complete file, without reading its bytes."""
        row = self._available(peer, thread, identifier)
        result: dict[str, Any] = json.loads(self._open(peer, identifier, row["offer"]))
        return result

    def chunk(
        self, peer: str, thread: str, identifier: str, offset: int
    ) -> tuple[dict[str, Any], bytes]:
        """One wire chunk of a complete file, read and authenticated on its own."""
        row = self._available(peer, thread, identifier)
        size = row["size"]
        if not 0 <= offset <= size:
            raise PairingError("file_chunk_offset")
        offer = json.loads(self._open(peer, identifier, row["offer"]))
        return offer, self._slice(peer, identifier, size, offset, min(CHUNK, size - offset))

    def content(
        self, peer: str, thread: str, identifier: str, *, limit: int = MAX_FILE
    ) -> tuple[dict[str, Any], bytes]:
        """A whole complete file in memory, refused above ``limit``."""
        row = self._available(peer, thread, identifier)
        if row["size"] > limit:
            raise PairingError("file_too_large")
        offer = json.loads(self._open(peer, identifier, row["offer"]))
        data = b"".join(self._records(peer, identifier, row["size"]))
        if hashlib.sha256(data).hexdigest() != offer["sha256"]:
            raise PairingError("file_integrity")
        return offer, data

    def cancel(self, peer: str, thread: str, identifier: str) -> None:
        deleted = self.registry.db.execute(
            "DELETE FROM device_files WHERE peer=? AND id=? AND thread=?",
            (peer, identifier, thread),
        ).rowcount
        if deleted:
            self._unlink(peer, identifier)
        self._expire()

    def snapshot(
        self,
        peer: str,
        thread: str,
        root: Path,
        path: Path,
        expected_identity: list[int] | None = None,
        *,
        limit: int = MAX_FILE,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Offer a copy of ``path`` under ``root``; ``name`` replaces its file name in the offer."""
        # Reject symlinks in every relative component and keep directory descriptors
        # open through the read, so a rename cannot redirect a later path lookup.
        if (
            not hasattr(os, "O_NOFOLLOW")
            or not hasattr(os, "O_DIRECTORY")
            or os.open not in os.supports_dir_fd
        ):
            raise PairingError("scoped_file_snapshot_unsupported_platform")
        identifier = str(uuid4())
        copying = False
        try:
            relative = path.absolute().relative_to(root.resolve(strict=True))
            if not relative.parts or ".." in relative.parts:
                raise ValueError()
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                metadata = os.fstat(descriptor)
                if expected_identity is not None and expected_identity != [
                    metadata.st_dev,
                    metadata.st_ino,
                ]:
                    raise ValueError()
                for part in relative.parts[:-1]:
                    child = os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                    )
                    os.close(descriptor)
                    descriptor = child
                # A FIFO must not block open before fstat can reject it. Regular
                # files ignore O_NONBLOCK; descriptor checks still prevent races.
                source = os.open(
                    relative.parts[-1],
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=descriptor,
                )
                try:
                    info = os.fstat(source)
                    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                        raise ValueError()
                    self._expire()
                    self._reserve(info.st_size)
                    copying = True
                    size, digest = self._copy(peer, identifier, source, info.st_size)
                finally:
                    os.close(source)
            finally:
                os.close(descriptor)
        except (OSError, ValueError, NotImplementedError):
            if copying:
                self._unlink(peer, identifier)
            raise PairingError("scoped_file_unavailable") from None
        except BaseException:
            if copying:
                self._unlink(peer, identifier)
            raise
        offer: dict[str, Any] = {
            "file_id": identifier,
            "name": (name or path.name)[:160],
            "mime": "application/octet-stream",
            "size": size,
            "sha256": digest,
            "expires_at": int(self.registry.clock()) + 3600,
        }
        raw = json.dumps(offer, sort_keys=True).encode()
        self.registry.db.execute(
            "INSERT INTO device_files VALUES (?,?,?,?,?,?,'available',?,NULL,1)",
            (
                peer,
                identifier,
                thread,
                self._seal(peer, identifier, raw),
                size,
                offer["expires_at"],
                size,
            ),
        )
        return offer

    def _copy(self, peer: str, identifier: str, source: int, expected: int) -> tuple[int, str]:
        """Seal a source descriptor into records, reading one record at a time."""
        digest = hashlib.sha256()
        size = 0
        fd = self._open_records(peer, identifier, create=True)
        try:
            os.ftruncate(fd, 0)
            index = 0
            while True:
                raw = b""
                while len(raw) < CHUNK:
                    piece = os.read(source, CHUNK - len(raw))
                    if not piece:
                        break
                    raw += piece
                if not raw:
                    break
                size += len(raw)
                if size > expected:
                    # The file grew while it was copied; the snapshot must be immutable.
                    raise ValueError()
                digest.update(raw)
                self._write_record(fd, peer, identifier, index, raw)
                index += 1
                if len(raw) < CHUNK:
                    break
            if size != expected:
                raise ValueError()
            os.fsync(fd)
        finally:
            os.close(fd)
        return size, digest.hexdigest()
