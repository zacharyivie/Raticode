"""Use Raticode from a paired phone: Rem, organizations, swarms, settings and usage.

Nothing here runs unless the user turns on "Use Raticode from this phone" in
Paired devices. It is off by default and requires thread sync. Every operation
calls the same desktop service the window uses, with that service's validation
and approval policies, so the phone gains no authority the desktop UI lacks.

The phone never sends paths. Projects are named by the IDs in the phone's
catalog; Rem tasks, runs, threads, organizations and swarms by their desktop IDs.
Results are projections for the phone screens, without paths, credentials,
grants or provider configuration.

Excluded on purpose: files and the code editor, terminals, the browser, pairing,
provider credentials, skill and MCP server definitions, organization
configuration and imports, and settings that widen what agents may do.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gofer.devices.registry import PairingError

READ_OPS = frozenset(
    {
        "overview",
        "rem.home",
        "rem.task",
        "rem.thread",
        "organizations.list",
        "organizations.read",
        "settings.get",
        "usage.overview",
    }
)
ACTION_OPS = frozenset(
    {
        "rem.message",
        "rem.instruct",
        "rem.task.create",
        "rem.task.run",
        "rem.task.pause",
        "rem.task.resume",
        "rem.task.delete",
        "rem.run.cancel",
        "rem.run.acknowledge",
        "organizations.control",
        "organizations.task",
        "swarms.start",
        "settings.update",
    }
)
# Result bodies are JSON text split into parts. Escaping can at most double a
# part inside the record, which stays below the 65,536-byte record limit.
CHUNK = 24000
MAX_PARTS = 48
OFF = "Turn on Use Raticode from this phone in desktop Paired devices."

_ACTIVITIES = [
    ("rem", "Rem"),
    ("workflows", "Workflows"),
    ("files", "Files"),
    ("search", "Search"),
    ("source-control", "Source control"),
    ("organizations", "Organizations"),
    ("second-brain", "Second Brain"),
]
# Display and default choices only. Agent tools, browser permissions, swarm
# access, execution mode, folders and keybindings stay desktop-only.
SETTINGS: list[dict[str, Any]] = [
    {
        "key": "appearance.theme",
        "group": "Appearance",
        "label": "Theme",
        "kind": "enum",
        "options": [["system", "System"], ["light", "Light"], ["dark", "Dark"]],
    },
    {
        "key": "appearance.reducedMotion",
        "group": "Appearance",
        "label": "Reduce motion",
        "kind": "enum",
        "options": [["system", "System"], ["on", "On"], ["off", "Off"]],
    },
    {
        "key": "general.initialActivity",
        "group": "General",
        "label": "Open Raticode to",
        "kind": "enum",
        "options": [list(item) for item in _ACTIVITIES],
    },
    {
        "key": "general.autosave",
        "group": "General",
        "label": "Save workflow edits automatically",
        "kind": "bool",
    },
    {
        "key": "general.checkForUpdates",
        "group": "General",
        "label": "Check for updates",
        "kind": "bool",
    },
    {
        "key": "general.archivedThreadRetention",
        "group": "General",
        "label": "Delete archived threads",
        "kind": "enum",
        "options": [
            ["never", "Never"],
            ["7", "After 7 days"],
            ["30", "After 30 days"],
            ["90", "After 90 days"],
        ],
    },
    {"key": "assistant.provider", "group": "Rem", "label": "Default provider", "kind": "provider"},
    {"key": "assistant.model", "group": "Rem", "label": "Default model", "kind": "model"},
    {"key": "assistant.effort", "group": "Rem", "label": "Default effort", "kind": "effort"},
    {
        "key": "assistant.defaultScope",
        "group": "Rem",
        "label": "New thread scope",
        "kind": "enum",
        "options": [["current-directory", "Current project"], ["global", "Global"]],
    },
    {
        "key": "assistant.autoHideThoughts",
        "group": "Rem",
        "label": "Hide thoughts after a reply",
        "kind": "bool",
    },
    {
        "key": "assistant.avatarEnabled",
        "group": "Rem",
        "label": "Show Rem's avatar",
        "kind": "bool",
    },
    {
        "key": "assistant.avatarAnimated",
        "group": "Rem",
        "label": "Animate Rem's avatar",
        "kind": "bool",
    },
    {
        "key": "memory.reportFormat",
        "group": "Reports",
        "label": "Report format",
        "kind": "enum",
        "options": [["md", "Markdown"], ["html", "HTML"], ["slides", "Slides"], ["pdf", "PDF"]],
    },
    {
        "key": "editor.fontSize",
        "group": "Editor",
        "label": "Font size",
        "kind": "number",
        "min": 10,
        "max": 24,
        "step": 1,
    },
    {
        "key": "editor.tabSize",
        "group": "Editor",
        "label": "Tab size",
        "kind": "number",
        "min": 1,
        "max": 8,
        "step": 1,
    },
    {"key": "editor.wordWrap", "group": "Editor", "label": "Word wrap", "kind": "bool"},
    {"key": "editor.minimap", "group": "Editor", "label": "Minimap", "kind": "bool"},
    {
        "key": "editor.markdownDefault",
        "group": "Editor",
        "label": "Open Markdown as",
        "kind": "enum",
        "options": [["preview", "Preview"], ["edit", "Editor"]],
    },
    {
        "key": "terminal.fontSize",
        "group": "Terminal",
        "label": "Font size",
        "kind": "number",
        "min": 8,
        "max": 28,
        "step": 0.5,
    },
    {
        "key": "terminal.cursorBlink",
        "group": "Terminal",
        "label": "Blinking cursor",
        "kind": "bool",
    },
    {
        "key": "terminal.scrollback",
        "group": "Terminal",
        "label": "Scrollback lines",
        "kind": "number",
        "min": 100,
        "max": 100000,
        "step": 100,
    },
]
SETTING_KEYS = {item["key"]: item for item in SETTINGS}
PENDING_SETTINGS = 20
logger = logging.getLogger(__name__)


class Refused(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _newest(items: list[dict[str, Any]], budget: int = 600_000) -> list[dict[str, Any]]:
    """Keep the newest items whose encoded size fits within one result."""
    kept: list[dict[str, Any]] = []
    for item in reversed(items):
        budget -= len(json.dumps(item, ensure_ascii=True))
        if budget < 0:
            break
        kept.append(item)
    return kept[::-1]


def _path_value(settings: dict[str, Any], key: str) -> Any:
    value: Any = settings
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _service_error(exc: BaseException) -> Refused:
    """Desktop service messages are written for the user and carry no secrets."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            first = errors()[0]
            where = ".".join(str(part) for part in first.get("loc", ()))
            message = str(first.get("msg", "")).removeprefix("Value error, ")
            return Refused("invalid", f"{where}: {message}"[:1000] if where else message[:1000])
        except (IndexError, TypeError, AttributeError):
            pass
    name = type(exc).__name__
    if name in {"RemConflict", "OrganizationConflict"}:
        return Refused("conflict", str(exc)[:1000] or "This changed on desktop. Refresh first.")
    if isinstance(exc, KeyError):
        return Refused("not_found", "This item no longer exists on desktop.")
    return Refused("invalid", str(exc)[:1000] or "The desktop could not apply this action.")


