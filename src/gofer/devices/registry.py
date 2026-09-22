"""Transactional device trust, enrollment recovery and revocation authority."""

from __future__ import annotations

import builtins
import json
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from gofer.devices.storage import Identity, SecretStore, StorageError, account_for

MAX_PEERS = 128
# Enrollment itself grants no execution authority; thread grants are local-only.
COMMUNICATION: tuple[str, ...] = ()


class PairingError(ValueError):
    """Safe error code, never include supplied records or secrets."""


def device_id(value: Any) -> str:
    if not isinstance(value, str):
        raise PairingError("invalid_device_id")
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except ValueError:
        raise PairingError("invalid_device_id") from None
    return value


class DeviceRegistry:
    def __init__(
        self,
        directory: Path,
        store: SecretStore,
        *,
        initialize: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.clock = clock
        self.lock = threading.RLock()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / "devices.sqlite3"
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        try:
            path.chmod(0o600)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.executescript("""
              CREATE TABLE IF NOT EXISTS identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                device_id TEXT NOT NULL, pin BLOB NOT NULL);
              CREATE TABLE IF NOT EXISTS peers (
                device_id TEXT PRIMARY KEY, pin BLOB UNIQUE NOT NULL,
                role TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL,
                created REAL NOT NULL, last_seen REAL, expires REAL, routes BLOB,
                capabilities TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS endpoints (
                device_id TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS config (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS relay_origins (peer TEXT PRIMARY KEY, origin TEXT NOT NULL,
                outbound INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS device_workspace_peers (peer TEXT PRIMARY KEY);
            """)
            columns = {r[1] for r in self.db.execute("PRAGMA table_info(peers)")}
            if "generation" not in columns:
                self.db.execute("ALTER TABLE peers ADD COLUMN generation TEXT NOT NULL DEFAULT ''")
            with self.transaction():
                for peer_row in self.db.execute(
                    "SELECT device_id FROM peers WHERE generation=''"
                ).fetchall():
                    self.db.execute(
                        "UPDATE peers SET generation=? WHERE device_id=?",
                        (str(uuid4()), peer_row[0]),
                    )
                row = self.db.execute("SELECT * FROM identity").fetchone()
                raw = store.get(account_for(directory))
                if raw is None:
                    if row is not None or not initialize:
                        raise StorageError("identity_missing_repair_required")
                    identity = Identity.create()
                    store.put(account_for(directory), identity.encode())
                else:
                    identity = Identity.decode(raw)
                if row is not None and (
                    row["device_id"] != identity.device_id or row["pin"] != identity.pin
                ):
                    raise StorageError("identity_mismatch_repair_required")
                if row is None:
                    self.db.execute(
                        "INSERT INTO identity VALUES (1,?,?)", (identity.device_id, identity.pin)
                    )
                self.identity = identity
                self._cipher = AESGCM(identity.storage_key)
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def _row(self, peer: str) -> sqlite3.Row:
        row = self.db.execute(
            "SELECT * FROM peers WHERE device_id=?", (device_id(peer),)
        ).fetchone()
        if not isinstance(row, sqlite3.Row):
            raise PairingError("peer_unavailable")
        return row

    def propose(
        self, peer: str, pin: bytes, role: str, expires: float, *, outbound: bool = False
    ) -> None:
        device_id(peer)
        if (
            len(pin) != 32
            or role not in ("controller", "desktop")
            or peer == self.identity.device_id
        ):
            raise PairingError("invalid_peer")
        with self.transaction():
            self.db.execute(
                "DELETE FROM peers WHERE state IN ('pending','outbound_pending') AND expires<=?",
                (self.clock(),),
            )
            self.db.execute(
                "DELETE FROM endpoints WHERE device_id NOT IN (SELECT device_id FROM peers)"
            )
            if self.db.execute("SELECT COUNT(*) FROM peers").fetchone()[0] >= MAX_PEERS:
                raise PairingError("peer_quota")
            try:
                self.db.execute(
                    "INSERT INTO peers (device_id,pin,role,state,revision,created,last_seen,"
                    "expires,routes,capabilities,generation) "
                    "VALUES (?,?,?,?,0,?,NULL,?,NULL,'[]',?)",
                    (
                        peer,
                        pin,
                        role,
                        "outbound_pending" if outbound else "pending",
                        self.clock(),
                        expires,
                        str(uuid4()),
                    ),
                )
            except sqlite3.IntegrityError:
                raise PairingError("peer_conflict") from None

    def confirm(self, peer: str, pin_hex: str) -> None:
        # Local-only caller must display the exact candidate identity before this action.
        with self.transaction():
            row = self._row(peer)
            if row["state"] != "pending" or row["expires"] <= self.clock():
                raise PairingError("enrollment_expired")
            if not secrets.compare_digest(row["pin"].hex(), pin_hex):
                raise PairingError("identity_changed")
            routes = json.dumps(
                {
                    "initiator_topic": secrets.token_urlsafe(32),
                    "responder_topic": secrets.token_urlsafe(32),
                }
            ).encode()
            nonce = secrets.token_bytes(12)
            encrypted = nonce + self._cipher.encrypt(nonce, routes, peer.encode())
            self.db.execute(
                "UPDATE peers SET state='confirmed',revision=1,expires=NULL,"
                "routes=?,capabilities=? WHERE device_id=?",
                (encrypted, json.dumps(COMMUNICATION), peer),
            )
            # Local confirmation pairs the owner's phone with their desktop workspace.
            # The workspace exposes it only after the authenticated ready acknowledgment.
            if row["role"] == "controller":
                self.db.execute("INSERT OR IGNORE INTO device_workspace_peers VALUES (?)", (peer,))

    def trusted_pins(self) -> frozenset[bytes]:
        with self.lock:
            return frozenset(
                row[0]
                for row in self.db.execute(
                    "SELECT pin FROM peers WHERE state IN ('confirmed','active')"
                )
            )

    def generation(self, peer: str) -> str:
        with self.lock:
            return str(self._row(peer)["generation"])

    def check_generation(self, peer: str, generation: str | None) -> None:
        if generation is None or self.generation(peer) != generation:
            raise PairingError("pairing_changed")

    def authenticate(self, peer: str, pin: bytes, role: str) -> None:
        with self.lock:
            row = self._row(peer)
            if (
                row["state"] not in ("confirmed", "active")
                or row["role"] != role
                or not secrets.compare_digest(row["pin"], pin)
            ):
                raise PairingError("peer_unavailable")

    def ready(self, peer: str, pin: bytes, role: str) -> dict[str, Any]:
        with self.lock:
            self.authenticate(peer, pin, role)
            row = self._row(peer)
            try:
                blob = row["routes"]
                routes = json.loads(self._cipher.decrypt(blob[:12], blob[12:], peer.encode()))
            except (InvalidTag, ValueError, TypeError):
                raise StorageError("protected_routes_invalid") from None
            return {
                "type": "session.ready",
                "device_id": peer,
                "role": role,
                "capabilities": json.loads(row["capabilities"]),
                **routes,
                "trust_revision": row["revision"],
            }

    def acknowledge(self, peer: str, pin: bytes, role: str, record: dict[str, Any]) -> None:
        with self.transaction():
            self.authenticate(peer, pin, role)
            row = self._row(peer)
            if (
                record
                != {
                    "type": "session.ready_ack",
                    "device_id": peer,
                    "trust_revision": row["revision"],
                }
                or type(record.get("trust_revision")) is not int
            ):
                raise PairingError("invalid_ready_ack")
            self.db.execute(
                "UPDATE peers SET state='active',last_seen=? WHERE device_id=?",
                (self.clock(), peer),
            )

    def revoke(self, peer: str) -> None:
        with self.transaction():
            self._row(peer)
            # Keep tombstones and pin to prevent a queued invitation from restoring trust.
            self.db.execute("DELETE FROM endpoints WHERE device_id=?", (peer,))
            self.db.execute(
                "UPDATE peers SET state='revoked',revision=revision+1,routes=NULL,"
                "capabilities='[]',expires=NULL WHERE device_id=?",
                (peer,),
            )

    def list(self) -> list[dict[str, Any]]:
        with self.lock:
            return [
                {
                    "device_id": r["device_id"],
                    "fingerprint": r["pin"].hex(),
                    "role": r["role"],
                    "state": "expired"
                    if r["state"] in ("pending", "outbound_pending")
                    and r["expires"] <= self.clock()
                    else r["state"],
                    "trust_revision": r["revision"],
                    "created_at": r["created"],
                    "expires_at": r["expires"],
                    "last_seen": r["last_seen"],
                    "capabilities": json.loads(r["capabilities"]),
                }
                for r in self.db.execute("SELECT * FROM peers ORDER BY created")
            ]

    def accept_remote(self, peer: str, pin: bytes, record: dict[str, Any]) -> None:
        """Persist a QR-pinned desktop's ready before sending its acknowledgment.

        Called only by the local-consent outbound enrollment, with TLS plaintext.
        Never accept worker or approval grants through pairing.
        """
        import re

        fields = {
            "type",
            "device_id",
            "role",
            "capabilities",
            "initiator_topic",
            "responder_topic",
            "trust_revision",
        }
        caps = record.get("capabilities")
        if (
            set(record) != fields
            or record["type"] != "session.ready"
            or record["device_id"] != self.identity.device_id
            or record["role"] != "desktop"
            or not isinstance(caps, list)
            or caps != []
            or type(record["trust_revision"]) is not int
            or record["trust_revision"] != 1
        ):
            raise PairingError("invalid_remote_ready")
        for key in ("initiator_topic", "responder_topic"):
            if not isinstance(record[key], str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{43}", record[key]
            ):
                raise PairingError("invalid_remote_ready")
        with self.transaction():
            row = self._row(peer)
            if (
                row["state"] != "outbound_pending"
                or row["expires"] <= self.clock()
                or row["pin"] != pin
                or row["role"] != "desktop"
            ):
                raise PairingError("peer_unavailable")
            routes = json.dumps(
                {key: record[key] for key in ("initiator_topic", "responder_topic")}
            ).encode()
            nonce = secrets.token_bytes(12)
            encrypted = nonce + self._cipher.encrypt(nonce, routes, peer.encode())
            self.db.execute(
                "UPDATE peers SET state='active',revision=1,expires=NULL,routes=?,"
                "capabilities=?,last_seen=? WHERE device_id=?",
                (encrypted, json.dumps(caps), self.clock(), peer),
            )

    def reconnect_pin(self, peer: str) -> bytes:
        """Recover only a previously locally approved QR pin or confirmed peer."""
        with self.lock:
            row = self._row(peer)
            if row["state"] not in ("outbound_pending", "active", "confirmed"):
                raise PairingError("peer_unavailable")
            if row["state"] == "outbound_pending" and row["expires"] <= self.clock():
                raise PairingError("enrollment_expired")
            return bytes(row["pin"])

    def recover_remote(self, peer: str, pin: bytes, record: dict[str, Any]) -> None:
        with self.lock:
            row = self._row(peer)
            if row["state"] == "outbound_pending":
                self.accept_remote(peer, pin, record)
            else:
                # The remote ready describes this initiator, not the responder.
                expected = self.ready(peer, pin, "desktop") | {"device_id": self.identity.device_id}
                if record != expected or type(record.get("trust_revision")) is not int:
                    raise PairingError("remote_trust_conflict")

    def save_endpoint(self, peer: str, endpoint: dict[str, Any]) -> None:
        from gofer.devices.pairing import lan_endpoint

        value = json.dumps(lan_endpoint(endpoint))
        with self.transaction():
            self.reconnect_pin(peer)
            self.db.execute("INSERT OR REPLACE INTO endpoints VALUES (?,?)", (peer, value))

    def endpoint(self, peer: str) -> dict[str, Any]:
        with self.lock:
            self.reconnect_pin(peer)
            row = self.db.execute(
                "SELECT value FROM endpoints WHERE device_id=?", (peer,)
            ).fetchone()
            if row is None:
                raise PairingError("peer_endpoint_unknown")
            result: dict[str, Any] = json.loads(row[0])
            return result

    def listener_port(self) -> int:
        with self.lock:
            row = self.db.execute("SELECT value FROM config WHERE name='listener_port'").fetchone()
            return int(row[0]) if row is not None else 0

    def save_listener_port(self, port: int) -> None:
        with self.transaction():
            self.db.execute("INSERT OR REPLACE INTO config VALUES ('listener_port',?)", (port,))

    def save_relay(self, peer: str, origin: str, *, outbound: bool = False) -> None:
        from gofer.devices.pairing import relay_origin

        checked = relay_origin(origin)
        with self.transaction():
            self._row(peer)
            self.db.execute(
                "INSERT OR REPLACE INTO relay_origins VALUES (?,?,?)",
                (peer, checked, int(outbound)),
            )

    def relay_channels(self) -> builtins.list[dict[str, Any]]:
        with self.lock:
            result = []
            for row in self.db.execute(
                "SELECT p.*,r.origin,r.outbound FROM peers p JOIN relay_origins r ON "
                "r.peer=p.device_id WHERE p.state IN ('confirmed','active')"
            ):
                ready = self.ready(row["device_id"], row["pin"], row["role"])
                result.append(
                    {
                        "peer": row["device_id"],
                        "origin": row["origin"],
                        "outbound": bool(row["outbound"]),
                        "receive": ready["initiator_topic"]
                        if row["outbound"]
                        else ready["responder_topic"],
                        "send": ready["responder_topic"]
                        if row["outbound"]
                        else ready["initiator_topic"],
                    }
                )
            return result
