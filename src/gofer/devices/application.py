"""Durable application inbox and outbox shared by every authenticated carrier.

Pairing grants communication only. Local thread grants select execution context;
remote events cannot supply paths, provider credentials, or permission modes.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import logging
import secrets
import socket
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from jsonschema import Draft202012Validator, ValidationError  # type: ignore[import-untyped]

from gofer.devices.files import DeviceFiles
from gofer.devices.organization_jobs import event_schema
from gofer.devices.registry import (
    DEFAULT_DESKTOP_NAME,
    DeviceRegistry,
    PairingError,
    device_id,
    display_name,
)

logger = logging.getLogger(__name__)
_SCHEMA = json.loads((Path(__file__).parent / "protocol/v2/event.schema.json").read_text())


_LOAD_READING = {
    "type": "object",
    "properties": {
        "max_threads": {"type": "integer", "minimum": 1, "maximum": 16},
        "running_threads": {"type": "integer", "minimum": 0, "maximum": 1000},
        "cpu_pressure": {
            "oneOf": [{"type": "number", "minimum": 0, "maximum": 100}, {"type": "null"}]
        },
        "memory_pressure": {
            "oneOf": [{"type": "number", "minimum": 0, "maximum": 100}, {"type": "null"}]
        },
        # Sent only to a desktop that asked with ``memory_hold: true`` (see _desktop_schema).
        "memory_hold": {
            "oneOf": [{"type": "string", "minLength": 1, "maxLength": 80}, {"type": "null"}]
        },
        # Sent only to a desktop that asked with ``running_by_kind: true``: counts only,
        # never what the workers are.
        "running_by_kind": {
            "type": "object",
            "properties": {
                kind: {"type": "integer", "minimum": 0, "maximum": 1000}
                for kind in ("rem", "phone", "organizations", "swarms")
            },
            "additionalProperties": False,
        },
    },
    "required": ["max_threads", "running_threads", "cpu_pressure", "memory_pressure"],
    "additionalProperties": False,
}


# A project's repository fingerprint (gofer.core.repository_fingerprint), or null when
# its folder isn't a git repository. Never a path.
_FINGERPRINT = {"oneOf": [{"type": "string", "pattern": "^[0-9a-f]{64}$"}, {"type": "null"}]}
# How long a paired desktop's catalog answer counts for placement; it is asked every
# FLEET_REFRESH (30 s) seconds with the fleet status.
CATALOG_FRESH = 120.0
# How long before asking again a desktop that refused the fingerprint request.
CATALOG_RETRY = 600.0
# How many Rem tasks one paired desktop may have queued or running here at once. The
# next one it hands over is refused, with a reason that desktop shows on the task.
REM_TASKS_PER_DESKTOP = 10


def _desktop_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Desktop-only fleet extension: a paired desktop announces its own listener.

    It is sent last in a session, after the answer arrives, so an older desktop
    that rejects the field still answers the request first.

    An announcement with ``load: true`` also asks for the answering desktop's own
    worker limit, running workers and pressure (gofer.core.computers), which it adds
    as ``load`` to its own entry in the fleet.snapshot reply. Only desktops send
    announcements, so phones never receive the field, and an older desktop that
    refuses ``load`` is sent plain announcements from then on.

    With ``memory_hold: true`` as well, the reading also carries ``memory_hold``: why
    the answering desktop is short of memory with a worker running (such as "0.9 GB
    available"), or null, so a held desktop gets none of the asker's work. A desktop
    asked without it never sends the field, as an older asker's schema has none. A
    desktop that refuses ``memory_hold`` (one from before it) is asked for ``load``
    alone from then on, and if it refuses that too, sent plain announcements.

    With ``running_by_kind: true`` as well, the reading also counts its running workers
    by kind (rem, phone, organizations, swarms), never naming them, for Settings ›
    Computers. A desktop asked without it never sends the field. A desktop that refuses
    it (one from before it) is asked for ``load`` and ``memory_hold`` from then on.

    A catalog.request with ``fingerprints: true`` asks for each project's repository
    fingerprint, which the answer adds as ``fingerprint`` to each catalog project, so
    placement can match a project here to the same repository there
    (gofer.core.placement_eligibility). Only desktops ask, and only desktops get the
    field. An older desktop refuses the request, and is listed as needing an update.
    """
    for rule in schema["allOf"]:
        kind = rule.get("if", {}).get("properties", {}).get("type", {}).get("const")
        if kind == "catalog.request":
            rule["then"]["properties"]["payload"]["properties"]["fingerprints"] = {"const": True}
        if kind == "catalog.snapshot":
            projects = rule["then"]["properties"]["payload"]["properties"]["projects"]
            projects["items"]["properties"]["fingerprint"] = _FINGERPRINT
        if (
            rule.get("if", {}).get("properties", {}).get("type", {}).get("const")
            == "fleet.snapshot"
        ):
            devices = rule["then"]["properties"]["payload"]["properties"]["devices"]
            devices["items"]["properties"]["load"] = _LOAD_READING
        if rule.get("if", {}).get("properties", {}).get("type", {}).get("const") == "fleet.request":
            rule["then"]["properties"]["payload"]["properties"]["advertise"] = {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 80},
                    "lan_endpoint": {
                        "oneOf": [
                            {
                                "type": "object",
                                "properties": {
                                    "host": {"type": "string", "minLength": 1, "maxLength": 253},
                                    "port": {"type": "integer", "minimum": 1, "maximum": 65535},
                                },
                                "required": ["host", "port"],
                                "additionalProperties": False,
                            },
                            {"type": "null"},
                        ]
                    },
                },
                "required": ["name", "lan_endpoint"],
                "additionalProperties": False,
            }
            advertised = rule["then"]["properties"]["payload"]["properties"]["advertise"]
            advertised["properties"]["load"] = {"const": True}
            advertised["properties"]["memory_hold"] = {"const": True}
            advertised["properties"]["running_by_kind"] = {"const": True}
    return schema


_VALIDATOR = Draft202012Validator(_desktop_schema(event_schema(_SCHEMA)))
_LOAD_VALIDATOR = Draft202012Validator(_LOAD_READING)
_EVENT_TYPES = frozenset(_SCHEMA["properties"]["type"]["enum"])
# Refusals that still end the session: the phone is no longer trusted, or there is no
# room to answer. A full outbox recovers when the phone's next sync acknowledges events.
_SESSION_ENDING = frozenset({"peer_unavailable", "outbox_quota", "outbox_byte_quota"})
# Refusals that cannot be stored under the request's event ID, so each resend is
# answered again until the phone drops the request.
_UNRECORDED = frozenset(
    {"event_id_conflict", "sequence_conflict", "inbox_quota", "invalid_application_event"}
)
_NOT_ALLOWED = frozenset(
    {
        "mobile_is_not_worker",
        "originating_request_not_authorized",
        "project_not_open",
        "file_not_in_open_desktop_project",
        "thread_hosting_disabled",
        "workspace_sharing_disabled",
    }
)
_MALFORMED = frozenset(
    {"file_chunk_encoding", "file_chunk_offset", "file_offer_bounds", "file_integrity"}
)


def _refusal(reason: str) -> tuple[str, str]:
    """The protocol error code and the sentence a phone shows for a refused request."""
    if reason == "invalid_application_event":
        return "unsupported", (
            "This desktop can't handle that request. Update Raticode on your computer, "
            "then try again."
        )
    if reason in ("event_id_conflict", "sequence_conflict"):
        return "conflict", (
            "The desktop already has a different request with this number. Send it again."
        )
    if reason in ("phone_locked", "suspicious_request"):
        from gofer.devices.phone_guard import LOCKED_DETAIL

        return "unauthorized", LOCKED_DETAIL
    if reason == "file_unavailable":
        return "not_found", (
            "This file is no longer available on the desktop. Ask Rem to send it again."
        )
    if reason.endswith("_conflict"):
        return "conflict", (
            "This request conflicts with one the desktop already has. Refresh, then try again."
        )
    if reason.endswith("_unavailable") or reason == "work_not_running":
        return "not_found", (
            "The desktop no longer has what this request refers to. Refresh, then try again."
        )
    if reason.endswith("_quota") or reason in ("file_too_large", "file_disk_full"):
        return "too_large", (
            "The desktop has no room for this request. Free some space or let other work "
            "finish, then try again."
        )
    if reason.endswith("_required") or reason in _NOT_ALLOWED:
        return "unauthorized", (
            "This phone isn't allowed to do that. Check its access in Paired devices on "
            "your computer."
        )
    if reason.startswith("invalid_") or reason in _MALFORMED:
        return "invalid_request", "The desktop couldn't use this request. Try again."
    return "internal", "The desktop couldn't complete this request. Try again."


OUTBOX_EVENTS = 10000
OUTBOX_BYTES = 64 * 1024 * 1024
RETAIN_ACKNOWLEDGED = 400
# Polling requests change no state, so their replay records are a bounded recent
# window instead of lifetime evidence. Otherwise a phone syncing in the background
# would exhaust the inbox quota after a few days.
READ_ONLY_KINDS = frozenset(
    {
        "sync.request",
        "catalog.request",
        "thread.history.request",
        "thread.search.request",
        "fleet.request",
        "swarm.request",
        "approval.request",
        "desktop.read",
    }
)
RECENT_READS = 2048


def incoming_job(event: dict[str, Any]) -> bool:
    """Whether a request is work another desktop's organization sent."""
    payload = event.get("payload") or {}
    return bool(event.get("type") == "job.submit" and payload.get("organization"))


def _bridge_starts_incoming(event: dict[str, Any]) -> bool:
    """Incoming work the chat bridge starts: never a handed-over Rem task, which Rem runs."""
    return incoming_job(event) and "rem_task" not in event["payload"]


def host_template(peer: str) -> str:
    """The grant a paired desktop's remote threads are created from."""
    return str(uuid5(NAMESPACE_URL, "raticode-thread-host:" + peer))


