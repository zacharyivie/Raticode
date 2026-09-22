"""Durable application inbox and outbox shared by every authenticated carrier.

Pairing grants communication only. Local thread grants select execution context;
remote events cannot supply paths, provider credentials, or permission modes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from gofer.devices.files import CHUNK, DeviceFiles
from gofer.devices.registry import DeviceRegistry, PairingError, device_id

_SCHEMA = json.loads((Path(__file__).parent / "protocol/v2/event.schema.json").read_text())
_VALIDATOR = Draft202012Validator(_SCHEMA)
OUTBOX_EVENTS = 10000
OUTBOX_BYTES = 64 * 1024 * 1024
RETAIN_ACKNOWLEDGED = 400


class DeviceApplication:
    lan_endpoint: dict[str, Any] | None = None

    def __init__(self, registry: DeviceRegistry) -> None:
        self.registry = registry
        self.rem_ready: Callable[[], bool] = lambda: False
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
              CREATE TABLE IF NOT EXISTS device_work (
                peer TEXT NOT NULL,request TEXT NOT NULL,thread TEXT NOT NULL,
                event BLOB NOT NULL,state TEXT NOT NULL,turn TEXT NOT NULL,
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
              CREATE TABLE IF NOT EXISTS device_remote_results (
                peer TEXT NOT NULL, id TEXT NOT NULL,event BLOB NOT NULL,
                PRIMARY KEY(peer,id));
              CREATE TABLE IF NOT EXISTS device_outbox (
                sequence INTEGER NOT NULL,peer TEXT NOT NULL,event BLOB NOT NULL,
                PRIMARY KEY(peer,sequence));
            """)
        self.files = DeviceFiles(registry)
        with registry.transaction():
            for row in registry.db.execute(
                "SELECT * FROM device_work WHERE state='running'"
            ).fetchall():
                self._status(
                    row["peer"],
                    self._decode(row["peer"], row["event"]),
                    "outcome_unknown",
                    "Desktop stopped during dispatch; review locally before retrying",
                )
            registry.db.execute(
                "UPDATE device_work SET state='outcome_unknown' WHERE state='running'"
            )

        from gofer.devices.workspace import DeviceWorkspace

        self.workspace = DeviceWorkspace(self)

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
                "device_transcript",
                "device_message_attachments",
                "device_sequences",
                "device_chunk_receipts",
                "device_file_sends",
                "device_remote_requests",
                "device_remote_results",
                "device_outbox",
                "device_files",
                "device_file_chunks",
                "device_workspace_peers",
                "device_workspace_threads",
                "device_workspace_versions",
                "device_workspace_denied",
                "relay_origins",
            ):
                db.execute(f"DELETE FROM {table} WHERE peer=?", (peer,))
            db.execute("DELETE FROM endpoints WHERE device_id=?", (peer,))
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
        sequence = db.execute(
            "SELECT MAX(value)+1 FROM (SELECT COALESCE(MAX(sequence),0) AS value FROM "
            "device_outbox WHERE peer=? UNION ALL SELECT value FROM device_sequences "
            "WHERE peer=?)",
            (peer, peer),
        ).fetchone()[0]
        db.execute("INSERT OR REPLACE INTO device_sequences VALUES (?,?)", (peer, sequence))
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

    def handle(self, peer: str, event: dict[str, Any]) -> list[dict[str, Any]]:
        if not _VALIDATOR.is_valid(event):
            raise PairingError("invalid_application_event")
        digest = hashlib.sha256(
            json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        with self.registry.transaction():
            self._trusted(peer)
            db = self.registry.db
            prior = db.execute(
                "SELECT digest FROM device_inbox WHERE peer=? AND id=?", (peer, event["id"])
            ).fetchone()
            if prior is not None:
                if prior[0] != digest:
                    raise PairingError("event_id_conflict")
                return self.pending(peer)
            if (
                db.execute("SELECT COUNT(*) FROM device_inbox WHERE peer=?", (peer,)).fetchone()[0]
                >= 100000
            ):
                raise PairingError("inbox_quota")
            if db.execute(
                "SELECT 1 FROM device_inbox WHERE peer=? AND sequence=?", (peer, event["sequence"])
            ).fetchone():
                raise PairingError("sequence_conflict")
            db.execute(
                "INSERT INTO device_inbox VALUES (?,?,?,?)",
                (peer, event["id"], digest, event["sequence"]),
            )
            db.execute(
                "UPDATE peers SET last_seen=? WHERE device_id=?", (self.registry.clock(), peer)
            )
            kind, payload = event["type"], event["payload"]
            workspace_result = self.workspace.handle(peer, event)
            if workspace_result is not None:
                return workspace_result
            if kind == "thread.create":

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
                        return rejected(
                            "conflict", "This creation request already has different content."
                        )
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
                    matches = [
                        (row["thread"], self._decode(peer, row["context"])) for row in templates
                    ]
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
                                value.get(field, [] if field == "resource_ids" else None)
                                == payload[field]
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
                            createdAt=datetime.fromtimestamp(
                                self.registry.clock(), UTC
                            ).isoformat(),
                            updatedAt=datetime.fromtimestamp(
                                self.registry.clock(), UTC
                            ).isoformat(),
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
            if kind == "sync.request":
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
                                self.workspace.details(peer, grant["thread"], context)
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
                                    }
                                }
                                if payload.get("include_connection")
                                else {}
                            ),
                        },
                    )
                )
                return events
            if kind == "fleet.request":
                now = int(self.registry.clock())
                devices = [
                    {
                        "device_id": row["device_id"],
                        "name": "Paired desktop" if row["role"] == "desktop" else "Paired mobile",
                        "role": row["role"],
                        "status": row["reachability"],
                        "last_seen": int(row["last_seen"]) if row["last_seen"] else None,
                        "observed_at": now,
                        "fresh_until": now + 60,
                        "running_jobs": row["running_jobs"] or [],
                    }
                    for row in self.fleet()
                    if row["state"] == "active"
                ][:100]
                jobs = [
                    row[0]
                    for row in db.execute("SELECT request FROM device_work WHERE state='running'")
                ]
                if self.local_jobs is not None:
                    remote_turns = {
                        row[0]
                        for row in db.execute("SELECT turn FROM device_work WHERE state='running'")
                    }
                    jobs.extend(
                        str(
                            uuid5(
                                NAMESPACE_URL, "raticode-job:" + j["thread_id"] + ":" + j["turn_id"]
                            )
                        )
                        for j in self.local_jobs()
                        if j["turn_id"] not in remote_turns
                    )
                devices.insert(
                    0,
                    {
                        "device_id": self.registry.identity.device_id,
                        "name": "Raticode Desktop",
                        "role": "desktop",
                        "status": "reachable",
                        "last_seen": now,
                        "observed_at": now,
                        "fresh_until": now + 60,
                        "running_jobs": jobs[:100],
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
            if kind in ("file.offer", "file.chunk", "file.request", "file.status", "file.cancel"):
                grant = db.execute(
                    "SELECT 1 FROM device_grants WHERE peer=? AND thread=?",
                    (peer, event["thread_id"]),
                ).fetchone()
                if grant is None:
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
                    db.execute(
                        "DELETE FROM device_file_sends WHERE peer=? AND id=?",
                        (peer, payload["file_id"]),
                    )
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
                if payload["state"] in ("receiving", "available"):
                    db.execute(
                        "DELETE FROM device_outbox WHERE peer=? AND sequence IN (SELECT sequence "
                        "FROM device_chunk_receipts WHERE peer=? AND id=? AND end_offset<=?)",
                        (peer, peer, payload["file_id"], payload["received_size"]),
                    )
                    db.execute(
                        "DELETE FROM device_chunk_receipts WHERE peer=? AND id=? AND end_offset<=?",
                        (peer, payload["file_id"], payload["received_size"]),
                    )
                sending = db.execute(
                    "SELECT * FROM device_file_sends WHERE peer=? AND id=? AND thread=?",
                    (peer, payload["file_id"], event["thread_id"]),
                ).fetchone()
                if sending is not None and payload["state"] == "receiving":
                    return [
                        self._file_chunk(peer, event, payload["file_id"], payload["received_size"])
                    ]
                if sending is not None and payload["state"] in ("available", "cancelled", "failed"):
                    db.execute(
                        "DELETE FROM device_file_sends WHERE peer=? AND id=?",
                        (peer, payload["file_id"]),
                    )
                return [self._emit(peer, event, "file.status", payload)]
            if kind in ("job.get", "job.cancel"):
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
            if kind == "request.cancel":
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
                        self._status(
                            peer, event, "cancel_requested", "Cancellation requested on desktop"
                        )
                    ]
                return self.pending(peer)
            if kind == "job.submit":
                grant_row = db.execute(
                    "SELECT context FROM device_grants WHERE peer=? AND thread=?",
                    (peer, event["thread_id"]),
                ).fetchone()
                context = self._decode(peer, grant_row[0]) if grant_row else {}
                if (
                    self.registry._row(peer)["role"] != "desktop"
                    or payload["target_device_id"] != self.registry.identity.device_id
                    or payload["project_id"] != context.get("project_id")
                    or payload["expected_policy_revision"] != context.get("policy_revision", 1)
                    or payload["resource_ids"]
                ):
                    return [
                        self._emit(
                            peer,
                            event,
                            "job.status",
                            {
                                "job_id": payload["job_id"],
                                "state": "rejected",
                                "revision": 1,
                                "detail": "Target desktop project grant required",
                            },
                        )
                    ]
                job_digest = hashlib.sha256(
                    json.dumps(
                        {"thread": event["thread_id"], "payload": payload}, sort_keys=True
                    ).encode()
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
            if kind in ("chat.submit", "job.submit"):
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
                            or context.get("provider")
                            not in context.get("provider_permissions", {})
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
                    offer, _ = self.files.content(peer, event["thread_id"], attachment)
                    attachment_metadata.append(
                        {
                            "id": attachment,
                            "name": offer["name"],
                            "size": offer["size"],
                            "mime": offer["mime"],
                        }
                    )
                if (
                    db.execute(
                        "SELECT COUNT(*) FROM device_work WHERE peer=? AND state IN "
                        "('accepted','running')",
                        (peer,),
                    ).fetchone()[0]
                    >= 100
                ):
                    raise PairingError("work_quota")
                db.execute(
                    "INSERT INTO device_work VALUES (?,?,?,?,?,?)",
                    (
                        peer,
                        event["request_id"],
                        event["thread_id"],
                        self._encode(peer, event),
                        "accepted",
                        str(uuid4()),
                    ),
                )
                if kind == "chat.submit":
                    self.workspace.name_from_message(
                        peer, event["thread_id"], payload.get("text", "")
                    )
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
                    else self._emit(
                        peer, event, "chat.accepted", {"message_id": event["request_id"]}
                    )
                ]
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

    def claim(self) -> dict[str, Any] | None:
        with self.registry.transaction():
            row = self.registry.db.execute(
                "SELECT w.*,g.context FROM device_work w JOIN peers p ON p.device_id=w.peer "
                "JOIN device_grants g ON g.peer=w.peer AND g.thread=w.thread WHERE "
                "w.state='accepted' AND p.state='active' AND NOT EXISTS (SELECT 1 FROM "
                "device_work busy WHERE busy.thread=w.thread AND busy.state='running') "
                "ORDER BY w.rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return None
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
                "event": event,
                "turn_id": row["turn"],
                "context": self._decode(row["peer"], row["context"]),
                "messages": self.history(
                    row["peer"], row["thread"], through_request=row["request"]
                ),
                "attachments": attachments,
            }

    def history(
        self, peer: str, thread: str, *, through_request: str | None = None
    ) -> list[dict[str, str]]:
        with self.registry.lock:
            if self.workspace.value(peer, thread) is not None:
                return [
                    {"role": m["role"], "content": m["body"]}
                    for m in self.workspace.messages(
                        peer, thread, for_dispatch=True, through_request=through_request
                    )[-200:]
                ]
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

    def complete(self, peer: str, request_id: str, text: str, error: str | None = None) -> None:
        with self.registry.transaction():
            self._trusted(peer)
            row = self.registry.db.execute(
                "SELECT * FROM device_work WHERE peer=? AND request=?", (peer, request_id)
            ).fetchone()
            if row is not None and row["state"] == "cancelled":
                event = self._decode(peer, row["event"])
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
            elif event["type"] == "fleet.snapshot":
                terminal = "completed"
            if terminal in ("completed", "failed", "cancelled", "outcome_unknown", "rejected"):
                self.registry.db.execute(
                    "UPDATE device_remote_requests SET state=? WHERE peer=? AND request=?",
                    (terminal, peer, event["request_id"]),
                )
            self.registry.db.execute(
                "UPDATE peers SET last_seen=? WHERE device_id=?", (self.registry.clock(), peer)
            )

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

    def _file_chunk(
        self, peer: str, event: dict[str, Any], identifier: str, offset: int
    ) -> dict[str, Any]:
        offer, data = self.files.content(peer, event["thread_id"], identifier)
        if not 0 <= offset <= len(data):
            raise PairingError("file_chunk_offset")
        chunk = data[offset : offset + CHUNK]
        result = self._emit(
            peer,
            event,
            "file.chunk",
            {
                "file_id": identifier,
                "offset": offset,
                "data": base64.urlsafe_b64encode(chunk).rstrip(b"=").decode(),
                "eof": offset + len(chunk) == len(data),
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
            root = Path(context["project_path"])
            identity = context["project_identity"]
            if context.get("desktop_parity"):
                candidates = [
                    {"root": str(root), "identity": identity},
                    *context.get("projects", []),
                ]
                selected = next(
                    (
                        p
                        for p in candidates
                        if Path(path).absolute().is_relative_to(Path(p["root"]))
                    ),
                    None,
                )
                if selected is None:
                    raise PairingError("file_not_in_open_desktop_project")
                root, identity = Path(selected["root"]), selected["identity"]
            offer = self.files.snapshot(peer, thread, root, Path(path), identity)
            return self._emit(
                peer, {"thread_id": thread, "request_id": str(uuid4())}, "file.offer", offer
            )

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
            if not context.get("desktop_parity"):
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
                "INSERT INTO device_work VALUES (?,?,?,?,?,?)",
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
            return [
                (row["peer"], self._decode(row["peer"], row["event"]))
                for row in self.registry.db.execute(
                    "SELECT r.* FROM device_remote_requests r JOIN peers p ON "
                    "p.device_id=r.peer WHERE r.state='queued' AND p.state='active' ORDER BY "
                    "r.rowid LIMIT 4"
                )
            ]

    def fleet(self) -> list[dict[str, Any]]:
        with self.registry.lock:
            now = self.registry.clock()
            result = []
            for peer in self.registry.list():
                observed = None
                for row in self.registry.db.execute(
                    "SELECT event FROM device_remote_results WHERE peer=? ORDER BY rowid DESC",
                    (peer["device_id"],),
                ):
                    event = self._decode(peer["device_id"], row[0])
                    if event["type"] == "fleet.snapshot":
                        observed = next(
                            (
                                d
                                for d in event["payload"]["devices"]
                                if d["device_id"] == peer["device_id"]
                            ),
                            None,
                        )
                        break
                fresh = (
                    observed is not None
                    and observed["observed_at"] <= now < observed["fresh_until"]
                    and now - observed["observed_at"] <= 60
                )
                result.append(
                    {
                        **peer,
                        "reachability": "reachable" if fresh else "unknown",
                        "running_jobs": observed["running_jobs"]
                        if fresh and observed is not None
                        else None,
                    }
                )
            return result

    def attachment(self, peer: str, thread: str, identifier: str) -> dict[str, Any]:
        offer, data = self.files.content(peer, thread, identifier)
        return {"name": offer["name"], "mime": offer["mime"], "data": data}

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
        self.registry.db.execute(
            "DELETE FROM device_chunk_receipts WHERE peer=? AND sequence NOT IN (SELECT "
            "sequence FROM device_outbox WHERE peer=?)",
            (peer, peer),
        )
