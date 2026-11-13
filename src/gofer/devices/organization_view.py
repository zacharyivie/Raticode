"""The parts of an organization a paired phone shows beyond its tasks.

These extend the ``organizations.read`` answer for the phone's five sections:
Organization (mission, milestones, facts), Projects (projects, milestones, epics),
Issues (ticket keys, epics and evidence), Workers (the reporting tree) and Knowledge
(note summaries and the links between notes and tickets). Like the rest of the phone's
view they are path-free: project folders appear by name and the shared repository
without its location. Note bodies are read one at a time with ``organizations.note``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from gofer.ui.organization_auto import waits_on_rem

# The phone's dangerous-permission check reads these labels; anything else is sandboxed.
PERMISSION_LABELS = {
    "read-only": "Read only",
    "workspace-write": "Workspace access",
    "danger-full-access": "Full access",
    "bypassPermissions": "Full access",
    "default": "Asks first",
    "acceptEdits": "Edits files",
    "plan": "Plans only",
}
KINDS = {"decision", "note", "report", "spec", "finding"}
MAX_NOTES = 300
MAX_NOTE_BODY = 40_000


def _text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _waiting(request: Any) -> bool:
    """A consent request that can still be approved: pending and less than a day old."""
    if not isinstance(request, dict) or request.get("status") != "pending":
        return False
    try:
        asked = datetime.fromisoformat(str(request.get("at")))
    except ValueError:
        return False
    return (datetime.now(UTC) - asked).total_seconds() < 24 * 3600


def _strings(values: Any, limit: int, size: int) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_text(value, size) for value in values if isinstance(value, str)][:limit]


def _author(actor: Any, employees: dict[Any, Any]) -> str:
    actor = str(actor or "")
    if actor == "user":
        return "You"
    if actor == "system":
        return "Raticode"
    if actor == "Rem" or actor.startswith("Rem"):
        return "Rem"
    eid = actor.removeprefix("employee:")
    return _text((employees.get(eid) or {}).get("name") or eid or "Unknown", 150)


def evidence(claims: list[dict[str, Any]]) -> dict[str, int]:
    """Evidence badge counts, as the desktop's ticket cards show them."""
    summary = {"ok": 0, "warn": 0, "bad": 0, "none": 0, "pending": 0}
    for claim in claims:
        state = ((claim.get("evidence") or {}).get("state")) or "none"
        if state in {"ok", "warn", "bad"}:
            summary[state] += 1
        elif state in {"queued", "running"}:
            summary["pending"] += 1
        else:
            summary["none"] += 1
    return summary


