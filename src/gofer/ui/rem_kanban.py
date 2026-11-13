"""The global Kanban board: Rem's own tickets beside every organization's.

Rem's tickets live in Rem's store. Organization tickets stay in their organization and are only
read here; every change to one goes through ``OrganizationManager.call``, so its approval, review
and revision rules still apply. The desktop, the phone and Rem's own decisions share this module.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from gofer.ui import rem_ticket_activity as activity
from gofer.ui.organization_store import (
    OrganizationConflict,
    assignable_roots,
    self_work_assignment,
    task_root,
    ticket_prefix,
)
from gofer.ui.rem_home_models import RemConflict, TicketRequest, now
from gofer.ui.rem_home_store import RemHomeStore, encode

# In review: a Rem ticket's run finished its work and its independent review is checking it.
REM_STATUSES = {"backlog", "todo", "in_progress", "in_review", "blocked", "done", "cancelled"}
PRIORITIES = {"critical": 0, "high": 1, "medium": 2, "low": 3}
TICKET_TYPES = {"research", "maintain", "develop", "errand"}
OPEN_STATUSES = {"backlog", "todo", "in_progress", "in_review", "blocked"}
MAX_THREADS = 16
DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "max_threads": 2,
    "auto_pull_backlog": False,
    "revision": 0,
}
# What one board response may carry, so a phone's reply always fits its message limit.
BOARD_BYTES = 700_000
BOARD_TICKETS = 500
CLOSED_TICKETS = 80
REPORT_ROWS = 2000
ORG_ACTOR = "Rem:kanban"
# A board ticket key as written in chat, such as REM-5 or AGE-18: a capital letter, then
# capitals or digits, a dash and a number. Not part of a longer word, path or version
# ("xREM-5", "REM-5a", "a/REM-5", "REM-5.2"). The desktop and phone match the same way
# (frontend/src/lib/ticketMentions.js, mobile TicketMentions.kt).
TICKET_KEY = re.compile(
    r"(?<![A-Za-z0-9_\-/.#@])[A-Z][A-Z0-9]{1,9}-[1-9][0-9]{0,6}(?![A-Za-z0-9_\-]|\.[0-9])"
)
# How many distinct keys one view resolves, and ticket references one run keeps.
INDEX_KEYS = 400
RUN_TICKETS = 40
# Organizations' ticket keys are read at most this often while chat views poll.
ORG_KEYS_FOR = 5.0
# Strongest first: a ticket a run created stays "created" if it later worked on it too.
TICKET_ROLES = ("created", "worked", "updated")
_STOP_WORDS = frozenset(
    "a an and add allow build create enable for implement in new of on support the to with".split()
)


def board_enabled(config: dict[str, Any]) -> bool:
    """Whether the desktop shows the global Kanban board; a window that doesn't say means yes.

    With the board off, self-work leaves tickets alone and runs research tasks only.
    """
    return config.get("kanban_board") is not False


class KanbanRefused(ValueError):
    """A request the board won't carry out. ``code`` matches the phone protocol's codes."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def title_words(title: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", title.casefold())
    return frozenset(w for w in words if w not in _STOP_WORDS) or frozenset(words)


def same_work(left: str, right: str) -> bool:
    """Whether two ticket titles describe the same work, conservatively.

    Only titles that share nearly every meaningful word match, so merging or dropping one
    can't lose a ticket that merely sounds related.
    """
    a, b = title_words(left), title_words(right)
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a | b) >= 3 and len(a & b) / len(a | b) >= 0.8


PRIORITY_POINTS = {"critical": 2, "high": 1, "medium": 0, "low": -1}


def backlog_score(ticket: dict[str, Any]) -> int:
    """Rem's impact and relevance scores, nudged by the user's priority. Highest is pulled first."""
    triage = ticket.get("triage") or {}
    points = PRIORITY_POINTS.get(ticket.get("priority", "medium"), 0)
    return int(triage.get("impact", 0)) + int(triage.get("relevance", 0)) + points


