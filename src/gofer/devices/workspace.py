"""Explicit desktop sharing of renderer-owned conversations with paired phones."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from gofer.devices.registry import PairingError


def attachments(message: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    raw = message.get("attachments", [])
    for item in (raw if isinstance(raw, list) else [])[:8]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "id": str(item.get("id", ""))[:180],
                "name": str(item.get("name", "Attachment"))[:160],
                "size": max(0, min(10485760, int(item.get("size", 0) or 0))),
                "mime": str(item.get("mime", "application/octet-stream"))[:120],
            }
        )
    return result


def presentation(message: dict[str, Any], *, bounded: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if message.get("kind") in {"thought", "turn-summary", "error", "final", "system"}:
        result["kind"] = message["kind"]
    if isinstance(message.get("groupId"), str):
        result["groupId"] = message["groupId"][:180]
    if isinstance(message.get("running"), bool):
        result["running"] = message["running"]
    for key in ("startedAt", "durationMs"):
        value = message.get(key)
        if isinstance(value, (int, float)) and 0 <= value <= 9007199254740991:
            result[key] = int(value)
    if isinstance(message.get("completedAt"), str):
        result["completedAt"] = message["completedAt"][:80]
    raw = message.get("trace")
    if isinstance(raw, dict):
        trace = {
            key: str(raw[key])[:4000] if bounded else str(raw[key])
            for key in (
                "id",
                "kind",
                "title",
                "detail",
                "body",
                "input",
                "output",
                "category",
                "shell",
                "command",
                "status",
            )
            if raw.get(key) is not None
        }
        # Leave room for text and attachment metadata inside the 45 KB history page.
        while bounded and len(json.dumps(trace).encode()) > 12000:
            key = max(trace, key=lambda k: len(json.dumps(trace[k]).encode()))
            trace[key] = trace[key][: len(trace[key]) // 2]
            result["truncated"] = True
        if bounded and any(len(str(raw.get(key, ""))) > 4000 for key in trace):
            result["truncated"] = True
        result["trace"] = trace
    return result


def wire_id(value: str) -> str:
    return str(uuid5(NAMESPACE_URL, "raticode-desktop-thread:" + value))


class DeviceWorkspace:
    def __init__(self, application: Any) -> None:
        self.app = application
        self.db = application.registry.db
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS device_workspace_threads (
            peer TEXT NOT NULL, thread TEXT NOT NULL, value BLOB NOT NULL,
            PRIMARY KEY(peer,thread));
          CREATE TABLE IF NOT EXISTS device_workspace_versions (
            peer TEXT NOT NULL, thread TEXT NOT NULL, version INTEGER NOT NULL,
            PRIMARY KEY(peer,thread));
          CREATE TABLE IF NOT EXISTS device_workspace_denied (
            peer TEXT NOT NULL, thread TEXT NOT NULL, PRIMARY KEY(peer,thread));
        """)
        self.providers: list[dict[str, Any]] = []

    def enabled(self) -> list[str]:
        return [
            r[0]
            for r in self.db.execute(
                "SELECT w.peer FROM device_workspace_peers w JOIN peers p ON p.device_id=w.peer "
                "WHERE p.state='active'"
            )
        ]

    def enable(self, peer: str, enabled: bool) -> None:
        with self.app.registry.transaction():
            self.app._trusted(peer)
            if self.app.registry._row(peer)["role"] != "controller":
                raise PairingError("phone_required")
            if enabled:
                self.db.execute("INSERT OR IGNORE INTO device_workspace_peers VALUES (?)", (peer,))
            else:
                self.db.execute("DELETE FROM device_workspace_peers WHERE peer=?", (peer,))
                for row in self.db.execute(
                    "SELECT thread FROM device_workspace_threads WHERE peer=?", (peer,)
                ).fetchall():
                    self.app._revoke_thread_tree(peer, row[0])
                self.db.execute("DELETE FROM device_workspace_threads WHERE peer=?", (peer,))

    def deny(self, peer: str, thread: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO device_workspace_denied VALUES (?,?)", (peer, thread)
        )

    def value(self, peer: str, thread: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT value FROM device_workspace_threads WHERE peer=? AND thread=?", (peer, thread)
        ).fetchone()
        return self.app._decode(peer, row[0]) if row else None

    def put(self, peer: str, thread: str, value: dict[str, Any]) -> None:
        encoded = self.app._encode(peer, value)
        used = self.db.execute(
            "SELECT COALESCE(SUM(LENGTH(value)),0) FROM device_workspace_threads "
            "WHERE peer=? AND thread<>?",
            (peer, thread),
        ).fetchone()[0]
        if used + len(encoded) > 64 * 1024 * 1024:
            raise PairingError("workspace_history_quota")
        self.db.execute(
            "INSERT OR REPLACE INTO device_workspace_threads VALUES (?,?,?)",
            (peer, thread, encoded),
        )
        self.db.execute(
            "INSERT INTO device_workspace_versions VALUES (?,?,1) ON CONFLICT(peer,thread) "
            "DO UPDATE SET version=version+1",
            (peer, thread),
        )

    def sync_token(self, peer: str, thread: str) -> str:
        version = self.db.execute(
            "SELECT version FROM device_workspace_versions WHERE peer=? AND thread=?",
            (peer, thread),
        ).fetchone()
        last = self.db.execute(
            "SELECT COALESCE(MAX(rowid),0) FROM device_work WHERE peer=? AND thread=?",
            (peer, thread),
        ).fetchone()[0]
        grant = self.db.execute(
            "SELECT 1 FROM device_grants WHERE peer=? AND thread=?", (peer, thread)
        ).fetchone()
        turns = self.local_turns(peer, thread)
        journal = hashlib.sha256(json.dumps(turns).encode()).hexdigest()
        return f"{version[0] if version else 0}:{last}:{bool(grant)}:{journal}"

    def local_turns(self, peer: str, thread: str) -> list[dict[str, Any]]:
        value = self.value(peer, thread)
        native = (value or {}).get("metadata", {}).get("id")
        jobs = self.app.local_chat_jobs
        return [
            {
                "request": row["request"],
                "turn": row["turn"],
                "state": row["state"],
                "revision": jobs.revision(native, row["turn"]) if jobs and native else "",
            }
            for row in self.db.execute(
                "SELECT request,turn,state FROM device_work WHERE peer=? AND thread=? "
                "ORDER BY rowid DESC",
                (peer, thread),
            ).fetchall()[::-1]
        ]

    def desktop_messages(self, peer: str, thread: str) -> list[dict[str, Any]]:
        from gofer.ui.device_transcript import project_device_turn

        messages = self.messages(peer, thread)
        value = self.value(peer, thread)
        native = (value or {}).get("metadata", {}).get("id")
        jobs = self.app.local_chat_jobs
        for turn in self.local_turns(peer, thread):
            request = turn["request"]
            for index, message in enumerate(messages):
                if message["id"] == request:
                    messages[index] = {**message, "origin": "phone"}
            if not jobs or not native or not any(m["id"] == request for m in messages):
                continue
            events = jobs.snapshot(native, turn["turn"])
            projected = [
                m
                for m in project_device_turn(request, events, turn["state"])
                if m["id"] not in set((value or {}).get("removed_ids", []))
            ]
            if not projected:
                continue
            replacements = {m["id"] for m in projected}
            messages = [m for m in messages if m["id"] not in replacements]
            index = next(
                (i + 1 for i, m in enumerate(messages) if m["id"] == request), len(messages)
            )
            messages[index:index] = projected
        return messages

    def exchange(
        self,
        peer: str,
        metadata: dict[str, Any],
        messages: list[dict[str, Any]],
        context: dict[str, Any],
        revision: int | None,
    ) -> dict[str, Any]:
        """Local authenticated API only. Project path grants are checked by the server."""
        with self.app.registry.transaction():
            self.app._trusted(peer)
            if peer not in self.enabled():
                raise PairingError("workspace_sharing_disabled")
            native = metadata.get("id")
            if not isinstance(native, str) or not native or len(native) > 160:
                raise PairingError("invalid_desktop_thread")
            thread = wire_id(native)
            if self.db.execute(
                "SELECT 1 FROM device_workspace_denied WHERE peer=? AND thread=?", (peer, thread)
            ).fetchone():
                return {"revoked": True}
            old = self.value(peer, thread)
            if old and (old.get("delete_requested") or revision != old["revision"]):
                return self.export(peer, thread)
            if len(messages) > 10000 or len(json.dumps(messages).encode()) > 8 * 1024 * 1024:
                raise PairingError("workspace_history_quota")
            clean = []
            for index, message in enumerate(messages):
                if message.get("role") not in {"user", "assistant", "system"} or not isinstance(
                    message.get("body"), str
                ):
                    continue
                clean.append(
                    {
                        "id": str(message.get("id", f"legacy-{index}"))[:160],
                        "role": message["role"],
                        "body": message["body"],
                        **presentation(message, bounded=False),
                        **(
                            {"attachments": attachments(message)}
                            if message.get("attachments")
                            else {}
                        ),
                    }
                )
            context = {
                **context,
                "desktop_thread_id": native,
                "allow_thread_create": bool(context.get("project_path")),
                "revision": (old or {}).get("revision", 0) + 1,
            }
            current = self.db.execute(
                "SELECT context FROM device_grants WHERE peer=? AND thread=?", (peer, thread)
            ).fetchone()
            if current:
                previous = self.app._decode(peer, current[0])
                if previous.get("parent_thread"):
                    context["parent_thread"] = previous["parent_thread"]
                    context["allow_thread_create"] = False
                changed = any(
                    previous.get(k) != context.get(k)
                    for k in ("project_path", "permission_mode", "workflow", "project_identity")
                )
                if changed and not context.get("desktop_parity"):
                    self.app._revoke_thread_tree(peer, thread)
            self.db.execute(
                "INSERT OR REPLACE INTO device_grants VALUES (?,?,?)",
                (peer, thread, self.app._encode(peer, context)),
            )
            self.put(
                peer,
                thread,
                {
                    "metadata": metadata,
                    "messages": clean,
                    "revision": context["revision"],
                    "removed_ids": sorted(
                        (
                            set((old or {}).get("removed_ids", []))
                            | {m["id"] for m in (old or {}).get("messages", [])}
                        )
                        - {m["id"] for m in clean}
                    ),
                },
            )
            return self.export(peer, thread)

    def remove(self, peer: str, thread: str) -> None:
        with self.app.registry.transaction():
            self.app._trusted(peer)
            if peer not in self.enabled():
                raise PairingError("workspace_sharing_disabled")
            if self.value(peer, thread) is not None:
                self.deny(peer, thread)
                self.app._revoke_thread_tree(peer, thread)
                self.db.execute(
                    "DELETE FROM device_workspace_threads WHERE peer=? AND thread=?", (peer, thread)
                )

    def export(self, peer: str, thread: str) -> dict[str, Any]:
        value = self.value(peer, thread)
        if value is None:
            return {}
        grant = self.db.execute(
            "SELECT context FROM device_grants WHERE peer=? AND thread=?", (peer, thread)
        ).fetchone()
        if grant is None:
            return {"revoked": True}
        context = self.app._decode(peer, grant[0])
        return {
            "thread_id": thread,
            "sync_token": self.sync_token(peer, thread),
            "mobile_created": value.get("mobile_created", False),
            "revision": value["revision"],
            "base_metadata": value["metadata"],
            "context_modified": value.get("context_modified", False),
            "organization_modified": bool(value.get("organization")),
            "title_modified": "auto_title" in value,
            "delete_requested": value.get("delete_requested", False),
            "metadata": {
                **value["metadata"],
                **value.get("organization", {}),
                **({"title": value["auto_title"]} if "auto_title" in value else {}),
                "provider": context["provider"],
                "model": context["model"],
                **(
                    {
                        "effort": context.get("effort"),
                        "permissionsByProvider": context.get("provider_permissions", {}),
                        "projectRoot": context.get("project_path")
                        if context.get("project_id")
                        else "",
                        "projectName": context.get("project_name", "Global")
                        if context.get("project_id")
                        else "Global",
                        "scopeMode": context.get("scope_mode", "project"),
                    }
                    if context.get("desktop_parity")
                    else {}
                ),
            },
            "messages": self.desktop_messages(peer, thread),
            "active_turn": next(
                (t["turn"] for t in self.local_turns(peer, thread) if t["state"] == "running"), None
            ),
            "running": any(
                t["state"] in {"accepted", "running", "cancel_requested"}
                for t in self.local_turns(peer, thread)
            ),
        }

    def messages(
        self,
        peer: str,
        thread: str,
        *,
        for_dispatch: bool = False,
        through_request: str | None = None,
    ) -> list[dict[str, Any]]:
        value = self.value(peer, thread)
        removed = set((value or {}).get("removed_ids", []))
        if for_dispatch:
            removed.update(
                row[0]
                for row in self.db.execute(
                    "SELECT request FROM device_work WHERE peer=? AND thread=? "
                    "AND state='accepted'",
                    (peer, thread),
                )
            )
        if through_request is not None:
            future = [
                row[0]
                for row in self.db.execute(
                    "SELECT request FROM device_work WHERE peer=? AND thread=? AND rowid > "
                    "(SELECT rowid FROM device_work WHERE peer=? AND request=?)",
                    (peer, thread, peer, through_request),
                )
            ]
            removed.update(future)
            removed.update("reply-" + identifier for identifier in future)
        result = {m["id"]: m for m in (value or {}).get("messages", []) if m["id"] not in removed}
        for row in self.db.execute(
            "SELECT request,event,state FROM device_work WHERE peer=? AND thread=? ORDER BY rowid",
            (peer, thread),
        ):
            if for_dispatch and row["state"] == "accepted":
                continue
            event = self.app._decode(peer, row["event"])
            identifier = row["request"]
            if identifier not in removed:
                result.setdefault(
                    identifier,
                    {"id": identifier, "role": "user", "body": event["payload"].get("text", "")},
                )
            if identifier in result:
                stored = self.db.execute(
                    "SELECT value FROM device_message_attachments WHERE peer=? AND request=?",
                    (peer, identifier),
                ).fetchone()
                if stored:
                    result[identifier] = {**result[identifier], **self.app._decode(peer, stored[0])}
                elif event["payload"].get("attachment_ids") and not result[identifier].get(
                    "attachments"
                ):
                    # Upgrade older accepted turns while their verified offers still exist.
                    recovered = []
                    for file_id in event["payload"]["attachment_ids"]:
                        try:
                            offer, _ = self.app.files.content(peer, thread, file_id)
                            recovered.append(
                                {
                                    "id": file_id,
                                    "name": offer["name"],
                                    "size": offer["size"],
                                    "mime": offer["mime"],
                                }
                            )
                        except PairingError:
                            recovered.append(
                                {
                                    "id": file_id,
                                    "name": "Attachment",
                                    "size": 0,
                                    "mime": "application/octet-stream",
                                }
                            )
                    result[identifier] = {**result[identifier], "attachments": recovered}
            answer = self.db.execute(
                "SELECT result FROM device_transcript WHERE peer=? AND request=?",
                (peer, identifier),
            ).fetchone()
            if answer and "reply-" + identifier not in removed:
                result.setdefault(
                    "reply-" + identifier,
                    {
                        "id": "reply-" + identifier,
                        "role": "assistant",
                        "body": self.app._decode(peer, answer[0])["text"],
                    },
                )
        return [
            message
            for message in result.values()
            if not for_dispatch or message.get("kind") not in {"thought", "turn-summary", "error"}
        ]

    def name_from_message(self, peer: str, thread: str, text: str) -> None:
        value = self.value(peer, thread)
        if not text.strip():
            return
        if not value or value.get("auto_title") or value["metadata"].get("title") != "New thread":
            return
        trimmed = text.strip()
        title = " ".join(trimmed.split()[:8])
        title = (title + "..." if len(title) < len(trimmed) else title) or "New thread"
        value["auto_title"] = title[:160]
        value["revision"] += 1
        self.put(peer, thread, value)
        row = self.db.execute(
            "SELECT context FROM device_grants WHERE peer=? AND thread=?", (peer, thread)
        ).fetchone()
        context = self.app._decode(peer, row[0])
        context.update(title=title[:160], revision=value["revision"])
        self.db.execute(
            "UPDATE device_grants SET context=? WHERE peer=? AND thread=?",
            (self.app._encode(peer, context), peer, thread),
        )

    def catalog(self, peer: str) -> dict[str, Any]:
        projects = {}
        for row in self.db.execute("SELECT context FROM device_grants WHERE peer=?", (peer,)):
            context = self.app._decode(peer, row[0])
            for project in context.get("projects", []):
                projects[project["id"]] = {"id": project["id"], "name": project["name"][:160]}
            if context.get("project_id"):
                projects[context["project_id"]] = {
                    "id": context["project_id"],
                    "name": context.get("project_name", context.get("title", "Project"))[:160],
                }
        return {
            "revision": 1,
            "projects": list(projects.values())[:100],
            "resources": [],
            "providers": self.providers,
        }

    def handle(self, peer: str, event: dict[str, Any]) -> list[dict[str, Any]] | None:
        kind, payload, thread = event["type"], event["payload"], event["thread_id"]
        if kind == "catalog.request":
            return [self.app._emit(peer, event, "catalog.snapshot", self.catalog(peer))]
        if kind == "thread.search.request":
            return [
                self.app._emit(peer, event, "thread.search.snapshot", self.search(peer, payload))
            ]
        if kind not in {"thread.history.request", "thread.context.update", "thread.manage"}:
            return None
        row = self.db.execute(
            "SELECT context FROM device_grants WHERE peer=? AND thread=?", (peer, thread)
        ).fetchone()

        def reject(code: str, detail: str) -> list[dict[str, Any]]:
            return [
                self.app._emit(
                    peer, event, "error", {"code": code, "retryable": False, "detail": detail}
                )
            ]

        if row is None:
            return reject("unauthorized", "Thread access was revoked on desktop.")
        context = self.app._decode(peer, row[0])
        if kind == "thread.history.request":
            # Split long messages deterministically. Pages remain under the TLS record bound.
            parts = []
            rich = payload.get("include_presentation", False)
            history = self.desktop_messages(peer, thread) if rich else self.messages(peer, thread)
            for message in history:
                body = message["body"]
                for offset in range(0, max(1, len(body)), 4000):
                    parts.append(
                        {
                            "id": message["id"]
                            if message["role"] == "user" and offset == 0
                            else str(
                                uuid5(
                                    NAMESPACE_URL,
                                    "raticode-message:" + message["id"] + ":" + str(offset),
                                )
                            ),
                            "role": message["role"],
                            "text": body[offset : offset + 4000],
                            **(
                                {
                                    "source_id": message["id"],
                                    "offset": offset,
                                    "presentation": presentation(message),
                                    "attachments": attachments(message) if offset == 0 else [],
                                }
                                if rich
                                else {}
                            ),
                        }
                    )
            end = min(
                payload.get("before") if payload.get("before") is not None else len(parts),
                len(parts),
            )
            if payload.get("message_id"):
                target = next(
                    (i for i, part in enumerate(parts) if part["id"] == payload["message_id"]), None
                )
                if target is not None:
                    end = target + 1
            start = end
            used = 0
            while start > 0 and end - start < payload.get("limit", 40):
                size = len(json.dumps(parts[start - 1]).encode())
                if used + size > 45000:
                    break
                used += size
                start -= 1
            return [
                self.app._emit(
                    peer,
                    event,
                    "thread.history.snapshot",
                    {
                        "messages": parts[start:end],
                        "before": start,
                        "has_more": start > 0,
                        **({"total": len(parts)} if rich else {}),
                    },
                )
            ]
        if payload["expected_revision"] != context.get("revision", 1):
            return reject("conflict", "Thread settings changed. Refresh and choose again.")
        value = self.value(peer, thread)
        if value and value.get("delete_requested"):
            return reject(
                "conflict", "Deletion is waiting for the desktop archive. Open desktop to finish."
            )
        if kind == "thread.manage":
            if not value or peer not in self.enabled():
                return reject("unauthorized", "Only shared desktop threads can be organized here.")
            action = payload["action"]
            if action == "delete":
                value["delete_requested"] = True
            else:
                value["organization"] = {
                    "pinned": action == "pin",
                    "archived": action == "archive",
                    "mobileGroup": "pinned"
                    if action == "pin"
                    else "archived"
                    if action == "archive"
                    else "active",
                    "updatedAt": datetime.fromtimestamp(self.app.registry.clock(), UTC).isoformat(),
                }
            context = {**context, "revision": context.get("revision", 1) + 1}
            value["revision"] = context["revision"]
            self.put(peer, thread, value)
        else:
            try:
                context = self.configure_context(context, payload)
            except (ValueError, OSError) as exc:
                return reject("invalid_request", str(exc))
        self.db.execute(
            "INSERT OR REPLACE INTO device_grants VALUES (?,?,?)",
            (peer, thread, self.app._encode(peer, context)),
        )
        value = self.value(peer, thread)
        if value:
            value["revision"] = context["revision"]
            if kind == "thread.context.update":
                value["context_modified"] = True
            self.put(peer, thread, value)
        result = {
            k: context.get(k, [] if k == "resource_ids" else None)
            for k in ("title", "project_id", "resource_ids", "provider", "model", "revision")
        }
        result.update(self.details(peer, thread, context))
        return [self.app._emit(peer, event, "thread.snapshot", result)]

    def configure_context(self, context: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if payload["resource_ids"] != context.get("resource_ids", []):
            raise ValueError("Refresh this thread's resource selection on desktop.")
        if payload["project_id"] != context.get("project_id") or (
            payload["project_id"] is None
            and context.get("desktop_parity")
            and context.get("scope_mode") != "global"
        ):
            try:
                context = self.scope_context(context, payload["project_id"])
            except (ValueError, OSError):
                raise ValueError(
                    "This project is no longer open on desktop. Refresh and choose again."
                )
        provider, model = payload["provider"], payload["model"]
        if not any(p["id"] == provider and model in p["models"] for p in self.providers) and (
            provider,
            model,
        ) != (context["provider"], context["model"]):
            raise ValueError("This model is unavailable on desktop.")
        permissions = context.get(
            "provider_permissions", {context["provider"]: context.get("permission_mode")}
        )
        if provider not in permissions:
            raise ValueError("Allow this provider on desktop first.")
        effort = payload.get("effort", context.get("effort"))
        if (
            "effort" in payload
            and effort not in (None, "", "cli-default")
            and (effort, provider, model)
            != (context.get("effort"), context["provider"], context["model"])
        ):
            capability = next((p for p in self.providers if p["id"] == provider), {})
            if effort not in capability.get("efforts", {}).get(model, []):
                raise ValueError("This effort is unavailable for the selected model.")
        mode = payload.get("permission_mode", permissions[provider])
        if mode != permissions[provider]:
            from gofer.core.provider_permissions import provider_permission_args

            if not context.get("desktop_parity"):
                raise ValueError("This conversation uses a scoped desktop grant.")
            try:
                provider_permission_args(provider, mode)
            except ValueError:
                raise ValueError("This provider does not support that permission mode.")
        permissions = {**permissions, provider: mode}
        context = {
            **context,
            "provider_permissions": permissions,
            "effort": effort,
            "provider": provider,
            "model": model,
            "permission_mode": permissions[provider],
            "revision": context.get("revision", 1) + 1,
        }
        return context

    def details(self, peer: str, thread: str, context: dict[str, Any]) -> dict[str, Any]:
        value = self.value(peer, thread) or {}
        meta = {**value.get("metadata", {}), **value.get("organization", {})}
        jobs = self.app.local_chat_jobs
        running = (
            self.db.execute(
                "SELECT 1 FROM device_work WHERE peer=? AND thread=? "
                "AND state IN ('accepted','running','cancel_requested') LIMIT 1",
                (peer, thread),
            ).fetchone()
            is not None
        )
        if jobs:
            running = running or any(
                t["thread_id"] == context.get("desktop_thread_id") for t in jobs.active_snapshot()
            )
        return {
            "can_manage": bool(value) and peer in self.enabled(),
            "delete_pending": value.get("delete_requested", False),
            "effort": context.get("effort") or "",
            "permission_mode": context.get("permission_mode") or "",
            "running": running,
            "group": meta.get(
                "mobileGroup",
                "pinned"
                if meta.get("pinned")
                else "archived"
                if meta.get("archived")
                else "active",
            ),
            "updated_at": str(meta.get("updatedAt") or "")[:80],
        }

    def scope_context(self, context: dict[str, Any], identifier: str | None) -> dict[str, Any]:
        from gofer.ui.device_chat import validate_context

        if not context.get("desktop_parity"):
            raise ValueError("desktop_scope_required")
        projects = context.get("projects", [])
        selected = next((p for p in projects if p["id"] == identifier), None)
        if identifier is not None and selected is None:
            raise ValueError("project_not_open")
        selected = selected or next(iter(projects), None)
        if selected is None:
            raise ValueError("open_project_required")
        workflow = dict(context.get("workflow") or {})
        workflow["remThreads"] = {**workflow.get("remThreads", {}), "global": identifier is None}
        workflow["remSwarmAccess"] = {
            **workflow.get("remSwarmAccess", {}),
            "grantId": selected.get("grantId"),
        }
        return validate_context(
            {
                **context,
                "project_id": identifier,
                "project_path": selected["root"],
                "project_identity": selected["identity"],
                "project_name": selected["name"] if identifier else "Global",
                "scope_mode": "project" if identifier else "global",
                "workflow": workflow,
            }
        )

    def select_project(self, peer: str, thread: str, root: str) -> None:
        row = self.db.execute(
            "SELECT context FROM device_grants WHERE peer=? AND thread=?", (peer, thread)
        ).fetchone()
        if row is None:
            raise ValueError("thread_revoked")
        context = self.app._decode(peer, row[0])
        identifier = next((p["id"] for p in context.get("projects", []) if p["root"] == root), None)
        if identifier is None:
            raise ValueError("project_not_open")
        context = self.scope_context(context, identifier)
        context["revision"] = context.get("revision", 1) + 1
        self.db.execute(
            "UPDATE device_grants SET context=? WHERE peer=? AND thread=?",
            (self.app._encode(peer, context), peer, thread),
        )
        value = self.value(peer, thread)
        if value:
            value.update(revision=context["revision"], context_modified=True)
            self.put(peer, thread, value)

    def search(self, peer: str, payload: dict[str, Any]) -> dict[str, Any]:
        def normalize(text: str) -> str:
            return unicodedata.normalize("NFKC", text).lower()

        needle = normalize(payload["query"]).strip()
        matches = []
        if needle:
            for row in self.db.execute(
                "SELECT thread,context FROM device_grants WHERE peer=?", (peer,)
            ):
                context = self.app._decode(peer, row["context"])
                title = context.get("title", "Remote Rem")
                match = (
                    {
                        "thread_id": row["thread"],
                        "title": title,
                        "snippet": "Title match",
                        "message_id": None,
                    }
                    if needle in normalize(title)
                    else None
                )
                for message in self.messages(peer, row["thread"]):
                    body = message["body"]
                    position = normalize(body).find(needle)
                    if position >= 0:
                        offset = position // 4000 * 4000
                        identifier = (
                            message["id"]
                            if message["role"] == "user" and offset == 0
                            else str(
                                uuid5(
                                    NAMESPACE_URL,
                                    "raticode-message:" + message["id"] + ":" + str(offset),
                                )
                            )
                        )
                        match = {
                            "thread_id": row["thread"],
                            "title": title,
                            "snippet": body[max(0, position - 45) : position + len(needle) + 100][
                                :500
                            ],
                            "message_id": identifier,
                        }
                        break
                if match:
                    value = self.value(peer, row["thread"]) or {}
                    matches.append((str(value.get("metadata", {}).get("updatedAt", "")), match))
        matches.sort(key=lambda item: item[0], reverse=True)
        offset = payload.get("offset", 0)
        page = [m for _, m in matches[offset : offset + 30]]
        return {
            "query": payload["query"],
            "offset": offset,
            "results": page,
            "has_more": len(matches) > offset + len(page),
        }