class DesktopControl:
    def __init__(self, application: Any) -> None:
        self.app = application
        self.db = application.registry.db
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS device_desktop_control (peer TEXT PRIMARY KEY);"
            "CREATE TABLE IF NOT EXISTS device_desktop_settings "
            "(key TEXT PRIMARY KEY, value BLOB NOT NULL);"
        )
        self.rem: Any = None
        self.organizations: Any = None
        self.usage: Callable[[], dict[str, Any]] | None = None

    # Local authenticated settings API only.
    def enable(self, peer: str, enabled: bool) -> None:
        with self.app.registry.transaction():
            self.app._trusted(peer)
            if self.app.registry._row(peer)["role"] != "controller":
                raise PairingError("phone_required")
            if enabled:
                self.db.execute("INSERT OR IGNORE INTO device_desktop_control VALUES (?)", (peer,))
            else:
                self.db.execute("DELETE FROM device_desktop_control WHERE peer=?", (peer,))

    def enabled(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT peer FROM device_desktop_control")]

    def allowed(self, peer: str) -> bool:
        return (
            self.db.execute("SELECT 1 FROM device_desktop_control WHERE peer=?", (peer,)).fetchone()
            is not None
            and peer in self.app.workspace.enabled()
        )

    def projects(self, peer: str) -> dict[str, dict[str, str]]:
        """Open desktop projects by catalog ID, as the window last shared them."""
        result: dict[str, dict[str, str]] = {}
        for row in self.db.execute("SELECT context FROM device_grants WHERE peer=?", (peer,)):
            context = self.app._decode(peer, row[0])
            for project in context.get("projects", []):
                if project.get("id") and project.get("root"):
                    result.setdefault(
                        project["id"],
                        {"root": project["root"], "name": _text(project.get("name"), 160)},
                    )
        return result

    def _project_name(self, peer: str, root: str | None) -> str:
        if not root:
            return ""
        for project in self.projects(peer).values():
            if project["root"] == root:
                return project["name"]
        return Path(root).name[:160]

    def handle(self, peer: str, event: dict[str, Any]) -> list[dict[str, Any]] | None:
        kind = event["type"]
        if kind not in {"desktop.read", "desktop.action"}:
            return None
        payload = event["payload"]
        op = payload["op"]
        try:
            if not self.allowed(peer):
                raise Refused("unauthorized", OFF)
            if kind == "desktop.read":
                result = self._read(peer, op, payload["params"])
            else:
                result = self._act(peer, op, payload["params"], event["request_id"])
        except Refused as exc:
            return [self._result(peer, event, op, ok=False, code=exc.code, detail=str(exc))]
        except (ValueError, KeyError, OSError) as exc:
            refused = _service_error(exc)
            return [self._result(peer, event, op, ok=False, code=refused.code, detail=str(refused))]
        except Exception as exc:  # noqa: BLE001
            # A phone retries the same event bytes. An unexpected failure must
            # answer it, or that request would block the phone's queue.
            logger.warning("Phone desktop request %s failed: %s", op, type(exc).__name__)
            return [
                self._result(
                    peer,
                    event,
                    op,
                    ok=False,
                    code="invalid",
                    detail="The desktop could not complete this request.",
                )
            ]
        body = json.dumps(result, separators=(",", ":"), ensure_ascii=True)
        # Phones poll the open screen. An unchanged view is answered with its digest only.
        digest = hashlib.sha256(body.encode()).hexdigest()[:32]
        if kind == "desktop.read" and payload["params"].get("known") == digest:
            body = json.dumps({"unchanged": True, "digest": digest})
        else:
            body = json.dumps(
                {**result, "digest": digest}, separators=(",", ":"), ensure_ascii=True
            )
        parts = [body[i : i + CHUNK] for i in range(0, len(body), CHUNK)] or [""]
        if len(parts) > MAX_PARTS:
            return [
                self._result(
                    peer, event, op, ok=False, code="invalid", detail="This result is too large."
                )
            ]
        return [
            self._result(peer, event, op, ok=True, part=index, parts=len(parts), body=part)
            for index, part in enumerate(parts)
        ]

    def _result(
        self,
        peer: str,
        event: dict[str, Any],
        op: str,
        *,
        ok: bool,
        part: int = 0,
        parts: int = 1,
        body: str = "",
        code: str | None = None,
        detail: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"op": op, "ok": ok, "part": part, "parts": parts, "body": body}
        if code:
            payload.update(code=code, detail=detail[:1000])
        result: dict[str, Any] = self.app._emit(
            peer, {**event, "thread_id": None}, "desktop.result", payload
        )
        return result

    def _need(self, service: Any, label: str) -> Any:
        if service is None:
            raise Refused("unavailable", f"{label} is not available on this desktop.")
        return service

    # Reads -----------------------------------------------------------------

    def _read(self, peer: str, op: str, params: dict[str, Any]) -> Any:
        if op == "overview":
            return {
                "projects": [
                    {"project_id": key, "name": value["name"]}
                    for key, value in self.projects(peer).items()
                ][:100],
                "rem": self.rem is not None,
                "organizations": bool(self.organizations and self.organizations.enabled),
                "swarms": self.app.remote.swarms is not None,
                "settings": self._settings_state()[0] is not None,
                "usage": self.usage is not None,
            }
        if op == "rem.home":
            return self._rem_home(peer)
        if op == "rem.task":
            return self._rem_task(peer, _text(params.get("task_id"), 128))
        if op == "rem.thread":
            return self._rem_thread(_text(params.get("thread_id"), 128))
        if op == "organizations.list":
            return self._organizations(peer)
        if op == "organizations.read":
            return self._organization(peer, _text(params.get("organization_id"), 100))
        if op == "settings.get":
            return self._settings_view()
        if op == "usage.overview":
            return self._usage()
        raise Refused("invalid", "Unknown desktop request.")

    def _rem_home(self, peer: str) -> dict[str, Any]:
        rem = self._need(self.rem, "Rem")
        snapshot = rem.store.conversation()
        conversation = snapshot.get("conversation") or {}
        messages = snapshot.get("messages") or []
        shown = _newest([self._rem_message(m) for m in messages[-80:]])
        return {
            "turn_state": _text(conversation.get("turn_state") or "idle", 40),
            "provider": _text(conversation.get("provider"), 80),
            "model": _text(conversation.get("model"), 160),
            "earlier": len(messages) - len(shown),
            "messages": shown,
            "tasks": [self._task_summary(peer, task) for task in rem.store.tasks()][:100],
        }

    @staticmethod
    def _rem_message(message: dict[str, Any]) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": _text(message.get("id"), 128),
            "role": _text(message.get("role"), 20),
            "body": _text(message.get("body"), 6000),
            "created_at": _text(message.get("created_at"), 80),
        }
        work = message.get("work")
        if isinstance(work, dict):
            item["work"] = {
                key: _text(work.get(key), 160)
                for key in ("task_id", "run_id", "thread_id", "title", "type", "state")
            }
        if isinstance(message.get("state"), str):
            item["state"] = _text(message["state"], 40)
        return item

    @staticmethod
    def _run(run: dict[str, Any] | None) -> dict[str, Any] | None:
        if not run:
            return None
        return {
            "run_id": _text(run.get("id"), 128),
            "state": _text(run.get("state"), 40),
            "created_at": _text(run.get("created_at"), 80),
            "finished_at": _text(run.get("finished_at"), 80),
            "summary": _text(run.get("summary_short") or run.get("summary"), 700),
            "needs_user": run.get("needs_user") is True,
            "acknowledged": run.get("acknowledged") is True,
            "thread_id": _text(run.get("thread_id"), 128),
            "verification_thread_id": _text(run.get("verification_thread_id"), 128),
        }

    def _task_summary(self, peer: str, task: dict[str, Any]) -> dict[str, Any]:
        schedule = task.get("schedule") or {}
        return {
            "task_id": _text(task.get("id"), 128),
            "title": _text(task.get("title"), 160),
            "type": _text(task.get("type"), 20),
            "state": _text(task.get("state"), 20),
            "revision": int(task.get("revision") or 1),
            "schedule": {
                "kind": _text(schedule.get("kind") or "once", 20),
                "at": _text(schedule.get("at"), 80),
                "local_time": _text(schedule.get("local_time"), 10),
                "weekdays": [_text(day, 12) for day in (schedule.get("weekdays") or [])][:7],
                "every_minutes": int(schedule.get("every_minutes") or 0),
                "timezone": _text(schedule.get("timezone"), 80),
            },
            "next_occurrence_at": _text(task.get("next_occurrence_at"), 80),
            "project_name": self._project_name(peer, task.get("project_dir")),
            "active_run": self._run(task.get("active_run")),
            "last_run": self._run(task.get("last_run")),
        }

    def _rem_task(self, peer: str, identifier: str) -> dict[str, Any]:
        rem = self._need(self.rem, "Rem")
        task = next((t for t in rem.store.tasks() if t.get("id") == identifier), None)
        if task is None:
            raise Refused("not_found", "This Rem task no longer exists.")
        return {
            **self._task_summary(peer, task),
            "prompt": _text(task.get("prompt"), 8000),
            "provider": _text(task.get("provider"), 80),
            "model": _text(task.get("model"), 160),
            "effort": _text(task.get("effort"), 80),
            "criteria": [
                _text(c.get("description"), 600) for c in task.get("acceptance_criteria") or []
            ][:40],
            "runs": [self._run(run) for run in (task.get("recent_runs") or [])[:20]],
        }

    def _rem_thread(self, identifier: str) -> dict[str, Any]:
        rem = self._need(self.rem, "Rem")
        thread = rem.store.thread(identifier)
        metadata = thread.get("metadata") or {}
        messages = [
            m for m in thread.get("messages") or [] if m.get("role") in {"user", "assistant"}
        ]
        shown = _newest(
            [
                {
                    "id": _text(m.get("id"), 128),
                    "role": _text(m.get("role"), 20),
                    "body": _text(m.get("body"), 4000),
                    "kind": _text(m.get("kind"), 40),
                }
                for m in messages[-120:]
            ]
        )
        return {
            "thread_id": identifier,
            "title": _text(metadata.get("title") or "Task thread", 160),
            "running": thread.get("running") is True,
            "earlier": len(messages) - len(shown),
            "messages": shown,
        }

    def _organizations(self, peer: str) -> dict[str, Any]:
        manager = self._organizations_manager()
        listed = manager.call("", "user", {"action": "list", "params": {}})
        return {
            "organizations": [
                {
                    "organization_id": _text(item.get("id"), 100),
                    "name": _text(item.get("name"), 200),
                    "state": _text(item.get("state"), 20),
                    "employees": int(item.get("employees") or 0),
                    "open_tasks": int(item.get("openTasks") or 0),
                    "projects": [
                        self._project_name(peer, root) for root in item.get("projectRoots") or []
                    ][:20],
                }
                for item in listed
            ][:100]
        }

    def _organizations_manager(self) -> Any:
        manager = self._need(self.organizations, "Organizations")
        if not manager.enabled:
            raise Refused(
                "unavailable",
                "Organizations is experimental and turned off on this desktop.",
            )
        return manager

    def _organization(self, peer: str, identifier: str) -> dict[str, Any]:
        manager = self._organizations_manager()
        org = manager.call(
            "", "user", {"action": "read", "organizationId": identifier, "params": {}}
        )
        config = org.get("config") or {}
        runtime = org.get("runtime") or {}
        employees = {e.get("id"): e for e in config.get("employees") or []}
        attention = set((org.get("diagnostics") or {}).get("attention") or [])
        tasks = []
        for task in (runtime.get("tasks") or [])[-200:]:
            tasks.append(
                {
                    "task_id": _text(task.get("id"), 100),
                    "title": _text(task.get("title"), 500),
                    "description": _text(task.get("description"), 1500),
                    "status": _text(task.get("status"), 20),
                    "priority": _text(task.get("priority"), 20),
                    "assignee": _text(
                        (employees.get(task.get("assignee")) or {}).get("name")
                        or task.get("assignee"),
                        150,
                    ),
                    "revision": int(task.get("revision") or 1),
                    "running": bool(task.get("claim")),
                    "needs_approval": bool(task.get("approvalRequired"))
                    and task.get("approvedRevision") != task.get("planRevision", 1),
                    "needs_review": task.get("status") == "in_review",
                    "attention": task.get("id") in attention,
                    "block_reason": _text(task.get("blockReason"), 600),
                    "evidence": _text(task.get("evidence"), 1500),
                    "due_at": _text(task.get("dueAt"), 80),
                    "comments": [
                        {
                            "actor": _text(
                                (employees.get(c.get("actor")) or {}).get("name") or c.get("actor"),
                                150,
                            ),
                            "at": _text(c.get("at"), 80),
                            "body": _text(c.get("body"), 1500),
                            "decision": _text(c.get("decision"), 20),
                        }
                        for c in (task.get("comments") or [])[-5:]
                    ],
                }
            )
        return {
            "organization_id": identifier,
            "name": _text(config.get("name"), 200),
            "description": _text(config.get("description"), 1500),
            "state": _text(runtime.get("state"), 20),
            "projects": [
                self._project_name(peer, root) for root in config.get("projectRoots") or []
            ][:20],
            "employees": [
                {
                    "employee_id": _text(e.get("id"), 100),
                    "name": _text(e.get("name"), 150),
                    "title": _text(e.get("title") or e.get("role"), 150),
                    "reports_to": _text(e.get("reportsTo"), 100),
                    "provider": _text(e.get("provider"), 80),
                    "model": _text(e.get("model"), 160),
                    "paused": e.get("paused") is True,
                }
                for e in employees.values()
            ][:100],
            "tasks": _newest(tasks),
        }

    def _usage(self) -> dict[str, Any]:
        source = self._need(self.usage, "Usage")
        overview = source()
        activity = overview.get("activity") or {}
        return {
            "days": int(overview.get("days") or 30),
            "observed_at": _text(overview.get("observed_at"), 80),
            "accounts": [
                {
                    "provider": _text(a.get("provider"), 80),
                    "label": _text(a.get("label") or a.get("provider"), 160),
                    "status": _text(a.get("status"), 40),
                    "reason": _text(a.get("reason"), 600),
                    "observed_at": _text(a.get("observed_at"), 80),
                    "windows": [
                        {
                            "label": _text(w.get("label") or w.get("id"), 160),
                            "used_percent": w.get("used_percent")
                            if isinstance(w.get("used_percent"), int | float)
                            else (
                                100 - w["remaining_percent"]
                                if isinstance(w.get("remaining_percent"), int | float)
                                else None
                            ),
                            "resets_at": _text(w.get("resets_at"), 80),
                        }
                        for w in (a.get("windows") or [])[:12]
                    ],
                }
                for a in (overview.get("accounts") or [])[:40]
            ],
            "activity": {
                "calls": int(activity.get("calls") or 0),
                "input_tokens": activity.get("input_tokens"),
                "output_tokens": activity.get("output_tokens"),
                "providers": [
                    {
                        "provider": _text(p.get("provider"), 80),
                        "calls": int(p.get("calls") or 0),
                        "input_tokens": p.get("input_tokens"),
                        "output_tokens": p.get("output_tokens"),
                    }
                    for p in (activity.get("providers") or [])[:40]
                ],
            },
        }

    # Actions ---------------------------------------------------------------

    def _act(self, peer: str, op: str, params: dict[str, Any], request: str) -> Any:
        if op.startswith("rem."):
            return self._rem_action(peer, op, params, request)
        if op == "organizations.control":
            manager = self._organizations_manager()
            if params.get("state") not in {"running", "paused"}:
                raise Refused("invalid", "Choose running or paused.")
            identifier = _text(params.get("organization_id"), 100)
            manager.call(
                "",
                "user",
                {
                    "action": "control",
                    "organizationId": identifier,
                    "params": {"state": params["state"]},
                },
            )
            return self._organization(peer, identifier)
        if op == "organizations.task":
            return self._organization_task(peer, params, request)
        if op == "swarms.start":
            remote = self.app.remote
            self._need(remote.swarms, "Swarms")
            task = _text(params.get("task"), 4000).strip()
            if not task:
                raise Refused("invalid", "Describe the task for the swarm.")
            root, identifier = remote._swarm(peer, _text(params.get("swarm_id"), 36))
            _, grants = remote.projects(peer)
            remote.swarms.start(root, identifier, task, workspace_grants=grants)
            return {"started": True}
        if op == "settings.update":
            return self._update_settings(params, request)
        raise Refused("invalid", "Unknown desktop action.")

    def _rem_action(self, peer: str, op: str, params: dict[str, Any], request: str) -> Any:
        rem = self._need(self.rem, "Rem")
        if op == "rem.message":
            rem.submit_message({"request_id": request, "body": _text(params.get("body"), 20000)})
            return self._rem_home(peer)
        if op == "rem.instruct":
            thread = _text(params.get("thread_id"), 128)
            rem.instruct(thread, {"request_id": request, "body": _text(params.get("body"), 20000)})
            return self._rem_thread(thread)
        if op == "rem.task.create":
            fields = {
                key: params[key]
                for key in ("title", "type", "prompt", "schedule", "execution_mode")
                if key in params
            }
            project = params.get("project_id")
            if project:
                known = self.projects(peer).get(str(project))
                if known is None:
                    raise Refused("invalid", "That project is no longer open on desktop.")
                fields["project_dir"] = known["root"]
            rem.create_task({"request_id": request, **fields})
            return self._rem_home(peer)
        task = _text(params.get("task_id"), 128)
        if op in {"rem.task.run", "rem.task.pause", "rem.task.delete"}:
            body: dict[str, Any] = {"request_id": request}
            if op == "rem.task.pause":
                body["cancel_current"] = params.get("cancel_current") is True
            rem.edit_task(task, body, op.rsplit(".", 1)[1])
            return self._rem_home(peer)
        if op == "rem.task.resume":
            revision = params.get("expected_revision")
            if not isinstance(revision, int):
                raise Refused("invalid", "Refresh Rem before resuming this task.")
            rem.edit_task(
                task,
                {"request_id": request, "expected_revision": revision, "enabled": True},
                "edit",
            )
            return self._rem_home(peer)
        if op in {"rem.run.cancel", "rem.run.acknowledge"}:
            rem.store.mutate_run(
                _text(params.get("run_id"), 128), {"request_id": request}, op.rsplit(".", 1)[1]
            )
            return self._rem_home(peer)
        raise Refused("invalid", "Unknown Rem action.")

    def _organization_task(self, peer: str, params: dict[str, Any], request: str) -> Any:
        manager = self._organizations_manager()
        identifier = _text(params.get("organization_id"), 100)
        action = params.get("action")
        task = _text(params.get("task_id"), 100)
        revision = params.get("expected_revision")
        if action == "create":
            arguments: dict[str, Any] = {
                "action": "task_create",
                "params": {
                    "title": _text(params.get("title"), 500),
                    "description": _text(params.get("description"), 100000),
                    "priority": params.get("priority", "medium"),
                    "idempotencyKey": request,
                    **({"assignee": params["assignee"]} if params.get("assignee") else {}),
                },
            }
        elif action == "comment":
            arguments = {
                "action": "comment",
                "params": {
                    "taskId": task,
                    "body": _text(params.get("body"), 100000),
                    "idempotencyKey": request,
                },
            }
        elif action in {"approve", "reopen"}:
            arguments = {"action": action, "params": {"taskId": task, "expectedRevision": revision}}
        elif action == "review":
            arguments = {
                "action": "review",
                "params": {
                    "taskId": task,
                    "expectedRevision": revision,
                    "decision": params.get("decision"),
                    "body": _text(params.get("body"), 100000),
                },
            }
        elif action == "cancel":
            arguments = {
                "action": "task_update",
                "params": {
                    "taskId": task,
                    "expectedRevision": revision,
                    "changes": {"status": "cancelled"},
                },
            }
        elif action == "wake":
            arguments = {
                "action": "wake",
                "params": {
                    "employeeId": _text(params.get("employee_id"), 100),
                    "message": _text(params.get("body"), 4000) or "Check in",
                    "idempotencyKey": request,
                },
            }
        else:
            raise Refused("invalid", "Unknown organization task action.")
        manager.call("", "user", {**arguments, "organizationId": identifier})
        return self._organization(peer, identifier)

    # Settings --------------------------------------------------------------
    # The window owns its settings. It publishes the allowed values here and
    # applies queued phone changes through its normal settings code.

    def _load(self, key: str) -> Any:
        row = self.db.execute(
            "SELECT value FROM device_desktop_settings WHERE key=?", (key,)
        ).fetchone()
        return self.app._decode("desktop-settings", row[0])["value"] if row else None

    def _store(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO device_desktop_settings VALUES (?,?)",
            (key, self.app._encode("desktop-settings", {"value": value})),
        )

    def _settings_state(self) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        return self._load("snapshot"), self._load("pending") or []

    def _providers(self) -> list[dict[str, Any]]:
        return list(self.app.workspace.providers or [])

    def _settings_view(self) -> dict[str, Any]:
        snapshot, pending = self._settings_state()
        values = dict((snapshot or {}).get("values") or {})
        for change in pending:
            values.update(change["values"])
        providers = self._providers()
        items = []
        for item in SETTINGS:
            entry = {k: v for k, v in item.items()}
            if item["kind"] in {"provider", "model", "effort"}:
                entry["kind"] = "enum"
                provider = next(
                    (p for p in providers if p.get("id") == values.get("assistant.provider")), {}
                )
                if item["kind"] == "provider":
                    choices = [str(p["id"]) for p in providers if p.get("id")]
                elif item["kind"] == "model":
                    choices = [""] + [str(m) for m in provider.get("models") or []]
                else:
                    efforts = (provider.get("efforts") or {}).get(values.get("assistant.model"))
                    choices = [""] + [str(e) for e in efforts or []]
                current = values.get(item["key"])
                if isinstance(current, str) and current not in choices:
                    choices.append(current)
                entry["options"] = [[c, c or "Default"] for c in choices][:100]
            items.append(entry)
        return {
            "available": snapshot is not None,
            "updated_at": (snapshot or {}).get("updated_at"),
            "pending": len(pending),
            "values": {key: values.get(key) for key in SETTING_KEYS},
            "settings": items,
        }

    def _valid_setting(self, key: str, value: Any) -> bool:
        item = SETTING_KEYS.get(key)
        if item is None:
            return False
        kind = item["kind"]
        if kind == "bool":
            return isinstance(value, bool)
        if kind == "enum":
            return value in {option[0] for option in item["options"]}
        if kind == "number":
            return (
                isinstance(value, int | float)
                and not isinstance(value, bool)
                and item["min"] <= value <= item["max"]
            )
        providers = self._providers()
        if kind == "provider":
            return value in {str(p.get("id")) for p in providers}
        if kind == "model":
            return value == "" or value in {
                str(m) for p in providers for m in p.get("models") or []
            }
        return value == "" or value in {
            str(e)
            for p in providers
            for efforts in (p.get("efforts") or {}).values()
            for e in efforts or []
        }

    def _update_settings(self, params: dict[str, Any], request: str) -> dict[str, Any]:
        changes = params.get("changes")
        if not isinstance(changes, dict) or not 1 <= len(changes) <= len(SETTINGS):
            raise Refused("invalid", "Choose at least one setting to change.")
        for key, value in changes.items():
            if not self._valid_setting(key, value):
                raise Refused(
                    "invalid",
                    f"{SETTING_KEYS.get(key, {}).get('label', key)} has an unsupported value.",
                )
        snapshot, pending = self._settings_state()
        if snapshot is None:
            raise Refused(
                "unavailable", "Open Raticode on your computer once to load its settings."
            )
        if len(pending) >= PENDING_SETTINGS:
            raise Refused("conflict", "Earlier changes are still waiting for the desktop window.")
        pending.append({"id": request, "values": changes})
        self._store("pending", pending)
        return self._settings_view()

    def exchange_settings(self, settings: Any, applied: Any) -> dict[str, Any]:
        """Local window only: publish allowed values, receive queued phone changes."""
        with self.app.registry.transaction():
            _, pending = self._settings_state()
            done = {str(item) for item in applied or []} if isinstance(applied, list) else set()
            pending = [change for change in pending if change["id"] not in done]
            if isinstance(settings, dict):
                values = {}
                for key in SETTING_KEYS:
                    value = _path_value(settings, key)
                    if isinstance(value, bool | int | float | str) and len(str(value)) <= 160:
                        values[key] = value
                self._store("snapshot", {"values": values, "updated_at": time.time()})
            self._store("pending", pending)
            return {"changes": pending}
