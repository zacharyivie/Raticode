"""Phone approvals and swarm console, carried out by the desktop's own services.

Nothing here runs unless the user turns on remote control for that phone in
Paired devices. Decisions use the same desktop approval and swarm code paths as
the desktop UI, so a phone can never widen a desktop policy. Every decision
names an action digest; a changed request needs a fresh phone review.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from gofer.devices.registry import PairingError

REMOTE_KINDS = frozenset(
    {"approval.request", "approval.resolve", "swarm.request", "swarm.control", "swarm.message"}
)
PAGE_BYTES = 45000


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def swarm_wire_id(root: str, identifier: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"raticode-swarm:{root}:{identifier}"))


def swarm_status(swarm: dict[str, Any]) -> str:
    run = swarm.get("run") or {}
    state = run.get("state")
    if not run:
        return "idle"
    if state == "running":
        pending = any(i.get("state") == "pending" for i in run.get("humanInbox", []))
        return "awaiting_approval" if pending else "running"
    return {
        "paused": "paused",
        "stopping": "cancelled",
        "stopped": "cancelled",
        "failed": "failed",
        "completed": "completed",
    }.get(str(state), "queued")


class RemoteControl:
    def __init__(self, application: Any) -> None:
        self.app = application
        self.db = application.registry.db
        self.db.execute("CREATE TABLE IF NOT EXISTS device_remote_control (peer TEXT PRIMARY KEY)")
        self.swarms: Any = None
        self.data_dir: Path | None = None
        self.decide_workflow: Callable[..., Any] | None = None

    # Local authenticated settings API only.
    def enable(self, peer: str, enabled: bool) -> None:
        with self.app.registry.transaction():
            self.app._trusted(peer)
            if self.app.registry._row(peer)["role"] != "controller":
                raise PairingError("phone_required")
            if enabled:
                self.db.execute("INSERT OR IGNORE INTO device_remote_control VALUES (?)", (peer,))
            else:
                self.db.execute("DELETE FROM device_remote_control WHERE peer=?", (peer,))

    def enabled(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT peer FROM device_remote_control")]

    def allowed(self, peer: str) -> bool:
        # Using Raticode from the phone includes approvals and swarm controls.
        return (
            self.db.execute("SELECT 1 FROM device_remote_control WHERE peer=?", (peer,)).fetchone()
            is not None
            and peer in self.app.workspace.enabled()
        ) or self.app.desktop.allowed(peer)

    def projects(self, peer: str) -> tuple[dict[str, str], dict[str, str]]:
        """Open desktop projects and their path grants, as the renderer last shared them."""
        names: dict[str, str] = {}
        grants: dict[str, str] = {}
        for row in self.db.execute("SELECT context FROM device_grants WHERE peer=?", (peer,)):
            context = self.app._decode(peer, row[0])
            for project in context.get("projects", []):
                names.setdefault(project["root"], str(project.get("name") or "Project")[:160])
                if project.get("grantId"):
                    grants.setdefault(project["root"], project["grantId"])
            access = (context.get("workflow") or {}).get("remSwarmAccess") or {}
            for root, grant in (access.get("workspaceGrants") or {}).items():
                grants.setdefault(root, grant)
        return names, grants

    def handle(self, peer: str, event: dict[str, Any]) -> list[dict[str, Any]] | None:
        kind = event["type"]
        if kind not in REMOTE_KINDS:
            return None

        def reject(code: str, detail: str) -> list[dict[str, Any]]:
            return [
                self.app._emit(
                    peer, event, "error", {"code": code, "retryable": False, "detail": detail}
                )
            ]

        if kind in {"approval.request", "swarm.request"}:
            # Without the opt-in, snapshots say so and carry no approvals or swarms.
            return [self._snapshot(peer, event, kind)]
        if not self.allowed(peer):
            return reject(
                "unauthorized",
                "Allow approvals and swarm controls for this phone in desktop Paired devices.",
            )
        payload = event["payload"]
        try:
            if kind == "approval.resolve":
                self._resolve(peer, payload)
                return [self._snapshot(peer, event, "approval.request")]
            root, identifier = self._swarm(peer, payload["swarm_id"])
            _, grants = self.projects(peer)
            if kind == "swarm.control":
                self.swarms.control(root, identifier, payload["action"], workspace_grants=grants)
            else:
                self.swarms.message(root, identifier, {"body": payload["body"]})
            return [self._snapshot(peer, event, "swarm.request", detail=payload["swarm_id"])]
        except (ValueError, OSError) as exc:
            # Desktop service messages are written for the user and contain no secrets.
            return reject("conflict", str(exc)[:1000] or "The desktop could not apply this action.")

    def _snapshot(
        self, peer: str, event: dict[str, Any], kind: str, detail: str | None = None
    ) -> dict[str, Any]:
        allowed = self.allowed(peer)
        if kind == "approval.request":
            approvals = [
                {k: v for k, v in item.items() if not k.startswith("_")}
                for item in (self.approvals(peer) if allowed else [])
            ]
            payload = {"remote_allowed": allowed, "approvals": approvals}
            result: dict[str, Any] = self.app._emit(peer, event, "approval.snapshot", payload)
            return result
        requested = detail or event["payload"].get("swarm_id")
        swarms = self.swarm_list(peer, requested) if allowed else []
        page: list[dict[str, Any]] = []
        used = 0
        for item in swarms:
            size = len(json.dumps(item).encode())
            if used + size > PAGE_BYTES:
                break
            page.append(item)
            used += size
        snapshot: dict[str, Any] = self.app._emit(
            peer,
            {**event, "thread_id": None},
            "swarm.snapshot",
            {"revision": 1, "remote_allowed": allowed, "swarms": page},
        )
        return snapshot

    def approvals(self, peer: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if self.data_dir is not None:
            from gofer.core.approvals import ApprovalStore

            for request in ApprovalStore(self.data_dir).list_pending()[:50]:
                if request.decision is not None:
                    continue
                identity = {
                    "source": "workflow",
                    "workflow": request.workflow_id,
                    "run": request.run_id,
                    "node": request.node_id,
                    "message": request.message,
                    "requested_at": request.requested_at,
                }
                result.append(
                    {
                        "approval_id": str(
                            uuid5(
                                NAMESPACE_URL,
                                "raticode-workflow-approval:"
                                f"{request.workflow_id}:{request.run_id}:{request.node_id}",
                            )
                        ),
                        "action_digest": _digest(identity),
                        "source": "workflow",
                        "title": f"Workflow {request.workflow_id}"[:160],
                        "summary": (request.message or "Approval requested")[:1000],
                        "requested_at": str(request.requested_at)[:80],
                        "instruction_required": False,
                        "_target": identity,
                    }
                )
        names, _ = self.projects(peer)
        if self.swarms is not None:
            for root, project in names.items():
                try:
                    summaries = self.swarms.list(root)
                except (ValueError, OSError):
                    continue
                for swarm in summaries:
                    for item in (swarm.get("run") or {}).get("humanInbox", []):
                        if item.get("state") != "pending":
                            continue
                        identity = {
                            "source": "swarm",
                            "root": root,
                            "swarm": swarm["id"],
                            "item": item["id"],
                            "description": item.get("description", ""),
                            "recommended": item.get("recommendedAction", ""),
                        }
                        result.append(
                            {
                                "approval_id": str(
                                    uuid5(
                                        NAMESPACE_URL,
                                        f"raticode-swarm-approval:{root}:{swarm['id']}:{item['id']}",
                                    )
                                ),
                                "action_digest": _digest(identity),
                                "source": "swarm",
                                "title": f"{swarm.get('name', 'Swarm')} · {project}"[:160],
                                "summary": (
                                    f"{item.get('description', '')}\n"
                                    f"Recommended: {item.get('recommendedAction', '')}"
                                )[:1000],
                                "requested_at": str(item.get("createdAt", ""))[:80],
                                "instruction_required": True,
                                "_target": identity,
                            }
                        )
        return result[:50]

    def _resolve(self, peer: str, payload: dict[str, Any]) -> None:
        match = next(
            (a for a in self.approvals(peer) if a["approval_id"] == payload["approval_id"]), None
        )
        if match is None:
            raise ValueError("This approval was already answered or is no longer pending.")
        if match["action_digest"] != payload["action_digest"]:
            raise ValueError("This approval changed on desktop. Review it again.")
        target = match["_target"]
        approve = payload["decision"] == "approve"
        instruction = str(payload.get("instruction", "")).strip()
        if target["source"] == "workflow":
            assert self.decide_workflow is not None
            self.decide_workflow(
                target["workflow"],
                target["run"],
                target["node"],
                "approved" if approve else "rejected",
                self.data_dir,
                decided_by="phone",
                notes=instruction,
            )
            return
        if not approve and not instruction:
            raise ValueError("Tell the swarm what to do instead.")
        self.swarms.execution(
            target["root"],
            target["swarm"],
            {
                "action": "human_response",
                "notificationId": target["item"],
                "decision": "proceed" if approve else "redirect",
                "instruction": instruction,
            },
        )

    def _swarm(self, peer: str, wire: str) -> tuple[str, str]:
        names, _ = self.projects(peer)
        if self.swarms is not None:
            for root in names:
                try:
                    for swarm in self.swarms.list(root):
                        if swarm_wire_id(root, swarm["id"]) == wire:
                            return root, swarm["id"]
                except (ValueError, OSError):
                    continue
        raise ValueError("This swarm is not in an open desktop project.")

    def swarm_list(self, peer: str, detail: str | None) -> list[dict[str, Any]]:
        names, _ = self.projects(peer)
        result: list[dict[str, Any]] = []
        if self.swarms is None:
            return result
        for root, project in names.items():
            try:
                summaries = self.swarms.list(root)
            except (ValueError, OSError):
                continue
            for swarm in summaries:
                wire = swarm_wire_id(root, swarm["id"])
                run = swarm.get("run") or {}
                item: dict[str, Any] = {
                    "swarm_id": wire,
                    "title": str(swarm.get("name") or "Swarm")[:160],
                    "status": swarm_status(swarm),
                    "detail": str(run.get("task") or swarm.get("charter") or "")[:300],
                    "project_name": project,
                    "progress": max(
                        0, min(100, int((run.get("progress") or {}).get("percent") or 0))
                    ),
                }
                if wire == detail:
                    full = self.swarms.get(root, swarm["id"], include_history=False)
                    run = full.get("run") or {}
                    agents = {a["id"]: a.get("name") or a["id"] for a in full.get("agents", [])}
                    item["agents"] = [
                        {
                            "name": str(agents.get(agent, agent))[:80],
                            "state": str(state.get("state", "idle"))[:40],
                        }
                        for agent, state in list((run.get("agentStates") or {}).items())[:16]
                    ]
                    item["messages"] = [
                        {
                            "sender": str(
                                "You"
                                if m.get("senderId") == "user"
                                else agents.get(m.get("senderId"), m.get("senderId") or "System")
                            )[:80],
                            "body": str(m.get("body", ""))[:1000],
                            "created_at": str(m.get("createdAt", ""))[:80],
                        }
                        for m in (run.get("messages") or [])[-20:]
                    ]
                    result.insert(0, item)
                else:
                    result.append(item)
        return result[:50]