def clean_priority(value: Any) -> str:
    """Accepts the phone's ``urgent`` for the board's ``critical``."""
    priority = "critical" if value == "urgent" else value or "medium"
    if priority not in PRIORITIES:
        raise KanbanRefused("invalid", "Choose a priority of low, medium, high or critical.")
    return str(priority)


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def get_ticket(db: sqlite3.Connection, identity: str) -> dict[str, Any]:
    row = db.execute("SELECT body FROM kanban_tickets WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise KanbanRefused("not_found", "This ticket no longer exists.")
    return cast(dict[str, Any], json.loads(row[0]))


def put_ticket(db: sqlite3.Connection, ticket: dict[str, Any]) -> None:
    if ticket["status"] not in REM_STATUSES:
        raise ValueError(f"Unknown ticket status {ticket['status']!r}")
    ticket["updated_at"] = now()
    db.execute(
        "INSERT OR REPLACE INTO kanban_tickets VALUES(?,?,?)",
        (ticket["id"], ticket["status"], encode(ticket)),
    )


def read_tickets(db: sqlite3.Connection, *statuses: str) -> list[dict[str, Any]]:
    if statuses:
        rows = db.execute(
            f"SELECT body FROM kanban_tickets WHERE status IN ({','.join('?' * len(statuses))})",
            statuses,
        )
    else:
        rows = db.execute("SELECT body FROM kanban_tickets")
    return [json.loads(row[0]) for row in rows]


def add_report(db: sqlite3.Connection, kind: str, **body: Any) -> None:
    """Add a line to the running report. Only the newest rows are kept."""
    cursor = db.execute(
        "INSERT INTO self_work_report(body) VALUES(?)",
        (encode({"kind": kind, "at": now(), **body}),),
    )
    if cursor.lastrowid and cursor.lastrowid % 100 == 0:
        db.execute("DELETE FROM self_work_report WHERE seq<=?", (cursor.lastrowid - REPORT_ROWS,))


def new_ticket(db: sqlite3.Connection, **fields: Any) -> dict[str, Any]:
    highest = db.execute(
        "SELECT MAX(json_extract(body,'$.number')) FROM kanban_tickets"
    ).fetchone()[0]
    return {
        "id": str(uuid4()),
        "number": int(highest or 0) + 1,
        "title": "",
        "description": "",
        "status": "todo",
        "priority": "medium",
        "type": "develop",
        "project_dir": None,
        "done_when": "",
        "created_by": "user",
        "proposal": None,
        "duplicate_of": None,
        "duplicate_restored": False,
        "block_reason": "",
        "blocked_at": None,
        "pulled_from_backlog": False,
        "triage": None,
        "revision": 1,
        "created_at": now(),
        "updated_at": now(),
        "claim": None,
        "task_id": None,
        "run_id": None,
        "thread_id": None,
        "summary": "",
        "attempts": 0,
        "moved_to": None,
        **fields,
    }


def ticket_keys(text: Any) -> list[str]:
    """Ticket keys written in ``text``, first mention first, each once."""
    if not isinstance(text, str) or "-" not in text:
        return []
    return list(dict.fromkeys(TICKET_KEY.findall(text)))


def ticket_ref(ticket: dict[str, Any], role: str) -> dict[str, Any]:
    """How a run remembers one of Rem's tickets it touched; the title is kept for history."""
    return {
        "ticket_id": ticket["id"],
        "key": f"REM-{ticket['number']}",
        "title": str(ticket.get("title") or "")[:200],
        "scope": "rem",
        "role": role,
    }


def merge_refs(
    existing: Any, added: list[dict[str, Any]], limit: int = RUN_TICKETS
) -> list[dict[str, Any]]:
    """``existing`` references plus ``added``, one per ticket, keeping the strongest role."""
    merged: dict[str, dict[str, Any]] = {}
    for ref in [*(existing if isinstance(existing, list) else []), *added]:
        if not isinstance(ref, dict) or not ref.get("ticket_id") or not ref.get("key"):
            continue
        identity = str(ref["ticket_id"])
        role = ref.get("role") if ref.get("role") in TICKET_ROLES else "updated"
        earlier = merged.get(identity)
        if earlier is not None and TICKET_ROLES.index(earlier["role"]) < TICKET_ROLES.index(role):
            role = earlier["role"]
        merged[identity] = {**(earlier or {}), **ref, "role": role}
    return list(merged.values())[:limit]


def link_tickets(
    db: sqlite3.Connection, store: RemHomeStore, run_id: str, refs: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Record on a run the tickets it created or worked on, so its card can list them."""
    row = db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None or not refs:
        return json.loads(row[0]) if row else None
    run = cast(dict[str, Any], json.loads(row[0]))
    run["tickets"] = merge_refs(run.get("tickets"), refs)
    store.put(db, "runs", run)
    return run


def message_ticket_keys(messages: list[dict[str, Any]]) -> list[str]:
    """Keys mentioned in chat messages and on their task cards, newest messages first."""
    keys: dict[str, None] = {}
    for message in reversed(messages):
        for text in (message.get("body"), message.get("details")):
            keys.update(dict.fromkeys(ticket_keys(text)))
        for ref in (message.get("work") or {}).get("tickets") or []:
            if isinstance(ref, dict) and isinstance(ref.get("key"), str):
                keys[ref["key"]] = None
        if len(keys) >= INDEX_KEYS:
            break
    return list(keys)[:INDEX_KEYS]


def task_ticket_keys(tasks: list[dict[str, Any]]) -> list[str]:
    """Keys of the tickets that tasks' recent runs created or worked on."""
    keys: dict[str, None] = {}
    for task in tasks:
        for run in task.get("recent_runs") or []:
            for ref in run.get("tickets") or []:
                if isinstance(ref, dict) and isinstance(ref.get("key"), str):
                    keys[ref["key"]] = None
    return list(keys)[:INDEX_KEYS]


def pending_proposal(ticket: dict[str, Any]) -> bool:
    """Whether a proposal is open and waiting for the user. A merged duplicate isn't."""
    return (ticket.get("proposal") or {}).get("state") == "pending" and (
        ticket["status"] != "cancelled"
    )


def rem_assignment(db: sqlite3.Connection, store: RemHomeStore) -> dict[str, Any]:
    """The projects the user assigned to Rem for self-work, with their revision."""
    saved = store.setting(db, "self_work_projects")
    return {"projects": list(saved.get("projects", [])), "revision": int(saved.get("revision", 0))}


def open_project_roots(config: dict[str, Any]) -> list[str]:
    return [p["root"] for p in config.get("projects", []) if isinstance(p, dict) and p.get("root")]


def usable_projects(config: dict[str, Any], assignment: dict[str, Any]) -> set[str]:
    """Where Rem may work on its own: assigned, open in Raticode, and still on disk."""
    opened = set(open_project_roots(config))
    return {r for r in assignment["projects"] if r in opened and Path(r).is_dir()}


def self_work_scope(ticket: dict[str, Any], assignment: dict[str, Any], usable: set[str]) -> str:
    """Why self-work can or can't take this ticket."""
    root = ticket.get("project_dir")
    if not root:
        return "no_project"
    if root in usable:
        return "assigned"
    return "unavailable" if root in assignment["projects"] else "unassigned"


def ticket_view(
    ticket: dict[str, Any],
    assignment: dict[str, Any] | None = None,
    usable: set[str] | None = None,
) -> dict[str, Any]:
    root = ticket.get("project_dir")
    return {
        "ticket_id": ticket["id"],
        "scope": "rem",
        "organization_id": "",
        "organization_name": "",
        "key": f"REM-{ticket['number']}",
        "title": ticket["title"],
        "description": ticket["description"][:4000],
        "status": ticket["status"],
        "priority": ticket["priority"],
        "assignee_id": "rem",
        "assignee": "Rem",
        "proposed": pending_proposal(ticket),
        "proposal_reason": (ticket.get("proposal") or {}).get("reason", ""),
        "duplicate_of": ticket.get("duplicate_of") or "",
        "created_by": ticket.get("created_by", ""),
        "created_at": ticket["created_at"],
        "updated_at": ticket["updated_at"],
        "revision": ticket["revision"],
        # A paused run keeps its claim but isn't running; "paused" says who paused it.
        "running": bool(ticket.get("claim")) and not ticket.get("paused"),
        "paused": str((ticket.get("paused") or {}).get("by") or ""),
        "block_reason": ticket.get("block_reason", ""),
        "summary": ticket.get("summary", ""),
        "type": ticket.get("type", "develop"),
        "project_dir": root,
        "project": Path(root).name if root else "",
        # How Rem ranked it for the next pull, and why it was left out, if it was.
        "backlog_score": backlog_score(ticket),
        "skip_reason": (ticket.get("triage") or {}).get("reason", "")
        if (ticket.get("triage") or {}).get("skip")
        else "",
        "self_work_scope": self_work_scope(ticket, assignment or {"projects": []}, usable or set()),
        "run_id": ticket.get("run_id") or "",
        "thread_id": ticket.get("thread_id") or "",
    }


def organization_ticket_view(org: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    employees = {e["id"]: e for e in org["config"]["employees"]}
    assignee = task.get("assignee") or ""
    number = task.get("number")
    root = task_root(org["config"], task)
    return {
        "ticket_id": task["id"],
        "scope": "organization",
        "organization_id": org["id"],
        "organization_name": org["config"]["name"],
        "key": f"{ticket_prefix(org['config'])}-{number}" if number else "",
        "title": task["title"],
        "description": task.get("description", "")[:4000],
        "status": task["status"],
        "priority": task.get("priority", "medium"),
        "assignee_id": assignee,
        "assignee": (employees.get(assignee) or {}).get("name", assignee),
        "proposed": False,
        "proposal_reason": "",
        "duplicate_of": "",
        "created_by": "",
        "created_at": task.get("createdAt", ""),
        "updated_at": task.get("createdAt", ""),
        "revision": task["revision"],
        "running": bool(task.get("claim")),
        "block_reason": task.get("blockReason", ""),
        "summary": "",
        "type": "",
        "project_dir": root or None,
        "project": Path(root).name if root else "",
        "backlog_score": 0,
        "skip_reason": "",
        "self_work_scope": "",
        "run_id": "",
        "thread_id": "",
    }


def _newest(ticket: dict[str, Any]) -> str:
    return str(ticket.get("updated_at") or ticket.get("created_at") or "")


class RemKanban:
    def __init__(
        self,
        store: RemHomeStore,
        *,
        organizations: Callable[[], Any] = lambda: None,
        start: Callable[[str, str], None] | None = None,
        resume: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.store = store
        self.organizations = organizations
        # Starts a ticket's run now (the self-work engine); given the ticket ID and actor.
        self.start = start
        # Resumes a ticket's paused run in its thread; given the run ID and actor.
        self.resume = resume
        # Organization ticket keys, re-read at most every ORG_KEYS_FOR seconds.
        self._org_keys: tuple[float, dict[str, dict[str, Any]]] = (-ORG_KEYS_FOR, {})

    # Organizations ------------------------------------------------------------

    def manager(self) -> Any:
        manager = self.organizations()
        return manager if manager is not None and manager.enabled else None

    def configured_organizations(self) -> list[dict[str, Any]]:
        """Every organization that isn't archived, with its tickets."""
        manager = self.manager()
        if manager is None:
            return []
        return [
            org
            for org in manager.store.list_organizations()
            if org["runtime"].get("archived") is not True
        ]

    def _organization(self, identifier: str) -> dict[str, Any]:
        org = next((o for o in self.configured_organizations() if o["id"] == identifier), None)
        if org is None:
            raise KanbanRefused("not_found", "That organization isn't available.")
        return org

    def _call(self, actor: str, identifier: str, action: str, params: dict[str, Any]) -> Any:
        """One change to an organization ticket, under that organization's own rules."""
        manager = self.manager()
        if manager is None:
            raise KanbanRefused("unavailable", "Organizations is turned off.")
        self._organization(identifier)
        try:
            return manager.call(
                "",
                "user" if actor == "user" else ORG_ACTOR,
                {"action": action, "organizationId": identifier, "params": params},
            )
        except OrganizationConflict as exc:
            raise KanbanRefused("conflict", str(exc)) from None

    # Ticket keys in chat ----------------------------------------------------------

    def resolve(self, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
        """The tickets that ``keys`` name on the board right now, by key.

        Only keys of tickets that exist are returned, with their current title and status,
        so a renamed ticket shows its new title and a deleted or declined one drops out.
        Rem's own REM-n tickets win over an organization that uses the same prefix.
        """
        wanted = [k for k in dict.fromkeys(keys) if isinstance(k, str) and TICKET_KEY.fullmatch(k)]
        wanted = wanted[:INDEX_KEYS]
        found: dict[str, dict[str, Any]] = {}
        numbers = [int(k[4:]) for k in wanted if k.startswith("REM-")]
        if numbers:
            with self.store.read() as db:
                rows = db.execute(
                    "SELECT body FROM kanban_tickets WHERE json_extract(body,'$.number') IN "
                    f"({','.join('?' * len(numbers))})",
                    numbers,
                ).fetchall()
            for row in rows:
                ticket = json.loads(row[0])
                if (ticket.get("proposal") or {}).get("state") == "declined":
                    continue
                key = f"REM-{ticket['number']}"
                found[key] = {
                    "ticket_id": ticket["id"],
                    "key": key,
                    "title": str(ticket.get("title") or "")[:200],
                    "status": ticket["status"],
                    "scope": "rem",
                    "organization_id": "",
                    "proposed": pending_proposal(ticket),
                }
        others = [k for k in wanted if k not in found]
        if others:
            known = self._organization_keys()
            found.update({k: known[k] for k in others if k in known})
        return found

    def _organization_keys(self) -> dict[str, dict[str, Any]]:
        read_at, known = self._org_keys
        if time.monotonic() - read_at < ORG_KEYS_FOR:
            return known
        known = {}
        try:
            organizations = self.configured_organizations()
        except Exception:  # noqa: BLE001 - chat still shows, with plain keys
            organizations = []
        for org in organizations:
            prefix = ticket_prefix(org["config"])
            for task in org["runtime"]["tasks"]:
                number = task.get("number")
                if task.get("heartbeat") or not number:
                    continue
                key = f"{prefix}-{number}"
                known.setdefault(
                    key,
                    {
                        "ticket_id": task["id"],
                        "key": key,
                        "title": str(task.get("title") or "")[:200],
                        "status": task["status"],
                        "scope": "organization",
                        "organization_id": org["id"],
                        "organization_name": org["config"]["name"],
                        "proposed": False,
                    },
                )
        self._org_keys = (time.monotonic(), known)
        return known

    def forget_organization_keys(self) -> None:
        """Read organizations' tickets again on the next lookup."""
        self._org_keys = (-ORG_KEYS_FOR, {})

    # Views ---------------------------------------------------------------------

    def board(self) -> dict[str, Any]:
        with self.store.read() as db:
            config = self.store.setting(db, "config")
            assignment = rem_assignment(db, self.store)
            usable = usable_projects(config, assignment)
            tickets = [
                ticket_view(t, assignment, usable)
                for t in read_tickets(db)
                if (t.get("proposal") or {}).get("state") != "declined"
            ]
        open_roots = open_project_roots(config)
        configured = self.configured_organizations()
        participants = [
            {
                "participant_id": "rem",
                "kind": "rem",
                "name": "Rem",
                "title": "Coordinator",
                "organization_id": "",
                "organization_name": "",
                "employee_id": "",
                "paused": False,
                "projects": assignment["projects"],
                "assignable": open_roots,
                "unavailable": [r for r in assignment["projects"] if r not in usable],
                "revision": assignment["revision"],
            }
        ]
        owners: dict[str, str] = {}
        for org in configured:
            for root in org["config"].get("projectRoots", []):
                owners.setdefault(root, org["config"]["name"])
            for employee in org["config"]["employees"]:
                saved = self_work_assignment(org["runtime"], employee["id"])
                assignable = assignable_roots(org["config"], employee)
                participants.append(
                    {
                        "participant_id": f"agent:{org['id']}:{employee['id']}",
                        "kind": "agent",
                        "name": employee["name"],
                        "title": employee.get("title", ""),
                        "organization_id": org["id"],
                        "organization_name": org["config"]["name"],
                        "employee_id": employee["id"],
                        "paused": employee.get("paused") is True,
                        "projects": saved["projects"],
                        "assignable": assignable,
                        "unavailable": [
                            r
                            for r in saved["projects"]
                            if r not in assignable or not Path(r).is_dir()
                        ],
                        "revision": saved["revision"],
                    }
                )
        known = list(
            dict.fromkeys([*open_roots, *owners, *(r for p in participants for r in p["projects"])])
        )
        projects = [
            {
                "root": root,
                "name": Path(root).name,
                "available": Path(root).is_dir(),
                "organization_name": owners.get(root, ""),
            }
            for root in known
        ]
        organizations = []
        for org in configured:
            organizations.append(
                {
                    "organization_id": org["id"],
                    "name": org["config"]["name"],
                    "state": org["runtime"]["state"],
                    "employees": [
                        {"employee_id": e["id"], "name": e["name"], "title": e.get("title", "")}
                        for e in org["config"]["employees"]
                    ],
                }
            )
            tickets.extend(
                organization_ticket_view(org, task)
                for task in org["runtime"]["tasks"]
                if not task.get("heartbeat")
            )
        opened = sorted((t for t in tickets if t["status"] in OPEN_STATUSES), key=_newest)
        closed = sorted((t for t in tickets if t["status"] not in OPEN_STATUSES), key=_newest)
        shown: list[dict[str, Any]] = []
        used = 0
        for ticket in [*reversed(opened), *list(reversed(closed))[:CLOSED_TICKETS]]:
            used += len(ticket["description"]) + 700
            if used > BOARD_BYTES or len(shown) >= BOARD_TICKETS:
                break
            shown.append(ticket)
        counts: dict[str, int] = {}
        for ticket in tickets:
            counts[ticket["status"]] = counts.get(ticket["status"], 0) + 1
        return {
            "tickets": shown,
            "organizations": organizations,
            "participants": participants,
            "projects": projects,
            "earlier": len(tickets) - len(shown),
            "counts": counts,
        }

    def for_prompt(self) -> dict[str, Any]:
        """The board as Rem's chat sees it, so it can name real tickets and organizations."""
        board = self.board()
        return {
            "tickets": [
                {
                    **{
                        k: t[k]
                        for k in (
                            "ticket_id",
                            "scope",
                            "organization_id",
                            "key",
                            "title",
                            "status",
                            "priority",
                            "assignee",
                            "revision",
                            "proposed",
                        )
                    },
                }
                for t in board["tickets"]
                if t["status"] in OPEN_STATUSES
            ][:120],
            "organizations": board["organizations"],
            "counts": board["counts"],
            # Set by the user on the board. Self-work stays inside these; requests may not.
            "your_self_work_projects": [
                Path(root).name for root in board["participants"][0]["projects"]
            ],
        }

    def set_participant_projects(self, actor: str, payload: dict[str, Any]) -> None:
        """Replace a participant's self-work projects. Only the user decides this."""
        if actor != "user":
            raise KanbanRefused("unauthorized", "Only you can assign projects.")
        identity = _text(payload.get("participant_id"), 300)
        revision = self._revision(payload)
        roots = payload.get("projects")
        if not isinstance(roots, list) or not all(isinstance(r, str) for r in roots):
            raise KanbanRefused("invalid", "Choose projects.")
        if len(roots) > 100 or len(set(roots)) != len(roots):
            raise KanbanRefused("invalid", "Choose each project once, at most 100.")
        if identity == "rem":
            with self.store.connect() as db:
                saved = rem_assignment(db, self.store)
                if saved["revision"] != revision:
                    raise KanbanRefused(
                        "conflict", "These assignments changed. Check them and try again."
                    )
                opened = open_project_roots(self.store.setting(db, "config"))
                if set(roots) - set(saved["projects"]) - set(opened):
                    raise KanbanRefused("invalid", "Rem can only be given open projects.")
                self.store.set_setting(
                    db, "self_work_projects", {"projects": roots, "revision": revision + 1}
                )
            return
        parts = identity.split(":", 2)
        if len(parts) != 3 or parts[0] != "agent":
            raise KanbanRefused("not_found", "That participant isn't available.")
        self._call(
            actor,
            parts[1],
            "self_work_projects",
            {"employeeId": parts[2], "projects": roots, "expectedRevision": revision},
        )

    # Changes --------------------------------------------------------------------

    def act(self, actor: str, payload: dict[str, Any], request_id: str) -> dict[str, Any] | None:
        """Carry out one board request from the desktop, a phone or Rem."""
        action = payload.get("action")
        if action == "create":
            return self.create(actor, payload, request_id)
        elif action == "edit":
            self.edit(actor, payload)
        elif action == "move":
            self.move(actor, payload)
        elif action == "assign":
            self.assign(actor, payload, request_id)
        elif action in {"approve", "decline"}:
            self.decide(actor, payload, str(action))
        elif action == "comment":
            self.comment(actor, payload, request_id)
        else:
            raise KanbanRefused("invalid", "Unknown ticket action.")
        return None

    @staticmethod
    def _ticket_id(payload: dict[str, Any]) -> str:
        identity = _text(payload.get("ticket_id"), 128)
        if not identity:
            raise KanbanRefused("invalid", "Choose a ticket.")
        return identity

    @staticmethod
    def _revision(payload: dict[str, Any]) -> int:
        revision = payload.get("expected_revision")
        if type(revision) is not int:
            raise KanbanRefused("invalid", "Refresh the board before changing this ticket.")
        return revision

    @staticmethod
    def _check_revision(ticket: dict[str, Any], revision: int) -> None:
        if ticket["revision"] != revision:
            raise KanbanRefused("conflict", "This ticket changed. Check it and try again.")

    @staticmethod
    def _organization_id(payload: dict[str, Any]) -> str:
        identity = _text(payload.get("organization_id"), 100)
        if not identity:
            raise KanbanRefused("invalid", "Choose an organization.")
        return identity

    def create(self, actor: str, payload: dict[str, Any], request_id: str) -> dict[str, Any]:
        title = _text(payload.get("title"), 500)
        if not title:
            raise KanbanRefused("invalid", "Give the ticket a title.")
        description = _text(payload.get("description"), 100_000)
        priority = clean_priority(payload.get("priority"))
        status = payload.get("status") or "todo"
        if status not in {"backlog", "todo"}:
            raise KanbanRefused("invalid", "New tickets start in Backlog or To do.")
        assignee = _text(payload.get("assignee_id"), 100)
        if payload.get("scope") == "organization" or payload.get("organization_id"):
            identifier = self._organization_id(payload)
            if assignee == "rem":
                raise KanbanRefused("invalid", "Tickets in an organization go to its workers.")
            result = self._call(
                actor,
                identifier,
                "task_create",
                {
                    "title": title,
                    "description": description,
                    "priority": priority,
                    "status": status,
                    "idempotencyKey": request_id,
                    **({"assignee": assignee} if assignee else {}),
                },
            )
            if actor == "rem":
                name = self._organization(identifier)["config"]["name"]
                with self.store.connect() as db:
                    add_report(
                        db,
                        "ticket_added",
                        ticket_id=str(result.get("id") or result.get("taskId") or ""),
                        title=title,
                        scope="organization",
                        organization_id=identifier,
                        organization_name=name,
                        proposed=False,
                    )
            return dict(result)
        if assignee not in {"", "rem"}:
            raise KanbanRefused("invalid", "Choose an organization for that worker.")
        kind = payload.get("type") or "develop"
        if kind not in TICKET_TYPES:
            raise KanbanRefused("invalid", "Choose a kind of work this ticket needs.")
        project = payload.get("project_dir")
        if project is not None and not isinstance(project, str):
            raise KanbanRefused("invalid", "Choose an open project.")

        def operation(db: sqlite3.Connection) -> dict[str, Any]:
            ticket = new_ticket(
                db,
                title=title,
                description=description,
                status=status,
                priority=priority,
                type=kind,
                project_dir=project or None,
                done_when=_text(payload.get("done_when"), 1000),
                created_by=actor,
            )
            put_ticket(db, ticket)
            if actor == "rem":
                add_report(
                    db,
                    "ticket_added",
                    ticket_id=ticket["id"],
                    title=title,
                    scope="rem",
                    organization_name="",
                    proposed=False,
                )
            return {"ticket": ticket}

        fingerprint = {k: v for k, v in payload.items() if k not in {"request_id", "action"}} | {
            "request_id": request_id
        }
        return self.store.command("kanban.create", fingerprint, operation)

    def edit(self, actor: str, payload: dict[str, Any]) -> None:
        identity, revision = self._ticket_id(payload), self._revision(payload)
        changes: dict[str, Any] = {}
        if "title" in payload:
            changes["title"] = _text(payload["title"], 500)
            if not changes["title"]:
                raise KanbanRefused("invalid", "Give the ticket a title.")
        if "description" in payload:
            changes["description"] = _text(payload["description"], 100_000)
        if "priority" in payload:
            changes["priority"] = clean_priority(payload["priority"])
        if payload.get("scope") == "organization":
            self._call(
                actor,
                self._organization_id(payload),
                "task_update",
                {"taskId": identity, "expectedRevision": revision, "changes": changes},
            )
            return
        if "project_dir" in payload:
            project = payload["project_dir"]
            if project is not None and not isinstance(project, str):
                raise KanbanRefused("invalid", "Choose an open project.")
            changes["project_dir"] = project or None
        if "type" in payload:
            if payload["type"] not in TICKET_TYPES:
                raise KanbanRefused("invalid", "Choose a kind of work this ticket needs.")
            changes["type"] = payload["type"]
        if "done_when" in payload:
            changes["done_when"] = _text(payload["done_when"], 1000)
        with self.store.connect() as db:
            ticket = get_ticket(db, identity)
            self._check_revision(ticket, revision)
            if ticket.get("claim"):
                raise KanbanRefused("conflict", "Stop this ticket's run before editing its plan.")
            if changes.keys() & {"title", "description", "project_dir"}:
                ticket["triage"] = None
            ticket.update(changes, revision=ticket["revision"] + 1)
            put_ticket(db, ticket)

    def move(self, actor: str, payload: dict[str, Any]) -> None:
        identity, revision = self._ticket_id(payload), self._revision(payload)
        column = payload.get("column")
        if payload.get("scope") == "organization":
            move_organization_ticket(
                self._organization_id(payload), identity, revision, column, self._move_call(actor)
            )
            return
        target = "in_progress" if column == "running" else column
        if target not in {"backlog", "todo", "in_progress", "done", "cancelled"}:
            raise KanbanRefused("invalid", "Choose a column for this ticket.")
        with self.store.connect() as db:
            ticket = get_ticket(db, identity)
            self._check_revision(ticket, revision)
            proposal = (ticket.get("proposal") or {}).get("state")
            # A merged proposal can come back to Backlog, where it still needs approval.
            if proposal == "pending" and (ticket["status"] != "cancelled" or target != "backlog"):
                raise KanbanRefused("invalid", "Approve this ticket before moving it.")
            if proposal == "declined" and actor != "user":
                raise KanbanRefused(
                    "unauthorized", "You declined this ticket, so Rem can't move it."
                )
            paused_run = (ticket.get("claim") or {}).get("run_id") if ticket.get("paused") else None
            if paused_run and target == "in_progress" and actor == "user" and self.resume:
                resume_run = paused_run
            elif paused_run:
                raise KanbanRefused(
                    "conflict",
                    "This ticket's task is paused. Move it to In progress to resume it, or stop "
                    "its run first.",
                )
            elif ticket.get("claim"):
                raise KanbanRefused(
                    "conflict", "This ticket is running. Stop its run or wait for it to finish."
                )
            else:
                resume_run = None
            if resume_run:
                pass  # The run resumes below, outside this transaction; the ticket follows it.
            elif target == "in_progress":
                if ticket["status"] not in {"backlog", "todo"}:
                    raise KanbanRefused("invalid", "Move it back to To do before starting it.")
            else:
                if target == "done" and actor != "user":
                    raise KanbanRefused("unauthorized", "Only you can mark a ticket done by hand.")
                if ticket["status"] != target:
                    before = ticket["status"]
                    duplicate = ticket.get("duplicate_of")
                    ticket.update(
                        # Moved by hand: it no longer follows its last run, and Rem may
                        # try to get it going on its own again.
                        sync=None,
                        unblock=None,
                        auto_unblocks=0,
                        status=target,
                        block_reason="",
                        blocked_at=None,
                        duplicate_of=None,
                        # Bringing back a merged ticket says it isn't a duplicate, for good.
                        duplicate_restored=bool(
                            ticket.get("duplicate_of") or ticket.get("duplicate_restored")
                        ),
                        # A moved ticket starts over; its last run no longer speaks for it.
                        task_id=None,
                        run_id=None,
                        pulled_from_backlog=False,
                        triage=None,
                        revision=ticket["revision"] + 1,
                        summary="Marked done by you."
                        if target == "done"
                        else ""
                        if ticket.get("duplicate_of")
                        else ticket["summary"],
                    )
                    put_ticket(db, ticket)
                    who = "you" if actor == "user" else "Rem"
                    activity.note_move(
                        db,
                        ticket,
                        before,
                        author="user" if actor == "user" else "rem",
                        title=(
                            "Marked done by hand"
                            if target == "done"
                            else "Cancelled"
                            if target == "cancelled"
                            else "Restored from a merge"
                            if duplicate
                            else f"Moved to {activity.STATUS_LABELS[target]}"
                        ),
                        body=f"Moved by {who}."
                        if target != "done"
                        else "Marked done by you without a verified run.",
                    )
                return
        if resume_run and self.resume:
            # Moving a paused ticket to In progress resumes the same run in its thread.
            self.resume(resume_run, actor)
            return
        if self.start is None:
            raise KanbanRefused("unavailable", "Rem can't start work right now.")
        self.start(identity, actor)

    def _move_call(self, actor: str) -> Callable[[str, str, dict[str, Any]], Any]:
        def call(identifier: str, action: str, params: dict[str, Any]) -> Any:
            return self._call(actor, identifier, action, params)

        return call

    def assign(self, actor: str, payload: dict[str, Any], request_id: str) -> None:
        identity, revision = self._ticket_id(payload), self._revision(payload)
        assignee = _text(payload.get("assignee_id"), 100)
        if payload.get("scope") == "organization":
            if assignee == "rem":
                raise KanbanRefused(
                    "invalid",
                    "An organization owns its tickets. Create a new ticket for Rem instead.",
                )
            self._call(
                actor,
                self._organization_id(payload),
                "task_update",
                {
                    "taskId": identity,
                    "expectedRevision": revision,
                    "changes": {"assignee": assignee or None},
                },
            )
            return
        destination = _text(payload.get("to_organization_id"), 100)
        if not destination:
            if assignee not in {"", "rem"}:
                raise KanbanRefused("invalid", "Choose an organization for that worker.")
            return
        # Hands a ticket to an organization: the copy there is created first, so a
        # refusal or crash can't lose it, and only then is Rem's closed.
        with self.store.read() as db:
            ticket = get_ticket(db, identity)
        self._check_revision(ticket, revision)
        if ticket.get("claim") or ticket["status"] not in {"backlog", "todo", "blocked"}:
            raise KanbanRefused("conflict", "Only tickets that aren't running can be handed over.")
        if pending_proposal(ticket):
            raise KanbanRefused("invalid", "Approve this ticket before handing it over.")
        result = self._call(
            actor,
            destination,
            "task_create",
            {
                "title": ticket["title"],
                "description": ticket["description"],
                "priority": ticket["priority"],
                "status": "backlog" if ticket["status"] == "backlog" else "todo",
                "idempotencyKey": f"handover:{identity}:{revision}",
                **({"assignee": assignee} if assignee and assignee != "rem" else {}),
            },
        )
        with self.store.connect() as db:
            current = get_ticket(db, identity)
            self._check_revision(current, revision)
            before = current["status"]
            name = self._organization(destination)["config"]["name"]
            current.update(
                status="cancelled",
                revision=current["revision"] + 1,
                moved_to={
                    "organization_id": destination,
                    "task_id": str(result.get("id") or result.get("taskId") or ""),
                },
                summary=f"Handed to {name}.",
            )
            put_ticket(db, current)
            activity.add_comment(
                db,
                identity,
                author="user" if actor == "user" else "rem",
                kind="status",
                title="Cancelled: handed to an organization",
                body=f"Handed to {name}, where the work continues as a new ticket.",
                status_from=before,
                status_to="cancelled",
            )

    def decide(self, actor: str, payload: dict[str, Any], action: str) -> None:
        if actor != "user":
            raise KanbanRefused("unauthorized", "Only you can approve or decline proposals.")
        identity, revision = self._ticket_id(payload), self._revision(payload)
        if payload.get("scope") == "organization":
            raise KanbanRefused("invalid", "Review this ticket in its organization.")
        with self.store.connect() as db:
            ticket = get_ticket(db, identity)
            self._check_revision(ticket, revision)
            if not pending_proposal(ticket):
                raise KanbanRefused("invalid", "This ticket isn't waiting for approval.")
            proposal = dict(ticket["proposal"])
            if action == "approve":
                proposal.update(state="approved", decided_at=now())
                # Approving is what moves an idea from Backlog to To do.
                ticket.update(proposal=proposal, status="todo", revision=ticket["revision"] + 1)
            else:
                proposal.update(
                    state="declined", decided_at=now(), decline=_text(payload.get("reason"), 1000)
                )
                ticket.update(
                    proposal=proposal, status="cancelled", revision=ticket["revision"] + 1
                )
                activity.add_comment(
                    db,
                    identity,
                    author="user",
                    kind="status",
                    title="Cancelled: proposal declined",
                    body=proposal.get("decline") or "Declined by you.",
                    status_from="backlog",
                    status_to="cancelled",
                )
            put_ticket(db, ticket)

    def comment(self, actor: str, payload: dict[str, Any], request_id: str) -> None:
        """Add a comment to a ticket's timeline: the user's, or Rem's."""
        body = _text(payload.get("body"), activity.COMMENT_CHARS)
        if not body:
            raise KanbanRefused("invalid", "Write a comment.")
        identity = self._ticket_id(payload)
        if payload.get("scope") == "organization":
            self._call(
                actor,
                self._organization_id(payload),
                "comment",
                {"taskId": identity, "body": body, "idempotencyKey": request_id},
            )
            return
        with self.store.connect() as db:
            get_ticket(db, identity)
            activity.add_comment(
                db,
                identity,
                author="user" if actor == "user" else "rem",
                body=body,
                dedupe=f"request:{request_id}",
            )

    def timeline(self, payload: dict[str, Any]) -> dict[str, Any]:
        """One ticket's timeline, oldest first, with the ticket as the board shows it now."""
        identity = self._ticket_id(payload)
        if payload.get("scope") == "organization":
            org = self._organization(self._organization_id(payload))
            task = next((t for t in org["runtime"]["tasks"] if t["id"] == identity), None)
            if task is None:
                raise KanbanRefused("not_found", "This ticket no longer exists.")
            comments = [
                activity.organization_comment(c, org)
                for c in activity.visible_comments(task.get("comments") or [])
            ]
            earlier = max(0, len(comments) - activity.TIMELINE_ENTRIES)
            return {
                "ticket": organization_ticket_view(org, task),
                "comments": comments[earlier:],
                "earlier": earlier,
                "can_comment": True,
            }
        with self.store.read() as db:
            ticket = get_ticket(db, identity)
            config = self.store.setting(db, "config")
            assignment = rem_assignment(db, self.store)
            entries, earlier = activity.read_comments(db, identity)
        return {
            "ticket": ticket_view(ticket, assignment, usable_projects(config, assignment)),
            "comments": [activity.rem_comment(e) for e in entries],
            "earlier": earlier,
            "can_comment": True,
        }

    def apply_decision(self, tickets: list[TicketRequest], request: str) -> str:
        """Carry out the ticket requests in Rem's decision; returns what to tell the user."""
        return self.apply_changes(tickets, request)[0]

    def apply_changes(
        self, tickets: list[TicketRequest], request: str
    ) -> tuple[str, list[dict[str, Any]]]:
        """Carry out Rem's ticket requests: what to tell the user, and the tickets changed.

        Each one succeeds or fails alone and is keyed by the chat request, so answering
        the same message again after a crash can't add a ticket twice. The reply names
        each ticket by its key, which chat shows as a pill with the ticket's title.
        """
        lines = []
        refs: list[dict[str, Any]] = []
        for index, ticket in enumerate(tickets):
            payload = ticket.model_dump(exclude_none=True)
            try:
                result = self.act("rem", payload, f"rem-ticket-{request}-{index}")
            except (ValueError, RemConflict) as exc:
                lines.append(f"Couldn't update the board for “{ticket.label()}”: {exc}")
                continue
            ref = self._changed(ticket, result)
            lines.append(ticket.done_text(self._destination(ticket), (ref or {}).get("key", "")))
            if ref:
                refs.append(ref)
        return "\n".join(lines), refs

    def _changed(
        self, ticket: TicketRequest, result: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """A reference to the ticket a request created or changed, when it can be found."""
        role = "created" if ticket.action == "create" else "updated"
        if ticket.organization_id or ticket.scope == "organization":
            identity = str((result or {}).get("id") or (result or {}).get("taskId") or "")
            identity = identity or str(ticket.ticket_id or "")
            self.forget_organization_keys()
            found = next(
                (t for t in self._organization_keys().values() if t["ticket_id"] == identity), None
            )
            if found is None:
                return None
            return {
                "ticket_id": found["ticket_id"],
                "key": found["key"],
                "title": found["title"],
                "scope": "organization",
                "organization_id": found["organization_id"],
                "role": role,
            }
        created = (result or {}).get("ticket")
        if isinstance(created, dict):
            return ticket_ref(created, role)
        try:
            with self.store.read() as db:
                return ticket_ref(get_ticket(db, str(ticket.ticket_id or "")), role)
        except KanbanRefused:
            return None

    def _destination(self, ticket: TicketRequest) -> str:
        if not ticket.organization_id:
            return "my"
        try:
            return f"{self._organization(ticket.organization_id)['config']['name']}’s"
        except ValueError:
            return "the organization’s"


def move_organization_ticket(
    identifier: str,
    task_id: str,
    revision: int,
    column: Any,
    call: Callable[[str, str, dict[str, Any]], Any],
) -> None:
    """Move an organization ticket between Backlog, To do and In progress, as its board does.

    Done goes through review, and Review is where finished runs land, so neither is a
    move. In progress means "work on this next": the desktop queues it for its worker.
    """
    if column not in {"backlog", "todo", "running", "in_progress"}:
        raise KanbanRefused("invalid", "Tickets move to Backlog, To do or In progress here.")
    wanted = "running" if column == "in_progress" else column
    org = call(identifier, "read", {})
    task = next(
        (t for t in (org.get("runtime") or {}).get("tasks") or [] if t.get("id") == task_id),
        None,
    )
    if task is None:
        raise KanbanRefused("not_found", "This ticket no longer exists.")
    if revision != task.get("revision"):
        raise KanbanRefused(
            "conflict", "This ticket changed on the desktop. Check it and try again."
        )
    status = task.get("status")
    if task.get("claim") or status == "in_progress" and wanted != "running":
        raise KanbanRefused(
            "conflict",
            "This ticket is running. Pause the organization or wait for the run to finish.",
        )
    if wanted == "running":
        if status == "blocked":
            raise KanbanRefused("invalid", "This ticket is blocked. Reopen it first.")
        if status not in {"backlog", "todo"}:
            raise KanbanRefused(
                "invalid",
                "Request changes to send it back for another run."
                if status == "in_review"
                else "Reopen it first.",
            )
        changes = {"status": "in_progress"}
    else:
        if status == "in_review":
            # Sending work back goes through a review, which records it and clears its claims.
            raise KanbanRefused("invalid", "Open it to request changes, so the review is recorded.")
        if status == wanted:
            return
        changes = {"status": wanted}
    if status in {"done", "blocked", "cancelled"}:
        # One call, straight into the chosen column, as the desktop board does: a ticket
        # reopened for Backlog never waits in To do, where a worker could start it.
        call(
            identifier,
            "reopen",
            {"taskId": task_id, "expectedRevision": revision, "status": wanted},
        )
        return
    call(
        identifier,
        "task_update",
        {"taskId": task_id, "expectedRevision": revision, "changes": changes},
    )
