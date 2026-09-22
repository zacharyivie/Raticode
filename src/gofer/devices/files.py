"""Scoped immutable file snapshots carried inside the authenticated TLS stream."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import stat
from pathlib import Path
from typing import Any
from uuid import uuid4

from gofer.devices.registry import DeviceRegistry, PairingError

MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 50 * 1024 * 1024
CHUNK = 16 * 1024


class DeviceFiles:
    def __init__(self, registry: DeviceRegistry) -> None:
        self.registry = registry
        registry.db.executescript("""
            CREATE TABLE IF NOT EXISTS device_files (
              peer TEXT NOT NULL,id TEXT NOT NULL,thread TEXT NOT NULL,offer BLOB NOT NULL,
              size INTEGER NOT NULL,expires REAL NOT NULL,state TEXT NOT NULL,
              received INTEGER NOT NULL,data BLOB,PRIMARY KEY(peer,id));
            CREATE TABLE IF NOT EXISTS device_file_chunks (
              peer TEXT NOT NULL,id TEXT NOT NULL,offset INTEGER NOT NULL,data BLOB NOT NULL,
              PRIMARY KEY(peer,id,offset));
        """)

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
        self.registry.db.execute(
            "DELETE FROM device_files WHERE expires<=?", (self.registry.clock(),)
        )
        self.registry.db.execute(
            "DELETE FROM device_file_chunks WHERE NOT EXISTS "
            "(SELECT 1 FROM device_files f WHERE f.peer=device_file_chunks.peer "
            "AND f.id=device_file_chunks.id)"
        )

    def offer(self, peer: str, thread: str, offer: dict[str, Any]) -> None:
        import json

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
        if not now < offer["expires_at"] <= now + 86400 or not 0 <= offer["size"] <= MAX_FILE:
            raise PairingError("file_offer_bounds")
        total = self.registry.db.execute(
            "SELECT COALESCE(SUM(size),0) FROM device_files"
        ).fetchone()[0]
        count = self.registry.db.execute("SELECT COUNT(*) FROM device_files").fetchone()[0]
        if count >= 128 or total + offer["size"] > MAX_TOTAL:
            raise PairingError("file_quota")
        self.registry.db.execute(
            "INSERT INTO device_files VALUES (?,?,?,?,?,?,?,0,NULL)",
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
        if row["state"] == "available":
            complete = self._open(peer, identifier, row["data"])
            if (
                not 0 <= offset <= len(complete)
                or complete[offset : offset + len(raw)] != raw
                or offset + len(raw) > len(complete)
            ):
                raise PairingError("file_chunk_conflict")
            return {"file_id": identifier, "state": "available", "received_size": len(complete)}
        if offset < row["received"]:
            previous = self.registry.db.execute(
                "SELECT data FROM device_file_chunks WHERE peer=? AND id=? AND offset=?",
                (peer, identifier, offset),
            ).fetchone()
            if previous is None or self._open(peer, identifier, previous[0]) != raw:
                raise PairingError("file_chunk_conflict")
            return {"file_id": identifier, "state": row["state"], "received_size": row["received"]}
        if (
            row["state"] != "receiving"
            or offset != row["received"]
            or offset + len(raw) > row["size"]
            or (not raw and not payload["eof"])
        ):
            raise PairingError("file_chunk_offset")
        self.registry.db.execute(
            "INSERT INTO device_file_chunks VALUES (?,?,?,?)",
            (peer, identifier, offset, self._seal(peer, identifier, raw)),
        )
        received = offset + len(raw)
        state = "receiving"
        if payload["eof"]:
            import json

            chunks = [
                self._open(peer, identifier, r[0])
                for r in self.registry.db.execute(
                    "SELECT data FROM device_file_chunks WHERE peer=? AND id=? ORDER BY offset",
                    (peer, identifier),
                )
            ]
            complete = b"".join(chunks)
            offer = json.loads(self._open(peer, identifier, row["offer"]))
            if received != row["size"] or hashlib.sha256(complete).hexdigest() != offer["sha256"]:
                raise PairingError("file_integrity")
            state = "available"
            self.registry.db.execute(
                "UPDATE device_files SET data=? WHERE peer=? AND id=?",
                (self._seal(peer, identifier, complete), peer, identifier),
            )
        if state == "available":
            self.registry.db.execute(
                "DELETE FROM device_file_chunks WHERE peer=? AND id=?", (peer, identifier)
            )
        self.registry.db.execute(
            "UPDATE device_files SET state=?,received=? WHERE peer=? AND id=?",
            (state, received, peer, identifier),
        )
        return {"file_id": identifier, "state": state, "received_size": received}

    def content(self, peer: str, thread: str, identifier: str) -> tuple[dict[str, Any], bytes]:
        import json

        self._expire()
        row = self.registry.db.execute(
            "SELECT * FROM device_files WHERE peer=? AND id=? AND thread=? AND state='available'",
            (peer, identifier, thread),
        ).fetchone()
        if row is None:
            raise PairingError("file_unavailable")
        return json.loads(self._open(peer, identifier, row["offer"])), self._open(
            peer, identifier, row["data"]
        )

    def cancel(self, peer: str, thread: str, identifier: str) -> None:
        self.registry.db.execute(
            "DELETE FROM device_files WHERE peer=? AND id=? AND thread=?",
            (peer, identifier, thread),
        )
        self._expire()

    def snapshot(
        self,
        peer: str,
        thread: str,
        root: Path,
        path: Path,
        expected_identity: list[int] | None = None,
    ) -> dict[str, Any]:
        # Reject symlinks in every relative component and keep directory descriptors
        # open through the read, so a rename cannot redirect a later path lookup.
        if (
            not hasattr(os, "O_NOFOLLOW")
            or not hasattr(os, "O_DIRECTORY")
            or os.open not in os.supports_dir_fd
        ):
            raise PairingError("scoped_file_snapshot_unsupported_platform")
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
                    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE:
                        raise ValueError()
                    with os.fdopen(source, "rb", closefd=False) as handle:
                        data = handle.read(MAX_FILE + 1)
                    if len(data) > MAX_FILE:
                        raise ValueError()
                finally:
                    os.close(source)
            finally:
                os.close(descriptor)
        except (OSError, ValueError, NotImplementedError):
            raise PairingError("scoped_file_unavailable") from None
        offer: dict[str, Any] = {
            "file_id": str(uuid4()),
            "name": path.name[:160],
            "mime": "application/octet-stream",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "expires_at": int(self.registry.clock()) + 3600,
        }
        self.offer(peer, thread, offer)
        self.registry.db.execute(
            "UPDATE device_files SET data=?,received=?,state='available' WHERE peer=? AND id=?",
            (self._seal(peer, offer["file_id"], data), len(data), peer, offer["file_id"]),
        )
        return offer