class DeviceApplication:
    lan_endpoint: dict[str, Any] | None = None
    # Set only while the user's Tailscale opt-in is listening.
    tailnet_endpoint: dict[str, Any] | None = None
    # False while the opt-in is on but not listening yet. The field is then left out,
    # because a null tells phones to forget the address.
    tailnet_known: bool = True
    # Includes loopback, which only another desktop on this machine can use.
    listener_endpoint: dict[str, Any] | None = None

    def __init__(self, registry: DeviceRegistry) -> None:
        self.registry = registry
        self.name = display_name(socket.gethostname()) or DEFAULT_DESKTOP_NAME
        self.rem_ready: Callable[[], bool] = lambda: False
        # Announcements that asked a desktop for its load reading, by request ID, with
        # how much they asked for ("kinds": the load, memory hold and counts by kind;
        # "hold": the load and memory hold; "load": the load alone), and the desktops
        # that refused one (older versions): those refusing the counts are asked for
        # the load and hold, those refusing the hold for the load alone, and those
        # refusing the load get plain announcements.
        self._load_requests: dict[str, tuple[str, str]] = {}
        self._hold_only_announcements: set[str] = set()
        self._load_only_announcements: set[str] = set()
        self._plain_announcements: set[str] = set()
        # Catalog requests that asked a desktop for its fingerprints, by request ID, and
        # each desktop's last answer for placement (see catalog_probe).
        self._catalog_requests: dict[str, str] = {}
        self._peer_catalogs: dict[str, dict[str, Any]] = {}
        # Organization jobs whose sender was told why they wait here, by (peer, request),
        # with what it was told. After a restart a waiting job is announced once more.
        self._waiting_told: dict[tuple[str, str], str] = {}
        # The last reason each handed-over Rem task's sender was told it waits for.
        self._rem_waiting: dict[tuple[str, str], str] = {}
        with registry.lock:
            registry.db.execute(
                "CREATE TABLE IF NOT EXISTS device_thread_requests ("
                "peer TEXT NOT NULL, request TEXT NOT NULL, digest TEXT NOT NULL, "
                "thread TEXT NOT NULL, PRIMARY KEY(peer,request))"
            )
        self.local_jobs: Any = None
        self.local_chat_jobs: Any = None
        with registry.lock:
            registry.db.executescript("""
              CREATE TABLE IF NOT EXISTS device_grants (
                peer TEXT NOT NULL, thread TEXT NOT NULL, context BLOB NOT NULL,
                PRIMARY KEY(peer,thread));
              CREATE TABLE IF NOT EXISTS device_inbox (
                peer TEXT NOT NULL,id TEXT NOT NULL,digest TEXT NOT NULL,sequence INTEGER NOT NULL,
                PRIMARY KEY(peer,id),UNIQUE(peer,sequence));
              CREATE TABLE IF NOT EXISTS device_inbox_reads (
                peer TEXT NOT NULL,id TEXT NOT NULL,digest TEXT NOT NULL,sequence INTEGER NOT NULL,
                PRIMARY KEY(peer,id),UNIQUE(peer,sequence));
              CREATE TABLE IF NOT EXISTS device_work (
                peer TEXT NOT NULL,request TEXT NOT NULL,thread TEXT NOT NULL,
                event BLOB NOT NULL,state TEXT NOT NULL,turn TEXT NOT NULL,
                incoming INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(peer,request));
              CREATE TABLE IF NOT EXISTS device_message_attachments (
                peer TEXT NOT NULL, request TEXT NOT NULL, value BLOB NOT NULL,
                PRIMARY KEY(peer,request));
              CREATE TABLE IF NOT EXISTS device_transcript (
                peer TEXT NOT NULL,request TEXT NOT NULL,thread TEXT NOT NULL,result BLOB NOT NULL,
                PRIMARY KEY(peer,request));
              CREATE TABLE IF NOT EXISTS device_sequences (
                peer TEXT PRIMARY KEY,value INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS device_chunk_receipts (
                peer TEXT NOT NULL,id TEXT NOT NULL,sequence INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                PRIMARY KEY(peer,sequence));
              CREATE TABLE IF NOT EXISTS device_job_ids (
                peer TEXT NOT NULL,id TEXT NOT NULL,request TEXT NOT NULL,digest TEXT NOT NULL,
                PRIMARY KEY(peer,id));
              CREATE TABLE IF NOT EXISTS device_file_sends (
                peer TEXT NOT NULL,id TEXT NOT NULL,thread TEXT NOT NULL,request TEXT NOT NULL,
                PRIMARY KEY(peer,id));
              CREATE TABLE IF NOT EXISTS device_remote_requests (
                peer TEXT NOT NULL, request TEXT NOT NULL,event BLOB NOT NULL,state TEXT NOT NULL,
                PRIMARY KEY(peer,request));
              CREATE TABLE IF NOT EXISTS device_remote_cancels (
                peer TEXT NOT NULL, request TEXT NOT NULL, event BLOB NOT NULL,
                PRIMARY KEY(peer,request));
              CREATE TABLE IF NOT EXISTS device_remote_results (
                peer TEXT NOT NULL, id TEXT NOT NULL,event BLOB NOT NULL,
                PRIMARY KEY(peer,id));
              CREATE TABLE IF NOT EXISTS device_outbox (
                sequence INTEGER NOT NULL,peer TEXT NOT NULL,event BLOB NOT NULL,
                PRIMARY KEY(peer,sequence));
              CREATE TABLE IF NOT EXISTS device_thread_hosts (peer TEXT PRIMARY KEY);
              CREATE TABLE IF NOT EXISTS device_remote_threads (
                peer TEXT NOT NULL, local TEXT NOT NULL, remote TEXT NOT NULL,
                PRIMARY KEY(peer,local));
              CREATE TABLE IF NOT EXISTS device_remote_cursors (
                peer TEXT PRIMARY KEY, value INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS device_peer_snapshots (
                peer TEXT NOT NULL, kind TEXT NOT NULL, sequence INTEGER NOT NULL,
                event BLOB NOT NULL, PRIMARY KEY(peer,kind));
              CREATE TABLE IF NOT EXISTS device_rem_tasks (
                peer TEXT NOT NULL, request TEXT NOT NULL, run_id TEXT,
                settled INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(peer,request));
            """)
        self.files = DeviceFiles(registry)
        with registry.transaction():
            self._mark_incoming_work()
            # A Rem task a paired desktop handed over runs as this desktop's own Rem
            # run, which survives a restart, so it keeps running (gofer.ui.rem_remote_tasks).
            handed_over = (
                "NOT EXISTS (SELECT 1 FROM device_rem_tasks r WHERE r.peer=device_work.peer "
                "AND r.request=device_work.request)"
            )
            for row in registry.db.execute(
                f"SELECT * FROM device_work WHERE state='running' AND {handed_over}"
            ).fetchall():
                self._status(
                    row["peer"],
                    self._decode(row["peer"], row["event"]),
                    "outcome_unknown",
                    "Desktop stopped during dispatch; review locally before retrying",
                )
            registry.db.execute(
                f"UPDATE device_work SET state='outcome_unknown' WHERE state='running' "
                f"AND {handed_over}"
            )

        from gofer.devices.workspace import DeviceWorkspace

        self.workspace = DeviceWorkspace(self)
        from gofer.devices.remote_control import RemoteControl

        self.remote = RemoteControl(self)
        from gofer.devices.desktop_control import DesktopControl

        self.desktop = DesktopControl(self)
        from gofer.devices.phone_guard import PhoneLocks

        self.locks = PhoneLocks(self)

    def _mark_incoming_work(self) -> None:
        """Add ``device_work.incoming`` to an older database and mark its queued jobs.

        ``incoming`` is 1 for work another desktop's organization sent that the chat
        bridge starts, so it can ask whether any waits (``incoming_waiting``) without
        decrypting every queued request.
        """
        db = self.registry.db
        columns = {row[1] for row in db.execute("PRAGMA table_info(device_work)")}
        if "incoming" not in columns:
            db.execute("ALTER TABLE device_work ADD COLUMN incoming INTEGER NOT NULL DEFAULT 0")
            for row in db.execute(
                "SELECT peer,request,event FROM device_work WHERE state='accepted'"
            ).fetchall():
                if _bridge_starts_incoming(self._decode(row["peer"], row["event"])):
                    db.execute(
                        "UPDATE device_work SET incoming=1 WHERE peer=? AND request=?",
                        (row["peer"], row["request"]),
                    )
        db.execute(
            "CREATE INDEX IF NOT EXISTS device_work_incoming_queued ON device_work(thread) "
            "WHERE state='accepted' AND incoming=1"
        )

    def _encode(self, peer: str, value: dict[str, Any]) -> bytes:
        raw = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()
        nonce = secrets.token_bytes(12)
        return nonce + self.registry._cipher.encrypt(nonce, raw, ("application:" + peer).encode())

    def _decode(self, peer: str, value: bytes) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(
            self.registry._cipher.decrypt(value[:12], value[12:], ("application:" + peer).encode())
        )
        return result

    def _trusted(self, peer: str) -> None:
        row = self.registry._row(peer)
        if row["state"] != "active":
            raise PairingError("peer_unavailable")

    def authorize(self, peer: str, thread: str, context: dict[str, Any]) -> None:
        """Local desktop admin only; never called from an application record."""
        device_id(thread)
        if not isinstance(context.get("provider"), str) or not isinstance(
            context.get("model"), str
        ):
            raise PairingError("invalid_thread_context")
        with self.registry.transaction():
            self._trusted(peer)
            self._revoke_thread_tree(peer, thread)
            self.registry.db.execute(
                "INSERT OR REPLACE INTO device_grants VALUES (?,?,?)",
                (peer, thread, self._encode(peer, context)),
            )

    def remove_peer(self, peer: str, *, revoked_only: bool = False) -> None:
        """Local unpair/forget. Fresh QR consent is required to trust the identity again."""
        with self.registry.transaction():
            row = self.registry._row(peer)
            if revoked_only and row["state"] != "revoked":
                raise PairingError("revoked_peer_required")
            if not revoked_only and row["state"] == "revoked":
                raise PairingError("use_remove_revoked")
            db = self.registry.db
            # Retain request IDs and terminal work records across re-pairing so an
            # old provider callback or retried request cannot execute a second turn.
            db.execute(
                "UPDATE device_work SET state='cancelled' WHERE peer=? "
                "AND state IN ('accepted','running')",
                (peer,),
            )
            for table in (
                "device_grants",
                "device_inbox",
                "device_inbox_reads",
                "device_transcript",
                "device_message_attachments",
                "device_sequences",
                "device_chunk_receipts",
                "device_file_sends",
                "device_remote_requests",
                "device_remote_cancels",
                "device_remote_results",
                "device_remote_cursors",
                "device_thread_hosts",
                "device_remote_threads",
                "device_peer_snapshots",
                "device_outbox",
                "device_files",
                "device_file_chunks",
                "device_workspace_peers",
                "device_workspace_threads",
                "device_workspace_versions",
                "device_workspace_denied",
                "device_remote_control",
                "device_desktop_control",
                "device_wake",
                "device_wake_answers",
                "device_rem_requests",
                "device_rem_files",
                "device_link_files",
                "device_workflow_requests",
                "relay_origins",
            ):
                db.execute(f"DELETE FROM {table} WHERE peer=?", (peer,))
            self.locks.forget_peer(peer)
            db.execute("DELETE FROM endpoints WHERE device_id=?", (peer,))
            db.execute("DELETE FROM peer_names WHERE device_id=?", (peer,))
            db.execute("DELETE FROM peers WHERE device_id=?", (peer,))

    def revoke_grant(self, peer: str, thread: str) -> None:
        with self.registry.transaction():
            self.workspace.deny(peer, thread)
            self._revoke_thread_tree(peer, thread)

    def _revoke_thread_tree(self, peer: str, thread: str) -> None:
        children = [
            row["thread"]
            for row in self.registry.db.execute(
                "SELECT thread,context FROM device_grants WHERE peer=? ORDER BY thread", (peer,)
            )
            if self._decode(peer, row["context"]).get("parent_thread") == thread
        ]
        for identifier in [thread, *children]:
            self.registry.db.execute(
                "UPDATE device_work SET state='cancelled' WHERE peer=? AND thread=? AND "
                "state IN ('accepted','running')",
                (peer, identifier),
            )
            self.registry.db.execute(
                "DELETE FROM device_grants WHERE peer=? AND thread=?", (peer, identifier)
            )

    def _emit(
        self, peer: str, source: dict[str, Any], kind: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        db = self.registry.db
        if (
            db.execute("SELECT COUNT(*) FROM device_outbox WHERE peer=?", (peer,)).fetchone()[0]
            >= OUTBOX_EVENTS
        ):
            raise PairingError("outbox_quota")
        used = db.execute(
            "SELECT COALESCE(SUM(LENGTH(event)),0) FROM device_outbox WHERE peer=?", (peer,)
        ).fetchone()[0]
        event = {
            "version": 2,
            "id": str(uuid4()),
            "thread_id": source.get("thread_id"),
            "request_id": source.get("request_id"),
            "sequence": 0,
            "type": kind,
            "payload": payload,
        }
        sequence = self._next_outbound_sequence(peer)
        event["sequence"] = sequence
        _VALIDATOR.validate(event)
        encoded = self._encode(peer, event)
        if used + len(encoded) > OUTBOX_BYTES:
            raise PairingError("outbox_byte_quota")
        db.execute(
            "INSERT INTO device_outbox(sequence,peer,event) VALUES (?,?,?)",
            (sequence, peer, encoded),
        )
        return event

    def _status(
        self, peer: str, source: dict[str, Any], status: str, detail: str
    ) -> dict[str, Any]:
        return self._emit(peer, source, "request.status", {"status": status, "detail": detail})

    def pending(self, peer: str, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self.registry.lock:
            self._trusted(peer)
            return [
                self._decode(peer, row[0])
                for row in self.registry.db.execute(
                    "SELECT event FROM device_outbox WHERE peer=? AND sequence>? ORDER BY "
                    "sequence LIMIT ?",
                    (peer, after, min(limit, 100)),
                )
            ]

    def _prepare_desktop_read(self, peer: str, event: dict[str, Any]) -> dict[str, Any] | None:
        """Read Rem, organization and usage data for a phone view outside the device lock.

        Only new reads from a trusted phone that may use the desktop get here.
        handle() checks trust and permission again under the lock before answering;
        actions stay entirely inside one transaction.
        """
        if event["type"] != "desktop.read":
            return None
        with self.registry.lock:
            try:
                self._trusted(peer)
            except PairingError:
                return None
            for table in ("device_inbox", "device_inbox_reads"):
                if self.registry.db.execute(
                    f"SELECT 1 FROM {table} WHERE peer=? AND id=?", (peer, event["id"])
                ).fetchone():
                    return None
            if not self.desktop.allowed(peer):
                return None
            # Git and file reads are scoped to the projects this phone may see, read under
            # the lock.
            from gofer.devices.desktop_control import _project_scoped

            scoped = _project_scoped(str(event["payload"].get("op", "")))
            projects = self.desktop.projects(peer) if scoped else None
        return self.desktop.gather(event, projects)

    def handle(self, peer: str, event: dict[str, Any]) -> list[dict[str, Any]]:
        if not _VALIDATOR.is_valid(event):
            return self._refuse(peer, event, None, "invalid_application_event")
        digest = hashlib.sha256(
            json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        try:
            # Screens a phone's request before anything else, Rem included, sees it.
            blocked = self.locks.check(peer, event)
        except PairingError as exc:
            if str(exc) in _SESSION_ENDING:
                raise
            return self._refuse(peer, event, digest, str(exc))
        if blocked:
            return self._refuse(peer, event, digest, blocked)
        prepared = self._prepare_desktop_read(peer, event)
        try:
            return self._handle(peer, event, digest, prepared)
        except PairingError as exc:
            if str(exc) in _SESSION_ENDING:
                raise
            return self._refuse(peer, event, digest, str(exc))
        except (KeyError, TypeError) as exc:
            return self._refuse(peer, event, digest, type(exc).__name__)

    def _refuse(
        self, peer: str, event: dict[str, Any], digest: str | None, reason: str
    ) -> list[dict[str, Any]]:
        """Answer a request the desktop will not carry out, instead of ending the session.

        The phone keeps a request queued until an event with its request ID arrives and
        resends its whole queue on every connection, so a refusal that only closed the
        session would block that phone until it was reinstalled. The answer is recorded
        under the request's event ID where possible, so a resend gets the same answer.
        """
        kind = event.get("type") if event.get("type") in _EVENT_TYPES else "invalid"
        logger.warning("Phone request %s refused: %s", kind, reason)
        code, detail = _refusal(reason)
        with self.registry.transaction():
            self._trusted(peer)
            if digest is not None and reason not in _UNRECORDED:
                for table in ("device_inbox", "device_inbox_reads"):
                    prior = self.registry.db.execute(
                        f"SELECT digest FROM {table} WHERE peer=? AND id=?", (peer, event["id"])
                    ).fetchone()
                    if prior is not None:
                        return self.pending(peer)
                self._record_inbound(peer, event, digest)
            try:
                return [
                    self._emit(
                        peer, event, "error", {"code": code, "retryable": False, "detail": detail}
                    )
                ]
            except ValidationError:
                # Too malformed to address an answer to; nothing was recorded.
                raise PairingError(reason) from None

    def _record_inbound(self, peer: str, event: dict[str, Any], digest: str) -> None:
        db = self.registry.db
        read_only = event["type"] in READ_ONLY_KINDS
        db.execute(
            f"INSERT INTO {'device_inbox_reads' if read_only else 'device_inbox'} VALUES (?,?,?,?)",
            (peer, event["id"], digest, event["sequence"]),
        )
        if read_only:
            db.execute(
                "DELETE FROM device_inbox_reads WHERE peer=? AND sequence NOT IN (SELECT "
                "sequence FROM device_inbox_reads WHERE peer=? ORDER BY sequence DESC "
                "LIMIT ?)",
                (peer, peer, RECENT_READS),
            )
        db.execute("UPDATE peers SET last_seen=? WHERE device_id=?", (self.registry.clock(), peer))

    def _handle(
        self,
        peer: str,
        event: dict[str, Any],
        digest: str,
        prepared: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        with self.registry.transaction():
            self._trusted(peer)
            db = self.registry.db
            read_only = event["type"] in READ_ONLY_KINDS
            for table in ("device_inbox", "device_inbox_reads"):
                prior = db.execute(
                    f"SELECT digest FROM {table} WHERE peer=? AND id=?", (peer, event["id"])
                ).fetchone()
                if prior is not None:
                    if prior[0] != digest:
                        raise PairingError("event_id_conflict")
                    return self.pending(peer)
            if not read_only and (
                db.execute("SELECT COUNT(*) FROM device_inbox WHERE peer=?", (peer,)).fetchone()[0]
                >= 100000
            ):
                raise PairingError("inbox_quota")
            for table in ("device_inbox", "device_inbox_reads"):
                if db.execute(
                    f"SELECT 1 FROM {table} WHERE peer=? AND sequence=?",
                    (peer, event["sequence"]),
                ).fetchone():
                    raise PairingError("sequence_conflict")
            self._record_inbound(peer, event, digest)
            kind, payload = event["type"], event["payload"]
            workspace_result = self.workspace.handle(peer, event)
            if workspace_result is not None:
                return workspace_result
            remote_result = self.remote.handle(peer, event)
            if remote_result is not None:
                return remote_result
            desktop_result = self.desktop.handle(peer, event, prepared)
            if desktop_result is not None:
                return desktop_result
            handler = EVENT_HANDLERS.get(kind)
            if handler is not None:
                return handler(self, peer, event, payload)
            return [
                self._emit(
                    peer,
                    event,
                    "error",
                    {
                        "code": "unsupported",
                        "retryable": False,
                        "detail": "This application operation is not enabled",
                    },
                )
            ]

    def _thread_create(
        self, peer: str, event: dict[str, Any], payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """A new conversation from a phone, made from a desktop creation grant."""
        db = self.registry.db

        def rejected(code: str, detail: str) -> list[dict[str, Any]]:
            return [
                self._emit(
                    peer,
                    event,
                    "error",
                    {"code": code, "retryable": False, "detail": detail},
                )
            ]

        creation_digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        previous = db.execute(
            "SELECT digest,thread FROM device_thread_requests WHERE peer=? AND request=?",
            (peer, event["request_id"]),
        ).fetchone()
        if previous:
            if previous["digest"] != creation_digest:
                return rejected("conflict", "This creation request already has different content.")
            existing = db.execute(
                "SELECT context FROM device_grants WHERE peer=? AND thread=?",
                (peer, previous["thread"]),
            ).fetchone()
            if existing is None:
                return rejected("unauthorized", "This conversation was revoked on desktop.")
            context = self._decode(peer, existing["context"])
            thread = previous["thread"]
        else:
            templates = db.execute(
                "SELECT thread,context FROM device_grants WHERE peer=? ORDER BY thread",
                (peer,),
            ).fetchall()
            # Only an explicit local creation grant can supply execution context.
            matches = [(row["thread"], self._decode(peer, row["context"])) for row in templates]
            matches = [
                (identifier, value)
                for identifier, value in matches
                if value.get("allow_thread_create") is True
                and (
                    payload.get("template_thread_id") is None
                    or identifier == payload["template_thread_id"]
                )
                and (value.get("project_id") is not None or value.get("desktop_parity"))
                and (
                    value.get("desktop_parity")
                    or all(
                        value.get(field, [] if field == "resource_ids" else None) == payload[field]
                        for field in ("project_id", "resource_ids", "provider", "model")
                    )
                )
            ]
            if len(matches) != 1:
                return rejected(
                    "unauthorized",
                    "Allow phone conversation creation for this project on desktop.",
                )
            if len(templates) >= 10000:
                return rejected(
                    "too_large", "The desktop allows up to 10000 conversations per phone."
                )
            parent, template = matches[0]
            from gofer.ui.device_chat import validate_context

            try:
                context = validate_context(
                    {
                        **template,
                        "title": payload["title"],
                        "allow_thread_create": False,
                        "parent_thread": parent,
                        "revision": 1,
                        # A new thread follows Rem's account setting unless the
                        # phone's Account menu names one (payload "account").
                        "provider_accounts": {},
                    }
                )
            except (ValueError, OSError):
                return rejected("unauthorized", "The desktop project grant needs renewal.")
            if template.get("desktop_parity"):
                try:
                    context = self.workspace.configure_context(context, payload)
                    context["revision"] = 1
                except (ValueError, OSError):
                    return rejected(
                        "invalid_request",
                        "Refresh desktop projects, models and permissions. Choose again.",
                    )
            native_thread = str(uuid4())
            if template.get("desktop_thread_id"):
                from gofer.devices.workspace import wire_id

                thread = wire_id(native_thread)
                context["desktop_thread_id"] = native_thread
                parent_value = self.workspace.value(peer, parent)
                metadata = dict((parent_value or {}).get("metadata", {}))
                metadata.update(
                    id=native_thread,
                    title=payload["title"],
                    archived=False,
                    pinned=False,
                    mobileGroup="active",
                    createdAt=datetime.fromtimestamp(self.registry.clock(), UTC).isoformat(),
                    updatedAt=datetime.fromtimestamp(self.registry.clock(), UTC).isoformat(),
                )
                self.workspace.put(
                    peer,
                    thread,
                    {
                        "metadata": metadata,
                        "messages": [],
                        "revision": 1,
                        "mobile_created": True,
                    },
                )
            else:
                thread = native_thread
            if parent == host_template(peer):
                # The turn journal is keyed by this thread, so history can show
                # the paired desktop each thought and tool step as it happens.
                self.workspace.put(
                    peer,
                    thread,
                    {
                        "metadata": {"id": thread, "title": payload["title"]},
                        "messages": [],
                        "revision": 1,
                    },
                )
            db.execute(
                "INSERT INTO device_grants VALUES (?,?,?)",
                (peer, thread, self._encode(peer, context)),
            )
            db.execute(
                "INSERT INTO device_thread_requests VALUES (?,?,?,?)",
                (peer, event["request_id"], creation_digest, thread),
            )
        return [
            self._emit(
                peer,
                {**event, "thread_id": thread},
                "thread.snapshot",
                {
                    "title": context["title"],
                    "project_id": context["project_id"],
                    "resource_ids": context.get("resource_ids", []),
                    "provider": context["provider"],
                    "model": context["model"],
                    "revision": 1,
                    **self.workspace.details(peer, thread, context),
                },
            )
        ]

    def _sync_request(
        self, peer: str, event: dict[str, Any], payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Pending events plus a page of the phone's conversations."""
        db = self.registry.db
        cursor = payload["after_sequence"]
        if cursor is not None:
            self._prune_acknowledged(peer, cursor)
        events = self.pending(peer, payload["after_sequence"] or 0, payload["limit"])
        threads = []
        for grant in db.execute(
            "SELECT thread,context FROM device_grants WHERE peer=? ORDER BY thread", (peer,)
        ):
            context = self._decode(peer, grant["context"])
            threads.append(
                {
                    "thread_id": grant["thread"],
                    "title": context.get("title", "Remote Rem"),
                    "revision": context.get("revision", 1),
                    "project_id": context.get("project_id"),
                    "resource_ids": context.get("resource_ids", []),
                    "provider": context["provider"],
                    "model": context["model"],
                    **(
                        self.workspace.details(
                            peer,
                            grant["thread"],
                            context,
                            activity=payload.get("include_activity") is True,
                        )
                        if payload.get("include_thread_details")
                        else {}
                    ),
                    **(
                        {"can_create": context.get("allow_thread_create") is True}
                        if payload.get("include_connection")
                        else {}
                    ),
                }
            )
        offset = payload.get("threads_offset", 0)
        page = []
        page_bytes = 0
        for candidate in threads[offset : offset + 50]:
            size = len(json.dumps(candidate).encode())
            if page_bytes + size > 45000:
                break
            page.append(candidate)
            page_bytes += size
        next_offset = offset + len(page)
        events.append(
            self._emit(
                peer,
                event,
                "sync.snapshot",
                {
                    "revision": 1,
                    "through_sequence": events[-1]["sequence"] if events else 0,
                    "threads": page,
                    **(
                        {
                            "threads_offset": payload["threads_offset"],
                            "next_threads_offset": next_offset
                            if len(threads) > next_offset
                            else None,
                        }
                        if "threads_offset" in payload
                        else {}
                    ),
                    **(
                        {
                            "connection": {
                                "lan_endpoint": self.lan_endpoint,
                                "rem_ready": self.rem_ready(),
                                "remote_control": self.remote.allowed(peer),
                                "desktop_control": self.desktop.allowed(peer),
                                # Older phones reject unknown fields, so only a
                                # phone that asks learns the Tailscale address.
                                **(
                                    {"tailnet_endpoint": self.tailnet_endpoint}
                                    if payload.get("include_tailnet_endpoint") is True
                                    and (self.tailnet_endpoint or self.tailnet_known)
                                    else {}
                                ),
                            }
                        }
                        if payload.get("include_connection")
                        else {}
                    ),
                },
            )
        )
        return events

    def _fleet_request(
        self, peer: str, event: dict[str, Any], payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """This desktop and the paired devices it can see."""
        db = self.registry.db
        load = None
        if "advertise" in payload:
            # A paired desktop names its own listener inside its pinned session,
            # so the reverse direction works without typing an address.
            self._advertised(peer, payload["advertise"])
            if payload["advertise"].get("load") is True:
                load = self._load_reading(
                    memory_hold=payload["advertise"].get("memory_hold") is True,
                    running_by_kind=payload["advertise"].get("running_by_kind") is True,
                )
        now = int(self.registry.clock())
        devices = [
            {
                "device_id": row["device_id"],
                "name": self._label(row),
                "role": row["role"],
                "status": row["reachability"],
                "last_seen": int(row["last_seen"]) if row["last_seen"] else None,
                "observed_at": now,
                "fresh_until": now + 60,
                "running_jobs": row["running_jobs"] or [],
            }
            for row in self.fleet()
            if row["state"] in ("active", "indirect") and row["device_id"] != peer
        ][:99]
        jobs = [
            row[0] for row in db.execute("SELECT request FROM device_work WHERE state='running'")
        ]
        if self.local_jobs is not None:
            remote_turns = {
                row[0] for row in db.execute("SELECT turn FROM device_work WHERE state='running'")
            }
            jobs.extend(
                str(uuid5(NAMESPACE_URL, "raticode-job:" + j["thread_id"] + ":" + j["turn_id"]))
                for j in self.local_jobs()
                if j["turn_id"] not in remote_turns
            )
        devices.insert(
            0,
            {
                "device_id": self.registry.identity.device_id,
                "name": self.name,
                "role": "desktop",
                "status": "reachable",
                "last_seen": now,
                "observed_at": now,
                "fresh_until": now + 60,
                "running_jobs": jobs[:100],
                **({"load": load} if load is not None else {}),
            },
        )
        return [
            self._emit(
                peer,
                event,
                "fleet.snapshot",
                {"revision": now, "observed_at": now, "devices": devices},
            )
        ]

    def _file_event(
        self, peer: str, event: dict[str, Any], payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """One file transfer step in a granted conversation or Rem's file channel."""
        kind = event["type"]
        db = self.registry.db
        grant = db.execute(
            "SELECT 1 FROM device_grants WHERE peer=? AND thread=?",
            (peer, event["thread_id"]),
        ).fetchone()
        # Rem's chat has one private file channel per phone, open only
        # while the user lets that phone use Raticode.
        rem_files = event["thread_id"] == self.desktop.files_thread(peer) and self.desktop.allowed(
            peer
        )
        if grant is None and not rem_files:
            raise PairingError("file_thread_grant_required")
        if kind == "file.offer":
            self.files.offer(peer, event["thread_id"], payload)
            return [
                self._emit(
                    peer,
                    event,
                    "file.status",
                    {
                        "file_id": payload["file_id"],
                        "state": "receiving",
                        "received_size": 0,
                    },
                )
            ]
        if kind == "file.chunk":
            status = self.files.receive(peer, event["thread_id"], payload)
            return [self._emit(peer, event, "file.status", status)]
        if kind == "file.cancel":
            self.files.cancel(peer, event["thread_id"], payload["file_id"])
            self._finish_file_send(peer, event["thread_id"], payload["file_id"])
            return [
                self._emit(
                    peer,
                    event,
                    "file.status",
                    {
                        "file_id": payload["file_id"],
                        "state": "cancelled",
                        "received_size": 0,
                    },
                )
            ]
        if kind == "file.request":
            db.execute(
                "INSERT OR REPLACE INTO device_file_sends VALUES (?,?,?,?)",
                (peer, payload["file_id"], event["thread_id"], event["request_id"]),
            )
            return [self._file_chunk(peer, event, payload["file_id"], 0)]
        sending = db.execute(
            "SELECT * FROM device_file_sends WHERE peer=? AND id=? AND thread=?",
            (peer, payload["file_id"], event["thread_id"]),
        ).fetchone()
        # Status is an acknowledgment, not a request for another status.
        # Check its thread before pruning any pending chunks or receipts.
        if sending is None:
            return []
        if payload["state"] == "receiving":
            db.execute(
                "DELETE FROM device_outbox WHERE peer=? AND sequence IN (SELECT sequence "
                "FROM device_chunk_receipts WHERE peer=? AND id=? AND end_offset<=?)",
                (peer, peer, payload["file_id"], payload["received_size"]),
            )
            db.execute(
                "DELETE FROM device_chunk_receipts WHERE peer=? AND id=? AND end_offset<=?",
                (peer, payload["file_id"], payload["received_size"]),
            )
            return [self._file_chunk(peer, event, payload["file_id"], payload["received_size"])]
        if payload["state"] in ("available", "cancelled", "failed"):
            self._finish_file_send(peer, event["thread_id"], payload["file_id"])
        return []

    def _job_lookup(
        self, peer: str, event: dict[str, Any], payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """A paired desktop asks about, or cancels, a job it sent here."""
        kind = event["type"]
        db = self.registry.db
        work = db.execute(
            "SELECT w.* FROM device_job_ids j JOIN device_work w ON w.peer=j.peer "
            "AND w.request=j.request WHERE j.peer=? AND j.id=? AND w.thread=?",
            (peer, payload["job_id"], event["thread_id"]),
        ).fetchone()
        if work is None or self.registry._row(peer)["role"] != "desktop":
            raise PairingError("job_unavailable")
        state = work["state"]
        if kind == "job.cancel" and state in ("accepted", "running"):
            db.execute(
                "UPDATE device_work SET state='cancelled' WHERE peer=? AND request=?",
                (peer, work["request"]),
            )
            state = "cancel_requested"
        return [
            self._emit(
                peer,
                event,
                "job.status",
                {
                    "job_id": payload["job_id"],
                    "state": state,
                    "revision": 1,
                    "detail": "Target desktop job state",
                },
            )
        ]

    def _request_cancel(
        self, peer: str, event: dict[str, Any], payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Cancel a chat or job request that has not finished."""
        db = self.registry.db
        work = db.execute(
            "SELECT state FROM device_work WHERE peer=? AND request=? AND thread=?",
            (peer, event["request_id"], event["thread_id"]),
        ).fetchone()
        if work is None:
            raise PairingError("request_unavailable")
        if work[0] in ("accepted", "running"):
            db.execute(
                "UPDATE device_work SET state='cancelled' WHERE peer=? AND request=?",
                (peer, event["request_id"]),
            )
            return [
                self._status(peer, event, "cancel_requested", "Cancellation requested on desktop")
            ]
        return self.pending(peer)

    def _job_submit(
        self, peer: str, event: dict[str, Any], payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """A paired desktop's job: refused, answered as before, or submitted."""
        db = self.registry.db
        grant_row = db.execute(
            "SELECT context FROM device_grants WHERE peer=? AND thread=?",
            (peer, event["thread_id"]),
        ).fetchone()
        context = self._decode(peer, grant_row[0]) if grant_row else {}
        refusal = (
            self._rem_task_refusal(peer, event, context)
            if payload.get("rem_task") is not None
            else "Target desktop project grant required"
            if (
                self.registry._row(peer)["role"] != "desktop"
                or payload["target_device_id"] != self.registry.identity.device_id
                or payload["project_id"] != context.get("project_id")
                or payload["expected_policy_revision"] != context.get("policy_revision", 1)
                or payload["resource_ids"]
            )
            else None
        )
        if refusal is not None:
            return [
                self._emit(
                    peer,
                    event,
                    "job.status",
                    {
                        "job_id": payload["job_id"],
                        "state": "rejected",
                        "revision": 1,
                        "detail": refusal,
                    },
                )
            ]
        job_digest = hashlib.sha256(
            json.dumps({"thread": event["thread_id"], "payload": payload}, sort_keys=True).encode()
        ).hexdigest()
        job = db.execute(
            "SELECT * FROM device_job_ids WHERE peer=? AND id=?", (peer, payload["job_id"])
        ).fetchone()
        if job is not None:
            if job["digest"] != job_digest:
                raise PairingError("job_id_conflict")
            work = db.execute(
                "SELECT state FROM device_work WHERE peer=? AND request=?",
                (peer, job["request"]),
            ).fetchone()
            return [
                self._emit(
                    peer,
                    event,
                    "job.status",
                    {
                        "job_id": payload["job_id"],
                        "state": work[0] if work else "outcome_unknown",
                        "revision": 1,
                        "detail": "Existing job; not dispatched twice",
                    },
                )
            ]
        db.execute(
            "INSERT INTO device_job_ids VALUES (?,?,?,?)",
            (peer, payload["job_id"], event["request_id"], job_digest),
        )
        return self._submit(peer, event, payload)

    def _submit(
        self, peer: str, event: dict[str, Any], payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Queue a chat message or job for this desktop's bridge or Rem."""
        kind = event["type"]
        db = self.registry.db
        grant = db.execute(
            "SELECT context FROM device_grants WHERE peer=? AND thread=?",
            (peer, event["thread_id"]),
        ).fetchone()
        context = self._decode(peer, grant[0]) if grant else {}
        if (
            (self.workspace.value(peer, event["thread_id"]) or {}).get("delete_requested")
            or grant is None
            or (
                context.get("desktop_thread_id")
                and (
                    not context.get("project_path")
                    or context.get("provider") not in context.get("provider_permissions", {})
                )
            )
        ):
            return [
                self._emit(
                    peer,
                    event,
                    "error",
                    {
                        "code": "unauthorized",
                        "retryable": False,
                        "detail": (
                            "This thread has no available desktop project or provider. "
                            "Refresh, then choose a scope and provider in Thread settings."
                        ),
                    },
                )
            ]
        previous = db.execute(
            "SELECT event FROM device_work WHERE peer=? AND request=?",
            (peer, event["request_id"]),
        ).fetchone()
        if previous is not None:
            old = self._decode(peer, previous[0])
            if old["thread_id"] != event["thread_id"] or old["payload"] != payload:
                raise PairingError("request_id_conflict")
            return self.pending(peer)
        attachment_metadata = []
        for attachment in payload.get("attachment_ids", []):
            offer = self.files.metadata(peer, event["thread_id"], attachment)
            attachment_metadata.append(
                {
                    "id": attachment,
                    "name": offer["name"],
                    "size": offer["size"],
                    "mime": offer["mime"],
                    "type": offer["mime"],
                }
            )
        if (
            db.execute(
                "SELECT COUNT(*) FROM device_work WHERE peer=? AND state IN ('accepted','running')",
                (peer,),
            ).fetchone()[0]
            >= 100
        ):
            raise PairingError("work_quota")
        db.execute(
            "INSERT INTO device_work (peer,request,thread,event,state,turn,incoming) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                peer,
                event["request_id"],
                event["thread_id"],
                self._encode(peer, event),
                "accepted",
                str(uuid4()),
                int(_bridge_starts_incoming(event)),
            ),
        )
        if kind == "job.submit" and payload.get("rem_task") is not None:
            # This desktop's Rem picks it up (rem_tasks), never the chat bridge.
            db.execute(
                "INSERT OR IGNORE INTO device_rem_tasks VALUES (?,?,NULL,0)",
                (peer, event["request_id"]),
            )
        if kind == "chat.submit":
            self.workspace.name_from_message(peer, event["thread_id"], payload.get("text", ""))
        if attachment_metadata:
            encoded_attachments = self._encode(peer, {"attachments": attachment_metadata})
            metadata_bytes = db.execute(
                "SELECT COALESCE(SUM(LENGTH(value)),0) FROM device_message_attachments "
                "WHERE peer=?",
                (peer,),
            ).fetchone()[0]
            if metadata_bytes + len(encoded_attachments) > 32 * 1024 * 1024:
                raise PairingError("attachment_history_quota")
            db.execute(
                "INSERT INTO device_message_attachments VALUES (?,?,?)",
                (
                    peer,
                    event["request_id"],
                    encoded_attachments,
                ),
            )
        return [
            self._emit(
                peer,
                event,
                "job.status",
                {
                    "job_id": payload["job_id"],
                    "state": "accepted",
                    "revision": 1,
                    "detail": "Accepted by target desktop",
                },
            )
            if kind == "job.submit"
            else self._emit(peer, event, "chat.accepted", {"message_id": event["request_id"]})
        ]

    def claim(
        self, *, own: bool = True, organization: bool = True, waiting: str | None = None
    ) -> dict[str, Any] | None:
        """Mark the oldest startable request running and return it, or None.

        Work another desktop's organization sent (a ``job.submit`` with an
        ``organization`` payload) starts only with ``organization``; every other
        request, your own turns from a phone or a thread you run here, only with
        ``own``. The bridge passes whichever has a free worker. An organization job
        skipped for want of one stays accepted, and its sender is told why it waits:
        ``waiting`` when the bridge gives one (such as "Waiting for free memory
        (0.9 GB available)"), otherwise that it waits for a free worker on this
        computer. It is told again only when the reason changes.
        """
        with self.registry.transaction():
            row = None
            for candidate in self.registry.db.execute(
                "SELECT w.*,g.context,p.role AS peer_role,n.name AS peer_name FROM device_work w "
                "JOIN peers p ON p.device_id=w.peer "
                "LEFT JOIN peer_names n ON n.device_id=w.peer "
                "JOIN device_grants g ON g.peer=w.peer AND g.thread=w.thread WHERE "
                "w.state='accepted' AND p.state='active' AND NOT EXISTS (SELECT 1 FROM "
                "device_work busy WHERE busy.thread=w.thread AND busy.state='running') "
                "ORDER BY w.rowid"
            ).fetchall():
                queued = self._decode(candidate["peer"], candidate["event"])
                if queued["type"] == "job.submit" and "rem_task" in queued["payload"]:
                    # A handed-over Rem task runs as this desktop's own Rem task.
                    continue
                if own and organization:
                    row = candidate
                    break
                incoming = incoming_job(queued)
                if organization if incoming else own:
                    row = candidate
                    break
                if incoming:
                    self._tell_waiting(candidate["peer"], candidate["request"], queued, waiting)
            if row is None:
                return None
            self._waiting_told.pop((row["peer"], row["request"]), None)
            context = self._decode(row["peer"], row["context"])
            native = context.get("desktop_thread_id")
            if (
                native
                and self.local_jobs is not None
                and any(j["thread_id"] == native for j in self.local_jobs())
            ):
                return None
            self.registry.db.execute(
                "UPDATE device_work SET state='running' WHERE peer=? AND request=?",
                (row["peer"], row["request"]),
            )
            event = self._decode(row["peer"], row["event"])
            try:
                attachments = [
                    self.attachment(row["peer"], row["thread"], identifier)
                    for identifier in event["payload"].get("attachment_ids", [])
                ]
            except PairingError:
                self.registry.db.execute(
                    "UPDATE device_work SET state='failed' WHERE peer=? AND request=?",
                    (row["peer"], row["request"]),
                )
                self._status(row["peer"], event, "failed", "Attachment expired or unavailable")
                return None
            event["payload"].setdefault("text", event["payload"].get("intent", ""))
            self._status(row["peer"], event, "running", "Rem dispatch started")
            return {
                "peer": row["peer"],
                # Who sent it, for the worker's label: a phone, or a desktop by its name.
                "peer_role": row["peer_role"],
                "peer_name": display_name(row["peer_name"]),
                "event": event,
                "turn_id": row["turn"],
                "context": self._decode(row["peer"], row["context"]),
                "messages": self.history(
                    row["peer"], row["thread"], through_request=row["request"]
                ),
                "attachments": attachments,
            }

    def incoming_waiting(self) -> bool:
        """Whether work another desktop's organization sent waits to start here.

        One indexed query, asked before the chat bridge takes a worker for it, with
        the same conditions ``claim`` starts a request under.
        """
        with self.registry.lock:
            return bool(
                self.registry.db.execute(
                    # CROSS JOIN keeps device_work first, read through the partial index
                    # of queued incoming jobs, so an empty queue reads no rows.
                    "SELECT EXISTS (SELECT 1 FROM device_work w "
                    "CROSS JOIN peers p ON p.device_id=w.peer "
                    "CROSS JOIN device_grants g ON g.peer=w.peer AND g.thread=w.thread WHERE "
                    "w.state='accepted' AND w.incoming=1 AND p.state='active' AND NOT EXISTS "
                    "(SELECT 1 FROM device_work busy WHERE busy.thread=w.thread AND "
                    "busy.state='running'))"
                ).fetchone()[0]
            )

    def _tell_waiting(
        self, peer: str, request: str, event: dict[str, Any], reason: str | None = None
    ) -> None:
        """Tell the sending desktop why its job waits here, once per reason.

        Called inside ``claim``'s transaction. The job stays accepted: it starts when
        a worker frees up.
        """
        key = (peer, request)
        detail = reason or f"Waiting for a free worker on {self.name}"
        if self._waiting_told.get(key) == detail:
            return
        try:
            self._emit(
                peer,
                event,
                "job.status",
                {
                    "job_id": event["payload"]["job_id"],
                    "state": "accepted",
                    "revision": 1,
                    "detail": detail,
                },
            )
        except PairingError:
            # A full outbox only loses this notice; the job still starts when it can.
            pass
        self._waiting_told[key] = detail

    def _rem_task_refusal(
        self, peer: str, event: dict[str, Any], context: dict[str, Any]
    ) -> str | None:
        """Why this desktop refuses a Rem task a paired desktop handed over, or None.

        Called inside ``_handle``'s transaction. The task reuses "Run threads from this
        desktop": it comes on that grant's thread, for one of the projects it shares,
        with a provider it allows, and only read-only task types run here. One desktop
        has at most ``REM_TASKS_PER_DESKTOP`` of them queued or running here at once.
        """
        payload = event["payload"]
        task = payload["rem_task"]
        if (
            self.registry._row(peer)["role"] != "desktop"
            or payload["target_device_id"] != self.registry.identity.device_id
            or payload["resource_ids"]
        ):
            return "Target desktop project grant required"
        hosting = self.registry.db.execute(
            "SELECT 1 FROM device_thread_hosts WHERE peer=?", (peer,)
        ).fetchone()
        if event["thread_id"] != host_template(peer) or hosting is None or not context:
            return f"Run threads from this desktop is off on {self.name} for that computer"
        if payload["expected_policy_revision"] != context.get("policy_revision", 1):
            return "Target desktop project grant required"
        if not any(p.get("id") == payload["project_id"] for p in context.get("projects") or []):
            return f"{self.name} doesn't share that project with that computer"
        if task["type"] != "research" and task["execution_mode"] != "conversation":
            return "Only research and conversation tasks run on another computer for now"
        provider = task.get("provider")
        if provider and provider not in (context.get("provider_permissions") or {}):
            return f"{self.name} doesn't allow that provider for that computer's work"
        # Its own request sent again isn't another task.
        held = self.registry.db.execute(
            "SELECT COUNT(*) FROM device_rem_tasks WHERE peer=? AND settled=0 AND request!=?",
            (peer, event["request_id"]),
        ).fetchone()[0]
        if held >= REM_TASKS_PER_DESKTOP:
            return (
                f"{self.name} already has {REM_TASKS_PER_DESKTOP} tasks from this computer "
                "queued or running, the most it takes from one computer at once. Run this "
                "one again when one of them ends."
            )
        return None

    def rem_tasks(self) -> list[dict[str, Any]]:
        """Rem tasks paired desktops handed over that this desktop hasn't settled yet.

        Each has the sending ``peer`` and its name, the ``request``, its ``state``
        (accepted, running, or cancelled when the sender asked to stop it), this
        desktop's Rem ``run_id`` once one was made, the ``task`` and the project's
        ``root`` here, and whether the sender still has the hosting grant
        (``authorized``). A task whose sender was unpaired stays listed, not authorized,
        so its run here is stopped instead of left running unseen.
        """
        with self.registry.lock:
            rows = self.registry.db.execute(
                "SELECT w.peer, w.request, w.thread, w.event, w.state, r.run_id, n.name, "
                "p.state AS peer_state, g.context FROM device_rem_tasks r JOIN device_work w "
                "ON w.peer=r.peer AND w.request=r.request LEFT JOIN peers p ON p.device_id=r.peer "
                "LEFT JOIN peer_names n ON n.device_id=r.peer "
                "LEFT JOIN device_grants g ON g.peer=w.peer AND g.thread=w.thread "
                "WHERE r.settled=0 ORDER BY w.rowid"
            ).fetchall()
            tasks = []
            for row in rows:
                event = self._decode(row["peer"], row["event"])
                context = self._decode(row["peer"], row["context"]) if row["context"] else {}
                project: dict[str, Any] = next(
                    (
                        p
                        for p in context.get("projects") or []
                        if p.get("id") == event["payload"]["project_id"]
                    ),
                    {},
                )
                tasks.append(
                    {
                        "peer": row["peer"],
                        "peer_name": row["name"] or "a paired desktop",
                        "request": row["request"],
                        "state": row["state"],
                        "run_id": row["run_id"],
                        "task": event["payload"]["rem_task"],
                        "root": project.get("root"),
                        "authorized": bool(
                            row["context"]
                            and row["peer_state"] == "active"
                            and row["peer"] in self._hosting_peers()
                        ),
                    }
                )
            return tasks

    def _hosting_peers(self) -> set[str]:
        return {r[0] for r in self.registry.db.execute("SELECT peer FROM device_thread_hosts")}

    def rem_task_started(self, peer: str, request: str, run_id: str) -> None:
        """Record this desktop's Rem run for a handed-over task and tell the sender."""
        with self.registry.transaction():
            row = self.registry.db.execute(
                "SELECT * FROM device_work WHERE peer=? AND request=?", (peer, request)
            ).fetchone()
            if row is None:
                raise PairingError("request_unavailable")
            self.registry.db.execute(
                "UPDATE device_rem_tasks SET run_id=? WHERE peer=? AND request=?",
                (run_id, peer, request),
            )
            if row["state"] != "accepted":
                return
            self.registry.db.execute(
                "UPDATE device_work SET state='running' WHERE peer=? AND request=?",
                (peer, request),
            )
            event = self._decode(peer, row["event"])
            self._emit(
                peer,
                event,
                "job.status",
                {
                    "job_id": event["payload"]["job_id"],
                    "state": "running",
                    "revision": 2,
                    "detail": f"Rem task started on {self.name}",
                },
            )

    def rem_task_waiting(self, peer: str, request: str, reason: str) -> None:
        """Tell the sender why its task waits here, such as for a free worker, once a reason."""
        key = (peer, request)
        if self._rem_waiting.get(key) == reason:
            return
        with self.registry.transaction():
            row = self.registry.db.execute(
                "SELECT event FROM device_work WHERE peer=? AND request=?", (peer, request)
            ).fetchone()
            if row is None:
                return
            event = self._decode(peer, row[0])
            try:
                self._emit(
                    peer,
                    event,
                    "job.status",
                    {
                        "job_id": event["payload"]["job_id"],
                        "state": "running",
                        "revision": 2,
                        "detail": reason[:1000],
                    },
                )
            except PairingError:
                return
        self._rem_waiting[key] = reason

    def finish_rem_task(
        self,
        peer: str,
        request: str,
        state: str,
        text: str = "",
        *,
        detail: str = "",
        usage: dict[str, Any] | None = None,
        run_id: str | None = None,
        report: str | None = None,
    ) -> None:
        """Send a handed-over task's outcome: its answer, usage and report, once.

        ``state`` is completed, failed, rejected or cancelled. A task the sender asked
        to stop settles as cancelled unless it had already completed.
        """
        with self.registry.transaction():
            row = self.registry.db.execute(
                "SELECT * FROM device_work WHERE peer=? AND request=?", (peer, request)
            ).fetchone()
            if row is None:
                raise PairingError("request_unavailable")
            if row["state"] == "cancelled" and state != "completed":
                state = "cancelled"
            gone = (
                self.registry.db.execute(
                    "SELECT 1 FROM peers WHERE device_id=?", (peer,)
                ).fetchone()
                is None
            )
            if gone:
                # The sender was unpaired: nothing goes to it; the task only settles here.
                self.registry.db.execute(
                    "UPDATE device_rem_tasks SET settled=1 WHERE peer=? AND request=?",
                    (peer, request),
                )
                self._rem_waiting.pop((peer, request), None)
                return
            event = self._decode(peer, row["event"])
            for start in range(0, len(text), 4000):
                self._emit(
                    peer,
                    event,
                    "chat.message",
                    {
                        "message_id": str(
                            uuid5(
                                NAMESPACE_URL,
                                "raticode-message:reply-" + request + ":" + str(start),
                            )
                        ),
                        "text": text[start : start + 4000],
                        "attachment_ids": [],
                        "final": start + 4000 >= len(text),
                    },
                )
            result: dict[str, Any] = {}
            if run_id:
                result = {"rem_task": {"run_id": run_id[:100], "report": report}}
            self._emit(
                peer,
                event,
                "job.status",
                {
                    "job_id": event["payload"]["job_id"],
                    "state": state,
                    "revision": 3,
                    "detail": (detail or f"Rem task {state} on {self.name}")[:1000],
                    **({"usage": usage} if usage else {}),
                    **result,
                },
            )
            self.registry.db.execute(
                "UPDATE device_work SET state=? WHERE peer=? AND request=?",
                (state, peer, request),
            )
            self.registry.db.execute(
                "UPDATE device_rem_tasks SET settled=1 WHERE peer=? AND request=?",
                (peer, request),
            )
            self._rem_waiting.pop((peer, request), None)

    def history(
        self, peer: str, thread: str, *, through_request: str | None = None
    ) -> list[dict[str, str]]:
        with self.registry.lock:
            if self.workspace.value(peer, thread) is not None:
                history = []
                for m in self.workspace.messages(
                    peer, thread, for_dispatch=True, through_request=through_request
                )[-200:]:
                    saved = [
                        item
                        for item in m.get("attachments", [])
                        if isinstance(item, dict) and item.get("storageName")
                    ]
                    history.append(
                        {
                            "role": m["role"],
                            "content": m["body"],
                            **({"attachments": saved} if saved else {}),
                        }
                    )
                return history
            messages = []
            for row in self.registry.db.execute(
                "SELECT event,state,request FROM device_work WHERE peer=? AND thread=? AND "
                "state IN ('running','completed','failed','outcome_unknown') ORDER BY rowid "
                "DESC LIMIT 20",
                (peer, thread),
            ).fetchall()[::-1]:
                event = self._decode(peer, row["event"])
                messages.append(
                    {
                        "role": "user",
                        "content": event["payload"].get("text", event["payload"].get("intent", "")),
                    }
                )
                answer = self.registry.db.execute(
                    "SELECT result FROM device_transcript WHERE peer=? AND request=?",
                    (peer, row["request"]),
                ).fetchone()
                if answer is not None:
                    messages.append(
                        {"role": "assistant", "content": self._decode(peer, answer[0])["text"]}
                    )

            return messages

    def authorized(self, peer: str, thread: str) -> bool:
        with self.registry.lock:
            return (
                self.registry.db.execute(
                    "SELECT 1 FROM device_grants g JOIN peers p ON p.device_id=g.peer WHERE "
                    "g.peer=? AND g.thread=? AND p.state='active'",
                    (peer, thread),
                ).fetchone()
                is not None
            )

    def runnable(self, peer: str, request_id: str) -> bool:
        with self.registry.lock:
            return (
                self.registry.db.execute(
                    "SELECT 1 FROM device_work w JOIN device_grants g ON g.peer=w.peer AND "
                    "g.thread=w.thread JOIN peers p ON p.device_id=w.peer WHERE w.peer=? AND "
                    "w.request=? AND w.state='running' AND p.state='active'",
                    (peer, request_id),
                ).fetchone()
                is not None
            )

    def complete(
        self,
        peer: str,
        request_id: str,
        text: str,
        error: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        with self.registry.transaction():
            self._trusted(peer)
            row = self.registry.db.execute(
                "SELECT * FROM device_work WHERE peer=? AND request=?", (peer, request_id)
            ).fetchone()
            if row is not None and row["state"] == "cancelled":
                event = self._decode(peer, row["event"])
                if incoming_job(event):
                    self._emit(
                        peer,
                        event,
                        "job.status",
                        {
                            "job_id": event["payload"]["job_id"],
                            "state": "cancelled",
                            "revision": 2,
                            "detail": "Managed job stopped after cancellation",
                            **({"usage": usage} if usage is not None else {}),
                        },
                    )
                self._status(peer, event, "cancelled", "Rem stopped after cancellation")
                return
            if row is None or row["state"] != "running":
                raise PairingError("work_not_running")
            event = self._decode(peer, row["event"])
            if error is None:
                self.registry.db.execute(
                    "DELETE FROM device_transcript WHERE peer=? AND thread=? AND request NOT IN "
                    "(SELECT request FROM device_work WHERE peer=? AND thread=? ORDER BY rowid "
                    "DESC LIMIT 20)",
                    (peer, row["thread"], peer, row["thread"]),
                )
                used = self.registry.db.execute(
                    "SELECT COALESCE(SUM(LENGTH(result)),0) FROM device_transcript WHERE peer=?",
                    (peer,),
                ).fetchone()[0]
                if used + len(text.encode()) > 64 * 1024 * 1024:
                    error = "transcript_quota"
            if error is None:
                self.registry.db.execute(
                    "INSERT OR REPLACE INTO device_transcript VALUES (?,?,?,?)",
                    (peer, request_id, row["thread"], self._encode(peer, {"text": text})),
                )
                shared = self.workspace.value(peer, row["thread"])
                if shared is not None:
                    shared["messages"] = self.workspace.messages(peer, row["thread"])
                    grant = self.registry.db.execute(
                        "SELECT context FROM device_grants WHERE peer=? AND thread=?",
                        (peer, row["thread"]),
                    ).fetchone()
                    if grant is not None:
                        context = self._decode(peer, grant[0])
                        context["revision"] = context.get("revision", 1) + 1
                        shared["revision"] = context["revision"]
                        self.registry.db.execute(
                            "UPDATE device_grants SET context=? WHERE peer=? AND thread=?",
                            (self._encode(peer, context), peer, row["thread"]),
                        )
                    self.workspace.put(peer, row["thread"], shared)
                    if grant is not None:
                        snapshot = {
                            key: context.get(key, [] if key == "resource_ids" else None)
                            for key in (
                                "title",
                                "project_id",
                                "resource_ids",
                                "provider",
                                "model",
                                "revision",
                            )
                        }
                        snapshot.update(self.workspace.details(peer, row["thread"], context))
                        self._emit(peer, event, "thread.snapshot", snapshot)
                for start in range(0, max(1, len(text)), 4000):
                    self._emit(
                        peer,
                        event,
                        "chat.message",
                        {
                            "message_id": str(
                                uuid5(
                                    NAMESPACE_URL,
                                    "raticode-message:reply-" + request_id + ":" + str(start),
                                )
                            ),
                            "text": text[start : start + 4000],
                            "attachment_ids": [],
                            "final": start + 4000 >= len(text),
                        },
                    )
            state = (
                "completed"
                if error is None
                else "cancelled"
                if error == "provider_stopped"
                else "failed"
            )
            if event["type"] == "job.submit":
                self._emit(
                    peer,
                    event,
                    "job.status",
                    {
                        "job_id": event["payload"]["job_id"],
                        "state": state,
                        "revision": 2,
                        "detail": "Target Rem finished",
                        **(
                            {"usage": usage}
                            if usage is not None and event["payload"].get("organization")
                            else {}
                        ),
                    },
                )
            self._status(
                peer,
                event,
                state,
                "Rem completed"
                if error is None
                else "Rem was stopped. You can send another message."
                if error == "provider_stopped"
                else "Rem could not finish. Check the provider and thread settings on desktop.",
            )
            self.registry.db.execute(
                "UPDATE device_work SET state=? WHERE peer=? AND request=?",
                (state, peer, request_id),
            )

    def queue_remote(self, peer: str, body: dict[str, Any]) -> dict[str, Any]:
        with self.registry.transaction():
            self._trusted(peer)
            if self.registry._row(peer)["role"] != "desktop":
                raise PairingError("mobile_is_not_worker")
            request_id = device_id(body.get("request_id"))
            existing = self.registry.db.execute(
                "SELECT event FROM device_remote_requests WHERE peer=? AND request=?",
                (peer, request_id),
            ).fetchone()
            if existing:
                old = self._decode(peer, existing[0])
                kind = body.get("kind", "chat.submit")
                text = old["payload"].get("text", old["payload"].get("intent", ""))
                if (
                    old["type"] != kind
                    or old["thread_id"] != body.get("thread_id")
                    or text != body.get("text", "")
                    or (
                        kind == "job.submit"
                        and (
                            old["payload"]["project_id"] != body.get("project_id")
                            or old["payload"].get("organization") != body.get("organization")
                            or old["payload"].get("rem_task") != body.get("rem_task")
                            or old["payload"]["expected_policy_revision"]
                            != body.get("policy_revision", 1)
                        )
                    )
                ):
                    raise PairingError("request_id_conflict")
                return old
            sequence = self.registry.db.execute(
                "SELECT COUNT(*) FROM device_remote_requests WHERE peer=?", (peer,)
            ).fetchone()[0]
            if sequence >= 100000:
                raise PairingError("remote_request_quota")
            sequence = self._next_outbound_sequence(peer)
            kind = body.get("kind", "chat.submit")
            payload = {"text": body.get("text", ""), "attachment_ids": []}
            if kind == "fleet.request":
                payload = {}
            if kind == "job.submit":
                payload = {
                    "job_id": request_id,
                    "target_device_id": peer,
                    "project_id": body.get("project_id"),
                    "resource_ids": [],
                    "intent": body.get("text", ""),
                    "expected_policy_revision": body.get("policy_revision", 1),
                    **({"organization": body["organization"]} if body.get("organization") else {}),
                    **({"rem_task": body["rem_task"]} if body.get("rem_task") else {}),
                }
            event = {
                "version": 2,
                "id": str(uuid4()),
                "thread_id": body.get("thread_id"),
                "request_id": request_id,
                "sequence": sequence,
                "type": kind,
                "payload": payload,
            }
            if kind not in (
                "chat.submit",
                "job.submit",
                "fleet.request",
            ) or not _VALIDATOR.is_valid(event):
                raise PairingError("invalid_remote_request")
            self.registry.db.execute(
                "INSERT INTO device_remote_requests VALUES (?,?,?,?)",
                (peer, request_id, self._encode(peer, event), "queued"),
            )
            return event

    def remote_result(self, peer: str, event: dict[str, Any]) -> None:
        if not _VALIDATOR.is_valid(event):
            raise PairingError("invalid_remote_response")
        with self.registry.transaction():
            self._trusted(peer)
            # The peer delivers its outbox in sequence order, so the highest number
            # received is this desktop's acknowledgment cursor for the next session.
            self.registry.db.execute(
                "INSERT INTO device_remote_cursors VALUES (?,?) ON CONFLICT(peer) DO UPDATE "
                "SET value=MAX(value,excluded.value)",
                (peer, event["sequence"]),
            )
            self.registry.db.execute(
                "UPDATE peers SET last_seen=? WHERE device_id=?", (self.registry.clock(), peer)
            )
            if self._catalog_requests.get(str(event["request_id"])) == peer:
                del self._catalog_requests[str(event["request_id"])]
                if event["type"] in ("catalog.snapshot", "error"):
                    self._peer_catalogs[peer] = self._catalog_answer(event)
                    return
            if event["type"] in ("catalog.snapshot", "thread.snapshot", "thread.history.snapshot"):
                # Answers to a live remote thread are consumed by that turn's stream.
                return
            asked = self._load_requests.pop(str(event["request_id"]), None)
            if asked is not None and asked[0] == peer and event["type"] == "error":
                if asked[1] == "kinds":
                    # An older desktop refused the counts by kind; ask for its load and hold.
                    self._hold_only_announcements.add(peer)
                elif asked[1] == "hold":
                    # An older desktop refused the memory hold; ask for its load alone.
                    self._load_only_announcements.add(peer)
                else:
                    # An older desktop refused the load request; announce plainly from now on.
                    self._plain_announcements.add(peer)
                return
            if event["type"] in ("fleet.snapshot", "sync.snapshot"):
                # Periodic status snapshots replace each other instead of filling the
                # result journal that holds delegated work answers.
                self.registry.db.execute(
                    "INSERT INTO device_peer_snapshots VALUES (?,?,?,?) "
                    "ON CONFLICT(peer,kind) DO UPDATE SET sequence=excluded.sequence,"
                    "event=excluded.event WHERE excluded.sequence>=sequence",
                    (peer, event["type"], event["sequence"], self._encode(peer, event)),
                )
                if event["type"] == "fleet.snapshot":
                    self.registry.db.execute(
                        "UPDATE device_remote_requests SET state='completed' WHERE peer=? "
                        "AND request=?",
                        (peer, event["request_id"]),
                    )
                return
            count = self.registry.db.execute(
                "SELECT COUNT(*) FROM device_remote_results WHERE peer=?", (peer,)
            ).fetchone()[0]
            used = self.registry.db.execute(
                "SELECT COALESCE(SUM(LENGTH(event)),0) FROM device_remote_results WHERE peer=?",
                (peer,),
            ).fetchone()[0]
            if count >= 10000 or used >= 64 * 1024 * 1024:
                self.registry.db.execute(
                    "DELETE FROM device_remote_results WHERE peer=? AND rowid NOT IN (SELECT "
                    "rowid FROM device_remote_results WHERE peer=? ORDER BY rowid DESC LIMIT "
                    "2000)",
                    (peer, peer),
                )
                used = self.registry.db.execute(
                    "SELECT COALESCE(SUM(LENGTH(event)),0) FROM device_remote_results WHERE peer=?",
                    (peer,),
                ).fetchone()[0]
                if used >= 64 * 1024 * 1024:
                    raise PairingError("remote_result_quota")
            prior = self.registry.db.execute(
                "SELECT event FROM device_remote_results WHERE peer=? AND id=?", (peer, event["id"])
            ).fetchone()
            if prior and self._decode(peer, prior[0]) != event:
                raise PairingError("remote_response_conflict")
            self.registry.db.execute(
                "INSERT OR IGNORE INTO device_remote_results VALUES (?,?,?)",
                (peer, event["id"], self._encode(peer, event)),
            )
            terminal = None
            if event["type"] == "request.status":
                terminal = event["payload"]["status"]
            elif event["type"] == "job.status":
                terminal = event["payload"]["state"]
            elif event["type"] == "error":
                terminal = "failed"
            if terminal in ("completed", "failed", "cancelled", "outcome_unknown", "rejected"):
                self.registry.db.execute(
                    "UPDATE device_remote_requests SET state=? WHERE peer=? AND request=?",
                    (terminal, peer, event["request_id"]),
                )

    def _next_outbound_sequence(self, peer: str) -> int:
        db = self.registry.db
        sequence = db.execute(
            "SELECT MAX(value)+1 FROM (SELECT COALESCE(MAX(sequence),0) AS value FROM "
            "device_outbox WHERE peer=? UNION ALL SELECT value FROM device_sequences "
            "WHERE peer=? UNION ALL SELECT COUNT(*) FROM device_remote_requests WHERE peer=?)",
            (peer, peer, peer),
        ).fetchone()[0]
        db.execute("INSERT OR REPLACE INTO device_sequences VALUES (?,?)", (peer, sequence))
        return int(sequence)

    def cancel_remote(self, peer: str, request_id: str) -> dict[str, Any]:
        with self.registry.transaction():
            self._trusted(peer)
            existing = self.registry.db.execute(
                "SELECT event FROM device_remote_cancels WHERE peer=? AND request=?",
                (peer, request_id),
            ).fetchone()
            if existing:
                return self._decode(peer, existing[0])
            row = self.registry.db.execute(
                "SELECT event FROM device_remote_requests WHERE peer=? AND request=?",
                (peer, request_id),
            ).fetchone()
            if row is None:
                raise PairingError("request_unavailable")
            event = self._decode(peer, row[0])
            cancellation = {
                **event,
                "id": str(uuid5(NAMESPACE_URL, "raticode-cancel:" + request_id)),
                "type": "request.cancel",
                "payload": {
                    "reason": "Rem task stopped"
                    if "rem_task" in event["payload"]
                    else "Organization execution stopped"
                },
                "sequence": self._next_outbound_sequence(peer),
            }
            self.registry.db.execute(
                "INSERT INTO device_remote_cancels VALUES (?,?,?)",
                (peer, request_id, self._encode(peer, cancellation)),
            )
            return cancellation

    def remote_status(self, peer: str, request_id: str) -> dict[str, Any]:
        with self.registry.lock:
            self._trusted(peer)
            events = [
                self._decode(peer, row[0])
                for row in self.registry.db.execute(
                    "SELECT event FROM device_remote_results WHERE peer=? ORDER BY rowid", (peer,)
                )
            ]
            row = self.registry.db.execute(
                "SELECT state FROM device_remote_requests WHERE peer=? AND request=?",
                (peer, request_id),
            ).fetchone()
            return {
                "state": row[0] if row else "unknown",
                "request_id": request_id,
                "events": [event for event in events if event["request_id"] == request_id],
            }

    def _finish_file_send(self, peer: str, thread: str, identifier: str) -> None:
        db = self.registry.db
        if (
            db.execute(
                "SELECT 1 FROM device_file_sends WHERE peer=? AND id=? AND thread=?",
                (peer, identifier, thread),
            ).fetchone()
            is None
        ):
            return
        # A finished or cancelled transfer must not replay queued chunks on reconnect.
        db.execute(
            "DELETE FROM device_outbox WHERE peer=? AND sequence IN "
            "(SELECT sequence FROM device_chunk_receipts WHERE peer=? AND id=?)",
            (peer, peer, identifier),
        )
        db.execute("DELETE FROM device_chunk_receipts WHERE peer=? AND id=?", (peer, identifier))
        db.execute("DELETE FROM device_file_sends WHERE peer=? AND id=?", (peer, identifier))

    def _file_chunk(
        self, peer: str, event: dict[str, Any], identifier: str, offset: int
    ) -> dict[str, Any]:
        offer, chunk = self.files.chunk(peer, event["thread_id"], identifier, offset)
        result = self._emit(
            peer,
            event,
            "file.chunk",
            {
                "file_id": identifier,
                "offset": offset,
                "data": base64.urlsafe_b64encode(chunk).rstrip(b"=").decode(),
                "eof": offset + len(chunk) == offer["size"],
            },
        )
        self.registry.db.execute(
            "INSERT INTO device_chunk_receipts VALUES (?,?,?,?)",
            (peer, identifier, result["sequence"], offset + len(chunk)),
        )
        return result

    def offer_file(
        self, peer: str, thread: str, path: str, origin_request: str | None = None
    ) -> dict[str, Any]:
        with self.registry.transaction():
            self._trusted(peer)
            row = self.registry.db.execute(
                "SELECT context FROM device_grants WHERE peer=? AND thread=?", (peer, thread)
            ).fetchone()
            if row is None:
                raise PairingError("file_thread_grant_required")
            if origin_request is not None:
                work = self.registry.db.execute(
                    "SELECT thread FROM device_work WHERE peer=? AND request=?",
                    (peer, origin_request),
                ).fetchone()
                if work is None or work[0] != thread or not self.runnable(peer, origin_request):
                    raise PairingError("originating_request_not_authorized")
            from gofer.ui.device_chat import validate_context

            context = validate_context(self._decode(peer, row[0]))
            offer = self._thread_snapshot(peer, thread, context, Path(path))
            return self._emit(
                peer, {"thread_id": thread, "request_id": str(uuid4())}, "file.offer", offer
            )

    def _thread_snapshot(
        self, peer: str, thread: str, context: dict[str, Any], path: Path
    ) -> dict[str, Any]:
        """Snapshot a file from a shared thread's project folders for that thread."""
        root = Path(context["project_path"])
        identity = context["project_identity"]
        if context.get("desktop_parity"):
            candidates = [
                {"root": str(root), "identity": identity},
                *context.get("projects", []),
            ]
            selected = next(
                (p for p in candidates if path.absolute().is_relative_to(Path(p["root"]))),
                None,
            )
            if selected is None:
                raise PairingError("file_not_in_open_desktop_project")
            root, identity = Path(selected["root"]), selected["identity"]
        offer: dict[str, Any] = self.files.snapshot(peer, thread, root, path, identity)
        return offer

    def spawn_thread(self, peer: str, origin_request: str, action: dict[str, Any]) -> None:
        """Apply the same explicit Rem child-thread action as the desktop renderer."""
        with self.registry.transaction():
            self._trusted(peer)
            if not self.runnable(peer, origin_request):
                raise PairingError("originating_request_not_authorized")
            db = self.registry.db
            origin = db.execute(
                "SELECT thread FROM device_work WHERE peer=? AND request=?", (peer, origin_request)
            ).fetchone()
            row = db.execute(
                "SELECT context FROM device_grants WHERE peer=? AND thread=?", (peer, origin[0])
            ).fetchone()
            context = self._decode(peer, row[0])
            if not context.get("desktop_parity") or not context.get("desktop_thread_id"):
                raise PairingError("workspace_sharing_required")
            request = str(uuid5(NAMESPACE_URL, "raticode-child:" + origin_request))
            if db.execute(
                "SELECT 1 FROM device_thread_requests WHERE peer=? AND request=?", (peer, request)
            ).fetchone():
                return
            identifier = next(
                (
                    p["id"]
                    for p in context.get("projects", [])
                    if p["root"] == action["projectRoot"]
                ),
                None,
            )
            if identifier is None:
                raise PairingError("project_not_open")
            context = self.workspace.scope_context(context, identifier)
            message = action["message"]
            if not isinstance(message, str) or not message.strip() or len(message) > 100_000:
                raise PairingError("invalid_child_task")
            if (
                db.execute(
                    "SELECT COUNT(*) FROM device_work WHERE peer=? "
                    "AND state IN ('accepted','running')",
                    (peer,),
                ).fetchone()[0]
                >= 100
            ):
                raise PairingError("work_quota")
            if (
                db.execute("SELECT COUNT(*) FROM device_grants WHERE peer=?", (peer,)).fetchone()[0]
                >= 10000
            ):
                raise PairingError("thread_quota")
            from gofer.devices.workspace import wire_id

            native = device_id(action["threadId"])
            thread = wire_id(native)
            now = datetime.fromtimestamp(self.registry.clock(), UTC).isoformat()
            parent_value = self.workspace.value(peer, origin[0]) or {}
            metadata = {
                **parent_value.get("metadata", {}),
                "id": native,
                "parentThreadId": context["desktop_thread_id"],
                "title": message.splitlines()[0][:160],
                "projectRoot": context["project_path"],
                "projectName": context["project_name"],
                "scopeMode": "project",
                "archived": False,
                "pinned": False,
                "mobileGroup": "active",
                "createdAt": now,
                "updatedAt": now,
            }
            context.update(desktop_thread_id=native, title=metadata["title"], revision=1)
            context.pop("parent_thread", None)
            context["workflow"]["remThreads"]["spawned"] = True
            self.workspace.put(
                peer,
                thread,
                {"metadata": metadata, "messages": [], "revision": 1, "mobile_created": True},
            )
            db.execute(
                "INSERT INTO device_grants VALUES (?,?,?)",
                (peer, thread, self._encode(peer, context)),
            )
            db.execute(
                "INSERT INTO device_thread_requests VALUES (?,?,?,?)",
                (peer, request, hashlib.sha256(message.encode()).hexdigest(), thread),
            )
            event = {
                "version": 2,
                "id": str(uuid4()),
                "request_id": request,
                "thread_id": thread,
                "sequence": 0,
                "type": "chat.submit",
                "payload": {"text": message, "attachment_ids": []},
            }
            db.execute(
                "INSERT INTO device_work (peer,request,thread,event,state,turn) "
                "VALUES (?,?,?,?,?,?)",
                (peer, request, thread, self._encode(peer, event), "accepted", str(uuid4())),
            )

    def select_project(self, peer: str, thread: str, root: str) -> None:
        with self.registry.transaction():
            self._trusted(peer)
            self.workspace.select_project(peer, thread, root)

    def grants(self) -> list[dict[str, Any]]:
        with self.registry.lock:
            return [
                {
                    "device_id": row["peer"],
                    "thread_id": row["thread"],
                    **self._decode(row["peer"], row["context"]),
                }
                for row in self.registry.db.execute("SELECT * FROM device_grants")
            ]

    def queued_remote(self) -> list[tuple[str, dict[str, Any]]]:
        with self.registry.lock:
            cancellations = [
                (row["peer"], self._decode(row["peer"], row["event"]))
                for row in self.registry.db.execute(
                    "SELECT c.* FROM device_remote_cancels c JOIN device_remote_requests r "
                    "ON c.peer=r.peer AND c.request=r.request JOIN peers p ON p.device_id=c.peer "
                    "WHERE p.state='active' AND r.state NOT IN "
                    "('completed','cancelled','failed','rejected') LIMIT 4"
                )
            ]
            return cancellations + [
                (row["peer"], self._decode(row["peer"], row["event"]))
                for row in self.registry.db.execute(
                    "SELECT r.* FROM device_remote_requests r JOIN peers p ON "
                    "p.device_id=r.peer WHERE r.state='queued' AND p.state='active' ORDER BY "
                    "r.rowid LIMIT 4"
                )
            ]

    def _snapshot(self, peer: str, kind: str) -> dict[str, Any] | None:
        row = self.registry.db.execute(
            "SELECT event FROM device_peer_snapshots WHERE peer=? AND kind=?", (peer, kind)
        ).fetchone()
        return self._decode(peer, row[0]) if row else None

    def fleet(self) -> list[dict[str, Any]]:
        """Direct peers, then devices a paired desktop reports from its own pairings."""
        with self.registry.lock:
            now = self.registry.clock()
            result = []
            reports: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for peer in self.registry.list():
                snapshot = self._snapshot(peer["device_id"], "fleet.snapshot")
                devices = snapshot["payload"]["devices"] if snapshot else []
                observed = next((d for d in devices if d["device_id"] == peer["device_id"]), None)
                fresh = observed is not None and self._fresh(observed, now)
                entry = {
                    **peer,
                    "name": peer["name"] or (observed or {}).get("name"),
                    "reachability": "reachable" if fresh else "unknown",
                    "running_jobs": observed["running_jobs"]
                    if fresh and observed is not None
                    else None,
                    # Its worker limit and pressure, when it reports them (gofer.core.computers).
                    "load": observed.get("load") if fresh and observed is not None else None,
                }
                if peer["role"] == "desktop" and peer["state"] == "active":
                    # The projects and providers it shares with this computer, for placement.
                    entry["catalog"] = self._fresh_catalog(peer["device_id"], now)
                if peer["role"] == "desktop":
                    entry["endpoint_known"] = (
                        self.registry.db.execute(
                            "SELECT 1 FROM endpoints WHERE device_id=?", (peer["device_id"],)
                        ).fetchone()
                        is not None
                    )
                    shared = self._snapshot(peer["device_id"], "sync.snapshot")
                    # Threads that desktop granted to this one; fleet work needs these IDs.
                    entry["shared_threads"] = [
                        {
                            key: thread[key]
                            for key in ("thread_id", "title", "project_id", "provider", "model")
                        }
                        for thread in (shared["payload"]["threads"] if shared else [])
                    ]
                    if peer["state"] == "active" and fresh:
                        reports.append((entry, snapshot or {}))
                result.append(entry)
            known = {self.registry.identity.device_id, *(p["device_id"] for p in result)}
            for via, snapshot in reports:
                for device in snapshot["payload"]["devices"]:
                    if device["device_id"] in known:
                        continue
                    known.add(device["device_id"])
                    fresh = device["status"] == "reachable" and self._fresh(device, now)
                    result.append(
                        {
                            "device_id": device["device_id"],
                            "name": display_name(device["name"]),
                            "role": device["role"],
                            "state": "indirect",
                            "via": via["device_id"],
                            "via_name": via["name"],
                            "last_seen": device["last_seen"],
                            "reachability": "reachable" if fresh else "unknown",
                            "running_jobs": device["running_jobs"] if fresh else None,
                        }
                    )
            return result

    @staticmethod
    def _fresh(observed: dict[str, Any], now: float) -> bool:
        return bool(
            observed["observed_at"] <= now < observed["fresh_until"]
            and now - observed["observed_at"] <= 60
        )

    def _label(self, row: dict[str, Any]) -> str:
        default = "Desktop" if row["role"] == "desktop" else "Phone"
        name = row.get("name") or default
        if row.get("via"):
            name = f"{name} (via {row.get('via_name') or 'paired desktop'})"
        return name[:80]

    def _advertised(self, peer: str, advert: dict[str, Any]) -> None:
        if self.registry._row(peer)["role"] != "desktop":
            raise PairingError("desktop_advertisement_required")
        self.registry.save_name(peer, advert["name"], chosen=False)
        endpoint = advert["lan_endpoint"]
        if endpoint is None:
            return
        from gofer.devices.pairing import lan_endpoint

        checked = lan_endpoint(endpoint)
        assert checked is not None
        # A loopback address names this machine; only trust it between local test peers.
        if ipaddress.ip_address(checked["host"]).is_loopback and self.lan_endpoint is not None:
            return
        self.registry.save_endpoint(peer, checked)

    def _session_event(
        self,
        peer: str,
        kind: str,
        payload: dict[str, Any],
        *,
        thread_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        with self.registry.transaction():
            self._trusted(peer)
            event = {
                "version": 2,
                "id": str(uuid4()),
                "thread_id": thread_id,
                "request_id": request_id or str(uuid4()),
                "sequence": self._next_outbound_sequence(peer),
                "type": kind,
                "payload": payload,
            }
        _VALIDATOR.validate(event)
        return event

    def hosting(self) -> list[str]:
        with self.registry.lock:
            return [
                row[0]
                for row in self.registry.db.execute(
                    "SELECT h.peer FROM device_thread_hosts h JOIN peers p ON p.device_id=h.peer "
                    "WHERE p.state='active'"
                )
            ]

    def enable_hosting(self, peer: str, enabled: bool) -> None:
        """Local choice: let a paired desktop start Rem threads that run on this one."""
        with self.registry.transaction():
            self._trusted(peer)
            if self.registry._row(peer)["role"] != "desktop":
                raise PairingError("desktop_required")
            db = self.registry.db
            if enabled:
                db.execute("INSERT OR IGNORE INTO device_thread_hosts VALUES (?)", (peer,))
                return
            db.execute("DELETE FROM device_thread_hosts WHERE peer=?", (peer,))
            # Turning it off revokes every thread that desktop started here.
            self._revoke_thread_tree(peer, host_template(peer))

    def host_template(self, peer: str, context: dict[str, Any]) -> None:
        """Local API only: this desktop's open projects and provider policies.

        Threads the paired desktop creates copy this context, then choose a project,
        provider, model and permission mode within it, exactly like a phone thread.
        """
        with self.registry.transaction():
            self._trusted(peer)
            if peer not in self.hosting():
                raise PairingError("thread_hosting_disabled")
            context = {
                **context,
                "title": "Threads from a paired desktop",
                "allow_thread_create": True,
                "revision": 1,
            }
            context.pop("desktop_thread_id", None)
            self.registry.db.execute(
                "INSERT OR REPLACE INTO device_grants VALUES (?,?,?)",
                (peer, host_template(peer), self._encode(peer, context)),
            )

    def remote_thread(self, peer: str, local: str) -> str | None:
        with self.registry.lock:
            row = self.registry.db.execute(
                "SELECT remote FROM device_remote_threads WHERE peer=? AND local=?", (peer, local)
            ).fetchone()
            return str(row[0]) if row else None

    def save_remote_thread(self, peer: str, local: str, remote: str | None) -> None:
        with self.registry.transaction():
            self._trusted(peer)
            if remote is None:
                self.registry.db.execute(
                    "DELETE FROM device_remote_threads WHERE peer=? AND local=?", (peer, local)
                )
            else:
                self.registry.db.execute(
                    "INSERT OR REPLACE INTO device_remote_threads VALUES (?,?,?)",
                    (peer, local, device_id(remote)),
                )

    def acknowledgment(self, peer: str) -> dict[str, Any]:
        """Leads every desktop session: acknowledges what arrived, asks for shared threads.

        Without it the peer replays its entire outbox on each new session and the
        outbox eventually reaches its quota.
        """
        with self.registry.lock:
            row = self.registry.db.execute(
                "SELECT value FROM device_remote_cursors WHERE peer=?", (peer,)
            ).fetchone()
        return self._session_event(
            peer, "sync.request", {"after_sequence": row[0] if row else None, "limit": 100}
        )

    def advertisement(self, peer: str) -> dict[str, Any]:
        advert: dict[str, Any] = {"name": self.name, "lan_endpoint": self.listener_endpoint}
        ask = peer not in self._plain_announcements
        hold = ask and peer not in self._load_only_announcements
        kinds = hold and peer not in self._hold_only_announcements
        if ask:
            advert["load"] = True
        if hold:
            advert["memory_hold"] = True
        if kinds:
            advert["running_by_kind"] = True
        event = self._session_event(peer, "fleet.request", {"advertise": advert})
        if ask:
            if len(self._load_requests) >= 1000:
                self._load_requests.clear()
            self._load_requests[event["request_id"]] = (
                peer,
                "kinds" if kinds else "hold" if hold else "load",
            )
        return event

    def _load_reading(
        self, *, memory_hold: bool = False, running_by_kind: bool = False
    ) -> dict[str, Any] | None:
        """This desktop's worker limit, running workers and pressure, when it has them.

        With ``memory_hold`` the reading keeps its memory hold, and with
        ``running_by_kind`` its counts by kind; a field not asked for (an older desktop
        asked) is left out.
        """
        service = getattr(self.desktop, "computers", None)
        if service is None:
            return None
        try:
            reading = service.load_reading()
        except Exception:
            logger.warning("Couldn't read this computer's load for a paired desktop", exc_info=True)
            return None
        if isinstance(reading, dict):
            left_out = {
                key
                for key, asked in (
                    ("memory_hold", memory_hold),
                    ("running_by_kind", running_by_kind),
                )
                if not asked
            }
            reading = {key: value for key, value in reading.items() if key not in left_out}
        return reading if _LOAD_VALIDATOR.is_valid(reading) else None

    def catalog_probe(self, peer: str) -> dict[str, Any] | None:
        """A catalog request asking a paired desktop for its repository fingerprints.

        Sent after each fleet probe. Placement reads the answer through ``fleet``:
        the projects and providers that desktop's grants for this computer cover,
        each project with its fingerprint. None while a desktop that refused the
        request (an older version) waits ``CATALOG_RETRY`` seconds to be asked again.
        """
        if self.registry._row(peer)["role"] != "desktop":
            raise PairingError("mobile_is_not_worker")
        with self.registry.lock:
            known = self._peer_catalogs.get(peer)
            if (
                known is not None
                and known["state"] == "unsupported"
                and self.registry.clock() - known["at"] < CATALOG_RETRY
            ):
                return None
        event = self._session_event(peer, "catalog.request", {"fingerprints": True})
        with self.registry.lock:
            if len(self._catalog_requests) >= 1000:
                self._catalog_requests.clear()
            self._catalog_requests[event["request_id"]] = peer
        return event

    def _catalog_answer(self, event: dict[str, Any]) -> dict[str, Any]:
        """What a desktop's answer to ``catalog_probe`` says, as placement reads it."""
        at = self.registry.clock()
        if event["type"] == "error":
            # An older desktop refuses the field it doesn't know as unsupported.
            unsupported = event["payload"].get("code") == "unsupported"
            return {"state": "unsupported" if unsupported else "refused", "at": at}
        payload = event["payload"]
        return {
            "state": "shared",
            "at": at,
            "projects": [
                {key: project[key] for key in ("id", "name", "fingerprint") if key in project}
                for project in payload["projects"]
            ],
            "providers": [provider["id"] for provider in payload["providers"]],
        }

    def _fresh_catalog(self, peer: str, now: float) -> dict[str, Any] | None:
        """A desktop's last catalog answer while it counts, without its time."""
        known = self._peer_catalogs.get(peer)
        if known is None:
            return None
        limit = CATALOG_RETRY if known["state"] == "unsupported" else CATALOG_FRESH
        if not 0 <= now - known["at"] <= limit:
            return None
        return {key: value for key, value in known.items() if key != "at"}

    def fleet_probe(self, peer: str) -> dict[str, Any]:
        """A read-only status request that is never journaled as delegated work."""
        if self.registry._row(peer)["role"] != "desktop":
            raise PairingError("mobile_is_not_worker")
        return self._session_event(peer, "fleet.request", {})

    def attachment(self, peer: str, thread: str, identifier: str) -> dict[str, Any]:
        offer, data = self.files.content(peer, thread, identifier)
        return {"id": identifier, "name": offer["name"], "mime": offer["mime"], "data": data}

    def record_attachments(
        self, peer: str, request_id: str, attachments: list[dict[str, Any]]
    ) -> None:
        """Point a phone message's attachments at their saved copies in the desktop thread.

        Phone uploads expire, so desktop turns that resend this message need the
        file name the desktop saved it under.
        """
        with self.registry.transaction():
            self._trusted(peer)
            self.registry.db.execute(
                "INSERT INTO device_message_attachments VALUES (?,?,?) "
                "ON CONFLICT(peer,request) DO UPDATE SET value=excluded.value",
                (peer, request_id, self._encode(peer, {"attachments": attachments})),
            )

    def _prune_acknowledged(self, peer: str, cursor: int) -> None:
        # A peer's cursor is its application receipt, not an HTTP publication result.
        # Keep recent conversation events for desktop provider context; never remove
        # events beyond the peer's acknowledged cursor.
        self.registry.db.execute(
            "DELETE FROM device_outbox WHERE peer=? AND sequence<=? AND sequence NOT IN "
            "(SELECT sequence FROM device_outbox WHERE peer=? ORDER BY sequence DESC "
            "LIMIT ?)",
            (peer, cursor, peer, RETAIN_ACKNOWLEDGED),
        )
        self.desktop.wake.forget_delivered(peer, cursor)
        self.registry.db.execute(
            "DELETE FROM device_chunk_receipts WHERE peer=? AND sequence NOT IN (SELECT "
            "sequence FROM device_outbox WHERE peer=?)",
            (peer, peer),
        )


PhoneEvent = Callable[
    [DeviceApplication, str, dict[str, Any], dict[str, Any]], list[dict[str, Any]]
]

# The phone event kinds DeviceApplication answers itself, once the workspace, remote
# and desktop handlers pass an event on. Any other kind is answered "unsupported".
EVENT_HANDLERS: dict[str, PhoneEvent] = {
    "thread.create": DeviceApplication._thread_create,
    "sync.request": DeviceApplication._sync_request,
    "fleet.request": DeviceApplication._fleet_request,
    "file.offer": DeviceApplication._file_event,
    "file.chunk": DeviceApplication._file_event,
    "file.request": DeviceApplication._file_event,
    "file.status": DeviceApplication._file_event,
    "file.cancel": DeviceApplication._file_event,
    "job.get": DeviceApplication._job_lookup,
    "job.cancel": DeviceApplication._job_lookup,
    "request.cancel": DeviceApplication._request_cancel,
    "job.submit": DeviceApplication._job_submit,
    "chat.submit": DeviceApplication._submit,
}