def claims_by_task(runtime: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {}
    for claim in (runtime.get("claims") or {}).values():
        if isinstance(claim, dict) and not claim.get("supersededAt"):
            found.setdefault(str(claim.get("taskId")), []).append(claim)
    return found


def task_extras(
    task: dict[str, Any],
    diagnostics: dict[str, Any],
    claims: dict[str, list[dict[str, Any]]],
    reads: dict[str, set[str]],
) -> dict[str, Any]:
    """Fields the phone's board and ticket page add to a task."""
    identifier = str(task.get("id") or "")
    return {
        "key": _text(task.get("key") or f"#{task.get('number') or '?'}", 40),
        "assignee_id": _text(task.get("assignee"), 100),
        "epic_id": _text(task.get("epicId"), 100),
        "project_id": _text(task.get("project"), 100),
        "done_when": _text(task.get("doneWhen"), 1500),
        "approval_required": bool(task.get("approvalRequired")),
        "notes": _strings(task.get("knowledge"), 50, 100),
        "read_notes": sorted(reads.get(identifier, set()))[:50],
        "start_hint": _text((diagnostics.get("taskReasons") or {}).get(identifier), 300),
        "evidence_counts": evidence(claims.get(identifier, [])),
        "claims": [
            {
                "text": _text(claim.get("text"), 300),
                "state": _text((claim.get("evidence") or {}).get("state") or "none", 20),
                "kind": _text((claim.get("evidence") or {}).get("kind"), 20),
            }
            for claim in sorted(claims.get(identifier, []), key=lambda c: str(c.get("at")))[-10:]
        ],
        "created_at": _text(task.get("createdAt"), 80),
    }


def note_reads(runtime: dict[str, Any]) -> dict[str, set[str]]:
    """Notes each ticket's runs actually read."""
    found: dict[str, set[str]] = {}
    for link in (runtime.get("links") or {}).values():
        if not isinstance(link, dict) or link.get("removed") or link.get("kind") != "read":
            continue
        source, target = str(link.get("from") or ""), str(link.get("to") or "")
        if source.startswith("ticket:") and target.startswith("kb:"):
            found.setdefault(source[7:], set()).add(target[3:])
    return found


def extend(
    org: dict[str, Any],
    project_name: Callable[[str | None], str],
    repository: dict[str, Any] | None,
) -> dict[str, Any]:
    """Everything the phone's five sections need besides tasks and employees' basics."""
    config = org.get("config") or {}
    runtime = org.get("runtime") or {}
    diagnostics = org.get("diagnostics") or {}
    employees = {e.get("id"): e for e in config.get("employees") or [] if isinstance(e, dict)}
    knowledge = [
        entry
        for entry in (runtime.get("knowledge") or {}).values()
        if isinstance(entry, dict) and not entry.get("archived")
    ]
    knowledge.sort(key=lambda entry: str(entry.get("updatedAt") or ""), reverse=True)
    links = [
        link
        for link in (runtime.get("links") or {}).values()
        if isinstance(link, dict) and not link.get("removed") and link.get("kind") == "asserted"
    ]
    hours = diagnostics.get("hours") or {}
    heartbeats = diagnostics.get("heartbeats") or {}
    return {
        # The phone shows the five-section layout only for desktops that send this.
        "v2": True,
        "mission": _text(config.get("mission"), 1500),
        "ticket_prefix": _text(config.get("ticketPrefix"), 20),
        "milestones": [
            {
                "milestone_id": _text(m.get("id"), 100),
                "title": _text(m.get("title"), 300),
                "short": _text(m.get("short"), 80),
                "due_at": _text(m.get("dueAt"), 40),
                "project_id": _text(m.get("projectId"), 100),
                "parent_id": _text(m.get("parentId"), 100),
                "status": _text(m.get("status"), 20),
            }
            for m in (config.get("milestones") or [])[:200]
            if isinstance(m, dict)
        ],
        "epics": [
            {
                "epic_id": _text(e.get("id"), 100),
                "title": _text(e.get("title"), 300),
                "milestone_id": _text(e.get("milestoneId"), 100),
                "project_id": _text(e.get("projectId"), 100),
                "owner": _text(e.get("owner"), 100),
                "description": _text(e.get("description"), 1500),
                "status": _text(e.get("status"), 20),
            }
            for e in (config.get("epics") or [])[:300]
            if isinstance(e, dict)
        ],
        "initiatives": [
            {
                "project_id": _text(p.get("id"), 100),
                "name": _text(p.get("name") or p.get("id"), 200),
                "owner": _text(p.get("owner"), 100),
                "color": _text(p.get("color"), 20),
                "description": _text(p.get("description"), 1500),
                "folder": project_name(p.get("workspacePath")),
                "checks": len(p.get("checks") or []),
            }
            for p in (config.get("projects") or [])[:100]
            if isinstance(p, dict)
        ],
        "workers": [
            {
                "employee_id": _text(e.get("id"), 100),
                "persona": _text(e.get("persona"), 500),
                "responsibilities": _strings(e.get("responsibilities"), 20, 200),
                "role": _text(e.get("role"), 150),
                "effort": _text(e.get("effort"), 40),
                "permission": PERMISSION_LABELS.get(
                    str(e.get("permissionMode") or ""), "Workspace access"
                ),
                "full_access": e.get("permissionMode")
                in {"danger-full-access", "bypassPermissions"},
                "heartbeat_seconds": int(e.get("heartbeatSeconds") or 0),
                "ticket_access": _ticket_access(e.get("ticketAccess")),
                "stalled": _text(
                    ((heartbeats.get(e.get("id")) or {}).get("stalled") or {}).get("reason"), 300
                ),
            }
            for e in employees.values()
        ][:100],
        "notes": [
            {
                "note_id": _text(entry.get("id"), 100),
                "kind": entry.get("kind") if entry.get("kind") in KINDS else "note",
                "title": _text(entry.get("title"), 300),
                "summary": _text(entry.get("summary"), 400),
                "topic": _text(entry.get("topic") or "General", 120),
                "project_id": _text(entry.get("projectId"), 100),
                "links": _strings(entry.get("links"), 30, 100),
                "author": _author(entry.get("author"), employees),
                "updated_at": _text(entry.get("updatedAt") or entry.get("createdAt"), 80),
                "second_brain": bool((entry.get("source") or {}).get("type") == "secondBrain"),
                "changed": bool(entry.get("changedAt")),
            }
            for entry in knowledge[:MAX_NOTES]
        ],
        "note_links": [
            {
                "from": _text(link.get("from"), 120),
                "to": _text(link.get("to"), 120),
                "state": _text(link.get("state") or "ok", 10),
            }
            for link in links[:500]
        ],
        "requests": [
            {
                # The ID lets the phone approve or decline exactly this request.
                "request_id": _text(r.get("id"), 100),
                "summary": _text(r.get("summary"), 400),
                "at": _text(r.get("at"), 80),
                # "rem" while Auto mode's review decides it, "you" when it waits on the user.
                "waiting_on": "rem" if waits_on_rem(org, r) else "you",
            }
            for r in sorted(
                filter(_waiting, (runtime.get("consentRequests") or {}).values()),
                key=lambda r: str(r.get("at")),
            )
        ][:20],
        "hours": {
            "mode": _text(hours.get("mode"), 20),
            "open": hours.get("open") is not False,
            "opens_at": _text(hours.get("opensAt"), 80),
            "closes_at": _text(hours.get("closesAt"), 80),
        },
        "repository": repository_summary(repository) if config.get("sharedRepository") else None,
        "archived": bool(runtime.get("archived")),
        # Whether a worker with nothing to do pulls accepted Backlog tickets into To do.
        "pull_backlog": config.get("pullBacklog", True) is not False,
        # Auto mode: Rem decides requests; why it stopped, and its recent decisions.
        "auto_mode": bool(config.get("autoMode")),
        "auto_halt": auto_halt_view(runtime.get("autoHalt")),
        "auto_decisions": [
            {
                "summary": _text(d.get("summary"), 400),
                "decision": _text(d.get("decision"), 20),
                "reason": _text(d.get("reason"), 1000),
                "recommendation": _text(d.get("recommendation"), 600),
                "applied": bool(d.get("applied")),
                "error": _text(d.get("error"), 400),
                "at": _text(d.get("at"), 80),
            }
            for d in reversed((runtime.get("autoDecisions") or [])[-10:])
            if isinstance(d, dict)
        ],
        "schedule": schedule_view(config, diagnostics.get("schedule") or {}, employees),
    }


def auto_halt_view(halt: Any) -> dict[str, Any] | None:
    if not isinstance(halt, dict):
        return None
    return {
        "summary": _text(halt.get("summary"), 400),
        "kind": _text(halt.get("kind"), 20),
        "reason": _text(halt.get("reason"), 1000),
        "user_action": _text(halt.get("userAction"), 600),
        "request_id": _text(halt.get("requestId"), 100),
        "at": _text(halt.get("at"), 80),
    }


def block_view(block: dict[str, Any]) -> dict[str, Any]:
    """One work schedule block, as the phone shows and edits it."""
    from gofer.ui.organization_schedule import describe

    def number(key: str) -> int | None:
        value = block.get(key)
        return value if type(value) is int else None

    return {
        "block_id": _text(block.get("id"), 100),
        "label": _text(block.get("label"), 80),
        "focus": _text(block.get("focus"), 600),
        "cadence": _text(block.get("cadence") or "daily", 20),
        "start": _text(block.get("start"), 5),
        "end": _text(block.get("end"), 5),
        "days": [d for d in block.get("days") or [] if type(d) is int][:7],
        "month_day": number("monthDay"),
        "nth_week": number("nthWeek"),
        "weekday": number("weekday"),
        "quarter_month": number("quarterMonth") or 1,
        "keywords": _strings(block.get("keywords"), 12, 40),
        "when": _text(describe(block) if block.get("start") else "", 200),
    }


def schedule_view(
    config: dict[str, Any], state: dict[str, Any], employees: dict[Any, Any]
) -> dict[str, Any]:
    """The work schedule: its blocks, what is on now and next, and who works under it."""
    blocks = ((config.get("workSchedule") or {}).get("blocks")) or []
    current, following = state.get("current") or None, state.get("next") or None
    return {
        "timezone": _text(state.get("timezone") or "UTC", 80),
        "configured": bool(blocks),
        "blocks": [block_view(b) for b in blocks if isinstance(b, dict)][:40],
        "template": [block_view(b) for b in state.get("template") or []][:40],
        "current": {
            **block_view(current),
            "started_at": _text(current.get("startedAt"), 80),
            "ends_at": _text(current.get("endsAt"), 80),
        }
        if current
        else None,
        "next": block_view(following) if following else None,
        "changes_at": _text(state.get("changesAt"), 80),
        "issues": [
            {"kind": _text(i.get("kind"), 30), "text": _text(i.get("text"), 300)}
            for i in state.get("issues") or []
        ][:20],
        "underway": [
            {
                "employee_id": _text(u.get("employeeId"), 100),
                "name": _text((employees.get(u.get("employeeId")) or {}).get("name"), 150),
                "task_id": _text(u.get("taskId"), 100),
                "title": _text(u.get("title"), 200),
                "check_in": bool(u.get("checkIn")),
                "block_label": _text((u.get("block") or {}).get("label"), 80),
            }
            for u in state.get("underway") or []
        ][:50],
    }


def _ticket_access(value: Any) -> list[dict[str, Any]] | None:
    """None means every project, as the worker's tools allow."""
    if not isinstance(value, dict):
        return None
    return [
        {"project_id": _text(project, 100), "allowed": _strings(allowed, 4, 20)}
        for project, allowed in list(value.items())[:50]
    ]


def repository_summary(value: dict[str, Any] | None) -> dict[str, Any]:
    """The shared repository without its location."""
    value = value or {}
    commits = []
    for commit in (value.get("commits") or [])[:10]:
        if isinstance(commit, (list, tuple)) and len(commit) >= 4:
            sha, at, author, subject = commit[:4]
            commits.append(
                {
                    "sha": _text(sha, 12),
                    "at": _text(at, 80),
                    "author": _text(author, 120),
                    "subject": _text(subject, 300),
                }
            )
    return {
        "exists": bool(value.get("exists")),
        "files": int(value.get("count") or 0),
        "branches": len(value.get("openBranches") or []),
        "commits": commits,
    }


def note(org: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """One note with its body and its links in both directions."""
    config = org.get("config") or {}
    runtime = org.get("runtime") or {}
    employees = {e.get("id"): e for e in config.get("employees") or [] if isinstance(e, dict)}
    identifier = str(entry.get("id") or "")
    ref = f"kb:{identifier}"
    links = []
    for link in (runtime.get("links") or {}).values():
        if not isinstance(link, dict) or link.get("removed"):
            continue
        if ref not in {link.get("from"), link.get("to")}:
            continue
        links.append(
            {
                "kind": _text(link.get("kind"), 20),
                "from": _text(link.get("from"), 120),
                "to": _text(link.get("to"), 120),
                "quote": _text(link.get("quote"), 600),
                "state": _text(link.get("state") or "ok", 10),
                "actor": _author(link.get("actor"), employees),
                "at": _text(link.get("at"), 80),
            }
        )
    body = str(entry.get("body") or "")
    return {
        "note_id": identifier[:100],
        "kind": entry.get("kind") if entry.get("kind") in KINDS else "note",
        "title": _text(entry.get("title"), 300),
        "summary": _text(entry.get("summary"), 1500),
        "topic": _text(entry.get("topic") or "General", 120),
        "project_id": _text(entry.get("projectId"), 100),
        "body": body[:MAX_NOTE_BODY],
        "truncated": len(body) > MAX_NOTE_BODY,
        "links": _strings(entry.get("links"), 30, 100),
        "author": _author(entry.get("author"), employees),
        "created_at": _text(entry.get("createdAt"), 80),
        "updated_at": _text(entry.get("updatedAt"), 80),
        "version": int(entry.get("version") or 1),
        "second_brain": bool((entry.get("source") or {}).get("type") == "secondBrain"),
        "source_missing": bool(entry.get("sourceMissing")),
        "connections": links[:100],
    }
